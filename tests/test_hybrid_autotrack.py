import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / 'app-source'
    / 'modules'
    / 'trackmeasure'
)
sys.path.insert(0, str(MODULE_PATH))

from autotrackalgorithms import AutoTrackAlgorithms, AutoTrackException, Data
from simplemeas_tools import (
    AutoTrackTool,
    HYBRID_TRACKING_METHOD,
    TrackTool,
    _rotate_tracking_offset,
)


class HybridAutoTrackTest(unittest.TestCase):
    def test_point_lock_refines_a_biased_rigid_pose_at_subpixel_resolution(self):
        reference_frame = np.full((101, 101), 24, dtype=np.float32)
        cv2.circle(reference_frame, (50, 50), 4, 230, -1)
        cv2.line(reference_frame, (47, 50), (53, 50), 120, 1)
        reference_patch = AutoTrackAlgorithms.extract_oriented_patch(
            reference_frame, (50.0, 50.0), (21, 21)
        )

        current_frame = np.full((101, 101), 24, dtype=np.float32)
        actual_point = (61.3, 56.7)
        shifted = cv2.warpAffine(
            reference_frame,
            np.array([[1.0, 0.0, actual_point[0] - 50.0],
                      [0.0, 1.0, actual_point[1] - 50.0]], dtype=np.float32),
            (101, 101),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=24,
        )
        current_frame[:] = shifted

        refined = AutoTrackAlgorithms.refine_anchor_point(
            reference_patch,
            current_frame,
            predicted_point=(65.0, 53.0),
            angle_deg=0.0,
            search_radius=8,
            position_precision=0.1,
        )

        self.assertIsNotNone(refined)
        self.assertAlmostEqual(refined.x_pos, actual_point[0], delta=0.35)
        self.assertAlmostEqual(refined.y_pos, actual_point[1], delta=0.35)
        self.assertGreater(refined.confid_val_ij, 0.75)

    def test_point_lock_rejects_a_featureless_reference(self):
        refined = AutoTrackAlgorithms.refine_anchor_point(
            np.full((21, 21), 50, dtype=np.uint8),
            np.full((61, 61), 50, dtype=np.uint8),
            predicted_point=(30.0, 30.0),
            angle_deg=0.0,
        )
        self.assertIsNone(refined)

    def test_pose_accumulates_across_the_180_degree_boundary(self):
        pattern = np.full((51, 51), 15, dtype=np.uint8)
        cv2.rectangle(pattern, (5, 6), (38, 12), 230, -1)
        cv2.rectangle(pattern, (31, 6), (38, 42), 230, -1)
        cv2.circle(pattern, (11, 39), 5, 150, -1)
        expected_angles = [160.0, 175.0, 190.0, 210.0]

        def make_frame(angle):
            frame = np.full((181, 181), 34, dtype=np.uint8)
            rotated = AutoTrackAlgorithms._rotate_image_expanded(pattern, angle)
            height, width = rotated.shape
            top = 90 - (height // 2)
            left = 90 - (width // 2)
            frame[top:top + height, left:left + width] = rotated
            return frame

        frames = [make_frame(angle) for angle in expected_angles]

        class SignalSink:
            def emit(self, *args, **kwargs):
                pass

        class ImageTools:
            def debayer(self, image, cfa, bpp, force_mono=1):
                return image

        class CineHandler:
            metadata = SimpleNamespace(
                ImWidth=181, ImHeight=181, CFA='Mono', RealBPP=8,
                ImageCount=len(frames), FirstImageNo=0,
            )

            def get_img(self, frame):
                return frames[int(frame)]

        vm = SimpleNamespace(
            cine_handler=CineHandler(), image_tools=ImageTools(),
            abort_autotrack=False, update_progress=SignalSink(),
            update_status_text=SignalSink(), track_complete=SignalSink(),
            active_frame=0,
        )
        track = {
            'name': 'Full-turn target',
            'points': np.array([[90.0, 90.0]]), 'frames': np.array([0]),
            'scores': np.array([1.0]), 'angles': np.array([160.0]),
            't_points': np.array([[90.0, 90.0]]), 't_frames': np.array([0]),
            't_angles': np.array([160.0]), 'anchor_frame': 0,
            'search_area_enable': True, 'search_area': (171, 171),
            'tpl_rng': (51, 51), 'acceptable_score': 0.4,
            'subpixel_size': '1.0 pix', 'subpixel_type': 'cubic',
            'update_template_enable': False, 'tpl_score': 0.8,
            'tracking_method': HYBRID_TRACKING_METHOD,
            'rotation_allowed': True, 'rotation_range': 180.0,
            'rotation_step': 2.0, 'edge_weight': 0.6,
            'edge_threshold': 0.30, 'smart_frames': True,
            'smart_miss_limit': 3, 'template_offset': (0.0, 0.0),
            'adjacent_confidence_weight': 0.65,
        }

        result = AutoTrackTool(vm).track(1, len(frames) - 1, track)

        self.assertEqual(result['frames'].tolist(), [0, 1, 2, 3])
        for actual, expected in zip(result['angles'], expected_angles):
            self.assertAlmostEqual(actual, expected, delta=3.0)
        self.assertGreater(result['angles'][-1], 180.0)

    def test_smart_tracking_recovers_after_one_unmatchable_frame(self):
        frames = [np.full((61, 61), 40, dtype=np.uint8) for _ in range(3)]

        class SignalSink:
            def emit(self, *args, **kwargs):
                pass

        class ImageTools:
            def debayer(self, image, cfa, bpp, force_mono=1):
                return image

        class CineHandler:
            metadata = SimpleNamespace(
                ImWidth=61, ImHeight=61, CFA='Mono', RealBPP=8,
                ImageCount=3, FirstImageNo=0,
            )

            def get_img(self, frame):
                return frames[int(frame)]

        vm = SimpleNamespace(
            cine_handler=CineHandler(), image_tools=ImageTools(),
            abort_autotrack=False, update_progress=SignalSink(),
            update_status_text=SignalSink(), track_complete=SignalSink(),
            active_frame=0,
        )
        track = {
            'name': 'Recoverable target',
            'points': np.array([[30.0, 30.0]]), 'frames': np.array([0]),
            'scores': np.array([1.0]), 'angles': np.array([0.0]),
            't_points': np.array([[30.0, 30.0]]), 't_frames': np.array([0]),
            't_angles': np.array([0.0]), 'anchor_frame': 0,
            'search_area_enable': True, 'search_area': (51, 51),
            'tpl_rng': (21, 21), 'acceptable_score': 0.5,
            'subpixel_size': '1.0 pix', 'subpixel_type': 'cubic',
            'update_template_enable': False, 'tpl_score': 0.8,
            'tracking_method': HYBRID_TRACKING_METHOD,
            'rotation_allowed': False, 'rotation_range': 0.0,
            'rotation_step': 2.0, 'edge_weight': 0.6,
            'edge_threshold': 0.30, 'smart_frames': True,
            'smart_miss_limit': 2,
        }
        calls = {'count': 0}

        def match_after_one_failure(*args, **kwargs):
            calls['count'] += 1
            if calls['count'] == 1:
                raise AutoTrackException('synthetic unreadable frame')
            return Data(
                norm_xcorr_map=np.ones((1, 1)), x_pos=30.0, y_pos=30.0,
                confid_val_ij=0.9, angle_deg=0.0,
            )

        with patch.object(
            AutoTrackAlgorithms, 'hybrid_pattern_matcher',
            side_effect=match_after_one_failure,
        ):
            result = AutoTrackTool(vm).track(1, 2, track)

        self.assertEqual(result['frames'].tolist(), [0, 2])
        self.assertIn('error', result['confidence_components'][1])
        self.assertAlmostEqual(result['confidence_components'][2]['combined'], 0.9)

    def test_dense_noise_is_penalized_relative_to_unique_geometry(self):
        clear = np.full((41, 41), 18, dtype=np.uint8)
        cv2.rectangle(clear, (5, 7), (30, 13), 220, -1)
        cv2.rectangle(clear, (24, 7), (30, 33), 220, -1)
        cv2.circle(clear, (11, 29), 5, 145, -1)
        clear_search = np.full((121, 121), 35, dtype=np.uint8)
        clear_search[40:81, 40:81] = clear
        clear_result = AutoTrackAlgorithms.hybrid_pattern_matcher(
            clear, clear_search, (60, 60), (121, 121),
            rotation_range=0.0, edge_weight=0.6, edge_threshold=0.30,
        )

        rng = np.random.default_rng(20)
        noise = rng.integers(0, 256, (41, 41), dtype=np.uint8)
        noisy_search = rng.integers(0, 256, (121, 121), dtype=np.uint8)
        noisy_search[40:81, 40:81] = noise
        noise_result = AutoTrackAlgorithms.hybrid_pattern_matcher(
            noise, noisy_search, (60, 60), (121, 121),
            rotation_range=0.0, edge_weight=0.6, edge_threshold=0.30,
        )

        self.assertGreater(clear_result.confid_val_ij, 0.75)
        self.assertLess(noise_result.confid_val_ij, 0.65)
        self.assertGreater(noise_result.edge_density, clear_result.edge_density)
        self.assertGreater(clear_result.confid_val_ij, noise_result.confid_val_ij)

    def test_hybrid_matcher_recovers_position_and_rotation_under_contrast_change(self):
        template = np.full((41, 41), 18, dtype=np.uint8)
        cv2.rectangle(template, (5, 7), (30, 13), 220, -1)
        cv2.rectangle(template, (24, 7), (30, 33), 220, -1)
        cv2.circle(template, (11, 29), 5, 145, -1)
        cv2.line(template, (8, 17), (20, 25), 250, 2)

        expected_angle = 11.3
        rotated = AutoTrackAlgorithms._rotate_image(template, expected_angle)
        # Defocus the target slightly so the test exercises the continuous
        # gradient matcher instead of relying on ideal one-pixel Canny edges.
        rotated = cv2.GaussianBlur(rotated, (0, 0), 1.0)
        rng = np.random.default_rng(7)
        search = np.tile(np.linspace(32, 68, 121, dtype=np.float32), (121, 1))
        search += rng.normal(0, 3.0, search.shape)

        expected_center = (77.0, 55.0)
        top_left_x = int(expected_center[0] - ((template.shape[1] - 1) / 2.0))
        top_left_y = int(expected_center[1] - ((template.shape[0] - 1) / 2.0))
        patch = 42.0 + rotated.astype(np.float32) * 0.72
        search[
            top_left_y:top_left_y + template.shape[0],
            top_left_x:top_left_x + template.shape[1],
        ] = patch
        search = np.clip(search, 0, 255).astype(np.uint8)

        result = AutoTrackAlgorithms.hybrid_pattern_matcher(
            template,
            search,
            sa_center=(60, 60),
            sa_rng=(121, 121),
            reference_angle=0.0,
            rotation_range=18.0,
            rotation_step=2.0,
            edge_weight=0.6,
        )

        self.assertAlmostEqual(result.x_pos, expected_center[0], delta=1.0)
        self.assertAlmostEqual(result.y_pos, expected_center[1], delta=1.0)
        self.assertAlmostEqual(result.angle_deg, expected_angle, delta=1.0)
        # Hybrid resolves its final pose at explicit tenths. The deterministic
        # synthetic target lands between the old 0.5° grid points.
        self.assertAlmostEqual(result.x_pos * 10.0, round(result.x_pos * 10.0), places=6)
        self.assertAlmostEqual(result.y_pos * 10.0, round(result.y_pos * 10.0), places=6)
        self.assertAlmostEqual(
            result.angle_deg * 10.0,
            round(result.angle_deg * 10.0),
            places=6,
        )
        self.assertGreater(abs((result.angle_deg * 2.0) - round(result.angle_deg * 2.0)), 0.05)
        self.assertGreater(result.confid_val_ij, 0.65)
        self.assertGreater(result.edge_score, 0.55)
        self.assertEqual(result.method, 'Hybrid')

    def test_soft_edges_retain_blurred_subpixel_information(self):
        image = np.zeros((41, 61), dtype=np.float32)
        image[:, 30:] = 200.0
        image = cv2.GaussianBlur(image, (0, 0), 1.8)

        soft_edges = AutoTrackAlgorithms._soft_edge_image(image, threshold=0.30)

        self.assertEqual(soft_edges.dtype, np.float32)
        self.assertGreaterEqual(float(np.min(soft_edges)), 0.0)
        self.assertLessEqual(float(np.max(soft_edges)), 1.0)
        nonzero_levels = np.unique(np.round(soft_edges[soft_edges > 0], 3))
        self.assertGreater(len(nonzero_levels), 4)

    def test_quadratic_peak_recovers_a_fractional_translation(self):
        rows, cols = np.mgrid[0:5, 0:5]
        score_map = -((cols - 2.3) ** 2) - ((rows - 1.8) ** 2)

        dx, dy = AutoTrackAlgorithms._subpixel_peak(score_map, 2, 2)

        self.assertAlmostEqual(dx, 0.3, places=6)
        self.assertAlmostEqual(dy, -0.2, places=6)
        self.assertAlmostEqual(
            AutoTrackAlgorithms._quantize_pose_value(40.0 + dx, 0.1),
            40.3,
            places=6,
        )

    def test_rotated_inner_fixture_boundary_uses_all_four_corners(self):
        self.assertTrue(AutoTrackTool._oriented_region_within_frame(
            center=(50.0, 50.0), size=(21, 11), angle_deg=30.0,
            frame_size=(100, 100),
        ))
        self.assertTrue(AutoTrackTool._oriented_region_within_frame(
            center=(10.0, 50.0), size=(21, 11), angle_deg=0.0,
            frame_size=(100, 100),
        ))
        self.assertFalse(AutoTrackTool._oriented_region_within_frame(
            center=(4.0, 50.0), size=(21, 11), angle_deg=30.0,
            frame_size=(100, 100),
        ))

    def test_hybrid_tracking_stops_when_inner_fixture_leaves_frame(self):
        frames = [np.full((81, 81), 40, dtype=np.uint8) for _ in range(2)]

        class SignalSink:
            def __init__(self):
                self.values = []

            def emit(self, *args, **kwargs):
                self.values.append(args)

        class ImageTools:
            def debayer(self, image, cfa, bpp, force_mono=1):
                return image

        class CineHandler:
            metadata = SimpleNamespace(
                ImWidth=81, ImHeight=81, CFA='Mono', RealBPP=8,
                ImageCount=2, FirstImageNo=-10,
            )

            def get_img(self, frame):
                return frames[int(frame)]

        status = SignalSink()
        vm = SimpleNamespace(
            cine_handler=CineHandler(), image_tools=ImageTools(),
            abort_autotrack=False, update_progress=SignalSink(),
            update_status_text=status, track_complete=SignalSink(),
            active_frame=0,
        )
        track = {
            'name': 'Departing target',
            'points': np.array([[40.0, 40.0]]), 'frames': np.array([0]),
            'scores': np.array([1.0]), 'angles': np.array([0.0]),
            't_points': np.array([[40.0, 40.0]]), 't_frames': np.array([0]),
            't_angles': np.array([0.0]), 'anchor_frame': 0,
            'template_offset': (0.0, 0.0),
            'search_area_enable': True, 'search_area': (61, 61),
            'tpl_rng': (21, 21), 'acceptable_score': 0.0,
            'subpixel_size': '1.0 pix', 'subpixel_type': 'cubic',
            'update_template_enable': False, 'tpl_score': 0.8,
            'tracking_method': HYBRID_TRACKING_METHOD,
            'rotation_allowed': True, 'rotation_range': 180.0,
            'rotation_step': 2.0, 'edge_weight': 0.6,
            'edge_threshold': 0.30, 'smart_frames': True,
            'smart_miss_limit': 3,
        }

        outside = Data(
            norm_xcorr_map=np.ones((1, 1)), x_pos=2.0, y_pos=40.0,
            confid_val_ij=0.95, angle_deg=15.0,
        )
        with patch.object(
            AutoTrackAlgorithms, 'hybrid_pattern_matcher', return_value=outside
        ):
            result = AutoTrackTool(vm).track(1, 1, track)

        self.assertEqual(result['frames'].tolist(), [0])
        self.assertEqual(
            result['boundary_reasons']['forward'],
            {'reason': 'fixture_out_of_frame', 'frame': 1, 'cine_frame': -9},
        )
        self.assertTrue(any('inner Hybrid fixture left' in value[0] for value in status.values))

    def test_track_points_keep_position_score_and_angle_aligned(self):
        track = {
            'points': np.array([[10.0, 10.0]]),
            'frames': np.array([0]),
            'scores': np.array([1.0]),
            'angles': np.array([0.0]),
            't_points': np.array([[10.0, 10.0]]),
            't_frames': np.array([0]),
            't_angles': np.array([0.0]),
        }
        tool = TrackTool(vm=None)
        tool.add_point_to_template(
            track, (14.0, 13.0), 2, score=0.91, angle=8.5, update_track_template=True
        )
        tool.add_point_to_template(
            track, (12.0, 11.0), 1, score=0.88, angle=4.0, update_track_template=False
        )

        self.assertEqual(track['frames'].tolist(), [0, 1, 2])
        self.assertEqual(track['angles'].tolist(), [0.0, 4.0, 8.5])
        self.assertEqual(track['t_frames'].tolist(), [0, 2])
        self.assertEqual(track['t_angles'].tolist(), [0.0, 8.5])

    def test_dual_reference_score_uses_neighbor_and_original_setup(self):
        setup_pattern = np.full((31, 31), 20, dtype=np.uint8)
        cv2.rectangle(setup_pattern, (3, 4), (24, 9), 230, -1)
        cv2.rectangle(setup_pattern, (19, 4), (24, 26), 230, -1)
        cv2.circle(setup_pattern, (8, 23), 4, 145, -1)

        changed_pattern = np.full((31, 31), 20, dtype=np.uint8)
        cv2.circle(changed_pattern, (15, 15), 10, 230, 4)
        cv2.line(changed_pattern, (5, 25), (25, 5), 170, 3)

        def make_frame(pattern):
            frame = np.full((101, 101), 35, dtype=np.uint8)
            frame[35:66, 35:66] = pattern
            return frame

        frames = [
            make_frame(setup_pattern),
            make_frame(changed_pattern),
            make_frame(changed_pattern),
        ]

        class SignalSink:
            def emit(self, *args, **kwargs):
                pass

        class ImageTools:
            def debayer(self, image, cfa, bpp, force_mono=1):
                return image

        class CineHandler:
            metadata = SimpleNamespace(
                ImWidth=101, ImHeight=101, CFA='Mono', RealBPP=8,
                ImageCount=3, FirstImageNo=0,
            )

            def get_img(self, frame):
                return frames[int(frame)]

        vm = SimpleNamespace(
            cine_handler=CineHandler(), image_tools=ImageTools(),
            abort_autotrack=False, update_progress=SignalSink(),
            update_status_text=SignalSink(), track_complete=SignalSink(),
            active_frame=0,
        )
        track = {
            'name': 'Dual reference target',
            'points': np.array([[50.0, 50.0]]), 'frames': np.array([0]),
            'scores': np.array([1.0]), 'angles': np.array([0.0]),
            't_points': np.array([[50.0, 50.0]]), 't_frames': np.array([0]),
            't_angles': np.array([0.0]), 'anchor_frame': 0,
            'template_offset': (0.0, 0.0),
            'search_area_enable': True, 'search_area': (81, 81),
            'tpl_rng': (31, 31), 'acceptable_score': 0.0,
            'subpixel_size': '1.0 pix', 'subpixel_type': 'cubic',
            'update_template_enable': False, 'tpl_score': 0.8,
            'tracking_method': HYBRID_TRACKING_METHOD,
            'rotation_range': 0.0, 'rotation_step': 2.0,
            'edge_weight': 0.6, 'edge_threshold': 0.30,
            'adjacent_confidence_weight': 0.65,
        }

        matcher_templates = []
        real_matcher = AutoTrackAlgorithms.hybrid_pattern_matcher

        def record_matcher_template(*args, **kwargs):
            matcher_templates.append(np.array(kwargs['tpl_img'], copy=True))
            return real_matcher(*args, **kwargs)

        with patch.object(
            AutoTrackAlgorithms,
            'hybrid_pattern_matcher',
            side_effect=record_matcher_template,
        ):
            result = AutoTrackTool(vm).track(1, 2, track)
        frame_two = result['confidence_components'][2]

        # There are three matcher calls per frame: pose from the setup model,
        # then adjacent/setup confidence diagnostics. The primary model for
        # both frames must be the same immutable setup-frame patch.
        np.testing.assert_array_equal(matcher_templates[0], matcher_templates[3])
        self.assertGreater(
            np.mean(np.abs(matcher_templates[4] - matcher_templates[3])),
            1.0,
        )
        self.assertEqual(frame_two['reference_frame'], 1)
        self.assertEqual(frame_two['setup_frame'], 0)
        self.assertGreater(frame_two['adjacent'], frame_two['setup'])
        self.assertLess(frame_two['combined'], frame_two['adjacent'])
        expected = (frame_two['adjacent'] ** 0.65) * (frame_two['setup'] ** 0.35)
        self.assertAlmostEqual(frame_two['combined'], expected, places=5)

    def test_auto_track_tool_records_pose_for_a_moving_rotating_object(self):
        pattern = np.full((31, 31), 15, dtype=np.uint8)
        cv2.rectangle(pattern, (4, 5), (24, 10), 230, -1)
        cv2.rectangle(pattern, (19, 5), (24, 26), 230, -1)
        cv2.circle(pattern, (8, 23), 4, 150, -1)

        def make_frame(center, angle, contrast=1.0):
            frame = np.full((121, 121), 34, dtype=np.float32)
            rotated = AutoTrackAlgorithms._rotate_image(pattern, angle).astype(np.float32)
            patch = 30.0 + contrast * rotated
            x0 = int(center[0] - 15)
            y0 = int(center[1] - 15)
            frame[y0:y0 + 31, x0:x0 + 31] = patch
            return np.clip(frame, 0, 255).astype(np.uint8)

        frames = [make_frame((60, 60), 0.0), make_frame((70, 55), 8.0, 0.72)]

        class SignalSink:
            def emit(self, *args, **kwargs):
                pass

        class ImageTools:
            def debayer(self, image, cfa, bpp, force_mono=1):
                return image

        class CineHandler:
            metadata = SimpleNamespace(
                ImWidth=121,
                ImHeight=121,
                CFA='Mono',
                RealBPP=8,
                ImageCount=2,
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
            active_frame=0,
        )
        track = {
            'name': 'Pose target',
            'points': np.array([[60.0, 60.0]]),
            'frames': np.array([0]),
            'scores': np.array([1.0]),
            'angles': np.array([0.0]),
            't_points': np.array([[60.0, 60.0]]),
            't_frames': np.array([0]),
            't_angles': np.array([0.0]),
            'search_area_enable': True,
            'search_area': (101, 101),
            'tpl_rng': (31, 31),
            'acceptable_score': 0.5,
            'subpixel_size': '1.0 pix',
            'subpixel_type': 'cubic',
            'update_template_enable': False,
            'tpl_score': 0.8,
            'tracking_method': HYBRID_TRACKING_METHOD,
            'rotation_range': 15.0,
            'rotation_step': 2.0,
            'edge_weight': 0.6,
        }

        result = AutoTrackTool(vm).track(1, 1, track)

        self.assertEqual(result['frames'].tolist(), [0, 1])
        self.assertAlmostEqual(result['points'][1][0], 70.0, delta=1.0)
        self.assertAlmostEqual(result['points'][1][1], 55.0, delta=1.0)
        self.assertAlmostEqual(result['angles'][1], 8.0, delta=1.0)

    def test_reinforcement_region_tracks_a_separate_attached_point(self):
        pattern = np.full((31, 31), 15, dtype=np.uint8)
        cv2.rectangle(pattern, (4, 5), (24, 10), 230, -1)
        cv2.rectangle(pattern, (19, 5), (24, 26), 230, -1)
        cv2.circle(pattern, (8, 23), 4, 150, -1)
        template_offset = np.array((10.0, 0.0))

        def make_frame(center, angle):
            frame = np.full((121, 121), 34, dtype=np.float32)
            rotated = AutoTrackAlgorithms._rotate_image(pattern, angle).astype(np.float32)
            x0 = int(center[0] - 15)
            y0 = int(center[1] - 15)
            frame[y0:y0 + 31, x0:x0 + 31] = 30.0 + rotated
            return np.clip(frame, 0, 255).astype(np.uint8)

        initial_reinforcement = np.array((60.0, 60.0))
        next_reinforcement = np.array((70.0, 55.0))
        initial_point = initial_reinforcement - _rotate_tracking_offset(template_offset, 0.0)
        expected_point = next_reinforcement - _rotate_tracking_offset(template_offset, 8.0)
        frames = [
            make_frame(initial_reinforcement, 0.0),
            make_frame(next_reinforcement, 8.0),
        ]

        class SignalSink:
            def emit(self, *args, **kwargs):
                pass

        class ImageTools:
            def debayer(self, image, cfa, bpp, force_mono=1):
                return image

        class CineHandler:
            metadata = SimpleNamespace(
                ImWidth=121, ImHeight=121, CFA='Mono', RealBPP=8,
                ImageCount=2, FirstImageNo=0,
            )

            def get_img(self, frame):
                return frames[int(frame)]

        vm = SimpleNamespace(
            cine_handler=CineHandler(), image_tools=ImageTools(),
            abort_autotrack=False, update_progress=SignalSink(),
            update_status_text=SignalSink(), track_complete=SignalSink(),
            active_frame=0,
        )
        track = {
            'name': 'Offset target',
            'points': np.array([initial_point]), 'frames': np.array([0]),
            'scores': np.array([1.0]), 'angles': np.array([0.0]),
            't_points': np.array([initial_point]), 't_frames': np.array([0]),
            't_angles': np.array([0.0]), 'template_offset': tuple(template_offset),
            'search_area_enable': True, 'search_area': (101, 101),
            'tpl_rng': (31, 31), 'acceptable_score': 0.5,
            'subpixel_size': '1.0 pix', 'subpixel_type': 'cubic',
            'update_template_enable': False, 'tpl_score': 0.8,
            'tracking_method': HYBRID_TRACKING_METHOD,
            'rotation_range': 15.0, 'rotation_step': 2.0,
            'edge_weight': 0.6, 'edge_threshold': 0.30,
        }

        result = AutoTrackTool(vm).track(1, 1, track)

        self.assertAlmostEqual(result['points'][1][0], expected_point[0], delta=1.2)
        self.assertAlmostEqual(result['points'][1][1], expected_point[1], delta=1.2)
        self.assertAlmostEqual(result['angles'][1], 8.0, delta=1.0)

    def test_hybrid_search_area_is_shifted_inside_frame_near_edge(self):
        pattern = np.full((31, 31), 15, dtype=np.uint8)
        cv2.rectangle(pattern, (4, 5), (24, 10), 230, -1)
        cv2.rectangle(pattern, (19, 5), (24, 26), 230, -1)
        cv2.circle(pattern, (8, 23), 4, 150, -1)

        def make_frame(center):
            frame = np.full((121, 121), 34, dtype=np.uint8)
            x0 = int(center[0] - 15)
            y0 = int(center[1] - 15)
            frame[y0:y0 + 31, x0:x0 + 31] = pattern
            return frame

        frames = [make_frame((100, 100)), make_frame((103, 98))]

        class SignalSink:
            def emit(self, *args, **kwargs):
                pass

        class ImageTools:
            def debayer(self, image, cfa, bpp, force_mono=1):
                return image

        class CineHandler:
            metadata = SimpleNamespace(
                ImWidth=121, ImHeight=121, CFA='Mono', RealBPP=8,
                ImageCount=2, FirstImageNo=0,
            )

            def get_img(self, frame):
                return frames[int(frame)]

        vm = SimpleNamespace(
            cine_handler=CineHandler(), image_tools=ImageTools(),
            abort_autotrack=False, update_progress=SignalSink(),
            update_status_text=SignalSink(), track_complete=SignalSink(),
            active_frame=0,
        )
        track = {
            'name': 'Edge target',
            'points': np.array([[100.0, 100.0]]), 'frames': np.array([0]),
            'scores': np.array([1.0]), 'angles': np.array([0.0]),
            't_points': np.array([[100.0, 100.0]]), 't_frames': np.array([0]),
            't_angles': np.array([0.0]), 'anchor_frame': 0,
            'search_area_enable': True, 'search_area': (101, 101),
            'tpl_rng': (31, 31), 'acceptable_score': 0.45,
            'subpixel_size': '1.0 pix', 'subpixel_type': 'cubic',
            'update_template_enable': False, 'tpl_score': 0.8,
            'tracking_method': HYBRID_TRACKING_METHOD,
            'rotation_allowed': False, 'rotation_range': 0.0,
            'rotation_step': 2.0, 'edge_weight': 0.6,
            'edge_threshold': 0.30, 'smart_frames': True,
            'smart_miss_limit': 2,
        }

        result = AutoTrackTool(vm).track(1, 1, track)

        self.assertEqual(result['frames'].tolist(), [0, 1])
        self.assertAlmostEqual(result['points'][1][0], 103.0, delta=1.0)
        self.assertAlmostEqual(result['points'][1][1], 98.0, delta=1.0)


if __name__ == '__main__':
    unittest.main()
