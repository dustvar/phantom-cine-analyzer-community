"""Continuous source motion for centered speed ramps in the BOS studio."""
from bisect import bisect_right
from fractions import Fraction
import math

import numpy as np

from viewer_timeline import MotionSpan, ramp_distance


def _footprint(speeds, cut, curve):
    """Source frames on either side of a one-second centered transition."""
    a, b = (Fraction(str(s.fps)) for s in speeds[cut-1:cut+1])
    left = ramp_distance(a, b, curve, Fraction(1, 2))
    return left, (a+b)/2 - left


def _boundary(speeds, cut):
    # An ordinary split between matching rates leaves existing ramps intact.
    return speeds[cut].ramp > 0 or speeds[cut].fps != speeds[cut-1].fps


def ramp_limit(speeds, sampling, cut, curve):
    """Maximum duration available here while honoring adjacent transitions."""
    first = cut-1
    while first > 0 and not _boundary(speeds, first):
        first -= 1
    end = cut+1
    while end < len(speeds) and not _boundary(speeds, end):
        end += 1
    position = sampling.offset(speeds[cut].first)
    available_left = position - sampling.offset(speeds[first].first)
    available_right = (sampling.offset(speeds[end].first) if end < len(speeds) else sampling.source_count) - position
    if speeds[first].ramp:
        available_left -= Fraction(str(speeds[first].ramp)) * _footprint(speeds, first, speeds[first].curve)[1]
    if end < len(speeds) and speeds[end].ramp:
        available_right -= Fraction(str(speeds[end].ramp)) * _footprint(speeds, end, speeds[end].curve)[0]
    left, right = _footprint(speeds, cut, curve)
    return max(0., float(min(available_left/left, available_right/right)))


class RampClock:
    """Integrate section rates in movie time over the original source frames."""

    def __init__(self, speeds, sampling):
        cuts = [i for i in range(1, len(speeds)) if _boundary(speeds, i)]
        cuts_at = [sampling.offset(speeds[i].first) for i in cuts]
        centers = [0, *cuts_at, sampling.source_count]
        weights = [_footprint(speeds, i, speeds[i].curve) for i in cuts]
        durations = [Fraction(str(speeds[i].ramp)) for i in cuts]

        # A moved cut or a changed speed can shrink an intervening section.
        # Reduce both ramps proportionally when together they exhaust its footage.
        for j in range(len(centers)-1):
            used = durations[j-1]*weights[j-1][1] if j else 0
            if j < len(cuts):
                used += durations[j]*weights[j][0]
            length = centers[j+1] - centers[j]
            if used > length:
                factor = length/used
                if j:
                    durations[j-1] *= factor
                if j < len(cuts):
                    durations[j] *= factor

        self.actual = {i:float(d) for i, d in zip(cuts, durations)}
        self.motions = []
        self.times = []
        self.offsets = []
        self.duration = Fraction()
        self.source_count = sampling.source_count
        self.centers = {}

        def append(start, length, a, b, duration, curve='smooth'):
            if length > 0:
                self.times.append(float(self.duration))
                self.offsets.append(float(start))
                self.motions.append(MotionSpan(float(start), float(length), a, b, float(duration), curve))
                self.duration += duration

        cursor = 0
        for cut, center, (left, right), d in zip(cuts, cuts_at, weights, durations):
            a, b = speeds[cut-1].fps, speeds[cut].fps
            start = center - d*left
            length = start - cursor
            append(cursor, length, a, a, length/Fraction(str(a)))
            self.centers[center] = float(self.duration+d/2)
            append(start, d*(left+right), a, b, d, speeds[cut].curve)
            cursor = center + d*right
        length = sampling.source_count - cursor
        fps = speeds[-1].fps
        append(cursor, length, fps, fps, length/Fraction(str(fps)))

    def time_at(self, offset):
        if offset >= self.source_count:
            return float(self.duration)
        if offset in self.centers:
            return self.centers[offset]
        i = max(0, bisect_right(self.offsets, offset)-1)
        span = self.motions[i]
        return self.times[i] + span.time_for_distance(offset-span.start)

    def sample(self, seconds):
        i = max(0, bisect_right(self.times, seconds)-1)
        span = self.motions[i]
        elapsed = min(span.duration, max(0., seconds-self.times[i]))
        position = span.start + span.distance(elapsed)
        rate = span.rate(elapsed)
        tolerance = 8*(math.ulp(max(1., seconds))*rate + math.ulp(max(1., position)))
        return min(self.source_count-1, math.floor(position+tolerance)), rate

    def times_for_offsets(self, offsets):
        """Evaluate a fade on an array of measured frames without a scalar loop."""
        times = np.empty(len(offsets), dtype=np.float64)
        i = max(0, bisect_right(self.offsets, offsets[0])-1)
        lo = 0
        while lo < len(offsets):
            span = self.motions[i]
            hi = np.searchsorted(offsets, self.offsets[i+1]) if i+1 < len(self.offsets) else len(offsets)
            distance = offsets[lo:hi]-span.start
            if span.rate_from == span.rate_to:
                elapsed = distance/span.rate_from
            else:
                target = distance/span.duration
                low = np.zeros_like(target)
                high = np.ones_like(target)
                for _ in range(48):
                    mid = (low+high)*.5
                    advance = ramp_distance(span.rate_from, span.rate_to, span.curve, mid) < target
                    low = np.where(advance, mid, low)
                    high = np.where(advance, high, mid)
                elapsed = (low+high)*(.5*span.duration)
            times[lo:hi] = self.times[i]+elapsed
            lo, i = hi, i+1
        return times
