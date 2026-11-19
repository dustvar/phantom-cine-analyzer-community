"""Choose the frame and playback duration for revealing a Cine's tracked objects."""
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
                               QVBoxLayout)


class TrackFadeDialog(QDialog):
    def __init__(self, parent, options, source_name, origin, count, playhead, track_count):
        super().__init__(parent)
        self.setWindowTitle('Tracking fade')
        self.origin = origin
        layout = QVBoxLayout(self)
        source = QLabel(source_name)
        source.setWordWrap(True)
        layout.addWidget(source)
        summary = QLabel(f'{track_count} enabled tracked object' + ('s' if track_count != 1 else '') + '.'
                         if track_count else 'No enabled tracks for this video. Open it in Track to add an object or enable an existing track.')
        summary.setWordWrap(True)
        layout.addWidget(summary)
        self.enabled = QCheckBox('Show tracking graphics')
        self.enabled.setChecked(options['enabled'])
        layout.addWidget(self.enabled)
        form = QFormLayout()
        self.start = QSpinBox()
        self.start.setRange(origin, origin + count - 1)
        self.start.setGroupSeparatorShown(True)
        self.start.setValue(origin + options['start'])
        self.start.setAccessibleName('First frame of the tracking fade')
        row = QHBoxLayout()
        row.addWidget(self.start)
        self.use_current = QPushButton('Use playhead')
        self.use_current.clicked.connect(lambda: self.start.setValue(origin + playhead))
        row.addWidget(self.use_current)
        form.addRow('Start at Cine frame', row)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0, 60)
        self.duration.setDecimals(2)
        self.duration.setSingleStep(.10)
        self.duration.setSuffix(' s')
        self.duration.setValue(options['duration'])
        form.addRow('Fade-in duration', self.duration)
        self.trail = QSpinBox()
        self.trail.setRange(0, 2000)
        self.trail.setValue(options['trail'])
        self.trail.setSpecialValueText('Marker only')
        self.trail.setSuffix(' frames')
        form.addRow('Trail length', self.trail)
        self.names = QCheckBox('Label objects')
        self.names.setChecked(options['names'])
        form.addRow(self.names)
        layout.addLayout(form)
        hint = QLabel('The footage plays clean until the start frame. Markers and trails fade in over the selected playback time. Set the duration to zero for an instant reveal.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(390, self.sizeHint().height())

    def options(self):
        return dict(enabled=self.enabled.isChecked(), start=self.start.value() - self.origin,
                    duration=self.duration.value(), trail=self.trail.value(), names=self.names.isChecked())
