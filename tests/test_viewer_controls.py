import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / 'app-source'
    / 'modules'
    / 'trackmeasure'
)
sys.path.insert(0, str(MODULE_PATH))

from PySide6.QtWidgets import QApplication, QGraphicsPathItem

import simplemeas_ui


class ViewerControlsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.window = simplemeas_ui.MainWindow()

    def setUp(self):
        self.window.cine_path = '/tmp/test.cine'
        self.window.first_fr = -50
        self.window.last_fr = 50
        self.window.frame_slider.setRange(0, 100)
        for button in self.window.transport_buttons:
            button.setEnabled(True)
        self.window.clip_range.setEnabled(True)
        self.window.clip_range.setRange(0, 100)
        self.window.clip_range.setValues(20, 80)
        self.window.current_tool = self.window.viewer_tool
        self.window._path_point_radius_scale = 1.0

    def tearDown(self):
        self.window._stop_playback()

    def test_clip_range_orders_and_clamps_values(self):
        self.window.clip_range.setValues(120, -10)
        self.assertEqual(self.window.clip_range.lowerValue(), 0)
        self.assertEqual(self.window.clip_range.upperValue(), 100)

    def test_viewer_playback_uses_clip_bounds(self):
        self.assertEqual(self.window._playback_bounds(), (20, 80))
        self.window.frame_slider.setValue(90)
        self.window._toggle_playback(True)
        self.assertEqual(self.window.frame_slider.value(), 20)
        self.assertTrue(self.window.playback_timer.isActive())

        self.window.frame_slider.setValue(79)
        self.window._advance_playback()
        self.assertEqual(self.window.frame_slider.value(), 80)
        self.assertFalse(self.window.playback_timer.isActive())

    def test_rewind_and_fast_forward_stay_inside_clip(self):
        self.window.frame_slider.setValue(25)
        self.window._seek_by(-50)
        self.assertEqual(self.window.frame_slider.value(), 20)
        self.window._seek_by(500)
        self.assertEqual(self.window.frame_slider.value(), 80)

    def test_continuous_reverse_and_forward_speeds_stop_at_clip_bounds(self):
        self.window.frame_slider.setValue(24)
        self.window._start_playback(-4)
        self.assertEqual(self.window._playback_step, -4)
        self.assertTrue(self.window.reverse_faster_button.isChecked())
        self.assertFalse(self.window.forward_play_button.isChecked())
        self.window._advance_playback()
        self.assertEqual(self.window.frame_slider.value(), 20)
        self.assertFalse(self.window.playback_timer.isActive())
        self.assertTrue(self.window.pause_button.isChecked())

        self.window.frame_slider.setValue(77)
        self.window._start_playback(4)
        self.assertTrue(self.window.forward_faster_button.isChecked())
        self.window._advance_playback()
        self.assertEqual(self.window.frame_slider.value(), 80)
        self.assertFalse(self.window.playback_timer.isActive())

    def test_pcc_normal_play_reverse_and_pause_modes(self):
        self.window.frame_slider.setValue(50)
        self.window._start_playback(-1)
        self.assertTrue(self.window.reverse_play_button.isChecked())
        self.assertEqual(self.window._playback_step, -1)
        self.assertTrue(self.window.playback_timer.isActive())

        # The most recently pressed direction immediately replaces the old one.
        self.window._start_playback(1)
        self.assertTrue(self.window.forward_play_button.isChecked())
        self.assertFalse(self.window.reverse_play_button.isChecked())
        self.assertEqual(self.window._playback_step, 1)

        self.window._pause_playback()
        self.assertFalse(self.window.playback_timer.isActive())
        self.assertFalse(self.window._playback_active)
        self.assertTrue(self.window.pause_button.isChecked())

    def test_playback_timer_is_self_throttling_single_shot(self):
        self.assertTrue(self.window.playback_timer.isSingleShot())

        self.window.frame_slider.setValue(50)
        self.window._start_playback(1)
        self.assertTrue(self.window._playback_active)
        self.assertTrue(self.window.playback_timer.isActive())

        self.window._advance_playback()
        self.assertEqual(self.window.frame_slider.value(), 51)
        self.assertTrue(self.window._playback_active)
        self.assertTrue(self.window.playback_timer.isActive())

    def test_long_track_path_is_batched_into_two_scene_items(self):
        graph = self.window.graph
        graph.scene().clear()
        graph.xMax = 1920
        graph.yMax = 1080
        self.window._path_fade_enabled = False
        points = np.column_stack((
            np.linspace(0, 1919, 2000),
            np.linspace(0, 1079, 2000),
        ))

        self.window.on_draw_track_points('main_graph', points, '#ff0000', 0)

        scene_items = graph.scene().items()
        self.assertEqual(len(scene_items), 2)
        self.assertTrue(all(isinstance(item, QGraphicsPathItem) for item in scene_items))

    def test_path_settings_labels_and_point_radius_scale(self):
        self.assertEqual(self.window.path_fade_button.text(), 'Path Settings  ▾')
        layout = self.window.path_fade_panel.layout()
        self.assertEqual(
            layout.labelForField(self.window.path_fade_transparency).text(),
            'Transparency',
        )
        self.assertEqual(
            layout.labelForField(self.window.path_fade_radius).text(),
            'Transparency Distance',
        )
        self.assertEqual(
            layout.labelForField(self.window.path_point_radius).text(),
            'Point Radius',
        )

        graph = self.window.graph
        graph.xMax = 1920
        graph.yMax = 1080
        self.window._path_fade_enabled = False
        point = np.array([[100.0, 100.0]])

        graph.scene().clear()
        self.window._path_point_radius_scale = 1.0
        self.window.on_draw_track_points('main_graph', point, '#ff0000', 0)
        normal_width = graph.scene().items()[0].path().boundingRect().width()

        graph.scene().clear()
        self.window._path_point_radius_scale = 2.0
        self.window.on_draw_track_points('main_graph', point, '#ff0000', 0)
        large_width = graph.scene().items()[0].path().boundingRect().width()

        self.assertAlmostEqual(large_width, normal_width * 2.0)

    def test_pcc_frame_buttons_pause_and_move_exactly_one_frame(self):
        self.window.frame_slider.setValue(50)
        self.window._start_playback(4)
        self.window._step_one_frame(-1)
        self.assertEqual(self.window.frame_slider.value(), 49)
        self.assertFalse(self.window.playback_timer.isActive())
        self.assertTrue(self.window.pause_button.isChecked())

        self.window._step_one_frame(1)
        self.assertEqual(self.window.frame_slider.value(), 50)
        self.assertTrue(self.window.pause_button.isChecked())

    def test_pcc_cluster_has_exactly_seven_controls(self):
        self.assertEqual(len(self.window.transport_buttons), 7)
        self.assertEqual(
            self.window.transport_buttons,
            [
                self.window.reverse_play_button,
                self.window.pause_button,
                self.window.forward_play_button,
                self.window.reverse_faster_button,
                self.window.previous_frame_button,
                self.window.next_frame_button,
                self.window.forward_faster_button,
            ],
        )

    def test_tracking_help_button_is_available_beside_add_object(self):
        self.assertEqual(self.window.tracking_help_button.text(), '?')
        self.assertIn('tracking method', self.window.tracking_help_button.toolTip())
        self.assertEqual(self.window.hybrid_search_multiplier.value(), 1.5)

    def test_scrubber_labels_align_with_slider_column(self):
        self.assertEqual(self.window.scrubber_layout.spacing(), 16)
        self.assertIs(
            self.window.scrubber_layout.itemAt(0).widget(),
            self.window.pcc_transport_controls,
        )
        slider_column = self.window.scrubber_layout.itemAt(1).layout()
        self.assertIs(slider_column, self.window.slider_column_layout)
        self.assertIs(slider_column.itemAt(0).layout(), self.window.frame_range_layout)
        slider_row = slider_column.itemAt(1).layout()
        self.assertIs(slider_row, self.window.slider_row_layout)
        self.assertIs(slider_row.itemAt(0).widget(), self.window.frame_first_button)
        self.assertIs(slider_row.itemAt(1).widget(), self.window.frame_slider)
        self.assertIs(slider_row.itemAt(2).widget(), self.window.frame_last_button)
        self.assertIs(
            self.window.frame_range_layout.itemAt(0).widget(),
            self.window.first_frame_disp,
        )
        self.assertIs(
            self.window.frame_range_layout.itemAt(2).widget(),
            self.window.last_frame_disp,
        )

    def test_current_frame_label_stays_inside_slider_reserved_space(self):
        self.window.resize(1200, 800)
        simplemeas_ui.QWidget.show(self.window)
        self.app.processEvents()
        self.window.frame_slider.setValue(50)
        self.window.update_active_frame(50)

        slider_origin = self.window.frame_slider.mapTo(
            self.window, simplemeas_ui.QPoint(0, 0)
        )
        slider_bottom = slider_origin.y() + self.window.frame_slider.height()

        self.assertGreaterEqual(
            self.window.active_frame_label.y(), slider_origin.y()
        )
        self.assertLessEqual(
            self.window.active_frame_label.geometry().bottom(), slider_bottom
        )
        self.window.hide()

    def test_scrubber_endpoint_buttons_ignore_viewer_clip(self):
        self.window.frame_slider.setValue(50)
        self.window._jump_to_first_frame()
        self.assertEqual(self.window.frame_slider.value(), 0)
        self.window._jump_to_last_frame()
        self.assertEqual(self.window.frame_slider.value(), 100)

    def test_scrubber_endpoint_icons_are_white(self):
        first_image = self.window.frame_first_button.icon().pixmap(24, 24).toImage()
        last_image = self.window.frame_last_button.icon().pixmap(24, 24).toImage()
        self.assertGreater(first_image.pixelColor(6, 12).lightness(), 240)
        self.assertGreater(last_image.pixelColor(17, 12).lightness(), 240)

    def test_path_fade_uses_checked_objects_and_live_values(self):
        original_vm = self.window.vm
        try:
            self.window.vm = SimpleNamespace(
                active_frame=10,
                track_data={
                    0: {
                        'enabled': True,
                        'frames': np.array([10]),
                        'points': np.array([[100.0, 100.0]]),
                    },
                    1: {
                        'enabled': False,
                        'frames': np.array([10]),
                        'points': np.array([[500.0, 500.0]]),
                    },
                },
            )
            self.window._path_fade_enabled = True
            self.window._path_fade_transparency = 70
            self.window._path_fade_radius = 80
            centers = self.window._fade_reference_points()

            self.assertEqual(centers.tolist(), [[100.0, 100.0]])
            self.assertEqual(
                self.window._track_path_alpha((110.0, 100.0), 0, centers),
                77,
            )
            self.assertEqual(
                self.window._track_path_alpha((190.0, 100.0), 0, centers),
                255,
            )
            self.window._path_fade_transparency = 40
            self.window._path_fade_radius = 10
            self.assertEqual(
                self.window._track_path_alpha((110.0, 100.0), 0, centers),
                153,
            )
        finally:
            self.window.vm = original_vm

    def test_checked_object_previews_fill_two_columns(self):
        preview_window = simplemeas_ui.MainWindow()
        preview_window.setParent(self.window)
        preview_window.vm = SimpleNamespace(
            active_frame=0,
            track_data={
                object_id: {
                    'enabled': object_id != 3,
                    'name': f'Fixture {object_id}',
                    'frames': np.array([0]),
                    'points': np.array([[10.0 + object_id, 20.0]]),
                }
                for object_id in range(4)
            },
        )

        preview_window._sync_object_preview_grid()

        self.assertEqual(preview_window._preview_object_order, [0, 1, 2])
        self.assertIs(
            preview_window.preview_grid_layout.itemAtPosition(0, 0).widget(),
            preview_window._preview_tiles[0],
        )
        self.assertIs(
            preview_window.preview_grid_layout.itemAtPosition(0, 1).widget(),
            preview_window._preview_tiles[1],
        )
        self.assertIs(
            preview_window.preview_grid_layout.itemAtPosition(1, 0).widget(),
            preview_window._preview_tiles[2],
        )
        self.assertEqual(
            preview_window._preview_tiles[2].name_label.text(), 'Fixture 2'
        )
        preview_window.deleteLater()

    def test_right_details_panel_starts_open_and_cannot_collapse(self):
        self.window.main_tab.setVisible(True)
        self.window._ensure_details_panel_open(force=True)
        self.assertFalse(self.window.tab_splitter.isCollapsible(1))
        self.assertGreaterEqual(self.window.main_tab.minimumWidth(), 280)
        self.assertGreaterEqual(
            self.window.tab_splitter.sizes()[1],
            self.window.main_tab.minimumWidth(),
        )

    def test_measurement_tools_use_full_timeline(self):
        self.window.current_tool = self.window.two_pt_tool
        self.assertEqual(self.window._playback_bounds(), (0, 100))
        self.window.frame_slider.setValue(95)
        self.window._seek_by(10)
        self.assertEqual(self.window.frame_slider.value(), 100)

    def test_mark_in_out_and_reset(self):
        self.window.frame_slider.setValue(35)
        self.window._set_clip_in()
        self.assertEqual(self.window.clip_range.lowerValue(), 35)
        self.window.frame_slider.setValue(65)
        self.window._set_clip_out()
        self.assertEqual(self.window.clip_range.upperValue(), 65)
        self.window._reset_clip_range()
        self.assertEqual(
            (self.window.clip_range.lowerValue(), self.window.clip_range.upperValue()),
            (0, 100),
        )

    def test_viewer_hides_analysis_panels_and_restores_them(self):
        self.window._set_viewer_controls_visible(True)
        self.assertFalse(self.window.viewer_controls.isHidden())
        self.assertFalse(self.window.pcc_transport_controls.isHidden())
        self.assertTrue(self.window.status_toolbar.isHidden())
        self.assertTrue(self.window.main_tab.isHidden())

        self.window._set_viewer_controls_visible(False)
        self.assertTrue(self.window.viewer_controls.isHidden())
        self.assertFalse(self.window.pcc_transport_controls.isHidden())
        self.assertFalse(self.window.status_toolbar.isHidden())
        self.assertFalse(self.window.main_tab.isHidden())

    def test_autotrack_dialog_uses_the_right_clicked_object(self):
        class FakeAutoDialog:
            def __init__(self):
                self.start_frame = None
                self.end_frame = None
                self.refreshes = []
                self.shown = False
                self.raised = False
                self.activated = False

            def refresh_params(self, **kwargs):
                self.refreshes.append(kwargs)

            def set_start_frame(self, value):
                self.start_frame = value

            def set_end_frame(self, value):
                self.end_frame = value

            def showNormal(self):
                self.shown = True

            def raise_(self):
                self.raised = True

            def activateWindow(self):
                self.activated = True

        class FakeVM:
            track_type = 'Auto'
            active_object = 7
            track_data = {
                7: {
                    'start': 5,
                    'end': 60,
                    'tpl_rng': (19, 19),
                    'search_area': (45, 45),
                    'subpixel_size': '1.0 pix',
                    'subpixel_type': 'Cubic',
                    'frames_enable': True,
                    'search_area_enable': True,
                    'tpl_rng_enable': True,
                    'update_template_enable': False,
                    'acceptable_score': 0.5,
                    'tpl_score': 0.8,
                    'name': 'First object',
                },
                42: {
                    'start': 10,
                    'end': 70,
                    'tpl_rng': (21, 21),
                    'search_area': (51, 51),
                    'subpixel_size': '1.0 pix',
                    'subpixel_type': 'Cubic',
                    'frames_enable': True,
                    'search_area_enable': True,
                    'tpl_rng_enable': True,
                    'update_template_enable': False,
                    'acceptable_score': 0.5,
                    'tpl_score': 0.8,
                    'name': 'Right-click target',
                },
            }

        original_vm = getattr(self.window, 'vm', None)
        original_auto = self.window.auto
        original_set_point_table = self.window._set_point_table
        try:
            fake_auto = FakeAutoDialog()
            self.window.vm = FakeVM()
            self.window.auto = fake_auto
            self.window._set_point_table = lambda track_data, selected_id: None
            self.window.track_table.setRowCount(2)
            self.window.first_fr = -50

            self.window.launch_autotrack_dialog(1, 0)

            self.assertEqual(self.window.vm.active_object, 42)
            self.assertEqual(fake_auto.start_frame, -50)
            self.assertEqual(fake_auto.end_frame, 70)
            self.assertEqual(fake_auto.refreshes[-1]['name'], 'Right-click target')
            self.assertTrue(fake_auto.shown)
            self.assertTrue(fake_auto.raised)
            self.assertTrue(fake_auto.activated)

            # Reopen for a different object without recreating the dialog.
            self.window.launch_autotrack_dialog(0, 0)
            self.assertEqual(self.window.vm.active_object, 7)
            self.assertEqual(fake_auto.end_frame, 60)
            self.assertEqual(fake_auto.refreshes[-1]['name'], 'First object')
            self.assertEqual(len(fake_auto.refreshes), 2)
        finally:
            self.window.vm = original_vm
            self.window.auto = original_auto
            self.window._set_point_table = original_set_point_table


if __name__ == '__main__':
    unittest.main()
