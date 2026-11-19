"""Replay rate controls shared by the BOS players."""

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import (QAbstractSpinBox, QDoubleSpinBox, QHBoxLayout,
                               QPushButton, QSlider, QWidget)

from bos_studio_model import MAX_PLAYBACK_FPS


class ReplaySpeed(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self.number = QDoubleSpinBox()
        self.number.setRange(1, MAX_PLAYBACK_FPS)
        self.number.setDecimals(2)
        self.number.setKeyboardTracking(False)
        self.number.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.number.setSuffix(' fps')
        self.number.setAccessibleName('Exact playback speed')
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, MAX_PLAYBACK_FPS)
        self.slider.setPageStep(10)
        self.slider.setMinimumWidth(60)
        self.slider.setMaximumWidth(164)
        self.slider.setAccessibleName('Playback speed slider')
        self.slider.setToolTip('Drag to set whole frames per second. Type a fractional rate in the FPS field.')
        self.slider.valueChanged.connect(self.number.setValue)
        self.number.valueChanged.connect(self.sync_slider)
        for label, delta in [('‹', -10), ('›', 10)]:
            button = QPushButton(label)
            button.setStyleSheet('min-width: 0; padding: 2px;')
            button.setFixedWidth(28)
            action = 'Decrease' if delta < 0 else 'Increase'
            button.setAccessibleName(f'{action} playback speed by 10 fps')
            button.setToolTip(f'{action} by 10 fps')
            button.clicked.connect(lambda checked=False, step=delta: self.number.setValue(self.number.value()+step))
            row.addWidget(button)
            if delta == -10:
                row.addWidget(self.slider, 1)
        row.addWidget(self.number)
        self.number.setValue(30)

    def sync_slider(self):
        with QSignalBlocker(self.slider):
            self.slider.setValue(round(self.number.value()))
