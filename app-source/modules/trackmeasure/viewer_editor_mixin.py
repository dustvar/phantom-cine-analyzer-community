"""Connect the native Viewer editor to workspace state, transport and export."""
from copy import deepcopy
import os
import math
import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, QSignalBlocker
from PySide6.QtWidgets import QPushButton, QFileDialog, QMessageBox, QDialog, QComboBox
from viewer_timeline import Timeline, default_edit, split_at, move_cut, validate_edit, section_at, default_camera_overlay, copy_options, copy_index, total_frames, copy_offset
from viewer_timeline_ui import TimelineEditor, SectionTimeline
from viewer_extras_ui import TimelineViewport
from viewer_sequence_mixin import ViewerSequenceMixin
from playback_preview import PlaybackPreviewMixin
from viewer_project import save_project, load_project
from viewer_transition_ui import TransitionDialog


class ViewerEditorMixin(PlaybackPreviewMixin, ViewerSequenceMixin):
    def _create_editor_controls(self):
        self._viewer_plan_cache = None
        self._viewer_preview_time = None
        self._overlay_drag_state = None
        self.timeline_button = QPushButton('Speed && Effects')
        self.fps_button = QPushButton('Data && Text')
        for button in (self.timeline_button, self.fps_button):
            button.setCheckable(True); button.setEnabled(False)
            button.setObjectName('qt_viewer_action')
        self.timeline_button.setToolTip('Edit the selected segment’s speed, transitions, zoom and text.')
        self.fps_button.setToolTip('Add playback data, camera details or custom text to the video.')
        self.split_button = QPushButton('Split')
        self.split_button.setObjectName('viewer_split_button')
        self.split_button.setEnabled(False)
        self.split_button.setAccessibleName('Split at the current frame')
        self.split_button.setToolTip('Cut the timeline at the preview frame (Ctrl/Cmd+B). Each segment can have its own speed.')
        self.track_fade_button = QPushButton('Tracking fade…')
        self.track_fade_button.setEnabled(False)
        self.track_fade_button.setToolTip('Reveal measured object markers and trails at the impact frame.')
        self.track_fade_button.clicked.connect(self._edit_tracking_fade)
        self.editor_panel = TimelineEditor()
        self.editor_panel.hide()
        self.section_timeline = SectionTimeline()
        self.timeline_viewport=TimelineViewport(self.section_timeline)
        self.multiply_button=QPushButton('Multiply Video')
        self.add_video_button=QPushButton('Add New Video')
        self.video_selector=QComboBox();self.video_selector.setMinimumWidth(150);self.video_selector.setMaximumWidth(300)
        self.multiply_button.setToolTip('Append one fresh copy of the selected video, without its edits.')
        self.add_video_button.setToolTip('Append another Cine, MP4, MOV, AVI, M4V, or MKV to this sequence.')
        self.timeline_zoom_out=QPushButton('−');self.timeline_zoom_in=QPushButton('+');self.timeline_fit=QPushButton('Fit')
        self.timeline_zoom_out.setToolTip('Zoom out of the timeline');self.timeline_zoom_in.setToolTip('Zoom into the timeline')
        for button in (self.timeline_zoom_out,self.timeline_zoom_in,self.timeline_fit):
            button.setObjectName('viewer_timeline_zoom');button.setFixedWidth(36)
        for button in (self.multiply_button,self.add_video_button):
            button.setObjectName('viewer_sequence_action');button.setEnabled(False)
        self.timeline_undo_button = QPushButton('Undo')
        self.timeline_redo_button = QPushButton('Redo')
        for button in (self.timeline_undo_button, self.timeline_redo_button):
            button.setEnabled(False)
            button.setObjectName('viewer_history_button')
            button.setFixedWidth(60)
            button.setFixedHeight(28)
        self.timeline_undo_button.setToolTip('Undo the last Viewer edit, including a cut move.')
        self.timeline_redo_button.setToolTip('Redo the last undone Viewer edit.')

    def _connect_viewer_editor(self):
        self.split_button.clicked.connect(self._split_viewer_section)
        self.multiply_button.clicked.connect(lambda:self._append_sequence_video())
        self.add_video_button.clicked.connect(lambda:self._append_sequence_video(new=True))
        self.video_selector.currentIndexChanged.connect(self._select_sequence_video)
        self.timeline_zoom_in.clicked.connect(lambda:self.timeline_viewport.set_zoom(self.timeline_viewport.factor*2))
        self.timeline_zoom_out.clicked.connect(lambda:self.timeline_viewport.set_zoom(self.timeline_viewport.factor/2))
        self.timeline_fit.clicked.connect(lambda:self.timeline_viewport.set_zoom(1))
        self.editor_panel.crop_edit_button.toggled.connect(self._crop_edit_changed)
        self.editor_panel.tabs.currentChanged.connect(self._crop_edit_changed)
        self.editor_panel.textSeekRequested.connect(self._seek_custom_text)
        self.timeline_button.toggled.connect(lambda checked: self._select_viewer_panel(self.timeline_button, checked))
        self.fps_button.toggled.connect(lambda checked: self._select_viewer_panel(self.fps_button, checked))
        self.editor_panel.changed.connect(self._apply_viewer_edit)
        self.editor_panel.dataBoxSelected.connect(self._enable_viewer_data_box)
        self.editor_panel.seek.connect(self._timeline_seek)
        self.section_timeline.seek.connect(self._timeline_seek)
        self.section_timeline.transitionRequested.connect(self._edit_viewer_transition)
        self.section_timeline.cutDragStarted.connect(self._stop_playback)
        self.section_timeline.cutMoved.connect(self._move_viewer_cut)
        self.timeline_undo_button.clicked.connect(self._undo_viewer_edit)
        self.timeline_redo_button.clicked.connect(self._redo_viewer_edit)
        self.editor_panel.transitionRequested.connect(self._edit_viewer_transition)
        self.editor_panel.splitRequested.connect(self._split_viewer_section)
        self.editor_panel.undoRequested.connect(self._undo_viewer_edit)
        self.editor_panel.redoRequested.connect(self._redo_viewer_edit)
        self.editor_panel.resetRequested.connect(self._reset_viewer_edit)
        self.editor_panel.saveRequested.connect(self._save_viewer_project)
        self.editor_panel.loadRequested.connect(self._load_viewer_project)
        self.frame_slider.valueChanged.connect(self._sync_timeline_position)

    def _has_viewer_edit(self):
        return self.vm is not None and bool(getattr(self.vm, 'viewer_edit', {}).get('sections'))

    def _select_viewer_panel(self, button, checked):
        if checked:
            for other in (self.levels_button, self.timeline_button, self.fps_button):
                if other is not button:
                    blocker = QSignalBlocker(other); other.setChecked(False); del blocker
            if button is self.fps_button:
                self.editor_panel.tabs.setCurrentIndex(1)
                self._enable_viewer_data_box(self.editor_panel.active_data_box)
            elif button is self.timeline_button:
                self.editor_panel.tabs.setCurrentIndex(0)
        self._sync_viewer_panels()

    def _enable_viewer_data_box(self, box):
        if not self._has_viewer_edit():
            return
        key = 'camera_overlay' if box == 'camera' else 'overlay'
        edit = deepcopy(self.vm.viewer_edit)
        options = copy_options(edit,self._viewer_copy()).setdefault(key,default_camera_overlay())
        if not options['enabled']:
            options['enabled'] = True
            self._apply_viewer_edit(edit)

    def _return_to_video_tools(self):
        if self._details_open:
            self._collapse_details()
        for button in (self.levels_button, self.timeline_button, self.fps_button):
            with QSignalBlocker(button):
                button.setChecked(False)
        self._sync_viewer_panels()
        self.timeline_toggle.setFocus(Qt.ShortcutFocusReason)

    def _layout_viewer_panels(self):
        ready = getattr(self, '_layout_ready', False)
        viewer = (self.current_tool is self.viewer_tool and not getattr(self, '_guide', None)
                  if ready else not self.viewer_controls.isHidden())
        editing = self.timeline_button.isChecked() or self.fps_button.isChecked()
        grading = self.levels_button.isChecked()
        show_editor = viewer
        if ready:
            inspector = self._details_open
            # Use the width of the whole working area. It stays stable when the
            # toolbox disappears, including while dragging the inspector divider.
            body = self.cine_disp_col.width()
            editor_width = (self.editor_panel.width() if editing else
                            self.grade_panel.width() if grading else 0)
            compact = body < 400 + self.viewer_side_tools.width() + editor_width
            show_editor = viewer and not (compact and inspector)
            collapse_tools = compact and (editing or grading or inspector)
            loaded = viewer and bool(self.cine_path)
            self.video_tools_back.setVisible(loaded and collapse_tools and not inspector)
            self.viewer_side_tools.setVisible(loaded and not collapse_tools)
        self.grade_panel.setVisible(show_editor and grading)
        self.editor_panel.setVisible(show_editor and editing)

    def _sync_viewer_panels(self):
        self._layout_viewer_panels()
        if not self.grade_panel.isHidden() and self.vm is not None:
            self.grade_panel.set_image(self.vm.transformed_img)

    def _viewer_timeline(self):
        if not self._has_viewer_edit():
            return None
        bounds = (self.clip_range.lowerValue(), self.clip_range.upperValue())
        key = (id(self.vm.viewer_edit), self.vm.active_cine_index, *bounds)
        if self._viewer_plan_cache is None or self._viewer_plan_cache[0] != key:
            self._viewer_plan_cache = (key, Timeline(self.vm.viewer_edit, *bounds))
        return self._viewer_plan_cache[1]

    def _sync_viewer_editor(self):
        if not self._has_viewer_edit():
            return
        self._viewer_plan_cache = None
        self._viewer_preview_time = None
        edit = self.vm.viewer_edit
        self._sync_sequence_range()
        self._sync_copy_controls()
        # These describe the selected source, not the last asynchronous frame.
        self._sync_orientation_controls()
        self._orient_graph(self.graph)
        self.editor_panel.set_edit(edit, frame=self._viewer_frame(),
            first_frame=self.vm.cine_handler.metadata.FirstImageNo,
            title=f'C{self.vm.active_cine_index + 1} · {os.path.basename(self.vm.cine_path)}',
            undo=bool(self.vm._viewer_edit_undo), redo=bool(self.vm._viewer_edit_redo))
        self.section_timeline.set_edit(edit, self.clip_range.lowerValue(), self.clip_range.upperValue(),
            self.vm.cine_handler.metadata.FirstImageNo)
        self.timeline_undo_button.setEnabled(bool(self.vm._viewer_edit_undo))
        self.timeline_redo_button.setEnabled(bool(self.vm._viewer_edit_redo))
        self._sync_timeline_position()
        self.timeline_button.setEnabled(True); self.fps_button.setEnabled(True)
        self.track_fade_button.setEnabled(True)
        self.split_button.setEnabled(True)
        self.multiply_button.setEnabled(True);self.add_video_button.setEnabled(True)
        self._update_export_available()
        if hasattr(self, '_sync_transport_fps'):
            self._sync_transport_fps()

    def _update_export_available(self):
        from video_export_ui import original_range
        plan = self._viewer_timeline()
        available = plan is not None and plan.frame_count > 0
        if plan is not None and not available:
            try:
                original_range(self.vm.cine_path, self.vm.viewer_edit,
                               self.clip_range.lowerValue(), self.clip_range.upperValue())
                available = True
            except ValueError:
                pass
        self.export_button.setEnabled(available and self.export_job is None)

    def _sync_timeline_position(self, frame=None):
        if not self._has_viewer_edit():
            return
        frame = self.frame_slider.value() if frame is None else frame
        self.section_timeline.set_frame(frame)
        if self._playback_active:self.timeline_viewport.ensureVisible(round(self.section_timeline._frame_x(frame)),28,40,0)
        self.editor_panel.set_frame(frame)
        plan = self._viewer_timeline()
        if not plan.parts:
            self.editor_panel.status.setText('All sections in this In/Out range are skipped.')
            return
        time = self._viewer_preview_time
        if time is None or plan.sample(time)[0] != frame:
            time = plan.time_for_source(frame)
        _, rate = plan.sample(time)
        text = f'{time:,.2f} / {plan.output_duration:,.2f} s · {rate:,.1f} fps playback'
        if plan.ramp_limits:
            text += '\nA transition is shortened to fit the available footage.'
        self.editor_panel.status.setText(text)

    def _apply_viewer_edit(self, edit, record=True):
        if not self._has_viewer_edit() or edit == self.vm.viewer_edit:
            return
        edit = validate_edit(edit, self.vm.cine_handler.metadata.ImageCount)
        self._stop_playback()
        if record:
            self.vm._viewer_edit_undo.append(deepcopy(self.vm.viewer_edit))
            self.vm._viewer_edit_undo = self.vm._viewer_edit_undo[-50:]
            self.vm._viewer_edit_redo.clear()
        self.vm.viewer_edit = deepcopy(edit)
        self.vm.sync_active_workspace_context()
        self._sync_viewer_editor()
        self._sequence_frame_changed(self.frame_slider.value())
        self.vm.redraw_cb()

    def _split_viewer_section(self):
        if self.current_tool == self.viewer_tool and self._has_viewer_edit():
            self._apply_viewer_edit(split_at(self.vm.viewer_edit, self.frame_slider.value()))

    def _move_viewer_cut(self, right_index, frame):
        if self.current_tool == self.viewer_tool and self._has_viewer_edit():
            self._apply_viewer_edit(move_cut(self.vm.viewer_edit, right_index, frame))

    def _edit_viewer_transition(self, right_index):
        if self.current_tool != self.viewer_tool or not self._has_viewer_edit():
            return
        sections = self.vm.viewer_edit['sections']
        if not 0 < right_index < len(sections) or not sections[right_index]['keep'] or not any(s['keep'] for s in sections[:right_index]):
            return
        self._stop_playback()
        dialog = TransitionDialog(self, self.vm.viewer_edit, right_index, self.vm.cine_handler.metadata.FirstImageNo)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_viewer_edit(dialog.result_edit())
        dialog.deleteLater()

    def _undo_viewer_edit(self):
        if self._has_viewer_edit() and self.vm._viewer_edit_undo:
            self.vm._viewer_edit_redo.append(deepcopy(self.vm.viewer_edit))
            self._apply_viewer_edit(self.vm._viewer_edit_undo.pop(), record=False)

    def _redo_viewer_edit(self):
        if self._has_viewer_edit() and self.vm._viewer_edit_redo:
            self.vm._viewer_edit_undo.append(deepcopy(self.vm.viewer_edit))
            self._apply_viewer_edit(self.vm._viewer_edit_redo.pop(), record=False)

    def _reset_viewer_edit(self):
        if self._has_viewer_edit():
            edit = default_edit(self.vm.cine_handler.metadata.ImageCount, self.vm.viewer_edit['output_fps'])
            edit['overlay'] = deepcopy(self.vm.viewer_edit['overlay'])
            edit['camera_overlay'] = deepcopy(self.vm.viewer_edit.get('camera_overlay', default_camera_overlay()))
            edit['loop'] = self.vm.viewer_edit['loop']
            self._apply_viewer_edit(edit)

    def _timeline_seek(self, frame):
        self._stop_playback()
        self._viewer_preview_time = None
        previous=self.frame_slider.value()
        self.frame_slider.setValue(frame)
        if previous==frame:self._sequence_frame_changed(frame);self.vm.redraw_cb()
        self._sync_timeline_position()
        self.timeline_viewport.ensureVisible(round(self.section_timeline._frame_x(frame)),28,30,0)

    def _start_viewer_edit_playback(self, step):
        if self.current_tool != self.viewer_tool or not self._has_viewer_edit():
            return False
        self.editor_panel.crop_edit_button.setChecked(False)
        self.editor_panel._commit_text()
        plan = self._viewer_timeline()
        if not plan.frame_count or not step:
            self._stop_playback()
            self.status_bar.setText('Include a section in the clip to preview it.')
            return True
        self._playback_step = int(step)
        first, _ = plan.sample(0)
        last, _ = plan.sample((plan.frame_count - 1) / plan.output_fps)
        current = self.frame_slider.value()
        time = self._viewer_preview_time
        if time is None or plan.sample(time)[0] != current:
            time = plan.time_for_source(current)
        if step > 0 and (current >= last or current < first):
            time = 0.0
        elif step < 0 and (current <= first or current > last):
            time = (plan.frame_count - 1) / plan.output_fps
        tick = math.ceil(time * plan.output_fps - 1e-8) if step > 0 else math.floor(time * plan.output_fps + 1e-8)
        self._viewer_origin_tick = min(plan.frame_count - 1, max(0, tick))
        self._playback_active = True
        self._sync_playback_controls(True)
        self._begin_preview_buffer()
        self._reset_transport_clock()
        frames = self._viewer_preview_frames(plan, self._viewer_origin_tick)
        if frames[0] != current:
            if not self._prepare_preview(frames, True):
                self._await_preview(self._viewer_origin_tick)
                return True
            self._viewer_preview_time = self._viewer_origin_tick / plan.output_fps
            self._show_transport_frame(frames[0])
        self._viewer_preview_time = self._viewer_origin_tick / plan.output_fps
        upcoming = self._viewer_preview_frames(plan, self._viewer_origin_tick + self._playback_step)
        self._prepare_preview(upcoming, True, prefetch_only=True)
        if self._playback_active:
            self._queue_next_tick(plan.output_fps)
        return True

    def _viewer_preview_frames(self, plan, tick, step=None):
        frames = []
        step = self._playback_step if step is None else step
        for i in range(12):
            position = tick + i * step
            if self.vm.viewer_edit['loop']:
                position %= plan.frame_count
            else:
                position = max(0, min(plan.frame_count - 1, position))
            frames.append(plan.sample(position / plan.output_fps)[0])
        return frames

    def _advance_viewer_edit_playback(self):
        if self.current_tool != self.viewer_tool or not self._has_viewer_edit():
            return False
        plan = self._viewer_timeline()
        if not plan.parts:
            self._stop_playback()
            return True
        pending = self._pending_tick
        elapsed_ticks = math.floor(self._viewer_clock.elapsed() * plan.output_fps / 1000)
        tick = self._viewer_origin_tick + elapsed_ticks * self._playback_step if pending is None else pending
        ended = tick >= plan.frame_count if self._playback_step > 0 else tick <= 0
        if self.vm.viewer_edit['loop']:
            tick %= plan.frame_count
            ended = False
        frames = self._viewer_preview_frames(plan, tick)
        if not self._prepare_preview(frames, True):
            # Pin the missed sample, even when a slow drive takes several periods.
            # Otherwise a fast transport could keep outrunning its own decoder.
            self._await_preview(tick)
            return True
        tick = max(0, min(plan.frame_count - 1, tick))
        if pending is not None:
            self._viewer_origin_tick = tick
            self._reset_transport_clock()
        seconds = tick / plan.output_fps
        refresh = self._viewer_preview_time != seconds or self.frame_slider.value() != frames[0]
        self._viewer_preview_time = seconds
        self._show_transport_frame(frames[0], force=refresh)
        if ended:
            self._stop_playback()
        elif self._playback_active:
            self._queue_next_tick(plan.output_fps)
        return True

    def _save_viewer_project(self):
        if not self._has_viewer_edit(): return
        self._stop_playback()
        self.editor_panel._commit_text()
        suggested = os.path.join(os.path.expanduser('~/Desktop'), os.path.splitext(os.path.basename(self.vm.cine_path))[0] + '.pcaedit')
        path, _ = QFileDialog.getSaveFileName(self, 'Save Viewer Edit', suggested, 'PCA Viewer edit (*.pcaedit)')
        if not path: return
        if not path.lower().endswith('.pcaedit'):
            path += '.pcaedit'
            if os.path.exists(path) and QMessageBox.question(
                    self, 'Replace Viewer Edit?', f'Replace {os.path.basename(path)}?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        try:
            save_project(path, self.vm.cine_path, self.clip_range.lowerValue(), self.clip_range.upperValue(),
                         self.vm.viewer_edit, self.vm.viewer_grade, self.vm.image_settings_snapshot())
            self.status_bar.setText(f'Viewer edit saved: {path}')
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Save Edit', str(error))

    def _load_viewer_project(self):
        if not self._has_viewer_edit(): return
        self._stop_playback()
        path, _ = QFileDialog.getOpenFileName(self, 'Load Viewer Edit', os.path.expanduser('~/Desktop'), 'PCA Viewer edit (*.pcaedit)')
        if not path: return
        try:
            payload = load_project(path, self.vm.cine_path, self.vm.cine_handler.metadata.ImageCount)
        except Exception as error:
            QMessageBox.warning(self, 'Could Not Load Edit', str(error)); return
        self._apply_viewer_edit(payload['edit'])
        self.vm.viewer_grade = payload['grade']
        for key, value in payload['image_settings'].items():
            setattr(self.vm, key, value)
        self.vm.sync_active_workspace_context()
        self.clip_range.setValues(*payload['clip'])
        self.grade_panel.set_grade(self.vm.viewer_grade)
        self.on_new_cine_load(self.vm.cine_path, self.vm.cine_handler.metadata, preserve_mode=True)
        self._sync_viewer_editor()
        self.vm.redraw_cb()
        self.status_bar.setText(f'Viewer edit loaded: {path}')
