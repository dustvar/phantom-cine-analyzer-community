"""Camera geometry and capture sampling; independent of Viewer edit state.

No camera noise/sensitivity model is implied. Pixel footprints are integrated in
linear space; timestamps describe captures, never presentation-frame repeats.
"""
from dataclasses import dataclass, asdict
from pathlib import Path
from functools import lru_cache
import json
import math
import os
import tempfile

import cv2
import numpy as np
from scipy.sparse import csr_matrix
from viewer_project import source_identity

CATALOG = json.loads((Path(__file__).parent/'comparator_data'/'profiles.json').read_text())
PROFILES = {p['id']: p for p in CATALOG['profiles']}


def finite(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be between {low:g} and {high:g}.')
    return float(value)


def profile_for_model(model):
    normalized = ''.join(c for c in str(model).upper() if c.isalnum())
    return next((key for key in PROFILES if key in normalized), '')


def available_modes(profile_id, variant='standard'):
    profile = PROFILES[profile_id]
    option = profile['variants'][variant]
    features = option.get('features', [])
    modes = []
    for entry in profile['modes']:
        if entry['requires'] and entry['requires'] not in features:
            continue
        mode = dict(entry)
        mode['max_fps'] = min(mode['max_fps'], option.get('max_fps', float('inf')))
        modes.append(mode)
    # FAST supersedes the ordinary ceiling for the same physical mode.
    grouped = {}
    for mode in modes:
        key = mode['width'], mode['height'], mode['binning']
        if key not in grouped or grouped[key]['max_fps'] < mode['max_fps']:
            grouped[key] = mode
    return list(grouped.values())


@dataclass(frozen=True)
class SourceSpec:
    width: int
    height: int
    profile_id: str
    pitch_um: float
    binning: int
    capture_fps: float
    exposure_us: float = 0
    # Recorded image center relative to the optical axis, in native-sensor µm.
    center_x_um: float = 0
    center_y_um: float = 0

    @property
    def effective_pitch(self):
        return self.pitch_um * self.binning

    def validate(self):
        for value in (self.width, self.height):
            if type(value) is not int or not 1 <= value <= 32768:
                raise ValueError('Invalid recorded image dimensions.')
        if type(self.binning) is not int or self.binning not in (1, 2):
            raise ValueError('Confirm source binning: unbinned or 2×2. Dimensions alone cannot identify it.')
        finite(self.pitch_um, 'Source native pixel pitch', .1, 200)
        finite(self.capture_fps, 'Source acquisition FPS', .01, 10_000_000)
        finite(self.exposure_us, 'Source exposure', 0, 100_000_000)
        finite(self.center_x_um, 'Source horizontal offset', -1e6, 1e6)
        finite(self.center_y_um, 'Source vertical offset', -1e6, 1e6)
        if self.profile_id:
            if self.profile_id not in PROFILES:
                raise ValueError('Unknown source camera profile.')
            profile = PROFILES[self.profile_id]
            if not math.isclose(self.pitch_um, profile['native_pitch_um'], abs_tol=1e-6):
                raise ValueError('Use Custom source geometry to override the profile pixel pitch.')
            for pixels, size, offset in zip((self.width,self.height), profile['active_pixels'], (self.center_x_um,self.center_y_um)):
                if pixels*self.effective_pitch + 2*abs(offset) > size*self.pitch_um + 1e-6:
                    raise ValueError('Source dimensions/binning/offset exceed the selected camera active array. Check source settings.')
        return self


@dataclass(frozen=True)
class Geometry:
    width: int
    height: int
    step: float
    left: float
    top: float
    source_width: int
    source_height: int
    mono: bool

    @property
    def right(self): return self.left + self.width*self.step
    @property
    def bottom(self): return self.top + self.height*self.step
    @property
    def intersection_area(self):
        return max(0, min(self.right,self.source_width)-max(self.left,0))*max(0,min(self.bottom,self.source_height)-max(self.top,0))
    @property
    def retained_fraction(self):
        return self.intersection_area/(self.source_width*self.source_height)
    @property
    def available_fraction(self):
        return self.intersection_area/(self.width*self.height*self.step**2)


def geometry(source, profile, mode, aim_x=0., aim_y=0.):
    """Aim offsets are hypothetical re-aiming measured in recorded source pixels."""
    source.validate()
    step = profile['native_pitch_um']*mode['binning']/source.effective_pitch
    left = (source.width-mode['width']*step)/2-source.center_x_um/source.effective_pitch+aim_x
    top = (source.height-mode['height']*step)/2-source.center_y_um/source.effective_pitch+aim_y
    return Geometry(mode['width'],mode['height'],step,left,top,source.width,source.height,mode['binning']==2)


def capture_interval(times, fallback_fps=None):
    """Average normal intervals to suppress TIME64 quantization, excluding gaps."""
    delta = np.diff(times)
    if not len(delta):
        return 1/finite(fallback_fps, 'Available FPS', .01, 10_000_000)
    median = float(np.median(delta))
    normal = delta[np.abs(delta-median) <= max(1e-9,median*.01)]
    return float(np.mean(normal)) if len(normal) else median


class SamplingPlan:
    """Nearest recorded captures at an evenly spaced requested cadence.

    Missing/irregular recordings can leave holes: never duplicate a capture to
    pretend it was recorded twice. Preserve actual selected times for sync view.
    """
    def __init__(self, times, target_fps, phase=0., fallback_fps=None):
        times = np.asarray(times, dtype=np.float64)
        if times.ndim != 1 or not len(times) or not np.all(np.isfinite(times)) or np.any(np.diff(times)<=0):
            raise ValueError('Capture timestamps must be finite and strictly increasing.')
        self.times = times-times[0]
        finite(target_fps, 'Target acquisition FPS', .01, 10_000_000)
        finite(phase, 'Sampling offset', 0, .999999)
        delta = np.diff(self.times)
        self.interval = capture_interval(self.times, fallback_fps or target_fps)
        self.available_fps = 1/self.interval
        # TIME64 quantization can shift median interval by ~0.002% at 100k fps.
        if target_fps > self.available_fps*(1+5e-5):
            raise ValueError(f'Target FPS exceeds the available source cadence ({self.available_fps:,.3f} fps).')
        self.target_fps = target_fps
        self.irregular = bool(len(delta) and np.max(np.abs(delta-self.interval)) > max(1e-9,self.interval*.01))
        self.duration = self.times[-1]+self.interval
        count = max(0, int(math.ceil(self.duration*target_fps-phase-1e-9)))
        if count <= len(times)+1:
            ordinals = np.arange(count,dtype=np.float64)
        else:
            # Large gaps must not allocate an array for every absent capture.
            nearest = self.times*target_fps-phase
            ordinals = np.unique(np.concatenate((np.floor(nearest),np.ceil(nearest))))
            ordinals = ordinals[(ordinals>=0)&(ordinals<count)]
        requested = (ordinals+phase)/target_fps
        right = np.searchsorted(self.times, requested).clip(0,len(times)-1)
        left = (right-1).clip(0,len(times)-1)
        indices = np.where(np.abs(self.times[right]-requested)<np.abs(self.times[left]-requested),right,left)
        errors = np.abs(self.times[indices]-requested)
        keep = errors <= self.interval/2+1e-9
        candidates = np.flatnonzero(keep)
        # Deduplicate after invalid candidates have been discarded.
        candidates = candidates[np.r_[True,np.diff(indices[candidates])>0]] if len(candidates) else candidates
        self.indices = indices[candidates]
        self.requested = requested[candidates]
        self.selected_times = self.times[self.indices]
        self.missing = int(count-len(self.indices))
        self.max_error = float(errors[candidates].max()) if len(candidates) else 0.
        self.approximate = self.max_error > max(1e-9,self.interval*.0001)
        if not len(self.indices):
            raise ValueError('No source captures match this range and sampling offset.')

    def pair(self, output_index, playback_mode):
        if not 0 <= output_index < len(self.times):
            raise IndexError(output_index)
        source_index = output_index
        if playback_mode == 'same_fps':
            target_ordinal = min(output_index,len(self.indices)-1)
            ended = output_index >= len(self.indices)
        elif playback_mode == 'event_sync':
            target_ordinal = int(np.searchsorted(self.selected_times,self.times[source_index]+1e-12,side='right')-1)
            ended = False
        else:
            raise ValueError('Unknown playback mode.')
        target_index = int(self.indices[target_ordinal]) if target_ordinal >= 0 else None
        return source_index,target_index,ended


@lru_cache(maxsize=32)
def _area_weights(source_size, output_size, start, step):
    """Exact overlap of each target pixel footprint with source pixel cells."""
    values, columns, row_ptr = [], [], [0]
    for row in range(output_size):
        low,high = start+row*step,start+(row+1)*step
        for column in range(max(0,math.floor(low)), min(source_size,math.ceil(high))):
            weight = max(0.,min(high,column+1)-max(low,column))/step
            if weight > 1e-12:
                columns.append(column); values.append(weight)
        row_ptr.append(len(values))
    return csr_matrix((np.asarray(values,dtype=np.float32),columns,row_ptr),shape=(output_size,source_size))


def integrate_pixels(image, geo):
    """Area average; normalization preserves scene brightness, not hardware SNR."""
    if image.shape[:2] != (geo.source_height,geo.source_width):
        raise ValueError('Decoded frame dimensions changed.')
    wx = _area_weights(geo.source_width,geo.width,geo.left,geo.step)
    wy = _area_weights(geo.source_height,geo.height,geo.top,geo.step)
    image = np.asarray(image,dtype=np.float32)
    if geo.mono and image.ndim == 3:
        image = image @ np.array([.2126,.7152,.0722],np.float32)
    def channel(data): return wy @ (wx @ data.T).T
    result = channel(image) if image.ndim == 2 else np.stack([channel(image[:,:,i]) for i in range(image.shape[2])],axis=-1)
    x = geo.left+np.arange(geo.width)*geo.step
    y = geo.top+np.arange(geo.height)*geo.step
    valid = ((y>=-1e-6)&(y+geo.step<=geo.source_height+1e-6))[:,None]&((x>=-1e-6)&(x+geo.step<=geo.source_width+1e-6))[None,:]
    return result,valid


def linear_frame(raw, metadata, ordinary_video=False):
    if ordinary_video:
        # Ordinary RGB video is treated as sRGB; exact transfer may be unknown.
        rgb = raw.astype(np.float32)/255
        return np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
    black = float(getattr(metadata,'BlackLevel',0) or 0)
    white = float(getattr(metadata,'WhiteLevel',0) or ((1<<metadata.RealBPP)-1))
    if white<=black: raise ValueError('Invalid source black/white levels.')
    image = np.clip((raw.astype(np.float32)-black)/(white-black),0,1)
    cfa = str(metadata.CFA).lower()
    if image.ndim==2 and cfa not in ('mono','monochrome'):
        codes={'gb/rg':cv2.COLOR_BayerGR2RGB,'rg/gb':cv2.COLOR_BayerBG2RGB,'gr/gb':cv2.COLOR_BayerGB2RGB,'bg/gr':cv2.COLOR_BayerRG2RGB}
        if cfa not in codes: raise ValueError('Unsupported source color filter array for linear comparison.')
        image = cv2.cvtColor(np.rint(image*65535).astype(np.uint16),codes[cfa]).astype(np.float32)/65535
        gains = getattr(metadata,'WBGain',None)
        if gains is not None and len(gains)>=2 and all(np.isfinite(g) and g>0 for g in gains[:2]):
            image *= np.array([gains[0],1.,gains[1]],np.float32)
    return image


def display_frame(linear, gamma=1.):
    finite(gamma,'Display gamma',.1,5)
    pixels = np.rint(np.clip(linear,0,1)**(1/gamma)*255).astype(np.uint8)
    return cv2.cvtColor(pixels,cv2.COLOR_GRAY2RGB) if pixels.ndim==2 else pixels


def hatch_frame(width, height, label='Outside recorded footage'):
    yy,xx=np.indices((height,width))
    grey=np.where(((xx+yy)//12)%2,48,68).astype(np.uint8)
    frame=np.repeat(grey[:,:,None],3,axis=2)
    if width>=180 and height>=32:
        cv2.putText(frame,label,(8,max(20,height//2)),cv2.FONT_HERSHEY_SIMPLEX,min(.5,width/600), (215,215,215),1,cv2.LINE_AA)
    return frame


def render_pair(source_image,target_image,geo,gamma=1.):
    original=display_frame(source_image,gamma)
    yy,xx=np.ogrid[:geo.source_height,:geo.source_width]
    included=(xx+.5>=geo.left)&(xx+.5<geo.right)&(yy+.5>=geo.top)&(yy+.5<geo.bottom)
    masked=original.copy()
    masked[~included]=np.rint(masked[~included]*.32+40*.68).astype(np.uint8)
    cv2.rectangle(masked,(round(geo.left),round(geo.top)),(round(geo.right)-1,round(geo.bottom)-1),(80,210,240),max(1,geo.source_width//600))
    if target_image is None:
        return masked,hatch_frame(geo.width,geo.height,'Before first capture')
    sampled,valid=integrate_pixels(target_image,geo)
    target=display_frame(sampled,gamma)
    missing=hatch_frame(geo.width,geo.height)
    target[~valid]=missing[~valid]
    return masked,target


def default_settings(count):
    return dict(target='T3610',variant='standard',mode='1280x320-b1',target_fps=30000.,playback_fps=30.,playback_mode='same_fps',phase=0.,first=0,last=max(0,count-1),aim_x=0.,aim_y=0.,gamma=1.,source=None,feature_pixels=0.)


def validate_settings(settings, count, width, height):
    if not isinstance(settings,dict): raise ValueError('Invalid comparison settings.')
    result=default_settings(count)
    result.update({k:v for k,v in settings.items() if k in result})
    source=SourceSpec(**result['source']).validate()
    if (source.width,source.height)!=(width,height): raise ValueError('Source dimensions do not match this recording.')
    result['source']=asdict(source)
    if result['target'] not in PROFILES: raise ValueError('Unknown target camera.')
    p=PROFILES[result['target']]
    if result['variant'] not in p['variants']: raise ValueError('Unknown target camera option.')
    mode=next((m for m in available_modes(result['target'],result['variant']) if m['id']==result['mode']),None)
    if mode is None: raise ValueError('This recording mode is unavailable with the selected options.')
    for key in ('first','last'):
        if type(result[key]) is not int: raise ValueError('Clip bounds must be frame indices.')
    if not 0<=result['first']<=result['last']<count: raise ValueError('Comparison range is outside the source.')
    finite(result['target_fps'],'Target acquisition FPS',p['minimum_fps'],mode['max_fps'])
    if result['target_fps']>source.capture_fps*(1+5e-5): raise ValueError('Target FPS exceeds source acquisition FPS.')
    finite(result['playback_fps'],'Playback FPS',1,120)
    finite(result['phase'],'Sampling offset',0,.999999)
    finite(result['aim_x'],'Horizontal re-aiming',-width,width)
    finite(result['aim_y'],'Vertical re-aiming',-height,height)
    finite(result['gamma'],'Display gamma',.1,5)
    finite(result['feature_pixels'],'Feature size',0,32768)
    if result['playback_mode'] not in ('same_fps','event_sync'): raise ValueError('Invalid playback mode.')
    return result,source,p,mode


def save_comparison(path, source_path, settings, profile):
    if Path(path).resolve()==Path(source_path).resolve(): raise ValueError('Do not overwrite the source recording.')
    payload=dict(format='pca-camera-comparison',version=1,source=source_identity(source_path),source_name=Path(source_path).name,settings=settings,profile_snapshot=profile,source_profile_snapshot=PROFILES.get(settings['source']['profile_id']))
    destination=Path(path)
    fd,temporary=tempfile.mkstemp(prefix='.pca-compare-',suffix='.json',dir=destination.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as stream: json.dump(payload,stream,indent=2,allow_nan=False)
        os.replace(temporary,destination)
    finally: Path(temporary).unlink(missing_ok=True)


def load_comparison(path, source_path, count, width, height):
    if Path(path).stat().st_size>1_000_000: raise ValueError('This comparison preset is too large.')
    payload=json.loads(Path(path).read_text())
    if not isinstance(payload,dict) or payload.get('format')!='pca-camera-comparison' or payload.get('version')!=1:
        raise ValueError('Not a supported PCA camera comparison preset.')
    if payload.get('source')!=source_identity(source_path): raise ValueError('This preset belongs to a different source recording.')
    settings,_,profile,_=validate_settings(payload.get('settings'),count,width,height)
    if payload.get('profile_snapshot') != profile or payload.get('source_profile_snapshot') != PROFILES.get(settings['source']['profile_id']):
        raise ValueError('Camera specifications have changed since this preset was saved. Recreate the comparison using the current verified profile.')
    return settings
