"""Live monitor tone mapping for linear Phantom images. Capture pixels stay in camera RAM."""
from math import exp, log
from time import monotonic

import numpy as np


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

    def render(self, pixels, mode='auto', now=None):
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
        gain = round(self.gain, 1) if mode == 'auto' else 1.
        key = (depth, gain)
        if self._key != key:
            # A bounded lift keeps white fixed while revealing shadow detail.
            # The second step encodes linear light for an ordinary sRGB display.
            x = np.linspace(0., 1., min(1 << depth, 4096))
            y = gain * x / (1. + (gain - 1.) * x)
            encoded = np.where(y <= .0031308, 12.92 * y, 1.055 * y ** (1 / 2.4) - .055)
            self._lut = np.rint(encoded * 255.).astype(np.uint8)
            self._key = key
        return self._lut[pixels if depth == 8 else pixels >> 4]
