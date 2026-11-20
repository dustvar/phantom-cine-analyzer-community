"""Serial, off-thread access to a completed Event Finder recording."""
from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Event
from time import monotonic

import numpy as np
from PySide6.QtCore import QThread, Signal

from event_cine_source import EventCineSource
from event_detection import EventScanCache, scan_events


@dataclass(frozen=True)
class RecordingInfo:
    width: int
    height: int
    count: int
    first: int
    fps: float | None
    file_signature: tuple | None
    transfer_modes: frozenset = frozenset()


class RecordingWorker(QThread):
    """The reader lives on this thread. A newer seek replaces any queued seek."""
    previewReady = Signal(int, object, int, object)
    scanReady = Signal(int, object)
    progress = Signal(int, int, int)
    failed = Signal(int, str)
    aborted = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.condition = Condition()
        self.pending = None
        self.cancel = Event()
        self.stopping = False

    def submit(self, ticket, source, index, scan=None):
        with self.condition:
            self.cancel.set()
            self.cancel = Event()
            self.pending = (ticket, source, index, scan, self.cancel)
            self.condition.notify()

    def interrupt(self):
        with self.condition:
            self.cancel.set()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.cancel.set()
            self.pending = None
            self.condition.notify()

    def run(self):
        cache = EventScanCache()

        def check_file(path, signature):
            stat = Path(path).stat()
            if (stat.st_size, stat.st_mtime_ns) != signature:
                cache.clear()
                raise ValueError('This Cine file changed. Reopen it before scanning.')

        reader = source = info = None
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.stopping or self.pending is not None)
                    if self.stopping:
                        return
                    ticket, requested, index, scan, cancel = self.pending
                    self.pending = None
                try:
                    if source != requested or reader is None:
                        if reader is not None:
                            reader.close()
                        reader = source = None
                        if requested is None:
                            continue
                        signature = None
                        if isinstance(requested, str):
                            reader = EventCineSource(requested)
                            stat = Path(requested).stat()
                            signature = (stat.st_size, stat.st_mtime_ns)
                        else:
                            reader = requested.open()
                        info = RecordingInfo(reader.width, reader.height, reader.count,
                                             reader.first_frame, reader.fps, signature,
                                             frozenset(getattr(reader, 'transfer_modes', ())))
                        if info.count < 1:
                            raise ValueError('This recording has no available frames.')
                        source = requested
                    if hasattr(reader, 'set_cancel_check'):
                        reader.set_cancel_check(cancel.is_set)
                    if cancel.is_set():
                        raise CancelledError()
                    if scan is None:
                        index = max(0, min(index, info.count-1))
                        image = np.array(reader.preview(index), dtype=np.uint8, order='C', copy=True)
                        if image.shape != (info.height, info.width):
                            raise ValueError('The preview dimensions do not match the recording.')
                        if not cancel.is_set():
                            self.previewReady.emit(ticket, info, index, image)
                    else:
                        roi, sensitivity, first, last, step, signal, mode = scan
                        if not 0 <= first <= last < info.count:
                            raise ValueError("The scan range is outside this recording.")
                        if hasattr(reader, 'set_transfer_mode'):
                            reader.set_transfer_mode(mode)
                        if isinstance(requested, str):
                            check_file(requested, info.file_signature)
                        cache_key = (requested, info.file_signature, roi, mode)
                        if cache.contains(cache_key, first, last, step, signal) and hasattr(reader, 'validate_recording'):
                            reader.validate_recording()

                        def begin_sequence():
                            if hasattr(reader, 'set_scan_stride'):
                                reader.set_scan_stride(step, first, last)

                        last_update = 0.

                        def report(done, total):
                            nonlocal last_update
                            now = monotonic()
                            if now - last_update >= .1 or done == total:
                                self.progress.emit(ticket, done, total)
                                last_update = now

                        result = scan_events(lambda i: reader.read_roi(i, roi), first, last,
                                             step=step, signal=signal, cache=cache, cache_key=cache_key,
                                             begin_sequence=begin_sequence,
                                             sensitivity=sensitivity, progress=report,
                                             cancelled=cancel.is_set)
                        if cancel.is_set():
                            raise CancelledError()
                        if isinstance(requested, str):
                            check_file(requested, info.file_signature)
                        else:
                            reader.close()
                            reader = source = None
                        self.scanReady.emit(ticket, result)
                except CancelledError:
                    if reader is not None and not isinstance(source, str):
                        reader.close()
                        reader = source = None
                    self.aborted.emit(ticket)
                except Exception as exc:
                    # A disconnected or invalidated recording needs a fresh reader on retry.
                    if reader is not None:
                        reader.close()
                    source = reader = None
                    self.failed.emit(ticket, str(exc) or type(exc).__name__)
        finally:
            cache.clear()
            if reader is not None:
                reader.close()
