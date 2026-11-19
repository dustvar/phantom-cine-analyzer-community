"""Frame publication, cancellation, and timing at the Qt transport boundary."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import test_workspace_interactions as fixtures


class ControlledBuffer:
    def __init__(self, metadata):
        self.md = metadata
        self.ready = False
        self.error = None
        self.requests = []
        self.generation = 0

    def prime(self, handler):
        self.md = handler.metadata

    def request(self, path, indices, settings):
        self.requests.append((path, indices))

    def get(self, path, index, settings):
        if self.error:
            raise self.error
        if self.ready:
            pixels = np.full((240, 320), index, dtype=np.uint16)
            return pixels, pixels

    def metadata(self, path, settings):
        return self.md

    def clear(self):
        self.generation += 1


class PlaybackPreviewsTest(TestCase):
    setUpClass = classmethod(fixtures.WorkspaceInteractionsTest.setUpClass.__func__)
    tearDown = fixtures.WorkspaceInteractionsTest.tearDown

    def setUp(self):
        fixtures.WorkspaceInteractionsTest.setUp(self)
        self.buffer = ControlledBuffer(self.vm.cine_handler.metadata)
        self.window._preview_buffer = self.buffer
        reader_type = patch('playback_preview.CineHandler', fixtures.ImageCineHandler)
        reader_type.start()
        self.addCleanup(reader_type.stop)

    def at(self, milliseconds):
        self.window._viewer_clock = SimpleNamespace(elapsed=lambda: milliseconds)
        self.window._advance_playback()

    def test_fast_forward_pins_missing_frame_then_pause_discards_late_work(self):
        self.window.forward_faster_button.click()
        self.at(34)
        self.assertEqual(self.window.frame_slider.value(), 0)
        self.assertEqual(self.buffer.requests[-1][1][0], 4)
        self.assertNotIn('Buffering', self.window.active_frame_label.text())
        self.window._stall_clock = SimpleNamespace(elapsed=lambda: 200)
        self.at(5000)
        self.assertEqual(self.window.active_frame_label.text(), 'Buffering…')
        self.assertEqual(self.buffer.requests[-1][1][0], 4)
        generation = self.buffer.generation
        self.window.pause_button.click()
        self.assertGreater(self.buffer.generation, generation)
        self.assertIn('Frame', self.window.active_frame_label.text())
        self.buffer.ready = True
        self.at(10000)
        self.assertEqual(self.window.frame_slider.value(), 0)
        self.assertFalse(self.window.playback_timer.isActive())

    def test_reverse_starts_from_displayed_frame_after_direction_change(self):
        self.window._timeline_seek(10)
        self.window.forward_faster_button.click()
        self.at(34)
        self.assertEqual(self.buffer.requests[-1][1][0], 14)
        self.window.reverse_play_button.click()
        self.buffer.ready = True
        with patch.object(self.vm.cine_handler, 'get_img', side_effect=AssertionError('GUI path decoded pixels')):
            self.at(34)
            self.assertEqual(self.vm.active_frame, 9)
            self.assertEqual(int(self.vm.transformed_img[0, 0]), 9)
            self.vm.update_mouse_pos_cb((10, 20))
            self.assertIn('RAW: 9', self.vm.pixel_val)
            self.window.pause_button.click()
            self.vm.update_mouse_pos_cb((10, 20))
            self.assertIn('RAW: 9', self.vm.pixel_val)

    def test_read_failure_does_not_move_playhead_or_repeat(self):
        self.window.forward_play_button.click()
        self.buffer.error = OSError('External volume disconnected')
        self.at(34)
        self.assertFalse(self.window._playback_active)
        self.assertEqual(self.window.frame_slider.value(), 0)
        self.assertFalse(self.window.playback_timer.isActive())
        self.assertIn('External volume disconnected', self.window.status_bar.text())

    def test_display_rate_is_reported_separately_from_requested_fps(self):
        self.window.status_bar.setText('Ready for tracking')
        requested = self.window.playback_fps.value()
        self.window.forward_play_button.click()
        self.window._throughput_clock = SimpleNamespace(elapsed=lambda: 2000, restart=lambda: None)
        self.window._throughput_count = 6
        self.window._update_playback_status()
        self.assertIn('Display 3.0 fps', self.window.status_bar.text())
        self.assertEqual(self.window.playback_fps.value(), requested)
        self.window.pause_button.click()
        self.assertEqual(self.window.status_bar.text(), 'Ready for tracking')


    def test_play_uses_forward_frames_warmed_at_a_settled_seek(self):
        self.window._timeline_seek(5)
        self.window._warm_paused_preview()
        path, indices = self.buffer.requests[-1]
        self.assertEqual((path, indices[0]), (self.vm.cine_path, 6))
        generation = self.buffer.generation
        self.buffer.ready = True
        self.window.forward_play_button.click()
        self.assertEqual(generation, self.buffer.generation)
        self.at(34)
        self.assertEqual(self.vm.active_frame, 6)
