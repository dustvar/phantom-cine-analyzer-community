"""Standalone adaptive silhouette tracking. Dependencies: NumPy and OpenCV only.

This module contains no PCA, Qt, Cine-reader, or application imports. Coordinates
are image pixels (x right, y down). A centroid is a projected area centroid, NOT
the physical center of mass. Confidence is a heuristic, not a probability.
"""
from dataclasses import dataclass, asdict
import math
import cv2
import numpy as np

TUMBLING_TRACKING_METHOD = 'Adaptive Silhouette (Tumbling)'
TUMBLING_TRACKING_DISPLAY = 'Tumbling Objects'


@dataclass(frozen=True)
class TrackerSettings:
    mode: str = 'adaptive'  # Graph-cut foreground segmentation; or dark/bright.
    threshold: float = 0.50  # Fraction between background and foreground levels.
    confidence: float = 0.60
    update_confidence: float = 0.75
    adaptation: float = 0.20
    max_motion: int = 60  # Pixels per source frame, not replay frame.
    growth: int = 15
    min_area: int = 20
    area_ratio: float = 4.0
    miss_limit: int = 3
    iterations: int = 3
    cleanup: int = 1

    def __post_init__(self):
        if self.mode not in ('adaptive', 'dark', 'bright'):
            raise ValueError('Unknown segmentation mode.')
        for key in ('threshold', 'confidence', 'update_confidence', 'adaptation'):
            value = getattr(self, key)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(key + ' must be between 0 and 1.')
        for key, lower, upper in (('max_motion',1,2000),('growth',1,200),
                                  ('min_area',3,10000000),('miss_limit',1,100),
                                  ('iterations',1,10),('cleanup',0,5)):
            value = getattr(self, key)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError('Invalid ' + key)
        if not math.isfinite(self.area_ratio) or not 1.1 <= self.area_ratio <= 20:
            raise ValueError('Area ratio must be between 1.1 and 20.')

    @classmethod
    def from_dict(cls, values):
        return cls(**{key: value for key, value in values.items() if key in cls.__dataclass_fields__})

    def to_dict(self):
        return asdict(self)


@dataclass
class TrackResult:
    frame: int
    accepted: bool
    centroid: tuple | None
    confidence: float
    area: int
    reason: str
    mask: np.ndarray | None = None
    origin: tuple = (0, 0)
    components: dict | None = None

    def record(self):
        """Small, serializable diagnostic. Full masks stay out of saved jobs."""
        contours = []
        if self.mask is not None:
            found, _ = cv2.findContours(self.mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
            for c in sorted(found, key=cv2.contourArea, reverse=True)[:32]:
                c = cv2.approxPolyDP(c, 0.8, True).reshape(-1, 2)
                if len(c) > 256: c = c[::int(math.ceil(len(c) / 256))]
                contours.append((c + np.asarray(self.origin)).tolist())
        return dict(frame=self.frame, accepted=self.accepted, centroid=self.centroid,
                    confidence=float(self.confidence), area=int(self.area), reason=self.reason,
                    contours=contours, components=self.components or {})


class AdaptiveSilhouetteTracker:
    """Sequential, bounded, conservative tracker; one instance per direction.

    initialize(image, (x, y, width, height), seed, frame) establishes the object.
    step(image, frame) returns a measured centroid or a rejected observation.
    Images may be gray or RGB, uint8/uint16/float. Frames must be monotonic.
    No coordinate is generated for a rejected frame; rejected frames never
    update the appearance model. Reinitialize to restart after loss/occlusion.
    """
    def __init__(self, settings=None):
        self.settings = settings or TrackerSettings()
        self.ready = False
        self.misses = 0
        self.stopped = False
        self.direction = 0

    def _image(self, image, initial=False):
        image = np.asarray(image)
        if image.ndim not in (2, 3) or (image.ndim == 3 and image.shape[2] != 3):
            raise ValueError('Use a grayscale or three-channel RGB image.')
        if min(image.shape[:2]) < 12 or (image.dtype.kind in 'fc' and not np.all(np.isfinite(image))):
            raise ValueError('Image must be at least 12×12 and contain finite pixels.')
        if initial:
            self.shape = image.shape
            if image.dtype == np.uint8:
                self.low, self.high = 0., 255.
            else:
                self.low, self.high = map(float, np.percentile(image, (0.1, 99.9)))
                if self.high - self.low < 1e-6:
                    self.low, self.high = float(image.min()), float(image.max())
            if self.high - self.low < 1e-6: raise ValueError('The setup image has no usable contrast.')
        elif image.shape != self.shape:
            raise ValueError('Image dimensions/channels changed during tracking.')
        if image.dtype == np.uint8 and self.low == 0. and self.high == 255.:
            u8 = image
        else:
            u8 = np.clip((image.astype(np.float32)-self.low)*255./max(1e-6,self.high-self.low),0,255).astype(np.uint8)
        # Optical flow needs the gray frame; segmentation needs RGB only in
        # its small search crop. Avoid allocating full-frame RGB for mono Cines.
        u8 = np.ascontiguousarray(u8)
        return u8, u8 if u8.ndim == 2 else cv2.cvtColor(u8,cv2.COLOR_RGB2GRAY)

    @staticmethod
    def _hist(rgb, mask):
        # Coarse channel marginals tolerate rotation and changing outlines.
        hist = np.concatenate([np.histogram(rgb[:,:,c][mask],16,(0,256))[0] for c in range(3)]).astype(float)
        return hist / max(1, hist.sum())

    @staticmethod
    def _crop(shape, center, half_size):
        h,w=shape[:2];cx,cy=center;rx,ry=half_size
        return max(0,int(cx-rx)),max(0,int(cy-ry)),min(w,int(math.ceil(cx+rx+1))),min(h,int(math.ceil(cy+ry+1)))

    def _segment(self, rgb, gray, prior, seed, initial=False):
        s=self.settings
        if s.mode != 'adaptive':
            border=np.r_[gray[0,:],gray[-1,:],gray[:,0],gray[:,-1]]
            background=float(np.median(border))
            if initial:
                sx,sy=map(int,seed)
                foreground=float(np.median(gray[max(0,sy-1):sy+2,max(0,sx-1):sx+2]))
            else: foreground=self.foreground
            cutoff=background+s.threshold*(foreground-background)
            if abs(foreground-background)<4: return np.zeros(gray.shape,bool)
            mask=gray<cutoff if s.mode=='dark' else gray>cutoff
        else:
            labels=np.full(gray.shape,cv2.GC_PR_BGD,np.uint8)
            if initial:
                labels[prior]=cv2.GC_PR_FGD
                labels[~prior]=cv2.GC_BGD
            else:
                labels[prior]=cv2.GC_PR_FGD
                # Permit silhouette change around the propagated foreground.
                allowed=cv2.dilate(prior.astype(np.uint8),np.ones((2*s.growth+1,)*2,np.uint8))>0
                labels[~allowed]=cv2.GC_BGD
            sx,sy=map(int,seed)
            cv2.circle(labels,(sx,sy),2,cv2.GC_FGD,-1)
            labels[[0,-1],:]=cv2.GC_BGD;labels[:,[0,-1]]=cv2.GC_BGD
            bg=np.zeros((1,65),np.float64);fg=bg.copy()
            cv2.grabCut(rgb,labels,None,bg,fg,s.iterations,cv2.GC_INIT_WITH_MASK)
            mask=(labels==cv2.GC_FGD)|(labels==cv2.GC_PR_FGD)
        if s.cleanup:
            k=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(s.cleanup*2+1,)*2)
            mask=cv2.morphologyEx(mask.astype(np.uint8),cv2.MORPH_CLOSE,k)>0
        return mask

    def _candidates(self, mask, rgb, gray, origin):
        n,labels,stats,centroids=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
        candidates=[]
        for i in range(1,n):
            x,y,w,h,area=map(int,stats[i])
            if area<self.settings.min_area: continue
            component=labels==i
            touches=x<=1 or y<=1 or x+w>=mask.shape[1]-1 or y+h>=mask.shape[0]-1
            candidates.append(dict(mask=component,area=area,centroid=centroids[i]+origin,
                                   bounds=(x,y,w,h),touches=touches,
                                   hist=self._hist(rgb,component),foreground=float(np.median(gray[component]))))
        return candidates

    def initialize(self,image,roi,seed=None,frame=0):
        rgb,gray=self._image(image,True)
        if len(roi)!=4 or not np.all(np.isfinite(roi)): raise ValueError('Invalid setup rectangle.')
        x,y,w,h=map(int,roi)
        if w<9 or h<9 or x<0 or y<0 or x+w>gray.shape[1] or y+h>gray.shape[0]:
            raise ValueError('Place a setup box at least 9×9 pixels fully inside the image.')
        seed=np.asarray(seed if seed is not None else (x+w/2,y+h/2),float)
        if seed.shape!=(2,) or not np.all(np.isfinite(seed)) or not (x+2<=seed[0]<x+w-2 and y+2<=seed[1]<y+h-2):
            raise ValueError('Click inside the object, away from the setup-box border.')
        # A context ring outside the user box provides known background samples.
        ox,oy,ex,ey=self._crop(gray.shape,(x+w/2,y+h/2),(w/2+8,h/2+8))
        crop=rgb[oy:ey,ox:ex];g=gray[oy:ey,ox:ex]
        if crop.ndim == 2: crop=cv2.cvtColor(crop,cv2.COLOR_GRAY2RGB)
        prior=np.zeros(g.shape,bool);prior[y-oy:y+h-oy,x-ox:x+w-ox]=True
        local_seed=seed-(ox,oy)
        mask=self._segment(crop,g,prior,local_seed,True)
        candidates=self._candidates(mask,crop,g,np.asarray((ox,oy)))
        valid=[c for c in candidates if c['mask'][int(local_seed[1]),int(local_seed[0])] and not c['touches']]
        if not valid: raise ValueError('No complete foreground at the selected point. Adjust the box or segmentation mode.')
        candidate=valid[0]
        bx,by,bw,bh=candidate['bounds']
        # Setup should identify the whole object, not a shape cut by the box.
        if bx+ox<=x or by+oy<=y or bx+ox+bw>=x+w or by+oy+bh>=y+h:
            raise ValueError('The foreground reaches the setup-box edge. Enlarge the box to include the whole object.')
        self.frame=int(frame);self.last_attempt=int(frame);self.velocity=np.zeros(2)
        self.gray=gray;self.center=candidate['centroid'];self.area=float(candidate['area'])
        self.hist=candidate['hist'];self.foreground=candidate['foreground']
        self.mask=np.zeros(gray.shape,np.uint8);self.mask[oy:ey,ox:ex]=candidate['mask']
        self.bounds=(ox+bx,oy+by,bw,bh)
        self._feature_frame=None;self._feature_points=None
        self.ready=True;self.misses=0;self.stopped=False;self.direction=0
        return TrackResult(int(frame),True,tuple(map(float,self.center)),1.,candidate['area'],'Setup silhouette',candidate['mask'],(ox,oy))

    def _prediction(self, gray, frame):
        delta=frame-self.frame
        move=self.velocity*delta
        flow_quality=0.
        if self._feature_frame != self.frame:
            # Corner detection is masked to the foreground. Five context pixels
            # preserve its derivative, block and nonmaximum-suppression support.
            bx,by,bw,bh=self.bounds
            ox=max(0,bx-5);oy=max(0,by-5)
            ex=min(self.gray.shape[1],bx+bw+5);ey=min(self.gray.shape[0],by+bh+5)
            points=cv2.goodFeaturesToTrack(self.gray[oy:ey,ox:ex],80,.01,4,
                mask=self.mask[oy:ey,ox:ex],blockSize=5)
            if points is not None:points += np.array([ox,oy],np.float32)
            self._feature_frame=self.frame;self._feature_points=points
        points=self._feature_points
        if points is not None and len(points)>=4:
            nxt,status,_=cv2.calcOpticalFlowPyrLK(self.gray,gray,points,None,winSize=(21,21),maxLevel=3)
            if nxt is not None:
                back,ok,_=cv2.calcOpticalFlowPyrLK(gray,self.gray,nxt,None,winSize=(21,21),maxLevel=3)
                if back is not None:
                    error=np.linalg.norm(back-points,axis=2).ravel()
                    valid=(status.ravel()>0)&(ok.ravel()>0)&(error<1.5)&np.all(np.isfinite(nxt.reshape(-1,2)),axis=1)
                    if valid.sum()>=4:
                        shifts=(nxt-points).reshape(-1,2)[valid]
                        median=np.median(shifts,axis=0)
                        inliers=np.linalg.norm(shifts-median,axis=1)<max(3.,self.settings.max_motion*.2)
                        if inliers.sum()>=4 and np.linalg.norm(median)<=self.settings.max_motion*abs(delta):
                            move=np.median(shifts[inliers],axis=0)
                            flow_quality=float(inliers.sum()/len(points))
        limit=self.settings.max_motion*abs(delta)
        norm=np.linalg.norm(move)
        if norm>limit: move*=limit/norm
        return self.center+move,flow_quality

    def step(self,image,frame):
        if not self.ready: raise ValueError('Initialize the tracker before stepping.')
        frame=int(frame);delta=frame-self.frame;attempt_delta=frame-self.last_attempt
        if not attempt_delta or (self.direction and np.sign(attempt_delta)!=self.direction):
            raise ValueError('Frames must advance monotonically; use a separate tracker for reverse processing.')
        self.direction=int(np.sign(attempt_delta));self.last_attempt=frame
        if self.stopped: return TrackResult(frame,False,None,0.,0,'Stopped; reinitialize at a clear frame.')
        rgb,gray=self._image(image)
        predicted,flow=self._prediction(gray,frame)
        _,_,bw,bh=self.bounds
        radius=self.settings.max_motion*min(abs(delta),self.settings.miss_limit)
        half=(max((bw-1)/2,self.settings.growth)+radius,
              max((bh-1)/2,self.settings.growth)+radius)
        ox,oy,ex,ey=self._crop(gray.shape,predicted,half)
        crop=rgb[oy:ey,ox:ex];g=gray[oy:ey,ox:ex]
        if crop.ndim == 2: crop=cv2.cvtColor(crop,cv2.COLOR_GRAY2RGB)
        transform=np.array([[1.,0.,predicted[0]-self.center[0]-ox],[0.,1.,predicted[1]-self.center[1]-oy]],np.float32)
        prior=cv2.warpAffine(self.mask,transform,(ex-ox,ey-oy),flags=cv2.INTER_NEAREST)>0
        if min(g.shape)<9 or not np.any(prior):
            return self._reject(frame,'Object left the image.')
        interior=cv2.distanceTransform(prior.astype(np.uint8),cv2.DIST_L2,3)
        sy,sx=np.unravel_index(np.argmax(interior),interior.shape)
        try:
            mask=self._segment(crop,g,prior,(sx,sy))
        except cv2.error:
            return self._reject(frame,'Foreground segmentation failed.')
        ranked=[]
        for c in self._candidates(mask,crop,g,np.asarray((ox,oy))):
            ratio=c['area']/self.area
            distance=float(np.linalg.norm(c['centroid']-predicted))
            displacement=float(np.linalg.norm(c['centroid']-self.center))
            if c['touches'] or not 1/self.settings.area_ratio<=ratio<=self.settings.area_ratio: continue
            if displacement>self.settings.max_motion*abs(delta) or distance>radius: continue
            motion=max(0.,1.-distance/max(1.,radius))
            area=math.exp(-abs(math.log(ratio)))
            appearance=float(np.sqrt(c['hist']*self.hist).sum())
            overlap=float(np.count_nonzero(c['mask']&prior)/max(1,min(np.count_nonzero(prior),c['area'])))
            score=(.35*motion+.20*area+.30*appearance+.15*overlap)
            # Forced graph-cut seed alone must never become a plausible object.
            if appearance<.30 or overlap<.08: continue
            c.update(score=score,components=dict(motion=motion,area=area,appearance=appearance,overlap=overlap,flow=flow))
            ranked.append(c)
        ranked.sort(key=lambda c:c['score'],reverse=True)
        if not ranked: return self._reject(frame,'Lost, clipped, or implausible foreground.')
        best=ranked[0]
        if len(ranked)>1 and best['score']-ranked[1]['score']<.10:
            return self._reject(frame,'Ambiguous foreground candidates.',best,(ox,oy))
        if best['score']<self.settings.confidence:
            return self._reject(frame,'Below acceptance threshold.',best,(ox,oy))
        old_center=self.center.copy()
        self.center=best['centroid'];self.velocity=.5*self.velocity+.5*(self.center-old_center)/delta
        self.frame=frame;self.gray=gray;self.misses=0
        self.mask=np.zeros(gray.shape,np.uint8);self.mask[oy:ey,ox:ex]=best['mask']
        bx,by,bw,bh=best['bounds'];self.bounds=(ox+bx,oy+by,bw,bh)
        # Geometry follows every accepted silhouette; appearance updates are stricter.
        self.area=float(best['area'])
        if best['score']>=max(self.settings.confidence,self.settings.update_confidence):
            a=self.settings.adaptation
            self.hist=(1-a)*self.hist+a*best['hist']
            self.foreground=(1-a)*self.foreground+a*best['foreground']
        return TrackResult(frame,True,tuple(map(float,self.center)),best['score'],best['area'],'Accepted',best['mask'],(ox,oy),best['components'])

    def _reject(self,frame,reason,candidate=None,origin=(0,0)):
        self.misses+=1;self.stopped=self.misses>=self.settings.miss_limit
        c=candidate or {}
        return TrackResult(frame,False,None,c.get('score',0.),c.get('area',0),reason,c.get('mask'),origin,c.get('components'))
