"""Image-space placement and artwork for editable BOS titles and vector keys."""
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from bos_processing import measurement_step
from bos_vectors import _key


@dataclass(frozen=True)
class CardPlacement:
    # Fractions of the free space between the card and the frame edges.
    # None keeps the default inset until the first drag.
    anchor: tuple | None = None
    scale: float = 1.


def card_rect(width, height, w, h, anchor=None, default=(.5, .5), margin=0):
    dx, dy = width-w, height-h
    if anchor is None:
        mx, my = min(margin, dx//2), min(margin, dy//2)
        return (mx+round(default[0]*(dx-2*mx)), my+round(default[1]*(dy-2*my)), w, h)
    ax, ay = anchor
    return round(min(1., max(0., ax))*dx), round(min(1., max(0., ay))*dy), w, h


def composite_card(image, card):
    if card is not None:
        pixels, (x, y, w, h) = card
        patch = image[y:y+h, x:x+w]
        alpha = pixels[:, :, 3:4].astype(np.uint16)
        patch[:] = (pixels[:, :, :3]*alpha + patch*(255-alpha) + 127)//255
    return image


@lru_cache(maxsize=32)
def _key_artwork(width, height, full_length, limit, style, scale):
    # Size the label and surround; never stretch the pictured measurement arrow.
    # Lock the layout during a drag so hitting a frame edge cannot collapse it
    # into a narrower design. Keep a common extent while the two styles fade.
    compact = any(_key(width, height, full_length, limit, style, inv, .25,
                       allow_compact=False) is None for inv in (False, True))

    def pair(size):
        result = [_key(width, height, full_length, limit, style, inv, size,
                       allow_compact=compact) for inv in (False, True)]
        return result if all(art is not None for art in result) else None

    result = pair(scale)
    if result is None:
        low, high = .25, scale
        result = pair(low)
        if result is None:
            return None
        for _ in range(10):
            middle = (low+high)/2
            trial = pair(middle)
            if trial is None:
                high = middle
            else:
                low, result = middle, trial
    h = max(art.shape[0] for art in result)
    w = max(art.shape[1] for art in result)
    return tuple(cv2.copyMakeBorder(art, 0, h-art.shape[0], 0, w-art.shape[1],
                                    cv2.BORDER_CONSTANT, value=(24,33,43)) for art in result)


@lru_cache(maxsize=48)
def _key_sprite(width, height, full_length, limit, style, scale, map_level, vector_level):
    art = _key_artwork(width, height, full_length, limit, style, scale)
    if art is None:
        return None
    regular, inverse = art
    if map_level in (0,255):
        sprite = regular if map_level else inverse
    else:
        sprite = cv2.addWeighted(regular,map_level/255.,inverse,1.-map_level/255.,0)
    rgba = np.empty((*sprite.shape[:2],4),np.uint8)
    rgba[:,:,:3] = sprite
    rgba[:,:,3] = round(.90*vector_level)
    return rgba


class StudioVectorKey:
    """Rebuild the measured length key from the cached analysis settings."""
    def __init__(self, clip):
        self.clip = clip
        self.available = clip.has_vector_track
        if self.available:
            from bos_vectors import vector_geometry
            settings = clip.manifest['settings']
            w,h = settings['roi'][2:]
            step = measurement_step(settings['window'], settings['detail'])
            _, self.full_length = vector_geometry(w,h,step,settings['vector_density'])
            self.limit = settings['limit']
            self.map_opacity = settings['opacity']

    def graphic(self, index, placement, style, map_level, vector_level):
        if not self.available or not vector_level or index < 0:
            return None
        measured = self.clip._measured
        if measured is not None and not measured[index]:
            return None
        if not self.clip.has_magnitude_backing:
            map_level = 255
        elif not self.map_opacity:
            map_level = 0
        w, h = self.clip.width, self.clip.height
        sprite = _key_sprite(w, h, self.full_length, self.limit, style or self.clip.vector_style,
                             round(placement.scale, 3), map_level, vector_level)
        if sprite is None:
            return None
        kh, kw = sprite.shape[:2]
        rect = card_rect(w, h, kw, kh, placement.anchor, (1.,1.), max(3, round(min(w,h)*.014)))
        return sprite, rect
