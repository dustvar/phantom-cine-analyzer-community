"""Read native Cine frames for the BOS preview and exported clips."""
import math
from pathlib import Path

import cv2
import numpy as np

from bos_processing import (ANCHOR_MIN_SIZE, camera_translation, compute_bos,
                            remove_camera_translation, render_bos)
from bos_studio_model import MAX_PLAYBACK_FPS
from bos_vectors import VectorStyle, key_rect, sample_vectors
from camera_comparator import linear_frame
from cine_metadata import CineHandler
from pyphantom_image.imagetools import ImageTools
from video_export import video_frame
from viewer_effects import default_image_settings
from viewer_processing import render_cine_image


WINDOW_STANDARD = 31
WINDOW_FINE = 17


def default_bos_settings():
    return dict(path='', position=0, reference=None, reference_count=1, roi=None, detail=True, window=WINDOW_FINE, search=4,
                method='optical_flow', component='horizontal', palette='gray', limit=0.20, opacity=0.95,
                playback_fps=30.0, soften=True, reduce_noise=True,
                motion_compensation=False, anchor_roi=None, show_vectors=False,
                vector_background='map', vector_density='balanced', **VectorStyle().settings())


def validate_bos_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError('Invalid BOS settings.')
    defaults = default_bos_settings()
    # Jobs written before detail modes used half-window grid spacing.
    if settings and 'detail' not in settings:
        defaults.update(detail=False, window=WINDOW_STANDARD)
    if settings and 'reduce_noise' not in settings:
        # Earlier releases always displayed the unfiltered displacement map.
        defaults['reduce_noise'] = False
    result = {key: settings.get(key, value) for key, value in defaults.items()}
    if type(result['reduce_noise']) is not bool:
        raise ValueError('Invalid BOS overlay noise setting.')
    if type(result['show_vectors']) is not bool:
        raise ValueError('Invalid BOS vector field setting.')
    if result['vector_background'] not in ('map', 'footage'):
        raise ValueError('Choose a displacement map or original footage for the vector background.')
    if result['vector_density'] not in ('sparse', 'balanced', 'dense', 'extra_dense'):
        raise ValueError('Choose a supported vector density.')
    VectorStyle.from_settings(result)
    if type(result['reference_count']) is not int or result['reference_count'] not in (1, 4, 8):
        raise ValueError('Choose a BOS reference of 1, 4 or 8 frames.')
    if result['method'] not in ('optical_flow', 'cross_correlation'):
        raise ValueError('Unknown BOS method. Choose optical flow or cross-correlation.')
    if type(result['detail']) is not bool:
        raise ValueError('Invalid BOS sampling mode.')
    if type(result['soften']) is not bool:
        raise ValueError('Invalid BOS dot softening.')
    if type(result['motion_compensation']) is not bool:
        raise ValueError('Invalid BOS camera movement correction.')
    if not isinstance(result['path'], str):
        raise ValueError('Invalid BOS source.')
    for key in ('reference', 'position'):
        value = result[key]
        if not (key == 'reference' and value is None) and (type(value) is not int or not 0 <= value < 2**31):
            raise ValueError('Invalid BOS frame.')
    for key in ('roi', 'anchor_roi'):
        roi = result[key]
        if roi is not None:
            if (not isinstance(roi, (list, tuple)) or len(roi) != 4 or
                    not all(type(v) is int and 0 <= v < 2**31 for v in roi) or min(roi[2:]) < 1):
                raise ValueError('Invalid BOS region.')
            if key == 'anchor_roi' and min(roi[2:]) < ANCHOR_MIN_SIZE:
                raise ValueError(f'The stationary patch must be at least {ANCHOR_MIN_SIZE} × {ANCHOR_MIN_SIZE} pixels.')
            result[key] = list(roi)
    if result['component'] not in ('horizontal', 'vertical', 'magnitude', 'direction') or result['palette'] not in ('gray', 'color'):
        raise ValueError('Invalid BOS display mode.')
    if result['component'] == 'direction':
        result['palette'] = 'color'
    for key, low, high in [('limit', .01, 16), ('opacity', 0, 1), ('playback_fps', 1, MAX_PLAYBACK_FPS)]:
        value = result[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('Invalid BOS ' + key + '.')
    if type(result['window']) is not int or result['window'] not in range(9, 62, 2):
        raise ValueError('Invalid BOS window.')
    if type(result['search']) is not int or not 2 <= result['search'] <= 16:
        raise ValueError('Invalid BOS search range.')
    return result


class BOSFrames:
    """One reader with one reference and one last result. Owned by a single worker."""
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self.reader = CineHandler()
        try:
            self.reader.load_cine(self.path)
            self.md = self.reader.metadata
            self.count = int(self.md.ImageCount)
            self.first = int(self.md.FirstImageNo)
            self.times = self.reader.capture_timestamps_from_trigger()
        except Exception:
            self.reader.close()
            raise
        self.image_tools = ImageTools()
        self.settings = default_image_settings(self.md)
        self.reference_key = self.position = self.field_key = None
        self.ref_pixels = self.cur_pixels = self.base = self.field = None
        self._reference_cache = {}
        self._anchor_reference_cache = {}
        self._drift_key = self._drift = None

    def close(self):
        self.reader.close()
        self._reference_cache.clear()
        self._anchor_reference_cache.clear()

    def _measurement(self, raw):
        if self.md.CFA.lower() in ('mono', 'monochrome'):
            return raw
        rgb = linear_frame(raw, self.md)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    def render(self, settings):
        index = int(settings['position'])
        reference = settings['reference']
        if not 0 <= index < self.count or (reference is not None and not 0 <= reference < self.count):
            raise ValueError('This frame is outside the recorded Cine.')
        if index != self.position:
            raw = self.reader.get_img(index)
            self.cur_pixels = self._measurement(raw)
            base = render_cine_image(raw, self.md, self.settings, self.image_tools)
            # Encoder preparation can pad one row/column; ROI uses original coordinates.
            self.base = video_frame(base)[:self.md.ImHeight, :self.md.ImWidth]
            self.position = index
        reference_key = None
        if reference is not None:
            requested = settings.get('reference_count', 1)
            if type(requested) is not int or requested not in (1, 4, 8):
                raise ValueError('Choose 1, 4 or 8 still frames for the reference.')
            reference_key = (reference, min(self.count, reference + requested))
        if reference_key != self.reference_key:
            self._reference_cache.clear()
            self._anchor_reference_cache.clear()
            self._drift_key = self._drift = None
            ref_pixels = None
            if reference_key is not None:
                start, stop = reference_key
                def pixels(i):
                    return self.cur_pixels if i == index else self._measurement(self.reader.get_img(i))
                ref_pixels = pixels(start)
                if stop - start > 1:
                    # Own the accumulator: the pinned frame can alias the current image.
                    # Average only the fixed baseline; do not blur a moving wave in time.
                    ref_pixels = ref_pixels.astype(np.float32, copy=True)
                    for i in range(start + 1, stop):
                        ref_pixels += pixels(i)
                    ref_pixels /= stop - start
            self.ref_pixels, self.reference_key = ref_pixels, reference_key
        image = self.base
        roi = tuple(settings['roi'] or ())
        field = None
        display_field = None
        vector_samples = None
        vector_key_rect = None
        anchor_roi = settings.get('anchor_roi') if settings.get('motion_compensation', False) else None
        drift = None
        warning = ''
        if roi:
            x, y, w, h = roi
            extent = settings['window'] + 2 * settings['search']
            if x < 0 or y < 0 or x+w > self.md.ImWidth or y+h > self.md.ImHeight:
                warning = 'Redraw the region inside the current image.'
            elif min(w, h) < extent:
                warning = f'Draw a region at least {extent} × {extent} pixels or reduce the window size.'
            elif reference is not None:
                fine = settings.get('detail', False)
                method = settings.get('method', 'optical_flow')
                key = (reference_key, index, roi, settings['window'], settings['search'], fine, method)
                if self.field_key != key:
                    self.field = compute_bos(self.ref_pixels, self.cur_pixels, roi,
                                             window=settings['window'], search=settings['search'], detail=fine, method=method,
                                             _reference_cache=self._reference_cache)
                    self.field_key = key
                field = self.field
                display_field = field
                if not field.valid.any():
                    warning = 'No reliable dot matches in the analysis region; showing original footage.'
                if settings.get('motion_compensation', False):
                    anchor = tuple(settings.get('anchor_roi') or ())
                    if (len(anchor) != 4 or min(anchor[:2]) < 0 or min(anchor[2:]) < ANCHOR_MIN_SIZE
                            or anchor[0]+anchor[2] > self.md.ImWidth or anchor[1]+anchor[3] > self.md.ImHeight):
                        drift = dict(reliable=False, dx=None, dy=None, coverage=0., scatter=None,
                                     message=f'Choose a stationary dot patch at least {ANCHOR_MIN_SIZE} × {ANCHOR_MIN_SIZE} pixels inside the image.')
                    else:
                        drift_key = (reference_key, index, anchor)
                        if drift_key != self._drift_key:
                            self._drift = camera_translation(self.ref_pixels, self.cur_pixels, anchor,
                                                            _reference_cache=self._anchor_reference_cache)
                            self._drift_key = drift_key
                        drift = dict(self._drift)
                    if drift['reliable']:
                        display_field = remove_camera_translation(field, drift)
                    else:
                        display_field = None
                        warning = drift['message'] + ' Camera correction unavailable; showing original footage.'
                if display_field is not None:
                    show_vectors = settings.get('show_vectors', False)
                    vector_style = VectorStyle.from_settings(settings)
                    if show_vectors:
                        vector_samples = sample_vectors(display_field, settings['limit'],
                                                        settings.get('vector_density', 'balanced'))
                        vector_key_rect = key_rect(self.base.shape[1], self.base.shape[0], display_field,
                                                   vector_samples, anchor_roi, vector_style)
                    image = render_bos(image, display_field, component=settings['component'], limit=settings['limit'],
                                       opacity=settings['opacity'], palette=settings['palette'], soften=settings['soften'],
                                       reduce_noise=settings.get('reduce_noise', False), show_vectors=show_vectors,
                                       vector_background=settings.get('vector_background', 'map'),
                                       anchor_roi=anchor_roi, _vector_samples=vector_samples, vector_style=vector_style)
        time_us = float(self.times[index] * 1e6) if self.times is not None else None
        return dict(image=np.ascontiguousarray(image), field=field, display_field=display_field, drift=drift,
                    vector_samples=vector_samples, vector_key_rect=vector_key_rect,
                    index=index, first=self.first,
                    count=self.count, reference=reference, time_us=time_us, warning=warning,
                    width=int(self.md.ImWidth), height=int(self.md.ImHeight))
