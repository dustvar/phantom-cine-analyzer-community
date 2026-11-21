"""Inclusive native-frame selection and sampling guidance for analyzed BOS playback."""
from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout,
                               QLabel, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout)

from bos_sampling import RangeSampling
from bos_studio_model import MAX_PLAYBACK_FPS


class BOSRangeBar(QFrame):
    analyzeRequested = Signal(int, int)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('bosRangeBar')
        self.setStyleSheet('QFrame#bosRangeBar {background: #282f39; border: 1px solid #505d6c; border-radius: 0px;}')
        self.first_native = 0
        self.count = 0
        self.current_index = 0
        self.ready = False
        self.blocked_reason = ''
        self.seconds_per_frame = None
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 7, 10, 7)
        root.setSpacing(5)
        row = QHBoxLayout()
        self.first = QSpinBox()
        self.last = QSpinBox()
        for name, spin in (('Mark in frame', self.first), ('Mark out frame', self.last)):
            spin.setAccessibleName(name)
            spin.setGroupSeparatorShown(True)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self.refresh)
        self.mark_in = QPushButton('Mark In')
        self.mark_out = QPushButton('Mark Out')
        self.mark_in.setToolTip('Use the frame currently displayed above as the first frame.')
        self.mark_out.setToolTip('Use the frame currently displayed above as the last frame.')
        self.mark_in.clicked.connect(lambda: self.first.setValue(self.first_native+self.current_index))
        self.mark_out.clicked.connect(lambda: self.last.setValue(self.first_native+self.current_index))
        self.analyze = QPushButton('Analyze')
        self.analyze.setStyleSheet('QPushButton {background: #695477; border: 1px solid #ba8dcb; font-weight: 600; padding: 6px 12px;} QPushButton:disabled {background: #303640; color: #77818f; border-color: #45505d;}')
        self.analyze.clicked.connect(self._analyze)
        self.cancel = QPushButton('Cancel')
        self.cancel.clicked.connect(self.cancelled)
        for widget in (self.mark_in, self.first, self.mark_out, self.last):
            row.addWidget(widget)
        row.addStretch()
        row.addWidget(self.analyze)
        row.addWidget(self.cancel)
        root.addLayout(row)

        self.sampling = QComboBox()
        self.sampling.setAccessibleName('Frames to analyze')
        for label, value in [('Every frame', 1), ('Match playback', 'match'),
                             ('Every other frame', 2), ('Every 3rd frame', 3),
                             ('Every 4th frame', 4), ('Every 5th frame', 5),
                             ('Every 10th frame', 10), ('Custom interval', 'custom')]:
            self.sampling.addItem(label, value)
        self.interval = QSpinBox()
        self.interval.setAccessibleName('Analyze every Nth frame')
        self.interval.setRange(1, 1_000_000)
        self.interval.setValue(3)
        self.interval.setKeyboardTracking(False)
        self.minimum_fps = QDoubleSpinBox()
        self.minimum_fps.setAccessibleName('Slowest planned playback in source frames per second')
        self.minimum_fps.setRange(1., MAX_PLAYBACK_FPS)
        self.minimum_fps.setDecimals(2)
        self.minimum_fps.setValue(200.)
        self.minimum_fps.setSuffix(' fps')
        self.minimum_fps.setKeyboardTracking(False)
        self.target_fps = QComboBox()
        self.target_fps.setAccessibleName('Target movie or display frame rate')
        for fps in (24, 30, 60, 120, 240):
            self.target_fps.addItem(f'{fps} fps', fps)
        self.target_fps.setCurrentIndex(self.target_fps.findData(60))
        self.target_fps.setToolTip('How many distinct frames per second you want to show. '
                                   'Choose your actual MP4 frame rate separately in Record Studio.')
        sample_row = QHBoxLayout()
        sample_row.setSpacing(6)
        self.interval_label = QLabel('Interval')
        self.minimum_label = QLabel('Slowest playback')
        self.cadence_label = QLabel('Movie / display')
        for label, editor in [(QLabel('Sampling'), self.sampling), (self.interval_label, self.interval),
                              (self.minimum_label, self.minimum_fps), (self.cadence_label, self.target_fps)]:
            label.setBuddy(editor)
            sample_row.addWidget(label)
            sample_row.addWidget(editor)
        sample_row.addStretch()
        root.addLayout(sample_row)
        self.sampling_note = QLabel()
        self.summary = QLabel()
        for label in (self.sampling_note, self.summary):
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            root.addWidget(label)
        self.sampling.currentIndexChanged.connect(self._sampling_changed)
        self.minimum_fps.valueChanged.connect(self._sampling_changed)
        self.target_fps.currentIndexChanged.connect(self._sampling_changed)
        self.interval.valueChanged.connect(self._sampling_changed)
        self._sampling_changed()

    def sample_step(self):
        mode = self.sampling.currentData()
        if mode == 'match':
            return max(1, int(self.minimum_fps.value() // self.target_fps.currentData()))
        if mode == 'custom':
            return self.interval.value()
        return mode

    def _sampling_changed(self):
        mode = self.sampling.currentData()
        for widget in (self.minimum_label, self.minimum_fps):
            widget.setVisible(mode == 'match')
        for widget in (self.interval_label, self.interval):
            widget.setVisible(mode == 'custom')
        # Every frame preserves all available samples whatever the delivery rate.
        for widget in (self.cadence_label, self.target_fps):
            widget.setVisible(mode != 1)
        step = self.sample_step()
        target = self.target_fps.currentData()
        if mode == 'match':
            slowest = self.minimum_fps.value()
            detail = 'Every frame' if step == 1 else f'1 in {step} frames'
            rate = f'{slowest / step:,.1f}'.rstrip('0').rstrip('.')
            note = f'{detail} gives {rate} measurements/s. Analyzed playback starts at {slowest:g} source fps. '
            if slowest < target:
                note += 'Even with every frame, playback this slow repeats images at the target cadence.'
            elif step > 1:
                note += 'Brief events between sampled frames can disappear.'
        elif step == 1:
            note = 'Every frame gives the most detail and takes the longest to process. Use it to inspect brief events or play slowly.'
        else:
            minimum_rate = step * target
            if minimum_rate <= MAX_PLAYBACK_FPS:
                guidance = f'Best for {minimum_rate:,} source fps or faster at {target} fps delivery. Slower playback can repeat images.'
            else:
                guidance = f'Even at {MAX_PLAYBACK_FPS:,} source fps, {target} fps delivery will repeat images at this interval.'
            note = guidance + ' Brief events between sampled frames can disappear.'
        self.sampling_note.setText(note.strip())
        self.refresh()

    def configure(self, first_native, count, current_index):
        self.first_native, self.count = int(first_native), int(count)
        self.current_index = max(0, min(self.count-1, int(current_index)))
        for spin in (self.first, self.last):
            with QSignalBlocker(spin):
                spin.setRange(self.first_native, self.first_native+self.count-1)
                spin.setValue(self.first_native+self.current_index)
        self.refresh()

    def update_ready(self, current_index, *, can_mark, can_analyze, reason='', seconds_per_frame=None):
        self.current_index = int(current_index)
        self.ready = bool(can_analyze)
        self.blocked_reason = reason
        self.seconds_per_frame = seconds_per_frame
        self.mark_in.setEnabled(can_mark)
        self.mark_out.setEnabled(can_mark)
        self.refresh()

    def indices(self):
        return self.first.value()-self.first_native, self.last.value()-self.first_native

    def refresh(self):
        first, last = self.indices()
        valid = bool(self.count and 0 <= first <= last < self.count)
        self.analyze.setEnabled(valid and self.ready)
        if not valid:
            self.summary.setText('Mark Out must be at or after Mark In. Use the playback slider, mark buttons, or type frame numbers.')
            return
        count = last-first+1
        step = self.sample_step()
        samples = RangeSampling(count, step).count
        estimate = ''
        if self.seconds_per_frame is not None and self.seconds_per_frame > 0:
            seconds = samples*self.seconds_per_frame
            estimate = (f' · measurement estimate {seconds/60:.1f} min' if seconds >= 60
                        else f' · measurement estimate {seconds:.0f} s') + ' + cache preparation'
        if step == 1:
            coverage = f'{count:,} frame{"s" if count != 1 else ""}, both endpoints included'
        else:
            unit = 'frame' if count == 1 else 'frames'
            coverage = f'Analyze {samples:,} of {count:,} source {unit}, both endpoints included'
        note = f'. {self.blocked_reason}' if not self.ready and self.blocked_reason else ''
        self.summary.setText(coverage + estimate + note)

    def _analyze(self):
        if self.analyze.isEnabled():
            self.analyzeRequested.emit(*self.indices())
