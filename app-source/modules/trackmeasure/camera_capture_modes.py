"""Capture presets from the manufacturer’s VEO 1310 and standalone C980 tables.

https://www.phantomhighspeed.com/-/media/project/ameteksxa/visionresearch/documents/datasheets/web/ds_web-veo-1310.pdf
https://www.phantomhighspeed.com/products/cameras/c-series/phantom-c980
The charts list representative sensor windows. Binning is model-specific.
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


_VEO_1310_MODEL = re.compile(r'(?:Phantom\s+)?VEO[\s-]*1310[LS]?(?:[ -]*E225)?', re.IGNORECASE)


# Standard C980 windows confirmed in the 4K camera's resolution chart. Some
# combined C540/C980 charts also list 1280 × 24, below the C980's 1920 px minimum.
_C980 = (
    (3840, 2160, 1180), (3840, 1680, 1510), (3840, 1080, 2330),
    (3840, 804, 3100), (2560, 1680, 1510), (2560, 24, 50820),
    (1920, 1080, 2330), (1920, 360, 6670), (1920, 228, 10130),
    (1920, 24, 50820),
)


def is_c980(model):
    return re.fullmatch(r'(?:Phantom\s+)?C980', model.strip(), flags=re.I) is not None


def published_modes(model, has_binning, max_fps):
    """Return only known geometry/rate pairs for the identified camera family."""
    ceiling = max_fps or float('inf')
    if is_c980(model):
        return tuple(RateOption(min(ceiling, fps), w, h, 1) for w, h, fps in _C980)
    if not _VEO_1310_MODEL.fullmatch(model.strip()):
        return ()
    if 'E225' in model.upper():
        ceiling = min(ceiling, 225000)
    result = []
    for width, height, standard, binned in _VEO_1310:
        result.append(RateOption(min(ceiling, standard), width, height, 1))
        if has_binning and binned is not None:
            result.append(RateOption(min(ceiling, binned), width, height, 2))
    return tuple(result)


def rate_options(status):
    """Offer the largest known output in each readout mode at each preset rate."""
    current_mode = status.width, status.height, status.binning
    modes = list(status.published_rates)
    minimum = status.min_fps or 0
    maximum = status.max_fps or float('inf')
    # This VEO 1310S operating point is measured, separate from the published maxima.
    if _VEO_1310_MODEL.fullmatch(status.model.strip()) and minimum <= 20000 <= maximum:
        modes.append(RateOption(20000, 800, 600, 1))

    # For an unlisted crop, its current rate and the rates of larger windows
    # in the same readout mode establish which presets can retain that crop.
    current_limit = status.fps
    for row in modes:
        if row.binning == status.binning and row.width >= status.width and row.height >= status.height:
            current_limit = max(current_limit, row.fps)
    aspect = status.width / status.height

    def preference(row):
        return row.width * row.height, -abs(row.width / row.height - aspect), row.width

    # Keep distinct windows from the chart when they share a maximum rate.
    options = {row for row in modes if minimum <= row.fps <= maximum}
    options.add(RateOption(status.fps, *current_mode))
    common = (50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000,
              100000, 200000, 400000)
    rates = set(common) | {row.fps for row in options}
    candidates = {}
    for row in sorted([*modes, RateOption(current_limit, *current_mode)],
                      key=preference, reverse=True):
        candidates.setdefault(row.binning, []).append(row)

    for fps in rates:
        if not minimum <= fps <= maximum:
            continue
        for windows in candidates.values():
            best = next((row for row in windows if row.fps >= fps), None)
            if best is not None:
                options.add(RateOption(fps, *best.mode))
        if fps in common and fps <= current_limit:
            options.add(RateOption(fps, *current_mode))

    return tuple(sorted(options, key=lambda o: (o.fps, o.binning, -o.width * o.height,
                                                abs(o.width / o.height - aspect), -o.width)))
