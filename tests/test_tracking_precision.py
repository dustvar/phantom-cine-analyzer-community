"""Point picking through actual Qt mouse/keyboard events, at image precision."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import test_guided_workspace as workspace
from tracking_geometry import pose_at
from tracking_setup_ui import CLASSIC, EDGE
from tumbling_tracker import TUMBLING_TRACKING_METHOD


class PrecisionPickingTests(unittest.TestCase):
    setUpClass = classmethod(workspace.GuidedWorkspaceTests.setUpClass.__func__)
    tearDownClass = classmethod(workspace.GuidedWorkspaceTests.tearDownClass.__func__)
    setUp = workspace.GuidedWorkspaceTests.setUp
    tearDown = workspace.GuidedWorkspaceTests.tearDown
    wait_worker = workspace.GuidedWorkspaceTests.wait_worker

    def pick_mode(self, method=CLASSIC):
        w = self.w
        w.track_tool.click()
        with patch.object(w, 'choose_tracking_method', return_value=method):
            w.add_object_button.click()
        w.raise_()
        w.activateWindow()
        QTest.qWait(80)
        w.graph.setFocus()
        position = w.graph.mapFromScene(QPointF(150, 120))
        # QTest may omit a native mouse move when its global position did not
        # change since the preceding method. Explicitly approach the target.
        QTest.mouseMove(w.graph.viewport(), position + QPoint(7, 0))
        QTest.qWait(20)
        QTest.mouseMove(w.graph.viewport(), position)
        QTest.qWait(40)
        # Deliver the final local-position event explicitly as well. Cocoa
        # may coalesce OS cursor moves while a newly shown window settles.
        viewport=w.graph.viewport()
        QApplication.sendEvent(viewport,QMouseEvent(QEvent.MouseMove,QPointF(position),
            QPointF(viewport.mapToGlobal(position)),Qt.NoButton,Qt.NoButton,Qt.NoModifier))
        self.assertLess(abs(w._point_candidate.x()-150), 1)
        self.assertLess(abs(w._point_candidate.y()-120), 1)
        self.assertTrue(w._point_picker.isVisible())
        self.assertTrue(w.status_toolbar.isHidden())

    def test_preview_and_enter_work_for_each_method_without_plot(self):
        for method in (CLASSIC, EDGE, TUMBLING_TRACKING_METHOD):
            with self.subTest(method=method):
                self.pick_mode(method)
                QTest.keyClick(self.w.graph, Qt.Key_Return)
                QTest.qWait(30)
                self.assertEqual(self.w._guide['step'], 1)
                self.assertEqual(self.w._guide_track()['tracking_method'], method)
                self.assertFalse(self.w._point_picker.isVisible())
                self.w.tracking_guide.cancel.click()

    def test_keys_use_image_pixels_at_both_zoom_levels_and_keep_fraction(self):
        for zoom in (0, 5):
            self.pick_mode()
            w = self.w
            w.graph.zoom_at(w.graph.viewport().rect().center(), zoom)
            w._set_point_candidate(QPointF(150, 120))
            frame = w.vm.active_frame
            QTest.keyClick(w.graph, Qt.Key_Right)
            QTest.keyClick(w.graph, Qt.Key_Up, Qt.ShiftModifier)
            self.assertAlmostEqual(w._point_candidate.x(), 151)
            self.assertAlmostEqual(w._point_candidate.y(), 119.9)
            self.assertEqual(w.vm.active_frame, frame)
            QTest.keyClick(w.graph, Qt.Key_Return)
            QTest.qWait(20)
            np.testing.assert_allclose(w._guide_track()['points'][0], (151, 119.9), atol=1e-5)
            w.tracking_guide.cancel.click()
            w.graph.reset_view_zoom()

    def test_rotated_arrows_follow_screen_direction_and_stay_inside_image(self):
        self.pick_mode()
        w = self.w
        w.graph.rotate(90)
        w._set_point_candidate(QPointF(150, 120))
        QTest.keyClick(w.graph, Qt.Key_Right)
        self.assertEqual(w._point_candidate, QPointF(150, 119))
        QTest.keyClick(w.graph, Qt.Key_Down, Qt.ShiftModifier)
        self.assertEqual(w._point_candidate, QPointF(150.1, 119))
        w._set_point_candidate(QPointF(0, 0))
        QTest.keyClick(w.graph, Qt.Key_Right)
        self.assertEqual(w._point_candidate, QPointF(0, 0))

    def test_panning_keeps_loupe_fixed_and_candidate_in_source_coordinates(self):
        self.pick_mode()
        w=self.w;graph=w.graph
        graph.zoom_at(graph.viewport().rect().center(),5)
        w._set_point_candidate(QPointF(150,120),keyboard=True)
        graph.horizontalScrollBar().setValue(graph.horizontalScrollBar().value()+40)
        graph.verticalScrollBar().setValue(graph.verticalScrollBar().value()+20)
        QTest.qWait(30)
        self.assertEqual(w._point_picker.geometry(),graph.viewport().rect())
        self.assertEqual(w._point_candidate,QPointF(150,120))

    def test_edit_point_and_reference_do_not_move_tracking_region(self):
        self.pick_mode(EDGE)
        QTest.keyClick(self.w.graph, Qt.Key_Return)
        QTest.qWait(20)
        w = self.w
        track = w._guide_track()
        center = pose_at(track, track['anchor_frame'])[0].copy()
        w._guide_pick('point')
        w._set_point_candidate(QPointF(180, 100))
        QTest.keyClick(w.graph, Qt.Key_Right, Qt.ShiftModifier)
        QTest.keyClick(w.graph, Qt.Key_Return)
        QTest.qWait(20)
        np.testing.assert_allclose(track['points'][0], (180.1, 100))
        np.testing.assert_allclose(pose_at(track, track['anchor_frame'])[0], center)
        w._guide_step(1)
        w._guide_step(1)
        w.tracking_guide.reference.setCurrentIndex(2)
        w._guide_pick('reference')
        w._set_point_candidate(QPointF(200, 200))
        QTest.keyClick(w.graph, Qt.Key_Up, Qt.ShiftModifier)
        QTest.keyClick(w.graph, Qt.Key_Return)
        QTest.qWait(20)
        self.assertEqual(w.tracking_guide.ref_y.value(), 199.9)
        np.testing.assert_allclose(track['points'][0], (180.1, 100))

    def test_picker_does_not_hijack_inputs_and_escape_cancels(self):
        self.pick_mode()
        w = self.w
        w.tracking_guide.target.setFocus()
        QTest.keyClick(w.tracking_guide.target, Qt.Key_Down)
        self.assertEqual(w.tracking_guide.target.currentData(), 'region_center')
        self.assertFalse(w._point_picker.isVisible())
        w.tracking_guide.target.setCurrentIndex(0)
        QTest.keyClick(w.graph, Qt.Key_Return)
        QTest.qWait(20)
        w._guide_step(1)
        w._guide_step(1)
        w.tracking_guide.reference.setCurrentIndex(2)
        w._guide_pick('reference')
        before = QPointF(w._point_candidate)
        spin = w.tracking_guide.ref_x
        spin.setFocus()
        value = spin.value()
        QTest.keyClick(spin, Qt.Key_Up)
        self.assertGreater(spin.value(), value)
        self.assertEqual(w._point_candidate, before)
        w.graph.setFocus()
        QTest.keyClick(w.graph, Qt.Key_Escape)
        QTest.qWait(20)
        self.assertIsNotNone(w._guide)
        self.assertFalse(w._point_picker.isVisible())
        QTest.keyClick(w.graph, Qt.Key_Escape)
        QTest.qWait(20)
        self.assertIsNone(w._guide)
        self.assertFalse(w.vm.track_data)

    def test_loupe_displays_source_pixel_and_fits_compact_window(self):
        self.w.resize(1024, 720)
        self.pick_mode()
        w = self.w
        w._set_point_candidate(QPointF(145, 120))
        QTest.qWait(30)
        image = w._point_picker.grab().toImage()
        source = w.graph._image_item.pixmap().toImage()
        lens = w._point_picker.lens_rect
        # Away from the crosshair lines, the nearest-neighbor magnification
        # contains exactly the same displayed source pixel, with no new read.
        location = ((lens.center() + QPointF(4, 4)) * image.devicePixelRatio()).toPoint()
        self.assertEqual(image.pixelColor(location), source.pixelColor(145, 120))
        self.assertTrue(w._point_picker.rect().contains(lens.toRect()))
        self.assertLessEqual(lens.width(), 156)


if __name__ == '__main__':
    unittest.main()
