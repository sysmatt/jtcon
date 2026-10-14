"""The Textual front end (--ui textual).  See docs/tui-textual-plan.md.

Stage 1: the decode stream with its fixed column header, a log strip for
jtcon's own messages, the command line and the status line.
"""

import asyncio
import sys

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Input, Label, RichLog, Static

from jtcon.core.bands import _freq_to_band
from jtcon.core.engine import Engine
from jtcon.core.events import Fatal, LogLine, Ready, SpotEvent, StationUpdated, UdpDown
from jtcon.tui.commands import Commands
from jtcon.tui.render import _header_line, _render, status_badge, status_text

DECODE_LINES = 5000     # decode pane lines kept
LOG_LINES    = 500      # log strip lines kept


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
    while scrolled back."""

    def __init__(self, **kw):
        super().__init__(wrap=False, auto_scroll=False, max_lines=DECODE_LINES, **kw)

    def add(self, text: str) -> None:
        follow = self.is_vertical_scroll_end
        self.write(_ansi(text), shrink=False, scroll_end=follow)


class JtconApp(App):
    """The Textual screen.  JtconTextual runs it together with the Engine."""

    CSS = """
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
        # Scroll the decode pane while typing at CMD>.
        Binding("up", "scroll_decodes(-1)", show=False, priority=True),
        Binding("down", "scroll_decodes(1)", show=False, priority=True),
        Binding("pageup", "page_decodes(-1)", show=False, priority=True),
        Binding("pagedown", "page_decodes(1)", show=False, priority=True),
        Binding("escape,ctrl+end", "decodes_to_live", show=False, priority=True),
    ]

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
        self.engine_task: asyncio.Future | None = None

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        yield DecodePane(id="decodes")
        yield LogStrip(id="log")
        with Horizontal(id="cmdline"):
            yield Label("CMD> ", id="prompt")
            yield Input(id="cmd")
        yield Static(id="status")

    def on_mount(self) -> None:
        self.decodes = self.query_one(DecodePane)
        self.log_strip = self.query_one(LogStrip)
        self.header = self.query_one("#header", Static)
        self.status = self.query_one("#status", Static)
        self.query_one("#cmd", Input).focus()
        self.watch(self.decodes, "scroll_x", lambda *_: self._show_header(), init=False)
        self.set_interval(0.25, self._show_status)
        self._show_header()
        self._show_status()
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

    def _show_header(self) -> None:
        """The column header, shifted with the decode pane if it scrolls sideways."""
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
        self.status.update(line)
        # The header depends on the logbook, which loads after startup.
        if self.engine.logbook is not None and "days" not in self._header_text:
            self._show_header()

    # ── Commands and keys ─────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value
        event.input.value = ""
        if text.strip():
            self.decodes.scroll_end(animate=False)
            self.commands.handle(text)

    def action_scroll_decodes(self, lines: int) -> None:
        self.decodes.scroll_relative(y=lines, animate=False)

    def action_page_decodes(self, direction: int) -> None:
        page = max(1, self.decodes.scrollable_content_region.height - 1)
        self.decodes.scroll_relative(y=direction * page, animate=False)

    def action_decodes_to_live(self) -> None:
        self.decodes.scroll_end(animate=False)

    def action_quit_jtcon(self) -> None:
        self.exit()

    def _clear(self) -> None:
        self.decodes.clear()
        self.log_strip.reset()

    # ── Engine events (on the event loop, this app's thread) ──────────────────

    def _on_event(self, ev) -> None:
        if isinstance(ev, SpotEvent):
            self._show_spot(ev)
        elif isinstance(ev, StationUpdated):
            ev.station.display = _render(ev.ctx, ev.result)     # for "list"
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
        if ev.station is not None:
            ev.station.display = lines
            lines = [f"{ev.station.idx:02d}  {lines[0]}"] + lines[1:]
        else:
            lines = [f"    {lines[0]}"] + lines[1:]
        for line in lines:
            self.decodes.add(line)

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
