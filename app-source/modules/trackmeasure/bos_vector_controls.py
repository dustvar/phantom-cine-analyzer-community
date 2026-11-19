"""Shared arrow appearance editor for the live BOS workspace and Record Studio."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QColorDialog, QComboBox, QFormLayout, QPushButton,
                               QSpinBox, QToolButton, QVBoxLayout, QWidget)

from bos_vectors import VectorStyle


class VectorAppearance(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colors = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.toggle = QToolButton()
        self.toggle.setText('Arrow style')
        self.toggle.setAccessibleName('Arrow style settings')
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setCheckable(True)
        layout.addWidget(self.toggle)

        self.panel = QWidget()
        self.form = QFormLayout(self.panel)
        self.form.setContentsMargins(0, 1, 0, 3)
        self.form.setSpacing(4)
        layout.addWidget(self.panel)

        self.shape = QComboBox()
        for label, value in [('Open tip', 'open'), ('Solid tip', 'filled'), ('Needle', 'needle')]:
            self.shape.addItem(label, value)
        self.shape.setAccessibleName('Arrow shape')
        self.form.addRow('Shape', self.shape)

        self.thickness = QSpinBox()
        self.thickness.setRange(0, 8)
        self.thickness.setSpecialValueText('Auto')
        self.thickness.setSuffix(' px')
        self.thickness.setAccessibleName('Arrow stroke thickness')
        self.thickness.setToolTip('Set the stroke width in source-image pixels. Auto follows arrow spacing.')
        self.form.addRow('Thickness', self.thickness)

        self.encoding = QComboBox()
        for label, value in [('Double tips', 'double_head'), ('Two colors', 'threshold'),
                             ('Black to white', 'grayscale')]:
            self.encoding.addItem(label, value)
        self.encoding.setAccessibleName('Arrow magnitude display')
        self.encoding.setToolTip('Double tips and two colors flag shifts above Full scale. '
                                 'Black to white shades the shift from zero to Full scale.')
        self.form.addRow('Magnitude', self.encoding)

        self.color = QPushButton()
        self.color.setAccessibleName('Choose arrow color')
        self.high_color = QPushButton()
        self.high_color.setAccessibleName('Choose arrow color above full scale')
        self.form.addRow('Color', self.color)
        self.form.addRow('Above scale', self.high_color)
        self.color.clicked.connect(lambda: self._choose_color('vector_color', self.color))
        self.high_color.clicked.connect(lambda: self._choose_color('vector_high_color', self.high_color))
        self.toggle.toggled.connect(self._expand)
        self.shape.currentIndexChanged.connect(lambda: self.changed.emit())
        self.thickness.valueChanged.connect(lambda: self.changed.emit())
        self.encoding.currentIndexChanged.connect(self._encoding_changed)
        self.set_settings({})
        self._expand(False)

    def _expand(self, visible):
        self.panel.setVisible(visible)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)

    def _update_rows(self):
        encoding = self.encoding.currentData()
        self.form.setRowVisible(self.color, encoding != 'grayscale')
        self.form.setRowVisible(self.high_color, encoding == 'threshold')

    def _encoding_changed(self):
        self._update_rows()
        self.changed.emit()

    def _set_color(self, key, button, value):
        self._colors[key] = value
        swatch = QPixmap(16, 16)
        swatch.fill(Qt.GlobalColor.transparent)
        painter = QPainter(swatch)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#9fa8af'), 1))
        painter.setBrush(QColor(value))
        painter.drawRoundedRect(1, 1, 14, 14, 3, 3)
        painter.end()
        button.setIcon(QIcon(swatch))
        button.setText(value.upper())

    def _choose_color(self, key, button):
        new_color = QColorDialog.getColor(QColor(self._colors[key]), self, button.accessibleName())
        if new_color.isValid() and new_color.name() != self._colors[key].lower():
            self._set_color(key, button, new_color.name())
            self.changed.emit()

    def set_settings(self, settings):
        style = VectorStyle.from_settings(settings)
        blocked = self.blockSignals(True)
        try:
            self.shape.setCurrentIndex(self.shape.findData(style.shape))
            self.encoding.setCurrentIndex(self.encoding.findData(style.encoding))
            self.thickness.setValue(style.thickness)
            self._set_color('vector_color', self.color, style.color)
            self._set_color('vector_high_color', self.high_color, style.high_color)
            self._update_rows()
        finally:
            self.blockSignals(blocked)

    def settings(self):
        return dict(self._colors, vector_shape=self.shape.currentData(),
                    vector_thickness=self.thickness.value(), vector_encoding=self.encoding.currentData())
