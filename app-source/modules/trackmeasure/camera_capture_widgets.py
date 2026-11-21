"""Compact preset editors and time labels used by the Camera Control workspace."""
from itertools import groupby

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QDoubleSpinBox, QFrame,
                               QHBoxLayout, QHeaderView, QMenu, QPushButton, QSizePolicy,
                               QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)


def number(value):
    return f'{value:,.3f}'.rstrip('0').rstrip('.')


def duration(seconds):
    if seconds == 0:
        return '0 s'
    if seconds < .001:
        return f'{number(seconds * 1_000_000)} µs'
    if seconds < 1:
        return f'{number(seconds * 1000)} ms'
    if seconds < 60:
        return f'{number(seconds)} s'
    minutes, rest = divmod(round(seconds, 2), 60)
    return f'{int(minutes)} min {number(rest)} s'


def geometry_label(mode):
    width, height, binning = mode
    return f'{width:,} × {height:,}' + (f' · {binning}×{binning} binning' if binning > 1 else '')


class CaptureNumber(QDoubleSpinBox):
    def textFromValue(self, value):
        # Retain the locale's decimal and grouping separators, with no redundant zeros.
        text = super().textFromValue(value)
        separator = self.locale().decimalPoint()
        if separator in text:
            text = text.rstrip('0').removesuffix(separator)
        return text


class _RateTable(QTableWidget):
    chosen = Signal(object)

    def __init__(self, parent):
        super().__init__(0, 3, parent)
        self.setAccessibleName('Capture frame rates and output resolutions')
        self.setAccessibleDescription('Use the arrow keys to browse. Press Enter to set the highlighted resolution.')
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setTabKeyNavigation(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(max(29, self.fontMetrics().height() + 12))
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for column in (1, 2):
            self.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
        self.itemClicked.connect(self.choose)

    def choose(self, item):
        if item and (option := item.data(Qt.UserRole)) is not None:
            self.chosen.emit(option)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.choose(self.currentItem())
            event.accept()
        else:
            super().keyPressEvent(event)


class _RatePopup(QFrame):
    def __init__(self, button, editor):
        super().__init__(button, Qt.Popup)
        self.setObjectName('camera_rate_popup')
        self.setAccessibleName('Capture frame rate presets')
        self.setStyleSheet('QFrame#camera_rate_popup { background: #252b34; border: 1px solid #596575; }')
        self.setAttribute(Qt.WA_NoMouseReplay)
        self.editor = editor
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)
        self.table = _RateTable(self)
        layout.addWidget(self.table)
        self.custom_button = QPushButton('Enter exact fps…')
        self.custom_button.setFocusPolicy(Qt.StrongFocus)
        self.custom_button.clicked.connect(self.edit_exact)
        layout.addWidget(self.custom_button)
        self.table.chosen.connect(self.select_option)

    def edit_exact(self):
        self.close()
        self.editor.window().activateWindow()
        self.editor.setFocus(Qt.OtherFocusReason)
        self.editor.selectAll()

    def select_option(self, option):
        button = self.parentWidget()
        self.close()
        button.chosen.emit(option)

    def populate(self, options, current):
        table = self.table
        table.clearSpans()
        table.setRowCount(0)
        factors = {option.binning for option in options if option.binning > 1}
        binned = 'Binned'
        if len(factors) == 1:
            factor = next(iter(factors))
            binned = f'Binned · {factor}×{factor}'
        table.setHorizontalHeaderLabels(('FPS', 'Standard', binned))
        checked = first = None
        for fps, group in groupby(options, key=lambda option: option.fps):
            group = list(group)
            columns = ([option for option in group if option.binning == 1],
                       [option for option in group if option.binning > 1])
            start = table.rowCount()
            count = max(map(len, columns))
            table.setRowCount(start + count)
            label = QTableWidgetItem(number(fps))
            label.setFlags(Qt.ItemIsEnabled)
            label.setTextAlignment(Qt.AlignCenter)
            table.setItem(start, 0, label)
            if count > 1:
                table.setSpan(start, 0, count, 1)
            for column, choices in enumerate(columns, 1):
                for offset in range(count):
                    item = QTableWidgetItem()
                    item.setTextAlignment(Qt.AlignCenter)
                    table.setItem(start + offset, column, item)
                    if offset >= len(choices):
                        item.setFlags(Qt.NoItemFlags)
                        if not choices:
                            item.setText('—')
                            item.setToolTip('No listed preset at this frame rate.')
                        continue
                    option = choices[offset]
                    w, h, factor = option.mode
                    geometry = f'{w:,} × {h:,}'
                    if factor > 1 and len(factors) > 1:
                        geometry += f' · {factor}×{factor}'
                    active = (option.fps, option.mode) == current
                    mode = 'standard' if factor == 1 else f'{factor}×{factor} binned'
                    description = f'{number(fps)} fps, {mode}, {w:,} × {h:,}'
                    item.setText(('✓  ' if active else '') + geometry)
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    item.setData(Qt.UserRole, option)
                    item.setData(Qt.AccessibleTextRole, description + (', current camera setting' if active else ''))
                    item.setToolTip(('Current camera setting · ' if active else '') + description)
                    if active:
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                        checked = item
                    if first is None:
                        first = item
        self.initial_item = checked if checked is not None else first
        if self.initial_item is not None:
            table.setCurrentItem(self.initial_item)

    def open_at_button(self):
        button = self.parentWidget()
        screen = button.screen().availableGeometry()
        anchor = button.mapToGlobal(QPoint(0, 0))
        below = screen.bottom() + 1 - (anchor.y() + button.height())
        above = anchor.y() - screen.top()
        height = (self.table.horizontalHeader().sizeHint().height()
                  + self.table.verticalHeader().defaultSectionSize() * min(12, self.table.rowCount())
                  + self.custom_button.sizeHint().height() + 20)
        width = max(360, self.fontMetrics().horizontalAdvance('✓  1,280 × 1,024') * 2 + 110)
        width = min(width, screen.width())
        height = min(height, max(above, below))
        x = max(screen.left(), min(anchor.x() + button.width() - width, screen.right() + 1 - width))
        y = anchor.y() + button.height() if below >= height else anchor.y() - height
        self.setGeometry(x, y, width, height)
        if self.initial_item is not None:
            self.table.setCurrentItem(self.initial_item)
        self.show()
        self.table.setFocus(Qt.PopupFocusReason)
        if self.table.currentItem() is not None:
            self.table.scrollToItem(self.table.currentItem())


class RatePresetButton(QToolButton):
    chosen = Signal(object)

    def __init__(self, editor):
        super().__init__()
        self.popup = _RatePopup(self, editor)
        self.choices = ((), None)
        self.displayed = None
        self.clicked.connect(self.open_picker)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_choices(self, options, current):
        self.choices = options, current
        if not self.popup.isVisible():
            self._refresh()

    def _refresh(self):
        if self.displayed != self.choices:
            self.popup.populate(*self.choices)
            self.displayed = self.choices

    def open_picker(self):
        if self.isEnabled():
            self._refresh()
            self.popup.open_at_button()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Down, Qt.Key_F4, Qt.Key_Return, Qt.Key_Enter):
            self.open_picker()
            event.accept()
        else:
            super().keyPressEvent(event)


def preset_field(name, suffix, *, rate_table=False):
    editor = CaptureNumber()
    editor.setAccessibleName(name)
    editor.setSuffix(suffix)
    editor.setDecimals(3)
    editor.setRange(.001, 1e9)
    editor.setGroupSeparatorShown(True)
    editor.setButtonSymbols(QAbstractSpinBox.NoButtons)
    editor.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    presets = RatePresetButton(editor) if rate_table else QToolButton()
    presets.setAccessibleName(f'{name} presets')
    presets.setToolTip(f'Choose {name.lower()} or type an exact value in the field.')
    presets.setArrowType(Qt.DownArrow)
    presets.setFixedWidth(30)
    presets.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    presets.setStyleSheet('QToolButton::menu-indicator { width: 0px; }')
    if not rate_table:
        presets.setPopupMode(QToolButton.InstantPopup)
        presets.setMenu(QMenu(presets))

    field = QWidget()
    layout = QHBoxLayout(field)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    layout.addWidget(editor, 1)
    layout.addWidget(presets)
    return editor, presets, field


def add_custom(menu, editor):
    menu.addSeparator()

    def select():
        editor.setFocus(Qt.OtherFocusReason)
        editor.selectAll()

    menu.addAction('Enter exact value…', select)


TRIGGER_STYLE = """
QSlider::groove:horizontal { height: 7px; border-radius: 0px; background: #404b57; }
QSlider::sub-page:horizontal { background: #3caa84; border-radius: 0px; }
QSlider::add-page:horizontal { background: #d46a6d; border-radius: 0px; }
QSlider::handle:horizontal { width: 10px; margin: -4px 0; border: 1px solid #f0f4f7;
                            background: #e4e9f0; border-radius: 0px; }
"""
