"""The prompt_toolkit full-screen application."""

import asyncio
import sys
import threading

from prompt_toolkit import Application
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_focus
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.utils import explode_text_fragments
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

from jtcon.core.engine import Engine
from jtcon.core.events import (Fatal, LogLine, Ready, SpotEvent, StationUpdated, StatusChanged,
                               UdpDown)
from jtcon.tui.render import _header_line, _render
from jtcon.ui.commands import COMMANDS, Commands, match_command
from jtcon.ui.status import status_badge, status_text

_MAX_OUTPUT_LINES = 5000   # output pane is trimmed when it grows past this…
_TRIM_OUTPUT_TO   = 4000   # …down to this many lines
_WHEEL_LINES      = 3      # lines per mouse-wheel notch (--mouse)

_COMMANDS = COMMANDS               # shared with the other front ends
_match_command = match_command


# ══════════════════════════════════════════════════════════════════════════════
#  ANSI lexer — lets the output TextArea render ANSI escape codes as colours
# ══════════════════════════════════════════════════════════════════════════════

class AnsiLexer(Lexer):
    """Parse ANSI escape sequences in each buffer line for prompt_toolkit."""

    def lex_document(self, document):
        lines = document.lines

        def get_line(lineno):
            if 0 <= lineno < len(lines):
                try:
                    return list(to_formatted_text(ANSI(lines[lineno])))
                except Exception:
                    return [("", lines[lineno])]
            return []

        return get_line


# ══════════════════════════════════════════════════════════════════════════════
#  TUI Application
# ══════════════════════════════════════════════════════════════════════════════

class JtconTUI:
    """prompt_toolkit full-screen TUI for jtcon."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.config = engine.config
        engine.subscribe(self._on_event)

        self._fatal_msg    = None   # set by _fatal(); the app exits on next render
        self._exiting      = False
        # Output pane: lines are queued in _pending (from any thread) and moved
        # into _lines (and the TextArea) by the UI thread just before each render.
        self._output_lock  = threading.Lock()
        self._pending: list = []
        self._lines:   list = []
        self._last_key = None       # key of the last line queued (see LogLine)
        self._replace_last = False  # the next flush replaces _lines[-1]
        # Top line of the decode pane while scrolled back from the command line;
        # None means the pane follows new output.
        self._scroll_top: int | None = None

        # ── Widgets ──────────────────────────────────────────────────────────
        self.output_area = TextArea(
            text="",
            read_only=True,
            scrollbar=True,
            wrap_lines=False,
            focusable=True,
            lexer=AnsiLexer(),
            style="class:output",
        )
        self.cmd_area = TextArea(
            height=1,
            multiline=False,
            wrap_lines=False,
            prompt="CMD> ",
            accept_handler=self._handle_command,
            style="class:cmd",
        )
        self._app = self._build_app()
        self._app.before_render += self._flush_output
        self.commands = Commands(engine, out=self._append_line,
                                 quit=lambda: self._exit_app(),
                                 clear=lambda: self._clear_output())

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_app(self) -> Application:
        # The column header stays put above the decode pane, which scrolls under it.
        header_window = Window(
            content=FormattedTextControl(self._get_header_text),
            dont_extend_height=True,
            wrap_lines=False,
        )
        separator = Window(height=1, char="─", style="class:separator")
        status_window = Window(
            content=FormattedTextControl(self._get_status_text),
            height=1,
            style="class:status",
        )

        layout = Layout(
            HSplit([
                header_window,
                self.output_area,
                separator,
                self.cmd_area,
                status_window,
            ]),
            focused_element=self.cmd_area,
        )

        kb = KeyBindings()

        @kb.add("c-c")
        @kb.add("c-q")
        def _quit(event):
            self._exit_app()

        @kb.add("tab")
        def _toggle_focus(event):
            app = event.app
            if app.layout.has_focus(self.cmd_area):
                app.layout.focus(self.output_area)
            else:
                app.layout.focus(self.cmd_area)

        # Scroll the decode pane while typing at CMD>.  These replace the
        # command-line history on ↑/↓.  Many terminals also send ↑/↓ for the
        # mouse wheel, so the wheel scrolls the pane even without --mouse.
        cmd_focused = has_focus(self.cmd_area)

        @kb.add("up", filter=cmd_focused)
        def _line_up(event):
            self._scroll_output(-1)

        @kb.add("down", filter=cmd_focused)
        def _line_down(event):
            self._scroll_output(1)

        @kb.add("pageup", filter=cmd_focused)
        def _page_up(event):
            self._scroll_output(-self._page_size())

        @kb.add("pagedown", filter=cmd_focused)
        def _page_down(event):
            self._scroll_output(self._page_size())

        # eager: don't wait to see if Esc starts an Alt-key sequence.
        @kb.add("escape", filter=cmd_focused, eager=True)
        @kb.add("c-end", filter=cmd_focused)
        def _to_live(event):
            self._scroll_to_live()

        if self.config.display.mouse:
            self._wheel_scrolls_output(self.output_area.control)
            self._wheel_scrolls_output(self.cmd_area.control)

        style = Style.from_dict({
            "output":     "",
            "cmd":        "",
            "separator":  "fg:ansibrightblack",
            "status":     "reverse",
            "status.tx":  "bg:ansired fg:ansiwhite bold noreverse",
            "status.rx":  "fg:ansibrightgreen noreverse",
            "status.scroll": "bg:ansiyellow fg:ansiblack noreverse",
        })

        return Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=True,
            mouse_support=self.config.display.mouse,
            refresh_interval=0.25,   # redraws status bar ~4×/s for smooth bargraph
        )

    # ── Status line ───────────────────────────────────────────────────────────

    def _get_status_text(self):
        badge = status_badge(self.engine)
        # A list of (style, text) tuples so the TX badge can be coloured.
        trx = ("class:status.rx" if badge == "RX" else "class:status.tx", f" {badge} ")
        scroll = [("class:status.scroll", " SCROLL ")] if self._scroll_top is not None else []
        return [trx, *scroll, ("class:status", status_text(self.engine))]

    # ── Command handler ───────────────────────────────────────────────────────

    def _handle_command(self, buffer) -> None:
        if not buffer.text.strip():
            return
        # Command output goes to the bottom of the pane; make sure it's visible.
        if self._scroll_top is not None:
            self._scroll_to_live()
        self.commands.handle(buffer.text)

    def _cmd_clear(self, arg: str = "") -> None:
        self._clear_output()

    def _clear_output(self) -> None:
        with self._output_lock:
            self._pending.clear()
            self._last_key, self._replace_last = None, False
        self._lines = []
        self._set_output(follow=True)

    # ── Output append (thread-safe) ───────────────────────────────────────────

    def _append_line(self, text: str, key: str | None = None) -> None:
        """Queue *text* for the output pane.  Safe to call from any thread.
        A *key* matching the last line's replaces that line (see LogLine)."""
        with self._output_lock:
            if key is not None and key == self._last_key:
                if self._pending:
                    self._pending.pop()
                else:
                    self._replace_last = True
            self._pending.extend(text.split("\n"))
            self._last_key = key
        self._app.invalidate()

    def _flush_output(self, _app=None) -> None:
        """before_render hook (UI thread): move queued lines into the pane."""
        if self._fatal_msg and not self._exiting:
            self._exit_app()
        with self._output_lock:
            new, self._pending = self._pending, []
            replace, self._replace_last = self._replace_last, False
        if not new:
            return
        if replace and self._lines:
            self._lines.pop()
        self._lines.extend(new)
        dropped = 0
        if len(self._lines) > _MAX_OUTPUT_LINES:
            dropped = len(self._lines) - _TRIM_OUTPUT_TO
            del self._lines[:dropped]
        self._set_output(dropped)

    def _set_output(self, dropped: int = 0, follow: bool = False) -> None:
        """
        Replace the pane text with self._lines (UI thread only).

        TextArea.text setter uses Document(value, 0) — cursor at top — so the
        Window's do_scroll() always scrolls back to line 0.  Set the Document
        directly with cursor at the end so do_scroll() tracks the bottom.
        When the user is reviewing older output — tabbed into the pane, or
        scrolled back from the command line — keep their view on the same
        lines, shifted up by the *dropped* lines trimmed from the top.
        *follow* forces the view back to the bottom.
        """
        text   = "\n".join(self._lines) + "\n"
        win    = self.output_area.window
        buf    = self.output_area.buffer
        if follow:
            self._scroll_top = None
        if self._scroll_top is not None and not self._app.layout.has_focus(win):
            self._scroll_top = max(0, self._scroll_top - dropped)
            doc = Document(text)
            self.output_area.document = Document(
                text, doc.translate_row_col_to_index(self._scroll_top, 0))
            win.vertical_scroll = self._scroll_top
            return
        if not follow and self._app.layout.has_focus(win):
            old = buf.document
            row = max(0, old.cursor_position_row - dropped)
            cursor = Document(text).translate_row_col_to_index(row, old.cursor_position_col)
            win.vertical_scroll = max(0, win.vertical_scroll - dropped)
        else:
            cursor = len(text)
        self.output_area.document = Document(text, cursor)

    def _page_size(self) -> int:
        info = self.output_area.window.render_info
        return max(1, (info.window_height if info else 20) - 1)

    def _scroll_output(self, delta: int) -> None:
        """Scroll the decode pane by *delta* lines (negative = up), keeping focus."""
        win    = self.output_area.window
        info   = win.render_info
        height = info.window_height if info else 20
        # The text ends in "\n", so the document has one more (empty) row.
        bottom = max(0, len(self._lines) + 1 - height)
        start  = win.vertical_scroll if self._scroll_top is None else self._scroll_top
        top    = max(0, min(bottom, start + delta))
        if top >= bottom:
            self._scroll_to_live()
            return
        self._scroll_top = top
        buf = self.output_area.buffer
        buf.cursor_position = buf.document.translate_row_col_to_index(top, 0)
        win.vertical_scroll = top

    def _scroll_to_live(self) -> None:
        """Return the decode pane to the bottom and resume following output."""
        self._scroll_top = None
        buf = self.output_area.buffer
        buf.cursor_position = len(buf.text)

    def _wheel_scrolls_output(self, control) -> None:
        """Make mouse-wheel events over *control* scroll the decode pane (--mouse)."""
        original = control.mouse_handler

        def handler(mouse_event):
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                self._scroll_output(-_WHEEL_LINES)
                return None
            if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                self._scroll_output(_WHEEL_LINES)
                return None
            return original(mouse_event)

        control.mouse_handler = handler

    def _header(self) -> str:
        return _header_line(self.config, has_log=self.engine.logbook is not None,
                            has_hamdat=self.engine.hamdat is not None)

    def _get_header_text(self):
        """The fixed header, shifted with the decode pane if it scrolls sideways."""
        frags = explode_text_fragments(to_formatted_text(ANSI(self._header())))
        skip = self.output_area.window.horizontal_scroll
        if not skip:
            return frags
        out, col = [], 0
        for style, text, *rest in frags:
            col = 0 if text == "\n" else col + 1
            if text == "\n" or col > skip:
                out.append((style, text, *rest))
        return out

    def _fatal(self, msg: str) -> None:
        """Log *msg*, stop, and close the TUI; *msg* is reprinted after exit."""
        self._append_line(msg)
        self._fatal_msg = msg
        self._app.invalidate()

    def _exit_app(self) -> None:
        """Exit the TUI (UI thread only); safe to call more than once."""
        if not self._exiting:
            self._exiting = True
            self._app.exit()

    # ── Engine events (event loop thread) ─────────────────────────────────────

    def _on_event(self, ev) -> None:
        if isinstance(ev, SpotEvent):
            self._show_spot(ev)
        elif isinstance(ev, StationUpdated):
            ev.station.display = _render(ev.ctx, ev.result)     # for "list"
        elif isinstance(ev, LogLine):
            self._append_line(ev.text, ev.key)
        elif isinstance(ev, StatusChanged):
            self._app.invalidate()
        elif isinstance(ev, Ready):
            self._append_line(ev.text)
        elif isinstance(ev, UdpDown):
            self._append_line(ev.text)
        elif isinstance(ev, Fatal):
            self._fatal(ev.text)

    def _show_spot(self, ev: SpotEvent) -> None:
        lines = _render(ev.ctx, ev.result)
        if ev.result.new_alert:
            sys.stderr.write("\a")
            sys.stderr.flush()
        if ev.station is not None:
            ev.station.display = lines
            lines = [f"{ev.station.idx:02d}  {lines[0]}"] + lines[1:]
        else:
            # Non-CQ with no hit (show = "all"): indent to keep columns aligned.
            lines = [f"    {lines[0]}"] + lines[1:]
        for line in lines:
            self._append_line(line)

    # ── Entry point ───────────────────────────────────────────────────────────

    async def run_async(self) -> int:
        """Run the engine and the TUI together; return a process exit status."""
        engine = asyncio.ensure_future(self.engine.run())
        engine.add_done_callback(self._engine_done)
        try:
            await self._app.run_async()
        finally:
            self.engine.stop()
            try:
                await engine
            except Exception:
                pass                # reported by _engine_done
            self.engine.retry_uploads_at_exit(print)
            self.engine.close()
        if self._fatal_msg:
            # The full-screen pane is gone; repeat the reason on the terminal.
            print(self._fatal_msg, file=sys.stderr)
            return 1
        return 0

    def _engine_done(self, task) -> None:
        """The engine stops on its own only after a Fatal event, or by crashing."""
        if not task.cancelled() and task.exception() is not None:
            exc = task.exception()
            self._fatal(f"[fatal] {type(exc).__name__}: {exc}")
        self._app.invalidate()

    def run(self) -> int:
        return asyncio.run(self.run_async())
