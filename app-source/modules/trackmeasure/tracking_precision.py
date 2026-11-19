"""Screen-sized precision preview, separate from measurement scene geometry."""
import math

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTransform
from PySide6.QtWidgets import QWidget


def image_step_for_arrow(transform, dx, dy, step=1.0):
    """Move in screen direction by a fixed source-image distance at any zoom."""
    inverse, valid = transform.inverted()
    if not valid:
        return QPointF(dx * step, dy * step)
    delta = inverse.map(QPointF(dx, dy)) - inverse.map(QPointF())
    length = math.hypot(delta.x(), delta.y())
    return delta * (step / length) if length else QPointF()


class PointPickerOverlay(QWidget):
    """Magnify the displayed image without obscuring or intercepting its input."""
    def __init__(self, graph):
        super().__init__(graph.viewport())
        self.graph = graph
        self.candidate = None
        self.lens_rect = QRectF()
        self.setObjectName('point_picker_preview')
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.setGeometry(graph.viewport().rect())
        graph.viewport().installEventFilter(self)
        graph.horizontalScrollBar().valueChanged.connect(self._view_changed)
        graph.verticalScrollBar().valueChanged.connect(self._view_changed)
        self.hide()

    def _view_changed(self, *args):
        self.setGeometry(self.graph.viewport().rect())
        self.update()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self.setGeometry(self.graph.viewport().rect())
            self.update()
        return super().eventFilter(watched, event)

    def show_point(self, point):
        self.candidate = QPointF(point)
        self.setGeometry(self.graph.viewport().rect())
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event):
        point = self.candidate
        if point is None:
            return
        item = getattr(self.graph, '_image_item', None)
        try:
            pixmap = item.pixmap() if item is not None else None
        except RuntimeError:  # A scene redraw can replace the previous image item.
            return
        if pixmap is None or pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        position = self.graph.mapFromScene(point)
        # Keep the loupe on the opposite side from the point being selected.
        lens = max(24, min(156, self.height() - 108, self.width() - 36))
        width, height = lens + 20, lens + 92
        left = 10 if position.x() > self.width() / 2 else self.width() - width - 10
        top=8
        # Ruler picking leaves the pane zoom buttons available. Place a
        # left-side loupe below them instead of drawing over - / Fit / +.
        panes=getattr(self.graph.parent,'workspace_panes',())
        index=getattr(self.graph,'pane_index',-1)
        controls=getattr(panes[index],'view_controls',None) if 0<=index<len(panes) else None
        bar=getattr(controls,'bar',None)
        if bar is not None and bar.isVisible() and left<bar.geometry().right()+8:
            if bar.geometry().bottom()+16+height<=self.height():top=bar.geometry().bottom()+8
            else:left=self.width()-width-10
        card = QRectF(max(4, left), top, width, height)
        painter.setPen(QPen(QColor('#6baebd'), 1))
        painter.setBrush(QColor(25, 31, 39, 242))
        painter.drawRoundedRect(card, 7, 7)
        font = QFont(self.font())
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor('#eef3f7'))
        painter.drawText(card.adjusted(10, 6, -10, -height + 25),
                         Qt.AlignCenter, 'Point preview · 8×')
        self.lens_rect = QRectF(card.left() + 10, card.top() + 29, lens, lens)
        painter.fillRect(self.lens_rect, QColor('#0d1116'))
        painter.save()
        painter.setClipRect(self.lens_rect)
        painter.translate(self.lens_rect.center())
        painter.scale(8, 8)
        # Display rotation follows the main view; coordinate values stay raw.
        transform = self.graph.transform()
        scale = math.hypot(transform.m11(), transform.m12()) or 1.0
        orientation = QTransform(transform.m11() / scale, transform.m12() / scale,
                                 transform.m21() / scale, transform.m22() / scale, 0, 0)
        painter.setTransform(orientation, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.drawPixmap(QPointF(-point.x(), -point.y()), pixmap)
        painter.restore()
        self._crosshair(painter, self.lens_rect.center(), lens / 2 - 4)
        painter.setPen(QColor('#eef3f7'))
        y = self.lens_rect.bottom() + 4
        painter.drawText(QRectF(card.left() + 4, y, width - 8, 19), Qt.AlignCenter,
                         f'X {point.x():.1f}   Y {point.y():.1f} px')
        painter.setPen(QColor('#b8c8d5'))
        font.setPixelSize(11)
        painter.setFont(font)
        painter.drawText(QRectF(card.left() + 3, y + 21, width - 6, 15), Qt.AlignCenter,
                         'Arrows 1 px · Shift 0.1 px')
        painter.drawText(QRectF(card.left() + 3, y + 36, width - 6, 15), Qt.AlignCenter,
                         'Enter to confirm')
        self._crosshair(painter, QPointF(position), 10)
        painter.end()

    @staticmethod
    def _crosshair(painter, point, radius):
        # A small central gap leaves the actual image detail visible.
        for color, width in [('#10151a', 3), ('#7ce8ff', 1)]:
            painter.setPen(QPen(QColor(color), width))
            for start, end in [((-radius, 0), (-3, 0)), ((3, 0), (radius, 0)),
                               ((0, -radius), (0, -3)), ((0, 3), (0, radius))]:
                painter.drawLine(point + QPointF(*start), point + QPointF(*end))
