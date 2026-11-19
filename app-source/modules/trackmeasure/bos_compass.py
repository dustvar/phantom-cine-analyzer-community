"""Source-image placement and shared artwork for the optional BOS direction key."""
from dataclasses import dataclass
from functools import lru_cache
import math

import cv2
import numpy as np

from bos_display import DIRECTION_COLORS


MIN_SIZE = .12
MAX_SIZE = .55


@dataclass(frozen=True)
class CompassPlacement:
    """Top-left fractions of the source dimensions, side fraction of its short edge."""
    x: float = .035
    y: float = .04
    size: float = .22


def compass_rect(placement, width, height):
    if min(width, height) < 1:
        raise ValueError('The compass needs a nonempty image.')
    if not all(math.isfinite(v) for v in (placement.x, placement.y, placement.size)):
        raise ValueError('The compass position and size must be finite.')
    scale = min(MAX_SIZE, max(MIN_SIZE, placement.size))
    side = max(1, round(scale * min(width, height)))
    x = min(width-side, max(0, round(placement.x * width)))
    y = min(height-side, max(0, round(placement.y * height)))
    return x, y, side, side


@lru_cache(maxsize=4)
def compass_rgba(side):
    """An immutable straight-alpha RGBA sprite at the requested source resolution."""
    if side < 1:
        raise ValueError('The compass size must be positive.')
    size = max(384, side)
    # Draw at high resolution so the default 100–150 px legend keeps clean edges.
    ink = np.empty((size, size, 3), np.uint8)
    ink[:] = (19, 25, 35)
    alpha = np.zeros((size, size), np.uint8)

    def point(x, y):
        return round(x*(size-1)), round(y*(size-1))

    center = point(.5, .5)
    radius = round(size*.488)
    cv2.circle(alpha, center, radius, 221, -1, cv2.LINE_AA)
    cv2.circle(ink, center, radius-1, (132, 145, 157), max(1, round(size*.004)), cv2.LINE_AA)
    origin = np.array((.5, .447))
    ring_center = point(*origin)
    cv2.circle(ink, ring_center, round(size*.272), (57, 67, 80),
               max(1, round(size*.003)), cv2.LINE_AA)
    for i, value in enumerate(DIRECTION_COLORS):
        theta = i*math.pi/4  # Clockwise in image space: right, down-right, down, ...
        unit = np.array((math.cos(theta), math.sin(theta)))
        across = np.array((-unit[1], unit[0]))
        vertices = [(.337, 0), (.23, -.052), (.246, -.019), (.087, -.013),
                    (.087, .013), (.246, .019), (.23, .052)]
        poly = np.array([point(*(origin+r*unit+t*across)) for r, t in vertices], np.int32)
        cv2.fillPoly(ink, [poly], tuple(bytes.fromhex(value[1:])), cv2.LINE_AA)
        cv2.fillPoly(alpha, [poly], 255, cv2.LINE_AA)
    cv2.circle(ink, ring_center, round(size*.027), (218, 227, 234), -1, cv2.LINE_AA)

    font = cv2.FONT_HERSHEY_SIMPLEX
    caption = 'DOT SHIFT'
    thickness = max(1, round(size*.005))
    scale = size*.0028
    (width, _), _ = cv2.getTextSize(caption, font, scale, thickness)
    baseline = ((size-width)//2, round(size*.868))
    cv2.putText(ink, caption, baseline, font, scale, (226, 232, 239), thickness, cv2.LINE_AA)
    cv2.putText(alpha, caption, baseline, font, scale, 255, thickness, cv2.LINE_AA)
    rgba = np.dstack((ink, alpha))
    if side != size:
        rgba = cv2.resize(rgba, (side, side), interpolation=cv2.INTER_AREA)
    rgba.flags.writeable = False
    return rgba


def draw_compass(image, placement):
    """Composite in place on a private, freshly decoded RGB export frame."""
    height, width = image.shape[:2]
    x, y, side, _ = compass_rect(placement, width, height)
    rgba = compass_rgba(side)
    alpha = rgba[..., 3:].astype(np.uint16)
    region = image[y:y+side, x:x+side]
    region[:] = (rgba[..., :3]*alpha + region*(255-alpha) + 127)//255
