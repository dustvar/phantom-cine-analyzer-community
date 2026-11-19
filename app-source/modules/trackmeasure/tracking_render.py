"""Record a fading trajectory without allocating a Qt scene item per point."""
import numpy as np
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPicture, QPainter, QPainterPath, QPen, QBrush, QColor
from PySide6.QtWidgets import QGraphicsPathItem
from shiboken6 import isValid


def fade_alphas(points, centers, radius, transparency):
    alphas = np.full(len(points), 255, dtype=np.uint8)
    alpha = int(np.clip(round(255.0 * (1.0 - float(transparency) / 100.0)), 0, 255))
    if float(radius) <= 0:
        alphas.fill(alpha)
        return alphas
    if not len(centers):
        return alphas
    # Work one center at a time, avoiding a points-by-objects temporary array.
    minimum = np.full(len(points), np.inf)
    for center in centers:
        delta = points - center
        np.minimum(minimum, np.sqrt(np.sum(delta * delta, axis=1)), out=minimum)
    alphas[minimum <= float(radius)] = alpha
    return alphas


class RecordedTrackPath(QGraphicsPathItem):
    """A non-interactive drawing; selection and snapping still use measured points."""
    def __init__(self, points, radius, color, alphas, heat_values, color_for_value):
        bounds = QRectF(float(points[:, 0].min()) - radius - 1,
                        float(points[:, 1].min()) - radius - 1,
                        float(np.ptp(points[:, 0])) + 2 * radius + 2,
                        float(np.ptp(points[:, 1])) + 2 * radius + 2)
        shape = QPainterPath(); shape.addRect(bounds)
        super().__init__(shape)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.picture = QPicture()
        painter = QPainter(self.picture)
        pens = {}
        def pen_for(qcolor):
            key = qcolor.rgba()
            if key not in pens:
                pen = QPen(qcolor); pen.setCosmetic(True)
                pens[key] = pen
            return pens[key]
        base = QColor(color)
        for index, point in enumerate(points):
            point_color = (QColor(base) if heat_values is None
                           else color_for_value(heat_values[index]))
            point_color.setAlpha(int(alphas[index]))
            painter.setPen(pen_for(point_color)); painter.setBrush(QBrush(point_color))
            painter.drawEllipse(QRectF(float(point[0]) - radius, float(point[1]) - radius,
                                      radius * 2, radius * 2))
            if index:
                line_color = QColor(base); line_color.setAlpha(int(min(alphas[index-1], alphas[index])))
                painter.setPen(pen_for(line_color)); painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawLine(QPointF(float(points[index-1][0]), float(points[index-1][1])),
                                 QPointF(float(point[0]), float(point[1])))
        painter.end()

    def paint(self, painter, option, widget=None):
        painter.drawPicture(0, 0, self.picture)

    def shape(self):
        # Decorative trajectories must not cover an editable region in itemAt
        # merely because its coordinates lie inside the trajectory's bounds.
        return QPainterPath()


def add_faded_path(graph, points, radius, color, alphas, heat_values, color_for_value):
    item = RecordedTrackPath(points, radius, color, alphas, heat_values, color_for_value)
    graph.scene().addItem(item)
    # Retain the Python wrapper through Qt hit tests and virtual paint dispatch.
    # Scene clear deletes the native items; prune those wrappers on the next draw.
    retained = [old for old in getattr(graph, '_recorded_track_paths', [])
                if isValid(old) and old.scene() is graph.scene()]
    graph._recorded_track_paths = retained + [item]
