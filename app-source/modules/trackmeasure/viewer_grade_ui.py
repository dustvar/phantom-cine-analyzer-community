"""Viewer-only levels and a draggable, monotone tone curve."""
from copy import deepcopy
import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QSignalBlocker
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
                               QDoubleSpinBox, QPushButton, QCheckBox, QScrollArea)
from viewer_processing import default_grade, curve_values, sample_histogram


class ToneCurve(QWidget):
    changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = default_grade()['curve']
        self.histogram = np.zeros(64)
        self.drag_index = None
        self.setMinimumSize(180, 180)
        self.setMaximumHeight(260)
        self.setAccessibleName('Tone curve: drag the three interior handles up to brighten or down to darken')
        self.setToolTip('Drag a handle up to brighten that tone, or down to darken it. Double-click to reset the curve.')

    def plot_rect(self):
        return QRectF(12, 12, self.width() - 24, self.height() - 24)

    def handle_position(self, index):
        rect = self.plot_rect()
        return QPointF(rect.left() + rect.width() * index / 4,
                       rect.bottom() - rect.height() * self.points[index])

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.plot_rect()
        painter.fillRect(rect, QColor('#202124'))
        peak = max(1, self.histogram.max())
        for i, count in enumerate(self.histogram):
            height = rect.height() * count / peak * .85
            painter.fillRect(QRectF(rect.left() + i * rect.width() / 64,
                                   rect.bottom() - height, rect.width() / 64, height), QColor('#41434a'))
        painter.setPen(QPen(QColor('#555860'), 1))
        for value in np.linspace(0, 1, 5):
            x, y = rect.left() + value * rect.width(), rect.bottom() - value * rect.height()
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        painter.setPen(QPen(QColor('#777b85'), 1, Qt.PenStyle.DashLine))
        painter.drawLine(rect.bottomLeft(), rect.topRight())
        path = QPainterPath()
        for i, (x, y) in enumerate(zip(np.linspace(0, 1, 129), curve_values(self.points, np.linspace(0, 1, 129)))):
            point = QPointF(rect.left() + x * rect.width(), rect.bottom() - y * rect.height())
            path.moveTo(point) if i == 0 else path.lineTo(point)
        painter.setPen(QPen(QColor('#d4a6ff'), 2))
        painter.drawPath(path)
        painter.setBrush(QColor('#faf6ff'))
        for i in range(1, 4):
            painter.drawEllipse(self.handle_position(i), 6, 6)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        distances = [(self.handle_position(i) - event.position()).manhattanLength() for i in range(1, 4)]
        index = int(np.argmin(distances))
        if distances[index] < 28:
            self.drag_index = index + 1
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.drag_index is None:
            return
        index, rect = self.drag_index, self.plot_rect()
        value = (rect.bottom() - event.position().y()) / rect.height()
        self.points[index] = float(np.clip(value, self.points[index - 1], self.points[index + 1]))
        self.changed.emit(list(self.points))
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag_index = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event):
        self.points = default_grade()['curve']
        self.changed.emit(list(self.points))
        self.update()


class ViewerGradePanel(QScrollArea):
    changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('viewer_grade_panel')
        self.setFixedWidth(246)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grade = default_grade()
        self.sample = None
        content = QWidget()
        content.setObjectName('viewer_grade_content')
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 10, 12, 10)
        self.title = QLabel('Levels & Curves')
        self.title.setStyleSheet('font-weight: bold; font-size: 14px;')
        layout.addWidget(self.title)
        self.source = QLabel('Selected Cine')
        self.source.setWordWrap(True)
        layout.addWidget(self.source)
        self.curve = ToneCurve()
        layout.addWidget(self.curve)
        caption = QLabel('Shadows  →  Midtones  →  Highlights\nDrag the handles up or down.')
        caption.setStyleSheet('font-size: 11px;')
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self.controls = {}
        self.sliders = {}
        for name, label, minimum, maximum, value in (
            ('black', 'Black point', 0, 99.9, 0),
            ('white', 'White point', .1, 100, 100),
            ('gamma', 'Midtones', .1, 5, 1),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            spin = QDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setDecimals(2 if name == 'gamma' else 1)
            spin.setSingleStep(.05 if name == 'gamma' else .5)
            if name != 'gamma':
                spin.setSuffix(' %')
            spin.setValue(value)
            spin.setAccessibleName(label)
            self.controls[name] = spin
            row.addWidget(spin)
            layout.addLayout(row)
            slider = QSlider(Qt.Orientation.Horizontal)
            multiplier = 100 if name == 'gamma' else 10
            slider.setRange(round(minimum * multiplier), round(maximum * multiplier))
            slider.setValue(round(value * multiplier))
            slider.setAccessibleName(label)
            slider.valueChanged.connect(lambda v, s=spin, m=multiplier: s.setValue(v / m))
            spin.valueChanged.connect(lambda v, n=name: self._control_changed(n, v))
            self.sliders[name] = slider
            layout.addWidget(slider)
        buttons = QHBoxLayout()
        self.auto_button = QPushButton('Auto Levels')
        self.reset_button = QPushButton('Reset')
        buttons.addWidget(self.auto_button)
        buttons.addWidget(self.reset_button)
        layout.addLayout(buttons)
        self.original = QCheckBox('Bypass levels && curve')
        self.original.setToolTip('Temporarily compare against the image before these Viewer adjustments.')
        layout.addWidget(self.original)
        note = QLabel('Applies to this Cine’s display and export.\nAuto Levels uses the current frame.')
        note.setWordWrap(True)
        note.setStyleSheet('font-size: 11px;')
        layout.addWidget(note)
        layout.addStretch()
        self.curve.changed.connect(self._curve_changed)
        self.auto_button.clicked.connect(self._auto)
        self.reset_button.clicked.connect(self._reset)
        self.original.toggled.connect(self._bypass)

    def set_grade(self, grade, title=None):
        self.grade = deepcopy(grade)
        if title is not None:
            self.source.setText(title)
        for name, spin in self.controls.items():
            value = grade[name] * (1 if name == 'gamma' else 100)
            blocker = QSignalBlocker(spin)
            spin.setValue(value)
            del blocker
            blocker = QSignalBlocker(self.sliders[name])
            self.sliders[name].setValue(round(value * (100 if name == 'gamma' else 10)))
            del blocker
        blocker = QSignalBlocker(self.original)
        self.original.setChecked(not grade.get('enabled', True))
        del blocker
        self.curve.points = list(grade['curve'])
        self.curve.update()

    def set_image(self, image):
        if image is not None:
            self.curve.histogram, self.sample = sample_histogram(image)
            self.curve.update()

    def _control_changed(self, name, value):
        self.grade[name] = value / (1 if name == 'gamma' else 100)
        if self.grade['white'] - self.grade['black'] < .001:
            if name == 'black':
                self.grade['black'] = self.grade['white'] - .001
            else:
                self.grade['white'] = self.grade['black'] + .001
        self.set_grade(self.grade)
        self.changed.emit(deepcopy(self.grade))

    def _curve_changed(self, points):
        self.grade['curve'] = points
        self.changed.emit(deepcopy(self.grade))

    def _bypass(self, bypass):
        self.grade['enabled'] = not bypass
        self.changed.emit(deepcopy(self.grade))

    def _reset(self):
        self.set_grade(default_grade())
        self.changed.emit(deepcopy(self.grade))

    def _auto(self):
        if self.sample is None:
            return
        low, high = np.percentile(self.sample, [1, 99])
        if high - low < .001:
            return
        grade = default_grade()
        grade.update(black=float(low), white=float(high))
        self.set_grade(grade)
        self.changed.emit(deepcopy(self.grade))
