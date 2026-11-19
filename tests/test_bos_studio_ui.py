"""Drive the Record Studio rail and inspector through native Qt events."""
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from bos_studio_model import StudioEdit
from bos_studio_ui import RecordStudio


class StudioTimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.clip = SimpleNamespace(source_frame=lambda i: i-200)
        self.model = StudioEdit(120, 30)
        self.panel = RecordStudio(self.clip, self.model)
        self.addCleanup(self.panel.close)
        self.panel.resize(760, self.panel.sizeHint().height())
        self.panel.show()
        self.app.processEvents()
        self.track = self.panel.track

    def point(self, frame, y=32):
        return QPoint(round(self.track.x_for(frame)), y)

    def drag(self, begin, end):
        QTest.mousePress(self.track, Qt.LeftButton, pos=self.point(begin))
        QTest.mouseMove(self.track, self.point(end))
        QTest.mouseRelease(self.track, Qt.LeftButton, pos=self.point(end))

    def overlay_ranges(self):
        return [(s.first, s.last) for s in self.model.overlays]

    def test_scissors_endstops_move_fill_and_fades(self):
        p = self.panel
        QTest.mouseClick(self.track, Qt.LeftButton, pos=self.point(60.5, 8))
        self.assertEqual(p.playhead, 60)
        p.split.click()
        self.assertEqual(self.overlay_ranges(), [(0, 59), (60, 119)])
        self.assertEqual((p.first.value(), p.last.value()), (-140, -81))
        # Pull facing handles apart. A drag updates the editor once, on release.
        commits = QSignalSpy(p.changed)
        self.drag(59.7, 40.7)
        self.drag(60.3, 80.3)
        self.assertEqual(commits.count(), 2)
        self.assertEqual(self.overlay_ranges(), [(0, 40), (80, 119)])
        self.drag(95.5, 84.5)
        self.assertEqual(self.overlay_ranges(), [(0, 40), (69, 108)])
        self.drag(87.5, 2.5)
        self.assertEqual(self.overlay_ranges(), [(0, 40), (41, 80)])
        p.first.setValue(-145)
        p.last.setValue(-125)
        self.assertEqual(self.overlay_ranges()[1], (55, 75))
        p.fade_in.setValue(.2)
        p.fade_out.setValue(.1)
        self.assertEqual(self.model.alpha_at(55), 0)
        self.assertEqual(self.model.alpha_at(75), 0)
        self.assertEqual(self.model.alpha_at(65), 1)
        QTest.mouseClick(self.track, Qt.LeftButton, pos=self.point(90.5))
        self.assertTrue(p.add.isEnabled())
        p.add.click()
        self.assertEqual(self.overlay_ranges()[-1], (76, 119))
        QTest.keyClick(self.track, Qt.Key_Delete)
        self.assertEqual(self.overlay_ranges(), [(0, 40), (55, 75)])

    def test_speed_cut_duration_precision_boundary_drag_and_merge(self):
        p = self.panel
        p.tabs[1].click()
        p.set_position(60)
        p.split.click()
        p.speed.setValue(240)
        self.assertAlmostEqual(self.model.duration, 2.25)
        self.assertEqual(p.duration_label.text(), 'Length 0:02.25')
        self.drag(60.3, 40.3)
        self.assertEqual(self.model.speeds[0].last, 39)
        self.assertEqual(self.model.speeds[1].first, 40)
        p.first.setValue(-152)
        self.assertEqual(self.model.speeds[1].first, 48)
        self.assertEqual(self.model.speeds[1].fps, 240)
        self.assertAlmostEqual(self.model.duration, 1.9)
        self.assertEqual(p.output_fps(), 60)
        p.encoding.setCurrentIndex(p.encoding.findData(240))
        self.assertEqual(p.output_fps(), 240)
        self.assertAlmostEqual(self.model.duration, 1.9)
        p.delete.click()
        self.assertEqual(len(self.model.speeds), 1)
        self.assertEqual(self.model.duration, 4.)

    def test_optional_timed_caption_presets_and_cancel(self):
        p = self.panel
        p.tabs[2].click()
        self.assertFalse(p.text_settings()['fields'])

        def fill():
            dlg = self.app.activeModalWidget()
            dlg.checks['playback'].setChecked(True)
            dlg.caption.setPlainText('Ignition')
            dlg.position.setCurrentIndex(dlg.position.findData('top_right'))
            dlg.size.setCurrentIndex(dlg.size.findData('small'))
            dlg.accept()
        QTimer.singleShot(0, fill)
        p.add.click()
        p.first.setValue(-183)
        p.last.setValue(-108)
        self.assertEqual(p.text_settings(), dict(fields=('frame', 'playback', 'custom'),
            custom='Ignition', first=17, last=92, position='top_right', size='small'))
        self.drag(30.5, 40.5)
        self.assertEqual((p.text_settings()['first'], p.text_settings()['last']), (27, 102))
        QTimer.singleShot(0, lambda: self.app.activeModalWidget().reject())
        p.add.click()
        self.assertIn('custom', p.text_settings()['fields'])
        p.delete.click()
        self.assertFalse(p.text_settings()['fields'])

    def test_hidden_seek_on_reopen_single_frame_and_narrow_width(self):
        p = self.panel
        self.assertFalse(p.split.isEnabled())
        p.hide()
        p.set_position(90)
        p.show()
        self.assertTrue(p.split.isEnabled())
        p.resize(560, p.sizeHint().height())
        self.app.processEvents()
        self.assertLessEqual(p.minimumSizeHint().width(), 560)
        self.assertLessEqual(p.fade_out.mapTo(p, p.fade_out.rect().topRight()).x(), p.width())
        self.assertGreater(p.inspector.y(), p.delete.mapTo(p, p.delete.rect().bottomLeft()).y())
        single = RecordStudio(self.clip, StudioEdit(1))
        self.addCleanup(single.close)
        single.show()
        self.assertFalse(single.split.isEnabled())
        single.delete.click()
        self.assertTrue(single.add.isEnabled())
        single.add.click()
        self.assertEqual(single.model.overlays[0].last, 0)
        self.assertFalse(single.split.isEnabled())

    def test_shared_theme_wraps_inspector_at_560_and_condenses_at_1080(self):
        theme = Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure/styles.css'
        original = self.app.styleSheet()
        self.addCleanup(self.app.setStyleSheet, original)
        self.app.setStyleSheet(theme.read_text())
        p = self.panel
        for width in (640, 560, 1080, 800, 640, 560):
            p.resize(width, p.sizeHint().height())
            QTest.qWait(10)  # allow the nested Qt layout requests to settle
            self.assertEqual(p.width(), width)
            self.assertLessEqual(p.fade_out.mapTo(p, p.fade_out.rect().bottomRight()).x(), width)
            self.assertEqual(p.header_hint.isVisible(), width >= 990)
            self.assertEqual(p.hint.isVisible(), width < 990)
        p.resize(1080, p.sizeHint().height())
        self.app.processEvents()
        self.assertGreater(p.inspector.x(), p.delete.mapTo(p, p.delete.rect().bottomRight()).x())

    def test_cut_stays_at_requested_playhead_if_pausing_updates_the_display(self):
        p = self.panel
        p.set_position(64)
        p.editStarted.connect(lambda: p.set_position(0))
        p.split.click()
        self.assertEqual(self.overlay_ranges(), [(0, 63), (64, 119)])

    def test_cancel_drag_and_ruler_keyboard(self):
        p = self.panel
        seeks = QSignalSpy(p.seekRequested)
        QTest.mousePress(self.track, Qt.LeftButton, pos=self.point(.3))
        QTest.mouseMove(self.track, self.point(30.3))
        QTest.keyClick(self.track, Qt.Key_Escape)
        QTest.mouseRelease(self.track, Qt.LeftButton, pos=self.point(30.3))
        self.assertEqual(self.overlay_ranges(), [(0, 119)])
        self.assertEqual(seeks.count(), 0)
        QTest.keyClick(self.track, Qt.Key_Right)
        self.assertEqual(p.playhead, 1)
        self.assertEqual(seeks.at(0), [1])
