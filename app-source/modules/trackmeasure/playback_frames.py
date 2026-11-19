"""Prepare a small window of playback frames without blocking the Qt thread."""
from itertools import islice
import os
import threading
import time

from pyphantom_image.imagetools import ImageTools

from cine_metadata import CineHandler
from viewer_effects import default_image_settings
from viewer_media import VideoReader, open_media
from viewer_processing import render_cine_image


class PlaybackFrameBuffer:
    """Bounded read/render workers with one independent reader. Indices are source-local."""

    def __init__(self, max_bytes=96 * 1024 * 1024, max_frames=12):
        self._limit = max(1, int(max_bytes))
        self._depth = max(1, int(max_frames))
        self._capacity = self._depth
        self._condition = threading.Condition()
        self._source = None
        self._generation = 0
        self._wanted = ()
        self._ready = {}
        self._raw_ready = {}
        self._rendering = None
        self._metadata = None
        self._index_source = None
        self._index_snapshot = None
        self._closed = False
        self._thread = None
        self._render_thread = None
        self._read_ms = self._render_ms = None

    @staticmethod
    def _source_key(path, settings):
        return os.fspath(path), None if settings is None else tuple(sorted(settings.items()))

    def prime(self, reader):
        """Borrow an already-loaded Cine's detached offset table, never its file."""
        if isinstance(reader, CineHandler) and reader.cine_loaded:
            with self._condition:
                if self._closed:
                    return
                self._index_source = os.fspath(reader.cine_path)
                self._index_snapshot = reader.playback_index()

    def request(self, path, indices, settings):
        """Replace the lookahead, keeping completed and in-flight work in the overlap."""
        source = self._source_key(path, settings)
        indices = tuple(dict.fromkeys(int(i) for i in islice(indices, self._depth)))
        if not indices:
            self.clear()
            return
        with self._condition:
            if self._closed:
                return
            if self._source != source:
                self._reset()
                self._source = source
            wanted = indices[:self._capacity]
            if wanted == self._wanted:
                return
            self._wanted = wanted
            self._ready = {i: item for i, item in self._ready.items() if i in wanted}
            self._raw_ready = {i: item for i, item in self._raw_ready.items() if i in wanted}
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='Cine read-ahead', daemon=True)
                self._render_thread = threading.Thread(target=self._render, name='Cine display processing', daemon=True)
                self._thread.start()
                self._render_thread.start()
            self._condition.notify_all()

    def get(self, path, index, settings):
        """Return (raw, processed), None while pending, or an error for a failed frame."""
        source = self._source_key(path, settings)
        with self._condition:
            item = self._ready.get(int(index)) if source == self._source else None
        if isinstance(item, RuntimeError):
            raise RuntimeError(str(item))
        return item

    def metadata(self, path, settings):
        source = self._source_key(path, settings)
        with self._condition:
            return self._metadata if self._source == source else None

    def _reset(self):
        self._generation += 1
        self._ready.clear()
        self._raw_ready.clear()
        self._rendering = None
        self._metadata = None
        self._source = None
        self._wanted = ()
        self._capacity = self._depth
        self._read_ms = self._render_ms = None

    def clear(self):
        with self._condition:
            self._reset()
            self._index_source = self._index_snapshot = None
            self._condition.notify_all()

    def close(self):
        """Signal shutdown immediately, even when a disk read is still pending."""
        with self._condition:
            self._closed = True
            self._reset()
            self._index_source = self._index_snapshot = None
            self._condition.notify_all()

    def _limit_capacity(self, size):
        """Called under the condition. One oversized frame is allowed to progress."""
        capacity = max(1, self._limit // max(1, size))
        if capacity < self._capacity:
            self._capacity = capacity
            self._wanted = self._wanted[:capacity]
            self._ready = {i: value for i, value in self._ready.items() if i in self._wanted}
            self._raw_ready = {i: value for i, value in self._raw_ready.items() if i in self._wanted}

    def status(self):
        with self._condition:
            return len(self._ready), len(self._wanted)

    def timings(self):
        with self._condition:
            return self._read_ms, self._render_ms

    def _run(self):
        # Disk reads and pixel conversion run on separate workers. A slow USB
        # read can overlap processing the previous frame; neither blocks Qt.
        reader = None
        reader_generation = None
        hinted = ()
        try:
            while True:
                with self._condition:
                    if self._closed:
                        return
                    generation, source = self._generation, self._source
                    snapshot = (self._index_snapshot if source and source[0] == self._index_source else None)
                    wanted = self._wanted
                    index = next((i for i in wanted if i not in self._ready
                                  and i not in self._raw_ready and self._rendering != (generation, i)), None)
                    if index is None and reader_generation == generation:
                        self._condition.wait()
                        continue
                if reader_generation != generation:
                    if reader is not None:
                        reader.close()
                    reader = None
                    reader_generation = generation
                    hinted = ()
                if index is None:
                    continue
                started = time.perf_counter()
                try:
                    if reader is None:
                        reader = open_media(source[0], initial_index=index, index_snapshot=snapshot)
                    if isinstance(reader, CineHandler):
                        if len(wanted) > 1 and wanted[1] - wanted[0] != 1:
                            reader.advise_playback(i for i in wanted if i != index and i not in hinted)
                        hinted = wanted
                    raw = reader.get_img(index)
                    video = isinstance(reader, VideoReader)
                    # Reserve both raw sensor values and processed display pixels.
                    # RGB uses three 8-bit channels, mono two bytes per pixel.
                    display_bytes = (0 if video else raw.shape[0] * raw.shape[1]
                                     * (3 if reader.metadata.CFA != 'Mono' else 2))
                    size = raw.nbytes + display_bytes
                    item = (raw, reader.metadata, video)
                    del raw
                except Exception as exc:
                    item = RuntimeError(f'Could not read playback frame {index}: {exc}')
                    size = 0
                with self._condition:
                    if self._generation == generation and index in self._wanted:
                        duration = (time.perf_counter() - started) * 1000
                        self._read_ms = duration if self._read_ms is None else .2 * duration + .8 * self._read_ms
                        if size:
                            self._limit_capacity(size)
                        if index in self._wanted:
                            if isinstance(item, RuntimeError):
                                self._ready[index] = item
                            else:
                                self._raw_ready[index] = item
                        self._condition.notify_all()
                del item
        finally:
            if reader is not None:
                reader.close()

    def _render(self):
        image_tools = ImageTools()
        while True:
            with self._condition:
                if self._closed:
                    return
                generation, source = self._generation, self._source
                index = next((i for i in self._wanted if i in self._raw_ready), None)
                if index is None:
                    self._condition.wait()
                    continue
                self._rendering = (generation, index)
                raw, metadata, video = self._raw_ready.pop(index)
            started = time.perf_counter()
            try:
                settings = (dict(source[1]) if source[1] is not None else default_image_settings(metadata))
                image = raw if video else render_cine_image(raw, metadata, settings, image_tools)
                item = raw, image
                size = raw.nbytes + (image.nbytes if image is not raw else 0)
                del image
            except Exception as exc:
                item = RuntimeError(f'Could not prepare playback frame {index}: {exc}')
                size = 0
            del raw
            with self._condition:
                if self._generation == generation and index in self._wanted:
                    duration = (time.perf_counter() - started) * 1000
                    self._render_ms = duration if self._render_ms is None else .2 * duration + .8 * self._render_ms
                    if size:
                        self._metadata = metadata
                        self._limit_capacity(size)
                    if index in self._wanted:
                        self._ready[index] = item
                if self._rendering == (generation, index):
                    self._rendering = None
                self._condition.notify_all()
            del item
