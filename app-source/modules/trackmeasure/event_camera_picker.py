"""Select a completed recording on the Phantom standard Ethernet interface."""
from concurrent.futures import CancelledError

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QPushButton, QVBoxLayout)

from event_camera import list_recordings, validate_address


class _FindRecordings(QThread):
    def __init__(self, host, parent):
        super().__init__(parent)
        self.host = host
        self.recordings = []
        self.error = ''

    def run(self):
        try:
            self.recordings = list_recordings(self.host, self.isInterruptionRequested)
        except CancelledError:
            pass
        except (ValueError, OSError, RuntimeError) as exc:
            self.error = str(exc)


class CameraRecordingDialog(QDialog):
    recordingSelected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Camera recordings')
        self.setMinimumWidth(400)
        self.worker = None
        self.items = []
        self.closing = False
        layout = QVBoxLayout(self)
        network = QHBoxLayout()
        self.address = QLineEdit()
        self.address.setPlaceholderText('Camera IPv4 address')
        self.address.setAccessibleName('Camera IPv4 address')
        network.addWidget(self.address, 1)
        self.connect_button = QPushButton('Find recordings')
        self.connect_button.setDefault(True)
        network.addWidget(self.connect_button)
        layout.addLayout(network)
        intro = QLabel('Enter the camera’s Ethernet IP. On a VEO using a 10 Gb link, use the XIP on its label. Finish other camera playback or downloads before opening a recording.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.list = QListWidget()
        self.list.setAccessibleName('Completed recordings in camera RAM')
        layout.addWidget(self.list)
        self.status = QLabel('Connect to list completed recordings in camera RAM.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Open | QDialogButtonBox.Cancel)
        self.use = self.buttons.button(QDialogButtonBox.Open)
        self.use.setText('Open recording')
        self.use.setEnabled(False)
        layout.addWidget(self.buttons)
        self.connect_button.clicked.connect(self.find_recordings)
        self.address.textChanged.connect(self._clear)
        self.list.currentRowChanged.connect(lambda row: self.use.setEnabled(row >= 0))
        self.list.itemDoubleClicked.connect(lambda _item: self._choose())
        self.buttons.accepted.connect(self._choose)
        self.buttons.rejected.connect(self.reject)

    def _clear(self):
        self.items = []
        self.list.clear()
        self.use.setEnabled(False)

    def find_recordings(self):
        if self.worker is not None:
            return
        self._clear()
        try:
            host = validate_address(self.address.text())
        except ValueError as exc:
            self.status.setText(str(exc))
            self.address.setFocus()
            return
        self.address.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.status.setText('Reading stored Cine slots…')
        self.worker = _FindRecordings(host, self)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _finished(self):
        worker, self.worker = self.worker, None
        if worker is None:
            return
        worker.deleteLater()
        self.address.setEnabled(True)
        self.connect_button.setEnabled(True)
        if self.closing:
            self.closing = False
            self.status.setText('Connect to list completed recordings in camera RAM.')
            super().reject()
            return
        self.items = worker.recordings
        for item in self.items:
            precision = '16-bit linear' if item.pixel_format == 'P16' else '8-bit linear'
            rate = f'    ·    {item.fps:,.0f} fps' if item.fps else ''
            self.list.addItem(f'{item.label}    ·    {item.width} × {item.height}{rate}\n'
                              f'Frames {item.first_frame:,} to {item.last_frame:,}    ·    {precision}')
        if self.items:
            self.list.setCurrentRow(0)
            self.status.setText('Select a recording to preview and scan.')
        else:
            self.status.setText(worker.error or 'No completed recordings in camera RAM. Wait for recording to finish, then find recordings again.')

    def _choose(self):
        index = self.list.currentRow()
        if 0 <= index < len(self.items):
            self.recordingSelected.emit(self.items[index])
            self.accept()

    def shutdown(self):
        if self.worker is not None and self.worker.isRunning():
            self.closing = True
            self.worker.requestInterruption()
            self.status.setText('Closing camera connection…')
            return False
        return True

    def reject(self):
        if self.shutdown():
            super().reject()

    def closeEvent(self, event):
        if self.shutdown():
            super().closeEvent(event)
        else:
            event.ignore()
