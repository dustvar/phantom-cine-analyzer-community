"""Background-oriented schlieren: apparent movement of texture in image pixels.

The reference is undisturbed footage from the same camera. Measure against
that fixed reference on each frame, so skipped frames do not accumulate drift.
"""
from dataclasses import dataclass, replace

import cv2
from bos_correlation import correlate, _prepare as prepare_correlation_reference
import numpy as np

from bos_display import direction_tone, overlay_vectors
from bos_vectors import VectorStyle, draw_vector_field, key_rect, sample_vectors


@dataclass(frozen=True)
class BOSField:
    roi: tuple
    x: np.ndarray
    y: np.ndarray
    dx: np.ndarray
    dy: np.ndarray
    valid: np.ndarray
    confidence: np.ndarray
    step: int
    reason: np.ndarray


_MATCH, _WEAK_TEXTURE, _RANGE, _INCONSISTENT, _LOW_CORRELATION, _AMBIGUOUS = range(6)


def reason_label(code):
    return ('Matched dot pattern', 'Too little usable texture', 'Shift outside search range',
            'Forward and reverse shifts disagree', 'Dot pattern differs from the reference',
            'Multiple similar dot-pattern matches')[int(code)]


ANCHOR_MIN_SIZE = 80


def camera_translation(reference, current, roi, *, _reference_cache=None):
    """Measure camera/background translation in an explicitly stationary patch.

    This never estimates motion from the analysis region: widespread real flow
    must not be silently subtracted. The user is responsible for placing the
    anchor outside the flow and keeping it unobscured. Rotation/perspective
    changes are deliberately not fitted by this translation-only correction.
    """
    field = compute_bos(reference, current, roi, window=31, search=8,
                        detail=False, method='optical_flow', _reference_cache=_reference_cache)
    usable = field.valid & (field.confidence >= .8)
    coverage = float(usable.mean())
    if usable.sum() < 9 or coverage < .6:
        return dict(reliable=False, dx=None, dy=None, coverage=coverage,
                    scatter=None, message='The stationary patch has too few reliable dot matches.')
    shifts = np.column_stack((field.dx[usable], field.dy[usable]))
    center = np.median(shifts, axis=0)
    # A robust residual check rejects an anchor that is distorted, obscured or
    # rotating appreciably rather than translating as a stationary background.
    scatter = float(1.4826*np.median(np.linalg.norm(shifts-center, axis=1)))
    if not np.isfinite(scatter) or scatter > .15:
        return dict(reliable=False, dx=None, dy=None, coverage=coverage,
                    scatter=scatter, message='The stationary patch is not moving consistently as one background.')
    return dict(reliable=True, dx=float(center[0]), dy=float(center[1]),
                coverage=coverage, scatter=scatter,
                message='Translation measured in the stationary patch.')


def remove_camera_translation(field, drift):
    """Create a corrected display field; retain raw vectors and rejection masks."""
    if not drift.get('reliable'):
        raise ValueError('Camera movement correction needs a reliable stationary patch.')
    return replace(field, dx=field.dx-drift['dx'], dy=field.dy-drift['dy'])


def _gray_patch(frame, roi):
    x, y, w, h = roi
    image = np.asarray(frame)[y:y+h, x:x+w]
    if image.ndim == 3 and image.shape[2] in (3, 4):
        image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY if image.shape[2] == 3 else cv2.COLOR_RGBA2GRAY)
    if image.ndim != 2 or image.dtype not in (np.uint8, np.uint16, np.float32):
        raise ValueError('BOS needs a grayscale or RGB image in 8-bit, 16-bit or float32 format.')
    # Normalize in sensor precision before conversion to the LK input type.
    # A bright plume elsewhere in the ROI must not change the contrast of a
    # still-visible patch. Clipping/rescaling each entire frame before this
    # step can make the forward and reverse matches disagree in dark areas.
    gray = image.astype(np.float32, copy=False)
    if not np.isfinite(gray).all():
        raise ValueError('The BOS region contains non-finite intensity values.')
    local_mean = cv2.GaussianBlur(gray, (0, 0), 5)
    detail = gray - local_mean
    variance = cv2.GaussianBlur(detail * detail, (0, 0), 5)
    # Estimate the noise floor in native sensor units with diagonal differences
    # from neighboring pixels. The robust statistic is insensitive to smooth
    # lighting and a small number of strong dot edges or saturated patches.
    step = max(2, int(np.ceil(np.sqrt(gray.size / 16384))))
    diagonal = ((gray[:-1:step, :-1:step] - gray[:-1:step, 1::step]) -
                (gray[1::step, :-1:step] - gray[1::step, 1::step]))
    noise = np.median(np.abs(diagonal - np.median(diagonal))) * 0.7413
    contrast = np.sqrt(np.maximum(variance, max(1e-12, noise * noise)))
    return np.clip(np.rint(128 + 40 * detail / contrast), 0, 255).astype(np.uint8)


def _patch_correlation(reference, image, starts, ends, window):
    """Validate local registration by sampling the same patch at the fitted shift."""
    axis = np.linspace(-(window//2), window//2, min(9, window), dtype=np.float32)
    delta = np.stack(np.meshgrid(axis, axis), axis=-1).reshape(-1, 2)
    correlation = np.zeros(len(starts), dtype=np.float32)
    for i in range(0, len(starts), 4096):
        selection = slice(i, i + 4096)
        def samples(frame, points):
            coords = points[selection, None, :] + delta
            patch = cv2.remap(frame, coords[..., 0], coords[..., 1], cv2.INTER_LINEAR).astype(np.float32)
            return patch - patch.mean(axis=1, keepdims=True)
        a = samples(reference, starts)
        b = samples(image, ends)
        product = np.einsum('ij,ij->i', a, b)
        denom = np.sqrt(np.einsum('ij,ij->i', a, a) * np.einsum('ij,ij->i', b, b))
        correlation[selection] = np.clip(product / np.maximum(denom, 1e-6), -1.0, 1.0)
    return correlation


def measurement_step(window, detail):
    return max(2, window // (4 if detail else 2))


def compute_bos(reference, current, roi, *, window=21, search=4, detail=False, method='optical_flow', _reference_cache=None):
    """Subpixel apparent displacement from reference -> current (+x right, +y down).

    Grid points are half a window apart, or a quarter in Fine. Optical flow uses
    a round-trip check. Cross-correlation searches for a distinct normalized peak.
    Invalid vectors are NaN. The search limit is in source pixels per axis.
    """
    if reference.shape[:2] != current.shape[:2]:
        raise ValueError('The BOS reference and current frame must have the same dimensions.')
    if window != int(window) or not 9 <= window <= 61 or not int(window) % 2:
        raise ValueError('Choose an odd BOS window from 9 to 61 pixels.')
    if search != int(search) or not 2 <= search <= 16:
        raise ValueError('Choose a displacement limit from 2 to 16 pixels.')
    window, search = int(window), int(search)
    x, y, w, h = (int(value) for value in roi)
    height, width = reference.shape[:2]
    right, bottom = min(width, x+w), min(height, y+h)
    x, y = max(0, x), max(0, y)
    w, h = right-x, bottom-y
    roi = (x, y, w, h)
    margin = window // 2 + search
    if min(w, h) < 2*margin + 1:
        raise ValueError(f'Draw a larger BOS region (at least {2*margin + 1} pixels across and high).')
    # A BOS session owns its fixed reference. Reuse its normalized pixels and
    # correlation statistics across frames, keeping at most one configuration.
    # Public calls without this private session cache remain fully stateless.
    cache_key = (roi, window, search, bool(detail), method)
    cached = (_reference_cache is not None and _reference_cache.get('source') is reference
              and _reference_cache.get('key') == cache_key)
    if cached:
        ref = _reference_cache['normalized']
    else:
        ref = _gray_patch(reference, roi)
        if _reference_cache is not None:
            _reference_cache.clear()
            _reference_cache.update(source=reference, key=cache_key, normalized=ref)
    img = _gray_patch(current, roi)
    step = measurement_step(window, detail)
    xs = np.arange(margin, w-margin, step, dtype=np.float32)
    ys = np.arange(margin, h-margin, step, dtype=np.float32)
    # Center the sampling grid in the rectangle rather than biasing one edge.
    xs += (w-1-xs[-1]-xs[0]) // 2
    ys += (h-1-ys[-1]-ys[0]) // 2
    if method == 'cross_correlation':
        prepared_reference = None
        if _reference_cache is not None:
            if 'correlation' not in _reference_cache:
                _reference_cache['correlation'] = prepare_correlation_reference(ref, window)
            prepared_reference = _reference_cache['correlation']
        dx, dy, confidence, weak, outside, ambiguous = correlate(ref, img, xs, ys, window, search,
                                                               prepared_reference=prepared_reference)
        reason = np.full(dx.shape, _MATCH, dtype=np.uint8)
        reason[ambiguous] = _AMBIGUOUS
        reason[outside] = _RANGE
        reason[weak] = _WEAK_TEXTURE
        # Smoothing helps locate the peak, but smoke can accidentally resemble
        # a smoothed patch. Verify the fitted correspondence against the finer
        # source texture too, using the same reference/current midpoint.
        candidates = (reason == _MATCH) & (confidence >= .65)
        if candidates.any():
            grid_x, grid_y = np.meshgrid(xs, ys)
            centers = np.column_stack((grid_x[candidates], grid_y[candidates]))
            half_shift = .5 * np.column_stack((dx[candidates], dy[candidates]))
            verified = _patch_correlation(ref, img, centers-half_shift, centers+half_shift, window)
            confidence[candidates] = np.minimum(confidence[candidates], verified)
        reason[confidence < .65] = _LOW_CORRELATION
        valid = reason == _MATCH
        dx[~valid] = dy[~valid] = np.nan
        return BOSField(roi, x+xs, y+ys, dx, dy, valid, confidence, step, reason)
    if method != 'optical_flow':
        raise ValueError('Choose optical flow or cross-correlation for BOS.')
    points = np.stack(np.meshgrid(xs, ys), axis=-1).reshape(-1, 1, 2)
    lk = dict(winSize=(window, window), maxLevel=1 if search <= 6 else 2,
              criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 24, 0.005),
              minEigThreshold=0.0005)
    matched, forward_ok, _ = cv2.calcOpticalFlowPyrLK(ref, img, points, None, **lk)
    returned, reverse_ok, _ = cv2.calcOpticalFlowPyrLK(img, ref, matched, None, **lk)
    starts, ends = points[:, 0], matched[:, 0]
    shifts = ends - starts
    fitted = forward_ok[:, 0].astype(bool) & reverse_ok[:, 0].astype(bool)
    confidence = np.zeros(len(points), dtype=np.float32)
    if fitted.any():
        confidence[fitted] = _patch_correlation(ref, img, starts[fitted], ends[fitted], window)
    # Prefer the direct evidence of a missing dot pattern over downstream
    # symptoms (a bad correspondence often fails several tests at once).
    reason = np.full(len(points), _MATCH, dtype=np.uint8)
    reason[np.linalg.norm(returned[:, 0] - starts, axis=1) > 0.55] = _INCONSISTENT
    reason[np.max(np.abs(shifts), axis=1) > search] = _RANGE
    reason[confidence < 0.65] = _LOW_CORRELATION
    reason[~fitted] = _WEAK_TEXTURE
    valid = reason == _MATCH
    shifts[~valid] = np.nan
    shape = (len(ys), len(xs))
    return BOSField(roi, x+xs, y+ys, shifts[:, 0].reshape(shape), shifts[:, 1].reshape(shape),
                    valid.reshape(shape), confidence.reshape(shape), step, reason.reshape(shape))


_SUPPORT_THRESHOLD = .8


def _sample_bounds(field):
    half = field.step // 2
    return (int(field.x[0]) - half, int(field.y[0]) - half,
            len(field.x) * field.step, len(field.y) * field.step)


def probe_support(field, x, y):
    """Classify a displayed pixel using the same sampling bounds and mask as rendering."""
    left, top, width, height = _sample_bounds(field)
    if not (left <= x < left+width and top <= y < top+height):
        return 'outside'
    gx = (x - float(field.x[0])) / field.step
    gy = (y - float(field.y[0])) / field.step
    col = int(np.floor(gx))
    row = int(np.floor(gy))
    # Bilinear interpolation is quantized to 32 subdivisions by OpenCV.
    fx = np.rint((gx - col) * 32) / 32
    fy = np.rint((gy - row) * 32) / 32
    cols = np.clip([col, col+1], 0, len(field.x)-1)
    rows = np.clip([row, row+1], 0, len(field.y)-1)
    block = field.valid[np.ix_(rows, cols)]
    support = ((1-fy) * ((1-fx)*block[0,0] + fx*block[0,1]) +
               fy * ((1-fx)*block[1,0] + fx*block[1,1]))
    return 'measured' if support >= _SUPPORT_THRESHOLD else 'weak'


def render_bos(current_rgb8, field, *, component='magnitude', limit=1.0, opacity=0.85,
               palette='color', soften=False, reduce_noise=False, show_vectors=False,
               vector_background='map', vector_density='balanced', anchor_roi=None, _vector_samples=None,
               vector_style=VectorStyle()):
    """Render the chosen displacement layers in shared source-image coordinates."""
    if vector_background not in ('map', 'footage'):
        raise ValueError('Choose a displacement map or grayscale magnitude beneath the vectors.')
    inverse = show_vectors and (vector_background == 'footage' or opacity == 0)
    result = _render_map(current_rgb8, field, component, limit, 0. if inverse else opacity,
                         palette, soften, reduce_noise)
    if show_vectors and field.valid.any():
        if inverse:
            apply_magnitude_backing(result, field.roi, magnitude_backing(result, field, limit, reduce_noise))
        samples = _vector_samples if _vector_samples is not None else sample_vectors(field,limit,vector_density)
        placement = key_rect(result.shape[1], result.shape[0], field, samples, anchor_roi, vector_style, inverse)
        draw_vector_field(result, field, samples, placement, vector_style, inverse=inverse)
    return result


def _interpolator(field):
    # Measurement support ends at the outermost sample plus half a grid cell.
    # Rejected grid cells are transparent rather than painted as zero flow.
    half = field.step // 2
    left, top, patch_width, patch_height = _sample_bounds(field)
    ux = (np.arange(patch_width, dtype=np.float32) - half) / field.step
    uy = (np.arange(patch_height, dtype=np.float32) - half) / field.step
    map_x, map_y = np.meshgrid(ux, uy)
    def interpolate(grid):
        return cv2.remap(grid, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    weights = interpolate(field.valid.astype(np.float32))
    def supported(values):
        return interpolate(np.where(field.valid, values, 0).astype(np.float32)) / np.maximum(weights, 1e-6)
    return (left,top,patch_width,patch_height), weights, supported


def magnitude_backing(image, field, limit, reduce_noise):
    """A grayscale ROI. Zero is reserved for pixels without reliable support."""
    x,y,w,h = field.roi
    result = np.zeros((h,w), np.uint8)
    (left,top,pw,ph), weights, interpolate = _interpolator(field)
    dx,dy = overlay_vectors(field, reduce_noise)
    strength = np.clip(interpolate(np.hypot(dx,dy))/limit, 0., 1.)
    # Keep a little of the real, softened scene under a fixed magnitude scale.
    # Use a full-strength plate even when the separate map track is disabled.
    base = cv2.cvtColor(image[top:top+ph,left:left+pw], cv2.COLOR_RGB2GRAY)
    soft = cv2.GaussianBlur(base, (0,0), 4., borderType=cv2.BORDER_REPLICATE)
    gray = np.rint(16. + 208.*strength + .10*soft).astype(np.uint8)
    result[top-y:top-y+ph,left-x:left-x+pw] = np.where(weights >= _SUPPORT_THRESHOLD, gray, 0)
    return result


def apply_magnitude_backing(image, roi, plate):
    x,y,w,h = roi
    np.copyto(image[y:y+h,x:x+w], plate[...,None], where=(plate != 0)[...,None])


def _render_map(current_rgb8, field, component, limit, opacity, palette, soften, reduce_noise):
    base = np.asarray(current_rgb8)
    if base.ndim == 2 and base.dtype == np.uint8:
        base = cv2.cvtColor(base, cv2.COLOR_GRAY2RGB)
    if base.ndim != 3 or base.shape[2] != 3 or base.dtype != np.uint8:
        raise ValueError('BOS display needs an RGB8 image in source coordinates.')
    if not np.isfinite(limit) or limit <= 0 or not 0 <= opacity <= 1:
        raise ValueError('Choose a positive pixel scale and an opacity from zero to one.')
    if palette not in ('gray', 'color'):
        raise ValueError('Choose grayscale or color for the BOS overlay.')
    result = base.copy()
    x, y, w, h = field.roi
    if min(x, y) < 0 or x+w > base.shape[1] or y+h > base.shape[0]:
        raise ValueError('The BOS region exceeds the display image.')
    if not opacity or not field.valid.any():
        return result
    dx, dy = overlay_vectors(field, reduce_noise)
    if component in ('horizontal', 'direction'):
        values = dx
    elif component == 'vertical':
        values = dy
    elif component == 'magnitude':
        values = np.hypot(dx, dy)
    else:
        raise ValueError('Unknown BOS displacement component.')
    (left,top,patch_width,patch_height), weights, interpolate = _interpolator(field)
    signal = interpolate(values)
    if component == 'direction':
        # Interpolate signed components separately, so opposite directions cancel
        # before assigning hue or strength. Positive y is down in image space.
        vertical = interpolate(dy)
        direction = direction_tone(signal, vertical)
        signal = np.hypot(signal, vertical)
    strength = np.clip(np.abs(signal) / limit, 0, 1)
    alpha = (float(opacity) * strength * (weights >= _SUPPORT_THRESHOLD))[..., None]
    if component == 'direction':
        tone = direction
    elif palette == 'gray':
        # Conventional knife-edge polarity for a right-moving nozzle front:
        # positive signed displacement darkens, negative brightens. Magnitude
        # is unsigned and always brightens. The numeric field keeps its sign.
        if component == 'magnitude':
            tone = 255.
        else:
            tone = np.where(signal < 0, 255., 0.).astype(np.float32)[..., None]
    elif component == 'magnitude':
        # Amber through red as magnitude approaches the chosen saturation level.
        tone = np.stack((np.full_like(strength, 255), 198 - 150*strength,
                         50 - 20*strength), axis=-1)
    else:
        tone = np.where((signal < 0)[..., None], (45, 127, 255), (255, 127, 39)).astype(np.float32)
    target = result[top:top+patch_height, left:left+patch_width]
    background = target
    if soften:
        # Reduce the dot texture only under trustworthy samples. Keep the
        # illumination and context in the image; all measurements use raw data.
        soft = cv2.GaussianBlur(target, (0, 0), 4.0, borderType=cv2.BORDER_REPLICATE)
        mix = (0.93 * float(opacity) * (weights >= _SUPPORT_THRESHOLD))[..., None]
        background = target*(1-mix) + soft*mix
    target[:] = np.rint(background*(1-alpha) + tone*alpha).astype(np.uint8)
    return result
