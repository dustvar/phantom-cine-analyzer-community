"""Find short-lived intensity changes in selected frames of a completed recording.

All indices use the frame numbers supplied by the caller. The input callback
reads a fixed, linear grayscale ROI. Stored measurements are one to three scalars
per analyzed frame; the scan never keeps the recording's image sequence in memory.
"""
from concurrent.futures import CancelledError
from dataclasses import dataclass
import heapq
from tempfile import SpooledTemporaryFile
from threading import Lock

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


def _mean_intensity(image):
    if image.dtype in (np.uint8, np.uint16, np.int16, np.float32, np.float64):
        return cv2.mean(image)[0]
    return np.mean(image, dtype=np.float64)


def _reference(read_roi, frame_at, count, cancelled):
    initial = _read(read_roi, frame_at(0))
    # Keep the reference stack compact even for a full-sensor ROI. For a large
    # source retain at least three observations so a lone event can be rejected.
    limit = max(3, min(9, (64 << 20)//initial.nbytes))
    offsets = _sample_indices(count, limit)
    anchors = np.empty((len(offsets), *initial.shape), initial.dtype)
    for i, offset in enumerate(offsets):
        _check_cancel(cancelled)
        anchors[i] = initial if offset == 0 else _read(read_roi, frame_at(int(offset)), initial.shape)
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


def sample_count(first, last, step=1):
    """Count the regular sample grid plus the last source frame when off-grid."""
    if type(first) is not int or type(last) is not int or last < first:
        raise ValueError('Choose an inclusive range of whole frame numbers.')
    if type(step) is not int or step < 1:
        raise ValueError('Choose a positive whole frame step.')
    return 1 + (last-first+step-1)//step


@dataclass
class _Evidence:
    records: object
    sampled: np.ndarray
    first: int
    last: int
    step: int
    quantum: float
    pixels: int


class EventScanCache:
    """Own at most one completed feature spool per panel, up to 64 MiB on disk."""

    def __init__(self):
        self._lock = Lock()
        self._key = None
        self._evidence = None

    def contains(self, key, first, last, step=1, signal='spatial'):
        with self._lock:
            return self._key == (key, first, last, step, signal)

    def _rerank(self, key, threshold, cancelled):
        with self._lock:
            if self._key == key:
                return _rank(self._evidence, threshold, cancelled)
        return None

    def _put(self, key, evidence):
        with self._lock:
            if self._evidence is not None:
                self._evidence.records.close()
            self._key = key
            self._evidence = evidence

    def clear(self):
        self._put(None, None)


def _rank(evidence, threshold, cancelled):
    records, sampled = evidence.records, evidence.sampled
    first, last, step = evidence.first, evidence.last, evidence.step
    count = sample_count(first, last, step)
    def source_frame(offset):
        return min(first+step*offset, last)
    center = np.median(sampled, axis=0)
    mad = np.median(np.abs(sampled-center), axis=0)
    # Flat integer footage can have zero measured variance. Fractions of a
    # source intensity count provide finite, ROI-size-aware floors.
    features = sampled.shape[1]
    whole_roi_floor = .20/np.sqrt(evidence.pixels)
    floors = evidence.quantum*np.array([whole_roi_floor, whole_roi_floor, .20][:features])
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
        data = records.read(_BUFFER_FRAMES * features * np.dtype(np.float64).itemsize)
        z = (np.frombuffer(data, np.float64).reshape(-1, features)-center)/scale
        signed = z[:, 0].copy()
        z[:, 0] = np.abs(signed)
        channels = np.argmax(z, axis=1)
        scores = np.max(z, axis=1)
        # Follow weaker shoulders only when the candidate contains a strong peak.
        active = scores >= threshold*.72
        boundaries = np.flatnonzero(np.diff(active.astype(np.int8)))+1
        for lo, hi in zip(np.r_[0, boundaries], np.r_[boundaries, len(active)]):
            index = offset+int(lo)
            if not active[lo]:
                if start is not None:
                    finish(source_frame(index-1))
                    start = None
                continue
            i = int(lo+np.argmax(scores[lo:hi]))
            if start is None:
                start = source_frame(index)
                best_score = -1.
            if scores[i] > best_score:
                best_score = float(scores[i])
                best_frame = source_frame(offset+i)
                brightness_reason = 'Region brightened' if signed[i] > 0 else 'Region darkened'
                if features == 1:
                    brightness_reason = 'Mean brightened' if signed[i] > 0 else 'Mean darkened'
                reason = (brightness_reason,
                          'Widespread pixel change', 'Localized pixel change')[channels[i]]
    if start is not None:
        finish(last)
    return EventScan(tuple(entry[2] for entry in sorted(strongest, reverse=True)), count, total)


def scan_events(read_roi, first, last, *, sensitivity='balanced', step=1, signal='spatial',
                progress=None, cancelled=None, begin_sequence=None, cache=None, cache_key=None):
    """Analyze selected native frame numbers, always including both endpoints.

    Results bound observed samples. A larger step can miss short changes. A
    caller-supplied cache key identifies immutable source content, ROI and read
    precision. Range, step and signal are appended automatically. Sensitivity
    only affects ranking, so a cache hit requires no image reads.
    """
    count = sample_count(first, last, step)
    if sensitivity not in _THRESHOLDS:
        raise ValueError('Choose high, balanced or low sensitivity.')
    if signal not in ('spatial', 'mean'):
        raise ValueError('Choose spatial changes or mean brightness.')
    if cache is not None and cache_key is None:
        raise ValueError('Identify the recording and region when caching measurements.')
    threshold = _THRESHOLDS[sensitivity]
    key = (cache_key, first, last, step, signal)
    if progress:
        progress(0, count)
    _check_cancel(cancelled)
    if cache is not None:
        result = cache._rerank(key, threshold, cancelled)
        if result is not None:
            if progress:
                progress(count, count)
            return result

    def frame_at(offset):
        return min(first+step*offset, last)

    if signal == 'spatial' and count > 1:
        reference, quantum = _reference(read_roi, frame_at, count, cancelled)
        measure = _Measurements(reference).measure
        shape = reference.shape
        features = _FEATURE_COUNT
    else:
        # The mean itself has a robust temporal reference during ranking;
        # random reference image downloads would add no information here.
        shape = None
        features = 1
        measure = _mean_intensity
    if begin_sequence is not None:
        begin_sequence()
    calibration = _sample_indices(count, 4096)
    sampled = np.empty((len(calibration), features), np.float64)
    sample_pos = 0
    batch = np.empty((_BUFFER_FRAMES, features), np.float64)
    records = SpooledTemporaryFile(max_size=1 << 20)
    try:
        for offset in range(count):
            _check_cancel(cancelled)
            frame = frame_at(offset)
            image = _read(read_roi, frame, shape)
            if shape is None:
                shape = image.shape
                quantum = (max(float(np.max(np.abs(image))), 1e-12)*np.finfo(np.float32).eps
                           if image.dtype.kind == 'f' else 1.)
            row = offset % _BUFFER_FRAMES
            batch[row] = measure(image)
            if sample_pos < len(calibration) and offset == calibration[sample_pos]:
                sampled[sample_pos] = batch[row]
                sample_pos += 1
            if row == _BUFFER_FRAMES-1 or offset+1 == count:
                records.write(batch[:row+1].tobytes())
            if progress and ((offset+1) % 16 == 0 or offset+1 == count):
                progress(offset+1, count)
        evidence = _Evidence(records, sampled, first, last, step, quantum, int(np.prod(shape)))
        result = _rank(evidence, threshold, cancelled)
        if cache is not None and count*features*8 <= (64 << 20):
            cache._put(key, evidence)
            records = None  # The panel now owns this completed spool.
        return result
    finally:
        if records is not None:
            records.close()
