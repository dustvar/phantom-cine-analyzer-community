"""Compact measured arrow geometry for independent, offline BOS compositing."""
from dataclasses import dataclass
import struct

import cv2
import numpy as np

from bos_processing import apply_magnitude_backing, magnitude_backing, measurement_step
from bos_vectors import VectorSamples, draw_vector_field, key_rect, sample_vectors, vector_capacity


HEADER = struct.Struct('<Idd')
SITES = struct.Struct('<I')
# Store coordinates, measured shifts and actual projected stroke lengths. Drawing
# can change color, weight or symbol without reading the Cine or estimating flow.
SITE = np.dtype([(k, '<i4') for k in ('rows', 'cols')]
                + [(k, '<f4') for k in ('x', 'y', 'dx', 'dy', 'draw_dx', 'draw_dy')]
                + [('saturated', '?')], align=False)


@dataclass(frozen=True)
class _Region:
    roi: tuple
    valid: np.bool_ = np.bool_(True)


def max_frame_bytes(settings):
    if not settings['show_vectors']:
        return 0
    w, h = settings['roi'][2:]
    step = measurement_step(settings['window'], settings['detail'])
    sites = vector_capacity(w, h, step, settings['vector_density'])
    # The grayscale image is compressed losslessly. Allow PNG headers and row overhead.
    return HEADER.size + SITES.size + sites*SITE.itemsize + w*h + 2*h + 65536 + 16


def write_vectors(stream, field, settings, base):
    start = stream.tell()
    if field is None or not field.valid.any():
        stream.write(HEADER.pack(0, 0., 0.))
    else:
        s = sample_vectors(field, settings['limit'], settings['vector_density'])
        stream.write(HEADER.pack(s.pitch, s.full_length, s.limit))
        stream.write(SITES.pack(len(s.x)))
        data = np.empty(len(s.x), dtype=SITE)
        for name in SITE.names:
            data[name] = getattr(s, name)
        stream.write(data.tobytes())
        plate = magnitude_backing(base, field, s.limit, settings['reduce_noise'])
        ok, png = cv2.imencode('.png', plate, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        if not ok:
            raise OSError('Could not store the BOS magnitude background.')
        stream.write(png.tobytes())
    return start, stream.tell()-start


def paint_vectors(stream, offsets, index, frame, settings, style, *, include_key=True,
                  has_backing=False, map_level=255, original=None):
    start, size = map(int, offsets[index])
    stream.seek(start)
    payload = stream.read(size)
    if len(payload) != size or size < HEADER.size:
        raise ValueError('Incomplete cached BOS vector field.')
    pitch, full_length, limit = HEADER.unpack_from(payload)
    if pitch == 0:
        return
    pos = HEADER.size
    if has_backing:
        if size < pos+SITES.size:
            raise ValueError('Incomplete cached BOS vector geometry.')
        count, = SITES.unpack_from(payload, pos)
        pos += SITES.size
    else:
        count, remainder = divmod(size-pos, SITE.itemsize)
        if remainder:
            raise ValueError('Incomplete cached BOS vector geometry.')
    end = pos+count*SITE.itemsize
    if end > size or (has_backing and end == size):
        raise ValueError('Incomplete cached BOS vector data.')
    data = np.frombuffer(payload, dtype=SITE, count=count, offset=pos)
    samples = VectorSamples(**{name: data[name] for name in SITE.names},
                            pitch=pitch, full_length=full_length, limit=limit)
    region = _Region(tuple(settings['roi']))
    anchor = settings.get('anchor_roi') if settings.get('motion_compensation') else None

    def draw(target, inverse):
        position = (key_rect(target.shape[1],target.shape[0],region,samples,anchor,style,inverse)
                    if include_key else None)
        draw_vector_field(target, region, samples, position, style, inverse=inverse)

    if has_backing and map_level < 255:
        # At a fade between tracks, mix two complete endpoint compositions.
        # This also crossfades ink without changing measured glyph geometry.
        mono = original if original is not None else frame
        plate = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8, offset=end), cv2.IMREAD_GRAYSCALE)
        if plate is None or plate.shape != (region.roi[3], region.roi[2]):
            raise ValueError('Incomplete cached BOS magnitude background.')
        apply_magnitude_backing(mono, region.roi, plate)
        draw(mono, True)
        if map_level:
            draw(frame, False)
            cv2.addWeighted(mono, 1.-map_level/255., frame, map_level/255., 0., dst=frame)
    else:
        draw(frame, False)
