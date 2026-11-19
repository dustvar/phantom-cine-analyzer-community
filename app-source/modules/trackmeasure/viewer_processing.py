"""Shared Cine rendering for the viewer and video export."""
from functools import lru_cache
import cv2
import numpy as np
from scipy.interpolate import PchipInterpolator
from pyphantom_image.imagetools import ImageProcessingConfig, ImageTools

def default_grade():
    return {'black': 0.0, 'white': 1.0, 'gamma': 1.0,
            'curve': [0.0, 0.25, 0.5, 0.75, 1.0], 'enabled': True}


def curve_values(points, inputs):
    return np.clip(PchipInterpolator(np.linspace(0, 1, 5), points)(inputs), 0, 1)


@lru_cache(maxsize=32)
def _grade_lut(maximum, black, white, gamma, points):
    values = np.arange(maximum + 1, dtype=np.float64) / maximum
    values = np.clip((values - black) / max(white - black, 0.001), 0, 1)
    values = values ** (1 / max(gamma, 0.1))
    values = curve_values(points, values)
    return np.rint(values * maximum).astype(np.uint8 if maximum == 255 else np.uint16)


def apply_grade(image, grade):
    """Display-only adjustment; never mutate a frame used by tracking."""
    if not grade or not grade.get('enabled', True):
        return image
    black, white, gamma = (float(grade[name]) for name in ('black', 'white', 'gamma'))
    points = tuple(grade['curve'])
    if (black, white, gamma) == (0, 1, 1) and points == (0, .25, .5, .75, 1):
        return image
    if image.dtype not in (np.uint8, np.uint16):
        raise ValueError('Viewer levels require an 8-bit or 16-bit image')
    maximum = np.iinfo(image.dtype).max
    return _grade_lut(maximum, black, white, gamma, points)[image]


def sample_histogram(image):
    # A fixed, bounded sample keeps curve dragging and playback responsive.
    stride = max(1, int(np.ceil(np.sqrt(image.shape[0] * image.shape[1] / 65536))))
    sample = image[::stride, ::stride].astype(np.float32)
    if sample.ndim == 3:
        sample = sample @ np.array([.2126, .7152, .0722], dtype=np.float32)
    maximum = np.iinfo(image.dtype).max
    sample /= maximum
    histogram = np.histogram(sample, bins=64, range=(0, 1))[0]
    return histogram, sample

@lru_cache(maxsize=16)
def _mono_display_lut(dtype_name, bpp, brightness, contrast, gamma, black, white):
    """Run the camera's per-pixel operations once per input code, rather than per frame."""
    tools = ImageTools()
    codes = np.arange(np.iinfo(dtype_name).max + 1, dtype=np.float32)
    values = tools.apply_brightness(codes, brightness)
    values = tools.apply_contrast(values, contrast)
    values = tools.apply_gamma(values, gamma, bpp)
    table = tools.stretch_image_rng(values, 16, black, white)
    table.setflags(write=False)
    return table


def _has_active_tone(tone):
    # The vendor pipeline applies a tone curve only with at least two populated
    # Y points. Its normalization depends on this particular frame's extrema.
    return tone is not None and np.count_nonzero(np.asarray(tone)[1::2]) >= 2


@lru_cache(maxsize=16)
def _mono_gamma_lut(dtype_name, bpp, brightness, contrast, gamma):
    tools = ImageTools()
    values = np.arange(np.iinfo(dtype_name).max + 1, dtype=np.float32)
    values = tools.apply_brightness(values, brightness)
    values = tools.apply_contrast(values, contrast)
    table = tools.apply_gamma(values, gamma, bpp)
    table.setflags(write=False)
    return table


@lru_cache(maxsize=16)
def _tone_display_lut(low, high, dtype_name, tone, black, white):
    # The vendor's 16-bit tone curve uses this frame's post-gamma extrema.
    # Evaluating only the possible codes preserves those exact semantics.
    tools = ImageTools()
    codes = np.arange(low, high + 1, dtype=dtype_name).reshape(1, -1)
    values = tools.apply_ftone(codes, tone)
    table = tools.stretch_image_rng(values, 16, black, white).ravel()
    table.setflags(write=False)
    return table


@lru_cache(maxsize=16)
def _color_bayer_luts(dtype_name, bpp, r_gain, b_gain):
    codes = np.arange(np.iinfo(dtype_name).max + 1, dtype=np.float32)
    maximum, shift = (1 << bpp) - 1, bpp - 8
    tables = tuple((np.clip(codes * gain, 0, maximum).astype(np.uint16) >> shift).astype(np.uint8)
                   for gain in (r_gain, b_gain))
    green = (codes.astype(np.uint16) >> shift).astype(np.uint8)
    for table in (*tables, green):
        table.setflags(write=False)
    return (*tables, green)


@lru_cache(maxsize=32)
def _color_display_lut(brightness, contrast, gamma, tone):
    tools = ImageTools()
    values = tools.apply_brightness(np.arange(256, dtype=np.uint8), float(brightness - 15))
    values = tools.apply_contrast(values, float(contrast + .25))
    values = tools.apply_gamma(values, gamma, 8)
    if tone is not None:
        values = tools.apply_ftone(values, tone)
    table = values.astype(np.uint8)
    table.setflags(write=False)
    return table


def _render_color_codes(img, cfa, bpp, r_gain, b_gain, settings, tone, tools):
    # Preserve the camera pipeline's two defect passes without changing the
    # raw sensor array. Most frames contain no defect marker and need no copy.
    if np.any(img == (255 << (bpp - 8))):
        img = tools.apply_dmap_correction(img.copy(), cfa, bpp)
        img = tools.apply_dmap_correction(img, cfa, bpp)
    red, blue, green = _color_bayer_luts(img.dtype.name, bpp, float(r_gain), float(b_gain))
    bayer = green[img]
    locations = {'gb/rg': ((1, 0), (0, 1), cv2.COLOR_BayerGR2RGB_EA),
                 'rg/gb': ((0, 0), (1, 1), cv2.COLOR_BayerBG2RGB_EA),
                 'gr/gb': ((0, 1), (1, 0), cv2.COLOR_BayerGB2RGB_EA),
                 'bg/gr': ((1, 1), (0, 0), cv2.COLOR_BayerRG2RGB_EA)}
    r, b, conversion = locations[cfa]
    bayer[r[0]::2, r[1]::2] = red[img[r[0]::2, r[1]::2]]
    bayer[b[0]::2, b[1]::2] = blue[img[b[0]::2, b[1]::2]]
    rgb = cv2.cvtColor(bayer, conversion)
    lut = _color_display_lut(float(settings['image_brightness']), float(settings['image_contrast']),
                             float(settings['image_gamma']), None if tone is None else tuple(tone))
    return cv2.LUT(rgb, lut)


def render_cine_image(img, metadata, settings, image_tools):
    if metadata.CFA == 'RGB' and img.ndim == 3:
        # Interpolated RGB Cine already has display channels. Keep the default
        # view pixel-exact; apply optional display adjustments channel by channel.
        if settings['raw_enabled']:
            return img
        lut = _mono_display_lut(img.dtype.name, 8, settings['image_brightness']/256.,
                                float(settings['image_contrast']), float(settings['image_gamma']), 0, 255)
        return (lut[img] >> 8).astype(np.uint8)
    cfa = metadata.CFA
    bpp = metadata.RealBPP
    r_gain = metadata.WBGain[0]
    b_gain = metadata.WBGain[1]
    black = metadata.BlackLevel
    white = metadata.WhiteLevel
    cm_calib = metadata.cmCalib
    bright = settings['image_brightness']/2**int(bpp)
    ftone = metadata.fTone
    if not settings['toggle_ftone']: ftone = None

    if white <= black:
        white = black + 1

    if settings['raw_enabled']:
        if cfa != 'Mono':
            #TODO: is this ok?
            img = img.astype(np.float32) if img.dtype != np.float32 else img
            img = image_tools.apply_wb(img, r_gain, b_gain, cfa, bpp)
        if bpp == 8 or bpp == 10 or bpp == 12:
            #TODO: is this ok?
            img = img.astype(np.uint16) if img.dtype != np.uint16 else img
            img = image_tools.cast_to_16bpp(img)
        img = img * (1 << (16 - bpp))
        return img

    if (cfa in ('gb/rg', 'rg/gb', 'gr/gb', 'bg/gr')
            and img.dtype in (np.uint8, np.uint16) and bpp in (8, 10, 12, 16)):
        return _render_color_codes(img, cfa, bpp, r_gain, b_gain, settings, ftone, image_tools)

    if (cfa == 'Mono' and img.dtype in (np.uint8, np.uint16)
            and bpp in (8, 10, 12, 16) and _has_active_tone(ftone)):
        if np.any(img == (255 << (bpp - 8))):
            img = image_tools.apply_dmap_correction(img.copy(), cfa, bpp)
        gamma_codes = _mono_gamma_lut(img.dtype.name, bpp, bright,
                                      float(settings['image_contrast']), float(settings['image_gamma']))[img]
        if gamma_codes.dtype == np.uint8:
            low, high = 0, 255
        else:
            low, high = int(gamma_codes.min()), int(gamma_codes.max())
        table = _tone_display_lut(low, high, gamma_codes.dtype.name, tuple(ftone), int(black), int(white))
        return table[gamma_codes - low]

    if (cfa == 'Mono' and img.dtype in (np.uint8, np.uint16)
            and bpp in (8, 10, 12, 16) and not _has_active_tone(ftone)):
        # Defect replacement is spatial. Apply it to a private copy only when
        # needed: tracking and export can still share the untouched raw pixels.
        if np.any(img == (255 << (bpp - 8))):
            img = image_tools.apply_dmap_correction(img.copy(), cfa, bpp)
        lut = _mono_display_lut(img.dtype.name, bpp, bright, float(settings['image_contrast']),
                                float(settings['image_gamma']), int(black), int(white))
        return lut[img]

    # defect pixel correction
    img = img.astype(np.float32) if img.dtype != np.float32 else img
    img = image_tools.apply_dmap_correction(img, cfa, bpp)

    if cfa == 'Mono':
        img = image_tools.apply_brightness(img, bright)
        img = image_tools.apply_contrast(img, float(settings['image_contrast']))
        img = image_tools.apply_gamma(img, float(settings['image_gamma']), bpp)
        if ftone is not None:
            img = image_tools.apply_ftone(img, ftone)
        img = image_tools.stretch_image_rng(img, 16, int(black), int(white))
        img = image_tools.cast_to_16bpp(img)
        return img

    else:
        bright = settings['image_brightness'] #TODO: check if this is correct for color
        config = ImageProcessingConfig(
            r_gain=r_gain,
            b_gain=b_gain,
            brightness=bright,
            contrast=settings['image_contrast'],
            gamma=settings['image_gamma'],
            cfa=cfa,
            bpp=bpp,
            ftone=ftone,
            cm_calib=cm_calib,
            black_offset=black,
            white_level=white,
            enable_timing=False
        )

        img = image_tools.apply_color_pipeline(img, config)

        return img
