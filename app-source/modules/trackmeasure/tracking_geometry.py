"""PCA geometry adapter: independent ROI/search transforms in image coordinates."""
import math
from dataclasses import dataclass
import numpy as np
from PySide6.QtCore import QPointF, QRectF


def rotate(points, angle):
    r=math.radians(float(angle));c,s=math.cos(r),math.sin(r)
    return np.asarray(points,dtype=float) @ np.array([[c,s],[-s,c]])


def corners(center,size,angle=0):
    w,h=(np.asarray(size,dtype=float)-1)/2
    return rotate([[-w,-h],[w,-h],[w,h],[-w,h]],angle)+np.asarray(center)


def contain_region(center,size,angle,roi_center,roi_size,roi_angle):
    """Expand only the outer box; never translate or modify the inner fixture."""
    local=rotate(corners(roi_center,roi_size,roi_angle)-np.asarray(center),-angle)
    required=2*np.max(np.abs(local),axis=0)+3
    values=np.maximum(np.asarray(size,dtype=float),required)
    return tuple(int(math.ceil(v))|1 for v in values)


def pose_at(track,frame):
    indices=np.flatnonzero(np.asarray(track['frames'])==frame)
    if not len(indices):return None
    index=int(indices[0]);point=np.asarray(track['points'][index],dtype=float)
    angle=float(track.get('angles',np.zeros(len(track['frames'])))[index])
    if not math.isfinite(angle):angle=0.
    if track.get('tumbling_seed') is not None:
        return np.asarray(track['tumbling_seed'])+np.asarray(track.get('template_offset',(0,0))),0.
    edge=track.get('tracking_method')=='Hybrid (Edge + Intensity)'
    center=point+rotate(track.get('template_offset',(0,0)),-angle if edge else 0)
    return center,angle


def search_pose(track,roi_center,pose):
    delta=-(pose-float(track.get('template_angle',0))) if track.get('search_follow_rotation',False) else 0.
    center=np.asarray(roi_center)+rotate(track.get('search_offset',(0,0)),delta)
    return center,float(track.get('search_angle',0))+delta,delta


def search_constraint(track,roi_center,pose):
    center,angle,_=search_pose(track,roi_center,pose)
    return dict(center=tuple(center),size=tuple(track['search_area']),angle=angle,
                roi_size=tuple(track['tpl_rng']),motion_axis=track.get('motion_axis','xy'),
                anchor_point=tuple(track.get('anchor_point',track['t_points'][0])),
                template_offset=tuple(track.get('template_offset',(0,0))))


def search_bounds(center,size,angle,frame_size):
    pts=corners(center,size,angle)
    lo=np.maximum(0,np.floor(pts.min(axis=0)).astype(int))
    hi=np.minimum(np.asarray(frame_size,dtype=int),np.ceil(pts.max(axis=0)).astype(int)+1)
    if np.any(hi<=lo):raise ValueError('Search area does not intersect the image.')
    actual=hi-lo
    # Matcher converts coordinates with floor(size/2), matching these bounds.
    origin_center=lo+actual//2
    return tuple(lo),tuple(hi),tuple(origin_center),tuple(actual)


@dataclass
class RegionSnapshot:
    object_id:int
    rectangle:QRectF
    center:QPointF
    angle:float
    def rect(self):return self.rectangle
    def pos(self):return self.center
    def rotation(self):return self.angle


def snapshot(item):
    return RegionSnapshot(item.object_id,QRectF(item.rect()),QPointF(item.pos()),float(item.rotation()))
