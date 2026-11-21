"""Live monitor tone mapping for linear Phantom images. Capture pixels stay in camera RAM."""
from dataclasses import dataclass
from math import exp, log
from time import monotonic

import numpy as np
from scipy.interpolate import PchipInterpolator


@dataclass(frozen=True)
class MonitorCurve:
    # A fixed base gain keeps the first edit of an automatic curve seamless.
    # None uses linear input. Other gains use the same sRGB encoding as Standard.
    base_gain: float | None = None
    points: tuple = ((0., 0.), (1., 1.))

    def base(self, x):
        if self.base_gain is None:
            return x
        y = self.base_gain * x / (1. + (self.base_gain - 1.) * x)
        return np.where(y <= .0031308, 12.92 * y, 1.055 * y ** (1 / 2.4) - .055)

    def values(self, x):
        encoded = self.base(x)
        if self.points == ((0., 0.), (1., 1.)):
            return encoded
        inputs, outputs = np.asarray(self.points).T
        return np.clip(PchipInterpolator(self.base(inputs), outputs)(encoded), 0., 1.)


LINEAR_CURVE = MonitorCurve()
PRESET_CURVES = {
    'linear': LINEAR_CURVE,
    'standard': MonitorCurve(base_gain=1.),
    'shadows': MonitorCurve(base_gain=3.),
    'contrast': MonitorCurve(points=((0., 0.), (.22, .14), (.5, .5), (.78, .86), (1., 1.))),
}


def active_curve(mode, gain, custom):
    if mode == 'auto':
        return MonitorCurve(base_gain=round(gain, 1))
    return PRESET_CURVES.get(mode, custom)


def _sample(pixels):
    h, w = pixels.shape
    return pixels[::max(1, (h + 95) // 96), ::max(1, (w + 127) // 128)]


def needs_shadow_precision(pixels, already_enabled):
    """Use the deeper transfer for dark scenes. Hysteresis avoids format flicker."""
    full = np.iinfo(pixels.dtype).max
    white = np.percentile(_sample(pixels), 99) / full
    return white < (.15 if already_enabled else .08)


class MonitorLevels:
    def __init__(self):
        self.reset()

    def reset(self):
        self.gain = 1.
        self.measured_at = None
        self.signal = ''
        self._depth = None
        self._key = self._lut = None

    def render(self, pixels, mode='auto', now=None, *, curve=LINEAR_CURVE):
        """Return an 8-bit display image using the original sensor precision."""
        now = monotonic() if now is None else now
        depth = pixels.dtype.itemsize * 8
        if depth != self._depth:
            self.reset()
            self._depth = depth
        full = (1 << depth) - 1
        if self.measured_at is None or now - self.measured_at >= .15:
            sample = _sample(pixels)
            white = np.percentile(sample, 99) / full
            target = np.clip(.65 / max(white, .001), 1., 12.)
            if self.measured_at is None:
                self.gain = float(target)
            else:
                fraction = 1. - exp(-(now - self.measured_at) / .35)
                self.gain = exp(log(self.gain) + fraction * (log(target) - log(self.gain)))
            self.measured_at = now
            self.signal = ''
            if white < .055:
                self.signal = 'Low light'
            elif np.count_nonzero(sample >= full * .985) >= .1 * sample.size:
                self.signal = 'Highlights near white'

        if mode == 'linear':
            return pixels if depth == 8 else (pixels >> 8).astype(np.uint8)
        display_curve = active_curve(mode, self.gain, curve)
        key = (depth, display_curve)
        if self._key != key:
            x = np.linspace(0., 1., min(1 << depth, 4096))
            self._lut = np.rint(display_curve.values(x) * 255.).astype(np.uint8)
            self._key = key
        return self._lut[pixels if depth == 8 else pixels >> 4]
