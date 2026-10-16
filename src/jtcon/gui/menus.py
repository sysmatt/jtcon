"""The window's menus, and the Keys, About and Ignored stations boxes.

Every menu item with a key works from anywhere in the window (Qt runs an
action's shortcut whether or not its menu is open)."""

import sys

from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import QDialog, QLabel, QScrollArea, QVBoxLayout

from jtcon import __version__
from jtcon.config import CONFIG_FILE, CONFIG_TEMPLATE, default_config_dir, write_private
from jtcon.core.wanted import rule_label
from jtcon.gui.ignored import IgnoredDialog
from jtcon.gui.theme import THEMES
from jtcon.gui.widgets import station_items

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
<tr><td><b>F2 F3 F4 F5</b></td><td>Wanted, Decodes, Side by side, Stacked (View › Layout)</td></tr>
<tr><td><b>Ctrl-arrows</b></td><td>the next or previous layout</td></tr>
<tr><td>View › Lock panels</td><td>off: the panels get title bars to drag them by</td></tr>
<tr><td>Drag a panel's title</td><td>beside, above or below another panel, or onto one for tabs</td></tr>
<tr><td>Double-click a panel's title</td><td>float it as its own window, and back</td></tr>
<tr><td>Right-click a table's headers</td><td>hide or show columns; drag a header to move it</td></tr>
</table>
<h3>Stations</h3>
<p>In the Wanted and Decodes panels; the Station menu does the same for the selected row.</p>
<table cellspacing="6">
<tr><td>Double-click, or <b>Enter</b></td><td>answer the station (any station calling CQ)</td></tr>
<tr><td><b>Delete</b> or <b>i</b></td><td>ignore it for this session</td></tr>
<tr><td>Right-click a station</td><td>answer, ignore, copy its call, look it up on QRZ.com</td></tr>
<tr><td>Station › Ignored stations</td><td>see what's ignored, unignore, add a call</td></tr>
</table>
<h3>Wanted table</h3>
<table cellspacing="6">
<tr><td><b>Ctrl-S</b>, or click a header</td><td>sort: last heard, SNR, call, entity (again: reversed)</td></tr>
<tr><td>QSO column</td><td>📣 calling CQ &nbsp; 🤝 answering a CQ &nbsp; 📶 sending reports
 &nbsp; 👋 signing off</td></tr>
</table>
<h3>Decodes</h3>
<table cellspacing="6">
<tr><td><b>PgUp PgDn</b></td><td>scroll back</td></tr>
<tr><td><b>Esc</b></td><td>back to the newest</td></tr>
<tr><td>Right-click</td><td>clear the decodes (and in the log: clear the log)</td></tr>
</table>
<h3>Live controls</h3>
<p>For this session only; the config file isn't changed.  The Rules menu has them too.</p>
<table cellspacing="6">
<tr><td><b>F6</b></td><td>rules on and off</td></tr>
<tr><td><b>F7</b></td><td>scope: check CQs only, or all decodes</td></tr>
<tr><td><b>F8</b></td><td>show: CQs and wanted stations, or all decodes</td></tr>
</table>
<h3>Other keys</h3>
<table cellspacing="6">
<tr><td><b>Ctrl-+ Ctrl-- Ctrl-0</b></td><td>text size (View › Text size)</td></tr>
<tr><td><b>Ctrl-,</b></td><td>settings (File › Settings)</td></tr>
<tr><td><b>Ctrl-R</b></td><td>reload the config (File › Reload config)</td></tr>
<tr><td><b>Ctrl-Q</b></td><td>quit</td></tr>
</table>"""


class Menus:
    """Builds the menu bar on *window* (a MainWindow)."""

    def __init__(self, window):
        self.window = window
        self.engine = window.engine
        self.config = window.config
        self.dialogs: dict = {}         # "keys", "about", "ignored": kept while open
        bar = window.menuBar()
        self._file(bar.addMenu("&File"))
        self._view(bar.addMenu("&View"))
        self.station_menu = bar.addMenu("&Station")
        self.station_menu.aboutToShow.connect(self.fill_station)
        self.fill_station()
        self.rules_menu = bar.addMenu("&Rules")
        self._rules(self.rules_menu)
        self._logbook(bar.addMenu("&Logbook"))
        self._help(bar.addMenu("&Help"))

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
        self.settings = self._action(menu, "&Settings…", self.show_settings, keys=["Ctrl+,"],
                                     tip="The config file's settings; Save reloads them")
        self.settings.setEnabled(self.window.startup is not None)
        self._action(menu, "&Edit config file", self.edit_config,
                     tip="Open the config file in your editor; File › Reload config uses"
                         " your changes")
        self.reload = self._action(menu, "&Reload config", self.window.reload_config,
                                   keys=["Ctrl+R"],
                                   tip="Read the config again and restart with it, in this"
                                       " window (the command line's options still win)")
        self.reload.setEnabled(self.window.reload is not None)
        menu.addSeparator()
        self.quit = self._action(menu, "&Quit", self.window.close, keys=["Ctrl+Q"])

    def show_settings(self) -> None:
        """A new Settings box each time: the file as it is now."""
        from jtcon.gui.settings import SettingsDialog
        old = self.dialogs.pop("settings", None)
        if old is not None:
            old.close()
        box = self.dialogs["settings"] = SettingsDialog(self.window)
        box.open()

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
        self.window.log_view.add(f"[config] Opened {path} in your editor.  Save it, then"
                                 " File › Reload config (Ctrl-R) to use your changes.")

    # ── View ──────────────────────────────────────────────────────────────────

    def _view(self, menu) -> None:
        win = self.window
        layouts = menu.addMenu("&Layout")
        self.layouts = QActionGroup(win)
        self.layout_actions = {}
        for name, text, key, tip in LAYOUT_ITEMS:
            action = self._action(layouts, text, lambda _=False, n=name: win.set_layout(n),
                                  keys=[key], tip=f"{tip} ({key})", checkable=True)
            self.layouts.addAction(action)
            self.layout_actions[name] = action
        layouts.addSeparator()
        self._action(layouts, "&Next layout", lambda: win.step_layout(1),
                     keys=["Ctrl+Right", "Ctrl+Down"])
        self._action(layouts, "&Previous layout", lambda: win.step_layout(-1),
                     keys=["Ctrl+Left", "Ctrl+Up"])
        layouts.aboutToShow.connect(self.show_layout)
        menu.addSeparator()
        for dock in win.docks.values():
            action = dock.toggleViewAction()
            action.setText(f"{dock.windowTitle()} panel")
            menu.addAction(action)
        self.lock = self._action(menu, "Lock pa&nels", lambda on: win.set_locked(on),
                                 tip="Hide the panels' title bars; off to drag panels around",
                                 checkable=True)
        menu.addSeparator()
        themes = menu.addMenu("&Theme")
        self.themes = QActionGroup(win)
        self.theme_actions = {}
        for name, text in THEMES.items():
            action = self._action(themes, text, lambda _=False, n=name: win.set_theme(n),
                                  checkable=True)
            self.themes.addAction(action)
            self.theme_actions[name] = action
        themes.addSeparator()
        note = themes.addAction("For this session; [display] theme sets it for good")
        note.setEnabled(False)
        themes.aboutToShow.connect(
            lambda: self.theme_actions[win.theme].setChecked(True))
        sizes = menu.addMenu("Te&xt size")
        self._action(sizes, "&Bigger", lambda: win.set_text_size(win.text_size + 1),
                     keys=["Ctrl++", "Ctrl+="])
        self._action(sizes, "&Smaller", lambda: win.set_text_size(win.text_size - 1),
                     keys=["Ctrl+-"])
        self._action(sizes, "&Normal", lambda: win.set_text_size(0), keys=["Ctrl+0"])
        menu.addSeparator()
        self._action(menu, "&Clear decodes", win.decodes.decodes.clear)
        self._action(menu, "C&lear log", win.log_view.reset)
        menu.addSeparator()
        self.reset = self._action(menu, "&Reset window layout", win.reset_layout,
                                  tip="Panels, columns and size as the first time")

    def show_layout(self) -> None:
        """Tick the current layout."""
        action = self.layout_actions.get(self.window.layout_name)
        if action:
            action.setChecked(True)

    # ── Station ───────────────────────────────────────────────────────────────

    def fill_station(self) -> None:
        """The selected station's items, as on its right-click menu."""
        win, menu = self.window, self.station_menu
        menu.clear()
        call = win.current_call()
        if call:
            station_items(menu, call, win.answer, win.ignore, win.can_answer(call))
        else:
            none = menu.addAction("Select a station in the Wanted or Decodes panel")
            none.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Ignored &stations…", self.show_ignored)

    def show_ignored(self) -> None:
        box = self.dialogs.get("ignored")
        if box is None:
            box = self.dialogs["ignored"] = IgnoredDialog(self.window)
        box.refresh()
        box.show()
        box.raise_()
        box.activateWindow()

    def ignored_changed(self) -> None:
        box = self.dialogs.get("ignored")
        if box is not None:
            box.refresh()

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
        self.keys = self._action(menu, "&Keys", self.show_keys, keys=["F1"])
        self._action(menu, "&About jtcon", self.show_about)

    def show_keys(self) -> None:
        self._box("keys", "jtcon: keys", KEYS_HELP)

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
