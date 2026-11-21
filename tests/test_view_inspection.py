"""The same framing gestures must preserve pixel coordinates in both viewing workflows."""
import os
import unittest

import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QNativeGestureEvent, QPointingDevice, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from camera_control_ui import _Preview
from view_inspection import PURPLE, YELLOW


class InspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = _Preview()
        self.view.set_mode('linear')
        self.view.resize(600, 400)
        self.view.show()
        self.view.set_frame(np.full((400, 600), 64, dtype=np.uint8))
        self.app.processEvents()
        self.addCleanup(self.view.close)

    def test_pinch_wheel_pan_and_fit(self):
        v = self.view
        vp = v.viewport()
        pos = QPoint(390, 230)
        world = v.mapToScene(pos)
        global_pos = QPointF(vp.mapToGlobal(pos))
        native = QNativeGestureEvent(Qt.ZoomNativeGesture, QPointingDevice.primaryPointingDevice(), 2,
                                     QPointF(pos), QPointF(pos), global_pos, .5, QPointF())
        QApplication.sendEvent(vp, native)
        self.assertAlmostEqual(v._view_zoom, 1.5)
        QApplication.sendEvent(vp, QWheelEvent(QPointF(pos), global_pos, QPoint(), QPoint(0,120),
                                                Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False))
        self.assertAlmostEqual(v._view_zoom, 1.8)
        self.assertLess((world - v.mapToScene(pos)).manhattanLength(), 2)
        before = v.navigator_geometry()[1]
        QTest.mousePress(vp, Qt.LeftButton, pos=pos)
        QTest.mouseMove(vp, pos+QPoint(50, 30))
        QTest.mouseRelease(vp, Qt.LeftButton, pos=pos+QPoint(50, 30))
        area, visible = v.navigator_geometry()
        self.assertLess(visible.x(), before.x())
        self.assertLess(visible.y(), before.y())
        self.assertTrue(area.contains(visible))
        self.assertLess(visible.width(), area.width())
        qimage = vp.grab().toImage()
        self.assertEqual(qimage.pixelColor(visible.center().toPoint()).red(), 64)
        self.assertTrue(any(qimage.pixelColor(round(visible.left())+offset, int(visible.center().y())) == YELLOW
                            for offset in range(-1, 2)))
        v.set_frame(np.full((400, 600), 120, dtype=np.uint8))
        self.assertEqual(v.navigator_geometry()[1], visible)
        self.assertEqual(vp.grab().toImage().pixelColor(visible.center().toPoint()).red(),120)
        QTest.mouseClick(v.bar.fit, Qt.LeftButton)
        self.assertEqual(v._view_zoom, 1)
        self.assertTrue(v.navigator_geometry()[0].isEmpty())

    def test_resize_keeps_zoom_relative_to_fit(self):
        v = self.view
        v.resize(350, 250)
        self.app.processEvents()
        v.bar.plus.click()
        v.resize(850, 600)
        self.app.processEvents()
        scale = v.transform().m11()
        relative_zoom = v._view_zoom
        v.reset_view_zoom()
        self.assertAlmostEqual(scale / v.transform().m11(), relative_zoom, places=3)

    def test_guide_cycle_resize_restart(self):
        v = self.view
        v.bar.guides.click()
        self.assertEqual(v.guide_mode, 1)
        img = v.viewport().grab().toImage()
        pt = v.mapFromScene(QPointF(300, 70))
        self.assertTrue(any(img.pixelColor(pt.x()+dx,pt.y()) == PURPLE for dx in (-1,0,1)))
        self.assertEqual(v.image.pixelColor(300,70).red(), 64)
        v.bar.guides.click()
        self.assertEqual(v.guide_mode, 2)
        v.bar.guides.click()
        self.assertEqual(v.guide_mode, 0)
        v.bar.plus.click()
        v.set_frame(np.zeros((100,100),dtype=np.uint8))
        self.assertEqual(v._view_zoom,1)
        self.assertEqual(v.sceneRect().width(),100)
        v.clear('Waiting for camera')
        self.assertTrue(v.bar.isHidden())
        self.assertTrue(v.navigator_geometry()[0].isEmpty())
