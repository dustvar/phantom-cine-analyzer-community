"""Stream the measured BOS overlay over an inclusive source-frame range."""
from copy import deepcopy
from pathlib import Path
import threading

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QLabel, QMessageBox, QProgressBar,
                               QPushButton, QSpinBox, QVBoxLayout)

from bos_frames import BOSFrames
from bos_studio_model import MAX_MP4_FPS, MAX_PLAYBACK_FPS, StudioEdit
from video_export import ExportCancelled, video_frame, write_video_frames


def _delivery_rate(playback_fps):
    return 60. if playback_fps > MAX_MP4_FPS else playback_fps


def export_bos_clip(settings, destination, first, last, fps, cancel=None, progress=None, report=None):
    destination = Path(destination)
    if destination.suffix.lower() != '.mp4':
        raise ValueError('Choose an MP4 destination.')
    if destination.resolve() == Path(settings['path']).resolve():
        raise ValueError('Choose a destination different from the source Cine.')
    if not 1 <= fps <= MAX_PLAYBACK_FPS:
        raise ValueError(f'Choose a playback rate between 1 and {MAX_PLAYBACK_FPS:,} fps.')
    if settings.get('reference') is None or not settings.get('roi'):
        raise ValueError('Select a reference frame and a region before exporting.')
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise ExportCancelled()
    session = BOSFrames(settings['path'])
    if report is not None:
        report.clear()
        report.update(warning_frame_count=0, warning_counts={}, warning_frames=[])
    def record_warning(result):
        warning = result.get('warning')
        if report is not None and warning:
            report['warning_frame_count'] += 1
            report['warning_counts'][warning] = report['warning_counts'].get(warning, 0) + 1
            if len(report['warning_frames']) < 5:
                report['warning_frames'].append(result['first'] + result['index'])
    try:
        current = dict(settings, position=first)
        initial = session.render(current)
        if initial['field'] is None or not 0 <= first <= last < initial['count']:
            raise ValueError('Choose a valid source frame range and BOS region.')
        record_warning(initial)
        first_image = video_frame(initial['image'])
        height, width = first_image.shape[:2]

        edit = StudioEdit(last-first+1, fps)
        delivery = _delivery_rate(fps)

        def frames():
            for offset in edit.output_frames(delivery):
                if cancel.is_set():
                    raise ExportCancelled()
                if offset == 0:
                    yield first_image
                else:
                    current['position'] = first+offset
                    rendered = session.render(current)
                    record_warning(rendered)
                    yield video_frame(rendered['image'])

        return write_video_frames(destination, frames(), width, height, delivery,
                                  edit.output_count(delivery), cancel, progress, full_rate_intent=delivery >= 120)
    finally:
        session.close()


class _Writer(QThread):
    progress = Signal(int, int)

    def __init__(self, parent, *args):
        super().__init__(parent)
        self.args = args
        self.cancel = threading.Event()
        self.error = None
        self.cancelled = False
        self.report = {}

    def run(self):
        try:
            export_bos_clip(*self.args, cancel=self.cancel, progress=self.progress.emit, report=self.report)
        except ExportCancelled:
            self.cancelled = True
        except Exception as exc:
            self.error = str(exc)


class _WritingDialog(QDialog):
    """Keep the worker alive until cleanup, including after Cancel, Esc or Close."""
    def __init__(self, parent, settings, destination, first, last, fps):
        super().__init__(parent)
        self.setWindowTitle('Export BOS clip')
        self.label = QLabel('Measuring dot displacement and writing video…')
        self.bar = QProgressBar()
        self.bar.setRange(0, StudioEdit(last-first+1, fps).output_count(_delivery_rate(fps)))
        self.stop = QPushButton('Cancel')
        layout = QVBoxLayout(self)
        for widget in (self.label, self.bar, self.stop):
            layout.addWidget(widget)
        self.worker = _Writer(self, deepcopy(settings), destination, first, last, fps)
        self.stop.clicked.connect(self.reject)
        self.worker.progress.connect(lambda completed, total: self.bar.setValue(completed))
        self.worker.finished.connect(self.accept)

    def reject(self):
        if self.worker.isRunning():
            self.worker.cancel.set()
            self.label.setText('Cancelling export…')
            self.stop.setEnabled(False)
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


def export_bos_dialog(parent, settings, first_image_no, image_count):
    options = QDialog(parent)
    options.setWindowTitle('Export BOS clip')
    layout = QVBoxLayout(options)
    form = QFormLayout()
    layout.addLayout(form)
    first, last = QSpinBox(), QSpinBox()
    for spin in (first, last):
        spin.setGroupSeparatorShown(True)
        spin.setRange(first_image_no, first_image_no + image_count - 1)
    # Start with the range the user actually inspected. Editing either end is explicit.
    positions = sorted((settings['reference'], settings['position']))
    first.setValue(first_image_no + positions[0])
    last.setValue(first_image_no + positions[1])
    first.valueChanged.connect(last.setMinimum)
    last.setMinimum(first.value())
    fps = QDoubleSpinBox()
    fps.setRange(1, MAX_PLAYBACK_FPS)
    fps.setValue(settings['playback_fps'])
    fps.setSuffix(' fps')
    form.addRow('First Cine frame', first)
    form.addRow('Last Cine frame', last)
    form.addRow('Source playback speed', fps)
    summary = QLabel()
    def update_duration():
        count = last.value() - first.value() + 1
        speed = fps.value()
        delivery = _delivery_rate(speed)
        total = StudioEdit(count, speed).output_count(delivery)
        summary.setText(f'{count:,} source frames at {speed:g} fps playback\n'
                        f'MP4 · {total:,} frames at {delivery:g} fps · {total/delivery:,.3f} s')
    for spin in (first, last, fps):
        spin.valueChanged.connect(update_duration)
    update_duration()
    layout.addWidget(summary)
    row = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    row.button(QDialogButtonBox.Save).setText('Choose file…')
    row.accepted.connect(options.accept)
    row.rejected.connect(options.reject)
    layout.addWidget(row)
    if options.exec() != QDialog.Accepted:
        return
    picker = QFileDialog(parent, 'Save BOS video')
    picker.setAcceptMode(QFileDialog.AcceptSave)
    picker.setNameFilter('MP4 video (*.mp4)')
    picker.setDefaultSuffix('mp4')
    picker.selectFile(str(Path.home()/'Desktop'/(Path(settings['path']).stem + '-BOS.mp4')))
    if picker.exec() != QDialog.Accepted:
        return
    destination = picker.selectedFiles()[0]
    dialog = _WritingDialog(parent, settings, destination, first.value()-first_image_no,
                            last.value()-first_image_no, fps.value())
    parent._bos_export_worker = dialog.worker
    try:
        dialog.worker.start()
        dialog.exec()
    finally:
        parent._bos_export_worker = None
    if dialog.worker.error:
        QMessageBox.warning(parent, 'BOS export', dialog.worker.error)
    elif not dialog.worker.cancelled:
        parent.status.setText(f'Saved BOS video to {destination}')
        missing = dialog.worker.report.get('warning_frame_count', 0)
        if missing:
            warnings = '\n'.join(dialog.worker.report['warning_counts'])
            examples = ', '.join(f'{frame:,}' for frame in dialog.worker.report['warning_frames'])
            parent.status.setText(f'Saved BOS video · {missing:,} frames show original footage because the BOS measurement was unavailable.')
            QMessageBox.information(parent, 'BOS export completed with gaps',
                f'Saved {destination}\n\n{missing:,} frames show original footage instead of an unreliable overlay. '
                f'Example Cine frames: {examples}.\n\n{warnings}')
    dialog.deleteLater()
