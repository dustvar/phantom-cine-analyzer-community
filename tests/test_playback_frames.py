"""Exercise the playback worker at its public boundary, including stale in-flight I/O."""
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app-source/modules/trackmeasure'))
from playback_frames import PlaybackFrameBuffer
from pyphantom_image.imagetools import ImageTools
from viewer_effects import default_image_settings
from viewer_media import open_media, VideoReader
from viewer_processing import render_cine_image
from test_cine_metadata import write_cine


class PlaybackFramesTests(unittest.TestCase):
    def test_disk_reader_runs_ahead_of_processing_with_bounded_frames(self):
        processing, second_read, release = threading.Event(), threading.Event(), threading.Event()
        reads = []
        class Reader:
            metadata = SimpleNamespace(CFA='Mono')
            def get_img(self, index):
                reads.append(index)
                if index == 2:
                    second_read.set()
                return np.full((20, 30), index, np.uint16)
            def close(self):
                pass
        def render(raw, *args):
            if raw[0, 0] == 1:
                processing.set()
                if not release.wait(5):
                    raise RuntimeError('test gate timed out')
            return raw.copy()
        buffer = PlaybackFrameBuffer(max_bytes=7200, max_frames=12)
        with patch('playback_frames.open_media', lambda *a, **kw: Reader()), patch('playback_frames.render_cine_image', render):
            try:
                buffer.request('slow', range(1, 13), {})
                self.assertTrue(processing.wait(3))
                self.assertTrue(second_read.wait(3), 'Disk read was serialized behind rendering')
                with buffer._condition:
                    self.assertEqual(buffer._capacity, 3)
                    self.assertLessEqual(len(buffer._raw_ready), 2)
                release.set()
                raw, image = self.ready(buffer, 'slow', 3, {})
                self.assertEqual(int(image[0, 0]), 3)
                self.assertLessEqual(len(reads), 3)
                self.assertEqual(buffer.status()[1], 3)
            finally:
                release.set(); buffer.close()
                buffer._thread.join(3); buffer._render_thread.join(3)

    def test_source_change_discards_a_completed_old_render(self):
        processing, release = threading.Event(), threading.Event()
        class Reader:
            metadata = SimpleNamespace(CFA='Mono')
            def __init__(self, path): self.path = path
            def get_img(self, index): return np.full((4, 4), index, np.uint16)
            def close(self): pass
        def render(raw, *args):
            if raw[0, 0] == 1:
                processing.set()
                release.wait(5)
            return raw.copy()
        buffer = PlaybackFrameBuffer()
        with patch('playback_frames.open_media', lambda path, **kw: Reader(path)), patch('playback_frames.render_cine_image', render):
            try:
                buffer.request('old', [1, 2], {})
                self.assertTrue(processing.wait(3))
                buffer.request('new', [7, 8], {})
                release.set()
                self.assertEqual(int(self.ready(buffer, 'new', 7, {})[1][0, 0]), 7)
                self.assertIsNone(buffer.get('old', 1, {}))
            finally:
                release.set(); buffer.close()
                buffer._thread.join(3); buffer._render_thread.join(3)

    def ready(self, buffer, path, index, settings=None):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            result = buffer.get(path, index, settings)
            if result is not None:
                return result
            time.sleep(.005)
        self.fail(f'frame {index} was never delivered')

    def test_reverse_lookahead_matches_foreground_cine_and_source_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'tiny.cine')
            pixels = write_cine(source)
            foreground = open_media(source)
            settings = default_image_settings(foreground.metadata)
            buffer = PlaybackFrameBuffer()
            try:
                buffer.prime(foreground)
                buffer.request(source, (5, 4, 3), None)
                raw, display = self.ready(buffer, source, 3)
                np.testing.assert_array_equal(raw, pixels[3])
                np.testing.assert_array_equal(display, render_cine_image(
                    foreground.get_img(3), foreground.metadata, settings, ImageTools()))
                self.assertEqual(buffer.metadata(source, None).ImageCount, len(pixels))
                settings['raw_enabled'] = True
                buffer.request(source, (5,), settings)
                self.assertIsNone(buffer.get(source, 3, None))
                _, changed = self.ready(buffer, source, 5, settings)
                np.testing.assert_array_equal(changed, render_cine_image(
                    foreground.get_img(5), foreground.metadata, settings, ImageTools()))
            finally:
                foreground.close()
                buffer.close()
                buffer._thread.join(3)

    def test_cancel_during_read_and_resume_on_different_source(self):
        entered, release = threading.Event(), threading.Event()
        closed = []
        reads = []
        class Reader(VideoReader):
            metadata = object()
            def __init__(self, path, **kwargs):
                self.path = path
            def get_img(self, index):
                reads.append((self.path, index))
                if self.path == 'old':
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError('test gate timed out')
                return np.full((2, 2), index, np.uint8)
            def close(self):
                closed.append(self.path)
        buffer = PlaybackFrameBuffer(max_bytes=4)
        with patch('playback_frames.open_media', Reader):
            try:
                buffer.request('old', [1, 2], {})
                self.assertTrue(entered.wait(3))
                buffer.request('old', [1, 2], {})
                buffer.clear()
                buffer.request('new', [3, 4, 5], {})
                self.assertIsNone(buffer.get('new', 3, {}))
                release.set()
                raw, converted = self.ready(buffer, 'new', 3, {})
                self.assertIs(raw, converted)
                self.assertIsNone(buffer.get('old', 1, {}))
                buffer.request('new', [4, 5], {})
                self.ready(buffer, 'new', 4, {})
                self.assertEqual(reads, [('old', 1), ('new', 3), ('new', 4)])
            finally:
                release.set()
                buffer.close()
                buffer._thread.join(3)
            self.assertEqual(closed, ['old', 'new'])

    def test_decode_error_available_to_controller_and_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'missing.cine')
            buffer = PlaybackFrameBuffer()
            try:
                buffer.request(path, [0], None)
                with self.assertRaisesRegex(RuntimeError, 'playback frame 0'):
                    self.ready(buffer, path, 0)
                buffer.clear()
                write_cine(path)
                buffer.request(path, [0], None)
                self.assertEqual(self.ready(buffer, path, 0)[0][0, 0], 20)
            finally:
                buffer.close()
                buffer._thread.join(3)
