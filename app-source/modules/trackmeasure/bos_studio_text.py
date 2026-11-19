"""Build the same source-space title and data card for BOS playback and sharing."""
from bisect import bisect_right
from functools import lru_cache
import math
import textwrap
import re

import numpy as np

from PIL import Image, ImageDraw

from bos_sampling import RangeSampling
from bos_camera_labels import camera_lines
from bos_studio_cards import card_rect, composite_card
from viewer_overlay import _font


def capture_rate(clip):
    value = clip.manifest.get('capture_fps')
    if value and math.isfinite(value) and value > 0:
        return value
    # Older analyzed caches carry trigger timestamps in microseconds.
    valid = np.flatnonzero(np.isfinite(clip.times))
    stride = min(32, len(valid)-1)
    if stride < 1:
        return None
    first, last = valid[:-stride], valid[stride:]
    intervals = clip.times[last] - clip.times[first]
    forward = intervals > 0
    if not np.any(forward):
        return None
    sampling = getattr(clip, 'sampling', RangeSampling(clip.count))
    source_offsets = np.minimum(valid * sampling.step, sampling.source_count-1)
    distances = source_offsets[stride:] - source_offsets[:-stride]
    return float(np.median(distances[forward]*1e6 / intervals[forward]))


@lru_cache(maxsize=512)
def _text_run(text, size, bold):
    """Antialiased glyphs on the same translucent backing as the card interior."""
    font = _font(size, bold)
    ascent, descent = font.getmetrics()
    left, _, right, _ = font.getbbox(text)
    advance = max(1, math.ceil(max(font.getlength(text), right) - min(left, 0)))
    image = Image.new('RGBA', (advance, max(1, ascent+descent)), (0, 0, 0, 180))
    ImageDraw.Draw(image).text((-min(left, 0), 0), text, font=font, fill='white')
    return np.asarray(image)


@lru_cache(maxsize=24)
def _background(width, height, pad):
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle((0, 0, width-1, height-1),
                                             radius=max(2, pad), fill=(0, 0, 0, 180))
    return np.asarray(image)


# Tabular numerals can change on every displayed frame. Keep words in runs so
# titles retain their kerning and dynamic values need only a few array copies.
_NUMBER_RUNS = re.compile(r'[0-9]|[^0-9]+')


class StudioText:
    def __init__(self, clip, settings, capture_fps):
        self.clip = clip
        self.sampling = getattr(clip, 'sampling', RangeSampling(clip.count))
        self.settings = dict(settings)
        self.capture = capture_fps
        self.blocks = tuple(StudioText(clip, child, capture_fps) for child in settings.get('blocks', ()))
        self._starts = [card.settings['first'] for card in self.blocks]
        self.fields = (frozenset().union(*(card.fields for card in self.blocks)) if self.blocks
                       else frozenset(settings.get('fields', ())))
        self.size = {'small': 2.7, 'medium': 3.8, 'large': 5.3}[settings['size']]
        line_width = max(8, int(.88*clip.width / (max(9, clip.height*self.size/100)*.65)))
        self.caption = []
        if 'custom' in self.fields:
            for line in settings['custom'].strip().splitlines():
                self.caption.extend(textwrap.wrap(line, width=line_width) or [''])
        # Static titles and duplicate presentation frames reuse a small sprite.
        self._graphic = lru_cache(maxsize=2)(self._render)

    def at(self, index):
        if self.blocks:
            pos = bisect_right(self._starts, index)-1
            return self.blocks[pos].at(index) if pos >= 0 else None
        return self if self.settings['first'] <= index <= self.settings['last'] and self.fields else None

    def lines(self, index, fps):
        if self.blocks:
            card = self.at(index)
            return card.lines(index, fps) if card else ()

        if not self.fields or not self.settings['first'] <= index <= self.settings['last']:
            return ()
        lines = self.caption.copy()
        if 'frame' in self.fields:
            lines.append(f'Frame {self.sampling.offset(index)+1:,} of {self.sampling.source_count:,}')
        if 'playback' in self.fields:
            lines.append(f'{fps:,.2f}'.rstrip('0').rstrip('.') + ' fps playback')
        if self.capture and 'capture' in self.fields:
            label = ' fps capture' if self.clip.manifest.get('capture_fps') else ' fps recorded (est.)'
            lines.append(f'{self.capture:,.2f}'.rstrip('0').rstrip('.') + label)
        if self.capture and 'slowdown' in self.fields:
            # The camera can save only every Nth exposure to its Cine.
            stride = self.clip.manifest.get('decimation', 1) if self.clip.manifest.get('capture_fps') else 1
            factor = self.capture/(max(1, stride)*fps)
            if abs(factor-1) < .0005:
                lines.append('Real time')
            else:
                value, label = (factor, 'slower') if factor > 1 else (1/factor, 'faster')
                lines.append(f'{value:,.2f}'.rstrip('0').rstrip('.') + f'× {label}')
        lines.extend(camera_lines(self.clip, self.fields, index))
        if 'time' in self.fields and math.isfinite(self.clip.times[index]):
            lines.append(f't = {self.clip.times[index]:+,.2f} µs')
        return tuple(lines)

    def graphic(self, index, fps):
        if self.blocks:
            child = self.at(index)
            return child.graphic(index, fps) if child else None
        lines = self.lines(index, fps)
        return self._graphic(lines) if lines else None

    def _render(self, lines):
        width, height = self.clip.width, self.clip.height
        margin = round(min(width, height)*.022)
        available_w = max(1, width-2*margin)
        available_h = max(1, height-2*margin)
        size = max(1, round(max(9, round(available_h*self.size/100))*self.settings.get('scale', 1.)))
        # Only the frame counter and trigger timestamp vary continuously. Cache
        # all other lines as complete runs, including the user’s own caption.
        dynamic = set()
        if 'frame' in self.fields:
            dynamic.add(len(self.caption))
        for i, line in enumerate(lines[len(self.caption):], len(self.caption)):
            if line.startswith(('t = ', 'Cine frame ', 'Exposure ')):
                dynamic.add(i)
        tokens = [_NUMBER_RUNS.findall(line) if i in dynamic else [line] for i,line in enumerate(lines)]
        while True:
            pad = max(1, round(size*.3))
            gap = max(1, round(size*.12))
            rows = [[_text_run(token, size, i == 0) for token in row] for i,row in enumerate(tokens)]
            widths = [sum(run.shape[1] for run in row) for row in rows]
            heights = [row[0].shape[0] if row else size for row in rows]
            w = max(widths, default=0) + 2*pad
            h = sum(heights) + gap*(len(rows)-1) + 2*pad
            if (w <= available_w and h <= available_h) or size == 1:
                break
            size = max(1, min(size-1, int(size*min(available_w/w, available_h/h))))
        w, h = min(w, available_w), min(h, available_h)
        sprite = _background(w, h, pad).copy()
        top = pad
        for row, row_height in zip(rows, heights):
            left = pad
            for run in row:
                draw_w, draw_h = min(run.shape[1], w-left), min(run.shape[0], h-top)
                if draw_w > 0 and draw_h > 0:
                    sprite[top:top+draw_h, left:left+draw_w] = run[:draw_h, :draw_w]
                left += run.shape[1]
            top += row_height + gap
        ax, ay = {'top_left': (0, 0), 'top_right': (1, 0), 'bottom_left': (0, 1),
                  'bottom_right': (1, 1), 'center': (.5, .5)}[self.settings.get('position','center')]
        return sprite, card_rect(width, height, w, h, self.settings.get('anchor'), (ax, ay), margin)

    def composite(self, image, index, fps):
        return composite_card(image, self.graphic(index, fps))
