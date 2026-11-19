"""Signed image-coordinate motion, independent of tracking/segmentation method."""
import numpy as np


def calculate_motion(track, tracks, timestamps, difference=4):
    points = np.asarray(track['points'], dtype=float).reshape(-1, 2)
    times = np.asarray(timestamps, dtype=float)
    n = max(1, int(difference))
    reference = track.get('reference_pixel')
    relative_id = track.get('relative_to')
    status = ''
    motion = points
    if reference is not None:
        origin = np.asarray(reference, dtype=float)
        if origin.shape != (2,) or not np.all(np.isfinite(origin)):
            raise ValueError('Reference must be a finite image coordinate (X, Y).')
    elif relative_id is not None:
        relative = tracks.get(relative_id, {})
        by_frame = dict(zip(relative.get('frames', []), relative.get('points', [])))
        origin = np.asarray([by_frame.get(f, (np.nan, np.nan)) for f in track['frames']], dtype=float).reshape(-1, 2)
        motion = points - origin
        if not np.all(np.isfinite(origin)):
            status = 'Reference object is not measured in every frame; missing values are unavailable.'
    elif track.get('origin_frame') is not None:
        matches = np.flatnonzero(np.asarray(track['frames']) == track['origin_frame'])
        origin = points[matches[0]] if len(matches) else np.array([np.nan, np.nan])
        if not len(matches):
            status = 'The chosen reference frame has no tracked point.'
    else:
        origin = points[0] if len(points) else np.zeros(2)
    displacement = points - origin

    def derivative(values, at):
        dt = at[n:] - at[:-n]
        delta = values[n:] - values[:-n]
        result = np.full_like(delta, np.nan, dtype=float)
        valid = np.isfinite(dt) & (dt > 0)
        np.divide(delta, dt[:, None], out=result, where=valid[:, None])
        return result, (at[n:] + at[:-n]) / 2

    velocity, velocity_times = derivative(motion, times)
    acceleration, acceleration_times = derivative(velocity, velocity_times)
    settings=track.get('motion_filter',{})
    if settings.get('mode') == 'local_polynomial':
        window=max(5,min(101,int(settings.get('window',21))))|1
        velocity=local_derivative(motion,times,velocity_times,1,window,n)
        acceleration=local_derivative(motion,times,acceleration_times,2,window,2*n)
    return {
        'X-Displacement': displacement[:, 0], 'Y-Displacement': displacement[:, 1],
        'Displacement': np.linalg.norm(displacement, axis=1),
        'X-Speed': velocity[:, 0], 'Y-Speed': velocity[:, 1],
        'Speed': np.linalg.norm(velocity, axis=1),
        'X-Acceleration': acceleration[:, 0], 'Y-Acceleration': acceleration[:, 1],
        'Acceleration': np.linalg.norm(acceleration, axis=1),
        '_velocity_ts': velocity_times, '_acceleration_ts': acceleration_times,
        '_reference_status': status,
    }


def local_derivative(points,times,targets,order,window,span):
    """Cubic local least-squares derivative at actual capture times.

    Raw positions are untouched. Fits never cross missing positions, nonpositive
    intervals, or timestamp gaps exceeding three ordinary sampling intervals.
    Output arrays retain PCA's existing centered finite-difference alignment.
    """
    output=np.full((len(targets),2),np.nan)
    if not len(targets):return output
    dt=np.diff(times);positive=dt[np.isfinite(dt)&(dt>0)]
    if not len(positive):return output
    normal=float(np.median(positive))
    valid=np.all(np.isfinite(points),axis=1)&np.isfinite(times)
    groups=np.zeros(len(times),int)
    for i in range(1,len(times)):
        groups[i]=groups[i-1]+int(not(valid[i-1] and valid[i]) or dt[i-1]<=0 or dt[i-1]>3*normal)
    boundaries=np.r_[0,np.flatnonzero(np.diff(groups))+1,len(times)]
    group_samples={int(groups[left]):np.arange(left,right) for left,right in zip(boundaries[:-1],boundaries[1:]) if valid[left]}
    for i,target in enumerate(targets):
        end=i+span
        if end>=len(times) or not(valid[i] and valid[end]) or groups[i]!=groups[end]:continue
        group=group_samples[groups[i]]
        if len(group)<max(5,order+2):continue
        center=int(np.searchsorted(times[group],target));left=max(0,min(center-window//2,len(group)-window))
        samples=group[left:left+window]
        distance=times[samples]-target;scale=float(np.max(np.abs(distance)))
        if scale<=0:continue
        design=np.polynomial.polynomial.polyvander(distance/scale,3)
        coefficients,_,rank,_=np.linalg.lstsq(design,points[samples],rcond=None)
        if rank<4:continue
        output[i]=coefficients[order]*(2 if order==2 else 1)/scale**order
    return output
