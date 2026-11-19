"""Trigger-relative synchronization and non-destructive, native-pixel overlays."""
import math
import numpy as np
from camera_comparator import display_frame
from camera_comparator_media import ComparisonSession


MAX_OVERLAY_PLAYBACK_FPS = 1000


def default_overlay():
    return dict(paths=['', ''], opacity=[50, 50], offsets=[0, 0],
                raw_t0=[True, True], t0_frames=[0, 0], x=0, y=0,
                mode='Blend', playback_fps=30., position=0)


def trigger_times(session):
    """Seconds from that recording's trigger, never from another run's epoch."""
    md = session.metadata
    fps = float(md.FrameRate) / max(1, float(getattr(md, 'fDecimation', 1) or 1))
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('A valid recorded frame rate is needed for synchronization.')
    if not session.ordinary:
        times = session.reader.capture_timestamps_from_trigger()
        if times is not None:
            if not np.all(np.isfinite(times)) or np.any(np.diff(times) <= 0):
                raise ValueError('Capture timestamps must increase for synchronization.')
            return times, 'Cine capture timestamps relative to trigger'
        # FirstImageNo is the first saved frame number. Decimation sets saved cadence.
        return float(md.FirstImageNo)/float(md.FrameRate) + np.arange(md.ImageCount)/fps, 'Estimated from Cine frame numbers and saved cadence; timestamp tag unavailable'
    return np.arange(md.ImageCount)/fps, 'Ordinary video has no Cine trigger; first frame is T0'


def nearest_capture(times, t, period):
    insertion = int(np.searchsorted(times, t))
    choices = [i for i in (insertion-1, insertion) if 0 <= i < len(times)]
    i = min(choices, key=lambda i: abs(times[i]-t))
    # No freezing the first/last image or inventing frames across missing captures.
    return i if abs(times[i]-t) <= period*.501 + 1e-12 else None


class OverlayPair:
    def __init__(self, paths):
        self.sessions = []
        try:
            if len(paths)!=2:raise ValueError('Choose exactly two videos for an overlay.')
            for path in paths:
                self.sessions.append(ComparisonSession(path))
            self.paths = [s.path for s in self.sessions]
            timed = [trigger_times(s) for s in self.sessions]
            self.times = [v[0] for v in timed]
            self.notes = [v[1] for v in timed]
            self.periods = [float(np.median(np.diff(t))) if len(t)>1 else
                max(1, float(getattr(s.metadata, 'fDecimation', 1) or 1))/float(s.metadata.FrameRate)
                for t,s in zip(self.times,self.sessions)]
        except Exception:
            self.close()
            raise

    def close(self):
        for session in self.sessions:
            session.close()
        self.sessions = []

    def timeline(self, settings):
        key = tuple((id(raw), len(raw), self.periods[i], bool(settings['raw_t0'][i]),
                     int(settings['t0_frames'][i]), int(settings['offsets'][i]), int(session.metadata.FirstImageNo))
                    for i, (raw, session) in enumerate(zip(self.times, self.sessions)))
        cached = getattr(self, '_timeline_cache', None)
        if cached is not None and cached[0] == key:
            return cached[1]
        times = []
        for i, (raw, session) in enumerate(zip(self.times, self.sessions)):
            zero = 0.
            if not settings['raw_t0'][i]:
                frame = int(settings['t0_frames'][i])-int(session.metadata.FirstImageNo)
                if not 0 <= frame < len(raw):
                    raise ValueError(f'Video {"AB"[i]} T0 must be a recorded frame.')
                zero = raw[frame]
            offset = int(settings['offsets'][i])*self.periods[i]
            times.append(raw if zero == 0 and offset == 0 else raw-zero + offset)
        start = min(t[0] for t in times)
        end = max(t[-1] for t in times)
        count = max(1, int(math.ceil((end-start)/self.periods[0]-1e-8))+1)
        if count > 50_000_000:
            raise ValueError('The requested offsets create an excessively long timeline.')
        result = times, start, count
        self._timeline_cache = key, result
        return result

    def render(self, settings):
        times, start, count = self.timeline(settings)
        position = max(0, min(int(settings['position']), count-1))
        t = start + position*self.periods[0]
        indices = [nearest_capture(ts,t,p) for ts,p in zip(times,self.periods)]
        width = max(int(s.metadata.ImWidth) for s in self.sessions)
        height = max(int(s.metadata.ImHeight) for s in self.sessions)
        frames = [session.frame(index) if index is not None else None
                  for session, index in zip(self.sessions, indices)]
        mono = all(frame is None or frame.ndim == 2 for frame in frames)
        layers = []
        for n, frame in enumerate(frames):
            layer = None
            if frame is not None:
                frame = frame.astype(np.float32, copy=False)
                if not mono and frame.ndim == 2: frame = frame[:,:,None]
                h,w = frame.shape[:2]
                x = (width-w)//2 + (int(settings['x']) if n else 0)
                y = (height-h)//2 + (int(settings['y']) if n else 0)
                if x == y == 0 and (h,w) == (height,width) and (mono or frame.shape[2] == 3):
                    layer = frame
            if layer is None:
                layer = np.zeros((height,width) if mono else (height,width,3),np.float32)
            if frame is not None and layer is not frame:
                left,top,right,bottom = max(0,x),max(0,y),min(width,x+w),min(height,y+h)
                if right>left and bottom>top:
                    layer[top:bottom,left:right] = frame[top-y:bottom-y,left-x:right-x]
            layers.append(layer)
        if settings['mode'] == 'Difference':
            pixels = np.empty_like(layers[0])
            if None not in indices:
                np.subtract(layers[0], layers[1], out=pixels); np.abs(pixels, out=pixels)
            else:
                pixels.fill(0)
        else:
            a,b = [max(0,min(100,float(x)))/100 for x in settings['opacity']]
            pixels = np.multiply(a, layers[0])
            pixels += b*layers[1]
            pixels /= max(1,a+b)
        labels = [None if i is None else i+int(s.metadata.FirstImageNo) for i,s in zip(indices,self.sessions)]
        errors = [None if i is None else float(ts[i]-t)*1e6 for i,ts in zip(indices,times)]
        return dict(image=display_frame(pixels), count=count, position=position,
                    time=t, frames=labels, errors_us=errors,
                    metadata=[dict(first=int(s.metadata.FirstImageNo), count=int(s.metadata.ImageCount),
                        width=int(s.metadata.ImWidth), height=int(s.metadata.ImHeight),
                        fps=float(s.metadata.FrameRate), model=getattr(s.metadata,'CameraModel','')) for s in self.sessions],
                    notes=self.notes)
