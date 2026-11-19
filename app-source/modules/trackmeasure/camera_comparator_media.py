"""Independent comparator readers, identical preview/export rendering, and export."""
from collections import OrderedDict
from pathlib import Path
import math
import os
import subprocess
import tempfile
import threading

import cv2
import numpy as np
from camera_comparator import (PROFILES, SourceSpec, SamplingPlan, geometry, linear_frame,
    render_pair, validate_settings, finite, profile_for_model)
from viewer_media import open_media, VideoReader, fit_canvas
from video_export import ExportCancelled, video_frame


class ComparisonSession:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        self.reader = open_media(path)
        self.cache = OrderedDict()
        self.metadata = self.reader.metadata
        self.ordinary = isinstance(self.reader,VideoReader)
        try:
            self.timestamps = None if self.ordinary else self.reader.relative_capture_timestamps()
            if self.timestamps is not None and (not np.all(np.isfinite(self.timestamps)) or np.any(np.diff(self.timestamps)<=0)):
                raise ValueError('Cine timestamps are non-monotonic. A trustworthy capture comparison is unavailable.')
        except Exception:
            self.close()
            raise

    def close(self):
        self.cache.clear()
        self.reader.close()

    def source_defaults(self):
        md=self.metadata
        key=profile_for_model(getattr(md,'CameraModel',''))
        return dict(width=int(md.ImWidth),height=int(md.ImHeight),profile_id=key,
            pitch_um=PROFILES[key]['native_pitch_um'] if key else 0.,binning=0,
            capture_fps=0. if self.ordinary else float(md.FrameRate or 0),
            exposure_us=0. if self.ordinary else float(getattr(md,'ExposureTime',0) or 0),center_x_um=0.,center_y_um=0.)

    def build(self, settings):
        md=self.metadata
        settings,source,profile,mode=validate_settings(settings,int(md.ImageCount),int(md.ImWidth),int(md.ImHeight))
        first,last=settings['first'],settings['last']
        decimation=1 if self.ordinary else max(1,float(getattr(md,'fDecimation',1) or 1))
        saved_fps=source.capture_fps/decimation
        times=(np.arange(first,last+1,dtype=np.float64)/saved_fps if self.timestamps is None else self.timestamps[first:last+1])
        plan=SamplingPlan(times,settings['target_fps'],settings['phase'],saved_fps)
        geo=geometry(source,profile,mode,settings['aim_x'],settings['aim_y'])
        notes=['Same optics and position. Source exposure/blur retained; target noise, sensitivity and extra detail are not simulated.']
        if self.ordinary: notes.append('Manual acquisition metadata; ordinary video transfer is approximated as sRGB.')
        if self.timestamps is None: notes.append('Capture timestamps unavailable: cadence is assumed from source FPS and saved decimation.')
        elif abs(plan.available_fps-saved_fps)/saved_fps>.0005:
            notes.append(f'Stored cadence is {plan.available_fps:,.3f} fps; source metadata predicts {saved_fps:,.3f}. Actual timestamps control sampling.')
        if decimation!=1: notes.append(f'Source was saved with {decimation:g}× frame decimation.')
        if plan.irregular: notes.append('Irregular source intervals: uniform capture timing cannot be reproduced exactly.')
        if plan.approximate: notes.append(f'Nearest-frame timing approximation: maximum offset {plan.max_error*1e6:.3f} µs.')
        if plan.missing: notes.append(f'{plan.missing:,} requested captures unavailable; no substitute captures were invented.')
        if geo.available_fraction<1-1e-6: notes.append('Hatched target pixels extend outside recorded footage.')
        if geo.step<1-1e-6: notes.append('Target pixels are smaller than source samples; additional target detail cannot be recovered.')
        if source.binning==2: notes.append('Source is already binned; its lost spatial detail cannot be recovered.')
        if geo.mono: notes.append('2×2 binned target output is monochrome; spatial averaging approximates hardware binning.')
        if source.center_x_um==source.center_y_um==0:
            notes.append('Active image centers aligned by assumption; source sensor crop origin is not verified.')
        if settings['aim_x'] or settings['aim_y']: notes.append('Offset represents hypothetical camera re-aiming, not a verified hardware ROI position.')
        exposure=source.exposure_us
        minimum=profile['variants'][settings['variant']]['exposure_us']
        if not exposure: notes.append('Source exposure unknown: target shutter compatibility is unverified.')
        elif exposure<minimum-1e-8 or exposure>1e6/settings['target_fps']+1e-8:
            notes.append(f'EXPOSURE INCOMPATIBLE: source {exposure:g} µs; target minimum {minimum:g} µs and frame period {1e6/settings["target_fps"]:g} µs. Blur is unchanged.')
        else: notes.append('Exposure is within published minimum/frame-period bounds; exact hardware timing margins remain unverified.')
        if not self.ordinary and getattr(md,'EDR',0): notes.append('Source uses EDR; its exposure-dependent image effects remain in the preview.')
        return Comparison(self,settings,source,profile,mode,plan,geo,notes)

    def frame(self,index):
        if index not in self.cache:
            self.cache[index]=linear_frame(self.reader.get_img(index),self.metadata,self.ordinary)
            while len(self.cache)>3: self.cache.popitem(last=False)
        else: self.cache.move_to_end(index)
        return self.cache[index]


class Comparison:
    def __init__(self,session,settings,source,profile,mode,plan,geo,notes):
        self.session,self.settings,self.source,self.profile,self.mode,self.plan,self.geo,self.notes=session,settings,source,profile,mode,plan,geo,notes

    def render(self,index):
        original,target,ended=self.plan.pair(index,self.settings['playback_mode'])
        first=self.settings['first']
        a=self.session.frame(first+original)
        b=self.session.frame(first+target) if target is not None else None
        left,right=render_pair(a,b,self.geo,self.settings['gamma'])
        return left,right,original,target,ended

    def frame_count(self,layout):
        return len(self.plan.indices) if layout=='target' and self.settings['playback_mode']=='same_fps' else len(self.plan.times)

    def export_frame(self,index,layout):
        left,right,original,target,ended=self.render(index)
        fps=self.settings['playback_fps']
        if self.settings['playback_mode']=='event_sync':
            slowdown=f'Event replay {self.plan.available_fps/fps:,.1f}x slow; target frames held between captures'
        else:
            slowdown=f'Nominal target {self.settings["target_fps"]/fps:,.1f}x slow'+(' | FINISHED - last capture held' if ended else '')
        target_time='No capture yet' if target is None else f't={self.plan.times[target]*1e6:,.2f} us'
        detail=f'{self.profile["name"]} ({self.profile["variants"][self.settings["variant"]]["label"]}) | {self.settings["target_fps"]:,.3f} capture fps | {self.mode["width"]}x{self.mode["height"]} | {self.mode["binning"]}x{self.mode["binning"]} sampling | {target_time}'
        # Explanatory band is outside the camera image and never changes its FOV.
        if layout=='target':
            width=max(640,right.shape[1]); height=max(180,right.shape[0])
            canvas=fit_canvas(right,width,height) if width==right.shape[1] else _pad_native(right,width,height)
        elif layout=='comparison':
            width=2*max(320,min(1280,self.source.width))
            height=max(240,min(960,self.source.height))
            canvas=np.concatenate([fit_canvas(left,width//2,height),fit_canvas(right,width//2,height)],axis=1)
        else: raise ValueError('Choose target-only or side-by-side export.')
        caution='Simulated camera comparison | Source exposure/blur retained'
        if any('EXPOSURE INCOMPATIBLE' in n for n in self.notes): caution+=' | EXPOSURE INCOMPATIBLE'
        if self.plan.missing: caution+=' | Missing captures'
        lines=[caution,detail,f'{fps:g} fps playback | '+slowdown]
        if layout=='comparison':
            lines.append(f'Left: source {self.source.capture_fps:,.3f} capture fps, t={self.plan.times[original]*1e6:,.2f} us | Right: simulated target')
        if self.geo.available_fraction<1-1e-6: lines.append('Hatched areas: Outside recorded footage')
        banner=np.full((24*len(lines)+12,canvas.shape[1],3),25,np.uint8)
        for i,line in enumerate(lines):
            scale=min(.48,(canvas.shape[1]-16)/max(1,cv2.getTextSize(line,cv2.FONT_HERSHEY_SIMPLEX,1,1)[0][0]))
            cv2.putText(banner,line,(8,23+i*24),cv2.FONT_HERSHEY_SIMPLEX,scale,(230,230,230),1,cv2.LINE_AA)
        return video_frame(np.concatenate([banner,canvas],axis=0))


def _pad_native(image,width,height):
    left=(width-image.shape[1])//2;top=(height-image.shape[0])//2
    return cv2.copyMakeBorder(image,top,height-image.shape[0]-top,left,width-image.shape[1]-left,cv2.BORDER_CONSTANT,value=(0,0,0))


def export_comparison(source,destination,settings,layout='comparison',cancel=None,progress=None):
    import imageio_ffmpeg
    cancel=cancel or threading.Event()
    destination=Path(destination)
    if destination.resolve()==Path(source).resolve(): raise ValueError('Do not overwrite source footage.')
    extension=destination.suffix.lower()
    if extension not in ('.mp4','.mov','.avi'): raise ValueError('Choose an MP4, MOV or AVI file.')
    session=None;process=None;temporary=None
    try:
        if cancel.is_set(): raise ExportCancelled()
        session=ComparisonSession(source)
        comparison=session.build(settings)
        first=comparison.export_frame(0,layout)
        height,width=first.shape[:2]
        fd,temporary=tempfile.mkstemp(prefix='.pca-compare-export-',suffix=extension,dir=destination.parent)
        os.close(fd)
        args=[imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}','-framerate',str(settings['playback_fps']),'-i','pipe:0','-an']
        args+=['-c:v','mjpeg','-q:v','2','-pix_fmt','yuvj420p'] if extension=='.avi' else ['-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart']
        args.append(temporary)
        with tempfile.TemporaryFile() as errors:
            process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=errors)
            try:
                count=comparison.frame_count(layout)
                for i in range(count):
                    if cancel.is_set(): raise ExportCancelled()
                    frame=first if i==0 else comparison.export_frame(i,layout)
                    process.stdin.write(frame.tobytes())
                    if progress: progress(i+1,count)
                process.stdin.close()
                while process.poll() is None:
                    if cancel.wait(.05): raise ExportCancelled()
                if process.returncode: raise RuntimeError('Encoder failed.')
            except (BrokenPipeError,RuntimeError) as error:
                errors.seek(0)
                raise RuntimeError('Comparison export failed: '+(errors.read().decode(errors='replace')[-2000:] or str(error))) from error
        if cancel.is_set(): raise ExportCancelled()
        os.replace(temporary,destination);temporary=None
        return str(destination)
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill();process.wait()
            if process.stdin and not process.stdin.closed:
                try: process.stdin.close()
                except BrokenPipeError: pass
        if session is not None: session.close()
        if temporary is not None: Path(temporary).unlink(missing_ok=True)
