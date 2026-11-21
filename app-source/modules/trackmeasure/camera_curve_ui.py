"""Interactive transfer curve for the live camera monitor."""
from dataclasses import replace

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from camera_monitor import LINEAR_CURVE, active_curve


MODES = (
    ('Auto brightness', 'auto', 'Adapts to the scene and lifts shadows for the monitor.'),
    ('Standard', 'standard', 'Uses a fixed sRGB curve for a natural monitor image.'),
    ('Linear', 'linear', 'Shows the sensor signal directly. Half signal gives half the display value.'),
    ('Lift shadows', 'shadows', 'Brightens darker tones with a fixed curve.'),
    ('Contrast', 'contrast', 'Deepens shadows and brightens highlights with an S-curve.'),
    ('Custom', 'custom', 'Uses the points you set on the curve.'),
)


class MonitorCurveGraph(QWidget):
    edited = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.curve = LINEAR_CURVE
        self.selected = None
        self.dragging = False
        self.setMinimumSize(220, 170)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName('Monitor brightness curve')
        self.setToolTip('Click the line to add a point. Drag up to brighten or down to darken. '
                        'Arrow keys adjust a selected point. Delete removes an interior point.')

    def set_curve(self, curve):
        if curve != self.curve:
            self.curve = curve
            self.selected = None
            self.update()

    def plot(self):
        return QRectF(self.rect()).adjusted(11, 25, -11, -22)

    def point_at(self, x, y):
        plot = self.plot()
        return QPointF(plot.left() + x * plot.width(), plot.bottom() - y * plot.height())

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor('#0c1015'))
        plot = self.plot()
        p.setPen(QPen(QColor('#33404b'), 1.))
        for f in (0., .25, .5, .75, 1.):
            p.drawLine(self.point_at(f, 0), self.point_at(f, 1))
            p.drawLine(self.point_at(0, f), self.point_at(1, f))
        p.setPen(QPen(QColor('#63717c'), 1., Qt.DashLine))
        p.drawLine(plot.bottomLeft(), plot.topRight())
        path = QPainterPath()
        x = np.linspace(0., 1., max(128, int(plot.width())))
        y = self.curve.values(x)
        path.moveTo(self.point_at(x[0], y[0]))
        for xx, yy in zip(x[1:], y[1:]):
            path.lineTo(self.point_at(xx, yy))
        p.setPen(QPen(QColor('#d795ee'), 2.))
        p.drawPath(path)
        for i, (xx, yy) in enumerate(self.curve.points):
            pos = self.point_at(xx, yy)
            ink = QColor('#88e2b3') if i == self.selected else QColor('#e8c9f4')
            p.setPen(QPen(ink, 1.5))
            p.setBrush(QColor('#17151e'))
            p.drawRect(QRectF(pos.x() - 3.5, pos.y() - 3.5, 7., 7.))
        p.setPen(QColor('#aab6c3'))
        font = p.font()
        font.setPixelSize(11)
        p.setFont(font)
        p.drawText(QPointF(plot.left(), 16.), 'OUTPUT ↑')
        p.drawText(QRectF(plot.left(), plot.bottom() + 3, plot.width(), 16),
                   Qt.AlignRight | Qt.AlignVCenter, 'INPUT →')
        if self.selected is not None:
            xx, yy = self.curve.points[self.selected]
            p.setPen(QColor('#88e2b3'))
            p.drawText(QRectF(plot.left(), 2, plot.width(), 18), Qt.AlignRight | Qt.AlignVCenter,
                       f'{xx:.0%} → {yy:.0%}')

    def _hit(self, pos):
        for index, (xx, yy) in enumerate(self.curve.points):
            if (self.point_at(xx, yy) - pos).manhattanLength() <= 10.:
                return index, xx
        plot = self.plot()
        x = (pos.x() - plot.left()) / plot.width()
        radius = 8 / plot.width()
        if x < -radius or x > 1 + radius:
            return None, None
        xx = np.linspace(max(0., x-radius), min(1., x+radius), 25)
        yy = self.curve.values(xx)
        dx = plot.left() + xx * plot.width() - pos.x()
        dy = plot.bottom() - yy * plot.height() - pos.y()
        distance_sq = dx * dx + dy * dy
        closest = np.argmin(distance_sq)
        return None, (float(xx[closest]) if distance_sq[closest] <= 64 else None)

    def _edit(self, points):
        self.curve = replace(self.curve, points=tuple(points))
        self.edited.emit(self.curve)
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        index, x = self._hit(event.position())
        if x is None:
            return
        self.setFocus(Qt.MouseFocusReason)
        if index is None:
            points = list(self.curve.points)
            index = next((i for i, point in enumerate(points) if point[0] >= x), len(points)-1)
            if x <= 0 or x >= 1 or points[index][0] == x:
                return
            points.insert(index, (x, float(self.curve.values(x))))
            self._edit(points)
        self.selected = index
        self.dragging = True
        self.setCursor(Qt.ClosedHandCursor)
        self.update()

    def _move_to(self, y):
        i, points = self.selected, list(self.curve.points)
        low = points[i-1][1] if i else 0.
        high = points[i+1][1] if i+1 < len(points) else 1.
        points[i] = (points[i][0], float(np.clip(y, low, high)))
        self._edit(points)

    def mouseMoveEvent(self, event):
        if self.dragging:
            plot = self.plot()
            self._move_to((plot.bottom()-event.position().y()) / plot.height())
        else:
            index, x = self._hit(event.position())
            cursor = Qt.ArrowCursor
            if index is not None:
                cursor = Qt.OpenHandCursor
            elif x is not None:
                cursor = Qt.CrossCursor
            self.setCursor(cursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            self.unsetCursor()

    def keyPressEvent(self, event):
        i, key = self.selected, event.key()
        if i is None:
            return super().keyPressEvent(event)
        if key in (Qt.Key_Up, Qt.Key_Down):
            self._move_to(self.curve.points[i][1] + (.005 if key == Qt.Key_Up else -.005))
        elif key in (Qt.Key_Delete, Qt.Key_Backspace) and 0 < i < len(self.curve.points)-1:
            points = list(self.curve.points)
            del points[i]
            self.selected = None
            self._edit(points)
        else:
            return super().keyPressEvent(event)


class MonitorCurveEditor(QDialog):
    changed = Signal(str, object)

    def __init__(self, gain, parent=None):
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle('Monitor brightness')
        self.gain = gain
        self.custom = LINEAR_CURVE
        self.resize(320, 314)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)
        header = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.setAccessibleName('Monitor brightness preset')
        for name, mode, tip in MODES:
            self.preset.addItem(name, mode)
            self.preset.setItemData(self.preset.count()-1, tip, Qt.ToolTipRole)
        header.addWidget(self.preset, 1)
        self.reset = QPushButton('Reset')
        self.reset.setAccessibleName('Reset monitor brightness to default')
        self.reset.setToolTip('Clear custom points and restore Auto brightness.')
        self.reset.setAutoDefault(False)
        header.addWidget(self.reset)
        layout.addLayout(header)
        self.graph = MonitorCurveGraph()
        layout.addWidget(self.graph, 1)
        note = QLabel('Drag the line to add a point. Delete removes it. Preview only.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.graph.edited.connect(self._curve_edited)
        self.reset.clicked.connect(self.reset_default)
        self.timer = QTimer(self)
        self.timer.setInterval(160)
        self.timer.timeout.connect(self._refresh)
        self._refresh()

    def _refresh(self):
        if self.graph.dragging:
            return
        mode = self.preset.currentData()
        self.preset.setToolTip(self.preset.currentData(Qt.ToolTipRole))
        self.graph.set_curve(active_curve(mode, self.gain(), self.custom))

    def _preset_changed(self):
        self._refresh()
        self.changed.emit(self.preset.currentData(), self.custom)

    def _curve_edited(self, curve):
        self.custom = curve
        with QSignalBlocker(self.preset):
            self.preset.setCurrentIndex(self.preset.findData('custom'))
        self.preset.setToolTip(self.preset.currentData(Qt.ToolTipRole))
        self.changed.emit('custom', curve)

    def reset_default(self):
        self.custom = LINEAR_CURVE
        with QSignalBlocker(self.preset):
            self.preset.setCurrentIndex(self.preset.findData('auto'))
        self.graph.selected = None
        self._preset_changed()
        self.graph.update()

    def toggle_at(self, button):
        if self.isVisible():
            self.hide()
            return
        available = button.screen().availableGeometry()
        anchor = button.mapToGlobal(QPoint(0, 0))
        x = min(max(anchor.x(), available.left()), max(available.left(), available.right()-self.width()))
        y = anchor.y()-self.height()-24
        if y < available.top():
            y = min(anchor.y()+button.height()+6, available.bottom()-self.height())
        self.move(x, max(available.top(), y))
        self.show()
        self.raise_()

    def showEvent(self, event):
        self._refresh()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
