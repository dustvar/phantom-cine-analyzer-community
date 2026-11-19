"""Draw revealable trajectories from measured Cine coordinates on the RGB movie canvas."""
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageColor, ImageDraw

from viewer_overlay import _font
from image_orientation import rotate_points


def default_track_fade(start=0):
    return dict(enabled=False, start=int(start), duration=0.50, trail=30, names=False)


def validate_track_fade(options, count):
    if (not isinstance(options, dict) or type(options.get('enabled')) is not bool
            or type(options.get('names')) is not bool):
        raise ValueError('Invalid tracking fade options')
    start = options.get('start')
    if type(start) is not int or not 0 <= start < count:
        raise ValueError('The tracking reveal frame is outside this video')
    duration = options.get('duration')
    if type(duration) not in (int, float) or not np.isfinite(duration) or not 0 <= duration <= 60:
        raise ValueError('Tracking fade duration must be between 0 and 60 seconds')
    trail = options.get('trail')
    if type(trail) is not int or not 0 <= trail <= 2000:
        raise ValueError('Track trails can cover up to 2,000 source frames')


@lru_cache(maxsize=128)
def _track_color(value):
    try:
        return ImageColor.getrgb(value)[:3]
    except (TypeError, ValueError):
        return (255, 135, 51)


def draw_track_fade(image, tracks, options, local_frame, elapsed, *, rotation=0, source_shape=None):
    if not tracks or local_frame < options['start']:
        return image
    duration = options['duration']
    opacity = min(1.0, max(0.0, elapsed / duration)) if duration else 1.0
    if opacity <= 0:
        return image
    # Search sorted track frames, keeping per-frame work independent of recording length.
    first = max(options['start'], local_frame - options['trail'])
    h, w = image.shape[:2]
    radius = max(3, int(round(min(w, h) / 120)))
    width = max(1, radius // 2)
    result = image
    labels = []
    for track in tracks.values():
        if not track.get('enabled', True):
            continue
        frames = np.asarray(track['frames'])
        begin, end = np.searchsorted(frames, [first, local_frame + 1])
        if begin == end:
            continue
        points = np.asarray(track['points'])[begin:end]
        if rotation:
            source_height, source_width = source_shape
            points = rotate_points(points, rotation, source_width, source_height)
        inside = (np.isfinite(points).all(axis=1) & (points[:, 0] >= 0) & (points[:, 0] < w)
                  & (points[:, 1] >= 0) & (points[:, 1] < h))
        if not inside.any():
            continue
        measured = frames[begin:end][inside]
        xy = np.rint(points[inside]).astype(np.int32)
        color = _track_color(track.get('color', '#ff8733'))
        if result is image:
            result = image.copy()
        # Break the trail at gaps: a missed measurement cannot specify a trajectory.
        for part in np.split(xy, np.flatnonzero(np.diff(measured) != 1) + 1):
            if len(part) == 1:
                cv2.circle(result, tuple(part[0]), width, color, -1, cv2.LINE_AA)
            else:
                cv2.polylines(result, [part], False, (24, 24, 24), width + 2, cv2.LINE_AA)
                cv2.polylines(result, [part], False, color, width, cv2.LINE_AA)
        if measured[-1] == local_frame:
            point = tuple(xy[-1])
            cv2.circle(result, point, radius, (24, 24, 24), width + 2, cv2.LINE_AA)
            cv2.circle(result, point, radius, color, width, cv2.LINE_AA)
            cv2.circle(result, point, 1, color, -1, cv2.LINE_AA)
            if options['names']:
                labels.append((point, track.get('name', ''), color))
    if labels:
        canvas = Image.fromarray(result)
        painter = ImageDraw.Draw(canvas)
        font = _font(max(10, radius * 3), bold=True)
        for (x, y), name, color in labels:
            padding = radius + width + 2
            name = name[:100]
            label_width = painter.textlength(name, font=font)
            x = max(1, min(w - label_width - 2, x + padding))
            y = max(1, min(h - font.size - 4, y - font.size - padding))
            painter.text((x, y), name, font=font, fill=color, stroke_width=1, stroke_fill='black')
        result = np.asarray(canvas)
    if result is image or opacity >= 1:
        return result
    return cv2.addWeighted(image, 1 - opacity, result, opacity, 0)
