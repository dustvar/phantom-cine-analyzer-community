"""Native-widget range selection and distinct stationary-anchor interaction."""
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from bos_frames import BOSFrames
from test_bos_ui import BOSWorkspaceTests


class BOSRangeWorkflowTests(BOSWorkspaceTests):
    # Reuse the real Cine fixture and Qt lifetime helpers, not its test cases.
    def prepare(self):
        self.wait(lambda: self.ready_at(0))
        self.panel.pin_reference()
        self.panel.set_roi([10, 10, 130, 72])
        self.wait(lambda: self.ready_at(0) and self.panel.last_result['field'] is not None)

    def test_range_marks_native_frames_rejects_reversal_and_freezes_settings(self):
        self.prepare()
        p = self.panel
        p.seek(2)
        self.wait(lambda: self.ready_at(2))
        QTest.mouseClick(p.analyze_range, Qt.MouseButton.LeftButton)
        self.assertTrue(p.range_bar.isVisible())
        self.assertEqual((p.range_bar.first.value(), p.range_bar.last.value()), (-8, -8))
        p.seek(1)
        self.wait(lambda: self.ready_at(1))
        QTest.mouseClick(p.range_bar.mark_in, Qt.MouseButton.LeftButton)
        p.seek(4)
        self.wait(lambda: self.ready_at(4))
        QTest.mouseClick(p.range_bar.mark_out, Qt.MouseButton.LeftButton)
        self.assertEqual(p.range_bar.indices(), (1, 4))
        self.assertIn('4 frames, both endpoints included', p.range_bar.summary.text())
        p.range_bar.first.setValue(-5)
        self.assertFalse(p.range_bar.analyze.isEnabled())
        self.assertEqual(p.range_bar.last.value(), -6)
        p.range_bar.first.setValue(-9)
        self.assertTrue(p.range_bar.analyze.isEnabled())
        p.fps.setValue(60)
        self.assertTrue(p.range_bar.analyze.isEnabled())
        launch = Mock()
        with patch.dict(sys.modules, {'bos_preview_ui': SimpleNamespace(analyze_bos_range=launch)}):
            QTest.mouseClick(p.range_bar.analyze, Qt.MouseButton.LeftButton)
        launch.assert_called_once()
        parent, snapshot, first, last = launch.call_args.args
        self.assertIs(parent, p)
        self.assertEqual((first, last), (1, 4))
        self.assertEqual(snapshot['playback_fps'], 60)
        self.assertEqual(snapshot, p.settings())
        p.state['roi'][0] = 11
        self.assertEqual(snapshot['roi'][0], 10)
        QTest.mouseClick(p.range_bar.cancel, Qt.MouseButton.LeftButton)
        self.assertFalse(p.range_bar.isVisible())
        self.assertFalse(p.analyze_range.isChecked())

    def test_pending_preview_cannot_start_a_stale_analysis(self):
        self.prepare()
        p = self.panel
        p.analyze_range.setChecked(True)
        original = BOSFrames.render
        def slow_render(session, state):
            time.sleep(.08)
            return original(session, state)
        launch = Mock()
        with patch.object(BOSFrames, 'render', slow_render), patch.dict(
                sys.modules, {'bos_preview_ui': SimpleNamespace(analyze_bos_range=launch)}):
            p.seek(3)
            self.assertFalse(p.range_bar.analyze.isEnabled())
            self.wait(lambda: p.busy())
            p._analyze_selected_range(0, 3)
            launch.assert_not_called()
            self.wait(lambda: self.ready_at(3))
            self.assertTrue(p.range_bar.analyze.isEnabled())

    def test_anchor_rectangle_is_independent_and_escape_or_clear_is_safe(self):
        self.prepare()
        p = self.panel
        original_roi = p.settings()['roi']
        rect = p.view.image_rect()
        def point(x, y):
            return QPoint(round(rect.left()+x*rect.width()/160), round(rect.top()+y*rect.height()/96))
        QTest.mouseClick(p.choose_anchor, Qt.MouseButton.LeftButton)
        QTest.mousePress(p.view, Qt.MouseButton.LeftButton, pos=point(20, 20))
        QTest.mouseMove(p.view, point(40, 40))
        QTest.mouseRelease(p.view, Qt.MouseButton.LeftButton, pos=point(40, 40))
        self.assertTrue(p.view.selecting_anchor)
        self.assertIsNone(p.state['anchor_roi'])
        QTest.keyClick(p.view, Qt.Key.Key_Escape)
        self.assertFalse(p.view.selecting_anchor)
        self.assertFalse(p.choose_anchor.isChecked())
        self.assertEqual(p.settings()['roi'], original_roi)
        QTest.mouseClick(p.choose_anchor, Qt.MouseButton.LeftButton)
        QTest.mousePress(p.view, Qt.MouseButton.LeftButton, pos=point(145, 91))
        QTest.mouseMove(p.view, point(45, 5))
        QTest.mouseRelease(p.view, Qt.MouseButton.LeftButton, pos=point(45, 5))
        self.wait(lambda: self.ready_at(0))
        self.assertTrue(p.stabilize.isChecked())
        self.assertFalse(p.view.selecting_anchor)
        self.assertEqual(p.settings()['roi'], original_roi)
        self.assertGreaterEqual(min(p.state['anchor_roi'][2:]), 80)
        self.assertIn('Yellow patch', p.anchor_label.text())
        p.seek(2)
        self.wait(lambda: self.ready_at(2))
        self.assertIsNotNone(p.last_result['display_field'])
        p._probe(p.view._pixel(point(65, 50)))
        self.assertIn('Raw Δx', p.probe_label.text())
        self.assertIn('Stabilized Δx', p.probe_label.text())
        QTest.mouseClick(p.clear_anchor, Qt.MouseButton.LeftButton)
        self.wait(lambda: self.ready_at(2))
        self.assertFalse(p.stabilize.isChecked())
        self.assertIsNone(p.settings()['anchor_roi'])
        self.assertEqual(p.settings()['roi'], original_roi)

    def test_measured_live_rate_exposes_slow_calculation_and_shutdown_cancels_preparation(self):
        self.prepare()
        p = self.panel
        original = BOSFrames.render
        def slow_render(session, state):
            time.sleep(.09)
            return original(session, state)
        p.fps.setValue(120)
        with patch.object(BOSFrames, 'render', slow_render):
            p.toggle_play()
            self.wait(lambda: not p.playing and self.ready_at(5))
        self.assertIsNotNone(p._preview_fps)
        self.assertLess(p._preview_fps, 120)
        self.assertGreater(p._preview_fps, 0)
        self.assertIn('Live ', p.preview_rate.text())
        self.assertIn('Requested 120 fps', p.preview_rate.toolTip())
        worker = SimpleNamespace(isRunning=lambda: True, cancel=threading.Event())
        p._bos_preview_worker = worker
        self.assertTrue(p.busy())
        self.assertFalse(p.shutdown())
        self.assertTrue(worker.cancel.is_set())
        p._bos_preview_worker = None

    def test_finished_worker_sentinel_protects_modal_dialog_until_it_unwinds(self):
        self.prepare()
        p = self.panel
        finished = SimpleNamespace(isRunning=lambda: False, cancel=threading.Event())
        p._bos_preview_worker = finished
        self.assertTrue(p.busy())
        self.assertFalse(p.shutdown())
        self.assertFalse(finished.cancel.is_set())
        p._bos_preview_worker = None
        self.assertFalse(p.busy())
        p._bos_export_worker = finished
        self.assertFalse(p.shutdown())
        p._bos_export_worker = None
        self.assertTrue(p.shutdown())


# Importing a unittest class collects it too; keep only the shared fixture
# methods so this file does not repeat the existing UI test cases.
for name in list(vars(BOSWorkspaceTests)):
    if name.startswith('test_'):
        setattr(BOSRangeWorkflowTests, name, None)
del BOSWorkspaceTests
