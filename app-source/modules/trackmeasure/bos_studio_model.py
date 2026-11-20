"""Frame-accurate ranges and presentation timing for one analyzed BOS clip.

Clip-local indices start at zero. Both endpoints of each span are included.
Playback rate is measured in source frames per second of presentation time.
"""
from bisect import bisect_right
from dataclasses import dataclass, replace
from fractions import Fraction
import math

import numpy as np

from bos_sampling import RangeSampling
from bos_speed_clock import RampClock, ramp_limit


MAX_PLAYBACK_FPS = 1000
MAX_MP4_FPS = 240


@dataclass(frozen=True)
class OverlaySpan:
    first: int
    last: int
    fade_in: float = 0.
    fade_out: float = 0.


@dataclass(frozen=True)
class SpeedSpan:
    first: int
    last: int
    fps: float
    ramp: float = 0.
    curve: str = 'smooth'


def _rate(value, maximum=MAX_PLAYBACK_FPS):
    value = float(value)
    if not math.isfinite(value) or not 1 <= value <= maximum:
        raise ValueError(f'Choose a rate from 1 to {maximum:,} fps.')
    return value


def _index(value):
    if type(value) is not int:
        raise ValueError('Choose a whole frame.')
    return value


def _clamp(value, low, high):
    return max(low, min(value, high))


class StudioEdit:
    def __init__(self, count, fps=30., *, sampling=None):
        if type(count) is not int or count < 1:
            raise ValueError('Record Studio needs at least one frame.')
        self.sampling = sampling or RangeSampling(count)
        if self.sampling.count != count:
            raise ValueError('The frame sampling does not match the analyzed clip.')
        self.count = count
        self.overlays = [OverlaySpan(0, count-1)]
        self.vectors = self.overlays.copy()
        self.vector_style = None
        self.speeds = [SpeedSpan(0, count-1, _rate(fps))]
        self._refresh_timing()

    def copy(self, *, sampling=None):
        edit = StudioEdit(self.count, sampling=sampling or self.sampling)
        edit.speeds = self.speeds.copy()
        edit.overlays = self.overlays.copy()
        edit.vectors = self.vectors.copy()
        edit.vector_style = self.vector_style
        edit._refresh_timing()
        if edit.sampling == self.sampling:
            edit._alphas = self._alphas.copy()
        return edit

    def _refresh_timing(self):
        self._speed_starts = [s.first for s in self.speeds]
        self._alphas = {}
        self._ramp_clock = None
        if any(s.ramp for s in self.speeds[1:]):
            self._ramp_clock = RampClock(self.speeds, self.sampling)
            for i, actual in self._ramp_clock.actual.items():
                if self.speeds[i].ramp != actual:
                    self.speeds[i] = replace(self.speeds[i], ramp=actual)
            self._exact_duration = self._ramp_clock.duration
            self._time_starts = [self._ramp_clock.time_at(self.sampling.offset(i)) for i in self._speed_starts]
            return
        self._time_starts = []
        duration = Fraction()
        for span in self.speeds:
            self._time_starts.append(float(duration))
            source_frames = self.sampling.offset(span.last+1) - self.sampling.offset(span.first)
            duration += source_frames / Fraction(str(span.fps))
        self._exact_duration = duration

    @property
    def duration(self):
        return float(self._exact_duration)

    def speed_index(self, frame):
        return max(0, bisect_right(self._speed_starts, frame)-1)

    def spans(self, layer):
        if layer == 'overlay':
            return self.overlays
        if layer == 'vectors':
            return self.vectors
        raise ValueError('Choose an overlay or vector track.')

    def overlay_index(self, frame, layer='overlay'):
        for index, span in enumerate(self.spans(layer)):
            if span.first <= frame <= span.last:
                return index
        return None

    def rate_at(self, frame):
        if self._ramp_clock is not None:
            return self.rate_at_time(self.time_at(frame))
        return self.speeds[self.speed_index(frame)].fps

    def rate_at_time(self, seconds):
        if not math.isfinite(seconds):
            raise ValueError('Choose a finite time.')
        if self._ramp_clock is not None:
            return self._ramp_clock.sample(_clamp(seconds, 0., self.duration))[1]
        return self.speeds[self.speed_index_at_time(seconds)].fps

    def time_at(self, frame):
        if frame >= self.count:
            return self.duration
        frame = max(0, frame)
        if self._ramp_clock is not None:
            return self._ramp_clock.time_at(self.sampling.offset(frame))
        i = self.speed_index(frame)
        span = self.speeds[i]
        return self._time_starts[i] + (self.sampling.offset(frame)-self.sampling.offset(span.first))/span.fps

    def frame_at(self, seconds):
        if not math.isfinite(seconds):
            raise ValueError('Choose a finite time.')
        seconds = _clamp(seconds, 0., self.duration)
        if self._ramp_clock is not None:
            return self.sampling.index_at(self._ramp_clock.sample(seconds)[0])
        # An exact boundary can round just below an integer after converting
        # decimal FPS and subtracting preceding segment durations.
        tolerance = 4*math.ulp(max(1., seconds))
        i = self.speed_index_at_time(seconds+tolerance)
        span = self.speeds[i]
        offset = math.floor((seconds-self._time_starts[i]+tolerance)*span.fps)
        return _clamp(self.sampling.index_at(self.sampling.offset(span.first)+offset), span.first, span.last)

    def nearest_boundary(self, seconds):
        """Choose the nearest sampled start or the exclusive end of the movie."""
        left = self.frame_at(seconds)
        right = min(left+1, self.count)
        return right if seconds-self.time_at(left) > self.time_at(right)-seconds else left

    def speed_boundary_at_fraction(self, cut, fraction_delta):
        """Find the cut under the pointer as the total movie length changes."""
        left, right = self.speeds[cut-1:cut+1]
        original = self.sampling.offset(right.first)
        elapsed = self.time_at(right.first)
        fraction = _clamp(elapsed/self.duration + fraction_delta, 0., 1.)
        if self._ramp_clock is not None:
            # A drag can shorten an adjacent ramp. Solve on the resulting
            # clocks, so the cut stays under the pointer as the ruler rescales.
            probe = self.copy()
            def at(candidate):
                probe.speeds = self.speeds.copy()
                probe.move_speed_cut(cut, candidate)
                return probe.time_at(candidate)/probe.duration
            low, high = left.first+1, right.last
            while high-low > 1:
                mid = (low+high)//2
                if at(mid) < fraction:
                    low = mid
                else:
                    high = mid
            return min((low, high), key=lambda candidate: abs(at(candidate)-fraction))
        # Advancing the cut by N source frames lengthens the left section by
        # N/left.fps and shortens the right by N/right.fps. Solve for N using
        # the requested fraction of the resulting movie length.
        scale = (1-fraction)/left.fps + fraction/right.fps
        n = (fraction*self.duration-elapsed)/scale
        return _clamp(self.sampling.nearest(original+n), left.first+1, right.last)

    def speed_index_at_time(self, seconds):
        return max(0, bisect_right(self._time_starts, seconds)-1)

    def preview_frame(self, frame, refresh_hz):
        """Choose a source frame on the display grid, preserving cut boundaries."""
        span = self.speeds[self.speed_index(frame)]
        if self._ramp_clock is not None:
            origin = self.time_at(span.first)
            next_tick = self._next_preview_tick(frame, origin, refresh_hz)
            return _clamp(self.frame_at(origin+(next_tick-1)/refresh_hz), span.first, frame)
        step = self._display_step(span.fps, refresh_hz)
        return span.first + ((frame-span.first)//step)*step

    def _next_preview_tick(self, frame, origin, refresh_hz):
        next_time = self.time_at(frame+1)
        ticks = (next_time-origin)*refresh_hz
        tolerance = 8*math.ulp(max(1., next_time))*refresh_hz
        return max(1, math.ceil(ticks-tolerance))

    def _display_step(self, fps, refresh_hz):
        # Existing rates use consecutive frames. For very fast sections, avoid
        # decoding hundreds of lossless frames each second that cannot be shown.
        samples_per_second = fps / self.sampling.step
        return max(1, round(samples_per_second/refresh_hz)) if fps > 240 else 1

    def preview_indices(self, first, capacity, refresh_hz):
        """Prefetch just the frames playback can present. Exact seeks stay exact."""
        frames = [first]
        while len(frames) < capacity and frames[-1] < self.count-1:
            current = frames[-1]
            span = self.speeds[self.speed_index(current)]
            if self._ramp_clock is not None:
                origin = self.time_at(span.first)
                tick = self._next_preview_tick(current, origin, refresh_hz)
                following = max(current+1, self.frame_at(origin+tick/refresh_hz))
            else:
                step = self._display_step(span.fps, refresh_hz)
                following = span.first + ((current-span.first)//step+1)*step
            frames.append(min(following, span.last+1, self.count-1))
        return tuple(frames)

    def output_count(self, output_fps):
        return math.ceil(self._exact_duration * Fraction(str(_rate(output_fps, MAX_MP4_FPS))))

    def output_frames(self, output_fps):
        output_fps = _rate(output_fps, MAX_MP4_FPS)
        for i in range(self.output_count(output_fps)):
            yield self.frame_at(i / output_fps)

    def split_speed(self, frame):
        frame = _index(frame)
        i = self.speed_index(frame)
        span = self.speeds[i]
        if not span.first < frame <= span.last:
            raise ValueError('Place the playhead inside a speed section to split it.')
        self.speeds[i:i+1] = [replace(span, last=frame-1), replace(span, first=frame, ramp=0., curve='smooth')]
        self._refresh_timing()
        return i+1

    def ramp_limit(self, cut, curve='smooth'):
        if not 1 <= cut < len(self.speeds):
            raise ValueError('Choose a cut between two speed sections.')
        if curve not in ('smooth', 'linear'):
            raise ValueError('Choose Smooth or Linear for the speed ramp.')
        return ramp_limit(self.speeds, self.sampling, cut, curve)

    def ramp_bounds(self, cut):
        span = self.speeds[cut]
        center = self.time_at(span.first)
        return max(0., center-span.ramp/2), min(self.duration, center+span.ramp/2)

    def set_ramp(self, cut, duration, curve='smooth'):
        maximum = self.ramp_limit(cut, curve)
        if not math.isfinite(duration) or duration < 0:
            raise ValueError('Choose a finite, non-negative ramp duration.')
        self.speeds[cut] = replace(self.speeds[cut], ramp=min(float(duration), maximum), curve=curve)
        self._refresh_timing()
        return cut

    def set_speed(self, index, fps):
        self.speeds[index] = replace(self.speeds[index], fps=_rate(fps))
        self._refresh_timing()
        return index

    def set_all_rates(self, fps):
        fps = _rate(fps)
        self.speeds = [replace(s, fps=fps) for s in self.speeds]
        self._refresh_timing()

    def move_speed_cut(self, index, frame):
        if not 1 <= index < len(self.speeds):
            raise ValueError('Choose a cut between two speed sections.')
        left, right = self.speeds[index-1:index+1]
        frame = _clamp(_index(frame), left.first+1, right.last)
        self.speeds[index-1:index+1] = [replace(left, last=frame-1), replace(right, first=frame)]
        self._refresh_timing()
        return index

    def remove_speed_cut(self, index):
        if not 1 <= index < len(self.speeds):
            raise ValueError('Choose a cut between two speed sections.')
        self.speeds[index-1:index+1] = [replace(self.speeds[index-1], last=self.speeds[index].last)]
        self._refresh_timing()
        return index-1

    def split_overlay(self, index, frame, layer='overlay'):
        spans = self.spans(layer)
        frame = _index(frame)
        span = spans[index]
        if not span.first < frame <= span.last:
            raise ValueError('Place the playhead inside the selected block to split it.')
        spans[index:index+1] = [replace(span, last=frame-1, fade_out=0.),
                                  replace(span, first=frame, fade_in=0.)]
        self._alphas.pop(layer, None)
        return index+1

    def add_overlay(self, first, last, layer='overlay'):
        spans = self.spans(layer)
        if not 0 <= _index(first) <= _index(last) < self.count:
            raise ValueError('Choose a range within the clip.')
        if any(first <= s.last and last >= s.first for s in spans):
            raise ValueError('Choose an empty part of the selected track.')
        i = bisect_right([s.first for s in spans], first)
        spans.insert(i, OverlaySpan(first, last))
        self._alphas.pop(layer, None)
        return i

    def _overlay_limits(self, index, layer='overlay'):
        spans = self.spans(layer)
        low = spans[index-1].last+1 if index else 0
        high = spans[index+1].first-1 if index+1 < len(spans) else self.count-1
        return low, high

    def move_overlay(self, index, first, layer='overlay'):
        spans = self.spans(layer)
        span = spans[index]
        low, high = self._overlay_limits(index, layer)
        width = span.last-span.first
        first = _clamp(_index(first), low, high-width)
        spans[index] = replace(span, first=first, last=first+width)
        self._alphas.pop(layer, None)
        return index

    def trim_overlay(self, index, first, last, layer='overlay'):
        spans = self.spans(layer)
        low, high = self._overlay_limits(index, layer)
        if _index(first) > _index(last):
            # Keep a one-frame interval at the stationary handle.
            if first != spans[index].first:
                first = last
            else:
                last = first
        spans[index] = replace(spans[index], first=_clamp(first, low, high),
                               last=_clamp(last, low, high))
        self._alphas.pop(layer, None)
        return index

    def set_fades(self, index, fade_in, fade_out, layer='overlay'):
        spans = self.spans(layer)
        if not all(math.isfinite(v) and v >= 0 for v in (fade_in, fade_out)):
            raise ValueError('Choose a non-negative fade duration.')
        spans[index] = replace(spans[index], fade_in=float(fade_in), fade_out=float(fade_out))
        self._alphas.pop(layer, None)
        return index

    def remove_overlay(self, index, layer='overlay'):
        spans = self.spans(layer)
        del spans[index]
        self._alphas.pop(layer, None)
        return min(index, len(spans)-1) if spans else None

    def opacity_bytes(self, layer='overlay'):
        """One byte per source frame, shared by live playback and export."""
        if layer not in self._alphas:
            levels = np.zeros(self.count, dtype=np.uint8)
            for span in self.spans(layer):
                if not (span.fade_in or span.fade_out):
                    levels[span.first:span.last+1] = 255
                    continue
                start, stop = self.time_at(span.first), self.time_at(span.last)
                # Compute fade durations in presentation time even if a speed
                # cut lies inside the green span. An endpoint with a fade is raw.
                for speed in self.speeds[self.speed_index(span.first):self.speed_index(span.last)+1]:
                    lo, hi = max(span.first, speed.first), min(span.last, speed.last)
                    offsets = np.minimum(np.arange(lo, hi+1)*self.sampling.step, self.sampling.source_count-1)
                    if self._ramp_clock is None:
                        times = self.time_at(lo) + (offsets-offsets[0])/speed.fps
                    else:
                        times = self._ramp_clock.times_for_offsets(offsets)
                    strength = np.ones_like(times)
                    if span.fade_in:
                        strength = np.minimum(strength, (times-start)/span.fade_in)
                    if span.fade_out:
                        strength = np.minimum(strength, (stop-times)/span.fade_out)
                    levels[lo:hi+1] = np.rint(255*np.clip(strength, 0, 1)).astype(np.uint8)
            self._alphas[layer] = levels.tobytes()
        return self._alphas[layer]

    def alpha_at(self, frame):
        if not 0 <= frame < self.count:
            return 0.
        return self.opacity_bytes()[frame]/255.
