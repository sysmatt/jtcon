"""The main window: the Wanted table, the decode table and the log, the
command line, the status bar.  Engine events arrive on the Qt thread (one event loop, qasync)."""

import asyncio
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QMessageBox, QSplitter,
                               QVBoxLayout, QWidget)

from jtcon.core.bands import _freq_to_band
from jtcon.core.events import Fatal, LogLine, Ready, SpotEvent, StationUpdated, UdpDown
from jtcon.gui.models import DecodeRow, decode_columns
from jtcon.gui.widgets import CommandLine, DecodesView, LogView, WantedView, fixed_font
from jtcon.tui.render import _render
from jtcon.ui.commands import Commands
from jtcon.ui.status import session_changes, status_badge, status_text

TICK_MS = 250           # status bar and column refresh

_BADGE_STYLE = {
    "RX": "background: #2e7d32; color: white; font-weight: bold; padding: 1px 8px;",
    "TX": "background: #c62828; color: white; font-weight: bold; padding: 1px 8px;",
}
_BADGE_STYLE["UDP DOWN"] = _BADGE_STYLE["TX"]
_NOTE_STYLE = "padding: 1px 6px; color: black; background: %s;"


class MainWindow(QMainWindow):
    """The window.  *closing* is emitted once, when it closes for any reason."""

    closing = Signal()

    def __init__(self, engine, start_engine: bool = True, dialogs: bool = True):
        super().__init__()
        self.engine = engine
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
        self.resize(1400, 820)
        self.wanted = WantedView(engine)
        self.decodes = DecodesView()
        self.log_view = LogView()
        self.cmd = CommandLine()
        split = QSplitter(Qt.Orientation.Vertical)
        for widget, stretch in ((self.wanted, 3), (self.decodes, 3), (self.log_view, 1)):
            split.addWidget(widget)
            split.setStretchFactor(split.count() - 1, stretch)
        split.setSizes([330, 300, 130])
        central = QWidget()
        box = QVBoxLayout(central)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(split)
        box.addWidget(self.cmd)
        self.setCentralWidget(central)
        self._status_bar()

        self.commands = Commands(engine, out=self.log_view.add, quit=self.close,
                                 clear=self._clear)
        self.cmd.submitted.connect(self._command)
        self.cmd.edit.arrow.connect(self.wanted.move)
        self.wanted.answer.connect(self.answer)
        self.wanted.ignore.connect(self.ignore)
        self.wanted.sort_changed.connect(lambda name: self.log_view.add(f"[wanted] Sorted by {name}"))
        for keys, slot in (("Ctrl+Q", self.close),
                           ("Ctrl+S", self.wanted.cycle_sort),
                           ("PgUp", lambda: self.decodes.page(-1)),
                           ("PgDown", lambda: self.decodes.page(1)),
                           ("Esc", self.decodes.to_end)):
            QShortcut(QKeySequence(keys), self, activated=slot)
        self.cmd.setFocus()

        self._timer = QTimer(self, interval=TICK_MS, timeout=self._tick)
        self._timer.start()
        self._tick()
        engine.subscribe(self._on_event)

    def _status_bar(self) -> None:
        bar = self.statusBar()
        self.badge = QLabel()
        self.freq = QLabel()
        self.freq.setStyleSheet("font-weight: bold; padding: 0 6px;")
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

    # ── Commands ──────────────────────────────────────────────────────────────

    def _command(self, text: str) -> None:
        """A command; or, on an empty line, answer the selected station."""
        if text.strip():
            self.decodes.to_end()
            self.commands.handle(text)
            self.refresh_wanted(force=True)
        else:
            self.answer(self.wanted.selected_call)

    def answer(self, call: str | None) -> None:
        station = self.engine.stations.find(call) if call else None
        if station:
            self.engine.reply(station)
        else:
            self.log_view.add("[cmd] No station selected in the Wanted table.")

    def ignore(self, call: str) -> None:
        self.engine.ignore(call)
        self.refresh_wanted(force=True)

    def _clear(self) -> None:
        self.decodes.decodes.clear()
        self.log_view.reset()

    # ── Engine events ─────────────────────────────────────────────────────────

    def _on_event(self, ev) -> None:
        if isinstance(ev, SpotEvent):
            self._show_spot(ev)
        elif isinstance(ev, StationUpdated):
            ev.station.display = _render(ev.ctx, ev.result)     # for "list"
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
            ev.station.display = _render(ev.ctx, ev.result)     # for "list"
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
            self.closing.emit()
        event.accept()
