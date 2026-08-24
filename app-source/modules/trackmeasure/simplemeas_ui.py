import os, logging, re
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas, NavigationToolbar2QT
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from pyphantom_qt.buttons import QIconTextButton, QColorBox, QToggleSwitch

import simplemeas_vm
from autotrackalgorithms import AutoTrackAlgorithms

import cv2
import math
import numpy as np
import threading
import tempfile
import pandas as pd

# constants
MINIGRAPH_SIZE = 160
VERSION = '1.2.0 + Multi-Cine Workspace'
HYBRID_TRACKING_METHOD = 'Hybrid (Edge + Intensity)'
CLASSIC_TRACKING_METHOD = 'Classic (Intensity Only)'
dir_path = os.path.dirname(os.path.abspath(__file__))

#region CUSTOM WIDGETS

class QDataTableWidget (QTableWidget):
    def __init__(self, rows, cols, header, parent=None, vertical_scrollbar=True, horizontal_scrollbar=False):
        super().__init__(parent)
        if cols != None: self.setColumnCount(cols)
        if rows != None: self.setRowCount(rows)
        if header != None:
            self.setHorizontalHeaderLabels(header)
        else:
            self.horizontalHeader().hide()
        
        v_sb = Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        if vertical_scrollbar:
            v_sb = Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        h_sb = Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        if horizontal_scrollbar:
            h_sb = Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setHorizontalScrollBarPolicy(h_sb)
        self.setVerticalScrollBarPolicy(v_sb)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.verticalHeader().setStretchLastSection(False)

class WorkerThread(QObject):
        finished = Signal()
        def __init__(self, fn, *args, **kwargs):
            super().__init__()
            self.fn = fn
            self.args = args
            self.kwargs = kwargs

        def run(self):
            self.fn(*self.args, **self.kwargs)
            self.finished.emit()
            
class LoadingWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.SplashScreen | Qt.WindowType.WindowStaysOnTopHint)
        self.movie = QMovie(os.path.join(dir_path, "images", "Spinner.gif"))
        self.movie.setScaledSize(QSize(80, 80))
        self.label = QLabel()
        self.label.setMovie(self.movie)
        self.setVisible(False)
        self.setLayout(QVBoxLayout(self))
        self.layout().addWidget(self.label, alignment=Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(QSize(256, 256))
        
    def center(self):
        frameGm = self.frameGeometry()
        centerPoint = self.window().frameGeometry().center()
        frameGm.moveCenter(centerPoint)
        self.move(frameGm.topLeft())
        
    def show_widget(self):
        self.show()
        self.setVisible(True)
        self.center()
        self.movie.start()
    
    def hide_widget(self):
        self.movie.stop()
        self.hide()

class LoadingMessage(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.SplashScreen)
        self.setText('Loading, please wait...')
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(QSize(384, 192))
    
    def center(self):
        frameGm = self.frameGeometry()
        centerPoint = self.window().frameGeometry().center()
        frameGm.moveCenter(centerPoint)
        self.move(frameGm.topLeft())
        
    def show_widget(self):
        self.show()
        self.center()

class ScaleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_units = parent.vm.length_units

        QBtn = QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        self.buttonBox = QDialogButtonBox(QBtn)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

        self.layout = QGridLayout()
        message = QLabel("Enter the known clicked length and physical units to use")
        self.length = QLineEdit()
        self.length.setValidator(QDoubleValidator())
        self.units = QComboBox()
        unit_list = ['μm', 'mm', 'cm', 'm', 'in', 'ft']
        self.units.addItems(unit_list)
        if self._current_units not in unit_list:
            self.units.insertItem(0, self._current_units) 
            self.units.setCurrentIndex(0) 
        else:
            self.units.setCurrentIndex(unit_list.index(self._current_units))
        self.units.setEditable(True)
        self.units.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        self.setWindowTitle("Measure Scale")
        self.setWindowIcon(QIcon(os.path.join(dir_path, "images", "ruler-32.png")))
        self.layout.addWidget(message,0,0,1,-1)
        self.layout.addWidget(self.length,1,0)
        self.layout.addWidget(self.units,1,1)
        self.layout.addWidget(self.buttonBox,2,0,1,-1)
        self.setLayout(self.layout)

    @staticmethod
    def launch(parent):
        dlg = ScaleDialog(parent)
        dlg.setObjectName('scale_dialog')
        r = dlg.exec()
        if r:
            return dlg.getValues()
        return None
    
    def getValues(self):
        self._current_units = self.units.currentText()
        return (float(self.length.text()), self.units.currentText())

class ScaleEditDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Scale")
        self._current_units = parent.vm.length_units

        self.layout = QGridLayout()
        self.scale = QLineEdit()
        self.scale.setValidator(QDoubleValidator())
        self.units = QComboBox()
        unit_list = ['μm', 'mm', 'cm', 'm', 'in', 'ft']
        self.units.addItems(unit_list)
        if self._current_units not in unit_list:
            self.units.insertItem(0, self._current_units) 
        self.units.setCurrentIndex(0) 
        self.units.setEditable(True)
        self.units.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        pix_label = QLabel("/ px")
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.accept)

        self.layout.addWidget(self.scale, 0, 0)
        self.layout.addWidget(self.units, 0, 1)
        self.layout.addWidget(pix_label, 0, 2)
        self.layout.addWidget(self.ok_button, 1, 0, 1, -1)
        self.setLayout(self.layout)

        # Connect the units dropdown change to convert_units
        self.units.currentTextChanged.connect(self.convert_units)

    def _get_conversion_factor(self, from_unit, to_unit):
        factors = {
        # μm conversions
        ('μm', 'mm'): 1e-3,
        ('μm', 'cm'): 1e-4,
        ('μm', 'm'):  1e-6,
        ('μm', 'in'): 3.93700787e-5,
        ('μm', 'ft'): 3.2808399e-6,
        # mm conversions
        ('mm', 'μm'): 1e3,
        ('mm', 'cm'): 1e-1,
        ('mm', 'm'):  1e-3,
        ('mm', 'in'): 0.0393700787,
        ('mm', 'ft'): 0.0032808399,
        # cm conversions
        ('cm', 'μm'): 1e4,
        ('cm', 'mm'): 10,
        ('cm', 'm'):  1e-2,
        ('cm', 'in'): 0.393700787,
        ('cm', 'ft'): 0.032808399,
        # m conversions
        ('m', 'μm'): 1e6,
        ('m', 'mm'): 1e3,
        ('m', 'cm'): 100,
        ('m', 'in'): 39.3700787,
        ('m', 'ft'): 3.2808399,
        # in conversions
        ('in', 'μm'): 25400,
        ('in', 'mm'): 25.4,
        ('in', 'cm'): 2.54,
        ('in', 'm'):  0.0254,
        ('in', 'ft'): 1/12,
        # ft conversions
        ('ft', 'μm'): 304800,
        ('ft', 'mm'): 304.8,
        ('ft', 'cm'): 30.48,
        ('ft', 'm'):  0.3048,
        ('ft', 'in'): 12,
    }
        if from_unit == to_unit:
            return 1.0
        return factors.get((from_unit, to_unit), 1.0)
    
    @staticmethod
    def launch(parent):
        dlg = ScaleEditDialog(parent)
        dlg.setObjectName('scale_edit_dialog')
        dlg.setValues(parent.vm.image_scale, parent.vm.length_units)
        if dlg.exec():
            return dlg.getValues()
        return None
    
    def convert_units(self, new_units):
        factor = self._get_conversion_factor(from_unit=self._current_units, to_unit=new_units)
        cur_val = float(self.scale.text())
        new_val = cur_val * factor
        self.scale.setText('{:.7g}'.format(new_val))
        self._current_units = new_units

    def getValues(self):
        return (float(self.scale.text()), self.units.currentText())
        
    def setValues(self, scale, units):
        self.scale.setText('{:.7g}'.format(scale))
        if units != 'pix':
            self.units.setCurrentText(units)

class ScaleLabel(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.parent = parent

    def enterEvent(self, event):
        self.parent.vm.scale_hover_cb()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.parent.vm.scale_leave_cb()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.parent.contextMenuEventScaleLabel(event)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.parent.on_edit_scale()
        super().mouseDoubleClickEvent(event)


class TrackingMethodDialog(QDialog):
    """Small, explicit method choice shown before a new Auto object is created."""
    @staticmethod
    def choose(parent):
        dialog = QDialog(parent)
        dialog.setObjectName('tracking_method_dialog')
        dialog.setWindowTitle('Choose Tracking Method')
        dialog.setMinimumSize(900, 280)
        dialog.setModal(True)
        selected_method = {'value': None}

        title = QLabel('How should this object be tracked?')
        title.setObjectName('tracking_method_title')
        title.setWordWrap(True)
        description = QLabel(
            'Intensity (Classic) uses the original PCA point/template workflow.\n'
            'Hybrid (Edge + Intensity) first records the exact point to follow, then lets you '\
            'position and tune a separate geometry-aware reinforcement region.'
        )
        description.setObjectName('tracking_method_description')
        description.setWordWrap(True)

        classic_button = QPushButton('Intensity (Classic)')
        hybrid_button = QPushButton('Hybrid (Edge + Intensity)')
        cancel_button = QPushButton('Cancel')
        classic_button.setObjectName('classic_method_button')
        hybrid_button.setObjectName('hybrid_method_button')
        cancel_button.setObjectName('tracking_method_cancel_button')
        hybrid_button.setStyleSheet('min-width: 300px; max-width: 300px;')
        classic_button.setStyleSheet('min-width: 250px; max-width: 250px;')
        cancel_button.setStyleSheet('min-width: 140px; max-width: 140px;')

        def accept_method(method):
            selected_method['value'] = method
            dialog.accept()

        classic_button.clicked.connect(lambda: accept_method(CLASSIC_TRACKING_METHOD))
        hybrid_button.clicked.connect(lambda: accept_method(HYBRID_TRACKING_METHOD))
        cancel_button.clicked.connect(dialog.reject)

        icon = QLabel()
        icon.setPixmap(dialog.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxQuestion
        ).pixmap(64, 64))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        text_layout = QVBoxLayout()
        text_layout.addWidget(title)
        text_layout.addWidget(description)
        text_layout.addStretch(1)
        content_layout = QHBoxLayout()
        content_layout.addWidget(icon)
        content_layout.addLayout(text_layout, 1)
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(hybrid_button)
        button_layout.addWidget(classic_button)
        button_layout.addWidget(cancel_button)
        button_layout.addStretch(1)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        layout.addLayout(content_layout)
        layout.addLayout(button_layout)
        dialog.exec()
        return selected_method['value']


class HybridTransformHandle(QGraphicsEllipseItem):
    """A fixed visual handle used to scale or rotate a Hybrid template region."""
    def __init__(self, kind, owner):
        super().__init__(-7, -7, 14, 14, owner)
        self.kind = kind
        self.owner = owner
        self.setBrush(QBrush(QColor('#f47efa')))
        pen = QPen(QColor('#ffffff'), 1)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setZValue(4)
        self.setCursor(
            Qt.CursorShape.SizeFDiagCursor
            if kind == 'scale'
            else Qt.CursorShape.CrossCursor
        )
        self.setToolTip('Drag to scale the tracking region' if kind == 'scale'
                        else 'Drag to rotate the tracking region')
        self.setData(0, f'hybrid_{kind}_handle')

        symbol = QGraphicsSimpleTextItem('↘' if kind == 'scale' else '↻', self)
        symbol.setBrush(QBrush(QColor('#ffffff')))
        symbol.setPos(-6, -9)
        symbol.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def mousePressEvent(self, event):
        self.owner.begin_handle_drag(self.kind, event.scenePos())
        event.accept()

    def mouseMoveEvent(self, event):
        self.owner.update_handle_drag(self.kind, event.scenePos())
        event.accept()

    def mouseReleaseEvent(self, event):
        self.owner.update_handle_drag(self.kind, event.scenePos())
        self.owner.commit_changes()
        event.accept()


class HybridRegionItem(QGraphicsRectItem):
    """Editable Hybrid ROI with adjacent scale and rotation handles."""
    def __init__(self, rect, center, angle, object_id, parent_window, editable=True):
        width = max(5.0, float(rect.width()))
        height = max(5.0, float(rect.height()))
        super().__init__(QRectF(-width / 2.0, -height / 2.0, width, height))
        self.object_id = object_id
        self.parent_window = parent_window
        self._drag_start_angle = 0.0
        self._drag_start_rotation = float(angle)
        self.setPos(QPointF(center[0], center[1]))
        self.setTransformOriginPoint(QPointF(0, 0))
        self.setRotation(float(angle))
        pen = QPen(QColor('#f47efa'), 2)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setZValue(2)
        self.setData(0, f'hybrid_region_{object_id}')
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, editable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, editable)
        self.setCursor(Qt.CursorShape.SizeAllCursor if editable else Qt.CursorShape.ArrowCursor)
        self.scale_handle = HybridTransformHandle('scale', self) if editable else None
        self.rotate_handle = HybridTransformHandle('rotate', self) if editable else None
        self._position_handles()

    def _position_handles(self):
        rect = self.rect()
        if self.scale_handle is not None:
            self.scale_handle.setPos(rect.bottomRight() + QPointF(12, 12))
        if self.rotate_handle is not None:
            self.rotate_handle.setPos(rect.topRight() + QPointF(12, -12))

    def begin_handle_drag(self, kind, scene_pos):
        if kind == 'rotate':
            center = self.mapToScene(QPointF(0, 0))
            delta = scene_pos - center
            self._drag_start_angle = math.degrees(math.atan2(delta.y(), delta.x()))
            self._drag_start_rotation = self.rotation()

    def update_handle_drag(self, kind, scene_pos):
        if kind == 'scale':
            local_pos = self.mapFromScene(scene_pos)
            half_width = max(3.0, abs(local_pos.x()))
            half_height = max(3.0, abs(local_pos.y()))
            self.setRect(QRectF(-half_width, -half_height, half_width * 2, half_height * 2))
            self._position_handles()
        elif kind == 'rotate':
            center = self.mapToScene(QPointF(0, 0))
            delta = scene_pos - center
            current_angle = math.degrees(math.atan2(delta.y(), delta.x()))
            self.setRotation(self._drag_start_rotation + current_angle - self._drag_start_angle)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.commit_changes()

    def commit_changes(self):
        self.parent_window.commit_hybrid_region(self)

class ResizableGraph(QGraphicsView):
    def __init__(self, label, parent):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.parent = parent
        self.xMax = 100
        self.yMax = 100
        self.scene().setSceneRect(QRectF(0, 0, self.xMax, self.yMax))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.label = label
        self.setMouseTracking(True)
        self.mouse_pos = (0,0)
        self._hybrid_drag_start = None
        self._hybrid_drag_preview = None
        self._view_zoom = 1.0
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def resizeEvent(self, event):
        if self._view_zoom <= 1.0:
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        super().resizeEvent(event)

    def reset_view_zoom(self):
        self._view_zoom = 1.0
        self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom_at(self, view_pos, wheel_steps):
        if not wheel_steps:
            return
        old_zoom = self._view_zoom
        new_zoom = float(np.clip(old_zoom * (1.20 ** wheel_steps), 1.0, 20.0))
        if abs(new_zoom - old_zoom) < 1e-9:
            return
        scene_before = self.mapToScene(view_pos)
        if new_zoom <= 1.0:
            self.reset_view_zoom()
        else:
            target_viewport_pos = QPointF(view_pos)
            self.scale(new_zoom / old_zoom, new_zoom / old_zoom)
            self._view_zoom = new_zoom
            # Keep the scene location beneath the wheel cursor stationary. At
            # the image boundary Qt may clamp a small amount to avoid showing
            # empty space, but the zoom still moves toward that cursor.
            self.centerOn(scene_before)
            viewport_delta = target_viewport_pos - QPointF(
                self.viewport().width() / 2.0,
                self.viewport().height() / 2.0,
            )
            viewport_center = self.mapFromScene(scene_before) - viewport_delta.toPoint()
            self.centerOn(self.mapToScene(viewport_center))
            scene_after = self.mapToScene(view_pos)
            residual = scene_before - scene_after
            transform = self.transform()
            self.horizontalScrollBar().setValue(
                round(self.horizontalScrollBar().value() + residual.x() * transform.m11())
            )
            self.verticalScrollBar().setValue(
                round(self.verticalScrollBar().value() + residual.y() * transform.m22())
            )

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        self.zoom_at(event.position().toPoint(), steps)
        event.accept()
    
    def mouseMoveEvent(self, event):
        if self._hybrid_drag_start is not None:
            scene_pos = self.mapToScene(event.pos())
            scene_pos.setX(min(max(scene_pos.x(), 0), self.xMax))
            scene_pos.setY(min(max(scene_pos.y(), 0), self.yMax))
            rect = QRectF(self._hybrid_drag_start, scene_pos).normalized()
            if self._hybrid_drag_preview is not None:
                self._hybrid_drag_preview.setRect(rect)
            event.accept()
            return
        scene_pos = self.mapToScene(event.pos())
        self.mouse_pos = (int(scene_pos.x()), int(scene_pos.y()))
        if self._within_bounds(scene_pos):
            self.parent.vm.update_mouse_pos_cb(self.mouse_pos)
            self.parent.vm.graph_mouse_motion_cb(self.mouse_pos)
        else:
            self.mouse_pos = None
            self.label.setText('Coordinates:\nN/A')
        super().mouseMoveEvent(event)

    def enterEvent(self, event):
        if self.parent.current_tool is not None and self.parent.status_bar.text() == '':
            self.parent.vm.update_status_text.emit('Use Ctrl + Arrow keys for fine mouse control and Ctrl + Enter to add a point')
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.mouse_pos = None
        self.parent.vm.update_mouse_pos_cb(None, clear=True)
        if self.parent.status_bar.text() == 'Use Ctrl + Arrow keys for fine mouse control and Ctrl + Enter to add a point':
            self.parent.vm.update_status_text.emit('')
  
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            event_pos = event.position().toPoint()
            item = self.itemAt(event_pos)
            owner = item
            while owner is not None and not isinstance(owner, HybridRegionItem):
                owner = owner.parentItem()
            if owner is not None:
                QGraphicsView.mousePressEvent(self, event)
                return

            if self.parent.is_hybrid_region_selection_active(self):
                scene_pos = self.mapToScene(event_pos)
                if self._within_bounds(scene_pos):
                    self.parent.create_hybrid_track_at(scene_pos)
                    event.accept()
                    return

            if self.parent.should_prompt_for_tracking_method():
                method = self.parent.choose_tracking_method()
                if method == CLASSIC_TRACKING_METHOD:
                    self.parent.create_classic_track_at(self.mapToScene(event_pos))
                elif method == HYBRID_TRACKING_METHOD:
                    self.parent.begin_hybrid_region_selection(self)
                event.accept()
                return
            self.graph_click(event.pos())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._hybrid_drag_start is not None:
            scene_pos = self.mapToScene(event.pos())
            scene_pos.setX(min(max(scene_pos.x(), 0), self.xMax))
            scene_pos.setY(min(max(scene_pos.y(), 0), self.yMax))
            rect = QRectF(self._hybrid_drag_start, scene_pos).normalized()
            if self._hybrid_drag_preview is not None:
                self.scene().removeItem(self._hybrid_drag_preview)
            self._hybrid_drag_start = None
            self._hybrid_drag_preview = None
            self.parent.finish_hybrid_region_selection(self, rect)
            event.accept()
            return
        QGraphicsView.mouseReleaseEvent(self, event)

    def graph_click(self, pos):
        scene_pos = self.mapToScene(pos)
        self.mouse_pos = (scene_pos.x(), scene_pos.y())
        if self.parent.auto.subpixel_size.currentText() == '1.0 pix' :
            self.mouse_pos = (round(scene_pos.x()), round(scene_pos.y()))
        if self._within_bounds(scene_pos):
            self.parent.vm.graph_click_cb(self.mouse_pos)
            self.parent.add_object_button.setChecked(False) # reset this in case it was on
        else:
            self.mouse_pos = None
    
    def _within_bounds(self, pos):
        return ((pos.x() >= 0 and pos.x() <= self.xMax) and (pos.y() >= 0 and pos.y() <= self.yMax))


class WorkspaceGraph(ResizableGraph):
    """A Cine viewport that activates its workspace before accepting tool clicks."""
    def __init__(self, label, parent, pane_index):
        super().__init__(label, parent)
        self.pane_index = pane_index

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.parent.active_pane_index != self.pane_index:
            self.parent.activate_cine_pane(self.pane_index)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.parent.active_pane_index == self.pane_index:
            super().mouseMoveEvent(event)
        else:
            QGraphicsView.mouseMoveEvent(self, event)

    def enterEvent(self, event):
        if self.parent.active_pane_index == self.pane_index:
            super().enterEvent(event)
        else:
            QGraphicsView.enterEvent(self, event)

    def leaveEvent(self, event):
        if self.parent.active_pane_index == self.pane_index:
            super().leaveEvent(event)
        else:
            QGraphicsView.leaveEvent(self, event)


class WorkspacePane(QFrame):
    def __init__(self, parent, pane_index):
        super().__init__(parent)
        self.pane_index = pane_index
        self.setObjectName('cine_workspace_pane')
        self.setProperty('activePane', pane_index == 0)
        self.title = QLabel(f'C{pane_index + 1}')
        self.title.setObjectName('cine_workspace_title')
        self.graph = WorkspaceGraph(parent.mouse_pos_label, parent, pane_index)
        self.graph.setObjectName(f'cine_workspace_graph_{pane_index}')
        self.graph.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)
        layout.addWidget(self.title)
        layout.addWidget(self.graph, 1)

    def set_active(self, active):
        self.setProperty('activePane', bool(active))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


def create_color_wheel_icon(size=28):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(2, 2, size - 4, size - 4)
    colors = ('#ff4d4d', '#ffb84d', '#fff04d', '#4dd97a', '#4da6ff', '#b84dff')
    for index, color in enumerate(colors):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPie(rect, index * 60 * 16, 60 * 16)
    painter.setBrush(QColor('#222222'))
    inner = QRectF(size * 0.36, size * 0.36, size * 0.28, size * 0.28)
    painter.drawEllipse(inner)
    painter.end()
    return QIcon(pixmap)


def create_playback_icon(kind, size=24):
    """Draw crisp PCC-style transport symbols without relying on OS icons."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#ffffff'))

    def triangle(center_x, direction, width=7, height=12):
        half_h = height / 2
        if direction == 'left':
            points = [
                QPointF(center_x - width / 2, size / 2),
                QPointF(center_x + width / 2, size / 2 - half_h),
                QPointF(center_x + width / 2, size / 2 + half_h),
            ]
        else:
            points = [
                QPointF(center_x + width / 2, size / 2),
                QPointF(center_x - width / 2, size / 2 - half_h),
                QPointF(center_x - width / 2, size / 2 + half_h),
            ]
        painter.drawPolygon(QPolygonF(points))

    if kind == 'reverse':
        triangle(size / 2, 'left', 10, 14)
    elif kind == 'pause':
        painter.drawRoundedRect(QRectF(6, 5, 4, 14), 1, 1)
        painter.drawRoundedRect(QRectF(14, 5, 4, 14), 1, 1)
    elif kind == 'forward':
        triangle(size / 2, 'right', 10, 14)
    elif kind == 'fast_reverse':
        triangle(9, 'left', 7, 12)
        triangle(15, 'left', 7, 12)
    elif kind == 'previous_frame':
        painter.drawRoundedRect(QRectF(5, 5, 3, 14), 1, 1)
        triangle(14, 'left', 9, 12)
    elif kind == 'next_frame':
        triangle(10, 'right', 9, 12)
        painter.drawRoundedRect(QRectF(16, 5, 3, 14), 1, 1)
    elif kind == 'fast_forward':
        triangle(9, 'right', 7, 12)
        triangle(15, 'right', 7, 12)

    painter.end()
    return QIcon(pixmap)

class AutoTrackDialog(QDialog):
    processRange = Signal(str)
    remove_roi = Signal(str)
    draw_roi = Signal(object, object, str)
    updateAutoParamValue = Signal(str, object)
    autotrackToStatusText = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_metadata = None
        self.setWindowTitle("AutoTrack Configuration")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        # Frames
        self.frames_enable = QCheckBox('Process Frames')
        self.start_frame_label = QLabel('Start')
        self.start_frame = QSpinBox()
        self.end_frame_label = QLabel('Stop')
        self.end_frame = QSpinBox()
        self.frames_enable.setChecked(True)

        # Search Area dimensions
        self.sa_x = OddSpinBox()
        self.sa_x.setKeyboardTracking(False)
        self.sa_y = OddSpinBox()
        self.sa_enable = QCheckBox('Enable Search Area')
        self.sa_x_label = QLabel('X:')
        self.sa_y_label = QLabel('Y:')

        # Template dimensions
        self.tpl_x = OddSpinBox()
        self.tpl_y = OddSpinBox()
        self.tpl_enable = QCheckBox('Show Template')
        self.tpl_x_label = QLabel('X:')
        self.tpl_y_label = QLabel('Y:')

        # SubPixel Resolution
        self.subpixel_label = QLabel('Interpolation')
        self.subpixel_size = QComboBox()
        self.subpixel_size.addItem('1.0 pix')
        self.subpixel_size.addItem('1/2 pix')
        self.subpixel_size.addItem('1/3 pix')
        self.subpixel_size.addItem('1/4 pix')
        self.subpixel_size.addItem('1/5 pix')
        self.subpixel_size.addItem('1/6 pix')
        self.subpixel_size.addItem('1/7 pix')
        self.subpixel_size.addItem('1/8 pix')
        self.subpixel_size.addItem('1/9 pix')
        self.subpixel_size.addItem('1/10 pix')
        self.subpixel_size.setEnabled(True)

        # Subpixel Type
        self.subpixel_type_label = QLabel('Type')
        self.subpixel_interp = QComboBox()
        self.subpixel_interp.addItem('Linear')
        self.subpixel_interp.addItem('Quadratic')
        self.subpixel_interp.addItem('Cubic') 
        self.subpixel_interp.addItem('Quartic') 
        self.subpixel_interp.addItem('Quintic') 
        default_index = self.subpixel_interp.findText('Cubic')
        self.subpixel_interp.setCurrentIndex(default_index)
        self.subpixel_interp.setEnabled(False)

        # Buttons
        buttons_layout = QHBoxLayout()
        self.process_button = QPushButton()
        self.process_button.setText('Process')
        self.process_button.setEnabled(True)
        self.process_all_button = QPushButton('Process All')
        buttons_layout.addWidget(self.process_button)
        buttons_layout.addWidget(self.process_all_button)

        self.applyButton = QToolButton(text='Apply to All Selected Objects')
        self.applyButton.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.applyButton.setObjectName('qt_apply_settings_button')

        # Update Template
        update_template_layout = QHBoxLayout()
        self.update_template_enable = QCheckBox('Update Template')
        update_template_layout.addWidget(self.update_template_enable)

        # Acceptable Score
        self.score_label = QLabel('Acceptable Score')
        self.score = QDoubleSpinBox()
        self.score.setRange(0, 1)
        self.score.setSingleStep(0.010)

        # Template Update Cutoff Score
        self.tpl_score_label = QLabel('Template Update Score')
        self.tpl_score = QDoubleSpinBox()
        self.tpl_score.setRange(0, 1)
        self.tpl_score.setSingleStep(0.010)

        # Geometry-aware tracking
        self.tracking_method_label = QLabel('Tracking Method')
        self.tracking_method = QComboBox()
        self.tracking_method.addItems([HYBRID_TRACKING_METHOD, CLASSIC_TRACKING_METHOD])
        self.rotation_range_label = QLabel('Rotation Range')
        self.rotation_range = QDoubleSpinBox()
        self.rotation_range.setRange(0.0, 180.0)
        self.rotation_range.setSingleStep(1.0)
        self.rotation_range.setSuffix('°')
        self.rotation_range.setValue(15.0)
        self.rotation_step_label = QLabel('Rotation Step')
        self.rotation_step = QDoubleSpinBox()
        self.rotation_step.setRange(0.25, 15.0)
        self.rotation_step.setSingleStep(0.25)
        self.rotation_step.setSuffix('°')
        self.rotation_step.setValue(2.0)
        self.edge_weight_label = QLabel('Edge Weight')
        self.edge_weight = QDoubleSpinBox()
        self.edge_weight.setRange(0.0, 1.0)
        self.edge_weight.setSingleStep(0.05)
        self.edge_weight.setValue(0.6)

        # Display Object Name
        self.obj_name_basic = QLabel()
        self.obj_name_adv = QLabel()

        # Grid Layout For Basic Tab
        basic_grid_layout = QGridLayout()
        basic_grid_layout.addWidget(self.frames_enable, 0, 0)
        basic_grid_layout.addWidget(self.start_frame_label, 0, 1, alignment=Qt.AlignmentFlag.AlignRight)
        basic_grid_layout.addWidget(self.start_frame, 0, 2)
        basic_grid_layout.addWidget(self.end_frame_label, 0, 3, alignment=Qt.AlignmentFlag.AlignRight)
        basic_grid_layout.addWidget(self.end_frame, 0, 4)
        basic_grid_layout.addWidget(self.sa_enable, 1, 0)
        basic_grid_layout.addWidget(self.sa_x_label, 1, 1, alignment=Qt.AlignmentFlag.AlignRight)
        basic_grid_layout.addWidget(self.sa_x, 1, 2)
        basic_grid_layout.addWidget(self.sa_y_label, 1, 3, alignment=Qt.AlignmentFlag.AlignRight)
        basic_grid_layout.addWidget(self.sa_y, 1, 4)
        basic_grid_layout.addWidget(self.tpl_enable, 2, 0)
        basic_grid_layout.addWidget(self.tpl_x_label, 2, 1, alignment=Qt.AlignmentFlag.AlignRight)
        basic_grid_layout.addWidget(self.tpl_x, 2, 2)
        basic_grid_layout.addWidget(self.tpl_y_label, 2, 3, alignment=Qt.AlignmentFlag.AlignRight)
        basic_grid_layout.addWidget(self.tpl_y, 2, 4)

        # Grid Layout For Advanced Tab
        advanced_grid_layout = QGridLayout()
        advanced_grid_layout.addWidget(self.tracking_method_label, 0, 0)
        advanced_grid_layout.addWidget(self.tracking_method, 0, 1)
        advanced_grid_layout.addWidget(self.rotation_range_label, 1, 0)
        advanced_grid_layout.addWidget(self.rotation_range, 1, 1)
        advanced_grid_layout.addWidget(self.rotation_step_label, 2, 0)
        advanced_grid_layout.addWidget(self.rotation_step, 2, 1)
        advanced_grid_layout.addWidget(self.edge_weight_label, 3, 0)
        advanced_grid_layout.addWidget(self.edge_weight, 3, 1)
        advanced_grid_layout.addWidget(self.score_label, 4, 0)
        advanced_grid_layout.addWidget(self.score, 4, 1)
        advanced_grid_layout.addWidget(self.tpl_score_label, 5, 0)
        advanced_grid_layout.addWidget(self.tpl_score, 5, 1)
        advanced_grid_layout.addWidget(self.subpixel_label, 6, 0)
        advanced_grid_layout.addWidget(self.subpixel_size, 6, 1)
        advanced_grid_layout.addWidget(self.subpixel_type_label, 7, 0)
        advanced_grid_layout.addWidget(self.subpixel_interp, 7, 1)

        # Load Bar
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        # Creating Tab Widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setMinimumWidth(350)
        self.tab_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        # Creating Basic Config Tab
        basic_tab = QWidget()
        basic_layout = QVBoxLayout()
        basic_layout.addLayout(basic_grid_layout)
        basic_layout.addLayout(update_template_layout)
        basic_tab.setLayout(basic_layout)

        # Create Advanced Config Tab
        advanced_tab = QWidget()
        advanced_layout = QVBoxLayout()
        advanced_layout.addLayout(advanced_grid_layout)
        advanced_tab.setLayout(advanced_layout)

        # Adding Tabs to Tab Widget
        self.tab_widget.addTab(basic_tab, "Basic")
        self.tab_widget.addTab(advanced_tab, "Advanced")

        # Final Layout
        vlayout = QVBoxLayout()
        vlayout.addWidget(self.tab_widget)
        vlayout.addWidget(self.applyButton)
        vlayout.addLayout(progress_layout)
        vlayout.addLayout(buttons_layout)
        vlayout.setStretch(0, 1)
        self.setLayout(vlayout)

        # Connect Statements
        self.frames_enable.toggled.connect(self.frames_enable_changed)
        self.sa_enable.toggled.connect(self.on_sa_enable_changed)
        self.tpl_enable.toggled.connect(self.on_tpl_enable_changed)
        self.start_frame.valueChanged.connect(self.on_start_frame_changed)
        self.end_frame.valueChanged.connect(self.on_end_frame_changed)
        self.sa_x.valueChanged.connect(self.on_sa_value_changed)
        self.sa_y.valueChanged.connect(self.on_sa_value_changed)
        self.tpl_x.valueChanged.connect(self.on_tpl_value_changed)
        self.tpl_y.valueChanged.connect(self.on_tpl_value_changed)
        self.subpixel_size.currentIndexChanged.connect(self.on_subpixel_size_changed)
        self.subpixel_interp.currentIndexChanged.connect(self.on_subpixel_interp_changed)
        self.process_button.clicked.connect(self.on_process_button_clicked)
        self.process_all_button.clicked.connect(self.on_process_all_clicked)
        self.update_template_enable.toggled.connect(self.on_update_tpl_enable_changed)
        self.score.valueChanged.connect(self.on_score_changed)
        self.tpl_score.valueChanged.connect(self.on_tpl_score_changed)
        self.tracking_method.currentTextChanged.connect(self.on_tracking_method_changed)
        self.rotation_range.valueChanged.connect(self.on_rotation_range_changed)
        self.rotation_step.valueChanged.connect(self.on_rotation_step_changed)
        self.edge_weight.valueChanged.connect(self.on_edge_weight_changed)
        self.on_tracking_method_changed(self.tracking_method.currentText())

    def update_progress(self, progress):
        self.progress_bar.setValue(progress)

    def format_progress(self, name):
        if name != '':
            self.progress_bar.setFormat(f"{name}: %p%")
        else:
            self.progress_bar.setFormat('%p%')

    def on_track_complete(self, state, track_all=False):
        if track_all:
            self.process_all_button.setText('Process All' if state else 'Cancel')
        else:
            self.process_button.setText('Process' if state else 'Cancel')
        self.format_progress('')
        self.update_progress(0)

    def set_frame_range(self, ff, lf):
        self.start_frame.setRange(ff, lf)
        self.end_frame.setRange(ff, lf)

    def set_start_frame(self, value):
        self.start_frame.setValue(value)
        self.updateAutoParamValue.emit('start', self.start_frame.value())

    def set_end_frame(self, value):
        self.end_frame.setValue(value)
        self.updateAutoParamValue.emit('end', self.end_frame.value())

    def refresh_params(self, start=0, end=0, search_area=(0,0), tpl_rng=(0,0), subpixel_size='1.0', subpixel_interp='Cubic',
                       frames_enable=False, search_area_enable=True, tpl_rng_enable=True, update_template_enable=True, 
                       acceptable_score = 0.8, tpl_score = 0.9, name='',
                       tracking_method=HYBRID_TRACKING_METHOD, rotation_range=15.0,
                       rotation_step=2.0, edge_weight=0.6):
        self.start_frame.setValue(start)
        self.end_frame.setValue(end)
        if tpl_rng != (0, 0):
            self.tpl_x.setValue(tpl_rng[0])
            self.tpl_y.setValue(tpl_rng[1])
        if search_area != (0, 0):
            self.sa_x.setValue(search_area[0])
            self.sa_y.setValue(search_area[1])
        self.subpixel_size.setCurrentText(subpixel_size)
        self.subpixel_interp.setCurrentText(subpixel_interp)
        self.score.setValue(acceptable_score)
        self.tpl_score.setValue(tpl_score)
        self.tracking_method.setCurrentText(tracking_method)
        self.rotation_range.setValue(rotation_range)
        self.rotation_step.setValue(rotation_step)
        self.edge_weight.setValue(edge_weight)
        self.frames_enable.setChecked(frames_enable)
        self.sa_enable.setChecked(search_area_enable)
        self.tpl_enable.setChecked(tpl_rng_enable)
        self.update_template_enable.setChecked(update_template_enable)
        self.set_roi_ranges((self.active_metadata.ImWidth, self.active_metadata.ImHeight)) # redundant?
        self.setWindowTitle(f"AutoTrack Configuration ({name})")

    def on_process_button_clicked(self):
        text = self.process_button.text()
        if text == 'Process':
            self.processRange.emit(text)
            self.autotrackToStatusText.emit('')
            self.process_button.setText('Cancel')
        elif text == 'Cancel':
            self.processRange.emit(text)
            self.process_button.setText('Process')
    
    def on_process_all_clicked(self):
        text = self.process_all_button.text()
        if text == 'Process All':
            self.processRange.emit(text)
            self.autotrackToStatusText.emit('')
            self.process_all_button.setText('Cancel')
        elif text == 'Cancel':
            self.processRange.emit(text)
            self.process_all_button.setText('Process All')

    def process_complete_cb(self):
        QApplication.restoreOverrideCursor()

    def frames_enable_changed(self, state):
        self.start_frame.setEnabled(state)
        self.start_frame_label.setEnabled(state)
        self.end_frame.setEnabled(state)
        self.end_frame_label.setEnabled(state)
        self.updateAutoParamValue.emit('frames_enable', state)

    def on_sa_enable_changed(self, state):
        self.sa_x.setEnabled(state)
        self.sa_y.setEnabled(state)
        self.sa_x_label.setEnabled(state)
        self.sa_y_label.setEnabled(state)
        self.updateAutoParamValue.emit('search_area_enable', state)

        if state:
            self.draw_roi.emit((int(self.sa_x.value()),int(self.sa_y.value())), None, 'search_area')
        else:
            try:
                self.remove_roi.emit('search_area')
            except:pass

    def on_tpl_enable_changed(self, state):
        self.updateAutoParamValue.emit('tpl_rng_enable', state)

        if state:
            self.draw_roi.emit((int(self.tpl_x.value()), int(self.tpl_y.value())), None, 'template')
        else:
            try:
                self.remove_roi.emit('template')
            except:pass   

    def on_update_tpl_enable_changed(self, state):
        self.updateAutoParamValue.emit('update_template_enable', state)

    def on_score_changed(self):
        self.updateAutoParamValue.emit('acceptable_score', self.score.value())
        self.set_score_range(self.score.value())

    def on_tpl_score_changed(self):
        self.updateAutoParamValue.emit('tpl_score', self.tpl_score.value())

    def on_tracking_method_changed(self, method):
        hybrid_enabled = method == HYBRID_TRACKING_METHOD
        self.rotation_range.setEnabled(hybrid_enabled)
        self.rotation_range_label.setEnabled(hybrid_enabled)
        self.rotation_step.setEnabled(hybrid_enabled)
        self.rotation_step_label.setEnabled(hybrid_enabled)
        self.edge_weight.setEnabled(hybrid_enabled)
        self.edge_weight_label.setEnabled(hybrid_enabled)
        self.subpixel_size.setEnabled(not hybrid_enabled)
        self.subpixel_label.setEnabled(not hybrid_enabled)
        self.subpixel_interp.setEnabled(
            not hybrid_enabled and self.subpixel_size.currentText() != '1.0 pix'
        )
        self.subpixel_type_label.setEnabled(not hybrid_enabled)
        self.updateAutoParamValue.emit('tracking_method', method)

    def on_rotation_range_changed(self):
        self.updateAutoParamValue.emit('rotation_range', self.rotation_range.value())

    def on_rotation_step_changed(self):
        self.updateAutoParamValue.emit('rotation_step', self.rotation_step.value())

    def on_edge_weight_changed(self):
        self.updateAutoParamValue.emit('edge_weight', self.edge_weight.value())

    def on_start_frame_changed(self):
        self.updateAutoParamValue.emit('start', self.start_frame.value())

    def on_end_frame_changed(self):
        self.updateAutoParamValue.emit('end', self.end_frame.value())

    def on_sa_value_changed(self):
        s = self.sender()
        self.updateAutoParamValue.emit('search_area', (int(self.sa_x.value()),int(self.sa_y.value())))
        if self.sa_enable.isChecked():
            self.remove_roi.emit('search_area')
            self.draw_roi.emit((int(self.sa_x.value()),int(self.sa_y.value())), None, 'search_area')
            self.set_roi_ranges((self.active_metadata.ImWidth, self.active_metadata.ImHeight))

    def on_tpl_value_changed(self):
        self.updateAutoParamValue.emit('tpl_rng', (int(self.tpl_x.value()), int(self.tpl_y.value())))
        if self.tpl_enable.isChecked():
            self.remove_roi.emit('template')
            self.draw_roi.emit((int(self.tpl_x.value()), int(self.tpl_y.value())), None, 'template')
            self.set_roi_ranges((self.active_metadata.ImWidth, self.active_metadata.ImHeight))

    def on_subpixel_size_changed(self):
        self.updateAutoParamValue.emit('subpixel_size', self.subpixel_size.currentText())
        if (
            self.tracking_method.currentText() != HYBRID_TRACKING_METHOD
            and self.subpixel_size.currentText() != '1.0 pix'
        ):
            self.subpixel_interp.setEnabled(True)
        else:
            self.subpixel_interp.setEnabled(False)

    def on_subpixel_interp_changed(self):
        self.updateAutoParamValue.emit('subpixel_type', self.subpixel_interp.currentText().lower())

    def on_new_cine_load(self, cine_path, metadata):
        self.active_metadata = metadata
        self.set_roi_ranges((self.active_metadata.ImWidth, self.active_metadata.ImHeight))
    
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            event.ignore()
        else:
            super().keyPressEvent(event)

    def set_roi_ranges(self, frame_dim):
        # make sure sa, tpl is no bigger than frame w,h
        # make sure tpl is less than sa
        # make sure template update score is no less than acceptable score
        self.sa_x.setRange(1, frame_dim[0])
        self.sa_y.setRange(1, frame_dim[1])
        self.tpl_x.setRange(1, self.sa_x.value())
        self.tpl_y.setRange(1, self.sa_y.value())

    def set_score_range(self, acc_score):
        self.tpl_score.setRange(acc_score, 1)

class OddSpinBox(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setKeyboardTracking(False)

    def validate(self, text, pos):
        try:
            value = int(text)
        except ValueError:
            return super().validate(text, pos)
        
        return (QValidator.State.Intermediate, text, pos)

    def fixup(self, text):
        try:
            value = int(text)
            if value % 2 == 0:
                value += 1 
            self.setValue(value)
        except ValueError:
            pass

    def stepBy(self, steps):
        current_value = self.value()
        new_value = current_value + 2 * steps
        self.setValue(new_value)

class TrackingGraph(FigureCanvas):
    def __init__(self, parent):
        self.parent_window = parent
        self.vm = parent.vm
        self.fig = Figure(facecolor='#333333')
        super().__init__(self.fig)
        self.setParent(parent)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_visible(False)
        self.frame_pos_line = None
        self.show_annotations = True
    
    def redraw(self, temps):
        self.fig.clear()
        val = self.parent_window.fig_dropdown.currentText()
        self.mode = val.split('-')[-1]
        self.ax = self.fig.add_subplot(111)
        n_diff = self.parent_window.vm.n_diff
        workspace_owner = getattr(self.parent_window, 'parent_window', self.parent_window)
        multi_cine_overlay = len(getattr(workspace_owner.vm, 'workspace_contexts', [])) > 1
        if self.mode != 'Vibration' and hasattr(workspace_owner, 'combined_track_data'):
            temps = workspace_owner.combined_track_data()
        plotted_count = 0

        if self.mode == 'Vibration':
            if len(temps) > 0:
                self.plot_vibration(val)
                self.ax.set_title(f"{val[0]}-Frequency Spectrum ({temps[self.parent_window.vm.active_object]['name']})", color='white')
            else:
                self.ax.set_title(f"{val[0]}-Frequency Spectrum", color='white')
        else:
            x_units, y_units = self.get_units(self.mode)
            time_axis = 'Time from Trigger' if multi_cine_overlay else 'Time'
            self.ax.set_title(f"{val} ({y_units}) vs {time_axis} ({x_units})", color='white')
            if len(temps) > 0:
                for id, t in temps.items():
                    if t['enabled']:
                        if self.mode in ('Speed', 'Angular Speed'):
                            if len(t['points']) <= n_diff:
                                continue
                            length = len(t['frame_ts'])
                            offset = math.floor(n_diff/2)
                            frs = t['frame_ts'][n_diff - offset:length - offset]
                        elif self.mode == 'Acceleration':
                            if len(t['points']) <= self.parent_window.vm.n_diff + 1:
                                continue
                            length = len(t['frame_ts'])
                            offset = 2 * math.floor(n_diff/2)
                            frs = t['frame_ts'][(2 * n_diff) - offset:length - offset]
                        else:
                            frs = t['frame_ts']
                        if val in ('Angle', 'Angular Speed'):
                            y_data = np.asarray(t.get(val, []), dtype=float)
                        else:
                            cine_cal = t.get('_cine_cal', self.parent_window.vm.cal)
                            y_data = cine_cal.point_transform(t[val])
                        line_styles = ('-', '--', '-.', ':')
                        cine_index = int(t.get('_cine_index', 0))
                        self.ax.plot(
                            frs, y_data, marker='D', linestyle=line_styles[cine_index % len(line_styles)],
                            ms=4, label=t['name'], color=t['color']
                        )
                        plotted_count += 1

                        if self.show_annotations:
                            for i, (x, y) in enumerate(zip(frs, y_data)):
                                if self.mode in ('Speed', 'Angular Speed'):
                                    loc_idx = i - int(n_diff // 2)
                                elif self.mode == 'Acceleration':
                                    loc_idx = i - n_diff
                                else:
                                    loc_idx = i
                                if i in t['notes'] and 0 <= loc_idx < len(frs):
                                    base_color = QColor(t['color'])
                                    lighter_color = QColor(
                                        int(base_color.red()   + (255 - base_color.red())   * 0.5),
                                        int(base_color.green() + (255 - base_color.green()) * 0.5),
                                        int(base_color.blue()  + (255 - base_color.blue())  * 0.5)
                                    )
                                    self.ax.plot(frs[loc_idx], y_data[loc_idx], marker='*', markersize=10, color=lighter_color.name(), zorder=10)
                                    self.ax.annotate(
                                        t['notes'][i],
                                        (frs[loc_idx], y_data[loc_idx]),
                                        textcoords="offset points",
                                        xytext=(0, 10),
                                        ha='center',
                                        fontsize=8,
                                        fontname='DejaVu Sans',
                                        color=lighter_color.name(),
                                        bbox=dict(boxstyle="round,pad=0.3", fc="black", ec=t['color'], alpha=0.7)
                                    )

        if plotted_count > 1:
            self.ax.legend(loc='best', fontsize=7, frameon=False, labelcolor='white')
        self.draw_frame_pos()
        self.ax.tick_params(axis='both', which='both', colors='white')
        self.ax.set_facecolor('#333333')
        plt.rc('font', size=8) # change exponent label font size (if any)
        for spine in self.ax.spines.values():
            spine.set_edgecolor('white')
        self.fig.tight_layout(pad=0.8)
        self.draw()

    def draw_frame_pos(self, fr=None):
        try:
            if self.mode == 'Vibration':
                raise Exception('Frame position marker is not applicable to vibration plot')
            
            # get marker position
            t = self.parent_window.vm.track_data
            i = int(np.where(t[self.parent_window.vm.active_object]['frames'] == self.parent_window.vm.active_frame)[0][0])
            active_track = t[self.parent_window.vm.active_object]
            workspace_owner = getattr(self.parent_window, 'parent_window', self.parent_window)
            is_multi_cine = len(getattr(workspace_owner.vm, 'workspace_contexts', [])) > 1
            timestamp_key = 'frame_ts_trig' if is_multi_cine and 'frame_ts_trig' in active_track else 'frame_ts'
            ts = float(active_track[timestamp_key][i])

            # edit position or add marker if it doesn't exist
            if self.frame_pos_line in self.ax.lines:
                self.frame_pos_line.set_xdata([ts, ts])
            else:
                self.frame_pos_line = self.ax.axvline(ts, color='#00FF7F', linestyle=':', label='frame_pos', linewidth=1)
            self.draw()

        except Exception as e: 
            # force clear the frame marker
            try: 
                self.frame_pos_line.remove()
                self.draw()
                self.frame_pos_line = None
            except:
                self.frame_pos_line = None
                self.draw()
                
    def get_units(self, mode):
        length_units = self.parent_window.vm.length_units.replace('u', 'μ')
        time_units = self.parent_window.vm.time_units
        if mode == 'Displacement':
            return time_units, length_units
        elif mode == 'Speed':
            return time_units, f'{length_units}/{time_units}'
        elif mode == 'Acceleration':
            return time_units, f'{length_units}/{time_units}²'
        elif mode == 'Vibration':
            return 'Hz', 'Amplitude'
        elif mode == 'Angle':
            return time_units, 'deg'
        elif mode == 'Angular Speed':
            return time_units, f'deg/{time_units}'
    
    def plot_vibration(self, val):
        freqs, fft_mag, largest_peaks = self.parent_window.vm.vibration(val[0])
        self.ax.plot(freqs, fft_mag) # this is always in the default color, is that confusing?
        colors = ['red', 'orange']
        for i, peak in enumerate(largest_peaks):
            self.ax.scatter(freqs[peak], fft_mag[peak], color=colors[i], label=f"{freqs[peak]:.2f} Hz")
        if len(largest_peaks) > 0:
            self.ax.legend(loc='upper right', fontsize=8, frameon=False, labelcolor='white')

    def save_subplots_to_csv(self, filepath):
        """
        Extracts data from each subplot in a Matplotlib figure and saves it to a separate CSV file.

        Args:
            fig (matplotlib.figure.Figure): The Matplotlib figure object.
            filename_prefix (str): The prefix for the CSV filenames (e.g., "subplot_data_1.csv").
        """
        meas_type = self.mode
        x_unit, y_unit = self.get_units(meas_type)

        try:
            for i, ax in enumerate(self.fig.axes):
                data = []
                labels = []
                for line in ax.lines:
                    x_data = line.get_xdata()
                    y_data = line.get_ydata()
                    if len(x_data) > 0 and len(y_data) > 0 and line.get_label() != 'frame_pos':
                        name = line.get_label()
                        if meas_type != 'Vibration':
                            x_label = f'Time ({x_unit})'
                            y_label = f'{name} {meas_type} ({y_unit})'
                        else:
                            x_label = x_unit
                            y_label = y_unit
                        data.append(x_data)
                        data.append(y_data)
                        labels.append(x_label) 
                        labels.append(y_label) 
                if data:
                    pad_data = self._pad_uneven_data(data)
                    df = pd.DataFrame(np.array(pad_data).T, columns=labels[:len(pad_data)])
                    # if filepath not blank, then use it as the file name
                    if filepath == '':
                        cp = self.vm.cine_path
                        cn = os.path.splitext(os.path.basename(cp))[0]
                        dir = os.path.dirname(cp)
                        n = re.sub(r'\([^)]*\)', '', self.ax.get_title())
                        filepath = os.path.join(dir, f'{cn}_{n}.csv')
                    df.to_csv(filepath, index=False)
                    self.vm.update_status_text.emit(f'{meas_type} data saved to {filepath}')
                else:
                    self.vm.update_status_text.emit(f'No line or scatter data found in subplot')
        except Exception as e:
            self.vm.update_status_text.emit(f'Exception occured during plot data export: {e}')

    def _pad_uneven_data(self, data, fillvalue=np.nan):
        """Pads a list of lists or arrays to the length of the longest element."""
        max_len = max(len(item) for item in data)
        padded_data = [list(item) + [fillvalue] * (max_len - len(item)) for item in data]
        return padded_data
    
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        if self.show_annotations:
            toggle_action = QAction("Hide Annotations", self)
        else:
            toggle_action = QAction("Show Annotations", self)
        toggle_action.triggered.connect(self.toggle_annotations)
        menu.addAction(toggle_action)
        menu.exec(event.globalPos())

    def toggle_annotations(self):
        self.show_annotations = not self.show_annotations
        self.redraw(self.parent_window.vm.track_data)   

class TrackingGraphWindow(QMainWindow):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent_window = parent
        self.vm = parent.vm
        self.setWindowTitle("Tracking Graph")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(600, 400)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.main_layout = QGridLayout()
        container = QWidget()

        self.graph = TrackingGraph(self)
        self.graph.setObjectName('qt_tracking_graph')
        self.toolbar = NavigationToolbar2QT(self.graph, self)
        

        self._create_dropdown_layout()
        self.main_layout.addWidget(self.toolbar, 0, 0)
        self.main_layout.addLayout(self.dropdown_layout, 1, 0)
        self.main_layout.addWidget(self.graph, 2, 0)
        container.setLayout(self.main_layout)
        self.setCentralWidget(container)

        self._connect_events()

    def _create_dropdown_layout(self):
        self.dropdown_layout = QHBoxLayout()

        measurements_label = QLabel('Measurement: ')
        self.fig_dropdown = QComboBox()
        self.fig_dropdown.setObjectName('qt_meas_dropdown')
        self.fig_dropdown.addItems(['Displacement', 'X-Displacement', 'Y-Displacement', 'Speed', 'X-Speed', 'Y-Speed',
                                    'Acceleration', 'X-Acceleration', 'Y-Acceleration', 'Angle', 'Angular Speed',
                                    'X-Vibration', 'Y-Vibration'])
        self.fig_dropdown.setCurrentText(self.parent_window.fig_dropdown.currentText())
        self.fig_dropdown.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.fig_dropdown.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.time_label = QLabel('Time Units: ')
        self.time_dropdown = QComboBox()
        self.time_dropdown.setObjectName('qt_time_dropdown')
        self.time_dropdown.addItems(['s', 'ms', 'μs'])
        self.time_dropdown.setCurrentText(self.vm.time_units)
        self.time_dropdown.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.time_dropdown.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                    
        self.export_csv = QPushButton()
        self.export_csv.setText('Export')

        self.dropdown_layout.addWidget(measurements_label)
        self.dropdown_layout.addWidget(self.fig_dropdown)

        # Add a spacer item with a fixed size of 30 pixels
        spacer = QSpacerItem(30, 20, QSizePolicy.Fixed, QSizePolicy.Minimum)
        self.dropdown_layout.addItem(spacer)

        self.dropdown_layout.addWidget(self.time_label)
        self.dropdown_layout.addWidget(self.time_dropdown)
        self.dropdown_layout.addStretch()
        self.dropdown_layout.addWidget(self.export_csv)

    def _connect_events(self):
        self.fig_dropdown.currentIndexChanged.connect(self.fig_selection_cb)
        self.time_dropdown.currentTextChanged.connect(self.vm.time_selection_cb)
        self.vm.track_fig_changed.connect(self.graph.redraw)
        self.parent_window.frame_slider.valueChanged.connect(self.graph.draw_frame_pos)
        
        self.time_dropdown.setCurrentText(self.vm.time_units)
        self.graph.redraw(self.vm.track_data)
        self.export_csv.clicked.connect(self.export_data_to_csv)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.graph.redraw(self.vm.track_data)
        self.setFocus()
        super().keyPressEvent(event)

    def fig_selection_cb(self):
        time_enabled = 'Vibration' not in self.fig_dropdown.currentText()
        self.time_label.setEnabled(time_enabled)
        self.time_dropdown.setEnabled(time_enabled)
        self.graph.redraw(self.vm.track_data)

    def export_data_to_csv(self):
        try:
            fp = self.parent_window._open_report_file_picker()
            self.graph.save_subplots_to_csv(fp)
        except Exception as e:
            pass # cancel will raise an exception


class PointDataTable(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = np.array([])
        self._frames = np.array([])
        self._origin_frame = None
        self.notes = {}  # row index -> note string

    def autosize_columns(self):
        parent = self.parent()
        header = parent.point_table.horizontalHeader()
        for col in range(self.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            parent.point_table.resizeColumnToContents(col)

    def enable_user_resizing(self):
        parent = self.parent()
        header = parent.point_table.horizontalHeader()
        for col in range(self.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)

    def rowCount(self, parent=None):
        return self._data.shape[0] if self._data.size > 0 else 0
    
    def columnCount(self, /, parent = ...):
        # Add 1 for the Note column
        return self._data.shape[1] + 1 if self._data.size > 0 else 0
    
    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DisplayRole or role == Qt.EditRole:
            if index.column() < self._data.shape[1]:
                return self._data[index.row()][index.column()]
            elif index.column() == self._data.shape[1]:
                return self.notes.get(index.row(), "")
        return None
    
    def update_data(self, frames, points, scores, notes, origin_frame=None, angles=None):
        self.beginResetModel()
        pts = [f'({round(p[0], 1)}, {round(p[1], 1)})' for p in points]
        frs = frames + self.parent().first_fr
        if angles is None or len(angles) != len(frames):
            angles = np.zeros(len(frames), dtype=float)
        angle_text = [f'{float(angle):.2f}°' for angle in angles]
        self._data = np.column_stack((frs, pts, angle_text, scores))
        self._frames = frames
        self._origin_frame = origin_frame
        self.notes = notes
        self.endResetModel()
        self.autosize_columns()
        self.enable_user_resizing()

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.EditRole:
            if index.column() < self._data.shape[1]:
                update = self.parent().update_point_element(index, value)
                if update:
                    self._data[index.row()][index.column()] = value
                # Only enable user resizing here, do NOT autosize
                self.enable_user_resizing()
                self.parent().vm.track_fig_changed.emit(self.parent().vm.track_data)
                return update
            elif index.column() == self._data.shape[1]:
                if isinstance(value, str) and value.strip() == "":
                    if index.row() in self.notes:
                        del self.notes[index.row()]
                else:
                    self.notes[index.row()] = value[:15]
                self.dataChanged.emit(index, index)
                self.enable_user_resizing()
                self.parent().vm.track_fig_changed.emit(self.parent().vm.track_data)
                return True
        return False

    def clearData(self):
        self.beginResetModel()
        self._data = np.array([])
        self.notes = {}
        self.endResetModel()
    
    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            if orientation == Qt.Horizontal:
                if section == 0:
                    return "Frame"
                elif section == 1:
                    return "Point"
                elif section == 2:
                    return "Angle"
                elif section == 3:
                    return "Score"
                elif section == 4:
                    return "Note"
            elif orientation == Qt.Vertical:
                # Mark the origin_frame with an asterisk
                if self._frames.size > 0:
                    if (self._origin_frame is None and section == 0) or \
                    (self._origin_frame is not None and self._frames.size > section and self._frames[section] == self._origin_frame):
                        return f"{section}*"
                return str(section)
        return None
        
    def flags(self, index):
        if index.column() in (0, 1, self._data.shape[1]):
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable
        
#endregion

class ClipRangeSlider(QWidget):
    """Compact two-handle range control used for non-destructive viewer clips."""
    rangeChanged = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._minimum = 0
        self._maximum = 0
        self._lower = 0
        self._upper = 0
        self._active_handle = 'lower'
        self.setMinimumHeight(32)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Viewer clip range')

    def setRange(self, minimum, maximum):
        self._minimum = int(minimum)
        self._maximum = max(self._minimum, int(maximum))
        self.setValues(self._minimum, self._maximum)

    def setValues(self, lower, upper):
        lower = max(self._minimum, min(int(lower), self._maximum))
        upper = max(self._minimum, min(int(upper), self._maximum))
        if lower > upper:
            lower, upper = upper, lower
        changed = (lower, upper) != (self._lower, self._upper)
        self._lower, self._upper = lower, upper
        self.update()
        if changed:
            self.rangeChanged.emit(self._lower, self._upper)

    def lowerValue(self):
        return self._lower

    def upperValue(self):
        return self._upper

    def _value_to_x(self, value):
        margin = 12
        span = max(1, self.width() - 2 * margin)
        if self._maximum == self._minimum:
            return margin
        ratio = (value - self._minimum) / (self._maximum - self._minimum)
        return margin + round(ratio * span)

    def _x_to_value(self, x):
        margin = 12
        span = max(1, self.width() - 2 * margin)
        ratio = max(0.0, min(1.0, (x - margin) / span))
        return round(self._minimum + ratio * (self._maximum - self._minimum))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() // 2
        x_min = self._value_to_x(self._minimum)
        x_max = self._value_to_x(self._maximum)
        x_lower = self._value_to_x(self._lower)
        x_upper = self._value_to_x(self._upper)

        painter.setPen(QPen(QColor('#777777'), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(x_min, y, x_max, y)
        painter.setPen(QPen(QColor('#f47efa'), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(x_lower, y, x_upper, y)

        painter.setPen(QPen(QColor('#ffffff'), 3))
        handle_height = 18
        arm = 6
        painter.drawLine(x_lower, y - handle_height // 2, x_lower, y + handle_height // 2)
        painter.drawLine(x_lower, y - handle_height // 2, x_lower + arm, y - handle_height // 2)
        painter.drawLine(x_lower, y + handle_height // 2, x_lower + arm, y + handle_height // 2)
        painter.drawLine(x_upper, y - handle_height // 2, x_upper, y + handle_height // 2)
        painter.drawLine(x_upper - arm, y - handle_height // 2, x_upper, y - handle_height // 2)
        painter.drawLine(x_upper - arm, y + handle_height // 2, x_upper, y + handle_height // 2)

    def mousePressEvent(self, event):
        lower_distance = abs(event.position().x() - self._value_to_x(self._lower))
        upper_distance = abs(event.position().x() - self._value_to_x(self._upper))
        self._active_handle = 'lower' if lower_distance <= upper_distance else 'upper'
        self._move_active_handle(event.position().x())
        self.setFocus()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._move_active_handle(event.position().x())

    def _move_active_handle(self, x):
        value = self._x_to_value(x)
        if self._active_handle == 'lower':
            self.setValues(min(value, self._upper), self._upper)
        else:
            self.setValues(self._lower, max(value, self._lower))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Tab:
            self._active_handle = 'upper' if self._active_handle == 'lower' else 'lower'
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            delta = -1 if event.key() == Qt.Key.Key_Left else 1
            if self._active_handle == 'lower':
                self.setValues(min(self._lower + delta, self._upper), self._upper)
            else:
                self.setValues(self._lower, max(self._upper + delta, self._lower))
            event.accept()
            return
        super().keyPressEvent(event)

#region MAINWINDOW
class MainWindow(QWidget):
    vm: simplemeas_vm.SimpleMeasVM
    
    def __init__(self):
        self.vm = None
        self.cine_path = ''
        self.first_fr = 0
        self.last_fr = 0
        self.workspace_panes = []
        self.workspace_clip_ranges = {}
        self.active_pane_index = 0
        self._hybrid_selection_graph = None
        self._base_stylesheet = ''
        self.theme_accent = '#f47efa'
        super().__init__()
        self.init_ui()
        self.shortcut = QShortcut(QKeySequence("Ctrl+o"), self)
        self.shortcut.activated.connect(self._choose_image)
        self.resize(1024, 720)
        self.setMouseTracking(True)
        self.selected_row_track_table = -1
        self.sa_color = None
        self.load_cine_thread = None

    def show(self):
        super().show()
        self.track_canvas.redraw(self.vm.track_data)

    def closeEvent(self, event):
        self._stop_playback()
        #cleanup cache
        if hasattr(self, 'vm') and self.vm is not None:
            self.vm.close_workspace()
            logging.info("Application closing - cache cleaned up")
        
        for window in QApplication.topLevelWidgets():
            window.close()

        event.accept()

    def init_ui(self):
        self.setWindowTitle(f"Track & Measure v{VERSION}")
        
        self._create_analysis_tools_col()
        self._create_metadata_tab()
        self._create_adjustments_tab()
        self._create_track_tab()
        self._create_bottom_row()
        self._create_cine_display_col()

        self.main_tab = QTabWidget()
        self.main_tab.setObjectName('cine_info_tab')
        self.main_tab.setMinimumWidth(280)
        self.main_tab.addTab(self.metadata_tab, "INFO")
        self.main_tab.addTab(self.adjustments_tab, "ADJ")
        self.main_tab.addTab(self.track_tab, "TRACK")
        
        middle_widget = QWidget()
        middle_layout = QGridLayout(middle_widget)
        middle_layout.addWidget(self.cine_disp_col, 0, 0)
        middle_layout.addWidget(self.status_toolbar, 1, 0)

        self.tab_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tab_splitter.addWidget(middle_widget)
        self.tab_splitter.addWidget(self.main_tab)
        self.tab_splitter.setStretchFactor(0, 1)
        self.tab_splitter.setStretchFactor(1, 0)
        self.tab_splitter.setCollapsible(1, False)

        main_layout = QGridLayout()
        main_layout.addWidget(self.analysis_tools, 0, 0)
        main_layout.addWidget(self.tab_splitter, 0, 2)
        self.setLayout(main_layout)

        self.auto = AutoTrackDialog(self)
        self.auto.setObjectName('autotrack_dialog')
        

#region LAYOUT

    def _create_analysis_tools_col(self):
        self.analysis_tools = QWidget()
        self.scale = ScaleLabel("1 px/px", self)
        self.scale.setObjectName('qt_scale_label')
        self.scale_tool = QPushButton()
        self.scale_tool.setToolTip("Measure image scale")
        self.scale_tool.setIcon(QIcon(os.path.join(dir_path, "images", "ruler-32.png")))
        self.scale_tool.setObjectName("qt_scale_tool")
        self.scale_tool.setCheckable(True)

        self.viewer_tool = QIconTextButton("VIEWER", os.path.join(dir_path, "images", "viewer-eye.svg"), "Watch and clip the cine", checkable=True, icon_size=QSize(20, 20))
        self.viewer_tool.setObjectName("qt_viewer_tool")
        self.track_tool = QIconTextButton("TRACK", os.path.join(dir_path, "images", "target-32.png"), "Manual/Auto tracking", checkable=True, icon_size=QSize(20, 20))
        self.two_pt_tool = QIconTextButton("   2 PT", os.path.join(dir_path, "images", "line-32.png"), "2-point analysis", checkable=True, icon_size=QSize(20, 20))
        self.three_pt_tool = QIconTextButton("   3 PT", os.path.join(dir_path, "images", "angle-32.png"), "3-point analysis", checkable=True, icon_size=QSize(20, 20))
        self.two_line_tool = QIconTextButton(" 2 LINE", os.path.join(dir_path, "images", "2-line-32.png"), "2-line analysis", checkable=True, icon_size=QSize(20, 20))
        self.area_tool = QIconTextButton(" AREA", os.path.join(dir_path, "images", "rect-32.png"), "Area analysis", checkable=True, icon_size=QSize(20, 20))

        self.analysis_tools_bg = QButtonGroup(self.analysis_tools)
        self.analysis_tools_bg.addButton(self.scale_tool)
        self.analysis_tools_bg.addButton(self.viewer_tool)
        self.analysis_tools_bg.addButton(self.track_tool)
        self.analysis_tools_bg.addButton(self.two_pt_tool)
        self.analysis_tools_bg.addButton(self.three_pt_tool)
        self.analysis_tools_bg.addButton(self.two_line_tool)
        self.analysis_tools_bg.addButton(self.area_tool)
        self.current_tool = None # necessary for unchecking buttons in a group

        self.export_button = QIconTextButton("EXPORT", os.path.join(dir_path, "images", "save-32.png"), "Export a report", checkable=False, icon_size=QSize(20, 20))
        self.export_button.setObjectName("qt_export_button")
        self.export_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        user_manual_button = QPushButton()
        user_manual_button.setObjectName('qt_user_manual_button')
        user_manual_button.setIcon(QIcon(os.path.join(dir_path, "images", "info-32.png")))
        user_manual_button.clicked.connect(self._open_manual)
        
        analysis_tools_layout = QGridLayout()
        analysis_tools_layout.addWidget(self.scale_tool, 0, 0)
        analysis_tools_layout.addWidget(self.scale, 0, 1)
        analysis_tools_layout.addWidget(self.viewer_tool, 1, 0, 1, -1)
        analysis_tools_layout.addWidget(self.track_tool, 2, 0, 1, -1)
        analysis_tools_layout.addWidget(self.two_pt_tool, 3, 0, 1, -1)
        analysis_tools_layout.addWidget(self.three_pt_tool, 4, 0, 1, -1)
        analysis_tools_layout.addWidget(self.two_line_tool, 5, 0, 1, -1)
        analysis_tools_layout.addWidget(self.area_tool, 6, 0, 1, -1)
        analysis_tools_layout.addWidget(self.export_button, 7, 0, 1, -1)
        analysis_tools_layout.setRowStretch(8, 1)
        analysis_tools_layout.addWidget(user_manual_button, 9, 0)
        
        self.analysis_tools.setLayout(analysis_tools_layout)
        self.analysis_tools.setObjectName("analysis_grid")

    def _create_cine_display_col(self):
        self.cine_disp_col = QWidget()
        cine_disp_layout = QGridLayout()
        self.cine_path_disp = QLabel("No Cine Selected")
        self.cine_path_disp.setObjectName('cine_path_disp')
        cine_upload_button = QPushButton("Choose Cines")
        cine_upload_button.setObjectName('cine_upload_button')
        cine_upload_button.clicked.connect(self._choose_image)
        cine_upload_button.setToolTip('Choose up to four Cine files')
        self.workspace_count_label = QLabel('1 Cine')
        self.workspace_count_label.setObjectName('workspace_count_label')
        self.theme_button = QToolButton()
        self.theme_button.setObjectName('qt_theme_button')
        self.theme_button.setIcon(create_color_wheel_icon())
        self.theme_button.setIconSize(QSize(26, 26))
        self.theme_button.setFixedSize(36, 34)
        self.theme_button.setToolTip('Choose interface accent color')
        self.theme_button.clicked.connect(self._choose_theme_color)

        self.workspace_grid_widget = QWidget()
        self.workspace_grid_widget.setObjectName('cine_workspace_grid')
        self.workspace_grid_layout = QGridLayout(self.workspace_grid_widget)
        self.workspace_grid_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_grid_layout.setSpacing(5)
        for pane_index in range(4):
            pane = WorkspacePane(self, pane_index)
            pane.setVisible(pane_index == 0)
            self.workspace_panes.append(pane)
            self.workspace_grid_layout.addWidget(pane, 0, pane_index)
        self.graph = self.workspace_panes[0].graph
        self.status_bar = QLabel('')
        self.status_bar.setObjectName("status_bar")
        self.status_bar.setWordWrap(True)
        self.status_bar.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.status_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.status_bar.setFixedHeight(self.fontMetrics().lineSpacing() * 2)
        self.status_bar.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.status_bar.customContextMenuRequested.connect(self.contextMenuEventStatusBar)
        self.cine_path_disp.setObjectName('cine_path_disp')
        self.frame_slider = QSlider(orientation=Qt.Orientation.Horizontal)
        self.frame_slider.setObjectName("qt_active_frame")
        self.playback_timer = QTimer(self)
        self.playback_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.playback_timer.setInterval(33)
        self._playback_step = 1

        self.pcc_transport_controls = QFrame()
        self.pcc_transport_controls.setObjectName('pcc_transport_controls')
        pcc_layout = QGridLayout(self.pcc_transport_controls)
        pcc_layout.setContentsMargins(5, 5, 5, 5)
        pcc_layout.setHorizontalSpacing(3)
        pcc_layout.setVerticalSpacing(3)

        self.reverse_play_button = QToolButton()
        self.pause_button = QToolButton()
        self.forward_play_button = QToolButton()
        self.reverse_faster_button = QToolButton()
        self.previous_frame_button = QToolButton()
        self.next_frame_button = QToolButton()
        self.forward_faster_button = QToolButton()

        button_specs = (
            (self.reverse_play_button, 'reverse', 'Play backward at normal speed (J)'),
            (self.pause_button, 'pause', 'Pause playback (Space)'),
            (self.forward_play_button, 'forward', 'Play forward at normal speed (L)'),
            (self.reverse_faster_button, 'fast_reverse', 'Play backward at 4× speed (Shift+J)'),
            (self.previous_frame_button, 'previous_frame', 'Pause and move back one frame (Left)'),
            (self.next_frame_button, 'next_frame', 'Pause and move forward one frame (Right)'),
            (self.forward_faster_button, 'fast_forward', 'Play forward at 4× speed (Shift+L)'),
        )
        for button, icon_kind, tooltip in button_specs:
            button.setObjectName('pcc_transport_button')
            button.setIcon(create_playback_icon(icon_kind))
            button.setIconSize(QSize(22, 22))
            button.setToolTip(tooltip)
            button.setEnabled(False)

        for button in (self.reverse_play_button, self.pause_button, self.forward_play_button,
                       self.reverse_faster_button, self.forward_faster_button):
            button.setCheckable(True)

        self.transport_mode_group = QButtonGroup(self.pcc_transport_controls)
        self.transport_mode_group.setExclusive(True)
        for button in (self.reverse_play_button, self.pause_button, self.forward_play_button,
                       self.reverse_faster_button, self.forward_faster_button):
            self.transport_mode_group.addButton(button)
        self.pause_button.setChecked(True)

        for button in (self.reverse_play_button, self.pause_button, self.forward_play_button):
            button.setFixedSize(41, 31)
        for button in (self.reverse_faster_button, self.previous_frame_button,
                       self.next_frame_button, self.forward_faster_button):
            button.setFixedSize(30, 31)

        pcc_layout.addWidget(self.reverse_play_button, 0, 0, 1, 4)
        pcc_layout.addWidget(self.pause_button, 0, 4, 1, 4)
        pcc_layout.addWidget(self.forward_play_button, 0, 8, 1, 4)
        pcc_layout.addWidget(self.reverse_faster_button, 1, 0, 1, 3)
        pcc_layout.addWidget(self.previous_frame_button, 1, 3, 1, 3)
        pcc_layout.addWidget(self.next_frame_button, 1, 6, 1, 3)
        pcc_layout.addWidget(self.forward_faster_button, 1, 9, 1, 3)

        self.viewer_speed_buttons = {
            self.reverse_faster_button: -4,
            self.reverse_play_button: -1,
            self.forward_play_button: 1,
            self.forward_faster_button: 4,
        }
        self.transport_buttons = [button for button, _, _ in button_specs]

        # Compatibility aliases retained for integrations and saved automation.
        self.play_button = self.forward_play_button
        self.viewer_play_button = self.forward_play_button
        self.reverse_fast_button = self.reverse_play_button
        self.forward_fast_button = self.forward_play_button
        self.rewind_button = self.previous_frame_button
        self.fast_forward_button = self.next_frame_button

        self.jump_start_button = QToolButton()
        self.jump_start_button.setObjectName('qt_transport_button')
        self.jump_start_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipBackward))
        self.jump_start_button.setToolTip('Jump to clip start (Home)')

        self.jump_end_button = QToolButton()
        self.jump_end_button.setObjectName('qt_transport_button')
        self.jump_end_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipForward))
        self.jump_end_button.setToolTip('Jump to clip end (End)')
        for button in (self.jump_start_button, self.jump_end_button):
            button.setEnabled(False)
            button.setFixedSize(34, 30)

        self.clip_range = ClipRangeSlider(self)
        self.clip_range.setObjectName('qt_clip_range')
        self.clip_range.setEnabled(False)
        self.clip_start_label = QLabel('In: —')
        self.clip_start_label.setObjectName('clip_range_label')
        self.clip_end_label = QLabel('Out: —')
        self.clip_end_label.setObjectName('clip_range_label')
        self.mark_in_button = QPushButton('Set In')
        self.mark_out_button = QPushButton('Set Out')
        self.reset_clip_button = QPushButton('Reset Clip')
        for button in (self.mark_in_button, self.mark_out_button, self.reset_clip_button):
            button.setObjectName('qt_clip_button')
            button.setEnabled(False)

        self.viewer_controls = QWidget()
        self.viewer_controls.setObjectName('viewer_controls')
        viewer_controls_layout = QVBoxLayout(self.viewer_controls)
        viewer_controls_layout.setContentsMargins(8, 6, 8, 6)
        clip_labels_layout = QHBoxLayout()
        clip_labels_layout.addWidget(self.jump_start_button)
        clip_labels_layout.addWidget(self.clip_start_label)
        clip_labels_layout.addStretch()
        clip_labels_layout.addWidget(self.mark_in_button)
        clip_labels_layout.addWidget(self.reset_clip_button)
        clip_labels_layout.addWidget(self.mark_out_button)
        clip_labels_layout.addStretch()
        clip_labels_layout.addWidget(self.clip_end_label)
        clip_labels_layout.addWidget(self.jump_end_button)
        viewer_controls_layout.addWidget(self.clip_range)
        viewer_controls_layout.addLayout(clip_labels_layout)
        self.viewer_controls.setVisible(False)
        self.first_frame_disp = QLabel('')
        self.first_frame_disp.setObjectName("qt_first_frame")
        self.first_frame_disp.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.last_frame_disp = QLabel('')
        self.last_frame_disp.setObjectName("qt_last_frame")
        self.last_frame_disp.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.active_frame_label = QLabel("0", self)
        self.active_frame_label.adjustSize()
        self.active_frame_label.setHidden(True)
        self.loading_widget = LoadingWidget()
        self.loading_widget.setObjectName('qt_loading_widget')
        self.loading_msg = LoadingMessage()
        self.loading_msg.setObjectName('qt_loading_msg')
        
        cine_header_layout = QHBoxLayout()
        cine_header_layout.setContentsMargins(0, 0, 0, 0)
        cine_header_layout.addWidget(cine_upload_button)
        cine_header_layout.addWidget(self.cine_path_disp, 1)
        cine_header_layout.addWidget(self.workspace_count_label)
        cine_header_layout.addWidget(self.theme_button)
        cine_disp_layout.addLayout(cine_header_layout,0,0,1,-1)
        cine_disp_layout.addWidget(self.workspace_grid_widget,1,0,1,-1)
        cine_disp_layout.addWidget(self.status_bar,2,0,1,-1)
        cine_disp_layout.rowStretch(2)

        self.frame_range_layout = QHBoxLayout()
        self.frame_range_layout.setContentsMargins(0, 0, 0, 0)
        self.frame_range_layout.addWidget(self.first_frame_disp)
        self.frame_range_layout.addStretch(1)
        self.frame_range_layout.addWidget(self.last_frame_disp)

        self.slider_column_layout = QVBoxLayout()
        self.slider_column_layout.setContentsMargins(0, 0, 0, 0)
        self.slider_column_layout.setSpacing(0)
        self.slider_column_layout.addLayout(self.frame_range_layout)
        self.slider_column_layout.addWidget(self.frame_slider)

        self.scrubber_layout = QHBoxLayout()
        self.scrubber_layout.setContentsMargins(0, 0, 0, 0)
        self.scrubber_layout.setSpacing(16)
        self.scrubber_layout.addWidget(self.pcc_transport_controls, 0, Qt.AlignmentFlag.AlignVCenter)
        self.scrubber_layout.addLayout(self.slider_column_layout, 1)
        cine_disp_layout.addLayout(self.scrubber_layout,3,0,1,-1)
        cine_disp_layout.rowStretch(3)
        cine_disp_layout.addWidget(self.viewer_controls,4,0,1,-1)
        self.cine_disp_col.setLayout(cine_disp_layout)
        
    def _create_metadata_tab(self):
        self.metadata_tab = QWidget()
        metadata_layout = QVBoxLayout(self.metadata_tab)

        timestamp_info_label = QLabel("Timestamp Info", self.metadata_tab)
        timestamp_info_label.setObjectName('timestamp_info_label')
        self.trigger_time = QLabel("Trigger Time:", self.metadata_tab)
        self.trigger_time.setObjectName("qt_trigger_time")
        self.time_from_trigger = QLabel("Time From Trigger:", self.metadata_tab)
        self.time_from_trigger.setObjectName("qt_time_from_trigger")
        
        md_label = QLabel("Other Metadata")
        md_label.setObjectName("other_metadata")
        self.metadata_table = QDataTableWidget(None, 2, None, parent=self.metadata_tab, vertical_scrollbar=False)
        self.metadata_table.setObjectName("metadata_table")
        self.metadata_table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.metadata_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.metadata_table.setVisible(False)
        self.metadata_table.horizontalHeader().setStretchLastSection(True)
     
        metadata_layout.addWidget(timestamp_info_label, alignment=Qt.AlignmentFlag.AlignTop)
        metadata_layout.addWidget(self.trigger_time, alignment=Qt.AlignmentFlag.AlignTop)
        metadata_layout.addWidget(self.time_from_trigger, alignment=Qt.AlignmentFlag.AlignTop)
        metadata_layout.addWidget(md_label, alignment=Qt.AlignmentFlag.AlignTop)
        metadata_layout.addWidget(self.metadata_table, alignment=Qt.AlignmentFlag.AlignTop)
        metadata_layout.addStretch()
        
        self.metadata_tab.setLayout(metadata_layout)
        
    def _create_track_tab(self):
        self.track_tab = QWidget()
        track_layout = QGridLayout(self.track_tab)
        
        self.track_type_man = QPushButton('Manual')
        self.track_type_man.setCheckable(True)
        self.track_type_man.setObjectName('man_track_button')
        self.track_type_auto = QPushButton('Auto')
        self.track_type_auto.setCheckable(True)
        self.track_type_auto.setChecked(True)
        self.track_type_auto.setObjectName('auto_track_button')
        self.track_type_bg = QButtonGroup(self.track_tab)
        self.track_type_bg.addButton(self.track_type_auto)
        self.track_type_bg.addButton(self.track_type_man)
        self.track_type_layout = QHBoxLayout()
        self.track_type_layout.addWidget(self.track_type_auto)
        self.track_type_layout.addWidget(self.track_type_man)

        self.hybrid_settings_panel = QFrame()
        self.hybrid_settings_panel.setObjectName('hybrid_settings_panel')
        hybrid_settings_layout = QVBoxLayout(self.hybrid_settings_panel)
        hybrid_settings_layout.setContentsMargins(8, 7, 8, 7)
        hybrid_settings_layout.setSpacing(6)

        hybrid_heading = QHBoxLayout()
        hybrid_title = QLabel('Hybrid Tracking')
        hybrid_title.setObjectName('hybrid_settings_title')
        self.hybrid_advanced_button = QToolButton()
        self.hybrid_advanced_button.setObjectName('hybrid_advanced_button')
        self.hybrid_advanced_button.setText('Advanced  ▸')
        self.hybrid_advanced_button.setCheckable(True)
        hybrid_heading.addWidget(hybrid_title)
        hybrid_heading.addStretch(1)
        hybrid_heading.addWidget(self.hybrid_advanced_button)
        hybrid_settings_layout.addLayout(hybrid_heading)

        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel('Match Threshold'))
        self.hybrid_match_threshold = QDoubleSpinBox()
        self.hybrid_match_threshold.setObjectName('hybrid_match_threshold')
        self.hybrid_match_threshold.setRange(0.0, 1.0)
        self.hybrid_match_threshold.setSingleStep(0.01)
        self.hybrid_match_threshold.setDecimals(2)
        self.hybrid_match_threshold.setValue(0.60)
        threshold_row.addWidget(self.hybrid_match_threshold)
        hybrid_settings_layout.addLayout(threshold_row)

        self.hybrid_smart_frames = QCheckBox('Smart process frame start/stop')
        self.hybrid_smart_frames.setChecked(True)
        self.hybrid_smart_frames.setToolTip(
            'Track in both directions from the selected frame and stop at a low-confidence boundary.'
        )
        hybrid_settings_layout.addWidget(self.hybrid_smart_frames)

        self.hybrid_advanced_panel = QWidget()
        hybrid_advanced_form = QFormLayout(self.hybrid_advanced_panel)
        hybrid_advanced_form.setContentsMargins(0, 2, 0, 0)

        self.hybrid_rotation_allowed = QCheckBox('Allow rotation')
        self.hybrid_rotation_allowed.setChecked(True)
        hybrid_advanced_form.addRow(self.hybrid_rotation_allowed)
        self.hybrid_rotation_range = QDoubleSpinBox()
        self.hybrid_rotation_range.setRange(0.0, 180.0)
        self.hybrid_rotation_range.setSingleStep(1.0)
        self.hybrid_rotation_range.setSuffix('°')
        self.hybrid_rotation_range.setValue(15.0)
        hybrid_advanced_form.addRow('Rotation Range', self.hybrid_rotation_range)
        self.hybrid_rotation_step = QDoubleSpinBox()
        self.hybrid_rotation_step.setRange(0.25, 15.0)
        self.hybrid_rotation_step.setSingleStep(0.25)
        self.hybrid_rotation_step.setSuffix('°')
        self.hybrid_rotation_step.setValue(2.0)
        hybrid_advanced_form.addRow('Rotation Step', self.hybrid_rotation_step)
        self.hybrid_edge_weight = QDoubleSpinBox()
        self.hybrid_edge_weight.setRange(0.0, 1.0)
        self.hybrid_edge_weight.setSingleStep(0.05)
        self.hybrid_edge_weight.setValue(0.60)
        hybrid_advanced_form.addRow('Edge Weight', self.hybrid_edge_weight)
        self.hybrid_edge_threshold = QDoubleSpinBox()
        self.hybrid_edge_threshold.setRange(0.02, 1.0)
        self.hybrid_edge_threshold.setSingleStep(0.02)
        self.hybrid_edge_threshold.setValue(0.30)
        self.hybrid_edge_threshold.setToolTip('Controls which image gradients appear in the purple edge preview.')
        hybrid_advanced_form.addRow('Edge Threshold', self.hybrid_edge_threshold)
        self.hybrid_search_multiplier = QDoubleSpinBox()
        self.hybrid_search_multiplier.setRange(1.25, 10.0)
        self.hybrid_search_multiplier.setSingleStep(0.25)
        self.hybrid_search_multiplier.setSuffix('×')
        self.hybrid_search_multiplier.setValue(3.0)
        hybrid_advanced_form.addRow('Search Area', self.hybrid_search_multiplier)
        self.hybrid_miss_limit = QSpinBox()
        self.hybrid_miss_limit.setRange(1, 30)
        self.hybrid_miss_limit.setValue(3)
        hybrid_advanced_form.addRow('Low-score Frames', self.hybrid_miss_limit)
        self.hybrid_update_template = QCheckBox('Update template as appearance changes')
        self.hybrid_update_template.setChecked(True)
        hybrid_advanced_form.addRow(self.hybrid_update_template)
        self.hybrid_advanced_panel.setVisible(False)
        hybrid_settings_layout.addWidget(self.hybrid_advanced_panel)

        self.hybrid_process_button = QPushButton('Process Smart Range')
        self.hybrid_process_button.setObjectName('hybrid_process_button')
        self.hybrid_progress_bar = QProgressBar()
        self.hybrid_progress_bar.setObjectName('hybrid_progress_bar')
        self.hybrid_progress_bar.setRange(0, 100)
        self.hybrid_progress_bar.setValue(0)
        self.hybrid_progress_bar.setFormat('Ready')
        self.hybrid_progress_bar.setTextVisible(True)
        hybrid_settings_layout.addWidget(self.hybrid_progress_bar)
        hybrid_settings_layout.addWidget(self.hybrid_process_button)
        self.hybrid_settings_panel.setVisible(False)
        
        self.template_img = QGraphicsView()
        self.template_img.setScene(QGraphicsScene(self.template_img))
        self.template_img.setObjectName('qt_template_img')
        self.template_img.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed)
        self.template_img.scene().setSceneRect(QRectF(0, 0, MINIGRAPH_SIZE, MINIGRAPH_SIZE))
        
        self.add_object_button = QPushButton("             Add Object")
        self.add_object_button.setCheckable(True)
        self.add_object_button.setIcon(QIcon(os.path.join(dir_path, "images", "add-32.png")))
        self.add_object_button.setObjectName("qt_add_object_button")

        track_table_label = QLabel('Objects', self.track_tab)
        track_table_label.setObjectName('track_table_label')
        self.track_table = QDataTableWidget(0, 3, ['#','Name','Color'], parent=self.track_tab)
        self.track_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.track_table.horizontalHeader().setStretchLastSection(False)
        self.track_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.track_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        point_table_label = QLabel('All Points', self.track_tab)
        point_table_label.setObjectName('point_table_label')
        self.point_data = PointDataTable(self)
        self.point_table = QTableView()
        self.point_table.setModel(self.point_data)
        self.point_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.point_table.setAlternatingRowColors(True)
        self.point_table.setSelectionMode(QAbstractItemView.SelectionMode.ContiguousSelection)
        self.point_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.point_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.point_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.point_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.point_table.horizontalHeader().setStretchLastSection(True)
        self.point_table.resizeColumnToContents(2)
        self.point_table.verticalHeader().setVisible(True)
        
        track_layout.addLayout(self.track_type_layout, 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        track_layout.addWidget(self.hybrid_settings_panel, 1, 0)
        track_layout.addWidget(self.template_img, 2, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        track_layout.addWidget(self.add_object_button, 3, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        track_layout.addWidget(track_table_label, 4, 0)
        track_layout.addWidget(self.track_table, 5, 0)
        track_layout.addWidget(point_table_label, 6, 0)
        track_layout.addWidget(self.point_table, 7, 0)
        track_layout.setRowStretch(track_layout.rowCount() - 1, 2)
        
        self.track_tab.setLayout(track_layout)  
        self.track_tab.setEnabled(False)

    def _create_adjustments_tab(self):
        self.adjustments_tab = QWidget()
        adjustments_layout = QGridLayout()
        adjustments_title = QLabel("Adjustments", self.adjustments_tab)
        adjustments_title.setObjectName('adjustments_title')
        self.contrast_slider = QSlider(orientation=Qt.Orientation.Horizontal)
        self.gamma_slider = QSlider(orientation=Qt.Orientation.Horizontal)
        self.bright_slider = QSlider(orientation=Qt.Orientation.Horizontal)
        self.contrast_slider.setObjectName("qt_image_contrast")
        self.gamma_slider.setObjectName("qt_image_gamma")
        self.bright_slider.setObjectName("qt_image_brightness")
        self.contrast_slider.setRange(0, 1000)
        self.contrast_slider.setValue(100)
        self.gamma_slider.setRange(100, 1000)
        self.bright_slider.setRange(-65535, 65535)
        self.bright_slider.setValue(0)
        self.toggle_ftone = QToggleSwitch('Apply Tone Curve')
        self.toggle_ftone.setObjectName("qt_toggle_ftone")
        self.raw_adj_button = QToggleSwitch('Show Raw Image')
        self.raw_adj_button.setObjectName("qt_raw_button")
        toggle_layout = QVBoxLayout()
        toggle_layout.addWidget(self.toggle_ftone)
        toggle_layout.addWidget(self.raw_adj_button)
        self.toggle_title = QLabel(" Image Processing",self.adjustments_tab)
        self.toggle_title.setObjectName('toggle_title')
        playback_title = QLabel("Playback Speed", self.adjustments_tab)
        playback_title.setObjectName('playback_title')
        playback_title.setFixedSize(100, 25)
        self.playback_speed = QSlider(orientation=Qt.Orientation.Horizontal)
        self.playback_speed.setObjectName("qt_playback_speed")
        self.playback_speed.setRange(1, 10)
        self.playback_speed.setValue(1)
        self.playback_speed.setSingleStep(1)
        # Fixed 1×/4× PCC transport buttons replace the legacy variable-speed slider.
        playback_title.setVisible(False)
        self.playback_speed.setVisible(False)

        button_layout = QHBoxLayout()
        self.reset_adj_button = QPushButton('Reset')
        self.reset_adj_button.setObjectName("qt_reset_button")
        button_layout.addWidget(self.reset_adj_button)

        adjustments_layout.addWidget(adjustments_title,0,0,1,-1, alignment=Qt.AlignmentFlag.AlignTop)
        adjustments_layout.addWidget(self.contrast_slider,1,1, alignment=Qt.AlignmentFlag.AlignTop)
        adjustments_layout.addWidget(self.gamma_slider,2,1, alignment=Qt.AlignmentFlag.AlignTop)
        adjustments_layout.addWidget(self.bright_slider,3,1, alignment=Qt.AlignmentFlag.AlignTop)

        adjustments_layout.addWidget(QLabel("Contrast"),1,0, alignment=Qt.AlignmentFlag.AlignTop)
        adjustments_layout.addWidget(QLabel("Gamma"),2,0, alignment=Qt.AlignmentFlag.AlignTop)
        adjustments_layout.addWidget(QLabel("Brightness"),3,0, alignment=Qt.AlignmentFlag.AlignTop)

        adjustments_layout.addLayout(button_layout,4,0,1,2)
        adjustments_layout.addWidget(QLabel(" "),5,0) # emptyspace
        adjustments_layout.addWidget(self.toggle_title,6,0,1,2, alignment=Qt.AlignmentFlag.AlignTop)
        adjustments_layout.addLayout(toggle_layout,7,0,1,2)
        adjustments_layout.setRowStretch(adjustments_layout.rowCount(), 1)
        
        adjustments_layout.addWidget(playback_title,9,0, alignment=Qt.AlignmentFlag.AlignVCenter)
        adjustments_layout.addWidget(self.playback_speed,9,1, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        self.adjustments_tab.setLayout(adjustments_layout)  

    def _create_bottom_row(self):
        self.status_toolbar = QWidget()
        status_toolbar_layout = QGridLayout()
       
        self.magnifier = QGraphicsView()
        self.magnifier.setScene(QGraphicsScene(self.magnifier))
        self.magnifier.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Fixed)
        self.magnifier.setObjectName("magnifier")
        self.magnifier.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.magnifier.scene().setSceneRect(QRectF(0, 0, MINIGRAPH_SIZE, MINIGRAPH_SIZE))

        zoom_layout = QVBoxLayout()
        self.zoom_out_button = QToolButton()
        self.zoom_out_button.setObjectName('qt_zoom_out')
        self.zoom_out_button.setIcon(QIcon(os.path.join(dir_path, "images", "zoom-out.png")))
        self.zoom_in_button = QToolButton()
        self.zoom_in_button.setObjectName('qt_zoom_in')
        self.zoom_in_button.setIcon(QIcon(os.path.join(dir_path, "images", "zoom-in.png")))
        self.zoom_slider = QSlider(orientation=Qt.Orientation.Vertical)
        self.zoom_slider.setObjectName('qt_zoom_slider')
        self.zoom_slider.setRange(0, 6)
        self.zoom_slider.setValue(3)
        self.zoom_levels = [8, 5, 3, 2, 1.5, 1, 0.75]
        self.zoom_slider.setInvertedAppearance(True)
        self.zoom_slider.setInvertedControls(True)
        zoom_layout.addWidget(self.zoom_in_button)
        zoom_layout.addWidget(self.zoom_slider)
        zoom_layout.addWidget(self.zoom_out_button)
        
        self.mouse_pos_label = QLabel('Coordinates:\nN/A')
        self.mouse_pos_label.setObjectName("qt_mouse_pos")
        self.pixel_val = QLabel('RAW:\nN/A')
        self.pixel_val.setObjectName("qt_pixel_val")
        mouse_info_layout = QHBoxLayout()
        mouse_info_layout.addWidget(self.mouse_pos_label, alignment=Qt.AlignmentFlag.AlignCenter)
        mouse_info_layout.addWidget(self.pixel_val, alignment=Qt.AlignmentFlag.AlignCenter)

        self.track_canvas = TrackingGraph(self)
        self.track_canvas.setObjectName('qt_track_canvas')
        self.fig_dropdown = QComboBox()
        self.fig_dropdown.setObjectName('qt_fig_dropdown')
        self.fig_dropdown.addItems(['Displacement', 'X-Displacement', 'Y-Displacement', 'Speed', 'X-Speed', 'Y-Speed', 
                                    'Acceleration', 'X-Acceleration', 'Y-Acceleration', 'Angle', 'Angular Speed',
                                    'X-Vibration', 'Y-Vibration'])
        self.fig_dropdown.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.fig_dropdown.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.graph_expand_button = QToolButton()
        self.graph_expand_button.setObjectName('qt_graph_expand')
        self.graph_expand_button.setIcon(QIcon(os.path.join(dir_path, "images", "popout.png")))

        dropdown_layout = QHBoxLayout()
        dropdown_layout.addWidget(self.fig_dropdown, alignment=Qt.AlignmentFlag.AlignLeft)
        dropdown_layout.addWidget(self.graph_expand_button, alignment=Qt.AlignmentFlag.AlignRight)
        
        status_toolbar_layout.addWidget(self.track_canvas, 0, 1)
        status_toolbar_layout.addWidget(self.magnifier, 0, 2)
        status_toolbar_layout.addLayout(dropdown_layout, 1, 1)
        status_toolbar_layout.addLayout(zoom_layout, 0, 3)
        status_toolbar_layout.addLayout(mouse_info_layout, 1, 2, alignment=Qt.AlignmentFlag.AlignCenter)
        self.status_toolbar.setLayout(status_toolbar_layout)
        self.status_toolbar.setObjectName('status_toolbar')

        
#endregion
#region PRIVATE METHODS
    
    def _choose_image(self, path=None):
        if self.auto.isVisible(): self.auto.hide()
        self.vm.sync_active_workspace_context()
        workspace_has_data = any(
            len(context.get('meas_data', {})) > 1 or len(context.get('track_data', {})) > 0
            for context in self.vm.workspace_contexts
        )
        if workspace_has_data:
            confirmation = QMessageBox.warning(self, "Warning", "Are you sure you want to load a new file? \nThis will erase all current data.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if confirmation == QMessageBox.StandardButton.No:
                return
        if path:
            cine_paths = path if isinstance(path, (list, tuple)) else [path]
        else:
            cine_paths, _ = QFileDialog.getOpenFileNames(
                self, 'Choose up to four Cine files', filter='Cine Files (*.cine)'
            )
        cine_paths = list(cine_paths or [])[:4]
        
        if cine_paths:
            self.loading_msg.show_widget()
            self.load_cine_thread = QThread()
            self.load_cine_worker = WorkerThread(self._load_cine, cine_paths)
            self.load_cine_worker.moveToThread(self.load_cine_thread)
            self.load_cine_thread.started.connect(self.load_cine_worker.run)
            self.load_cine_thread.finished.connect(self.loading_msg.hide)
            self.load_cine_thread.start()
            self.load_cine_thread.setPriority(QThread.Priority.TimeCriticalPriority)  # or HighestPriority
            
    def _load_cine(self, cine_paths):
        #toggle off the reset and raw buttons and enable sliders when an image is chosen
        self.reset_adj_button.setEnabled(True)
        self.raw_adj_button.setEnabled(True)
        self.vm.toggle_ftone = True
        self.vm.raw_enabled = False

        self.contrast_slider.setEnabled(True)
        self.gamma_slider.setEnabled(True)
        self.bright_slider.setEnabled(True)

        self.vm.load_cine_workspace_cb(cine_paths)

        if self.load_cine_thread is not None: self.load_cine_thread.terminate()

    def _configure_workspace_grid(self, count):
        count = max(1, min(int(count), 4))
        positions = {
            1: [(0, 0, 1, 2)],
            2: [(0, 0, 1, 1), (0, 1, 1, 1)],
            3: [(0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 2)],
            4: [(0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 1), (1, 1, 1, 1)],
        }
        for index, pane in enumerate(self.workspace_panes):
            pane.setVisible(index < count)
            if index < count:
                row, col, row_span, col_span = positions[count][index]
                self.workspace_grid_layout.addWidget(pane, row, col, row_span, col_span)

    def _set_active_pane_style(self, active_index):
        self.active_pane_index = active_index
        for index, pane in enumerate(self.workspace_panes):
            pane.set_active(index == active_index)

    def activate_cine_pane(self, index):
        if not (0 <= index < len(self.vm.workspace_contexts)):
            return
        if index == self.active_pane_index and self.vm.active_cine_index == index:
            return
        self._stop_playback()
        if self.clip_range.isEnabled() and self.vm.active_cine_index >= 0:
            self.workspace_clip_ranges[self.vm.active_cine_index] = (
                self.clip_range.lowerValue(), self.clip_range.upperValue()
            )
        if self.auto.isVisible():
            self.auto.hide()
        self.graph = self.workspace_panes[index].graph
        self._set_active_pane_style(index)
        self.vm.activate_cine_cb(index)
        self.status_bar.setText(f'Active Cine C{index + 1}: {os.path.basename(self.vm.cine_path)}')

    def _render_workspace_preview(self, index):
        frame_data = self.vm.workspace_frame(index)
        if frame_data is None:
            return
        img, bpp, cfa = frame_data
        self._draw_image_to_graph(self.workspace_panes[index].graph, img, bpp, cfa)

    def refresh_workspace_previews(self):
        for index in range(len(self.vm.workspace_contexts)):
            if index != self.active_pane_index:
                self._render_workspace_preview(index)

    def on_workspace_changed(self, summaries, active_index):
        count = len(summaries)
        if count == 0:
            return
        self._configure_workspace_grid(count)
        valid_indices = {summary['index'] for summary in summaries}
        self.workspace_clip_ranges = {
            index: bounds for index, bounds in self.workspace_clip_ranges.items()
            if index in valid_indices
        }
        self.workspace_count_label.setText(f'{count} Cine' + ('s' if count != 1 else ''))
        for summary in summaries:
            index = summary['index']
            self.workspace_clip_ranges.setdefault(index, (0, max(0, summary['frame_count'] - 1)))
            name = os.path.basename(summary['path'])
            self.workspace_panes[index].title.setText(f'C{index + 1} · {name}')
            self.workspace_panes[index].setToolTip(summary['path'])
        self.graph = self.workspace_panes[active_index].graph
        self._set_active_pane_style(active_index)
        if self.vm.active_cine_index >= 0:
            QTimer.singleShot(0, self.refresh_workspace_previews)

    def combined_track_data(self):
        if len(self.vm.workspace_contexts) <= 1:
            return self.vm.track_data
        self.vm.sync_active_workspace_context()
        combined = {}
        for cine_index, context in enumerate(self.vm.workspace_contexts):
            cine_name = os.path.splitext(os.path.basename(context['cine_path']))[0]
            for object_id, track in context.get('track_data', {}).items():
                plotted_track = dict(track)
                plotted_track['name'] = f'C{cine_index + 1} · {cine_name} · {track["name"]}'
                plotted_track['_cine_index'] = cine_index
                plotted_track['_cine_cal'] = context.get('cal', self.vm.cal)
                plotted_track['frame_ts'] = track.get('frame_ts_trig', track.get('frame_ts', []))
                combined[(cine_index, object_id)] = plotted_track
        return combined

    def initialize_theme(self):
        self._base_stylesheet = QApplication.instance().styleSheet()
        accent = self.vm.config.get('ui_accent_color') or '#f47efa'
        self.apply_theme_color(QColor(accent), persist=False)

    def _choose_theme_color(self):
        color = QColorDialog.getColor(QColor(self.theme_accent), self, 'Choose interface accent color')
        if color.isValid():
            self.apply_theme_color(color)

    def apply_theme_color(self, color, persist=True):
        color = QColor(color)
        if not color.isValid():
            return
        self.theme_accent = color.name()
        if not self._base_stylesheet:
            self._base_stylesheet = QApplication.instance().styleSheet()
        replacements = {
            '#f47efa': color.name(),
            '#9a4e9e': color.darker(135).name(),
            '#a460a8': color.lighter(118).name(),
            '#8b468e': color.darker(155).name(),
            '#512953': color.darker(230).name(),
            '#5e425f': color.darker(190).name(),
            '#3f2c40': color.darker(300).name(),
            '#332834': color.darker(360).name(),
        }
        themed = self._base_stylesheet
        for source, replacement in replacements.items():
            themed = re.sub(re.escape(source), replacement, themed, flags=re.IGNORECASE)
        QApplication.instance().setStyleSheet(themed)
        self.theme_button.setStyleSheet(f'QToolButton {{ border: 2px solid {color.name()}; border-radius: 5px; }}')
        self._set_active_pane_style(self.active_pane_index)
        if persist and self.vm is not None:
            self.vm.config.set('ui_accent_color', color.name())

    def _shrink_string(self, string, label):
        seg = int(label.width()/13)
        if len(string) > label.width()/6:
            left, right = string[:seg], string[len(string) - seg:]
            string = f'{left}...{right}'
        return string

    def _set_metadata_table(self, data):
        for i, d in enumerate(data):
            self.metadata_table.setItem(i, 0, QTableWidgetItem(d[0]))
            self.metadata_table.setItem(i, 1, QTableWidgetItem(d[1]))
        sz = self.metadata_table.rowHeight(0) * len(data)
        self.metadata_table.setFixedHeight(sz)
        self.metadata_table.setVisible(True)

    def _on_contrast_changed(self, val):
        self.contrast_slider.setToolTip(f'{val/100:.2f}')
        self.vm.image_contrast = val/100

    def _on_gamma_changed(self, val):
        self.gamma_slider.setToolTip(f'{val/100:.2f}')
        self.vm.image_gamma = val/100

    def _on_brightness_changed(self, val):
        self.bright_slider.setToolTip(f'{val}')
        self.vm.image_brightness = val

    def _on_toggle_ftone(self, checked):
        self.vm.toggle_ftone_cb(checked)
        if checked:
            self.toggle_ftone.setToolTip('F-Tone enabled')
        else:
            self.toggle_ftone.setToolTip('F-Tone disabled')

    def _on_playback_speed_changed(self, val):
        self.playback_speed.setToolTip(f'Playback speed: {val}x')

    def _playback_bounds(self):
        if self.current_tool == self.viewer_tool and self.clip_range.isEnabled():
            return self.clip_range.lowerValue(), self.clip_range.upperValue()
        return self.frame_slider.minimum(), self.frame_slider.maximum()

    def _toggle_playback(self, playing):
        if not playing:
            self._stop_playback()
            return
        self._start_playback(1)

    def _toggle_viewer_playback(self, playing):
        if not playing:
            self._stop_playback()
            return
        self._start_playback(1)

    def _start_playback(self, frame_step):
        first_frame, last_frame = self._playback_bounds()
        frame_step = int(frame_step)
        if not self.cine_path or last_frame <= first_frame or frame_step == 0:
            self._stop_playback()
            return

        current_frame = self.frame_slider.value()
        if frame_step > 0 and (current_frame < first_frame or current_frame >= last_frame):
            self.frame_slider.setValue(first_frame)
        elif frame_step < 0 and (current_frame > last_frame or current_frame <= first_frame):
            self.frame_slider.setValue(last_frame)

        self._playback_step = frame_step
        self._sync_playback_controls(True)
        self.playback_timer.start()

    def _sync_playback_controls(self, playing):
        target_button = self.pause_button
        if playing:
            target_button = next(
                (button for button, step in self.viewer_speed_buttons.items()
                 if step == self._playback_step),
                self.forward_play_button,
            )
        for button in (self.reverse_play_button, self.pause_button, self.forward_play_button,
                       self.reverse_faster_button, self.forward_faster_button):
            button.blockSignals(True)
            button.setChecked(button is target_button)
            button.blockSignals(False)

    def _stop_playback(self):
        if hasattr(self, 'playback_timer'):
            self.playback_timer.stop()
        self._sync_playback_controls(False)

    def _pause_playback(self):
        self._stop_playback()

    def _toggle_transport_play_pause(self):
        if self.playback_timer.isActive():
            self._pause_playback()
        else:
            self._start_playback(1)

    def _advance_playback(self):
        current_frame = self.frame_slider.value()
        first_frame, last_frame = self._playback_bounds()
        next_frame = max(first_frame, min(current_frame + self._playback_step, last_frame))
        self.frame_slider.setValue(next_frame)
        if (self._playback_step > 0 and next_frame >= last_frame) or (
            self._playback_step < 0 and next_frame <= first_frame
        ):
            self._stop_playback()

    def _seek_by(self, frame_delta):
        if not self.cine_path:
            return
        self._stop_playback()
        first_frame, last_frame = self._playback_bounds()
        next_frame = max(first_frame, min(self.frame_slider.value() + int(frame_delta), last_frame))
        self.frame_slider.setValue(next_frame)

    def _step_one_frame(self, frame_delta):
        self._pause_playback()
        self._seek_by(-1 if frame_delta < 0 else 1)

    def _jump_to_clip_start(self):
        if self.cine_path:
            self._stop_playback()
            self.frame_slider.setValue(self._playback_bounds()[0])

    def _jump_to_clip_end(self):
        if self.cine_path:
            self._stop_playback()
            self.frame_slider.setValue(self._playback_bounds()[1])

    def _set_clip_in(self):
        if self.clip_range.isEnabled():
            self.clip_range.setValues(
                min(self.frame_slider.value(), self.clip_range.upperValue()),
                self.clip_range.upperValue()
            )

    def _set_clip_out(self):
        if self.clip_range.isEnabled():
            self.clip_range.setValues(
                self.clip_range.lowerValue(),
                max(self.frame_slider.value(), self.clip_range.lowerValue())
            )

    def _reset_clip_range(self):
        if self.clip_range.isEnabled():
            self.clip_range.setValues(self.frame_slider.minimum(), self.frame_slider.maximum())

    def _on_clip_range_changed(self, lower, upper):
        if self.vm is not None and self.vm.active_cine_index >= 0:
            self.workspace_clip_ranges[self.vm.active_cine_index] = (lower, upper)
        self.clip_start_label.setText(f'In: {lower + int(self.first_fr)}')
        self.clip_end_label.setText(f'Out: {upper + int(self.first_fr)}')
        if self.current_tool == self.viewer_tool:
            if self.frame_slider.value() < lower:
                self.frame_slider.setValue(lower)
            elif self.frame_slider.value() > upper:
                self.frame_slider.setValue(upper)

    def _set_viewer_controls_visible(self, visible):
        visible = bool(visible)
        if visible != (not self.viewer_controls.isHidden()):
            self._stop_playback()
        self.viewer_controls.setVisible(visible)
        self.pcc_transport_controls.setVisible(True)
        self.status_toolbar.setVisible(not visible)
        self.main_tab.setVisible(not visible)
        if visible:
            self.tab_splitter.setSizes([1, 0])
        else:
            QTimer.singleShot(0, self._ensure_details_panel_open)

    def should_prompt_for_tracking_method(self):
        return bool(
            self.current_tool == self.track_tool
            and self.vm is not None
            and self.vm.track_type == 'Auto'
            and self.vm.active_tool is not None
            and getattr(self.vm.active_tool, 'track_select', False)
            and self._hybrid_selection_graph is None
        )

    def choose_tracking_method(self):
        return TrackingMethodDialog.choose(self)

    def create_classic_track_at(self, scene_pos):
        if not (0 <= scene_pos.x() <= self.graph.xMax and 0 <= scene_pos.y() <= self.graph.yMax):
            return
        self.vm.pending_tracking_method = CLASSIC_TRACKING_METHOD
        try:
            self.vm.graph_click_cb((round(scene_pos.x()), round(scene_pos.y())))
        finally:
            self.vm.pending_tracking_method = None
        self.add_object_button.setChecked(False)
        self._sync_hybrid_settings_panel()

    def begin_hybrid_region_selection(self, graph):
        self._hybrid_selection_graph = graph
        self.vm.pending_tracking_method = HYBRID_TRACKING_METHOD
        self.vm.update_status_text.emit(
            'Step 1 of 2: click the exact point that must stay attached to the object.'
        )

    def _on_add_object_toggled(self, checked):
        if not checked and self._hybrid_selection_graph is not None:
            self._hybrid_selection_graph = None
            if self.vm is not None:
                self.vm.pending_tracking_method = None
                self.vm.update_status_text.emit('Hybrid region selection canceled.')

    def is_hybrid_region_selection_active(self, graph):
        return self._hybrid_selection_graph is graph

    @staticmethod
    def _odd_dimension(value, minimum=5):
        value = max(minimum, int(round(value)))
        return value if value % 2 else value + 1

    @staticmethod
    def _rotate_tracking_offset(offset, angle_deg):
        dx, dy = float(offset[0]), float(offset[1])
        radians = math.radians(float(angle_deg))
        cosine = math.cos(radians)
        sine = math.sin(radians)
        return np.array([
            (cosine * dx) + (sine * dy),
            (-sine * dx) + (cosine * dy),
        ], dtype=float)

    def create_hybrid_track_at(self, scene_pos):
        graph = self._hybrid_selection_graph
        if graph is None or not (0 <= scene_pos.x() <= graph.xMax and 0 <= scene_pos.y() <= graph.yMax):
            return

        tracked_point = (round(scene_pos.x()), round(scene_pos.y()))
        self.vm.pending_tracking_method = HYBRID_TRACKING_METHOD
        try:
            self.vm.graph_click_cb(tracked_point)
        finally:
            self.vm.pending_tracking_method = None
        self._hybrid_selection_graph = None
        self.add_object_button.setChecked(False)

        object_id = self.vm.active_object
        if object_id not in self.vm.track_data:
            return
        track = self.vm.track_data[object_id]
        image_width = int(self.vm.cine_handler.metadata.ImWidth)
        image_height = int(self.vm.cine_handler.metadata.ImHeight)
        default_size = self._odd_dimension(
            np.clip(min(image_width, image_height) * 0.08, 31, 101)
        )
        search_multiplier = 3.0
        max_width = image_width - 1
        max_height = image_height - 1
        track.update({
            'tracking_method': HYBRID_TRACKING_METHOD,
            'tpl_rng': (default_size, default_size),
            'search_area': (
                self._odd_dimension(min(max_width, default_size * search_multiplier)),
                self._odd_dimension(min(max_height, default_size * search_multiplier)),
            ),
            'template_angle': 0.0,
            'template_offset': (0.0, 0.0),
            'rotation_allowed': True,
            'edge_threshold': 0.30,
            'smart_frames': True,
            'smart_miss_limit': 3,
            'search_area_multiplier': search_multiplier,
        })
        track['angles'][0] = 0.0
        track['t_angles'][0] = 0.0
        self.main_tab.setCurrentIndex(2)
        self._ensure_details_panel_open()
        self._sync_hybrid_settings_panel()
        self.vm.new_track_data.emit(self.vm.track_data, object_id)
        # Repaint immediately so the image remains visible and the editable
        # reinforcement box appears around the newly selected point.
        self.vm.redraw_cb()
        self.vm.update_status_text.emit(
            'Step 2 of 2: move, resize, and rotate the purple reinforcement box over unique geometry, then process.'
        )

    def finish_hybrid_region_selection(self, graph, rect):
        """Compatibility path for an in-progress drag from an older build."""
        if graph is not self._hybrid_selection_graph:
            return
        if rect.width() < 5 or rect.height() < 5:
            self.vm.update_status_text.emit('Draw a Hybrid tracking box at least 5 × 5 pixels.')
            return
        self.create_hybrid_track_at(rect.center())
        object_id = self.vm.active_object
        track = self.vm.track_data.get(object_id)
        if track is not None:
            track['tpl_rng'] = (
                self._odd_dimension(rect.width()),
                self._odd_dimension(rect.height()),
            )
            self.vm.redraw_cb()

    def commit_hybrid_region(self, item):
        object_id = item.object_id
        if self.vm is None or object_id not in self.vm.track_data:
            return
        track = self.vm.track_data[object_id]
        if track.get('tracking_method') != HYBRID_TRACKING_METHOD:
            return
        rect = item.rect()
        template_size = (
            self._odd_dimension(rect.width()),
            self._odd_dimension(rect.height()),
        )
        center = item.pos()
        center_point = np.array([float(center.x()), float(center.y())])
        angle = ((float(item.rotation()) + 180.0) % 360.0) - 180.0
        anchor_frame = int(track.get('anchor_frame', track['t_frames'][0]))
        point_indices = np.flatnonzero(track['frames'] == anchor_frame)
        template_indices = np.flatnonzero(track['t_frames'] == anchor_frame)
        tracked_point = None
        if point_indices.size:
            index = int(point_indices[0])
            tracked_point = np.asarray(track['points'][index], dtype=float)
            track['angles'][index] = angle
        if template_indices.size:
            index = int(template_indices[0])
            track['t_angles'][index] = angle
            if tracked_point is None:
                tracked_point = np.asarray(track['t_points'][index], dtype=float)
        if tracked_point is None:
            return
        global_offset = center_point - tracked_point
        track['template_offset'] = tuple(
            self._rotate_tracking_offset(global_offset, -angle)
        )
        track['tpl_rng'] = template_size
        track['template_angle'] = angle
        multiplier = float(track.get('search_area_multiplier', 3.0))
        max_width = int(self.vm.cine_handler.metadata.ImWidth) - 1
        max_height = int(self.vm.cine_handler.metadata.ImHeight) - 1
        track['search_area'] = (
            self._odd_dimension(min(max_width, template_size[0] * multiplier)),
            self._odd_dimension(min(max_height, template_size[1] * multiplier)),
        )
        self.vm.new_track_data.emit(self.vm.track_data, object_id)
        # Redraw after the current graphics-item mouse event returns; clearing a
        # scene while one of its transform handles is dispatching is unsafe.
        QTimer.singleShot(0, self.vm.redraw_cb)

    def _set_hybrid_advanced_visible(self, visible):
        self.hybrid_advanced_panel.setVisible(bool(visible))
        self.hybrid_advanced_button.setText('Advanced  ▾' if visible else 'Advanced  ▸')

    def _sync_hybrid_settings_panel(self, *args):
        if self.vm is None or self.vm.active_object not in self.vm.track_data:
            self.hybrid_settings_panel.setVisible(False)
            return
        track = self.vm.track_data[self.vm.active_object]
        is_hybrid = track.get('tracking_method') == HYBRID_TRACKING_METHOD
        self.hybrid_settings_panel.setVisible(is_hybrid and self.vm.track_type == 'Auto')
        if not is_hybrid:
            return
        controls = (
            self.hybrid_match_threshold,
            self.hybrid_smart_frames,
            self.hybrid_rotation_allowed,
            self.hybrid_rotation_range,
            self.hybrid_rotation_step,
            self.hybrid_edge_weight,
            self.hybrid_edge_threshold,
            self.hybrid_search_multiplier,
            self.hybrid_miss_limit,
            self.hybrid_update_template,
        )
        blockers = [QSignalBlocker(control) for control in controls]
        self.hybrid_match_threshold.setValue(float(track.get('acceptable_score', 0.60)))
        self.hybrid_smart_frames.setChecked(bool(track.get('smart_frames', True)))
        self.hybrid_rotation_allowed.setChecked(bool(track.get('rotation_allowed', True)))
        self.hybrid_rotation_range.setValue(float(track.get('rotation_range', 15.0)))
        self.hybrid_rotation_step.setValue(float(track.get('rotation_step', 2.0)))
        self.hybrid_edge_weight.setValue(float(track.get('edge_weight', 0.60)))
        self.hybrid_edge_threshold.setValue(float(track.get('edge_threshold', 0.30)))
        self.hybrid_search_multiplier.setValue(float(track.get('search_area_multiplier', 3.0)))
        self.hybrid_miss_limit.setValue(int(track.get('smart_miss_limit', 3)))
        self.hybrid_update_template.setChecked(bool(track.get('update_template_enable', True)))
        del blockers
        rotation_enabled = self.hybrid_rotation_allowed.isChecked()
        self.hybrid_rotation_range.setEnabled(rotation_enabled)
        self.hybrid_rotation_step.setEnabled(rotation_enabled)

    def _save_hybrid_settings(self, *args):
        if self.vm is None or self.vm.active_object not in self.vm.track_data:
            return
        track = self.vm.track_data[self.vm.active_object]
        if track.get('tracking_method') != HYBRID_TRACKING_METHOD:
            return
        track.update({
            'acceptable_score': self.hybrid_match_threshold.value(),
            'smart_frames': self.hybrid_smart_frames.isChecked(),
            'rotation_allowed': self.hybrid_rotation_allowed.isChecked(),
            'rotation_range': self.hybrid_rotation_range.value(),
            'rotation_step': self.hybrid_rotation_step.value(),
            'edge_weight': self.hybrid_edge_weight.value(),
            'edge_threshold': self.hybrid_edge_threshold.value(),
            'search_area_multiplier': self.hybrid_search_multiplier.value(),
            'smart_miss_limit': self.hybrid_miss_limit.value(),
            'update_template_enable': self.hybrid_update_template.isChecked(),
        })
        self.hybrid_rotation_range.setEnabled(track['rotation_allowed'])
        self.hybrid_rotation_step.setEnabled(track['rotation_allowed'])
        max_width = int(self.vm.cine_handler.metadata.ImWidth) - 1
        max_height = int(self.vm.cine_handler.metadata.ImHeight) - 1
        track['search_area'] = (
            self._odd_dimension(min(max_width, track['tpl_rng'][0] * track['search_area_multiplier'])),
            self._odd_dimension(min(max_height, track['tpl_rng'][1] * track['search_area_multiplier'])),
        )
        self.vm.redraw_cb()

    def _process_active_hybrid(self):
        if self.vm is None or self.vm.active_object not in self.vm.track_data:
            return
        self._save_hybrid_settings()
        self.hybrid_progress_bar.setValue(0)
        self.hybrid_progress_bar.setFormat('Processing Hybrid: %p%')
        self.hybrid_process_button.setEnabled(False)
        self.hybrid_process_button.setText('Processing…')
        self.process_autotrack('Process')

    def _on_hybrid_track_complete(self, *args):
        self.hybrid_progress_bar.setValue(100)
        self.hybrid_progress_bar.setFormat('Hybrid processing complete')
        self.hybrid_process_button.setEnabled(True)
        self.hybrid_process_button.setText('Process Smart Range')
        self._sync_hybrid_settings_panel()

    def _ensure_details_panel_open(self, force=False):
        """Give the analysis tabs a useful default width when they are visible."""
        if self.main_tab.isHidden():
            return
        sizes = self.tab_splitter.sizes()
        total_width = sum(sizes) if len(sizes) == 2 else self.tab_splitter.width()
        total_width = max(total_width, self.tab_splitter.width(), 1)
        minimum_width = self.main_tab.minimumWidth()
        preferred_width = min(320, max(minimum_width, total_width // 4))
        if force or len(sizes) != 2 or sizes[1] < minimum_width:
            self.tab_splitter.setSizes([
                max(1, total_width - preferred_width),
                preferred_width,
            ])

    def _on_zoom_in(self):
        self.zoom_slider.triggerAction(QSlider.SliderAction.SliderSingleStepSub)

    def _on_zoom_out(self):
        self.zoom_slider.triggerAction(QSlider.SliderAction.SliderSingleStepAdd)

    def _clear_track(self):
        self.point_data.clearData()
        self.track_table.clearContents()
        self.track_table.setRowCount(0)
        self.selected_row_track_table = -1
        self.template_img.scene().clear()

    def _set_point_table(self, track_data, selected_id):
        if selected_id in track_data:
            t = track_data[selected_id]
            frs = t['frames']
            pts = t['points']
            scr = t['scores']
            angles = t.get('angles', np.zeros(len(frs), dtype=float))
            notes = t['notes']
            origin_frame = t.get('origin_frame', None)
            self.point_data.update_data(
                frs, pts, scr, notes, origin_frame=origin_frame, angles=angles
            )
        else:
            self.point_data.clearData()
            self.point_table.clearSelection()
    
    def _clear_tool_button_select(self):
        self.on_clear_scene()
        self.analysis_tools_bg.setExclusive(False)
        for button in self.analysis_tools_bg.buttons():
            button.setChecked(False)
        self.analysis_tools_bg.setExclusive(True)
        if self.current_tool == self.track_tool:
            self._tracking_disabled()
        self.current_tool = None
        self._set_viewer_controls_visible(False)

    def _tracking_disabled(self):
        self.track_tab.setEnabled(False)
        self.track_canvas.redraw({})
        self.main_tab.setCurrentIndex(0)

    def _on_change_track_type_cb(self, event):
        self.add_object_button.setChecked(False)
        self._hybrid_selection_graph = None
        if self.vm is not None:
            self.vm.pending_tracking_method = None

    def _open_manual(self):
        base_path = os.path.dirname(os.path.dirname(dir_path))
        man_path = os.path.join(base_path, 'docs', 'Cine Analyzer User Manual - Track and Measure Module.pdf')
        if os.path.exists(man_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(man_path))
        else:
            man_path = os.path.join(base_path,'cineanalyzer', 'docs', 'docs_final', 'Cine Analyzer User Manual - Track and Measure Module.pdf')
            QDesktopServices.openUrl(QUrl.fromLocalFile(man_path))

    def _toggle_enable_autogen_report_name(self):
        self.vm.autogen_report_name = not self.vm.autogen_report_name

    def _open_report_file_picker(self):
        file_path = ''
        if not self.vm.autogen_report_name:
            file_dialog = QFileDialog()
            dir = os.path.dirname(self.cine_path)
            name = ''
            p = os.path.join(dir, name)
            file_path, _ = file_dialog.getSaveFileName(self, "Save File", p, "CSV Files (*.csv)")
            if file_path == '':
                s = 'File picker cancel was pressed.'
                self.vm.update_status_text.emit('')
                raise Exception(s)
        return file_path
            
#endregion
            
#region SIGNAL METHODS
    def showEvent(self, event):
        super().showEvent(event)
        # Start the analysis panel open while still allowing the video to stretch.
        self._ensure_details_panel_open(force=True)

    def on_active_frame_changed(self, value):
        self.vm.active_frame = value

    def on_new_track_data(self, track_data, selected_id):
        # setup track table
        l = len(track_data)
        if l > 0:
            self.track_table.setRowCount(l)
            for i, t_id in enumerate(track_data):
                t = track_data[t_id]
                ID = QTableWidgetItem('')
                ID.setFlags(ID.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                ID.setCheckState(Qt.CheckState.Checked if t['enabled'] == True else Qt.CheckState.Unchecked)
                ID.setFlags(ID.flags() & ~Qt.ItemFlag.ItemIsEditable)
                id_display = f'{t_id}'
                if "relative_to" in t and t["relative_to"] is not None:
                    id_display += f" [{t['relative_to']}]"
                ID.setText(id_display)
                self.track_table.setItem(i, 0, ID) # ID

                color = QColorBox(QColor(self.vm.track_data[t_id]['color']), parent = self.track_table, row = i, col = 2, size=32)
                color.colorChanged.connect(lambda new_color, t_id=t_id: self.on_color_changed(new_color, t_id))
                color.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                color.customContextMenuRequested.connect(
                    lambda pos, row=i, widget=color: self._show_track_context_menu(
                        row, 2, widget.mapToGlobal(pos)
                    )
                )

                self.track_table.setItem(i, 1, QTableWidgetItem(t["name"])) # Name

                self.track_table.setCellWidget(i, 2, color)
            self._set_point_table(track_data, selected_id)

            selected_row = self._track_row_for_object(selected_id)
            if selected_row is not None and selected_row != self.selected_row_track_table:
                self.track_table.selectRow(selected_row)
                self.selected_row_track_table = selected_row
        else:
            self._clear_track()
    
    def on_new_cine_load(self, cine_path, metadata):
        self._stop_playback()
        self.cine_path = cine_path
        self.first_fr = metadata.FirstImageNo
        img_ct = metadata.ImageCount - 1
        self.last_fr = metadata.FirstImageNo + img_ct
        self.tool_clicked()
        pane_prefix = f'C{self.vm.active_cine_index + 1} · ' if len(self.vm.workspace_contexts) > 1 else ''
        self.cine_path_disp.setText(self._shrink_string(pane_prefix + cine_path, self.cine_path_disp)) 
        self.frame_slider.setMaximum(img_ct)
        self.frame_slider.setRange(0, img_ct)
        for button in self.transport_buttons:
            button.setEnabled(img_ct > 0)
        self.clip_range.setEnabled(img_ct > 0)
        self.clip_range.setRange(0, img_ct)
        clip_lower, clip_upper = self.workspace_clip_ranges.get(
            self.vm.active_cine_index, (0, img_ct)
        )
        self.clip_range.setValues(
            max(0, min(clip_lower, img_ct)), max(0, min(clip_upper, img_ct))
        )
        for button in (
            self.jump_start_button, self.jump_end_button,
            self.mark_in_button, self.mark_out_button,
            self.reset_clip_button
        ):
            button.setEnabled(img_ct > 0)
        self.analysis_tools_bg.setExclusive(False)
        for button in self.analysis_tools_bg.buttons():
            button.setChecked(False)
        self.analysis_tools_bg.setExclusive(True)
        self.current_tool = None
        self.viewer_tool.setChecked(True)
        self.tool_clicked()
        b_rng = int(2**metadata.RealBPP - 1)
        if metadata.CFA != 'Mono': b_rng = 255
        self.bright_slider.setRange(-b_rng, b_rng)
        md_data_list = self.vm.cine_handler.print_metadata()         
        self.metadata_table.setRowCount(len(md_data_list))
        self._set_metadata_table(md_data_list)

        #setting sliders without triggering signals
        self.bright_slider.blockSignals(True)
        self.contrast_slider.blockSignals(True)
        self.gamma_slider.blockSignals(True)
        self.bright_slider.setValue(self.vm.image_brightness)
        self.contrast_slider.setValue(self.vm.image_contrast * 100)
        self.gamma_slider.setValue(self.vm.image_gamma * 100)
        self.bright_slider.blockSignals(False)
        self.gamma_slider.blockSignals(False)
        self.contrast_slider.blockSignals(False)

        self._on_contrast_changed(self.contrast_slider.value())
        self._on_gamma_changed(self.gamma_slider.value())
        self._on_brightness_changed(self.bright_slider.value())
        self.raw_adj_button.setChecked(False)
        self.toggle_ftone.setChecked(True)

        self.auto.set_frame_range(self.first_fr, self.last_fr)
        x = int(self.vm.cine_handler.get_img().shape[1])
        y = int(self.vm.cine_handler.get_img().shape[0])

        self.auto.set_roi_ranges((x,y))

    def _graph_widget(self, graph_name):
        if graph_name == 'main_graph':
            return self.graph
        return self.findChild(QWidget, graph_name)

    def _draw_image_to_graph(self, graph, img, bpp, cfa, current_point=None):
        if graph is None or img is None:
            return
        graph.scene().clear()
        is_main_graph = graph in [pane.graph for pane in self.workspace_panes]
        if not is_main_graph and current_point is not None and current_point[0] is not None and current_point[1] is not None:
            zoom_size = int(MINIGRAPH_SIZE / self.zoom_levels[self.zoom_slider.value()])
            current_point = (int(current_point[0]), int(current_point[1]))
            border_sz = zoom_size // 4
            graph_size = (MINIGRAPH_SIZE, MINIGRAPH_SIZE)

            img_border = cv2.copyMakeBorder(img, border_sz, border_sz, border_sz, border_sz, cv2.BORDER_CONSTANT, None, value=[0,0,0])
            img = img_border[current_point[1]: current_point[1] + zoom_size//2, current_point[0]: current_point[0] + zoom_size//2]
            if img.size != 0:
                img = cv2.resize(img, graph_size, interpolation=cv2.INTER_CUBIC)

        if cfa == 'Mono' or self.vm.raw_enabled:
            fmt = QImage.Format.Format_Grayscale16
            bytes_per_line = 2 * img.shape[1]
        else:
            fmt = QImage.Format.Format_RGB888
            bytes_per_line = 3 * img.shape[1]

        img = np.copy(img)
        q_img = QImage(img, img.shape[1], img.shape[0], bytes_per_line, fmt)
        pixmap = QPixmap.fromImage(q_img)
        graph.scene().addPixmap(pixmap)
        if current_point is None:
            graph.scene().setSceneRect(QRectF(pixmap.rect()))
            if getattr(graph, '_view_zoom', 1.0) <= 1.0:
                if hasattr(graph, 'reset_view_zoom'):
                    graph.reset_view_zoom()
                else:
                    graph.fitInView(graph.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            graph.xMax = pixmap.rect().width()
            graph.yMax = pixmap.rect().height()

        if graph is self.magnifier:
            graph.scene().addLine(0, MINIGRAPH_SIZE/2, MINIGRAPH_SIZE, MINIGRAPH_SIZE/2, pen=QPen(QColor('springgreen')))
            graph.scene().addLine(MINIGRAPH_SIZE/2, 0, MINIGRAPH_SIZE/2, MINIGRAPH_SIZE, pen=QPen(QColor('springgreen')))

    def on_draw_frame(self, graph_name, img, bpp, cfa, current_point=None):
        # graph name can be: main_graph, qt_template_img, magnifier
        graph = self._graph_widget(graph_name)
        self._draw_image_to_graph(graph, img, bpp, cfa, current_point)
            
    def on_draw_points(self, graph_name, points, color='red'):
        graph = self._graph_widget(graph_name)
        for pt in points:
            dot_rad = math.sqrt((graph.xMax * graph.yMax)/(math.pi*10000))
            graph.scene().addEllipse(pt[0] - dot_rad, pt[1] - dot_rad, dot_rad*2, dot_rad*2, pen=QPen(QColor(color)), brush=QBrush(QColor(color)))
    
    def on_clear_scene(self):
        graph = self.graph
        #only delete lines and points, not the image
        for item in graph.scene().items():
            if isinstance(item, QGraphicsLineItem):
                graph.scene().removeItem(item)
            elif isinstance(item, QGraphicsEllipseItem):
                graph.scene().removeItem(item)
            elif isinstance(item, QGraphicsRectItem):
                graph.scene().removeItem(item)

    def on_color_changed(self, new_color, t_id):
        self.vm.track_data[t_id]['color'] = new_color.name() # hex code
        self.vm.redraw_cb()
        self.vm.track_fig_changed.emit(self.vm.track_data)


    def on_draw_lines(self, graph_name, points, point_target, connect_pairs, connect_box, connect_end, color='red'):
        graph = self._graph_widget(graph_name)
        dot_rad = max(1, math.sqrt((graph.xMax * graph.yMax)/(math.pi*10000)))
        # line_width = max(1, dot_rad // 2.5)
        line_width = 1
        pen = QPen(QColor(color), line_width)
        pen.setCosmetic(True)

        if (connect_pairs and (len(points) == 4)):
              graph.scene().addLine(points[0][0], points[0][1], points[1][0], points[1][1], pen=pen)
              graph.scene().addLine(points[2][0], points[2][1], points[3][0], points[3][1], pen=pen)

        elif (connect_box and len(points) == 2):
              graph.scene().addEllipse(points[1][0]-dot_rad, points[0][1]-dot_rad, dot_rad*2, dot_rad*2, pen=pen, brush=QBrush(QColor(color)))
              graph.scene().addEllipse(points[0][0]-dot_rad, points[1][1]-dot_rad, dot_rad*2, dot_rad*2, pen=pen, brush=QBrush(QColor(color)))
              graph.scene().addLine(points[0][0], points[0][1], points[1][0], points[0][1], pen=pen)
              graph.scene().addLine(points[0][0], points[0][1], points[0][0], points[1][1], pen=pen)
              graph.scene().addLine(points[0][0], points[1][1], points[1][0], points[1][1], pen=pen)
              graph.scene().addLine(points[1][0], points[0][1], points[1][0], points[1][1], pen=pen)

        elif (connect_pairs and len(points) == 3):
            graph.scene().addLine(points[0][0], points[0][1], points[1][0], points[1][1], pen=pen)
        else:
            for i, p in enumerate(points):
                if i > 0:
                    graph.scene().addLine(points[i-1][0], points[i-1][1], points[i][0], points[i][1], pen=pen)
                if (connect_end and (point_target - 1 == i)):
                    graph.scene().addLine(points[0][0], points[0][1], points[i][0], points[i][1], pen=pen)
    
    def on_draw_text_label(self, graph_name, points, color='red'):
        graph = self._graph_widget(graph_name)
        for i, pt in enumerate(points):
            item = graph.scene().addText(str(i))
            item.setPos(pt[0], pt[1])
            item.setDefaultTextColor(QColor(color))

    def on_get_new_scale(self, fnc, arg):
        if self.cine_path:
            ret = ScaleDialog.launch(self)
            if ret:
                known_length = ret[0]
                length_units = ret[1]
                # all done, call the callback
                self._clear_tool_button_select()
                return fnc(arg, known_length, length_units)

    def on_edit_scale(self):
        ret = ScaleEditDialog.launch(self)
        if ret:
            scale = ret[0]
            length_units = ret[1]
            self.vm.edit_scale_cb(scale, length_units)
            
    def on_remove_roi(self, type='template'):
        graph = self._graph_widget(self.vm._graph)
        for name in graph.scene().items():
            if name.data(0) == f'{type}_{self.vm.active_object}':
                graph.scene().removeItem(name)

    def _add_hybrid_edge_preview(self, region_item, track, center, template_size, angle):
        image = self.vm.transformed_img
        if image is None:
            return
        try:
            patch = AutoTrackAlgorithms.extract_oriented_patch(
                image,
                center,
                template_size,
                angle,
            )
            if patch.ndim == 3:
                patch = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY)
            edges = AutoTrackAlgorithms._edge_image(
                patch,
                threshold=track.get('edge_threshold', 0.30),
                dilate=False,
            )
            height, width = edges.shape[:2]
            rgba = np.zeros((height, width, 4), dtype=np.uint8)
            accent = QColor(self.theme_accent)
            edge_mask = edges > 0
            rgba[edge_mask, 0] = accent.red()
            rgba[edge_mask, 1] = accent.green()
            rgba[edge_mask, 2] = accent.blue()
            rgba[edge_mask, 3] = 230
            image_overlay = QImage(
                rgba.data,
                width,
                height,
                rgba.strides[0],
                QImage.Format.Format_RGBA8888,
            ).copy()
            edge_item = QGraphicsPixmapItem(QPixmap.fromImage(image_overlay), region_item)
            edge_item.setPos(-width / 2.0, -height / 2.0)
            edge_item.setZValue(1)
            edge_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            edge_item.setData(0, f'hybrid_edges_{region_item.object_id}')
        except Exception:
            logging.exception('Unable to draw Hybrid edge preview')

    def on_draw_roi(self, dim, t_id, type='template'):
        # get active track object
        t_id = self.vm.active_object if t_id == None else t_id
        if t_id in self.vm.track_data:
            t = self.vm.track_data[t_id]
        
            # if active track object real, check if active frame is in the object list
            frame = None
            f = np.argwhere(t['frames']==self.vm.active_frame)
            if f.size > 0: frame = f[0,0]

            if frame != None:
                # active frame is in the list, draw the rectangle
                graph = self._graph_widget(self.vm._graph)
                color = t['color']
                if type=='search_area': 
                    color = self.sa_color
                pen = QPen(QColor(color), 1)
                pen.setCosmetic(True)
                obj = t['points'][frame]
                angle = float(t.get('template_angle', 0.0))
                if 'angles' in t and frame < len(t['angles']):
                    angle = float(t['angles'][frame])
                reinforcement_center = np.asarray(obj, dtype=float)
                if t.get('tracking_method') == HYBRID_TRACKING_METHOD:
                    reinforcement_center = reinforcement_center + self._rotate_tracking_offset(
                        t.get('template_offset', (0.0, 0.0)), angle
                    )

                # get dim in graph domain
                roi_center = (
                    reinforcement_center
                    if t.get('tracking_method') == HYBRID_TRACKING_METHOD
                    else obj
                )
                x_start = roi_center[0] - math.floor(dim[0]/2)
                y_start = roi_center[1] - math.floor(dim[1]/2)
                w = dim[0]
                h = dim [1]
                
                if type == 'template' and t.get('tracking_method') == HYBRID_TRACKING_METHOD:
                    anchor_frame = int(t.get('anchor_frame', t['frames'][0]))
                    editable = bool(
                        t_id == self.vm.active_object
                        and self.vm.active_frame == anchor_frame
                    )
                    box = HybridRegionItem(
                        QRectF(0, 0, w, h),
                        reinforcement_center,
                        angle,
                        t_id,
                        self,
                        editable=editable,
                    )
                    box.setData(0, f'{type}_{t_id}')
                    graph.scene().addItem(box)
                    self._add_hybrid_edge_preview(
                        box, t, reinforcement_center, (w, h), angle
                    )
                else:
                    # Original PCA rectangle rendering used by Classic tracking.
                    box = QGraphicsRectItem(QRectF(x_start, y_start, w, h))
                    box.setPen(pen)
                    box.setData(0,f'{type}_{t_id}')
                    graph.scene().addItem(box)
        else:
            logging.warning(f"Invalid track ID: {t_id}")
            return
        
    def on_update_status_text(self, s):
        new_str = re.sub(r'\n', r'; ', s)
        self.status_bar.setText(new_str)

    def _track_object_id_for_row(self, row):
        keys = list(self.vm.track_data.keys())
        return keys[row] if 0 <= row < len(keys) else None

    def _track_row_for_object(self, object_id):
        keys = list(self.vm.track_data.keys())
        try:
            return keys.index(object_id)
        except ValueError:
            return None

    def _activate_track_row(self, row):
        object_id = self._track_object_id_for_row(row)
        if object_id is None:
            return None
        self.vm.active_object = object_id
        self.track_table.selectRow(row)
        self.selected_row_track_table = row
        self._set_point_table(self.vm.track_data, object_id)
        return object_id

    def on_track_table_click(self, row):
        if self._activate_track_row(row) is not None:
            self.vm.redraw_cb()

    def on_point_table_changed(self, selection):
        if selection.indexes() is not []:
            point_fr = self.point_data.data(selection.indexes()[0])
            if point_fr is not None:
                fr = int(point_fr) - int(self.first_fr)
                self.frame_slider.setValue(fr)
            
    def update_point_element(self, index, value):
        valid = True
        if index.column() == 1:
            try:
                point = value.strip('()').split(',')
                if len(point) != 2:
                    raise ValueError("Invalid point format")
                r, c = float(point[0]), float(point[1])
                if r < 0 or c < 0 or r > self.vm.cine_handler.get_img().shape[1] or c > self.vm.cine_handler.get_img().shape[0]:
                    raise ValueError("Point out of bounds")
                self.vm.update_point_element_cb((r, c), 'points', index.row())
            except ValueError as e:
                logging.exception(e)
                valid = False

        elif index.column() == 0:
            try:
                frameno = int(value)
                if frameno < self.first_fr or frameno > self.last_fr:
                    raise ValueError("Frame out of bounds")
                self.vm.update_point_element_cb(frameno - self.first_fr, 'frames', index.row())
            except ValueError as e:
                logging.exception(e)
                valid = False

        return valid

    def edit_track_element(self, row, col):
        if col == 1:
            self.track_table.cellChanged.connect(self.update_track_element)
            item = self.track_table.item(row, col)
            self.track_table.editItem(item)

    def set_relative_to(self, row, col):
        # Prompt user
        self.vm.update_status_text.emit("Click another object row to set as 'Relative To' or click the same object to clear.")
        self._setting_relative_to_row = row

        def on_select_relative_to(selected_row, selected_col):
            try:
                keys = list(self.vm.track_data.keys())
                current_obj_id = keys[self._setting_relative_to_row] if 0 <= self._setting_relative_to_row < len(keys) else None
                selected_obj_id = keys[selected_row] if 0 <= selected_row < len(keys) else None

                # Cancel if clicked on same row or invalid
                if selected_obj_id is None:
                    self.vm.update_status_text.emit("")
                elif selected_row == self._setting_relative_to_row:
                    self.vm.track_data[current_obj_id]['relative_to'] = None
                    self.vm.update_status_text.emit(f"Object {current_obj_id} ({self.vm.track_data[current_obj_id]['name']}) relation cleared.")
                    self.vm.track_fig_calculations(obj=self.vm.track_data[current_obj_id])
                    self.vm.new_track_data.emit(self.vm.track_data, current_obj_id)
                else:
                    # Check for circular reference
                    selected_obj = self.vm.track_data[selected_obj_id]
                    if selected_obj['relative_to'] == current_obj_id:
                        self.vm.update_status_text.emit(
                            "Warning: Objects cannot be set as each other's relative."
                        )
                    else:
                        self.vm.track_data[current_obj_id]['relative_to'] = selected_obj_id
                        vals_ok = np.isin(self.vm.track_data[current_obj_id]['frames'], self.vm.track_data[selected_obj_id]['frames']).all()
                        warning_text = "" if vals_ok else f"Warning: not all points, frames are found in the selected object, {self.vm.track_data[selected_obj_id]['name']}."
                        self.vm.update_status_text.emit(f"Object {current_obj_id} ({self.vm.track_data[current_obj_id]['name']}) set relative to object {selected_obj_id} ({self.vm.track_data[selected_obj_id]['name']}). {warning_text}")
                        self.vm.track_fig_calculations(obj=self.vm.track_data[current_obj_id])
                        self.vm.new_track_data.emit(self.vm.track_data, current_obj_id)
                        self.vm.track_fig_calculations(obj=self.vm.track_data[selected_obj_id])
                        self.vm.new_track_data.emit(self.vm.track_data, selected_obj_id)

            except Exception as e:
                self.vm.update_status_text.emit("Canceled: Invalid selection.")
            finally:
                self.track_table.cellClicked.disconnect(on_select_relative_to)
                if hasattr(self, "_setting_relative_to_row"):
                    del self._setting_relative_to_row

        # Connect for one-time use
        self.track_table.cellClicked.connect(on_select_relative_to)

    def update_track_element(self, row, col):
        if col == 1:
            item = self.track_table.item(row, col)
            name = item.text()
            max = 12
            if len(name) > max:
                name = name[:max]
            self.vm.update_object_name_cb(name, row)
            self.track_table.cellChanged.disconnect(self.update_track_element)
            self.on_new_track_data(self.vm.track_data, self.vm.active_object)
    
    def update_overlays(self, row ,col):
        if col == 0:
            item = self.track_table.item(row, col)
            keys = list(self.vm.track_data.keys())
            if 0 <= row < len(keys):  # Ensure the row index is within bounds
                t = self.vm.track_data[keys[row]]
                t['enabled'] = item.checkState() == Qt.CheckState.Checked
                self.vm.redraw_cb()
                self.track_canvas.redraw(self.vm.track_data)
            else:
                logging.warning(f"Invalid row index: {row}. Unable to update overlays.")
               
    def remove_track_data_points(self, rows, mode):
        logging.info(f'Removing track data points - mode: {mode}')
        # Remove Selected Point(s)
        if self.vm.active_object is not None:
            t = self.vm.track_data[self.vm.active_object]
        else:
            logging.warning("No active object selected in track data.")
            return
        start_idx = 0
        if 'point' in mode:
            if len(rows) == len(t['frames']):
                del self.vm.track_data[self.vm.active_object]
                self.refresh_track_keys()
            else:
                inds = list(rows)
                t_mask = np.isin(t['t_frames'], t['frames'][inds])
                if mode == 'points':
                    t_mask = ~t_mask
                    start_idx = min(rows)
                self.remove_points_from_template(inds, t_mask, start_idx)

        # Removing Points Before or After Current Frame
        elif mode == 'before' and rows != 0:
            inds = np.s_[:rows]
            t_mask = t['t_frames'] > rows
            self.remove_points_from_template(inds, t_mask, start_idx)
        elif mode == 'after' and rows < len(t['frames'])-1:
            inds = np.s_[rows+1:]
            t_mask = t['t_frames'] < rows
            start_idx = rows
            self.remove_points_from_template(inds, t_mask, start_idx)

        # Removing All Points or Track Data
        elif mode == 'all' or mode == 'track':
            del self.vm.track_data[self.vm.active_object]
            # Clear 'relative_to' in all other objects if they referenced this one
            for t in self.vm.track_data.values():
                if t.get('relative_to') == self.vm.active_object:
                    t['relative_to'] = None
            self.refresh_track_keys()
        # Removing All Track Data
        elif mode == 'tracks':
            self.vm.track_data.clear()
            self.track_table.clearContents()
            self.track_table.setRowCount(0)
            self.vm.active_object = None 
            self.refresh_track_keys()
        self.track_canvas.redraw(self.vm.track_data)
        self.vm.redraw_cb()

    def remove_points_from_template(self, inds, t_mask, start_idx):
        t = self.vm.track_data[self.vm.active_object]

        t['points'] = np.delete(t['points'], inds, axis=0)
        t['frames'] = np.delete(t['frames'], inds)
        t['scores'] = np.delete(t['scores'], inds)
        if 'angles' in t:
            t['angles'] = np.delete(t['angles'], inds)
        t['t_points'] = t['t_points'][t_mask]
        t['t_frames'] = t['t_frames'][t_mask]
        if 't_angles' in t:
            t['t_angles'] = t['t_angles'][t_mask]
        # If all templates were deleted, use the first point as new template
        if len(t['t_frames'])==0:
            t['t_frames'] = np.array([t['frames'][0]])
            t['t_points'] = np.array([t['points'][0]])
            t['t_angles'] = np.array([t.get('angles', np.array([0.0]))[0]])

        self._set_point_table(self.vm.track_data, self.vm.active_object)
        self.vm.track_fig_calculations()

    def refresh_track_keys(self):
        temp_dict = {}
        keys = list(self.vm.track_data.keys())
        if len(keys) != 0:
            for id in range(len(keys)):
                self.vm.track_data[keys[id]]['id'] = id
                temp_dict[id] = self.vm.track_data[keys[id]]
            self.vm.track_data = temp_dict
            if self.vm.active_object in self.vm.track_data.keys():
                pass
            else:
                self.vm.active_object = list(self.vm.track_data.keys())[0]
        else:
            self.vm.active_object = None
        self.on_new_track_data(self.vm.track_data, self.vm.active_object)

    def set_origin_frame_from_row(self, row):
        if self.vm.active_object is not None:
            t = self.vm.track_data[self.vm.active_object]
            selected_frame = int(t['frames'][row])
            # If already set to this frame, clear it
            if t['origin_frame'] == selected_frame:
                t['origin_frame'] = None
            else:
                t['origin_frame'] = selected_frame
            self._set_point_table(self.vm.track_data, self.vm.active_object)
            self.vm.track_fig_calculations(obj=t)
            self.vm.new_track_data.emit(self.vm.track_data, self.vm.active_object)

    def expand_tracking_graph(self):
        graph_window = TrackingGraphWindow(self)
        graph_window.setObjectName('graph_window')
        graph_window.show()

    def tool_clicked(self):
        clicked_tool = self.analysis_tools_bg.checkedButton()
        if clicked_tool == None:
            self.vm.clear_frame_cb()
            self._tracking_disabled() 
        else:
            if self.cine_path != '':
                if self.current_tool == clicked_tool:
                    self.vm.clear_frame_cb()
                    self._tracking_disabled() 
                    self.analysis_tools_bg.setExclusive(False)
                    clicked_tool.setChecked(False)
                    self.analysis_tools_bg.setExclusive(True)

                elif clicked_tool == self.track_tool:
                    self.vm.track_tool_click_cb()
                    self.main_tab.setCurrentIndex(2)
                    self.track_tab.setEnabled(True)
                    self.track_canvas.redraw(self.vm.track_data) 
                    #self.vm.redraw_cb()
                elif clicked_tool == self.viewer_tool:
                    self.vm.clear_frame_cb()
                    self.main_tab.setCurrentIndex(0)
                    self._tracking_disabled()
                elif clicked_tool == self.two_pt_tool:
                    self.vm.two_pt_click_cb()
                elif clicked_tool == self.three_pt_tool:
                    self.vm.three_pt_click_cb()
                elif clicked_tool == self.two_line_tool:
                    self.vm.two_line_click_cb()
                elif clicked_tool == self.area_tool:
                    self.vm.area_pt_click_cb()      
                elif clicked_tool == self.scale_tool:
                    self.vm.cal_pt_click_cb()  
                
                if self.current_tool == self.track_tool and clicked_tool != self.track_tool:
                    self._tracking_disabled()

                if self.current_tool != self.track_tool and clicked_tool == self.track_tool:
                    # first time clicking track tool, go into add object mode automatically
                    self.add_object_button.click()
            else:
                self.analysis_tools_bg.setExclusive(False)
                clicked_tool.setChecked(False)
                self.analysis_tools_bg.setExclusive(True)
                self._tracking_disabled() 

            self.current_tool = self.analysis_tools_bg.checkedButton()
            self._set_viewer_controls_visible(self.current_tool == self.viewer_tool)

    def launch_autotrack_dialog(self, row, col=0):
        object_id = self._activate_track_row(row)
        if self.vm.track_type != 'Auto' or object_id is None:
            return
        t = self.vm.track_data.get(object_id)
        if not t:
            return
        self.update_autotrack_dialog()
        self.auto.set_start_frame(self.first_fr)
        self.auto.set_end_frame(t['end'])
        self.auto.showNormal()
        self.auto.raise_()
        self.auto.activateWindow()

    def update_autotrack_dialog(self):
        if self.vm.active_object in self.vm.track_data:
            t = self.vm.track_data.get(self.vm.active_object)
            t.setdefault('tracking_method', HYBRID_TRACKING_METHOD)
            t.setdefault('rotation_range', 15.0)
            t.setdefault('rotation_step', 2.0)
            t.setdefault('edge_weight', 0.6)
            t.setdefault('edge_threshold', 0.30)
            t.setdefault('rotation_allowed', True)
            t.setdefault('smart_frames', False)
            t.setdefault('smart_miss_limit', 3)
            t.setdefault('search_area_multiplier', 3.0)
            self.auto.refresh_params(start=t['start'], end=t['end'], tpl_rng=t['tpl_rng'], 
                                    search_area=t['search_area'], subpixel_size=t['subpixel_size'], 
                                    subpixel_interp=t['subpixel_type'], 
                                    frames_enable=t['frames_enable'], 
                                    search_area_enable=t['search_area_enable'], 
                                    tpl_rng_enable=t['tpl_rng_enable'], 
                                    update_template_enable=t['update_template_enable'], 
                                    acceptable_score=t['acceptable_score'], tpl_score=t['tpl_score'], 
                                    name=t['name'], tracking_method=t['tracking_method'],
                                    rotation_range=t['rotation_range'], rotation_step=t['rotation_step'],
                                    edge_weight=t['edge_weight'])
        
    def process_autotrack(self, text):
        if 'Process' in text:
            self.vm.abort_autotrack = False
            logging.info('Starting autotrack auto process...')
            thread = threading.Thread(target=self.vm.autotrack_cb, kwargs={'track_all': 'All' in text})
            thread.start()
        if text == 'Cancel':
            logging.info('Canceling autotrack auto process...')
            self.vm.abort_autotrack = True
            
    def contextMenuEventPointTable(self, event):
        # Variables
        index = self.point_table.indexAt(event)
        rows = set(idx.row() for idx in self.point_table.selectedIndexes())
        if index.isValid():
            multiselect = len(rows) > 1
            row = index.row()
            fr = int(self.point_data.index(row, 0).data())
            jump_frame = QAction('Jump to Frame', self)
            jump_frame.triggered.connect(lambda: self.frame_slider.setValue(fr - self.first_fr))
            # Remove Selected Points
            removeSelected = QAction('Remove Selected Points', self)
            removeSelected.triggered.connect(lambda: self.remove_track_data_points(rows, 'points'))
            # Remove Unselected Points
            removeUnselected = QAction('Remove Unselected Points', self)
            removeUnselected.triggered.connect(lambda: self.remove_track_data_points(rows, 'u_points'))
            removeUnselected.setVisible(multiselect)
            # Remove All Points
            removeAllPoints = QAction('Remove All Points', self)
            removeAllPoints.triggered.connect(lambda: self.remove_track_data_points(rows, 'all'))
            # Remove All Points After Current Frame
            removeAfterPoints = QAction('Remove All Points After Frame' + f' ({fr})' , self)
            removeAfterPoints.triggered.connect(lambda: self.remove_track_data_points(row, 'after'))
            removeAfterPoints.setVisible(not multiselect)
            # Remove All Points Before Current Frame
            removeBeforePoints = QAction('Remove All Points Before Frame' + f' ({fr})', self)
            removeBeforePoints.triggered.connect(lambda: self.remove_track_data_points(row, 'before'))
            removeBeforePoints.setVisible(not multiselect)
            # Add Set as Origin Frame action
            setOrigin = QAction('Set as Origin Frame', self)
            setOrigin.triggered.connect(lambda: self.set_origin_frame_from_row(row))
        
            # Instantiating Menu
            point_menu = QMenu(self.point_table)
            point_menu.addAction(jump_frame)
            point_menu.addSeparator()
            point_menu.addAction(removeSelected)
            point_menu.addAction(removeAllPoints)
            point_menu.addAction(removeAfterPoints)
            point_menu.addAction(removeBeforePoints)
            point_menu.addSeparator()
            point_menu.addAction(setOrigin)
            point_menu.exec(self.point_table.mapToGlobal(event))

    def _show_track_context_menu(self, row, col, global_pos):
        obj_id = self._activate_track_row(row)
        if obj_id is None:
            return
        t = self.vm.track_data.get(obj_id, {})

        removeObject = QAction('Remove Object', self)
        removeObject.triggered.connect(lambda: self.remove_track_data_points(row, 'track'))
        removeAllObjects = QAction('Remove All Objects', self)
        removeAllObjects.triggered.connect(lambda: self.remove_track_data_points(row, 'tracks'))
        editName = QAction('Edit Name', self)
        editName.triggered.connect(lambda: self.edit_track_element(row, col))
        auto_dlg = QAction('Open Autotrack Dialog', self)
        auto_dlg.triggered.connect(lambda: self.launch_autotrack_dialog(row, col))
        auto_dlg.setVisible(self.vm.track_type == 'Auto')

        if 'relative_to' in t and t['relative_to'] is not None:
            rel_to_text = f"Relative to Object {t['relative_to']}"
        else:
            rel_to_text = "Set Relative To..."
        rel_to = QAction(rel_to_text, self)
        rel_to.triggered.connect(lambda: self.set_relative_to(row, col))

        track_menu = QMenu(self.track_table)
        track_menu.addAction(auto_dlg)
        track_menu.addAction(editName)
        track_menu.addSeparator()
        track_menu.addAction(removeObject)
        track_menu.addAction(removeAllObjects)
        track_menu.addSeparator()
        track_menu.addAction(rel_to)
        track_menu.exec(global_pos)

    def contextMenuEventTrackTable(self, event):
        index = self.track_table.indexAt(event)
        if index.isValid():
            self._show_track_context_menu(
                index.row(),
                index.column(),
                self.track_table.viewport().mapToGlobal(event)
            )

    def contextMenuEventExportButton(self, event):
        autogen_report_action = QAction('Autogenerate Report Name', self)
        autogen_report_action.setCheckable(True)
        autogen_report_action.setChecked(self.vm.autogen_report_name)
        autogen_report_action.triggered.connect(lambda: self._toggle_enable_autogen_report_name())

        export_menu = QMenu(self.export_button)
        export_menu.addAction(autogen_report_action)

        export_menu.exec(self.export_button.mapToGlobal(event))

    def contextMenuEventScaleLabel(self, event):
        edit_scale = QAction('Edit Scale', self)
        edit_scale.triggered.connect(self.on_edit_scale)
        
        scale_menu = QMenu(self.scale)
        scale_menu.addAction(edit_scale)

        scale_menu.exec(self.scale.mapToGlobal(event.pos()))

    def contextMenuEventStatusBar(self, event):
        clear_action = QAction('Clear', self)
        clear_action.triggered.connect(lambda: self.status_bar.setText(''))
        copy_action = QAction('Copy All', self)
        copy_action.triggered.connect(lambda: QApplication.clipboard().setText(self.status_bar.text()))

        menu = QMenu(self.status_bar)
        menu.addAction(clear_action)
        menu.addAction(copy_action)
        menu.exec(self.status_bar.mapToGlobal(event))
      
    def mouseMoveEvent(self, event):
        if self.cine_path != '':
            relative_pos = self.graph.mapToScene(event.pos())
            if not self.graph._within_bounds(relative_pos):
                self.graph.label.setText('Coordinates:\nN/A')
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        # clear tool select if esc pressed
        if event.key() == Qt.Key.Key_Escape:
            self._clear_tool_button_select()
            self.setFocus()

        # fine mouse control or point add with ctrl arrow or ctrl enter
        # Support both Ctrl (Windows/Linux) and Command (Mac) as modifier
        ctrl_mod = (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
            or event.modifiers() & Qt.KeyboardModifier.MetaModifier
        )
        arrow_key = event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Enter, Qt.Key.Key_Return)
        if ctrl_mod:
            self.setFocus()
            if event.key() == Qt.Key.Key_Up:
                QCursor.setPos(QCursor.pos().x(), QCursor.pos().y() - 1)
            elif event.key() == Qt.Key.Key_Down:
                QCursor.setPos(QCursor.pos().x(), QCursor.pos().y() + 1)
            elif event.key() == Qt.Key.Key_Left:
                QCursor.setPos(QCursor.pos().x() - 1, QCursor.pos().y())
            elif event.key() == Qt.Key.Key_Right:
                QCursor.setPos(QCursor.pos().x() + 1, QCursor.pos().y())
            elif event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
                self.graph.graph_click(self.graph.mapFromGlobal(QCursor.pos()))
            elif event.key() == Qt.Key.Key_P and self.current_tool == self.track_tool:
                self.auto.process_button.click()
                self.status_bar.setText('Tracking object... Please wait.')
            elif event.key() == Qt.Key.Key_A and self.current_tool == self.track_tool:
                self.auto.process_all_button.click()
                self.status_bar.setText('Tracking all objects... Please wait.')

        super().keyPressEvent(event)

    def wheelEvent(self, obj):
        if self.cine_path != '' and obj.modifiers().name == 'ControlModifier':
            if obj.angleDelta().y() <= 0:
                self._on_zoom_out()
            else:
                self._on_zoom_in()
        super().wheelEvent(obj)

    def update_active_frame(self, value):
        pad_y = 0
        try:
            self.active_frame_label.setHidden(False)
            frame_num = value + int(self.first_fr)
            if frame_num == int(self.first_fr) or frame_num == int(self.last_fr):
                self.active_frame_label.setText('')
            else:
                self.active_frame_label.setText(str(frame_num)) 
            self.active_frame_label.adjustSize()
            option = QStyleOptionSlider()
            self.frame_slider.initStyleOption(option)
            handle_width = self.frame_slider.style().pixelMetric(
                QStyle.PixelMetric.PM_SliderLength,
                option,
                self.frame_slider,
            )
            travel = max(0, self.frame_slider.width() - handle_width)
            slider_pos = QStyle.sliderPositionFromValue(
                self.frame_slider.minimum(),
                self.frame_slider.maximum(),
                value,
                travel,
                option.upsideDown,
            )
            slider_origin = self.frame_slider.mapTo(self, QPoint(0, 0))
            label_x = slider_origin.x() + (handle_width // 2) + slider_pos
            label_x -= self.active_frame_label.width() // 2
            label_x = max(
                slider_origin.x(),
                min(label_x, slider_origin.x() + self.frame_slider.width() - self.active_frame_label.width()),
            )
            self.active_frame_label.move(
                int(label_x),
                slider_origin.y() + self.frame_slider.height() + pad_y,
            )
        except Exception as e:
            pass

    def resizeEvent(self, event):
        self.update_active_frame(self.frame_slider.value())
        if self.cine_path != '':
            self.cine_path_disp.setText(self._shrink_string(self.cine_path, self.cine_path_disp))
        self.track_canvas.redraw(self.vm.track_data)
        super().resizeEvent(event)

    def raw_image_cb(self):
        enable_state = not self.raw_adj_button.isChecked()
        self.vm.raw_enabled = not enable_state
        self.vm.redraw_cb()
        
        self.bright_slider.setValue(0)
        self.contrast_slider.setValue(100)
        self.gamma_slider.setValue(100)

        self.reset_adj_button.setEnabled(enable_state)
        self.contrast_slider.setEnabled(enable_state)
        self.gamma_slider.setEnabled(enable_state)
        self.bright_slider.setEnabled(enable_state)
        self.toggle_ftone.setEnabled(enable_state)
        
        if enable_state:
            self.vm.reset_image_tools_cb()

    def on_export_report(self):
        if self.vm.report_handler.active_type == '':
            self.vm.update_status_text.emit('No Data to Export')
            return
        if self.vm.report_handler.active_type == 'meas' and self.vm.meas_data == {}:
            self.vm.update_status_text.emit('No Measurement Data to Export')
            return
        if self.vm.report_handler.active_type == 'track' and self.vm.track_data == {}:
            self.vm.update_status_text.emit('No Tracking Data to Export')
            return
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        self.export_button.setEnabled(False)
        
        self.vm.update_status_text.emit(f'Generating {self.vm.report_handler.active_type.capitalize()} Report, please wait...')
        if self.vm.report_handler.active_type == 'track':
            # cache current state
            prior_states = {}
            max_frame = self.frame_slider.value()
            for k, v in self.vm.track_data.items():
                prior_states[k] = {}
                prior_states[k]['enabled'] = v['enabled']
                v['enabled'] = True
                prior_states[k]['search_area_enable'] = v['search_area_enable']
                v['search_area_enable'] = False
                prior_states[k]['tpl_rng_enable'] = v['tpl_rng_enable']
                v['tpl_rng_enable'] = False
                max_frame = max(max_frame, v['frames'][-1])
            self.frame_slider.setValue(max_frame)
            self.vm.redraw_cb() #TODO: do we need this?

            # take screenshot of plot
            scene_rect = self.graph.scene().sceneRect()
            pixmap = QPixmap(scene_rect.size().toSize())
            painter = QPainter(pixmap)
            self.graph.scene().render(painter, QRectF(pixmap.rect()), scene_rect)
            pen = QPen(Qt.GlobalColor.darkGray)
            pen.setWidth(5)
            painter.setPen(pen)
            painter.drawRect(pixmap.rect())
            painter.end()
            temp_dir = os.path.join(tempfile.gettempdir(), 'PCA_report_temp')
            os.makedirs(temp_dir, exist_ok=True)
            pixmap.save(os.path.join(temp_dir, 'graph.png'), 'PNG')
            
            # reset from cache
            for k, v in self.vm.track_data.items():
                v['enabled'] = prior_states[k]['enabled']
                v['search_area_enable'] = prior_states[k]['search_area_enable']
                v['tpl_rng_enable'] = prior_states[k]['tpl_rng_enable']
            self.vm.redraw_cb() #TODO: do we need this?
        try:
            fp = self._open_report_file_picker()
            self.vm.export_cb(fp)
        except: pass
        finally:
            QApplication.restoreOverrideCursor()
            self.export_button.setEnabled(True)

#endregion

#region SIGNAL BINDING
    def connect_events(self):
        # connect the vm callback functions to the button click event
        self.scale_tool.clicked.connect(self.tool_clicked)
        self.viewer_tool.clicked.connect(self.tool_clicked)
        self.track_tool.clicked.connect(self.tool_clicked)
        self.two_pt_tool.clicked.connect(self.tool_clicked)
        self.three_pt_tool.clicked.connect(self.tool_clicked)
        self.two_line_tool.clicked.connect(self.tool_clicked)
        self.area_tool.clicked.connect(self.tool_clicked)
        self.track_type_man.clicked.connect(lambda: self.auto.on_sa_enable_changed(False))
        self.track_type_man.clicked.connect(lambda: self.auto.on_tpl_enable_changed(False))
        self.track_type_auto.clicked.connect(lambda: self.auto.on_sa_enable_changed(True))
        self.track_type_auto.clicked.connect(lambda: self.auto.on_tpl_enable_changed(True))
        self.add_object_button.clicked.connect(self.vm.add_new_template_cb)
        self.add_object_button.toggled.connect(self._on_add_object_toggled)
        self.contrast_slider.valueChanged.connect(self._on_contrast_changed)
        self.contrast_slider.valueChanged.connect(self.vm.redraw_cb)
        self.gamma_slider.valueChanged.connect(self._on_gamma_changed)
        self.gamma_slider.valueChanged.connect(self.vm.redraw_cb)
        self.bright_slider.valueChanged.connect(self._on_brightness_changed)
        self.bright_slider.valueChanged.connect(self.vm.redraw_cb)
        self.reset_adj_button.clicked.connect(self.vm.reset_image_tools_cb)
        self.raw_adj_button.toggled.connect(self.raw_image_cb)
        self.toggle_ftone.toggled.connect(lambda: self._on_toggle_ftone(self.toggle_ftone.isChecked()))
        self.toggle_ftone.toggled.connect(self.vm.redraw_cb)
        self.playback_speed.valueChanged.connect(self.vm.playback_speed_changed_cb)
        self.playback_speed.valueChanged.connect(self._on_playback_speed_changed)
        self.playback_timer.timeout.connect(self._advance_playback)
        self.reverse_play_button.clicked.connect(lambda checked=False: self._start_playback(-1))
        self.pause_button.clicked.connect(lambda checked=False: self._pause_playback())
        self.forward_play_button.clicked.connect(lambda checked=False: self._start_playback(1))
        self.reverse_faster_button.clicked.connect(lambda checked=False: self._start_playback(-4))
        self.previous_frame_button.clicked.connect(lambda checked=False: self._step_one_frame(-1))
        self.next_frame_button.clicked.connect(lambda checked=False: self._step_one_frame(1))
        self.forward_faster_button.clicked.connect(lambda checked=False: self._start_playback(4))
        self.jump_start_button.clicked.connect(self._jump_to_clip_start)
        self.jump_end_button.clicked.connect(self._jump_to_clip_end)
        self.mark_in_button.clicked.connect(self._set_clip_in)
        self.mark_out_button.clicked.connect(self._set_clip_out)
        self.reset_clip_button.clicked.connect(self._reset_clip_range)
        self.clip_range.rangeChanged.connect(self._on_clip_range_changed)
        self.frame_slider.valueChanged.connect(self.on_active_frame_changed)
        self.frame_slider.valueChanged.connect(self.vm.redraw_cb)
        self.frame_slider.valueChanged.connect(self.track_canvas.draw_frame_pos)
        self.zoom_slider.valueChanged.connect(self.vm.zoom_cb)
        self.zoom_in_button.clicked.connect(self._on_zoom_in)
        self.zoom_out_button.clicked.connect(self._on_zoom_out)

        self.track_type_bg.buttonClicked.connect(self.vm.track_type_changed_cb)
        self.track_type_bg.buttonClicked.connect(self._on_change_track_type_cb)
        self.track_type_bg.buttonClicked.connect(self._sync_hybrid_settings_panel)
        self.hybrid_advanced_button.toggled.connect(self._set_hybrid_advanced_visible)
        for control in (
            self.hybrid_match_threshold,
            self.hybrid_rotation_range,
            self.hybrid_rotation_step,
            self.hybrid_edge_weight,
            self.hybrid_edge_threshold,
            self.hybrid_search_multiplier,
        ):
            control.valueChanged.connect(self._save_hybrid_settings)
        self.hybrid_miss_limit.valueChanged.connect(self._save_hybrid_settings)
        for control in (
            self.hybrid_smart_frames,
            self.hybrid_rotation_allowed,
            self.hybrid_update_template,
        ):
            control.toggled.connect(self._save_hybrid_settings)
        self.hybrid_process_button.clicked.connect(self._process_active_hybrid)
        self.track_table.cellDoubleClicked.connect(self.launch_autotrack_dialog)
        self.auto.processRange.connect(self.process_autotrack)
        self.auto.remove_roi.connect(self.on_remove_roi)
        self.auto.draw_roi.connect(self.on_draw_roi)
        self.auto.updateAutoParamValue.connect(self.vm.update_autotrack_params_cb)
        self.auto.tracking_method.currentTextChanged.connect(self._sync_hybrid_settings_panel)
        self.auto.autotrackToStatusText.connect(self.on_update_status_text)
        self.auto.applyButton.clicked.connect(self.vm.apply_params_to_all)
        self.vm.draw_roi.connect(self.on_draw_roi)
        self.vm.new_track_data.connect(self.update_autotrack_dialog)
        self.vm.new_track_data.connect(self._sync_hybrid_settings_panel)
        self.vm.track_complete.connect(self._on_hybrid_track_complete)
        self.vm.update_progress.connect(self.hybrid_progress_bar.setValue)
        self.track_table.cellClicked.connect(self.update_autotrack_dialog)
        self.track_table.cellClicked.connect(self.on_track_table_click)
        self.point_table.selectionModel().selectionChanged.connect(self.on_point_table_changed)
        self.point_table.customContextMenuRequested.connect(self.contextMenuEventPointTable)
        self.track_table.customContextMenuRequested.connect(self.contextMenuEventTrackTable)
        self.export_button.customContextMenuRequested.connect(self.contextMenuEventExportButton)
        self.track_table.cellClicked.connect(self.update_overlays)
        self.export_button.clicked.connect(self.on_export_report)
        self.fig_dropdown.currentIndexChanged.connect(self.vm.track_fig_refresh_cb)
        self.graph_expand_button.clicked.connect(self.expand_tracking_graph)

        decr_frame = QAction("Decrease Frame", self)
        decr_frame.setShortcut('left')
        decr_frame.triggered.connect(lambda: self._step_one_frame(-1))
        incr_frame = QAction("Increase Frame", self)
        incr_frame.setShortcut('right')
        incr_frame.triggered.connect(lambda: self._step_one_frame(1))
        self.addAction(decr_frame)
        self.addAction(incr_frame)

        self.toggle_playback_shortcut = QShortcut(QKeySequence("Space"), self)
        self.toggle_playback_shortcut.activated.connect(self._toggle_transport_play_pause)

        self.jump_start_shortcut = QShortcut(QKeySequence("Home"), self)
        self.jump_start_shortcut.activated.connect(self._jump_to_clip_start)
        self.jump_end_shortcut = QShortcut(QKeySequence("End"), self)
        self.jump_end_shortcut.activated.connect(self._jump_to_clip_end)
        self.rewind_shortcut = QShortcut(QKeySequence("J"), self)
        self.rewind_shortcut.activated.connect(lambda: self._start_playback(-1))
        self.rewind_faster_shortcut = QShortcut(QKeySequence("Shift+J"), self)
        self.rewind_faster_shortcut.activated.connect(lambda: self._start_playback(-4))
        self.fast_forward_shortcut = QShortcut(QKeySequence("L"), self)
        self.fast_forward_shortcut.activated.connect(lambda: self._start_playback(1))
        self.fast_forward_faster_shortcut = QShortcut(QKeySequence("Shift+L"), self)
        self.fast_forward_faster_shortcut.activated.connect(lambda: self._start_playback(4))
        self.mark_in_shortcut = QShortcut(QKeySequence("I"), self)
        self.mark_in_shortcut.activated.connect(
            lambda: self._set_clip_in() if self.current_tool == self.viewer_tool else None
        )
        self.mark_out_shortcut = QShortcut(QKeySequence("O"), self)
        self.mark_out_shortcut.activated.connect(
            lambda: self._set_clip_out() if self.current_tool == self.viewer_tool else None
        )

        viewer_shrtct = QShortcut(QKeySequence("Ctrl+0"), self)
        track_shrtct = QShortcut(QKeySequence("Ctrl+1"), self)
        two_pt_shrtct = QShortcut(QKeySequence("Ctrl+2"), self)
        three_pt_shrtct = QShortcut(QKeySequence("Ctrl+3"), self)
        two_line_shrtct = QShortcut(QKeySequence("Ctrl+4"), self)
        area_shrtct = QShortcut(QKeySequence("Ctrl+5"), self)

        viewer_shrtct.activated.connect(self.viewer_tool.click)
        track_shrtct.activated.connect(self.track_tool.click)
        two_pt_shrtct.activated.connect(self.two_pt_tool.click)
        three_pt_shrtct.activated.connect(self.three_pt_tool.click)
        two_line_shrtct.activated.connect(self.two_line_tool.click)
        area_shrtct.activated.connect(self.area_tool.click)
        
        self.frame_slider.valueChanged.connect(self.update_active_frame)
        self.track_table.selectionModel().selectionChanged.connect(self.vm.track_table_selection_cb)
        
        #SIGNAL REGISTRATIONS
        self.vm.new_track_data.connect(self.on_new_track_data)
        self.vm.new_cine_load.connect(self.on_new_cine_load)
        self.vm.new_cine_load.connect(self.auto.on_new_cine_load)
        self.vm.draw_frame.connect(self.on_draw_frame)
        self.vm.draw_points.connect(self.on_draw_points)
        self.vm.draw_lines.connect(self.on_draw_lines)
        self.vm.draw_text_label.connect(self.on_draw_text_label)
        self.vm.get_new_scale.connect(self.on_get_new_scale)
        self.vm.track_fig_changed.connect(self.track_canvas.redraw)
        self.vm.process_tool_complete.connect(self.auto.process_complete_cb)
        self.vm.update_progress.connect(self.auto.update_progress)
        self.vm.format_progress.connect(self.auto.format_progress)
        self.vm.track_complete.connect(self.auto.on_track_complete)
        self.vm.update_status_text.connect(self.on_update_status_text)
        self.vm.clear_scene.connect(self.on_clear_scene)
        self.vm.active_frame_changed.connect(self.frame_slider.setValue)
        self.vm.brightness_changed.connect(self.bright_slider.setValue)
        self.vm.ftone_changed.connect(self.toggle_ftone.setChecked)
        self.vm.contrast_changed.connect(lambda x: self.contrast_slider.setValue(x*100))
        self.vm.gamma_changed.connect(lambda x: self.gamma_slider.setValue(x*100))
        self.vm.mouse_pos_changed.connect(self.mouse_pos_label.setText)
        self.vm.pixel_val_changed.connect(self.pixel_val.setText)
        self.vm.last_frame_changed.connect(self.last_frame_disp.setText)
        self.vm.first_frame_changed.connect(self.first_frame_disp.setText)
        self.vm.image_scale_changed.connect(self.scale.setText)
        self.vm.trigger_time_changed.connect(lambda x: self.trigger_time.setText(f'Trigger Time: \n {x} s'))
        self.vm.time_from_trigger_changed.connect(lambda x: self.time_from_trigger.setText(f'Time From Trigger: \n {x}'))
        self.vm.request_choose_cine.connect(self._choose_image)
        self.vm.workspace_changed.connect(self.on_workspace_changed)
        
#endregion    
