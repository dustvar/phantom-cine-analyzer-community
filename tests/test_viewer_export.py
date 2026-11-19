import os
import sys
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import cv2
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app-source/modules/trackmeasure'))
from test_cine_metadata import write_cine
from cine_metadata import CineHandler
from viewer_processing import apply_grade, default_grade, render_cine_image, curve_values
from video_export import export_clip, ExportCancelled, video_frame
from pyphantom_image.imagetools import ImageTools
from viewer_timeline import Timeline, default_edit, split_at, overlay_text
from viewer_overlay import apply_fps_overlay, apply_data_overlays

SETTINGS = dict(image_brightness=0, image_contrast=1, image_gamma=1,
                toggle_ftone=False, raw_enabled=False)


class GradeTests(unittest.TestCase):
    def test_identity_and_bypass_preserve_frame(self):
        image = np.arange(65536, dtype=np.uint16).reshape(256, 256)
        self.assertIs(apply_grade(image, default_grade()), image)
        grade = default_grade()
        grade.update(gamma=2, enabled=False)
        self.assertIs(apply_grade(image, grade), image)

    def test_levels_gamma_and_curve_are_monotone_and_do_not_mutate_tracking_frame(self):
        image = np.arange(65536, dtype=np.uint16).reshape(256, 256)
        original = image.copy()
        grade = default_grade()
        grade.update(black=.1, white=.9, gamma=1.5, curve=[0, .3, .7, .8, 1])
        result = apply_grade(image, grade)
        self.assertEqual(result.dtype, np.uint16)
        self.assertEqual(result.flat[0], 0)
        self.assertEqual(result.flat[-1], 65535)
        self.assertGreater(result[128, 0], image[128, 0])
        self.assertTrue(np.all(np.diff(result.ravel().astype(int)) >= 0))
        np.testing.assert_array_equal(image, original)
        np.testing.assert_allclose(curve_values(grade['curve'], np.linspace(0, 1, 5)), grade['curve'])

    def test_rgb_levels_and_odd_dimensions(self):
        image = np.full((15, 17, 3), (32, 64, 128), dtype=np.uint8)
        grade = default_grade()
        grade['gamma'] = 2
        result = apply_grade(image, grade)
        self.assertEqual(result.dtype, np.uint8)
        self.assertEqual(result.shape, image.shape)
        self.assertTrue(np.all(result > image))
        self.assertEqual(video_frame(result).shape, (16, 18, 3))
        self.assertEqual(video_frame(result, 10).shape, (10, 12, 3))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'test.cine'
        write_cine(self.source)

    def test_all_formats_include_exact_range_rate_and_viewer_appearance(self):
        grade = default_grade()
        grade['gamma'] = 1.4
        h = CineHandler()
        self.addCleanup(h.close)
        h.load_cine(self.source)
        expected = [video_frame(apply_grade(render_cine_image(h.get_img(i), h.metadata, SETTINGS, ImageTools()), grade)) for i in range(2, 6)]
        for extension in ('.mp4', '.mov', '.avi'):
            with self.subTest(extension=extension):
                output = self.source.with_suffix(extension)
                progress = []
                export_clip(self.source, output, 2, 5, 24, SETTINGS, grade,
                            progress=lambda count, total: progress.append((count, total)))
                cap = cv2.VideoCapture(str(output))
                self.assertTrue(cap.isOpened())
                self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT), 4)
                self.assertAlmostEqual(cap.get(cv2.CAP_PROP_FPS), 24)
                for wanted in expected:
                    ok, frame = cap.read()
                    self.assertTrue(ok)
                    self.assertLess(np.abs(frame.astype(float) - wanted).mean(), 4)
                self.assertFalse(cap.read()[0])
                cap.release()
                self.assertEqual(progress, [(1, 4), (2, 4), (3, 4), (4, 4)])

    def test_cancellation_keeps_existing_destination_and_removes_partial(self):
        output = self.source.with_suffix('.mp4')
        output.write_bytes(b'existing video')
        event = threading.Event()
        with self.assertRaises(ExportCancelled):
            export_clip(self.source, output, 0, 7, 30, SETTINGS, default_grade(), cancel=event,
                        progress=lambda count, total: event.set())
        self.assertEqual(output.read_bytes(), b'existing video')
        self.assertEqual(list(output.parent.glob('.pca-export-*')), [])

    def test_single_frame_and_invalid_ranges(self):
        output = self.source.with_suffix('.mp4')
        export_clip(self.source, output, 4, 4, 30, SETTINGS, default_grade())
        cap = cv2.VideoCapture(str(output))
        self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT), 1)
        cap.release()
        original = output.read_bytes()
        for first, last in [(-1, 4), (3, 2), (0, 8)]:
            with self.assertRaises(ValueError):
                export_clip(self.source, output, first, last, 30, SETTINGS, default_grade())
        self.assertEqual(output.read_bytes(), original)

    def test_encoder_failure_removes_partial_and_preserves_destination(self):
        output = self.source.with_suffix('.mp4')
        output.write_bytes(b'existing video')
        with patch('subprocess.Popen', side_effect=OSError('Encoder unavailable')):
            with self.assertRaises(OSError):
                export_clip(self.source, output, 0, 2, 30, SETTINGS, default_grade())
        self.assertEqual(output.read_bytes(), b'existing video')
        self.assertEqual(list(output.parent.glob('.pca-export-*')), [])

    def test_retimed_export_matches_the_preview_map_and_live_overlay(self):
        write_cine(self.source, frames=12, width=320, height=200)
        edit = split_at(default_edit(12), 6)
        edit['sections'][0]['fps'] = 60
        edit['sections'][1].update(fps=15, ramp=.1)
        edit['overlay'].update(enabled=True, size=5, x=.8, y=.9, color='#ffdf8c')
        for place, curve in [('right', 'smooth'), ('left', 'ease_in'), ('center', 'ease_out')]:
            edit['sections'][1].update(ramp_place=place, ramp_curve=curve)
            edit['overlay'].update(enabled=place != 'left', layout='straight' if place == 'left' else 'stacked', decimals=place == 'center')
            edit['camera_overlay'].update(enabled=place != 'right', layout='straight' if place == 'left' else 'stacked')
            with self.subTest(place=place):
                plan = Timeline(edit, 0, 11)
                output = self.source.with_suffix('.mp4')
                progress = []
                export_clip(self.source, output, 0, 11, 30, SETTINGS, default_grade(), edit=edit,
                            progress=lambda count,total: progress.append((count,total)))
                handler = CineHandler(); handler.load_cine(self.source); self.addCleanup(handler.close)
                cap = cv2.VideoCapture(str(output))
                self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT), plan.frame_count)
                for index, rate in plan.samples():
                    base = video_frame(render_cine_image(handler.get_img(index), handler.metadata, SETTINGS, ImageTools()))
                    wanted, _ = apply_data_overlays(base, edit, handler.metadata, rate)
                    ok, actual = cap.read(); self.assertTrue(ok)
                    self.assertLess(np.abs(actual[:,:,::-1].astype(float)-wanted).mean(), 4)
                self.assertFalse(cap.read()[0]);cap.release()
                self.assertEqual(progress[-1],(plan.frame_count,plan.frame_count))

    def test_skipped_sections_are_omitted_and_all_skipped_preserves_destination(self):
        edit = split_at(default_edit(8), 4); edit['sections'][0]['keep'] = False
        output = self.source.with_suffix('.mp4')
        export_clip(self.source, output, 0, 7, 30, SETTINGS, default_grade(), edit=edit)
        cap = cv2.VideoCapture(str(output)); self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT),4)
        ok, frame = cap.read(); self.assertTrue(ok); self.assertGreater(frame.mean(),90);cap.release()
        old = output.read_bytes(); edit['sections'][1]['keep']=False
        with self.assertRaisesRegex(ValueError,'Include at least one'):
            export_clip(self.source,output,0,7,30,SETTINGS,default_grade(),edit=edit)
        self.assertEqual(output.read_bytes(),old)
