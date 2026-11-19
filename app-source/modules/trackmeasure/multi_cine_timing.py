"""Capture-time alignment for independent Cine recordings, in sensor seconds.

A clock belongs to one loaded source and is prepared once, never once per frame.
Trigger-relative times deliberately ignore recording dates, display calibration,
playback FPS and timeline edits. No missing image is fabricated across a gap.
"""
from dataclasses import dataclass
import math
import numbers
import numpy as np


@dataclass(frozen=True)
class CaptureClock:
    times: np.ndarray
    period: float
    estimated: bool
    note: str

    @property
    def count(self):
        return len(self.times)

    @classmethod
    def from_handler(cls, handler):
        metadata = handler.metadata
        count = int(metadata.ImageCount)
        if count < 1:
            raise ValueError('This Cine has no recorded frames to synchronize.')
        raw = handler.capture_timestamps_from_trigger()
        fps = float(getattr(metadata, 'FrameRate', 0) or 0)
        decimation = float(getattr(metadata, 'fDecimation', 1) or 1)
        cadence_valid = math.isfinite(fps) and fps > 0 and math.isfinite(decimation) and decimation >= 1
        if raw is None:
            if not cadence_valid:
                raise ValueError('A valid capture frame rate is needed when Cine timestamps are unavailable.')
            period = decimation/fps
            first = float(metadata.FirstImageNo)/fps
            times = first + np.arange(count, dtype=np.float64)*period
            note = 'Estimated from Cine frame numbers and saved cadence; timestamp tag unavailable'
        else:
            times = np.array(raw, dtype=np.float64, copy=True)
            if times.ndim != 1 or len(times) != count:
                raise ValueError('Capture timestamp count does not match the recorded Cine frames.')
            delta = np.diff(times)
            if not np.all(np.isfinite(times)) or np.any(delta <= 0):
                raise ValueError('Capture timestamps must be finite and strictly increasing for synchronization.')
            if count > 1:
                period = float(np.median(delta))
                # A short clip can consist entirely of gaps. Do not infer a
                # slower cadence that would make missing captures look valid.
                if cadence_valid and period > 1.5*decimation/fps:
                    period = decimation/fps
            elif cadence_valid:
                period = decimation/fps
            else:
                # One timestamp is usable only at its exact instant without a cadence.
                period = 0.0
            note = 'Cine capture timestamps relative to trigger T0'
        if not np.all(np.isfinite(times)):
            raise ValueError('Cine frame timing is not finite.')
        times.setflags(write=False)
        return cls(times, period, raw is None, note)

    def time_at(self, index):
        if isinstance(index, bool) or not isinstance(index, numbers.Integral) or not 0 <= index < self.count:
            raise ValueError('Frame is outside this Cine recording.')
        return float(self.times[index])

    def index_at(self, time):
        time = float(time)
        if not math.isfinite(time):
            return None
        insertion = int(np.searchsorted(self.times, time))
        candidates = [i for i in (insertion-1, insertion) if 0 <= i < self.count]
        if not candidates:
            return None
        # Earlier exposure wins an exact tie, consistently in forward/reverse playback.
        index = min(candidates, key=lambda i: abs(float(self.times[i])-time))
        tolerance = self.period*.501 + 1e-12
        return index if abs(float(self.times[index])-time) <= tolerance else None
