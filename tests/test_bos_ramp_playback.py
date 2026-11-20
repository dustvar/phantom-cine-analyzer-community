"""A held BOS measurement keeps its image while the on-screen playback rate changes."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from bos_frames import default_bos_settings
from bos_preview import export_cached_bos, prepare_bos_preview
from bos_preview_ui import BOSPreviewWindow
from bos_studio_model import StudioEdit
from bos_studio_text import StudioText
from test_bos_motion import texture, write_frames


class BOSRampPlaybackTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        source = self.root/'held.cine'
        image = cv2.resize(texture(), (96, 64))
        write_frames(source, np.stack([image]*240))
        settings = dict(default_bos_settings(), path=str(source), reference=0,
                        roi=[0, 0, 96, 64], playback_fps=100)
        self.clip = prepare_bos_preview(settings, 0, 239, root=self.root/'cache', sample_step=40)
        self.addCleanup(self.clip.close)

    @staticmethod
    def ramp(edit):
        cut = edit.split_speed(3)  # Native offset 120
        edit.set_speed(cut, 300)
        edit.set_ramp(cut, .4, 'linear')

    def test_decoded_movie_preserves_presented_rates_within_one_measurement(self):
        edit = StudioEdit(self.clip.count, 100, sampling=self.clip.sampling)
        self.ramp(edit)
        def mark(frame, index, rate):
            frame[:24,:24] = index*30
            frame[:24,32:56] = round(rate/2)
        path = self.root/'ramp.mp4'
        export_cached_bos(self.clip, path, 60, studio=edit, text_renderer=mark)
        # The ramp starts at source offset 90 and ends at 170. It spends
        # 0.4 seconds covering 80 source frames, half of that time on each side.
        duration = 90/100 + .4 + 70/300
        cap = cv2.VideoCapture(str(path))
        self.addCleanup(cap.release)
        self.assertEqual(cap.get(cv2.CAP_PROP_FPS), 60)
        self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT), round(duration*60))
        for output_index in range(round(duration*60)):
            ok, picture = cap.read()
            self.assertTrue(ok)
            t = output_index/60
            if t < .9:
                offset, rate = t*100, 100
            elif t <= 1.3:
                elapsed = t-.9
                rate = 100+500*elapsed
                offset = 90+100*elapsed+250*elapsed**2
            else:
                offset, rate = 170+300*(t-1.3), 300
            sample = self.clip.sampling.index_at(int(offset+1e-8))
            self.assertAlmostEqual(float(np.mean(picture[4:20,4:20])), sample*30, delta=4)
            self.assertAlmostEqual(float(np.mean(picture[4:20,36:52])), rate/2, delta=4)
        self.assertFalse(cap.read()[0])

    def test_live_titles_update_across_held_frame_and_pause_returns_its_exact_rate(self):
        app = QApplication.instance() or QApplication([])
        window = BOSPreviewWindow(None,self.clip)
        try:
            self.ramp(window.edit_model)
            window._studio_changed()
            until = time.monotonic()+5
            while (window.pending_seek is not None or window.reader.get(2) is None) and time.monotonic()<until:
                app.processEvents()
                time.sleep(.005)
            self.assertIsNotNone(window.reader.get(2))
            window.timer.stop()
            window._text = StudioText(self.clip, dict(fields=['playback'], size='medium',
                                       first=0, last=self.clip.count-1), None)
            window.playing = True
            window.clock = (10.,0.)
            window._last_controls_update = 0.
            with patch.object(window._text, 'graphic', wraps=window._text.graphic) as paint:
                for timestamp, expected in ((.95, 125), (1.0, 150)):
                    with patch('bos_preview_ui.time.monotonic',return_value=10+timestamp):
                        window._tick()
                    self.assertEqual(window.position, 2)
                    self.assertEqual(window.fps.value(), expected)
                    self.assertEqual(paint.call_args.args[0],2)
                    self.assertAlmostEqual(paint.call_args.args[1],expected)
                window.pause()
                self.assertEqual(window.fps.value(), 100)
                self.assertEqual(paint.call_args.args,(2,100))
        finally:
            window.close()
            app.processEvents()
