"""Viewer playhead and inclusive clip markers on the same frame ruler."""
from PySide6.QtCore import QObject, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSlider


class ClipRange(QObject):
    """The sequence selection can span more frames than the analysis slider."""
    rangeChanged = Signal(int, int)

    def __init__(self, slider):
        super().__init__(slider)
        self._minimum = self._maximum = self._lower = self._upper = 0
        self._enabled = False

    def setEnabled(self, enabled):
        self._enabled = bool(enabled)
        self.parent().update()

    def isEnabled(self):
        return self._enabled

    def setRange(self, minimum, maximum):
        self.parent().cancel_marker_drag()
        self._minimum = int(minimum)
        self._maximum = max(self._minimum, int(maximum))
        self.setValues(self._minimum, self._maximum)

    def setValues(self, lower, upper):
        lower, upper = sorted((int(lower), int(upper)))
        lower = max(self._minimum, min(lower, self._maximum))
        upper = max(self._minimum, min(upper, self._maximum))
        if (lower, upper) != (self._lower, self._upper):
            self._lower, self._upper = lower, upper
            self.parent().update()
            self.rangeChanged.emit(lower, upper)

    def lowerValue(self):
        return self._lower

    def upperValue(self):
        return self._upper


class ViewerScrubber(QSlider):
    """Seek on the rail or drag either bracket below it, even for a one-frame clip."""
    markerPressed = Signal()
    markerPreview = Signal(int, int)
    margin = 14

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.clip_range = ClipRange(self)
        self.viewer_mode = False
        self._marker = None
        self._preview = None
        self._press_x = self._start_frame = 0
        self._head_grab = False
        self.setAccessibleDescription('Drag on the rail to seek or drag the lower brackets to trim. Mark In and Mark Out also set exact frames.')
        self.setMouseTracking(True)
        self.valueChanged.connect(lambda _: self.update())

    def set_viewer_mode(self, enabled):
        if self.viewer_mode == enabled:
            return
        self.cancel_marker_drag()
        self.viewer_mode = enabled
        self.unsetCursor()
        self.setFixedHeight(34 if enabled else 26)
        self.setToolTip('Seek on the rail. Drag [ or ] below it to set the first or last export frame.'
                        if enabled else 'Drag to preview a frame.')
        self.update()

    def _marks(self):
        return self._preview or (self.clip_range.lowerValue(), self.clip_range.upperValue())

    def _x(self, frame):
        span = max(1, self.width() - 2 * self.margin)
        return self.margin + (frame - self.minimum()) * span / max(1, self.maximum() - self.minimum())

    def _frame(self, x):
        proportion = (x - self.margin) / max(1, self.width() - 2 * self.margin)
        return self.minimum() + round(max(0., min(1., proportion)) * (self.maximum() - self.minimum()))

    def _rail_y(self):
        return self.height() // 2 - 2

    def _marker_at(self, pos):
        if not self.viewer_mode or not self.clip_range.isEnabled():
            return None
        lower, upper = self._marks()
        left, right = self._x(lower) - 3, self._x(upper) + 3
        if pos.y() < self._rail_y() - 1:
            return None
        left_dist, right_dist = abs(pos.x() - left), abs(pos.x() - right)
        if min(left_dist, right_dist) > 9:
            return None
        return 'in' if left_dist <= right_dist else 'out'

    def paintEvent(self, event):
        if not (self.viewer_mode and self.clip_range.isEnabled()):
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self._rail_y()
        lower, upper = self._marks()
        left, right = self._x(lower), self._x(upper)
        def line(color, width, x1, y1, x2, y2):
            painter.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        line('#494f59', 4, self._x(self.minimum()), y, self._x(self.maximum()), y)
        line('#81baac', 4, left, y, right, y)
        for marker, x, direction in [('in', left-3, 1), ('out', right+3, -1)]:
            tint = '#fff0af' if marker == self._marker else '#a2e2cb'
            for arm_y in (y-1, y+11):
                line(tint, 2.5, x, arm_y, x+direction*5, arm_y)
            line(tint, 2.5, x, y-1, x, y+11)
        head = self._x(self.sliderPosition())
        painter.setBrush(QColor('#f5f8fb'))
        painter.setPen(QPen(QColor('#202a34'), 1))
        painter.drawPolygon(QPolygonF([QPointF(head-5, y-11), QPointF(head+5, y-11),
                                      QPointF(head+5, y-5), QPointF(head, y+2), QPointF(head-5, y-5)]))

    def mousePressEvent(self, event):
        if not (self.viewer_mode and self.clip_range.isEnabled()):
            return super().mousePressEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._marker = self._marker_at(event.position())
        self._press_x = event.position().x()
        if self._marker:
            self._start_frame = self._marks()[0 if self._marker == 'in' else 1]
            self.markerPressed.emit()
            self.update()
        else:
            self._start_frame = self.sliderPosition()
            self._head_grab = (abs(self._press_x - self._x(self._start_frame)) <= 6
                               and event.position().y() <= self._rail_y()+2)
            self.setSliderDown(True)
            self._seek(self._press_x)
        event.accept()

    def _drag_frame(self, x):
        # A press and release on an existing handle must leave its exact frame
        # intact even when a single display pixel represents hundreds of frames.
        span = max(1, self.width() - 2 * self.margin)
        delta = round((x - self._press_x) * (self.maximum() - self.minimum()) / span)
        frame = max(self.minimum(), min(self._start_frame + delta, self.maximum()))
        if x <= 0:
            frame = self.minimum()
        elif x >= self.width() - 1:
            frame = self.maximum()
        return frame

    def _seek(self, x):
        self.setSliderPosition(self._drag_frame(x) if self._head_grab else self._frame(x))

    def _drag_marker(self, x):
        lower, upper = self.clip_range.lowerValue(), self.clip_range.upperValue()
        frame = self._drag_frame(x)
        self._preview = (min(frame, upper), upper) if self._marker == 'in' else (lower, max(lower, frame))
        self.markerPreview.emit(*self._preview)
        self.update()

    def mouseMoveEvent(self, event):
        if not (self.viewer_mode and self.clip_range.isEnabled()):
            return super().mouseMoveEvent(event)
        if self._marker:
            self._drag_marker(event.position().x())
        elif self.isSliderDown():
            self._seek(event.position().x())
        else:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._marker_at(event.position()) else Qt.CursorShape.PointingHandCursor)
        event.accept()

    def mouseReleaseEvent(self, event):
        if not (self.viewer_mode and self.clip_range.isEnabled()):
            return super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            if self._marker:
                self._drag_marker(event.position().x())
                bounds = self._marks()
                self.cancel_marker_drag()
                self.clip_range.setValues(*bounds)
            elif self.isSliderDown():
                self._seek(event.position().x())
                self.setSliderDown(False)
        event.accept()

    def cancel_marker_drag(self):
        if self._marker:
            self._marker = self._preview = None
            self.markerPreview.emit(self.clip_range.lowerValue(), self.clip_range.upperValue())
            self.update()
