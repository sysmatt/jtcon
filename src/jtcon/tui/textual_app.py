"""The Textual front end (--ui textual).  See docs/tui-textual-plan.md.

The Wanted table and the decode stream (with its fixed column header), in one
of four layouts; a log strip for jtcon's own messages; the command line and
the status line.
"""

import asyncio
import sys
import time
from collections import deque

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, RichLog, SelectionList, Static

from jtcon.core.bands import _freq_to_band
from jtcon.core.engine import Engine
from jtcon.core.events import Fatal, LogLine, Ready, SpotEvent, StationUpdated, UdpDown
from jtcon.tui.commands import Commands
from jtcon.tui.render import (_header_compact, _header_line, _render, _render_compact,
                              status_badge, status_text)
from jtcon.tui.wanted_table import WantedTable
from jtcon.core.wanted import rule_label

DECODE_LINES = 5000     # decode pane lines kept
LOG_LINES    = 500      # log strip lines kept

# Layouts, in the order Ctrl-arrows step through them, with their names.
LAYOUTS = {"wanted": "Wanted", "decodes": "Decodes", "side": "Side by side",
           "stacked": "Stacked"}
AUTO_SIDE_COLUMNS = 200     # auto layout: side by side from this wide...
AUTO_STACKED_ROWS = 50      # ...else stacked from this tall, else Wanted


def auto_layout(width: int, height: int) -> str:
    if width >= AUTO_SIDE_COLUMNS:
        return "side"
    if height >= AUTO_STACKED_ROWS:
        return "stacked"
    return "wanted"


def _ansi(text: str) -> Text:
    return Text.from_ansi(text, no_wrap=True, overflow="ignore")


class LogStrip(RichLog):
    """jtcon's own messages.  A line with a key replaces the line before it
    when that one has the same key (progress shown on one line, see LogLine)."""

    def __init__(self, **kw):
        super().__init__(wrap=False, auto_scroll=True, max_lines=LOG_LINES, **kw)
        self.entries: list = []        # (text, key)

    def add(self, text: str, key: str | None = None) -> None:
        if key is not None and self.entries and self.entries[-1][1] == key:
            self.entries[-1] = (text, key)
            self.clear()
            for line, _ in self.entries:
                super().write(_ansi(line), shrink=False)
            return
        for line in text.split("\n"):
            self.entries.append((line, key))
            super().write(_ansi(line), shrink=False)
        del self.entries[:-LOG_LINES]

    def reset(self) -> None:
        self.entries.clear()
        self.clear()


class DecodePane(RichLog):
    """The decode stream: follows new lines while at the bottom, stays put
    while scrolled back.  Each decode is kept in its full and compact forms
    (rendered when it arrived), so switching layouts redraws without
    re-rendering."""

    def __init__(self, **kw):
        super().__init__(wrap=False, auto_scroll=False, max_lines=DECODE_LINES, **kw)
        self.compact = False
        self.entries: deque = deque(maxlen=DECODE_LINES)    # (full lines, compact lines)

    def add(self, text: str) -> None:
        """A line of text (command output): the same in both forms."""
        self.add_entry([text], [text])

    def add_entry(self, full: list, compact: list) -> None:
        self.entries.append((full, compact))
        follow = self.is_vertical_scroll_end
        for line in compact if self.compact else full:
            self.write(_ansi(line), shrink=False, scroll_end=follow)

    def set_compact(self, compact: bool) -> None:
        if compact == self.compact:
            return
        self.compact = compact
        self.clear()
        for full, short in self.entries:
            for line in short if compact else full:
                self.write(_ansi(line), shrink=False, scroll_end=False)

    def reset(self) -> None:
        self.entries.clear()
        self.clear()


class RulesScreen(ModalScreen):
    """The rules panel: each [[wanted]] rule with a switch, applied at once."""

    CSS = """
    RulesScreen { align: center middle; }
    #rules-box { width: 56; height: auto; max-height: 80%; padding: 1 2;
                 border: round $primary; background: $surface; }
    #rules { height: auto; max-height: 20; border: none; }
    #rules-help { color: $text-muted; padding-top: 1; }
    """
    BINDINGS = [Binding("escape,f6", "dismiss", "Close")]

    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def compose(self) -> ComposeResult:
        cfg = self.engine.config
        with Vertical(id="rules-box"):
            yield Label("[b]Wanted rules[/b] for this session")
            yield SelectionList(*[(f"{rule_label(w)}  [dim]{w.rule}[/dim]"
                                   if rule_label(w) != w.rule.upper() else rule_label(w),
                                   w.name, w.name not in cfg.rules_off)
                                  for w in cfg.wanted], id="rules")
            yield Label("Space or Enter switches a rule.  F7 scope, F8 show.\n"
                        "Esc or F6 closes.  The config file isn't changed.", id="rules-help")

    def on_selection_list_selection_toggled(self, event: SelectionList.SelectionToggled) -> None:
        name = event.selection.value
        self.engine.set_rule_enabled(name, name in event.selection_list.selected)
        self.app.rules_changed()


HELP = """\
[b]Layouts[/b]
  F2  Wanted          F3  Decodes          F4  Side by side      F5  Stacked
  Ctrl-→ Ctrl-↓  next layout              Ctrl-← Ctrl-↑  previous layout

[b]Wanted table[/b]
  ↑ ↓        select a station (while typing at CMD>)
  Enter      on an empty CMD> line: answer the selected station
  Ctrl-S     sort: last heard, SNR, call, entity (or click a column header)
  Tab        focus the table; then Enter answers and i ignores
  Mouse      a click selects, a double-click answers

  QSO        📣 calling CQ   🤝 answering a CQ   📶 sending reports   👋 signing off

[b]Decodes[/b]
  PgUp PgDn  scroll back          Esc  back to the newest

[b]Live controls[/b] (this session only; the config file isn't changed)
  F6  rules on and off       F7  scope: CQs or all decodes       F8  show: CQs or all

[b]Commands at CMD>[/b] (any unique start works: l, s, un)
  07, W1ABC        answer that station
  list             the wanted stations
  ignore 07|CALL   ignore a station for this session; ignore alone lists them
  unignore CALL    stop ignoring it
  sync \\[full]      fetch online logbook changes, retry waiting uploads
  clear            clear the screen
  quit, Ctrl-Q     quit

Esc or F1 closes this."""


class HelpScreen(ModalScreen):
    CSS = """
    HelpScreen { align: center middle; }
    #help { width: auto; height: auto; padding: 1 2; border: round $primary;
            background: $surface; }
    """
    BINDINGS = [Binding("escape,f1", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Static(HELP, id="help")


class JtconApp(App):
    """The Textual screen.  JtconTextual runs it together with the Engine."""

    CSS = """
    #panes   { height: 1fr; layout: vertical; }
    #panes.side { layout: horizontal; }
    #panes.wanted #decode-pane, #panes.decodes #wanted { display: none; }
    #wanted  { height: 1fr; scrollbar-size: 1 1; }
    #decode-pane { height: 1fr; }
    #panes.stacked #wanted { height: 3fr; }
    #panes.stacked #decode-pane { height: 2fr; border-top: solid $primary-darken-2; }
    #panes.side #wanted { width: 1fr; }
    #panes.side #decode-pane { width: 68; border-left: solid $primary-darken-2; }
    #header  { height: 2; color: $text-muted; }
    #decodes { height: 1fr; border: none; scrollbar-size: 1 1; }
    #log     { height: 7; border-top: solid $primary-darken-2; scrollbar-size: 1 1; }
    #cmdline { height: 1; }
    #prompt  { width: 5; }
    #cmd     { border: none; height: 1; padding: 0; }
    #status  { height: 1; background: $boost; }
    """

    BINDINGS = [
        Binding("ctrl+q,ctrl+c", "quit_jtcon", "Quit", priority=True),
        # While typing at CMD>: ↑↓ move through the Wanted table, PgUp/PgDn
        # scroll the decode pane.  With the decode pane focused, ↑↓ scroll it.
        Binding("up", "arrow(-1)", show=False, priority=True),
        Binding("down", "arrow(1)", show=False, priority=True),
        Binding("ctrl+s", "sort", "Sort", show=False, priority=True),
        Binding("pageup", "page_decodes(-1)", show=False, priority=True),
        Binding("pagedown", "page_decodes(1)", show=False, priority=True),
        Binding("escape,ctrl+end", "decodes_to_live", show=False, priority=True),
        Binding("f2", "layout('wanted')", "Wanted", priority=True),
        Binding("f3", "layout('decodes')", "Decodes", priority=True),
        Binding("f4", "layout('side')", "Side by side", priority=True),
        Binding("f5", "layout('stacked')", "Stacked", priority=True),
        Binding("ctrl+right,ctrl+down", "step_layout(1)", show=False, priority=True),
        Binding("ctrl+left,ctrl+up", "step_layout(-1)", show=False, priority=True),
        Binding("f1", "help", "Help", priority=True),
        Binding("f6", "rules", "Rules", priority=True),
        Binding("f7", "toggle_scope", "Scope", priority=True),
        Binding("f8", "toggle_show", "Show", priority=True),
    ]

    # Keys the main screen takes even while typing; with the rules panel open
    # they go to the panel instead.
    _MAIN_SCREEN_ACTIONS = {"arrow", "sort", "page_decodes", "decodes_to_live", "layout",
                            "step_layout", "rules", "help"}

    def __init__(self, engine: Engine, start_engine: bool = True):
        super().__init__()
        self.engine = engine
        self.start_engine = start_engine      # False in tests: the engine is set up
        self.config = engine.config
        self.fatal_msg: str | None = None
        self.commands = Commands(engine, out=lambda t: self.log_strip.add(t),
                                 quit=lambda: self.action_quit_jtcon(),
                                 clear=lambda: self._clear(),
                                 listing=lambda t: self.decodes.add(t))
        self._header_text = ""
        self._table_dirty = True
        self._table_shown = 0.0         # monotonic time of the last rebuild
        self.layout = ""                # set on mount
        # As configured, to show on the status line when changed for the session.
        self.configured = (engine.config.scope.decodes, engine.config.display.show)
        self.engine_task: asyncio.Future | None = None

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Container(id="panes"):
            yield WantedTable(self.engine, id="wanted")
            with Vertical(id="decode-pane"):
                yield Static(id="header")
                yield DecodePane(id="decodes")
        yield LogStrip(id="log")
        with Horizontal(id="cmdline"):
            yield Label("CMD> ", id="prompt")
            yield Input(id="cmd")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.decodes = self.query_one(DecodePane)
        self.wanted = self.query_one(WantedTable)
        self.log_strip = self.query_one(LogStrip)
        self.header = self.query_one("#header", Static)
        self.status = self.query_one("#status", Static)
        self.query_one("#cmd", Input).focus()
        layout = self.config.display.layout
        self.set_layout(auto_layout(*self.size) if layout == "auto" else layout)
        self.watch(self.decodes, "scroll_x", lambda *_: self._show_header(), init=False)
        self.set_interval(0.25, self._show_status)
        self.set_interval(0.25, self._show_table)
        self._show_header()
        self._show_status()
        self.log_strip.add("[keys] F1 help · F2–F5 layouts · F6 rules · F7 scope · F8 show"
                           " · Enter on an empty CMD> answers the selected station")
        self.engine.subscribe(self._on_event)
        # The Engine starts once the screen is up to show what it reports.
        if self.start_engine:
            self.engine_task = asyncio.ensure_future(self.engine.run())
            self.engine_task.add_done_callback(self._engine_done)

    def _engine_done(self, task) -> None:
        """The engine stops on its own only after a Fatal event, or by crashing."""
        if not task.cancelled() and task.exception() is not None:
            exc = task.exception()
            self.fatal(f"[fatal] {type(exc).__name__}: {exc}")

    # ── Header and status ─────────────────────────────────────────────────────

    def set_layout(self, layout: str) -> None:
        follow = self.decodes.is_vertical_scroll_end
        panes = self.query_one("#panes")
        panes.set_classes(layout)
        self.layout = layout
        # The split layouts use the compact decode line; the table shows the facts.
        self.decodes.set_compact(layout in ("side", "stacked"))
        self._show_header()
        if follow:          # keep following the newest decodes in the new size
            self.call_after_refresh(self.decodes.scroll_end, animate=False)
        if layout == "decodes" and self.focused is self.wanted:
            self.query_one("#cmd", Input).focus()

    def action_layout(self, layout: str) -> None:
        self.set_layout(layout)

    def action_step_layout(self, step: int) -> None:
        names = list(LAYOUTS)
        self.set_layout(names[(names.index(self.layout) + step) % len(names)])

    def _show_header(self) -> None:
        """The column header, shifted with the decode pane if it scrolls sideways."""
        if self.decodes.compact:
            text = _header_compact()
        else:
            text = _header_line(self.config, has_log=self.engine.logbook is not None,
                                has_hamdat=self.engine.hamdat is not None)
        self._header_text = text
        skip = int(self.decodes.scroll_x)
        self.header.update(Text("\n".join(line[skip:] for line in text.split("\n")),
                                no_wrap=True, overflow="crop"))

    def _show_status(self) -> None:
        eng = self.engine
        badge = status_badge(eng)
        style = "bold white on red" if badge != "RX" else "bold green"
        line = Text(f" {badge} ", style=style)
        if eng.dial_freq:
            band = _freq_to_band(eng.dial_freq) or ""
            line.append(f" {eng.dial_freq / 1e6:.3f} MHz {band}", style="bold")
        if not self.decodes.is_vertical_scroll_end:
            line.append(" SCROLL ", style="black on yellow")
        line.append(status_text(eng))
        changes = self.session_changes()          # last, out of the way (Matt)
        if changes:
            line.append(f" {changes} ", style="black on cyan")
        self.status.update(line)
        # The header depends on the logbook, which loads after startup.
        if (self.engine.logbook is not None and not self.decodes.compact
                and "days" not in self._header_text):
            self._show_header()

    def _show_table(self, force: bool = False) -> None:
        """Rebuild the Wanted table when something changed, and every second
        for the "Seen" times."""
        now = time.monotonic()
        if force or self._table_dirty or now - self._table_shown >= 1.0:
            self._table_dirty = False
            self._table_shown = now
            self.wanted.refresh_rows()

    # ── Commands and keys ─────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """A command; or, on an empty line, answer the selected station."""
        text = event.value
        event.input.value = ""
        if text.strip():
            self.decodes.scroll_end(animate=False)
            self.commands.handle(text)
            self._show_table(force=True)
        else:
            self.answer(self.wanted.selected_call)

    def on_wanted_table_answer(self, event: WantedTable.Answer) -> None:
        self.answer(event.call)

    def answer(self, call: str | None) -> None:
        station = self.engine.stations.find(call) if call else None
        if station:
            self.engine.reply(station)
        else:
            self.log_strip.add("[cmd] No station selected in the Wanted table.")

    def action_arrow(self, step: int) -> None:
        if self.focused is self.decodes or self.layout == "decodes":
            self.decodes.scroll_relative(y=step, animate=False)
        elif self.wanted.row_count:
            self.wanted.move_cursor(row=self.wanted.cursor_row + step, animate=False)

    def action_sort(self) -> None:
        self.log_strip.add(f"[wanted] Sorted by {self.wanted.cycle_sort()}")

    def action_page_decodes(self, direction: int) -> None:
        page = max(1, self.decodes.scrollable_content_region.height - 1)
        self.decodes.scroll_relative(y=direction * page, animate=False)

    def action_decodes_to_live(self) -> None:
        self.decodes.scroll_end(animate=False)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action in self._MAIN_SCREEN_ACTIONS and isinstance(self.screen, ModalScreen):
            return False
        return True

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_rules(self) -> None:
        self.push_screen(RulesScreen(self.engine))

    def rules_changed(self) -> None:
        """A rule was switched: the status columns and rows follow."""
        self._show_header()
        self._show_table(force=True)

    def action_toggle_scope(self) -> None:
        cfg = self.config
        self.engine.set_scope("cq" if cfg.scope.decodes == "all" else "all")
        self._show_table(force=True)

    def action_toggle_show(self) -> None:
        cfg = self.config
        self.engine.set_show("cq" if cfg.display.show == "all" else "all")

    def session_changes(self) -> str:
        """What differs from the config file for this session, for the status line."""
        cfg, out = self.config, []
        if cfg.scope.decodes != self.configured[0]:
            out.append(f"scope:{cfg.scope.decodes}")
        if cfg.display.show != self.configured[1]:
            out.append(f"show:{cfg.display.show}")
        if cfg.rules_off:
            out.append("off:" + ",".join(rule_label(w) for w in cfg.wanted
                                         if w.name in cfg.rules_off))
        return " ".join(out)

    def action_quit_jtcon(self) -> None:
        self.exit()

    def _clear(self) -> None:
        self.decodes.reset()
        self.log_strip.reset()

    # ── Engine events (on the event loop, this app's thread) ──────────────────

    def _on_event(self, ev) -> None:
        if isinstance(ev, SpotEvent):
            self._show_spot(ev)
        elif isinstance(ev, StationUpdated):
            ev.station.display = _render(ev.ctx, ev.result)     # for "list"
            self.wanted.note(ev.result, ev.ctx, ev.station)
            self._table_dirty = True
        elif isinstance(ev, LogLine):
            self.log_strip.add(ev.text, ev.key)
        elif isinstance(ev, (Ready, UdpDown)):
            self.log_strip.add(ev.text)
        elif isinstance(ev, Fatal):
            self.fatal(ev.text)

    def _show_spot(self, ev: SpotEvent) -> None:
        lines = _render(ev.ctx, ev.result)
        if ev.result.new_alert:
            self.bell()
        prefix = "    "
        if ev.station is not None:
            ev.station.display = lines
            self.wanted.note(ev.result, ev.ctx, ev.station)
            self._table_dirty = True
            prefix = f"{ev.station.idx:02d}  "
        self.decodes.add_entry([prefix + lines[0]] + lines[1:],
                               [prefix + _render_compact(ev.ctx, ev.result)])

    def fatal(self, msg: str) -> None:
        """Show *msg*, then close; it's repeated on the terminal after exit."""
        self.log_strip.add(msg)
        self.fatal_msg = msg
        self.exit()


class JtconTextual:
    """Runs the Engine and the Textual app together."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.app = JtconApp(engine)

    async def run_async(self) -> int:
        try:
            await self.app.run_async()
        finally:
            self.engine.stop()
            if self.app.engine_task is not None:
                try:
                    await self.app.engine_task
                except Exception:
                    pass                # reported by JtconApp._engine_done
            self.engine.retry_uploads_at_exit(print)
            self.engine.close()
        if self.app.fatal_msg:
            print(self.app.fatal_msg, file=sys.stderr)
            return 1
        return 0

    def run(self) -> int:
        return asyncio.run(self.run_async())
