"""The window's parts: the decode table, the log, the command line."""

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPlainTextEdit, QTableView, QWidget)

from jtcon.gui.models import COLOURS, DecodeModel

LOG_LINES = 500         # log lines kept

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
        char = self.fontMetrics().horizontalAdvance("M")
        for i, (key, label) in enumerate(self.decodes.cols):
            chars = self._WIDTHS.get(key, max(len(label), 6))
            self.setColumnWidth(i, (max(chars, len(label)) + 2) * char)

    def page(self, direction: int) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + direction * max(1, bar.pageStep()))

    def to_end(self) -> None:
        self.scrollToBottom()


class CommandLine(QWidget):
    """CMD> and the line you type at; *submitted(text)* on Enter."""

    submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        prompt = QLabel("CMD>")
        prompt.setFont(fixed_font())
        self.edit = QLineEdit()
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
