"""Canvas interaction tests in letterboxed / pillarboxed Cine previews."""
import os
import unittest

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from bos_compass import MAX_SIZE, MIN_SIZE, compass_rect, draw_compass
from bos_compass_view import CompassViewMixin
from camera_comparator_ui import ImageView


class TestView(CompassViewMixin, ImageView):
    pass


class CompassViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = TestView()
        self.addCleanup(self.view.close)
        self.view.resize(600, 600)
        self.frame = np.full((400, 600, 3), 92, dtype=np.uint8)
        self.view.set_frame(self.frame)
        self.view.show()
        self.app.processEvents()
        self.view.set_compass_enabled(True)

    def drag(self, start, end):
        QTest.mousePress(self.view, Qt.LeftButton, pos=start)
        QTest.mouseMove(self.view, end)
        QTest.mouseRelease(self.view, Qt.LeftButton, pos=end)

    def source_rect(self):
        return compass_rect(self.view.compass, 600, 400)

    def test_move_keep_size_and_clamp_to_image(self):
        rect = self.view.compass_view_rect()
        self.assertAlmostEqual(rect.top(), 100 + self.source_rect()[1])
        self.drag(rect.center().toPoint(), QPoint(596, 596))
        x, y, side, _ = self.source_rect()
        self.assertEqual((x+side, y+side), (600, 400))
        self.assertEqual(side, round(.22*400))
        self.view.resize(900, 400)
        self.app.processEvents()
        self.assertAlmostEqual(self.view.compass_view_rect().right(), 750)
        self.assertAlmostEqual(self.view.compass_view_rect().bottom(), 400)

    def test_resize_clamps_to_bounds_and_keeps_opposite_corner(self):
        rect = self.view.compass_view_rect()
        initial = self.source_rect()
        self.drag((rect.bottomRight()-QPointF(3, 3)).toPoint(), QPoint(595, 595))
        x, y, side, _ = self.source_rect()
        self.assertEqual((x, y), initial[:2])
        self.assertEqual(side, round(MAX_SIZE*400))
        rect = self.view.compass_view_rect()
        self.drag((rect.topLeft()+QPointF(3, 3)).toPoint(), rect.bottomRight().toPoint())
        x2, y2, side2, _ = self.source_rect()
        self.assertEqual(side2, round(MIN_SIZE*400))
        self.assertEqual((x2+side2, y2+side2), (x+side, y+side))

    def test_deselect_reselect_remove_and_restore(self):
        v = self.view
        removed = []
        v.compass_removed = lambda: removed.append(True)
        QTest.mouseClick(v, Qt.LeftButton, pos=QPoint(4, 4))
        self.assertFalse(v.compass_selected)
        QTest.mouseClick(v, Qt.LeftButton, pos=v.compass_view_rect().center().toPoint())
        self.assertTrue(v.compass_selected)
        QTest.keyClick(v, Qt.Key_Escape)
        self.assertFalse(v.compass_selected)
        QTest.mouseClick(v, Qt.LeftButton, pos=v.compass_view_rect().center().toPoint())
        placement = v.compass
        close = v._remove_rect(v.compass_view_rect()).center().toPoint()
        QTest.mouseClick(v, Qt.LeftButton, pos=close)
        self.assertIsNone(v.compass)
        self.assertEqual(len(removed), 1)
        v.set_compass_enabled(True)
        self.assertEqual(v.compass, placement)
        QTest.keyClick(v, Qt.Key_Backspace)
        self.assertIsNone(v.compass)
        self.assertEqual(len(removed), 2)

    def test_raw_frame_gates_artwork_and_hit_testing(self):
        v = self.view
        pos = v.compass_view_rect().center().toPoint()
        saved = v.compass
        v.set_compass_visible(False)
        self.assertTrue(v.compass_view_rect().isEmpty())
        self.drag(pos, pos+QPoint(55, 35))
        self.assertFalse(v.compass_selected)
        self.assertEqual(v.compass, saved)
        v.set_compass_visible(True)
        self.assertFalse(v.compass_view_rect().isEmpty())

    def test_paused_preview_matches_export_and_leaves_base_unmodified(self):
        from PySide6.QtGui import QImage
        v = self.view
        v.deselect_compass()
        v.resize(600, 400)
        self.app.processEvents()
        rendered = v.grab().toImage().convertToFormat(QImage.Format_RGB888)
        # Retina screens return device pixels; reduce to one source pixel per point.
        actual = rendered.scaled(600, 400, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        data = np.frombuffer(actual.constBits(), dtype=np.uint8)
        rgb = data.reshape(400, actual.bytesPerLine())[:, :1800].reshape(400, 600, 3)
        expected = self.frame.copy()
        draw_compass(expected, v.compass)
        diff = np.abs(rgb.astype('int16')-expected.astype('int16'))
        self.assertLess(diff.mean(), .35)
        x, y, side, _ = self.source_rect()
        patch_error = diff[y:y+side, x:x+side].mean()
        self.assertLess(patch_error, 2)
        self.assertTrue(np.all(self.frame == 92))
