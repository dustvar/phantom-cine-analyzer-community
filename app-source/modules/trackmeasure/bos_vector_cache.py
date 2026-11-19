"""Compact measured arrow geometry for independent, offline BOS compositing."""
from dataclasses import dataclass
import struct

import numpy as np

from bos_processing import measurement_step
from bos_vectors import VectorSamples, draw_vector_field, key_rect, sample_vectors, vector_capacity


HEADER = struct.Struct('<Idd')
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
    return HEADER.size + sites*SITE.itemsize + 16  # Two uint64 index entries.


def write_vectors(stream, field, settings):
    start = stream.tell()
    if field is None or not field.valid.any():
        stream.write(HEADER.pack(0, 0., 0.))
    else:
        s = sample_vectors(field, settings['limit'], settings['vector_density'])
        stream.write(HEADER.pack(s.pitch, s.full_length, s.limit))
        data = np.empty(len(s.x), dtype=SITE)
        for name in SITE.names:
            data[name] = getattr(s, name)
        stream.write(data.tobytes())
    return start, stream.tell()-start


def paint_vectors(stream, offsets, index, frame, settings, style):
    start, size = map(int, offsets[index])
    stream.seek(start)
    payload = stream.read(size)
    if len(payload) != size or size < HEADER.size or (size-HEADER.size) % SITE.itemsize:
        raise ValueError('Incomplete cached BOS vector field.')
    pitch, full_length, limit = HEADER.unpack_from(payload)
    if pitch == 0:
        return
    data = np.frombuffer(payload, dtype=SITE, offset=HEADER.size)
    samples = VectorSamples(**{name: data[name] for name in SITE.names},
                            pitch=pitch, full_length=full_length, limit=limit)
    region = _Region(tuple(settings['roi']))
    anchor = settings.get('anchor_roi') if settings.get('motion_compensation') else None
    placement = key_rect(frame.shape[1], frame.shape[0], region, samples, anchor, style=style)
    draw_vector_field(frame, region, samples, placement, style=style)
