"""The window's parts: the Wanted table, the decode table, the log, the
command line."""

import re
from urllib.parse import quote

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMenu, QPlainTextEdit, QTableView, QWidget)

from jtcon.core.messages import base_call
from jtcon.gui.models import COLOURS, DecodeModel, WantedModel
from jtcon.ui.wanted import SORT_BY_COLUMN

LOG_LINES = 500         # log lines kept
QRZ_LOOKUP = "https://www.qrz.com/db/{call}"

_SGR = re.compile(r"\x1b\[([0-9;]*)m")
# ANSI colour numbers (30-37, 90-97) to our colours; others show plain.
_ANSI_COLOURS = {1: "red", 2: "green", 3: "yellow", 5: "magenta", 6: "cyan"}


def ansi_segments(text: str) -> list:
    """Split *text* at its ANSI colour codes: [(plain text, bold, colour name)]."""
    out, bold, colour, pos = [], False, None, 0
    for m in _SGR.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], bold, colour))
        pos = m.end()
        for code in [int(c) for c in m.group(1).split(";") if c] or [0]:
            if code == 0:
                bold, colour = False, None
            elif code == 1:
                bold = True
            elif code == 2:
                colour = "dim"
            elif code == 22:
                bold = False
            elif 30 <= code <= 37 or 90 <= code <= 97:
                colour = _ANSI_COLOURS.get(code % 10)
            elif code == 39:
                colour = None
    if pos < len(text):
        out.append((text[pos:], bold, colour))
    return out


def fixed_font():
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)


class LogView(QPlainTextEdit):
    """jtcon's own messages.  A line with a key replaces the line before it
    when that one has the same key (progress shown on one line, see LogLine)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setMaximumBlockCount(LOG_LINES)
        self.setFont(fixed_font())
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._last_key: str | None = None

    def lines(self) -> list:
        text = self.toPlainText()
        return text.split("\n") if text else []

    def add(self, text: str, key: str | None = None) -> None:
        bar = self.verticalScrollBar()
        follow = bar.value() >= bar.maximum()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if key is not None and key == self._last_key and not self.document().isEmpty():
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock,
                                QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            self._insert(cursor, text.split("\n")[0])
        else:
            for line in text.split("\n"):
                if not self.document().isEmpty():
                    cursor.insertBlock()
                self._insert(cursor, line)
        self._last_key = key
        if follow:
            bar.setValue(bar.maximum())

    def _insert(self, cursor: QTextCursor, line: str) -> None:
        for text, bold, colour in ansi_segments(line):
            fmt = QTextCharFormat()
            if bold:
                fmt.setFontWeight(700)
            if colour:
                fmt.setForeground(COLOURS[colour])
            cursor.insertText(text, fmt)

    def reset(self) -> None:
        self.clear()
        self._last_key = None


class DecodesView(QTableView):
    """The decode table: follows new decodes while scrolled to the bottom,
    stays put while scrolled back."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.decodes = DecodeModel(self)
        self.setModel(self.decodes)
        self.setFont(fixed_font())
        self.setWordWrap(False)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        rows = self.verticalHeader()
        rows.hide()
        rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        rows.setDefaultSectionSize(self.fontMetrics().height() + 4)
        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._follow = True
        self.decodes.rowsAboutToBeInserted.connect(self._before_insert)
        self.decodes.rowsInserted.connect(self._after_insert)
        self.decodes.modelReset.connect(self._size_columns)

    @property
    def at_end(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum()

    def _before_insert(self, *_) -> None:
        self._follow = self.at_end

    def _after_insert(self, *_) -> None:
        if self._follow:
            self.scrollToBottom()

    # Widths from typical contents, in characters; columns can be dragged wider.
    _WIDTHS = {"idx": 3, "local": 9, "time": 7, "db": 4, "dt": 5, "hz": 5, "mode": 4,
               "msg": 22, "call": 10, "grid": 6, "days": 5, "st": 3, "entity": 22, "cqz": 4,
               "ituz": 4, "ct": 3, "qrz": 4, "lotw": 4}

    def _size_columns(self) -> None:
        char = self.fontMetrics().averageCharWidth()
        for i, (key, label) in enumerate(self.decodes.cols):
            chars = self._WIDTHS.get(key, max(len(label), 6))
            self.setColumnWidth(i, (max(chars, len(label)) + 2) * char)

    def page(self, direction: int) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + direction * max(1, bar.pageStep()))

    def to_end(self) -> None:
        self.scrollToBottom()


class WantedView(QTableView):
    """The Wanted table.  A click selects; a double-click or Enter answers
    (*answer(call)*); i ignores (*ignore(call)*); a header click sorts
    (*sort_changed(name)*); right-click for the rest."""

    answer = Signal(str)
    ignore = Signal(str)
    sort_changed = Signal(str)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.wanted = WantedModel(engine, self)
        self.setModel(self.wanted)
        self.setWordWrap(False)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        rows = self.verticalHeader()
        rows.hide()
        rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        rows.setDefaultSectionSize(self.fontMetrics().height() + 6)
        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSortIndicatorShown(True)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._header_clicked)
        self.doubleClicked.connect(lambda index: self._answer_row(index.row()))
        self.customContextMenuRequested.connect(self._menu)
        self.wanted.modelReset.connect(self._columns_changed)

    @property
    def selected_call(self) -> str | None:
        rows = self.selectionModel().selectedRows()
        return self.wanted.rows[rows[0].row()].call if rows else None

    def select_call(self, call: str) -> None:
        row = self.wanted.row_of(call)
        if row is not None:
            self.selectRow(row)

    def move(self, step: int) -> None:
        """Select the row *step* away (↑↓ at CMD>); the first row if none is."""
        n = self.wanted.rowCount()
        if not n:
            return
        rows = self.selectionModel().selectedRows()
        row = min(max(rows[0].row() + step, 0), n - 1) if rows else 0
        self.selectRow(row)
        self.scrollTo(self.wanted.index(row, 0))

    def keyPressEvent(self, event) -> None:
        call = self.selected_call
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and call:
            self.answer.emit(call)
        elif event.key() == Qt.Key.Key_I and not event.modifiers() and call:
            self.ignore.emit(call)
        else:
            super().keyPressEvent(event)

    def _answer_row(self, row: int) -> None:
        if 0 <= row < len(self.wanted.rows):
            self.answer.emit(self.wanted.rows[row].call)

    # ── Sorting ───────────────────────────────────────────────────────────────

    def cycle_sort(self) -> str:
        """The next sort order (Ctrl-S)."""
        return self.sort_by(None)

    def sort_by(self, name: str | None, reverse: bool = False) -> str:
        source = self.wanted.source
        source.set_sort(name, reverse)
        self.wanted.refresh()
        self._show_sort()
        self.sort_changed.emit(source.sort_name + (", reversed" if reverse else ""))
        return source.sort_name

    def _header_clicked(self, section: int) -> None:
        """Sort by that column; the same column again reverses it."""
        name = SORT_BY_COLUMN.get(self.wanted.cols[section][0])
        if name is None:
            self._show_sort()          # other columns don't sort
            return
        source = self.wanted.source
        self.sort_by(name, reverse=not source.reverse if name == source.sort_name else False)

    def _show_sort(self) -> None:
        source = self.wanted.source
        columns = {name: key for key, name in SORT_BY_COLUMN.items()}
        keys = [k for k, _ in self.wanted.cols]
        key = columns.get(source.sort_name)
        order = (Qt.SortOrder.DescendingOrder if source.reverse
                 else Qt.SortOrder.AscendingOrder)
        self.horizontalHeader().setSortIndicator(keys.index(key) if key in keys else -1, order)

    # Widths from typical contents, in characters; columns can be dragged wider.
    _WIDTHS = {"idx": 3, "call": 10, "qso": 3, "entity": 22, "cqz": 4, "ituz": 4, "ct": 3,
               "st": 3, "grid": 6, "db": 4, "seen": 6, "count": 3, "days": 5, "qrz": 4,
               "lotw": 4}

    def _columns_changed(self) -> None:
        char = self.fontMetrics().averageCharWidth()
        for i, (key, label) in enumerate(self.wanted.cols):
            chars = self._WIDTHS.get(key, max(len(label), 6))
            self.setColumnWidth(i, (max(chars, len(label)) + 2) * char)
        self._show_sort()

    # ── Right-click menu ──────────────────────────────────────────────────────

    def _menu(self, pos) -> None:
        index = self.indexAt(pos)
        if not index.isValid():
            return
        self.selectRow(index.row())
        self.menu_for(self.wanted.rows[index.row()].call).popup(
            self.viewport().mapToGlobal(pos))

    def menu_for(self, call: str) -> QMenu:
        menu = QMenu(self)
        menu.addAction(f"Answer {call}", lambda: self.answer.emit(call))
        menu.addAction("Ignore for this session", lambda: self.ignore.emit(call))
        menu.addSeparator()
        menu.addAction("Copy call", lambda: QApplication.clipboard().setText(call))
        menu.addAction("Look up on QRZ.com", lambda: lookup(call))
        return menu


def lookup(call: str) -> None:
    """Open the station's QRZ.com page (its home call) in the browser."""
    QDesktopServices.openUrl(QUrl(QRZ_LOOKUP.format(call=quote(base_call(call), safe=""))))


class CommandEdit(QLineEdit):
    """The line you type at.  ↑↓ move through the Wanted table (*arrow(step)*):
    there's no command history to walk."""

    arrow = Signal(int)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Up:
            self.arrow.emit(-1)
        elif event.key() == Qt.Key.Key_Down:
            self.arrow.emit(1)
        else:
            super().keyPressEvent(event)


class CommandLine(QWidget):
    """CMD> and the line you type at; *submitted(text)* on Enter."""

    submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        prompt = QLabel("CMD>")
        prompt.setFont(fixed_font())
        self.edit = CommandEdit()
        self.edit.setFont(fixed_font())
        self.edit.setPlaceholderText("a command (list, ignore, sync…), an index or a callsign")
        layout.addWidget(prompt)
        layout.addWidget(self.edit)
        self.edit.returnPressed.connect(self._submit)
        self.setFocusProxy(self.edit)

    def _submit(self) -> None:
        text = self.edit.text()
        self.edit.clear()
        self.submitted.emit(text)
