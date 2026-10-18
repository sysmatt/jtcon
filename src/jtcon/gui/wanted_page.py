"""Settings › Wanted rules: the [[wanted]] list.  Add, remove and reorder
rules; each rule's own settings beside the list.

Each entry remembers where it came from in the file (its place in
ConfigFile.wanted()), so saving changes it in place and its comments stay.
A setting is written only if the file had it or it's not the rule's default.

On a profile, one box says whether the profile has its own rules (they
replace the config file's whole list); unticked, the config file's rules
are shown, greyed."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMenu, QPlainTextEdit, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from jtcon.config import _REQUIRED, CONFIRM_SOURCES, DEFAULT_WANTED, RULES
from jtcon.core.wanted.base import LABELS

SOURCE_LABELS = {"lotw": "LoTW", "qrz": "QRZ", "eqsl": "eQSL", "card": "Card"}

LABEL = {"calls": "Callsigns"}

HELP = {
    "name": "Its name in the Wanted column and the Rules menu.  Needed when a rule is used"
            " twice (WAS by LoTW and WAS by QRZ, say).",
    "confirm": "The confirmations that count.  None ticked: worked is enough.",
    "calls": "One per line.  A callsign means that station (K1ABC: also K1ABC/P); anything"
             " else is a pattern searched in the call: ^VK0 for calls starting VK0.",
    "patterns": "One per line: patterns searched in the whole message, upper or lower case"
                " alike.  \\bQRP\\b: QRP as a word.",
    "files": "And/or files of them, one per line (# starts a comment), for lists kept"
             " elsewhere.",
    "states": "State codes, separated by spaces or commas: AK DE.",
    "file": "Or a file listing the states.",
}


def _default(rule: str, key: str):
    default = RULES[rule].settings.get(key)
    return None if default is _REQUIRED or default == "" else default


class Entry:
    """One rule in the list: where it came from in the file (None: new), and
    its settings as the file would have them."""

    def __init__(self, origin: int | None, values: dict):
        self.origin = origin
        self.values = dict(values)
        self.original = dict(values)

    @property
    def rule(self) -> str:
        return self.values["rule"]

    def text(self) -> str:
        name = self.values.get("name")
        label = name or LABELS.get(self.rule, self.rule.upper())
        return f"{label}  —  {RULES[self.rule].label}"


class WantedPage(QWidget):
    """The list on the left, the selected rule's settings on the right.
    *entries* are the file's [[wanted]] entries (None: it has none, so
    jtcon's defaults are shown).  On a profile, *base_name* is the config
    file's name and *base* its entries (None: the defaults)."""

    def __init__(self, entries: list | None, notes: list, parent=None,
                 base: list | None = None, base_name: str | None = None):
        super().__init__(parent)
        self.profile = base_name is not None
        self.from_file = entries is not None
        self.base = base if base is not None else [{"rule": r} for r in DEFAULT_WANTED]
        self.own_entries = entries          # the profile's own, to go back to
        if entries is None:
            entries = self.base if self.profile else [{"rule": r} for r in DEFAULT_WANTED]
        self.entries = [Entry(i if self.from_file else None, e) for i, e in enumerate(entries)]
        self.editors: dict = {}             # setting -> its widget, for the selected rule
        self.current: Entry | None = None
        self.here = None

        if self.profile:
            text = (f"Unticked, this profile uses {base_name}'s rules (shown greyed).  Ticked,"
                    f" it has its own, which replace {base_name}'s whole list; they start as"
                    " a copy of them.")
        else:
            text = ("Each rule makes a station wanted for its own reason; the Wanted column"
                    " shows which.  Award rules (DXCC, zones, WAS…) want what you haven't"
                    " confirmed; the others watch for activators or your lists."
                    + ("" if self.from_file else "  The config file has no rules, so jtcon"
                       " uses these; they're written to it only if you change them."))
        intro = QLabel(text)
        intro.setWordWrap(True)
        layout = QVBoxLayout(self)
        if self.profile:
            self.here = QCheckBox("This profile has its own rules")
            self.here.setChecked(self.from_file)
            layout.addWidget(self.here)
        layout.addWidget(intro)
        for note in notes:
            mark = QLabel(f"⚑ {note}")
            mark.setWordWrap(True)
            mark.setStyleSheet("color: #e5a50a;")
            layout.addWidget(mark)

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        add = QToolButton()
        add.setText("&Add rule")
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_menu = QMenu(add)
        for key, info in RULES.items():
            self.add_menu.addAction(f"{LABELS.get(key, key.upper())}  —  {info.label}",
                                    lambda k=key: self.add(k))
        add.setMenu(self.add_menu)
        self.remove_button = QPushButton("&Remove")
        self.remove_button.clicked.connect(self.remove)
        self.up_button = QPushButton("Move &up")
        self.up_button.clicked.connect(lambda: self.move(-1))
        self.down_button = QPushButton("Move &down")
        self.down_button.clicked.connect(lambda: self.move(1))
        buttons = QHBoxLayout()
        for button in (add, self.remove_button, self.up_button, self.down_button):
            buttons.addWidget(button)
        buttons.addStretch()

        self.editor = QWidget()
        self.form = QFormLayout(self.editor)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        body = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(self.list, 1)
        left.addLayout(buttons)
        body.addLayout(left, 1)
        body.addWidget(self.editor, 1)
        self.body = QWidget()
        self.body.setLayout(body)
        body.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.body, 1)
        self._fill()
        self.list.setCurrentRow(0)
        self.initial = (self.own, self.result())
        if self.here is not None:
            self.body.setEnabled(self.here.isChecked())
            self.here.toggled.connect(self._set_own)

    @property
    def own(self) -> bool:
        """The file has its own rules (always, for the config file)."""
        return self.here is None or self.here.isChecked()

    def _set_own(self, on: bool) -> None:
        """Ticked: the profile's own rules (as they were, or a copy of the
        config file's); unticked: the config file's, greyed."""
        self.current = None
        if on and self.own_entries is not None:
            self.entries = [Entry(i, e) for i, e in enumerate(self.own_entries)]
        else:
            self.entries = [Entry(None, e) for e in self.base]
        self.body.setEnabled(on)
        self._refresh(0)

    # ── The list ──────────────────────────────────────────────────────────────

    def _fill(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for entry in self.entries:
            self.list.addItem(entry.text())
        self.list.blockSignals(False)

    def _refresh(self, row: int) -> None:
        self._fill()
        self.current = None
        self.list.setCurrentRow(min(row, len(self.entries) - 1))
        if not self.entries:
            self._select(-1)

    def add(self, rule: str) -> None:
        self.commit()
        self.entries.append(Entry(None, {"rule": rule}))
        self._refresh(len(self.entries) - 1)

    def remove(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.entries):
            self.current = None
            del self.entries[row]
            self._refresh(row)

    def move(self, step: int) -> None:
        self.commit()
        row = self.list.currentRow()
        to = row + step
        if 0 <= row < len(self.entries) and 0 <= to < len(self.entries):
            self.entries[row], self.entries[to] = self.entries[to], self.entries[row]
            self._refresh(to)

    def _buttons(self) -> None:
        row, n = self.list.currentRow(), len(self.entries)
        self.remove_button.setEnabled(0 <= row < n)
        self.up_button.setEnabled(0 < row < n)
        self.down_button.setEnabled(0 <= row < n - 1)

    # ── The selected rule's settings ──────────────────────────────────────────

    def _select(self, row: int) -> None:
        self.commit()
        while self.form.rowCount():
            self.form.removeRow(0)
        self.editors = {}
        self.current = self.entries[row] if 0 <= row < len(self.entries) else None
        self._buttons()
        if self.current is None:
            return
        entry, info = self.current, RULES[self.current.rule]
        title = QLabel(f"<b>{LABELS.get(entry.rule, entry.rule.upper())}</b>: {info.label}"
                       f"  (rule = \"{entry.rule}\")")
        title.setWordWrap(True)
        self.form.addRow(title)
        name = QLineEdit(entry.values.get("name", ""))
        name.setPlaceholderText(entry.rule)
        self._row("Name", "name", name)
        name.textChanged.connect(self.commit)      # the list shows the name as it's typed
        for key in info.settings:
            value = entry.values.get(key, _default(entry.rule, key))
            if key == "confirm":
                widget = QWidget()
                row = QHBoxLayout(widget)
                row.setContentsMargins(0, 0, 0, 0)
                boxes = {}
                for source in CONFIRM_SOURCES:
                    box = boxes[source] = QCheckBox(SOURCE_LABELS[source])
                    box.setChecked(source in (value or []))
                    row.addWidget(box)
                row.addStretch()
                widget.boxes = boxes
            elif key in ("calls", "patterns"):
                widget = QPlainTextEdit("\n".join(value or []))
                widget.setFixedHeight(widget.fontMetrics().height() * 8 + 12)
            elif key == "files":
                widget = _PathList("\n".join(value or []))
            elif key == "states":
                widget = QLineEdit(" ".join(value or []))
            else:                           # "file"
                widget = _PathEdit(value or "")
            self._row(LABEL.get(key, key.capitalize()), key, widget)

    def _row(self, label: str, key: str, widget: QWidget) -> None:
        self.editors[key] = widget
        cell = QWidget()
        column = QVBoxLayout(cell)
        column.setContentsMargins(0, 0, 0, 6)
        column.addWidget(widget)
        help_label = QLabel(HELP[key])
        help_label.setWordWrap(True)
        help_label.setEnabled(False)
        column.addWidget(help_label)
        self.form.addRow(label, cell)

    def commit(self) -> None:
        """The editors' values into the selected entry."""
        entry = self.current
        if entry is None or not self.editors:
            return
        values = {"rule": entry.rule}
        name = self.editors["name"].text().strip()
        if name:
            values["name"] = name
        for key in RULES[entry.rule].settings:
            widget = self.editors[key]
            if key == "confirm":
                value = [s for s, box in widget.boxes.items() if box.isChecked()]
            elif key in ("calls", "patterns"):
                value = [line.strip() for line in widget.toPlainText().splitlines()
                         if line.strip()]
            elif key == "files":
                value = widget.paths()
            elif key == "states":
                value = [s.upper() for s in widget.text().replace(",", " ").split()]
            else:
                value = widget.edit.text().strip()
            # Written if the file had it, or if it isn't the rule's default.
            # An empty confirm list means something (worked is enough); an
            # empty file or list doesn't.
            empty = value in ([], "") and key != "confirm"
            if key in entry.original or (value != _default(entry.rule, key) and not empty):
                values[key] = value
        entry.values = values
        row = self.entries.index(entry)
        if self.list.item(row) is not None:
            self.list.item(row).setText(entry.text())

    # ── Saving ────────────────────────────────────────────────────────────────

    def result(self) -> list:
        """[(origin, entry)] as ConfigFile.set_wanted takes them."""
        self.commit()
        return [(e.origin, dict(e.values)) for e in self.entries]

    @property
    def changed(self) -> bool:
        return (self.own, self.result()) != self.initial


class _PathEdit(QWidget):
    def __init__(self, text: str):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(text)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.edit)
        row.addWidget(browse)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "File", self.edit.text())
        if path:
            self.edit.setText(path)


class _PathList(QWidget):
    def __init__(self, text: str):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.edit = QPlainTextEdit(text)
        self.edit.setFixedHeight(self.edit.fontMetrics().height() * 4 + 12)
        add = QPushButton("Add file…")
        add.clicked.connect(self._add)
        row.addWidget(self.edit)
        row.addWidget(add, alignment=Qt.AlignmentFlag.AlignTop)

    def paths(self) -> list:
        return [line.strip() for line in self.edit.toPlainText().splitlines() if line.strip()]

    def _add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Files")
        if paths:
            self.edit.setPlainText("\n".join(self.paths() + paths))
