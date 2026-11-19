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

    def point(self, frame, y=None):
        if y is None:
            y = round(self.track.lane().center().y())
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
        # The cut stays under the handle as the new speed mix changes total length.
        target_fraction = (40/30) / (40/30 + 80/240)
        lane = self.track.lane()
        offset = self.track.x_for(60.3) - self.track.x_for(60)
        begin = self.point(60.3)
        end = QPoint(round(lane.left()+lane.width()*target_fraction+offset), begin.y())
        QTest.mousePress(self.track, Qt.LeftButton, pos=begin)
        QTest.mouseMove(self.track, end)
        QTest.mouseRelease(self.track, Qt.LeftButton, pos=end)
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
            dlg.accept()
        QTimer.singleShot(0, fill)
        p.add.click()
        p.first.setValue(-183)
        p.last.setValue(-108)
        self.assertEqual(p.text_settings(), dict(fields=('frame', 'playback', 'custom'),
            custom='Ignition', first=17, last=92, position='center', size='medium'))
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

    def test_stacked_tracks_select_on_click_and_use_edited_seconds(self):
        clip = SimpleNamespace(source_frame=lambda i: i-30, has_vector_track=True)
        model = StudioEdit(600, 200)
        model.split_speed(200)
        model.split_speed(400)
        model.set_speed(1, 600)
        panel = RecordStudio(clip, model)
        self.addCleanup(panel.close)
        panel.resize(810, panel.sizeHint().height())
        panel.show()
        self.app.processEvents()
        track = panel.track
        self.assertEqual(track.rows, [0, 3, 1, 2])
        left = track.x_for(200) - track.x_for(0)
        middle = track.x_for(400) - track.x_for(200)
        right = track.x_for(600) - track.x_for(400)
        self.assertAlmostEqual(left / middle, 3.)
        self.assertAlmostEqual(right / middle, 3.)
        self.assertAlmostEqual(model.duration, 7/3)
        tool_pos = panel.split.mapTo(panel, panel.split.rect().topLeft())
        for mode in track.rows:
            point = QPoint(round(track.x_for(268)), round(track.lane(mode).center().y()))
            QTest.mouseClick(track, Qt.LeftButton, pos=point)
            self.assertEqual(panel.mode, mode)
            self.assertTrue(panel.tabs[mode].isChecked())
            self.assertEqual(panel.playhead, model.frame_at(
                (point.x()-track.lane().left()) / track.lane().width() * model.duration))
            self.assertTrue(panel.split.isVisible())
            self.assertEqual(panel.split.mapTo(panel, panel.split.rect().topLeft()), tool_pos)
            self.assertEqual(panel.appearance.isVisible(), mode == 3)
            self.assertEqual(panel.speed.isVisible(), mode == 1)
            self.assertEqual(panel.fade_in.isVisible(), mode in (0, 3))
        ruler_at_one_second = QPoint(round(track.x_for(200)), 8)
        QTest.mouseClick(track, Qt.LeftButton, pos=ruler_at_one_second)
        self.assertLess(abs(model.time_at(panel.playhead)-1), .01)
        self.assertEqual(panel.mode, 2)

    def test_split_captions_keep_independent_appearance_and_content(self):
        panel = self.panel
        panel.tabs[2].click()
        def text_dialog(message):
            def enter():
                dialog = self.app.activeModalWidget()
                dialog.caption.setPlainText(message)
                for field in ('camera_model','resolution','exposure','cine_frame'):
                    dialog.checks[field].setChecked(True)
                dialog.accept()
            QTimer.singleShot(0, enter)
            panel.add.click()
        text_dialog('Ignition')
        panel.set_text_placement(5, dict(anchor=(.2,.3), scale=.7))
        panel.set_position(45)
        panel.split.click()
        text_dialog('Hot plume')
        panel.set_text_placement(50, dict(anchor=(.8,.6), scale=1.6))
        blocks = panel.text_settings()['blocks']
        self.assertEqual([(b['first'],b['last'],b['custom'],b['anchor'],b['scale']) for b in blocks],
                         [(0,44,'Ignition',(.2,.3),.7), (45,119,'Hot plume',(.8,.6),1.6)])
        self.assertTrue({'resolution','exposure','camera_model','cine_frame'}.issubset(blocks[0]['fields']))
        panel.first.setValue(self.clip.source_frame(2))  # A handle cannot cross the adjacent block.
        self.assertEqual(panel.text_blocks[1]['first'],45)
        panel.last.setValue(self.clip.source_frame(105))
        QTest.mouseClick(self.track, Qt.LeftButton, pos=self.point(116.5))
        text_dialog('Cooling')
        self.assertEqual(panel.text_blocks[2]['position'], 'center')
        self.assertNotIn('anchor', panel.text_blocks[2])
        self.assertEqual((panel.text_blocks[2]['first'], panel.text_blocks[2]['last']), (106,119))
        panel.select_text_at(20)
        panel.delete.click()
        self.assertEqual([b['custom'] for b in panel.text_blocks], ['Hot plume','Cooling'])

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
