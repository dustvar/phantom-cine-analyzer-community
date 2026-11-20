"""Find short-lived intensity changes in a completed recording at source cadence.

All indices use the frame numbers supplied by the caller. The input callback
reads a fixed, linear grayscale ROI. Stored measurements are three scalars
per frame; the scan never keeps the recording's image sequence in memory.
"""
from concurrent.futures import CancelledError
from dataclasses import dataclass
import heapq
from tempfile import SpooledTemporaryFile

import cv2
import numpy as np


_THRESHOLDS = {'high': 5.5, 'balanced': 8.0, 'low': 12.0}
_FEATURE_COUNT = 3
_BUFFER_FRAMES = 1024
_MAX_EVENTS = 1000


@dataclass(frozen=True)
class Event:
    first: int
    peak: int
    last: int
    score: float
    reason: str


@dataclass(frozen=True)
class EventScan:
    events: tuple
    frame_count: int
    total_events: int


def _sample_indices(count, limit):
    """Reproducible samples spread through the range without locking to flicker."""
    if count <= limit:
        return np.arange(count, dtype=np.int64)
    boundaries = np.linspace(0, count, limit+1, dtype=np.int64)
    return np.random.default_rng(1729).integers(boundaries[:-1], boundaries[1:])


def _check_cancel(cancelled):
    if cancelled is not None and cancelled():
        raise CancelledError()


def _read(read_roi, frame, shape=None):
    roi = np.asarray(read_roi(frame))
    if roi.ndim != 2 or not roi.size or roi.dtype.kind not in 'uif':
        raise ValueError('Read a nonempty, two-dimensional grayscale region for each frame.')
    if shape is not None and roi.shape != shape:
        raise ValueError(f'The analysis region changed size at frame {frame:,}.')
    if roi.dtype.kind == 'f' and not np.isfinite(roi).all():
        raise ValueError(f'The analysis region contains nonfinite values at frame {frame:,}.')
    return roi


def _reference(read_roi, first, count, cancelled):
    initial = _read(read_roi, first)
    # Keep the reference stack compact even for a full-sensor ROI. For a large
    # source retain at least three observations so a lone event can be rejected.
    limit = max(3, min(9, (64 << 20)//initial.nbytes))
    offsets = _sample_indices(count, limit)
    anchors = np.empty((len(offsets), *initial.shape), initial.dtype)
    for i, offset in enumerate(offsets):
        _check_cancel(cancelled)
        anchors[i] = initial if offset == 0 else _read(read_roi, first+int(offset), initial.shape)
    # Median on each pixel preserves sharp stationary details; performing it in
    # strips also bounds NumPy's temporary sorting workspace on large regions.
    template = np.empty(initial.shape, np.float32)
    for y in range(0, len(template), 64):
        _check_cancel(cancelled)
        template[y:y+64] = np.median(anchors[:, y:y+64], axis=0, overwrite_input=True)
    if initial.dtype.kind in 'ui':
        quantum = 1.
    else:
        quantum = max(float(np.max(np.abs(template))), 1e-12)*np.finfo(np.float32).eps
    return template, quantum


class _Measurements:
    def __init__(self, template):
        self.template = template
        self.delta = np.empty_like(template)
        self.deviation = np.empty_like(template)
        self.patch = np.empty_like(template)
        self.kernel = (min(3, template.shape[1]), min(3, template.shape[0]))

    def measure(self, image):
        np.subtract(image, self.template, out=self.delta, dtype=np.float32)
        brightness = cv2.mean(self.delta)[0]
        cv2.absdiff(self.delta, brightness, dst=self.deviation)
        # Local changes are measured after removing the common brightness
        # shift. A tiny bubble need not move the average of a large ROI.
        spread = cv2.mean(self.deviation)[0]
        cv2.blur(self.deviation, self.kernel, dst=self.patch, borderType=cv2.BORDER_CONSTANT)
        peak = cv2.minMaxLoc(self.patch)[1]
        return brightness, spread, peak


def _rank(records, sampled, first, count, quantum, pixels, threshold, cancelled):
    center = np.median(sampled, axis=0)
    mad = np.median(np.abs(sampled-center), axis=0)
    # Flat integer footage can have zero measured variance. Fractions of a
    # source intensity count provide finite, ROI-size-aware floors.
    floors = quantum*np.array([.20/np.sqrt(pixels), .20/np.sqrt(pixels), .20])
    scale = np.maximum(1.4826*mad, floors)
    strongest = []
    total = 0
    start = None
    best_frame = best_score = reason = None

    def finish(last):
        nonlocal total
        if best_score >= threshold:
            total += 1
            hit = Event(start, best_frame, last, best_score, reason)
            key = (best_score, -best_frame, hit)
            if len(strongest) < _MAX_EVENTS:
                heapq.heappush(strongest, key)
            elif key[:2] > strongest[0][:2]:
                heapq.heapreplace(strongest, key)

    records.seek(0)
    for offset in range(0, count, _BUFFER_FRAMES):
        _check_cancel(cancelled)
        data = records.read(_BUFFER_FRAMES * _FEATURE_COUNT * np.dtype(np.float64).itemsize)
        z = (np.frombuffer(data, np.float64).reshape(-1, _FEATURE_COUNT)-center)/scale
        signed = z[:, 0].copy()
        z[:, 0] = np.abs(signed)
        channels = np.argmax(z, axis=1)
        scores = np.max(z, axis=1)
        # Follow weaker shoulders only when the candidate contains a strong peak.
        active = scores >= threshold*.72
        boundaries = np.flatnonzero(np.diff(active.astype(np.int8)))+1
        for lo, hi in zip(np.r_[0, boundaries], np.r_[boundaries, len(active)]):
            frame = first+offset+int(lo)
            if not active[lo]:
                if start is not None:
                    finish(frame-1)
                    start = None
                continue
            i = int(lo+np.argmax(scores[lo:hi]))
            if start is None:
                start = frame
                best_score = -1.
            if scores[i] > best_score:
                best_score = float(scores[i])
                best_frame = first+offset+i
                reason = ('Region brightened' if signed[i] > 0 else 'Region darkened',
                          'Widespread pixel change', 'Localized pixel change')[channels[i]]
    if start is not None:
        finish(first+count-1)
    return EventScan(tuple(entry[2] for entry in sorted(strongest, reverse=True)), count, total)


def scan_events(read_roi, first, last, *, sensitivity='balanced', progress=None, cancelled=None):
    """Scan every frame; progress(done,total) counts the sequential pass.

    A per-pixel median reference and robust temporal calibration suppress
    static texture and ordinary sensor variation. Scores express departures
    from the recording's usual range. Up to 1,000 strongest excursions are
    returned with inclusive endpoints and the first frame of their highest score.
    """
    if type(first) is not int or type(last) is not int or last < first:
        raise ValueError('Choose an inclusive range of whole frame numbers.')
    if sensitivity not in _THRESHOLDS:
        raise ValueError('Choose high, balanced or low sensitivity.')
    count = last-first+1
    if progress:
        progress(0, count)
    _check_cancel(cancelled)
    if count == 1:
        _read(read_roi, first)
        if progress:
            progress(1, 1)
        return EventScan((), 1, 0)
    reference, quantum = _reference(read_roi, first, count, cancelled)
    measurements = _Measurements(reference)
    calibration = _sample_indices(count, 4096)
    sampled = np.empty((len(calibration), _FEATURE_COUNT), np.float64)
    sample_pos = 0
    batch = np.empty((_BUFFER_FRAMES, _FEATURE_COUNT), np.float64)
    with SpooledTemporaryFile(max_size=1 << 20) as records:
        for offset in range(count):
            _check_cancel(cancelled)
            image = _read(read_roi, first+offset, reference.shape)
            row = offset % _BUFFER_FRAMES
            batch[row] = measurements.measure(image)
            if sample_pos < len(calibration) and offset == calibration[sample_pos]:
                sampled[sample_pos] = batch[row]
                sample_pos += 1
            if row == _BUFFER_FRAMES-1 or offset+1 == count:
                records.write(batch[:row+1].tobytes())
            if progress and ((offset+1) % 16 == 0 or offset+1 == count):
                progress(offset+1, count)
        return _rank(records, sampled, first, count, quantum, reference.size,
                     _THRESHOLDS[sensitivity], cancelled)
