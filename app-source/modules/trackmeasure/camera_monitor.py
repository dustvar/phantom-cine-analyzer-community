"""Live monitor tone mapping for linear Phantom images. Capture pixels stay in camera RAM."""
from dataclasses import dataclass
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


def active_curve(mode, selected=LINEAR_CURVE):
    return PRESET_CURVES.get(mode, selected)


def auto_curve(pixels):
    """Meter one linear preview frame and return a fixed, editable display curve."""
    # A lit subject may occupy only a few percent of a black sensor field.
    # Exclude digital black, but require enough signal to rule out hot pixels.
    lit_count = np.count_nonzero(pixels)
    minimum_lit = max(32, (pixels.size + 9999) // 10000)
    if not lit_count or lit_count < min(minimum_lit, (pixels.size + 1) // 2):
        return LINEAR_CURVE
    signal = pixels if lit_count == pixels.size else pixels[pixels != 0]
    full = np.iinfo(pixels.dtype).max
    middle, white = np.percentile(signal, (50, 95)) / full
    if middle == 1.:
        return LINEAR_CURVE

    # Invert MonitorCurve.base's soft shoulder to place the median at 20%
    # linear light (about 48% display brightness). Limit amplification in
    # near-black scenes and protect bright detail that is still recoverable.
    gain = .25 * (1. - middle) / max(middle, 1. / full)
    if white < .985:
        highlight_gain = (.82 / (1. - .82)) * (1. - white) / white
        gain = min(gain, highlight_gain)
    return MonitorCurve(base_gain=float(np.clip(gain, .25, 12.)))


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
        self.measured_at = None
        self.signal = ''
        self._depth = None
        self._key = self._lut = None

    def render(self, pixels, mode='linear', now=None, *, curve=LINEAR_CURVE):
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
            self.measured_at = now
            self.signal = ''
            if white < .055:
                self.signal = 'Low light'
            elif np.count_nonzero(sample >= full * .985) >= .1 * sample.size:
                self.signal = 'Highlights near white'

        if mode == 'linear':
            return pixels if depth == 8 else (pixels >> 8).astype(np.uint8)
        display_curve = active_curve(mode, curve)
        key = (depth, display_curve)
        if self._key != key:
            x = np.linspace(0., 1., min(1 << depth, 4096))
            self._lut = np.rint(display_curve.values(x) * 255.).astype(np.uint8)
            self._key = key
        return self._lut[pixels if depth == 8 else pixels >> 4]
