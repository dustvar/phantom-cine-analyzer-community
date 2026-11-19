"""Format recorded camera metadata for a title on the analyzed BOS clip."""
import math


def camera_lines(clip, fields, index):
    lines = []
    if 'camera_model' in fields:
        lines.append(clip.manifest.get('camera_model') or 'Camera model unavailable')
    if 'resolution' in fields:
        lines.append(f'{clip.width:,} × {clip.height:,} px')
    if 'exposure' in fields:
        ticks = getattr(clip, 'exposure_ticks', None)
        if ticks is not None and ticks[index]:
            microseconds = int(ticks[index]) * (1e6 / 2**32)
            label = 'Exposure'
        else:
            microseconds = clip.manifest.get('setup_exposure_us')
            label = 'Exposure setting'
        if microseconds and math.isfinite(microseconds) and microseconds > 0:
            value = f'{microseconds:,.3f}'.rstrip('0').rstrip('.')
            if value == '0':
                value = '<0.001'
            lines.append(f'{label} {value} µs')
        else:
            lines.append('Exposure unavailable')
    if 'cine_frame' in fields:
        lines.append(f'Cine frame {clip.source_frame(index):,}')
    return tuple(lines)
