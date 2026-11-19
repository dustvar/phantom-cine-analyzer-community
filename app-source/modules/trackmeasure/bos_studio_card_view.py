"""Direct manipulation of rectangular graphics over a fitted BOS image."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter

from bos_compass_view import CompassViewMixin, _CORNERS, _CURSORS


class StudioCardViewMixin(CompassViewMixin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.text_card = self.vector_key_card = None
        self.selected_card = None
        self._card_drag = None
        self.card_selected = self.card_placed = self.card_restored = None
        self.card_placement = None

    def _card(self, name):
        return self.text_card if name == 'text' else self.vector_key_card

    def card_view_rect(self, name):
        card = self._card(name)
        if card is None or self.image is None:
            return QRectF()
        x, y, w, h = card[1]
        r = self.image_rect()
        sx, sy = r.width()/self.image.width(), r.height()/self.image.height()
        return QRectF(r.x()+x*sx, r.y()+y*sy, w*sx, h*sy)

    def _card_hit(self, pos):
        if self.selected_card:
            rect = self.card_view_rect(self.selected_card)
            for name, grip in self._grips(rect):
                if grip.contains(pos):
                    return self.selected_card, name
        for name in ('text', 'vector_key'):
            if self.card_view_rect(name).contains(pos):
                return name, 'move'
        return None

    def clear_card_selection(self):
        self._card_drag = self.selected_card = None
        self.unsetCursor()
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.image is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for name in ('vector_key','text'):
            card = self._card(name)
            if card is None:
                continue
            pixels, (_, _, w, h) = card
            art = QImage(pixels.data, w, h, pixels.strides[0], QImage.Format.Format_RGBA8888)
            painter.drawImage(self.card_view_rect(name), art)
        if self.selected_card:
            rect = self.card_view_rect(self.selected_card)
            if not rect.isEmpty():
                self._paint_selection(painter, rect, removable=False)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self._card_hit(event.position())
            if hit:
                name, action = hit
                self.deselect_compass()
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                if self.card_selected:
                    self.card_selected(name)
                self.selected_card = name
                self._card_drag = (name, action, QPointF(event.position()), self._card(name)[1],
                                   self.card_placement(name))
                self.setCursor(Qt.CursorShape.ClosedHandCursor if action == 'move' else _CURSORS[action])
                self.update()
                event.accept()
                return
            self.clear_card_selection()
        super().mousePressEvent(event)

    def _move_card(self, pos):
        name, action, start, (x, y, w, h), placement = self._card_drag
        rect = self.image_rect()
        if rect.isEmpty():
            return
        dx = (pos.x()-start.x())*self.image.width()/rect.width()
        dy = (pos.y()-start.y())*self.image.height()/rect.height()
        if action == 'move':
            self.card_placed(name, placement.scale, (x+dx,y+dy), (0,0))
            return
        sx, sy = _CORNERS[action]
        # Project the pointer on the diagonal. All four handles preserve aspect
        # ratio and fix the opposite corner in source coordinates.
        ratio = 1.+(sx*dx*w+sy*dy*h)/(w*w+h*h)
        px, py = x if sx > 0 else x+w, y if sy > 0 else y+h
        bound = min((self.image.width()-px if sx > 0 else px)/w,
                    (self.image.height()-py if sy > 0 else py)/h)
        scale = min(4., max(.25, min(ratio,bound)*placement.scale))
        self.card_placed(name, scale, (px,py), (int(sx<0),int(sy<0)))

    def mouseMoveEvent(self, event):
        if self._card_drag is not None:
            self._move_card(event.position())
            event.accept()
            return
        hit = self._card_hit(event.position())
        if hit and self._drag is None:
            self.setCursor(_CURSORS[hit[1]])
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._card_drag is not None:
            if event.position() != self._card_drag[2]:
                self._move_card(event.position())
            self._card_drag = None
            hit = self._card_hit(event.position())
            self.setCursor(_CURSORS[hit[1]] if hit else Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.selected_card:
            if self._card_drag:
                name, _, _, _, placement = self._card_drag
                self.card_restored(name, placement)
            self.clear_card_selection()
            event.accept()
            return
        super().keyPressEvent(event)
