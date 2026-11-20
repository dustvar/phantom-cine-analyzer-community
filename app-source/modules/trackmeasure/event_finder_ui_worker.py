"""Serial, off-thread access to a completed Event Finder recording."""
from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Event
from time import monotonic

import numpy as np
from PySide6.QtCore import QThread, Signal

from event_cine_source import EventCineSource
from event_detection import scan_events


@dataclass(frozen=True)
class RecordingInfo:
    width: int
    height: int
    count: int
    first: int
    fps: float | None
    file_signature: tuple | None


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
                                             reader.first_frame, reader.fps, signature)
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
                        roi, sensitivity = scan
                        last_update = 0.

                        def report(done, total):
                            nonlocal last_update
                            now = monotonic()
                            if now - last_update >= .1 or done == total:
                                self.progress.emit(ticket, done, total)
                                last_update = now

                        result = scan_events(lambda i: reader.read_roi(i, roi), 0, info.count-1,
                                             sensitivity=sensitivity, progress=report,
                                             cancelled=cancel.is_set)
                        if cancel.is_set():
                            raise CancelledError()
                        if not isinstance(requested, str):
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
            if reader is not None:
                reader.close()
