"""Edit a BOS speed cut in finished-video seconds."""
import math

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget)


def rate_curve(rect, start_fps, end_fps, curve):
    """Draw the normalized playback rate; screen y increases downward."""
    x, width = rect.left(), rect.width()
    y0, y1 = ((rect.bottom(), rect.top()) if start_fps < end_fps else
              (rect.top(), rect.bottom()) if start_fps > end_fps else
              (rect.center().y(), rect.center().y()))
    line = QPainterPath(QPointF(x, y0))
    if curve == 'smooth':
        # Bézier control points for y = 3u² − 2u³, with x linear in u.
        line.cubicTo(x+width/3, y0, x+2*width/3, y1, x+width, y1)
    else:
        if curve == 'instant':
            line.lineTo(x+width/2, y0)
            line.lineTo(x+width/2, y1)
        line.lineTo(x+width, y1)
    return line


class _RampPreview(QWidget):
    def __init__(self, left, right):
        super().__init__()
        self.left, self.right = left, right
        self.curve = 'instant'
        self.setFixedHeight(66)
        self.setAccessibleName('Speed ramp curve preview')

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(QColor('#202831'))
        p.setPen(QColor('#43515f'))
        p.drawRoundedRect(outer, 5, 5)
        area = outer.adjusted(12, 10, -12, -10)
        p.setPen(QPen(QColor('#536170'), 1, Qt.PenStyle.DotLine))
        p.drawLine(QPointF(area.center().x(), area.top()), QPointF(area.center().x(), area.bottom()))
        p.setPen(QPen(QColor('#99cff8'), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(rate_curve(area, self.left, self.right, self.curve))


class SpeedRampDialog(QDialog):
    def __init__(self, model, cut, cine_frame, parent):
        super().__init__(parent)
        self.model, self.cut = model, cut
        left, right = model.speeds[cut-1:cut+1]
        self.setWindowTitle('Speed ramp')
        self.setObjectName('bosRampEditor')
        self.setStyleSheet('QDialog#bosRampEditor { background: #20262e; } '
                           'QDialog#bosRampEditor QLabel { color: #e0e8ef; }')
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'Cut at Cine frame {cine_frame:,}    ·    {left.fps:g} → {right.fps:g} fps'))
        self.graph = _RampPreview(left.fps, right.fps)
        layout.addWidget(self.graph)
        form = QFormLayout()
        self.curve = QComboBox()
        self.curve.addItem('Ease in and out', 'smooth')
        self.curve.addItem('Linear', 'linear')
        self.curve.setAccessibleName('Speed ramp curve')
        self.curve.setCurrentIndex(self.curve.findData(right.curve))
        form.addRow('Curve', self.curve)
        self.duration = QDoubleSpinBox()
        self.duration.setSuffix(' s')
        self.duration.setKeyboardTracking(False)
        self.duration.setAccessibleName('Speed ramp duration in finished-video seconds')
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setAccessibleName('Adjust speed ramp duration')
        seconds = QHBoxLayout()
        seconds.addWidget(self.slider, 1)
        seconds.addWidget(self.duration)
        form.addRow('Duration', seconds)
        layout.addLayout(form)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.curve.currentIndexChanged.connect(self._set_limit)
        self.duration.valueChanged.connect(self._duration_changed)
        self.slider.valueChanged.connect(lambda n: self.duration.setValue(self.duration.maximum()*n/1000))
        self._set_limit()
        self.duration.setValue(right.ramp)
        self.original = (right.ramp, right.curve)
        self.initial_display = (self.duration.value(), self.curve.currentData())
        self._duration_changed()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_limit(self):
        limit = self.model.ramp_limit(self.cut, self.curve.currentData())
        digits = 6 if 0 < limit < .01 else 3
        # Round down to a duration the two adjacent sections can actually supply.
        maximum = math.floor(limit * 10**digits + 1e-9) / 10**digits
        self.duration.setDecimals(digits)
        self.duration.setRange(0, maximum)
        self.duration.setSingleStep(min(.05, max(10**-digits, round(maximum/20, digits))))
        self.slider.setEnabled(maximum > 0)
        self._duration_changed()

    def _duration_changed(self):
        value, limit = self.duration.value(), self.duration.maximum()
        with QSignalBlocker(self.slider):
            self.slider.setValue(round(1000 * value/limit) if limit else 0)
        self.graph.curve = self.curve.currentData() if value else 'instant'
        self.graph.update()
        note = ('The two sections have the same playback FPS.' if self.graph.left == self.graph.right else
                'Centered on the cut in finished-video time.')
        self.hint.setText(f'{note} Set 0 for an instant change. Up to {limit:g} s available here.')


    def settings(self):
        # Retain the exact model duration when reopening a constrained ramp.
        current = (self.duration.value(), self.curve.currentData())
        return self.original if current == self.initial_display else current
