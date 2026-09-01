import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / 'app-source'
    / 'modules'
    / 'trackmeasure'
)
sys.path.insert(0, str(MODULE_PATH))

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from autotrackalgorithms import AutoTrackAlgorithms
from simplemeas_tools import (
    AutoTrackTool,
    CLASSIC_TRACKING_METHOD,
    HYBRID_TRACKING_METHOD,
    TrackTool,
)
import simplemeas_ui
import simplemeas_vm


class SignalSink:
    def __init__(self):
        self.values = []

    def emit(self, *args):
        self.values.append(args)


class TrackingCreationWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _new_object(method):
        vm = SimpleNamespace(
            track_data={},
            active_object=None,
            pending_tracking_method=method,
            cine_handler=SimpleNamespace(metadata=SimpleNamespace(
                FirstImageNo=-20,
                ImageCount=100,
            )),
            update_status_text=SignalSink(),
        )
        tool = TrackTool(vm)
        tool.track_select = True
        tool.draw_all = lambda *args, **kwargs: None
        tool.collect_points((25, 30), 7)
        return vm.track_data[0]

    def test_classic_choice_keeps_original_intensity_defaults(self):
        track = self._new_object(CLASSIC_TRACKING_METHOD)
        self.assertEqual(track['tracking_method'], CLASSIC_TRACKING_METHOD)
        self.assertEqual(track['tpl_rng'], (31, 31))
        self.assertEqual(track['search_area'], (101, 101))
        self.assertFalse(track['smart_frames'])
        self.assertEqual(track['points'].tolist(), [[25, 30]])

    def test_hybrid_choice_enables_smart_geometry_defaults(self):
        track = self._new_object(HYBRID_TRACKING_METHOD)
        self.assertEqual(track['tracking_method'], HYBRID_TRACKING_METHOD)
        self.assertTrue(track['smart_frames'])
        self.assertTrue(track['rotation_allowed'])
        self.assertEqual(track['rotation_range'], 180.0)
        self.assertEqual(track['subpixel_size'], '1/10 pix')
        self.assertEqual(track['edge_threshold'], 0.30)
        self.assertEqual(track['position_precision'], 0.1)
        self.assertEqual(track['angle_precision'], 0.1)
        self.assertEqual(track['acceptable_score'], 0.9)
        self.assertEqual(track['search_area_multiplier'], 1.5)
        self.assertEqual(track['scores'][0], 'N/A')
        self.assertEqual(list(track['hybrid_candidates']), [7])

    def test_add_object_chooses_hybrid_before_the_canvas_click(self):
        calls = []
        active_tool = SimpleNamespace(track_select=False)

        def arm_new_object():
            calls.append('arm')
            active_tool.track_select = True

        dummy = SimpleNamespace(
            vm=SimpleNamespace(
                active_tool=active_tool,
                add_new_template_cb=arm_new_object,
                redraw_cb=lambda: calls.append('redraw'),
            ),
            graph=object(),
            should_prompt_for_tracking_method=lambda: active_tool.track_select,
            choose_tracking_method=lambda: HYBRID_TRACKING_METHOD,
            begin_classic_point_selection=lambda graph: calls.append('classic'),
            begin_hybrid_region_selection=lambda graph: calls.append('hybrid'),
            _cancel_new_object_creation=lambda: calls.append('cancel'),
        )

        simplemeas_ui.MainWindow._on_add_object_clicked(dummy, True)

        self.assertEqual(calls[:2], ['arm', 'hybrid'])
        self.assertNotIn('classic', calls)
        self.assertNotIn('cancel', calls)

    def test_tracking_help_explains_first_time_hybrid_setup(self):
        help_text = simplemeas_ui.TrackingHelpDialog.HELP_HTML
        self.assertIn('exact point you want reported', help_text)
        self.assertIn('1.5×', help_text)
        self.assertIn('Avoid smooth areas', help_text)
        self.assertIn('Process Smart', help_text)

    def test_live_threshold_rebuilds_hybrid_series_without_rerun(self):
        track = {
            'tracking_method': HYBRID_TRACKING_METHOD,
            'acceptable_score': 0.90,
            'smart_frames': True,
            'frame_number_offset': -50,
            'start': -50,
            'end': -47,
            'frames': np.array([0]),
            'points': np.array([[10.0, 20.0]]),
            'scores': np.array(['N/A'], dtype=object),
            'angles': np.array([0.0]),
            'notes': {},
            'hybrid_candidates': {
                0: {'point': (10.0, 20.0), 'score': 'N/A', 'angle': 0.0},
                1: {'point': (11.0, 21.0), 'score': 0.92, 'angle': 1.0},
                2: {'point': (12.0, 22.0), 'score': 0.85, 'angle': 2.0},
                3: {'point': (13.0, 23.0), 'score': 0.97, 'angle': 3.0},
            },
        }

        TrackTool.apply_hybrid_threshold(track)
        self.assertEqual(track['frames'].tolist(), [0, 1, 3])
        self.assertEqual(track['points'].tolist(), [[10.0, 20.0], [11.0, 21.0], [13.0, 23.0]])
        self.assertEqual((track['start'], track['end']), (-50, -47))

        track['acceptable_score'] = 0.80
        TrackTool.apply_hybrid_threshold(track)
        self.assertEqual(track['frames'].tolist(), [0, 1, 2, 3])

        track['acceptable_score'] = 0.95
        TrackTool.apply_hybrid_threshold(track)
        self.assertEqual(track['frames'].tolist(), [0, 3])
        self.assertEqual((track['start'], track['end']), (-50, -47))
        self.assertEqual(len(track['hybrid_candidates']), 4)

    def test_empty_track_list_rearms_method_choice_without_add_button_click(self):
        track_tool = object()
        dummy = SimpleNamespace(
            current_tool=track_tool,
            track_tool=track_tool,
            _hybrid_selection_graph=None,
            vm=SimpleNamespace(
                track_type='Auto',
                active_tool=SimpleNamespace(track_select=False),
                track_data={},
            ),
        )

        self.assertTrue(simplemeas_ui.MainWindow.should_prompt_for_tracking_method(dummy))

    def test_remove_all_points_rearms_creation_and_clears_hybrid_state(self):
        redraws = []
        canvas_redraws = []

        class CheckedButton:
            checked = False

            def setChecked(self, checked):
                self.checked = bool(checked)

        class Viewport:
            def update(self):
                redraws.append('viewport')

        dummy = SimpleNamespace(
            vm=SimpleNamespace(
                active_object=0,
                track_data={0: {'relative_to': None}},
                pending_tracking_method=HYBRID_TRACKING_METHOD,
                active_tool=SimpleNamespace(track_select=False),
                update_status_text=SignalSink(),
                redraw_cb=lambda: redraws.append('cine'),
            ),
            _hybrid_selection_graph=object(),
            add_object_button=CheckedButton(),
            track_canvas=SimpleNamespace(
                redraw=lambda data: canvas_redraws.append(dict(data))
            ),
            graph=SimpleNamespace(viewport=lambda: Viewport()),
        )

        def refresh_track_keys():
            dummy.vm.active_object = None

        dummy.refresh_track_keys = refresh_track_keys

        simplemeas_ui.MainWindow.remove_track_data_points(dummy, set(), 'all')
        self.app.processEvents()

        self.assertEqual(dummy.vm.track_data, {})
        self.assertIsNone(dummy._hybrid_selection_graph)
        self.assertIsNone(dummy.vm.pending_tracking_method)
        self.assertTrue(dummy.vm.active_tool.track_select)
        self.assertTrue(dummy.add_object_button.checked)
        self.assertEqual(canvas_redraws, [{}])
        self.assertGreaterEqual(redraws.count('cine'), 2)

    def test_hybrid_advanced_dialog_keeps_setup_geometry_frozen(self):
        dialog = simplemeas_ui.AutoTrackDialog()
        dialog.update_template_enable.setChecked(True)

        dialog.on_tracking_method_changed(HYBRID_TRACKING_METHOD)

        self.assertFalse(dialog.update_template_enable.isChecked())
        self.assertFalse(dialog.update_template_enable.isEnabled())
        self.assertIn('setup-frame geometry fixed', dialog.update_template_enable.toolTip())

        dialog.on_tracking_method_changed(CLASSIC_TRACKING_METHOD)
        self.assertTrue(dialog.update_template_enable.isEnabled())

    def test_hybrid_region_exposes_scale_and_rotation_handles(self):
        commits = []
        parent = SimpleNamespace(commit_hybrid_region=lambda item: commits.append(item))
        item = simplemeas_ui.HybridRegionItem(
            QRectF(0, 0, 41, 31),
            (50, 60),
            8.0,
            3,
            parent,
            editable=True,
        )
        self.assertIsNotNone(item.scale_handle)
        self.assertIsNotNone(item.rotate_handle)
        self.assertEqual(item.rotation(), -8.0)
        item.setRotation(12.0)
        item.commit_changes()
        self.assertEqual(commits, [item])

    def test_hybrid_region_move_does_not_move_selected_tracking_point(self):
        track = {
            'tracking_method': HYBRID_TRACKING_METHOD,
            'points': np.array([[40.0, 50.0]]),
            'frames': np.array([3]),
            'angles': np.array([0.0]),
            't_points': np.array([[40.0, 50.0]]),
            't_frames': np.array([3]),
            't_angles': np.array([0.0]),
            'tpl_rng': (31, 31),
            'search_area_multiplier': 3.0,
        }
        redraws = []
        dummy = SimpleNamespace(
            vm=SimpleNamespace(
                track_data={0: track},
                cine_handler=SimpleNamespace(metadata=SimpleNamespace(
                    ImWidth=200, ImHeight=160,
                )),
                new_track_data=SignalSink(),
                redraw_cb=lambda: redraws.append(True),
            ),
            _odd_dimension=simplemeas_ui.MainWindow._odd_dimension,
            _rotate_tracking_offset=simplemeas_ui.MainWindow._rotate_tracking_offset,
            _hybrid_search_dimensions=lambda size, multiplier, rotation_allowed=True: (
                157, 157
            ),
        )
        item = simplemeas_ui.HybridRegionItem(
            QRectF(0, 0, 41, 31), (62, 58), 0.0, 0, dummy, editable=True
        )
        item.setRotation(-25.0)

        simplemeas_ui.MainWindow.commit_hybrid_region(dummy, item)
        self.app.processEvents()

        self.assertEqual(track['points'].tolist(), [[40.0, 50.0]])
        self.assertEqual(track['t_points'].tolist(), [[40.0, 50.0]])
        self.assertAlmostEqual(track['angles'][0], 25.0)
        expected_offset = simplemeas_ui.MainWindow._rotate_tracking_offset(
            (22.0, 8.0), -25.0
        )
        np.testing.assert_allclose(track['template_offset'], expected_offset)

    def test_rotating_hybrid_uses_diagonal_sized_search_window(self):
        dummy = SimpleNamespace(
            vm=SimpleNamespace(cine_handler=SimpleNamespace(metadata=SimpleNamespace(
                ImWidth=200, ImHeight=160,
            ))),
            _odd_dimension=simplemeas_ui.MainWindow._odd_dimension,
        )

        size = simplemeas_ui.MainWindow._hybrid_search_dimensions(
            dummy, (41, 31), 3.0, True
        )

        self.assertEqual(size, (157, 157))

    def test_hybrid_template_view_receives_pose_for_circular_preview(self):
        emitted = []
        dummy = SimpleNamespace(
            active_object=None,
            active_frame=3,
            _template='qt_template_img',
            track_data={0: {
                'tracking_method': HYBRID_TRACKING_METHOD,
                'frames': np.array([1, 3]),
                'points': np.array([[20.0, 25.0], [42.0, 47.0]]),
                'angles': np.array([12.0, 73.5]),
            }},
            _draw_frame=lambda graph, **kwargs: emitted.append((graph, kwargs)),
        )

        simplemeas_vm.SimpleMeasVM._refresh_template(dummy, 0)

        self.assertEqual(emitted[0][0], 'qt_template_img')
        self.assertEqual(emitted[0][1]['index'], 3)
        self.assertEqual(
            emitted[0][1]['current_point'],
            {'point': (42, 47), 'angle': 73.5},
        )

    def test_hybrid_preview_is_circular_and_preserves_16_bit_crop(self):
        source = np.tile(
            np.linspace(1000, 50000, 220, dtype=np.uint16),
            (180, 1),
        )
        graph = simplemeas_ui.QGraphicsView()
        graph.setScene(simplemeas_ui.QGraphicsScene(graph))
        dummy = SimpleNamespace(
            workspace_panes=[],
            zoom_levels=[1.0],
            zoom_slider=SimpleNamespace(value=lambda: 0),
            vm=SimpleNamespace(raw_enabled=False),
            magnifier=None,
            theme_accent='#f47efa',
        )
        dummy._decorate_hybrid_preview = (
            lambda pixmap, angle:
                simplemeas_ui.MainWindow._decorate_hybrid_preview(
                    dummy, pixmap, angle
                )
        )

        simplemeas_ui.MainWindow._draw_image_to_graph(
            dummy,
            graph,
            source,
            16,
            'Mono',
            {'point': (110, 90), 'angle': 37.5},
        )

        items = graph.scene().items()
        self.assertEqual(len(items), 1)
        preview = items[0].pixmap().toImage().convertToFormat(
            simplemeas_ui.QImage.Format.Format_RGBA8888
        )
        self.assertEqual(preview.width(), simplemeas_ui.MINIGRAPH_SIZE)
        self.assertEqual(preview.height(), simplemeas_ui.MINIGRAPH_SIZE)
        self.assertEqual(preview.pixelColor(0, 0).alpha(), 0)
        self.assertGreater(
            preview.pixelColor(
                preview.width() // 2, preview.height() // 2
            ).alpha(),
            0,
        )

    def test_canvas_routes_second_hybrid_click_to_exact_point_creation(self):
        created = []

        class Parent:
            current_tool = None
            status_bar = QLabel('')
            vm = SimpleNamespace(
                update_mouse_pos_cb=lambda *args, **kwargs: None,
                graph_mouse_motion_cb=lambda *args, **kwargs: None,
                update_status_text=SignalSink(),
            )

            def is_hybrid_region_selection_active(self, graph):
                return True

            def create_hybrid_track_at(self, point):
                # The real callback redraws the Cine and clears this scene.
                # This is only safe after mousePressEvent has returned.
                graph.scene().clear()
                created.append(point)

        graph = simplemeas_ui.ResizableGraph(QLabel(), Parent())
        graph.resize(220, 180)
        graph.show()
        self.app.processEvents()
        QTest.mousePress(
            graph.viewport(), simplemeas_ui.Qt.MouseButton.LeftButton,
            pos=graph.viewport().rect().center(),
        )
        self.assertEqual(created, [])
        self.app.processEvents()
        self.assertEqual(len(created), 1)
        graph.close()

    def test_entering_hybrid_point_selection_repaints_the_cine_viewport(self):
        redraws = []
        graph = simplemeas_ui.ResizableGraph(QLabel(), SimpleNamespace(
            current_tool=None,
            status_bar=QLabel(''),
            vm=SimpleNamespace(),
        ))
        graph.resize(220, 180)
        graph.show()
        dummy = SimpleNamespace(
            _hybrid_selection_graph=None,
            vm=SimpleNamespace(
                pending_tracking_method=None,
                update_status_text=SignalSink(),
                redraw_cb=lambda: redraws.append(True),
            ),
        )

        simplemeas_ui.MainWindow.begin_hybrid_region_selection(dummy, graph)
        self.app.processEvents()

        self.assertIs(dummy._hybrid_selection_graph, graph)
        self.assertEqual(dummy.vm.pending_tracking_method, HYBRID_TRACKING_METHOD)
        self.assertTrue(redraws)
        graph.close()

    def test_wheel_zoom_keeps_center_cursor_near_same_scene_point(self):
        parent = SimpleNamespace(
            current_tool=None,
            status_bar=QLabel(''),
            vm=SimpleNamespace(),
        )
        graph = simplemeas_ui.ResizableGraph(QLabel(), parent)
        graph.scene().setSceneRect(QRectF(0, 0, 1000, 800))
        graph.resize(600, 400)
        graph.show()
        self.app.processEvents()
        graph.reset_view_zoom()
        cursor = graph.viewport().rect().center()
        before = graph.mapToScene(cursor)
        initial_scale = graph.transform().m11()

        graph.zoom_at(cursor, 1.0)
        after = graph.mapToScene(cursor)

        self.assertGreater(graph.transform().m11(), initial_scale)
        self.assertLess(abs(after.x() - before.x()), 6.0)
        self.assertLess(abs(after.y() - before.y()), 6.0)
        graph.close()

    def test_canvas_click_routes_classic_choice_to_original_point_creation(self):
        created = []

        class Parent:
            current_tool = None
            status_bar = QLabel('')
            vm = SimpleNamespace(
                update_mouse_pos_cb=lambda *args, **kwargs: None,
                graph_mouse_motion_cb=lambda *args, **kwargs: None,
                update_status_text=SignalSink(),
            )

            def should_prompt_for_tracking_method(self):
                return True

            def choose_tracking_method(self):
                return simplemeas_ui.CLASSIC_TRACKING_METHOD

            def create_classic_track_at(self, point):
                created.append(point)

            def begin_hybrid_region_selection(self, graph):
                raise AssertionError('Classic choice must not enter Hybrid drawing mode')

            def is_hybrid_region_selection_active(self, graph):
                return False

        graph = simplemeas_ui.ResizableGraph(QLabel(), Parent())
        graph.resize(220, 180)
        graph.show()
        self.app.processEvents()
        QTest.mousePress(
            graph.viewport(),
            simplemeas_ui.Qt.MouseButton.LeftButton,
            pos=graph.viewport().rect().center(),
        )
        self.assertEqual(len(created), 1)
        self.assertGreaterEqual(created[0].x(), 0)
        self.assertLessEqual(created[0].x(), graph.xMax)
        graph.close()

    def test_add_object_click_wins_over_an_existing_hybrid_fixture(self):
        created = []

        class Parent:
            current_tool = None
            status_bar = QLabel('')
            vm = SimpleNamespace(
                update_mouse_pos_cb=lambda *args, **kwargs: None,
                graph_mouse_motion_cb=lambda *args, **kwargs: None,
                update_status_text=SignalSink(),
            )

            def should_prompt_for_tracking_method(self):
                return True

            def choose_tracking_method(self):
                return CLASSIC_TRACKING_METHOD

            def create_classic_track_at(self, point):
                created.append(point)

            def is_hybrid_region_selection_active(self, graph):
                return False

        parent = Parent()
        graph = simplemeas_ui.ResizableGraph(QLabel(), parent)
        graph.resize(220, 180)
        graph.show()
        self.app.processEvents()
        item = simplemeas_ui.HybridRegionItem(
            QRectF(0, 0, 80, 80), (50, 50), 0.0, 0, parent,
            editable=True,
        )
        graph.scene().addItem(item)

        QTest.mousePress(
            graph.viewport(), simplemeas_ui.Qt.MouseButton.LeftButton,
            pos=graph.viewport().rect().center(),
        )

        self.assertEqual(len(created), 1)
        graph.close()

    def test_tracking_snap_reuses_exact_fractional_point(self):
        graph = simplemeas_ui.ResizableGraph(QLabel(), SimpleNamespace(
            current_tool=None,
            status_bar=QLabel(''),
            vm=SimpleNamespace(
                update_mouse_pos_cb=lambda *args, **kwargs: None,
                graph_mouse_motion_cb=lambda *args, **kwargs: None,
            ),
        ))
        graph.resize(400, 300)
        graph.show()
        self.app.processEvents()
        active_tool = SimpleNamespace(track_select=True)
        dummy = SimpleNamespace(
            vm=SimpleNamespace(
                active_frame=3,
                track_data={0: {
                    'name': 'Object 0',
                    'frames': np.array([3]),
                    'points': np.array([[42.3, 47.6]]),
                }},
                active_tool=active_tool,
            ),
            is_hybrid_region_selection_active=lambda candidate_graph: False,
            should_prompt_for_tracking_method=lambda: True,
        )

        candidate = simplemeas_ui.MainWindow.tracking_snap_candidate(
            dummy, graph, QPointF(42.8, 47.2)
        )

        self.assertEqual(candidate['object_id'], 0)
        self.assertEqual(candidate['point'], (42.3, 47.6))
        graph.close()

    def test_reused_hybrid_fixture_preserves_global_fixture_pose(self):
        source = {
            'name': 'Object 0',
            'tracking_method': HYBRID_TRACKING_METHOD,
            'frames': np.array([3]),
            'points': np.array([[40.0, 50.0]]),
            'angles': np.array([30.0]),
            'template_offset': (10.0, 4.0),
            'tpl_rng': (41, 31),
            'rotation_allowed': True,
            'rotation_range': 180.0,
            'rotation_step': 2.0,
            'edge_weight': 0.7,
            'edge_threshold': 0.25,
            'search_area_multiplier': 2.5,
            'smart_frames': True,
            'smart_miss_limit': 2,
            'acceptable_score': 0.6,
            'adjacent_confidence_weight': 0.65,
            'position_precision': 0.1,
            'angle_precision': 0.1,
        }
        target = {
            'points': np.array([[40.0, 50.0]]),
            'angles': np.array([0.0]),
            't_angles': np.array([0.0]),
        }
        dummy = SimpleNamespace(
            vm=SimpleNamespace(
                active_frame=3,
                track_data={0: source},
                cine_handler=SimpleNamespace(metadata=SimpleNamespace(
                    ImWidth=200, ImHeight=160,
                )),
            ),
            _rotate_tracking_offset=simplemeas_ui.MainWindow._rotate_tracking_offset,
            _hybrid_search_dimensions=lambda size, multiplier, allowed: (129, 129),
        )

        reused = simplemeas_ui.MainWindow._reuse_hybrid_fixture(
            dummy, target, 0, (40.0, 50.0)
        )

        self.assertTrue(reused)
        self.assertEqual(target['fixture_source_object'], 0)
        self.assertEqual(target['tpl_rng'], (41, 31))
        self.assertEqual(target['edge_weight'], 0.7)
        self.assertAlmostEqual(target['angles'][0], 30.0)
        source_center = np.array(source['points'][0]) + (
            simplemeas_ui.MainWindow._rotate_tracking_offset(
                source['template_offset'], 30.0
            )
        )
        target_center = np.array(target['points'][0]) + (
            simplemeas_ui.MainWindow._rotate_tracking_offset(
                target['template_offset'], 30.0
            )
        )
        np.testing.assert_allclose(target_center, source_center)

    def test_blank_image_does_not_turn_into_a_solid_edge_preview(self):
        edges = AutoTrackAlgorithms._edge_image(
            np.zeros((40, 50), dtype=np.uint8),
            threshold=0.30,
        )
        self.assertEqual(np.count_nonzero(edges), 0)

    def test_smart_processing_detects_low_confidence_start_and_stop(self):
        pattern = np.full((21, 21), 18, dtype=np.uint8)
        cv2.rectangle(pattern, (3, 4), (16, 8), 230, -1)
        cv2.rectangle(pattern, (13, 4), (16, 17), 230, -1)
        cv2.circle(pattern, (6, 15), 3, 145, -1)

        def frame_with_pattern(visible):
            frame = np.full((81, 81), 28, dtype=np.uint8)
            if visible:
                frame[30:51, 30:51] = pattern
            return frame

        frames = [frame_with_pattern(2 <= index <= 6) for index in range(9)]

        class ImageTools:
            def debayer(self, image, cfa, bpp, force_mono=1):
                return image

        class CineHandler:
            metadata = SimpleNamespace(
                ImWidth=81,
                ImHeight=81,
                CFA='Mono',
                RealBPP=8,
                ImageCount=len(frames),
                FirstImageNo=0,
            )

            def get_img(self, frame):
                return frames[int(frame)]

        vm = SimpleNamespace(
            cine_handler=CineHandler(),
            image_tools=ImageTools(),
            abort_autotrack=False,
            update_progress=SignalSink(),
            update_status_text=SignalSink(),
            track_complete=SignalSink(),
            active_frame=4,
        )
        track = {
            'name': 'Smart target',
            'points': np.array([[40.0, 40.0]]),
            'frames': np.array([4]),
            'scores': np.array([1.0]),
            'angles': np.array([0.0]),
            't_points': np.array([[40.0, 40.0]]),
            't_frames': np.array([4]),
            't_angles': np.array([0.0]),
            'anchor_frame': 4,
            'start': 0,
            'end': 8,
            'frames_enable': True,
            'search_area_enable': True,
            'search_area': (61, 61),
            'tpl_rng': (21, 21),
            'acceptable_score': 0.55,
            'subpixel_size': '1.0 pix',
            'subpixel_type': 'cubic',
            'update_template_enable': False,
            'tpl_score': 0.8,
            'tracking_method': HYBRID_TRACKING_METHOD,
            'rotation_allowed': False,
            'rotation_range': 0.0,
            'rotation_step': 2.0,
            'edge_weight': 0.6,
            'edge_threshold': 0.30,
            'smart_frames': True,
            'smart_miss_limit': 2,
        }

        result = AutoTrackTool(vm).process_template(track, (-1, -1), 4)

        self.assertEqual(result['frames'].tolist(), [2, 3, 4, 5, 6])
        self.assertEqual(result['smart_detected_start'], 2)
        self.assertEqual(result['smart_detected_end'], 6)
        anchor_index = result['frames'].tolist().index(4)
        self.assertEqual(result['scores'][anchor_index], 1.0)
        progress = [value[0] for value in vm.update_progress.values]
        self.assertTrue(progress)
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(progress[-1], 100)
        attempts = list(result['confidence_components'])
        self.assertEqual(attempts, [5, 6, 7, 8, 3, 2, 1, 0])


if __name__ == '__main__':
    unittest.main()
