"""The prompt_toolkit full-screen application."""

import asyncio
import re
import sys
import threading
import time

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
from jtcon.core.messages import _CALLSIGN_RE
from jtcon.core.stations import fmt_age
from jtcon.tui.render import _DIM, _bargraph, _col, _header_line, _render

_MAX_OUTPUT_LINES = 5000   # output pane is trimmed when it grows past this…
_TRIM_OUTPUT_TO   = 4000   # …down to this many lines
_WHEEL_LINES      = 3      # lines per mouse-wheel notch (--mouse)

# CMD> commands and the argument each takes ("": none; "[...]": optional).  Any
# unique abbreviation works; a word with a digit is a callsign, never a command.
_COMMANDS = {
    "quit":     "",
    "exit":     "",
    "clear":    "",
    "list":     "",
    "ignore":   "[NN|CALL]",
    "unignore": "CALL",
}


def _match_command(word: str) -> list:
    """The commands *word* abbreviates: one, several (ambiguous) or none."""
    word = word.lower()
    if word in _COMMANDS:
        return [word]
    if any(c.isdigit() for c in word):
        return []
    return [name for name in _COMMANDS if name.startswith(word)]


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
        eng = self.engine
        bar = _bargraph(eng.cycle_tracker)
        ct  = eng.cycle_tracker

        if ct and ct.current_time_ms is not None:
            parity = ct._parity
            mode   = ct.current_mode
            ev, od = ct._sums["even"], ct._sums["odd"]
            all_n   = ev[2] + od[2]
            avg_dec = (ev[0] + od[0]) / all_n if all_n else 0.0
            avg_cq  = (ev[1] + od[1]) / all_n if all_n else 0.0
            ev_d = ev[0] / ev[2] if ev[2] else 0.0
            od_d = od[0] / od[2] if od[2] else 0.0
            ev_c = ev[1] / ev[2] if ev[2] else 0.0
            od_c = od[1] / od[2] if od[2] else 0.0
            stats = (
                f"  {mode}"
                f"  dec={ct.cycle_total} cq={ct.cycle_cq}"
                f"  avg dec={avg_dec:.1f} cq={avg_cq:.1f}"
                f"  even dec={ev_d:.1f} cq={ev_c:.1f}"
                f"  odd dec={od_d:.1f} cq={od_c:.1f}"
            )
        else:
            parity = "even"
            stats  = "  (waiting for decodes…)"

        # Return a list of (style, text) tuples so the TX badge can be coloured.
        if eng.udp_down:
            trx = ("class:status.tx", " UDP DOWN ")
        elif eng.transmitting:
            trx = ("class:status.tx", " TX ")
        else:
            trx = ("class:status.rx", " RX ")

        de = f"  de={eng.de_call}" if eng.de_call else ""
        ign = f"  ign:{len(eng.ignored)}" if eng.ignored else ""
        scroll = [("class:status.scroll", " SCROLL ")] if self._scroll_top is not None else []
        return [
            trx,
            *scroll,
            ("class:status", f" [{bar}] {parity}{stats}{de}{ign} "),
        ]

    # ── Command handler ───────────────────────────────────────────────────────

    def _handle_command(self, buffer) -> None:
        raw = buffer.text.strip()
        if not raw:
            return

        # Command output goes to the bottom of the pane; make sure it's visible.
        if self._scroll_top is not None:
            self._scroll_to_live()

        word, _, arg = raw.partition(" ")
        arg = arg.strip()
        names = _match_command(word)

        if re.fullmatch(r"\d{1,2}", raw):
            idx = int(raw)
            station = self.engine.stations.get(idx)
            if station:
                self.engine.reply(station)
            else:
                self._append_line(f"[cmd] No CQ at index {idx:02d}.")

        elif len(names) > 1:
            self._append_line(f"[cmd] {word!r} is ambiguous: {', '.join(names)}.")

        elif names:
            name = names[0]
            takes = _COMMANDS[name]
            if arg and not takes:
                self._append_line(f"[cmd] {name} takes no argument.")
            elif not arg and takes and not takes.startswith("["):
                self._append_line(f"[cmd] usage: {name} {takes}")
            else:
                getattr(self, f"_cmd_{name}")(arg)

        elif _CALLSIGN_RE.match(raw):
            station = self.engine.stations.find(raw.upper())
            if station:
                self.engine.reply(station)
            else:
                self._append_line(f"[cmd] {raw.upper()} not found in rolling buffer.")

        else:
            commands = ", ".join(f"{n} {a}".rstrip() for n, a in _COMMANDS.items())
            self._append_line(
                f"[cmd] Unknown: {raw!r}  (commands: {commands}; any unique abbreviation"
                " works; or enter index 00-99 / callsign to reply)"
            )

    def _cmd_quit(self, arg: str) -> None:
        self._exit_app()

    _cmd_exit = _cmd_quit

    def _cmd_clear(self, arg: str) -> None:
        with self._output_lock:
            self._pending.clear()
        self._lines = []
        self._set_output(follow=True)

    def _cmd_ignore(self, arg: str) -> None:
        """ignore NN / ignore CALL: ignore for this session; ignore: list them."""
        if not arg:
            self._show_ignored()
            return
        if re.fullmatch(r"\d{1,2}", arg):
            station = self.engine.stations.get(int(arg))
            if station is None:
                self._append_line(f"[cmd] No station at index {int(arg):02d}.")
                return
            arg = station.callsign
        if self._is_call(arg, "ignore"):
            self.engine.ignore(arg)

    def _cmd_unignore(self, arg: str) -> None:
        if self._is_call(arg, "unignore"):
            self.engine.unignore(arg)

    def _is_call(self, arg: str, name: str) -> bool:
        if _CALLSIGN_RE.match(arg.strip("<>")):
            return True
        self._append_line(f"[cmd] {arg!r} is not a callsign (usage: {name} {_COMMANDS[name]}).")
        return False

    def _show_ignored(self) -> None:
        ignored = self.engine.ignored
        if not ignored:
            self._append_line("[ignore] Nothing ignored.  ignore NN or ignore CALL ignores"
                              " a station.")
            return
        self._append_line(f"[ignore] {len(ignored)} ignored ——")
        for source in ("config", "session"):
            calls = [c for c, s in ignored.items() if s == source]
            if calls:
                self._append_line(f"  {source + ':':<9} {' '.join(calls)}")

    def _cmd_list(self, arg: str = "") -> None:
        """Print the wanted stations in the rolling buffer, most recently heard last."""
        hits = self.engine.stations.hits()
        if not hits:
            self._append_line("[list] No wanted stations in the rolling buffer.")
            return
        now = time.monotonic()
        self._append_line(f"[list] {len(hits)} wanted (last heard at bottom) ——")
        for st in hits:
            lines = st.display or [st.callsign]
            # Keep the decode row aligned with the column header; the heard
            # stats go on the WANTED line below it.
            seen = _col(
                f"(last heard {fmt_age(now - st.last_seen)} ago, ×{st.count})",
                _DIM, self.config.display.color,
            )
            self._append_line(f"{st.idx:02d}  {lines[0]}")
            if len(lines) > 1:
                self._append_line(f"{lines[1]}  {seen}")
            else:
                self._append_line(f"  {seen}")
            for line in lines[2:]:
                self._append_line(line)

    # ── Output append (thread-safe) ───────────────────────────────────────────

    def _append_line(self, text: str) -> None:
        """Queue *text* for the output pane.  Safe to call from any thread."""
        with self._output_lock:
            self._pending.extend(text.split("\n"))
        self._app.invalidate()

    def _flush_output(self, _app=None) -> None:
        """before_render hook (UI thread): move queued lines into the pane."""
        if self._fatal_msg and not self._exiting:
            self._exit_app()
        with self._output_lock:
            new, self._pending = self._pending, []
        if not new:
            return
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
            self._append_line(ev.text)
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
