"""Readers for additional Cine and ordinary video clips in a Viewer sequence."""
from pathlib import Path
from types import SimpleNamespace
import math
import cv2
from cine_metadata import CineHandler


class VideoReader:
    def __init__(self):
        self.capture=None; self.next_frame=0; self.cine_loaded=False

    def load_cine(self,path):
        self.capture=cv2.VideoCapture(str(path))
        if not self.capture.isOpened(): raise ValueError('This video could not be opened.')
        count=int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps=self.capture.get(cv2.CAP_PROP_FPS)
        width=int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH));height=int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if count<1 or width<1 or height<1 or not math.isfinite(fps) or fps<=0:
            raise ValueError('This video does not contain readable frames and timing.')
        self.metadata=SimpleNamespace(ImageCount=count,FirstImageNo=0,FrameRate=fps,
            ImWidth=width,ImHeight=height,CameraModel='',fDecimation=1,RealBPP=8,CFA='RGB',
            Bright=0,Contrast=1,Gamma=1)
        self.cine_loaded=True

    def get_img(self,index):
        if index!=self.next_frame:
            if not self.capture.set(cv2.CAP_PROP_POS_FRAMES,index): raise ValueError('Could not seek in the added video.')
        ok,image=self.capture.read()
        if not ok: raise ValueError(f'Could not decode video frame {index}.')
        self.next_frame=index+1
        return cv2.cvtColor(image,cv2.COLOR_BGR2RGB)

    def close(self):
        if self.capture is not None: self.capture.release()
        self.capture=None;self.cine_loaded=False


def open_media(path, *, initial_index=0, index_snapshot=None):
    reader=CineHandler() if Path(path).suffix.lower()=='.cine' else VideoReader()
    try:
        if isinstance(reader, CineHandler):
            reader.load_cine(path, initial_index=initial_index, index_snapshot=index_snapshot)
        else:
            reader.load_cine(path)
        return reader
    except Exception:
        reader.close(); raise


def media_descriptor(path,reader):
    from viewer_project import source_identity
    md=reader.metadata
    return dict(source_path=str(Path(path).resolve()),frame_count=md.ImageCount,
                first_frame=md.FirstImageNo,identity=source_identity(path),name=Path(path).name,
                playback_fps=30.0 if Path(path).suffix.lower()=='.cine' else min(10_000_000,max(.1,md.FrameRate)))


def fit_canvas(image,width,height):
    if image.shape[:2]==(height,width): return image
    scale=min(width/image.shape[1],height/image.shape[0])
    w,h=max(1,round(image.shape[1]*scale)),max(1,round(image.shape[0]*scale))
    resized=cv2.resize(image,(w,h),interpolation=cv2.INTER_AREA if scale<1 else cv2.INTER_LINEAR)
    left,top=(width-w)//2,(height-h)//2
    return cv2.copyMakeBorder(resized,top,height-h-top,left,width-w-left,cv2.BORDER_CONSTANT,value=(0,0,0))
