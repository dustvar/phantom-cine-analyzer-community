"""Live setup and recording controls for directly connected Phantom cameras."""
from math import ceil
from time import monotonic

from PySide6.QtCore import QSettings, QSize, Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget)

from app_runtime import user_data_dir
from camera_control_ui_worker import CameraWorker
from camera_monitor import MonitorLevels
from camera_capture_widgets import (TRIGGER_STYLE, add_custom, duration, geometry_label,
                                    number, preset_field)
from event_camera import validate_address
from event_camera_picker import _Discover


class _Preview(QWidget):
    def __init__(self):
        super().__init__()
        self.image = self.pixels = None
        self.levels = MonitorLevels()
        self.mode = 'auto'
        self.message = 'Connect a camera to see the live image.'
        self.setAccessibleName('Live camera image')
        self.setMinimumSize(160, 110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self):
        return QSize(640, 420)

    def clear(self, message):
        self.image = self.pixels = None
        self.levels.reset()
        self.message = message
        self.update()

    def set_mode(self, mode):
        self.mode = mode
        if self.pixels is not None:
            self.set_frame(self.pixels)

    def set_frame(self, pixels):
        self.pixels = pixels
        array = self.levels.render(pixels, self.mode)
        height, width = array.shape
        self.image = QImage(array.data, width, height, int(array.strides[0]),
                            QImage.Format_Grayscale8).copy()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#101317'))
        if self.image is None:
            p.setPen(QColor('#b6beca'))
            p.drawText(self.rect().adjusted(10, 5, -10, -5),
                       Qt.AlignCenter | Qt.TextWordWrap, self.message)
        else:
            size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
            box = self.rect()
            box.setSize(size)
            box.moveCenter(self.rect().center())
            p.drawImage(box, self.image)
        p.end()


def _label(text=''):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


def _group(title):
    group = QGroupBox(title)
    return group, QVBoxLayout(group)


class CameraControlPanel(QWidget):
    def __init__(self, parent=None, camera_in_use=None):
        super().__init__(parent)
        self.camera_in_use = camera_in_use
        self.active = False
        self.needs_discovery = False
        self.workers = set()
        self.discovery = self.session = self.snapshot = None
        self.read_pending = False
        self.pending = ''
        self.edits = set()
        self.draft_mode = None
        self.next_status = self.next_frame = 0.
        self.frames = 0
        self.preview_clock = 0.
        self.settings = QSettings(str(user_data_dir()/'cameras.ini'), QSettings.IniFormat)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.trigger_guard = QTimer(self)
        self.trigger_guard.setSingleShot(True)
        self.trigger_guard.setTimerType(Qt.PreciseTimer)
        self.trigger_guard.timeout.connect(self._controls)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout()
        title = QLabel('Camera Control')
        font = title.font()
        font.setBold(True)
        font.setPointSizeF(max(14., font.pointSizeF() + 3))
        title.setFont(font)
        top.addWidget(title)
        top.addStretch()
        self.cameras = QComboBox()
        self.cameras.setAccessibleName('Camera connection')
        self.cameras.setMinimumContentsLength(17)
        self.cameras.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cameras.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cameras.setMaximumWidth(360)
        top.addWidget(self.cameras)
        self.refresh = QPushButton('Refresh')
        self.refresh.setAccessibleName('Refresh cameras')
        self.connect_button = QPushButton('Connect')
        top.addWidget(self.refresh)
        top.addWidget(self.connect_button)
        layout.addLayout(top)
        self.address = QLineEdit()
        self.address.setAccessibleName('Camera IPv4 address')
        self.address.setPlaceholderText('Camera IPv4 address')
        self.address.setMaximumWidth(300)
        layout.addWidget(self.address, 0, Qt.AlignRight)
        self.address.hide()

        body = QHBoxLayout()
        layout.addLayout(body, 1)
        left = QVBoxLayout()
        body.addLayout(left, 1)
        self.view = _Preview()
        left.addWidget(self.view, 1)
        live_row = QHBoxLayout()
        self.preview_info = _label()
        self.preview_button = QPushButton('Pause preview')
        self.preview_button.setToolTip('Pause the monitor while the camera continues operating.')
        live_row.addWidget(self.preview_info, 1)
        self.monitor_mode = QComboBox()
        self.monitor_mode.setAccessibleName('Monitor brightness')
        for label, mode in [('Auto brightness', 'auto'), ('Standard', 'standard'), ('Linear', 'linear')]:
            self.monitor_mode.addItem(label, mode)
        self.monitor_mode.setToolTip('Monitor brightness affects only this preview. Linear shows the camera signal directly.')
        self.monitor_mode.currentIndexChanged.connect(lambda: self.view.set_mode(self.monitor_mode.currentData()))
        live_row.addWidget(self.monitor_mode)
        live_row.addWidget(self.preview_button)
        left.addLayout(live_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(274)
        scroll.setMaximumWidth(322)
        sidebar = QWidget()
        rail = QVBoxLayout(sidebar)
        rail.setContentsMargins(0, 0, 4, 0)
        capture, controls = _group('Capture settings')
        controls.setSpacing(4)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.fps, self.rate_presets, rate_field = preset_field(
            'Capture frame rate', ' fps', self._rate_menu)
        self.exposure, self.exposure_presets, exposure_field = preset_field(
            'Exposure time', ' µs', self._exposure_menu)
        form.addRow('Frame rate', rate_field)
        form.addRow('Exposure', exposure_field)
        self.geometry_note = _label()
        self.geometry_note.setToolTip('Output width × height. A smaller sensor window crops the view. Binning combines pixels.')
        form.addRow('Image size', self.geometry_note)
        controls.addLayout(form)
        controls.addWidget(QLabel('Trigger position'))
        self.position = QSlider(Qt.Horizontal)
        self.position.setAccessibleName('Trigger position, percent before trigger')
        self.position.setRange(0, 100)
        self.position.setStyleSheet(TRIGGER_STYLE)
        self.position.setToolTip('Green shows time before the trigger. Red shows time after it.')
        controls.addWidget(self.position)
        ends = QHBoxLayout()
        self.before = _label('Before')
        ends.addWidget(self.before)
        ends.addStretch()
        self.after = _label('After')
        ends.addWidget(self.after)
        controls.addLayout(ends)
        self.record_time = _label()
        self.record_time.setAccessibleName('Total recording time')
        controls.addWidget(self.record_time)
        self.post = QSpinBox()
        self.post.setAccessibleName('Frames after trigger')
        self.post.setGroupSeparatorShown(True)
        after_row = self.frames_after_row = QFormLayout()
        after_row.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        after_row.setRowWrapPolicy(QFormLayout.WrapLongRows)
        after_row.addRow('Frames after', self.post)
        controls.addLayout(after_row)
        self.breakdown = _label()
        controls.addWidget(self.breakdown)
        edit_actions = QHBoxLayout()
        self.apply_button = QPushButton('Apply settings')
        self.revert_button = QPushButton('Revert')
        self.revert_button.setToolTip('Restore the current camera settings in these controls.')
        edit_actions.addWidget(self.apply_button, 1)
        edit_actions.addWidget(self.revert_button)
        controls.addLayout(edit_actions)
        self.draft_note = _label()
        self.draft_note.setAccessibleName('Pending capture settings')
        controls.addWidget(self.draft_note)
        self.csr_button = QPushButton('Black reference (CSR)…')
        self.csr_button.setToolTip('Calibrate the sensor’s black level at the current settings.')
        controls.addWidget(self.csr_button)
        rail.addWidget(capture)
        record, actions = _group('Recording')
        self.state = _label('Disconnected')
        self.arm_button = QPushButton('Arm')
        self.arm_button.setToolTip('Start buffering the next recording in camera RAM.')
        self.trigger_button = QPushButton('Trigger')
        self.trigger_button.setProperty('primaryAction', True)
        self.trigger_button.setToolTip('Send a software trigger to the armed camera.')
        actions.addWidget(self.state)
        record_buttons = QHBoxLayout()
        record_buttons.addWidget(self.arm_button, 1)
        record_buttons.addWidget(self.trigger_button, 1)
        actions.addLayout(record_buttons)
        rail.addWidget(record)
        rail.addStretch()
        scroll.setWidget(sidebar)
        body.addWidget(scroll)
        self.notice = _label()
        layout.addWidget(self.notice)
        self.preview_on = False
        self.cameras.currentIndexChanged.connect(self._source_changed)
        self.refresh.clicked.connect(self._discover)
        self.connect_button.clicked.connect(self._connect_clicked)
        self.address.returnPressed.connect(self._connect_clicked)
        self.preview_button.clicked.connect(self._toggle_preview)
        self.fps.valueChanged.connect(self._rate_changed)
        self.exposure.valueChanged.connect(lambda: self._edited('exposure_us'))
        self.position.valueChanged.connect(self._slider_changed)
        self.post.valueChanged.connect(self._post_changed)
        self.apply_button.clicked.connect(self._apply)
        self.csr_button.clicked.connect(self._csr)
        self.revert_button.clicked.connect(self._revert)
        self.arm_button.clicked.connect(self._arm)
        self.trigger_button.clicked.connect(self._trigger)
        self._controls()

    def _start(self, worker):
        self.workers.add(worker)
        worker.finished.connect(self._finished)
        worker.start()

    def set_active(self, active):
        if self.active == active:
            return
        self.active = active
        self.needs_discovery = active
        if active:
            if not self.workers:
                self._discover()
        else:
            self._stop()

    def pause(self):
        self.set_active(False)

    def shutdown(self):
        self.set_active(False)
        return all(w.isFinished() for w in self.workers)

    def _stop(self):
        if self.discovery is not None:
            self.discovery.requestInterruption()
            self.discovery = None
        if self.session is not None:
            self.session.stop()
            self.session = None
        self.timer.stop()
        self.snapshot = None
        self.pending = ''
        self.read_pending = False
        self.preview_on = False
        self.edits.clear()
        self.state.setText('Disconnected')
        self.breakdown.clear()
        self.draft_mode = None
        self.geometry_note.clear()
        self.record_time.clear()
        self.before.setText('Before')
        self.after.setText('After')
        self.preview_info.clear()
        self.notice.clear()
        self.view.clear('Connect a camera to see the live image.')
        self._controls()

    def _discover(self):
        if not self.active or self.session or self.discovery:
            return
        self.needs_discovery = False
        self.discovery = _Discover(self)
        self.cameras.clear()
        self.notice.setText('Looking for Phantom cameras on Ethernet…')
        self._controls()
        self._start(self.discovery)

    def _finished(self):
        worker = self.sender()
        self.workers.discard(worker)
        if worker is self.discovery:
            self.discovery = None
            self._list_cameras(worker)
        elif worker is self.session:
            message = self.notice.text()
            self._stop()
            self.notice.setText(message)
        elif self.active and self.needs_discovery and not self.workers:
            self._discover()
        self._controls()
        worker.deleteLater()

    def _list_cameras(self, worker):
        previous = self.settings.value('last/host', '', type=str)
        previous_serial = self.settings.value('last/serial', '', type=str)
        previous_model = self.settings.value('last/model', '', type=str)
        groups = {}
        for cam in worker.cameras:
            groups.setdefault(cam.serial, []).append(cam)
        for serial, group in groups.items():
            hosts = tuple(dict.fromkeys(c.host for c in group))
            name = next((c.name for c in group if c.name and c.name != serial), '')
            model = previous_model if serial == previous_serial else ''
            self.cameras.addItem(f'{name or model or "Phantom"} · S/N {serial}', (hosts, serial))
        if previous and previous_serial not in groups and not any(previous == c.host for c in worker.cameras):
            try:
                host = validate_address(previous)
            except ValueError:
                pass
            else:
                self.cameras.addItem(f'{previous_model or "Phantom"} · Previously used',
                                     ((host,), previous_serial))
        self.cameras.addItem('Enter address manually…', None)
        self._source_changed()
        self.notice.setText(worker.error or ('Select a camera and connect.' if groups
                            else 'No camera replied. Check the Ethernet connection or enter its address.'))
        if len(groups) == 1:
            self._connect_clicked()

    def _source_changed(self):
        self.address.setVisible(self.cameras.count() > 0 and self.cameras.currentData() is None)

    def _connect_clicked(self):
        if self.session:
            self._stop()
            return
        if not self.active or self.discovery or self.workers:
            return
        data = self.cameras.currentData()
        if data is None:
            try:
                host = validate_address(self.address.text())
            except ValueError as exc:
                self.notice.setText(str(exc))
                return
            data = ((host,), '')
        hosts, serial = data
        if self.camera_in_use and self.camera_in_use(hosts, serial):
            self.notice.setText('Event Finder is scanning this camera. Finish or cancel that scan before starting live view.')
            return
        self.session = CameraWorker(hosts, serial, self)
        self.session.reply.connect(self._reply)
        self.pending = 'connect'
        self.notice.setText('Connecting to the camera…')
        self.view.clear('Connecting…')
        self._controls()
        self._start(self.session)

    def _controls(self):
        ready = self.snapshot is not None and self.session is not None
        editing = ready and self.snapshot.can_apply and not self.pending
        self.cameras.setEnabled(self.active and not self.session and not self.discovery)
        self.address.setEnabled(self.cameras.isEnabled())
        self.refresh.setEnabled(self.active and not self.session and not self.workers and not self.discovery)
        self.connect_button.setText('Disconnect' if self.session else 'Connect')
        self.connect_button.setEnabled(self.active and not self.pending and not self.discovery
                                      and (self.session is not None or not self.workers))
        self.preview_button.setEnabled(ready)
        self.preview_button.setText('Pause preview' if self.preview_on else 'Resume preview')
        self.fps.setEnabled(editing and self.snapshot.internal_clock)
        self.rate_presets.setEnabled(self.fps.isEnabled())
        self.exposure.setEnabled(editing and self.snapshot.manual_exposure)
        self.exposure_presets.setEnabled(self.exposure.isEnabled())
        self.position.setEnabled(editing)
        self.post.setEnabled(editing and self.draft_mode is None)
        self.csr_button.setVisible(bool(ready and self.snapshot.supports_csr))
        self.csr_button.setEnabled(bool(ready and self.snapshot.can_csr and not self.pending and not self.edits))
        self.csr_button.setText(f'CSR · {self.snapshot.csr_remaining:,} frames left'
                                if ready and self.snapshot.csr_remaining else 'Black reference (CSR)…')
        issue = self._draft_issue() if ready else ''
        self.draft_note.setVisible(bool(ready and self.edits))
        if ready and self.edits:
            live = f'On camera · {number(self.snapshot.fps)} fps · {number(self.snapshot.exposure_us)} µs'
            self.draft_note.setText(issue or f'Pending changes. {live}')
        self.apply_button.setEnabled(bool(editing and self.edits and not issue))
        self.revert_button.setEnabled(bool(ready and self.edits and not self.pending))
        replace = self.snapshot.replaceable[0] if ready and self.snapshot.replaceable and not self.snapshot.can_arm else None
        self.arm_button.setText(f'Replace Cine {replace.slot}…' if replace else 'Arm')
        self.arm_button.setToolTip('Replace the stored Cine and start buffering a new recording.' if replace else
                                   'Start buffering the next recording in camera RAM.')
        self.arm_button.setEnabled(bool(ready and not self.pending and not self.edits and
                                  (self.snapshot.can_arm or self.snapshot.replaceable)))
        self.trigger_button.setEnabled(bool(ready and not self.pending and not self.edits
                                           and not self.trigger_guard.isActive() and self.snapshot.can_trigger))

    def _reply(self, command, result, error):
        if self.sender() is not self.session or not self.active:
            return
        if command in ('preview', 'status'):
            self.read_pending = False
        if command == self.pending:
            self.pending = ''
        if command == 'preview':
            if error:
                self._pause_preview(error)
            elif self.preview_on:
                self.view.set_frame(result)
                self.frames += 1
                now = monotonic()
                if now - self.preview_clock >= 1.:
                    rate = self.frames / (now - self.preview_clock)
                    info = f'Live · {rate:.1f} preview fps  ·  {result.shape[1]} × {result.shape[0]}'
                    if self.view.levels.signal:
                        info += '  ·  ' + self.view.levels.signal
                    self.preview_info.setText(info)
                    self.frames, self.preview_clock = 0, now
            self._schedule()
            return
        if command == 'stop_preview':
            if error:
                self.notice.setText(error)
            return
        if result is None:
            self._stop()
            self.notice.setText(error or 'Camera disconnected.')
            return
        self._readback(result, reset=command in ('connect', 'apply'))
        if command == 'connect':
            self.preview_on = True
            self.frames, self.preview_clock = 0, monotonic()
            self.next_status = monotonic() + 1.
            self.preview_info.setText('Starting live preview…')
            self.notice.setText(f'{result.model} · S/N {result.serial} · {self.session.host}')
            for key, val in [('host', self.session.host), ('serial', result.serial), ('model', result.model)]:
                self.settings.setValue('last/'+key, val)
            self.settings.sync()
            self.next_frame = 0.
        elif command == 'status':
            self.next_status = monotonic() + 1.
        elif error:
            self.notice.setText(error)
        elif command in ('apply', 'arm', 'trigger', 'csr'):
            self.notice.setText({'apply':'Capture settings read back from the camera.',
                                 'arm':'Arm command accepted.', 'trigger':'Trigger command accepted.',
                                 'csr':'Black reference command accepted.'}[command])
        self._controls()
        self._schedule()

    def _readback(self, s, reset):
        self.snapshot = s
        if reset:
            self.edits.clear()
            self.draft_mode = None
        if self.draft_mode == (s.width, s.height, s.binning):
            self.draft_mode = None
            self.edits.discard('mode')
        with QSignalBlocker(self.fps):
            self.fps.setRange(min(s.fps, s.min_fps or .001), max(s.fps, s.max_fps or 1e9))
            if 'fps' not in self.edits and self.fps.value() != round(s.fps, 3):
                self.fps.setValue(s.fps)
        self._update_exposure_limit()
        with QSignalBlocker(self.exposure):
            if 'exposure_us' not in self.edits and self.exposure.value() != round(s.exposure_us, 3):
                self.exposure.setValue(s.exposure_us)
        with QSignalBlocker(self.post):
            self.post.setMaximum(s.capacity)
            if 'post_frames' not in self.edits and self.draft_mode is None and self.post.value() != s.post_frames:
                self.post.setValue(s.post_frames)
        self.geometry_note.setText(geometry_label(self._mode()))
        self._update_position()
        states = dict(s.slots)
        active = states.get(s.active_slot, ())
        if 'TRG' in active and 'ACT' in active:
            state = f'Recording after trigger · Cine {s.active_slot}'
        elif 'WTR' in active and 'ACT' in active:
            state = f'{"Armed" if s.can_trigger else "Buffering"} · Cine {s.active_slot}'
        else:
            count = sum(1 for n, flags in s.slots if n > 0 and 'STR' in flags)
            state = f'{count} stored {"Cine" if count == 1 else "Cines"}' if count else 'Ready to arm'
        if s.direct_recording:
            state = 'Direct-to-media recording mode'
        self.state.setText(state)
        self.fps.setToolTip('Type a capture rate or use the arrow to choose a resolution and rate.'
                            if s.internal_clock else 'Frame rate is set by external sync.')
        self.exposure.setToolTip('Sensor exposure time' if s.manual_exposure else 'Exposure is set by the camera’s current mode.')

    def _schedule(self):
        if self.active and self.session and self.snapshot and not self.pending and not self.read_pending:
            due = min(self.next_status, self.next_frame) if self.preview_on else self.next_status
            self.timer.start(max(0, ceil((due - monotonic()) * 1000)))

    def _tick(self):
        if not self.session or self.pending or self.read_pending:
            return
        now = monotonic()
        if now >= self.next_status:
            self.next_status = now + 1.
            command = 'status'
        elif self.preview_on and now >= self.next_frame:
            self.next_frame = now + 1. / 30
            command = 'preview'
        else:
            self._schedule()
            return
        self.read_pending = True
        self.session.submit(command)

    def _toggle_preview(self):
        if self.preview_on:
            self._pause_preview('Preview paused')
        elif self.session:
            self.preview_on = True
            self.frames, self.preview_clock = 0, monotonic()
            self.preview_info.setText('Resuming live preview…')
            self.next_frame = 0.
            self._controls()
            self._schedule()

    def _pause_preview(self, message):
        self.preview_on = False
        self.preview_info.setText(message)
        if self.session:
            self.session.submit('stop_preview')
        self._controls()
        self._schedule()

    def _mode(self):
        s = self.snapshot
        return self.draft_mode or (s.width, s.height, s.binning)

    def _rate_menu(self, menu):
        menu.clear()
        if self.snapshot is None:
            return
        for option in self.snapshot.rate_options():
            text = f'{number(option.fps)} fps   ·   {geometry_label(option.mode)}'
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(option.fps == self.fps.value() and option.mode == self._mode())
            action.triggered.connect(lambda checked=False, item=option: self._select_rate(item))
        if menu.isEmpty():
            menu.addAction('Camera rate presets unavailable').setEnabled(False)
        add_custom(menu, self.fps)

    def _select_rate(self, option):
        if not self.rate_presets.isEnabled():
            return
        s = self.snapshot
        old_mode = self.draft_mode
        self.draft_mode = option.mode if option.mode != (s.width, s.height, s.binning) else None
        if self.draft_mode:
            self.edits.add('mode')
        else:
            self.edits.discard('mode')
            if old_mode and 'before_percent' in self.edits:
                self.post.setValue(round(s.capacity * (100 - self.position.value()) / 100))
                self.edits.discard('before_percent')
        self.geometry_note.setText(geometry_label(self._mode()))
        with QSignalBlocker(self.fps):
            self.fps.setValue(option.fps)
        self._rate_changed()

    def _exposure_menu(self, menu):
        menu.clear()
        if self.snapshot is None:
            return
        longest = self.snapshot.longest_exposure_us(self.fps.value())
        if longest is not None and longest >= self.exposure.minimum():
            menu.addAction(f'Longest · {number(longest)} µs',
                           lambda: self.exposure.setValue(longest))
            menu.addSeparator()
        for value in (10000, 5000, 2000, 1000, 500, 250, 100, 50, 20, 10, 5, 2, 1):
            if self.exposure.minimum() <= value <= self.exposure.maximum() and (longest is None or value < longest):
                menu.addAction(f'{number(value)} µs',
                               lambda checked=False, us=value: self.exposure.setValue(us))
        add_custom(menu, self.exposure)

    def _update_exposure_limit(self):
        s = self.snapshot
        limit = s.longest_exposure_us(self.fps.value())
        minimum = min(s.exposure_us, s.min_exposure_us or .001)
        maximum = max(minimum, limit) if limit is not None else max(s.exposure_us, 1e6 / self.fps.value())
        # Always represent the applied reading, including an externally controlled exposure.
        if 'fps' not in self.edits and self.fps.value() == round(s.fps, 3):
            maximum = max(maximum, s.exposure_us)
        with QSignalBlocker(self.exposure):
            self.exposure.setRange(minimum, maximum)

    def _rate_changed(self):
        if self.snapshot is None:
            return
        self._edited('fps')
        self._update_exposure_limit()
        if self.snapshot.manual_exposure:
            self._edited('exposure_us')
        self._update_position()
        self._controls()

    def _draft_issue(self):
        if not self.edits:
            return ''
        s = self.snapshot
        if 'fps' in self.edits or self.draft_mode:
            maximum = s.max_rate_for(self._mode())
            if maximum is not None and self.fps.value() > maximum:
                return f'This image size supports up to {number(maximum)} fps. Choose a preset to change size.'
        if 'exposure_us' in self.edits or 'fps' in self.edits:
            limit = s.longest_exposure_us(self.fps.value())
            if s.manual_exposure and limit is not None and self.exposure.value() > limit:
                return f'Choose an exposure of {number(limit)} µs or shorter.'
        return ''

    def _update_position(self):
        s = self.snapshot
        if s is None:
            return
        rate = self.fps.value()
        self.frames_after_row.setRowVisible(self.post, self.draft_mode is None)
        if self.draft_mode is None:
            count = s.capacity
            after = self.post.value()
            with QSignalBlocker(self.position):
                self.position.setValue(round(100 * (count - after) / max(1, count)))
            title = 'Record time'
        else:
            width, height, _ = self.draft_mode
            count = round(s.capacity * s.width * s.height / (width * height))
            after = round(count * (100 - self.position.value()) / 100)
            title = 'Estimated record time'
        before = count - after
        self.breakdown.setText(f'{before:,} before · {after:,} after')
        self.before.setText(f'Before · {duration(before / rate)}')
        self.after.setText(f'After · {duration(after / rate)}')
        self.record_time.setText(f'{title} · {duration(count / rate)}')
        self.record_time.setToolTip('Estimated from the current RAM capacity and the new image size. '
                                    'Apply settings to read the exact capacity.' if self.draft_mode else
                                    f'{count:,} frames at {number(rate)} fps.')

    def _slider_changed(self, percent):
        if self.snapshot is None:
            return
        if self.draft_mode is None:
            self.post.setValue(round(self.snapshot.capacity * (100 - percent) / 100))
        else:
            self.edits.add('before_percent')
            self._update_position()
            self._controls()

    def _post_changed(self):
        self._update_position()
        self._edited('post_frames')

    def _edited(self, field):
        if self.snapshot is None:
            return
        box = {'fps':self.fps, 'exposure_us':self.exposure, 'post_frames':self.post}[field]
        value = getattr(self.snapshot, field)
        if value == box.value() or (isinstance(box, QDoubleSpinBox) and
                                    round(value, box.decimals()) == box.value()):
            self.edits.discard(field)
        else:
            self.edits.add(field)
        self._controls()

    def _send(self, command, argument=None):
        if self.pending or not self.session:
            return
        self.pending = command
        self.timer.stop()
        self._controls()
        self.session.submit(command, argument)

    def _revert(self):
        if self.snapshot is not None:
            self._readback(self.snapshot, reset=True)
            self._controls()

    def _apply(self):
        if self.apply_button.isEnabled():
            values = {'fps':self.fps.value(), 'exposure_us':self.exposure.value(),
                      'post_frames':self.post.value()}
            changes = {key: values[key] for key in values if key in self.edits}
            if self.draft_mode is not None:
                changes['mode'] = self.draft_mode
                if 'before_percent' in self.edits or 'post_frames' in self.edits:
                    changes.pop('post_frames', None)
                    changes['before_percent'] = self.position.value()
            self._send('apply', changes)

    def _csr(self):
        if not self.csr_button.isEnabled():
            return
        s = self.snapshot
        confirm = QMessageBox(self)
        confirm.setWindowTitle('Capture black reference')
        confirm.setText(f'Run a current session reference (CSR) on {s.model}?')
        instruction = ('The camera will briefly close its shutter.' if s.capping_shutter else
                       'Cover the lens before starting and keep it covered until the reference finishes.')
        confirm.setInformativeText(f'{instruction} This interrupts pre-trigger buffering.')
        confirm.addButton(QMessageBox.Cancel)
        confirm.setDefaultButton(QMessageBox.Cancel)
        run = confirm.addButton('Run CSR', QMessageBox.AcceptRole)
        confirm.exec()
        if confirm.clickedButton() is run and self.csr_button.isEnabled() and self.snapshot.serial == s.serial:
            self._send('csr')

    def _arm(self):
        if not self.arm_button.isEnabled():
            return
        s = self.snapshot
        token = None
        if not s.can_arm:
            token = s.replaceable[0]
            confirm = QMessageBox(self)
            confirm.setWindowTitle('Replace stored recording?')
            confirm.setIcon(QMessageBox.Warning)
            confirm.setText(f'Arming will erase RAM Cine {token.slot} on {s.model} (S/N {s.serial}).')
            confirm.setInformativeText('Save any footage you want to keep before replacing it.')
            confirm.addButton(QMessageBox.Cancel)
            confirm.setDefaultButton(QMessageBox.Cancel)
            replace_button = confirm.addButton('Replace and arm', QMessageBox.DestructiveRole)
            confirm.exec()
            if confirm.clickedButton() is not replace_button:
                return
        self._send('arm', token)

    def _trigger(self):
        if self.trigger_button.isEnabled():
            # A fast re-arm can expose a second partition within the same double-click.
            deadline = monotonic() + 1.
            self.trigger_guard.start(QApplication.doubleClickInterval() + 1)
            self._send('trigger', (self.snapshot.active_slot, deadline))
