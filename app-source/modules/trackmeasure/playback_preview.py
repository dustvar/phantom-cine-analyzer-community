"""Prepare preview frames ahead of the transport, then publish pixels and position together."""
import math
from dataclasses import dataclass

from PySide6.QtCore import QElapsedTimer

from cine_metadata import CineHandler
from playback_frames import PlaybackFrameBuffer
from viewer_timeline import copy_index, copy_offset, copy_options


@dataclass
class PreparedFrame:
    raw: object
    image: object
    metadata: object
    original_source: bool


class PlaybackPreviewMixin:
    def _finish_buffering(self):
        self._stall_clock = None
        if getattr(self, '_showing_buffering', False):
            self._showing_buffering = False
            self.update_active_frame(self.frame_slider.value())

    def _reset_transport_clock(self):
        self._finish_buffering()
        self._pending_tick = None
        self._viewer_clock = QElapsedTimer()
        self._viewer_clock.start()

    def _begin_preview_buffer(self):
        self._warm_timer.stop()
        handler = getattr(self.vm, 'cine_handler', None)
        self._buffer_playback = isinstance(handler, CineHandler) and handler.cine_loaded
        if self._buffer_playback:
            if self._preview_buffer is None:
                self._preview_buffer = PlaybackFrameBuffer()
                self.destroyed.connect(self._preview_buffer.close)
            self._preview_buffer.prime(handler)
            if self._playback_active and getattr(self, '_throughput_clock', None) is None:
                self._throughput_clock = QElapsedTimer()
                self._throughput_clock.start()
                self._throughput_count = 0
                self._preview_status_before = self.status_bar.text()
                self._preview_tooltip_before = self.status_bar.toolTip()
                self._preview_status_message = None

    def _schedule_preview_warmup(self):
        if not self._playback_active:
            self._warm_timer.start()

    def _warm_paused_preview(self):
        group = getattr(self, 'group_playback', None)
        if group is not None and group.enabled():
            return
        if (self._playback_active or not self.isVisible() or self._tracking_busy()
                or self.workflow_stack.currentWidget() is not self.tab_splitter):
            return
        self._begin_preview_buffer()
        if not self._buffer_playback:
            return
        frame = self.frame_slider.value()
        viewer = self.current_tool == self.viewer_tool and self._has_viewer_edit()
        if viewer:
            plan = self._viewer_timeline()
            if not plan.frame_count:
                return
            tick = round(plan.time_for_source(frame) * plan.output_fps)
            tick = 0 if tick >= plan.frame_count - 1 else tick + 1
            frames = self._viewer_preview_frames(plan, tick, step=1)
        else:
            first, last = self._playback_bounds()
            start = first if frame >= last else frame + 1
            frames = self._analysis_preview_frames(start, step=1)
        self._prepare_preview(frames, viewer, prefetch_only=True)

    def _clear_preview_buffer(self):
        self._warm_timer.stop()
        self._finish_buffering()
        self._buffer_playback = False
        self._pending_tick = None
        self._prepared_frame = None
        self._throughput_clock = None
        message = getattr(self, '_preview_status_message', None)
        if message and self.status_bar.text() == message:
            self.status_bar.setText(getattr(self, '_preview_status_before', ''))
            self.status_bar.setToolTip(getattr(self, '_preview_tooltip_before', ''))
        self._preview_status_message = None
        if getattr(self, '_preview_buffer', None) is not None:
            self._preview_buffer.clear()

    def _prepare_preview(self, frames, viewer, *, prefetch_only=False):
        """Request frames in presentation order. Nothing here opens files or waits for IO."""
        if not self._buffer_playback:
            return True
        buffer = self._preview_buffer
        frame = frames[0]
        path = self.vm.cine_path
        settings = self.vm.image_settings_snapshot()
        local_frames = frames
        original_source = True
        if viewer:
            edit = self.vm.viewer_edit
            ci = copy_index(edit, frame)
            if ci:
                options = copy_options(edit, ci)
                source_path = edit['copies'][ci].get('source_path')
                path = source_path or path
                original_source = not source_path
                settings = options.get('image_settings')
            offset = copy_offset(edit, ci)
            local_frames = [position - offset for position in frames if copy_index(edit, position) == ci]
        try:
            buffer.request(path, local_frames, settings)
            if prefetch_only:
                return True
            result = buffer.get(path, local_frames[0], settings)
            if result is None:
                return False
            self._prepared_frame = PreparedFrame(*result, buffer.metadata(path, settings), original_source)
            return True
        except Exception as error:
            self._stop_playback()
            self.status_bar.setText(f'Playback stopped at the last displayed frame: {error}')
            return False

    def _show_transport_frame(self, frame, force=False):
        try:
            if frame != self.frame_slider.value():
                self.frame_slider.setValue(frame)
            elif force:
                self._redraw_selected_frame()
                self._sync_timeline_position(frame)
            if self._playback_active and self._buffer_playback:
                self._throughput_count = getattr(self, '_throughput_count', 0) + 1
                self._update_playback_status()
        finally:
            self._prepared_frame = None

    def _update_playback_status(self):
        """Report actual display throughput, independently of requested playback FPS."""
        clock = getattr(self, '_throughput_clock', None)
        if clock is None or clock.elapsed() < 1000:
            return
        elapsed = clock.elapsed()
        fps = self._throughput_count * 1000 / max(1, elapsed)
        status = getattr(self._preview_buffer, 'status', None)
        ready, wanted = status() if status is not None else (0, 0)
        message = f'Display {fps:.1f} fps'
        if wanted:
            message += f' · buffer {ready}/{wanted}'
        if getattr(self, '_showing_buffering', False):
            message += ' · waiting for drive / processing'
        self.status_bar.setText(message)
        hint = 'Actual preview updates per second. Requested playback FPS and exported timing are unchanged. A slow source drive may need time to fill the buffer.'
        timings = getattr(self._preview_buffer, 'timings', None)
        if timings:
            read_ms, render_ms = timings()
            if read_ms is not None and render_ms is not None:
                hint += f'\nRecent source read/decode: {read_ms:.1f} ms per frame; image processing: {render_ms:.1f} ms per frame.'
        self.status_bar.setToolTip(hint)
        self._preview_status_message = message
        self._throughput_count = 0
        clock.restart()

    def _redraw_selected_frame(self):
        group = getattr(self, 'group_playback', None)
        if group is not None and not group.publishing and group.seek(self.frame_slider.value()):
            return
        prepared = self._prepared_frame
        if prepared is None:
            self.vm.redraw_cb()
        else:
            self.vm.draw_prepared_frame(prepared)

    def _queue_next_tick(self, fps):
        # Schedule against an absolute deadline. Rounding 33.33 ms DOWN to
        # 33 ms and flooring to output frames repeats a frame every second.
        elapsed = self._viewer_clock.elapsed()
        next_tick = math.floor(elapsed * fps / 1000) + 1
        self.playback_timer.start(max(1, math.ceil(next_tick * 1000 / fps - elapsed)))

    def _await_preview(self, tick):
        if self._playback_active:
            if self._stall_clock is None:
                self._stall_clock = QElapsedTimer()
                self._stall_clock.start()
            elif self._stall_clock.elapsed() >= 180 and not getattr(self, '_showing_buffering', False):
                self._showing_buffering = True
                self.active_frame_label.setText('Buffering…')
            self._update_playback_status()
            self._pending_tick = tick
            self.playback_timer.start(4)
