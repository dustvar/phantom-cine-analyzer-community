"""Video-first layout with a resizable plot and compact floating playback."""
from shiboken6 import isValid
from PySide6.QtCore import Qt,QTimer,QSignalBlocker,QPoint,QEvent
from PySide6.QtWidgets import (QWidget,QFrame,QVBoxLayout,QHBoxLayout,QGridLayout,
    QSplitter,QLabel,QToolButton,QPushButton,QSizePolicy,QComboBox,QSpinBox,QLayout)


class WorkspaceLayoutMixin:
    def _install_video_workspace(self):
        self._layout_ready=False
        self._playback_position_timer=QTimer(self);self._playback_position_timer.setSingleShot(True)
        self._playback_position_timer.timeout.connect(self._position_playback)
        self._plot_sizes=None
        self._plot_explicit=False
        self.main_tab.tabBar().hide()
        self.details_panel=QFrame();self.details_panel.setObjectName('details_panel')
        detail_layout=QVBoxLayout(self.details_panel);detail_layout.setContentsMargins(4,4,4,4)
        header=QHBoxLayout();self.details_title=QLabel('Info');header.addWidget(self.details_title,1)
        self.details_close=QToolButton();self.details_close.setText('Close ›')
        self.details_close.setFocusPolicy(Qt.StrongFocus)
        self.details_close.setAccessibleName('Close inspector')
        self.details_close.clicked.connect(self._collapse_details);header.addWidget(self.details_close)
        detail_layout.addLayout(header)
        self.tab_splitter.replaceWidget(1,self.details_panel)
        detail_layout.addWidget(self.main_tab)
        self.details_panel.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Expanding)
        self.details_panel.hide();self.main_tab.hide()
        self.tab_splitter.setCollapsible(1,True)
        self.main_tab.currentChanged.connect(lambda i:self.details_title.setText(('Info','Adjust','Tracks')[i]))
        self.track_pages.tabBar().hide()
        # QStackedWidget owns visibility. Calling show() on the inactive Setup
        # page caused it to paint over Results, especially under Windows styles.
        self.track_pages.setCurrentIndex(1)
        self.tracking_setup.hide()

        middle=self.tab_splitter.widget(0);layout=middle.layout()
        layout.removeWidget(self.cine_disp_col);layout.removeWidget(self.status_toolbar)
        self.video_plot_splitter=QSplitter(Qt.Vertical)
        self.video_plot_splitter.setObjectName('video_plot_splitter')
        self.video_plot_splitter.setHandleWidth(9)
        self.video_plot_splitter.addWidget(self.cine_disp_col)
        self.video_plot_splitter.addWidget(self.status_toolbar)
        self.video_plot_splitter.setChildrenCollapsible(False)
        self.video_plot_splitter.setStretchFactor(0,3);self.video_plot_splitter.setStretchFactor(1,1)
        self.video_plot_splitter.handle(1).setToolTip('Drag up/down to resize the video and graph')
        self.video_plot_splitter.splitterMoved.connect(self._remember_plot_size)
        layout.addWidget(self.video_plot_splitter,0,0);layout.setRowStretch(0,1)
        self.cine_disp_col.setMinimumHeight(280)
        self.status_toolbar.setMinimumHeight(0);self.status_toolbar.setMaximumHeight(16777215)
        self.track_canvas.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)

        # Move non-timeline editor actions to the side, leaving the horizontal
        # timeline available on demand instead of consuming half the video area.
        self.viewer_side_tools=QFrame();self.viewer_side_tools.setObjectName('viewer_side_tools')
        self.viewer_side_tools.setFixedWidth(184)
        side=QVBoxLayout(self.viewer_side_tools);side.setContentsMargins(8,8,8,8);side.setSpacing(6)
        side.addWidget(QLabel('Video tools'))
        self.timeline_toggle=QPushButton('Show timeline');self.timeline_toggle.setCheckable(True)
        self.timeline_toggle.setToolTip('Show or hide the segment and speed editor.')
        self.timeline_toggle.toggled.connect(self._sync_video_layout)
        for control in (self.timeline_toggle,self.timeline_button,self.levels_button,self.fps_button,self.track_fade_button,self.export_button):side.addWidget(control)
        side.addSpacing(7);side.addWidget(QLabel('Sequence'))
        side.addWidget(self.video_selector)
        row=QHBoxLayout();row.addWidget(self.multiply_button);row.addWidget(self.add_video_button);side.addLayout(row)
        self.multiply_button.setText('Repeat');self.add_video_button.setText('Add video')
        side.addSpacing(7);side.addWidget(QLabel('Clip bounds'))
        hint=QLabel('Drag the brackets on the playback bar to trim.');hint.setWordWrap(True)
        hint.setObjectName('viewer_group_hint');side.addWidget(hint)
        row=QHBoxLayout();row.addWidget(self.mark_in_button);row.addWidget(self.mark_out_button);side.addLayout(row)
        row=QHBoxLayout();row.addWidget(self.jump_start_button);row.addWidget(self.reset_clip_button);row.addWidget(self.jump_end_button);side.addLayout(row)
        row=QHBoxLayout();row.addWidget(self.clip_start_label);row.addWidget(self.clip_end_label);side.addLayout(row)
        side.addStretch(1)
        self.finish_card.hide()
        # Only Split/Undo/Redo remain under the timeline.
        self.video_selector.setMinimumWidth(0);self.video_selector.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
        for button in (self.multiply_button,self.add_video_button):button.setObjectName('compact_side_button')

        grid=self.cine_disp_col.layout()
        workspace=grid.itemAtPosition(1,0).layout()
        body=workspace.itemAt(workspace.count()-1).layout()
        body.addWidget(self.viewer_side_tools)
        self.viewer_side_tools.hide()
        grid.removeWidget(self.playback_card)
        # Keep the long seek bar below the image. The small transport floats on
        # the image and its background is translucent, without fading its text.
        self.seek_bar = QWidget()
        self.seek_bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        seek = QGridLayout(self.seek_bar)
        seek.setContentsMargins(2, 0, 2, 0)
        seek.setSpacing(6)
        self.seek_track = QWidget(self.seek_bar)
        track_row = QHBoxLayout(self.seek_track)
        track_row.setContentsMargins(0, 0, 0, 0)
        track_row.setSpacing(6)
        for widget in (self.frame_first_button, self.frame_slider, self.frame_last_button):
            track_row.addWidget(widget, int(widget is self.frame_slider))
        self.seek_actions = QWidget(self.seek_bar)
        actions_row = QHBoxLayout(self.seek_actions)
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(6)
        self.transport_toggle=QToolButton();self.transport_toggle.setText('Playback');self.transport_toggle.setCheckable(True);self.transport_toggle.setChecked(True)
        self.transport_toggle.setToolTip('Show / hide floating playback controls')
        self.transport_toggle.toggled.connect(self._sync_video_layout)
        actions_row.addWidget(self.transport_toggle)
        self._seek_arrangement = None
        self._arrange_seek_bar('single')
        self.first_frame_disp.hide();self.last_frame_disp.hide()
        old=self.playback_card.layout()
        def clear(layout):
            while layout.count():
                item=layout.takeAt(0)
                if item.widget():item.widget().hide()
                if item.layout():clear(item.layout())
        clear(old)
        self.playback_card.setParent(self.workspace_grid_widget)
        self.playback_card.setObjectName('floating_playback')
        self.playback_card.setProperty('viewerCard',False)
        old.setContentsMargins(5, 5, 5, 5)
        old.setSpacing(0)
        old.setSizeConstraint(QLayout.SetFixedSize)
        controls = QGridLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setHorizontalSpacing(6)
        controls.setVerticalSpacing(self.pcc_transport_controls.layout().spacing())
        controls.addWidget(self.pcc_transport_controls, 0, 0, 2, 1)
        controls.addWidget(self.active_frame_label, 0, 1)
        controls.addWidget(self.playback_fps, 1, 1)
        self.active_frame_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        old.addLayout(controls)
        for widget in (self.pcc_transport_controls,self.active_frame_label,self.playback_fps):widget.show()
        grid.addWidget(self.seek_bar,3,0,1,-1)
        self.status_bar.setWordWrap(False);self.status_bar.setFixedHeight(18)
        grid.removeWidget(self.status_bar);grid.addWidget(self.status_bar,5,0,1,-1)
        self.workspace_grid_widget.installEventFilter(self)
        self.playback_card.installEventFilter(self)
        self.seek_bar.installEventFilter(self)
        for pane in self.workspace_panes:pane.graph.viewport().installEventFilter(self)

        # Derivative controls live with the graph and always identify filtering.
        controls=QHBoxLayout();controls.addWidget(QLabel('Derivatives'))
        self.motion_filter=QComboBox();self.motion_filter.addItem('Local fit (recommended)','local_polynomial');self.motion_filter.addItem('Raw differences','raw')
        self.motion_window=QSpinBox();self.motion_window.setRange(5,101);self.motion_window.setSingleStep(2);self.motion_window.setValue(21);self.motion_window.setSuffix(' frames');self.motion_window.setKeyboardTracking(False)
        controls.addWidget(self.motion_filter);controls.addWidget(self.motion_window)
        controls.addStretch(1)
        self.motion_hint=QLabel('Raw points are preserved');self.motion_hint.setWordWrap(True)
        self.status_toolbar.layout().addLayout(controls,2,1,1,3)
        self.status_toolbar.layout().addWidget(self.motion_hint,3,1,1,3)
        # Let the complete grid (plot plus every control row) set the splitter's
        # minimum. A fixed 130px parent let a taller canvas paint over controls.
        self.status_toolbar.layout().setSizeConstraint(QLayout.SetMinimumSize)
        self.motion_filter.currentIndexChanged.connect(self._change_motion_filter)
        self.motion_window.valueChanged.connect(self._change_motion_filter)
        self._layout_ready=True
        QTimer.singleShot(0,self,self._sync_video_layout)

    def _connect_video_workspace(self):
        self.vm.update_progress.connect(self.run_progress.setValue)
        self.vm.new_track_data.connect(self._sync_motion_filter)
        self.vm.new_track_data.connect(self._sync_video_layout)
        self.vm.workspace_changed.connect(self._sync_video_layout)
        self.analysis_tools_bg.buttonClicked.connect(self._sync_video_layout)
        self._sync_motion_filter()

    def eventFilter(self,obj,event):
        if (event.type() == QEvent.ShortcutOverride and event.key() == Qt.Key_Space
                and obj in (*self.details_buttons, self.details_close)):
            # Space activates a focused inspector button before the playback shortcut.
            event.accept()
            return True
        if (hasattr(self,'workspace_header') and event.type() in (QEvent.FontChange,QEvent.StyleChange)
                and (obj in self.details_buttons or obj is self.playback_fps)):
            QTimer.singleShot(0,self,self._sync_compact_control_metrics)
        if isValid(self) and getattr(self,'_layout_ready',False) and event.type()==QEvent.Resize:
            if obj in (self.workspace_grid_widget, self.playback_card, self.seek_bar) or any(obj is pane.graph.viewport() for pane in self.workspace_panes):
                self._playback_position_timer.start(0)
        return super().eventFilter(obj,event)

    def _remember_plot_size(self,*args):
        if self.status_toolbar.isVisible():self._plot_sizes=self.video_plot_splitter.sizes()
        self._position_playback()

    def _sync_video_layout(self,*args):
        if not getattr(self,'_layout_ready',False):return
        if hasattr(self,'workspace_header'):self._sync_workspace_header()
        if hasattr(self,'pane_layout_manager'):self.pane_layout_manager.sync_controls()
        guided=bool(getattr(self,'_guide',None))
        busy=self._tracking_busy()
        for widget in (self.track_type_auto,self.track_type_man):
            widget.setVisible(not guided);widget.setEnabled(not guided and not busy)
        if hasattr(self,'setup_area'):
            self.setup_area.setVisible(guided or busy);self.run_panel.setVisible(busy)
        viewer=self.current_tool is self.viewer_tool and not guided
        self.frame_slider.set_viewer_mode(viewer)
        self.viewer_side_tools.setVisible(viewer and bool(self.cine_path))
        self.viewer_controls.setVisible(viewer and self.timeline_toggle.isChecked())
        self.timeline_toggle.setText('Hide timeline' if self.timeline_toggle.isChecked() else 'Show timeline')
        self.finish_card.hide()
        has_data=bool(self.vm and any(len(track.get('frames',[]))>1 for track in self.vm.track_data.values()))
        show_graph=not viewer and not guided and self._graphs_open and (has_data or self._plot_explicit)
        self.status_bar.setVisible(not guided)
        was=self.status_toolbar.isVisible()
        self.status_toolbar.setVisible(show_graph)
        if show_graph and not was:
            total=max(400,self.video_plot_splitter.height())
            self.video_plot_splitter.setSizes(self._plot_sizes or [int(total*.68),int(total*.32)])
        self.playback_card.setVisible(bool(self.cine_path) and self.transport_toggle.isChecked() and self.current_tool is not self.scale_tool)
        self._place_header_controls()
        self.details_panel.setVisible(self._details_open)
        self._sync_viewer_panels()
        self._playback_position_timer.start(0)

    def _arrange_seek_bar(self, arrangement):
        if self._seek_arrangement == arrangement:
            return
        self._seek_arrangement = arrangement
        grid = self.seek_bar.layout()
        for widget in (self.playback_card, self.seek_track, self.seek_actions):
            grid.removeWidget(widget)
        grid.setColumnStretch(0, int(arrangement in ('single', 'narrow')))
        grid.setColumnStretch(1, int(arrangement in ('inline', 'wrapped')))
        if arrangement == 'inline':
            grid.addWidget(self.playback_card, 0, 0)
            grid.addWidget(self.seek_track, 0, 1)
            grid.addWidget(self.seek_actions, 0, 2)
        elif arrangement == 'wrapped':
            grid.addWidget(self.playback_card, 0, 0)
            grid.addWidget(self.seek_actions, 0, 1, Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(self.seek_track, 1, 0, 1, 2)
        else:
            # With unusually wide readouts, give the card its own row.
            row = int(arrangement == 'narrow')
            if row:
                grid.addWidget(self.playback_card, 0, 0, 1, 2, Qt.AlignLeft)
            grid.addWidget(self.seek_track, row, 0)
            grid.addWidget(self.seek_actions, row, 1)

    def _position_playback(self):
        if not isValid(self) or not getattr(self,'_layout_ready',False):return
        if not isValid(self.workspace_grid_widget) or not isValid(self.playback_card):return
        # A shared transport must not cover neighboring videos in narrow rows
        # or consume most of a short stacked pane. Keep it beside the seek bar.
        multiple=sum(pane.isVisible() for pane in self.workspace_panes)>1
        docked=self.playback_card.parentWidget() is self.seek_bar
        visible=bool(self.cine_path) and self.transport_toggle.isChecked() and self.current_tool is not self.scale_tool
        if multiple:
            if not docked:
                self.playback_card.setParent(self.seek_bar)
                self.playback_card.setVisible(visible)
            arrangement = 'single'
            if visible:
                grid = self.seek_bar.layout()
                margins = grid.contentsMargins()
                available = self.seek_bar.width() - margins.left() - margins.right()
                control_width = self.playback_card.sizeHint().width() + self.seek_actions.sizeHint().width() + grid.horizontalSpacing()
                ends = self.frame_first_button.sizeHint().width() + self.frame_last_button.sizeHint().width()
                # Base reflow on the allocated width, independent of the current
                # arrangement. Keep at least 200 px for scrubbing in one row.
                if available >= control_width + ends + 3 * grid.horizontalSpacing() + 200:
                    arrangement = 'inline'
                elif available >= control_width:
                    arrangement = 'wrapped'
                else:
                    arrangement = 'narrow'
            self._arrange_seek_bar(arrangement)
            return
        if docked:
            self.seek_bar.layout().removeWidget(self.playback_card)
            self.playback_card.setParent(self.workspace_grid_widget)
            self.playback_card.setVisible(visible)
        self._arrange_seek_bar('single')
        index=max(0,getattr(self.vm,'active_cine_index',0)) if self.vm else 0
        pane=self.workspace_panes[index]
        viewport=pane.graph.viewport()
        corner=viewport.mapTo(self.workspace_grid_widget,viewport.rect().bottomRight())
        self.playback_card.move(max(0,corner.x()-self.playback_card.width()-10),max(0,corner.y()-self.playback_card.height()-9))
        self.playback_card.raise_()
        if self.path_fade_panel.isVisible():self.path_fade_panel.raise_()

    def _sync_motion_filter(self,*args):
        if self.vm is None:return
        track=self.vm.track_data.get(self.vm.active_object)
        settings=(track or {}).get('motion_filter',{'mode':'local_polynomial','window':21})
        with QSignalBlocker(self.motion_filter):self.motion_filter.setCurrentIndex(max(0,self.motion_filter.findData(settings['mode'])))
        with QSignalBlocker(self.motion_window):self.motion_window.setValue(settings.get('window',21))
        self.motion_filter.setEnabled(track is not None)
        self.motion_window.setEnabled(track is not None and settings['mode']=='local_polynomial')
        self.motion_hint.setText('Local cubic fit · raw positions unchanged' if settings['mode']=='local_polynomial' else 'Raw differences · sensitive to point jitter')

    def _change_motion_filter(self,*args):
        if self.vm is None or self._tracking_busy():return
        track=self.vm.track_data.get(self.vm.active_object)
        if track is None:return
        track['motion_filter']={'mode':self.motion_filter.currentData(),'window':self.motion_window.value()|1}
        self.vm.track_fig_calculations(obj=track);self.vm.new_track_data.emit(self.vm.track_data,self.vm.active_object)
        self.vm.sync_active_workspace_context();self.vm.redraw_cb()
