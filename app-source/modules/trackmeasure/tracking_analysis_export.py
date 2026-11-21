"""Stream a measured Cine with tracking graphics and an optional synchronized plot.

The input is an immutable UI snapshot. Workers own their Cine reader and Agg
figure; no live Qt scene or Matplotlib widget is accessed while exporting.
"""
from copy import deepcopy
import colorsys
import math
from pathlib import Path
import threading

import cv2
import numpy as np
from PIL import ImageColor
from cine_metadata import CineHandler, capture_time_seconds
from image_orientation import rotate_image, rotate_points
from pyphantom_image.imagetools import ImageTools
from tracking_render import fade_alphas
from video_export import ExportCancelled, video_frame, write_video_frames
from viewer_processing import apply_grade, render_cine_image


def _rgb(value):
    try:
        return tuple(ImageColor.getrgb(str(value))[:3])
    except (TypeError, ValueError):
        return (255, 135, 51)


def _heat_color(value):
    if not np.isfinite(value):
        return (137, 148, 163)
    value = round(float(np.clip(value, 0, 1)) * 100) / 100
    return tuple(round(c * 255) for c in colorsys.hsv_to_rgb((1-value)/3, 1, 1))


def _enabled_tracks(tracks):
    prepared = []
    for key, track in tracks.items():
        if not track.get('enabled', True):
            continue
        frames = np.asarray(track.get('frames', []), dtype=np.int64)
        points = np.asarray(track.get('points', []), dtype=np.float64)
        if not len(frames):
            continue
        if frames.ndim != 1 or points.shape != (len(frames), 2) or np.any(np.diff(frames) <= 0):
            raise ValueError('Tracking samples must have ordered, unique frames and matching X/Y coordinates.')
        heat = track.get('heat_values')
        if heat is not None:
            heat = np.asarray(heat, dtype=float)
            if heat.shape != (len(frames),):
                raise ValueError('Heat-map values do not match the tracked samples.')
        prepared.append(dict(key=key, frames=frames.copy(), points=points.copy(),
                             heat=None if heat is None else heat.copy(),
                             color=_rgb(track.get('color', '#ff8733')),
                             name=str(track.get('name', key))[:100]))
    if not prepared:
        raise ValueError('Enable at least one object with tracked points before exporting analysis.')
    return prepared


class AnalysisOverlay:
    """Render raw-coordinate paths using the same heat values and fade rule as the UI."""
    def __init__(self, tracks, style, source_size, display_size, rotation=0):
        self.tracks = _enabled_tracks(tracks)
        self.style = dict(style)
        self.width, self.height = map(int, display_size)
        source_width, source_height = source_size
        oriented_width = source_height if rotation in (90, 270) else source_width
        self.scale = self.width / oriented_width
        self.radius = max(.1, max(.5, .5*math.sqrt(source_width*source_height/(math.pi*10000)))
                          * float(style.get('radius_scale', 1)) * self.scale)
        self.line_width = max(1, round(self.scale))
        for t in self.tracks:
            points = rotate_points(t['points'], rotation, source_width, source_height)
            t['xy'] = (points + .5) * self.scale - .5
        self._static = None
        if not style.get('fade_enabled', False):
            self._static = self._path_layer(None)

    def _alphas(self, track, centers):
        if not self.style.get('fade_enabled', False):
            return np.full(len(track['points']), 255, np.uint8)
        return fade_alphas(track['points'], centers, self.style.get('fade_radius', 0),
                           self.style.get('transparency', 80))

    def _path_layer(self, centers):
        layer = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        for track in self.tracks:
            finite = np.isfinite(track['xy']).all(axis=1)
            alphas = self._alphas(track, centers)
            points = np.rint(np.nan_to_num(track['xy'])).clip(-1000000, 1000000).astype(np.int32)
            # Lines go underneath every dot, so a later segment cannot paint
            # over an earlier sample's heat-map color.
            for i in range(1, len(points)):
                if finite[i] and finite[i-1] and track['frames'][i] == track['frames'][i-1]+1:
                    alpha = int(min(alphas[i-1], alphas[i]))
                    if alpha:
                        cv2.line(layer, tuple(points[i-1]), tuple(points[i]), (*track['color'], alpha),
                                 self.line_width, cv2.LINE_AA)
            for i, point in enumerate(points):
                if not finite[i]:
                    continue
                alpha = int(alphas[i])
                if alpha:
                    color = track['color'] if track['heat'] is None else _heat_color(track['heat'][i])
                    # Fixed-point radii retain subpixel dots in a reduced export.
                    cv2.circle(layer, tuple(point*16), max(2, round(self.radius*16)), (*color, alpha),
                               -1, cv2.LINE_AA, shift=4)
        return layer

    def render(self, image, frame):
        centers = []
        current = []
        for track in self.tracks:
            i = int(np.searchsorted(track['frames'], frame))
            if i < len(track['frames']) and track['frames'][i] == frame and np.isfinite(track['points'][i]).all():
                centers.append(track['points'][i]); current.append((track, i))
        centers = np.asarray(centers, dtype=float).reshape(-1, 2)
        layer = (self._static if self._static is not None else self._path_layer(centers)).copy()
        for track, i in current:
            xy = track['xy'][i]
            if not (0 <= xy[0] < self.width and 0 <= xy[1] < self.height):
                continue
            alpha = int(self._alphas(track, centers)[i])
            if not alpha:
                continue
            point = tuple(np.rint(xy).astype(int))
            radius = max(3, round(self.radius*2))
            color = track['color'] if track['heat'] is None else _heat_color(track['heat'][i])
            cv2.circle(layer, point, radius+1, (15, 19, 24, alpha), 2, cv2.LINE_AA)
            cv2.circle(layer, point, radius, (*color, alpha), 1, cv2.LINE_AA)
            if self.style.get('names', True):
                text = track['name'].encode('ascii', 'replace').decode('ascii')
                scale = max(.35, min(.8, self.width/1600))
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
                origin = (max(1, min(self.width-tw-2, point[0]+radius+4)),
                          max(th+2, min(self.height-3, point[1]-radius-4)))
                cv2.putText(layer, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale,
                            (15, 19, 24, alpha), 3, cv2.LINE_AA)
                cv2.putText(layer, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale,
                            (*color, alpha), 1, cv2.LINE_AA)
        alpha = layer[:, :, 3:4].astype(np.float32) / 255
        return np.rint(image*(1-alpha) + layer[:, :, :3]*alpha).astype(np.uint8)


class AnalysisPlot:
    """Private Agg plot with a cached background and source-time cursor."""
    def __init__(self, snapshot, width, height):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        self.snapshot = deepcopy(snapshot)
        self.figure = Figure(figsize=(width/100, height/100), dpi=100, facecolor='#171c22')
        self.canvas = FigureCanvasAgg(self.figure)
        self.axes = self.figure.add_subplot(111, facecolor='#20262e')
        ax = self.axes
        for series in self.snapshot.get('series', []):
            ax.plot(series['x'], series['y'], color=series['color'], linewidth=1.2,
                    marker='o', markersize=2, label=str(series['name'])[:50])
        ax.set_title(self.snapshot.get('title', self.snapshot.get('metric', 'Tracking')), fontsize=10, color='#edf1f7')
        ax.set_xlabel(self.snapshot.get('x_label', 'Time'), fontsize=9, color='#dbe3ed')
        ax.set_ylabel(self.snapshot.get('y_label', ''), fontsize=9, color='#dbe3ed')
        ax.tick_params(colors='#dbe3ed', labelsize=8)
        ax.xaxis.get_offset_text().set_color('#dbe3ed');ax.yaxis.get_offset_text().set_color('#dbe3ed')
        for spine in ax.spines.values():spine.set_color('#627081')
        ax.grid(axis='y', color='#66717e', alpha=.35, linewidth=.7)
        for key, setter in [('xlim', ax.set_xlim), ('ylim', ax.set_ylim)]:
            limits = self.snapshot.get(key)
            if limits is not None and len(limits) == 2 and np.isfinite(limits).all() and limits[0] != limits[1]:setter(limits)
        if self.snapshot.get('series'):
            ax.legend(loc='upper right', fontsize=7, labelcolor='#e6edf7', frameon=False,
                      ncol=min(3, len(self.snapshot['series'])))
        self.vertical = ax.axvline(0, color='#00ff7f', linewidth=1.3, linestyle=':', animated=True)
        self.horizontal = ax.axhline(0, color='#00ff7f', linewidth=.8, alpha=.65, linestyle=':', animated=True)
        self.figure.tight_layout(pad=.8)
        self.canvas.draw()
        self.background = self.canvas.copy_from_bbox(self.figure.bbox)

    def render(self, frame, timestamp, cine_index=0, object_id=None):
        self.canvas.restore_region(self.background)
        if self.snapshot.get('mode') != 'Vibration' and np.isfinite(timestamp):
            self.vertical.set_xdata([timestamp, timestamp]);self.axes.draw_artist(self.vertical)
            for series in self.snapshot.get('series', []):
                if series.get('cine_index', 0) != cine_index or (object_id is not None and series.get('object_id') != object_id):
                    continue
                indexes = np.flatnonzero(np.asarray(series.get('frames', [])) == frame)
                if indexes.size:
                    value = float(np.asarray(series['y'])[indexes[0]])
                    if np.isfinite(value):
                        self.horizontal.set_ydata([value, value]);self.axes.draw_artist(self.horizontal)
                    break
        return np.asarray(self.canvas.buffer_rgba())[:, :, :3].copy()


def export_tracking_analysis(source, destination, first, last, fps, settings, grade,
                             tracks, style=None, rotation=0, graph=None, max_height=None,
                             quality='High', cine_index=0, object_id=None, cancel=None,
                             progress=None, handler_factory=CineHandler):
    """Write every chosen source frame once, replayed at ``fps``; never alter raw Cine."""
    source, destination = Path(source), Path(destination)
    if destination.suffix.lower() not in ('.mp4', '.cine'):
        raise ValueError('Choose MP4 or Rendered Cine for a tracking analysis video.')
    if source.resolve() == destination.resolve() or (destination.exists() and source.samefile(destination)):
        raise ValueError('Choose a new file; the source Cine cannot be replaced.')
    if not np.isfinite(fps) or not 1 <= fps <= 120:
        raise ValueError('Playback rate must be between 1 and 120 fps.')
    if rotation not in (0, 90, 180, 270):
        raise ValueError('Video orientation must be a quarter turn.')
    if max_height is not None and (type(max_height) is not int or max_height < 2):
        raise ValueError('Choose a valid video height.')
    style = dict(style or {})
    cancel = cancel or threading.Event()
    if cancel.is_set():raise ExportCancelled()
    handler = handler_factory()
    try:
        handler.load_cine(str(source))
        if type(first) is not int or type(last) is not int or not 0 <= first <= last < handler.metadata.ImageCount:
            raise ValueError('Choose an inclusive frame range within the original Cine.')
        tools = ImageTools()
        def image_at(index):
            raw = handler.get_img(index)
            display = apply_grade(render_cine_image(raw, handler.metadata, settings, tools), grade)
            return video_frame(rotate_image(display, rotation), max_height, pad_even=False)
        initial = image_at(first)
        height, width = initial.shape[:2]
        overlay = AnalysisOverlay(tracks, style, (handler.metadata.ImWidth, handler.metadata.ImHeight),
                                  (width, height), rotation)
        plot_height = max(200, min(360, round(height*.3))) if graph is not None else 0
        output_width = max(640, width) if graph is not None else width
        output_height = height + plot_height
        plot = AnalysisPlot(graph, output_width, plot_height) if graph is not None else None
        movie = destination.suffix.lower() == '.mp4'
        def frames():
            for index in range(first, last+1):
                if cancel.is_set():raise ExportCancelled()
                image = overlay.render(initial if index == first else image_at(index), index)
                if plot is not None:
                    canvas = np.full((output_height, output_width, 3), (23, 28, 34), dtype=np.uint8)
                    x = (output_width-width)//2;canvas[:height, x:x+width] = image
                    timing = handler.get_timestamp(index)
                    seconds = capture_time_seconds(timing, from_trigger=graph.get('time_reference') == 'trigger')
                    timestamp = float(seconds) * float(graph.get('time_scale', 1))
                    canvas[height:] = plot.render(index, timestamp, cine_index, object_id)
                    image = canvas
                yield video_frame(image) if movie else np.ascontiguousarray(image)
        count = last-first+1
        if not movie:
            from cine_render import write_rendered_cine
            return write_rendered_cine(destination, frames(), output_width, output_height, fps, count, cancel, progress)
        return write_video_frames(destination, frames(), output_width+output_width%2,
                                  output_height+output_height%2, fps, count, cancel, progress, quality)
    finally:
        handler.close()
