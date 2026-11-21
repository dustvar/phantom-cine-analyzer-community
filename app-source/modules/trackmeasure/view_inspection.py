"""Display-only framing guides, magnification and orientation for Cine and live images."""
import math
import sys

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView, QHBoxLayout, QToolButton


GUIDE_NAMES = ('Off', 'Crosshair', 'Thirds grid')
PURPLE = QColor('#d395ed')
YELLOW = QColor('#ffcf52')


def guide_icon(mode, ink):
    icon = QPixmap(36, 36)
    icon.fill(Qt.transparent)
    painter = QPainter(icon)
    painter.scale(2, 2)
    painter.setPen(QPen(ink, 1))
    painter.drawRect(QRectF(1.5, 2.5, 15, 13))
    for t in ((6.5, 11.5) if mode == 2 else (9.,)):
        painter.drawLine(QPointF(t, 3), QPointF(t, 15))
    for t in ((7., 11.) if mode == 2 else (9.,)):
        painter.drawLine(QPointF(2, t), QPointF(16, t))
    painter.end()
    icon.setDevicePixelRatio(2)
    return QIcon(icon)


class InspectionBar(QFrame):
    def __init__(self, view, label):
        super().__init__(view.viewport())
        self.view = view
        self.setObjectName('pane_zoom_controls')
        self.setStyleSheet(
            'QFrame#pane_zoom_controls { background: rgba(17,20,27,226); border: 1px solid #505565; }'
            'QToolButton { border-radius: 0; padding: 0; font-size: 11px; }'
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
        def button(text, name, callback, width=24):
            control = QToolButton(self)
            control.setText(text)
            control.setFixedSize(width, 22)
            control.setAccessibleName(f'{name} · {label}')
            control.setToolTip(name)
            control.clicked.connect(callback)
            row.addWidget(control)
            return control

        self.minus = button('−', 'Zoom out', lambda: self.zoom(-1))
        self.fit = button('Fit', 'Fit the entire image', view.reset_view_zoom, 30)
        self.plus = button('+', 'Zoom in', lambda: self.zoom(1))
        self.plus.setToolTip('Zoom in · use the wheel or pinch over a detail, then drag to pan')
        self.minus.setToolTip('Zoom out · wheel down or pinch inward')
        self.guides = button('', 'Framing guides', self.cycle_guides)
        self.guides.setIconSize(QSize(18, 18))
        self.guide_icons = [guide_icon(mode, PURPLE if mode else QColor('#a9b2c3'))
                            for mode in range(len(GUIDE_NAMES))]
        self.update_guide_button()
        self.adjustSize()
        self.move(6, 6)

    def zoom(self, steps):
        self.view.zoom_at(self.view.viewport().rect().center(), steps)

    def cycle_guides(self):
        self.view.guide_mode = (self.view.guide_mode + 1) % len(GUIDE_NAMES)
        self.update_guide_button()
        self.view.viewport().update()

    def update_guide_button(self):
        mode = self.view.guide_mode
        self.guides.setIcon(self.guide_icons[mode])
        self.guides.setAccessibleDescription(f'Framing guides: {GUIDE_NAMES[mode]}')
        self.guides.setToolTip(f'Guides: {GUIDE_NAMES[mode]} · click for {GUIDE_NAMES[(mode+1) % 3].lower()}')


class InspectionView(QGraphicsView):
    """Image coordinates belong to the scene. Monitor overlays are painted in screen pixels."""
    def __init__(self):
        super().__init__()
        self._view_zoom = 1.
        self.guide_mode = 0
        self._pinch_total = 1.
        self.setScene(QGraphicsScene(self))
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        # A screen-fixed navigator must also be redrawn when Qt scrolls the scene.
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        if sys.platform != 'darwin':
            self.viewport().grabGesture(Qt.PinchGesture)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._view_zoom <= 1.:
            self.reset_view_zoom()

    def reset_view_zoom(self):
        self._view_zoom = 1.
        if self.scene() and not self.sceneRect().isEmpty():
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
        self.viewport().update()

    def zoom_at(self, view_pos, wheel_steps):
        if self.sceneRect().isEmpty() or not wheel_steps:
            return
        factor = 1.20 ** max(-20., min(20., wheel_steps))
        zoom = min(20., max(1., self._view_zoom * factor))
        if math.isclose(zoom, self._view_zoom):
            return
        if zoom <= 1.:
            self.reset_view_zoom()
            return
        point = self.mapToScene(view_pos)
        self.scale(zoom / self._view_zoom, zoom / self._view_zoom)
        self._view_zoom = zoom
        delta = self.mapFromScene(point) - view_pos
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + delta.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta.y())
        self.viewport().update()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.
        if not steps:
            steps = event.pixelDelta().y() / 80.
        self.zoom_at(event.position().toPoint(), steps)
        event.accept()

    def _gesture(self, event):
        if event.type() == QEvent.NativeGesture and event.gestureType() == Qt.ZoomNativeGesture:
            # macOS reports each magnification increment relative to the previous one.
            ratio = max(.05, 1. + event.value())
            self.zoom_at(self.viewport().mapFromGlobal(event.globalPosition().toPoint()),
                         math.log(ratio) / math.log(1.2))
        elif event.type() == QEvent.Gesture and (pinch := event.gesture(Qt.PinchGesture)):
            if pinch.state() == Qt.GestureStarted:
                self._pinch_total = 1.
            total = max(.05, pinch.totalScaleFactor())
            ratio = total / self._pinch_total
            self._pinch_total = total
            position = (self.viewport().mapFromGlobal(pinch.hotSpot().toPoint()) if pinch.hasHotSpot()
                        else pinch.centerPoint().toPoint())
            self.zoom_at(position, math.log(ratio) / math.log(1.2))
            event.accept(pinch)
        else:
            return False
        event.accept()
        return True

    def event(self, event):
        return self._gesture(event) or super().event(event)

    def viewportEvent(self, event):
        return self._gesture(event) or super().viewportEvent(event)

    def inspection_raster(self):
        item = getattr(self, '_image_item', None)
        if item is None:
            return None
        try:
            return item.pixmap()
        except RuntimeError:  # Qt can clear scene items during an empty-job restore.
            return None

    def position_navigator(self, area, viewport):
        # The Cine transport floats at the lower right; keep the overview clear of it.
        if area.width() + 9 + 122 < viewport.width():
            area.moveTopRight(viewport.topRight() + QPointF(-9, 9))
        else:
            area.moveBottomLeft(viewport.bottomLeft() + QPointF(9, -9))

    def navigator_geometry(self):
        """Return miniature frame and visible region in logical viewport coordinates."""
        viewport = QRectF(self.viewport().rect())
        image = self.viewportTransform().mapRect(self.sceneRect())
        if self._view_zoom <= 1. or image.isEmpty() or viewport.isEmpty():
            return QRectF(), QRectF()
        ratio = min(144. / image.width(), viewport.width() * .24 / image.width(),
                    96. / image.height(), viewport.height() * .22 / image.height())
        area = QRectF(0, 0, image.width() * ratio, image.height() * ratio)
        self.position_navigator(area, viewport)
        visible = viewport.intersected(image)
        window = QRectF(area.left() + (visible.left() - image.left()) * ratio,
                        area.top() + (visible.top() - image.top()) * ratio,
                        visible.width() * ratio, visible.height() * ratio)
        return area, window

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if not self.guide_mode and self._view_zoom <= 1.:
            return
        raster = self.inspection_raster()
        if raster is None or raster.isNull():
            return
        painter.save()
        painter.resetTransform()
        image_rect = self.viewportTransform().mapRect(self.sceneRect())
        if self.guide_mode:
            painter.setPen(QPen(PURPLE, 1))
            for part in ((.5,) if self.guide_mode == 1 else (1/3, 2/3)):
                x = image_rect.left() + image_rect.width() * part
                y = image_rect.top() + image_rect.height() * part
                painter.drawLine(QPointF(x, image_rect.top()), QPointF(x, image_rect.bottom()))
                painter.drawLine(QPointF(image_rect.left(), y), QPointF(image_rect.right(), y))
        area, window = self.navigator_geometry()
        if not area.isEmpty():
            painter.fillRect(area.adjusted(-3, -3, 3, 3), QColor(13, 15, 21, 235))
            painter.save()
            painter.translate(area.topLeft())
            ratio = area.width() / image_rect.width()
            painter.scale(ratio, ratio)
            painter.translate(-image_rect.topLeft())
            painter.setTransform(self.viewportTransform(), True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            if isinstance(raster, QImage):
                painter.drawImage(self.sceneRect(), raster)
            else:
                painter.drawPixmap(self.sceneRect(), raster, QRectF(raster.rect()))
            painter.restore()
            painter.setPen(QPen(YELLOW, 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(window)
        painter.restore()


class LiveInspectionView(InspectionView):
    """Display the incoming image directly and pan independently of the acquisition."""
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor('#0c1016'))
        self.setMouseTracking(True)
        self.bar = InspectionBar(self, 'Live camera')
        self.bar.hide()
        self.last_pan = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._view_zoom > 1. and not self.sceneRect().isEmpty():
            # Keep magnification relative to Fit when a panel opens or the window resizes.
            center = self.mapToScene(self.viewport().rect().center())
            bounds = self.transform().mapRect(self.sceneRect())
            viewport = self.viewport().rect().adjusted(2, 2, -2, -2)
            if not bounds.isEmpty() and not viewport.isEmpty():
                scale = min(viewport.width()/bounds.width(), viewport.height()/bounds.height())
                scale *= self._view_zoom
                self.scale(scale, scale)
                self.centerOn(center)

    def position_navigator(self, area, viewport):
        area.moveBottomRight(viewport.bottomRight() - QPointF(9, 9))

    def inspection_raster(self):
        return self.image

    def refresh_image(self):
        if self.image is None:
            self.reset_view_zoom()
            self.setSceneRect(QRectF())
            self.last_pan = None
            self.viewport().unsetCursor()
        elif self.sceneRect().size() != self.image.size():
            self.setSceneRect(QRectF(self.image.rect()))
            self.reset_view_zoom()
        self.bar.setVisible(self.image is not None)
        self.viewport().update()

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        if self.image is not None:
            painter.drawImage(self.sceneRect(), self.image)
        else:
            painter.save()
            painter.resetTransform()
            painter.setPen(QColor('#a6b2c3'))
            painter.drawText(self.viewport().rect().adjusted(10, 5, -10, -5),
                             Qt.AlignCenter | Qt.TextWordWrap, self.message)
            painter.restore()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._view_zoom > 1.:
            self.last_pan = event.position().toPoint()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.last_pan is not None:
            position = event.position().toPoint()
            delta = position - self.last_pan
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self.last_pan = position
            event.accept()
            return
        self.viewport().setCursor(Qt.OpenHandCursor if self._view_zoom > 1. else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.last_pan is not None and event.button() == Qt.LeftButton:
            self.last_pan = None
            self.viewport().setCursor(Qt.OpenHandCursor if self._view_zoom > 1. else Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)
