"""Live setup and recording controls for directly connected Phantom cameras."""
from math import ceil, floor
from time import monotonic

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QSignalBlocker, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (QApplication, QComboBox, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QSpinBox, QStyle,
                               QStyleOptionButton, QVBoxLayout, QWidget)

from app_runtime import user_data_dir
from camera_control_ui_worker import CameraWorker
from camera_ethernet_ui import CameraEthernetDialog
from camera_monitor import LINEAR_CURVE, MonitorLevels, auto_curve
from camera_curve_ui import MonitorCurveEditor
from camera_capture_widgets import (TRIGGER_STYLE, add_custom, duration, geometry_label,
                                    number, preset_field)
from event_camera import validate_address
from event_camera_discovery import camera_groups
from event_camera_picker import _Discover
from terminal_workspace import TOOLBAR_SPACING
from view_inspection import LiveInspectionView


class _Preview(LiveInspectionView):
    frameAvailable = Signal(bool)

    def __init__(self):
        super().__init__()
        self.image = self.pixels = None
        self.levels = MonitorLevels()
        self.mode = 'linear'
        self.curve = LINEAR_CURVE
        self.message = 'Connect a camera to see the live image.'
        self.setAccessibleName('Live camera image')
        self.setMinimumSize(160, 110)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self):
        return QSize(640, 420)

    def clear(self, message):
        self.image = self.pixels = None
        self.levels.reset()
        self.frameAvailable.emit(False)
        self.message = message
        self.refresh_image()

    def set_mode(self, mode, curve=LINEAR_CURVE):
        self.mode = mode
        self.curve = curve
        if self.pixels is not None:
            self.set_frame(self.pixels)

    def set_frame(self, pixels):
        first_frame = self.pixels is None
        self.pixels = pixels
        array = self.levels.render(pixels, self.mode, curve=self.curve)
        height, width = array.shape
        self.image = QImage(array.data, width, height, int(array.strides[0]),
                            QImage.Format_Grayscale8).copy()
        if first_frame:
            self.frameAvailable.emit(True)
        self.refresh_image()


class _MonitorButton(QPushButton):
    """Keep the monitor toolbar steady when switching transport and tone labels."""

    def __init__(self, text, labels):
        super().__init__(text)
        self.labels = labels

    def sizeHint(self):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        widest = max(self.labels, key=option.fontMetrics.horizontalAdvance)
        content = option.fontMetrics.size(Qt.TextShowMnemonic, widest)
        return self.style().sizeFromContents(QStyle.CT_PushButton, option, content, self)


class _MonitorFooter(QWidget):
    def __init__(self, status, buttons):
        super().__init__()
        self.setObjectName('camera_monitor_footer')
        self.status = status
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.actions = QWidget()
        controls = QHBoxLayout(self.actions)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(TOOLBAR_SPACING)
        for button in buttons:
            controls.addWidget(button)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(TOOLBAR_SPACING)
        self.grid.setColumnStretch(0, 1)
        self.stacked = None
        self._arrange()

    def _arrange(self):
        text_width = self.status.fontMetrics().horizontalAdvance(self.status.text())
        required = text_width + TOOLBAR_SPACING + self.actions.sizeHint().width()
        stacked = bool(text_width and self.width() < required)
        if stacked == self.stacked:
            return
        self.stacked = stacked
        if self.grid.indexOf(self.status) >= 0:
            self.grid.removeWidget(self.status)
            self.grid.removeWidget(self.actions)
        if stacked:
            self.grid.addWidget(self.status, 0, 0, 1, 2)
            self.grid.addWidget(self.actions, 1, 0, 1, 2, Qt.AlignRight)
        else:
            self.grid.addWidget(self.status, 0, 0)
            self.grid.addWidget(self.actions, 0, 1, Qt.AlignRight)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()

    def event(self, event):
        if event.type() == QEvent.LayoutRequest:
            self._arrange()
        return super().event(event)


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
        self.selected_serial = ''
        self.auto_connect = True
        self.discovery_retry = QTimer(self, interval=4000, singleShot=True)
        self.discovery_retry.timeout.connect(self._retry_discovery)
        self.workers = set()
        self.discovery = self.session = self.snapshot = None
        self.read_pending = False
        self.pending = ''
        # Only user commits enter these dictionaries. A pending request and any
        # later choices have separate lifetimes because the camera rounds settings.
        self.sending = {}
        self.queued = {}
        self.typing = set()
        self.drag_start = self.drag_percent = None
        self.commit_timer = QTimer(self)
        self.commit_timer.setSingleShot(True)
        self.commit_timer.timeout.connect(self._flush_settings)
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
        layout.setSpacing(TOOLBAR_SPACING)
        top = QHBoxLayout()
        top.setSpacing(TOOLBAR_SPACING)
        self.camera_title = QLabel('Camera Control')
        self.camera_title.setTextFormat(Qt.PlainText)
        font = self.camera_title.font()
        font.setBold(True)
        font.setPointSizeF(max(14., font.pointSizeF() + 3))
        self.camera_title.setFont(font)
        self.camera_specs = _label()
        self.camera_specs.setObjectName('camera_identity_summary')
        self.camera_specs.hide()
        heading = QVBoxLayout()
        heading.setSpacing(1)
        heading.addWidget(self.camera_title)
        heading.addWidget(self.camera_specs)
        top.addLayout(heading, 1)
        self.cameras = QComboBox()
        self.cameras.setAccessibleName('Camera connection')
        self.cameras.setPlaceholderText('Looking for a camera…')
        self.cameras.setMinimumContentsLength(17)
        self.cameras.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cameras.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cameras.setMaximumWidth(360)
        top.addWidget(self.cameras)
        self.refresh = QPushButton('Refresh')
        self.refresh.setAccessibleName('Refresh cameras')
        network = QPushButton('Ethernet setup')
        network.setAccessibleName('Ethernet setup')
        network.setToolTip('Check the wired link speed and camera subnet.')
        self.ethernet = CameraEthernetDialog(self)
        network.clicked.connect(self._open_ethernet)
        self.ethernet.networkChanged.connect(self._discover)
        self.ethernet.finished.connect(lambda: self._discover())
        self.connect_button = QPushButton('Connect')
        top.addWidget(network)
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
        body.setSpacing(TOOLBAR_SPACING)
        layout.addLayout(body, 1)
        left = QVBoxLayout()
        left.setSpacing(TOOLBAR_SPACING)
        body.addLayout(left, 1)
        self.view = _Preview()
        left.addWidget(self.view, 1)
        self.preview_info = _label()
        self.preview_button = _MonitorButton('Pause preview', ('Pause preview', 'Resume preview'))
        self.preview_button.setToolTip('Pause the monitor while the camera continues operating.')
        self.curve_editor = MonitorCurveEditor(self)
        self.auto_button = QPushButton('Auto')
        self.auto_button.setAccessibleName('Auto monitor brightness')
        self.auto_button.setToolTip('Set the monitor curve from this frame. Click again to update it. '
                                    'Raw Cine recordings keep their original pixels.')
        self.auto_button.setEnabled(False)
        self.view.frameAvailable.connect(self.auto_button.setEnabled)
        self.auto_button.clicked.connect(self._auto_brightness)
        preset = self.curve_editor.preset
        self.brightness_button = _MonitorButton('Linear', [preset.itemText(i) for i in range(preset.count())])
        self.brightness_button.setProperty('pcaMenu', True)
        self.brightness_button.setAccessibleName('Monitor brightness presets')
        self.brightness_button.setToolTip('Choose or edit the monitor brightness curve.')
        self.brightness_menu = QMenu(self.brightness_button)
        self.brightness_button.setMenu(self.brightness_menu)
        self._populate_brightness_menu()
        self.brightness_menu.triggered.connect(self._select_brightness)
        self.curve_editor.changed.connect(self._brightness_changed)
        left.addWidget(_MonitorFooter(self.preview_info, (self.preview_button, self.auto_button,
                                                          self.brightness_button)))

        scroll = QScrollArea()
        scroll.setObjectName('camera_settings_scroll')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(274)
        scroll.setMaximumWidth(322)
        sidebar = QWidget()
        sidebar.setObjectName('camera_settings_rail')
        rail = QVBoxLayout(sidebar)
        rail.setContentsMargins(0, 0, 0, 0)
        rail.setSpacing(8)
        capture, controls = _group('Capture settings')
        controls.setSpacing(4)
        form = self.capture_form = QFormLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(5)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.fps, self.rate_presets, rate_field = preset_field(
            'Capture frame rate', ' fps', rate_table=True)
        self.exposure, self.exposure_presets, exposure_field = preset_field(
            'Exposure time', ' µs')
        self.rate_presets.chosen.connect(self._select_rate)
        self.rate_presets.setToolTip('Choose a frame rate and output resolution supported by this camera.')
        self._exposure_state = None
        self._exposure_menu_open = False
        exposure_menu = self.exposure_presets.menu()
        exposure_menu.aboutToShow.connect(self._exposure_menu_showing)
        exposure_menu.aboutToHide.connect(lambda: QTimer.singleShot(0, self, self._exposure_menu_closed))
        form.addRow('Frame rate', rate_field)
        form.addRow('Exposure', exposure_field)
        self.geometry_note = _label()
        self.geometry_note.setToolTip('Output width × height. A smaller sensor window crops the view. Binning combines pixels.')
        form.addRow('Image size', self.geometry_note)
        controls.addLayout(form)
        form.addRow(QLabel('Trigger position'))
        self.position = QSlider(Qt.Horizontal)
        self.position.setAccessibleName('Trigger position, percent before trigger')
        self.position.setRange(0, 100)
        self.position.setStyleSheet(TRIGGER_STYLE)
        self.position.setToolTip('Green shows time before the trigger. Red shows time after it.')
        form.addRow(self.position)
        ends = QHBoxLayout()
        self.before = _label('Before')
        ends.addWidget(self.before)
        ends.addStretch()
        self.after = _label('After')
        self.after.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ends.addWidget(self.after)
        form.addRow(ends)
        self.record_time = _label()
        self.record_time.setAccessibleName('Total recording time')
        form.addRow(self.record_time)
        self.post = QSpinBox()
        self.post.setAccessibleName('Frames after trigger')
        self.post.setGroupSeparatorShown(True)
        form.addRow('Frames after', self.post)
        self.breakdown = _label()
        controls.addWidget(self.breakdown)
        self.draft_note = _label()
        self.draft_note.setAccessibleName('Capture settings status')
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
        record_buttons.setSpacing(TOOLBAR_SPACING)
        record_buttons.addWidget(self.arm_button, 1)
        record_buttons.addWidget(self.trigger_button, 1)
        actions.addLayout(record_buttons)
        rail.addWidget(record)
        rail.addStretch()
        scroll.setWidget(sidebar)
        body.addWidget(scroll)
        self.notice = _label()
        self.notice.hide()
        layout.addWidget(self.notice)
        self.preview_on = False
        self.cameras.currentIndexChanged.connect(self._source_changed)
        self.cameras.activated.connect(self._camera_chosen)
        self.refresh.clicked.connect(self._discover)
        self.connect_button.clicked.connect(self._connect_clicked)
        self.address.returnPressed.connect(self._connect_clicked)
        self.preview_button.clicked.connect(self._toggle_preview)
        self.editors = {'fps': self.fps, 'exposure_us': self.exposure, 'post_frames': self.post}
        for field, editor in self.editors.items():
            editor.setKeyboardTracking(False)
            editor.lineEdit().textEdited.connect(lambda _, key=field: self._typing_started(key))
            editor.valueChanged.connect(lambda _, key=field: self._number_changed(key))
            editor.editingFinished.connect(lambda key=field: self._typing_finished(key))
        self.position.sliderPressed.connect(self._drag_started)
        self.position.valueChanged.connect(self._slider_changed)
        self.position.sliderReleased.connect(self._drag_finished)
        self.csr_button.clicked.connect(self._csr)
        self.arm_button.clicked.connect(self._arm)
        self.trigger_button.clicked.connect(self._trigger)
        self._controls()

    def _set_notice(self, message=''):
        self.notice.setText(message)
        self.notice.setVisible(bool(message))

    def _start(self, worker):
        self.workers.add(worker)
        worker.finished.connect(self._finished)
        worker.start()

    def _auto_brightness(self):
        pixels = self.view.pixels
        if pixels is not None:
            self.curve_editor.set_auto_curve(auto_curve(pixels))
            self.curve_editor.open_at(self.brightness_button)

    def _populate_brightness_menu(self):
        preset = self.curve_editor.preset
        for index in range(preset.count()):
            action = self.brightness_menu.addAction(preset.itemText(index))
            action.setData(index)
            action.setCheckable(True)
            action.setChecked(index == preset.currentIndex())
            action.setVisible(preset.model().item(index).isEnabled())
            action.setToolTip(preset.itemData(index, Qt.ToolTipRole))
        self.brightness_menu.addSeparator()
        self.brightness_menu.addAction('Edit curve…')

    def _select_brightness(self, action):
        index = action.data()
        if index is not None:
            self.curve_editor.preset.setCurrentIndex(index)
        self.curve_editor.open_at(self.brightness_button)

    def _brightness_changed(self, mode, curve):
        preset = self.curve_editor.preset
        self.brightness_button.setText(preset.currentText())
        for action in self.brightness_menu.actions():
            index = action.data()
            if index is not None:
                action.setChecked(index == preset.currentIndex())
                action.setVisible(preset.model().item(index).isEnabled())
        self.view.set_mode(mode, curve)

    def set_active(self, active):
        if self.active == active:
            return
        self.active = active
        self.needs_discovery = active
        if active:
            self.selected_serial = ''
            self.auto_connect = True
            self.cameras.clear()
            self.cameras.setPlaceholderText('Looking for a camera…')
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
        self.discovery_retry.stop()
        self.curve_editor.hide()
        self.brightness_menu.close()
        if self.discovery is not None:
            self.discovery.requestInterruption()
            self.discovery = None
        if self.session is not None:
            self.session.stop()
            self.session = None
        self.timer.stop()
        self.snapshot = None
        self.rate_presets.popup.close()
        self.exposure_presets.menu().close()
        self._update_preset_menus()
        self.pending = ''
        self.read_pending = False
        self.preview_on = False
        self.commit_timer.stop()
        self.queued.clear()
        self.sending.clear()
        self.typing.clear()
        self.drag_start = self.drag_percent = None
        self.state.setText('Disconnected')
        self.breakdown.clear()
        self.geometry_note.clear()
        self.record_time.clear()
        self.before.setText('Before')
        self.after.setText('After')
        self.preview_info.clear()
        self._set_notice()
        self.view.clear('Connect a camera to see the live image.')
        self.curve_editor.reset_default()
        self.camera_title.setText('Camera Control')
        self.camera_specs.clear()
        self.camera_specs.hide()
        self.ethernet.set_camera('')
        self._controls()

    def _open_ethernet(self):
        if not self.snapshot:
            peer = self.cameras.currentData(Qt.UserRole + 1)
            if peer:
                name = peer.name if peer.name != peer.serial else ''
                self.ethernet.set_candidate(peer.host, name, peer.serial, peer.setup_device)
            else:
                self.ethernet.set_candidate('')
        self.ethernet.open()

    def _manual_selected(self):
        return self.cameras.currentIndex() >= 0 and self.cameras.currentData() is None

    def _retry_discovery(self):
        if self.cameras.view().isVisible():
            self.discovery_retry.start()
        elif not self._manual_selected():
            self._discover(background=True)

    def _discover(self, *, background=False):
        if not self.active or self.session or self.discovery:
            return
        self.discovery_retry.stop()
        self.needs_discovery = False
        self.discovery = _Discover(self)
        self.discovery.background = background
        if not background:
            self._set_notice('Looking for Phantom cameras on Ethernet…')
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
            self._set_notice(message)
        elif self.active and self.needs_discovery and not self.workers:
            self._discover()
        self._controls()
        worker.deleteLater()

    def _list_cameras(self, worker):
        # A periodic search must not interrupt a manual address or a menu selection.
        if worker.background and (self._manual_selected() or self.cameras.view().isVisible()):
            if not self._manual_selected():
                self.discovery_retry.start()
            return
        current = self.cameras.currentData()
        manual = self._manual_selected()
        previous = self.settings.value('last/host', '', type=str)
        previous_serial = self.settings.value('last/serial', '', type=str)
        previous_model = self.settings.value('last/model', '', type=str)
        groups = camera_groups(worker.cameras)
        selected = 0 if groups and not self.selected_serial else -1
        with QSignalBlocker(self.cameras):
            self.cameras.clear()
            for serial, group in groups.items():
                hosts = tuple(dict.fromkeys(c.host for c in group))
                name = next((c.name for c in group if c.name and c.name != serial), '')
                model = previous_model if serial == previous_serial else ''
                label = f'{name or model or "Phantom"} · S/N {serial}'
                if group[0].connection_issue:
                    label += ' · Setup needed' if group[0].needs_network_setup else f' · PH{group[0].protocol}'
                row = self.cameras.count()
                self.cameras.addItem(label, (hosts, serial))
                self.cameras.setItemData(row, group[0], Qt.UserRole + 1)
                if self.selected_serial == serial or (current and current[1] == serial):
                    selected = row
            if previous and previous_serial not in groups and not any(previous == c.host for c in worker.cameras):
                try:
                    host = validate_address(previous)
                except ValueError:
                    pass
                else:
                    self.cameras.addItem(f'{previous_model or "Phantom"} · Previously used',
                                         ((host,), previous_serial))
                    if current == ((host,), previous_serial):
                        selected = self.cameras.count() - 1
            self.cameras.addItem('Enter address manually…', None)
            self.cameras.setPlaceholderText('Waiting for a camera…')
            self.cameras.setCurrentIndex(self.cameras.count()-1 if manual else selected)
        self._source_changed()
        selected_peer = self.cameras.currentData(Qt.UserRole + 1)
        if not manual:
            if not groups:
                self._set_notice((worker.error or 'No camera replied on Ethernet.') + ' Searching automatically.')
            elif selected_peer and selected_peer.connection_issue:
                self._set_notice(selected_peer.connection_issue)
            else:
                self._set_notice('Select a camera and connect.')
        if self.auto_connect and len(groups) == 1 and selected_peer and not selected_peer.connection_issue:
            self._connect_clicked()
        elif not manual and (not groups or any(c.needs_network_setup for c in worker.cameras)):
            self.discovery_retry.start()

    def _camera_chosen(self):
        data = self.cameras.currentData()
        self.selected_serial = data[1] if data else ''
        peer = self.cameras.currentData(Qt.UserRole + 1)
        if self._manual_selected():
            self._set_notice('Enter the address printed on the camera, then connect.')
        else:
            self._set_notice(peer.connection_issue if peer and peer.connection_issue else 'Select Connect to open live view.')

    def _source_changed(self):
        self.address.setVisible(self.snapshot is None and self._manual_selected())
        if self._manual_selected():
            self.discovery_retry.stop()
        peer = self.cameras.currentData(Qt.UserRole + 1)
        if self.session is None:
            self.connect_button.setText('Set up Ethernet…' if peer and peer.needs_network_setup else 'Connect')
            enabled = self.cameras.currentIndex() >= 0 and (not peer or not peer.connection_issue or peer.needs_network_setup)
            self.connect_button.setEnabled(bool(enabled and self.active and not self.workers and not self.discovery))

    def _connect_clicked(self):
        if self.session:
            self.auto_connect = False
            self._stop()
            return
        if not self.active or self.discovery or self.workers or self.cameras.currentIndex() < 0:
            return
        peer = self.cameras.currentData(Qt.UserRole + 1)
        if peer and peer.connection_issue:
            self._set_notice(peer.connection_issue)
            if peer.needs_network_setup:
                self._open_ethernet()
            return
        self.discovery_retry.stop()
        data = self.cameras.currentData()
        if data is None:
            try:
                host = validate_address(self.address.text())
            except ValueError as exc:
                self._set_notice(str(exc))
                return
            data = ((host,), '')
        hosts, serial = data
        if self.camera_in_use and self.camera_in_use(hosts, serial):
            self._set_notice('Event Finder is scanning this camera. Finish or cancel that scan before starting live view.')
            return
        self.auto_connect = True
        self.session = CameraWorker(hosts, serial, self)
        self.session.reply.connect(self._reply)
        self.pending = 'connect'
        self._set_notice('Connecting to the camera…')
        self.view.clear('Connecting…')
        self.curve_editor.reset_default()
        self._controls()
        self._start(self.session)

    def _controls(self):
        ready = self.snapshot is not None and self.session is not None
        editing = ready and self.snapshot.can_apply and self.pending in ('', 'apply')
        settled = not (self.pending or self.queued or self.typing or self.drag_start is not None)
        self.cameras.setVisible(not ready)
        self.refresh.setVisible(not ready)
        self._source_changed()
        self.cameras.setEnabled(self.active and not self.session and self.cameras.count() > 0)
        self.address.setEnabled(self.cameras.isEnabled())
        self.refresh.setEnabled(self.active and not self.session and not self.workers and not self.discovery)
        if self.session:
            self.connect_button.setText('Disconnect')
            self.connect_button.setEnabled(self.active and not self.pending)
        self.preview_button.setEnabled(ready)
        self.preview_button.setText('Pause preview' if self.preview_on else 'Resume preview')
        self.fps.setEnabled(editing and self.snapshot.internal_clock)
        self.rate_presets.setEnabled(self.fps.isEnabled())
        if not self.fps.isEnabled() and self.rate_presets.popup.isVisible():
            self.rate_presets.popup.close()
        self.exposure.setEnabled(editing and self.snapshot.manual_exposure)
        self.exposure_presets.setEnabled(self.exposure.isEnabled())
        self.position.setEnabled(editing)
        self.post.setEnabled(editing and self._mode() == self._camera_mode())
        self.csr_button.setVisible(bool(ready and self.snapshot.supports_csr))
        self.csr_button.setEnabled(bool(ready and self.snapshot.can_csr and settled))
        self.csr_button.setText(f'CSR · {self.snapshot.csr_remaining:,} frames left'
                                if ready and self.snapshot.csr_remaining else 'Black reference (CSR)…')
        self.draft_note.setVisible(ready)
        if ready and not self.snapshot.supports_acquisition:
            self.draft_note.setText('Live preview only for this camera model and firmware.')
        elif ready and self.snapshot.direct_recording:
            self.draft_note.setText('Capture settings are locked during direct-to-media recording.')
        elif ready and self.snapshot.csr_remaining:
            self.draft_note.setText('Capture settings unlock when the black reference finishes.')
        elif ready and not self.snapshot.can_apply:
            self.draft_note.setText('Capture settings are locked in the current recording state.')
        elif self.typing:
            self.draft_note.setText('Press Enter or Tab to set the value.')
        elif self.drag_start is not None:
            self.draft_note.setText('Release to set the trigger position.')
        elif self.pending == 'apply' or self.queued:
            self.draft_note.setText('Updating camera settings…')
        else:
            self.draft_note.setText('Capture settings update automatically.')
        replace = self.snapshot.replaceable[0] if ready and self.snapshot.replaceable and not self.snapshot.can_arm else None
        self.arm_button.setText(f'Replace Cine {replace.slot}…' if replace else 'Arm')
        self.arm_button.setToolTip('Replace the stored Cine and start buffering a new recording.' if replace else
                                   'Start buffering the next recording in camera RAM.')
        self.arm_button.setEnabled(bool(ready and settled and
                                  (self.snapshot.can_arm or self.snapshot.replaceable)))
        self.trigger_button.setEnabled(bool(ready and settled
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
                self._set_notice(error)
            return
        if result is None:
            retry = self.active and not self._manual_selected() and (
                self.snapshot is not None or self.cameras.currentData(Qt.UserRole + 1) is not None)
            self._stop()
            self._set_notice(error or 'Camera disconnected.')
            if retry:
                self.discovery_retry.start()
            return
        if command == 'apply':
            self.sending.clear()
        # A later edit can depend on a rejected rate or sensor window. Show
        # the worker's verified readback and never retry an uncertain write.
        self._readback(result, reset=command == 'connect' or (command == 'apply' and bool(error)))
        if command == 'connect':
            self.preview_on = True
            self.frames, self.preview_clock = 0, monotonic()
            self.next_status = monotonic() + 1.
            self.preview_info.setText('Starting live preview…')
            self._set_notice()
            index = self.cameras.currentIndex()
            if index >= 0 and self.cameras.itemData(index) is not None:
                self.cameras.setItemText(index, f'{result.model} · S/N {result.serial}')
            for key, val in [('host', self.session.host), ('serial', result.serial), ('model', result.model)]:
                self.settings.setValue('last/'+key, val)
            self.settings.sync()
            self.next_frame = 0.
        elif command == 'status':
            self.next_status = monotonic() + 1.
        elif error:
            self._set_notice(error)
        elif command in ('apply', 'arm', 'trigger', 'csr'):
            self._set_notice({'apply':'Capture settings read back from the camera.',
                                 'arm':'Arm command accepted.', 'trigger':'Trigger command accepted.',
                                 'csr':'Black reference command accepted.'}[command])
        self._controls()
        if self.queued and not self.pending:
            self.commit_timer.start(0)
        self._schedule()

    def _readback(self, s, reset):
        previous = self.snapshot
        self.snapshot = s
        if previous is None or (previous.model, previous.serial, previous.identity) != (s.model, s.serial, s.identity):
            self.camera_title.setText(s.model)
            details = s.identity.summary
            self.camera_specs.setText(' · '.join(value for value in (details, f'S/N {s.serial}') if value))
            self.camera_specs.setToolTip(s.identity.details)
            self.camera_specs.show()
            self.ethernet.set_camera(self.session.host, s.model, s.serial, details, s.identity.details)
        if reset:
            self.commit_timer.stop()
            self.queued.clear()
            self.sending.clear()
            self.typing.clear()
            self.drag_start = self.drag_percent = None
        unavailable = set()
        if not s.can_apply:
            unavailable.update(self.editors)
            self.drag_start = self.drag_percent = None
        if not s.internal_clock:
            unavailable.add('fps')
        if not s.manual_exposure:
            unavailable.add('exposure_us')
        if not s.can_apply or self.queued.keys() & unavailable:
            self.queued.clear()
        cancelled = self.typing & unavailable
        self.typing -= cancelled
        self._sync_editors(force=reset or bool(cancelled))
        states = dict(s.slots)
        active = states.get(s.active_slot, ())
        if 'TRG' in active and 'ACT' in active:
            state = f'Recording after trigger · Cine {s.active_slot}'
        elif 'WTR' in active and 'ACT' in active:
            state = f'{"Armed" if "ABL" in active else "Buffering"} · Cine {s.active_slot}'
        else:
            count = sum(1 for n, flags in s.slots if n > 0 and 'STR' in flags)
            state = f'{count} stored {"Cine" if count == 1 else "Cines"}' if count else 'Ready to arm'
        if s.direct_recording:
            state = 'Direct-to-media recording mode'
        self.state.setText(state)
        self.fps.setToolTip('Choose a preset for an immediate update, or enter a rate and press Enter.'
                            if s.internal_clock else 'Frame rate is set by external sync.')
        self.exposure.setToolTip('Choose a preset or enter an exposure in microseconds and press Enter.' if s.manual_exposure else 'Exposure is set by the camera’s current mode.')

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

    def _camera_mode(self):
        s = self.snapshot
        return s.width, s.height, s.binning

    def _mode(self):
        return self.queued.get('mode', self.sending.get('mode', self._camera_mode()))

    def _exposure_menu_showing(self):
        self._exposure_menu_open = True

    def _exposure_menu_closed(self):
        if not self.exposure_presets.menu().isVisible():
            self._exposure_menu_open = False
            self._update_preset_menus()

    def _update_preset_menus(self):
        s = self.snapshot
        options = s.rate_options() if s else ()
        current = (s.fps, self._camera_mode()) if s else None
        self.rate_presets.set_choices(options, current)
        # The native exposure menu needs its actions before Cocoa handles the first press.
        rate = self.fps.value()
        state = (s.longest_exposure_us(rate), self.exposure.minimum(), self.exposure.maximum()) if s else ()
        if not self._exposure_menu_open and self._exposure_state != state:
            self._exposure_menu(self.exposure_presets.menu())
            self._exposure_state = state

    def _select_rate(self, option):
        if not self.rate_presets.isEnabled() or self.snapshot is None:
            return
        if option not in self.snapshot.rate_options():
            self._set_notice('The available capture modes changed. Reopen the frame-rate table and choose a resolution.')
            return
        self._set_rate(option.fps, option.mode)

    def _exposure_menu(self, menu):
        menu.clear()
        if self.snapshot is None:
            return
        longest = self.snapshot.longest_exposure_us(self.fps.value())
        if longest is not None and longest >= self.exposure.minimum():
            menu.addAction(f'Longest · {number(longest)} µs',
                           lambda: self._queue_settings(exposure_us=longest))
            menu.addSeparator()
        for value in (10000, 5000, 2000, 1000, 500, 250, 100, 50, 20, 10, 5, 2, 1):
            if self.exposure.minimum() <= value <= self.exposure.maximum() and (longest is None or value < longest):
                menu.addAction(f'{number(value)} µs',
                               lambda checked=False, us=value: self._queue_settings(exposure_us=us))
        add_custom(menu, self.exposure)

    @staticmethod
    def _merge_settings(target, changes):
        # Exact frame count and percentage are alternative trigger positions.
        if 'post_frames' in changes:
            target.pop('before_percent', None)
        if 'before_percent' in changes:
            target.pop('post_frames', None)
        target.update(changes)

    def _intent(self):
        wanted = self.sending.copy()
        self._merge_settings(wanted, self.queued)
        return wanted

    def _sync_editors(self, force=False):
        s = self.snapshot
        wanted = self._intent()
        rate = wanted.get('fps', s.fps)
        exposure = wanted.get('exposure_us', s.exposure_us)
        minimum = min(s.exposure_us, s.min_exposure_us or .001)
        longest = s.longest_exposure_us(rate)
        maximum = longest if longest is not None else floor(1e9 / rate) / 1000
        if rate == s.fps:
            maximum = max(maximum, s.exposure_us)

        def sync(field, low, high, value):
            if field in self.typing:
                return
            editor = self.editors[field]
            with QSignalBlocker(editor):
                if (editor.minimum(), editor.maximum()) != (low, high):
                    editor.setRange(low, high)
                if force or editor.value() != value:
                    editor.setValue(value)

        sync('fps', min(s.fps, s.min_fps or .001), max(s.fps, s.max_fps or 1e9), rate)
        sync('exposure_us', minimum, max(minimum, maximum), exposure)

        mode = self._mode()
        resizing = mode != self._camera_mode()
        count = round(s.capacity * s.width * s.height / (mode[0] * mode[1])) if resizing else s.capacity
        percent = self.drag_percent if self.drag_percent is not None else wanted.get('before_percent')
        if percent is not None:
            after = round(count * (1 - percent / 100))
        elif resizing:
            after = round(count * wanted.get('post_frames', s.post_frames) / max(1, s.capacity))
        else:
            after = wanted.get('post_frames', s.post_frames)
        self.capture_form.setRowVisible(self.post, not resizing)
        if not resizing:
            sync('post_frames', 0, s.capacity, after)
        before = count - after
        with QSignalBlocker(self.position):
            self.position.setValue(round(percent if percent is not None else 100 * before / max(1, count)))
        self.geometry_note.setText(geometry_label(mode))
        self.breakdown.setText(f'{before:,} before · {after:,} after')
        self.before.setText(f'Before · {duration(before / rate)}')
        self.after.setText(f'After · {duration(after / rate)}')
        title = 'Estimated record time' if resizing else 'Record time'
        self.record_time.setText(f'{title} · {duration(count / rate)}')
        self.record_time.setToolTip('Exact capacity will appear when the camera confirms the new image size.'
                                    if resizing else f'{count:,} frames at {number(rate)} fps.')
        self._update_preset_menus()

    def _set_rate(self, rate, mode=None):
        target = mode or self._mode()
        limit = self.snapshot.max_rate_for(target)
        if limit is not None and rate > limit:
            self._set_notice(f'This image size supports up to {number(limit)} fps. Choose a preset to change size.')
            self._sync_editors()
            self._controls()
            return
        changes = {'fps': rate}
        if mode is not None:
            changes['mode'] = mode
            if mode != self._camera_mode() and 'post_frames' in self.queued:
                changes['before_percent'] = 100 * (1 - self.queued['post_frames'] / max(1, self.snapshot.capacity))
        exposure = self.exposure.value()
        longest = self.snapshot.longest_exposure_us(rate)
        if self.snapshot.manual_exposure:
            if longest is None:
                # Older firmware may omit sensor dead time. The frame period is
                # still a hard upper bound. The camera validates the actual value.
                longest = floor(1e9 / rate) / 1000
                if self.snapshot.exposure_dead_us is not None or longest < (self.snapshot.min_exposure_us or .001):
                    self._set_notice('This rate leaves too little time for the minimum exposure. Choose a lower rate.')
                    self._sync_editors()
                    self._controls()
                    return
            if exposure > longest:
                changes['exposure_us'] = longest
        self._queue_settings(**changes)

    def _typing_started(self, field):
        if self.editors[field].isEnabled():
            self.typing.add(field)
            self._controls()

    def _typing_finished(self, field):
        if field in self.typing:
            self._number_changed(field)

    def _number_changed(self, field):
        self.typing.discard(field)
        if self.snapshot is None or not self.editors[field].isEnabled():
            self._controls()
            return
        value = self.editors[field].value()
        if field == 'fps':
            self._set_rate(value)
        else:
            self._queue_settings(**{field: value})

    def _drag_started(self):
        self.drag_start = self.position.value()
        self._controls()

    def _slider_changed(self, percent):
        if self.snapshot is None or not self.position.isEnabled():
            return
        if self.position.isSliderDown():
            self.drag_percent = percent if self.drag_start is not None and percent != self.drag_start else None
            self._sync_editors()
            self._controls()
        else:
            self._set_position(percent)

    def _drag_finished(self):
        percent = self.drag_percent
        self.drag_start = self.drag_percent = None
        if percent is not None:
            self._set_position(percent)
        else:
            self._controls()

    def _set_position(self, percent):
        if self._mode() != self._camera_mode() or 'mode' in self.sending:
            self._queue_settings(before_percent=percent)
        else:
            self._queue_settings(post_frames=round(self.snapshot.capacity * (1 - percent / 100)))

    def _queue_settings(self, **changes):
        if self.snapshot is None or not self.snapshot.can_apply or self.pending not in ('', 'apply'):
            return
        self._merge_settings(self.queued, changes)
        self._sync_editors()
        self._controls()
        if not self.pending:
            # Group the signals from a single gesture into one camera request.
            self.commit_timer.start(0)

    def _flush_settings(self):
        if self.pending or self.snapshot is None or not self.session or not self.queued:
            return
        s = self.snapshot
        current = {'fps': round(s.fps, self.fps.decimals()), 'exposure_us': round(s.exposure_us, self.exposure.decimals()),
                   'mode': self._camera_mode(), 'post_frames': s.post_frames}
        changes = {k: v for k, v in self.queued.items() if current.get(k) != v}
        if 'mode' in self.queued and ('mode' in changes or 'fps' in changes):
            # A table cell is one choice, even if either value currently matches.
            # The worker rechecks camera settings after any queued monitor read.
            changes.update(mode=self.queued['mode'], fps=self.queued['fps'])
        self.queued.clear()
        if changes.get('mode', self._camera_mode()) == self._camera_mode() and 'before_percent' in changes:
            after = round(s.capacity * (1 - changes.pop('before_percent') / 100))
            if after != s.post_frames:
                changes['post_frames'] = after
        if changes and s.can_apply:
            self.sending = changes
            self._send('apply', changes)
        else:
            self._sync_editors()
            self._controls()

    def _send(self, command, argument=None):
        if self.pending or not self.session:
            return
        self.pending = command
        self.timer.stop()
        self._controls()
        self.session.submit(command, argument)

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
