"""Record Studio controls for the analyzed BOS result.

The rail is in source-frame coordinates. Its time readout, fade slopes and MP4
duration use the edited playback clock. All interval endpoints shown to the user
are inclusive native Cine frame numbers.
"""
from PySide6.QtCore import Qt, Signal, QSignalBlocker, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QFontMetrics
from PySide6.QtWidgets import (QBoxLayout, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox,
    QVBoxLayout, QWidget)

from bos_studio_model import MAX_PLAYBACK_FPS


_TRACKS = ('Overlay', 'Speed', 'Text', 'Vectors')
_COLORS = (
    ('#366f5e', '#74d8af'), ('#3b5c79', '#92c6f1'), ('#69527b', '#d4ace8'), ('#66532f', '#f0c77c')
)
_FIELD_NAMES = (('frame', 'Frame X of N'), ('playback', 'Playback FPS'),
                ('capture', 'Capture FPS'), ('slowdown', 'Slowdown'),
                ('time', 'Time from trigger'))
_POSITIONS = (('Top left', 'top_left'), ('Top right', 'top_right'),
              ('Bottom left', 'bottom_left'), ('Bottom right', 'bottom_right'),
              ('Center', 'center'))


def _duration(seconds):
    # Avoid printing 0:60.00 around minute boundaries.
    centis = round(seconds * 100)
    minutes, centis = divmod(centis, 6000)
    return f'{minutes}:{centis/100:05.2f}'


class _StudioTrack(QWidget):
    """A draggable source-frame track with a separate ruler for seeking."""
    def __init__(self, studio):
        super().__init__(studio)
        self.studio = studio
        self.setObjectName('studioTrack')
        self.setAccessibleName('Record Studio timeline')
        self.setToolTip('Click the ruler to seek. Drag a block to move it or drag either end to trim.')
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(54)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.drag = None
        self.preview = None

    def lane(self):
        return QRectF(7., 21., max(1., self.width()-14.), 27.)

    def x_for(self, boundary):
        lane = self.lane()
        return lane.left()+boundary/self.studio.model.count*lane.width()

    def _frame(self, x):
        lane = self.lane()
        value = int((x-lane.left())/lane.width()*self.studio.model.count)
        return max(0, min(self.studio.model.count-1, value))

    def _spans(self):
        panel = self.studio
        if panel.layer:
            return [(s.first, s.last) for s in panel.model.spans(panel.layer)]
        if panel.mode == 1:
            return [(s.first, s.last) for s in panel.model.speeds]
        return [(panel._text['first'], panel._text['last'])] if panel._text['fields'] else []

    def span_rect(self, start, end):
        lane = self.lane()
        left, right = self.x_for(start), self.x_for(end+1)
        inset = min(1.2, (right-left)*.08)
        return QRectF(left+inset, lane.top()+1, max(1., right-left-2*inset), lane.height()-2)

    def _hit(self, pos):
        if not self.lane().contains(pos):
            return None, None
        spans = self._spans()
        for i, (start, end) in enumerate(spans):
            left, right = self.x_for(start), self.x_for(end+1)
            if left <= pos.x() < right or (i == len(spans)-1 and pos.x() == right):
                threshold = min(8., (right-left)/3.)
                if pos.x()-left <= threshold:
                    if self.studio.mode != 1 or i > 0:
                        return i, 'left'
                if right-pos.x() <= threshold:
                    if self.studio.mode != 1 or i < len(spans)-1:
                        return i, 'right'
                return i, 'body'
        return None, None

    def paintEvent(self, event):
        panel = self.studio
        lane = self.lane()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#202831'))
        painter.drawRoundedRect(lane, 4, 4)
        font = painter.font()
        font.setPointSizeF(max(7.5, font.pointSizeF()-.8))
        painter.setFont(font)
        color, accent = _COLORS[panel.mode]
        spans = self._spans()
        if self.preview is not None:
            idx, first, last = self.preview
            if panel.mode == 1:
                if self.drag['part'] == 'left':
                    spans[idx-1] = (spans[idx-1][0], first-1)
                elif self.drag['part'] == 'right':
                    spans[idx+1] = (last+1, spans[idx+1][1])
            spans[idx] = (first, last)
        for i, (start, end) in enumerate(spans):
            rect = self.span_rect(start, end)
            selected = panel.selected[panel.mode] == i
            painter.setPen(QPen(QColor(accent if selected else '#727e87'), 1.4 if selected else .7))
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(rect, min(rect.width()*.1,4.), 4)
            painter.save()
            painter.setClipRect(rect)
            if panel.layer:
                s = panel.model.spans(panel.layer)[i]
                if end > start and (s.fade_in or s.fade_out):
                    t0, t1 = panel.model.time_at(start), panel.model.time_at(end)
                    attack = min(end, max(start, panel.model.frame_at(t0+s.fade_in)))
                    release = min(end, max(start, panel.model.frame_at(max(t0,t1-s.fade_out))))
                    # Put the envelope below the clip name so a fade starting
                    # at the left handle never strikes through the label.
                    painter.setPen(QPen(QColor(accent), 1.2))
                    if s.fade_in:
                        painter.drawLine(QPointF(rect.left(), rect.bottom()-1), QPointF(self.x_for(attack),rect.bottom()-7))
                    if s.fade_out:
                        painter.drawLine(QPointF(self.x_for(release),rect.bottom()-7),QPointF(rect.right(),rect.bottom()-1))
                label = 'Vector duration' if panel.layer == 'vectors' else 'Overlay duration'
            elif panel.mode == 1:
                label = f'{panel.model.speeds[i].fps:g} fps'
            else:
                label = panel._text['custom'] if panel._text['fields'] == ('custom',) else 'Text & data'
            if rect.width() > 34:
                painter.setPen(QColor('#f3f7fa'))
                label = QFontMetrics(font).elidedText(label, Qt.TextElideMode.ElideRight, int(rect.width()-20))
                painter.drawText(rect.adjusted(9.,0.,-9.,0.), Qt.AlignmentFlag.AlignVCenter, label)
            if selected and rect.width() >= 12:
                painter.setPen(QPen(QColor(accent), 2))
                for x in (rect.left()+3, rect.right()-3):
                    painter.drawLine(QPointF(x,rect.center().y()-5),QPointF(x,rect.center().y()+5))
            painter.restore()
        if not spans:
            painter.setPen(QColor('#b9c4cc'))
            message = (f'Add {_TRACKS[panel.mode].lower()} at the playhead' if panel.layer
                       else 'Add text or frame data to the video')
            painter.drawText(lane, Qt.AlignmentFlag.AlignCenter, message)
        # A separate ruler allows scrubbing even where clips occupy the whole track.
        painter.setPen(QColor('#aeb9c4'))
        left = f'{panel.clip.source_frame(0):,}'
        right = f'{panel.clip.source_frame(panel.model.count-1):,}'
        painter.drawText(QRectF(lane.left(),0,lane.width(),16), Qt.AlignmentFlag.AlignLeft,left)
        painter.drawText(QRectF(lane.left(),0,lane.width(),16), Qt.AlignmentFlag.AlignRight,right)
        x = self.x_for(panel.playhead+.5)
        painter.setPen(QPen(QColor('#ffdc87'),1.5))
        painter.drawLine(QPointF(x,18),QPointF(x,lane.bottom()+4))
        caret = QPainterPath(QPointF(x-4,15))
        caret.lineTo(x+4,15)
        caret.lineTo(x,21)
        caret.closeSubpath()
        painter.fillPath(caret,QColor('#ffdc87'))

    def _seek(self, frame):
        self.studio.set_position(frame)
        self.studio.seekRequested.emit(frame)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus()
        panel = self.studio
        panel.editStarted.emit()
        pos = event.position()
        frame = self._frame(pos.x())
        idx, part = self._hit(pos)
        if idx is None:
            self.drag = {'part': 'seek'}
            if self.lane().contains(pos):
                panel.selected[panel.mode] = None
            self._seek(frame)
            panel.refresh()
        else:
            panel.selected[panel.mode] = idx
            start, end = self._spans()[idx]
            self.drag = dict(idx=idx, part=part, x=pos.x(), start=start, end=end, frame=frame)
            panel.refresh()
        event.accept()

    def _bounds(self, x):
        drag = self.drag
        idx, part, start, end = (drag[k] for k in ('idx','part','start','end'))
        count = self.studio.model.count
        step = round((x-drag['x'])/self.lane().width()*count)
        spans = self._spans()
        before = spans[idx-1][1]+1 if idx else 0
        after = spans[idx+1][0]-1 if idx+1 < len(spans) else count-1
        if self.studio.mode == 1:
            if part == 'left':
                start = min(end, max(spans[idx-1][0]+1, start+step))
            elif part == 'right':
                end = max(start, min(spans[idx+1][1]-1, end+step))
        elif part == 'left':
            start = max(before, min(end, start+step))
        elif part == 'right':
            end = min(after, max(start, end+step))
        else:
            step = max(before-start, min(after-end, step))
            start, end = start+step, end+step
        return idx, start, end

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self.drag is None:
            _, part = self._hit(pos)
            cursor = (Qt.CursorShape.SizeHorCursor if part in ('left','right') else
                      Qt.CursorShape.OpenHandCursor if part == 'body' and self.studio.mode != 1
                      else Qt.CursorShape.PointingHandCursor)
            self.setCursor(cursor)
        elif self.drag['part'] == 'seek':
            self._seek(self._frame(pos.x()))
        elif abs(pos.x()-self.drag['x']) >= 2 or self.preview is not None:
            self.preview = self._bounds(pos.x())
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.drag is None:
            return super().mouseReleaseEvent(event)
        drag = self.drag
        if drag['part'] == 'seek':
            self._seek(self._frame(event.position().x()))
        elif abs(event.position().x()-drag['x']) >= 2 or self.preview is not None:
            idx, first, last = self._bounds(event.position().x())
            self.drag = self.preview = None
            if (first,last) != (drag['start'],drag['end']):
                self.studio.apply_bounds(idx, first, last, drag['part'])
        else:
            self._seek(drag['frame'])
        self.drag = self.preview = None
        self.update()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.drag is not None:
            self.drag = self.preview = None
            self.update()
            event.accept()
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.studio.delete_selected()
            event.accept()
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.studio.editStarted.emit()
            self._seek(max(0,min(self.studio.model.count-1, self.studio.playhead +
                                 (1 if event.key() == Qt.Key.Key_Right else -1))))
            event.accept()
        else:
            super().keyPressEvent(event)


class _TitleDialog(QDialog):
    def __init__(self, settings, parent):
        super().__init__(parent)
        self.setWindowTitle('Text & data')
        self.setMinimumWidth(365)
        outer = QVBoxLayout(self)
        choices = QGridLayout()
        self.checks = {}
        fields = settings['fields']
        for i, (key, title) in enumerate(_FIELD_NAMES):
            check = QCheckBox(title)
            check.setChecked(key in fields or (not fields and key == 'frame'))
            self.checks[key] = check
            choices.addWidget(check, i//2, i%2)
        outer.addLayout(choices)
        form = QFormLayout()
        self.caption = QPlainTextEdit(settings['custom'])
        self.caption.setAccessibleName('Custom title')
        self.caption.setPlaceholderText('Optional caption')
        self.caption.setFixedHeight(62)
        form.addRow('Custom text', self.caption)
        self.position = QComboBox()
        for label, key in _POSITIONS:
            self.position.addItem(label, key)
        self.position.setCurrentIndex(self.position.findData(settings['position']))
        form.addRow('Position',self.position)
        self.size = QComboBox()
        for key in ('small','medium','large'):
            self.size.addItem(key.title(), key)
        self.size.setCurrentIndex(self.size.findData(settings['size']))
        form.addRow('Size',self.size)
        outer.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        for control in self.checks.values():
            control.toggled.connect(self.validate)
        self.caption.textChanged.connect(self.validate)
        self.validate()
        outer.addWidget(buttons)

    def validate(self):
        self.ok.setEnabled(any(box.isChecked() for box in self.checks.values())
                           or bool(self.caption.toPlainText().strip()))

    def settings(self):
        custom = self.caption.toPlainText().strip()
        fields = [key for key, check in self.checks.items() if check.isChecked()]
        if custom:
            fields.append('custom')
        return dict(fields=tuple(fields), custom=custom, position=self.position.currentData(),
                    size=self.size.currentData())


class RecordStudio(QFrame):
    changed = Signal()
    seekRequested = Signal(int)
    editStarted = Signal()

    def __init__(self, clip, model, parent=None):
        super().__init__(parent)
        self.clip, self.model = clip, model
        self.playhead = self.mode = 0
        self.selected = [0,0,None,0]
        self._text = dict(fields=(),custom='',position='bottom_left',size='medium',
                          first=0,last=model.count-1)
        self.setObjectName('bosRecordStudio')
        self.setStyleSheet('''
            QFrame#bosRecordStudio { background: #242b34; border: 1px solid #45505e; border-radius: 6px; }
            QFrame#bosRecordStudio QLabel { color: #d6dfe8; background: transparent; }
            QFrame#bosRecordStudio QPushButton { min-width: 0; min-height: 22px; padding: 2px 8px; }
            QFrame#bosRecordStudio QSpinBox, QFrame#bosRecordStudio QDoubleSpinBox {
                min-height: 19px; padding-top: 2px; padding-bottom: 2px; }
            QFrame#bosRecordStudio QPushButton[studioTab="true"]:checked {
                background: #394550; border: 1px solid #8a9caf; color: #ffffff; }
        ''')
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(9,5,9,6)
        outer.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.tab_group = QButtonGroup(self)
        self.tabs = []
        for index, name in enumerate(_TRACKS):
            button = QPushButton(name)
            button.setCheckable(True)
            button.setProperty('studioTab', True)
            button.setAccessibleName(f'Edit {name.lower()} track')
            self.tab_group.addButton(button,index)
            button.clicked.connect(lambda checked=False,i=index: self.set_mode(i))
            if name == 'Vectors':
                top.insertWidget(1, button)
                button.setVisible(getattr(clip, 'has_vector_track', False))
            else:
                top.addWidget(button)
            self.tabs.append(button)
        self.tabs[0].setChecked(True)
        self.header_hint = QLabel()
        self.header_hint.setMinimumWidth(0)
        self.header_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.header_hint, 1)
        top.addStretch()
        self.duration_label = QLabel()
        self.duration_label.setToolTip('Total playback time after speed edits.')
        top.addWidget(self.duration_label)
        top.addSpacing(8)
        top.addWidget(QLabel('MP4'))
        self.encoding = QComboBox()
        for fps, text in [(60,'60 fps'), (30,'30 fps'), (24,'24 fps'),
                          (120,'120 fps'),(240,'240 fps')]:
            self.encoding.addItem(text,fps)
        self.encoding.setAccessibleName('Encoded MP4 frame rate')
        self.encoding.setToolTip('Movie file frame rate. 60 fps is recommended for sharing. The Speed track sets movie duration.')
        top.addWidget(self.encoding)
        outer.addLayout(top)
        self.track = _StudioTrack(self)
        outer.addWidget(self.track)

        self.footer = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.footer.setSpacing(3)
        outer.addLayout(self.footer)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(5)
        self.split = QPushButton('✂ Split')
        self.split.setAccessibleName('Split at playhead')
        self.split.clicked.connect(self.split_at_playhead)
        self.add = QPushButton('+ Overlay')
        self.add.clicked.connect(self.add_item)
        self.delete = QPushButton('Delete')
        self.delete.clicked.connect(self.delete_selected)
        for btn in (self.split,self.add,self.delete):
            self.actions.addWidget(btn)
        self.appearance = QPushButton('Appearance…')
        self.appearance.setAccessibleName('Edit vector appearance')
        self.appearance.setToolTip('Change the arrow color, symbol and thickness throughout this analyzed clip.')
        self.appearance.clicked.connect(self.edit_appearance)
        self.actions.addWidget(self.appearance)
        self.actions.addSpacing(6)
        self.hint = QLabel()
        self.hint.setMinimumWidth(0)
        self.hint.setWordWrap(True)
        self.hint.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.actions.addWidget(self.hint,1)
        self.footer.addLayout(self.actions)

        self.inspector = QWidget()
        self.inspector_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.inspector)
        self.inspector_layout.setContentsMargins(0, 0, 0, 0)
        self.inspector_layout.setSpacing(5)
        bounds = QHBoxLayout()
        bounds.setSpacing(5)
        self.inspector_layout.addLayout(bounds)
        self.start = clip.source_frame(0)
        self.first = QSpinBox()
        self.last = QSpinBox()
        for name, field in [('In',self.first),('Out',self.last)]:
            field.setAccessibleName(f'Selected section {name.lower()} Cine frame')
            field.setKeyboardTracking(False)
            field.setGroupSeparatorShown(True)
            field.setRange(self.start,clip.source_frame(model.count-1))
            # Keep focus loss from committing a stale value to a newly selected section.
            field.valueChanged.connect(lambda number,f=field:self._exact_bounds(f, number))
            bounds.addWidget(QLabel(name))
            bounds.addWidget(field)
        bounds.addStretch(1)
        line = QHBoxLayout()
        line.setSpacing(5)
        self.inspector_layout.addLayout(line)
        self.fade_widgets = []
        for name in ('Fade in','Fade out'):
            label = QLabel(name)
            field = QDoubleSpinBox()
            field.setAccessibleName(name+' seconds')
            field.setRange(0., 99999)
            field.setDecimals(2)
            field.setSingleStep(.05)
            field.setSuffix(' s')
            field.setKeyboardTracking(False)
            field.valueChanged.connect(self._fades_changed)
            line.addWidget(label)
            line.addWidget(field)
            self.fade_widgets.extend((label,field))
        self.fade_in,self.fade_out = self.fade_widgets[1::2]
        self.speed_label = QLabel('Playback')
        self.speed = QDoubleSpinBox()
        self.speed.setRange(1, MAX_PLAYBACK_FPS)
        self.speed.setDecimals(2)
        self.speed.setSuffix(' fps')
        self.speed.setKeyboardTracking(False)
        self.speed.setAccessibleName('Selected section playback FPS')
        self.speed.setToolTip('Source frames per second in this section. Higher values play the action faster.')
        self.speed.valueChanged.connect(self._speed_changed)
        line.addWidget(self.speed_label)
        line.addWidget(self.speed)
        line.addStretch(1)
        self.footer.addWidget(self.inspector, 1)
        self._adapt_width(self.width())
        self.refresh()

    def _adapt_width(self, width):
        # Keep all fields reachable on a small display. A roomy preview recovers
        # another row of picture height by placing the inspector beside the tools.
        wide = width >= 990
        self.footer.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
        self.inspector_layout.setDirection(QBoxLayout.Direction.TopToBottom if width < 740
                                           else QBoxLayout.Direction.LeftToRight)
        self.header_hint.setVisible(wide)
        self.hint.setVisible(not wide)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adapt_width(event.size().width())

    def text_settings(self):
        return dict(self._text)

    def output_fps(self):
        return self.encoding.currentData()

    def showEvent(self, event):
        super().showEvent(event)
        # A cached preview may have advanced while the timeline was closed.
        self.set_mode(self.mode)

    @property
    def layer(self):
        return {0: 'overlay', 3: 'vectors'}.get(self.mode)

    def edit_appearance(self):
        from bos_vector_controls import VectorAppearance
        from bos_vectors import VectorStyle

        self.editStarted.emit()
        dialog = QDialog(self)
        dialog.setWindowTitle('Vector appearance')
        layout = QVBoxLayout(dialog)
        appearance = VectorAppearance()
        previous = self.model.vector_style
        appearance.set_settings((previous or self.clip.vector_style).settings())
        appearance.toggle.setChecked(True)
        appearance.toggle.hide()
        layout.addWidget(appearance)

        def preview_style():
            self.model.vector_style = VectorStyle.from_settings(appearance.settings())
            self.changed.emit()

        appearance.changed.connect(preview_style)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted and self.model.vector_style != previous:
            self.model.vector_style = previous
            self.changed.emit()
        dialog.deleteLater()

    def set_mode(self, mode):
        if self.track.drag is not None:
            self.track.drag = self.track.preview = None
        self.mode = mode
        self.tabs[mode].setChecked(True)
        self.selected[mode] = (self.model.overlay_index(self.playhead, self.layer) if self.layer else
                               self.model.speed_index(self.playhead) if mode == 1 else
                               0 if self._text['fields'] else None)
        self.refresh()

    def set_position(self, index):
        value = max(0,min(self.model.count-1, int(index)))
        if self.playhead != value:
            self.playhead = value
            if self.isVisible():
                self.track.update()
                self._update_actions()

    def _split_index(self):
        if self.layer:
            i = self.model.overlay_index(self.playhead, self.layer)
            if i is not None and self.playhead > self.model.spans(self.layer)[i].first:
                return i
        elif self.mode == 1:
            i = self.model.speed_index(self.playhead)
            if self.playhead > self.model.speeds[i].first:
                return i
        return None

    def _update_actions(self):
        self.split.setEnabled(self._split_index() is not None)
        if self.layer:
            self.add.setEnabled(self.model.overlay_index(self.playhead, self.layer) is None)
        else:
            self.add.setEnabled(True)

    def refresh(self):
        self.track.update()
        spans = self.track._spans()
        idx = self.selected[self.mode]
        if idx is not None and not 0 <= idx < len(spans):
            self.selected[self.mode] = idx = None
        self.split.setVisible(self.mode != 2)
        self.split.setToolTip('Cut the current section immediately before the displayed frame.')
        self.add.setVisible(self.mode != 1)
        self.appearance.setVisible(self.layer == 'vectors')
        self.add.setText('+ ' + _TRACKS[self.mode] if self.layer else
                         'Edit text…' if self._text['fields'] else '+ Text…')
        self.add.setToolTip('Fill the gap at the playhead.' if self.layer else 'Choose a caption and data to place on the video.')
        self.delete.setText('Merge' if self.mode == 1 else 'Delete')
        self.delete.setEnabled(idx is not None and (self.mode != 1 or len(spans) > 1))
        self.delete.setToolTip('Remove this cut and join the neighboring speed sections.' if self.mode == 1 else
                               'Remove the selected block from this track.')
        self._update_actions()
        self.duration_label.setText(f'Length {_duration(self.model.duration)}')
        for widget in self.fade_widgets:
            widget.setVisible(bool(self.layer))
        self.speed_label.setVisible(self.mode == 1)
        self.speed.setVisible(self.mode == 1)
        self.inspector.setVisible(idx is not None)
        hint = ('Drag ends to trim. Split and pull the facing ends apart for a gap.',
                'Split at the playhead, then set each section’s playback FPS.',
                'Add a caption or frame data. Drag its ends to set when it appears.',
                'Trim or split the arrows independently of the color map.')[self.mode]
        self.hint.setText(hint)
        self.header_hint.setText(hint)
        if idx is None:
            return
        first,last = spans[idx]
        for field, value in [(self.first, first),(self.last,last)]:
            with QSignalBlocker(field):
                field.setValue(value+self.start)
        self.first.setEnabled(self.mode != 1 or idx > 0)
        self.last.setEnabled(self.mode != 1 or idx < len(spans)-1)
        if self.layer:
            span = self.model.spans(self.layer)[idx]
            for field,value in ((self.fade_in,span.fade_in),(self.fade_out,span.fade_out)):
                with QSignalBlocker(field):
                    field.setValue(value)
        elif self.mode == 1:
            with QSignalBlocker(self.speed):
                self.speed.setValue(self.model.speeds[idx].fps)

    def _edited(self):
        self.refresh()
        self.changed.emit()

    def split_at_playhead(self):
        cut = self.playhead
        i = self._split_index()
        if i is None:
            return
        self.editStarted.emit()
        if self.layer:
            self.selected[self.mode] = self.model.split_overlay(i, cut, self.layer)
        else:
            self.selected[1] = self.model.split_speed(cut)
        self._edited()

    def add_item(self):
        frame = self.playhead
        self.editStarted.emit()
        if self.layer:
            if self.model.overlay_index(frame, self.layer) is not None:
                return
            left = max((span.last+1 for span in self.model.spans(self.layer) if span.last < frame), default=0)
            right = min((span.first-1 for span in self.model.spans(self.layer) if span.first > frame),
                        default=self.model.count-1)
            self.selected[self.mode] = self.model.add_overlay(left, right, self.layer)
        elif self.mode == 2:
            dialog = _TitleDialog(self._text,self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self._text.update(dialog.settings())
            self.selected[2] = 0
        else:
            return
        self._edited()

    def delete_selected(self):
        idx = self.selected[self.mode]
        if idx is None:
            return
        self.editStarted.emit()
        if self.layer:
            self.model.remove_overlay(idx, self.layer)
            self.selected[self.mode] = self.model.overlay_index(self.playhead, self.layer)
        elif self.mode == 1:
            if len(self.model.speeds) <= 1:
                return
            self.model.remove_speed_cut(max(1,idx))
            self.selected[1] = self.model.speed_index(self.playhead)
        else:
            self._text.update(fields=(),custom='',first=0,last=self.model.count-1)
            self.selected[2] = None
        self._edited()

    def _exact_bounds(self, field, number):
        idx = self.selected[self.mode]
        if idx is None:
            return
        self.editStarted.emit()
        first, last = self.track._spans()[idx]
        if field is self.first:
            self.apply_bounds(idx,min(number-self.start,last),last,'left')
        else:
            self.apply_bounds(idx,first,max(number-self.start,first),'right')

    def apply_bounds(self, idx, first, last, part):
        if self.layer:
            if part == 'body':
                self.model.move_overlay(idx, first, self.layer)
            else:
                self.model.trim_overlay(idx, first, last, self.layer)
        elif self.mode == 1:
            if part == 'left' and idx > 0:
                self.model.move_speed_cut(idx,first)
            elif part == 'right' and idx+1 < len(self.model.speeds):
                self.model.move_speed_cut(idx+1,last+1)
        else:
            self._text.update(first=first,last=last)
        self._edited()

    def _fades_changed(self):
        idx = self.selected[self.mode]
        if self.layer and idx is not None:
            self.editStarted.emit()
            self.model.set_fades(idx, self.fade_in.value(), self.fade_out.value(), self.layer)
            self._edited()

    def _speed_changed(self, fps):
        idx = self.selected[1]
        if self.mode == 1 and idx is not None:
            self.editStarted.emit()
            self.model.set_speed(idx,fps)
            self._edited()
