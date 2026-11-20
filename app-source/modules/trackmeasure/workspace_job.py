"""Versioned whole-job archives: JSON + typed arrays, never executable pickle.

Media stays external. Paths and source fingerprints support moving/relinking a
job without silently attaching tracking results to different footage.
"""
from copy import deepcopy
from pathlib import Path
import io
import json
import math
import os
import tempfile
import zipfile
import numpy as np
from viewer_project import source_identity
from viewer_timeline import validate_edit, total_frames

FORMAT = 'phantom-analyzer-job'
VERSION = 1
MAX_ARCHIVE_BYTES = 1024*1024*1024
CONTEXT_FIELDS = ('track_data','meas_data','active_object','_active_frame','_viewer_frame',
    'zoom_pos','_image_brightness','_image_contrast','_image_gamma','_toggle_ftone',
    '_raw_enabled','_image_scale','_known_length','_length_units','_area_units',
    '_time_units','_time_scale','viewer_grade','viewer_edit')
TOOL_FIELDS = ('points','phys_pts','points_frame_ids')
TOOL_NAMES = ('ScaleTool','ManualTrackTool','AutoTrackTool','TwoPointTool','ThreePointTool','TwoLineTool','AreaTool')


def validate_pane_view(view, count):
    """Reject invalid pane identities before a saved job changes live footage."""
    order = view.get('pane_order', list(range(count)))
    if (not isinstance(order, list) or len(order) != count
            or any(type(index) is not int for index in order)
            or sorted(order) != list(range(count))):
        raise ValueError('Invalid saved Cine pane order.')
    if view.get('pane_layout', 'auto') not in ('auto', 'grid', 'side_by_side', 'stacked'):
        raise ValueError('Invalid saved Cine pane layout.')
    if 'play_visible' in view and type(view['play_visible']) is not bool:
        raise ValueError('Invalid synchronized playback option.')


def tool_state(tool):
    if tool is None:return None
    return dict(kind=type(tool).__name__,state={k:deepcopy(getattr(tool,k)) for k in TOOL_FIELDS})


def restore_tool(vm,saved):
    if saved is None:return None
    import simplemeas_tools
    if saved['kind'] not in TOOL_NAMES:raise ValueError('Unsupported saved measurement tool.')
    tool=getattr(simplemeas_tools,saved['kind'])(vm)
    for k in TOOL_FIELDS:setattr(tool,k,deepcopy(saved['state'][k]))
    return tool


def capture_context(context):
    return dict(source=context['cine_path'],fields={k:deepcopy(context[k]) for k in CONTEXT_FIELDS},
                tool=tool_state(context['active_tool']))


def encode(value,archive):
    counter=[0]
    def visit(v):
        if isinstance(v,np.generic):v=v.item()
        if v is None or type(v) in (bool,int,str):return v
        if type(v) is float:
            if math.isfinite(v):return v
            return {'@float':str(v)}
        if isinstance(v,np.ndarray):
            # Object arrays are encoded recursively; np.load never permits pickle.
            if v.dtype.hasobject:return {'@object_array':visit(v.tolist()),'shape':list(v.shape)}
            buffer=io.BytesIO();np.save(buffer,v,allow_pickle=False)
            name=f'arrays/{counter[0]}.npy';counter[0]+=1;archive.writestr(name,buffer.getvalue())
            return {'@array':name}
        if isinstance(v,dict):return {'@dict':[[visit(k),visit(x)] for k,x in v.items()]}
        if isinstance(v,tuple):return {'@tuple':[visit(x) for x in v]}
        if isinstance(v,list):return [visit(x) for x in v]
        raise ValueError('Cannot save unsupported job data: '+type(v).__name__)
    return visit(value)


def decode(value,archive):
    def visit(v,depth=0):
        if depth>100:raise ValueError('Job data is nested too deeply.')
        if isinstance(v,list):return [visit(x,depth+1) for x in v]
        if not isinstance(v,dict):return v
        if set(v)=={'@dict'}:
            result={}
            for k,x in v['@dict']:
                key=visit(k,depth+1)
                if key in result:raise ValueError('Duplicate job data key.')
                result[key]=visit(x,depth+1)
            return result
        if set(v)=={'@tuple'}:return tuple(visit(x,depth+1) for x in v['@tuple'])
        if set(v)=={'@float'} and v['@float'] in ('nan','inf','-inf'):return float(v['@float'])
        if set(v)=={'@array'}:
            name=v['@array']
            if not isinstance(name,str) or not name.startswith('arrays/') or not name.endswith('.npy'):
                raise ValueError('Invalid array reference.')
            with archive.open(name) as stream:
                buffer=io.BytesIO(stream.read())
                version=np.lib.format.read_magic(buffer)
                if version==(1,0):shape,order,dtype=np.lib.format.read_array_header_1_0(buffer)
                elif version==(2,0):shape,order,dtype=np.lib.format.read_array_header_2_0(buffer)
                else:raise ValueError('Unsupported array format in job.')
                if dtype.hasobject or len(shape)>32 or math.prod(shape)*dtype.itemsize!=buffer.getbuffer().nbytes-buffer.tell():
                    raise ValueError('Invalid array size or type in job.')
                buffer.seek(0);result=np.load(buffer,allow_pickle=False)
            if not isinstance(result,np.ndarray):raise ValueError('Invalid array.')
            return result
        if set(v)=={'@object_array','shape'}:
            data=visit(v['@object_array'],depth+1);shape=v['shape']
            result=np.asarray(data,dtype=object)
            if list(result.shape)!=shape:raise ValueError('Invalid object array shape.')
            return result
        raise ValueError('Unrecognized job data encoding.')
    return visit(value)


def media_paths(payload):
    paths=[]
    for context in payload['contexts']:
        paths.append(context['source'])
        for copy in context['fields']['viewer_edit'].get('copies',[]):
            if copy.get('source_path'):paths.append(copy['source_path'])
    for name in ('bos', 'event_finder'):
        source = payload.get(name, {}).get('path')
        if source:
            paths.append(source)
    overlay=payload.get('overlay',{})
    paths.extend(p for p in overlay.get('paths',[]) if p)
    comparator=payload.get('comparator',{})
    paths.extend(comparator.get('states',{}))
    if comparator.get('source'):paths.append(comparator['source'])
    return list(dict.fromkeys(paths))


def save_job(path,payload):
    path=Path(path).resolve();payload=deepcopy(payload)
    payload['media']={}
    for source in media_paths(payload):
        absolute=Path(source).resolve()
        if absolute==path:raise ValueError('A job cannot overwrite its source footage.')
        payload['media'][source]=dict(absolute=str(absolute),relative=os.path.relpath(absolute,path.parent),identity=source_identity(absolute))
    manifest=dict(format=FORMAT,version=VERSION)
    fd,temporary=tempfile.mkstemp(prefix='.pca-job-',suffix='.tmp',dir=path.parent);os.close(fd)
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
            manifest['payload']=encode(payload,archive)
            text=json.dumps(manifest,ensure_ascii=False,allow_nan=False)
            if len(text.encode('utf-8'))>64*1024*1024:raise ValueError('Job manifest exceeds the 64 MiB limit.')
            archive.writestr('job.json',text)
            if sum(entry.file_size for entry in archive.infolist())>MAX_ARCHIVE_BYTES:
                raise ValueError('Job data exceeds the 1 GiB limit.')
        # Flush archive to disk before atomically replacing the previous save.
        with open(temporary,'rb') as stream:os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:Path(temporary).unlink(missing_ok=True)


def read_job(path):
    if Path(path).stat().st_size>MAX_ARCHIVE_BYTES:raise ValueError('Job archive exceeds the 1 GiB limit.')
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist();names=[i.filename for i in entries]
        if len(entries)>100000 or len(set(names))!=len(names) or sum(i.file_size for i in entries)>MAX_ARCHIVE_BYTES:
            raise ValueError('Job archive is too large or contains duplicate entries.')
        if archive.getinfo('job.json').file_size>64*1024*1024:raise ValueError('Job manifest is too large.')
        manifest=json.loads(archive.read('job.json'))
        if manifest.get('format')!=FORMAT or manifest.get('version')!=VERSION:raise ValueError('Unsupported PCA job version.')
        payload=decode(manifest['payload'],archive)
    if not isinstance(payload,dict) or not isinstance(payload.get('contexts'),list) or len(payload['contexts'])>4:
        raise ValueError('Invalid Cine workspace in job.')
    sources=[c.get('source') for c in payload['contexts']]
    if any(not isinstance(p,str) or not p for p in sources) or len(set(sources))!=len(sources):raise ValueError('Invalid or duplicate Cine references.')
    if type(payload.get('active')) is not int or (payload['contexts'] and not 0<=payload['active']<len(payload['contexts'])):
        raise ValueError('Invalid active Cine in job.')
    if set(media_paths(payload))!=set(payload.get('media',{})):raise ValueError('Incomplete source information in job.')
    for context in payload['contexts']:
        fields=context.get('fields',{})
        if set(fields)!=set(CONTEXT_FIELDS):raise ValueError('Incomplete saved Cine state.')
        tool=context.get('tool')
        if tool is not None and (tool.get('kind') not in TOOL_NAMES or set(tool.get('state',{}))!=set(TOOL_FIELDS)):
            raise ValueError('Invalid saved measurement tool.')
    return payload


def resolve_media(payload,job_path,relink=None):
    """Resolve everything before changing the live workspace. Cancellation is atomic."""
    mapping={};directories=[]
    for old,ref in payload['media'].items():
        candidates=[Path(job_path).resolve().parent/ref['relative'],Path(ref['absolute'])]
        candidates += [directory/Path(old).name for directory in directories]
        resolved=None
        for candidate in candidates:
            if candidate.is_file() and source_identity(candidate)==ref['identity']:
                resolved=str(candidate.resolve());break
        if resolved is None and relink is not None:
            selected=relink(old)
            if not selected:raise ValueError('Opening the job was cancelled; current work is unchanged.')
            if source_identity(selected)!=ref['identity']:
                raise ValueError('Selected footage does not match the saved job: '+Path(old).name)
            resolved=str(Path(selected).resolve());directories.append(Path(resolved).parent)
        if resolved is None:raise FileNotFoundError('Missing or changed footage: '+old)
        mapping[old]=resolved
    result=deepcopy(payload)
    for context in result['contexts']:
        context['source']=mapping[context['source']]
        for copy in context['fields']['viewer_edit']['copies']:
            if copy.get('source_path'):copy['source_path']=mapping[copy['source_path']]
    for name in ('bos', 'event_finder'):
        if result.get(name, {}).get('path'):
            result[name]['path'] = mapping[result[name]['path']]
    if 'overlay' in result:result['overlay']['paths']=[mapping[p] if p else '' for p in result['overlay']['paths']]
    if 'comparator' in result:
        c=result['comparator'];c['states']={mapping[p]:s for p,s in c.get('states',{}).items()}
        if c.get('source'):c['source']=mapping[c['source']]
    return result


def validate_context(saved,context):
    f=saved['fields'];count=int(context['cine_handler'].metadata.ImageCount)
    if not isinstance(f['track_data'],dict) or not isinstance(f['meas_data'],dict):raise ValueError('Invalid tracking or measurement data.')
    if f['active_object'] is not None and f['active_object'] not in f['track_data']:raise ValueError('Invalid selected object.')
    if any(type(key) is not int or key<0 for key in f['track_data']):raise ValueError('Invalid object IDs.')
    if not isinstance(f['zoom_pos'],(tuple,list)) or len(f['zoom_pos'])!=2 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in f['zoom_pos']):raise ValueError('Invalid image center.')
    for key in ('_length_units','_area_units','_time_units'):
        if not isinstance(f[key],str):raise ValueError('Invalid measurement units.')
    if '_curr_i' not in f['meas_data']:raise ValueError('Incomplete measurements.')
    for track in f['track_data'].values():
        if not isinstance(track,dict) or not isinstance(track.get('name'),str) or type(track.get('enabled')) is not bool or not isinstance(track.get('color'),str):raise ValueError('Invalid object properties.')
        frames,points=track.get('frames'),track.get('points')
        if not isinstance(frames,np.ndarray) or frames.ndim!=1 or frames.dtype.kind not in 'iu' or np.any(frames<0) or np.any(frames>=count):
            raise ValueError('Tracking frames are outside this Cine.')
        if not isinstance(points,np.ndarray) or points.shape!=(len(frames),2) or not np.all(np.isfinite(points)):
            raise ValueError('Invalid tracked points.')
        reference=track.get('reference_pixel')
        if reference is not None:
            width=context['cine_handler'].metadata.ImWidth
            height=context['cine_handler'].metadata.ImHeight
            if (not isinstance(reference,(tuple,list)) or len(reference)!=2
                or any(type(value) not in (int,float) or not math.isfinite(value) for value in reference)
                or not (0<=reference[0]<width and 0<=reference[1]<height)):
                raise ValueError('Invalid fixed reference pixel.')
        origin=track.get('origin_frame')
        if origin is not None and (not isinstance(origin,(int,np.integer)) or not 0<=origin<count):
            raise ValueError('Invalid displacement reference frame.')
        motion=track.get('motion_filter')
        if motion is not None and (not isinstance(motion,dict) or motion.get('mode') not in ('raw','local_polynomial') or type(motion.get('window')) is not int or not 5<=motion['window']<=101):
            raise ValueError('Invalid motion filter.')
        if track.get('motion_axis','xy') not in ('xy','x','y'):raise ValueError('Invalid motion constraint.')
        for key in ('search_angle','template_angle'):
            if key in track and (not isinstance(track[key],(int,float)) or not math.isfinite(track[key])):raise ValueError('Invalid tracking angle.')
        for key in ('search_offset','template_offset','anchor_point'):
            if key in track and (not isinstance(track[key],(tuple,list)) or len(track[key])!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in track[key])):raise ValueError('Invalid tracking geometry.')
        for key in ('t_frames',):
            arr=track.get(key)
            if not isinstance(arr,np.ndarray) or arr.ndim!=1 or np.any(arr<0) or np.any(arr>=count):raise ValueError('Invalid template frames.')
    f['viewer_edit']=validate_edit(f['viewer_edit'],count)
    for name,limit in (('_active_frame',count),('_viewer_frame',total_frames(f['viewer_edit']))):
        if type(f[name]) is not int or not 0<=f[name]<limit:raise ValueError('Invalid saved playhead.')
    for name in ('_image_scale','_known_length','_time_scale','_image_gamma'):
        if not isinstance(f[name],(int,float)) or not math.isfinite(f[name]) or f[name]<=0:raise ValueError('Invalid calibration or image setting.')
    grade=f['viewer_grade']
    from viewer_effects import validate_copy_appearance
    validate_copy_appearance(dict(grade=grade, image_settings=dict(image_brightness=f['_image_brightness'],
        image_contrast=f['_image_contrast'],image_gamma=f['_image_gamma'],raw_enabled=f['_raw_enabled'],toggle_ftone=f['_toggle_ftone'])))
