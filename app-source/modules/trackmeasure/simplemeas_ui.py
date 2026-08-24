import os, logging, re
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas, NavigationToolbar2QT
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from pyphantom_qt.buttons import QIconTextButton, QColorBox, QToggleSwitch

import simplemeas_vm

import cv2
import math
import numpy as np
import threading
import tempfile
import pandas as pd

# constants
MINIGRAPH_SIZE = 160
VERSION = '1.1.0'
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

    def resizeEvent(self, event):
        self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        super().resizeEvent(event)
    
    def mouseMoveEvent(self, event):
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
            self.graph_click(event.pos())

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

class AutoTrackDialog(QDialog):
    processRange = Signal(str)
    remove_roi = Signal(str)
    draw_roi = Signal(object, object, str)
    updateAutoParamValue = Signal(str, object)
    autotrackToStatusText = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__()
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
        advanced_grid_layout.addWidget(self.score_label, 0, 0)
        advanced_grid_layout.addWidget(self.score, 0, 1)
        advanced_grid_layout.addWidget(self.tpl_score_label, 1, 0)
        advanced_grid_layout.addWidget(self.tpl_score, 1, 1)
        advanced_grid_layout.addWidget(self.subpixel_label, 2, 0)
        advanced_grid_layout.addWidget(self.subpixel_size, 2, 1)
        advanced_grid_layout.addWidget(self.subpixel_type_label, 3, 0)
        advanced_grid_layout.addWidget(self.subpixel_interp, 3, 1)

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
                       acceptable_score = 0.8, tpl_score = 0.9, name=''):
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
        if self.subpixel_size.currentText() != '1.0 pix':
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

        if self.mode == 'Vibration':
            if len(temps) > 0:
                self.plot_vibration(val)
                self.ax.set_title(f"{val[0]}-Frequency Spectrum ({temps[self.parent_window.vm.active_object]['name']})", color='white')
            else:
                self.ax.set_title(f"{val[0]}-Frequency Spectrum", color='white')
        else:
            x_units, y_units = self.get_units(self.mode)
            self.ax.set_title(f"{val} ({y_units}) vs Time ({x_units})", color='white')
            if len(temps) > 0:
                for id, t in temps.items():
                    if t['enabled']:
                        if self.mode == 'Speed':
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
                        y_data = self.parent_window.vm.cal.point_transform(t[val])
                        self.ax.plot(frs, y_data, 'D-', ms=4, label=t['name'], color=t['color'])

                        if self.show_annotations:
                            for i, (x, y) in enumerate(zip(frs, y_data)):
                                if self.mode == 'Speed':
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
            ts = float(t[self.parent_window.vm.active_object]['frame_ts'][i])

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
                                    'Acceleration', 'X-Acceleration', 'Y-Acceleration', 'X-Vibration', 'Y-Vibration'])
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
    
    def update_data(self, frames, points, scores, notes, origin_frame=None):
        self.beginResetModel()
        pts = [f'({round(p[0], 1)}, {round(p[1], 1)})' for p in points]
        frs = frames + self.parent().first_fr
        self._data = np.column_stack((frs, pts, scores))
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
                    return "Score"
                elif section == 3:
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

#region MAINWINDOW
class MainWindow(QWidget):
    vm: simplemeas_vm.SimpleMeasVM
    
    def __init__(self):
        self.vm = None
        self.cine_path = ''
        self.first_fr = 0
        self.last_fr = 0
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
        #cleanup cache
        if hasattr(self, 'vm') and hasattr(self.vm, 'cine_handler'):
            self.vm.cine_handler.close()
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

        main_layout = QGridLayout()
        main_layout.addWidget(self.analysis_tools, 0, 0)
        main_layout.addWidget(self.tab_splitter, 0, 2)
        self.setLayout(main_layout)

        self.auto = AutoTrackDialog()
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

        self.track_tool = QIconTextButton("TRACK", os.path.join(dir_path, "images", "target-32.png"), "Manual/Auto tracking", checkable=True, icon_size=QSize(20, 20))
        self.two_pt_tool = QIconTextButton("   2 PT", os.path.join(dir_path, "images", "line-32.png"), "2-point analysis", checkable=True, icon_size=QSize(20, 20))
        self.three_pt_tool = QIconTextButton("   3 PT", os.path.join(dir_path, "images", "angle-32.png"), "3-point analysis", checkable=True, icon_size=QSize(20, 20))
        self.two_line_tool = QIconTextButton(" 2 LINE", os.path.join(dir_path, "images", "2-line-32.png"), "2-line analysis", checkable=True, icon_size=QSize(20, 20))
        self.area_tool = QIconTextButton(" AREA", os.path.join(dir_path, "images", "rect-32.png"), "Area analysis", checkable=True, icon_size=QSize(20, 20))

        self.analysis_tools_bg = QButtonGroup(self.analysis_tools)
        self.analysis_tools_bg.addButton(self.scale_tool)
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
        analysis_tools_layout.addWidget(self.track_tool, 1, 0, 1, -1)
        analysis_tools_layout.addWidget(self.two_pt_tool, 2, 0, 1, -1)
        analysis_tools_layout.addWidget(self.three_pt_tool, 3, 0, 1, -1)
        analysis_tools_layout.addWidget(self.two_line_tool, 4, 0, 1, -1)
        analysis_tools_layout.addWidget(self.area_tool, 5, 0, 1, -1)
        analysis_tools_layout.addWidget(self.export_button, 6, 0, 1, -1)
        analysis_tools_layout.setRowStretch(7, 1)
        analysis_tools_layout.addWidget(user_manual_button, 8, 0)
        
        self.analysis_tools.setLayout(analysis_tools_layout)
        self.analysis_tools.setObjectName("analysis_grid")

    def _create_cine_display_col(self):
        self.cine_disp_col = QWidget()
        cine_disp_layout = QGridLayout()
        self.cine_path_disp = QLabel("No Cine Selected")
        self.cine_path_disp.setObjectName('cine_path_disp')
        cine_upload_button = QPushButton("Choose Cine")
        cine_upload_button.setObjectName('cine_upload_button')
        cine_upload_button.clicked.connect(self._choose_image)
        self.graph = ResizableGraph(self.mouse_pos_label, self)
        self.graph.setObjectName('main_graph')
        self.graph.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
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
        self.first_frame_disp = QLabel('')
        self.first_frame_disp.setObjectName("qt_first_frame")
        self.last_frame_disp = QLabel('')
        self.last_frame_disp.setObjectName("qt_last_frame")
        self.active_frame_label = QLabel("0", self)
        self.active_frame_label.adjustSize()
        self.active_frame_label.setHidden(True)
        self.loading_widget = LoadingWidget()
        self.loading_widget.setObjectName('qt_loading_widget')
        self.loading_msg = LoadingMessage()
        self.loading_msg.setObjectName('qt_loading_msg')
        
        cine_disp_layout.addWidget(cine_upload_button,0,0)
        cine_disp_layout.addWidget(self.cine_path_disp,0,1,1,-1)
        cine_disp_layout.addWidget(self.graph,1,0,1,-1)
        cine_disp_layout.addWidget(self.status_bar,2,0,1,-1)
        cine_disp_layout.rowStretch(2)
        cine_disp_layout.addWidget(self.first_frame_disp,3,0,alignment=Qt.AlignmentFlag.AlignLeft)
        cine_disp_layout.addWidget(self.last_frame_disp,3,2,alignment=Qt.AlignmentFlag.AlignRight)
        cine_disp_layout.rowStretch(3)
        cine_disp_layout.addWidget(self.frame_slider,4,0,1,-1)
        cine_disp_layout.rowStretch(4)
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
        track_layout.addWidget(self.template_img, 1, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        track_layout.addWidget(self.add_object_button, 2, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        track_layout.addWidget(track_table_label, 3, 0)
        track_layout.addWidget(self.track_table, 4, 0)
        track_layout.addWidget(point_table_label, 5, 0)
        track_layout.addWidget(self.point_table, 6, 0)
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
                                    'Acceleration', 'X-Acceleration', 'Y-Acceleration', 'X-Vibration', 'Y-Vibration'])
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
        if len(self.vm.meas_data) > 1 or len(self.vm.track_data) > 0:
            confirmation = QMessageBox.warning(self, "Warning", "Are you sure you want to load a new file? \nThis will erase all current data.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if confirmation == QMessageBox.StandardButton.No:
                return
        if path:
            cine_path = path
        else:
            cine_path, _ = QFileDialog.getOpenFileName(self, "Choose Image", filter='Cine Files (*.cine)')
        
        if cine_path:
            self.loading_msg.show_widget()
            self.load_cine_thread = QThread()
            self.load_cine_worker = WorkerThread(self._load_cine, cine_path)
            self.load_cine_worker.moveToThread(self.load_cine_thread)
            self.load_cine_thread.started.connect(self.load_cine_worker.run)
            self.load_cine_thread.finished.connect(self.loading_msg.hide)
            self.load_cine_thread.start()
            self.load_cine_thread.setPriority(QThread.Priority.TimeCriticalPriority)  # or HighestPriority
            
    def _load_cine(self, cine_path):
        #toggle off the reset and raw buttons and enable sliders when an image is chosen
        self.reset_adj_button.setEnabled(True)
        self.raw_adj_button.setEnabled(True)
        self.vm.toggle_ftone = True
        self.vm.raw_enabled = False

        self.contrast_slider.setEnabled(True)
        self.gamma_slider.setEnabled(True)
        self.bright_slider.setEnabled(True)

        self.vm.load_cine_cb(cine_path)
        self.cine_path_disp.setText(self._shrink_string(cine_path.replace('/','\\'), self.cine_path_disp))

        if self.load_cine_thread is not None: self.load_cine_thread.terminate()

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
            notes = t['notes']
            origin_frame = t.get('origin_frame', None)
            self.point_data.update_data(frs, pts, scr, notes, origin_frame=origin_frame)
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

    def _tracking_disabled(self):
        self.track_tab.setEnabled(False)
        self.track_canvas.redraw({})
        self.main_tab.setCurrentIndex(0)

    def _on_change_track_type_cb(self, event):
        self.add_object_button.setChecked(False)

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
        # Set initial splitter sizes after window is visible
        self.tab_splitter.setSizes([self.width() - 250, 250])

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

                self.track_table.setItem(i, 1, QTableWidgetItem(t["name"])) # Name

                self.track_table.setCellWidget(i, 2, color)
            self._set_point_table(track_data, selected_id)

            if selected_id != self.selected_row_track_table:
                self.track_table.selectRow(selected_id)
                self.selected_row_track_table = selected_id            
        else:
            self._clear_track()
    
    def on_new_cine_load(self, cine_path, metadata):
        self.cine_path = cine_path
        self.first_fr = metadata.FirstImageNo
        img_ct = metadata.ImageCount - 1
        self.last_fr = metadata.FirstImageNo + img_ct
        self.tool_clicked()
        self.cine_path_disp.setText(self._shrink_string(cine_path, self.cine_path_disp)) 
        self.frame_slider.setMaximum(img_ct)
        self.frame_slider.setRange(0, img_ct)
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


    def on_draw_frame(self, graph_name, img, bpp, cfa, current_point=None):
        # graph name can be: main_graph, qt_template_img, magnifier
        graph = self.findChild(QWidget, graph_name)
        graph.scene().clear()
        if graph_name != 'main_graph' and current_point is not None and current_point[0] is not None and current_point[1] is not None: # some of this may be redundant
            # little graphs
            zoom_size = int(MINIGRAPH_SIZE / self.zoom_levels[self.zoom_slider.value()])
            current_point = (int(current_point[0]), int(current_point[1]))
            border_sz = zoom_size // 4
            graph_size = (MINIGRAPH_SIZE, MINIGRAPH_SIZE)
            
            img_border = cv2.copyMakeBorder(img, border_sz, border_sz, border_sz, border_sz, cv2.BORDER_CONSTANT, None, value=[0,0,0])
            img = img_border[current_point[1]: current_point[1] + zoom_size//2, current_point[0]: current_point[0] + zoom_size//2]
            if img.size != 0:
                img = cv2.resize(img, graph_size, interpolation=cv2.INTER_CUBIC) # remove interpolation for zoom?

        if cfa == 'Mono' or self.vm.raw_enabled:
            fmt =  QImage.Format.Format_Grayscale16
            bytes_per_line = 2 * img.shape[1]
        else:
            fmt =  QImage.Format.Format_RGB888
            bytes_per_line = 3 * img.shape[1]

        img = np.copy(img)
        q_img = QImage(img, img.shape[1], img.shape[0], bytes_per_line, fmt) 
        pixmap = QPixmap.fromImage(q_img)
        graph.scene().addPixmap(pixmap)
        #val = np.median(img)
        #self.sa_color = 'black' if val > 128 else 'white'
        if current_point == None:
            graph.scene().setSceneRect(QRectF(pixmap.rect()))
            graph.fitInView(graph.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            graph.xMax = pixmap.rect().width()
            graph.yMax = pixmap.rect().height()

        if graph_name == 'magnifier':
            self.magnifier.scene().addLine(0, MINIGRAPH_SIZE/2, MINIGRAPH_SIZE,MINIGRAPH_SIZE/2, pen=QPen(QColor("springgreen")))
            self.magnifier.scene().addLine(MINIGRAPH_SIZE/2, 0, MINIGRAPH_SIZE/2,MINIGRAPH_SIZE, pen=QPen(QColor("springgreen")))
            
    def on_draw_points(self, graph_name, points, color='red'):
        graph = self.findChild(QWidget, graph_name)
        for pt in points:
            dot_rad = math.sqrt((graph.xMax * graph.yMax)/(math.pi*10000))
            graph.scene().addEllipse(pt[0] - dot_rad, pt[1] - dot_rad, dot_rad*2, dot_rad*2, pen=QPen(QColor(color)), brush=QBrush(QColor(color)))
    
    def on_clear_scene(self):
        graph = self.findChild(QWidget, 'main_graph')
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
        graph = self.findChild(QWidget, graph_name)
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
        graph = self.findChild(QWidget, graph_name)
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
        graph = self.findChild(QWidget, self.vm._graph)
        for name in graph.scene().items():
            if name.data(0) == f'{type}_{self.vm.active_object}':
                graph.scene().removeItem(name)

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
                graph = self.findChild(QWidget, self.vm._graph)
                color = t['color']
                if type=='search_area': 
                    color = self.sa_color
                pen = QPen(QColor(color), 1)
                pen.setCosmetic(True)
                obj = t['points'][frame]

                # get dim in graph domain
                x_start = obj[0] - math.floor(dim[0]/2)
                y_start = obj[1] - math.floor(dim[1]/2)
                w = dim[0]
                h = dim [1]
                
                # do the draw
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

    def on_track_table_click(self, row):
        keys = list(self.vm.track_data.keys())
        self.vm.active_object = keys[row]
        self._set_point_table(self.vm.track_data, self.vm.active_object)
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
        t['t_points'] = t['t_points'][t_mask]
        t['t_frames'] = t['t_frames'][t_mask]
        # If all templates were deleted, use the first point as new template
        if len(t['t_frames'])==0:
            t['t_frames'] = np.array([t['frames'][0]])
            t['t_points'] = np.array([t['points'][0]])

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

    def launch_autotrack_dialog(self, row, col):
        if self.vm.track_type == 'Auto':
            t = self.vm.track_data[self.vm.active_object]
            self.auto.set_start_frame(self.first_fr)
            self.auto.set_end_frame(t['end'])
            self.auto.open()

    def update_autotrack_dialog(self):
        if self.vm.active_object != None:
            t = self.vm.track_data[self.vm.active_object]
            self.auto.refresh_params(start=t['start'], end=t['end'], tpl_rng=t['tpl_rng'], 
                                    search_area=t['search_area'], subpixel_size=t['subpixel_size'], 
                                    subpixel_interp=t['subpixel_type'], 
                                    frames_enable=t['frames_enable'], 
                                    search_area_enable=t['search_area_enable'], 
                                    tpl_rng_enable=t['tpl_rng_enable'], 
                                    update_template_enable=t['update_template_enable'], 
                                    acceptable_score=t['acceptable_score'], tpl_score=t['tpl_score'], 
                                    name=t['name'])
        
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

    def contextMenuEventTrackTable(self, event):
        # Variables
        item = self.track_table.itemAt(event)
        if isinstance(item, QTableWidgetItem):
            row = item.row()
            col = item.column()
            
            # Get the object ID for this row
            keys = list(self.vm.track_data.keys())
            obj_id = keys[row] if 0 <= row < len(keys) else None
            t = self.vm.track_data.get(obj_id, {})
            
            # Remove Single Object
            removeObject = QAction('Remove Object', self)
            removeObject.triggered.connect(lambda: self.remove_track_data_points(row, 'track'))
            # Remove All Objects
            removeAllObjects = QAction('Remove All Objects', self)
            removeAllObjects.triggered.connect(lambda: self.remove_track_data_points(row, 'tracks'))
            # Edit Name
            editName = QAction('Edit Name', self)
            editName.triggered.connect(lambda: self.edit_track_element(row, col))
            # autotrack dialog
            auto_dlg = QAction('Open Autotrack Dialog', self)
            auto_dlg.triggered.connect(lambda: self.launch_autotrack_dialog(row, col))
            auto_dlg.setVisible(self.vm.track_type == 'Auto')
            # set relative to
            if 'relative_to' in t and t['relative_to'] is not None:
                rel_to_text = f"Relative to Object {t['relative_to']}"
            else:
                rel_to_text = "Set Relative To..."
            rel_to = QAction(rel_to_text, self)
            rel_to.triggered.connect(lambda: self.set_relative_to(row, col))

            # Instantiating Menu
            track_menu = QMenu(self.track_table)
            track_menu.addAction(auto_dlg)
            track_menu.addAction(editName)
            track_menu.addSeparator()
            track_menu.addAction(removeObject)
            track_menu.addAction(removeAllObjects)
            track_menu.addSeparator()
            track_menu.addAction(rel_to)
            track_menu.exec(self.track_table.mapToGlobal(event))

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
        pad_x = 15
        pad_y = 0
        try:
            self.active_frame_label.setHidden(False)
            frame_num = value + int(self.first_fr)
            if frame_num == int(self.first_fr) or frame_num == int(self.last_fr):
                self.active_frame_label.setText('')
            else:
                self.active_frame_label.setText(str(frame_num)) 
            self.active_frame_label.adjustSize()
            slider_pos = value / (self.last_fr - self.first_fr)
            new_x = self.frame_slider.x() + int(slider_pos * (self.frame_slider.width() - self.frame_slider.minimumSizeHint().width())) + self.analysis_tools.width() + pad_x
            self.active_frame_label.move(int(new_x), self.frame_slider.y() + self.frame_slider.height() + pad_y)
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
        self.frame_slider.valueChanged.connect(self.on_active_frame_changed)
        self.frame_slider.valueChanged.connect(self.vm.redraw_cb)
        self.frame_slider.valueChanged.connect(self.track_canvas.draw_frame_pos)
        self.zoom_slider.valueChanged.connect(self.vm.zoom_cb)
        self.zoom_in_button.clicked.connect(self._on_zoom_in)
        self.zoom_out_button.clicked.connect(self._on_zoom_out)

        self.track_type_bg.buttonClicked.connect(self.vm.track_type_changed_cb)
        self.track_type_bg.buttonClicked.connect(self._on_change_track_type_cb)
        self.track_table.cellDoubleClicked.connect(self.launch_autotrack_dialog)
        self.auto.processRange.connect(self.process_autotrack)
        self.auto.remove_roi.connect(self.on_remove_roi)
        self.auto.draw_roi.connect(self.on_draw_roi)
        self.auto.updateAutoParamValue.connect(self.vm.update_autotrack_params_cb)
        self.auto.autotrackToStatusText.connect(self.on_update_status_text)
        self.auto.applyButton.clicked.connect(self.vm.apply_params_to_all)
        self.vm.draw_roi.connect(self.on_draw_roi)
        self.vm.new_track_data.connect(self.update_autotrack_dialog)
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
        decr_frame.triggered.connect(self.vm.left_arrow_keypress_cb)
        incr_frame = QAction("Increase Frame", self)
        incr_frame.setShortcut('right')
        incr_frame.triggered.connect(self.vm.right_arrow_keypress_cb)
        self.addAction(decr_frame)
        self.addAction(incr_frame)

        track_shrtct = QShortcut(QKeySequence("Ctrl+1"), self)
        two_pt_shrtct = QShortcut(QKeySequence("Ctrl+2"), self)
        three_pt_shrtct = QShortcut(QKeySequence("Ctrl+3"), self)
        two_line_shrtct = QShortcut(QKeySequence("Ctrl+4"), self)
        area_shrtct = QShortcut(QKeySequence("Ctrl+5"), self)

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
        
#endregion    


