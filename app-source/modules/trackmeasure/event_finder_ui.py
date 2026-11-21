"""Find and review short changes in a recorded Cine."""
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal, QSignalBlocker
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QProgressBar, QPushButton, QSlider,
                               QSpinBox, QSplitter, QStyle, QStyleOptionSlider, QTableView,
                               QVBoxLayout, QWidget)

from event_region_view import EventRegionView
from event_detection import Event, sample_count
from event_finder_ui_state import validate_event_settings
from event_finder_ui_worker import RecordingWorker


class _Candidates(QAbstractTableModel):
    headers = ('Peak frame', 'First', 'Last')

    def __init__(self, parent):
        super().__init__(parent)
        self.events = ()
        self.first = 0
        self.sampled = False

    def replace(self, events, first):
        self.beginResetModel()
        self.events, self.first = events, first
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.events)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return ('Sampled peak', 'First seen', 'Last seen')[section] if self.sampled else self.headers[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        event = self.events[index.row()]
        if role == Qt.DisplayRole:
            native = (event.peak, event.first, event.last)[index.column()] + self.first
            return f'{native:,}'
        if role == Qt.ToolTipRole:
            return f'{event.reason} · Relative change score {event.score:.1f}'
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignCenter)
        return None


class _EventSlider(QSlider):
    """Mark candidate peaks below the seek bar, including single-frame events."""
    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.setMinimumHeight(26)
        self.peaks = ()
        self.tick_width = None
        self.selected = None

    def set_peaks(self, events):
        self.peaks = tuple(e.peak for e in events)
        self.tick_width = None
        self.selected = None
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.peaks:
            return
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        gr = self.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderGroove, self)
        handle = self.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self)
        span = max(0, gr.width()-handle.width())
        start = gr.left()+handle.width()//2
        def column(index):
            return start + QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), index, span, option.upsideDown)
        key = (start, span, self.maximum(), option.upsideDown)
        if key != self.tick_width:
            self.tick_columns = {column(i) for i in self.peaks}
            self.tick_width = key
        painter = QPainter(self)
        y = self.height()-2
        painter.setPen(QColor('#79c9cf'))
        for x in self.tick_columns:
            painter.drawLine(x, y-3, x, y)
        if self.selected is not None:
            painter.setPen(QColor('#f5ce80'))
            x = column(self.selected)
            painter.drawLine(x, y-5, x, y)


class EventFinderPanel(QWidget):
    openInViewer = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('eventFinder')
        self.current = self.info = self.worker = self.picker = None
        self.position = self.ticket = 0
        self.roi = self.restore = None
        self.activity = ''
        self.scanned = self.cancelling = self.closing = False
        self._rate_start = None
        self._active_file = ''
        self._restored_selection = False
        self._build()
        self._select(None, 0)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout()
        title = QLabel('Event Finder')
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        top.addWidget(title)
        top.addStretch()
        self.source = QComboBox()
        self.source.setAccessibleName('Event Finder recording')
        self.source.setMinimumContentsLength(12)
        self.source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.source.currentIndexChanged.connect(self._source_changed)
        top.addWidget(self.source)
        self.browse = QPushButton('Open Cine…')
        self.browse.clicked.connect(self._browse)
        top.addWidget(self.browse)
        self.camera = QPushButton('Camera recordings…')
        self.camera.clicked.connect(self._camera_picker)
        top.addWidget(self.camera)
        layout.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        video = QVBoxLayout(left)
        video.setContentsMargins(0, 0, 0, 0)
        self.view = EventRegionView()
        self.view.setAccessibleName('Event Finder footage. Drag a rectangle around the area to inspect.')
        self.view.regionSelected.connect(self.set_roi)
        video.addWidget(self.view, 1)
        nav = QHBoxLayout()
        self.prev = QPushButton('‹')
        self.prev.setAccessibleName('Previous Cine frame')
        self.prev.clicked.connect(lambda: self.seek(self.position-1))
        self.next = QPushButton('›')
        self.next.setAccessibleName('Next Cine frame')
        self.next.clicked.connect(lambda: self.seek(self.position+1))
        self.slider = _EventSlider()
        self.slider.setAccessibleName('Event Finder frame. Marks indicate candidate peaks.')
        self.slider.valueChanged.connect(self.seek)
        self.native = QSpinBox()
        self.native.setAccessibleName('Native Cine frame')
        self.native.setKeyboardTracking(False)
        self.native.setPrefix('Frame ')
        self.native.setGroupSeparatorShown(True)
        self.native.valueChanged.connect(self._native_seek)
        for button in (self.prev, self.next):
            button.setProperty('analysisStep', True)
            button.setToolTip(button.accessibleName())
            nav.addWidget(button)
        nav.addWidget(self.slider, 1)
        nav.addWidget(self.native)
        video.addLayout(nav)
        split.addWidget(left)

        inspector = QFrame()
        inspector.setObjectName('eventFinderInspector')
        side = QVBoxLayout(inspector)
        self.region_label = QLabel()
        self.region_label.setWordWrap(True)
        region = QHBoxLayout()
        self.shape = QComboBox()
        self.shape.setAccessibleName('Analysis region shape')
        self.shape.addItem('Rectangle', 'rectangle')
        self.shape.addItem('4 × 4 point', 'point')
        self.shape.currentIndexChanged.connect(self._shape_changed)
        region.addWidget(QLabel('Region'))
        region.addWidget(self.shape, 1)
        self.full = QPushButton('Whole frame')
        self.full.clicked.connect(self._whole_frame)
        region.addWidget(self.full)
        side.addLayout(region)
        side.addWidget(self.region_label)
        scan_range = QHBoxLayout()
        self.first = QSpinBox()
        self.last = QSpinBox()
        for control, name in ((self.first,'First'), (self.last,'Last')):
            control.setKeyboardTracking(False)
            control.setGroupSeparatorShown(True)
            control.setAccessibleName(name+' native frame to scan')
            control.setToolTip(name+' native frame to scan, inclusive')
            scan_range.addWidget(QLabel(name))
            scan_range.addWidget(control, 1)
            control.valueChanged.connect(self._range_changed)
        side.addLayout(scan_range)
        interval = QHBoxLayout()
        interval.addWidget(QLabel('Check every'))
        self.step = QSpinBox()
        self.step.setRange(1, 2_147_483_647)
        self.step.setKeyboardTracking(False)
        self.step.setAccessibleName('Check every N frames')
        self.step.setValue(1)
        self.step.valueChanged.connect(self._sensitivity_changed)
        interval.addWidget(self.step)
        self.step_unit = QLabel('frame')
        interval.addWidget(self.step_unit, 1)
        self.reset_range = QPushButton('Reset')
        self.reset_range.setAccessibleName('Reset to the full frame range')
        self.reset_range.setToolTip('Use the entire recorded Cine')
        self.reset_range.clicked.connect(self._reset_range)
        interval.addWidget(self.reset_range)
        side.addLayout(interval)
        self.coverage = QLabel()
        self.coverage.setWordWrap(True)
        side.addWidget(self.coverage)
        self.precision = QComboBox()
        self.precision.setAccessibleName('Camera transfer method and precision')
        self.precision.currentIndexChanged.connect(self._transfer_changed)
        self.precision_label = QLabel('Transfer')
        precision_row = QHBoxLayout()
        precision_row.addWidget(self.precision_label)
        precision_row.addWidget(self.precision, 1)
        side.addLayout(precision_row)
        self.transfer_status = QLabel()
        self.transfer_status.setAccessibleName('Camera transfer status')
        self.transfer_status.setTextFormat(Qt.PlainText)
        self.transfer_status.setWordWrap(True)
        side.addWidget(self.transfer_status)
        options = QHBoxLayout()
        options.addWidget(QLabel('Sensitivity'))
        self.sensitivity = QComboBox()
        self.sensitivity.setAccessibleName('Event detection sensitivity')
        for label in ('Low', 'Balanced', 'High'):
            self.sensitivity.addItem(label, label.lower())
        self.sensitivity.setCurrentIndex(1)
        self.sensitivity.setToolTip('High can reveal faint changes and return more noise.')
        self.sensitivity.currentIndexChanged.connect(self._sensitivity_changed)
        options.addWidget(self.sensitivity, 1)
        side.addLayout(options)
        actions = QHBoxLayout()
        self.analyze = QPushButton('Analyze recording')
        self.analyze.setProperty('primaryAction', True)
        self.analyze.setToolTip('Look for changes inside the selected region and frame range.')
        self.analyze.clicked.connect(self._analyze)
        self.cancel = QPushButton('Cancel')
        self.cancel.clicked.connect(self.cancel_scan)
        actions.addWidget(self.analyze, 1)
        actions.addWidget(self.cancel)
        side.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setAccessibleName('Frames examined')
        side.addWidget(self.progress)
        self.retry = QPushButton('Retry preview')
        self.retry.clicked.connect(lambda: self.seek(self.position))
        self.retry.hide()
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        side.addWidget(self.status)
        side.addWidget(self.retry)

        self.result_title = QLabel('Candidates')
        self.result_title.setStyleSheet('font-weight: 600;')
        side.addWidget(self.result_title)
        self.hits = _Candidates(self)
        self.table = QTableView()
        self.table.setModel(self.hits)
        self.table.setAccessibleName('Event candidates ranked by strongest change. Select a row to view its peak.')
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setMinimumWidth(225)
        self.table.setMinimumHeight(80)
        self.table.selectionModel().currentRowChanged.connect(self._select_hit)
        self.table.clicked.connect(lambda index: self._select_hit(index))
        side.addWidget(self.table, 1)
        self.evidence = QLabel()
        self.evidence.setWordWrap(True)
        side.addWidget(self.evidence)
        self.viewer = QPushButton('Open frame in Viewer')
        self.viewer.clicked.connect(self._open_in_viewer)
        side.addWidget(self.viewer)
        split.addWidget(inspector)
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([820, 314])
        layout.addWidget(split, 1)
        # Stepping remains available while the image or results table has focus.
        for key, delta in ((Qt.Key_Left, -1), (Qt.Key_Right, 1)):
            for widget in (self.view, self.table):
                shortcut = QShortcut(QKeySequence(key), widget)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(lambda d=delta: self.seek(self.position+d))

    def _source_changed(self):
        chosen = self.source.currentData()
        if chosen != self.current:
            self._select(chosen, 0)

    def set_sources(self, paths, active_path=None, position=None):
        files = list(dict.fromkeys(str(Path(p).resolve()) for p in paths if p and Path(p).suffix.lower()=='.cine'))
        active = str(Path(active_path).resolve()) if active_path and Path(active_path).suffix.lower()=='.cine' else ''
        target = self.current
        if (target is None or (active and active != self._active_file and self.activity != 'scan'
                              and not self._restored_selection)):
            target = active or next(iter(files), None)
        if self.current is not None and self.current not in files:
            files.append(self.current)
        self._active_file = active
        self._restored_selection = False
        with QSignalBlocker(self.source):
            self.source.clear()
            for value in files:
                self.source.addItem(Path(value).name if isinstance(value,str) else value.label, value)
            self.source.setCurrentIndex(self.source.findData(target) if target is not None else -1)
        if target != self.current:
            self._select(target, int(position or 0))
        elif target is not None and self.info is None and not self.activity:
            self.seek(self.position)

    def _add_source(self, value, position=0):
        if isinstance(value,str):
            value = str(Path(value).resolve())
        with QSignalBlocker(self.source):
            idx = next((i for i in range(self.source.count()) if self.source.itemData(i) == value), -1)
            if idx == -1:
                self.source.addItem(Path(value).name if isinstance(value,str) else value.label, value)
                idx = self.source.count()-1
            self.source.setCurrentIndex(idx)
        self._select(value, position)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Find events in a Cine', '', 'Phantom Cine (*.cine *.CINE)')
        if path:
            self._add_source(path)

    def _camera_picker(self):
        if self.picker is None:
            from event_camera_picker import CameraRecordingDialog
            self.picker = CameraRecordingDialog(self)
            self.picker.recordingSelected.connect(self._add_source)
        self.picker.open()
        self.picker.raise_()

    def _select(self, source, position):
        self.retry.hide()
        self.current, self.info, self.roi = source, None, None
        self.restore = None
        self.position = max(0, position)
        self._clear_hits()
        self.view.image = self.view.roi = self.view.start = self.view.end = None
        self.view.message = 'Opening recording…' if source is not None else 'Open a recorded Cine or choose a recording on the camera.'
        self.view.update()
        self.status.setText('')
        self.transfer_status.clear()
        with QSignalBlocker(self.shape), QSignalBlocker(self.step), QSignalBlocker(self.precision):
            self.shape.setCurrentIndex(0)
            self.view.set_point_mode(False)
            self.step.setValue(1)
            self.precision.clear()
        for widget in (self.native, self.slider, self.first, self.last):
            with QSignalBlocker(widget):
                widget.setRange(0, 0)
        if source is None:
            self.ticket += 1
            self.activity = ''
            if self.worker:
                self.worker.submit(self.ticket, None, 0)
        else:
            self._request(self.position, reopen=True)
        self._controls()

    def _request(self, index, scan=None, reopen=False):
        if self.closing:
            return
        if self.worker is None:
            self.worker = RecordingWorker(self)
            self.worker.previewReady.connect(self._preview_ready)
            self.worker.scanReady.connect(self._scan_ready)
            self.worker.failed.connect(self._failed)
            self.worker.aborted.connect(self._aborted)
            self.worker.progress.connect(self._progress)
            self.worker.transferStatus.connect(self._transfer_status_changed)
            self.worker.start()
        self.ticket += 1
        self.cancelling = False
        self.activity = 'scan' if scan else 'preview'
        self.worker.submit(self.ticket, self.current, index, scan, reopen)

    def seek(self, index):
        if self.current is None or self.activity == 'scan' or self.closing:
            return
        index = max(0, int(index))
        if self.info:
            index = min(index, self.info.count-1)
        if self.info and index==self.position and self.view.image is not None and self.activity!='preview':
            return
        self.position = index
        self._sync_transport()
        self._request(index)
        self._controls()

    def _sync_transport(self):
        if self.info:
            with QSignalBlocker(self.native), QSignalBlocker(self.slider):
                self.slider.setRange(0, self.info.count-1)
                self.native.setRange(self.info.first, self.info.first+self.info.count-1)
                self.native.setValue(self.info.first+self.position)
                self.slider.setValue(self.position)

    def _native_seek(self, value):
        if self.info:
            self.seek(value-self.info.first)

    def _preview_ready(self, ticket, info, index, pixels):
        if self.closing or ticket != self.ticket:
            return
        self.retry.hide()
        new_source = info != self.info
        if new_source and self.info is not None:
            self.roi = self.view.roi = None
            self._clear_hits()
        self.info, self.position, self.activity = info, index, ''
        if new_source:
            self._set_bounds()
            with QSignalBlocker(self.precision):
                self.precision.clear()
                for mode, label, hint in (
                    ('ximg', 'High-speed Ethernet · 12-bit (ximg)',
                     'Reads packed 12-bit images over the camera’s wired link. macOS may ask for administrator approval when a scan starts.'),
                    ('fast', 'Standard · 8-bit', 'Transfers fewer intensity levels and reduces download time over the standard connection.'),
                    ('full', 'Full precision · 16-bit', 'Uses the standard connection and retains all 16-bit intensity values.'),
                ):
                    if mode in info.transfer_modes:
                        self.precision.addItem(label, mode)
                        self.precision.setItemData(self.precision.count()-1, hint, Qt.ToolTipRole)
            self._update_transfer_hint()

        self.view.image = QImage(pixels.data, info.width, info.height, pixels.strides[0], QImage.Format_Grayscale8).copy()
        self.view.message = ''
        self.view.update()
        self._sync_transport()
        if self.restore:
            state, self.restore = self.restore, None
            bounds = state['scan_range']
            valid_bounds = bounds is None or bounds[1] < info.count
            if valid_bounds:
                self._set_bounds(bounds)
            with QSignalBlocker(self.shape), QSignalBlocker(self.step):
                self.step.setValue(state['step'])
                self.shape.setCurrentIndex(self.shape.findData(state['shape']))
            self.view.set_point_mode(state['shape']=='point')
            x, y, w, h = state.get('roi') or (0,0,0,0)
            if w and h and x+w <= info.width and y+h <= info.height:
                self.set_roi([x,y,w,h])
                saved = state.get('events')
                if (saved is not None and tuple(state['file_signature'])==info.file_signature and valid_bounds
                        and all(self.first.value() <= info.first+hit[0] <= info.first+hit[2] <= self.last.value() for hit in saved)):
                    self._show_results(tuple(Event(*hit) for hit in saved), state['total_events'], jump=False)
        self._controls()

    def set_roi(self, rect):
        if self.info is None or self.activity == 'scan':
            return
        x, y, w, h = map(int, rect)
        if x<0 or y<0 or w<1 or h<1 or x+w>self.info.width or y+h>self.info.height:
            return
        self.roi = (x,y,w,h)
        self.view.roi = self.roi
        self.view.update()
        self._clear_hits()
        self.status.setText('')
        self._controls()

    def _shape_changed(self):
        point = self.shape.currentData() == 'point'
        self.view.set_point_mode(point)
        self.roi = self.view.roi = self.view.start = self.view.end = None
        self.view.update()
        self._sensitivity_changed()

    def _set_bounds(self, bounds=None):
        native = self.info.first
        end = native+self.info.count-1
        lo, hi = (native, end) if bounds is None else (native+bounds[0], native+bounds[1])
        with QSignalBlocker(self.first), QSignalBlocker(self.last):
            self.first.setRange(native, end)
            self.last.setRange(native, end)
            self.first.setValue(lo)
            self.last.setValue(hi)

    def _reset_range(self):
        if self.info:
            self._set_bounds()
            self._sensitivity_changed()

    def _range_changed(self):
        if self.first.value() > self.last.value():
            other = self.last if self.sender() is self.first else self.first
            with QSignalBlocker(other):
                other.setValue(self.sender().value())
        self._sensitivity_changed()

    def _bounds(self):
        return self.first.value()-self.info.first, self.last.value()-self.info.first

    def _whole_frame(self):
        if self.info:
            self.set_roi((0, 0, self.info.width, self.info.height))

    def _sensitivity_changed(self):
        self._clear_hits()
        self.status.setText('')
        self._controls()

    def _update_transfer_hint(self):
        hint = self.precision.currentData(Qt.ToolTipRole) or ''
        self.precision.setToolTip(hint)
        self.transfer_status.setText('macOS may ask for permission to read camera images.'
                                     if self.precision.currentData() == 'ximg' else '')

    def _transfer_changed(self):
        self._update_transfer_hint()
        self._sensitivity_changed()

    def _transfer_status_changed(self, ticket, message):
        if ticket == self.ticket and self.activity == 'scan' and not self.cancelling and not self.closing:
            self.transfer_status.setText(message)
            self.transfer_status.setVisible(bool(message))

    def _clear_hits(self):
        self.scanned = False
        self.total_events = 0
        self.hits.sampled = self.step.value() > 1
        self.hits.replace((), self.info.first if self.info else 0)
        self.slider.set_peaks(())
        self.evidence.clear()
        self.result_title.setText('Candidates')

    def _analyze(self):
        if not self.info or not self.roi or self.activity or self.view.image is None:
            return
        first, last = self._bounds()
        step = self.step.value()
        samples = sample_count(first, last, step)
        if samples < 3:
            return
        signal = 'mean' if self.shape.currentData() == 'point' else 'spatial'
        mode = self.precision.currentData() or 'full'
        self._rate_start = None
        self.progress.setRange(0, samples)
        self.progress.setValue(0)
        self.status.setText('Preparing scan…')
        self._request(self.position, (self.roi,self.sensitivity.currentData(),first,last,step,signal,mode))
        self._controls()

    def _progress(self, ticket, done, total):
        if ticket == self.ticket and not self.cancelling and not self.closing:
            self.progress.setValue(done)
            if not done:
                return
            if done == total:
                self.status.setText('Ranking candidates…')
                return
            now = monotonic()
            if self._rate_start is None:
                self._rate_start = now, done
            started, initial = self._rate_start
            status = f'Examined {done:,} of {total:,} frames'
            if now-started >= 2 and done > initial:
                speed = (done-initial)/(now-started)
                seconds = round((total-done)/speed)
                if seconds >= 3600:
                    eta = f'{seconds//3600} h {(seconds//60)%60} min'
                elif seconds >= 60:
                    eta = f'{seconds//60} min {seconds%60} s'
                else:
                    eta = f'{seconds} s'
                status += f' · {speed:,.1f} frames/s · ~{eta} left'
            self.status.setText(status)

    def cancel_scan(self):
        if self.activity == 'scan':
            self.cancelling = True
            self.worker.interrupt()
            self.cancel.setEnabled(False)
            self.status.setText('Cancelling scan…')

    def _aborted(self, ticket):
        if ticket == self.ticket and not self.closing:
            self.activity = ''
            self._update_transfer_hint()
            self.status.setText('Scan cancelled.')
            self._controls()

    def _failed(self, ticket, message, invalidated):
        if ticket == self.ticket and not self.closing:
            retry_preview = self.activity != 'scan' or invalidated
            self.activity = ''
            if invalidated:
                self._clear_hits()
            self._update_transfer_hint()
            self.status.setText(message)
            if retry_preview:
                self.view.image = None
                self.view.message = 'Could not read the recording.'
                self.view.update()
                self.retry.show()
            self._controls()

    def _scan_ready(self, ticket, result):
        if ticket == self.ticket and not self.closing:
            if self.cancelling:
                self._aborted(ticket)
                return
            self.activity = ''
            self._show_results(result.events, result.total_events, jump=True)
            self._controls()

    def _show_results(self, events, total, jump):
        self.hits.sampled = self.step.value() > 1
        self.hits.replace(events, self.info.first)
        self.scanned = True
        self.slider.set_peaks(events)
        self.total_events = total
        label = f'Candidates · {total:,}' if total == len(events) else f'Candidates · strongest {len(events):,} of {total:,}'
        self.result_title.setText(label)
        if events:
            self.status.setText('Strongest changes first. Select a candidate to inspect its peak.')
            if jump:
                self.table.selectRow(0)
        else:
            self.status.setText('No candidates at this sensitivity. Try a tighter region or higher sensitivity.')

    def _select_hit(self, index, previous=None):
        if index.isValid() and index.row()<len(self.hits.events):
            hit = self.hits.events[index.row()]
            count = hit.last-hit.first+1
            if self.step.value() > 1:
                self.evidence.setText(f'Sampled peak at frame {self.info.first+hit.peak:,} · {hit.reason}')
            else:
                self.evidence.setText(f'{count:,} frame{"s" if count != 1 else ""} · {hit.reason}')
            self.slider.selected = hit.peak
            self.slider.update()
            if self.position != hit.peak:
                self.seek(hit.peak)

    def _open_in_viewer(self):
        if isinstance(self.current, str) and self.info and self.view.image is not None and not self.activity:
            self.openInViewer.emit(self.current,self.position)

    def _controls(self):
        scanning = self.activity == 'scan'
        ready = self.info is not None
        self.view.setEnabled(not scanning)
        point = self.shape.currentData() == 'point'
        fits_probe = not ready or min(self.info.width, self.info.height) >= 4
        self.shape.model().item(self.shape.findData('point')).setEnabled(fits_probe)
        instruction = ('Click to measure the mean brightness of 4 × 4 sensor pixels.' if point else
                       'Draw a rectangle around the area where the event could occur.')
        self.region_label.setText(f'{self.roi[2]} × {self.roi[3]} px at ({self.roi[0]}, {self.roi[1]})'
                                  if self.roi else instruction)
        self.full.setVisible(not point)
        show_precision = bool(ready and self.info.transfer_modes)
        self.precision_label.setVisible(show_precision)
        self.precision.setVisible(show_precision)
        self.transfer_status.setVisible(show_precision and bool(self.transfer_status.text()))
        self.step_unit.setText('frame' if self.step.value()==1 else 'frames')
        self.precision.setEnabled(ready and self.precision.count()>1 and not scanning)
        for control in (self.shape, self.first, self.last, self.step, self.reset_range):
            control.setEnabled(ready and not scanning)
        samples = 0
        note = ''
        if ready:
            first, last = self._bounds()
            step = self.step.value()
            samples = sample_count(first,last,step)
            if samples < 3:
                note = 'Choose at least three sampled frames. Widen the range or reduce the interval.'
            elif step == 1:
                note = f'Checks all {samples:,} frames in the range.'
            else:
                note = f'{samples:,} of {last-first+1:,} frames · changes shorter than {step:,} frames can be missed.'
        self.coverage.setText(note)
        verified = ready and self.view.image is not None and not self.activity
        self.analyze.setEnabled(bool(verified and self.roi and samples >= 3))
        self.cancel.setVisible(scanning)
        self.cancel.setEnabled(scanning and not self.cancelling)
        self.progress.setVisible(scanning)
        self.sensitivity.setEnabled(not scanning)
        self.full.setEnabled(ready and not scanning)
        self.prev.setEnabled(ready and self.position>0 and not scanning)
        self.next.setEnabled(ready and self.position+1<self.info.count and not scanning)
        self.slider.setEnabled(ready and not scanning)
        self.native.setEnabled(ready and not scanning)
        self.viewer.setVisible(isinstance(self.current, str))
        self.viewer.setEnabled(verified)
        self.table.setEnabled(not scanning)

    def settings(self):
        if not isinstance(self.current, str):
            return {}
        if self.restore:
            return dict(self.restore)
        hits = self.hits.events
        events = [(e.first,e.peak,e.last,e.score,e.reason) for e in hits] if self.scanned else None
        return dict(path=self.current,position=self.position,roi=self.roi,
                    sensitivity=self.sensitivity.currentData(),events=events, total_events=self.total_events,
                    file_signature=self.info.file_signature if self.info else None,
                    scan_range=self._bounds() if self.info else None, step=self.step.value(), shape=self.shape.currentData())

    def apply_settings(self, settings):
        state = validate_event_settings(settings)
        self._active_file = ''
        if not state:
            with QSignalBlocker(self.source):
                self.source.clear()
            self._select(None,0)
            return
        self._add_source(state['path'],state['position'])
        with QSignalBlocker(self.sensitivity):
            self.sensitivity.setCurrentIndex(self.sensitivity.findData(state['sensitivity']))
        self.restore = state
        self._restored_selection = True

    def pause(self):
        pass

    def busy(self):
        return self.activity == 'scan'

    def shutdown(self):
        self.closing = True
        self.ticket += 1
        self.activity = ''
        picker_done = self.picker is None or self.picker.shutdown()
        if self.worker is None:
            return picker_done
        self.worker.stop()
        return self.worker.isFinished() and picker_done

    def showEvent(self, event):
        super().showEvent(event)
        if self.closing and self.worker is not None and self.worker.isFinished():
            self.worker.deleteLater()
            self.worker = None
        self.closing = False
