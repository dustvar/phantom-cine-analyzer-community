from PySide6.QtCore import QCoreApplication, QEvent
"""Exercise workspace changes with real Qt signals, tools and image scenes."""
import os
import gc
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from copy import deepcopy
from types import SimpleNamespace

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app-source/modules/trackmeasure'))

from PySide6.QtCore import Qt, QThread, QPointF, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsPixmapItem

import measmodel
import simplemeas_ui
import simplemeas_vm
from simplemeas_tools import ScaleTool, TrackTool
from test_multi_cine_workspace import FakeConfig, FakeCineHandler, FakeReportHandler
from viewer_timeline import split_at
from viewer_transition_ui import TransitionDialog
from PySide6.QtWidgets import QDialog, QDialogButtonBox


class ImageCineHandler(FakeCineHandler):
    def load_cine(self, path):
        super().load_cine(path)
        self.metadata = type('Metadata', (), dict(
            FirstImageNo=-10, ImageCount=20 if 'one' in path else 8,
            TriggerTime_displ=0.0, Bright=0, Contrast=1.0, Gamma=1.0,
            RealBPP=16, CFA='Mono', FrameRate=1000, ImWidth=320, ImHeight=240,
        ))()

    def get_img(self, index=0):
        return np.tile(np.arange(320, dtype=np.uint16) * 200, (240, 1))

    def print_metadata(self):
        return []

    def get_timestamp(self, index):
        return {'time_from_trig': index * 1000, 'time_from_start': index * 1000}


class WorkspaceInteractionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.signal_errors = []
        hook = patch.object(sys, 'excepthook', lambda *error: self.signal_errors.append(error))
        hook.start()
        self.addCleanup(hook.stop)
        self.window = simplemeas_ui.MainWindow()
        report = FakeReportHandler()
        report.update_active_report = lambda kind: None
        self.vm = simplemeas_vm.SimpleMeasVM(
            self.window, measmodel.MeasModel(), report, FakeConfig(),
            ImageCineHandler(), {},
        )
        self.vm._apply_image_tools = lambda img: img
        self.window.vm = self.vm
        self.window.connect_events()
        self.handler_patch = patch.object(simplemeas_vm, 'CineHandler', ImageCineHandler)
        self.handler_patch.start()
        self.addCleanup(self.handler_patch.stop)
        self.vm.load_cine_workspace_cb(['/tmp/one.cine', '/tmp/two.cine', '/tmp/three.cine', '/tmp/four.cine'])
        self.window.show()
        self.window.timeline_toggle.setChecked(True)  # These tests exercise the expanded editor.
        self.app.processEvents()

    def tearDown(self):
        self.window._stop_playback()
        self.vm.close_workspace()
        self.window.hide()
        self.window.deleteLater(); QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.app.processEvents()
        del self.window, self.vm
        gc.collect()
        self.assertEqual(self.signal_errors, [], f'Exceptions in Qt signals: {self.signal_errors}')

    def click(self, button):
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def assert_images_visible(self):
        for pane in self.window.workspace_panes:
            if pane.isHidden():
                continue
            images = [item for item in pane.graph.scene().items() if isinstance(item, QGraphicsPixmapItem)]
            self.assertEqual(len(images), 1, f'Cine {pane.pane_index}: {pane.graph.scene().items()}')
            self.assertFalse(images[0].pixmap().isNull())
            rect = pane.graph.mapFromScene(images[0].sceneBoundingRect()).boundingRect()
            self.assertGreater(rect.width(), 30)
            self.assertGreater(rect.height(), 30)
            self.assertTrue(rect.intersects(pane.graph.viewport().rect()))

    def test_calibration_keeps_all_images_visible(self):
        self.assert_images_visible()
        self.click(self.window.scale_tool)
        self.assertIsInstance(self.vm.active_tool, ScaleTool)
        self.assert_images_visible()
        self.vm.graph_click_cb((20, 30))
        self.app.processEvents()
        self.assert_images_visible()
        with patch.object(simplemeas_ui.ScaleDialog, 'launch', return_value=(10, 'mm')):
            self.vm.graph_click_cb((120, 30))
        self.assertAlmostEqual(self.vm.image_scale, 0.1)
        self.assert_images_visible()

    def test_switching_cines_preserves_analysis_mode_and_frame(self):
        # Enter Track without starting the interactive Add Object dialog.
        with patch.object(self.window, 'choose_tracking_method', return_value=None):
            self.click(self.window.track_tool)
        self.window.frame_slider.setValue(16)
        self.window.activate_cine_pane(1)
        self.app.processEvents()
        self.assertIs(self.window.current_tool, self.window.track_tool)
        self.assertEqual(self.window.main_tab.currentIndex(), 2)
        self.assertIsInstance(self.vm.active_tool, TrackTool)
        self.assertEqual(self.vm.active_frame, 0)
        self.assertEqual(self.window.frame_slider.value(), 0)
        self.window.frame_slider.setValue(4)
        self.window.activate_cine_pane(0)
        self.assertEqual(self.vm.active_frame, 16)
        self.assertEqual(self.window.frame_slider.value(), 16)
        self.assert_images_visible()

    def test_switching_cines_preserves_adjustments(self):
        self.vm.image_brightness = 17
        self.vm.image_contrast = 1.23
        self.vm.image_gamma = 1.45
        self.vm.raw_enabled = True
        self.vm.toggle_ftone = False
        self.window.activate_cine_pane(1)
        self.window.activate_cine_pane(0)
        self.assertEqual(self.vm.image_brightness, 17)
        self.assertEqual(self.vm.image_contrast, 1.23)
        self.assertEqual(self.vm.image_gamma, 1.45)
        self.assertTrue(self.vm.raw_enabled)
        self.assertFalse(self.vm.toggle_ftone)

    def test_hide_restore_reflows_without_losing_cine_state(self):
        with patch.object(self.window, 'choose_tracking_method', return_value=None):
            self.click(self.window.track_tool)
        self.window.frame_slider.setValue(13)
        self.vm.image_scale = 2.5
        handler = self.vm.cine_handler
        self.vm.active_tool.track_select = True
        self.vm.active_tool.collect_points((25, 30), 13)
        self.vm.new_track_data.emit(self.vm.track_data, self.vm.active_object)
        track = self.vm.track_data[0]
        for index in (0, 2, 3):
            self.click(self.window.workspace_panes[index].hide_button)
        self.assertEqual(self.window.hidden_cine_indices, {0, 2, 3})
        self.assertEqual(self.window.active_pane_index, 1)
        self.assertEqual(self.window.workspace_grid_layout.count(), 1)
        self.assertGreater(self.window.workspace_panes[1].width(), self.window.workspace_grid_widget.width() - 10)
        self.assertFalse(handler.closed)
        self.assertFalse(self.window.workspace_panes[1].hide_button.isEnabled())
        self.click(self.window.workspace_restore_buttons[0])
        self.assertEqual(self.window.workspace_grid_layout.count(), 2)
        self.assertEqual(self.vm.active_frame, 13)
        self.assertEqual(self.vm.image_scale, 2.5)
        self.assertIs(self.vm.track_data[0], track)
        self.assertIs(self.window.current_tool, self.window.track_tool)
        self.assertEqual(self.window.main_tab.currentIndex(), 2)
        self.assert_images_visible()
        for index in (2, 3):
            self.click(self.window.workspace_restore_buttons[index])
        self.assertEqual(self.window.workspace_grid_layout.count(), 4)
        self.assertTrue(self.window.hidden_cines_bar.isHidden())
        self.assert_images_visible()

    def test_calibration_after_pan_and_zoom_accepts_points(self):
        with patch.object(self.window, 'choose_tracking_method', return_value=None):
            self.click(self.window.track_tool)
        self.window.graph.zoom_at(self.window.graph.viewport().rect().center(), 2)
        self.window._set_pan_mode(True)
        self.click(self.window.scale_tool)
        self.assertFalse(self.window._pan_mode_enabled)
        position = self.window.graph.mapFromScene(QPointF(150, 120))
        QTest.mouseClick(self.window.graph.viewport(), Qt.MouseButton.LeftButton, pos=position)
        self.app.processEvents()
        self.assertEqual(len(self.vm.active_tool.points), 1)
        self.assert_images_visible()

    def test_calibration_mouse_clicks_survive_dialog_and_repeated_redraw(self):
        self.click(self.window.scale_tool)
        for point in (QPointF(80, 120), QPointF(240, 120)):
            position = self.window.graph.mapFromScene(point)
            with patch.object(simplemeas_ui.ScaleDialog, 'launch', return_value=(10, 'mm')):
                QTest.mouseClick(self.window.graph.viewport(), Qt.MouseButton.LeftButton, pos=position)
                self.app.processEvents()
            self.assert_images_visible()
        self.assertEqual(self.vm.length_units, 'mm')
        for frame in (3, 6, 0):
            self.window.frame_slider.setValue(frame)
            self.vm.redraw_cb()
            self.app.processEvents()
            self.assert_images_visible()

    def test_calibration_line_follows_cursor_and_disappears_on_second_click(self):
        self.click(self.window.scale_tool)
        graph = self.window.graph
        first = graph.mapFromScene(QPointF(40, 80))
        second = graph.mapFromScene(QPointF(200, 120))
        QTest.mouseClick(graph.viewport(), Qt.MouseButton.LeftButton, pos=first)
        self.app.processEvents()
        QTest.mouseMove(graph.viewport(), second)
        self.app.processEvents()
        preview = graph._calibration_preview
        self.assertIsNotNone(preview)
        self.assertEqual(preview.pen().color().name(), '#ff3b3b')
        self.assertLess(abs(preview.line().p2().x() - 200), 2)
        self.assertLess(abs(preview.line().p2().y() - 120), 2)
        self.assert_images_visible()
        def finish(parent):
            self.assertIsNone(graph._calibration_preview)
            return (1, 'in')
        with patch.object(simplemeas_ui.ScaleDialog, 'launch', side_effect=finish):
            QTest.mouseClick(graph.viewport(), Qt.MouseButton.LeftButton, pos=second)
            self.app.processEvents()
        self.assertIsNone(graph._calibration_preview)
        self.assertIsNone(self.vm.active_tool)
        self.assert_images_visible()

    def test_viewer_levels_stay_with_cine_and_leave_tracking_buffer_unchanged(self):
        self.click(self.window.levels_button)
        self.assertFalse(self.window.grade_panel.isHidden())
        original = self.vm.transformed_img.copy()
        self.window.grade_panel.controls['gamma'].setValue(1.8)
        self.window._redraw_viewer_grade()
        self.assertEqual(self.vm.viewer_grade['gamma'], 1.8)
        np.testing.assert_array_equal(self.vm.transformed_img, original)
        self.window.activate_cine_pane(1)
        self.assertEqual(self.window.grade_panel.controls['gamma'].value(), 1)
        self.window.grade_panel.controls['gamma'].setValue(.6)
        self.window.activate_cine_pane(0)
        self.assertEqual(self.window.grade_panel.controls['gamma'].value(), 1.8)
        self.assertEqual(self.vm.workspace_contexts[1]['viewer_grade']['gamma'], .6)
        self.click(self.window.scale_tool)
        self.assertTrue(self.window.grade_panel.isHidden())
        self.click(self.window.viewer_tool)
        self.assertFalse(self.window.grade_panel.isHidden())
        self.assertEqual(self.vm.viewer_grade['gamma'], 1.8)
        self.assert_images_visible()

    def test_curve_drag_and_reset_update_selected_cine(self):
        self.click(self.window.levels_button)
        curve = self.window.grade_panel.curve
        start = curve.handle_position(2).toPoint()
        end = start + simplemeas_ui.QPoint(0, -30)
        QTest.mousePress(curve, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(curve, end)
        QTest.mouseRelease(curve, Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        self.assertGreater(self.vm.viewer_grade['curve'][2], .5)
        self.assertEqual(self.vm.workspace_contexts[1]['viewer_grade']['curve'][2], .5)
        self.click(self.window.grade_panel.reset_button)
        self.assertEqual(self.vm.viewer_grade['curve'], [0, .25, .5, .75, 1])

    def test_preview_format_is_independent_of_active_raw_mode(self):
        self.vm.raw_enabled = True
        rgb = np.zeros((40, 60, 3), dtype=np.uint8)
        rgb[:, :, 0] = 255
        graph = self.window.workspace_panes[1].graph
        self.window._draw_image_to_graph(graph, rgb, 8, 'RGB')
        image = graph.scene().items()[0].pixmap().toImage()
        pixel = image.pixelColor(20, 20)
        self.assertEqual((pixel.red(), pixel.green(), pixel.blue()), (255, 0, 0))

    def test_toolbar_split_is_undoable_without_opening_editor(self):
        self.assertTrue(self.window.editor_panel.isHidden())
        original = deepcopy(self.vm.viewer_edit)
        self.window.frame_slider.setValue(7)
        self.click(self.window.split_button)
        self.assertEqual([s['start'] for s in self.vm.viewer_edit['sections']], [0, 7])
        self.assertTrue(self.window.editor_panel.isHidden())
        self.click(self.window.timeline_undo_button)
        self.assertEqual(self.vm.viewer_edit, original)
        self.click(self.window.timeline_redo_button)
        self.assertEqual([s['start'] for s in self.vm.viewer_edit['sections']], [0, 7])
        self.assert_images_visible()

    def test_timeline_pan_only_appears_when_zoomed_and_does_not_edit_footage(self):
        original = deepcopy(self.vm.viewer_edit)
        self.window.frame_slider.setValue(7)
        bar = self.window.timeline_viewport.horizontalScrollBar()
        self.assertFalse(bar.isVisible())
        self.click(self.window.timeline_zoom_in)
        self.assertTrue(bar.isVisible())
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum())
        self.click(self.window.timeline_fit)
        self.assertFalse(bar.isVisible())
        self.assertEqual(bar.value(), 0)
        self.assertEqual(self.vm.viewer_edit, original)
        self.assertEqual(self.vm.active_frame, 7)

    def test_split_rate_transition_skip_and_undo_redo(self):
        self.click(self.window.timeline_button)
        self.window.frame_slider.setValue(7)
        self.click(self.window.editor_panel.split_button)
        self.assertEqual(len(self.vm.viewer_edit['sections']),2)
        self.window.editor_panel.rate.setValue(15)
        def configure(dialog):
            dialog.duration.setValue(.1)
            return QDialog.DialogCode.Accepted
        with patch.object(TransitionDialog, 'exec', configure):
            self.click(self.window.editor_panel.transition_button)
        self.assertEqual(self.vm.viewer_edit['sections'][1]['fps'],15)
        self.assertEqual(self.vm.viewer_edit['sections'][1]['ramp'],.1)
        self.window.editor_panel.ensureWidgetVisible(self.window.editor_panel.keep)
        self.app.processEvents()
        QTest.mouseClick(self.window.editor_panel.keep, Qt.MouseButton.LeftButton,
                         pos=simplemeas_ui.QPoint(8, self.window.editor_panel.keep.height() // 2))
        self.app.processEvents()
        self.assertFalse(self.vm.viewer_edit['sections'][1]['keep'])
        self.window.editor_panel.ensureWidgetVisible(self.window.editor_panel.undo_button)
        self.click(self.window.editor_panel.undo_button)
        self.assertTrue(self.vm.viewer_edit['sections'][1]['keep'])
        self.click(self.window.editor_panel.redo_button)
        self.assertFalse(self.vm.viewer_edit['sections'][1]['keep'])
        self.assert_images_visible()

    def test_timeline_and_fps_settings_survive_cine_switch_and_hidden_pane(self):
        edit=split_at(self.vm.viewer_edit,8)
        edit['sections'][0]['fps']=90
        edit['overlay'].update(enabled=True,x=.7,y=.8,size=5)
        self.window._apply_viewer_edit(edit)
        self.window.activate_cine_pane(1)
        self.assertEqual(len(self.vm.viewer_edit['sections']),1)
        self.assertFalse(self.vm.viewer_edit['overlay']['enabled'])
        self.window.hide_cine_pane(0)
        self.click(self.window.workspace_restore_buttons[0])
        self.assertEqual(self.vm.viewer_edit,edit)
        self.assertTrue(self.vm._viewer_edit_undo)
        self.assert_images_visible()

    def test_edited_playback_uses_elapsed_time_and_loops_at_the_edited_end(self):
        edit=split_at(self.vm.viewer_edit,6)
        edit['sections'][0]['fps']=60
        edit['sections'][1].update(fps=15,ramp=.1)
        self.window._apply_viewer_edit(edit)
        plan=self.window._viewer_timeline()
        self.window._start_playback(1)
        self.window._viewer_clock=SimpleNamespace(elapsed=lambda:200)
        self.window._advance_playback()
        self.assertEqual(self.vm.active_frame,plan.sample(.2)[0])
        self.window._viewer_clock=SimpleNamespace(elapsed=lambda:100000)
        self.window._advance_playback()
        self.assertFalse(self.window._playback_active)
        edit=deepcopy(self.vm.viewer_edit);edit['loop']=True
        self.window._apply_viewer_edit(edit)
        self.window._timeline_seek(0)
        self.window._start_playback(1)
        elapsed=round((plan.output_duration+.2)*1000)
        self.window._viewer_clock=SimpleNamespace(elapsed=lambda:elapsed)
        self.window._advance_playback()
        self.assertTrue(self.window._playback_active)
        tick=int(((elapsed/1000)%plan.output_duration)*plan.output_fps+1e-8)
        self.assertEqual(self.vm.active_frame,plan.sample(tick/plan.output_fps)[0])

    def test_preview_stops_on_the_last_exported_frame_at_fast_fractional_end(self):
        edit=deepcopy(self.vm.viewer_edit);edit['sections'][0]['fps']=90
        self.window._apply_viewer_edit(edit)
        plan=self.window._viewer_timeline()
        self.window._timeline_seek(0);self.window._start_playback(1)
        self.window._viewer_clock=SimpleNamespace(elapsed=lambda:10000)
        self.window._advance_playback()
        self.assertFalse(self.window._playback_active)
        self.assertEqual(self.vm.active_frame,list(plan.samples())[-1][0])
        self.window._start_playback(1)
        self.assertTrue(self.window._playback_active)
        self.assertEqual(self.vm.active_frame,plan.sample(0)[0])

    def test_analysis_playback_ignores_editor_speeds(self):
        edit=deepcopy(self.vm.viewer_edit);edit['sections'][0]['fps']=900
        self.window._apply_viewer_edit(edit)
        self.click(self.window.scale_tool)
        self.window.frame_slider.setValue(0)
        self.window._start_playback(1);self.window._advance_playback()
        self.assertEqual(self.vm.active_frame,1)

    def test_fps_visualizer_drag_moves_text_as_one_undoable_edit(self):
        for index in (1,2,3):self.window.hide_cine_pane(index)
        self.click(self.window.fps_button)
        self.assertTrue(self.vm.viewer_edit['overlay']['enabled'])
        graph=self.window.graph
        self.assertIsNotNone(graph._fps_rect)
        before=deepcopy(self.vm.viewer_edit)
        undo_count=len(self.vm._viewer_edit_undo)
        rect=graph._fps_rect
        start=graph.mapFromScene(rect.center())
        finish=graph.mapFromScene(rect.center()+QPointF(50,100))
        QTest.mousePress(graph.viewport(),Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(graph.viewport(),finish)
        QTest.mouseRelease(graph.viewport(),Qt.MouseButton.LeftButton,pos=finish)
        self.app.processEvents()
        self.assertGreater(self.vm.viewer_edit['overlay']['y'],before['overlay']['y'])
        self.assertEqual(len(self.vm._viewer_edit_undo),undo_count+1)
        self.window._undo_viewer_edit()
        self.assertEqual(self.vm.viewer_edit,before)
        self.assert_images_visible()

    def test_all_skipped_allows_original_frames_only_and_disables_playback(self):
        edit=deepcopy(self.vm.viewer_edit);edit['sections'][0]['keep']=False
        self.window._apply_viewer_edit(edit)
        self.assertTrue(self.window.export_button.isEnabled())
        from video_export_ui import ExportDialog
        dialog = ExportDialog(self.window, self.vm.cine_path, 0, 7, self.vm.cine_handler.metadata, edit)
        self.assertEqual(dialog.format.currentData(), 'original')
        self.assertTrue(dialog.choose.isEnabled())
        for key in ('.mp4', 'rendered'):
            dialog.format.setCurrentIndex(dialog.format.findData(key))
            self.assertFalse(dialog.choose.isEnabled())
        dialog.close()
        self.window._start_playback(1)
        self.assertFalse(self.window._playback_active)

    def test_loading_new_workspace_resets_edits_and_undo_history(self):
        self.window._apply_viewer_edit(split_at(self.vm.viewer_edit,5))
        self.vm.load_cine_workspace_cb(['/tmp/two.cine'])
        self.assertEqual(len(self.vm.viewer_edit['sections']),1)
        self.assertEqual(self.vm.viewer_edit['sections'][0]['end'],7)
        self.assertEqual(self.vm._viewer_edit_undo,[])

    def test_failed_preview_does_not_change_active_context(self):
        self.vm.active_frame = 12
        handler = self.vm.cine_handler
        other_handler = self.vm.workspace_contexts[1]['cine_handler']
        self.vm.workspace_contexts[1]['_processed_cache'].clear()
        with patch.object(other_handler, 'get_img', side_effect=ValueError('bad frame')):
            with self.assertRaisesRegex(ValueError, 'bad frame'):
                self.vm.workspace_frame(1)
        self.assertEqual(self.vm.active_cine_index, 0)
        self.assertEqual(self.vm.active_frame, 12)
        self.assertIs(self.vm.cine_handler, handler)

    def test_reloading_workspace_resets_clip_bounds_and_visibility(self):
        self.window.clip_range.setValues(2, 6)
        self.window.hide_cine_pane(2)
        self.vm.load_cine_workspace_cb(['/tmp/one.cine', '/tmp/two.cine'])
        self.app.processEvents()
        self.assertEqual(self.window.hidden_cine_indices, set())
        self.assertEqual(self.window.workspace_clip_ranges, {0: (0, 19), 1: (0, 7)})
        self.assertEqual(self.window.workspace_grid_layout.count(), 2)

    def test_workspace_load_commits_on_ui_thread(self):
        commits = []
        original = self.vm.install_cine_workspace
        def commit(contexts):
            commits.append(QThread.currentThread() == self.app.thread())
            return original(contexts)
        with patch.object(self.vm, 'install_cine_workspace', side_effect=commit):
            self.window._choose_image(['/tmp/two.cine', '/tmp/one.cine'])
            for _ in range(200):
                self.app.processEvents()
                if self.window.load_cine_thread is None:
                    break
                QTest.qWait(5)
        self.assertIsNone(self.window.load_cine_thread)
        self.assertEqual(commits, [True])
        self.assertTrue(self.window.isEnabled())
        self.assertEqual(self.vm.cine_path, '/tmp/two.cine')
        self.assert_images_visible()

    def test_failed_load_closes_partial_handlers_and_keeps_workspace(self):
        original_contexts = self.vm.workspace_contexts
        original_handler = self.vm.cine_handler
        created = []
        class FailingHandler(ImageCineHandler):
            def __init__(self):
                super().__init__()
                created.append(self)
            def load_cine(self, path):
                super().load_cine(path)
                if 'bad' in path:
                    raise ValueError('invalid Cine')
        with patch.object(simplemeas_vm, 'CineHandler', FailingHandler):
            with self.assertLogs(level='ERROR'):
                self.assertFalse(self.vm.load_cine_workspace_cb(['/tmp/one.cine', '/tmp/bad.cine']))
        self.assertIs(self.vm.workspace_contexts, original_contexts)
        self.assertIs(self.vm.cine_handler, original_handler)
        self.assertFalse(original_handler.closed)
        self.assertTrue(all(handler.closed for handler in created))

    def test_clicking_cut_icon_opens_transition_editor_and_applies_one_undo_step(self):
        self.window.frame_slider.setValue(7)
        self.window._split_viewer_section()
        original=deepcopy(self.vm.viewer_edit);undo_count=len(self.vm._viewer_edit_undo)
        button=self.window.section_timeline.transition_buttons[1]
        self.assertTrue(button.isVisible())
        called=[]
        def configure(dialog):
            called.append((dialog.left_index,dialog.right_index))
            dialog.show();self.app.processEvents()
            dialog.placement.setCurrentIndex(dialog.placement.findData('center'))
            dialog.curve.setCurrentIndex(dialog.curve.findData('linear'))
            dialog.duration.setValue(.1)
            dialog.from_fps.setValue(60);dialog.to_fps.setValue(10)
            self.assertIn('0.05 s before cut',dialog.summary.text())
            self.click(dialog.buttons.button(QDialogButtonBox.StandardButton.Apply))
            return dialog.result()
        with patch.object(TransitionDialog,'exec',configure):self.click(button)
        self.assertEqual(called,[(0,1)])
        self.assertEqual(self.vm.viewer_edit['sections'][1]['ramp_place'],'center')
        self.assertEqual(self.vm.viewer_edit['sections'][1]['ramp_curve'],'linear')
        self.assertEqual(self.vm.viewer_edit['sections'][0]['fps'],60)
        self.assertEqual(self.vm.viewer_edit['sections'][1]['fps'],10)
        self.assertEqual(len(self.vm._viewer_edit_undo),undo_count+1)
        self.window._undo_viewer_edit();self.assertEqual(self.vm.viewer_edit,original)
        self.assert_images_visible()

    def test_transition_cancel_and_remove_leave_section_footage_intact(self):
        edit=split_at(self.vm.viewer_edit,7)
        edit['sections'][1].update(fps=10,ramp=.1,ramp_place='left')
        self.window._apply_viewer_edit(edit)
        def cancel(dialog):
            dialog.duration.setValue(10);dialog.placement.setCurrentIndex(2)
            dialog.cut_frame.setValue(5)
            return QDialog.DialogCode.Rejected
        with patch.object(TransitionDialog,'exec',cancel):self.window._edit_viewer_transition(1)
        self.assertEqual(self.vm.viewer_edit,edit)
        def remove(dialog):
            dialog.show();self.app.processEvents();self.click(dialog.remove_button)
            return dialog.result()
        with patch.object(TransitionDialog,'exec',remove):self.window._edit_viewer_transition(1)
        self.assertEqual(self.vm.viewer_edit['sections'][1]['ramp'],0)
        self.assertEqual([(s['start'],s['end']) for s in self.vm.viewer_edit['sections']],[(0,6),(7,19)])
        self.window._undo_viewer_edit();self.assertEqual(self.vm.viewer_edit,edit)

    def test_transition_cut_slider_and_typed_frame_use_cine_numbering(self):
        self.window._apply_viewer_edit(split_at(self.vm.viewer_edit,7))
        original = deepcopy(self.vm.viewer_edit); undo_count = len(self.vm._viewer_edit_undo)
        def configure(dialog):
            dialog.show(); self.app.processEvents()
            self.assertEqual(dialog.cut_frame.value(), -3)
            self.assertEqual((dialog.cut_frame.minimum(),dialog.cut_frame.maximum()), (-9,9))
            dialog.cut_slider.setValue(2)
            self.assertEqual(dialog.cut_frame.value(), 2)
            self.assertEqual(dialog.result_edit()['sections'][1]['start'], 12)
            dialog.cut_frame.lineEdit().selectAll()
            QTest.keyClicks(dialog.cut_frame.lineEdit(), '-1')
            QTest.keyClick(dialog.cut_frame, Qt.Key.Key_Tab)
            self.assertEqual(dialog.cut_slider.value(), -1)
            self.click(dialog.buttons.button(QDialogButtonBox.StandardButton.Apply))
            return dialog.result()
        with patch.object(TransitionDialog, 'exec', configure):
            self.click(self.window.section_timeline.transition_buttons[1])
        self.assertEqual([(s['start'],s['end']) for s in self.vm.viewer_edit['sections']],[(0,8),(9,19)])
        self.assertEqual(len(self.vm._viewer_edit_undo), undo_count+1)
        self.click(self.window.timeline_undo_button)
        self.assertEqual(self.vm.viewer_edit, original)
        self.click(self.window.timeline_redo_button)
        self.assertEqual(self.vm.viewer_edit['sections'][1]['start'], 9)

    def test_drag_cut_previews_live_and_commits_one_undo_without_opening_dialog(self):
        self.window._apply_viewer_edit(split_at(self.vm.viewer_edit,7))
        self.app.processEvents()
        original = deepcopy(self.vm.viewer_edit); undo_count = len(self.vm._viewer_edit_undo)
        timeline = self.window.section_timeline
        button = timeline.transition_buttons[1]
        self.window._start_viewer_edit_playback(1)
        with patch.object(TransitionDialog, 'exec', return_value=QDialog.DialogCode.Rejected) as dialog:
            QTest.mousePress(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
            for frame in (10,14,12):
                target = QPoint(round(timeline._frame_x(frame)), button.geometry().center().y())
                QTest.mouseMove(button, button.mapFrom(timeline,target))
                self.app.processEvents()
                self.assertEqual(timeline.edit['sections'][1]['start'], frame)
                self.assertEqual(self.vm.viewer_edit, original)
            self.assertFalse(self.window._playback_active)
            QTest.mouseRelease(button, Qt.MouseButton.LeftButton, pos=button.mapFrom(timeline,target))
            self.app.processEvents()
            dialog.assert_not_called()
        self.assertEqual(self.vm.viewer_edit['sections'][1]['start'], 12)
        self.assertEqual(len(self.vm._viewer_edit_undo), undo_count+1)
        self.assertTrue(self.window.timeline_undo_button.isEnabled())
        self.assertFalse(self.window.timeline_redo_button.isEnabled())
        self.click(self.window.timeline_undo_button); self.assertEqual(self.vm.viewer_edit, original)
        self.assertTrue(self.window.timeline_redo_button.isEnabled())
        self.click(self.window.timeline_redo_button)
        self.assertEqual(self.vm.viewer_edit['sections'][1]['start'], 12)
        self.assert_images_visible()

    def test_escape_cancels_cut_drag_without_an_edit_or_dialog(self):
        self.window._apply_viewer_edit(split_at(self.vm.viewer_edit,7))
        self.app.processEvents()
        original = deepcopy(self.vm.viewer_edit); undo_count = len(self.vm._viewer_edit_undo)
        timeline = self.window.section_timeline; button = timeline.transition_buttons[1]
        target = QPoint(round(timeline._frame_x(12)), button.geometry().center().y())
        with patch.object(TransitionDialog,'exec',return_value=QDialog.DialogCode.Rejected) as dialog:
            QTest.mousePress(button,Qt.MouseButton.LeftButton,pos=button.rect().center())
            QTest.mouseMove(button,button.mapFrom(timeline,target))
            QTest.keyClick(button,Qt.Key.Key_Escape)
            QTest.mouseRelease(button,Qt.MouseButton.LeftButton,pos=button.mapFrom(timeline,target))
            self.app.processEvents(); dialog.assert_not_called()
        self.assertEqual(self.vm.viewer_edit,original)
        self.assertEqual(timeline.edit,original)
        self.assertEqual(len(self.vm._viewer_edit_undo),undo_count)

    def test_data_checkboxes_control_the_card_and_undo_restore(self):
        self.click(self.window.fps_button)
        self.assertEqual(self.window.fps_button.text(),'Data && Text')
        for key,check in self.window.editor_panel.data_fields.items():check.setChecked(False)
        self.app.processEvents()
        self.assertEqual(self.vm.viewer_edit['overlay']['fields'],[])
        self.assertIsNone(self.window.graph._fps_rect)
        self.window.editor_panel.data_fields['capture'].setChecked(True)
        self.assertEqual(self.vm.viewer_edit['overlay']['fields'],['capture'])
        self.assertIsNotNone(self.window.graph._fps_rect)
        self.window._undo_viewer_edit();self.assertEqual(self.vm.viewer_edit['overlay']['fields'],[])
        self.assert_images_visible()

    def test_data_layout_buttons_and_decimal_checkbox_are_undoable_and_per_cine(self):
        self.click(self.window.fps_button)
        panel=self.window.editor_panel
        self.assertTrue(panel.layout_buttons['stacked'].isChecked())
        self.assertFalse(panel.overlay_decimals.isChecked())
        self.click(panel.layout_buttons['straight'])
        self.assertEqual(self.vm.viewer_edit['overlay']['layout'],'straight')
        self.assertFalse(panel.layout_buttons['stacked'].isChecked())
        panel.overlay_decimals.setChecked(True)
        self.assertTrue(self.vm.viewer_edit['overlay']['decimals'])
        self.window._undo_viewer_edit()
        self.assertFalse(panel.overlay_decimals.isChecked())
        self.window._redo_viewer_edit()
        self.assertTrue(panel.overlay_decimals.isChecked())
        self.window.activate_cine_pane(1)
        self.assertTrue(panel.layout_buttons['stacked'].isChecked())
        self.assertFalse(panel.overlay_decimals.isChecked())
        self.window.activate_cine_pane(0)
        self.assertTrue(panel.layout_buttons['straight'].isChecked())
        self.assertTrue(panel.overlay_decimals.isChecked())
        self.click(panel.layout_buttons['stacked'])
        self.assertEqual(self.vm.viewer_edit['overlay']['layout'],'stacked')
        self.assert_images_visible()

    def test_camera_data_selector_enables_independent_controls_and_both_boxes(self):
        self.click(self.window.fps_button)
        panel=self.window.editor_panel
        self.assertEqual(panel.overlay_enabled.text(),'Enable')
        self.assertEqual(panel.data_box_buttons['playback'].text(),'Playback Data')
        self.assertEqual(panel.data_box_buttons['camera'].text(),'Camera Data')
        self.assertFalse(panel.data_fields['capture'].isChecked())
        playback=deepcopy(self.vm.viewer_edit['overlay'])
        self.click(panel.data_box_buttons['camera'])
        self.assertEqual(panel.active_data_box,'camera')
        self.assertTrue(panel.overlay_enabled.isChecked())
        self.assertTrue(panel.data_fields['capture'].isChecked())
        self.assertTrue(panel.data_fields['model'].isVisible())
        self.assertTrue(panel.data_fields['resolution'].isVisible())
        self.assertFalse(panel.data_fields['slowdown'].isVisible())
        self.assertEqual(set(self.window.graph._data_rects),{'camera','playback'})
        self.click(panel.layout_buttons['straight']);panel.overlay_decimals.setChecked(True)
        panel.overlay_color.setCurrentIndex(1)
        self.assertEqual(self.vm.viewer_edit['overlay'],playback)
        self.assertEqual(self.vm.viewer_edit['camera_overlay']['layout'],'straight')
        panel.overlay_enabled.setChecked(False)
        self.assertEqual(set(self.window.graph._data_rects),{'playback'})
        self.click(panel.data_box_buttons['playback'])
        self.assertFalse(panel.overlay_decimals.isChecked())
        self.assertTrue(panel.layout_buttons['stacked'].isChecked())
        self.assertEqual(panel.overlay_color.currentIndex(),0)
        self.assert_images_visible()

    def test_each_card_can_be_dragged_without_moving_the_other(self):
        for index in (1,2,3):self.window.hide_cine_pane(index)
        self.click(self.window.fps_button)
        self.click(self.window.editor_panel.data_box_buttons['camera'])
        for box,key,other,dx in [('camera','camera_overlay','overlay',70),('playback','overlay','camera_overlay',-70)]:
            graph=self.window.graph;before=deepcopy(self.vm.viewer_edit)
            count=len(self.vm._viewer_edit_undo)
            rect=graph._data_rects[box]
            start=graph.mapFromScene(rect.center())
            finish=graph.mapFromScene(rect.center()+QPointF(dx,-70))
            QTest.mousePress(graph.viewport(),Qt.MouseButton.LeftButton,pos=start)
            QTest.mouseMove(graph.viewport(),finish)
            QTest.mouseRelease(graph.viewport(),Qt.MouseButton.LeftButton,pos=finish)
            QTest.qWait(60);self.app.processEvents()
            self.assertEqual(self.window.editor_panel.active_data_box,box)
            self.assertLess(self.vm.viewer_edit[key]['y'],before[key]['y'])
            self.assertEqual(self.vm.viewer_edit[other],before[other])
            self.assertEqual(len(self.vm._viewer_edit_undo),count+1)
            self.window._undo_viewer_edit();self.assertEqual(self.vm.viewer_edit,before)
        self.assert_images_visible()

    def test_camera_card_survives_cine_switch_hidden_pane_and_timeline_reset(self):
        self.click(self.window.fps_button)
        self.click(self.window.editor_panel.data_box_buttons['camera'])
        self.window.editor_panel.overlay_x.setValue(25)
        self.window.editor_panel.data_fields['model'].setChecked(False)
        camera=deepcopy(self.vm.viewer_edit['camera_overlay'])
        self.window._apply_viewer_edit(split_at(self.vm.viewer_edit,7))
        self.window._reset_viewer_edit()
        self.assertEqual(self.vm.viewer_edit['camera_overlay'],camera)
        self.window.activate_cine_pane(1)
        self.assertFalse(self.vm.viewer_edit['camera_overlay']['enabled'])
        self.window.hide_cine_pane(0)
        self.click(self.window.workspace_restore_buttons[0])
        self.assertEqual(self.vm.viewer_edit['camera_overlay'],camera)
        self.assertFalse(self.window.editor_panel.data_fields['model'].isChecked())
        self.assertEqual(self.window.editor_panel.overlay_x.value(),25)
        self.assert_images_visible()


if __name__ == '__main__':
    unittest.main()
