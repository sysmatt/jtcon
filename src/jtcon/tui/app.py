"""The prompt_toolkit full-screen application."""

import re
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_focus
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

from jtcon.core.adif import AdifLog, _find_wsjt_logs
from jtcon.core.cty import CTY_DAT_URL, CTY_MAX_AGE_DAYS, CtyDat, download_cty
from jtcon.core.cycle import CycleTracker
from jtcon.core.decode import DecodeContext, handle_packet
from jtcon.core.hamdat import HAMDAT_DB_DEFAULT, HamdatLookup, load_state_file
from jtcon.core.messages import _CALLSIGN_RE
from jtcon.core.patterns import ACTIVATION_KEYWORDS, keyword_patterns, load_regex_patterns
from jtcon.core.protocol import _send_reply, try_parse_status
from jtcon.tui.render import _DIM, _bargraph, _col, _fmt_age, _header_line


_BUF_SIZE      = 100   # rolling buffer slots (two-digit index 00-99)
_MAX_OUTPUT_LINES = 5000   # output pane is trimmed when it grows past this…
_TRIM_OUTPUT_TO   = 4000   # …down to this many lines
_WHEEL_LINES      = 3      # lines per mouse-wheel notch (--mouse)
_STALE_REPLY_S = 120   # warn when replying to an entry last heard longer ago than this


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

    def __init__(self, config, sock, tx_sock):
        self.config = config
        self.sock = sock
        self.tx_sock = tx_sock

        # Data objects — all set to empty defaults; populated by _startup_then_udp().
        self.cty           = CtyDat()
        self.adif_log      = None
        self.json_fh       = None
        self.call_patterns = []
        self.msg_patterns  = []
        self.state_set     = set()
        self.hamdat        = None
        self.cycle_tracker = CycleTracker()
        self.alerted: dict = {}   # alert key -> monotonic time last heard

        self._running      = True
        self._fatal_msg    = None   # set by _fatal(); the app exits on next render
        self._exiting      = False
        self._udp_down     = False
        # Output pane: background threads queue lines in _pending; the UI thread
        # moves them into _lines (and the TextArea) just before each render.
        self._output_lock  = threading.Lock()
        self._pending: list = []
        self._lines:   list = []
        # Top line of the decode pane while scrolled back from the command line;
        # None means the pane follows new output.
        self._scroll_top: int | None = None
        self._transmitting = False
        self._dial_freq    = 0   # Hz; updated from WSJT-X Status messages
        self._de_call      = ""  # operator callsign; updated from WSJT-X Status messages

        self._adif_mtimes: dict  = {}
        self._line_count:  int   = 0
        self.proxy_dests:  list  = []
        self._cq_buffer:   dict  = {}   # idx (0-99) -> entry dict
        self._call_idx:    dict  = {}   # callsign -> idx; one slot per station
        self._next_idx:    int   = 0    # where to start looking for a free slot
        self._buf_lock           = threading.Lock()

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
        separator = Window(height=1, char="─", style="class:separator")
        status_window = Window(
            content=FormattedTextControl(self._get_status_text),
            height=1,
            style="class:status",
        )

        layout = Layout(
            HSplit([
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
        bar = _bargraph(self.cycle_tracker)
        ct  = self.cycle_tracker

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
        if self._udp_down:
            trx = ("class:status.tx", " UDP DOWN ")
        elif self._transmitting:
            trx = ("class:status.tx", " TX ")
        else:
            trx = ("class:status.rx", " RX ")

        de = f"  de={self._de_call}" if self._de_call else ""
        scroll = [("class:status.scroll", " SCROLL ")] if self._scroll_top is not None else []
        return [
            trx,
            *scroll,
            ("class:status", f" [{bar}] {parity}{stats}{de} "),
        ]

    # ── Command handler ───────────────────────────────────────────────────────

    def _handle_command(self, buffer) -> None:
        raw = buffer.text.strip()
        cmd = raw.lower()

        if not raw:
            return

        # Command output goes to the bottom of the pane; make sure it's visible.
        if self._scroll_top is not None:
            self._scroll_to_live()

        if cmd in ("quit", "exit", "q"):
            self._exit_app()

        elif cmd == "clear":
            with self._output_lock:
                self._pending.clear()
            self._lines = self._header().split("\n")
            self._set_output(follow=True)

        elif cmd and "list".startswith(cmd):
            self._cmd_list()

        elif re.match(r'^\d{1,2}$', raw):
            idx = int(raw)
            with self._buf_lock:
                entry = self._cq_buffer.get(idx)
            if entry:
                self._reply_to_entry(entry)
            else:
                self._append_line(f"[cmd] No CQ at index {idx:02d}.")

        elif _CALLSIGN_RE.match(raw):
            entry = self._find_callsign(raw.upper())
            if entry:
                self._reply_to_entry(entry)
            else:
                self._append_line(f"[cmd] {raw.upper()} not found in rolling buffer.")

        else:
            self._append_line(
                f"[cmd] Unknown: {raw!r}  "
                "(commands: quit, clear, list; or enter index 00-99 / callsign to reply)"
            )

    def _reply_to_entry(self, entry: dict) -> None:
        """Send a WSJT-X Reply for a rolling-buffer entry."""
        decoded   = entry.get('decoded')
        wsjt_addr = entry.get('wsjt_addr')
        callsign  = entry.get('callsign', '?')
        idx       = entry.get('idx', -1)
        if not decoded or not wsjt_addr:
            self._append_line(f"[cmd] No decode data for {callsign}.")
            return
        age = time.monotonic() - entry.get('last_seen', time.monotonic())
        if age > _STALE_REPLY_S:
            self._append_line(
                f"[cmd] Warning: {callsign} last heard {_fmt_age(age)} ago"
                " — WSJT-X may no longer have that decode."
            )
        try:
            _send_reply(self.tx_sock, wsjt_addr, decoded, log=self._append_line)
            self._append_line(
                f"[cmd] ► Reply sent to {callsign} [{idx:02d}]"
                f"  msg={decoded.get('message', '?')!r}"
                f"  → {wsjt_addr[0]}:{wsjt_addr[1]}"
            )
        except Exception as exc:
            self._append_line(f"[cmd] Reply to {callsign} failed: {exc}")

    def _find_callsign(self, callsign: str) -> dict | None:
        """Return the rolling-buffer entry for *callsign*, if any."""
        with self._buf_lock:
            idx = self._call_idx.get(callsign)
            return self._cq_buffer.get(idx) if idx is not None else None

    def _store_entry(self, callsign: str, meta: dict, lines: list, wsjt_addr) -> int:
        """
        Record a decode in the rolling buffer and return its index.

        Each callsign owns one slot.  A repeat decode keeps the station's index
        and refreshes the entry so replies use the newest decode.  A new station
        takes the next free slot, or evicts the least recently heard station.
        """
        now = time.monotonic()
        with self._buf_lock:
            idx = self._call_idx.get(callsign)
            if idx is not None:
                entry = self._cq_buffer[idx]
                entry['count'] += 1
            else:
                idx = self._alloc_slot()
                old = self._cq_buffer.get(idx)
                if old:
                    del self._call_idx[old['callsign']]
                self._call_idx[callsign] = idx
                entry = self._cq_buffer[idx] = {
                    'idx':        idx,
                    'callsign':   callsign,
                    'count':      1,
                    'first_seen': now,
                }
            entry.update({
                'decoded':      meta.get('decoded'),
                'wsjt_addr':    wsjt_addr,
                'needed_flags': meta.get('needed_flags', []),
                'match_flags':  meta.get('match_flags', []),
                'lines':        list(lines),
                'last_seen':    now,
            })
        return idx

    def _alloc_slot(self) -> int:
        """Pick a slot for a new station; caller holds _buf_lock."""
        for i in range(_BUF_SIZE):
            idx = (self._next_idx + i) % _BUF_SIZE
            if idx not in self._cq_buffer:
                self._next_idx = (idx + 1) % _BUF_SIZE
                return idx
        return min(self._cq_buffer.values(), key=lambda e: e['last_seen'])['idx']

    def _clear_buffer(self) -> int:
        """Empty the rolling buffer; return how many entries were dropped."""
        with self._buf_lock:
            n = len(self._cq_buffer)
            self._cq_buffer.clear()
            self._call_idx.clear()
            self._next_idx = 0
        return n

    def _cmd_list(self) -> None:
        """Print NEEDED/MATCH entries from the rolling buffer, most recently heard last."""
        with self._buf_lock:
            hits = sorted(
                (e for e in self._cq_buffer.values()
                 if e['needed_flags'] or e['match_flags']),
                key=lambda e: e['last_seen'],
            )
        if not hits:
            self._append_line("[list] No NEEDED/MATCH entries in rolling buffer.")
            return
        now = time.monotonic()
        self._append_line(f"[list] {len(hits)} NEEDED/MATCH entries (last heard at bottom) ——")
        for entry in hits:
            lines = entry['lines']
            # Keep the decode row aligned with the column header; the heard
            # stats go on the NEEDED/MATCH line below it.
            seen = _col(
                f"(last heard {_fmt_age(now - entry['last_seen'])} ago, ×{entry['count']})",
                _DIM, self.config.display.color,
            )
            self._append_line(f"{entry['idx']:02d}  {lines[0]}")
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
        return _header_line(self.config, has_adif=self.adif_log is not None,
                            has_hamdat=self.hamdat is not None)

    def _fatal(self, msg: str) -> None:
        """Log *msg*, stop, and close the TUI; *msg* is reprinted after exit."""
        self._append_line(msg)
        self._fatal_msg = msg
        self._running   = False
        self._app.invalidate()

    def _exit_app(self) -> None:
        """Exit the TUI (UI thread only); safe to call more than once."""
        self._running = False
        if not self._exiting:
            self._exiting = True
            self._app.exit()

    # ── UDP receive loop (runs in background thread) ──────────────────────────

    def _udp_loop(self) -> None:
        while self._running:
            try:
                ready, _, _ = select.select([self.sock], [], [], 0.2)
                if not ready:
                    continue
                data, wsjt_addr = self.sock.recvfrom(65535)
            except ConnectionResetError:
                continue   # Windows reports ICMP port-unreachable here; harmless
            except (OSError, ValueError) as exc:   # ValueError: socket closed
                if self._running:
                    self._udp_down = True
                    self._append_line(
                        f"[udp] Receive loop stopped: {exc} — no further decodes; restart jtcon."
                    )
                break

            # Forward packet to proxy destinations before any other processing.
            for dest in self.proxy_dests:
                try:
                    self.tx_sock.sendto(data, dest)
                except OSError:
                    pass

            # Hot-reload ADIF when any watched file changes.
            if self.adif_log and self._adif_mtimes:
                try:
                    changed = any(
                        p.stat().st_mtime != self._adif_mtimes.get(p)
                        for p in self.adif_log.paths
                    )
                    if changed:
                        self.adif_log = AdifLog(
                            self.adif_log.paths, self.cty, self.hamdat,
                            qsl_only=self.config.entity_needs_lotw,
                            log=self._append_line,
                        )
                        self._adif_mtimes = {
                            p: p.stat().st_mtime for p in self.adif_log.paths
                        }
                        self._append_line(
                            f"[adif] Reloaded {self.adif_log.total_qsos} QSOs"
                            f" from {len(self.adif_log.paths)} file(s)."
                        )
                        adif_change_script = self.config.actions.script_on_log_reload
                        if adif_change_script:
                            try:
                                proc = subprocess.Popen(
                                    [str(adif_change_script)]
                                    + [str(p) for p in self.adif_log.paths],
                                    stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT,
                                    text=True,
                                )
                                def _relay_output(p=proc):
                                    for line in p.stdout:
                                        self._append_line(
                                            f"[adif-change-script] {line.rstrip()}"
                                        )
                                    p.wait()
                                threading.Thread(
                                    target=_relay_output, daemon=True
                                ).start()
                            except Exception as exc:
                                self._append_line(f"[adif-change-script] {exc}")
                except OSError:
                    pass

            # Intercept Status messages to track TX/RX state and current band.
            status = try_parse_status(data)
            if status is not None:
                self._transmitting = status.get("transmitting", False)
                new_freq = status.get("dial_freq", 0)
                if new_freq and self._dial_freq and new_freq != self._dial_freq:
                    # Decodes from the old frequency can't be replied to usefully.
                    dropped = self._clear_buffer()
                    if dropped:
                        self._append_line(
                            f"[buffer] Dial frequency changed — cleared {dropped} entries."
                        )
                if new_freq:
                    self._dial_freq = new_freq
                de = status.get("de_call", "").strip()
                if de:
                    self._de_call = de
                self._app.invalidate()
                continue

            meta: dict = {}
            ctx = DecodeContext(
                config=self.config, cty=self.cty, adif_log=self.adif_log,
                json_fh=self.json_fh, call_patterns=self.call_patterns,
                msg_patterns=self.msg_patterns, state_set=self.state_set,
                hamdat=self.hamdat, cycle_tracker=self.cycle_tracker,
                alerted=self.alerted, dial_freq=self._dial_freq,
                de_call=self._de_call, log=self._append_line,
            )
            try:
                out_lines = handle_packet(data, ctx, wsjt_addr, meta)
            except Exception as exc:
                out_lines = [f"[error] packet handler: {exc}"]

            if meta.get('bell'):
                sys.stderr.write('\a')
                sys.stderr.flush()

            if out_lines:
                call = meta.get('callsign')
                is_cq   = meta.get('is_cq')
                is_hit  = meta.get('needed_flags') or meta.get('match_flags')
                if call and (is_cq or is_hit):
                    idx = self._store_entry(call.upper(), meta, out_lines, wsjt_addr)
                    out_lines = [f"{idx:02d}  {out_lines[0]}"] + out_lines[1:]
                else:
                    # Non-CQ with no hit (raw mode) — indent to keep columns aligned.
                    out_lines = [f"    {out_lines[0]}"] + out_lines[1:]

                for line in out_lines:
                    self._append_line(line)

                self._line_count += 1
                if self._line_count % 15 == 0:
                    self._append_line(self._header())

    # ── Startup (runs in background thread before UDP loop) ──────────────────

    def _startup_then_udp(self) -> None:
        """
        Load all data files, logging each step to the decode pane, then fall
        straight into the UDP receive loop.  Runs entirely in a background thread
        so the TUI is already visible when the messages appear.
        """
        log = self._append_line
        cfg = self.config
        for src in cfg.sources:
            log(f"[config] Loaded {src}")
        rules = ", ".join(w.name for w in cfg.wanted) or "none"
        log(f"[config] Wanted rules: {rules}")

        # ── cty.dat ───────────────────────────────────────────────────────────
        cty_path = cfg.inputs.cty
        stale = False
        if cty_path.exists():
            age_days = (time.time() - cty_path.stat().st_mtime) / 86400
            stale = age_days > CTY_MAX_AGE_DAYS
        if stale or not cty_path.exists():
            reason = f"older than {CTY_MAX_AGE_DAYS} days" if stale else "not found"
            log(f"[cty.dat] {reason} — downloading from {CTY_DAT_URL} …")
            try:
                download_cty(cty_path)
                log(f"[cty.dat] Saved to {cty_path}")
            except Exception as exc:
                if stale:
                    log(f"[cty.dat] Download failed: {exc} — using cached copy")
                else:
                    log(f"[cty.dat] Download failed: {exc} — entity lookup unavailable")

        cty = CtyDat()
        if cty_path.exists():
            try:
                cty.load(cty_path)
                log(f"[cty.dat] Loaded {len(cty._prefixes)} prefixes"
                    f" + {len(cty._exact)} exact callsigns")
            except Exception as exc:
                log(f"[cty.dat] Load error: {exc} — entity lookup unavailable")
        else:
            log("[cty.dat] File not found — entity lookup unavailable")
        self.cty = cty

        # ── hamdat ────────────────────────────────────────────────────────────
        states_rule = cfg.rule("states")
        if states_rule:
            self.state_set = set(states_rule.settings["states"])
            if states_rule.settings["file"]:
                try:
                    self.state_set |= load_state_file(states_rule.settings["file"])
                except OSError as exc:
                    self._fatal(f"[states] Cannot load state list: {exc}")
                    return
            log(f"[states] {len(self.state_set)} state(s):"
                f" {', '.join(sorted(self.state_set))}")

        db_path = cfg.inputs.hamdat
        if db_path.exists():
            try:
                self.hamdat = HamdatLookup(db_path)
                label = "Auto-detected" if db_path == HAMDAT_DB_DEFAULT else "Using"
                log(f"[hamdat] {label}: {db_path}")
            except Exception as exc:
                if states_rule:
                    self._fatal(f"[hamdat] Failed to open {db_path}: {exc}")
                    return
                log(f"[hamdat] Warning: found but could not open {db_path}: {exc}")
        elif states_rule:
            self._fatal(f"[states] hamdat database not found: {db_path}")
            return

        # ── ADIF ──────────────────────────────────────────────────────────────
        adif_paths: list[Path] = []
        for p in _find_wsjt_logs():
            log(f"[adif] Auto-discovered: {p}")
            adif_paths.append(p)
        for f in cfg.inputs.adif:
            p = f.resolve()
            if not p.exists():
                self._fatal(f"[adif] File not found: {p}")
                return
            if p not in adif_paths:
                adif_paths.append(p)

        if adif_paths:
            try:
                self.adif_log = AdifLog(adif_paths, self.cty, self.hamdat,
                                        qsl_only=cfg.entity_needs_lotw,
                                        log=log)
                for p in adif_paths:
                    log(f"[adif] Loaded: {p.name}")
                al = self.adif_log
                summary = (f"[adif] {al.total_qsos} QSOs across {len(adif_paths)} file(s)"
                           f" — {len(al.worked_calls)} calls,"
                           f" {len(al.worked_dxcc_all)} DXCC,"
                           f" {len(al.worked_cqz_all)} CQ zones worked")
                if cfg.entity_needs_lotw:
                    summary += (f"; {len(al.worked_dxcc)} DXCC,"
                                f" {len(al.worked_cqz)} CQ zones LoTW-confirmed")
                log(summary)
                for p in adif_paths:
                    try:
                        self._adif_mtimes[p] = p.stat().st_mtime
                    except OSError:
                        pass
            except Exception as exc:
                log(f"[adif] Failed to load: {exc}")
        else:
            log("[adif] No ADIF log found — NEEDED detection disabled")

        # ── Watchlists ────────────────────────────────────────────────────────
        try:
            for key, attr in (("callsigns", "call_patterns"), ("message", "msg_patterns")):
                spec = cfg.rule(key)
                if spec:
                    files = spec.settings["files"]
                    patterns = load_regex_patterns(files, log=log)
                    setattr(self, attr, patterns)
                    log(f"[{key}] {len(patterns)} pattern(s)"
                        f" from {', '.join(str(f) for f in files)}")
        except OSError as exc:
            self._fatal(f"[wanted] Cannot read pattern file: {exc}")
            return
        rules = [w.rule for w in cfg.wanted]
        self.msg_patterns.extend(keyword_patterns(rules))
        for key, word in ACTIVATION_KEYWORDS.items():
            if key in rules:
                log(f"[{key}] {word} pattern active")

        # ── JSON output ───────────────────────────────────────────────────────
        json_log = cfg.actions.json_log
        if json_log:
            try:
                self.json_fh = open(json_log, "a", encoding="utf-8")
                log(f"[save] Appending JSON records to: {json_log}")
            except OSError as exc:
                self._fatal(f"[save] Cannot open {json_log}: {exc}")
                return

        # ── UDP proxy destinations ────────────────────────────────────────────
        self.proxy_dests = [("127.0.0.1", port) for port in cfg.udp.proxy]
        if self.proxy_dests:
            targets = ", ".join(f"localhost:{p}" for _, p in self.proxy_dests)
            log(f"[proxy] Forwarding all packets to: {targets}")

        # ── Column header then ready banner ───────────────────────────────────
        log(self._header())
        scope = ("all decodes" if cfg.show_all else
                 "CQ + matched" if cfg.evaluate_all else "CQ calls only")
        log(f"[ready] Listening on {cfg.udp.host}:{cfg.udp.port}  ({scope})")

        # ── Hand off to UDP receive loop ──────────────────────────────────────
        self._udp_loop()

    # ── Entry point ───────────────────────────────────────────────────────────

    def _worker(self) -> None:
        """Background thread: startup, then the UDP loop.  Never dies silently."""
        try:
            self._startup_then_udp()
        except Exception as exc:
            self._fatal(f"[fatal] {type(exc).__name__}: {exc}")

    def run(self) -> int:
        """Run the TUI; return a process exit status."""
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()
        try:
            self._app.run()
        finally:
            self._running = False
            if self.json_fh:
                self.json_fh.close()
            if self.hamdat:
                self.hamdat.close()
        if self._fatal_msg:
            # The full-screen pane is gone; repeat the reason on the terminal.
            print(self._fatal_msg, file=sys.stderr)
            return 1
        return 0
