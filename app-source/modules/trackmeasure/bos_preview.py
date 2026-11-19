"""Lossless, bounded-memory BOS previews: measure once, play without the Cine."""
from collections import Counter, OrderedDict
from contextlib import nullcontext
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time

import cv2
import numpy as np

from app_runtime import user_data_dir
from bos_compass import compass_rect, draw_compass
from bos_frames import BOSFrames, validate_bos_settings
from bos_vector_cache import max_frame_bytes, write_vectors, paint_vectors
from bos_vectors import VectorStyle
from bos_studio_model import MAX_MP4_FPS
from video_export import ExportCancelled, video_frame, write_video_frames


FORMAT = 'pca-lossless-bos-preview-1'
RESERVE_BYTES = 256 * 1024 * 1024


def cache_root():
    return user_data_dir() / 'cache' / 'bos-previews'


def _cancelled(cancel):
    if cancel.is_set():
        raise ExportCancelled()


def _remove_abandoned(root):
    """Only remove this feature's private caches whose owning process has exited."""
    for directory in root.glob('bos-*'):
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            owner = json.loads((directory / 'owner.json').read_text())
            if owner.get('format') != FORMAT or type(owner.get('pid')) is not int or owner['pid'] <= 0:
                continue
            try:
                os.kill(owner['pid'], 0)
            except ProcessLookupError:
                shutil.rmtree(directory)
        except (OSError, ValueError, TypeError):
            continue


class CachedBOSClip:
    """Metadata/index only; each reader opens its own single compressed frame store."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.manifest = json.loads((self.directory / 'manifest.json').read_text())
        if self.manifest.get('format') != FORMAT:
            raise ValueError('Unsupported BOS preview cache.')
        self.times = np.load(self.directory / 'times.npy', allow_pickle=False)
        self.count = int(self.manifest['count'])
        self.width = int(self.manifest['width'])
        self.height = int(self.manifest['height'])
        if (self.count < 1 or min(self.width, self.height) < 1
                or self.times.shape != (self.count,) or self.times.dtype != np.float64):
            raise ValueError('Incomplete BOS preview index.')
        self.offsets = self._load_index('frames', 'index')
        self.original_offsets = self._load_index('original', 'original-index')
        settings = self.manifest['settings']
        store = self.manifest.get('vector_store')
        if store not in (None, 'sites-v1'):
            raise ValueError('Unsupported cached BOS vector format.')
        self.has_vector_track = store is not None
        self.vector_offsets = self._load_index('vectors', 'vector-index') if self.has_vector_track else None
        self.vector_style = VectorStyle.from_settings(settings) if self.has_vector_track else None
        self.map_initially_on = not (self.has_vector_track and settings.get('vector_background') == 'footage')
        self.can_show_direction_colors = settings['component'] == 'direction' and settings['opacity'] > 0
        self.has_direction_colors = (settings['component'] == 'direction' and settings['opacity'] > 0
                                     and not (settings.get('show_vectors')
                                              and settings.get('vector_background') == 'footage'))
        availability = self.directory / 'measured.npy'
        self._measured = None
        if availability.exists():
            self._measured = np.load(availability, allow_pickle=False)
            if self._measured.shape != (self.count,) or self._measured.dtype != np.bool_:
                raise ValueError('Incomplete BOS preview measurements.')

    def _load_index(self, store, index):
        offsets = np.load(self.directory / (index + '.npy'), allow_pickle=False)
        if offsets.shape != (self.count, 2) or offsets.dtype != np.uint64:
            raise ValueError('Incomplete BOS preview index.')
        size = (self.directory / (store + '.bin')).stat().st_size
        if (np.any(offsets > size) or np.any(offsets[:, 1] == 0)
                or offsets[0, 0] != 0
                or np.any(offsets[:, 0] + offsets[:, 1] > size)
                or np.any(offsets[1:, 0] != offsets[:-1, 0] + offsets[:-1, 1])
                or int(offsets[-1, 0]) + int(offsets[-1, 1]) != size):
            raise ValueError('Incomplete BOS preview frames.')
        return offsets

    def compass_available(self, index, *, map_on=None):
        directional = self.has_direction_colors
        if map_on is not None and self.has_vector_track:
            directional = map_on and self.can_show_direction_colors
        if not directional or not 0 <= index < self.count:
            return False
        if self._measured is None:
            # Older temporary caches did not record which frames failed.
            return not self.manifest.get('warning_counts')
        return bool(self._measured[index])

    def reader(self):
        return CacheReader(self)

    def close(self):
        """Call after all playback/export readers have stopped."""
        shutil.rmtree(self.directory, ignore_errors=True)

    def source_frame(self, index):
        return self.manifest['first_image_no'] + self.manifest['first'] + int(index)


class CacheReader:
    def __init__(self, clip):
        self.clip = clip
        self.stream = (clip.directory / 'frames.bin').open('rb')

        self.original = (clip.directory / 'original.bin').open('rb')
        self.vectors = (clip.directory / 'vectors.bin').open('rb') if clip.has_vector_track else None

    def _read_image(self, index, overlay):
        if not 0 <= index < self.clip.count:
            raise IndexError('Frame outside analyzed range.')
        offsets = self.clip.offsets if overlay else self.clip.original_offsets
        stream = self.stream if overlay else self.original
        start, length = map(int, offsets[index])
        stream.seek(start)
        payload = stream.read(length)
        if len(payload) != length:
            raise ValueError('The cached BOS frame could not be read completely.')
        image = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape != (self.clip.height, self.clip.width, 3):
            raise ValueError('The cached BOS frame could not be decoded.')
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    def read(self, index, overlay=True):
        """Read the initial analyzed composition or the untouched Cine frame."""
        return self.blended(index, 255 if overlay else 0)

    def blended(self, index, level, vector_level=None, style=None):
        """Compose the map first, then the independently faded arrow artwork."""
        if vector_level is None:
            # Callers using the original single-layer API fade the whole result.
            vector_level = level
            if not self.clip.map_initially_on:
                level = 0
        if level in (0, 255):
            image = self._read_image(index, level == 255)
        else:
            alpha = level / 255.
            image = cv2.addWeighted(self._read_image(index, False), 1.-alpha,
                                    self._read_image(index, True), alpha, 0.)
        if vector_level and self.vectors is not None:
            arrows = image if vector_level == 255 else image.copy()
            paint_vectors(self.vectors, self.clip.vector_offsets, index, arrows,
                          self.clip.manifest['settings'], style or self.clip.vector_style)
            if vector_level < 255:
                cv2.addWeighted(image, 1.-vector_level/255., arrows, vector_level/255., 0., dst=image)
        return image

    def close(self):
        self.stream.close()
        self.original.close()
        if self.vectors is not None:
            self.vectors.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _write_frame(stream, image):
    monochrome = (np.array_equal(image[..., 0], image[..., 1])
                  and np.array_equal(image[..., 0], image[..., 2]))
    pixels = image[..., 0] if monochrome else cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    ok, encoded = cv2.imencode('.png', pixels, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    if not ok:
        raise ValueError('Could not prepare a lossless BOS frame.')
    start = stream.tell()
    stream.write(encoded)
    return start, encoded.nbytes


def prepare_bos_preview(settings, first, last, *, cancel=None, progress=None, root=None):
    """Render every selected source frame once and commit a private lossless cache.

    The frame range is inclusive and uses zero-based source indices. PNG is only
    storage compression; decoded RGB pixels exactly match BOSFrames.render().
    """
    state = validate_bos_settings(deepcopy(settings))
    if type(first) is not int or type(last) is not int or not 0 <= first <= last:
        raise ValueError('Choose a valid Mark In and Mark Out range.')
    if state['reference'] is None or not state['roi']:
        raise ValueError('Pin a reference and draw an analysis region first.')
    cancel = cancel or threading.Event()
    _cancelled(cancel)
    root = Path(root) if root is not None else cache_root()
    root.mkdir(parents=True, exist_ok=True)
    _remove_abandoned(root)
    session = None
    directory = None
    committed = False
    started = time.monotonic()
    try:
        session = BOSFrames(state['path'])
        if last >= session.count:
            raise ValueError('The selected range extends beyond this Cine.')
        total = last - first + 1
        # Conservative upper bound, including PNG overhead. Do not fill the
        # system disk on a long analysis, even when compression is ineffective.
        worst_bytes = total * (int(session.md.ImWidth) * int(session.md.ImHeight) * 3 + 65536)
        worst_bytes *= 2  # Original and map frames.
        vector_bytes = max_frame_bytes(state)
        worst_bytes += total*vector_bytes
        if shutil.disk_usage(root).free < worst_bytes + RESERVE_BYTES:
            raise ValueError(f'This range needs up to {worst_bytes / 1e9:,.1f} GB of local cache space. '
                             'Choose a shorter range or free local disk space.')
        directory = Path(tempfile.mkdtemp(prefix='bos-', dir=root))
        (directory / 'owner.json').write_text(json.dumps(dict(format=FORMAT, pid=os.getpid())))
        offsets = np.zeros((total, 2), dtype=np.uint64)
        original_offsets = np.zeros_like(offsets)
        vector_offsets = np.zeros_like(offsets) if state['show_vectors'] else None
        times = np.full(total, np.nan, dtype=np.float64)
        measured = np.zeros(total, dtype=np.bool_)
        warnings = Counter()
        warning_examples = []
        anchor_failures = 0
        # Store the pure map even for arrows-only previews. The two RGB stores
        # plus a sparse table of measured sites replace a baked third movie.
        measurement_settings = dict(state, show_vectors=False)
        vector_store = (directory/'vectors.bin').open('wb') if state['show_vectors'] else nullcontext()
        with (vector_store as vector_output, (directory / 'frames.bin').open('wb') as output,
              (directory / 'original.bin').open('wb') as original_output):
            for local, index in enumerate(range(first, last + 1)):
                _cancelled(cancel)
                rendered = session.render(dict(measurement_settings, position=index))
                if rendered['field'] is None:
                    raise ValueError(rendered.get('warning') or 'The analysis region could not be measured.')
                display_field = rendered['display_field']
                measured[local] = display_field is not None and display_field.valid.any()
                image = rendered['image']
                if local % 32 == 0 and shutil.disk_usage(root).free < RESERVE_BYTES + 2*image.nbytes + vector_bytes:
                    raise ValueError('Local cache space is running low. Choose a shorter range.')
                offsets[local] = _write_frame(output, image)
                # render() already prepared this untouched display frame from the same read.
                original_offsets[local] = _write_frame(original_output, session.base)
                if vector_output is not None:
                    vector_offsets[local] = write_vectors(vector_output, display_field, state)
                if rendered.get('time_us') is not None:
                    times[local] = rendered['time_us']
                warning = rendered.get('warning', '')
                drift = rendered.get('drift')
                if drift is not None and not drift.get('reliable', False):
                    anchor_failures += 1
                if warning:
                    warnings[warning] += 1
                    if len(warning_examples) < 12:
                        warning_examples.append(dict(frame=session.first + index, message=warning))
                if progress:
                    progress(local + 1, total)
            output.flush()
            os.fsync(output.fileno())
        _cancelled(cancel)
        if vector_offsets is not None:
            np.save(directory/'vector-index.npy', vector_offsets, allow_pickle=False)
        np.save(directory / 'index.npy', offsets, allow_pickle=False)
        np.save(directory / 'original-index.npy', original_offsets, allow_pickle=False)
        np.save(directory / 'measured.npy', measured, allow_pickle=False)
        np.save(directory / 'times.npy', times, allow_pickle=False)
        capture_fps = float(getattr(session.md, 'FrameRate', 0.) or 0.)
        decimation = max(1, int(getattr(session.md, 'fDecimation', 1) or 1))
        manifest = dict(decimation=decimation, capture_fps=capture_fps if math.isfinite(capture_fps) and capture_fps > 0 else None,
                        format=FORMAT, settings=state, source=state['path'], first=first, last=last,
                        first_image_no=session.first, count=total, width=image.shape[1], height=image.shape[0],
                        created=time.time(), seconds=time.monotonic()-started, warning_counts=dict(warnings),
                        warning_examples=warning_examples, anchor_failures=anchor_failures,
                        encoded_bytes=sum((directory / f).stat().st_size for f in ('frames.bin','original.bin')))
        if vector_offsets is not None:
            manifest['vector_store'] = 'sites-v1'
            manifest['encoded_bytes'] += (directory/'vectors.bin').stat().st_size
        (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        result = CachedBOSClip(directory)
        committed = True
        return result
    finally:
        if session is not None:
            session.close()
        if directory is not None and not committed:
            shutil.rmtree(directory, ignore_errors=True)


def _compass_at_opacity(frame, compass, level):
    if level == 255:
        draw_compass(frame, compass)
        return
    x, y, side, _ = compass_rect(compass, frame.shape[1], frame.shape[0])
    region = frame[y:y+side, x:x+side]
    base = region.copy()
    draw_compass(frame, compass)
    cv2.addWeighted(region, level/255., base, 1.-level/255., 0., dst=region)


def export_cached_bos(clip, destination, fps, cancel=None, progress=None, *, overlay_mask=None,
                      compass=None, studio=None, text_renderer=None, vectors_enabled=True):
    """Encode at constant output FPS, sampling the prepared clip on its edited clock.

    With no studio edits, each cached frame appears exactly once at the given FPS.
    text_renderer receives a private RGB frame, local source index and playback rate.
    """
    destination = Path(destination)
    source = Path(clip.manifest['source'])
    if (destination.resolve() == source.resolve()
            or (destination.exists() and source.exists() and destination.samefile(source))):
        raise ValueError('Choose a destination different from the source Cine.')
    if destination.suffix.lower() != '.mp4' or not math.isfinite(fps) or not 1 <= fps <= MAX_MP4_FPS:
        raise ValueError(f'Choose an MP4 file and an output rate from 1 to {MAX_MP4_FPS} fps.')
    cancel = cancel or threading.Event()
    _cancelled(cancel)
    if overlay_mask is not None:
        overlay_mask = bytes(overlay_mask)
        if len(overlay_mask) != clip.count:
            raise ValueError('Overlay selections must cover the analyzed clip.')
    if studio is not None and studio.count != clip.count:
        raise ValueError('These studio edits belong to a different frame count.')
    edit = studio.copy() if studio is not None else None
    opacity = edit.opacity_bytes() if edit is not None else None
    vector_opacity = edit.opacity_bytes('vectors') if edit is not None and clip.has_vector_track else None
    indices = edit.output_frames(fps) if edit is not None else range(clip.count)
    total = edit.output_count(fps) if edit is not None else clip.count
    with clip.reader() as reader:
        def frames():
            previous = None
            result = None
            for index in indices:
                _cancelled(cancel)
                if index != previous:
                    level = opacity[index] if opacity is not None else 255
                    if overlay_mask is not None and not overlay_mask[index]:
                        level = 0
                    if clip.has_vector_track:
                        if edit is None:
                            arrows = level if vectors_enabled else 0
                            level = level if clip.map_initially_on else 0
                        else:
                            arrows = vector_opacity[index] if vectors_enabled else 0
                        frame = reader.blended(index, level, arrows, edit.vector_style if edit else None)
                    else:
                        frame = reader.blended(index, level)
                    if compass is not None and level and clip.compass_available(index, map_on=True):
                        _compass_at_opacity(frame, compass, level)
                    if text_renderer is not None:
                        updated = text_renderer(frame, index, edit.rate_at(index) if edit else fps)
                        if updated is not None:
                            frame = updated
                    result = video_frame(frame)
                    previous = index
                yield result
        width, height = clip.width + clip.width % 2, clip.height + clip.height % 2
        return write_video_frames(destination, frames(), width, height, fps, total, cancel, progress, full_rate_intent=fps >= 120)


class PreviewReadAhead(threading.Thread):
    """Two independent cache readers, bounded RGB memory, no GUI-thread decoding."""
    def __init__(self, clip, memory_bytes=64 * 1024 * 1024):
        super().__init__(name='PCA BOS preview decode', daemon=True)
        self.clip = clip
        self.capacity = max(1, min(120, memory_bytes // (clip.width * clip.height * 3)))
        self.condition = threading.Condition()
        self.frames = OrderedDict()
        self.inflight = set()
        self.alpha_mask = None
        self.vector_mask = None
        self.vector_style = None
        self.targets = range(min(clip.count, self.capacity))
        self.stopped = False
        self.error = None

    def set_overlay_mask(self, mask):
        self.set_overlay_alpha(None if mask is None else bytes(255 if v else 0 for v in mask))

    def set_overlay_alpha(self, levels):
        # Single-track callers control the whole original composition.
        self.set_layers(levels, None)

    def set_layers(self, levels, vectors, style=None):
        levels = bytes(levels) if levels is not None else None
        vectors = bytes(vectors) if vectors is not None else None
        if any(mask is not None and len(mask) != self.clip.count for mask in (levels, vectors)):
            raise ValueError('Layer opacity must cover the analyzed clip.')
        with self.condition:
            old = {index: self._level(index) for index in self.frames}
            self.alpha_mask = levels
            self.vector_mask = vectors
            self.vector_style = style
            for index, value in old.items():
                if value != self._level(index):
                    del self.frames[index]
            self.condition.notify_all()

    def _level(self, index):
        map_level = 255 if self.alpha_mask is None else self.alpha_mask[index]
        if not self.clip.has_vector_track:
            return (map_level,)
        vector_level = None if self.vector_mask is None else self.vector_mask[index]
        return map_level, vector_level, self.vector_style

    def request(self, index, targets=None):
        with self.condition:
            index = max(0, min(self.clip.count-1, int(index)))
            self.targets = (range(index, min(self.clip.count, index+self.capacity))
                            if targets is None else targets)
            for key in list(self.frames):
                if key not in self.targets:
                    del self.frames[key]
            self.condition.notify_all()

    def get(self, index):
        with self.condition:
            return self.frames.get(index)

    def ready_count(self, index):
        with self.condition:
            count = 0
            for key in self.targets:
                if key < index:
                    continue
                if key not in self.frames:
                    break
                count += 1
            return count

    def stop(self):
        with self.condition:
            self.stopped = True
            self.condition.notify_all()

    def run(self):
        helpers = [threading.Thread(target=self._decode, name='PCA BOS preview decode', daemon=True)
                   for _ in range(min(3, self.capacity-1))]
        for helper in helpers:
            helper.start()
        self._decode()
        for helper in helpers:
            helper.join()

    def _decode(self):
        try:
            with self.clip.reader() as reader:
                while True:
                    with self.condition:
                        if self.stopped:
                            return
                        index = next((i for i in self.targets
                                      if i not in self.frames and i not in self.inflight), None)
                        if index is None:
                            self.condition.wait(.2)
                            continue
                        self.inflight.add(index)
                        level = self._level(index)
                    image = reader.blended(index, *level)
                    with self.condition:
                        self.inflight.discard(index)
                        if (level == self._level(index) and not self.stopped
                                and index in self.targets):
                            self.frames[index] = image
                        self.condition.notify_all()
        except Exception as error:
            with self.condition:
                self.error = str(error)
                self.stopped = True
                self.condition.notify_all()
