"""Open and edit speed transitions using the actual Record Studio controls."""
import os
import sys
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMenu

from bos_studio_model import StudioEdit
from bos_studio_ui import RecordStudio


class SpeedRampUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.model = StudioEdit(1800, 200)
        self.model.split_speed(600)
        self.model.set_speed(1, 800)
        self.panel = RecordStudio(SimpleNamespace(source_frame=lambda i: i-2000), self.model)
        self.addCleanup(self.panel.close)
        self.panel.resize(820, self.panel.sizeHint().height())
        self.panel.show()
        self.app.processEvents()

    def point(self, sample, row=1):
        track = self.panel.track
        return QPoint(round(track.x_for(sample)), round(track.lane(row).center().y()))

    def open_cut(self, sample):
        QTest.mouseClick(self.panel.track, Qt.RightButton, pos=self.point(sample))
        self.app.processEvents()
        self.assertIsNotNone(self.panel._ramp_dialog)
        return self.panel._ramp_dialog

    def test_right_click_drafts_curve_and_duration_without_seeking(self):
        self.panel.set_position(100)
        edits = QSignalSpy(self.panel.changed)
        seek = QSignalSpy(self.panel.seekRequested)
        dialog = self.open_cut(600)
        self.assertEqual((self.panel.mode, self.panel.selected[1]), (1, 1))
        self.assertEqual(dialog.duration.value(), 0)
        self.assertEqual(dialog.graph.curve, 'instant')
        dialog.slider.setValue(500)
        self.assertGreater(dialog.duration.value(), 0)
        dialog.curve.setCurrentIndex(dialog.curve.findData('linear'))
        dialog.duration.setValue(.6)
        self.assertEqual(dialog.graph.curve, 'linear')
        self.assertEqual(self.model.speeds[1].ramp, 0)
        dialog.reject()
        self.assertEqual(edits.count(), 0)
        self.assertIsNone(self.panel._ramp_dialog)
        dialog = self.open_cut(600)
        dialog.duration.setValue(.6)
        dialog.accept()
        self.assertEqual(self.model.speeds[1].ramp, .6)
        self.assertEqual(self.model.speeds[1].curve, 'smooth')
        self.assertEqual(edits.count(), 1)
        self.assertEqual(seek.count(), 0)
        self.assertEqual(self.panel.playhead, 100)
        first, last = self.model.ramp_bounds(1)
        self.assertAlmostEqual(last-first, .6)
        track = self.panel.track
        on_band = QPoint(round(track._time_x(first+.03, self.model)), round(track.lane(1).bottom()-4))
        self.assertEqual(track._cut_at(on_band), 1)
        QTest.mouseClick(track, Qt.RightButton, pos=on_band)
        self.assertEqual(self.panel._ramp_dialog.duration.value(), .6)
        self.panel._ramp_dialog.duration.setValue(0)
        self.panel._ramp_dialog.accept()
        self.assertEqual(self.model.speeds[1].ramp, 0)
        self.assertEqual(edits.count(), 2)

    def test_keyboard_cut_picker_targets_ramp_down_and_native_context_menu(self):
        self.model.split_speed(1200)
        self.model.set_speed(2, 200)
        self.panel.set_mode(1)
        self.panel.ramp.setFocus()
        QTest.keyClick(self.panel.ramp, Qt.Key_Space)
        self.app.processEvents()
        menu = self.app.activePopupWidget()
        self.assertIsInstance(menu, QMenu)
        last = menu.actions()[-1]
        self.assertIn('800 → 200 fps', last.text())
        QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(last).center())
        self.app.processEvents()
        dialog = self.panel._ramp_dialog
        self.assertEqual(dialog.cut, 2)
        dialog.curve.setCurrentIndex(dialog.curve.findData('linear'))
        dialog.duration.setValue(.3)
        dialog.accept()
        self.assertEqual((self.model.speeds[2].ramp, self.model.speeds[2].curve), (.3, 'linear'))
        self.assertEqual(self.model.speeds[1].ramp, 0)
        pos = self.point(600)
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, self.panel.track.mapToGlobal(pos))
        self.app.sendEvent(self.panel.track, event)
        self.assertEqual(self.panel._ramp_dialog.cut, 1)
        self.panel._ramp_dialog.reject()

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS Control-click')
    def test_physical_control_click_opens_editor_without_seek(self):
        self.panel.set_position(75)
        seek = QSignalSpy(self.panel.seekRequested)
        mod = (Qt.ControlModifier if self.app.testAttribute(Qt.ApplicationAttribute.AA_MacDontSwapCtrlAndMeta)
               else Qt.MetaModifier)
        QTest.mouseClick(self.panel.track, Qt.LeftButton, mod, pos=self.point(600))
        self.assertEqual(self.panel._ramp_dialog.cut, 1)
        self.assertEqual(self.panel.playhead, 75)
        self.assertEqual(self.model.speeds[1].first, 600)
        self.assertEqual(seek.count(), 0)
        self.panel._ramp_dialog.reject()

    def test_other_rows_and_section_body_ignore_right_click(self):
        track = self.panel.track
        QTest.mouseClick(track, Qt.RightButton, pos=self.point(600, 0))
        QTest.mouseClick(track, Qt.RightButton, pos=self.point(260))
        self.assertIsNone(self.panel._ramp_dialog)
        self.assertEqual(self.panel.mode, 0)
        self.assertTrue(self.panel.ramp.isHidden())
        self.model.remove_speed_cut(1)
        self.panel.set_mode(1)
        self.assertFalse(self.panel.ramp.isEnabled())
