"""PCA integration only. The reusable engine lives in tumbling_tracker.py."""
from copy import deepcopy
import time
import numpy as np
from tumbling_tracker import AdaptiveSilhouetteTracker, TrackerSettings


def read_tracking_image(vm, frame):
    raw=np.asarray(vm.cine_handler.get_img(int(frame)))
    meta=vm.cine_handler.metadata
    # Preserve color when available; no Viewer grade/tone curve participates.
    return vm.image_tools.debayer(raw.astype(np.float32),meta.CFA,meta.RealBPP,force_mono=0)


def setup_tracker(vm, track, image=None):
    settings=TrackerSettings.from_dict(track.get('tumbling',{}))
    tracker=AdaptiveSilhouetteTracker(settings)
    frame=int(track['anchor_frame'])
    seed=track['tumbling_seed'] if 'tumbling_seed' in track else track['t_points'][0]
    w,h=map(int,track['tpl_rng'])
    center=np.asarray(seed)+np.asarray(track.get('template_offset',(0,0)))
    roi=(int(round(center[0]-w/2)),int(round(center[1]-h/2)),w,h)
    result=tracker.initialize(read_tracking_image(vm,frame) if image is None else image,roi,seed,frame)
    return tracker,result


def run_tumbling_track(tool, track):
    """Process both sides independently, preserving rejected-frame diagnostics."""
    vm=tool.vm;anchor=int(track['anchor_frame']);meta=vm.cine_handler.metadata
    # Validate the new setup before replacing prior measured data.
    anchor_image=read_tracking_image(vm,anchor)
    tracker,first=setup_tracker(vm,track,anchor_image)
    offset=int(meta.FirstImageNo)
    start=max(0,int(track['start'])-offset);end=min(meta.ImageCount-1,int(track['end'])-offset)
    if start>end:raise ValueError('Start frame must be at or before the end frame.')
    if not start<=anchor<=end:raise ValueError('The processing range must include the setup frame.')
    track['points']=np.asarray([first.centroid],float);track['frames']=np.asarray([anchor],int)
    track['scores']=np.asarray(['N/A']);track['angles']=np.asarray([np.nan])
    track['t_points']=track['points'].copy();track['t_frames']=track['frames'].copy();track['t_angles']=track['angles'].copy()
    track['notes']={};track['tumbling_observations']={anchor:first.record()}
    track['measurement_kind']='Projected silhouette centroid'
    track['tumbling_run_settings']=deepcopy(track['tumbling'])
    track['tumbling_dirty']=False
    total=max(1,end-start);processed=0;last_progress=-1;last_status=0.
    for direction,indices in enumerate((range(anchor+1,end+1),range(anchor-1,start-1,-1))):
        if not indices:continue
        if direction:tracker,_=setup_tracker(vm,track,anchor_image)
        for frame in indices:
            if vm.abort_autotrack:break
            result=tracker.step(read_tracking_image(vm,frame),frame)
            track['tumbling_observations'][frame]=result.record()
            if result.accepted:
                tool.add_point_to_template(track,result.centroid,frame,round(result.confidence,3),False,angle=np.nan)
            processed+=1;progress=round(100*processed/total)
            if progress!=last_progress:vm.update_progress.emit(progress);last_progress=progress
            now=time.monotonic()
            if now-last_status>=.2 or processed==total:
                vm.update_status_text.emit(f'{track["name"]} · frame {frame+offset}: {result.reason} · confidence {result.confidence:.2f}')
                last_status=now
            if tracker.stopped:break
        if vm.abort_autotrack:break
    track['tumbling_cancelled']=bool(vm.abort_autotrack)
    if not vm.abort_autotrack:vm.update_progress.emit(100)
    accepted=len(track['frames']);rejected=sum(not r['accepted'] for r in track['tumbling_observations'].values())
    vm.update_status_text.emit(f'{track["name"]}: {accepted} measured centroid(s), {rejected} rejected frame(s). '+
                              ('Cancelled; partial measurements retained.' if vm.abort_autotrack else 'Review the mask and trajectory.'))
    return track
