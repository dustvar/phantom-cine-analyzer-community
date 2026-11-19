"""Export options and a cancellable background job for the native Viewer."""
from copy import deepcopy
from pathlib import Path
import threading
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QComboBox,
                               QDoubleSpinBox, QDialogButtonBox)
from video_export import export_clip, ExportCancelled
from viewer_timeline import Timeline, copy_index, copy_offset, copy_options, frame_label
from cine_render import rendered_size


def original_range(source, edit, first, last):
    """Map timeline marks back into a single occurrence of a native source."""
    if edit:
        index = copy_index(edit, first)
        if index != copy_index(edit, last):
            raise ValueError('For an Original Cine, place both marks in the same video. Rendered Cine can include joins.')
        source = edit['copies'][index].get('source_path') or source
        offset = copy_offset(edit, index)
        first, last = first - offset, last - offset
    if Path(source).suffix.lower() != '.cine':
        raise ValueError('Original Cine requires marks in a Cine source. Choose Rendered Cine for this video.')
    return str(source), first, last


class ExportDialog(QDialog):
    def __init__(self, parent, source, first, last, metadata, edit=None):
        super().__init__(parent)
        self.setWindowTitle('Export Clip')
        self.setMinimumWidth(450)
        self.frame_count = last - first + 1
        self.edit = deepcopy(edit)
        self.first, self.last = first, last
        self.source, self.metadata = source, metadata
        self.plan = Timeline(self.edit, first, last) if edit else None
        self.output_count = self.plan.frame_count if self.plan is not None else self.frame_count
        layout = QVBoxLayout(self)
        self.original_error = ''
        try:
            self.original = original_range(source, edit, first, last)
        except ValueError as exc:
            self.original, self.original_error = None, str(exc)
        self.in_label = frame_label(edit,first,metadata.FirstImageNo) if edit else str(first+metadata.FirstImageNo)
        self.out_label = frame_label(edit,last,metadata.FirstImageNo) if edit else str(last+metadata.FirstImageNo)
        summary = QLabel(f'In {self.in_label}    →    Out {self.out_label}   ·   {self.frame_count:,} marked frames')
        summary.setWordWrap(True)
        layout.addWidget(summary)
        form = QFormLayout()
        self.format = QComboBox()
        for label, key in [('MP4 · H.264', '.mp4'), ('Original Cine · lossless trim', 'original'),
                           ('Rendered Cine · with graphics', 'rendered'), ('QuickTime MOV · H.264', '.mov'),
                           ('AVI · Motion JPEG', '.avi')]:
            self.format.addItem(label, key)
        form.addRow('Format', self.format)
        self.fps = QDoubleSpinBox()
        self.fps.setRange(1, 120)
        self.fps.setDecimals(2)
        self.fps.setValue(edit['output_fps'] if edit else 30)
        self.fps.setSuffix(' fps')
        self.fps.setEnabled(edit is None)
        self.fps.setToolTip('Set Video FPS in the Viewer section editor to change the output timing.')
        form.addRow('Video FPS (set in Viewer)' if edit else 'Playback frame rate', self.fps)
        self.size = QComboBox()
        for label, height in [('Original size', None), ('Up to 1080 pixels high', 1080), ('Up to 720 pixels high', 720)]:
            self.size.addItem(label, height)
        form.addRow('Resolution', self.size)
        self.quality = QComboBox()
        self.quality.addItems(['High', 'Balanced', 'Small'])
        form.addRow('Quality / file size', self.quality)
        layout.addLayout(form)
        self.form = form
        self.duration = QLabel()
        self.duration.setWordWrap(True)
        layout.addWidget(self.duration)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.choose = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.choose.setText('Choose File…')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.fps.valueChanged.connect(self.update_duration)
        self.format.currentIndexChanged.connect(self.update_duration)
        self.size.currentIndexChanged.connect(self.update_duration)
        if self.output_count == 0 and self.original:
            self.format.setCurrentIndex(self.format.findData('original'))
        self.update_duration()

    def extension(self):
        return '.cine' if self.format.currentData() in ('original','rendered') else self.format.currentData()

    def update_duration(self):
        key = self.format.currentData()
        raw = key == 'original'
        for widget in (self.size,self.fps):
            self.form.setRowVisible(widget, not raw)
        self.form.setRowVisible(self.quality, key not in ('original','rendered'))
        if raw:
            self.duration.setText(self.original_error or f'Copy {self.frame_count:,} consecutive original frames from {Path(self.original[0]).name}.')
            self.note.setText('Keeps the camera pixels, native frame numbers and capture timing. Copies every frame between the marks, including any skipped sections. Choose Rendered Cine to include edits and graphics.')
            self.choose.setEnabled(self.original is not None)
            return
        fps = self.fps.value()
        self.choose.setEnabled(self.output_count > 0)
        if self.output_count == 0:
            self.duration.setText('Include at least one section in the marked range to render it.')
        else:
            self.duration.setText(f'{self.output_count:,} output frames at {fps:g} fps · {self.output_count/fps:,.2f} s')
        if key == 'rendered':
            first = self.plan.sample(0)[0] if self.plan is not None and self.output_count else self.first
            added_source = self.edit and self.edit['copies'][copy_index(self.edit, first)].get('source_path')
            if self.output_count and not added_source and hasattr(self.metadata, 'ImWidth'):
                width, height = self.metadata.ImWidth, self.metadata.ImHeight
                if self.edit and copy_options(self.edit, copy_index(self.edit, first)).get('rotation', 0) in (90, 270):
                    width, height = height, width
                maximum = self.size.currentData()
                if maximum and height > maximum:
                    width, height = max(2, round(width * maximum / height)), maximum
                size_mb = rendered_size(width, height, self.output_count) / 1e6
                self.duration.setText(self.duration.text() + f' · {size_mb:,.1f} MB')
            self.note.setText('Writes uncompressed 24-bit color with rotation, speed edits, zooms, text and enabled tracking graphics baked into the pixels. Numbering and timestamps start at the beginning of the edited clip.')
        else:
            self.note.setText('Includes rotation, speed edits, zooms, data, text and enabled tracking graphics. Movie exports are silent.')

    def options(self):
        if self.format.currentData() == 'original':
            source, first, last = self.original
            return dict(source=source, first=first, last=last, original=True)
        return dict(fps=self.fps.value(), max_height=self.size.currentData(), quality=self.quality.currentText(),
                    edit=deepcopy(self.edit))


class ExportJob(QThread):
    progress = Signal(int, int)

    def __init__(self, parent, **options):
        super().__init__(parent)
        self.options = options
        self.cancel_event = threading.Event()
        self.result = None
        self.error = None
        self.cancelled = False
        self.original = self.options.pop('original', False)

    def run(self):
        try:
            if self.original:
                from cine_trim import trim_cine
                self.result = trim_cine(**self.options, cancel=self.cancel_event, progress=self.progress.emit)
            else:
                self.result = export_clip(**self.options, cancel=self.cancel_event, progress=self.progress.emit)
        except ExportCancelled:
            self.cancelled = True
        except Exception as error:
            self.error = str(error)
