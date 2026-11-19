"""Fast Classic intensity correlation with local subpixel refinement.

The native-pixel search finds candidate locations. Only small neighborhoods of
those locations are sampled at the requested subpixel spacing, so a 1/10-pixel
setting does not inflate the whole search window to 100 times as many pixels.
Coordinates refer to source pixel centers; interpolation adds no half-pixel bias.
"""
import math
import cv2
import numpy as np
from scipy.ndimage import map_coordinates, spline_filter
from edge_tracking import candidate_search_mask, constrain_match_center, Data

ORDERS = {'linear': 1, 'quadratic': 2, 'cubic': 3, 'quartic': 4, 'quintic': 5}


def _correlation(image, template):
    """Centered zero-mean normalized correlation, including the search border."""
    h, w = template.shape
    padded = cv2.copyMakeBorder(image, h//2, (h-1)//2, w//2, (w-1)//2,
                               cv2.BORDER_CONSTANT, value=0)
    return cv2.matchTemplate(padded, template, cv2.TM_CCOEFF_NORMED)


def _sample(coefficients, ys, xs, order):
    yy, xx = np.meshgrid(ys, xs, indexing='ij')
    return map_coordinates(coefficients, (yy, xx), order=order,
                           mode='constant', cval=0.0, prefilter=False,
                           output=np.float32)


def match_classic(template, search, origin, factor=10, interpolation='cubic', constraint=None):
    template = np.ascontiguousarray(template, dtype=np.float32)
    search = np.ascontiguousarray(search, dtype=np.float32)
    factor = int(factor)
    order = ORDERS[interpolation.lower()]
    if factor < 1 or factor > 10:
        raise ValueError('Classic precision must be between 1 and 1/10 pixel.')
    if template.ndim != 2 or search.ndim != 2 or min(template.shape) < 2:
        raise ValueError('Classic requires a two-dimensional textured region.')
    if any(t > s for t, s in zip(template.shape, search.shape)):
        raise ValueError('The search area must contain the tracking region.')
    if not np.all(np.isfinite(template)) or not np.all(np.isfinite(search)):
        raise ValueError('The tracking images contain non-finite values.')
    if float(np.std(template)) <= np.finfo(np.float32).eps * max(1., float(np.max(np.abs(template)))):
        raise ValueError('The tracking region is featureless. Select a visible point or textured region.')
    coarse = _correlation(search, template)
    allowed = np.ones(coarse.shape, dtype=bool)
    if constraint is not None:
        coarse_constraint = dict(constraint)
        if factor > 1:
            # A valid fractional center need not have an integer center inside
            # the rectangle. Expand only this candidate-finding stage by 1px.
            coarse_constraint['size'] = tuple(float(v)+2 for v in constraint['size'])
        allowed = candidate_search_mask(np.arange(search.shape[1])[None, :]+origin[0],
                                        np.arange(search.shape[0])[:, None]+origin[1],
                                        0., coarse_constraint,
                                        axis_tolerance=1.01 if factor > 1 else .51)
    if not np.any(allowed):
        raise ValueError('No candidate fits the search area.')
    coarse = np.where(allowed, coarse, -np.inf)
    if factor == 1:
        row, col = np.unravel_index(np.argmax(coarse), coarse.shape)
        x, y = float(col+origin[0]), float(row+origin[1])
        score = float(coarse[row, col])
    else:
        # Preserve alternative peaks so a slightly poorer integer-pixel match
        # can become the strongest match at fractional coordinates.
        candidates = []
        remaining = coarse.copy()
        top = float(np.max(remaining))
        for _ in range(5):
            row, col = np.unravel_index(np.argmax(remaining), remaining.shape)
            value = float(remaining[row, col])
            if not math.isfinite(value) or (candidates and value < top-.15):
                break
            candidates.append((row, col))
            remaining[max(0,row-3):row+4, max(0,col-3):col+4] = -np.inf
        sc = spline_filter(search, order=order, mode='constant') if order > 1 else search
        tc = spline_filter(template, order=order, mode='constant') if order > 1 else template
        th, tw = template.shape
        fine_template = _sample(tc, np.arange((th-1)*factor+1)/factor,
                               np.arange((tw-1)*factor+1)/factor, order)
        best = None
        for row, col in candidates:
            xmin, xmax = max(0,col-2), min(search.shape[1]-1,col+2)
            ymin, ymax = max(0,row-2), min(search.shape[0]-1,row+2)
            xs = np.arange((xmax-xmin+tw-1)*factor+1)/factor+xmin-(tw-1)/2
            ys = np.arange((ymax-ymin+th-1)*factor+1)/factor+ymin-(th-1)/2
            fine_search = _sample(sc, ys, xs, order)
            scores = cv2.matchTemplate(fine_search, fine_template, cv2.TM_CCOEFF_NORMED)
            cx = np.arange(scores.shape[1])[None, :]/factor+xmin+origin[0]
            cy = np.arange(scores.shape[0])[:, None]/factor+ymin+origin[1]
            if constraint is not None:
                valid = candidate_search_mask(cx, cy, 0., constraint, axis_tolerance=.51/factor)
                if not np.any(valid):
                    continue
                scores = np.where(valid, scores, -np.inf)
            iy, ix = np.unravel_index(np.argmax(scores), scores.shape)
            candidate = (float(scores[iy,ix]), float(cx[0,ix]), float(cy[iy,0]))
            if best is None or candidate[0] > best[0]:
                best = candidate
        if best is None:
            raise ValueError('No fractional candidate fits the search area.')
        score, x, y = best
    if constraint is not None:
        x, y = constrain_match_center(x, y, 0., constraint)
    # This diagnostic map is native resolution; final coordinates and score
    # come from the local subpixel search. No measurement uses the map's grid.
    return Data(norm_xcorr_map=coarse, x_pos=x, y_pos=y,
                confid_val_ij=float(np.clip(score,-1.,1.)))
