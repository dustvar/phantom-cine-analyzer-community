"""Background-oriented schlieren workspace with a source-coordinate ROI and live preview."""
import math
from collections import deque
from copy import deepcopy
from pathlib import Path
import time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRegion
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QSplitter, QVBoxLayout, QWidget)
from bos_display import DIRECTION_COLORS
from bos_playback_controls import ReplaySpeed
from bos_frames import (ANCHOR_MIN_SIZE, BOSFrames, WINDOW_FINE, WINDOW_STANDARD,
                        default_bos_settings, validate_bos_settings)
from bos_processing import probe_support, reason_label
from camera_comparator_ui import ImageView
from bos_range_ui import BOSRangeBar
from bos_vector_controls import VectorAppearance
from bos_vectors import MIN_FRACTION


METHOD_HELP = {
    'optical_flow': 'Pyramidal Lucas–Kanade estimates patch motion from the dot image gradients. '
                    'Each match is checked in reverse and against the pinned reference.',
    'cross_correlation': 'Matches each reference patch at nearby shifts using normalized '
                         'cross-correlation. The peak gives the shift to subpixel precision.'
}

PROBE_HINT = 'Hover to measure a shift or see why a patch was masked.'


class RegionView(ImageView):
    regionSelected = Signal(object)
    anchorSelected = Signal(object)
    anchorSelectionCancelled = Signal()
    dragStarted = Signal()
    probeMoved = Signal(object)
    def __init__(self):
        super().__init__()
        self.roi = self.start = self.end = None
        self.anchor_roi = None
        self.vector_key_rect = None
        self.selecting_anchor = False
        self.setAccessibleName('BOS footage. Drag a rectangle around the patterned background.')
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    def _pixel(self, point):
        rect = self.image_rect()
        return QPointF(max(0., min(self.image.width(), (point.x()-rect.x())*self.image.width()/rect.width())),
                       max(0., min(self.image.height(), (point.y()-rect.y())*self.image.height()/rect.height())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.image is not None and self.image_rect().contains(event.position()):
            self.dragStarted.emit()
            self.start = self.end = self._pixel(event.position())
            self.update()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.start is not None and self.image is not None:
            self.end = self._pixel(event.position())
            self.update()
        elif self.image is not None:
            self.probeMoved.emit(self._pixel(event.position()) if self.image_rect().contains(event.position()) else None)

    def leaveEvent(self, event):
        self.probeMoved.emit(None)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.start is not None and self.image is not None:
            self.end = self._pixel(event.position())
            area = QRectF(self.start, self.end).normalized()
            self.start = self.end = None
            x, y = math.floor(area.x()), math.floor(area.y())
            if area.width() >= 1 and area.height() >= 1:
                signal = self.anchorSelected if self.selecting_anchor else self.regionSelected
                signal.emit([x, y, math.ceil(area.right())-x, math.ceil(area.bottom())-y])
            self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.start = self.end = None
            if self.selecting_anchor:
                self.selecting_anchor = False
                self.anchorSelectionCancelled.emit()
            self.update()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.image is None:
            return
        target = self.image_rect()
        w, h = target.width()/self.image.width(), target.height()/self.image.height()
        painter = QPainter(self)
        if self.vector_key_rect is not None:
            x, y, width, height = self.vector_key_rect
            key = QRectF(target.x()+x*w, target.y()+y*h, width*w, height*h)
            painter.setClipRegion(QRegion(self.rect()).subtracted(QRegion(key.toAlignedRect())))
        outlines = []
        if self.roi and not (self.start is not None and not self.selecting_anchor):
            outlines.append((QRectF(*self.roi), '#73e4e8'))
        if self.anchor_roi and not (self.start is not None and self.selecting_anchor):
            outlines.append((QRectF(*self.anchor_roi), '#f1cb64'))
        if self.start is not None:
            outlines.append((QRectF(self.start, self.end).normalized(), '#f1cb64' if self.selecting_anchor else '#73e4e8'))
        for roi, color in outlines:
            outline = QRectF(target.left()+roi.x()*w, target.top()+roi.y()*h, roi.width()*w, roi.height()*h)
            painter.setPen(QPen(QColor('#14212b'), 3))
            painter.drawRect(outline)
            painter.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.DashLine))
            painter.drawRect(outline)


class FrameJob(QThread):
    def __init__(self, parent, session, state, generation):
        super().__init__(parent)
        self.session = session
        self.state = state
        self.generation = generation
        self.result = self.error = None

    def run(self):
        started = None
        try:
            if self.session is None or self.session.path != self.state['path']:
                if self.session is not None:
                    self.session.close()
                    self.session = None
                self.session = BOSFrames(self.state['path'])
            previous_field = self.session.field
            previous_reference = self.session.reference_key
            started = time.monotonic()
            self.result = self.session.render(self.state)
            self.computed_bos = self.result['field'] is not None and self.result['field'] is not previous_field
            # Opening a large Cine and loading its first reference is one-time
            # work; multiplying that cost by the range would be misleading.
            self.estimate_sample = (self.computed_bos and previous_field is not None
                                    and self.session.reference_key == previous_reference)
        except Exception as error:
            self.error = str(error)
        finally:
            self.elapsed = time.monotonic()-started if started is not None else None


def wrapped_label(text=''):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def choice(entries):
    combo = QComboBox()
    for label, key in entries:
        combo.addItem(label, key)
    return combo


class BOSPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('bos_workspace')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet('QWidget#bos_workspace {background-color: #20242b;}')
        self.state = default_bos_settings()
        self.remembered = {}
        self.session = self.job = self.last_result = self.presented_state = None
        self.generation = 0
        self.probe_point = None
        self.pending = self.playing = self.closing = False
        self._preview_times = deque(maxlen=20)
        self._preview_fps = None
        self._last_render_seconds = None
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self._start_render)
        self.play_timer = QTimer(self)
        self.play_timer.setSingleShot(True)
        self.play_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.play_timer.timeout.connect(self._next_frame)
        self._build()
        self._restore_controls()

    def _build(self):
        root = QVBoxLayout(self)
        title_row = QHBoxLayout()
        title = QLabel('Background-oriented schlieren')
        title.setStyleSheet('font-size: 20px; font-weight: 600')
        title_row.addWidget(title)
        title_row.addStretch()
        self.source = QComboBox()
        self.source.setMinimumContentsLength(12)
        self.source.setMaximumWidth(320)
        self.source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.source.setAccessibleName('BOS source Cine')
        self.source.currentIndexChanged.connect(self._source_chosen)
        title_row.addWidget(self.source)
        browse = QPushButton('Open Cine…')
        browse.clicked.connect(self.browse)
        title_row.addWidget(browse)
        root.addLayout(title_row)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.view = RegionView()
        self.view.regionSelected.connect(self.set_roi)
        self.view.anchorSelected.connect(self.set_anchor)
        self.view.anchorSelectionCancelled.connect(self._cancel_anchor_selection)
        self.view.dragStarted.connect(self.pause)
        self.view.probeMoved.connect(self._probe)
        split.addWidget(self.view)
        side = QWidget()
        col = QVBoxLayout(side)
        col.setContentsMargins(6, 0, 3, 2)
        ref_box = QGroupBox('1   Reference')
        ref = QVBoxLayout(ref_box)
        ref.addWidget(wrapped_label('Pin a still frame. For an average, choose consecutive frames before flow begins.'))
        reference_options = QFormLayout()
        self.reference_count = choice([('1 frame', 1), ('4 frames', 4), ('8 frames', 8)])
        self.reference_count.setAccessibleName('BOS reference average')
        self.reference_count.setToolTip('Average consecutive frames starting at the pin to reduce '
                                        'reference noise. The span ends at the last available frame.')
        reference_options.addRow('Average', self.reference_count)
        ref.addLayout(reference_options)
        self.pin = QPushButton('Use displayed frame')
        self.pin.clicked.connect(self.pin_reference)
        ref.addWidget(self.pin)
        self.ref_label = wrapped_label()
        ref.addWidget(self.ref_label)
        col.addWidget(ref_box)
        region_box = QGroupBox('2   Analysis region')
        region = QVBoxLayout(region_box)
        region.addWidget(wrapped_label('Drag across the dots where the gas travels. Drag again to replace the region.'))
        self.region_label = wrapped_label()
        region.addWidget(self.region_label)
        col.addWidget(region_box)
        map_box = QGroupBox('3   Displacement map')
        form = QFormLayout(map_box)
        self.method = choice([('Optical flow', 'optical_flow'), ('Cross-correlation', 'cross_correlation')])
        self.method.setAccessibleName('BOS measurement method')
        form.addRow('Method', self.method)
        self.show_vectors = QPushButton('Show Vector Field')
        self.show_vectors.setCheckable(True)
        self.show_vectors.setAccessibleName('Show Vector Field')
        self.show_vectors.setToolTip('Show the direction and size of the apparent background-dot shift '
                                     'relative to the pinned reference. Arrows are included in Analyze Range and MP4.')
        self.show_vectors.setStyleSheet('QPushButton:checked {border: 1px solid #75baab; '
                                        'background: #29473f; color: #e8faf5;}')
        form.addRow(self.show_vectors)
        self.vector_options = QFrame()
        self.vector_options.setObjectName('bos_vector_options')
        self.vector_options.setStyleSheet('QFrame#bos_vector_options {border-left: 2px solid #508a7e;}')
        vectors = QFormLayout(self.vector_options)
        vectors.setContentsMargins(7, 0, 0, 2)
        vectors.setSpacing(4)
        self.vector_background = choice([('Arrows + map', 'map'), ('Arrows only', 'footage')])
        self.vector_background.setAccessibleName('Vector field layers')
        self.vector_background.setToolTip('Draw over the selected displacement map or over the original footage.')
        vectors.addRow('Layers', self.vector_background)
        self.vector_density = choice([('Sparse', 'sparse'), ('Balanced', 'balanced'), ('Dense', 'dense'),
                                      ('Extra Dense', 'extra_dense')])
        self.vector_density.setAccessibleName('Vector arrow density')
        self.vector_density.setToolTip('Sets the spacing of displayed arrows on a fixed grid. '
                                       f'Shifts below {MIN_FRACTION:.0%} of full scale are omitted from the drawing. '
                                       'Extra Dense adds a second lattice between Dense sites where samples exist. '
                                       'Every reliable patch is available in the CSV.')
        vectors.addRow('Density', self.vector_density)
        self.vector_appearance = VectorAppearance()
        vectors.addRow(self.vector_appearance)
        self.save_vectors = QPushButton('Save vectors…')
        self.save_vectors.setAccessibleName('Save current frame vector data as CSV')
        self.save_vectors.setToolTip('Pause to save this frame’s patch coordinates and dot shifts in source pixels as CSV.')
        self.save_vectors.clicked.connect(self._save_vectors)
        vectors.addRow(self.save_vectors)
        form.addRow(self.vector_options)
        self.detail = choice([('Fine detail', True), ('Standard', False)])
        self.detail.setAccessibleName('BOS sampling detail')
        self.detail.setToolTip('Fine detail uses smaller, closer patches to resolve narrower fronts. '
                               'Standard averages more dots per patch and can give a steadier map.')
        form.addRow('Sampling', self.detail)
        self.component = choice([('Horizontal (Δx)', 'horizontal'), ('Vertical (Δy)', 'vertical'),
                                 ('Horizontal + Vertical', 'direction'), ('Magnitude', 'magnitude')])
        self.palette = choice([('Schlieren gray', 'gray'), ('Color overlay', 'color')])
        self.limit = QDoubleSpinBox()
        self.limit.setRange(.01, 16.)
        self.limit.setDecimals(2)
        self.limit.setSuffix(' px')
        self.limit.setSingleStep(.1)
        self.limit.setKeyboardTracking(False)
        self.limit.setToolTip('Fixed displacement scale for colors and arrows throughout the clip. '
                              'Lower values magnify smaller shifts. Arrow style controls how magnitude is indicated.')
        self.opacity = QSpinBox()
        self.opacity.setRange(0, 100)
        self.opacity.setSuffix('%')
        self.opacity.setSingleStep(5)
        for label, widget in [('Component', self.component), ('Palette', self.palette),
                              ('Full scale', self.limit), ('Opacity', self.opacity)]:
            form.addRow(label, widget)
        self.reduce_noise = QCheckBox('Reduce overlay noise')
        self.reduce_noise.setToolTip('Smooth the displayed displacement using nearby valid patches in this frame. '
                                     'Hover to read the original measurement at a patch.')
        form.addRow(self.reduce_noise)
        self.soften = QCheckBox('Soften background dots')
        self.soften.setToolTip('Reduce dot contrast in the overlay. Measurements always use the source pixels.')
        form.addRow(self.soften)
        self.stabilize = QCheckBox('Stabilize background')
        self.stabilize.setToolTip('Subtract camera/background translation measured in a stationary dot patch. '
                                 'Choose dots outside the gas or plume; real whole-field flow is not removed automatically.')
        form.addRow(self.stabilize)
        anchor_row = QHBoxLayout()
        self.choose_anchor = QPushButton('Stationary patch…')
        self.choose_anchor.setAccessibleName('Choose stationary patch')
        self.choose_anchor.setCheckable(True)
        self.choose_anchor.setToolTip('Draw the yellow patch over stationary dots outside the gas or plume.')
        self.choose_anchor.toggled.connect(self._choose_anchor)
        self.clear_anchor = QPushButton('Clear')
        self.clear_anchor.clicked.connect(self._clear_anchor)
        anchor_row.addWidget(self.choose_anchor, 1)
        anchor_row.addWidget(self.clear_anchor)
        form.addRow(anchor_row)
        self.anchor_label = wrapped_label('No stationary patch chosen')
        form.addRow(self.anchor_label)
        self.legend = wrapped_label()
        form.addRow(self.legend)
        self.quality = wrapped_label('Coverage —')
        self.probe_label = wrapped_label(PROBE_HINT)
        self.probe_label.setToolTip('Hover over an arrow to read the measurement at its grid site. '
                                    'Positive Δx points right and positive Δy points down in the Cine. '
                                    '|Δ| is the apparent background-dot shift in source pixels.')
        col.addWidget(map_box)
        advanced = QPushButton('Matching settings ▸')
        advanced.setCheckable(True)
        col.addWidget(advanced)
        settings = QWidget()
        params = QFormLayout(settings)
        params.setContentsMargins(0, 1, 0, 2)
        self.window = choice([(f'{v} × {v} px', v) for v in range(9, 62, 2)])
        self.search = QSpinBox()
        self.search.setRange(2, 16)
        self.search.setPrefix('±')
        self.search.setSuffix(' px')
        self.method_hint = wrapped_label()
        params.addRow(self.method_hint)
        params.addRow('Window', self.window)
        params.addRow('Search', self.search)
        params.addRow(wrapped_label('Smaller windows can resolve a thinner front but contain fewer dots. Search limits the shift in either direction. Hover over a gap to inspect the nearest patch.'))
        col.addWidget(settings)
        settings.hide()
        advanced.toggled.connect(settings.setVisible)
        advanced.toggled.connect(lambda on: advanced.setText('Matching settings ▾' if on else 'Matching settings ▸'))
        self.save_image = QPushButton('Save frame…')
        self.save_image.clicked.connect(self._save_image)
        self.export = QPushButton('Export clip…')
        self.export.clicked.connect(self._export_clip)
        col.addStretch()
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(side)
        scroll.setMinimumWidth(230)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setSizes([900, 258])
        root.addWidget(split, 1)
        data_row = QHBoxLayout()
        self.analyze_range = QPushButton('Analyze range…')
        self.analyze_range.setCheckable(True)
        self.analyze_range.setStyleSheet('QPushButton {background: #695477; border: 1px solid #ba8dcb; font-weight: 600; padding: 7px 12px;} QPushButton:disabled {background: #303640; color: #77818f; border-color: #45505d;}')
        self.analyze_range.setToolTip('Calculate every frame in a selected range once, then watch the prepared result without live BOS calculations.')
        self.analyze_range.toggled.connect(self._toggle_range)
        data_row.addWidget(self.analyze_range)
        data_row.addWidget(self.probe_label, 2)
        data_row.addWidget(self.quality, 1)
        data_row.addWidget(self.save_image)
        data_row.addWidget(self.export)
        root.addLayout(data_row)
        self.range_bar = BOSRangeBar(self)
        self.range_bar.analyzeRequested.connect(self._analyze_selected_range)
        self.range_bar.cancelled.connect(lambda: self.analyze_range.setChecked(False))
        root.addWidget(self.range_bar)
        self.range_bar.hide()
        transport = QHBoxLayout()
        self.previous = QPushButton('‹')
        self.previous.setAccessibleName('Previous BOS frame')
        self.previous.clicked.connect(lambda: self.step(-1))
        self.play = QPushButton('Play')
        self.play.clicked.connect(self.toggle_play)
        self.following = QPushButton('›')
        self.following.setAccessibleName('Next BOS frame')
        self.following.clicked.connect(lambda: self.step(1))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setAccessibleName('BOS timeline')
        self.slider.valueChanged.connect(self.seek)
        self.frame_number = QSpinBox()
        self.frame_number.setRange(0, 0)
        self.frame_number.setPrefix('Frame ')
        self.frame_number.setKeyboardTracking(False)
        self.frame_number.setMinimumWidth(145)
        self.frame_number.valueChanged.connect(self._seek_native)
        self.speed_control = ReplaySpeed()
        self.fps = self.speed_control.number
        self.fps.setDecimals(2)
        self.fps.setKeyboardTracking(False)
        self.fps.setSuffix(' fps')
        self.fps.setAccessibleName('BOS replay speed')
        self.fps.setToolTip('Source frames per second. Above 240 fps, live playback skips ahead to keep time. '
                            'Analyze range prepares frames first for playback without BOS computation.')
        for widget in (self.previous, self.play, self.following, self.slider, self.frame_number, self.speed_control):
            transport.addWidget(widget, int(widget is self.slider))
        for button in (self.previous, self.following):
            button.setStyleSheet('min-width: 0; padding: 2px;')
            button.setFixedWidth(36)
        self.play.setStyleSheet('min-width: 0; padding: 2px;')
        self.play.setFixedWidth(70)
        self.preview_rate = QLabel('Live preview')
        self.preview_rate.setToolTip('Live preview calculates BOS while you watch. Analyze range prepares a clip first for smoother playback.')
        transport.addWidget(self.preview_rate)
        root.addLayout(transport)
        self.status = wrapped_label()
        root.addWidget(self.status)
        for ctrl in (self.method, self.component, self.palette, self.window, self.vector_background, self.vector_density):
            ctrl.currentIndexChanged.connect(self._style_changed)
        for ctrl in (self.opacity, self.limit, self.search):
            ctrl.valueChanged.connect(self._style_changed)
        self.reference_count.currentIndexChanged.connect(self._style_changed)
        self.detail.currentIndexChanged.connect(self._sampling_changed)
        self.soften.toggled.connect(self._style_changed)
        self.reduce_noise.toggled.connect(self._style_changed)
        self.stabilize.toggled.connect(self._style_changed)
        self.show_vectors.toggled.connect(self._style_changed)
        self.vector_appearance.changed.connect(self._style_changed)
        self.fps.valueChanged.connect(self._rate_changed)
        self._clear_image()

    def settings(self):
        return deepcopy(self.state)

    def set_sources(self, paths, active_path=None, position=None):
        paths = list(dict.fromkeys(str(Path(p).resolve()) for p in paths if p))
        selected = str(Path(active_path).resolve()) if active_path else None
        current = self.state['path']
        if current and current not in paths:
            paths.append(current)
        with QSignalBlocker(self.source):
            self.source.clear()
            for path in paths:
                self.source.addItem(Path(path).name, path)
            target = selected if selected in paths else current if current in paths else next(iter(paths), '')
            self.source.setCurrentIndex(self.source.findData(target))
        if target != current:
            self._switch_source(target, position)
        elif current and self.last_result is None and not self.job:
            self._request_render()

    def apply_settings(self, settings):
        state = validate_bos_settings(settings)
        self.pause()
        self.remembered.clear()
        self._clear_image()
        self.state = state
        with QSignalBlocker(self.source):
            self.source.clear()
            if state['path']:
                self.state['path'] = str(Path(state['path']).resolve())
                self.source.addItem(Path(self.state['path']).name, self.state['path'])
        self._restore_controls()
        self._request_render()

    def _restore_controls(self):
        for key, combo in [('method', self.method), ('reference_count', self.reference_count), ('detail', self.detail), ('component', self.component),
                           ('palette', self.palette), ('window', self.window),
                           ('vector_background', self.vector_background), ('vector_density', self.vector_density)]:
            with QSignalBlocker(combo):
                combo.setCurrentIndex(combo.findData(self.state[key]))
        for control, val in [(self.limit, self.state['limit']), (self.opacity, round(self.state['opacity']*100)),
                             (self.search, self.state['search']), (self.fps, self.state['playback_fps'])]:
            with QSignalBlocker(control):
                control.setValue(val)
        self.speed_control.sync_slider()
        with QSignalBlocker(self.reduce_noise):
            self.reduce_noise.setChecked(self.state['reduce_noise'])
        with QSignalBlocker(self.soften):
            self.soften.setChecked(self.state['soften'])
        with QSignalBlocker(self.stabilize):
            self.stabilize.setChecked(self.state.get('motion_compensation', False))
        with QSignalBlocker(self.show_vectors):
            self.show_vectors.setChecked(self.state['show_vectors'])
        self.vector_appearance.set_settings(self.state)
        self.view.roi = self.state['roi']
        self.view.anchor_roi = self.state.get('anchor_roi')
        self._labels()

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open footage for BOS', self.state['path'], 'Phantom Cine (*.cine *.CINE)')
        if path:
            self.set_sources([self.source.itemData(i) for i in range(self.source.count())] + [path], path)

    def _source_chosen(self):
        self._switch_source(self.source.currentData() or '')

    def _switch_source(self, path, position=None):
        if self.state['path']:
            self.remembered[self.state['path']] = self.settings()
        self.pause()
        self._clear_image()
        if path in self.remembered:
            self.state = self.remembered[path]
        else:
            self.state = dict(default_bos_settings(), path=path, position=max(0, int(position or 0)))
        self._restore_controls()
        self._request_render()

    def _clear_image(self):
        self.last_result = self.presented_state = None
        self.probe_point = None
        self.view.start = self.view.end = self.view.roi = self.view.vector_key_rect = None
        self.view.anchor_roi = None
        self._cancel_anchor_selection()
        with QSignalBlocker(self.analyze_range):
            self.analyze_range.setChecked(False)
        self.range_bar.hide()
        self.range_bar.count = 0
        self._preview_times.clear()
        self._preview_fps = self._last_render_seconds = None
        self._update_preview_rate()
        self.view.image = None
        self.view.message = 'Choose a Cine to inspect the background pattern.'
        self.view.update()

    def _sampling_changed(self):
        window = WINDOW_FINE if self.detail.currentData() else WINDOW_STANDARD
        with QSignalBlocker(self.window):
            self.window.setCurrentIndex(self.window.findData(window))
        self._style_changed()

    def _style_changed(self):
        self.pause()
        if self.component.currentData() == 'direction':
            with QSignalBlocker(self.palette):
                self.palette.setCurrentIndex(self.palette.findData('color'))
        self.state.update(method=self.method.currentData(), reference_count=self.reference_count.currentData(),
                          detail=self.detail.currentData(), show_vectors=self.show_vectors.isChecked(),
                          vector_background=self.vector_background.currentData(), vector_density=self.vector_density.currentData(),
                          component=self.component.currentData(), palette=self.palette.currentData(),
                          window=self.window.currentData(), search=self.search.value(),
                          limit=self.limit.value(), opacity=self.opacity.value()/100, soften=self.soften.isChecked(),
                          reduce_noise=self.reduce_noise.isChecked(), motion_compensation=self.stabilize.isChecked(),
                          **self.vector_appearance.settings())
        self._labels()
        self._request_render()

    def _rate_changed(self, rate):
        self.state['playback_fps'] = rate
        if self.playing:
            self._reset_live_clock()
            self._preview_times.clear()
            self._preview_fps = None
        self._update_preview_rate()

    def _update_preview_rate(self):
        target = self.state['playback_fps']
        if self._preview_fps is None:
            self.preview_rate.setText('Live preview')
        else:
            self.preview_rate.setText(f'Live {self._preview_fps:.1f} fps')
        self.preview_rate.setToolTip(f'Requested {target:g} fps. The measured live rate includes disk reads, '
                                    'matching and drawing. Analyze range prepares every frame first; '
                                    'its playback does no BOS calculations.')

    def _labels(self):
        roi = self.state['roi']
        self.region_label.setText(f'{roi[2]} × {roi[3]} px at ({roi[0]}, {roi[1]})' if roi else 'No region drawn')
        anchor = self.state.get('anchor_roi')
        stabilization = self.state.get('motion_compensation', False)
        self.clear_anchor.setEnabled(bool(anchor))
        self.choose_anchor.setEnabled(self.last_result is not None)
        self.view.anchor_roi = anchor if stabilization or self.view.selecting_anchor else None
        if anchor:
            text = f'Yellow patch: {anchor[2]} × {anchor[3]} px at ({anchor[0]}, {anchor[1]})'
            drift = self.last_result.get('drift') if self.last_result and stabilization else None
            if drift and drift.get('reliable'):
                text += f'\nRemoved translation: {drift["dx"]:+.3f}, {drift["dy"]:+.3f} px'
            elif stabilization and drift:
                text += '\nCorrection unavailable on this frame; original footage is shown.'
            elif not stabilization:
                text += '\nStabilization is off.'
            self.anchor_label.setText(text)
        else:
            self.anchor_label.setText('Choose stationary dots outside the gas or plume. The yellow patch measures camera movement.' if stabilization
                                      else 'Optional: use stationary dots outside the gas or plume to remove camera movement.')
        ready = self.last_result is not None
        ref = self.state['reference']
        if ref is None or not ready:
            self.ref_label.setText('No reference pinned')
        else:
            first = ref + self.last_result['first']
            requested = self.state['reference_count']
            count = min(requested, self.last_result['count'] - ref)
            if requested == 1:
                self.ref_label.setText(f'Pinned frame {first:,}')
            else:
                span = f'{first:,} to {first + count - 1:,}' if count > 1 else f'{first:,}'
                summary = f'{count} of {requested} frames' if count < requested else f'{count} frames'
                self.ref_label.setText(f'Reference {span} ({summary})')
        hint = METHOD_HELP[self.state['method']]
        self.method.setToolTip(hint)
        self.method_hint.setText(hint)
        signed = self.state['component'] != 'magnitude'
        scale = self.state['limit']
        directional = self.state['component'] == 'direction'
        vectors = self.state['show_vectors']
        map_visible = not vectors or self.state['vector_background'] == 'map'
        self.vector_options.setVisible(vectors)
        self.component.setEnabled(map_visible)
        self.palette.setEnabled(map_visible and not directional)
        self.opacity.setEnabled(map_visible)
        self.reduce_noise.setEnabled(map_visible)
        self.soften.setEnabled(map_visible)
        if not map_visible:
            self.legend.setText('Arrow direction follows the dot shift. The on-image scale gives its length in source pixels.')
        elif directional:
            arrows = ('→', '↘', '↓', '↙', '←', '↖', '↑', '↗')
            def swatch(index):
                return f'<b style="color:{DIRECTION_COLORS[index]};font-size:19px">{arrows[index]}</b>'
            rows = ((5, 6, 7), (4, None, 0), (3, 2, 1))
            cells = ''.join('<tr>' + ''.join('<td align="center">' + (swatch(i) if i is not None else '·')
                                             + '</td>' for i in row) + '</tr>' for row in rows)
            self.legend.setText(f'<table cellspacing="2">{cells}</table>Hue shows shift direction. '
                                f'Color reaches full strength at {scale:g} px.')
        elif signed:
            colors = ('Bright', 'dark') if self.state['palette'] == 'gray' else ('Blue', 'orange')
            direction = 'right' if self.state['component'] == 'horizontal' else 'down'
            self.legend.setText(f'{colors[0]} −{scale:g} px   ·   {colors[1]} +{scale:g} px\nPositive is {direction}.')
        else:
            color = 'bright' if self.state['palette'] == 'gray' else 'red'
            self.legend.setText(f'0 to {scale:g} px total shift   ·   {color} at full scale')
        for w in (self.previous, self.following, self.play, self.frame_number, self.slider):
            w.setEnabled(ready)
        self.pin.setEnabled(ready and self.last_result['index'] == self.state['position'] and not self.pending)
        self.export.setEnabled(ready and self.last_result['field'] is not None and not self.pending)
        self.save_image.setEnabled(ready and not self.pending)
        preview_worker = getattr(self, '_bos_preview_worker', None)
        preparing = preview_worker is not None
        self.analyze_range.setEnabled(ready and not preparing)
        settled = ready and self.job is None and not self.pending and not self.playing and not preparing
        matched_state = (settled and self.presented_state is not None
                         and all(self.presented_state.get(key) == value for key, value in self.state.items()
                                 if key != 'playback_fps'))
        visible_field = self.last_result.get('display_field') if matched_state and vectors else None
        self.save_vectors.setEnabled(visible_field is not None and bool(visible_field.valid.any()))
        can_analyze = matched_state and self.last_result['field'] is not None and (not stabilization or bool(anchor))
        if not ready:
            blocked = 'Open a Cine first.'
        elif self.playing:
            blocked = 'Pause on a frame to mark it or start analysis.'
        elif not settled:
            blocked = 'Waiting for the displayed frame to finish matching…'
        elif stabilization and not anchor:
            blocked = 'Choose a stationary patch for stabilization, or turn stabilization off.'
        elif self.last_result['field'] is None:
            blocked = 'Pin a no-flow reference and draw the analysis region first.'
        else:
            blocked = ''
        self.range_bar.update_ready(self.last_result['index'] if ready else 0,
            can_mark=settled, can_analyze=can_analyze, reason=blocked, seconds_per_frame=self._last_render_seconds)
        if not ready:
            self.status.setText('Opening Cine…' if self.state['path'] else 'Open a Cine or select one from the source menu.')
            self.quality.setText('Coverage —')
            self.quality.setToolTip('')
            self.probe_label.setText(PROBE_HINT)

    def _toggle_range(self, enabled):
        self.pause()
        if enabled and self.last_result is not None:
            if not self.range_bar.count:
                self.range_bar.configure(self.last_result['first'], self.last_result['count'], self.last_result['index'])
            self.range_bar.show()
        else:
            self.range_bar.hide()
        self._labels()

    def _analyze_selected_range(self, first, last):
        self.pause()
        self._labels()
        if not self.range_bar.analyze.isEnabled():
            return
        if not 0 <= first <= last < self.last_result['count']:
            return
        from bos_preview_ui import analyze_bos_range
        try:
            analyze_bos_range(self, self.settings(), first, last)
        finally:
            self._labels()

    def _choose_anchor(self, enabled):
        self.pause()
        self.view.selecting_anchor = bool(enabled)
        self.view.start = self.view.end = None
        self.view.anchor_roi = self.state.get('anchor_roi') if enabled or self.state.get('motion_compensation') else None
        self.view.update()
        if enabled:
            self.status.setText(f'Draw a yellow patch over stationary dots outside the gas or plume (at least {ANCHOR_MIN_SIZE} × {ANCHOR_MIN_SIZE} pixels). Escape cancels.')
            self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        elif self.last_result:
            self._present(self.last_result)

    def _cancel_anchor_selection(self):
        self.view.selecting_anchor = False
        self.view.start = self.view.end = None
        self.view.anchor_roi = self.state.get('anchor_roi') if self.state.get('motion_compensation') else None
        with QSignalBlocker(self.choose_anchor):
            self.choose_anchor.setChecked(False)
        self.view.update()

    def _clear_anchor(self):
        self.pause()
        self._cancel_anchor_selection()
        self.state['anchor_roi'] = None
        self.state['motion_compensation'] = False
        with QSignalBlocker(self.stabilize):
            self.stabilize.setChecked(False)
        self.view.anchor_roi = None
        self._request_render()

    def set_anchor(self, roi):
        if min(roi[2:]) < ANCHOR_MIN_SIZE:
            self.status.setText(f'Draw a stationary patch at least {ANCHOR_MIN_SIZE} × {ANCHOR_MIN_SIZE} source pixels, outside the plume.')
            return
        self.pause()
        self.state['anchor_roi'] = list(roi)
        self.state['motion_compensation'] = True
        self._cancel_anchor_selection()
        with QSignalBlocker(self.stabilize):
            self.stabilize.setChecked(True)
        self.view.anchor_roi = list(roi)
        self.view.update()
        self._request_render()

    def pin_reference(self):
        if self.last_result and self.last_result['index'] == self.state['position']:
            self.pause()
            self.state['reference'] = self.last_result['index']
            self._request_render()

    def set_roi(self, roi):
        extent = self.state['window'] + 2*self.state['search']
        if min(roi[2:]) < extent:
            self.status.setText(f'Draw a larger region, at least {extent} × {extent} source pixels.')
            return
        self.pause()
        self.state['roi'] = list(roi)
        self.view.roi = roi
        self.view.update()
        self._request_render()

    def _seek_native(self, frame):
        if self.last_result:
            self.seek(frame - self.last_result['first'])

    def seek(self, index):
        self.pause()
        if self.last_result and 0 <= index < self.last_result['count']:
            self.state['position'] = int(index)
            self._request_render()

    def step(self, amount):
        if self.last_result:
            self.seek(max(0, min(self.last_result['count']-1, self.state['position']+amount)))

    def _request_render(self):
        self.generation += 1
        self.pending = bool(self.state['path'])
        self._labels()
        if not self.state['path'] and not self.job:
            self._close_session()
        if self.isVisible() and self.pending and not self.closing:
            self.render_timer.start(0)

    def _start_render(self):
        if not self.pending or self.job or not self.isVisible() or self.closing:
            return
        self.pending = False
        self.job = FrameJob(self, self.session, self.settings(), self.generation)
        self.session = None
        self.job.finished.connect(self._finished)
        self.job.start()

    def _finished(self):
        job, self.job = self.job, None
        self.session = job.session
        if self.closing or not self.isVisible() or self.state['path'] == '':
            self._close_session()
        elif self.pending:
            self._start_render()
        elif job.generation == self.generation:
            if job.error:
                self.pause()
                # Keep a good displayed frame seekable if a later disk read
                # fails. A newly selected source has no previous frame to keep.
                if self.presented_state is None:
                    self._clear_image()
                    self._labels()
                    self.view.message = job.error
                    self.view.update()
                else:
                    self.state = self.presented_state.copy()
                    self._restore_controls()
                    self._present(self.last_result)
                self.status.setText(job.error)
            else:
                if getattr(job, 'estimate_sample', False):
                    self._last_render_seconds = job.elapsed
                self.presented_state = job.state
                self._present(job.result)
        job.deleteLater()
        self._labels()

    def _present(self, result):
        self.last_result = result
        self.state['position'] = result['index']
        self.view.vector_key_rect = result.get('vector_key_rect')
        self.view.set_frame(result['image'])
        first, count, index = result['first'], result['count'], result['index']
        with QSignalBlocker(self.slider):
            self.slider.setRange(0, count-1)
            self.slider.setValue(index)
        with QSignalBlocker(self.frame_number):
            self.frame_number.setRange(first, first+count-1)
            self.frame_number.setValue(first+index)
        field = result['field']
        if field is not None:
            self.quality.setText(f'{100*np.mean(field.valid):.0f}% matched · {field.step} px spacing')
            self.quality.setToolTip(f'Compared {field.valid.size:,} dot patches using '
                                    f'{self.state["window"]} × {self.state["window"]} source pixels per patch. '
                                    'Where a match is unreliable, the original image shows through.')
        else:
            self.quality.setText('Coverage —')
            self.quality.setToolTip('')
        if result['warning']:
            message = result['warning']
        elif result['reference'] is None:
            message = 'Find a no-flow frame and click Use displayed frame.'
        elif not self.state['roi']:
            message = 'Drag a rectangle around the patterned background.'
        elif self.state['reference_count'] > 1 and result['reference'] <= index < result['reference'] + self.state['reference_count']:
            message = 'This frame is part of the reference average. Step or play into the flow.'
        elif result['reference'] == index:
            message = 'This is the pinned reference. Step or play into the blast to see displacement.'
        elif field is not None and not np.any(field.valid):
            message = 'No reliable dot matches. Try a more textured region or a closer reference.'
        else:
            message = ('Apparent dot displacement relative to the reference average.' if self.state['reference_count'] > 1
                       else 'Apparent dot displacement relative to the pinned frame.')
        if result['time_us'] is not None:
            message = f't = {result["time_us"]:+,.2f} µs from trigger   ·   {message}'
        if self.view.selecting_anchor:
            message = f'Draw a yellow patch over stationary dots outside the gas or plume (at least {ANCHOR_MIN_SIZE} × {ANCHOR_MIN_SIZE} pixels). Escape cancels.'
        self.status.setText(message)
        self._probe(self.probe_point)
        self._labels()
        if self.playing:
            now = time.monotonic()
            self._preview_times.append(now)
            if len(self._preview_times) > 1:
                self._preview_fps = (len(self._preview_times)-1)/(self._preview_times[-1]-self._preview_times[0])
            self._update_preview_rate()
            if index >= count-1:
                self.pause()
            else:
                wait = max(0, self.play_started + 1/self.state['playback_fps'] - time.monotonic())
                self.play_timer.start(round(1000*wait))

    def _probe(self, point):
        self.probe_point = point
        field = self.last_result['field'] if self.last_result and point is not None else None
        if field is None:
            self.probe_label.setText(PROBE_HINT)
            return
        x, y = math.floor(point.x()), math.floor(point.y())
        key = self.last_result.get('vector_key_rect')
        if key and QRectF(*key).contains(point):
            scale = self.presented_state['limit']
            encoding = self.presented_state.get('vector_encoding', 'double_head')
            explanation = {'double_head': 'Double tips exceed it.', 'threshold': 'The second color exceeds it.',
                           'grayscale': 'White is at or above it.'}[encoding]
            self.probe_label.setText(f'Arrow scale {scale:g} px · {explanation}\n'
                                     f'Shifts below {scale*MIN_FRACTION:.3g} px are hidden.')
            return
        samples = self.last_result.get('vector_samples')
        arrow = samples.nearest(x, y) if samples is not None and QRectF(*field.roi).contains(point) else None
        if arrow is None:
            support = probe_support(field, x, y)
            if support == 'outside':
                if QRectF(*field.roi).contains(point):
                    self.probe_label.setText('Region edge · a full patch and its search area must fit inside.')
                else:
                    self.probe_label.setText('Move inside the analysis region.')
                return
            half = field.step // 2
            col = math.floor((x - field.x[0] + half) / field.step)
            row = math.floor((y - field.y[0] + half) / field.step)
        else:
            row, col = arrow
            support = 'measured'
        kind = 'Arrow' if arrow is not None else 'Nearest patch'
        coords = f'{kind} ({int(field.x[col])}, {int(field.y[row])})'
        if not field.valid[row, col]:
            self.probe_label.setText(f'{coords}  ·  {reason_label(field.reason[row, col])}')
            return
        if support == 'weak' and not (self.presented_state or {}).get('show_vectors'):
            self.probe_label.setText('Overlay gap · too few surrounding patches matched.')
            return
        dx, dy = float(field.dx[row, col]), float(field.dy[row, col])
        if (self.presented_state or {}).get('show_vectors'):
            displayed = self.last_result.get('display_field')
            if displayed is None or not displayed.valid[row, col]:
                self.probe_label.setText(f'{coords} · Correction unavailable · Raw Δx {dx:+.3f} px, Δy {dy:+.3f} px')
                return
            vx, vy = float(displayed.dx[row, col]), float(displayed.dy[row, col])
            label = 'Stabilized ' if self.presented_state.get('motion_compensation') else ''
            self.probe_label.setText(f'{coords} · |Δ| {math.hypot(vx, vy):.3f} px\n'
                                     f'{label}Δx {vx:+.3f} px   Δy {vy:+.3f} px')
            return
        if self.presented_state and self.presented_state.get('motion_compensation'):
            corrected = self.last_result.get('display_field')
            raw_text = f'Raw Δx {dx:+.3f} px, Δy {dy:+.3f} px'
            if corrected is not None and corrected.valid[row, col]:
                cx, cy = float(corrected.dx[row,col]), float(corrected.dy[row,col])
                self.probe_label.setText(f'{coords} · Stabilized Δx {cx:+.3f} px, Δy {cy:+.3f} px · {raw_text}')
            else:
                self.probe_label.setText(f'{coords} · Correction unavailable · {raw_text}')
        else:
            measured = 'Measured ' if self.presented_state and self.presented_state.get('reduce_noise') else ''
            self.probe_label.setText(f'{coords}  ·  {measured}Δx {dx:+.2f} px   Δy {dy:+.2f} px')

    def toggle_play(self):
        if self.playing:
            self.pause()
        elif self.last_result:
            self.playing = True
            self._reset_live_clock()
            self._preview_times.clear()
            self._preview_fps = None
            self._update_preview_rate()
            self.play.setText('Pause')
            self._next_frame()

    def _reset_live_clock(self):
        self.live_clock = time.monotonic()
        self.live_origin = self.last_result['index']
        if self.live_origin == self.last_result['count']-1:
            self.live_origin = 0

    def _next_frame(self):
        if self.playing and self.last_result:
            self.play_started = time.monotonic()
            count = self.last_result['count']
            target = (self.last_result['index']+1) % count
            rate = self.state['playback_fps']
            if rate > 240:
                # Process the frame due now; Analyze Range still measures each source frame.
                scheduled = self.live_origin + int((self.play_started-self.live_clock)*rate)
                target = min(count-1, max(target, scheduled))
            self.state['position'] = target
            self._request_render()

    def pause(self):
        if self.playing:
            self.playing = False
            self.play_timer.stop()
            self.play.setText('Play')
            # Discard any future playhead queued before Pause.
            self.generation += 1
            self.pending = False
            self.render_timer.stop()
            if self.last_result:
                self.state['position'] = self.last_result['index']
                self._labels()

    def _save_vectors(self):
        if not self.save_vectors.isEnabled():
            return
        from bos_vector_data import save_vector_csv

        result = self.last_result
        state = self.settings()
        source = Path(state['path'])
        frame = result['first'] + result['index']
        default = source.with_name(f'{source.stem}-vectors-{frame}.csv')
        picker = QFileDialog(self, 'Save BOS vector data')
        picker.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        picker.setNameFilter('CSV data (*.csv)')
        picker.setDefaultSuffix('csv')
        picker.selectFile(str(default))
        if picker.exec() != QFileDialog.DialogCode.Accepted:
            return
        path = Path(picker.selectedFiles()[0])
        try:
            save_vector_csv(path, result, state)
        except (OSError, ValueError) as error:
            self.status.setText(f'Could not save vector data: {error}')
            return
        self.status.setText(f'Saved vector data for Cine frame {frame:,} to {path.name}.')

    def _save_image(self):
        if self.last_result:
            self.pause()
            default = str(Path(self.state['path']).with_name(
                f'{Path(self.state["path"]).stem}-BOS-{self.last_result["first"] + self.last_result["index"]}.png'))
            name, _ = QFileDialog.getSaveFileName(self, 'Save BOS frame', default, 'PNG image (*.png)')
            if name and not self.view.image.save(name, 'PNG'):
                self.status.setText('Could not save the frame to that location.')

    def _export_clip(self):
        self.pause()
        if self.last_result and self.last_result['field'] is not None:
            from bos_export import export_bos_dialog
            export_bos_dialog(self, self.settings(), self.last_result['first'], self.last_result['count'])

    def _close_session(self):
        if self.session:
            self.session.close()
            self.session = None

    def busy(self):
        preview_worker = getattr(self, '_bos_preview_worker', None)
        # The modal preparation dialog still owns the thread after it stops,
        # until exec() unwinds and its caller clears this sentinel.
        return self.job is not None or preview_worker is not None

    def shutdown(self):
        self.closing = True
        self.pause()
        self.pending = False
        self.render_timer.stop()
        windows_ready = True
        for window in list(getattr(self, '_bos_preview_windows', [])):
            if not window.shutdown():
                windows_ready = False
        export_worker = getattr(self, '_bos_export_worker', None)
        if export_worker is not None:
            if export_worker.isRunning():
                export_worker.cancel.set()
            return False
        preview_worker = getattr(self, '_bos_preview_worker', None)
        if preview_worker is not None:
            if preview_worker.isRunning():
                preview_worker.cancel.set()
            return False
        if self.job:
            return False
        self._close_session()
        return windows_ready

    def showEvent(self, event):
        super().showEvent(event)
        self.closing = False
        self._request_render()

    def hideEvent(self, event):
        self.pause()
        self.generation += 1
        self.render_timer.stop()
        if not self.job:
            self._close_session()
        super().hideEvent(event)
