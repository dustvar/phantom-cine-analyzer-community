"""The same compact data card for Qt previews and exported frames."""
from functools import lru_cache
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import matplotlib
from viewer_timeline import overlay_text, camera_overlay_text, default_camera_overlay


@lru_cache(maxsize=64)
def _font(size, bold=False):
    path = Path(matplotlib.get_data_path()) / 'fonts/ttf' / ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf')
    return ImageFont.truetype(str(path), size)


def overlay_graphic(width, height, text, options):
    size = max(9, round(height * options['size'] / 100))
    lines = text.splitlines()

    def layout(size):
        runs = []
        for index, line in enumerate(lines):
            scale = 1.0 if options.get('uniform') else (1.0, .8, .62)[min(index, 2)]
            font = _font(max(1, round(size * scale)), index == 0)
            runs.append((line, font, font.getbbox(line)))
        chunks = []
        gap = max(2, round(size * .22))
        if options.get('layout', 'stacked') == 'straight':
            # Separate the fields on a shared baseline, keeping the hierarchy.
            separator_font = _font(max(1, round(size * .62)))
            separated = []
            for index, run in enumerate(runs):
                if index:
                    separated.append(('·', separator_font, separator_font.getbbox('·')))
                separated.append(run)
            baseline = max(font.getmetrics()[0] - bb[1] for _, font, bb in separated)
            total_w = total_h = 0
            for line, font, bb in separated:
                dy = baseline - font.getmetrics()[0] + bb[1]
                chunks.append((line, font, bb, total_w, dy))
                total_w += bb[2] - bb[0] + gap
                total_h = max(total_h, dy + bb[3] - bb[1])
            total_w -= gap
        else:
            total_w = total_h = 0
            for index, (line, font, bb) in enumerate(runs):
                if index: total_h += gap
                chunks.append((line, font, bb, 0, total_h))
                total_h += bb[3] - bb[1]
                total_w = max(total_w, bb[2] - bb[0])
        pad = max(2, round(size * .25))
        return chunks, total_w + 2 * pad, total_h + 2 * pad, pad

    chunks, w, h, pad = layout(size)
    while (w > width or h > height) and size > 1:
        size = max(1, min(size - 1, int(size * min(width / w, height / h))))
        chunks, w, h, pad = layout(size)
    w, h = min(width, w), min(height, h)
    x = round(options['x'] * max(0, width - w))
    y = round(options['y'] * max(0, height - h))
    graphic = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(graphic)
    if options['background']:
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=max(2, pad), fill=(0, 0, 0, 180))
    for line, font, bound, dx, dy in chunks:
        draw.text((pad + dx - bound[0], pad + dy - bound[1]), line, font=font,
                  fill=options['color'], stroke_width=max(1, round(size / 24)), stroke_fill=(0, 0, 0, 230))
    return np.asarray(graphic), (x, y, w, h)


def apply_fps_overlay(image, text, options):
    if not options.get('enabled', False) or not text:
        return image, None
    graphic, rect = overlay_graphic(image.shape[1], image.shape[0], text, options)
    x, y, w, h = rect
    result = image.copy()
    alpha = graphic[:, :, 3].astype(np.float32) / 255
    maximum = np.iinfo(image.dtype).max
    color = graphic[:, :, :3].astype(np.float32) / 255 * maximum
    patch = image[y:y+h, x:x+w].astype(np.float32)
    if image.ndim == 2:
        color = color @ np.array([.2126, .7152, .0722], dtype=np.float32)
    else:
        alpha = alpha[:, :, None]
    result[y:y+h, x:x+w] = np.rint(patch * (1 - alpha) + color * alpha).astype(image.dtype)
    return result, rect


def apply_data_overlays(image, edit, metadata, source_fps):
    """Draw both independently enabled cards, returning hit boxes in paint order."""
    boxes = {}
    camera = edit.get('camera_overlay', default_camera_overlay())
    playback = edit['overlay']
    for key, options, text in [
        ('camera', camera, camera_overlay_text(camera, metadata)),
        ('playback', playback, overlay_text(playback, source_fps, edit['output_fps'],
            getattr(metadata, 'FrameRate', None), getattr(metadata, 'fDecimation', 1)))]:
        image, rect = apply_fps_overlay(image, text, options)
        if rect is not None:
            boxes[key] = rect
    return image, boxes
