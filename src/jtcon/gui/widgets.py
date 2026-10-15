"""The window's parts: the Wanted table, the decode table, the log, the
command line."""

import re
from urllib.parse import quote

from PySide6.QtCore import QEvent, Qt, QUrl, Signal
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


class Table(QTableView):
    """A table with the columns' widths, order and visibility the user's to
    change, kept by column key so they survive columns coming and going (the
    status columns follow the rules).  Right-click the header to hide or show
    columns; drag a header to move it.  state() and restore() save them."""

    _WIDTHS: dict = {}              # typical widths, in characters
    SHORT: set = set()              # the "Short" column choice; empty: none

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(False)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        rows = self.verticalHeader()
        rows.hide()
        rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda pos: self.columns_menu().popup(header.mapToGlobal(pos)))
        header.sectionResized.connect(self._resized)
        header.sectionMoved.connect(self._moved)
        self.hidden: set = set()        # keys of hidden columns
        self.widths: dict = {}          # key -> width the user dragged it to
        self.order: list = []           # keys in the order the user moved them to
        self._applying = False
        self._dragging = False
        header.viewport().installEventFilter(self)

    def keys(self) -> list:
        return [key for key, _ in self.model().cols]

    def _row_height(self, extra: int) -> None:
        self._extra = extra
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + extra)

    def font_changed(self) -> None:
        """After setFont: rows and default column widths to suit."""
        self._row_height(self._extra)
        if self.model().cols:
            self.apply_columns()

    # ── Applying the user's choices ───────────────────────────────────────────

    def apply_columns(self) -> None:
        """Widths, visibility and order, for the columns there are now."""
        self._applying = True
        try:
            char = self.fontMetrics().averageCharWidth()
            labels = dict(self.model().cols)
            keys = self.keys()
            for i, key in enumerate(keys):
                chars = max(self._WIDTHS.get(key, max(len(labels[key]), 6)), len(labels[key]))
                self.setColumnWidth(i, self.widths.get(key, (chars + 2) * char))
                self.setColumnHidden(i, key in self.hidden)
            # The moved columns in the user's order, in the places they occupy;
            # others (new rule columns) stay where they are.
            moved = iter([k for k in self.order if k in keys])
            target = [next(moved) if k in self.order else k for k in keys]
            header = self.horizontalHeader()
            for visual, key in enumerate(target):
                header.moveSection(header.visualIndex(keys.index(key)), visual)
        finally:
            self._applying = False

    def eventFilter(self, obj, event) -> bool:
        """Note when the mouse is down on the header: only then is a column
        resized by the user rather than by Qt (the last column stretching, which
        isn't kept either)."""
        if event.type() == QEvent.Type.MouseButtonPress:
            self._dragging = True
        elif event.type() == QEvent.Type.MouseButtonRelease:
            self._dragging = False
        return False

    def _resized(self, logical: int, old: int, new: int) -> None:
        if self._dragging and not self._applying and new > 0 and logical != self._stretched():
            self.widths[self.keys()[logical]] = new

    def _stretched(self) -> int:
        """The last column showing, which stretches to fill the table."""
        header = self.horizontalHeader()
        shown = [i for i in range(header.count()) if not header.isSectionHidden(i)]
        return max(shown, key=header.visualIndex) if shown else -1

    def _moved(self, *_) -> None:
        if not self._applying:
            header, keys = self.horizontalHeader(), self.keys()
            self.order = [keys[header.logicalIndex(v)] for v in range(len(keys))]

    def set_hidden(self, hidden: set) -> None:
        keys = set(self.keys())
        if keys and keys <= set(hidden):
            return                      # always keep one column
        self.hidden = set(hidden)
        self.apply_columns()

    def columns_menu(self) -> QMenu:
        """A check item per column, plus All (and Short, where there is one)."""
        menu = QMenu(self)
        for key, label in self.model().cols:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key not in self.hidden)
            action.toggled.connect(lambda on, key=key: self.set_hidden(
                self.hidden - {key} if on else self.hidden | {key}))
        menu.addSeparator()
        menu.addAction("All columns", lambda: self.set_hidden(set()))
        if self.SHORT:
            menu.addAction("Short: time, dB, dt, Hz, message",
                           lambda: self.set_hidden(set(self.keys()) - self.SHORT))
        menu.addAction("Reset widths and order", self.reset_columns)
        return menu

    def reset_columns(self) -> None:
        self.widths, self.order = {}, []
        header = self.horizontalHeader()
        self._applying = True
        for logical in range(header.count()):
            header.moveSection(header.visualIndex(logical), logical)
        self._applying = False
        self.apply_columns()

    # ── Saving ────────────────────────────────────────────────────────────────

    def state(self) -> dict:
        return {"hidden": sorted(self.hidden), "widths": self.widths, "order": self.order}

    def restore(self, state: dict) -> None:
        self.hidden = set(state.get("hidden", []))
        self.widths = {k: int(v) for k, v in state.get("widths", {}).items()}
        self.order = list(state.get("order", []))
        if self.model().cols:
            self.apply_columns()


class DecodesView(Table):
    """The decode table: follows new decodes while scrolled to the bottom,
    stays put while scrolled back.

    Which columns show is kept per layout: the split layouts start with the
    short columns (the Wanted table has the facts), the others with all."""

    SHORT = {"idx", "local", "time", "db", "dt", "hz", "msg"}
    SPLIT = ("side", "stacked")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.decodes = DecodeModel(self)
        self.setModel(self.decodes)
        self.setFont(fixed_font())
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._row_height(4)
        self.layout_name = "decodes"
        self.hidden_by_layout: dict = {}    # layout -> hidden keys, once chosen
        self._follow = True
        self.decodes.rowsAboutToBeInserted.connect(self._before_insert)
        self.decodes.rowsInserted.connect(self._after_insert)
        self.decodes.modelReset.connect(self._columns_changed)

    def set_layout(self, name: str) -> None:
        """Show the columns chosen for layout *name*."""
        self.layout_name = name
        self._columns_changed()

    def _columns_changed(self) -> None:
        hidden = self.hidden_by_layout.get(self.layout_name)
        if hidden is None:              # not chosen yet: the layout's default
            hidden = set(self.keys()) - self.SHORT if self.layout_name in self.SPLIT else set()
        self.hidden = set(hidden)
        self.apply_columns()

    def set_hidden(self, hidden: set) -> None:
        super().set_hidden(hidden)
        self.hidden_by_layout[self.layout_name] = sorted(self.hidden)

    def state(self) -> dict:
        return {**super().state(), "hidden_by_layout": self.hidden_by_layout}

    def restore(self, state: dict) -> None:
        self.hidden_by_layout = dict(state.get("hidden_by_layout", {}))
        super().restore(state)
        self._columns_changed()

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

    def page(self, direction: int) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + direction * max(1, bar.pageStep()))

    def to_end(self) -> None:
        self.scrollToBottom()


class WantedView(Table):
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
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._row_height(6)
        header = self.horizontalHeader()
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
        self.apply_columns()
        self._show_sort()

    def state(self) -> dict:
        source = self.wanted.source
        return {**super().state(), "sort": source.sort_name, "reverse": source.reverse}

    def restore(self, state: dict) -> None:
        super().restore(state)
        if "sort" in state:
            self.wanted.source.set_sort(state["sort"], bool(state.get("reverse")))
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

    _LAYOUT_KEYS = (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)

    def event(self, event) -> bool:
        # Ctrl-arrows change the layout, as on the Textual screen, rather than
        # moving a word at a time: let the window's shortcuts have them.
        if (event.type() == QEvent.Type.ShortcutOverride
                and event.modifiers() == Qt.KeyboardModifier.ControlModifier
                and event.key() in self._LAYOUT_KEYS):
            event.ignore()
            return False
        return super().event(event)

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
