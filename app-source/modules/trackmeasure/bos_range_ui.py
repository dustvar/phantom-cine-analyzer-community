"""Explicit, inclusive source-frame selection for precomputed BOS playback."""
from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSpinBox, QVBoxLayout)


class BOSRangeBar(QFrame):
    analyzeRequested = Signal(int, int)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('bosRangeBar')
        self.setStyleSheet('QFrame#bosRangeBar {background: #282f39; border: 1px solid #505d6c; border-radius: 5px;}')
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
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setMinimumWidth(0)
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(self.summary)

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
        estimate = ''
        if self.seconds_per_frame is not None and self.seconds_per_frame > 0:
            seconds = count*self.seconds_per_frame
            estimate = (f' · measurement estimate {seconds/60:.1f} min' if seconds >= 60
                        else f' · measurement estimate {seconds:.0f} s') + ' + cache preparation'
        self.summary.setText(f'{count:,} frame{"s" if count != 1 else ""}, both endpoints included{estimate}. '
                             + (self.blocked_reason if not self.ready and self.blocked_reason else
                                'Use the playback slider to mark the range. Analyze uses the current reference, region and display settings.'))

    def _analyze(self):
        if self.analyze.isEnabled():
            self.analyzeRequested.emit(*self.indices())
