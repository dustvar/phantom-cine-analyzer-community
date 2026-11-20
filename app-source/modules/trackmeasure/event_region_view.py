"""Draw an analysis rectangle or place a fixed 4 × 4 sensor-pixel probe."""
from PySide6.QtCore import QLineF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from bos_ui import RegionView


class EventRegionView(RegionView):
    def __init__(self):
        super().__init__()
        self.point_mode = False
        self._point_pressed = False
        self.set_point_mode(False)

    def set_point_mode(self, enabled):
        self.point_mode = bool(enabled)
        self._point_pressed = False
        self.start = self.end = None
        self.setAccessibleName('Cine image. Click to place the 4 × 4 pixel probe.' if enabled
                               else 'Cine image. Drag to select the search rectangle.')
        self.update()

    def mousePressEvent(self, event):
        if not self.point_mode:
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self.image is not None:
            self._point_pressed = self.image_rect().contains(event.position())
            if self._point_pressed:
                self.dragStarted.emit()
                event.accept()

    def mouseReleaseEvent(self, event):
        if not self.point_mode:
            return super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pressed, self._point_pressed = self._point_pressed, False
        if not pressed or self.image is None or not self.image_rect().contains(event.position()):
            return
        width, height = self.image.width(), self.image.height()
        if min(width, height) < 4:
            return
        pixel = self._pixel(event.position())
        x = min(width - 4, max(0, round(pixel.x() - 2)))
        y = min(height - 4, max(0, round(pixel.y() - 2)))
        self.regionSelected.emit((x, y, 4, 4))
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._point_pressed = False
        super().keyPressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.point_mode or self.image is None or self.roi is None:
            return
        target = self.image_rect()
        x, y, w, h = self.roi
        scale_x = target.width() / self.image.width()
        scale_y = target.height() / self.image.height()
        cx = target.left() + (x + w / 2) * scale_x
        cy = target.top() + (y + h / 2) * scale_y
        gap_x = w * scale_x / 2 + 3
        gap_y = h * scale_y / 2 + 3
        ticks = (QLineF(cx - gap_x - 5, cy, cx - gap_x, cy), QLineF(cx + gap_x, cy, cx + gap_x + 5, cy),
                 QLineF(cx, cy - gap_y - 5, cx, cy - gap_y), QLineF(cx, cy + gap_y, cx, cy + gap_y + 5))
        painter = QPainter(self)
        painter.setClipRect(target)
        for color, width in (('#17242c', 3), ('#73e4e8', 1)):
            painter.setPen(QPen(QColor(color), width))
            for tick in ticks:
                painter.drawLine(tick)
