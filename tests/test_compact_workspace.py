from PySide6.QtCore import QCoreApplication, QEvent
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSpinBox, QScrollArea, QWidget, QVBoxLayout, QLabel
import simplemeas_ui, simplemeas_vm, measmodel, reportmodel
from cine_metadata import CineHandler
from test_cine_metadata import write_cine
from test_multi_cine_workspace import FakeConfig
from compact_workspace import ReferenceDialog
from search_region_ui import SearchRegionItem
from tracking_setup_ui import CLASSIC, EDGE


class CompactWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.source = self.root/'test.cine'; write_cine(self.source, frames=40, width=320, height=240)
        self.errors = []
        self.hook = patch('sys.excepthook', lambda *error: self.errors.append(error)); self.hook.start()
        self.w = simplemeas_ui.MainWindow()
        self.w.vm = simplemeas_vm.SimpleMeasVM(self.w, measmodel.MeasModel(), reportmodel.ReportHandler(),
            FakeConfig(), CineHandler(), {})
        self.w.connect_events(); self.w.initialize_theme(); self.w.resize(1280, 820); self.w.show()
        self.assertTrue(self.w.vm.load_cine_workspace_cb([str(self.source)]))
        self.app.processEvents()

    def tearDown(self):
        self.w._stop_playback(); self.w.overlay_panel.shutdown(); self.w.comparator_panel.shutdown()
        self.w.vm.close_workspace(); self.w.hide(); self.w.deleteLater(); QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete); self.app.processEvents()
        self.hook.stop(); self.temp.cleanup()
        if self.errors:
            import traceback
            self.fail(''.join(''.join(traceback.format_exception(*error)) for error in self.errors))

    def track(self):
        self.w.track_tool.click(); self.w.track_type_man.click()
        self.w.vm.active_tool.track_select = True
        self.w.vm.active_tool.collect_points((120, 100), 0)
        track = self.w.vm.track_data[0]
        frames = np.arange(20)
        track.update(frames=frames, points=np.column_stack((120+frames, 100+2*frames)),
                     angles=np.zeros(20), scores=np.full(20, .99))
        self.w.vm.track_fig_calculations(obj=track)
        self.w.vm.new_track_data.emit(self.w.vm.track_data, 0); self.w.vm.redraw_cb()
        self.app.processEvents()
        return track

    def test_navigation_drawers_and_header_leave_video_unobstructed(self):
        w = self.w
        self.assertTrue(w.main_tab.isHidden())
        self.assertNotIn('Camera Comparator', [a.text() for a in w.mode_actions.values()])
        self.assertIn('Video Overlay', [a.text() for a in w.mode_actions.values()])
        w.track_tool.click(); self.app.processEvents()
        self.assertTrue(w.main_tab.isHidden())
        self.assertFalse(w.track_pages.isTabVisible(0))
        self.assertIs(w.viewer_overlay_controls.parentWidget(), w.workspace_panes[0])
        self.assertLess(w.viewer_overlay_controls.geometry().bottom(), w.graph.geometry().top())
        w.details_buttons[2].click(); self.app.processEvents()
        self.assertTrue(w.main_tab.isVisible()); self.assertLessEqual(w.main_tab.width(), 390)
        w.details_buttons[2].click(); self.assertTrue(w.main_tab.isHidden())

    def test_first_add_object_opens_chooser_once_and_edit_reopens_setup(self):
        w = self.w; w.track_tool.click()
        self.assertFalse(w.add_object_button.isChecked())
        with patch.object(w, 'choose_tracking_method', return_value=EDGE) as choose:
            w.add_object_button.click()
        choose.assert_called_once()
        self.assertEqual(w.vm.pending_tracking_method, EDGE)
        self.assertTrue(w.tracking_guide.isVisible()); self.assertTrue(w.main_tab.isHidden())
        w._guide_target_chosen(QPointF(150, 120))
        self.assertEqual(len(w.vm.track_data), 1)
        w._cancel_tracking_setup()
        self.assertTrue(w.main_tab.isHidden()); self.assertFalse(w.track_pages.isTabVisible(0))
        self.assertEqual(len(w.vm.track_data),0)
        w._start_tracking_method(EDGE);w._guide_target_chosen(QPointF(150,120));w._guide_step(1);w._guide_step(1)
        with patch.object(w,'process_autotrack'):w._complete_guided_tracking()
        w.edit_object_button.click()
        self.assertTrue(w.tracking_guide.isVisible());self.assertTrue(w.main_tab.isHidden())

    def test_fixed_and_frame_references_update_xy_heat_maps_and_survive_job(self):
        track = self.track(); w = self.w
        original_speed = track['Speed'].copy()
        w._apply_tracking_reference(0, 'pixel', point=(130, 120))
        np.testing.assert_allclose(track['X-Displacement'], np.arange(20)-10)
        np.testing.assert_allclose(track['Y-Displacement'], 2*(np.arange(20)-10))
        np.testing.assert_allclose(track['Speed'], original_speed)
        for mode in ('X-Displacement', 'Y-Displacement'):
            w.fig_dropdown.setCurrentText(mode); w.path_heat_map.setChecked(True)
            values = w._heat_map_values_for_track(0, 20)
            self.assertEqual(values[0], 0); self.assertEqual(values[-1], 1)
        w.fig_dropdown.setCurrentText('X-Speed')
        self.assertEqual(w.path_metric.currentIndex(), -1)
        w.path_metric.setCurrentText('Y-Displacement')
        self.assertEqual(w.fig_dropdown.currentText(), 'Y-Displacement')
        job = self.root/'ref.pcajob'; w.save_job_path(job)
        w.open_job_path(job)
        self.assertEqual(tuple(w.vm.track_data[0]['reference_pixel']), (130, 120))
        dialog = ReferenceDialog(w, 0)
        dialog.mode.setCurrentIndex(1); dialog.frame.setValue(-5); dialog.apply()
        self.assertEqual(w.vm.track_data[0]['origin_frame'], 5)
        self.assertIsNone(w.vm.track_data[0]['reference_pixel'])
        self.assertEqual(w.vm.track_data[0]['X-Displacement'][5], 0)
        dialog.close()

    def test_reference_pick_is_image_coordinate_and_does_not_add_a_track(self):
        self.track(); w = self.w
        w._open_reference_editor(); dialog = w._reference_dialog
        dialog.mode.setCurrentIndex(2); dialog._pick()
        point = QPointF(200, 140)
        self.assertTrue(w._consume_reference_pick(w.graph, point)); self.app.processEvents()
        dialog.apply()
        self.assertEqual(w.vm.track_data[0]['reference_pixel'], (200, 140))
        self.assertEqual(len(w.vm.track_data), 1)
        dialog.close()

    def test_search_window_drag_changes_search_not_measurement_or_template(self):
        track = self.track(); w = self.w
        original = track['points'].copy(); template = track['tpl_rng']
        w.vm.track_type = 'Auto'; track['tracking_method'] = EDGE
        w._commit_search_region(0, (135, 110), (151, 121))
        self.assertEqual(track['search_area'], (151, 121))
        self.assertEqual(track['search_offset'], (15, 10))
        self.assertTrue(track['search_area_manual'])
        w._sync_tracking_setup()
        w.tracking_setup.controls['acceptable_score'].setValue(.7)
        self.assertEqual(track['search_area'], (151, 121))
        np.testing.assert_array_equal(track['points'], original)
        self.assertEqual(track['tpl_rng'], template)
        w.vm.redraw_cb()
        self.assertTrue(any(isinstance(item, SearchRegionItem) for item in w.graph.scene().items()))

    def test_transport_fps_controls_analysis_preview_and_viewer_segment(self):
        w = self.w; w.track_tool.click(); w.playback_fps.setValue(12.5)
        self.assertEqual(w._playback_interval_ms, 80)
        self.assertEqual(w.vm.cine_handler.metadata.FrameRate, 2000)
        w.viewer_tool.click(); w.playback_fps.setValue(7.5)
        self.assertEqual(w.vm.viewer_edit['sections'][0]['fps'], 7.5)
        self.assertTrue(w.vm._viewer_edit_undo)
        w._undo_viewer_edit()
        self.assertEqual(w.playback_fps.value(), 30)

    def test_heat_map_keeps_missing_reference_measurements_unavailable(self):
        track = self.track(); w = self.w
        track['X-Displacement'][4] = np.nan
        w.fig_dropdown.setCurrentText('X-Displacement'); w.path_heat_map.setChecked(True)
        values = w._heat_map_values_for_track(0, 20)
        self.assertTrue(np.isnan(values[4]))
        self.assertEqual(w._heat_map_color(values[4]).name(), '#8994a3')

    def test_search_window_edges_and_corners_accept_actual_mouse_drags(self):
        w = self.w; w.track_tool.click()
        with patch.object(w, 'choose_tracking_method', return_value=EDGE):
            w.add_object_button.click()
        w._guide_target_chosen(QPointF(150, 120))
        w._collapse_details(); w._commit_search_region(0, (150, 120), (91, 81))
        self.app.processEvents()
        track = w.vm.track_data[0]; original = track['points'].copy()
        def search():
            return next(item for item in w.graph.scene().items() if isinstance(item, SearchRegionItem))
        def drag(start, end):
            viewport = w.graph.viewport()
            QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, w.graph.mapFromScene(start))
            QTest.mouseMove(viewport, w.graph.mapFromScene(end), 20)
            QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, w.graph.mapFromScene(end))
            self.app.processEvents()
        item = search()
        start = item.mapToScene(QPointF(item.rect().right(), 0))
        drag(start, start+QPointF(10, 5))
        np.testing.assert_allclose(track['search_offset'], (10, 5), atol=1)
        item = search(); old_width = track['search_area'][0]
        start = item.mapToScene(item.rect().bottomRight())
        drag(start, start+QPointF(10, 10))
        self.assertGreater(track['search_area'][0], old_width+10)
        np.testing.assert_array_equal(track['points'], original)

    def test_opening_older_job_recalculates_stored_motion_and_rejects_bad_reference(self):
        track = self.track(); w = self.w
        expected = track['Speed'].copy()
        track['Speed'][:] = 0
        path = self.root/'older.pcajob'; w.save_job_path(path); w.open_job_path(path)
        np.testing.assert_allclose(w.vm.track_data[0]['Speed'], expected)
        from workspace_job import read_job, save_job
        payload = read_job(path)
        payload['contexts'][0]['fields']['track_data'][0]['reference_pixel'] = (-1, 200)
        save_job(path, payload)
        current = w.vm.cine_handler
        with self.assertRaisesRegex(ValueError, 'reference pixel'): w.open_job_path(path)
        self.assertIs(w.vm.cine_handler, current)

    def test_hover_wheel_scrolls_form_and_only_explicit_focus_edits_values(self):
        area = QScrollArea(self.w); content = QWidget(); layout = QVBoxLayout(content)
        spin = QSpinBox(); spin.setRange(0, 100); spin.setValue(50); layout.addWidget(spin)
        for i in range(20): layout.addWidget(QLabel('Other setting'))
        area.setWidget(content); area.resize(220, 120); area.show(); self.app.processEvents()
        self.w.graph.setFocus(); self.app.processEvents()
        def wheel(delta):
            pos = QPointF(spin.rect().center())
            event = QWheelEvent(pos, QPointF(spin.mapToGlobal(pos.toPoint())), QPoint(), QPoint(0, delta),
                                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            QApplication.sendEvent(spin, event)
        wheel(-120)
        self.assertEqual(spin.value(), 50)
        self.assertGreater(area.verticalScrollBar().value(), 0)
        area.verticalScrollBar().setValue(0)
        QTest.mouseClick(spin.lineEdit(), Qt.LeftButton); wheel(120)
        self.assertGreater(spin.value(), 50)
        area.close()


if __name__ == '__main__': unittest.main()
