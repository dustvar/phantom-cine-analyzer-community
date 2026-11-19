"""High source-frame speeds must preserve presentation time and exact frame inspection."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from bos_export import export_bos_clip
from bos_frames import BOSFrames, default_bos_settings, validate_bos_settings
from bos_playback_controls import ReplaySpeed
from bos_studio_model import StudioEdit
from test_bos_motion import write_frames


class HighSpeedBOSPlaybackTests(unittest.TestCase):
    def test_whole_and_fractional_widget_and_saved_speeds(self):
        app = QApplication.instance() or QApplication([])
        speed = ReplaySpeed()
        try:
            speed.slider.setValue(1000)
            self.assertEqual(speed.number.value(), 1000)
            speed.number.setValue(999.75)
            self.assertEqual(speed.slider.value(), 1000)
            for rate in (999.75, 1000):
                self.assertEqual(validate_bos_settings({'playback_fps': rate})['playback_fps'], rate)
            with self.assertRaises(ValueError):
                validate_bos_settings({'playback_fps': 1000.01})
        finally:
            speed.close()

    def test_sparse_schedule_preserves_seeks_cut_boundaries_and_slow_sections(self):
        edit = StudioEdit(1100, 1000)
        edit.set_speed(edit.split_speed(1000), 50)
        self.assertEqual(edit.duration, 3.)
        self.assertEqual(edit.output_count(60), 180)
        self.assertEqual(list(edit.output_frames(60))[60], 1000)
        with self.assertRaises(ValueError):
            edit.output_count(1000)  # MP4 has an independent output-rate limit.
        early = edit.preview_indices(5, 4, 60)
        self.assertEqual(early, (5, 17, 34, 51))
        boundary = edit.preview_indices(986, 7, 60)
        self.assertEqual(boundary, (986, 1000, 1001, 1002, 1003, 1004, 1005))
        self.assertEqual(edit.preview_frame(999, 60), 986)
        self.assertEqual(edit.preview_frame(1000, 60), 1000)
        self.assertEqual(edit.preview_indices(1099, 4, 60), (1099,))

    def test_direct_export_samples_1000_source_fps_at_60_fps_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cine, movie = root/'dots.cine', root/'1000-playback.mp4'
            texture = np.random.default_rng(72).integers(0, 245, (64, 96), dtype=np.uint8)
            # An offset range catches confusion between source and clip-local indices.
            write_frames(cine, np.array([np.roll(texture, i%3, axis=1) for i in range(1002)]))
            settings = dict(default_bos_settings(), path=str(cine), reference=0,
                            roi=[5, 4, 84, 54], playback_fps=1000)
            measured = []
            render = BOSFrames.render
            def record(session, state):
                measured.append(state['position'])
                return render(session, state)
            with patch.object(BOSFrames, 'render', record):
                export_bos_clip(settings, movie, 2, 1001, 1000)
            self.assertEqual(measured, [2 + i*1000//60 for i in range(60)])
            cap = cv2.VideoCapture(str(movie))
            try:
                fps = cap.get(cv2.CAP_PROP_FPS)
                timestamps = []
                while cap.read()[0]:
                    timestamps.append(cap.get(cv2.CAP_PROP_POS_MSEC))
                self.assertAlmostEqual(fps, 60.)
                self.assertEqual(len(timestamps), 60)
                self.assertEqual(len(timestamps)/fps, 1.)
                np.testing.assert_allclose(np.diff(timestamps), 1000/60, atol=.005)
            finally:
                cap.release()
