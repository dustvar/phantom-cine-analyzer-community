"""Per-video appearance, animated crops, and timed text shared by preview/export."""
from bisect import bisect_right
from copy import deepcopy
import math
import cv2
import numpy as np
from viewer_timeline import (Timeline, source_count, total_frames, copy_index, copy_options, copy_count, copy_offset,
                             default_zoom, ease_value, overlay_text, camera_overlay_text)
from viewer_overlay import apply_fps_overlay
from viewer_track_fade import draw_track_fade
from image_orientation import rotate_image


def default_image_settings(metadata):
    return dict(image_brightness=getattr(metadata, 'Bright', 0),
                image_contrast=getattr(metadata, 'Contrast', 1),
                image_gamma=getattr(metadata, 'Gamma', 1), toggle_ftone=True, raw_enabled=False)


def _number(value, lo, hi):
    return type(value) in (int,float) and math.isfinite(value) and lo <= value <= hi


def validate_copy_appearance(options):
    grade = options.get('grade')
    if not isinstance(grade,dict) or not all(_number(grade.get(k),lo,hi) for k,lo,hi in [('black',0,1),('white',0,1),('gamma',.1,5)]) or grade['white']-grade['black'] < .00099:
        raise ValueError('Invalid video levels')
    curve = grade.get('curve')
    if not isinstance(curve,list) or len(curve)!=5 or not all(_number(v,0,1) for v in curve) or curve != sorted(curve) or curve[0]!=0 or curve[-1]!=1 or type(grade.get('enabled')) is not bool:
        raise ValueError('Invalid video tone curve')
    settings = options.get('image_settings')
    if settings is not None and (not isinstance(settings,dict) or not all(_number(settings.get(k),lo,hi) for k,lo,hi in [('image_brightness',-65535,65535),('image_contrast',0,100),('image_gamma',.01,100)]) or not all(type(settings.get(k)) is bool for k in ('toggle_ftone','raw_enabled'))):
        raise ValueError('Invalid video image settings')


def validate_texts(texts):
    if not isinstance(texts,list) or len(texts)>200:
        raise ValueError('Invalid custom text list')
    ids = set()
    for text in texts:
        if not isinstance(text,dict) or not isinstance(text.get('id'),str) or not 1<=len(text['id'])<=100 or text['id'] in ids:
            raise ValueError('Invalid text identifier')
        ids.add(text['id'])
        if not isinstance(text.get('text'),str) or len(text['text'])>2000:
            raise ValueError('Custom text must have at most 2,000 characters')
        for key,lo,hi in [('start',0,1e9),('duration',.01,1e9),('x',0,1),('y',0,1),('size',1,15)]:
            if not _number(text.get(key),lo,hi): raise ValueError('Invalid custom text '+key)
        for key in ('enabled','background','pinned'):
            if type(text.get(key)) is not bool: raise ValueError('Invalid text option')
        color=text.get('color','')
        if not isinstance(color,str) or len(color)!=7 or color[0]!='#' or any(c not in '0123456789abcdefABCDEF' for c in color[1:]):
            raise ValueError('Invalid text color')


def crop_rect(zoom):
    size=1/max(1,float(zoom['factor']))
    x=min(1-size,max(0,float(zoom['cx'])-size/2))
    y=min(1-size,max(0,float(zoom['cy'])-size/2))
    return np.array([x,y,size,size],dtype=float)


class VisualTimeline:
    """Zoom events stay continuous across a plain split and timed source trims."""
    def __init__(self, edit):
        self.edit=edit
        self.plan=Timeline(edit,0,total_frames(edit)-1)
        self.events=[]; self.starts=[]
        candidates=[]
        for index, section in enumerate(edit['sections']):
            if not section['keep'] or section.get('zoom',default_zoom()).get('inherit',False): continue
            ci=copy_index(edit,section['start'])
            count=copy_count(edit,ci);offset=copy_offset(edit,ci)
            lo,hi=edit['copies'][ci].get('trim',[0,count-1])
            if section['end'] < offset+lo or section['start'] > offset+hi: continue
            candidates.append((self.plan.time_for_source(section['start']),section['zoom']))
        previous=np.array([0.,0.,1.,1.])
        for i,(start,zoom) in enumerate(candidates):
            end=candidates[i+1][0] if i+1<len(candidates) else self.plan.duration
            target=crop_rect(zoom)
            self.events.append((start,min(float(zoom['duration']),max(0,end-start)),previous,target,zoom['curve']))
            self.starts.append(start);previous=target
        self.copy_starts=[self.plan.time_for_source(copy_offset(edit,i)) for i in range(len(edit['copies']))]
        self.fade_starts = {}
        for ci in range(len(edit['copies'])):
            fade = copy_options(edit, ci).get('track_fade')
            if fade and fade['enabled']:
                self.fade_starts[ci] = self.plan.time_for_source(copy_offset(edit, ci) + fade['start'])


    def crop_at(self, seconds):
        index=bisect_right(self.starts,seconds)-1
        if index<0: return np.array([0.,0.,1.,1.])
        start,duration,previous,target,curve=self.events[index]
        phase=1 if duration==0 else ease_value(curve,(seconds-start)/duration)
        return previous+(target-previous)*phase

    def offset_for(self, timeline):
        return self.plan.time_for_source(timeline.sample(0)[0]) if timeline.parts else 0


def zoom_image(image, crop):
    if np.allclose(crop,[0,0,1,1],rtol=0,atol=1e-12): return image
    height,width=image.shape[:2];x,y,w,h=crop
    matrix=np.array([[w,0,x*width+(w-1)/2],[0,h,y*height+(h-1)/2]],dtype=float)
    return cv2.warpAffine(image,matrix,(width,height),flags=cv2.INTER_LINEAR|cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)


def apply_effects(image, edit, metadata, visual, seconds, frame, rate, show_crop=False, tracks=None):
    """Draw footage-anchored text before cropping and screen-pinned text after."""
    ci=copy_index(edit,frame); options=copy_options(edit,ci)
    rotation = options.get('rotation', 0)
    source_shape = image.shape[:2]
    image = rotate_image(image, rotation)
    crop=np.array([0.,0.,1.,1.]) if show_crop else visual.crop_at(seconds)
    if tracks and ci in visual.fade_starts:
        local_frame = frame - copy_offset(edit, ci)
        image = draw_track_fade(image, tracks, options['track_fade'], local_frame,
                                seconds - visual.fade_starts[ci], rotation=rotation, source_shape=source_shape)
    camera,playback=options['camera_overlay'],options['overlay']
    overlays=[('camera',camera,camera_overlay_text(camera,metadata)),
              ('playback',playback,overlay_text(playback,rate,edit['output_fps'],
                  getattr(metadata,'FrameRate',None),getattr(metadata,'fDecimation',1)))]
    local_time=seconds-visual.copy_starts[ci]
    for annotation in options.get('texts',[]):
        if annotation['start']-1e-8 <= local_time < annotation['start']+annotation['duration']-1e-8:
            overlays.append(('text:'+annotation['id'],dict(annotation,uniform=True),annotation['text']))
    height,width=image.shape[:2]; boxes={}; source_rects={}
    for pinned in (False,True):
        if pinned: image=zoom_image(image,crop)
        for key,style,text in overlays:
            if style.get('pinned',True)!=pinned: continue
            image,rect=apply_fps_overlay(image,text,style)
            if rect is None: continue
            source_rects[key]=rect
            x,y,w,h=rect
            if not pinned:
                x,y=(x-crop[0]*width)/crop[2],(y-crop[1]*height)/crop[3]
                w,h=w/crop[2],h/crop[3]
            left,top=max(0,x),max(0,y); right,bottom=min(width,x+w),min(height,y+h)
            if right>left and bottom>top: boxes[key]=(left,top,right-left,bottom-top)
    return image,boxes,source_rects,crop
