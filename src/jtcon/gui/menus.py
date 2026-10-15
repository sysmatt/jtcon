"""The window's menus, its layout toolbar, and the Keys and About boxes.

Every menu item with a key works from anywhere in the window (Qt runs an
action's shortcut whether or not its menu is open)."""

import sys

from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import QDialog, QLabel, QScrollArea, QToolBar, QVBoxLayout

from jtcon import __version__
from jtcon.config import CONFIG_FILE, CONFIG_TEMPLATE, default_config_dir, write_private
from jtcon.core.wanted import rule_label

HOMEPAGE = "https://github.com/sysmatt/jtcon"

# Layout presets: (name, menu text, key, what it shows).
LAYOUT_ITEMS = [
    ("wanted",  "&Wanted",       "F2", "The Wanted table and the log"),
    ("decodes", "&Decodes",      "F3", "The decodes and the log"),
    ("side",    "&Side by side", "F4", "The Wanted table and the decodes side by side"),
    ("stacked", "S&tacked",      "F5", "The Wanted table above the decodes"),
]

KEYS_HELP = """\
<h3>Layouts</h3>
<table cellspacing="6">
<tr><td><b>F2 F3 F4 F5</b></td><td>Wanted, Decodes, Side by side, Stacked; or the buttons
 along the top</td></tr>
<tr><td><b>Ctrl-arrows</b></td><td>the next or previous layout</td></tr>
<tr><td>Drag a panel's title</td><td>beside, above or below another panel, or onto one for tabs</td></tr>
<tr><td>Double-click a panel's title</td><td>float it as its own window, and back</td></tr>
<tr><td>Right-click a table's headers</td><td>hide or show columns; drag a header to move it</td></tr>
</table>
<h3>Wanted table</h3>
<table cellspacing="6">
<tr><td><b>↑ ↓</b> at CMD&gt;</td><td>select a station</td></tr>
<tr><td><b>Enter</b> on an empty CMD&gt;</td><td>answer the selected station</td></tr>
<tr><td>Double-click, or <b>Enter</b> in the table</td><td>answer that station</td></tr>
<tr><td><b>i</b> in the table</td><td>ignore the selected station for this session</td></tr>
<tr><td>Right-click a station</td><td>answer, ignore, copy its call, look it up on QRZ.com</td></tr>
<tr><td><b>Ctrl-S</b>, or click a header</td><td>sort: last heard, SNR, call, entity (again: reversed)</td></tr>
<tr><td>QSO column</td><td>📣 calling CQ &nbsp; 🤝 answering a CQ &nbsp; 📶 sending reports
 &nbsp; 👋 signing off</td></tr>
</table>
<h3>Decodes</h3>
<table cellspacing="6">
<tr><td><b>PgUp PgDn</b></td><td>scroll back</td></tr>
<tr><td><b>Esc</b></td><td>back to the newest</td></tr>
</table>
<h3>Live controls</h3>
<p>For this session only; the config file isn't changed.  The Rules menu has them too.</p>
<table cellspacing="6">
<tr><td><b>F6</b></td><td>rules on and off</td></tr>
<tr><td><b>F7</b></td><td>scope: check CQs only, or all decodes</td></tr>
<tr><td><b>F8</b></td><td>show: CQs and wanted stations, or all decodes</td></tr>
</table>
<h3>Commands at CMD&gt;</h3>
<p>Any unique start works: l, s, un.</p>
<table cellspacing="6">
<tr><td><b>07</b>, <b>W1ABC</b></td><td>answer that station</td></tr>
<tr><td><b>list</b></td><td>the wanted stations</td></tr>
<tr><td><b>ignore</b> 07|CALL</td><td>ignore a station for this session; alone, list them</td></tr>
<tr><td><b>unignore</b> CALL</td><td>stop ignoring it</td></tr>
<tr><td><b>sync</b> [full]</td><td>fetch online logbook changes, retry waiting uploads</td></tr>
<tr><td><b>clear</b></td><td>clear the decodes and the log</td></tr>
<tr><td><b>quit</b>, <b>Ctrl-Q</b></td><td>quit</td></tr>
</table>"""


class Menus:
    """Builds the menu bar and the layout toolbar on *window* (a MainWindow)."""

    def __init__(self, window):
        self.window = window
        self.engine = window.engine
        self.config = window.config
        self.dialogs: dict = {}         # "keys", "about": kept while open
        bar = window.menuBar()
        self._file(bar.addMenu("&File"))
        self._view(bar.addMenu("&View"))
        self.rules_menu = bar.addMenu("&Rules")
        self._rules(self.rules_menu)
        self._logbook(bar.addMenu("&Logbook"))
        self._help(bar.addMenu("&Help"))
        self._toolbar()

    def _action(self, menu, text: str, slot, keys=(), tip: str = "",
                checkable: bool = False) -> QAction:
        action = QAction(text, self.window)
        action.setShortcuts([QKeySequence(k) for k in keys])
        if tip:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        action.setCheckable(checkable)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    # ── File ──────────────────────────────────────────────────────────────────

    def _file(self, menu) -> None:
        self._action(menu, "&Edit config file", self.edit_config,
                     tip="Open the config file in your editor; restart jtcon to use the changes")
        menu.addSeparator()
        self.quit = self._action(menu, "&Quit", self.window.close, keys=["Ctrl+Q"])

    def config_file(self):
        """The file to edit: the profile if one is in use, else the config file
        (written first, from the starter, if there isn't one)."""
        if self.config.sources:
            return self.config.sources[-1]
        path = default_config_dir() / CONFIG_FILE
        if not path.exists():
            write_private(path, CONFIG_TEMPLATE)
            self.window.log_view.add(f"[config] Wrote a starter config: {path}")
        return path

    def edit_config(self) -> None:
        path = self.config_file()
        if not open_in_editor(str(path)):
            self.window.log_view.add(f"[config] Couldn't open an editor; the file is {path}")
            return
        self.window.log_view.add(f"[config] Opened {path} in your editor.  jtcon reads it at"
                                 " startup: restart jtcon to use your changes.")

    # ── View ──────────────────────────────────────────────────────────────────

    def _view(self, menu) -> None:
        win = self.window
        self.layouts = QActionGroup(win)
        self.layout_actions = {}
        for name, text, key, tip in LAYOUT_ITEMS:
            action = self._action(menu, text, lambda _=False, n=name: win.set_layout(n),
                                  keys=[key], tip=f"{tip} ({key})", checkable=True)
            self.layouts.addAction(action)
            self.layout_actions[name] = action
        self._action(menu, "&Next layout", lambda: win.step_layout(1),
                     keys=["Ctrl+Right", "Ctrl+Down"])
        self._action(menu, "&Previous layout", lambda: win.step_layout(-1),
                     keys=["Ctrl+Left", "Ctrl+Up"])
        menu.addSeparator()
        for dock in win.docks.values():
            action = dock.toggleViewAction()
            action.setText(f"{dock.windowTitle()} panel")
            menu.addAction(action)
        menu.addSeparator()
        self.reset = self._action(menu, "&Reset window layout", win.reset_layout,
                                  tip="Panels, columns and size as the first time")
        menu.aboutToShow.connect(self.show_layout)

    def show_layout(self) -> None:
        """Tick the current layout (menu and toolbar)."""
        action = self.layout_actions.get(self.window.layout_name)
        if action:
            action.setChecked(True)

    def _toolbar(self) -> None:
        bar = QToolBar("Layouts", self.window)
        bar.setObjectName("layout-bar")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        bar.addWidget(QLabel(" Layout: "))
        for action in self.layout_actions.values():
            bar.addAction(action)
        self.window.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.toolbar = bar

    # ── Rules ─────────────────────────────────────────────────────────────────

    def _rules(self, menu) -> None:
        self.rule_actions = {}
        for spec in self.config.wanted:
            label = rule_label(spec)
            text = label if label == spec.rule.upper() else f"{label}  ({spec.rule})"
            action = self._action(menu, text, lambda on, n=spec.name: self.set_rule(n, on),
                                  checkable=True)
            self.rule_actions[spec.name] = action
        if not self.config.wanted:
            none = menu.addAction("No [[wanted]] rules in the config")
            none.setEnabled(False)
        menu.addSeparator()
        self.scope = self._action(menu, "Check &all decodes", self.toggle_scope, keys=["F7"],
                                  tip="Check every decode, not only CQs (F7)", checkable=True)
        self.show = self._action(menu, "&Show all decodes", self.toggle_show, keys=["F8"],
                                 tip="Show every decode, not only CQs and wanted stations (F8)",
                                 checkable=True)
        note = menu.addAction("For this session; the config file isn't changed")
        note.setEnabled(False)
        menu.aboutToShow.connect(self.show_rules)
        self.show_rules()

    def show_rules(self) -> None:
        """Tick what's on now."""
        cfg = self.config
        for name, action in self.rule_actions.items():
            action.setChecked(name not in cfg.rules_off)
        self.scope.setChecked(cfg.scope.decodes == "all")
        self.show.setChecked(cfg.display.show == "all")

    def set_rule(self, name: str, on: bool) -> None:
        self.engine.set_rule_enabled(name, on)
        self.window.rules_changed()
        self.show_rules()

    def toggle_scope(self) -> None:
        self.engine.set_scope("cq" if self.config.scope.decodes == "all" else "all")
        self.window.rules_changed()
        self.show_rules()

    def toggle_show(self) -> None:
        self.engine.set_show("cq" if self.config.display.show == "all" else "all")
        self.show_rules()

    def popup_rules(self) -> None:
        """F6: the Rules menu, in the middle of the window."""
        self.show_rules()
        win = self.window
        self.rules_menu.popup(win.mapToGlobal(win.rect().center()))

    # ── Logbook ───────────────────────────────────────────────────────────────

    def _logbook(self, menu) -> None:
        self.sync = self._action(menu, "&Sync", lambda: self.engine.sync(False),
                                 tip="Fetch what changed in the QRZ logbook; retry waiting uploads")
        self.sync_full = self._action(menu, "Sync, &full download", lambda: self.engine.sync(True),
                                      tip="Download the whole QRZ logbook again")
        menu.aboutToShow.connect(self.show_logbook)

    def show_logbook(self) -> None:
        on = self.engine.qrz is not None
        for action in (self.sync, self.sync_full):
            action.setEnabled(on)

    # ── Help ──────────────────────────────────────────────────────────────────

    def _help(self, menu) -> None:
        self.keys = self._action(menu, "&Keys and commands", self.show_keys, keys=["F1"])
        self._action(menu, "&About jtcon", self.show_about)

    def show_keys(self) -> None:
        self._box("keys", "jtcon: keys and commands", KEYS_HELP)

    def show_about(self) -> None:
        self._box("about", "About jtcon",
                  f"<h2>jtcon {__version__}</h2>"
                  "<p>Watches WSJT-X for the stations you want, and answers them.</p>"
                  f'<p><a href="{HOMEPAGE}">{HOMEPAGE}</a></p>'
                  "<p>© Matt Hoskins.  Free software under the GNU General Public License,"
                  " version 3 or later.</p>")

    def _box(self, name: str, title: str, html: str) -> None:
        """A box of text that stays open beside the window until closed."""
        box = self.dialogs.get(name)
        if box is None:
            box = QDialog(self.window)
            box.setWindowTitle(title)
            label = QLabel(html)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setOpenExternalLinks(True)
            label.setWordWrap(True)
            label.setMargin(8)
            scroll = QScrollArea()
            scroll.setWidget(label)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            layout = QVBoxLayout(box)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(scroll)
            # Wide enough for the rows; no taller than the screen.
            label.setMinimumWidth(640)
            screen = self.window.screen().availableGeometry()
            box.resize(680, min(label.sizeHint().height() + 20, screen.height() - 80))
            self.dialogs[name] = box
        box.show()
        box.raise_()
        box.activateWindow()


def open_in_editor(path: str) -> bool:
    """Open *path* in the desktop's editor for it; on Windows and macOS, where
    a .toml file may have none, Notepad or TextEdit."""
    if sys.platform == "win32":
        return QProcess.startDetached("notepad.exe", [path])[0]
    if sys.platform == "darwin":
        return QProcess.startDetached("open", ["-t", path])[0]
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))
