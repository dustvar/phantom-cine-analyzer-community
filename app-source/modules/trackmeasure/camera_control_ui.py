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
from event_camera import validate_address
from event_camera_picker import _Discover


class _Preview(QWidget):
    def __init__(self):
        super().__init__()
        self.image = None
        self.message = 'Connect a camera to see the live image.'
        self.setAccessibleName('Live camera image')
        self.setMinimumSize(160, 110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self):
        return QSize(640, 420)

    def clear(self, message):
        self.image, self.message = None, message
        self.update()

    def set_frame(self, array):
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


def _number(name, suffix, decimals, maximum):
    box = QDoubleSpinBox()
    box.setAccessibleName(name)
    box.setSuffix(suffix)
    box.setDecimals(decimals)
    box.setRange(10 ** -decimals, maximum)
    box.setGroupSeparatorShown(True)
    return box


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
        live_row.addWidget(self.preview_button)
        left.addLayout(live_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(244)
        scroll.setMaximumWidth(300)
        sidebar = QWidget()
        rail = QVBoxLayout(sidebar)
        rail.setContentsMargins(0, 0, 4, 0)
        capture, controls = _group('Capture settings')
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.fps = _number('Capture frame rate', ' fps', 3, 1e9)
        self.exposure = _number('Exposure time', ' µs', 3, 1e9)
        form.addRow('Frame rate', self.fps)
        form.addRow('Exposure', self.exposure)
        controls.addLayout(form)
        controls.addWidget(QLabel('Trigger position'))
        self.position = QSlider(Qt.Horizontal)
        self.position.setAccessibleName('Trigger position, percent before trigger')
        self.position.setRange(0, 100)
        self.position.setToolTip('Left puts the trigger at the start. Right puts it at the end.')
        controls.addWidget(self.position)
        ends = QHBoxLayout()
        ends.addWidget(QLabel('Start'))
        ends.addStretch()
        ends.addWidget(QLabel('End'))
        controls.addLayout(ends)
        self.post = QSpinBox()
        self.post.setAccessibleName('Frames after trigger')
        self.post.setGroupSeparatorShown(True)
        after_row = QFormLayout()
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
        rail.addWidget(capture)
        record, actions = _group('Recording')
        self.state = _label('Disconnected')
        self.arm_button = QPushButton('Arm')
        self.arm_button.setToolTip('Start buffering the next recording in camera RAM.')
        self.trigger_button = QPushButton('Trigger')
        self.trigger_button.setProperty('primaryAction', True)
        self.trigger_button.setToolTip('Send a software trigger to the armed camera.')
        actions.addWidget(self.state)
        actions.addWidget(self.arm_button)
        actions.addWidget(self.trigger_button)
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
        self.fps.valueChanged.connect(lambda: self._edited('fps'))
        self.exposure.valueChanged.connect(lambda: self._edited('exposure_us'))
        self.position.valueChanged.connect(self._slider_changed)
        self.post.valueChanged.connect(self._post_changed)
        self.apply_button.clicked.connect(self._apply)
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
        self.exposure.setEnabled(editing and self.snapshot.manual_exposure)
        self.position.setEnabled(editing)
        self.post.setEnabled(editing)
        self.apply_button.setEnabled(bool(editing and self.edits))
        self.revert_button.setEnabled(bool(ready and self.edits and not self.pending))
        self.arm_button.setText('Replace Cine and arm…' if ready and not self.snapshot.can_arm
                                and self.snapshot.replaceable else 'Arm')
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
                    self.preview_info.setText(f'Live · {rate:.1f} preview fps  ·  {result.shape[1]} × {result.shape[0]}')
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
        elif command in ('apply', 'arm', 'trigger'):
            self.notice.setText({'apply':'Capture settings read back from the camera.',
                                 'arm':'Arm command accepted.', 'trigger':'Trigger command accepted.'}[command])
        self._controls()
        self._schedule()

    def _readback(self, s, reset):
        self.snapshot = s
        if reset:
            self.edits.clear()
        limits = ((self.fps, 'fps', s.fps), (self.exposure, 'exposure_us', s.exposure_us),
                  (self.post, 'post_frames', s.post_frames))
        for widget, name, value in limits:
            with QSignalBlocker(widget):
                if name == 'post_frames':
                    widget.setMaximum(s.capacity)
                elif name == 'fps':
                    widget.setRange(min(s.fps, s.min_fps or .001), max(s.fps, s.max_fps or 1e9))
                elif name == 'exposure_us':
                    widget.setMinimum(min(s.exposure_us, s.min_exposure_us or .001))
                if name not in self.edits and widget.value() != value:
                    widget.setValue(value)
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
        self.fps.setToolTip('Capture frame rate' if s.internal_clock else 'Frame rate is set by external sync.')
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

    def _update_position(self):
        if self.snapshot is None:
            return
        count = self.snapshot.capacity
        after = self.post.value()
        with QSignalBlocker(self.position):
            self.position.setValue(round(100 * (count - after) / max(1, count)))
        self.breakdown.setText(f'{count - after:,} before · {after:,} after')

    def _slider_changed(self, percent):
        if self.snapshot:
            self.post.setValue(round(self.snapshot.capacity * (100 - percent) / 100))

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
            self._send('apply', {key: values[key] for key in self.edits})

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
