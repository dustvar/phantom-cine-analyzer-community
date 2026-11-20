"""Record Studio controls for the analyzed BOS result.

All tracks share the finished movie's clock. Exact interval controls display
inclusive native Cine frame numbers.
"""
import math
import sys

from PySide6.QtCore import QCoreApplication, Qt, Signal, QSignalBlocker, QPointF, QRectF
from PySide6.QtGui import QContextMenuEvent, QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (QBoxLayout, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QMenu, QPlainTextEdit, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget)

from bos_playback_controls import SampleFrameBox
from bos_speed_ramp_ui import SpeedRampDialog, rate_curve
from bos_studio_model import MAX_PLAYBACK_FPS


_TRACKS = ('Overlay', 'Speed', 'Text', 'Vectors')
_COLORS = (
    ('#366f5e', '#74d8af'), ('#3b5c79', '#92c6f1'), ('#69527b', '#d4ace8'), ('#66532f', '#f0c77c')
)
_FIELD_NAMES = (('frame', 'Frame X of N'), ('cine_frame', 'Cine frame number'), ('playback', 'Playback FPS'),
                ('capture', 'Capture FPS'), ('slowdown', 'Slowdown'),
                ('time', 'Time from trigger'), ('camera_model', 'Camera model'),
                ('exposure', 'Exposure time'), ('resolution', 'Resolution'))


def _duration(seconds):
    # Avoid printing 0:60.00 around minute boundaries.
    if seconds < .01:
        return f'0:{seconds:06.3f}'
    centis = round(seconds * 100)
    minutes, centis = divmod(centis, 6000)
    return f'{minutes}:{centis/100:05.2f}'


class _StudioTrack(QWidget):
    """Stack the editable lanes on the same presentation-time scale as the MP4."""
    LABEL_WIDTH = 84
    RULER_HEIGHT = 23
    ROW_HEIGHT = 28

    def __init__(self, studio):
        super().__init__(studio)
        self.studio = studio
        self.rows = [0, 3, 1, 2] if getattr(studio.clip, 'has_vector_track', False) else [0, 1, 2]
        self.drag = None
        self.preview = None
        self.preview_clock = None
        self.setObjectName('studioTrack')
        self.setAccessibleName('Record Studio tracks in movie seconds')
        self.setToolTip('Click a track or block to edit it. Drag the time ruler to seek.')
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(self.RULER_HEIGHT+self.ROW_HEIGHT*len(self.rows)+2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.buttons = []
        self.group = QButtonGroup(self)
        for mode, name in enumerate(_TRACKS):
            button = QPushButton(name, self)
            button.setAccessibleName(f'Edit {name.lower()} track')
            button.setCheckable(True)
            button.setProperty('studioTab', True)
            button.clicked.connect(lambda checked=False, m=mode: studio.set_mode(m))
            self.group.addButton(button, mode)
            self.buttons.append(button)
            if mode in self.rows:
                y = self.RULER_HEIGHT + self.rows.index(mode)*self.ROW_HEIGHT
                button.setGeometry(3, y+3, self.LABEL_WIDTH-6, self.ROW_HEIGHT-6)
            else:
                button.hide()
        self.buttons[0].setChecked(True)

    def lane(self, mode=None):
        mode = self.studio.mode if mode is None else mode
        y = self.RULER_HEIGHT + self.rows.index(mode)*self.ROW_HEIGHT
        return QRectF(self.LABEL_WIDTH+2, y+2, max(1., self.width()-self.LABEL_WIDTH-10), self.ROW_HEIGHT-4)

    def row_at(self, y):
        row = math.floor((y-self.RULER_HEIGHT)/self.ROW_HEIGHT)
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def _time_x(self, seconds, clock):
        lane = self.lane()
        return lane.left()+seconds/clock.duration*lane.width()

    def x_for(self, boundary, clock=None):
        clock = clock or self.studio.model
        return self._time_x(clock.time_at(boundary), clock)

    def _frame(self, x):
        lane = self.lane()
        clock = self.studio.model
        return clock.frame_at((x-lane.left())/lane.width()*clock.duration)

    def _spans(self, mode=None):
        return self.studio.spans(self.studio.mode if mode is None else mode)

    def span_rect(self, start, end, mode=None, clock=None):
        lane = self.lane(mode)
        left, right = self.x_for(start, clock), self.x_for(end+1, clock)
        inset = min(1., (right-left)*.06)
        return QRectF(left+inset, lane.top()+2, max(.5, right-left-2*inset), lane.height()-4)

    def _hit(self, mode, pos):
        if mode is None or not self.lane(mode).contains(pos):
            return None, None
        spans = self._spans(mode)
        for i, (start, end) in enumerate(spans):
            left, right = self.x_for(start), self.x_for(end+1)
            if left <= pos.x() < right or (i == len(spans)-1 and pos.x() == right):
                threshold = min(8., (right-left)/3.)
                if pos.x()-left <= threshold and (mode != 1 or i > 0):
                    return i, 'left'
                if right-pos.x() <= threshold and (mode != 1 or i < len(spans)-1):
                    return i, 'right'
                return i, 'body'
        return None, None

    def _cut_at(self, pos):
        lane = self.lane(1)
        if not lane.contains(pos):
            return None
        clock = self.studio.model
        cuts = range(1, len(clock.speeds))
        near = min(cuts, key=lambda i: abs(self.x_for(clock.speeds[i].first)-pos.x()), default=None)
        if near is not None and abs(self.x_for(clock.speeds[near].first)-pos.x()) <= 9:
            return near
        if pos.y() >= lane.bottom()-10:
            for cut in cuts:
                if clock.speeds[cut].ramp:
                    first, last = clock.ramp_bounds(cut)
                    if self._time_x(first, clock) <= pos.x() <= self._time_x(last, clock):
                        return cut
        return None

    def _draw_speed_cuts(self, painter, clock):
        lane = self.lane(1)
        for cut, span in enumerate(clock.speeds[1:], 1):
            x = self.x_for(span.first, clock)
            accent = QColor('#b4e1ff' if span.ramp else '#92aac0')
            if span.ramp:
                first, last = clock.ramp_bounds(cut)
                x0, x1 = self._time_x(first, clock), self._time_x(last, clock)
                band = QRectF(x0, lane.bottom()-7, max(1., x1-x0), 6)
                painter.fillRect(band, QColor('#273a4d'))
                painter.setPen(QPen(accent, 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(rate_curve(band.adjusted(0, 1, 0, -1), clock.speeds[cut-1].fps,
                                            span.fps, span.curve))
            y = lane.top()+5
            mark = QPainterPath(QPointF(x, y-3.5))
            mark.lineTo(x+3.5, y)
            mark.lineTo(x, y+3.5)
            mark.lineTo(x-3.5, y)
            mark.closeSubpath()
            painter.setPen(QPen(accent, 1.1))
            painter.setBrush(accent if span.ramp else QColor('#273a4d'))
            painter.drawPath(mark)

    def _draw_ruler(self, painter, clock):
        lane = self.lane()
        # Pick readable divisions from the familiar 1, 2, 5 sequence.
        step = clock.duration * 76 / lane.width()
        decade = 10.**math.floor(math.log10(step))
        step = next(k for k in (1, 2, 5, 10) if k*decade >= step) * decade
        baseline = self.RULER_HEIGHT-2
        painter.setPen(QColor('#aebcca'))
        painter.drawText(QRectF(8, 0, self.LABEL_WIDTH-10, baseline), Qt.AlignmentFlag.AlignVCenter, 'Seconds')
        end_label = f'{clock.duration:,.3f}'.rstrip('0').rstrip('.')+' s'
        fm = painter.fontMetrics()
        end_x = lane.right()-fm.horizontalAdvance(end_label)
        # Leave space for the exact end label, including very short clips.
        for i in range(int(clock.duration/step)+1):
            seconds = i*step
            x = self._time_x(seconds, clock)
            label = format(seconds, '.10g')
            if x+fm.horizontalAdvance(label)+7 >= end_x and i:
                break
            painter.drawLine(QPointF(x, baseline-4), QPointF(x, baseline))
            painter.drawText(QPointF(x, baseline-7), label)
        painter.drawText(QPointF(end_x, baseline-7), end_label)
        painter.drawLine(QPointF(lane.right(), baseline-4), QPointF(lane.right(), baseline))

    def _draw_row(self, painter, mode, clock):
        panel = self.studio
        lane = self.lane(mode)
        active = panel.mode == mode
        color, accent = _COLORS[mode]
        painter.setPen(QPen(QColor(accent), 1.4) if active else Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#2a3640' if active else '#1b222a'))
        painter.drawRoundedRect(QRectF(1., lane.top()-1, self.width()-3., lane.height()+2), 4, 4)
        layer = {0:'overlay', 3:'vectors'}.get(mode)
        spans = self._spans(mode)
        if mode == 1 and self.preview_clock is not None:
            spans = [(s.first, s.last) for s in clock.speeds]
        elif active and self.preview is not None:
            idx, first, last = self.preview
            spans[idx] = (first, last)
        for i, (start, end) in enumerate(spans):
            rect = self.span_rect(start, end, mode, clock)
            selected = active and panel.selected[mode] == i
            painter.setPen(QPen(QColor('#f1f4f7' if selected else accent), 1.5 if selected else .6))
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(rect, min(rect.width()*.1,3.), 3)
            painter.save()
            painter.setClipRect(rect)
            if layer:
                span = panel.model.spans(layer)[i]
                label = 'Vector field' if mode == 3 else 'Color map'
                start_time, stop_time = clock.time_at(start), clock.time_at(end)
                painter.setPen(QPen(QColor(accent), 1.2))
                if span.fade_in:
                    x = self._time_x(min(stop_time, start_time+span.fade_in), clock)
                    painter.drawLine(QPointF(rect.left(), rect.bottom()-1), QPointF(x, rect.bottom()-6))
                if span.fade_out:
                    x = self._time_x(max(start_time, stop_time-span.fade_out), clock)
                    painter.drawLine(QPointF(x, rect.bottom()-6), QPointF(rect.right(), rect.bottom()-1))
            elif mode == 1:
                label = f'{clock.speeds[i].fps:g} fps'
            else:
                label = panel.text_blocks[i]['custom'].replace('\n', ' ') or 'Frame data'
            if rect.width() > 32:
                painter.setPen(QColor('#f3f7fa'))
                label = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, int(rect.width()-16))
                # Leave the lower edge free for the playback-rate curve.
                caption = rect.adjusted(8., 0., -8., -4. if mode == 1 else 0.)
                painter.drawText(caption, Qt.AlignmentFlag.AlignVCenter, label)
            if selected and rect.width() >= 12:
                painter.setPen(QPen(QColor(accent), 2))
                for x in (rect.left()+3, rect.right()-3):
                    painter.drawLine(QPointF(x,rect.center().y()-4),QPointF(x,rect.center().y()+4))
            painter.restore()
        if mode == 1:
            self._draw_speed_cuts(painter, clock)
        if not spans:
            painter.setPen(QColor('#aab6c3'))
            painter.drawText(lane.adjusted(7,0,0,0), Qt.AlignmentFlag.AlignVCenter, 'No blocks')

    def paintEvent(self, event):
        clock = self.preview_clock or self.studio.model
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = painter.font()
        font.setPointSizeF(max(7.5, font.pointSizeF()-.8))
        painter.setFont(font)
        self._draw_ruler(painter, clock)
        for mode in self.rows:
            self._draw_row(painter, mode, clock)
        x = self.x_for(self.studio.playhead, clock)
        y = self.RULER_HEIGHT
        painter.setPen(QPen(QColor('#ffdc87'),1.5))
        painter.drawLine(QPointF(x,y-4),QPointF(x,self.height()-2))
        caret = QPainterPath(QPointF(x-4,y-7))
        caret.lineTo(x+4,y-7)
        caret.lineTo(x,y)
        caret.closeSubpath()
        painter.fillPath(caret,QColor('#ffdc87'))

    def _seek(self, frame):
        self.studio.set_position(frame)
        self.studio.seekRequested.emit(frame)

    def cancel_drag(self):
        self.drag = self.preview = self.preview_clock = None
        self.update()

    def contextMenuEvent(self, event):
        if event.reason() == QContextMenuEvent.Reason.Keyboard:
            if self.studio.mode == 1:
                self.studio.choose_ramp()
                event.accept()
                return
        else:
            cut = self._cut_at(event.pos())
            if cut is not None:
                self.studio.edit_speed_ramp(cut)
                event.accept()
                return
        super().contextMenuEvent(event)

    def mousePressEvent(self, event):
        # On Cocoa the physical Control key maps to Qt Meta unless swapping
        # was disabled. Treat Control-click like the trackpad secondary click.
        mac_control = (Qt.KeyboardModifier.ControlModifier if QCoreApplication.testAttribute(
            Qt.ApplicationAttribute.AA_MacDontSwapCtrlAndMeta) else Qt.KeyboardModifier.MetaModifier)
        secondary = (event.button() == Qt.MouseButton.RightButton or
                     sys.platform == 'darwin' and event.button() == Qt.MouseButton.LeftButton
                     and bool(event.modifiers() & mac_control))
        if secondary:
            cut = self._cut_at(event.position())
            if cut is not None:
                self.studio.edit_speed_ramp(cut)
                event.accept()
                return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus()
        panel = self.studio
        pos = event.position()
        mode = self.row_at(pos.y())
        frame = self._frame(pos.x())
        panel.editStarted.emit()
        if mode is not None and mode != panel.mode:
            panel.set_mode(mode)
        idx, part = self._hit(mode, pos)
        if idx is None:
            self.drag = {'part': 'seek'}
            if mode is not None:
                panel.selected[mode] = None
            self._seek(frame)
        else:
            panel.selected[mode] = idx
            start, end = self._spans()[idx]
            self.drag = dict(idx=idx, part=part, x=pos.x(), start=start, end=end, frame=frame)
        panel.refresh()
        event.accept()

    def _bounds(self, x):
        drag = self.drag
        idx, part, start, end = (drag[k] for k in ('idx','part','start','end'))
        model = self.studio.model
        spans = self._spans()
        fraction_delta = (x-drag['x'])/self.lane().width()
        if self.studio.mode == 1:
            if part == 'body':
                return idx, start, end
            cut = idx if part == 'left' else idx+1
            boundary = model.speed_boundary_at_fraction(cut, fraction_delta)
            return (idx, boundary, end) if part == 'left' else (idx, start, boundary-1)
        anchor = start if part == 'left' else end+1 if part == 'right' else drag['frame']
        time = model.time_at(anchor)+fraction_delta*model.duration
        step = model.nearest_boundary(time)-anchor
        before = spans[idx-1][1]+1 if idx else 0
        after = spans[idx+1][0]-1 if idx+1 < len(spans) else model.count-1
        if part == 'left':
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
            mode = self.row_at(pos.y())
            _, part = self._hit(mode, pos)
            cursor = (Qt.CursorShape.SizeHorCursor if part in ('left','right') else
                      Qt.CursorShape.OpenHandCursor if part == 'body' and mode != 1
                      else Qt.CursorShape.PointingHandCursor)
            self.setCursor(cursor)
        elif self.drag['part'] == 'seek':
            self._seek(self._frame(pos.x()))
        elif abs(pos.x()-self.drag['x']) >= 2 or self.preview is not None:
            self.preview = self._bounds(pos.x())
            if self.studio.mode == 1 and self.drag['part'] != 'body':
                idx, first, last = self.preview
                self.preview_clock = self.studio.model.copy()
                if self.drag['part'] == 'left':
                    self.preview_clock.move_speed_cut(idx, first)
                else:
                    self.preview_clock.move_speed_cut(idx+1, last+1)
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
            if (first,last) != (drag['start'],drag['end']):
                self.studio.apply_bounds(idx, first, last, drag['part'])
        else:
            self._seek(drag['frame'])
        self.cancel_drag()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.drag is not None:
            self.cancel_drag()
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
        return dict(fields=tuple(fields), custom=custom)


class RecordStudio(QFrame):
    changed = Signal()
    seekRequested = Signal(int)
    editStarted = Signal()

    def __init__(self, clip, model, parent=None):
        super().__init__(parent)
        self.clip, self.model = clip, model
        self.playhead = self.mode = 0
        self.selected = [0,0,None,0]
        self.text_blocks = []
        self.empty_text = dict(fields=(), custom='', position='center', size='medium',
                               first=0, last=model.count-1)
        self.setObjectName('bosRecordStudio')
        self.setStyleSheet('''
            QFrame#bosRecordStudio { background: #242b34; border: 1px solid #45505e; border-radius: 6px; }
            QFrame#bosRecordStudio QLabel { color: #d6dfe8; background: transparent; }
            QFrame#bosRecordStudio QPushButton { min-width: 0; min-height: 22px; padding: 2px 8px; }
            QFrame#bosRecordStudio QSpinBox, QFrame#bosRecordStudio QDoubleSpinBox {
                min-height: 19px; padding-top: 2px; padding-bottom: 2px; }
            QFrame#bosRecordStudio QPushButton[studioTab="true"] {
                min-height: 0; padding: 0 6px; text-align: left; border: 1px solid transparent;
                background: transparent; color: #bcc8d4; }
            QFrame#bosRecordStudio QPushButton[studioTab="true"]:checked {
                background: #394550; border: 1px solid #8a9caf; color: #ffffff; }
        ''')
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(9,5,9,6)
        outer.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(4)
        self._ramp_dialog = None
        self.track = _StudioTrack(self)
        self.tabs = self.track.buttons
        self.split = QPushButton('✂ Split')
        self.split.setAccessibleName('Split at playhead')
        self.split.setToolTip('Cut the active track immediately before the displayed frame.')
        self.split.clicked.connect(self.split_at_playhead)
        top.addWidget(self.split)
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
        outer.addWidget(self.track)

        self.footer = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.footer.setSpacing(3)
        outer.addLayout(self.footer)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(5)
        self.add = QPushButton('+ Overlay')
        self.add.clicked.connect(self.add_item)
        self.delete = QPushButton('Delete')
        self.delete.clicked.connect(self.delete_selected)
        for btn in (self.add,self.delete):
            self.actions.addWidget(btn)
        self.appearance = QPushButton('Appearance…')
        self.appearance.setAccessibleName('Edit vector appearance')
        self.appearance.setToolTip('Change the arrow color, symbol and thickness throughout this analyzed clip.')
        self.appearance.clicked.connect(self.edit_appearance)
        self.actions.addWidget(self.appearance)
        self.ramp = QPushButton('Speed ramp…')
        self.ramp.setAccessibleName('Edit a speed ramp')
        self.ramp.setToolTip('Choose a speed cut and set how long it takes to reach the next playback speed.')
        self.ramp.clicked.connect(self.choose_ramp)
        self.actions.addWidget(self.ramp)
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
        self.first = SampleFrameBox(self.start, model.sampling)
        self.last = SampleFrameBox(self.start, model.sampling)
        for name, field in [('In',self.first),('Out',self.last)]:
            field.setAccessibleName(f'Selected section {name.lower()} Cine frame')
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
        values = dict(self.text_blocks[0] if self.text_blocks else self.empty_text)
        if len(self.text_blocks) > 1:
            values['blocks'] = tuple(dict(block) for block in self.text_blocks)
        return values

    def _text_index_at(self, index):
        return next((i for i, block in enumerate(self.text_blocks)
                     if block['first'] <= index <= block['last']), None)

    def set_text_placement(self, index, placement):
        i = self._text_index_at(index)
        if i is not None:
            self.text_blocks[i].update(placement)
            self._edited()

    def select_text_at(self, index):
        i = self._text_index_at(index)
        if i is not None:
            self.set_mode(2)
            self.selected[2] = i
            self.refresh()

    def spans(self, mode):
        if mode == 2:
            return [(b['first'], b['last']) for b in self.text_blocks]
        spans = self.model.speeds if mode == 1 else self.model.spans('vectors' if mode == 3 else 'overlay')
        return [(s.first, s.last) for s in spans]

    def output_fps(self):
        return self.encoding.currentData()

    def showEvent(self, event):
        super().showEvent(event)
        # A cached preview may have advanced while the timeline was closed.
        self.set_mode(self.mode)

    @property
    def layer(self):
        return {0: 'overlay', 3: 'vectors'}.get(self.mode)

    def choose_ramp(self):
        spans = self.model.speeds
        if len(spans) < 2:
            return
        if len(spans) == 2:
            self.edit_speed_ramp(1)
            return
        menu = QMenu(self.ramp)
        for cut, span in enumerate(spans[1:], 1):
            frame = self.clip.source_frame(span.first)
            label = f'Frame {frame:,}   ·   {spans[cut-1].fps:g} → {span.fps:g} fps'
            if span.ramp:
                label += f'   ·   {span.ramp:g} s'
            action = menu.addAction(label)
            action.triggered.connect(lambda checked=False, i=cut: self.edit_speed_ramp(i))
            if cut == max(1, self.selected[1] or 0):
                menu.setActiveAction(action)
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(self.ramp.mapToGlobal(self.ramp.rect().bottomLeft()))

    def edit_speed_ramp(self, cut):
        if self._ramp_dialog is not None:
            return
        self.editStarted.emit()
        self.set_mode(1)
        self.selected[1] = cut
        self.refresh()
        span = self.model.speeds[cut]
        dialog = SpeedRampDialog(self.model, cut, self.clip.source_frame(span.first), self)
        self._ramp_dialog = dialog

        def finish(result):
            self._ramp_dialog = None
            if result == QDialog.DialogCode.Accepted:
                duration, curve = dialog.settings()
                if duration != span.ramp or curve != span.curve:
                    self.model.set_ramp(cut, duration, curve)
                    self._edited()
            dialog.deleteLater()

        dialog.finished.connect(finish)
        dialog.open()

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
        self.track.cancel_drag()
        self.mode = mode
        self.tabs[mode].setChecked(True)
        self.selected[mode] = (self.model.overlay_index(self.playhead, self.layer) if self.layer else
                               self.model.speed_index(self.playhead) if mode == 1 else
                               self._text_index_at(self.playhead))
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
        else:
            i = self._text_index_at(self.playhead)
            if i is not None and self.playhead > self.text_blocks[i]['first']:
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
        self.add.setVisible(self.mode != 1)
        self.ramp.setVisible(self.mode == 1)
        self.ramp.setEnabled(len(self.model.speeds) > 1)
        self.appearance.setVisible(self.layer == 'vectors')
        self.add.setText('+ ' + _TRACKS[self.mode] if self.layer else
                         'Edit text…' if idx is not None else '+ Text…')
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
                ('Split at the playhead to set a new playback FPS.' if len(self.model.speeds) < 2 else
                 'Right-click a speed cut to ramp between sections.'),
                'Drag text on the picture to place it. Drag a corner to resize.',
                'Trim or split the arrows independently of the color map.')[self.mode]
        self.hint.setText(hint)
        self.header_hint.setText(hint)
        if idx is None:
            return
        first,last = spans[idx]
        for field, value in [(self.first, first),(self.last,last)]:
            with QSignalBlocker(field):
                field.setValue(self.clip.source_frame(value))
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
        elif self.mode == 1:
            self.selected[1] = self.model.split_speed(cut)
        else:
            block = self.text_blocks[i]
            self.text_blocks[i:i+1] = [dict(block, last=cut-1), dict(block, first=cut)]
            self.selected[2] = i+1
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
            idx = self.selected[2]
            if idx is None:
                idx = self._text_index_at(frame)
            block = self.text_blocks[idx] if idx is not None else self.empty_text
            dialog = _TitleDialog(block, self)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            content = dialog.settings() if accepted else None
            dialog.deleteLater()
            if not accepted:
                return
            if idx is None:
                idx = sum(b['last'] < frame for b in self.text_blocks)
                first = self.text_blocks[idx-1]['last']+1 if idx else 0
                last = self.text_blocks[idx]['first']-1 if idx < len(self.text_blocks) else self.model.count-1
                block = dict(self.empty_text, first=first, last=last)
                self.text_blocks.insert(idx, block)
            block.update(content)
            self.selected[2] = idx
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
            del self.text_blocks[idx]
            self.selected[2] = self._text_index_at(self.playhead)
        self._edited()

    def _exact_bounds(self, field, number):
        idx = self.selected[self.mode]
        if idx is None:
            return
        self.editStarted.emit()
        first, last = self.track._spans()[idx]
        sample = self.model.sampling.nearest(number-self.start)
        if field is self.first:
            self.apply_bounds(idx,min(sample,last),last,'left')
        else:
            self.apply_bounds(idx,first,max(sample,first),'right')

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
            previous_end = self.text_blocks[idx-1]['last'] if idx else -1
            next_start = self.text_blocks[idx+1]['first'] if idx+1 < len(self.text_blocks) else self.model.count
            self.text_blocks[idx].update(first=max(first, previous_end+1), last=min(last, next_start-1))
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
