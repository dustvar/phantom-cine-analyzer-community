"""Sparse analyzed frames retain original Cine timing through editing and MP4."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import cv2
import numpy as np

from bos_frames import BOSFrames, default_bos_settings
from bos_preview import CachedBOSClip, prepare_bos_preview, export_cached_bos
from bos_playback_controls import SampleFrameBox
from bos_preview_ui import BOSPreviewWindow
from bos_sampling import RangeSampling
from bos_studio_model import StudioEdit
from bos_studio_text import StudioText, capture_rate
from bos_studio_ui import RecordStudio
from test_bos_motion import texture, write_frames


class SampledBOSPlaybackTests(unittest.TestCase):
    def test_irregular_tail_and_exact_fades_across_speed_cuts(self):
        sampling = RangeSampling(12, 5)
        self.assertEqual([sampling.offset(i) for i in range(5)], [0, 5, 10, 11, 12])
        for offset in range(12):
            self.assertLessEqual(sampling.offset(sampling.index_at(offset)), offset)
        sparse = StudioEdit(4, 200, sampling=sampling)
        dense = StudioEdit(12, 200)
        for edit, split in ((sparse, 1), (dense, 5)):
            edit.set_speed(edit.split_speed(split), 1000)
            edit.set_fades(0, .03, .004, layer='vectors')
        self.assertEqual(sparse.duration, dense.duration)
        self.assertEqual(sparse.output_count(240), dense.output_count(240))
        frames = list(sparse.output_frames(240))
        self.assertEqual(frames, [sampling.index_at(i) for i in dense.output_frames(240)])
        for i in range(4):
            self.assertEqual(sparse.frame_at(sparse.time_at(i)), i)
            self.assertAlmostEqual(sparse.time_at(i), dense.time_at(sampling.offset(i)))
            self.assertEqual(sparse.opacity_bytes('vectors')[i], dense.opacity_bytes('vectors')[sampling.offset(i)])
        single = StudioEdit(1, 1000, sampling=RangeSampling(1, 6))
        self.assertEqual(single.duration, .001)
        self.assertEqual(list(single.output_frames(30)), [0])
        with self.assertRaises(ValueError):
            RangeSampling(2, 0)
        with self.assertRaises(ValueError):
            StudioEdit(3, sampling=sampling)

    def test_native_spinboxes_and_data_at_short_last_step(self):
        app = QApplication.instance() or QApplication([])
        sampling = RangeSampling(12, 5)
        clip = SimpleNamespace(sampling=sampling, count=4, width=80, height=40,
                               times=np.array([0., 50., 100., 110.]), manifest={},
                               source_frame=lambda i: -20+sampling.offset(i))
        self.assertAlmostEqual(capture_rate(clip), 100000)
        card = StudioText(clip, dict(fields=['frame', 'playback', 'slowdown'], size='medium',
                                    first=0, last=3), capture_rate(clip))
        self.assertEqual(card.lines(2, 200), ('Frame 11 of 12', '200 fps playback', '500× slower'))
        box = SampleFrameBox(-20, sampling)
        box.setValue(-9)
        box.stepDown()
        self.assertEqual(box.value(), -10)
        box.stepDown()
        self.assertEqual(box.value(), -15)
        box.stepUp()
        self.assertEqual(box.value(), -10)
        studio = RecordStudio(clip, StudioEdit(4, 200, sampling=sampling))
        studio.last.setValue(-11)
        self.assertEqual(studio.last.value(), -10)
        self.assertEqual(studio.model.overlays[0].last, 2)
        studio.close()
        app.processEvents()

    def test_main_scrubber_uses_original_clock_and_arrows_step_the_short_tail(self):
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'scrub.cine'
            frame = cv2.resize(texture(), (80, 64))
            write_frames(source, np.stack([frame]*202))
            state = dict(default_bos_settings(), path=str(source), reference=0,
                         roi=[0, 0, 80, 64], playback_fps=200.)
            clip = prepare_bos_preview(state, 0, 201, root=root/'cache', sample_step=200)
            window = BOSPreviewWindow(None, clip)
            window.show()

            def wait_for_sample(index):
                deadline = time.monotonic()+5
                while window.pending_seek is not None and time.monotonic() < deadline:
                    app.processEvents()
                    QTest.qWait(2)
                self.assertIsNone(window.pending_seek)
                self.assertEqual(window.position, index)

            try:
                wait_for_sample(0)
                slider = window.slider
                self.assertEqual(slider.maximum(), 201)
                QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=slider.rect().center())
                self.assertFalse(slider.isSliderDown())
                self.assertEqual(slider.value(), 0)
                wait_for_sample(0)
                QTest.keyClick(slider, Qt.Key.Key_Right)
                wait_for_sample(1)
                self.assertEqual(slider.value(), 200)
                QTest.keyClick(slider, Qt.Key.Key_Right)
                wait_for_sample(2)
                self.assertEqual(slider.value(), 201)
                QTest.keyClick(slider, Qt.Key.Key_Left)
                wait_for_sample(1)
                self.assertEqual(slider.value(), 200)
            finally:
                window.close()
                app.processEvents()

    def test_sparse_cache_calls_and_movie_with_no_edits(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'dots.cine'
            frames = np.stack([np.roll(texture(), i, axis=1) for i in range(8)])
            write_frames(source, frames)
            state = dict(default_bos_settings(), path=str(source), reference=0,
                         roi=[140, 10, 240, 220], playback_fps=30.)
            measured, progress = [], []
            original_render = BOSFrames.render

            def render(session, settings):
                measured.append(settings['position'])
                return original_render(session, settings)

            with patch('bos_preview.BOSFrames.render', render):
                sparse = prepare_bos_preview(state, 1, 7, root=root/'cache', sample_step=5,
                                             progress=lambda done, total: progress.append((done, total)))
            self.assertEqual(measured, [1, 6, 7])
            self.assertEqual(progress, [(1, 3), (2, 3), (3, 3)])
            self.assertEqual([sparse.source_frame(i) for i in range(3)], [-9, -4, -3])
            session = BOSFrames(source)
            try:
                with sparse.reader() as reader:
                    for i, native_index in enumerate(measured):
                        direct = session.render(dict(state, position=native_index))
                        np.testing.assert_array_equal(reader.read(i), direct['image'])
                        self.assertEqual(direct['time_us'], sparse.times[i])
            finally:
                session.close()

            movie = root/'sampled.mp4'
            # 7 source frames at 30 source fps need 14 encoded frames at 60 Hz.
            indices = []
            export_cached_bos(sparse, movie, 60, text_renderer=lambda _, i, rate: indices.append((i, rate)))
            self.assertEqual(indices, [(0, 30.), (1, 30.), (2, 30.)])
            cap = cv2.VideoCapture(str(movie))
            try:
                self.assertEqual(cap.get(cv2.CAP_PROP_FPS), 60)
                self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT), 14)
            finally:
                cap.release()

            dense = prepare_bos_preview(state, 3, 3, root=root/'cache')
            manifest = dense.directory/'manifest.json'
            recorded = json.loads(manifest.read_text())
            del recorded['sample_step']
            manifest.write_text(json.dumps(recorded))
            reopened = CachedBOSClip(dense.directory)
            self.assertEqual(reopened.source_frame(0), -7)
            export_cached_bos(reopened, movie, 30)
            cap = cv2.VideoCapture(str(movie))
            try:
                self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT), 1)
            finally:
                cap.release()
            sparse.close()
            dense.close()


if __name__ == '__main__':
    unittest.main()
