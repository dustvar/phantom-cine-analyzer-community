"""Interactive tracking graph: display-only navigation and explicit sample edits."""
from copy import deepcopy
from pathlib import Path
import re
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.font_manager import FontProperties
from PySide6.QtCore import Qt, QTimer, QEvent, QSignalBlocker
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget, QHBoxLayout, QToolButton, QLabel, QComboBox, QSizePolicy, QMenu
from cine_metadata import capture_time_seconds
from tracking_graph_data import (LENGTH_UNITS, LENGTH_METERS, normalize_length_unit,
                                 metric_mode, metric_units, graph_series)


class TrackingGraph(FigureCanvas):
    def __init__(self, parent):
        self.parent_window = parent; self.vm = parent.vm
        self.fig = Figure(facecolor='#20262e'); super().__init__(self.fig); self.setParent(parent)
        self.setMinimumHeight(172); self.setFocusPolicy(Qt.StrongFocus); self.setMouseTracking(True)
        self.ax = self.fig.add_subplot(111); self.ax.set_visible(False)
        self.frame_pos_line = None; self.frame_value_line = None; self.selection_marker = None
        self.show_annotations = True; self._plot_background = None
        self._redrawing_plot = False; self._pending_plot_data = None; self._last_data = {}
        self._manual_limits = False; self._plot_signature = None; self._series = []
        self._selected = None; self._selected_index = None; self._press = None; self._range = None; self._range_patch = None
        self._create_controls()
        self.mpl_connect('draw_event', self._capture_plot_background)
        self.mpl_connect('resize_event', self._resize_plot)
        self.mpl_connect('scroll_event', self._on_scroll)
        self.mpl_connect('button_press_event', self._on_press)
        self.mpl_connect('button_release_event', self._on_release)
        self.mpl_connect('motion_notify_event', self._on_motion)

    @property
    def owner(self):
        return getattr(self.parent_window, 'parent_window', self.parent_window)

    def _create_controls(self):
        self.controls = QWidget(self); self.controls.setObjectName('tracking_graph_controls')
        row = QHBoxLayout(self.controls); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(4)
        def button(text, tip, callback):
            b = QToolButton(self.controls); b.setText(text); b.setToolTip(tip)
            b.setAccessibleName(tip); b.clicked.connect(callback); row.addWidget(b); return b
        self.zoom_out_button = button('−', 'Zoom graph out', lambda: self.zoom_graph(1/1.5))
        self.fit_button = button('Fit', 'Fit all plotted values', self.fit_graph)
        self.zoom_in_button = button('+', 'Zoom graph in', lambda: self.zoom_graph(1.5))
        self.delete_range_button = button('Delete range', 'Pick two points on the same line, then confirm removal', self.begin_delete_range)
        self.confirm_range_button = button('Confirm', 'Delete the selected source measurements, including both endpoints', self.confirm_delete_range)
        self.cancel_range_button = button('Cancel', 'Cancel range selection without removing points', self.cancel_delete_range)
        self.undo_button = button('Undo', 'Undo the last tracking-point deletion', self.undo_delete)
        self.undo_button.setEnabled(False)
        self.confirm_range_button.hide(); self.cancel_range_button.hide()
        self.value_label = QLabel('Hover a point · drag to pan', self.controls)
        self.value_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.value_label.setMinimumWidth(0); row.addWidget(self.value_label, 1)
        self.controls.adjustSize()
        self.setToolTip('Scroll to zoom both axes. Drag to pan. Hover or click a point, then use ← / → to inspect neighboring samples; Delete removes its source measurement.')

    def install_units_control(self, layout):
        combo = QComboBox(self.parent_window); combo.setObjectName('tracking_graph_units')
        combo.setAccessibleName('Graph length units')
        combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.units_combo = combo; self.parent_window.graph_units = combo
        layout.addWidget(combo); combo.currentIndexChanged.connect(self._units_changed)
        self._sync_units_control(); return combo

    def _display_length(self):
        source = normalize_length_unit(getattr(self.parent_window.vm, 'length_units', 'pix'))
        selected = normalize_length_unit(getattr(self.owner, '_graph_length_unit', None) or source)
        if source == 'pix': return 'pix'
        if selected not in LENGTH_UNITS and selected != source: return source
        return selected

    def _sync_units_control(self):
        if not hasattr(self, 'units_combo'): return
        vm = self.parent_window.vm; source = normalize_length_unit(getattr(vm, 'length_units', 'pix'))
        mode = metric_mode(self.parent_window.fig_dropdown.currentText()) if hasattr(self.parent_window, 'fig_dropdown') else ''
        enabled = source != 'pix' and mode not in ('Angle', 'Angular Speed', 'Vibration')
        options = list(LENGTH_UNITS) if source in LENGTH_METERS else [source] + (['pix'] if source != 'pix' else [])
        selected = self._display_length()
        with QSignalBlocker(self.units_combo):
            self.units_combo.clear()
            for unit in options: self.units_combo.addItem('px' if unit == 'pix' else unit, unit)
            self.units_combo.setCurrentIndex(max(0, self.units_combo.findData(selected)))
        self.units_combo.setEnabled(enabled)
        self.units_combo.setToolTip('Convert graph values without changing calibration or sensor coordinates.' if enabled else
                                    'Calibrate with the ruler in physical units to enable length conversion.' if source == 'pix' else
                                    'Length units do not apply to this metric.')

    def _units_changed(self):
        if not hasattr(self, 'units_combo'): return
        self.owner._graph_length_unit = self.units_combo.currentData()
        vm = self.parent_window.vm
        signal = getattr(vm, 'track_fig_changed', None)
        if signal is not None: signal.emit(vm.track_data)
        else: self.redraw(self._last_data)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'controls'):
            height = max(28, self.controls.sizeHint().height())
            self.controls.setGeometry(6, 4, max(50, self.width()-12), height); self.controls.raise_()

    def _invalidate_plot_background(self): self._plot_background = None

    def _limits_changed(self, axes):
        self._invalidate_plot_background()
        if not self._redrawing_plot: self._manual_limits = True

    def _resize_plot(self, event):
        self._invalidate_plot_background()
        if hasattr(self, 'mode'): self._layout_plot()

    def _layout_plot(self):
        if getattr(self, 'mode', None) != 'Vibration':
            entries = [(s['line'], s['name']) for s in self._series]
            if len(entries) > 1:
                renderer = self.get_renderer(); ratio = self.device_pixel_ratio
                widths = [renderer.get_text_width_height_descent(label, FontProperties(size=7), False)[0] for _, label in entries]
                columns = max(1, min(len(entries), int(max(1, self.fig.bbox.width-60*ratio)/(max(widths)+40*ratio))))
                self.ax.legend(*zip(*entries), loc='upper right', ncol=columns, fontsize=7,
                               frameon=False, labelcolor='white', borderaxespad=.3, columnspacing=1,
                               labelspacing=.2, handlelength=1.5, handletextpad=.5)
        self.ax.title.set_fontsize(9)
        reserve = (max(28, self.controls.sizeHint().height())+7)/max(1, self.height())
        self.fig.tight_layout(pad=.6, rect=(0, 0, 1, max(.3, 1-reserve)))

    def request_redraw(self, temps):
        if not self.isVisible(): self._pending_plot_data = temps; return
        self.redraw(temps)

    def showEvent(self, event):
        super().showEvent(event); QTimer.singleShot(0, self, self._refresh_pending_plot)

    def _refresh_pending_plot(self):
        if self.isVisible():
            if self._pending_plot_data is not None: self.redraw(self._pending_plot_data)
            else: self.draw_frame_pos()

    def _animated_artists(self):
        return [a for a in (self._range_patch, self.frame_pos_line, self.frame_value_line, self.selection_marker)
                if a is not None and a.axes is self.ax and a.get_visible()]

    def _capture_plot_background(self, event):
        if event.canvas is not self: return
        if self.is_saving(): self._invalidate_plot_background(); return
        self._plot_region = self.ax.bbox.padded(2)
        self._plot_background = self.copy_from_bbox(self._plot_region)
        for artist in self._animated_artists(): self.ax.draw_artist(artist)

    def _paint_frame_marker(self):
        if self._redrawing_plot: return
        if self._plot_background is None: self.draw_idle(); return
        self.restore_region(self._plot_background)
        for artist in self._animated_artists(): self.ax.draw_artist(artist)
        self.blit(self._plot_region)

    def draw_idle(self):
        if not (getattr(self, '_draw_pending', False) or getattr(self, '_is_drawing', False)):
            self._draw_pending = True; QTimer.singleShot(0, self, self._draw_idle)

    def redraw(self, temps):
        self._pending_plot_data = None; self._last_data = temps
        vm = self.parent_window.vm; self.vm = vm
        metric = self.parent_window.fig_dropdown.currentText(); self.mode = metric_mode(metric)
        length = self._display_length(); self._sync_units_control()
        contexts = getattr(self.owner.vm, 'workspace_contexts', [])
        signature = (metric, length, getattr(vm, 'time_units', 's'), tuple(c.get('cine_path') for c in contexts))
        keep_limits = self._manual_limits and signature == self._plot_signature
        old_limits = (self.ax.get_xlim(), self.ax.get_ylim()) if keep_limits else None
        if signature != self._plot_signature:
            self._manual_limits = False; self._selected = None; self.cancel_delete_range(repaint=False)
        self._plot_signature = signature; self._invalidate_plot_background(); self._redrawing_plot = True
        self.fig.clear(); self.ax = self.fig.add_subplot(111)
        self.ax.callbacks.connect('xlim_changed', self._limits_changed)
        self.ax.callbacks.connect('ylim_changed', self._limits_changed)
        self.frame_pos_line = None; self.frame_value_line = None; self.selection_marker = None; self._range_patch = None
        self._series = []; self._omitted = []
        multi = len(contexts) > 1
        if self.mode == 'Vibration':
            self.plot_vibration(metric)
            self.ax.set_title(f'{metric[0]}-Frequency Spectrum', color='white')
        else:
            if hasattr(self.owner, 'combined_track_data'): temps = self.owner.combined_track_data()
            self._series, self._omitted = graph_series(temps, metric, vm.n_diff,
                target_length=length, target_time_scale=getattr(vm, 'time_scale', 1),
                default_length=getattr(vm, 'length_units', 'pix'), default_cal=getattr(vm, 'cal', None),
                default_time_scale=getattr(vm, 'time_scale', 1), contexts=contexts,
                active_cine=getattr(vm, 'active_cine_index', 0))
            xu, yu = self.get_units(self.mode)
            time_label = 'Time from Trigger' if multi else 'Time'
            self.ax.set_title(f'{metric} ({yu}) vs {time_label} ({xu})', color='white')
            for s in self._series:
                s['line'], = self.ax.plot(s['x'], s['y'], marker='D', ms=4,
                    linestyle=('-', '--', '-.', ':')[s['cine_index'] % 4], label=s['name'], color=s['color'])
                if self.show_annotations:
                    for row, note in s['notes'].items():
                        matches = np.flatnonzero(s['source_rows'] == row)
                        if not note or not len(matches): continue
                        i = int(matches[0]); x, y = s['x'][i], s['y'][i]
                        if np.isfinite(x) and np.isfinite(y):
                            self.ax.annotate(str(note), (x, y), textcoords='offset points', xytext=(0, 9),
                                             ha='center', fontsize=8, color='#ffffff', clip_on=True,
                                             bbox=dict(boxstyle='round,pad=.2', fc='#20262e', ec=s['color'], alpha=.8))
        self.ax.tick_params(axis='both', colors='#dbe1e9', labelsize=8)
        for axis in (self.ax.xaxis, self.ax.yaxis):
            axis.get_offset_text().set_color('#dbe1e9'); axis.get_offset_text().set_fontsize(8)
        self.ax.set_facecolor('#20262e'); self.ax.yaxis.grid(True, color='#697787', linewidth=.6, alpha=.45)
        self.ax.set_axisbelow(True)
        for spine in self.ax.spines.values(): spine.set_edgecolor('#697787')
        if old_limits: self.ax.set_xlim(old_limits[0]); self.ax.set_ylim(old_limits[1])
        self._manual_limits = bool(old_limits)
        self.draw_frame_pos(); self._update_selection_artist(); self._update_range_artist()
        self._layout_plot(); self._redrawing_plot = False
        self._sync_edit_controls(); self.draw()

    def get_units(self, mode):
        return metric_units(mode, self._display_length(), getattr(self.parent_window.vm, 'time_units', 's'))

    def _current_time(self):
        vm = self.parent_window.vm; frame = int(getattr(vm, 'active_frame', 0))
        contexts = getattr(self.owner.vm, 'workspace_contexts', [])
        key = 'frame_ts_trig' if len(contexts) > 1 else 'frame_ts'
        tracks = getattr(vm, 'track_data', {})
        active = tracks.get(getattr(vm, 'active_object', None))
        candidates = [active] if active is not None else list(tracks.values())
        for t in candidates:
            rows = np.flatnonzero(np.asarray(t.get('frames', [])) == frame)
            times = t.get(key, t.get('frame_ts', []))
            if len(rows) and rows[0] < len(times): return float(times[rows[0]])
        handler = getattr(vm, 'cine_handler', None)
        if handler is not None:
            try:
                stamp = handler.get_timestamp(frame)
                seconds = capture_time_seconds(stamp, from_trigger=len(contexts) > 1)
                return float(seconds) * getattr(vm, 'time_scale', 1)
            except (IndexError, KeyError, ValueError, TypeError): pass
        return None

    def draw_frame_pos(self, fr=None):
        if not self.isVisible(): return
        ts = None if getattr(self, 'mode', None) == 'Vibration' else self._current_time()
        if ts is None or not np.isfinite(ts):
            for name in ('frame_pos_line', 'frame_value_line'):
                artist = getattr(self, name)
                if artist is not None and artist.axes is self.ax: artist.remove()
                setattr(self, name, None)
            self._paint_frame_marker(); return
        oldx, oldy = self.ax.get_xlim(), self.ax.get_ylim()
        manual_limits = self._manual_limits
        if self.frame_pos_line is not None and self.frame_pos_line.axes is self.ax:
            self.frame_pos_line.set_xdata([ts, ts])
        else:
            self.frame_pos_line = self.ax.axvline(ts, color='#00FF7F', linestyle=':', linewidth=1.2, label='frame_pos', animated=True)
        vm = self.parent_window.vm
        active_key = (getattr(vm, 'active_cine_index', 0), getattr(vm, 'active_object', None))
        candidates = [s for s in self._series if (s['cine_index'], s['object_id']) == active_key]
        if not candidates and len(self._series) == 1: candidates = self._series
        y = None
        if candidates:
            s = candidates[0]
            # Missing/deleted captures have no measured value. Keep the time
            # cursor, but never borrow a neighboring point across such a gap.
            matches = np.flatnonzero((s['frames'] == int(getattr(vm, 'active_frame', 0)))
                                     & np.isfinite(s['x']) & np.isfinite(s['y']))
            if len(matches):
                i = matches[int(np.argmin(np.abs(s['x'][matches]-ts)))]; y = float(s['y'][i])
        if y is not None:
            if self.frame_value_line is not None and self.frame_value_line.axes is self.ax:
                self.frame_value_line.set_ydata([y, y]); self.frame_value_line.set_visible(True)
            else:
                self.frame_value_line = self.ax.axhline(y, color='#00FF7F', linestyle=':', linewidth=.8, alpha=.55, label='_current_value', animated=True)
        elif self.frame_value_line is not None: self.frame_value_line.set_visible(False)
        # A playhead is a guide, never a reason to rescale the user's view.
        if self.ax.get_xlim() != oldx: self.ax.set_xlim(oldx)
        if self.ax.get_ylim() != oldy: self.ax.set_ylim(oldy)
        self._manual_limits = manual_limits
        self._paint_frame_marker()

    def zoom_graph(self, factor, center=None):
        if not self.ax.get_visible(): return
        xlim, ylim = self.ax.get_xlim(), self.ax.get_ylim()
        x, y = center or ((xlim[0]+xlim[1])/2, (ylim[0]+ylim[1])/2)
        if not np.isfinite([x, y, factor]).all() or factor <= 0: return
        self.ax.set_xlim(x+(np.asarray(xlim)-x)/factor)
        self.ax.set_ylim(y+(np.asarray(ylim)-y)/factor)
        self._manual_limits = True; self.draw_idle()

    def fit_graph(self):
        self._manual_limits = False; self.redraw(self._last_data)

    def _on_scroll(self, event):
        if event.inaxes is self.ax and event.xdata is not None:
            self.setFocus(Qt.MouseFocusReason)
            self.zoom_graph(1.25 ** event.step, (event.xdata, event.ydata))

    def _nearest(self, event, key=None):
        if event.inaxes is not self.ax or event.x is None or event.y is None: return None
        candidates = []; radius_squared = (11*self.device_pixel_ratio)**2
        for s in self._series:
            if key is not None and (s['cine_index'], s['object_id']) != key: continue
            points = np.column_stack((s['x'], s['y'])); finite = np.flatnonzero(np.isfinite(points).all(axis=1))
            if not len(finite): continue
            pixels = self.ax.transData.transform(points[finite])
            distance = np.sum((pixels-np.array([event.x, event.y]))**2, axis=1)
            i = int(np.argmin(distance))
            if distance[i] < radius_squared:
                candidates.append((float(distance[i]), s, int(finite[i]), pixels[i]))
        if not candidates: return None
        nearest = min(candidates, key=lambda candidate: candidate[0])
        # Identical trajectories must still let the selected object be edited.
        # Compare the sample locations, not just their pointer distances: two
        # visibly separated curves retain ordinary nearest-hit selection.
        vm = self.owner.vm
        preferred = (getattr(vm, 'active_cine_index', 0), getattr(vm, 'active_object', None))
        if self._range is not None and self._range['start'] is not None:
            preferred = self._range['start'][:2]
        coincident_radius = .75*self.device_pixel_ratio
        preferred_hits = [candidate for candidate in candidates
                          if (candidate[1]['cine_index'], candidate[1]['object_id']) == preferred
                          and np.linalg.norm(candidate[3]-nearest[3]) <= coincident_radius]
        chosen = min(preferred_hits, key=lambda candidate: candidate[0]) if preferred_hits else nearest
        return chosen[1], chosen[2]

    @staticmethod
    def _sample_key(s, i): return (s['cine_index'], s['object_id'], int(s['frames'][i]))

    def _resolve_selected(self):
        if self._selected is None: return None
        cine, obj, frame = self._selected
        for s in self._series:
            if (s['cine_index'], s['object_id']) != (cine, obj): continue
            rows = np.flatnonzero((s['frames'] == frame) & np.isfinite(s['x']) & np.isfinite(s['y']))
            if self._selected_index is not None and self._selected_index in rows:
                return s, int(self._selected_index)
            if len(rows): return s, int(rows[0])
        return None

    def _select(self, sample, seek=False):
        if sample is None: return
        s, i = sample; self._selected = self._sample_key(s, i); self._selected_index = int(i)
        self._update_selection_artist(); self._paint_frame_marker(); self._sync_edit_controls()
        if seek: self._seek_sample(s, i)

    def _update_selection_artist(self):
        sample = self._resolve_selected()
        if sample is None:
            if self.selection_marker is not None: self.selection_marker.set_visible(False)
            if self._range is None:
                self.value_label.setText('Some Cines need calibration' if getattr(self, '_omitted', []) else 'Hover a point · drag to pan')
                self.value_label.setToolTip('\n'.join(getattr(self, '_omitted', [])))
            return
        s, i = sample; x, y = s['x'][i], s['y'][i]
        if self.selection_marker is None or self.selection_marker.axes is not self.ax:
            self.selection_marker, = self.ax.plot([x], [y], linestyle='None', marker='D', markersize=8,
                markerfacecolor=s['color'], markeredgecolor='white', markeredgewidth=1.2, label='_selected', animated=True, zorder=30)
        else:
            self.selection_marker.set_data([x], [y]); self.selection_marker.set_markerfacecolor(s['color']); self.selection_marker.set_visible(True)
        xu, yu = self.get_units(self.mode)
        if self._range is None: self.value_label.setText(f'X {x:,.6g} {xu}   Y {y:,.6g} {yu}')
        frame = int(s['frames'][i]); contexts = getattr(self.owner.vm, 'workspace_contexts', [])
        if 0 <= s['cine_index'] < len(contexts):
            handler = contexts[s['cine_index']].get('cine_handler')
            frame += int(getattr(getattr(handler, 'metadata', None), 'FirstImageNo', 0))
        tip = f'{s["name"]} · Frame {frame:,}\nX {x:,.9g} {xu}\nY {y:,.9g} {yu}\n← / →: neighboring sample · Delete: remove source tracking point'
        if self.mode in ('Speed', 'Acceleration', 'Angular Speed'):
            tip += '\nDerived value: deletion removes the nearest source measurement and recomputes the curve.'
        self.value_label.setToolTip(tip)

    def _seek_sample(self, s, i):
        owner = self.owner
        if hasattr(owner, '_pause_playback'): owner._pause_playback()
        if hasattr(owner, 'activate_cine_pane') and getattr(owner.vm, 'active_cine_index', 0) != s['cine_index']:
            owner.activate_cine_pane(s['cine_index'])
        owner.vm.active_object = s['object_id']
        if hasattr(owner, '_set_point_table'): owner._set_point_table(owner.vm.track_data, s['object_id'])
        if hasattr(owner, 'frame_slider'): owner.frame_slider.setValue(int(s['frames'][i]))
        else: owner.vm.active_frame = int(s['frames'][i])
        self.draw_frame_pos()

    def _on_press(self, event):
        if event.button == 1 and event.inaxes is self.ax:
            self.setFocus(Qt.MouseFocusReason)
            self._press = (event.x, event.y, self.ax.get_xlim(), self.ax.get_ylim())
            self._dragged = False

    def _on_motion(self, event):
        if self._press is not None and event.x is not None and event.y is not None:
            px, py, xlim, ylim = self._press; dx, dy = event.x-px, event.y-py
            if self._dragged or abs(dx)+abs(dy) > 4*self.device_pixel_ratio:
                self._dragged = True; self.setCursor(Qt.ClosedHandCursor)
                self.ax.set_xlim(np.asarray(xlim)-dx/self.ax.bbox.width*(xlim[1]-xlim[0]))
                self.ax.set_ylim(np.asarray(ylim)-dy/self.ax.bbox.height*(ylim[1]-ylim[0]))
                self.draw_idle()
            return
        sample = self._nearest(event)
        if sample is not None:
            self.setCursor(Qt.PointingHandCursor); self.setFocus(Qt.MouseFocusReason); self._select(sample)
        elif event.inaxes is self.ax: self.setCursor(Qt.OpenHandCursor)
        else: self.unsetCursor()

    def _on_release(self, event):
        if self._press is None: return
        dragged = self._dragged; self._press = None; self.setCursor(Qt.OpenHandCursor)
        if dragged: return
        sample = self._nearest(event)
        if sample is None: return
        self._select(sample, seek=self._range is None)
        if self._range is not None: self._pick_range(sample)

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride and self.hasFocus():
            if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Escape):
                event.accept(); return True
            if event.key() == Qt.Key_Z and event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
                event.accept(); return True
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_Right):
            sample = self._resolve_selected()
            if sample is not None:
                s, i = sample; valid = np.flatnonzero(np.isfinite(s['x']) & np.isfinite(s['y']))
                position = int(np.flatnonzero(valid == i)[0]); position = min(len(valid)-1, max(0, position+(1 if key == Qt.Key_Right else -1)))
                self._select((s, int(valid[position])), seek=True)
            event.accept(); return
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected(); event.accept(); return
        if key == Qt.Key_Escape:
            self.cancel_delete_range(); event.accept(); return
        if key == Qt.Key_Z and event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self.undo_delete(); event.accept(); return
        super().keyPressEvent(event)

    def _sync_edit_controls(self):
        available = bool(self._series) and self.mode != 'Vibration' if hasattr(self, 'mode') else False
        self.delete_range_button.setEnabled(available)
        saved = getattr(self.owner, '_graph_delete_undo', None)
        vm = self.owner.vm; contexts = getattr(vm, 'workspace_contexts', [])
        index = getattr(vm, 'active_cine_index', -1)
        undo_available = bool(saved and 0 <= index < len(contexts) and saved.get('context') is contexts[index])
        self.undo_button.setEnabled(undo_available)

    def begin_delete_range(self):
        if not self._series: return
        if hasattr(self.owner, '_pause_playback'): self.owner._pause_playback()
        self._range = {'start': None, 'end': None}; self.confirm_range_button.show(); self.confirm_range_button.setEnabled(False)
        self.cancel_range_button.show(); self.delete_range_button.hide()
        self.value_label.setText('Pick first point'); self.setFocus(Qt.OtherFocusReason)

    def _pick_range(self, sample):
        s, i = sample; key = self._sample_key(s, i)
        if self._range['start'] is None:
            self._range['start'] = key; self.value_label.setText('Pick last point on the same line')
        elif key[:2] != self._range['start'][:2]:
            self.value_label.setText('Choose a point on the first line'); return
        else:
            self._range['end'] = key; self.confirm_range_button.setEnabled(True)
            low, high = sorted((self._range['start'][2], key[2]))
            # Count raw source measurements, not only the derivative dots.
            track = self._source_track(key[0], key[1]); frames = np.asarray(track.get('frames', []))
            count = int(np.count_nonzero((frames >= low) & (frames <= high)))
            self.value_label.setText(f'{count} points · inclusive'); self.value_label.setToolTip('Confirm removes both endpoints and all source measurements between them. Undo restores the deletion.')
        self._update_range_artist(); self._paint_frame_marker()

    def _source_track(self, cine, obj):
        vm = self.owner.vm
        if cine == getattr(vm, 'active_cine_index', 0): return vm.track_data.get(obj, {})
        contexts = getattr(vm, 'workspace_contexts', [])
        return contexts[cine].get('track_data', {}).get(obj, {}) if 0 <= cine < len(contexts) else {}

    def _update_range_artist(self):
        if self._range_patch is not None and self._range_patch.axes is self.ax: self._range_patch.remove()
        self._range_patch = None
        if self._range is None or self._range['start'] is None: return
        first = self._range['start']; last = self._range['end'] or first
        for s in self._series:
            if (s['cine_index'], s['object_id']) != first[:2]: continue
            xs = [s['x'][np.argmin(np.abs(s['frames']-key[2]))] for key in (first, last)]
            self._range_patch = self.ax.axvspan(min(xs), max(xs), color='#c58bd4', alpha=.2, animated=True)
            break

    def cancel_delete_range(self, repaint=True):
        self._range = None
        if self._range_patch is not None and self._range_patch.axes is self.ax: self._range_patch.remove()
        self._range_patch = None
        self.confirm_range_button.hide(); self.cancel_range_button.hide(); self.delete_range_button.show()
        if repaint: self._update_selection_artist(); self._paint_frame_marker()

    def delete_selected(self):
        if self._range is not None: return
        sample = self._resolve_selected()
        if sample is None: return
        s, i = sample; key = self._sample_key(s, i)
        callback = getattr(self.owner, 'delete_graph_tracking_samples', None)
        if callback is not None and callback(key[0], key[1], [key[2]]):
            self.redraw(self.owner.vm.track_data)
            self._select_near(key[0], key[1], key[2]); self._sync_edit_controls()

    def confirm_delete_range(self):
        if not self._range or self._range['end'] is None: return
        first, last = self._range['start'], self._range['end']; low, high = sorted((first[2], last[2]))
        track = self._source_track(first[0], first[1]); frames = np.asarray(track.get('frames', []))
        chosen = frames[(frames >= low) & (frames <= high)].astype(int).tolist()
        callback = getattr(self.owner, 'delete_graph_tracking_samples', None)
        if callback is not None and callback(first[0], first[1], chosen, frame_range=(low, high)):
            self.cancel_delete_range(repaint=False); self.redraw(self.owner.vm.track_data)
            self._select_near(first[0], first[1], high); self._sync_edit_controls()

    def _select_near(self, cine, obj, frame):
        for s in self._series:
            if (s['cine_index'], s['object_id']) == (cine, obj):
                finite = np.flatnonzero(np.isfinite(s['x']) & np.isfinite(s['y']))
                if len(finite): self._select((s, int(finite[np.argmin(np.abs(s['frames'][finite]-frame))])))
                return
        self._selected = None; self._update_selection_artist(); self._paint_frame_marker()

    def undo_delete(self):
        callback = getattr(self.owner, 'undo_graph_tracking_delete', None)
        if callback is not None and callback(): self.redraw(self.owner.vm.track_data)
        self._sync_edit_controls()

    def plot_vibration(self, metric):
        vm = self.parent_window.vm
        if not getattr(vm, 'track_data', {}) or getattr(vm, 'active_object', None) not in vm.track_data: return
        freqs, magnitude, peaks = vm.vibration(metric[0]); self.ax.plot(freqs, magnitude, color='#8dc8ed', label='Spectrum')
        for i, peak in enumerate(peaks): self.ax.scatter(freqs[peak], magnitude[peak], color=('red', 'orange')[i % 2], label=f'{freqs[peak]:.2f} Hz')
        if len(peaks): self.ax.legend(loc='upper right', fontsize=8, frameon=False, labelcolor='white')

    def graph_export_snapshot(self):
        vm = self.parent_window.vm; xu, yu = self.get_units(getattr(self, 'mode', 'Displacement'))
        series = [{key: (value.copy() if isinstance(value, np.ndarray) else deepcopy(value))
                   for key, value in s.items() if key != 'line'} for s in self._series]
        if getattr(self, 'mode', None) == 'Vibration':
            series = [dict(cine_index=getattr(vm, 'active_cine_index', 0), object_id=getattr(vm, 'active_object', None),
                           name=line.get_label(), color=line.get_color(), x=np.array(line.get_xdata()), y=np.array(line.get_ydata()),
                           frames=np.array([], int), source_rows=np.array([], int))
                      for line in self.ax.lines if not line.get_label().startswith('_selected')]
        multi = len(getattr(self.owner.vm, 'workspace_contexts', [])) > 1
        return dict(metric=self.parent_window.fig_dropdown.currentText(), mode=getattr(self, 'mode', ''),
                    x_unit=xu, y_unit=yu, x_label=('Frequency' if self.mode == 'Vibration' else 'Time from Trigger' if multi else 'Time')+f' ({xu})',
                    y_label=self.parent_window.fig_dropdown.currentText()+f' ({yu})', title=self.ax.get_title(),
                    xlim=tuple(self.ax.get_xlim()), ylim=tuple(self.ax.get_ylim()), series=series,
                    time_reference='trigger' if multi else 'start', time_scale=float(getattr(vm, 'time_scale', 1)))

    def save_subplots_to_csv(self, filepath):
        import pandas as pd
        snapshot = self.graph_export_snapshot(); columns = {}
        from collections import Counter
        counts = Counter(s['name'] for s in snapshot['series'])
        for s in snapshot['series']:
            name = s['name'] if counts[s['name']] == 1 else f'{s["name"]} (C{s["cine_index"]+1}, #{s["object_id"]})'
            columns[f'{name} X ({snapshot["x_unit"]})'] = pd.Series(s['x'])
            columns[f'{name} {snapshot["metric"]} ({snapshot["y_unit"]})'] = pd.Series(s['y'])
        if not columns: return
        if not filepath:
            source = Path(self.parent_window.vm.cine_path)
            filepath = source.with_name(source.stem+'_'+re.sub(r'[^A-Za-z0-9_-]', '_', snapshot['metric'])+'.csv')
        pd.DataFrame(columns).to_csv(filepath, index=False)
        signal = getattr(self.parent_window.vm, 'update_status_text', None)
        if signal is not None: signal.emit(f'{snapshot["metric"]} data saved to {filepath}')

    @staticmethod
    def _pad_uneven_data(data, fillvalue=np.nan):
        count = max(len(item) for item in data)
        return [list(item)+[fillvalue]*(count-len(item)) for item in data]

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        action = QAction('Hide Annotations' if self.show_annotations else 'Show Annotations', self)
        action.triggered.connect(self.toggle_annotations); menu.addAction(action); menu.exec(event.globalPos())

    def toggle_annotations(self):
        self.show_annotations = not self.show_annotations; self.redraw(self._last_data)
