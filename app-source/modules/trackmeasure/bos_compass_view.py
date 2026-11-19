"""Image-space editing of the direction compass in the analyzed BOS player."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from bos_compass import CompassPlacement, MAX_SIZE, MIN_SIZE, compass_rect, compass_rgba


_CORNERS = {'nw': (-1, -1), 'ne': (1, -1), 'sw': (-1, 1), 'se': (1, 1)}
_CURSORS = {
    'nw': Qt.CursorShape.SizeFDiagCursor, 'se': Qt.CursorShape.SizeFDiagCursor,
    'ne': Qt.CursorShape.SizeBDiagCursor, 'sw': Qt.CursorShape.SizeBDiagCursor,
    'move': Qt.CursorShape.OpenHandCursor, 'remove': Qt.CursorShape.PointingHandCursor,
}


class CompassViewMixin:
    """Paint the export artwork on the fitted image and edit its source coordinates.

    The host is an ImageView with an optional compass_removed / compass_edit_started
    callback. Selection controls belong to the widget and never enter the frame cache.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.compass = None
        self.compass_selected = False
        self.compass_removed = None
        self.compass_edit_started = None
        self._last_placement = CompassPlacement()
        self._compass_visible = True
        self._compass_opacity = 1.
        self._drag = None
        self._artwork = None
        self.setMouseTracking(True)

    def set_compass_enabled(self, enabled):
        if enabled:
            self.compass = self._last_placement
            self.compass_selected = self._compass_visible
        else:
            self.compass = None
            self.deselect_compass()
        self.update()

    def set_compass_visible(self, visible):
        """Follow the current frame's BOS visibility while retaining placement."""
        if self._compass_visible == bool(visible):
            return
        self._compass_visible = bool(visible)
        if not visible:
            self.deselect_compass()
        self.update()

    def set_compass_opacity(self, value):
        if self._compass_opacity != value:
            self._compass_opacity = value
            self.update()

    def deselect_compass(self):
        self._drag = None
        self.unsetCursor()
        if self.compass_selected:
            self.compass_selected = False
            self.update()

    def _remove_compass(self):
        self.set_compass_enabled(False)
        if self.compass_removed:
            self.compass_removed()

    def compass_view_rect(self):
        if self.compass is None or not self._compass_visible or self.image is None:
            return QRectF()
        width, height = self.image.width(), self.image.height()
        x, y, side, _ = compass_rect(self.compass, width, height)
        target = self.image_rect()
        sx, sy = target.width()/width, target.height()/height
        return QRectF(target.x()+x*sx, target.y()+y*sy, side*sx, side*sy)

    @staticmethod
    def _grips(rect):
        # Small canvases still need a grabbable center between the corner grips.
        extent = min(10., rect.width()*.22, rect.height()*.22)
        for name, (sx, sy) in _CORNERS.items():
            left = rect.left() if sx < 0 else rect.right()-extent
            top = rect.top() if sy < 0 else rect.bottom()-extent
            yield name, QRectF(left, top, extent, extent)

    @staticmethod
    def _remove_rect(rect):
        if min(rect.width(), rect.height()) < 40:
            return QRectF()
        return QRectF(rect.center().x()-8, rect.top()+1, 16, 16)

    def _hit(self, position):
        rect = self.compass_view_rect()
        if rect.isEmpty() or not rect.contains(position):
            return None
        if self.compass_selected:
            if self._remove_rect(rect).contains(position):
                return 'remove'
            for name, grip in self._grips(rect):
                if grip.contains(position):
                    return name
        return 'move'

    def paintEvent(self, event):
        super().paintEvent(event)
        rect = self.compass_view_rect()
        if rect.isEmpty():
            return
        _, _, side, _ = compass_rect(self.compass, self.image.width(), self.image.height())
        if self._artwork is None or self._artwork.width() != side:
            pixels = compass_rgba(side)
            self._artwork = QImage(pixels.data, side, side, pixels.strides[0],
                                   QImage.Format.Format_RGBA8888).copy().convertToFormat(
                                       QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(self._compass_opacity)
        painter.drawImage(rect, self._artwork)
        painter.setOpacity(1.)
        if self.compass_selected:
            self._paint_selection(painter, rect)
        painter.end()

    def _paint_selection(self, painter, rect):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#19232c'), 3))
        border = rect.adjusted(1.5, 1.5, -1.5, -1.5)
        painter.drawRect(border)
        painter.setPen(QPen(QColor('#eef4f7'), 1))
        painter.drawRect(border)
        painter.setPen(QPen(QColor('#24313b'), 1.25))
        painter.setBrush(QColor('#f5f9fb'))
        for _, grip in self._grips(rect):
            handle = grip.adjusted(1, 1, -1, -1)
            painter.drawRoundedRect(handle, 1.5, 1.5)
        control = self._remove_rect(rect)
        if not control.isEmpty():
            painter.setBrush(QColor('#27323b'))
            painter.setPen(QPen(QColor('#f5f9fb'), 1.1))
            painter.drawEllipse(control.adjusted(.75, .75, -.75, -.75))
            center = control.center()
            for sx in (-1, 1):
                painter.drawLine(center+QPointF(sx*3, -3), center+QPointF(-sx*3, 3))
        painter.restore()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            hit = self._hit(pos)
            if hit:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                if self.compass_edit_started:
                    self.compass_edit_started()
                if hit == 'remove':
                    self._remove_compass()
                else:
                    self.compass_selected = True
                    self._drag = (hit, QPointF(pos), self.compass)
                    self.setCursor(Qt.CursorShape.ClosedHandCursor if hit == 'move' else _CURSORS[hit])
                    self.update()
                event.accept()
                return
            self.deselect_compass()
        super().mousePressEvent(event)

    def _drag_to(self, position):
        hit, start, placement = self._drag
        image = self.image_rect()
        if self.image is None or image.isEmpty():
            return
        width, height = self.image.width(), self.image.height()
        short = min(width, height)
        dx = (position.x()-start.x())*width/image.width()
        dy = (position.y()-start.y())*height/image.height()
        x, y, side, _ = compass_rect(placement, width, height)
        size = placement.size
        if hit == 'move':
            x = min(max(0, x+dx), width-side)
            y = min(max(0, y+dy), height-side)
        else:
            sx, sy = _CORNERS[hit]
            # Keep the diagonally opposite corner fixed, even when the pointer
            # has moved beyond the image or across the opposite corner.
            anchor_x = x if sx > 0 else x+side
            anchor_y = y if sy > 0 else y+side
            bound = min(width-anchor_x if sx > 0 else anchor_x,
                        height-anchor_y if sy > 0 else anchor_y, max(1, round(MAX_SIZE*short)))
            change = dx*sx if abs(dx) >= abs(dy) else dy*sy
            side = min(bound, max(max(1, round(MIN_SIZE*short)), round(side+change)))
            size = max(MIN_SIZE, min(MAX_SIZE, side/short))
            x = anchor_x if sx > 0 else anchor_x-side
            y = anchor_y if sy > 0 else anchor_y-side
        self.compass = self._last_placement = CompassPlacement(x/width, y/height, size)
        self.update()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self._drag_to(event.position())
            event.accept()
            return
        hit = self._hit(event.position())
        if hit:
            self.setCursor(_CURSORS[hit])
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            # Some native event sequences deliver only the final drag position.
            if event.position() != self._drag[1]:
                self._drag_to(event.position())
            self._drag = None
            self.setCursor(_CURSORS.get(self._hit(event.position()), Qt.CursorShape.ArrowCursor))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.compass_selected:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._remove_compass()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Escape:
                if self._drag is not None:
                    self.compass = self._last_placement = self._drag[2]
                self.deselect_compass()
                event.accept()
                return
        super().keyPressEvent(event)
