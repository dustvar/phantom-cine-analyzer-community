"""Source chooser and persistent navigation for the PCA desktop workspace."""
import math
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from app_runtime import APP_VERSION


# Each icon is drawn on the same 24-unit grid; Qt owns hover and focus feedback.
GLYPHS = {
    'camera': [[(2,6),(15,6),(15,18),(2,18),(2,6)],[(15,10),(21,7),(21,17),(15,14)]],
    'viewer': [[(3,4),(21,4),(21,20),(3,20),(3,4)],[(10,8),(16,12),(10,16),(10,8)]],
    'event': [[(2,15),(7,15),(10,6),(13,18),(16,11),(22,11)]],
    'overlay': [[(3,3),(16,3),(16,15),(3,15),(3,3)],[(7,18),(7,21),(21,21),(21,8),(19,8)]],
    'schlieren': [[(2,9),(6,7),(11,8),(16,4),(22,3)],[(2,14),(7,11),(12,12),(17,8),(22,7)],[(2,19),(8,15),(14,16),(19,12),(22,11)]],
    'track': [[(2,17),(8,10),(13,14),(21,5)],[(16,5),(21,5),(21,10)],[(2,14),(2,18),(6,18)]],
    'scale': [[(2,16),(16,2),(22,8),(8,22),(2,16)],[(7,11),(10,14)],[(10,8),(12,10)],[(13,5),(16,8)]],
    'distance': [[(4,18),(20,6)],[(3,15),(6,20)],[(18,4),(21,9)]],
    'angle': [[(17,4),(4,19),(22,19)],[(11,11),(14,13),(16,17)]],
    'lines': [[(2,9),(21,3)],[(2,20),(21,14)]],
    'area': [[(3,19),(5,5),(15,3),(21,12),(16,20),(3,19)],[(8,15),(16,7)],[(12,16),(17,11)]],
    'report': [[(5,2),(15,2),(20,7),(20,22),(5,22),(5,2)],[(15,3),(15,8),(19,8)],[(8,16),(11,12),(14,14),(17,10)]],
    'open': [[(2,6),(9,6),(11,8),(20,8),(20,11)],[(2,6),(2,19),(18,19),(22,11),(6,11),(2,19)]],
    'save': [[(3,3),(17,3),(21,7),(21,21),(3,21),(3,3)],[(7,3),(7,10),(16,10),(16,3)],[(7,21),(7,14),(17,14),(17,21)]],
    'settings': [[(2,6),(9,6)],[(13,6),(22,6)],[(10,3),(10,9)],[(2,17),(15,17)],[(19,17),(22,17)],[(16,14),(16,20)]],
}


def glyph(name):
    icon = QIcon()
    for mode, ink in ((QIcon.Normal, '#d1c4e6'), (QIcon.Disabled, '#74757e'),
                      (QIcon.Active, '#f1e7ff'), (QIcon.Selected, '#f1e7ff')):
        pix = QPixmap(48, 48)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(2, 2)
        painter.setPen(QPen(QColor(ink), 1.55, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        for points in GLYPHS[name]:
            path = QPainterPath(QPointF(*points[0]))
            for point in points[1:]:
                path.lineTo(QPointF(*point))
            painter.drawPath(path)
        painter.end()
        pix.setDevicePixelRatio(2)
        icon.addPixmap(pix, mode)
    return icon


# Five-pixel bitmap letters remain sharp at Retina and standard display scales.
LETTERS = {
    'P':(30,17,17,30,16,16,16), 'h':(16,16,30,17,17,17,17),
    'a':(0,0,14,1,15,17,15), 'n':(0,0,30,17,17,17,17),
    't':(8,8,30,8,8,9,6), 'o':(0,0,14,17,17,17,14),
    'm':(0,0,26,21,21,21,21), 'C':(14,17,16,16,16,17,14),
    'A':(14,17,17,31,17,17,17), ' ':(0,)*7,
}


class PixelBrand(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAccessibleName(f'Phantom PCA {APP_VERSION} - Beta')
        self.setFixedSize(148, 43)
        self.tick = 0
        self.timer = QTimer(self, interval=115)
        self.timer.timeout.connect(self.advance)
        self.version = QLabel(f'{APP_VERSION} - Beta', self)
        self.version.setObjectName('brand_version')
        self.version.setGeometry(9, 23, 134, 18)

    def advance(self):
        self.tick = (self.tick + 1) % 90
        if self.tick <= 24:
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        for col, char in enumerate('Phantom PCA'):
            phase = self.tick - col
            offset = round(2 * math.sin(phase * math.pi / 5)) if 0 <= phase <= 10 else 0
            color = QColor('#dcb7fa' if col < 8 else '#97e7bb')
            for row, bits in enumerate(LETTERS[char]):
                for bit in range(5):
                    if bits & (16 >> bit):
                        painter.fillRect(9 + 12*col + 2*bit, 4 + 2*row + offset, 2, 2, color)
        painter.end()


NAVIGATION = (
    ('Workspace', (
        ('camera_control_tool','Camera Control','camera','Connect a Phantom and monitor live footage'),
        ('viewer_tool','Viewer','viewer','Play, trim, grade and export Cines'))),
    ('Setup', (
        ('scale_tool','Calibrate','scale','Set the physical scale from two image points before measuring'),)),
    ('Analysis', (
        ('event_tool','Event Finder','event','Find short events in recorded footage or camera memory'),
        ('track_tool','Track','track','Track objects and plot motion'),
        ('overlay_tool','Video Overlay','overlay','Synchronize and overlay two recordings'),
        ('bos_tool','Schlieren','schlieren','Background-oriented schlieren (BOS) analysis'))),
    ('Measurement', (
        ('two_pt_tool','Distance','distance','Measure the distance between two points'),
        ('three_pt_tool','Angle','angle','Measure an angle from three points'),
        ('two_line_tool','Lines','lines','Measure the angle between two lines'),
        ('area_tool','Area','area','Measure the area inside a polygon'))),
)


class CineSourceLabel(QLabel):
    def __init__(self):
        super().__init__('No Cine Selected')
        self.prefix = ''
        self.path = ''
        self.setTextFormat(Qt.PlainText)

    def set_source(self, path, prefix=''):
        self.path = str(path)
        self.prefix = prefix
        self.setToolTip(self.path)
        self._update_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_text()

    def _update_text(self):
        if not self.path:
            self.setText('No Cine Selected')
            return
        width = self.contentsRect().width()
        full = self.prefix + self.path
        metrics = self.fontMetrics()
        if metrics.horizontalAdvance(full) > width:
            full = metrics.elidedText(self.prefix + Path(self.path).name, Qt.ElideMiddle, width)
        self.setText(full)


class StartWorkspace(QFrame):
    """First-session source actions inside the otherwise empty Cine canvas."""
    def __init__(self, parent, choose, camera):
        super().__init__(parent)
        self.setObjectName('source_welcome')
        self.setAttribute(Qt.WA_StyledBackground)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(12)
        self.heading = QLabel('Open a source', self)
        self.heading.setObjectName('source_heading')
        self.heading.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.heading)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.choose = QPushButton(glyph('open'), 'Choose Cines', self)
        self.choose.setObjectName('source_choose')
        self.choose.setToolTip('Open up to four Cine recordings')
        self.camera = QPushButton(glyph('camera'), 'Camera Control', self)
        self.camera.setToolTip('Find a connected Phantom and open its live monitor')
        for button, callback in ((self.choose, choose), (self.camera, camera)):
            button.setMinimumHeight(34)
            actions.addWidget(button)
            button.clicked.connect(callback)
        layout.addLayout(actions)
        self.pulses = 0
        self.pulse = QTimer(self, interval=540)
        self.pulse.timeout.connect(self.advance_hint)
        parent.installEventFilter(self)
        self.setGeometry(parent.rect())

    def advance_hint(self):
        self.pulses += 1
        self.choose.setProperty('hint', self.pulses % 2 == 1)
        self.choose.style().unpolish(self.choose)
        self.choose.style().polish(self.choose)
        if self.pulses >= 6:
            self.pulse.stop()

    def showEvent(self, event):
        super().showEvent(event)
        if self.pulses < 6:
            self.pulse.start()

    def hideEvent(self, event):
        self.pulse.stop()
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
        return False

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setPen(QColor('#222531'))
        for y in range(14, self.height(), 24):
            for x in range(14, self.width(), 24):
                p.drawPoint(x, y)
        # The open viewfinder corners frame the source choices.
        cx, cy = self.width()//2, self.height()//2
        dx, dy = min(232, cx-8), min(94, cy-8)
        p.setPen(QPen(QColor('#544564'), 1))
        for a in (-1,1):
            for b in (-1,1):
                x, y = cx+a*dx, cy+b*dy
                p.drawLine(x,y,x-18*a,y)
                p.drawLine(x,y,x,y-18*b)
        p.end()


def navigation_button(parent, label, icon, hint, callback, *, checkable=True):
    button = QToolButton(parent)
    button.setObjectName('rail_tool')
    action = QAction(glyph(icon), label, button)
    action.setCheckable(checkable)
    action.setToolTip(hint)
    action.triggered.connect(callback)
    button.setDefaultAction(action)
    button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    button.setIconSize(QSize(18,18))
    button.setAccessibleName(label)
    button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return button, action
