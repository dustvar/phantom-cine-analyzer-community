"""Non-destructive Cine sections and a shared preview/export time map.

Section FPS means source frames per output second. Output FPS is the constant
video-file cadence. Ramps use smoothstep velocity integrated in output time;
their duration is measured in seconds of the finished video.
"""
from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass
import math
from viewer_processing import default_grade


def default_overlay():
    return dict(enabled=False, fields=['slowdown', 'playback'], size=3.0, x=.96, y=.96,
                background=True, color='#ffffff', layout='stacked', decimals=False, pinned=True)


def default_camera_overlay():
    return dict(enabled=False, fields=['model', 'capture', 'resolution'], size=3.0, x=.04, y=.96,
                background=True, color='#ffffff', layout='stacked', decimals=False, pinned=True)


def default_edit(frame_count=0, fps=30.0):
    return dict(version=3, source_count=frame_count, copies=[{}], texts=[], selection=[0,frame_count-1], output_fps=float(fps), loop=False,
                sections=([dict(start=0, end=frame_count - 1, fps=float(fps),
                                ramp=0.0, ramp_place='right', ramp_curve='smooth', keep=True, zoom=default_zoom())] if frame_count else []),
                overlay=default_overlay(), camera_overlay=default_camera_overlay())


def default_zoom():
    return dict(factor=1.0, cx=.5, cy=.5, duration=0.0, curve='smooth', inherit=False)


def source_count(edit):
    return edit.get('source_count', edit['sections'][-1]['end'] + 1 if edit['sections'] else 0)


def copy_count(edit, index):
    return edit.get('copies', [{}])[index].get('frame_count', source_count(edit))


def copy_offset(edit, index):
    return sum(copy_count(edit,i) for i in range(index))


def total_frames(edit):
    return sum(copy_count(edit,i) for i in range(len(edit.get('copies',[{}]))))


def copy_index(edit, frame):
    ends=[]; end=0
    for i in range(len(edit.get('copies',[{}]))):
        end+=copy_count(edit,i); ends.append(end)
    return min(len(ends)-1,bisect_right(ends,max(0,int(frame))))


def copy_options(edit, index):
    return edit if index == 0 else edit['copies'][index]


def frame_label(edit, frame, first_frame=0):
    ci=copy_index(edit,frame)
    first_frame=edit.get('copies',[{}])[ci].get('first_frame',first_frame)
    label=f'{frame-copy_offset(edit,ci)+first_frame:,}'
    return f'V{ci+1} · {label}' if len(edit.get('copies',[{}]))>1 else label


def is_video_join(edit, index):
    return index > 0 and edit['sections'][index]['start'] in [copy_offset(edit,i) for i in range(1,len(edit.get('copies',[{}])))]


def zoom_options(edit, index):
    for i in range(index, -1, -1):
        options = edit['sections'][i].get('zoom', default_zoom())
        if not options.get('inherit', False):
            return dict(options, duration=0.0) if i != index else deepcopy(options)
    return default_zoom()


def append_video(edit, settings=None, first=None, last=None, media=None, template_index=0):
    result = deepcopy(edit)
    template=result['copies'][template_index]
    info=deepcopy(media) if media else {k:deepcopy(v) for k,v in template.items() if k in ('frame_count','source_path','identity','first_frame','name','playback_fps')}
    count=info.get('frame_count',copy_count(result,template_index))
    info['frame_count']=count
    if not count or len(result['copies']) >= 100 or total_frames(result) + count > 2_147_483_647:
        raise ValueError('This timeline has reached its video-copy limit.')
    # Freeze the current In/Out selection before extending the sequence. A new
    # pass always contains the entire original Cine, regardless of that trim.
    if first is not None and last is not None:
        for index, options in enumerate(result['copies']):
            length=copy_count(result,index);offset=copy_offset(result,index)
            lo, hi = options.get('trim', [0, length - 1])
            lo, hi = max(lo, first - offset), min(hi, last - offset)
            options['trim'] = [max(0,lo), min(length-1,hi)] if lo <= hi else [0,-1]
    start = total_frames(result)
    fresh = dict(overlay=default_overlay(), camera_overlay=default_camera_overlay(),
                 texts=[], grade=default_grade(), image_settings=deepcopy(settings))
    fresh.update(info)
    result['copies'].append(fresh)
    section = default_edit(count,info.get('playback_fps',30))['sections'][0]
    section.update(start=start, end=start+count-1)
    result['sections'].append(section)
    result['selection']=[0,total_frames(result)-1]
    return result


def upgrade_edit(edit):
    """Migrate older single-pass recipes without rewriting the saved file."""
    if not isinstance(edit, dict) or edit.get('version') not in (1, 2):
        return edit
    result = deepcopy(edit)
    if result['version'] == 1:
        for section in result.get('sections', []):
            if isinstance(section, dict): section.update(ramp_place='right', ramp_curve='smooth')
        overlay = result.get('overlay')
        if isinstance(overlay, dict):
            if overlay.get('mode') not in ('playback', 'speed', 'effective'):
                raise ValueError('Invalid saved FPS display mode')
            overlay.pop('mode'); overlay['fields'] = ['capture', 'slowdown', 'playback']
    result['version'] = 3
    sections = result.get('sections')
    if isinstance(sections, list) and sections and isinstance(sections[-1], dict):
        result.setdefault('source_count', sections[-1].get('end', -1) + 1)
        for section in sections:
            if isinstance(section, dict): section.setdefault('zoom', default_zoom())
    result.setdefault('copies', [{}]); result.setdefault('texts', [])
    result.setdefault('selection',[0,result.get('source_count',0)-1])
    for key, default in [('overlay', default_overlay), ('camera_overlay', default_camera_overlay)]:
        options = result.setdefault(key, default())
        if isinstance(options, dict): options.setdefault('pinned', True)
    return result


def validate_edit(edit, frame_count):
    """Reject malformed saved edits rather than silently changing their timing."""
    edit = upgrade_edit(edit)
    if not isinstance(edit, dict) or edit.get('version') != 3:
        raise ValueError('Unsupported Viewer edit version')
    result = deepcopy(edit)
    def finite(value, low, high, label):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'Invalid {label}')
    finite(result.get('output_fps'), 1, 120, 'output FPS')
    if type(result.get('source_count')) is not int or result['source_count'] != frame_count:
        raise ValueError('The edit does not match the source frame count')
    copies = result.get('copies')
    if not isinstance(copies, list) or not 1 <= len(copies) <= 100 or any(not isinstance(c, dict) for c in copies):
        raise ValueError('Invalid video copies')
    for copy in copies:
        length=copy.get('frame_count',frame_count)
        if type(length) is not int or length<1: raise ValueError('Invalid video frame count')
        if copy.get('source_path') is not None and (not isinstance(copy['source_path'],str) or not copy['source_path']): raise ValueError('Invalid video path')
        if 'first_frame' in copy and type(copy['first_frame']) is not int: raise ValueError('Invalid video frame origin')
    total = total_frames(result)
    if total > 2_147_483_647: raise ValueError('Timeline is too long')
    selection=result.setdefault('selection',[0,total-1])
    if not isinstance(selection,list) or len(selection)!=2 or any(type(v) is not int for v in selection) or not 0<=selection[0]<=selection[1]<total: raise ValueError('Invalid timeline selection')
    sections = result.get('sections')
    if not isinstance(sections, list) or not sections or len(sections) > total:
        raise ValueError('Invalid section list')
    next_frame = 0
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError('Invalid section')
        start, end = section.get('start'), section.get('end')
        if type(start) is not int or type(end) is not int or start != next_frame or not start <= end < total or copy_index(result,start) != copy_index(result,end):
            raise ValueError('Sections must cover each video copy in frame order')
        finite(section.get('fps'), .1, 10_000_000, 'section playback FPS')
        finite(section.get('ramp'), 0, 3600, 'transition duration')
        if section.get('ramp_place') not in ('left', 'right', 'center'):
            raise ValueError('Invalid transition placement')
        if section.get('ramp_curve') not in ('smooth', 'linear', 'ease_in', 'ease_out'):
            raise ValueError('Invalid transition curve')
        if type(section.get('keep')) is not bool:
            raise ValueError('Invalid section visibility')
        zoom = section.setdefault('zoom', default_zoom())
        if not isinstance(zoom, dict) or type(zoom.get('inherit')) is not bool:
            raise ValueError('Invalid zoom')
        for key, low, high in [('factor',1,20),('cx',0,1),('cy',0,1),('duration',0,3600)]:
            finite(zoom.get(key), low, high, 'zoom ' + key)
        if zoom.get('curve') not in ('smooth','linear','ease_in','ease_out'):
            raise ValueError('Invalid zoom curve')
        if start == copy_offset(result,copy_index(result,start)) and zoom['inherit']:
            raise ValueError('A new video must have its own zoom setting')
        next_frame = end + 1
    if next_frame != total:
        raise ValueError('Sections do not cover this Cine')
    from viewer_effects import validate_texts, validate_copy_appearance
    from viewer_track_fade import validate_track_fade
    for index, copy in enumerate(copies):
        length=copy_count(result,index)
        trim = copy.get('trim', [0,length-1])
        if not isinstance(trim,list) or len(trim)!=2 or any(type(v) is not int for v in trim) or not (trim == [0,-1] or 0 <= trim[0] <= trim[1] < length):
            raise ValueError('Invalid copy trim')
        options = copy_options(result, index)
        rotation = options.get('rotation', 0)
        if type(rotation) is not int or rotation not in (0, 90, 180, 270):
            raise ValueError('Invalid video rotation')
        if 'track_fade' in options:
            validate_track_fade(options['track_fade'], length)
        options.setdefault('camera_overlay', default_camera_overlay())
        if index:
            validate_copy_appearance(options)
        validate_texts(options.setdefault('texts', []))
        for option_key, allowed in [('overlay', ('capture', 'slowdown', 'playback')),
                                    ('camera_overlay', ('model', 'capture', 'resolution'))]:
            overlay = options.get(option_key)
            if not isinstance(overlay, dict):
                raise ValueError('Invalid data box')
            overlay.setdefault('pinned', True)
            if type(overlay['pinned']) is not bool: raise ValueError('Invalid data zoom anchoring')
            fields = overlay.get('fields')
            if not isinstance(fields, list) or any(v not in allowed for v in fields) or len(set(fields)) != len(fields):
                raise ValueError('Invalid data fields')
            overlay.setdefault('layout', 'stacked')
            overlay.setdefault('decimals', False)
            if overlay['layout'] not in ('stacked', 'straight') or type(overlay['decimals']) is not bool:
                raise ValueError('Invalid data layout or decimal option')
            for key, low, high in [('size', 1, 15), ('x', 0, 1), ('y', 0, 1)]:
                finite(overlay.get(key), low, high, 'data box ' + key)
            color = overlay.get('color', '')
            if not isinstance(color, str) or len(color) != 7 or color[0] != '#' or any(c not in '0123456789abcdefABCDEF' for c in color[1:]):
                raise ValueError('Invalid data text color')
            if type(overlay.get('enabled')) is not bool or type(overlay.get('background')) is not bool:
                raise ValueError('Invalid data box options')
    if type(result.get('loop')) is not bool:
        raise ValueError('Invalid loop option')
    return result


def section_at(edit, frame):
    for index, section in enumerate(edit['sections']):
        if section['start'] <= frame <= section['end']:
            return index
    return None


def split_at(edit, frame):
    result = deepcopy(edit)
    index = section_at(result, frame)
    if index is None or frame == result['sections'][index]['start']:
        return result
    section = result['sections'][index]
    right = dict(section, start=int(frame), ramp=0.0, ramp_place='right', ramp_curve='smooth')
    right['zoom'] = dict(default_zoom(), inherit=True)
    section['end'] = int(frame) - 1
    result['sections'].insert(index + 1, right)
    return result


def move_cut(edit, right_index, frame):
    """Move a source boundary without dropping frames or erasing either neighbor."""
    result = deepcopy(edit)
    if not 0 < right_index < len(result['sections']):
        return result
    if is_video_join(result, right_index): return result
    left, right = result['sections'][right_index - 1:right_index + 1]
    frame = max(left['start'] + 1, min(right['end'], int(frame)))
    left['end'], right['start'] = frame - 1, frame
    return result


def merge_next(edit, index):
    result = deepcopy(edit)
    if 0 <= index < len(result['sections']) - 1 and not is_video_join(result, index + 1):
        following = result['sections'].pop(index + 1)
        result['sections'][index]['end'] = following['end']
    return result


def ease_value(curve, u):
    u = min(1.0, max(0.0, u))
    if curve == 'linear': return u
    if curve == 'ease_in': return u * u
    if curve == 'ease_out': return 2 * u - u * u
    return 3 * u * u - 2 * u * u * u


def ease_integral(curve, u):
    if curve == 'linear': return u * u / 2
    if curve == 'ease_in': return u ** 3 / 3
    if curve == 'ease_out': return u * u - u ** 3 / 3
    return u ** 3 - u ** 4 / 2


def ramp_distance(a, b, curve, u):
    """Source distance per one second of total ramp duration, through phase u."""
    return a * u + (b - a) * ease_integral(curve, u)


@dataclass
class MotionSpan:
    start: float  # Position in the concatenation of included source frames.
    length: float
    rate_from: float
    rate_to: float
    duration: float
    curve: str = 'smooth'

    def distance(self, seconds):
        u = min(1.0, max(0.0, seconds / self.duration))
        return self.duration * ramp_distance(self.rate_from, self.rate_to, self.curve, u)

    def rate(self, seconds):
        return self.rate_from + (self.rate_to - self.rate_from) * ease_value(self.curve, seconds / self.duration)

    def time_for_distance(self, distance):
        distance = min(max(distance, 0), self.length)
        if self.rate_from == self.rate_to:
            return distance / self.rate_to
        if distance == 0: return 0.0
        if distance == self.length: return self.duration
        lo, hi = 0.0, self.duration
        for _ in range(52):
            mid = (lo + hi) / 2
            if self.distance(mid) < distance: lo = mid
            else: hi = mid
        return (lo + hi) / 2


class Timeline:
    def __init__(self, edit, first, last):
        edit = upgrade_edit(edit)
        self.output_fps = float(edit['output_fps'])
        self.parts, self.ends, self.transitions, self.ramp_limits = [], [], [], []
        self.duration = 0.0
        # With plain joins, each pass retains its exact original time map,
        # including a trim through the middle of a speed ramp.
        joins=[s for i,s in enumerate(edit['sections']) if is_video_join(edit,i)]
        if len(edit.get('copies',[{}]))>1 and all(s['ramp']==0 for s in joins):
            for ci,copy in enumerate(edit['copies']):
                offset=copy_offset(edit,ci);count=copy_count(edit,ci)
                indexed=[(i,s) for i,s in enumerate(edit['sections']) if copy_index(edit,s['start'])==ci]
                lo,hi=copy.get('trim',[0,count-1]);lo=max(lo,first-offset);hi=min(hi,last-offset)
                if lo>hi:continue
                child=dict(edit,source_count=count,copies=[{}],sections=[dict(s,start=s['start']-offset,end=s['end']-offset) for _,s in indexed])
                part=Timeline(child,lo,hi)
                for span,t0,t1,time,a,b,shift in part.parts:
                    self.parts.append((span,t0,t1,time+self.duration,a+offset,b+offset,shift+offset))
                self.ends.extend(time+self.duration for time in part.ends)
                for t in part.transitions:
                    t=dict(t,index=indexed[t['index']][0],left_index=indexed[t['left_index']][0])
                    for key in ('source_left_start','source_right_end','source_cut'):t[key]+=offset
                    self.transitions.append(t)
                self.ramp_limits.extend((frame+offset,wanted,actual) for frame,wanted,actual in part.ramp_limits)
                self.duration+=part.duration
            self.frame_count=max(1,math.ceil(self.duration*self.output_fps-1e-8)) if self.parts else 0
            self.output_duration=self.frame_count/self.output_fps
            return
        groups = []
        # A plain split inside an existing ramp must not change that ramp.
        for index, original in enumerate(edit['sections']):
            section = dict(original)
            ci = copy_index(edit, section['start']); count = copy_count(edit,ci); offset=copy_offset(edit,ci)
            trim = edit.get('copies', [{}])[ci].get('trim', [0,count-1])
            section['start'] = max(section['start'], offset + trim[0])
            section['end'] = min(section['end'], offset + trim[1])
            if not section['keep'] or section['end'] < section['start']: continue
            if groups and not is_video_join(edit, index) and groups[-1]['end'] + 1 == section['start'] and groups[-1]['fps'] == section['fps'] and section['ramp'] == 0:
                groups[-1]['end'] = section['end']
                groups[-1]['last_index'] = index
            else:
                groups.append(dict(section, index=index, last_index=index))
        cursor = 0.0
        for group in groups:
            group['vstart'] = cursor
            group['length'] = group['end'] - group['start'] + 1
            cursor += group['length']
            group['vend'] = cursor
        for left, right in zip(groups, groups[1:]):
            a, b = float(left['fps']), float(right['fps'])
            phase = {'left': 1.0, 'center': .5, 'right': 0.0}[right['ramp_place']]
            curve, requested = right['ramp_curve'], float(right['ramp'])
            l = ramp_distance(a, b, curve, phase)
            r = ramp_distance(a, b, curve, 1) - l
            self.transitions.append(dict(index=right['index'], left_index=left['last_index'],
                boundary=right['vstart'], a=a, b=b, curve=curve, place=right['ramp_place'],
                phase=phase, requested=requested, duration=requested, left_per_second=l, right_per_second=r))
        # Neighboring ramps share finite source footage. Proportional reductions
        # only decrease consumption, so already-satisfied constraints stay valid.
        for i, group in enumerate(groups):
            incoming = self.transitions[i - 1] if i else None
            outgoing = self.transitions[i] if i < len(self.transitions) else None
            consumption = ((incoming['duration'] * incoming['right_per_second']) if incoming else 0)
            consumption += ((outgoing['duration'] * outgoing['left_per_second']) if outgoing else 0)
            if consumption > group['length']:
                factor = group['length'] / consumption
                if incoming: incoming['duration'] *= factor
                if outgoing: outgoing['duration'] *= factor
        motions = []
        cursor = 0.0
        for i, transition in enumerate(self.transitions):
            d = transition['duration']
            l, r = d * transition['left_per_second'], d * transition['right_per_second']
            start, end = transition['boundary'] - l, transition['boundary'] + r
            transition.update(left_frames=l, right_frames=r,
                source_left_start=groups[i]['end'] + 1 - l,
                source_right_end=groups[i + 1]['start'] + r,
                source_cut=groups[i + 1]['start'])
            if start > cursor + 1e-10:
                motions.append(MotionSpan(cursor, start - cursor, transition['a'], transition['a'], (start - cursor) / transition['a']))
            if d > 0:
                motions.append(MotionSpan(start, l + r, transition['a'], transition['b'], d, transition['curve']))
            cursor = end
            if d + 1e-8 < transition['requested'] and transition['source_right_end'] > first and transition['source_left_start'] <= last:
                self.ramp_limits.append((transition['source_cut'], transition['requested'], d))
        if groups and groups[-1]['vend'] > cursor + 1e-10:
            length, rate = groups[-1]['vend'] - cursor, float(groups[-1]['fps'])
            motions.append(MotionSpan(cursor, length, rate, rate, length / rate))
        # Slice motions at original file boundaries and In/Out. Keeping the
        # original phase makes trimming through any ramp preserve its velocity.
        group_ends = [g['vend'] for g in groups]
        for span in motions:
            gi = min(bisect_right(group_ends, span.start), len(groups) - 1)
            while gi < len(groups):
                group = groups[gi]
                if group['vstart'] >= span.start + span.length - 1e-10: break
                shift = group['start'] - group['vstart']
                lo = max(first, group['start'], span.start + shift)
                hi = min(last + 1, group['end'] + 1, span.start + span.length + shift)
                if hi > lo + 1e-10:
                    t0 = span.time_for_distance(lo - shift - span.start)
                    t1 = span.time_for_distance(hi - shift - span.start)
                    self.parts.append((span, t0, t1, self.duration, lo, hi, shift))
                    self.duration += t1 - t0
                    self.ends.append(self.duration)
                gi += 1
        self.frame_count = max(1, math.ceil(self.duration * self.output_fps - 1e-8)) if self.parts else 0
        self.output_duration = self.frame_count / self.output_fps

    def sample(self, seconds):
        if not self.parts:
            raise ValueError('No included frames in this In/Out range')
        seconds = max(0.0, min(float(seconds), self.duration))
        index = min(bisect_right(self.ends, seconds), len(self.parts) - 1)
        span, t0, t1, offset, lo, hi, shift = self.parts[index]
        time = min(t1, t0 + (seconds - offset))
        position = span.start + span.distance(time) + shift
        # Subtracting a long prior section's time can lose low bits at very
        # high playback rates. Snap within that floating-point uncertainty.
        epsilon = max(1e-8, 8 * math.ulp(seconds) * span.rate(time))
        frame = min(math.ceil(hi - 1e-9) - 1, max(math.floor(lo + 1e-9), math.floor(position + epsilon)))
        return frame, span.rate(time)

    def time_for_source(self, frame):
        if not self.parts: return 0.0
        for span, t0, t1, offset, lo, hi, shift in self.parts:
            if frame < lo: return offset
            if frame < hi:
                return offset + (span.time_for_distance(frame - shift - span.start) - t0)
        return max(0, self.duration - 1 / self.output_fps)

    def samples(self):
        for index in range(self.frame_count):
            yield self.sample(index / self.output_fps)


def format_number(value, decimals=False):
    # Whole numbers are a display choice, never a change to playback timing.
    if not decimals and 0 < value < 1:
        return '<1'
    return f'{value:,.1f}' if decimals else f'{value:,.0f}'


def overlay_text(overlay, source_fps, output_fps, capture_fps, decimation=1):
    """Compact capture/playback data. Slowdown is relative to physical reality.

    Output cadence does not affect source frames consumed per output second.
    Decimated Cines retain the raw capture label but use saved-frame timing for
    their real-world slowdown ratio.
    """
    fields = overlay.get('fields', ['capture', 'slowdown', 'playback'])
    if not fields: return ''
    valid_capture = isinstance(capture_fps, (int, float)) and math.isfinite(capture_fps) and capture_fps > 0
    decimals = overlay.get('decimals', False)
    lines = []
    if 'slowdown' in fields:
        if valid_capture and math.isfinite(source_fps) and source_fps > 0:
            factor = capture_fps / max(1, decimation) / source_fps
            if abs(factor - 1) < .0005: text = 'Real time'
            elif factor > 1: text = f'{format_number(factor, decimals)}× slower'
            else: text = f'{format_number(1 / factor, decimals)}× faster'
        else: text = 'Slowdown unavailable'
        lines.append(text)
    if 'capture' in fields:
        lines.append(f'{format_number(capture_fps, decimals)} fps' if valid_capture else 'Capture FPS unavailable')
    if 'playback' in fields:
        lines.append(f'{format_number(source_fps, decimals)} fps playback')
    # Keep fields separate so the shared renderer can stack or align them.
    return '\n'.join(lines)


def camera_overlay_text(overlay, metadata):
    """Describe the source camera; exported resizing does not change this data."""
    fields = overlay.get('fields', ['model', 'capture', 'resolution'])
    lines = []
    if 'model' in fields:
        model = getattr(metadata, 'CameraModel', '')
        lines.append(model.strip() if isinstance(model, str) and model.strip() else 'Model not stored')
    if 'capture' in fields:
        fps = getattr(metadata, 'FrameRate', None)
        valid = isinstance(fps, (int, float)) and math.isfinite(fps) and fps > 0
        lines.append(f"{format_number(fps, overlay.get('decimals', False))} fps" if valid else 'Capture FPS unavailable')
    if 'resolution' in fields:
        width, height = getattr(metadata, 'ImWidth', 0), getattr(metadata, 'ImHeight', 0)
        lines.append(f'{width:,} × {height:,} px' if width and height else 'Resolution unavailable')
    return '\n'.join(lines)
