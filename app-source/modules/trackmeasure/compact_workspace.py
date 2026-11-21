"""Compact workspace controls shared by the Windows and Mac applications."""
from copy import deepcopy
import os
import numpy as np
from PySide6.QtCore import QObject, QEvent, Qt, QSignalBlocker, QPointF, QTimer
from PySide6.QtGui import QIcon, QWheelEvent, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QAbstractSpinBox, QAbstractSlider,
    QAbstractScrollArea, QScrollBar, QComboBox, QFrame, QToolButton, QPushButton,
    QMenu, QLabel, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QDoubleSpinBox,
    QSpinBox, QDialog, QDialogButtonBox, QSizePolicy, QMessageBox)


class IntentionalWheelEditing(QObject):
    """Hover scrolls the page; click/tab focus explicitly arms value editing."""
    def _control(self, widget):
        while isinstance(widget, QWidget):
            if isinstance(widget, (QAbstractSpinBox, QComboBox, QAbstractSlider)):
                return None if isinstance(widget, QScrollBar) else widget
            if isinstance(widget, QAbstractScrollArea):
                break
            widget = widget.parentWidget()
        return None

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind not in (QEvent.Polish, QEvent.MouseButtonPress, QEvent.FocusIn,
                        QEvent.FocusOut, QEvent.Wheel):
            return False
        control = self._control(obj)
        if control is None:
            return False
        if kind == QEvent.Polish:
            control.setFocusPolicy(Qt.StrongFocus)
        elif kind == QEvent.MouseButtonPress:
            control.setProperty('wheelEditArmed', True)
        elif kind == QEvent.FocusIn and event.reason() in (Qt.MouseFocusReason, Qt.TabFocusReason, Qt.BacktabFocusReason):
            control.setProperty('wheelEditArmed', True)
        elif kind == QEvent.FocusOut:
            control.setProperty('wheelEditArmed', False)
        elif kind == QEvent.Wheel:
            focused = QApplication.focusWidget()
            if control.property('wheelEditArmed') and (focused is control or (focused and control.isAncestorOf(focused))):
                return False
            ancestor = control.parentWidget()
            while ancestor and not isinstance(ancestor, QAbstractScrollArea):
                ancestor = ancestor.parentWidget()
            if ancestor:
                viewport = ancestor.viewport()
                forwarded = QWheelEvent(QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                    event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                    event.buttons(), event.modifiers(), event.phase(), event.inverted())
                QApplication.sendEvent(viewport, forwarded)
            event.accept()
            return True
        return False


class ReferenceDialog(QDialog):
    def __init__(self, owner, object_id):
        super().__init__(owner, Qt.Tool)
        self.owner = owner
        self.object_id = object_id
        track = owner.vm.track_data[object_id]
        self.track_identity = track
        self.setWindowTitle('Displacement reference · '+track['name'])
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        note = QLabel('Choose the zero for displacement. This does not move the tracking point or change speed.')
        note.setWordWrap(True); layout.addWidget(note)
        form = QFormLayout(); layout.addLayout(form)
        self.mode = QComboBox()
        for label, value in [('First tracked point', 'first'), ('Object at frame', 'frame'), ('Fixed image pixel', 'pixel')]:
            self.mode.addItem(label, value)
        form.addRow('Reference', self.mode)
        self.frame = QSpinBox(); self.frame.setRange(int(owner.first_fr), int(owner.last_fr))
        self.frame.setKeyboardTracking(False)
        self.frame.setValue(int(owner.first_fr)+int(track.get('origin_frame') if track.get('origin_frame') is not None else track['frames'][0]))
        form.addRow('Frame', self.frame)
        self.x = QDoubleSpinBox(); self.y = QDoubleSpinBox()
        metadata = owner.vm.cine_handler.metadata
        for control, maximum in ((self.x, metadata.ImWidth-1), (self.y, metadata.ImHeight-1)):
            control.setRange(0, maximum); control.setDecimals(2); control.setKeyboardTracking(False)
        point = track.get('reference_pixel')
        if point is None: point = track['points'][0]
        self.x.setValue(float(point[0])); self.y.setValue(float(point[1]))
        form.addRow('X pixel', self.x); form.addRow('Y pixel', self.y)
        self.pick = QPushButton('Pick on video'); self.pick.clicked.connect(self._pick)
        layout.addWidget(self.pick)
        self.convention = QLabel('Image coordinates: (0, 0) at top left; X right, Y down.\nThe fixed pixel stays the same in every frame.')
        self.convention.setWordWrap(True); layout.addWidget(self.convention)
        self.error = QLabel(); self.error.setWordWrap(True); layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.close); layout.addWidget(buttons)
        self.mode.setCurrentIndex(2 if track.get('reference_pixel') is not None else 1 if track.get('origin_frame') is not None else 0)
        self.mode.currentIndexChanged.connect(self._visibility); self._visibility()

    def _visibility(self):
        mode = self.mode.currentData()
        self.frame.setEnabled(mode == 'frame')
        for control in (self.x, self.y, self.pick): control.setEnabled(mode == 'pixel')

    def _pick(self):
        self.owner._picking_reference = self
        self.owner.reference_escape.setEnabled(True)
        self.owner._stop_playback(); self.hide()
        self.owner.status_bar.setText('Click a fixed reference pixel in the active Cine. Esc cancels.')

    def picked(self, point):
        self.x.setValue(point.x()); self.y.setValue(point.y()); self.show(); self.raise_()

    def apply(self):
        try:
            if self.owner.vm.track_data.get(self.object_id) is not self.track_identity:
                raise ValueError('Select the original object and Cine before applying this reference.')
            self.owner._apply_tracking_reference(self.object_id, self.mode.currentData(),
                self.frame.value()-int(self.owner.first_fr), (self.x.value(), self.y.value()))
            self.error.setText('Reference applied. Existing measurements and heat maps updated.')
        except ValueError as error:
            self.error.setText(str(error))

    def closeEvent(self, event):
        if getattr(self.owner, '_picking_reference', None) is self:
            self.owner._picking_reference = None
            self.owner.reference_escape.setEnabled(False)
        super().closeEvent(event)


class CompactWorkspaceMixin:
    def _install_workspace_header(self):
        """One shared header; no permanent navigation gutter or job row."""
        self.workspace_header=QWidget(self);self.workspace_header.setObjectName('workspace_header')
        header=QHBoxLayout(self.workspace_header);header.setContentsMargins(0,0,0,0);header.setSpacing(6)
        tools=self.analysis_tools.layout()
        tools.removeWidget(self.mode_button);tools.removeWidget(self.scale_tool)
        tools.setRowStretch(2,0);tools.setHorizontalSpacing(6)
        tools.addWidget(self.mode_button,0,0);tools.addWidget(self.scale_tool,0,1)
        self.analysis_tools.setMinimumWidth(0);self.analysis_tools.setMaximumWidth(16777215)
        self.analysis_tools.setSizePolicy(QSizePolicy.Maximum,QSizePolicy.Fixed)
        self.mode_button.setFixedSize(96,38);self.mode_button.setToolTip('Choose a viewing or analysis tool')
        self.mode_button.setStyleSheet('QToolButton { background: #704f7c; color: #ffffff; border: 1px solid #b18ac1; font-weight: 600; font-size: 14px; padding: 4px 10px; } QToolButton:hover { background: #83608f; } QToolButton:disabled { background: #34313a; color: #8d8492; }')
        self.scale_tool.setFixedSize(38,38);self.scale_tool.setAccessibleName('Calibrate image scale')
        self.scale_tool.setStyleSheet('padding: 4px; min-width: 28px; min-height: 28px;')
        header.addWidget(self.analysis_tools)
        cine_grid=self.cine_disp_col.layout()
        cine_header=cine_grid.itemAtPosition(0,0).layout()
        cine_grid.removeItem(cine_header)
        self._viewer_source_row = cine_header
        header.addLayout(cine_header,1)
        header.addWidget(self.job_controls)
        outer=self.layout()
        for widget in (self.analysis_tools,self.workflow_stack,self.details_rail):outer.removeWidget(widget)
        outer.addWidget(self.workspace_header,0,0,1,2)
        outer.addWidget(self.workflow_stack,1,0);outer.addWidget(self.details_rail,1,1)
        for column in range(outer.columnCount()):outer.setColumnStretch(column,0);outer.setColumnMinimumWidth(column,0)
        outer.setColumnStretch(0,1);outer.setContentsMargins(6,6,6,6);outer.setSpacing(4)
        self.tab_splitter.widget(0).layout().setContentsMargins(0,0,0,0)
        cine_grid.setContentsMargins(0,0,0,0)
        self.analysis_tools_bg.buttonClicked.connect(self._sync_workspace_header)
        self.workflow_stack.currentChanged.connect(self._sync_workspace_header)
        for control in (*self.details_buttons,self.playback_fps):control.installEventFilter(self)
        self._sync_workspace_header()

    def _sync_compact_control_metrics(self):
        """Keep compact defaults while accommodating larger accessibility fonts."""
        width=max(46,*(button.fontMetrics().horizontalAdvance(button.text())+12 for button in self.details_buttons))
        for button in self.details_buttons:button.setFixedWidth(width)
        self.details_rail.setFixedWidth(width+4)
        fps_width=max(100,self.playback_fps.sizeHint().width())
        self.playback_fps.setFixedWidth(fps_width)
        self.playback_card.setFixedSize(268+fps_width-100,max(76,self.playback_card.layout().minimumSize().height()))
        if getattr(self,'_layout_ready',False):self._playback_position_timer.start(0)

    def _sync_workspace_header(self,*args):
        self._sync_compact_control_metrics()
        analysis=self.workflow_stack.currentWidget() is self.tab_splitter
        tracking=analysis and self.current_tool is self.track_tool
        has_own_source = self.workflow_stack.currentWidget() in (self.event_finder, self.camera_control)
        for index in range(self._viewer_source_row.count()):
            widget = self._viewer_source_row.itemAt(index).widget()
            if widget is not None and widget is not self.theme_button:
                widget.setVisible(not has_own_source)
        self.scale_tool.setVisible(not has_own_source)
        self.details_rail.setVisible(analysis)
        self.details_buttons[2].setVisible(tracking)
        self.details_buttons[2].setToolTip('Tracked objects, measured points and object settings')
        if not tracking and self.main_tab.currentIndex()==2:
            self._collapse_details();self.main_tab.setCurrentIndex(0)
        self._sync_mode_menu()

    def _install_compact_workspace(self):
        self._details_open = False
        self._tracking_editor_open = False
        self._graphs_open = True
        self._picking_reference = None
        self.reference_escape = QShortcut(QKeySequence('Escape'), self)
        self.reference_escape.activated.connect(self._cancel_reference_pick)
        self.reference_escape.setEnabled(False)
        self._analysis_fps = 30.0
        app = QApplication.instance()
        if not hasattr(app, '_pca_wheel_guard'):
            app._pca_wheel_guard = IntentionalWheelEditing(app)
            app.installEventFilter(app._pca_wheel_guard)
        # Keep the existing buttons/signals as the workflow API, presented by a menu.
        layout = self.analysis_tools.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget(): item.widget().hide()
        self.analysis_tools.setFixedWidth(48)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode_button = QToolButton(); self.mode_button.setText('Tools')
        self.mode_button.setAccessibleName('Choose workspace tool')
        self.mode_button.setPopupMode(QToolButton.InstantPopup)
        self.mode_button.setFixedSize(46, 38)
        self.mode_menu = QMenu(self.mode_button); self.mode_button.setMenu(self.mode_menu)
        self.mode_actions = {}
        names = [('Viewer', self.viewer_tool, 'viewer-eye.svg'), ('Video Overlay', self.overlay_tool, 'viewer-eye.svg'),
                 ('Camera Control', self.camera_control_tool, 'icons8-camera-transparent.gif'),
                 ('Event Finder', self.event_tool, 'target-32.png'),
                 ('Background-Oriented Schlieren (BOS)', self.bos_tool, 'bos.svg'),
                 ('Track', self.track_tool, 'target-32.png'), ('Distance', self.two_pt_tool, 'line-32.png'),
                 ('Angle', self.three_pt_tool, 'angle-32.png'), ('Lines', self.two_line_tool, '2-line-32.png'),
                 ('Area', self.area_tool, 'rect-32.png')]
        for name, button, icon in names:
            action = self.mode_menu.addAction(QIcon(os.path.join(os.path.dirname(__file__), 'images', icon)), name)
            action.setCheckable(True); action.triggered.connect(lambda checked=False, b=button: b.click())
            self.mode_actions[button] = action
        self.mode_menu.addSeparator()
        report = self.mode_menu.addAction('Report…')
        # The original Report button is created before Viewer creates its Export button.
        self.report_tool = self.findChild(QWidget, 'qt_export_button')
        report.triggered.connect(lambda: self.report_tool.click())
        self.mode_menu.aboutToShow.connect(self._sync_mode_menu)
        layout.addWidget(self.mode_button, 0, 0, 1, 2)
        self.scale_tool.setFixedSize(40, 34); self.scale_tool.show()
        layout.addWidget(self.scale_tool, 1, 0, 1, 2)
        layout.setRowStretch(2, 1)
        self.comparator_tool.hide()

        self.details_rail = QFrame(); self.details_rail.setObjectName('details_rail')
        self.details_rail.setFixedWidth(50)
        rail = QVBoxLayout(self.details_rail); rail.setContentsMargins(2, 2, 2, 2)
        self.details_buttons = []
        for index, text in enumerate(('Info', 'Adjust', 'Tracks')):
            button = QToolButton(); button.setText(text); button.setFixedSize(46, 32)
            button.setCheckable(True); button.setToolTip('Show / hide '+text.lower())
            button.clicked.connect(lambda checked=False, i=index: self._toggle_details(i))
            rail.addWidget(button); self.details_buttons.append(button)
        rail.addStretch()
        self.layout().addWidget(self.details_rail, 1, 3)
        self.layout().setColumnStretch(2, 1)
        self.main_tab.setMinimumWidth(0)
        self.main_tab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.tab_splitter.setCollapsible(1, True)
        self.tab_splitter.splitterMoved.connect(self._details_splitter_moved)
        self.main_tab.currentChanged.connect(self._sync_details_buttons)
        self.tracking_setup.form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.tracking_setup.setMinimumWidth(0)
        self.tracking_setup.methods[next(k for k in self.tracking_setup.methods if 'Tumbling' in k)].setText('Tumbling')
        for button in self.tracking_setup.methods.values():
            button.setMinimumWidth(0); button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.track_pages.setCurrentIndex(1); self.track_pages.setTabVisible(0, False)
        self._collapse_details()

        self.header_objects = QComboBox(); self.header_objects.setFixedWidth(96)
        self.header_objects.setToolTip('Select a tracked object')
        self.edit_object_button = QToolButton(); self.edit_object_button.setText('Edit')
        self.edit_object_button.setToolTip('Edit the selected object’s tracking settings')
        self.reference_button = QToolButton(); self.reference_button.setText('Reference')
        self.reference_button.setToolTip('Set displacement zero to a frame or a fixed image pixel')
        self.graphs_button = QToolButton(); self.graphs_button.setText('Plot'); self.graphs_button.setCheckable(True)
        self.graphs_button.setChecked(True); self.graphs_button.setToolTip('Show / hide graphs and point preview')
        self.header_more = QToolButton(); self.header_more.setText('…')
        self.header_more.setAccessibleName('More tracking tools'); self.header_more.setToolTip('Follow, plots and help')
        self.header_more.setPopupMode(QToolButton.InstantPopup)
        more = QMenu(self.header_more); self.header_more.setMenu(more)
        more.addAction('Add Object…',lambda:self.add_object_button.click())
        more.addAction('Edit selected object…',self._edit_selected_object)
        more.addAction('Displacement reference…',self._open_reference_editor)
        more.addAction('Path settings…',lambda:self.path_fade_button.click())
        objects=more.addMenu('Select object')
        def fill_objects():
            objects.clear()
            for key,track in self.vm.track_data.items():
                action=objects.addAction(track['name']);action.setCheckable(True);action.setChecked(key==self.vm.active_object)
                action.triggered.connect(lambda checked=False,k=key:self._select_tracking_object(k))
        objects.aboutToShow.connect(fill_objects)
        more.addSeparator()
        self.viewer_follow_menu.setTitle('Follow object'); more.addMenu(self.viewer_follow_menu)
        plot = more.addAction('Show plot / preview'); plot.setCheckable(True)
        plot.triggered.connect(lambda: self.graphs_button.click())
        more.addAction('Tracking help…', lambda: self.tracking_help_button.click())
        more.aboutToShow.connect(lambda: plot.setChecked(self.graphs_button.isChecked()))
        tools = self.viewer_overlay_controls.layout()
        tools.insertWidget(2, self.header_objects)
        self.add_object_button.setStyleSheet('QPushButton { background: #765084; color: white; border: 1px solid #c38ad5; font-weight: 600; padding: 3px 8px; } QPushButton:hover { background: #90609e; } QPushButton:disabled { background: #35313b; color: #958a9b; }')
        tools.insertWidget(tools.count()-1, self.edit_object_button)
        tools.insertWidget(tools.count()-1, self.reference_button)
        tools.insertWidget(tools.count()-1, self.graphs_button)
        tools.addWidget(self.header_more)
        self.header_objects.activated.connect(lambda i: self._select_tracking_object(self.header_objects.itemData(i)))
        self.edit_object_button.clicked.connect(self._edit_selected_object)
        self.reference_button.clicked.connect(self._open_reference_editor)
        self.graphs_button.toggled.connect(self._set_graphs_open)
        self.viewer_overlay_controls.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        for pane in self.workspace_panes:
            pane.title.setMinimumWidth(64); pane.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.path_metric = QComboBox()
        self.path_metric.addItems(['Displacement', 'X-Displacement', 'Y-Displacement', 'Speed', 'Acceleration', 'Angle'])
        self.path_metric.setPlaceholderText('Choose heat-map value')
        self.path_fade_panel.layout().addRow('Heat-map value', self.path_metric)
        self.path_metric.currentTextChanged.connect(self.fig_dropdown.setCurrentText)
        self.fig_dropdown.currentTextChanged.connect(lambda text:
            self.path_metric.setCurrentIndex(self.path_metric.findText(text)))
        hint = QLabel('Green = lowest · red = highest\nX/Y displacement is signed from the reference.')
        hint.setWordWrap(True); self.path_fade_panel.layout().addRow(hint)
        self.playback_fps = QDoubleSpinBox(); self.playback_fps.setRange(.1, 10000)
        self.playback_fps.setDecimals(1); self.playback_fps.setValue(30); self.playback_fps.setSuffix(' fps')
        self.playback_fps.setKeyboardTracking(False); self.playback_fps.setFixedWidth(112)
        self.playback_fps.setAccessibleName('Playback frames per second')
        self.playback_card.layout().itemAt(0).layout().insertWidget(2, self.playback_fps)
        self.playback_fps.valueChanged.connect(self._change_transport_fps)
        # Reuse all existing PCC transport actions/shortcuts in the familiar two rows.
        grid = self.pcc_transport_controls.layout()
        while grid.count(): grid.takeAt(0)
        for button in self.transport_buttons:
            button.setText(''); button.setToolButtonStyle(Qt.ToolButtonIconOnly); button.setFixedSize(32, 26)
            button.setAccessibleName(button.toolTip())
        for column, button in enumerate((self.reverse_play_button, self.pause_button, self.forward_play_button)):
            grid.addWidget(button, 0, column*4, 1, 4)
        for column, button in enumerate((self.reverse_faster_button, self.previous_frame_button, self.next_frame_button, self.forward_faster_button)):
            grid.addWidget(button, 1, column*3, 1, 3)
        self._place_header_controls()

    def _connect_compact_workspace(self):
        self.vm.new_track_data.connect(self._sync_compact_objects)
        self.vm.image_scale_changed.connect(lambda text: self.scale_tool.setToolTip('Calibrate distance · '+text))
        self.frame_slider.valueChanged.connect(self._sync_transport_fps)
        self.analysis_tools_bg.buttonClicked.connect(lambda *args: self._sync_transport_fps())
        self.analysis_tools_bg.buttonClicked.connect(lambda *args: self._cancel_reference_pick())
        self.workflow_stack.currentChanged.connect(lambda: self.details_rail.setVisible(
            self.workflow_stack.currentWidget() is self.tab_splitter))
        self._sync_compact_objects()

    def _sync_mode_menu(self):
        panel = self.workflow_stack.currentWidget()
        active = self.current_tool
        if panel is self.camera_control:
            active = self.camera_control_tool
        elif panel is self.event_finder:
            active = self.event_tool
        elif panel is self.bos_panel:
            active = self.bos_tool
        elif panel is self.overlay_panel:
            active = self.overlay_tool
        for button, action in self.mode_actions.items():
            action.setChecked(button is active)

    def _details_splitter_moved(self, *args):
        if self.main_tab.isVisible() and self.tab_splitter.sizes()[1] == 0:
            self._collapse_details()
        QTimer.singleShot(0, self, self._place_header_controls)

    def _sync_details_buttons(self, *args):
        for index, button in enumerate(self.details_buttons):
            button.setChecked(self._details_open and self.main_tab.currentIndex() == index)
        if self._details_open and self.main_tab.currentIndex() == 2:
            QTimer.singleShot(0, self, self._refresh_object_previews)

    def _toggle_details(self, index):
        if index==2 and (self.current_tool is not self.track_tool or self.workflow_stack.currentWidget() is not self.tab_splitter):
            return
        if self._details_open and self.main_tab.currentIndex() == index:
            self._collapse_details()
        else:
            if index == 2 and not self._tracking_editor_open: self.track_pages.setCurrentIndex(1)
            self.main_tab.setCurrentIndex(index); self._ensure_details_panel_open(force=True)

    def _collapse_details(self):
        self._details_open = False; self.main_tab.hide()
        if hasattr(self,'details_panel'): self.details_panel.hide()
        self.tab_splitter.setSizes([max(1, self.tab_splitter.width()), 0])
        self._sync_details_buttons()

    def _set_graphs_open(self, checked):
        self._graphs_open = bool(checked)
        self._plot_explicit=True
        self.status_toolbar.setVisible(checked and self.current_tool != self.viewer_tool)
        if getattr(self,'_layout_ready',False):self._sync_video_layout()

    def _place_header_controls(self):
        if not hasattr(self, 'header_objects') or not self.workspace_panes: return
        index = max(0, getattr(self.vm, 'active_cine_index', 0)) if self.vm else 0
        pane = self.workspace_panes[index]
        if self.viewer_overlay_controls.parentWidget() is not pane:
            pane.header.insertWidget(pane.header.count()-1, self.viewer_overlay_controls)
        narrow = pane.width() < 850
        compact = pane.width() < 650
        tiny = pane.width() < 460
        self.add_object_button.setText('Add' if tiny else 'Add Object')
        self.add_object_button.setToolTip('Add Object · choose a tracking method')
        self.reference_button.setText('Ref' if narrow else 'Reference')
        self.path_fade_button.setText('Path' if narrow else 'Path Settings')
        self.header_objects.setFixedWidth(96)
        self.header_objects.setVisible(bool(self.vm and self.vm.track_data) and not tiny)
        for control in (self.edit_object_button,self.reference_button,self.path_fade_button):control.setVisible(not tiny)
        self.viewer_pan_button.hide()
        for control in (self.viewer_follow_button, self.graphs_button, self.tracking_help_button):
            control.setVisible(not compact)
        for item in self.workspace_panes:
            item.hide_button.setVisible(self.current_tool is not self.track_tool)
        self.header_more.setVisible(compact)
        self.viewer_overlay_controls.setVisible(self.current_tool == self.track_tool and not getattr(self,'_guide',None))
        position = self.viewer_overlay_controls.mapTo(self.workspace_grid_widget,
            self.viewer_overlay_controls.rect().bottomRight())
        self.path_fade_panel.adjustSize()
        self.path_fade_panel.move(max(0, min(position.x()-self.path_fade_panel.width(),
            self.workspace_grid_widget.width()-self.path_fade_panel.width())), position.y()+4)
        self.path_fade_panel.raise_()

    def _sync_compact_objects(self, *args):
        if self.vm is None: return
        with QSignalBlocker(self.header_objects):
            self.header_objects.clear()
            for key, track in self.vm.track_data.items(): self.header_objects.addItem(track['name'], key)
            self.header_objects.setCurrentIndex(self.header_objects.findData(self.vm.active_object))
        active = self.vm.active_object in self.vm.track_data
        self.edit_object_button.setEnabled(active); self.reference_button.setEnabled(active)
        self._place_header_controls()

    def _edit_selected_object(self):
        if self.vm.active_object not in self.vm.track_data or self._tracking_busy(): return
        self._tracking_editor_open = True
        if self.vm.track_type == 'Auto' and hasattr(self,'_begin_guided_tracking'):
            track=self.vm.track_data[self.vm.active_object]
            self._sync_tracking_setup();self._begin_guided_tracking(track.get('tracking_method','Classic (Intensity Only)'),self.vm.active_object)
            return
        if self.vm.track_type == 'Auto':
            self.track_pages.setTabVisible(0, True); self.track_pages.setCurrentIndex(0)
            self._sync_tracking_setup()
        else: self.track_pages.setCurrentIndex(1)
        self.main_tab.setCurrentIndex(2); self._ensure_details_panel_open(force=True)

    def _open_reference_editor(self):
        if self.vm.active_object not in self.vm.track_data or self._tracking_busy(): return
        old = getattr(self, '_reference_dialog', None)
        if old: old.close(); old.deleteLater()
        self._reference_dialog = ReferenceDialog(self, self.vm.active_object)
        self._reference_dialog.show()

    def _apply_tracking_reference(self, object_id, mode, frame=None, point=None):
        if self._tracking_busy():
            raise ValueError('Finish or cancel tracking before changing the reference.')
        if mode not in ('first', 'frame', 'pixel'):
            raise ValueError('Choose a frame or fixed pixel reference.')
        track = self.vm.track_data.get(object_id)
        if track is None: raise ValueError('Select a tracked object first.')
        if mode == 'frame' and frame not in track['frames']:
            raise ValueError('Track the object in that frame first, or choose a frame with a measured point.')
        if mode == 'pixel':
            metadata = self.vm.cine_handler.metadata
            if point is None or not np.all(np.isfinite(point)) or not (0 <= point[0] < metadata.ImWidth and 0 <= point[1] < metadata.ImHeight):
                raise ValueError('Choose a reference pixel inside the image.')
        track['reference_pixel'] = tuple(map(float, point)) if mode == 'pixel' else None
        track['origin_frame'] = int(frame) if mode == 'frame' else None
        track['relative_to'] = None
        self.vm.track_fig_calculations(obj=track)
        self.vm.new_track_data.emit(self.vm.track_data, self.vm.active_object)
        self.vm.sync_active_workspace_context(); self.vm.redraw_cb()

    def _consume_reference_pick(self, graph, position):
        dialog = self._picking_reference
        if dialog is None: return False
        if self.vm.track_data.get(dialog.object_id) is not dialog.track_identity:
            self._cancel_reference_pick(); return True
        if graph is not self.graph: return True
        if graph._within_bounds(position):
            self._picking_reference = None
            self.reference_escape.setEnabled(False)
            QTimer.singleShot(0, dialog, lambda p=QPointF(position): dialog.picked(p))
            self.status_bar.setText('Reference pixel selected. Apply to update displacement.')
        return True

    def _cancel_reference_pick(self):
        if self._picking_reference is None: return
        dialog, self._picking_reference = self._picking_reference, None
        self.reference_escape.setEnabled(False)
        dialog.error.setText('Pixel selection cancelled.'); dialog.show()
        self.status_bar.setText('Reference selection cancelled.')

    def _change_transport_fps(self, fps):
        group = getattr(self, 'group_playback', None)
        grouped = group is not None and group.enabled()
        if self.current_tool == self.viewer_tool and self._has_viewer_edit() and not grouped:
            from viewer_timeline import section_at
            edit = deepcopy(self.vm.viewer_edit)
            index = section_at(edit, int(self.frame_slider.value()))
            if index is None: return
            edit['sections'][index]['fps'] = float(fps)
            self._apply_viewer_edit(edit)
        else:
            self._analysis_fps = float(fps)
            self._playback_interval_ms = max(1, round(1000/fps))

    def _sync_transport_fps(self, *args):
        if not hasattr(self, 'playback_fps'): return
        value = self._analysis_fps
        group = getattr(self, 'group_playback', None)
        grouped = group is not None and group.enabled()
        if self.current_tool == self.viewer_tool and self._has_viewer_edit() and not grouped:
            from viewer_timeline import section_at
            edit = self.vm.viewer_edit
            index = section_at(edit, int(self.frame_slider.value()))
            if index is not None: value = edit['sections'][index]['fps']
            self.playback_fps.setToolTip('Playback FPS for the selected video segment; included in export.')
        else:
            self.playback_fps.setToolTip('Synchronized updates per second; the selected Cine advances one recorded frame per update. Export edits are unchanged.' if grouped else
                                        'Playback FPS for viewing. Measurements use the recorded capture timestamps.')
        self._playback_interval_ms = max(1, round(1000/self._analysis_fps))
        with QSignalBlocker(self.playback_fps): self.playback_fps.setValue(value)

    def _commit_search_region(self,object_id,center,size,angle=None):
        from tracking_geometry import pose_at,search_pose,rotate,contain_region
        track=self.vm.track_data.get(object_id)
        if track is None or self._tracking_busy():return
        pose=pose_at(track,int(self.vm.active_frame))
        if pose is None:return
        base,pose_angle=pose
        _,previous_angle,delta=search_pose(track,base,pose_angle)
        angle=previous_angle if angle is None else float(angle)
        meta=self.vm.cine_handler.metadata
        center=np.clip(np.asarray(center,dtype=float),[0,0],[meta.ImWidth-1,meta.ImHeight-1])
        dimensions=contain_region(center,tuple(self._odd_dimension(v,11) for v in size),angle,base,track['tpl_rng'],-pose_angle)
        track.update(search_area=dimensions,search_offset=tuple(rotate(center-base,-delta)),
            search_angle=(angle-delta+180)%360-180,search_area_manual=True,search_geometry_version=1)
        self._sync_tracking_setup();self.vm.sync_active_workspace_context();self.vm.redraw_cb()
        if getattr(self,'_guide',None):self._sync_guide()
        self.status_bar.setText('Search area updated; tracking region unchanged. Rerun tracking to apply.')
