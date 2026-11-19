"""Stream an inclusive Cine clip to a shareable video using a separate reader."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import cv2
import numpy as np
from cine_metadata import CineHandler
from pyphantom_image.imagetools import ImageTools
from viewer_processing import apply_grade, render_cine_image
from viewer_timeline import Timeline, default_edit, validate_edit, total_frames, copy_index, copy_options, copy_offset
from viewer_effects import VisualTimeline, apply_effects, default_image_settings
from viewer_media import open_media, VideoReader, fit_canvas
from image_orientation import rotate_image


class ExportCancelled(Exception):
    pass


def video_frame(image, max_height=None, *, pad_even=True):
    if image.dtype == np.uint16:
        # Positive uint16 values divided by 257 never land on a half integer.
        # OpenCV rounds directly into uint8 without full-size uint32 temporaries.
        image = cv2.convertScaleAbs(image, alpha=1 / 257)
    elif image.dtype != np.uint8:
        raise ValueError('Unsupported export image format')
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    if max_height and image.shape[0] > max_height:
        scale = max_height / image.shape[0]
        image = cv2.resize(image, (max(2, round(image.shape[1] * scale)), max_height), interpolation=cv2.INTER_AREA)
    # H.264 yuv420p needs even dimensions. Pad at most one pixel, without cropping.
    if pad_even and (image.shape[0] % 2 or image.shape[1] % 2):
        image = cv2.copyMakeBorder(image, 0, image.shape[0] % 2, 0, image.shape[1] % 2,
                                   cv2.BORDER_REPLICATE)
    return np.ascontiguousarray(image)


def export_clip(source, destination, first, last, fps, settings, grade,
                max_height=None, quality='High', cancel=None, progress=None,
                handler_factory=CineHandler, edit=None, tracks_by_copy=None):
    cancel = cancel or threading.Event()
    destination = Path(destination)
    extension = destination.suffix.lower()
    if extension not in ('.mp4', '.mov', '.avi', '.cine'):
        raise ValueError('Choose an MP4, MOV, AVI or rendered Cine destination')
    if not np.isfinite(fps) or not 1 <= fps <= 120:
        raise ValueError('Playback frame rate must be between 1 and 120 fps')
    if Path(source).resolve() == destination.resolve():
        raise ValueError('The export must use a different file from the source Cine')
    handler = handler_factory()
    readers={}
    try:
        handler.load_cine(source)
        edit = validate_edit(edit or default_edit(handler.metadata.ImageCount, fps), handler.metadata.ImageCount)
        if any(Path(c['source_path']).resolve() == destination.resolve() for c in edit['copies'] if c.get('source_path')):
            raise ValueError('Choose a destination different from every source video.')
        if not (0 <= first <= last < total_frames(edit)):
            raise ValueError('The In/Out range is outside the video sequence')
        edit['output_fps'] = fps
        timeline = Timeline(edit, first, last)
        if timeline.frame_count == 0:
            raise ValueError('Include at least one section within the In/Out range')
        image_tools = ImageTools()
        visual=VisualTimeline(edit);offset=visual.offset_for(timeline)
        def render(index):
            ci=copy_index(edit,index);options=copy_options(edit,ci)
            path=edit['copies'][ci].get('source_path')
            if path and path not in readers:readers[path]=open_media(path)
            reader=readers[path] if path else handler
            frame=index-copy_offset(edit,ci)
            raw=reader.get_img(frame)
            config=(options.get('image_settings') or default_image_settings(reader.metadata)) if ci else settings
            image=raw if isinstance(reader,VideoReader) else render_cine_image(raw,reader.metadata,config,image_tools)
            return video_frame(apply_grade(image,options['grade'] if ci else grade), pad_even=False),reader.metadata
        if cancel.is_set():
            raise ExportCancelled()
        initial_index, initial_rate = timeline.sample(0)
        initial,initial_metadata = render(initial_index)
        orientation = copy_options(edit, copy_index(edit, initial_index)).get('rotation', 0)
        height, width = video_frame(rotate_image(initial, orientation), max_height, pad_even=False).shape[:2]
        movie = extension != '.cine'
        def rendered_frames():
            cached_index, cached_frame, cached_metadata = initial_index, initial, initial_metadata
            for count, (index, rate) in enumerate(timeline.samples()):
                if cancel.is_set():
                    raise ExportCancelled()
                if index != cached_index:
                    cached_index = index
                    cached_frame, cached_metadata = render(index)
                tracks = tracks_by_copy.get(copy_index(edit, index)) if tracks_by_copy else None
                frame, _, _, _ = apply_effects(cached_frame, edit, cached_metadata, visual,
                                                offset + count / fps, index, rate, tracks=tracks)
                fitted = fit_canvas(frame, width, height)
                yield video_frame(fitted) if movie else fitted
        if extension == '.cine':
            from cine_render import write_rendered_cine
            return write_rendered_cine(destination, rendered_frames(), width, height, fps,
                                       timeline.frame_count, cancel, progress)
        return write_video_frames(destination, rendered_frames(), width + width % 2, height + height % 2, fps,
                                  timeline.frame_count, cancel, progress, quality)
    finally:
        handler.close()
        for reader in readers.values():
            reader.close()


def write_video_frames(destination, frames, width, height, fps, total, cancel, progress=None, quality='High',
                       *, full_rate_intent=False):
    """Stream packed RGB frames to a temporary movie and publish only on success."""
    import imageio_ffmpeg
    destination = Path(destination)
    extension = destination.suffix.lower()
    process = temporary = None
    try:
        handle, temporary = tempfile.mkstemp(prefix='.pca-export-', suffix=extension, dir=destination.parent)
        os.close(handle)
        args = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'rawvideo', '-vcodec', 'rawvideo', '-pix_fmt', 'rgb24',
                '-s', f'{width}x{height}', '-framerate', str(fps), '-i', 'pipe:0', '-an']
        if extension == '.avi':
            args += ['-c:v', 'mjpeg', '-q:v', str({'High': 2, 'Balanced': 5, 'Small': 8}[quality]),
                     '-pix_fmt', 'yuvj420p']
        else:
            args += ['-c:v', 'libx264', '-preset', 'fast',
                     '-crf', str({'High': 18, 'Balanced': 23, 'Small': 28}[quality]),
                     '-pix_fmt', 'yuv420p', '-movflags',
                     '+faststart+use_metadata_tags' if full_rate_intent else '+faststart']
        if full_rate_intent:
            from mp4_playback_intent import INTENT_KEY, finalize_full_rate_intent
            args += ['-metadata', f'{INTENT_KEY}=1']
        args += [temporary]
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors)
            try:
                for count, frame in enumerate(frames, 1):
                    if cancel.is_set():
                        raise ExportCancelled()
                    process.stdin.write(frame.tobytes())
                    if progress:
                        progress(count, total)
                process.stdin.close()
                while process.poll() is None:
                    if cancel.wait(.05):
                        raise ExportCancelled()
                if process.returncode:
                    raise RuntimeError('The video encoder could not complete the export')
            except (BrokenPipeError, RuntimeError) as error:
                errors.seek(0)
                detail = errors.read().decode('utf-8', errors='replace')[-3000:]
                raise RuntimeError(f'Video export failed: {detail or str(error)}') from error
        if cancel.is_set():
            raise ExportCancelled()
        if full_rate_intent:
            finalize_full_rate_intent(temporary)
        os.replace(temporary, destination)
        temporary = None
        return str(destination)
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdin and not process.stdin.closed:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
