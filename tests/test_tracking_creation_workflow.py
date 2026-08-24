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

from PySide6.QtCore import QRectF
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
        self.assertEqual(track['edge_threshold'], 0.30)
        self.assertEqual(track['scores'][0], 'N/A')

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
