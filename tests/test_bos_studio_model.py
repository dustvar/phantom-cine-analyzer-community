"""Record Studio edits use one media clock for the player, fades and MP4 encoder."""
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from bos_compass import CompassPlacement, draw_compass
from bos_frames import default_bos_settings
from bos_preview import CacheReader, PreviewReadAhead, export_cached_bos, prepare_bos_preview
from bos_studio_model import StudioEdit
from test_bos_motion import texture, write_frames


class StudioEditTests(unittest.TestCase):
    def test_240_source_frames_per_second_is_fifteen_seconds_at_both_delivery_rates(self):
        edit = StudioEdit(3600, 240)
        self.assertEqual(edit.duration, 15)
        self.assertEqual(list(edit.output_frames(60)), list(range(0, 3600, 4)))
        self.assertEqual(list(edit.output_frames(240)), list(range(3600)))
        self.assertEqual(edit.output_count(30), 450)

    def test_fractional_rates_and_piecewise_presentation_boundaries(self):
        edit = StudioEdit(2997, 29.97)
        self.assertEqual(edit.duration, 100)
        self.assertEqual(edit.output_count(29.97), 2997)
        self.assertEqual(list(edit.output_frames(29.97)), list(range(2997)))
        for cut, fps in ((7, 240), (107, 1.), (1002, 11.3), (2104, 59.94)):
            edit.set_speed(edit.split_speed(cut), fps)
        self.assertEqual([edit.frame_at(edit.time_at(i)) for i in range(2997)], list(range(2997)))
        self.assertEqual(edit.frame_at(edit.time_at(107)-1e-7), 106)
        self.assertEqual(edit.frame_at(-1), 0)
        self.assertEqual(edit.frame_at(edit.time_at(edit.count)), edit.count-1)
        other = edit.copy()
        edit.move_speed_cut(1, 10000)
        self.assertEqual(edit.speeds[0].last, 105)
        edit.remove_speed_cut(1)
        self.assertEqual(len(edit.speeds), 4)
        self.assertEqual(other.speeds[0].last, 6)
        with self.assertRaises(ValueError):
            edit.set_speed(0, float('nan'))

    def test_opacity_follows_output_time_through_speed_cuts_and_gaps(self):
        edit = StudioEdit(13, 10)
        edit.set_speed(edit.split_speed(4), 5)
        edit.set_fades(0, .8, .4)
        self.assertEqual(list(edit.opacity_bytes())[:9], [0, 32, 64, 96, 128, 191, 255, 255, 255])
        self.assertAlmostEqual(edit.alpha_at(11), .5, delta=1/255)
        right = edit.split_overlay(0, 8)
        self.assertEqual(edit.overlays[0].fade_out, 0)
        self.assertEqual(edit.overlays[right].fade_in, 0)
        self.assertEqual(edit.opacity_bytes()[7:9], bytes([255, 255]))
        edit.trim_overlay(0, 1, 4)
        edit.trim_overlay(right, 10, 12)
        edit.move_overlay(right, 3)  # Collides with left range. Its length is preserved.
        self.assertEqual((edit.overlays[right].first, edit.overlays[right].last), (5, 7))
        outside = edit.add_overlay(9, 11)
        self.assertEqual(edit.overlay_index(8), None)
        with self.assertRaises(ValueError):
            edit.add_overlay(10, 12)
        self.assertEqual(edit.opacity_bytes()[8], 0)
        self.assertEqual(edit.opacity_bytes()[9:12], bytes([255]*3))
        edit.remove_overlay(outside)
        edit.set_all_rates(10)
        self.assertAlmostEqual(edit.alpha_at(4), .375, delta=1/255)
        self.assertEqual(edit.opacity_bytes()[8:], bytes(5))

    def test_one_frame_can_be_edited_and_has_nonzero_export_duration(self):
        edit = StudioEdit(1, 240)
        self.assertEqual(edit.output_count(30), 1)
        self.assertEqual(list(edit.output_frames(30)), [0])
        for action in (lambda: edit.split_overlay(0, 0), lambda: edit.split_speed(0)):
            with self.assertRaises(ValueError):
                action()
        self.assertIsNone(edit.remove_overlay(0))
        self.assertEqual(edit.opacity_bytes(), bytes(1))
        self.assertEqual(edit.add_overlay(0, 0), 0)
        self.assertEqual(edit.opacity_bytes(), bytes([255]))


class StudioExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = self.root/'disconnected.cine'
        dots = texture()
        write_frames(source, np.array([np.roll(dots, i%3, axis=1) for i in range(24)]))
        state = dict(default_bos_settings(), path=str(source), reference=0,
                     roi=[140, 10, 240, 220], component='direction', palette='color')
        self.clip = prepare_bos_preview(state, 0, 23, root=self.root/'cache')
        self.addCleanup(self.clip.close)
        source.unlink()

    def test_decoded_mp4_has_expected_duration_content_order_and_faded_compass(self):
        edit = StudioEdit(24, 240)
        edit.set_speed(edit.split_speed(12), 30)
        edit.set_fades(0, .025, .1)
        placement = CompassPlacement(.6, .1, .32)
        expected_indices = [0, 4, 8] + [i for i in range(12, 24) for _ in range(2)]
        expected_levels = {0: 0., 4: 2./3, 8: 1., 21: 2./3, 22: 1./3, 23: 0.}
        calls = []
        with self.clip.reader() as reader:
            def text(frame, index, source_rate):
                calls.append(index)
                self.assertEqual(source_rate, 240 if index < 12 else 30)
                if index in expected_levels:
                    opacity = expected_levels[index]
                    raw = reader.read(index, False)
                    analysis = reader.read(index, True)
                    expected = cv2.addWeighted(raw, 1-opacity, analysis, opacity, 0.)
                    legend = expected.copy()
                    draw_compass(legend, placement)
                    expected = cv2.addWeighted(expected, 1-opacity, legend, opacity, 0.)
                    np.testing.assert_allclose(frame, expected, atol=2)
                frame[:30, :80] = 40+index*6
            destination = self.root/'edited.mp4'
            with patch('bos_preview.BOSFrames', side_effect=AssertionError('unexpected analysis')):
                export_cached_bos(self.clip, destination, 60, studio=edit, compass=placement,
                                  text_renderer=text)
        self.assertEqual(calls, list(dict.fromkeys(expected_indices)))
        movie = cv2.VideoCapture(str(destination))
        try:
            self.assertEqual(movie.get(cv2.CAP_PROP_FPS), 60)
            self.assertEqual(movie.get(cv2.CAP_PROP_FRAME_COUNT), 27)
            self.assertEqual(27/60, edit.duration)
            for index in expected_indices:
                success, frame = movie.read()
                self.assertTrue(success)
                self.assertAlmostEqual(float(np.median(frame[4:24, 4:70])), 40+index*6, delta=3.)
            self.assertFalse(movie.read()[0])
        finally:
            movie.release()

    def test_high_rate_intent_is_numeric_movie_metadata_and_samples_are_intact(self):
        key = b'com.apple.quicktime.full-frame-rate-playback-intent'
        def boxes(data):
            cursor = 0
            while cursor < len(data):
                size, kind = struct.unpack_from('>I4s', data, cursor)
                self.assertGreaterEqual(size, 8)
                self.assertLessEqual(cursor+size, len(data))
                yield kind, data[cursor+8:cursor+size]
                cursor += size
        edit = StudioEdit(24, 240)
        for rate in (60, 120, 240):
            target = self.root/f'{rate}.mp4'
            export_cached_bos(self.clip, target, rate, studio=edit)
            contents = target.read_bytes()
            top = dict(boxes(contents))
            self.assertLess(list(top).index(b'moov'), list(top).index(b'mdat'))
            if rate < 120:
                self.assertNotIn(key, contents)
            else:
                meta = dict(boxes(dict(boxes(top[b'moov']))[b'meta']))
                keys = list(boxes(meta[b'keys'][8:]))
                entry = keys.index((b'mdta', key))+1
                item = dict(boxes(meta[b'ilst']))[entry.to_bytes(4, 'big')]
                self.assertEqual(dict(boxes(item))[b'data'], struct.pack('>IIB', 22, 0, 1))
            movie = cv2.VideoCapture(str(target))
            try:
                self.assertEqual(movie.get(cv2.CAP_PROP_FPS), rate)
                self.assertEqual(movie.get(cv2.CAP_PROP_FRAME_COUNT), rate//10)
                for _ in range(rate//10):
                    self.assertTrue(movie.read()[0])
                self.assertFalse(movie.read()[0])
            finally:
                movie.release()
        previous = target.read_bytes()
        with patch('mp4_playback_intent.finalize_full_rate_intent', side_effect=ValueError('metadata')):
            with self.assertRaisesRegex(ValueError, 'metadata'):
                export_cached_bos(self.clip, target, 240, studio=edit)
        self.assertEqual(target.read_bytes(), previous)
        self.assertEqual(list(self.root.glob('.pca-export-*')), [])

    def test_enabling_fade_during_decode_discards_old_result(self):
        worker = PreviewReadAhead(self.clip, memory_bytes=1)
        worker.request(4)
        entered, proceed = threading.Event(), threading.Event()
        original = CacheReader.blended
        def delayed(reader, index, level):
            if index == 4 and level == 255 and not entered.is_set():
                entered.set()
                proceed.wait(3)
            return original(reader, index, level)
        with patch.object(CacheReader, 'blended', delayed):
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.clip.reader() as reader:
                    for level in (127, 0, 255, 64):
                        worker.set_overlay_alpha(bytes([level]*self.clip.count))
                        proceed.set()
                        deadline = time.monotonic()+3
                        while worker.get(4) is None and time.monotonic() < deadline:
                            time.sleep(.002)
                        np.testing.assert_array_equal(worker.get(4), reader.blended(4, level))
                self.assertIsNone(worker.error)
            finally:
                proceed.set()
                worker.stop()
                worker.join(3)
        self.assertFalse(worker.is_alive())
