"""Pure display series for tracking plots, independent of Qt and sensor storage.

Position measurements stay in sensor pixels. Unit conversion is applied only
when assembling a graph/export snapshot; derivatives use each Cine's own time
scale before conversion to the graph's common time scale.
"""
import numpy as np

LENGTH_METERS = {'μm': 1e-6, 'mm': 1e-3, 'cm': 1e-2, 'm': 1., 'in': .0254, 'ft': .3048}
LENGTH_UNITS = ('μm', 'mm', 'cm', 'm', 'in', 'ft', 'pix')


def normalize_length_unit(value):
    text = str(value or 'pix').strip()
    aliases = {'um': 'μm', 'µm': 'μm', 'micron': 'μm', 'microns': 'μm',
               'inch': 'in', 'inches': 'in', 'feet': 'ft', 'foot': 'ft',
               'pixels': 'pix', 'pixel': 'pix', 'px': 'pix',
               'millimeters': 'mm', 'centimeters': 'cm', 'meters': 'm'}
    return aliases.get(text.lower(), text)


def metric_mode(metric):
    return metric.split('-')[-1]


def metric_units(metric, length_unit, time_unit):
    mode = metric_mode(metric)
    if mode == 'Vibration': return 'Hz', 'Amplitude'
    if mode == 'Angle': return time_unit, 'deg'
    if mode == 'Angular Speed': return time_unit, 'deg/' + time_unit
    suffix = {'Speed': '/' + time_unit, 'Acceleration': '/' + time_unit + '²'}.get(mode, '')
    return time_unit, length_unit + suffix


def nearest_rows(times, targets):
    """Return actual tracked rows nearest derivative sample times (earlier ties)."""
    times = np.asarray(times, dtype=float)
    targets = np.asarray(targets, dtype=float)
    if not len(times): return np.zeros(len(targets), dtype=int)
    right = np.searchsorted(times, targets).clip(0, len(times) - 1)
    left = np.maximum(0, right - 1)
    left_distance = np.abs(targets-times[left]); right_distance = np.abs(times[right]-targets)
    # A computed midpoint can round a few ulps toward either endpoint. Keep
    # the documented earlier-frame tie break stable across timestamp scales.
    tolerance = np.finfo(float).eps*8*np.maximum(1., np.maximum(np.abs(targets), np.abs(times[right])))
    return np.where(left_distance <= right_distance+tolerance, left, right)


def graph_series(tracks, metric, difference, *, target_length='pix', target_time_scale=1.,
                 default_length='pix', default_cal=None, default_time_scale=1., contexts=(), active_cine=0):
    """Return (series, omitted reasons) without modifying tracked arrays.

    Each series maps displayed samples to original frame IDs. A derivative dot
    maps to its nearest source measurement; deleting it recomputes derivatives.
    Mixed calibrated units are converted consistently. A physical-unit graph
    omits uncalibrated/incompatible Cines instead of silently relabeling pixels.
    """
    mode = metric_mode(metric); n = max(1, int(difference)); series = []; omitted = []
    target_length = normalize_length_unit(target_length)
    for key, track in tracks.items():
        if not track.get('enabled', True): continue
        cine_index, object_id = key if isinstance(key, tuple) else (active_cine, key)
        cine_index = int(track.get('_cine_index', cine_index))
        context = contexts[cine_index] if 0 <= cine_index < len(contexts) else {}
        source_length = normalize_length_unit(context.get('_length_units', default_length))
        source_scale = float(context.get('_time_scale', default_time_scale) or 1.)
        time_ratio = float(target_time_scale) / source_scale
        times = np.asarray(track.get('frame_ts', []), dtype=float)
        frames = np.asarray(track.get('frames', []), dtype=int)
        if mode in ('Speed', 'Angular Speed'):
            if len(times) <= n: continue
            sample_times = (times[n:] + times[:-n]) / 2
            power = 1
        elif mode == 'Acceleration':
            if len(times) <= 2*n: continue
            velocity_times = (times[n:] + times[:-n]) / 2
            sample_times = (velocity_times[n:] + velocity_times[:-n]) / 2
            power = 2
        else:
            sample_times = times; power = 0
        values = np.asarray(track.get(metric, []), dtype=float).reshape(-1)
        count = min(len(sample_times), len(values))
        if not count or len(times) != len(frames): continue
        sample_times = sample_times[:count]; values = values[:count].copy()
        if mode not in ('Angle', 'Angular Speed'):
            if target_length != 'pix':
                if source_length == 'pix' or (source_length != target_length and
                        (source_length not in LENGTH_METERS or target_length not in LENGTH_METERS)):
                    omitted.append(f'{track.get("name", "Object")}: calibrate this Cine to show {target_length}')
                    continue
                cal = track.get('_cine_cal', context.get('cal', default_cal))
                if cal is not None: values = np.asarray(cal.point_transform(values), dtype=float)
                if source_length != target_length:
                    values *= LENGTH_METERS[source_length] / LENGTH_METERS[target_length]
        if power: values /= time_ratio ** power
        rows = nearest_rows(times, sample_times)
        series.append(dict(cine_index=cine_index, object_id=object_id,
                           name=str(track.get('name', f'Object {object_id}')),
                           color=str(track.get('color', '#e5a3ee')),
                           x=sample_times.copy()*time_ratio, y=values,
                           frames=frames[rows].copy(), source_rows=rows.copy(),
                           notes=dict(track.get('notes', {}))))
    return series, omitted
