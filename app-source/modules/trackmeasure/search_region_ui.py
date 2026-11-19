"""Editable search geometry; every commit snapshots values before scene redraw."""
import math
import weakref
from PySide6.QtCore import Qt,QRectF,QPointF,QTimer
from PySide6.QtGui import QColor,QPen,QBrush,QPainterPath,QPainterPathStroker
from PySide6.QtWidgets import QGraphicsRectItem,QGraphicsEllipseItem


class SearchRotateHandle(QGraphicsEllipseItem):
    def __init__(self,owner):
        super().__init__(-7,-7,14,14,owner);self.owner=weakref.proxy(owner)
        self.setBrush(QColor('#e6bf6c'));self.setPen(QPen(QColor('#ffffff'),1))
        self.setCursor(Qt.CrossCursor);self.setToolTip('Drag to rotate the search area')
        self.setFlag(self.GraphicsItemFlag.ItemIgnoresTransformations,True)
    def mousePressEvent(self,event):
        self.owner.window._stop_playback()
        delta=event.scenePos()-self.owner.pos()
        self.start=math.degrees(math.atan2(delta.y(),delta.x()));self.angle=self.owner.rotation();event.accept()
    def mouseMoveEvent(self,event):
        delta=event.scenePos()-self.owner.pos()
        self.owner.setRotation(self.angle+math.degrees(math.atan2(delta.y(),delta.x()))-self.start);event.accept()
    def mouseReleaseEvent(self,event):
        self.mouseMoveEvent(event);self.owner.queue_commit();event.accept()


class SearchRegionItem(QGraphicsRectItem):
    def __init__(self,center,size,object_id,window,editable=True,angle=0):
        super().__init__(QRectF(-size[0]/2,-size[1]/2,size[0],size[1]))
        self.window=weakref.proxy(window);self.object_id=object_id;self.editable=editable
        self.setPos(QPointF(*center));self.setRotation(float(angle));self.setZValue(3)
        self.setData(0,f'search_area_{object_id}')
        pen=QPen(QColor('#e6bf6c'),1.5,Qt.DashLine);pen.setCosmetic(True)
        self.setPen(pen);self.setBrush(Qt.NoBrush);self.setAcceptHoverEvents(editable)
        self.setAcceptedMouseButtons(Qt.LeftButton if editable else Qt.NoButton)
        self.setToolTip('Search: drag an edge to move, a corner to resize, or the round handle to rotate. The pink region stays fixed.')
        self.dragging=False
        self.rotate_handle=SearchRotateHandle(self) if editable else None
        self._place_handle()

    def _margin(self):
        views=self.scene().views() if self.scene() else []
        scale=math.hypot(views[0].transform().m11(),views[0].transform().m12()) if views else 1
        return 7/max(.05,scale)
    def _place_handle(self):
        if self.rotate_handle:self.rotate_handle.setPos(0,self.rect().top()-18)
    def boundingRect(self):return self.rect().adjusted(-160,-160,160,160)
    def shape(self):
        path=QPainterPath();path.addRect(self.rect());stroke=QPainterPathStroker();stroke.setWidth(self._margin()*2)
        return stroke.createStroke(path)
    def _corner(self,pos):
        rect=self.rect()
        for index,corner in enumerate((rect.topLeft(),rect.topRight(),rect.bottomLeft(),rect.bottomRight())):
            if (pos-corner).manhattanLength()<=self._margin()*2:return index
        return None
    def hoverMoveEvent(self,event):
        corner=self._corner(event.pos())
        self.setCursor(Qt.SizeAllCursor if corner is None else Qt.SizeFDiagCursor if corner in (0,3) else Qt.SizeBDiagCursor)
        super().hoverMoveEvent(event)
    def paint(self,painter,option,widget=None):
        super().paint(painter,option,widget)
        if not self.editable:return
        width=self._margin()*.65;painter.setBrush(QBrush(QColor('#e6bf6c')));rect=self.rect()
        for corner in (rect.topLeft(),rect.topRight(),rect.bottomLeft(),rect.bottomRight()):
            painter.drawRect(QRectF(corner.x()-width/2,corner.y()-width/2,width,width))
        painter.drawLine(QPointF(0,rect.top()),QPointF(0,rect.top()-18))
    def mousePressEvent(self,event):
        self.window._stop_playback();self.dragging=True;self.start=event.scenePos();self.start_center=QPointF(self.pos())
        self.corner=self._corner(event.pos());event.accept()
    def mouseMoveEvent(self,event):
        if not self.dragging:return
        if self.corner is None:self.setPos(self.start_center+event.scenePos()-self.start)
        else:
            delta=self.mapFromScene(event.scenePos());w=max(11.,abs(delta.x())*2);h=max(11.,abs(delta.y())*2)
            self.setRect(QRectF(-w/2,-h/2,w,h));self._place_handle()
        event.accept()
    def queue_commit(self):
        center=(self.pos().x(),self.pos().y());size=(self.rect().width(),self.rect().height());angle=float(self.rotation())
        object_id=self.object_id;window=self.window
        QTimer.singleShot(0,lambda:window._commit_search_region(object_id,center,size,angle))
    def mouseReleaseEvent(self,event):
        self.mouseMoveEvent(event);self.dragging=False;self.queue_commit();event.accept()
