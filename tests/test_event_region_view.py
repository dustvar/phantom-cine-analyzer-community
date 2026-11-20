"""Point-probe placement stays in source pixels at any displayed image size."""
import unittest

import numpy as np
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from event_region_view import EventRegionView


class EventRegionViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_probe_click_clamps_at_edges_and_ignores_letterbox(self):
        view = EventRegionView()
        view.resize(370, 340)
        view.set_frame(np.zeros((60, 110, 3), np.uint8))
        view.set_point_mode(True)
        rectangles = []
        view.regionSelected.connect(rectangles.append)
        view.show()
        try:
            self.app.processEvents()
            area = view.image_rect()
            QTest.mouseClick(view, Qt.LeftButton, pos=QPoint(1, 1))
            self.assertEqual(rectangles, [])
            for px, py, expected in ((0, 0, (0, 0, 4, 4)), (109, 59, (106, 56, 4, 4)),
                                     (44, 28, (42, 26, 4, 4))):
                position = QPoint(round(area.left() + (px + .2) * area.width() / 110),
                                  round(area.top() + (py + .2) * area.height() / 60))
                QTest.mouseClick(view, Qt.LeftButton, pos=position)
                self.assertEqual(rectangles[-1], expected)
            view.set_point_mode(False)
            QTest.mouseClick(view, Qt.LeftButton, pos=area.center().toPoint())
            self.assertEqual(len(rectangles), 3)
        finally:
            view.close()
