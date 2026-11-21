"""Capture presets from the Vision Research VEO 1310 datasheet, rev. 8 (August 2025).

https://www.phantomhighspeed.com/-/media/project/ameteksxa/visionresearch/documents/datasheets/web/ds_web-veo-1310.pdf
The chart lists representative sensor windows in standard and 2 × 2 binned modes.
The camera remains authoritative about the settings that it actually accepts.
"""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class RateOption:
    fps: float
    width: int
    height: int
    binning: int

    @property
    def mode(self):
        return self.width, self.height, self.binning


# Output width, output height, maximum standard FPS, maximum 2 × 2 binned FPS.
_VEO_1310 = (
    (1280, 960, 10860, None), (1280, 804, 12900, None),
    (1280, 720, 14350, None), (1280, 240, 40300, None),
    (960, 960, 13330, None), (640, 480, 30030, 40300),
    (640, 240, 57360, 73510), (640, 120, 105260, 125000),
    (640, 96, 126360, 145360), (640, 72, 158030, 173650),
    (640, 48, 210900, 215610), (320, 240, 57360, 105260),
    (320, 72, 158030, 253270), (320, 24, 316930, 423350),
)


def published_modes(model, has_binning, max_fps):
    """Return only known geometry/rate pairs for the identified camera family."""
    if not re.fullmatch(r'(?:Phantom\s+)?VEO[\s-]*1310[LS]?(?:[ -]*E225)?',
                        model.strip(), re.IGNORECASE):
        return ()
    ceiling = max_fps or float('inf')
    if 'E225' in model.upper():
        ceiling = min(ceiling, 225000)
    result = []
    for width, height, standard, binned in _VEO_1310:
        result.append(RateOption(min(ceiling, standard), width, height, 1))
        if has_binning and binned is not None:
            result.append(RateOption(min(ceiling, binned), width, height, 2))
    return tuple(result)


def rate_options(status):
    mode = (status.width, status.height, status.binning)
    modes = list(status.published_rates)
    # A VEO 1310S capture at 800 × 600 verified 20,000 fps. This is a
    # demonstrated operating point, separate from the published maxima.
    if modes and (status.max_fps is None or status.max_fps >= 20000):
        modes.append(RateOption(20000, 800, 600, 1))

    # Within the same sensor readout mode, any rate achievable in a larger
    # window is also available in a smaller one. This supplies useful speeds
    # for unlisted crops without estimating their exact maximum.
    known = [row.fps for row in modes if row.binning == status.binning
             and row.width >= status.width and row.height >= status.height]
    # A connected camera has also demonstrated its current window/rate. When
    # the window is unlisted, offer slower standard speeds in that same window.
    current_limit = max([status.fps, *known])
    common = (50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000,
              100000, 200000, 400000)
    options = {row for row in modes if row.fps >= (status.min_fps or 0)}
    options.add(RateOption(status.fps, *mode))
    aspect = status.width / status.height

    def preference(row):
        pixels = row.width * row.height
        return pixels * row.binning**2, pixels, -abs(row.width / row.height - aspect)

    for rate in common:
        if rate < (status.min_fps or 0):
            continue
        if rate <= current_limit:
            options.add(RateOption(rate, *mode))
        else:
            # For faster targets, preserve sensor area first, then output
            # detail. Break ties with the current window's aspect ratio.
            best = max((row for row in modes if row.fps >= rate), key=preference, default=None)
            if best is not None:
                options.add(RateOption(rate, *best.mode))
    return tuple(sorted(options, key=lambda o: (o.fps, -o.width*o.height, o.binning)))
