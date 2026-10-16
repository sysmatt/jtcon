"""The main window: the Wanted table, the decode table and the log as panels
you arrange (four preset layouts), and the status bar.  No command line: the
menus, the tables and their right-click menus do what CMD> does on the other
screens.  Engine events arrive on the Qt thread (one event loop, qasync).

The window remembers its size, its panels and the tables' columns between runs
(QSettings, not config.toml: they're the window's settings, not jtcon's)."""

import asyncio
import json
import time

from PySide6.QtCore import QEvent, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractButton, QApplication, QDockWidget, QLabel, QMainWindow,
                               QMessageBox, QToolTip, QWidget)

from jtcon.config import default_config_dir
from jtcon.core.bands import _freq_to_band
from jtcon.core.events import Fatal, LogLine, Ready, SpotEvent, StationUpdated, UdpDown
from jtcon.gui.menus import Menus
from jtcon.gui.models import DecodeRow, decode_columns
from jtcon.gui.theme import app_icon, apply_theme
from jtcon.gui.widgets import DecodesView, LogView, WantedView, fixed_font
from jtcon.ui.status import session_changes, status_badge, status_text

TICK_MS = 250           # status bar and column refresh
TEXT_SIZES = range(-4, 13)  # points smaller or bigger than normal (View › Text size)

# Layouts, in the order Ctrl-arrows step through them, with their names.
LAYOUTS = {"wanted": "Wanted", "decodes": "Decodes", "side": "Side by side",
           "stacked": "Stacked"}
AUTO_SIDE_PX = 2400     # auto layout: side by side on a screen this wide, else stacked
STATE_VERSION = 1       # of the saved panel arrangement


def auto_layout(screen_width: int) -> str:
    return "side" if screen_width >= AUTO_SIDE_PX else "stacked"


def window_settings() -> QSettings:
    """Where the window keeps itself: window.ini beside config.toml."""
    return QSettings(str(default_config_dir() / "window.ini"), QSettings.Format.IniFormat)

# Colours only: a font in a style sheet would override the text size, and the
# labels without a style sheet follow the theme's palette.
_BADGE_STYLE = {
    "RX": "background: #2e7d32; color: white; padding: 1px 8px;",
    "TX": "background: #c62828; color: white; padding: 1px 8px;",
}
_BADGE_STYLE["UDP DOWN"] = _BADGE_STYLE["TX"]
_NOTE_STYLE = "padding: 1px 6px; color: black; background: %s;"


class MainWindow(QMainWindow):
    """The window.  *closing* is emitted once, when it closes for any reason."""

    closing = Signal()

    def __init__(self, engine, start_engine: bool = True, dialogs: bool = True,
                 settings: QSettings | None = None, reload=None, log: list | None = None,
                 startup=None):
        """*reload()* restarts with the config read again (File › Reload
        config), returning why not if it can't; *log* is the log so far, from
        the window before a reload; *startup* how jtcon was started (the
        settings editor's file and command line)."""
        super().__init__()
        self.engine = engine
        self.reload = reload
        self.startup = startup
        self.settings = settings if settings is not None else window_settings()
        self.config = engine.config
        self.start_engine = start_engine    # False in tests: the engine is set up
        self.dialogs = dialogs              # False in tests: no message boxes
        self.fatal_msg: str | None = None
        self.engine_task: asyncio.Future | None = None
        self._closed = False
        self._wanted_dirty = True
        self._wanted_shown = 0.0        # monotonic time of the last refresh
        # As configured, to show on the status bar when changed for the session.
        self.configured = (self.config.scope.decodes, self.config.display.show)

        self.setWindowTitle("jtcon")
        self.setWindowIcon(app_icon())
        self.theme = ""
        self.set_theme(self.config.display.theme)
        self.wanted = WantedView(engine)
        self.decodes = DecodesView()
        self.log_view = LogView()
        self.layout_name = ""
        self.locked = False
        self.last_table = self.wanted   # the Station menu acts on its selection
        self._panels()
        self._status_bar()
        self.text_size = 0
        self._fonts = {w: QFont(w.font()) for w in self._text_widgets()}

        for view in (self.wanted, self.decodes):
            view.answer.connect(self.answer)
            view.ignore.connect(self.ignore)
            view.can_answer = self.can_answer
            view.focused.connect(lambda v=view: setattr(self, "last_table", v))
        self.wanted.sort_changed.connect(lambda name: self.log_view.add(f"[wanted] Sorted by {name}"))
        self.menus = Menus(self)        # with the keys F1-F8, Ctrl-arrows, Ctrl-Q
        for keys, slot in (("Ctrl+S", self.wanted.cycle_sort),
                           ("PgUp", lambda: self.decodes.page(-1)),
                           ("PgDown", lambda: self.decodes.page(1)),
                           ("Esc", self.decodes.to_end),
                           ("F6", self.menus.popup_rules)):
            QShortcut(QKeySequence(keys), self, activated=slot)
        self._tick()                    # the columns, before restoring their state
        self._restore()
        (self.wanted if self.docks["wanted"].isVisibleTo(self) else self.decodes).setFocus()
        if log is None:
            self.log_view.add("[keys] F1 help · F2–F5 layouts (View › Layout) · F6 rules ·"
                              " F7 scope · F8 show · double-click or Enter answers a station,"
                              " Delete ignores it · right-click for more · View › Lock panels"
                              " off to move panels")
        else:
            for line in log:
                self.log_view.add(line)
            self.log_view.add("[config] Reloaded: the decodes start afresh; the stations"
                              " ignored and the rules switched off this session still are.")

        self._timer = QTimer(self, interval=TICK_MS, timeout=self._tick)
        self._timer.start()
        self._tick()
        engine.subscribe(self._on_event)

    # ── Panels and layouts ────────────────────────────────────────────────────

    def _panels(self) -> None:
        """Wanted, Decodes and Log as dock panels.  There's no central widget:
        the panels fill the window."""
        self.docks = {}
        for name, title, widget in (("wanted", "Wanted", self.wanted),
                                    ("decodes", "Decodes", self.decodes),
                                    ("log", "Log", self.log_view)):
            dock = QDockWidget(title, self)
            dock.setObjectName(f"{name}-panel")
            dock.setWidget(widget)
            self._title_tips(dock)
            dock.topLevelChanged.connect(lambda _floating, d=dock: self._lock(d))
            self.docks[name] = dock
        self.setDockNestingEnabled(True)
        # The log runs the full width along the bottom.
        for corner in (Qt.Corner.BottomLeftCorner, Qt.Corner.BottomRightCorner):
            self.setCorner(corner, Qt.DockWidgetArea.BottomDockWidgetArea)

    def set_locked(self, on: bool) -> None:
        """Lock the panels (View › Lock panels): no title bars, so they can't
        be dragged or floated by accident and take no room; the dividers
        between them still resize."""
        self.locked = on
        self.menus.lock.setChecked(on)
        for dock in self.docks.values():
            self._lock(dock)

    _UNLOCKED = (QDockWidget.DockWidgetFeature.DockWidgetClosable
                 | QDockWidget.DockWidgetFeature.DockWidgetMovable
                 | QDockWidget.DockWidgetFeature.DockWidgetFloatable)

    def _lock(self, dock) -> None:
        # A floating panel keeps its title bar, to be dragged back.
        if self.locked and not dock.isFloating():
            if dock.titleBarWidget() is None:
                dock.setTitleBarWidget(QWidget(dock))
            # Closable all the same: the View menu's panel items need it.
            dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        else:
            if dock.titleBarWidget() is not None:
                dock.setTitleBarWidget(None)
            dock.setFeatures(self._UNLOCKED)

    _TITLE_TIP = ("Drag this title to move the panel: beside, above or below another,"
                  " or onto one for tabs.  Double-click it to float the panel as its own"
                  " window, and again to put it back.  F2–F5 (View › Layout) lay the"
                  " panels out for you; View › Lock panels hides these titles.")
    _BUTTON_TIPS = {
        "qt_dockwidget_floatbutton": "Float this panel as its own window, or put it back",
        "qt_dockwidget_closebutton": "Close this panel (the View menu or F2–F5 bring it"
                                     " back)",
    }

    def _title_tips(self, dock) -> None:
        """Tooltips on a panel's title bar and its buttons, which Qt draws
        itself: they're how the panels move, and easy to miss."""
        for button in dock.findChildren(QAbstractButton):
            tip = self._BUTTON_TIPS.get(button.objectName())
            if tip:
                button.setToolTip(tip)
        dock.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        # A tooltip over a panel's title bar (above its content); elsewhere in
        # the panel, the content's own tooltips.
        if (event.type() == QEvent.Type.ToolTip and isinstance(obj, QDockWidget)
                and obj.widget() is not None and event.pos().y() < obj.widget().y()):
            QToolTip.showText(event.globalPos(), self._TITLE_TIP, obj)
            return True
        return super().eventFilter(obj, event)

    def set_layout(self, name: str) -> None:
        """Arrange the panels as preset *name* (F2-F5): the Wanted table, the
        decodes, both side by side, or stacked; the log along the bottom."""
        docks = self.docks
        wanted, decodes, log = docks["wanted"], docks["decodes"], docks["log"]
        # Out and back in: Qt keeps placeholders where panels were, which would
        # leave gaps in the new arrangement.
        for dock in docks.values():
            dock.setFloating(False)
            self.removeDockWidget(dock)
        top = Qt.DockWidgetArea.TopDockWidgetArea
        self.addDockWidget(top, wanted)
        if name in ("side", "stacked"):
            self.splitDockWidget(wanted, decodes, Qt.Orientation.Horizontal if name == "side"
                                 else Qt.Orientation.Vertical)
        else:
            self.addDockWidget(top, decodes)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, log)
        wanted.setVisible(name != "decodes")
        decodes.setVisible(name != "wanted")
        log.setVisible(True)
        width, height = max(self.width(), 800), max(self.height(), 500)
        if name == "side":
            self.resizeDocks([wanted, decodes], [width * 11 // 20, width * 9 // 20],
                             Qt.Orientation.Horizontal)
        elif name == "stacked":
            self.resizeDocks([wanted, decodes], [height * 3 // 7, height * 2 // 7],
                             Qt.Orientation.Vertical)
        self.resizeDocks([log], [max(110, height // 6)], Qt.Orientation.Vertical)
        self.layout_name = name
        self.decodes.set_layout(name)
        self.menus.show_layout()
        if name == "decodes" and self.wanted.hasFocus():
            self.decodes.setFocus()
        elif name == "wanted" and self.decodes.hasFocus():
            self.wanted.setFocus()

    def step_layout(self, step: int) -> None:
        names = list(LAYOUTS)
        current = names.index(self.layout_name) if self.layout_name in names else 0
        self.set_layout(names[(current + step) % len(names)])

    # ── Remembering the window ────────────────────────────────────────────────

    def _restore(self) -> None:
        """The window as it was last time; the first time, the configured layout."""
        s = self.settings
        try:
            size = int(s.value("text_size", 0))
        except (TypeError, ValueError):
            size = 0
        if size:
            self.set_text_size(size)
        self.set_locked(str(s.value("locked", "true")).lower() != "false")
        for view, key in ((self.wanted, "wanted_columns"), (self.decodes, "decode_columns")):
            try:
                view.restore(json.loads(s.value(key, "{}")))
            except (TypeError, ValueError):
                pass                    # unreadable: the defaults
        geometry, state = s.value("geometry"), s.value("state")
        if geometry is None or not self.restoreGeometry(geometry):
            self.resize(1400, 820)
        name = s.value("layout", "")
        if state is not None and name in LAYOUTS and self.restoreState(state, STATE_VERSION):
            self.layout_name = name
            self.decodes.set_layout(name)
            self.menus.show_layout()
        else:
            self.set_layout(self.first_layout())

    # ── Theme and text size ───────────────────────────────────────────────────

    def set_theme(self, name: str) -> None:
        """Dark, light or system, for the whole application; the config file's
        theme at startup, View › Theme for the session."""
        apply_theme(QApplication.instance(), name)
        self.theme = name

    def _text_widgets(self) -> list:
        return [self.wanted, self.decodes, self.log_view, self.status,
                self.badge, self.freq, self.scroll_note, self.changes]

    def set_text_size(self, size: int) -> None:
        """Text *size* points bigger (or smaller) than normal, in the panels
        and the status bar."""
        self.text_size = min(max(size, TEXT_SIZES.start), TEXT_SIZES.stop - 1)
        for widget, base in self._fonts.items():
            font = QFont(base)
            if base.pointSizeF() > 0:
                font.setPointSizeF(max(5.0, base.pointSizeF() + self.text_size))
            else:                       # a font sized in pixels
                font.setPixelSize(max(7, base.pixelSize() + self.text_size * 4 // 3))
            widget.setFont(font)
        for view in (self.wanted, self.decodes):
            view.font_changed()

    def first_layout(self) -> str:
        name = self.config.display.layout
        if name == "auto":
            screen = QGuiApplication.primaryScreen()
            return auto_layout(screen.availableGeometry().width() if screen else 0)
        return name

    def reset_layout(self) -> None:
        """Forget the saved arrangement and columns: as the first time."""
        self.wanted.hidden, self.decodes.hidden_by_layout = set(), {}
        self.wanted.reset_columns()
        self.decodes.reset_columns()
        self.set_layout(self.first_layout())

    def save_window(self) -> None:
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("state", self.saveState(STATE_VERSION))
        s.setValue("layout", self.layout_name)
        s.setValue("text_size", self.text_size)
        s.setValue("locked", self.locked)
        s.setValue("wanted_columns", json.dumps(self.wanted.state()))
        s.setValue("decode_columns", json.dumps(self.decodes.state()))
        s.sync()

    def _status_bar(self) -> None:
        bar = self.statusBar()
        bold = QFont(self.font())
        bold.setBold(True)
        self.badge = QLabel()
        self.badge.setFont(bold)
        self.freq = QLabel()
        self.freq.setFont(bold)
        self.freq.setContentsMargins(6, 0, 6, 0)
        self.scroll_note = QLabel(" SCROLL ")
        self.scroll_note.setStyleSheet(_NOTE_STYLE % "#e5c07b")
        self.status = QLabel()
        self.status.setFont(fixed_font())
        self.changes = QLabel()
        self.changes.setStyleSheet(_NOTE_STYLE % "#56b6c2")
        for widget in (self.badge, self.freq, self.scroll_note, self.status):
            bar.addWidget(widget)
        bar.addPermanentWidget(self.changes)      # at the end, out of the way (Matt)

    def start(self) -> None:
        """Start the Engine; the window is up to show what it reports."""
        self.engine_task = asyncio.ensure_future(self.engine.run())
        self.engine_task.add_done_callback(self._engine_done)

    def _engine_done(self, task) -> None:
        """The engine stops on its own only after a Fatal event, or by crashing."""
        if not task.cancelled() and task.exception() is not None:
            exc = task.exception()
            self.fatal(f"[fatal] {type(exc).__name__}: {exc}")

    # ── Status bar, title, columns ────────────────────────────────────────────

    def _tick(self) -> None:
        eng = self.engine
        badge = status_badge(eng)
        self.badge.setText(badge)
        self.badge.setStyleSheet(_BADGE_STYLE[badge])
        freq = ""
        if eng.dial_freq:
            freq = f"{eng.dial_freq / 1e6:.3f} MHz {_freq_to_band(eng.dial_freq) or ''}".strip()
        self.freq.setText(freq)
        self.freq.setVisible(bool(freq))
        self.scroll_note.setVisible(not self.decodes.at_end)
        self.status.setText(status_text(eng).strip())
        changes = session_changes(self.config, self.configured)
        self.changes.setText(changes)
        self.changes.setVisible(bool(changes))
        title = ["jtcon"]
        if freq:
            ct = eng.cycle_tracker
            title.append(f"{freq} {ct.current_mode}" if ct and ct.current_mode else freq)
        if eng.de_call:
            title.append(eng.de_call)
        self.setWindowTitle(" — ".join(title))
        # The columns depend on the logbook and hamdat, which load after startup.
        self.decodes.decodes.set_columns(decode_columns(
            self.config, has_log=eng.logbook is not None, has_hamdat=eng.hamdat is not None))
        self.refresh_wanted()

    def refresh_wanted(self, force: bool = False) -> None:
        """Update the Wanted table when something changed, and every second
        for the "Seen" times and the changed-row highlight."""
        now = time.monotonic()
        if force or self._wanted_dirty or now - self._wanted_shown >= 1.0:
            self._wanted_dirty = False
            self._wanted_shown = now
            self.wanted.wanted.refresh(now)

    # ── Stations ──────────────────────────────────────────────────────────────

    def current_call(self) -> str | None:
        """The selected station for the Station menu: in the table used last,
        else in the other one."""
        other = self.decodes if self.last_table is self.wanted else self.wanted
        for view in (self.last_table, other):
            if view.isVisibleTo(self) and view.selected_call:
                return view.selected_call
        return None

    def can_answer(self, call: str) -> bool:
        """Answering needs a CQ from *call* in the buffer."""
        return self.engine.stations.find(call) is not None

    def answer(self, call: str | None) -> None:
        station = self.engine.stations.find(call) if call else None
        if station:
            self.engine.reply(station)
        elif call:
            self.log_view.add(f"[answer] No CQ from {call} to answer.")
        else:
            self.log_view.add("[answer] Select a station in the Wanted or Decodes panel.")

    def rules_changed(self) -> None:
        """A rule, the scope or the show setting changed: the status columns
        and the rows follow."""
        self._tick()
        self.refresh_wanted(force=True)

    def ignore(self, call: str) -> None:
        self.engine.ignore(call)
        self.refresh_wanted(force=True)
        self.menus.ignored_changed()

    def reload_config(self) -> None:
        """File › Reload config: restart with the config read again, or say
        why not and carry on."""
        if self.reload is None:
            return
        self.log_view.add("[config] Reloading the config …")
        error = self.reload()
        if error:
            self.log_view.add(f"[config] {error}  (carrying on as before)")
            if self.dialogs:
                QMessageBox(QMessageBox.Icon.Warning, "jtcon", error, parent=self).open()

    # ── Engine events ─────────────────────────────────────────────────────────

    def _on_event(self, ev) -> None:
        if isinstance(ev, SpotEvent):
            self._show_spot(ev)
        elif isinstance(ev, StationUpdated):
            self.wanted.wanted.note(ev.result, ev.ctx, ev.station)
            self._wanted_dirty = True
        elif isinstance(ev, LogLine):
            self.log_view.add(ev.text, ev.key)
        elif isinstance(ev, (Ready, UdpDown)):
            self.log_view.add(ev.text)
        elif isinstance(ev, Fatal):
            self.fatal(ev.text)

    def _show_spot(self, ev: SpotEvent) -> None:
        if ev.result.new_alert:
            QApplication.beep()
        if ev.station is not None:
            self.wanted.wanted.note(ev.result, ev.ctx, ev.station)
            self._wanted_dirty = True
        self.decodes.decodes.add(DecodeRow(ev.ctx, ev.result, ev.station))

    # ── Closing ───────────────────────────────────────────────────────────────

    def fatal(self, msg: str) -> None:
        """Show *msg*, then close; it's repeated on the terminal after exit."""
        self.log_view.add(msg)
        self.fatal_msg = msg
        if not self.dialogs:
            self.close()
            return
        box = QMessageBox(QMessageBox.Icon.Critical, "jtcon", msg, parent=self)
        box.finished.connect(self.close)
        box.open()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        if not self._closed:
            self._closed = True
            self.save_window()
            self.closing.emit()
        event.accept()
