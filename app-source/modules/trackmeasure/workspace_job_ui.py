"""Whole-job capture/restore wired into the existing native workspace."""
from copy import deepcopy
from pathlib import Path
import logging
import math
from PySide6.QtCore import Qt,QTimer,QPointF,QSignalBlocker
from PySide6.QtGui import QKeySequence,QShortcut,QColor,QTransform
from PySide6.QtWidgets import QWidget,QHBoxLayout,QToolButton,QMenu,QStyle,QLabel,QFileDialog,QMessageBox,QApplication
from workspace_job import (capture_context,restore_tool,save_job,read_job,resolve_media,validate_context)

# Only presentation choices: graph calculations remain the existing application's.
VALUE_WIDGETS=('path_fade_transparency','path_fade_radius','path_point_radius','zoom_slider')
CHECK_WIDGETS=('path_fade_button','path_show_edge_detection','path_heat_map')
VIEW_FIELDS=('_path_fade_enabled','_path_fade_transparency','_path_fade_radius',
    '_path_point_radius_scale','_show_edge_detection','_heat_map_enabled',
    '_preview_object_order','_follow_object_id')
TOOL_BUTTONS=('viewer_tool','track_tool','two_pt_tool','three_pt_tool','two_line_tool','area_tool','scale_tool')


class WorkspaceJobMixin:
    def _create_job_bar(self):
        self.job_path='';self._job_base_title=self.windowTitle();bar=QWidget();layout=QHBoxLayout(bar);layout.setContentsMargins(0,0,0,0);layout.setSpacing(4)
        self.open_job_button=QToolButton(bar);self.save_job_button=QToolButton(bar)
        for button,label,icon in ((self.open_job_button,'Open',QStyle.SP_DirOpenIcon),(self.save_job_button,'Save',QStyle.SP_DialogSaveButton)):
            button.setText(label);button.setIcon(self.style().standardIcon(icon));button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setPopupMode(QToolButton.InstantPopup);button.setFixedHeight(38)
            button.setAccessibleName(label+' complete job');layout.addWidget(button)
        self.open_job_menu=QMenu(self.open_job_button);self.open_job_button.setMenu(self.open_job_menu)
        self.open_job_action=self.open_job_menu.addAction('Open Job…',lambda:self.open_job_dialog())
        self.save_job_menu=QMenu(self.save_job_button);self.save_job_button.setMenu(self.save_job_menu)
        self.save_job_action=self.save_job_menu.addAction('Save Job',lambda:self.save_job_dialog())
        self.save_job_as_action=self.save_job_menu.addAction('Save Job As…',lambda:self.save_job_dialog(save_as=True))
        self.save_job_menu.addSeparator()
        from tracking_analysis_export_ui import open_tracking_analysis_export, analysis_export_available
        self.export_tracking_action=self.save_job_menu.addAction('Export Tracking Analysis…',lambda:open_tracking_analysis_export(self))
        self.export_tracking_action.setEnabled(False)
        self.save_job_menu.aboutToShow.connect(lambda:self.export_tracking_action.setEnabled(analysis_export_available(self)))
        self.open_job_button.setToolTip('Open a complete job (.pcajob), including tracking, measurements and edits')
        self.save_job_button.setToolTip('Save the complete job, or choose Save Job As… for a new copy')
        # Retain status for job consumers without reserving another toolbar row.
        self.job_label=QLabel('Save tracking, measurements and edits together.',bar);self.job_label.hide()
        bar.setToolTip('Jobs reference the original video files. Keep those files, or relink them when opening on another computer.')
        for sequence,callback in [(QKeySequence.StandardKey.Save,self.save_job_dialog),(QKeySequence.StandardKey.Open,self.open_job_dialog),(QKeySequence.StandardKey.SaveAs,lambda:self.save_job_dialog(save_as=True))]:
            shortcut=QShortcut(QKeySequence(sequence),bar);shortcut.activated.connect(callback)
        return bar

    def _set_job_status(self,text):
        self.job_label.setText(text)
        self.save_job_button.setToolTip(text+'\nSave the complete job, or choose Save Job As… for a new copy')

    def _job_work_busy(self):
        thread=getattr(self,'_tracking_thread',None)
        if thread is not None and thread.is_alive():return True
        load=getattr(self,'load_cine_thread',None)
        return load is not None and load.isRunning()

    def capture_job(self):
        if getattr(self,'_guide',None):raise ValueError('Complete or cancel object setup before saving the job.')
        if self._job_work_busy():raise ValueError('Finish or cancel tracking / Cine loading before saving the job.')
        self.vm.sync_active_workspace_context()
        c=self.comparator_panel
        states=deepcopy(c.saved_states)
        if c.session is not None:states[c.session.path]=deepcopy(c.settings())
        workflows = {self.overlay_panel: 'overlay', c: 'comparator', self.bos_panel: 'bos', self.event_finder: 'event_finder'}
        view=dict(tool=next((name for name in TOOL_BUTTONS if getattr(self,name) is self.current_tool),'viewer_tool'),
            workflow=workflows.get(self.workflow_stack.currentWidget(), 'analysis'),
            tab=self.main_tab.currentIndex(), hidden=sorted(self.hidden_cine_indices),clip_ranges=deepcopy(self.workspace_clip_ranges),
            pane_order=[i for i in getattr(self,'pane_order',range(len(self.vm.workspace_contexts))) if i<len(self.vm.workspace_contexts)],
            pane_layout=getattr(self,'pane_layout','auto'),
            play_visible=bool(getattr(self,'group_playback_toggle',None) and self.group_playback_toggle.isChecked()),
            graph=self.fig_dropdown.currentText(),graph_length_unit=getattr(self,'_graph_length_unit',None),annotations=self.track_canvas.show_annotations,
            plot_sizes=self.video_plot_splitter.sizes(),graphs_open=self._graphs_open,timeline_open=self.timeline_toggle.isChecked(),
            splitter=self.tab_splitter.sizes(),theme=self.theme_accent,
            flags={key:deepcopy(getattr(self,key)) for key in VIEW_FIELDS},
            suppressed=list(self._preview_suppressed),
            values={key:getattr(self,key).value() for key in VALUE_WIDGETS if hasattr(self,key)},
            checks={key:getattr(self,key).isChecked() for key in CHECK_WIDGETS if hasattr(self,key)},
            graph_views=[],pan=self._pan_mode_enabled,
            graph_limits=[list(self.track_canvas.ax.get_xlim()),list(self.track_canvas.ax.get_ylim())],
            timeline_zoom=self.timeline_viewport.factor,timeline_scroll=self.timeline_viewport.horizontalScrollBar().value(),
            editor_tab=self.editor_panel.tabs.currentIndex(),editor_open=not self.editor_panel.isHidden(),grade_open=not self.grade_panel.isHidden())
        for pane in self.workspace_panes[:len(self.vm.workspace_contexts)]:
            graph=pane.graph;t=graph.transform();center=graph.mapToScene(graph.viewport().rect().center())
            view['graph_views'].append(dict(transform=[t.m11(),t.m12(),t.m21(),t.m22(),t.dx(),t.dy()],zoom=graph._view_zoom,center=[center.x(),center.y()]))
        return dict(contexts=[capture_context(ctx) for ctx in self.vm.workspace_contexts],active=self.vm.active_cine_index,
            track_type=self.vm.track_type,n_diff=self.vm.n_diff,view=view,
            comparator=dict(states=states,source=c.session.path if c.session is not None else '',position=c.slider.value()),
            overlay=self.overlay_panel.settings(), bos=self.bos_panel.settings(), event_finder=self.event_finder.settings())

    def save_job_path(self,path):
        payload=self.capture_job()
        if not payload['contexts'] and not any(payload['overlay']['paths']) and not payload['comparator']['source'] and not payload['bos']['path'] and not payload['event_finder'].get('path'):
            raise ValueError('Load footage before saving a job.')
        if Path(path).suffix.lower()!='.pcajob':path=str(path)+'.pcajob'
        save_job(path,payload);self.job_path=str(Path(path).resolve())
        self._set_job_status(Path(path).name+' · Saved · Keep original footage files')
        self.setWindowTitle(Path(path).name+' — '+self._job_base_title)
        return self.job_path

    def save_job_dialog(self,save_as=False):
        path='' if save_as else self.job_path
        if not path:
            name=Path(self.cine_path).stem if self.cine_path else 'Phantom job'
            path,_=QFileDialog.getSaveFileName(self,'Save complete job',self.job_path or str(Path.home()/'Desktop'/(name+'.pcajob')),'Phantom job (*.pcajob)')
        if not path:return False
        try:self.save_job_path(path);return True
        except Exception as error:
            logging.exception('Could not save complete job');QMessageBox.warning(self,'Save job',str(error));return False

    def open_job_dialog(self):
        path,_=QFileDialog.getOpenFileName(self,'Open complete job',str(Path.home()/'Desktop'),'Phantom job (*.pcajob)')
        if not path:return
        if self.vm.workspace_contexts or any(self.overlay_panel.settings()['paths']) or self.bos_panel.settings()['path'] or self.event_finder.settings().get('path'):
            answer=QMessageBox.question(self,'Open another job','Save the current job before opening another?',QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Save)
            if answer==QMessageBox.StandardButton.Cancel:return
            if answer==QMessageBox.StandardButton.Save and not self.save_job_dialog():return
        self.request_open_job(path)

    def request_open_job(self,path):
        # Finish short decoder jobs without sharing or closing their readers mid-use.
        self._stop_playback();self.overlay_panel.pause();self.comparator_panel.pause();self.bos_panel.pause()
        if self.event_finder.busy():
            QMessageBox.information(self, 'Open job', 'Finish or cancel the event search before opening another job.')
            return
        if self._job_work_busy():
            QMessageBox.warning(self,'Open job','Finish or cancel tracking / Cine loading first.');return
        if (self.comparator_panel.export_job is not None or getattr(self.bos_panel, '_bos_export_worker', None) is not None
                or (self.export_job is not None and self.export_job.isRunning())):
            QMessageBox.warning(self,'Open job','Finish or cancel the running export first.');return
        if self.overlay_panel.busy() or self.comparator_panel.busy() or self.bos_panel.busy():
            QTimer.singleShot(100,self,lambda:self.request_open_job(path));return
        try:self.open_job_path(path,relink=self._relink_job_media)
        except Exception as error:
            logging.exception('Could not open complete job');QMessageBox.warning(self,'Open job',str(error))

    def _relink_job_media(self,missing):
        path,_=QFileDialog.getOpenFileName(self,'Locate original footage: '+Path(missing).name,str(Path(missing).parent),'Footage (*.cine *.mp4 *.mov *.avi *.m4v *.mkv);;All files (*)')
        return path

    def open_job_path(self,path,relink=None):
        if getattr(self,'_guide',None):self._cancel_guided_setup()
        if self._job_work_busy() or self.event_finder.busy() or self.overlay_panel.busy() or self.comparator_panel.busy() or self.bos_panel.busy():
            raise ValueError('Wait for the current operation before opening a job.')
        payload=resolve_media(read_job(path),path,relink)
        contexts=[]
        try:
            contexts=self.vm.prepare_cine_workspace([c['source'] for c in payload['contexts']]) if payload['contexts'] else []
            for saved,context in zip(payload['contexts'],contexts):validate_context(saved,context)
            self._validate_job_view(payload)
            # Prove saved tools can be reconstructed before replacing any live media.
            for saved,context in zip(payload['contexts'],contexts):restore_tool(self.vm,saved['tool'])
        except Exception:
            for context in contexts:context['cine_handler'].close()
            raise
        self._stop_playback();self._close_sequence_readers();self._leave_camera_comparator()
        self.overlay_panel.shutdown();self.comparator_panel.shutdown()
        # Old comparator state must not leak into the restored job on source switch.
        c=self.comparator_panel;c.sources.clear();c.saved_states={};c.pending_path=None;c.pending_render=None;c.closing=False
        self.overlay_panel.closing=False
        for combo in self.overlay_panel.sources:
            with QSignalBlocker(combo):combo.clear();combo.addItem('Choose footage…','')
        if contexts:
            self.vm.install_cine_workspace(contexts)
            for saved,context in zip(payload['contexts'],contexts):context.update(deepcopy(saved['fields']))
            self.vm.active_cine_index=-1
            self.vm.track_type=payload.get('track_type','Auto');self.vm.n_diff=payload.get('n_diff',4)
            self.vm.activate_cine_cb(payload['active'],save_current=False)
            # Activation emits UI signals; restore saved tools after those callbacks.
            for saved,context in zip(payload['contexts'],contexts):
                context['active_tool']=restore_tool(self.vm,saved['tool'])
                context['_active_track_tool']=context['active_tool'] if saved['tool'] and saved['tool']['kind'] in ('AutoTrackTool','ManualTrackTool') else None
            self.vm._restore_workspace_context(payload['active'])
            # Jobs store measured points as well as derived metrics. Recompute the
            # latter so jobs made by older versions receive corrected kinematics.
            with QSignalBlocker(self.vm):
                for index in range(len(contexts)):
                    self.vm._restore_workspace_context(index)
                    for track in self.vm.track_data.values():
                        self.vm.track_fig_calculations(obj=track)
                    self.vm.sync_active_workspace_context()
                self.vm._restore_workspace_context(payload['active'])
        else:
            self.vm.close_workspace();self.vm.workspace_contexts=[];self.vm.active_cine_index=-1
            self.vm.track_data={};self.vm.meas_data={'_curr_i':0};self.vm.cine_path='';self.cine_path=''
            self.vm.active_tool=None;self.vm.active_object=None
            for pane in self.workspace_panes:pane.graph.scene().clear();pane.hide()
            self.cine_path_disp.setText('No Cine Selected');self.metadata_table.setRowCount(0)
            self.track_canvas.redraw({})
        self._restore_job_view(payload)
        c.saved_states=deepcopy(payload['comparator']['states'])
        source=payload['comparator'].get('source')
        if source:
            c.add_sources(list(c.saved_states),source)
            c._job_restore_position=payload['comparator'].get('position',0)
        self.overlay_panel.apply_settings(payload['overlay'])
        self.bos_panel.apply_settings(payload.get('bos', {}))
        self.event_finder.apply_settings(payload.get('event_finder', {}))
        workflow=payload['view']['workflow']
        if workflow == 'event_finder':
            self.event_tool.setChecked(True)
            self._show_event_finder(restore=True)
        if workflow == 'bos':
            self.bos_tool.setChecked(True)
            self._show_bos(restore=True)
        if workflow=='overlay':self.overlay_tool.setChecked(True);self._show_video_overlay()
        # Retain comparator settings from old jobs, but open the normal workspace
        # while that feature is absent from the Tools menu.
        self.job_path=str(Path(path).resolve());self._set_job_status(Path(path).name+' · Restored · Keep original footage files')
        self.setWindowTitle(Path(path).name+' — '+self._job_base_title)
        logging.info('Restored job %s: %d Cines, %d tracked objects, %s workspace',path,len(self.vm.workspace_contexts),sum(len(c['track_data']) for c in self.vm.workspace_contexts),workflow)
        return payload

    def _validate_job_view(self,p):
        def number(value,low=-1e12,high=1e12):
            return type(value) in (int,float) and math.isfinite(value) and low<=value<=high
        def numbers(values,length,low=-1e12,high=1e12):
            return isinstance(values,(list,tuple)) and len(values)==length and all(number(x,low,high) for x in values)
        v=p.get('view')
        if not isinstance(v,dict) or v.get('tool') not in TOOL_BUTTONS or v.get('workflow') not in ('analysis','overlay','comparator','bos','event_finder'):
            raise ValueError('Invalid saved workspace view.')
        if 'event_finder' in p:
            from event_finder_ui import validate_event_settings
            p['event_finder'] = validate_event_settings(p['event_finder'])
        from bos_frames import validate_bos_settings
        if 'bos' in p:
            p['bos'] = validate_bos_settings(p['bos'])
        if p.get('track_type') not in ('Manual','Auto') or type(p.get('n_diff')) is not int or not 1<=p['n_diff']<=10000:
            raise ValueError('Invalid saved tracking settings.')
        count=len(p['contexts'])
        graph_unit=v.get('graph_length_unit')
        if graph_unit is not None and (not isinstance(graph_unit,str) or len(graph_unit)>32):raise ValueError('Invalid graph length unit.')
        from workspace_job import validate_pane_view
        validate_pane_view(v,count)
        if any(type(i) is not int or not 0<=i<count for i in v['hidden']) or (count and len(set(v['hidden']))>=count):raise ValueError('Invalid hidden Cine list.')
        if count and p['active'] in v['hidden']:raise ValueError('The active Cine cannot be hidden.')
        if type(v['tab']) is not int or not 0<=v['tab']<3 or self.fig_dropdown.findText(v['graph'])<0:raise ValueError('Invalid saved graph selection.')
        if 'plot_sizes' in v and not numbers(v['plot_sizes'],2,0,100000):raise ValueError('Invalid video/plot divider.')
        if not numbers(v['splitter'],2,0,100000) or not QColor(v['theme']).isValid():raise ValueError('Invalid saved layout.')
        if len(v['graph_views'])!=count:raise ValueError('Incomplete saved Cine views.')
        for view in v['graph_views']:
            if not numbers(view['transform'],6) or not numbers(view['center'],2) or not number(view['zoom'],1,20):raise ValueError('Invalid saved image view.')
        if not isinstance(v['flags'],dict) or set(v['flags'])!=set(VIEW_FIELDS) or any(k not in VALUE_WIDGETS or not number(value) for k,value in v['values'].items()) or any(k not in CHECK_WIDGETS or type(value) is not bool for k,value in v['checks'].items()):raise ValueError('Invalid display options.')
        for key in ('_path_fade_transparency','_path_fade_radius','_path_point_radius_scale'):
            if not number(v['flags'][key],0,100000):raise ValueError('Invalid tracking display options.')
        if not isinstance(v['flags']['_preview_object_order'],list) or not isinstance(v['suppressed'],list):raise ValueError('Invalid object previews.')
        if not isinstance(v['clip_ranges'],dict):raise ValueError('Invalid clip ranges.')
        from viewer_timeline import total_frames
        for index,bounds in v['clip_ranges'].items():
            if type(index) is not int or not 0<=index<count or not numbers(bounds,2,0) or any(type(x) is not int for x in bounds) or not bounds[0]<=bounds[1]<total_frames(p['contexts'][index]['fields']['viewer_edit']):raise ValueError('Invalid clip range.')
        for limits in v.get('graph_limits',[]):
            if not numbers(limits,2) or limits[0]==limits[1]:raise ValueError('Invalid saved graph limits.')
        if not number(v.get('timeline_zoom',1),1,1000) or not number(v.get('timeline_scroll',0),0,8_000_000):raise ValueError('Invalid timeline view.')
        from video_overlay import MAX_OVERLAY_PLAYBACK_FPS, default_overlay
        o=p['overlay']
        if set(o)!=set(default_overlay()) or any(not isinstance(o[k],list) or len(o[k])!=2 for k in ('paths','opacity','offsets','raw_t0','t0_frames')):raise ValueError('Invalid overlay setup.')
        if o['mode'] not in ('Blend','Difference') or not numbers(o['opacity'],2,0,100) or not numbers(o['offsets'],2,-1000000,1000000) or not numbers(o['t0_frames'],2,-2147483647,2147483647) or not all(type(x) is bool for x in o['raw_t0']) or not number(o['playback_fps'],.1,MAX_OVERLAY_PLAYBACK_FPS) or not number(o['position'],0,50_000_000) or not number(o['x'],-20000,20000) or not number(o['y'],-20000,20000):raise ValueError('Invalid overlay settings.')
        from camera_comparator import default_settings,PROFILES,available_modes
        for s in p['comparator']['states'].values():
            if not isinstance(s,dict) or not set(default_settings(1)).issubset(s) or not isinstance(s['source'],dict):raise ValueError('Invalid comparator setup.')
            src=s['source']
            required=('width','height','pitch_um','binning','capture_fps','exposure_us','center_x_um','center_y_um')
            if any(not number(src.get(k)) for k in required) or src.get('profile_id') not in ('',None,*PROFILES) or s['target'] not in PROFILES or s['variant'] not in PROFILES[s['target']]['variants']:raise ValueError('Invalid comparator camera settings.')
            if s['mode'] is not None and s['mode'] not in [m['id'] for m in available_modes(s['target'])]:raise ValueError('Invalid comparator mode.')
            for key in ('target_fps','playback_fps','phase','first','last','aim_x','aim_y','gamma','feature_pixels'):
                if not number(s[key]):raise ValueError('Invalid comparator setting.')

    def _restore_job_view(self,p):
        v=p['view'];vm=self.vm
        self.pane_order=list(v.get('pane_order',range(len(vm.workspace_contexts))))
        self.pane_layout=v.get('pane_layout','auto')
        if hasattr(self,'group_playback_toggle'):
            with QSignalBlocker(self.group_playback_toggle):self.group_playback_toggle.setChecked(v.get('play_visible',False))
        self.hidden_cine_indices=set(v['hidden']);self.workspace_clip_ranges=deepcopy(v['clip_ranges'])
        self._preview_suppressed=set(v['suppressed'])
        for key,value in v['flags'].items():
            if key in VIEW_FIELDS:setattr(self,key,deepcopy(value))
        for key,value in v['values'].items():
            if key in VALUE_WIDGETS and hasattr(self,key):getattr(self,key).setValue(value)
        for key,value in v['checks'].items():
            if key in CHECK_WIDGETS:getattr(self,key).setChecked(value)
        self.apply_theme_color(QColor(v['theme']),persist=False)
        self._graph_length_unit=v.get('graph_length_unit')
        self.track_canvas.show_annotations=v['annotations'];self.fig_dropdown.setCurrentText(v['graph'])
        self.tab_splitter.setSizes(v['splitter']);self.main_tab.setCurrentIndex(v['tab'])
        self._plot_sizes=v.get('plot_sizes');self._graphs_open=v.get('graphs_open',True)
        self.graphs_button.setChecked(self._graphs_open);self.timeline_toggle.setChecked(v.get('timeline_open',False))
        if self._plot_sizes:self.video_plot_splitter.setSizes(self._plot_sizes)
        self.analysis_tools_bg.setExclusive(False)
        for b in self.analysis_tools_bg.buttons():b.setChecked(False)
        self.current_tool=getattr(self,v['tool']);self.current_tool.setChecked(True);self.analysis_tools_bg.setExclusive(True)
        self.track_type_man.setChecked(vm.track_type=='Manual');self.track_type_auto.setChecked(vm.track_type=='Auto')
        self._set_viewer_controls_visible(self.current_tool is self.viewer_tool)
        self._set_tracking_overlay_visible(self.current_tool is self.track_tool)
        if hasattr(self, '_collapse_details'):
            self._tracking_editor_open=False
            self.track_pages.setTabVisible(0,False)
            self.track_pages.setCurrentIndex(1)
            self._collapse_details()
            self._sync_mode_menu()
        if vm.workspace_contexts:
            self._configure_workspace_grid(len(vm.workspace_contexts))
            bounds=self.workspace_clip_ranges.get(vm.active_cine_index)
            if bounds:
                with QSignalBlocker(self.clip_range):self.clip_range.setValues(*bounds)
            self._sync_viewer_editor()
            vm.new_track_data.emit(vm.track_data,vm.active_object);vm.track_fig_changed.emit(vm.track_data);vm.draw_all()
            self.track_tab.setEnabled(self.current_tool is self.track_tool)
            self.refresh_workspace_previews()
            # Fit events occur after layout changes; restore pan/zoom afterwards.
            self._set_pan_mode(v.get('pan',False))
            self.timeline_viewport.set_zoom(v.get('timeline_zoom',1))
            self.timeline_viewport.horizontalScrollBar().setValue(v.get('timeline_scroll',0))
            self.editor_panel.tabs.setCurrentIndex(v.get('editor_tab',0))
            if self.current_tool is self.viewer_tool:
                self.editor_panel.setVisible(v.get('editor_open',False));self.grade_panel.setVisible(v.get('grade_open',False))
            QTimer.singleShot(0,self,lambda:self._restore_job_graph_views(v['graph_views'],v.get('graph_limits')))

    def _restore_job_graph_views(self,views,limits=None):
        for pane,saved in zip(self.workspace_panes,views):
            pane.graph.setTransform(QTransform(*saved['transform']));pane.graph._view_zoom=saved['zoom']
            pane.graph.centerOn(QPointF(*saved['center']))

        if limits:
            self.track_canvas.ax.set_xlim(*limits[0]);self.track_canvas.ax.set_ylim(*limits[1]);self.track_canvas.draw_idle()
