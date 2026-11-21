"""Choose a Phantom on the local Ethernet link and open one of its completed Cines."""
from concurrent.futures import CancelledError
from dataclasses import dataclass, replace

from PySide6.QtCore import QSettings, Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from app_runtime import user_data_dir
from event_camera import list_recordings, validate_address
from event_camera_discovery import discover_cameras


@dataclass(frozen=True)
class _Camera:
    serial: str
    model: str
    hosts: tuple
    previous: bool = False
    name: str = ''

    @property
    def label(self):
        fields = [self.name or self.model or 'Phantom camera']
        if self.model and self.name and self.model.casefold() != self.name.casefold():
            fields.append(self.model)
        if self.serial:
            fields.append(f'S/N {self.serial}')
        fields.append('Previously used' if self.previous else self.hosts[0])
        return '  ·  '.join(fields)


class _Discover(QThread):
    def run(self):
        self.cameras, self.error = [], ''
        try:
            self.cameras = discover_cameras(cancelled=self.isInterruptionRequested)
        except CancelledError:
            pass
        except (ValueError, OSError, RuntimeError) as exc:
            self.error = str(exc)


class _FindRecordings(QThread):
    def __init__(self, camera, parent):
        super().__init__(parent)
        self.camera = camera
        self.recordings = []
        self.error = ''

    def run(self):
        saw_empty = False
        for host in self.camera.hosts:
            if self.isInterruptionRequested():
                return
            try:
                recordings = list_recordings(host, self.isInterruptionRequested)
                if self.camera.serial and any(r.serial != self.camera.serial for r in recordings):
                    raise ValueError('A different camera is using this address. Refresh the camera list.')
                if recordings:
                    self.recordings = recordings
                    self.error = ''
                    return
                saw_empty = True
            except CancelledError:
                return
            except (ValueError, OSError, RuntimeError) as exc:
                self.error = str(exc)
        if saw_empty:
            self.error = ''


class CameraRecordingDialog(QDialog):
    recordingSelected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Camera recordings')
        self.setMinimumWidth(450)
        self.resize(515, 460)
        self.discovery = self.lookup = None
        self.workers = set()
        self.items = []
        self.settings = QSettings(str(user_data_dir()/'cameras.ini'), QSettings.IniFormat)
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(QLabel('Cameras'), 1)
        self.refresh = QPushButton('Refresh')
        self.refresh.setAccessibleName('Refresh cameras')
        self.refresh.setAutoDefault(False)
        heading.addWidget(self.refresh)
        layout.addLayout(heading)
        self.cameras = QListWidget()
        self.cameras.setAccessibleName('Cameras on Ethernet')
        self.cameras.setMaximumHeight(106)
        layout.addWidget(self.cameras)
        self.discovery_note = QLabel()
        self.discovery_note.setWordWrap(True)
        self.discovery_note.setTextFormat(Qt.PlainText)
        layout.addWidget(self.discovery_note)
        self.manual = QPushButton('Enter address manually')
        self.manual.setCheckable(True)
        self.manual.setAutoDefault(False)
        layout.addWidget(self.manual, 0, Qt.AlignLeft)
        self.network = QWidget()
        network = QHBoxLayout(self.network)
        network.setContentsMargins(0, 0, 0, 0)
        self.address = QLineEdit()
        self.address.setPlaceholderText('Camera IPv4 address')
        self.address.setAccessibleName('Camera IPv4 address')
        network.addWidget(self.address, 1)
        self.connect_button = QPushButton('Find recordings')
        self.connect_button.setAutoDefault(False)
        network.addWidget(self.connect_button)
        self.network.hide()
        layout.addWidget(self.network)
        layout.addWidget(QLabel('Recordings in camera RAM'))
        self.list = QListWidget()
        self.list.setAccessibleName('Completed recordings in camera RAM')
        layout.addWidget(self.list, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Open | QDialogButtonBox.Cancel)
        self.use = self.buttons.button(QDialogButtonBox.Open)
        self.use.setText('Open recording')
        self.use.setDefault(True)
        self.use.setEnabled(False)
        layout.addWidget(self.buttons)
        self.refresh.clicked.connect(self.refresh_cameras)
        self.cameras.currentRowChanged.connect(self._camera_selected)
        self.cameras.itemClicked.connect(self._retry_empty_camera)
        self.manual.toggled.connect(self._toggle_manual)
        self.address.textEdited.connect(self._clear_recordings)
        self.address.returnPressed.connect(self.find_recordings)
        self.connect_button.clicked.connect(self.find_recordings)
        self.list.currentRowChanged.connect(lambda row: self.use.setEnabled(0 <= row < len(self.items)))
        self.list.itemDoubleClicked.connect(self._choose)
        self.buttons.accepted.connect(self._choose)
        self.buttons.rejected.connect(self.reject)

    def showEvent(self, event):
        super().showEvent(event)
        if not event.spontaneous():
            self.manual.setChecked(False)
            self.refresh_cameras()

    def _toggle_manual(self, checked):
        self.network.setVisible(checked)
        if checked:
            self.cameras.setCurrentRow(-1)
            self._clear_recordings()
            self.status.setText('Enter the camera address, then choose Find recordings.')
            self.address.setFocus()
        elif self.cameras.currentRow() == -1:
            self._clear_recordings()
            self.status.clear()

    def _clear_recordings(self):
        if self.lookup is not None:
            self.lookup.requestInterruption()
            self.lookup = None
        self.items = []
        self.list.clear()
        self.status.clear()
        self.use.setEnabled(False)

    def _start(self, worker):
        self.workers.add(worker)
        worker.finished.connect(self._finished)
        worker.start()

    def refresh_cameras(self):
        if self.discovery is not None:
            self.discovery.requestInterruption()
        self._clear_recordings()
        self.cameras.clear()
        self.manual.setChecked(False)
        self.refresh.setEnabled(False)
        self.discovery_note.setText('Looking for Phantom cameras on Ethernet…')
        self.discovery = _Discover(self)
        self._start(self.discovery)

    def _remembered(self):
        try:
            host = validate_address(self.settings.value('last/host', '', type=str))
        except ValueError:
            return None
        return _Camera(self.settings.value('last/serial', '', type=str),
                       self.settings.value('last/model', '', type=str), (host,), previous=True)

    def _show_cameras(self, worker):
        self.refresh.setEnabled(True)
        previous = self._remembered()
        groups = {}
        for found in worker.cameras:
            key = found.serial or found.host
            if key not in groups:
                groups[key] = _Camera(found.serial, '', (found.host,), name=found.name or '')
            else:
                camera = groups[key]
                groups[key] = replace(camera, hosts=tuple(dict.fromkeys((*camera.hosts, found.host))),
                                      name=camera.name or found.name or '')
        live_count = len(groups)
        cameras = []
        remembered_listed = False
        for camera in groups.values():
            if previous and camera.serial and camera.serial == previous.serial:
                camera = replace(camera, model=previous.model,
                                 hosts=tuple(dict.fromkeys((*camera.hosts, *previous.hosts))))
            if previous and previous.hosts[0] in camera.hosts:
                remembered_listed = True
            cameras.append(camera)
        if previous and not remembered_listed:
            cameras.append(previous)
        for camera in cameras:
            item = QListWidgetItem(camera.label)
            item.setToolTip(', '.join(camera.hosts))
            item.setData(Qt.UserRole, camera)
            self.cameras.addItem(item)
        if live_count:
            self.discovery_note.setText('Choose a camera to see its recordings.' if live_count > 1 else '')
        else:
            self.discovery_note.setText(worker.error or 'No camera replied. Check the Ethernet connection, then refresh.')
        if live_count == 1 and not self.manual.isChecked():
            self.cameras.setCurrentRow(0)

    def _camera_selected(self, row):
        self._clear_recordings()
        if row >= 0:
            self.manual.setChecked(False)
            self._read(self.cameras.item(row).data(Qt.UserRole))

    def _retry_empty_camera(self, item):
        if self.lookup is None and not self.items:
            self._read(item.data(Qt.UserRole))

    def find_recordings(self):
        try:
            host = validate_address(self.address.text())
        except ValueError as exc:
            self.status.setText(str(exc))
            self.address.setFocus()
            return
        self._read(_Camera('', '', (host,)))

    def _read(self, camera):
        self._clear_recordings()
        self.status.setText('Reading stored Cine slots…')
        self.lookup = _FindRecordings(camera, self)
        self._start(self.lookup)

    def _finished(self):
        worker = self.sender()
        self.workers.discard(worker)
        if worker is self.discovery:
            self.discovery = None
            self._show_cameras(worker)
        elif worker is self.lookup:
            self.lookup = None
            self._show_recordings(worker)
        worker.deleteLater()

    def _show_recordings(self, worker):
        self.items = worker.recordings
        for item in self.items:
            precision = '16-bit linear' if item.pixel_format == 'P16' else '8-bit linear'
            rate = f'    ·    {item.fps:,.0f} fps' if item.fps else ''
            self.list.addItem(f'{item.label}    ·    {item.width} × {item.height}{rate}\n'
                              f'Frames {item.first_frame:,} to {item.last_frame:,}    ·    {precision}')
        if self.items:
            item = self.cameras.currentItem()
            if item is not None:
                rec = self.items[0]
                camera = replace(worker.camera, model=rec.model, serial=rec.serial, previous=False,
                                 hosts=tuple(dict.fromkeys((rec.host, *worker.camera.hosts))))
                item.setData(Qt.UserRole, camera)
                item.setText(camera.label)
            self.list.setCurrentRow(0)
            self.status.setText('Select a recording to preview and scan.')
        else:
            self.status.setText(worker.error or 'No completed recordings in camera RAM. After recording finishes, refresh.')

    def _choose(self):
        index = self.list.currentRow()
        if 0 <= index < len(self.items):
            recording = self.items[index]
            for key in ('host', 'serial', 'model'):
                self.settings.setValue(f'last/{key}', getattr(recording, key))
            self.settings.sync()
            self.recordingSelected.emit(recording)
            self.accept()

    def shutdown(self):
        self.discovery = self.lookup = None
        for worker in self.workers:
            worker.requestInterruption()
        return all(worker.isFinished() for worker in self.workers)

    def done(self, result):
        self.shutdown()
        super().done(result)
