"""Station › Ignored stations: the calls jtcon skips, with Unignore and Add."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from jtcon.core.messages import _CALLSIGN_RE

SOURCES = {"config": "the config file", "session": "this session"}


class IgnoredDialog(QDialog):
    """The ignored calls and where each came from.  Changes are for this
    session; [filters] ignore in the config file lists calls for good."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.engine = window.engine
        self.setWindowTitle("Ignored stations")
        note = QLabel("jtcon skips these calls (and their portable forms): not checked,"
                      " not highlighted, not in the tables.  Changes here are for this"
                      " session; <b>[filters] ignore</b> in the config file ignores a call"
                      " for good.")
        note.setWordWrap(True)
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["Call", "Ignored by"])
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._selection)
        self.unignore_button = QPushButton("&Unignore")
        self.unignore_button.clicked.connect(self.unignore)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Callsign")
        self.entry.returnPressed.connect(self.add)
        add = QPushButton("&Add")
        add.clicked.connect(self.add)
        add.setAutoDefault(False)
        self.unignore_button.setAutoDefault(False)
        self.message = QLabel()
        row = QHBoxLayout()
        row.addWidget(self.entry)
        row.addWidget(add)
        row.addStretch()
        row.addWidget(self.unignore_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(self.list)
        layout.addLayout(row)
        layout.addWidget(self.message)
        layout.addWidget(buttons)
        self.resize(460, 380)
        self.refresh()

    def refresh(self) -> None:
        """The list as the engine has it now."""
        self.list.clear()
        for call, source in sorted(self.engine.ignored.items()):
            item = QTreeWidgetItem([call, SOURCES.get(source, source)])
            item.setData(0, Qt.ItemDataRole.UserRole, call)
            self.list.addTopLevelItem(item)
        self.list.resizeColumnToContents(0)
        self._selection()

    def calls(self) -> list:
        return [self.list.topLevelItem(i).text(0) for i in range(self.list.topLevelItemCount())]

    def _selection(self) -> None:
        self.unignore_button.setEnabled(bool(self.list.selectedItems()))

    def unignore(self) -> None:
        for item in self.list.selectedItems():
            self.engine.unignore(item.data(0, Qt.ItemDataRole.UserRole))
        self.message.setText("")
        self.window.refresh_wanted(force=True)
        self.refresh()

    def add(self) -> None:
        call = self.entry.text().strip().upper()
        if not call:
            return
        if not _CALLSIGN_RE.match(call):
            self.message.setText(f"{call} isn't a callsign.")
            return
        self.message.setText("")
        self.entry.clear()
        self.window.ignore(call)        # refreshes this list too
