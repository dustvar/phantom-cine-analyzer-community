"""A compact section editor, source timeline, and compact data box controls."""
from copy import deepcopy
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QSize, QSignalBlocker, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QIcon, QPixmap, QPainterPath
from PySide6.QtWidgets import (QWidget, QScrollArea, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QPushButton, QToolButton, QCheckBox, QDoubleSpinBox, QComboBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView, QTabWidget, QButtonGroup, QApplication)
from viewer_timeline import default_edit, section_at, split_at, move_cut, merge_next, Timeline, default_camera_overlay, copy_index, copy_options, copy_offset, copy_count, frame_label, is_video_join

from viewer_extras_ui import ExtraEditorControls


class TransitionHandle(QToolButton):
    """A click opens the editor; a drag previews a single boundary edit."""
    def __init__(self, timeline, index):
        super().__init__(timeline)
        self.timeline, self.index = timeline, index
        self._press = None
        self._dragging = False
        self.setCursor(Qt.CursorShape.SizeHorCursor if not is_video_join(timeline.edit,index) else Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._grab_offset = (self.timeline.mapFromGlobal(self._press).x()
                                 - self.timeline._frame_x(self.timeline.edit['sections'][self.index]['start']))
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None and not is_video_join(self.timeline.edit,self.index) and event.buttons() & Qt.MouseButton.LeftButton:
            if not self._dragging and (event.globalPosition().toPoint() - self._press).manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
                self.timeline._begin_cut_drag(self.index)
                self.setDown(False)
            if self._dragging:
                self.timeline._preview_cut_drag(self.index,
                    self.timeline.mapFromGlobal(event.globalPosition().toPoint()).x() - self._grab_offset)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = None
            if self._dragging:
                self._dragging = False
                self.setDown(False)
                self.timeline._finish_cut_drag(self.index)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self._dragging:
            self.timeline._finish_cut_drag(self.index, cancel=True)
            # Retain the drag flag until release, suppressing a dialog click.
            event.accept()
            return
        super().keyPressEvent(event)


class SectionTimeline(QWidget):
    seek = Signal(int)
    transitionRequested = Signal(int)
    cutMoved = Signal(int, int)
    cutDragStarted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.edit = default_edit()
        self.transition_buttons = {}
        self.plan = None
        self._cut_original = None
        self.source_first_frame = 0
        self.first, self.last, self.frame = 0, 0, 0
        self.setFixedHeight(56)
        self.setObjectName('viewer_section_timeline')
        self.setAccessibleName('Section timeline: click or drag to move the playhead')
        self.setToolTip('Click a section to seek; click a junction icon to edit its transition, or drag it to move the cut.')

    def set_edit(self, edit, first, last, source_first_frame=0):
        self.edit, self.first, self.last = edit, first, last
        self.source_first_frame = source_first_frame
        self._cut_original = None
        self.plan = Timeline(edit, first, last)
        for button in self.transition_buttons.values():
            button.hide(); button.deleteLater()
        self.transition_buttons = {}
        previous = None
        for i, section in enumerate(edit['sections']):
            if not section['keep']: continue
            if previous is not None:
                button = TransitionHandle(self, i)
                button.setFixedSize(26, 26)
                button.setIconSize(QSize(20, 20))
                pix = QPixmap(20, 20); pix.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pix); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(QColor('#ffdf8c' if section['ramp'] else '#ded3e8'), 2))
                curve = QPainterPath(); curve.moveTo(2, 15); curve.cubicTo(9, 15, 11, 5, 18, 5)
                if is_video_join(edit,i):
                    painter.setPen(QPen(QColor('#7ef9ff'),2))
                    painter.drawRoundedRect(QRectF(1,5,11,7),3,3)
                    painter.drawRoundedRect(QRectF(8,9,11,7),3,3)
                else:
                    painter.drawPath(curve); painter.drawLine(2,11,2,17);painter.drawLine(18,3,18,9)
                painter.end()
                button.setIcon(QIcon(pix))
                button.setStyleSheet('QToolButton { background: #293440; border: 1px solid #8294aa; border-radius: 5px; padding: 0; } QToolButton:hover { background: #43566b; }')
                button.setAccessibleName(f'Edit transition: section {previous + 1} to {i + 1}')
                detail = f"{section['ramp']:g} s · {section['ramp_place']}" if section['ramp'] else 'Cut · add a speed transition'
                button.setToolTip(f'Section {previous+1} → {i+1}\nCut at {frame_label(edit,section["start"],source_first_frame)}\n{detail}\n' + ('New video join · click to edit effects' if is_video_join(edit,i) else 'Click to edit · drag to move cut'))
                button.clicked.connect(lambda checked=False, index=i: self.transitionRequested.emit(index))
                self.transition_buttons[i] = button
            previous = i
        self._position_transitions()
        self.update()

    def _position_transitions(self):
        for index, button in self.transition_buttons.items():
            frame = self.edit['sections'][index]['start']
            x = self._frame_x(frame)
            button.move(round(x - 13), 18)
            button.setVisible(self.first < frame <= self.last)

    def _frame_x(self, frame):
        return 6 + (frame - self.first) / max(1, self.last - self.first + 1) * max(1, self.width() - 12)

    def _begin_cut_drag(self, index):
        self._cut_original = self.edit
        self.cutDragStarted.emit()

    def _preview_cut_drag(self, index, x):
        if self._cut_original is None:
            return
        frame = round((x - 6) / max(1, self.width() - 12) * (self.last - self.first + 1)) + self.first
        frame = min(self.last, max(self.first + 1, frame))
        self.edit = move_cut(self._cut_original, index, frame)
        self.plan = Timeline(self.edit, self.first, self.last)
        self._position_transitions()
        self.update()

    def _finish_cut_drag(self, index, cancel=False):
        if self._cut_original is None:
            return
        frame = self.edit['sections'][index]['start']
        original = self._cut_original
        self.edit, self._cut_original = original, None
        self.plan = Timeline(self.edit, self.first, self.last)
        self._position_transitions()
        self.update()
        if not cancel and frame != original['sections'][index]['start']:
            # Applying the edit rebuilds handles. Let this mouse release finish
            # before replacing the widget that currently owns the mouse grab.
            QTimer.singleShot(0, lambda: self.cutMoved.emit(index, frame) if self.edit is original else None)

    def resizeEvent(self, event):
        self._position_transitions()
        super().resizeEvent(event)

    def set_frame(self, frame):
        self.frame = frame
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = max(1, self.width() - 12)
        count = max(1, self.last - self.first + 1)
        colors = ['#34495f', '#354e4a', '#4b455c', '#514937']
        p.fillRect(self.rect(), QColor('#1d242e'))
        for i, section in enumerate(self.edit['sections']):
            first, end = max(section['start'], self.first), min(section['end'], self.last)
            if first > end:
                continue
            x = 6 + (first - self.first) / count * width
            w = (end - first + 1) / count * width
            rect = QRectF(x, 14, max(1, w - 2), 33)
            p.setBrush(QColor(colors[copy_index(self.edit,section['start']) % len(colors)] if section['keep'] else '#404045'))
            p.setPen(QPen(QColor('#9cb8d6' if first <= self.frame <= end else '#60748a'), 1))
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(QColor('#ffffff' if section['keep'] else '#999999'))
            label = f'Video {copy_index(self.edit,section["start"])+1} · Segment {i+1} · {section["fps"]:g} fps playback' if section['keep'] else f'{i+1} · skipped'
            if section['keep'] and w < 290:
                label = f'S{i+1} · {section["fps"]:g} fps playback'
            if w > 60:
                inset = 19 if i in self.transition_buttons else 6
                p.drawText(rect.adjusted(inset, 0, -16, 0), Qt.AlignmentFlag.AlignVCenter,
                           p.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, max(0, int(w - inset - 16))))
        if self.plan:
            for transition in self.plan.transitions:
                if not transition['duration']: continue
                left = self.edit['sections'][transition['left_index']]
                for start, end in [(transition['source_left_start'], left['end'] + 1),
                                   (transition['source_cut'], transition['source_right_end'])]:
                    start, end = max(start, self.first), min(end, self.last + 1)
                    if end > start:
                        x = 6 + (start - self.first) / count * width
                        p.fillRect(QRectF(x, 7, (end - start) / count * width, 3), QColor('#ffdf8c'))
        for ci,copy in enumerate(self.edit.get('copies',[{}])):
            length=copy_count(self.edit,ci);offset=copy_offset(self.edit,ci)
            lo,hi=copy.get('trim',[0,length-1])
            for a,b in [(offset,offset+lo),(offset+hi+1,offset+length)]:
                a,b=max(a,self.first),min(b,self.last+1)
                if b>a:
                    rect=QRectF(self._frame_x(a),14,(b-a)/count*width,33)
                    p.fillRect(rect,QColor(20,20,24,210))
                    if rect.width()>70:
                        p.setPen(QColor('#aaaaaa'));p.drawText(rect,Qt.AlignmentFlag.AlignCenter,'trimmed')
        if self.first <= self.frame <= self.last:
            x = 6 + (self.frame - self.first + .5) / count * width
            p.setPen(QPen(QColor('#ffdf8c'), 2))
            p.drawLine(QPointF(x, 10), QPointF(x, 51))
            p.setBrush(QColor('#ffdf8c'))
            p.drawPolygon(QPolygonF([QPointF(x - 4, 4), QPointF(x + 4, 4), QPointF(x, 10)]))
        if self._cut_original is not None:
            index = next(i for i, s in enumerate(self.edit['sections'])
                         if s['start'] != self._cut_original['sections'][i]['start']) if self.edit != self._cut_original else None
            if index is not None:
                text = f'Cut at {frame_label(self.edit,self.edit["sections"][index]["start"],self.source_first_frame)}'
                p.setPen(QColor('#ffdf8c'))
                p.drawText(self.rect().adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, text)
        p.end()

    def _seek(self, position):
        fraction = min(1, max(0, (position.x() - 6) / max(1, self.width() - 12)))
        self.seek.emit(min(self.last, self.first + int(fraction * (self.last - self.first + 1))))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._seek(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._seek(event.position())


class TimelineEditor(ExtraEditorControls, QWidget):
    changed = Signal(object)
    dataBoxSelected = Signal(str)
    textSeekRequested = Signal(float)
    seek = Signal(int)
    splitRequested = Signal()
    transitionRequested = Signal(int)
    undoRequested = Signal()
    redoRequested = Signal()
    saveRequested = Signal()
    loadRequested = Signal()
    resetRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('viewer_editor_panel')
        self.setFixedWidth(330)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.source = QLabel('Viewer Editor')
        self.source.setWordWrap(True)
        self.source.setStyleSheet('font-weight: bold; font-size: 14px;')
        layout.addWidget(self.source)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs,1)
        self.edit = default_edit()
        self.frame = 0
        self.selected = 0
        self.first_frame = 0
        self.capture_fps = 0
        self.active_data_box = 'playback'
        self._updating = False
        self._build_sections()
        self._build_overlay()
        self._build_zoom()
        self._build_text()
        self.status = QLabel('')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.undo_button = QPushButton('Undo')
        self.redo_button = QPushButton('Redo')
        row.addWidget(self.undo_button); row.addWidget(self.redo_button)
        self.tabs.widget(0).widget().layout().addLayout(row)
        row = QHBoxLayout()
        self.save_button = QPushButton('Save Edit…')
        self.load_button = QPushButton('Load Edit…')
        row.addWidget(self.save_button); row.addWidget(self.load_button)
        layout.addLayout(row)
        self.reset_button = QPushButton('Reset Timeline')
        self.tabs.widget(0).widget().layout().addWidget(self.reset_button)
        self.undo_button.clicked.connect(self.undoRequested)
        self.redo_button.clicked.connect(self.redoRequested)
        self.save_button.clicked.connect(self.saveRequested)
        self.load_button.clicked.connect(self.loadRequested)
        self.reset_button.clicked.connect(self.resetRequested)

    def _add_editor_tab(self,content,title):
        content.setObjectName('viewer_editor_content')
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        self.tabs.addTab(scroll,title)

    def ensureWidgetVisible(self,widget):
        for i in range(self.tabs.count()):
            scroll=self.tabs.widget(i)
            if scroll.widget().isAncestorOf(widget):
                self.tabs.setCurrentIndex(i);scroll.ensureWidgetVisible(widget);return

    def _build_sections(self):
        tab = QWidget(); layout = QVBoxLayout(tab)
        self._add_editor_tab(tab, 'Segments')
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Segment', 'Frames', 'Playback fps'])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setFixedHeight(112)
        self.table.itemSelectionChanged.connect(self._select_section)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.split_button = QPushButton('Split Here')
        self.split_button.setToolTip('Split before the current frame (Ctrl/Command+B).')
        self.merge_button = QPushButton('Merge Next')
        self.merge_button.setToolTip('Join the next section using this section’s speed and inclusion setting.')
        row.addWidget(self.split_button); row.addWidget(self.merge_button)
        layout.addLayout(row)
        self.split_button.clicked.connect(self.splitRequested)
        self.merge_button.clicked.connect(lambda: self.changed.emit(merge_next(self.edit, self.selected)))
        self.section_label = QLabel('Section 1')
        layout.addWidget(self.section_label)
        form = QFormLayout()
        self.rate = QDoubleSpinBox(); self.rate.setRange(.1, 10_000_000); self.rate.setDecimals(2)
        self.rate.setSuffix(' fps'); self.rate.setSingleStep(5)
        self.rate.setToolTip('Recorded frames played per finished-video second. Higher is faster. Very low rates may hold individual frames.')
        form.addRow('Playback', self.rate)
        layout.addLayout(form)
        presets = QHBoxLayout()
        for label, factor in [('¼×', .25), ('1×', 1), ('4×', 4), ('10×', 10)]:
            button = QPushButton(label); button.setObjectName('viewer_speed_preset')
            button.clicked.connect(lambda checked=False, value=factor: self.rate.setValue(self.edit['output_fps'] * value))
            presets.addWidget(button)
        layout.addLayout(presets)
        self.transition_button = QPushButton('Edit incoming transition…')
        self.transition_button.clicked.connect(lambda: self.transitionRequested.emit(self.selected))
        layout.addWidget(self.transition_button)
        self.keep = QCheckBox('Include this section')
        layout.addWidget(self.keep)
        note = QLabel('Split at the playhead, then set each section’s FPS. Click the icon at a cut to edit its transition.')
        note.setWordWrap(True); note.setStyleSheet('font-size: 11px;')
        layout.addWidget(note)
        form = QFormLayout()
        self.output_fps = QDoubleSpinBox(); self.output_fps.setRange(1, 120); self.output_fps.setDecimals(2)
        self.output_fps.setSuffix(' fps')
        self.output_fps.setToolTip('The fixed frame rate of preview and export. Section playback FPS controls the speed of the footage.')
        form.addRow('Video FPS', self.output_fps)
        layout.addLayout(form)
        self.loop = QCheckBox('Loop edited preview')
        layout.addWidget(self.loop)
        self.rate.valueChanged.connect(lambda value: self._section_change('fps', value))
        self.keep.toggled.connect(lambda value: self._section_change('keep', value))
        self.output_fps.valueChanged.connect(lambda value: self._root_change('output_fps', value))
        self.loop.toggled.connect(lambda value: self._root_change('loop', value))

    def _build_overlay(self):
        tab = QWidget(); layout = QVBoxLayout(tab)
        self._add_editor_tab(tab, 'Data')
        self.overlay_enabled = QCheckBox('Enable')
        self.overlay_enabled.setToolTip('Show the selected data box in Viewer and exported videos.')
        layout.addWidget(self.overlay_enabled)
        row = QHBoxLayout()
        self.data_box_group = QButtonGroup(self)
        self.data_box_buttons = {}
        for label, key in [('Playback Data', 'playback'), ('Camera Data', 'camera')]:
            button = QPushButton(label); button.setCheckable(True)
            button.setToolTip('Select and enable this data box. Its settings are independent of the other box.')
            self.data_box_group.addButton(button); self.data_box_buttons[key] = button
            row.addWidget(button)
            button.clicked.connect(lambda checked=False, value=key: self._choose_data_box(value))
        self.data_box_buttons['playback'].setChecked(True)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.overlay_layout_group = QButtonGroup(self)
        self.layout_buttons = {}
        for label, key in [('Stacked', 'stacked'), ('Straight', 'straight')]:
            button = QPushButton(label); button.setCheckable(True)
            self.overlay_layout_group.addButton(button)
            self.layout_buttons[key] = button
            row.addWidget(button)
            button.clicked.connect(lambda checked=False, value=key: self._overlay_change('layout', value))
        self.layout_buttons['stacked'].setChecked(True)
        layout.addLayout(row)
        self.data_fields = {}
        for label, key in [('Times slower', 'slowdown'), ('Camera model', 'model'), ('Capture FPS', 'capture'), ('Playback FPS', 'playback'), ('Resolution', 'resolution')]:
            check = QCheckBox(label)
            self.data_fields[key] = check
            layout.addWidget(check)
            check.toggled.connect(self._data_fields_changed)
        self.overlay_decimals = QCheckBox('Show one decimal place')
        self.overlay_decimals.setToolTip('Display one decimal place. Off rounds the readouts to whole numbers without changing playback.')
        self.overlay_decimals.toggled.connect(lambda value: self._overlay_change('decimals', value))
        layout.addWidget(self.overlay_decimals)
        form = QFormLayout()
        self.overlay_size = QDoubleSpinBox(); self.overlay_size.setRange(1, 15); self.overlay_size.setDecimals(1)
        self.overlay_size.setSuffix(' %'); self.overlay_size.setSingleStep(.5)
        form.addRow('Main text size', self.overlay_size)
        self.overlay_x = QDoubleSpinBox(); self.overlay_x.setRange(0, 100); self.overlay_x.setSuffix(' %')
        self.overlay_y = QDoubleSpinBox(); self.overlay_y.setRange(0, 100); self.overlay_y.setSuffix(' %')
        form.addRow('Horizontal', self.overlay_x); form.addRow('Vertical', self.overlay_y)
        self.overlay_color = QComboBox()
        for name, color in [('White', '#ffffff'), ('Gold', '#ffdf8c'), ('Cyan', '#7ef9ff')]:
            self.overlay_color.addItem(name, color)
        form.addRow('Text color', self.overlay_color)
        layout.addLayout(form)
        self.overlay_pinned = QCheckBox('Keep on screen during zoom')
        self.overlay_pinned.toggled.connect(lambda v:self._overlay_change('pinned',v))
        layout.addWidget(self.overlay_pinned)
        self.custom_text_button=QPushButton('Add Custom Text…')
        self.custom_text_button.clicked.connect(lambda:self.tabs.setCurrentIndex(3))
        layout.addWidget(self.custom_text_button)
        self.overlay_background = QCheckBox('Translucent background')
        layout.addWidget(self.overlay_background)
        note = QLabel('Playback Data starts at bottom right; Camera Data starts at bottom left. Drag either box on the video to move it.\n\n'
            'Enable and all settings apply to the selected box. Both boxes can appear together.')
        note.setWordWrap(True); note.setStyleSheet('font-size: 11px;')
        layout.addWidget(note)
        self.overlay_enabled.toggled.connect(lambda value: self._overlay_change('enabled', value))
        self.overlay_size.valueChanged.connect(lambda value: self._overlay_change('size', value))
        self.overlay_x.valueChanged.connect(lambda value: self._overlay_change('x', value / 100))
        self.overlay_y.valueChanged.connect(lambda value: self._overlay_change('y', value / 100))
        self.overlay_color.currentIndexChanged.connect(lambda: self._overlay_change('color', self.overlay_color.currentData()))
        self.overlay_background.toggled.connect(lambda value: self._overlay_change('background', value))

    def set_edit(self, edit, frame=None, first_frame=None, title=None, undo=False, redo=False):
        self._updating = True
        try:
            self.edit = deepcopy(edit)
            if frame is not None: self.frame = frame
            if first_frame is not None: self.first_frame = first_frame
            if title is not None: self.source.setText(title)
            self.selected = section_at(edit, self.frame) or 0
            self.table.setRowCount(len(edit['sections']))
            for i, section in enumerate(edit['sections']):
                values = [f'V{copy_index(edit,section["start"])+1} · {i+1}' + (' (skip)' if not section['keep'] else ''),
                    f'{frame_label(edit,section["start"],self.first_frame)}…{frame_label(edit,section["end"],self.first_frame)}',
                    f'{section["fps"]:g}']
                for col, text in enumerate(values):
                    item = QTableWidgetItem(text); item.setToolTip(text)
                    self.table.setItem(i, col, item)
            self.table.selectRow(self.selected)
            self._load_section()
            self.output_fps.setValue(edit['output_fps']); self.loop.setChecked(edit['loop'])
            self._load_data_controls()
            self._load_text()
            self.undo_button.setEnabled(undo); self.redo_button.setEnabled(redo)
        finally:
            self._updating = False

    @property
    def data_options_key(self):
        return 'camera_overlay' if self.active_data_box == 'camera' else 'overlay'

    def _field_order(self):
        return ['model', 'capture', 'resolution'] if self.active_data_box == 'camera' else ['slowdown', 'capture', 'playback']

    def set_data_box(self, key):
        if key not in self.data_box_buttons:
            return
        self.active_data_box = key
        self._load_data_controls()

    def _choose_data_box(self, key):
        self.set_data_box(key)
        self.dataBoxSelected.emit(key)

    def _load_data_controls(self):
        updating = self._updating
        self._updating = True
        try:
            overlay = copy_options(self.edit,copy_index(self.edit,self.frame)).get(self.data_options_key,default_camera_overlay())
            self.data_box_buttons[self.active_data_box].setChecked(True)
            self.overlay_enabled.setChecked(overlay['enabled'])
            self.layout_buttons[overlay.get('layout', 'stacked')].setChecked(True)
            self.overlay_decimals.setChecked(overlay.get('decimals', False))
            for key, check in self.data_fields.items():
                check.setVisible(key in self._field_order())
                check.setChecked(key in overlay['fields'])
            self.overlay_size.setValue(overlay['size'])
            self.overlay_x.setValue(overlay['x'] * 100); self.overlay_y.setValue(overlay['y'] * 100)
            self.overlay_color.setCurrentIndex(self.overlay_color.findData(overlay['color']))
            self.overlay_background.setChecked(overlay['background'])
            self.overlay_pinned.setChecked(overlay.get('pinned',True))
        finally:
            self._updating = updating

    def set_frame(self, frame):
        self.frame = frame
        index = section_at(self.edit, frame)
        if index is not None and index != self.selected:
            self._updating = True
            self.selected = index
            self.table.selectRow(index)
            self._load_section()
            self._load_data_controls()
            self._load_text()
            self._updating = False
        section = self.edit['sections'][self.selected] if self.edit['sections'] else None
        self.split_button.setEnabled(bool(section and frame > section['start']))

    def _load_section(self):
        if not self.edit['sections']:
            return
        s = self.edit['sections'][self.selected]
        self.section_label.setText(f'Video {copy_index(self.edit,self.frame)+1} · Segment {self.selected+1} · {s["end"]-s["start"]+1:,} frames')
        self._load_zoom()
        self.rate.setValue(s['fps']); self.keep.setChecked(s['keep'])
        self.transition_button.setEnabled(any(item['keep'] for item in self.edit['sections'][:self.selected]) and s['keep'])
        self.merge_button.setEnabled(self.selected < len(self.edit['sections']) - 1 and not is_video_join(self.edit,self.selected+1))
        self.split_button.setEnabled(self.frame > s['start'])

    def _select_section(self):
        if self._updating:
            return
        index = self.table.currentRow()
        if 0 <= index < len(self.edit['sections']):
            self.seek.emit(self.edit['sections'][index]['start'])

    def _section_change(self, key, value):
        if not self._updating and self.edit['sections']:
            result = deepcopy(self.edit); result['sections'][self.selected][key] = value
            self.changed.emit(result)

    def _root_change(self, key, value):
        if not self._updating:
            result = deepcopy(self.edit); result[key] = value; self.changed.emit(result)

    def _data_fields_changed(self):
        self._overlay_change('fields', [key for key in self._field_order() if self.data_fields[key].isChecked()])

    def _overlay_change(self, key, value):
        if not self._updating:
            result = deepcopy(self.edit)
            copy_options(result,copy_index(result,self.frame)).setdefault(self.data_options_key,default_camera_overlay())[key]=value
            self.changed.emit(result)
