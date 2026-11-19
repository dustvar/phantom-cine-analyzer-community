"""Analyze a fixed BOS range and play its lossless cached result independently."""
from collections import deque
from copy import deepcopy
import math
from pathlib import Path
import threading
import time

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, QSignalBlocker, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QDialog, QDoubleSpinBox,
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QSlider, QStyle, QStyleOptionSlider, QVBoxLayout)

from bos_compass_view import CompassViewMixin
from bos_display import DIRECTION_COLORS
from bos_playback_controls import ReplaySpeed, SampleFrameBox
from bos_sampling import RangeSampling
from bos_studio_model import StudioEdit
from bos_studio_ui import RecordStudio
from bos_studio_text import StudioText, capture_rate
from bos_preview import prepare_bos_preview, export_cached_bos, PreviewReadAhead
from camera_comparator_ui import ImageView
from video_export import ExportCancelled


class _Operation(QThread):
    progress = Signal(int, int)

    def __init__(self, parent, operation):
        super().__init__(parent)
        self.operation = operation
        self.cancel = threading.Event()
        self.result = self.error = None
        self.cancelled = False

    def run(self):
        try:
            self.result = self.operation(self.cancel, self.progress.emit)
            if self.cancel.is_set() and hasattr(self.result, 'close'):
                self.result.close()
                self.result = None
                self.cancelled = True
        except ExportCancelled:
            self.cancelled = True
        except Exception as error:
            self.error = str(error)


class _Progress(QDialog):
    def __init__(self, parent, title, total, operation):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        self.summary = QLabel('Preparing…')
        self.summary.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, total)
        self.cancel_button = QPushButton('Cancel')
        for widget in (self.summary, self.bar, self.cancel_button):
            layout.addWidget(widget)
        self.started = time.monotonic()
        self.worker = _Operation(self, operation)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(lambda: self.done(QDialog.DialogCode.Accepted))
        self.cancel_button.clicked.connect(self.reject)

    def _progress(self, completed, total):
        self.bar.setValue(completed)
        elapsed = time.monotonic() - self.started
        eta = elapsed * (total-completed) / max(1, completed)
        if not self.worker.cancel.is_set():
            self.summary.setText(f'{completed:,} of {total:,} frames · '
                                 f'{completed/max(.001, elapsed):.1f} frames/s · '
                                 f'about {eta/60:.1f} min remaining')

    def reject(self):
        if self.worker.isRunning():
            self.worker.cancel.set()
            self.cancel_button.setEnabled(False)
            self.summary.setText('Cancelling and removing unfinished files…')
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


class _SampleTimeline(QSlider):
    """A native-source timeline whose keyboard steps land on analyzed frames."""
    def __init__(self, sampling):
        super().__init__(Qt.Orientation.Horizontal)
        self.sampling = sampling
        self.setRange(0, sampling.source_count-1)
        self.setPageStep(max(self.pageStep(), sampling.step))
        self.actionTriggered.connect(self._step_sample)

    def _step_sample(self, action):
        if action in (QSlider.SliderAction.SliderSingleStepAdd.value,
                      QSlider.SliderAction.SliderSingleStepSub.value):
            direction = 1 if action == QSlider.SliderAction.SliderSingleStepAdd.value else -1
            index = self.sampling.index_at(self.value())+direction
            index = min(self.sampling.count-1, max(0, index))
            self.setSliderPosition(self.sampling.offset(index))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                                 QStyle.SubControl.SC_SliderHandle, self)
            if not handle.contains(event.position().toPoint()):
                # A click in the groove seeks directly; keep Qt's normal handle dragging.
                span = max(1, self.width()-handle.width())
                x = round(event.position().x()-handle.width()/2)
                self.setSliderDown(True)
                self.setValue(QStyle.sliderValueFromPosition(0, self.maximum(), x, span, option.upsideDown))
        super().mousePressEvent(event)


class _PlaybackView(CompassViewMixin, ImageView):
    framePainted = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text_card = None
        self.index = -1
        self.painted_index = -1
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def present(self, image, index):
        self.index = index
        # Cache pixels are immutable. Keep their owner alive while Qt displays
        # this frame instead of copying the entire image on each presentation.
        self._pixels = image
        self.image = QImage(image.data, image.shape[1], image.shape[0],
                            image.strides[0], QImage.Format.Format_RGB888)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.text_card and self.image is not None:
            pixels, (x, y, w, h) = self.text_card
            card = QImage(pixels.data, w, h, pixels.strides[0], QImage.Format.Format_RGBA8888)
            target = self.image_rect()
            sx, sy = target.width()/self.image.width(), target.height()/self.image.height()
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF(target.x()+x*sx, target.y()+y*sy, w*sx, h*sy), card)
            painter.end()
        if self.index != self.painted_index:
            self.painted_index = self.index
            self.framePainted.emit(self.index)


class BOSPreviewWindow(QDialog):
    """Real-time transport over predecoded cache frames, with an honest rate readout."""
    def __init__(self, parent, clip):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.clip = clip
        self.directional = clip.has_direction_colors or (clip.has_vector_track and clip.can_show_direction_colors)
        self.edit_model = StudioEdit(clip.count, clip.manifest['settings']['playback_fps'], sampling=clip.sampling)
        self.capture_fps = capture_rate(clip)
        self.display_hz = 60.
        self.fast_preview = self.edit_model.speeds[0].fps > 240
        self._levels = self.edit_model.opacity_bytes()
        self._raw = bytes(clip.count)
        if not clip.map_initially_on:
            self._levels = self._raw
        self._vector_levels = self.edit_model.opacity_bytes('vectors') if clip.has_vector_track else None
        self._vector_style = None
        self.reader = PreviewReadAhead(clip)
        self.reader.set_layers(self._levels, self._vector_levels)
        self.position = -1
        self.playing = False
        self.buffering = False
        self.buffer_target = None
        self.clock = None
        self.resume_after_seek = False
        self.pending_seek = 0
        self.closing = self.cleaned = False
        self.export_worker = None
        self.paints = deque()
        self.skipped = 0
        self._last_rate_update = 0.
        self._last_controls_update = 0.
        self.setWindowTitle(f'Analyzed BOS · {Path(clip.manifest["source"]).name}')
        self.setMinimumSize(560, 360)
        self._build()
        rect = self.screen().availableGeometry()
        self.resize(min(1280, rect.width()-40), min(960, rect.height()-60))
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(2 if self.fps.value() > 120 else 5)
        self.timer.timeout.connect(self._tick)
        self.reader.start()
        self.reader.request(0)
        self.timer.start()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)
        title = QLabel('Analyzed BOS')
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        header = QHBoxLayout()
        header.setSpacing(6)
        header.addWidget(title)
        if self.directional:
            key = QLabel(' &nbsp; '.join(
                f'<b style="color:{DIRECTION_COLORS[i]}">{arrow}</b>'
                for i, arrow in [(4, '←'), (6, '↑'), (2, '↓'), (0, '→')]))
            key.setStyleSheet('font-size: 19px;')
            key.setAccessibleName('Direction key: left mint, up blue, down gold, right coral')
            key.setToolTip('Direction of apparent dot shift. Diagonal shifts blend between the arrows.')
            header.addWidget(key)
        sampling = self.clip.sampling
        summary = QLabel(f'{self.clip.count:,} frames' if sampling.step == 1 else
                         f'{sampling.count:,} of {sampling.source_count:,} frames analyzed')
        summary.setToolTip(f'Cine {self.clip.source_frame(0):,} to '
                           f'{self.clip.source_frame(self.clip.count-1):,} · Lossless cached result')
        header.insertWidget(1, summary)
        header.insertStretch(2, 1)
        layout.addLayout(header)
        self.view = _PlaybackView(self)
        self.view.message = 'Loading analyzed frames…'
        self.view.framePainted.connect(self._painted)
        layout.addWidget(self.view, 1)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        failures = self.clip.manifest.get('anchor_failures', 0)
        messages = self.clip.manifest.get('warning_counts', {})
        if failures:
            self.warning.setText(f'{failures:,} frames had an unreliable stationary patch and show the original '
                                 'footage without an overlay. Choose a clearer stationary patch and analyze again if needed.')
        elif messages:
            self.warning.setText('Analysis notes: ' + '; '.join(f'{text} ({count:,} frames)' for text, count in messages.items()))
        self.warning.setVisible(bool(self.warning.text()))
        self.warning.setStyleSheet('color: #e4c284;')
        layout.addWidget(self.warning)
        self.slider = _SampleTimeline(sampling)
        self.slider.setAccessibleName('Analyzed BOS timeline')
        self.slider.valueChanged.connect(self._seek_source_offset)
        self.slider.sliderReleased.connect(self._sync_slider)
        layout.addWidget(self.slider)
        transport = QHBoxLayout()
        self.previous = QPushButton('‹')
        self.previous.setAccessibleName('Previous analyzed frame')
        self.previous.setToolTip('Previous frame')
        self.previous.clicked.connect(lambda: self.seek(max(0, self.position-1)))
        self.play = QPushButton('Play')
        self.play.clicked.connect(self.toggle_play)
        self.following = QPushButton('›')
        self.following.setAccessibleName('Next analyzed frame')
        self.following.setToolTip('Next frame')
        self.following.clicked.connect(lambda: self.seek(min(self.clip.count-1, self.position+1)))
        self.loop = QCheckBox('Loop')
        self.number = SampleFrameBox(self.clip.source_frame(0), sampling)
        self.number.setPrefix('Frame ')
        self.number.valueChanged.connect(lambda value: self.seek(sampling.nearest(value-self.clip.source_frame(0))))
        self.speed_control = ReplaySpeed()
        self.fps = self.speed_control.number
        self.fps.setDecimals(2)
        self.fps.setKeyboardTracking(False)
        self.fps.setSuffix(' fps')
        self.fps.setValue(self.clip.manifest['settings']['playback_fps'])
        self.fps.setToolTip('Requested replay rate. The shown rate reports frames painted by the app; '
                            'your display refresh rate can limit visible motion at very high rates.')
        self.fps.valueChanged.connect(self._rate_changed)
        for widget in (self.previous, self.play, self.following, self.loop):
            transport.addWidget(widget)
        transport.addStretch()
        transport.addWidget(self.number)
        transport.addWidget(self.speed_control)
        for button in (self.previous, self.following):
            button.setStyleSheet('min-width: 22px; max-width: 22px; padding: 2px;')
        self.play.setMinimumWidth(70)
        layout.addLayout(transport)
        edit_row = QHBoxLayout()
        edit_row.setSpacing(5)
        self.overlay = QCheckBox('Overlay')
        self.overlay.setAccessibleName('Show BOS overlay')
        self.overlay.setChecked(self.clip.map_initially_on)
        self.overlay.setToolTip('Show the overlay track in playback and export.')
        if self.clip.manifest['settings'].get('show_vectors') and not self.clip.has_vector_track:
            self.overlay.setToolTip('This earlier analysis combines the map and arrows. Analyze the range again to edit them separately.')
        self.vectors = QCheckBox('Vectors')
        self.vectors.setAccessibleName('Show BOS vectors')
        self.vectors.setChecked(self.clip.has_vector_track)
        self.vectors.setVisible(self.clip.has_vector_track)
        self.vectors.setToolTip('Show the vector track in playback and export.')
        self.studio_button = QPushButton('Record Studio')
        self.studio_button.setCheckable(True)
        self.studio_button.setToolTip('Trim and fade overlay or vector ranges, style arrows, set playback speed and add text.')
        self.studio_button.setStyleSheet('QPushButton {border: 1px solid #5ba996; padding: 4px 12px;}'
                                         'QPushButton:checked {background: #275449; color: white;}')
        edit_row.addWidget(self.studio_button)
        header.addSpacing(5)
        for box in (self.overlay, self.vectors):
            box.ensurePolished()
            box.setMinimumWidth(max(box.sizeHint().width(), box.fontMetrics().horizontalAdvance(box.text())+32))
        header.addWidget(self.overlay)
        if self.clip.has_vector_track:
            header.addSpacing(5)
        header.addWidget(self.vectors)
        header.addSpacing(8)
        self.compass_button = QPushButton('Compass')
        self.compass_button.setCheckable(True)
        self.compass_button.setVisible(self.directional)
        self.compass_button.setAccessibleName('Show direction compass on video and export')
        self.compass_button.setToolTip('Add a movable, resizable direction key to the video and exported MP4.')
        header.addWidget(self.compass_button)
        self.view.compass_edit_started = self.pause
        self.view.compass_removed = lambda: self.compass_button.setChecked(False)
        self.compass_button.toggled.connect(self._toggle_compass)
        layout.addLayout(edit_row)
        self.studio = RecordStudio(self.clip, self.edit_model, self)
        self.studio.hide()
        layout.addWidget(self.studio)
        self.studio_button.toggled.connect(self._show_studio)
        self.studio.changed.connect(self._studio_changed)
        self.studio.editStarted.connect(self.pause)
        self.studio.seekRequested.connect(self.seek)
        self.overlay.toggled.connect(self._studio_changed)
        self.vectors.toggled.connect(self._studio_changed)
        self._text = StudioText(self.clip, self.studio.text_settings(), self.capture_fps)
        if self.clip.has_direction_colors:
            with QSignalBlocker(self.compass_button):
                self.compass_button.setChecked(True)
            self.view.set_compass_enabled(True)
            self.view.deselect_compass()
        self.rate = QLabel('Preparing cached playback…')
        self.rate.setWordWrap(True)
        layout.addWidget(self.rate)
        self.frame_info = QLabel('Preparing the analyzed frame…')
        self.frame_info.setWordWrap(True)
        self.frame_info.setMinimumWidth(0)
        self.frame_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        edit_row.addWidget(self.frame_info, 1)
        self.export = QPushButton('Export MP4…')
        self.export.setToolTip('Save these prepared frames at the selected replay rate without recalculating BOS.')
        self.export.clicked.connect(self.export_movie)
        edit_row.addWidget(self.export)
        close = QPushButton('Close')
        close.setToolTip('Close this prepared result and free its temporary cache. Export MP4 first to keep a video.')
        close.clicked.connect(self.close)
        edit_row.addWidget(close)
        self.play.setEnabled(False)
        self.studio.encoding.currentIndexChanged.connect(self._update_export_summary)
        self._update_export_summary()

    def _update_export_summary(self):
        fps = self.studio.output_fps()
        seconds = self.edit_model.output_count(fps)/fps
        self.export.setText(f'Export MP4…  {seconds:.1f} s')
        self.export.setToolTip(f'{fps:g} fps MP4 · {seconds:.2f} seconds. '
                               'Set playback speed and the MP4 frame rate in Record Studio.')

    def _toggle_compass(self, enabled):
        if enabled:
            self.pause()
            self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        self.view.set_compass_enabled(enabled)
        self._update_compass_frame(self.position)

    def _update_compass_frame(self, index):
        if not self.directional:
            return
        enabled = self.compass_button.isChecked()
        visible = (enabled and index >= 0 and self.overlay.isChecked()
                   and self._levels[index] > 0 and self.clip.compass_available(index, map_on=True))
        self.view.set_compass_visible(visible)
        if visible:
            self.view.set_compass_opacity(self._levels[index]/255.)
        if not enabled or self.playing:
            hint = ''
        elif visible:
            hint = 'Drag the compass to move it. Drag a corner to resize it.'
        else:
            hint = 'Compass appears on frames showing directional BOS.'
        tip = hint or 'Show or hide the direction key on the video and exported MP4.'
        if self.compass_button.toolTip() != tip:
            self.compass_button.setToolTip(tip)

    def _show_studio(self, visible):
        if visible:
            self.pause()
            self.studio.set_position(max(0, self.position))
        self.studio.setVisible(visible)

    def overlay_mask(self):
        return bytes(bool(level) for level in self._levels)

    def _studio_changed(self):
        self._update_export_summary()
        levels = self.edit_model.opacity_bytes() if self.overlay.isChecked() else self._raw
        self._text = StudioText(self.clip, self.studio.text_settings(), self.capture_fps)
        self.fast_preview = any(span.fps > 240 for span in self.edit_model.speeds)
        self.timer.setInterval(2 if any(span.fps > 120 for span in self.edit_model.speeds) else 5)
        vectors = None
        if self.clip.has_vector_track:
            vectors = self.edit_model.opacity_bytes('vectors') if self.vectors.isChecked() else self._raw
        style = self.edit_model.vector_style
        if levels != self._levels or vectors != self._vector_levels or style != self._vector_style:
            self._levels, self._vector_levels, self._vector_style = levels, vectors, style
            self.reader.set_layers(levels, vectors, style)
            resume = self.playing or self.resume_after_seek
            self.seek(self.pending_seek if self.pending_seek is not None else max(0, self.position))
            self.resume_after_seek = resume
        elif self.position >= 0:
            self._update_frame_controls(self.position)
            self._update_card(self.position)
            self.view.update()

    def _update_card(self, index):
        self.view.text_card = self._text.graphic(index, self.edit_model.rate_at(index))

    def _painted(self, index):
        if self.playing and not self.buffering:
            self.paints.append(time.monotonic())

    def _request_frames(self, index):
        targets = None
        if self.playing and self.fast_preview:
            targets = self.edit_model.preview_indices(index, self.reader.capacity, self.display_hz)
        self.reader.request(index, targets)

    def _present(self, index, image):
        self.position = index
        self._update_compass_frame(index)
        self._update_card(index)
        self.view.present(image, index)
        self._request_frames(index)
        now = time.monotonic()
        if not self.playing or self.fps.value() <= 120 or now-self._last_controls_update >= 1/30:
            self._update_frame_controls(index)
            self._last_controls_update = now

    def _update_frame_controls(self, index):
        if not self.slider.isSliderDown():
            self._sync_slider(index)
        with QSignalBlocker(self.number):
            self.number.setValue(self.clip.source_frame(index))
        rate = self.edit_model.rate_at(index)
        if self.fps.value() != rate:
            with QSignalBlocker(self.fps):
                self.fps.setValue(rate)
            self.speed_control.sync_slider()
        if self.studio.isVisible():
            self.studio.set_position(index)
        stamp = self.clip.times[index]
        text = f'{index+1:,} / {self.clip.count:,} analyzed frames'
        if math.isfinite(stamp):
            text += f' · t = {stamp:+,.2f} µs from trigger'
        level = self._levels[index]
        layers = [('BOS', level)] if self._vector_levels is None else [
            ('Overlay', level), ('Vectors', self._vector_levels[index])]
        active = [name if value == 255 else f'{name} {value/255:.0%}' for name, value in layers if value]
        self.frame_info.setText(f"{text} · {' + '.join(active) or 'Original'}")

    def _sync_slider(self, index=None):
        if index is None:
            index = self.pending_seek if self.pending_seek is not None else max(0, self.position)
        with QSignalBlocker(self.slider):
            self.slider.setValue(self.clip.sampling.offset(index))

    def _seek_source_offset(self, offset):
        index = self.clip.sampling.index_at(offset)
        if not self.slider.isSliderDown():
            self._sync_slider(index)
        if index == self.pending_seek:
            return
        if index == self.position and self.pending_seek is None:
            if self.playing:
                self.pause()
            return
        self.seek(index)

    def seek(self, index):
        if self.closing:
            return
        self.pending_seek = max(0, min(self.clip.count-1, int(index)))
        self.pause()
        if self.studio.isVisible():
            self.studio.set_position(self.pending_seek)
        self.reader.request(self.pending_seek)
        self.play.setEnabled(False)
        self.rate.setText('Loading cached frame…')

    def toggle_play(self):
        if self.playing:
            self.pause()
        elif self.pending_seek is None and not self.reader.error:
            self.buffer_target = 0 if self.position >= self.clip.count-1 else max(0, self.position)
            self.display_hz = max(1., self.screen().refreshRate() or 60.)
            self.playing = True
            self._request_frames(self.buffer_target)
            self.buffering = True
            self.clock = None
            self.paints.clear()
            self.skipped = 0
            self.view.deselect_compass()
            self._update_compass_frame(self.position)
            self.play.setText('Pause')
            self._tick()

    def pause(self):
        self.resume_after_seek = False
        self.playing = self.buffering = False
        self.buffer_target = None
        self.clock = None
        self.play.setText('Play')
        if not self.closing:
            if self.position >= 0 and self.pending_seek is None:
                self._update_frame_controls(self.position)
                self._update_compass_frame(self.position)
            self.rate.setText(f'Paused · {self.fps.value():g} fps replay · no live BOS calculation')

    def _rate_changed(self, value):
        index = self.pending_seek if self.pending_seek is not None else max(0, self.position)
        self.edit_model.set_speed(self.edit_model.speed_index(index), value)
        self.studio.refresh()
        self._studio_changed()
        if self.pending_seek is not None:
            return
        if self.playing:
            self.clock = None
            self.buffering = True
            self.buffer_target = max(0, self.position)
            self._request_frames(self.buffer_target)
            self.paints.clear()
        else:
            self.pause()

    def _tick(self):
        if self.closing:
            self._finish_close()
            return
        if self.reader.error:
            self.pause()
            self.play.setEnabled(False)
            self.rate.setText('Cached playback stopped: ' + self.reader.error)
            return
        now = time.monotonic()
        if self.pending_seek is not None:
            image = self.reader.get(self.pending_seek)
            if image is not None:
                self._present(self.pending_seek, image)
                self.pending_seek = None
                self.play.setEnabled(True)
                resume = self.resume_after_seek
                self.pause()
                if resume and self.position < self.clip.count-1:
                    self.toggle_play()
                    return
        if not self.playing:
            return
        if self.buffering:
            start = self.buffer_target if self.buffer_target is not None else max(0, self.position)
            rate = self.edit_model.rate_at(start)
            measured_rate = rate / self.clip.sampling.step
            painted_rate = min(measured_rate, self.display_hz) if rate > 240 else measured_rate
            needed = min(len(self.reader.targets), max(2, math.ceil(painted_rate*.15)))
            if self.reader.ready_count(start) < needed:
                self.rate.setText('Buffering cached frames…')
                return
            image = self.reader.get(start)
            self.skipped += max(0, start-self.position-1)
            self._present(start, image)
            self.buffering = False
            self.buffer_target = None
            self.clock = (now, self.edit_model.time_at(self.position))
        elapsed = self.clock[1] + (now-self.clock[0])
        if elapsed >= self.edit_model.duration:
            if self.loop.isChecked():
                self.buffer_target = 0
                self._request_frames(0)
                self.buffering = True
                self.clock = None
            else:
                image = self.reader.get(self.clip.count-1)
                if image is not None:
                    self._present(self.clip.count-1, image)
                    self.pause()
                else:
                    # Even after a long UI stall, ending playback must display
                    # the actual last analyzed frame, not an earlier cached one.
                    self.seek(self.clip.count-1)
            return
        target = self.edit_model.frame_at(elapsed)
        if self.fast_preview:
            target = max(self.position, self.edit_model.preview_frame(target, self.display_hz))
        if target != self.position:
            image = self.reader.get(target)
            if image is None:
                # Pause the presentation clock instead of pretending a slow
                # decode met the requested rate, or accumulating a long lag.
                self.buffer_target = target
                self._request_frames(target)
                self.buffering = True
                self.clock = None
                self.rate.setText('Buffering cached frames…')
                return
            self.skipped += max(0, target-self.position-1)
            self._present(target, image)
        if now-self._last_rate_update >= .25:
            while self.paints and now-self.paints[0] > 1.2:
                self.paints.popleft()
            actual = ((len(self.paints)-1)/(self.paints[-1]-self.paints[0]) if len(self.paints)>1 else 0.)
            rate_label = 'source fps' if self.clip.sampling.step > 1 else 'fps requested'
            self.rate.setText(f'{self.fps.value():g} {rate_label} · {actual:.1f} fps shown'
                              + (f' · {self.skipped:,} frames skipped to keep time' if self.skipped else '')
                              + ' · Cached playback')
            self._last_rate_update = now

    def export_movie(self):
        self.pause()
        fps = self.studio.output_fps()
        edit = self.edit_model.copy()
        total = edit.output_count(fps)
        title = f'Export MP4 · {total/fps:.2f} s at {fps:g} fps'
        path, _ = QFileDialog.getSaveFileName(self, title,
            str(Path.home()/'Desktop'/(Path(self.clip.manifest['source']).stem+'-BOS.mp4')), 'MP4 video (*.mp4)')
        if not path:
            return
        if not path.lower().endswith('.mp4'):
            path += '.mp4'
            if Path(path).exists() and QMessageBox.question(self, 'Replace video?',
                    f'Replace {Path(path).name}?', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        mask = None if self.overlay.isChecked() else self._raw
        titles = StudioText(self.clip, self.studio.text_settings(), self.capture_fps)
        compass = self.view.compass if self.compass_button.isChecked() else None
        vectors_enabled = self.vectors.isChecked()
        dialog = _Progress(self, title, total,
                           lambda cancel, progress: export_cached_bos(self.clip, path, fps, cancel, progress,
                                                                    overlay_mask=mask, compass=compass, studio=edit,
                                                                    vectors_enabled=vectors_enabled,
                                                                    text_renderer=titles.composite if titles.fields else None))
        self.export_worker = dialog.worker
        self.export.setEnabled(False)
        dialog.worker.start()
        dialog.exec()
        worker = dialog.worker
        self.export_worker = None
        self.export.setEnabled(True)
        if worker.error and not self.closing:
            QMessageBox.warning(self, 'BOS export', worker.error)
        elif not worker.cancelled and not self.closing:
            self.rate.setText(f'Saved {total/fps:.2f} s · {total:,} frames at {fps:g} fps to {path}')
        dialog.deleteLater()

    def keyPressEvent(self, event):
        if not isinstance(self.focusWidget(), QAbstractSpinBox):
            if event.key() == Qt.Key.Key_Space:
                self.toggle_play(); event.accept(); return
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                self.seek(self.position + (1 if event.key() == Qt.Key.Key_Right else -1))
                event.accept(); return
        super().keyPressEvent(event)

    def reject(self):
        self.close()

    def shutdown(self):
        if self.cleaned:
            return True
        self.closing = True
        self.pause()
        self.reader.stop()
        if self.export_worker is not None:
            # The queued finished signal must unwind the modal export before
            # either dialog or its owning player can be destroyed.
            if self.export_worker.isRunning():
                self.export_worker.cancel.set()
            return False
        if self.reader.is_alive():
            return False
        self.clip.close()
        self.cleaned = True
        self.timer.stop()
        return True

    def _finish_close(self):
        if self.shutdown():
            self.close()

    def closeEvent(self, event):
        if self.shutdown():
            event.accept()
        else:
            event.ignore()


def analyze_bos_range(parent, settings, first, last, *, sample_step=1):
    """Called by the BOS workspace after the user confirms an inclusive range."""
    snapshot = deepcopy(settings)
    sampling = RangeSampling(last-first+1, sample_step)
    dialog = _Progress(parent, 'Analyze BOS range', sampling.count,
                       lambda cancel, progress: prepare_bos_preview(snapshot, first, last, sample_step=sample_step,
                                                                    cancel=cancel, progress=progress))
    parent._bos_preview_worker = dialog.worker
    dialog.worker.start()
    dialog.exec()
    worker = dialog.worker
    parent._bos_preview_worker = None
    window = None
    if worker.error and not getattr(parent, 'closing', False):
        QMessageBox.warning(parent, 'Analyze BOS range', worker.error)
    elif worker.result is not None:
        if getattr(parent, 'closing', False):
            worker.result.close()
        else:
            try:
                window = BOSPreviewWindow(parent, worker.result)
                windows = getattr(parent, '_bos_preview_windows', None)
                if windows is None:
                    parent._bos_preview_windows = windows = []
                windows.append(window)
                window.destroyed.connect(lambda: windows.remove(window) if window in windows else None)
                window.show()
                parent.status.setText(f'Analyzed {window.clip.count:,} frames. The cached result is open in its own playback window.')
            except Exception as error:
                worker.result.close()
                QMessageBox.warning(parent, 'Analyze BOS range', str(error))
    dialog.deleteLater()
    return window
