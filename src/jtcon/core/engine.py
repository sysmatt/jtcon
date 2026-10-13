"""The Engine: inputs, the WSJT-X UDP link, evaluation, the station table and actions.

Front ends subscribe to its events (core/events.py) and call its commands.  It
runs on asyncio; anything slow (cty.dat download, loading logs) runs in a worker
thread so the front end stays responsive.  Subscribers are always called on the
event loop's thread.
"""

import asyncio
import collections
import subprocess
import threading
import time

from jtcon.config import Config
from jtcon.core.adif import _find_wsjt_logs
from jtcon.core.cty import CTY_DAT_URL, CTY_MAX_AGE_DAYS, CtyDat, download_cty
from jtcon.core.cycle import CycleTracker
from jtcon.core.decode import DecodeContext, debug_text, evaluate
from jtcon.core.events import (Fatal, InputReloaded, LogLine, Ready, SpotEvent,
                               StatusChanged, UdpDown)
from jtcon.core.hamdat import HAMDAT_DB_DEFAULT, HamdatLookup
from jtcon.core.inputs import AdifInput, PatternInput, StatesInput
from jtcon.core.logbook import qso_from_logged
from jtcon.core.patterns import ACTIVATION_KEYWORDS, keyword_patterns
from jtcon.core.protocol import (Close, Decode, LoggedAdif, QsoLogged, Status, encode_highlight,
                                 encode_reply, mode_name, parse_message)
from jtcon.core.stations import StationTable, fmt_age


STALE_REPLY_S  = 120   # warn when replying to a station last heard longer ago than this
INPUT_CHECK_S  = 1.0   # how often logs and watchlists are checked for changes


def _rgb(hex_color: str) -> tuple:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, engine: "Engine"):
        self.engine = engine

    def datagram_received(self, data, addr):
        self.engine.datagram_received(data, addr)

    def error_received(self, exc):
        # Windows reports ICMP port-unreachable for earlier sends here; harmless.
        if not isinstance(exc, ConnectionResetError):
            self.engine.log(f"[udp] {exc}")

    def connection_lost(self, exc):
        self.engine.connection_lost(exc)


class Highlights:
    """Callsigns we have coloured in WSJT-X, so they can be cleared again."""

    def __init__(self, send, background: tuple, foreground: tuple):
        self._send = send
        self._colors = (background, foreground)
        self.active: dict = {}       # callsign -> (client id, WSJT-X address)

    def set(self, callsign: str, client_id: str, addr) -> None:
        if self.active.get(callsign) == (client_id, addr):
            return
        self.active[callsign] = (client_id, addr)
        self._send(encode_highlight(client_id, callsign, *self._colors), addr)

    def clear(self, callsign: str) -> None:
        where = self.active.pop(callsign, None)
        if where:
            self._send(encode_highlight(where[0], callsign, None, None), where[1])

    def clear_all(self) -> None:
        for callsign in list(self.active):
            self.clear(callsign)

    def forget(self, client_id: str) -> None:
        """WSJT-X *client_id* closed; its highlights went with it."""
        for callsign, (cid, _) in list(self.active.items()):
            if cid == client_id:
                del self.active[callsign]


class Engine:
    def __init__(self, config: Config, sock, tx_sock):
        self.config  = config
        self.sock    = sock         # bound UDP socket (WSJT-X → us)
        self.tx_sock = tx_sock      # replies, highlights, proxy forwarding

        self.cty           = CtyDat()
        self.logbook       = None     # Logbook: every QSO, merged across sources
        self.adif_log      = None     # WorkedIndex built from the logbook
        self.adif_input    = None
        self.inputs: list  = []       # inputs watched for changes
        self.hamdat        = None
        self.json_fh       = None
        self.call_patterns = []
        self.msg_patterns  = []
        self.keyword_patterns = []    # pota/sota/iota; always among msg_patterns
        self.state_set     = set()
        self.proxy_dests   = []
        self.cycle_tracker = CycleTracker()
        self.alerted: dict = {}       # alert key -> monotonic time last heard
        self.stations      = StationTable()
        self.highlights    = Highlights(self._send, _rgb(config.actions.highlight_background),
                                        _rgb(config.actions.highlight_foreground))

        self.dial_freq    = 0         # Hz, from WSJT-X Status messages
        self.de_call      = ""        # operator callsign, from Status messages
        self.transmitting = False
        self.udp_down     = False

        self._subscribers: list = []
        self._loop        = None
        self._loop_thread = None
        self._transport   = None
        self._stopped     = None
        self._reloading   = False
        self._held        = collections.deque()   # datagrams waiting for a reload

    # ── Events ────────────────────────────────────────────────────────────────

    def subscribe(self, callback) -> None:
        """Call *callback(event)* for every event, on the event loop's thread."""
        self._subscribers.append(callback)

    def _emit(self, event) -> None:
        if self._loop is not None and threading.get_ident() != self._loop_thread:
            self._loop.call_soon_threadsafe(self._emit, event)
            return
        for callback in self._subscribers:
            callback(event)

    def log(self, text: str) -> None:
        """Send a LogLine; safe from any thread."""
        self._emit(LogLine(text))

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Load the inputs, then serve WSJT-X until stop()."""
        self._loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        self._stopped = asyncio.Event()
        if not await asyncio.to_thread(self._load_inputs):
            return
        cfg = self.config
        scope = ("all decodes" if cfg.show_all else
                 "CQ + matched" if cfg.evaluate_all else "CQ calls only")
        self._emit(Ready(f"[ready] Listening on {cfg.udp.host}:{cfg.udp.port}  ({scope})"))
        self._transport, _ = await self._loop.create_datagram_endpoint(
            lambda: _Protocol(self), sock=self.sock)
        checker = asyncio.create_task(self._check_inputs_periodically())
        try:
            await self._stopped.wait()
        finally:
            checker.cancel()

    def stop(self) -> None:
        if self._stopped is not None:
            self._stopped.set()

    def close(self) -> None:
        """Clear our highlights in WSJT-X and release files.  Call once, after run()."""
        self.highlights.clear_all()
        if self.json_fh:
            self.json_fh.close()
            self.json_fh = None
        if self.hamdat:
            self.hamdat.close()
        if self._transport:
            self._transport.close()   # also closes self.sock

    def _fatal(self, text: str) -> bool:
        self._emit(Fatal(text))
        return False

    # ── Startup: load every input (worker thread) ─────────────────────────────

    def _load_inputs(self) -> bool:
        """Load cty.dat, hamdat, logs and watchlists.  False after a fatal error."""
        log = self.log
        cfg = self.config
        for src in cfg.sources:
            log(f"[config] Loaded {src}")
        log(f"[config] Wanted rules: {', '.join(w.name for w in cfg.wanted) or 'none'}")

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
        if cty_path.exists():
            try:
                self.cty.load(cty_path)
                log(f"[cty.dat] Loaded {len(self.cty._prefixes)} prefixes"
                    f" + {len(self.cty._exact)} exact callsigns")
            except Exception as exc:
                log(f"[cty.dat] Load error: {exc} — entity lookup unavailable")
        else:
            log("[cty.dat] File not found — entity lookup unavailable")

        # ── hamdat and the states rule ────────────────────────────────────────
        states_rule = cfg.rule("states")
        if states_rule:
            self.state_set = set(states_rule.settings["states"])
            if states_rule.settings["file"]:
                inp = StatesInput(self.state_set, states_rule.settings["file"])
                try:
                    self.state_set = inp.load()
                except OSError as exc:
                    return self._fatal(f"[states] Cannot load state list: {exc}")
                self.inputs.append(inp)
            log(f"[states] {StatesInput.describe(self.state_set)}")

        db_path = cfg.inputs.hamdat
        if db_path.exists():
            try:
                self.hamdat = HamdatLookup(db_path)
                label = "Auto-detected" if db_path == HAMDAT_DB_DEFAULT else "Using"
                log(f"[hamdat] {label}: {db_path}")
            except Exception as exc:
                if states_rule:
                    return self._fatal(f"[hamdat] Failed to open {db_path}: {exc}")
                log(f"[hamdat] Warning: found but could not open {db_path}: {exc}")
        elif states_rule:
            return self._fatal(f"[states] hamdat database not found: {db_path}")

        # ── ADIF ──────────────────────────────────────────────────────────────
        adif_paths: list = []
        for p in _find_wsjt_logs():
            log(f"[adif] Auto-discovered: {p}")
            adif_paths.append(p)
        for f in cfg.inputs.adif:
            p = f.resolve()
            if not p.exists():
                return self._fatal(f"[adif] File not found: {p}")
            if p not in adif_paths:
                adif_paths.append(p)

        if adif_paths:
            inp = AdifInput(adif_paths, self.cty, self.hamdat,
                            qsl_only=cfg.entity_needs_lotw, log=log)
            try:
                self.logbook, self.adif_log = inp.load()
                for p in adif_paths:
                    log(f"[adif] Loaded: {p.name}")
                al = self.adif_log
                summary = (f"[adif] {self._qso_count()} across {len(adif_paths)} file(s)"
                           f" — {len(al.worked_calls)} calls,"
                           f" {len(al.worked_dxcc_all)} DXCC,"
                           f" {len(al.worked_cqz_all)} CQ zones worked")
                if cfg.entity_needs_lotw:
                    summary += (f"; {len(al.worked_dxcc)} DXCC,"
                                f" {len(al.worked_cqz)} CQ zones LoTW-confirmed")
                log(summary)
                self.adif_input = inp
                self.inputs.append(inp)
            except Exception as exc:
                log(f"[adif] Failed to load: {exc}")
        else:
            log("[adif] No ADIF log found — NEEDED detection disabled")

        # ── Watchlists ────────────────────────────────────────────────────────
        rules = [w.rule for w in cfg.wanted]
        self.keyword_patterns = keyword_patterns(rules)
        self.msg_patterns = list(self.keyword_patterns)
        for key in ("callsigns", "message"):
            spec = cfg.rule(key)
            if spec:
                inp = PatternInput(key, spec.settings["files"], log=log)
                try:
                    patterns = inp.load()
                except OSError as exc:
                    return self._fatal(f"[wanted] Cannot read pattern file: {exc}")
                self.inputs.append(inp)
                self._set_patterns(key, patterns)
                log(f"[{key}] {inp.describe(patterns)}")
        for key, word in ACTIVATION_KEYWORDS.items():
            if key in rules:
                log(f"[{key}] {word} pattern active")

        # ── Actions ───────────────────────────────────────────────────────────
        json_log = cfg.actions.json_log
        if json_log:
            try:
                self.json_fh = open(json_log, "a", encoding="utf-8")
                log(f"[save] Appending JSON records to: {json_log}")
            except OSError as exc:
                return self._fatal(f"[save] Cannot open {json_log}: {exc}")
        if cfg.actions.highlight:
            log(f"[highlight] Wanted stations are highlighted in WSJT-X"
                f" ({cfg.actions.highlight_background} on {cfg.actions.highlight_foreground})")

        self.proxy_dests = [("127.0.0.1", port) for port in cfg.udp.proxy]
        if self.proxy_dests:
            targets = ", ".join(f"localhost:{p}" for _, p in self.proxy_dests)
            log(f"[proxy] Forwarding all packets to: {targets}")
        return True

    # ── Input change detection and reload ─────────────────────────────────────

    def _qso_count(self) -> str:
        lb = self.logbook
        text = f"{len(lb) - lb.unsaved} QSOs"
        if lb.merged:
            text += f" ({lb.merged} duplicates merged)"
        return text

    def _set_patterns(self, key: str, patterns: list) -> None:
        if key == "callsigns":
            self.call_patterns = patterns
        else:
            self.msg_patterns = patterns + self.keyword_patterns

    def check_inputs(self) -> None:
        """Start a reload if any log or watchlist changed.  Decodes received
        meanwhile are held and evaluated against the new data once it's loaded."""
        if self._reloading:
            return
        changed = [inp for inp in self.inputs if inp.changed()]
        if changed:
            self._reloading = True
            asyncio.ensure_future(self._reload(changed))

    async def _reload(self, changed: list) -> None:
        try:
            for inp in changed:
                try:
                    value = await asyncio.to_thread(inp.load)
                except Exception as exc:
                    inp.skip()            # don't retry a broken file forever
                    self.log(f"[{inp.name}] Reload failed: {exc} — keeping the previous data")
                    continue
                self._apply_reload(inp, value)
                self._emit(InputReloaded(inp.name))
        finally:
            self._reloading = False
            while self._held and not self._reloading:
                self._handle_datagram(*self._held.popleft())

    def _apply_reload(self, inp, value) -> None:
        if inp is self.adif_input:
            self.logbook, self.adif_log = value
            unsaved = (f", plus {self.logbook.unsaved} logged but not in the files yet"
                       if self.logbook.unsaved else "")
            self.log(f"[adif] Reloaded {self._qso_count()} from {len(inp.paths)} file(s)"
                     f"{unsaved}.")
            self._run_log_reload_script(inp.paths)
        elif isinstance(inp, StatesInput):
            self.state_set = value
            self.log(f"[states] Reloaded {inp.describe(value)}")
        else:
            self._set_patterns(inp.name, value)
            self.log(f"[{inp.name}] Reloaded {inp.describe(value)}")

    async def _check_inputs_periodically(self) -> None:
        while True:
            await asyncio.sleep(INPUT_CHECK_S)
            self.check_inputs()

    def _run_log_reload_script(self, paths) -> None:
        script = self.config.actions.script_on_log_reload
        if not script:
            return
        try:
            proc = subprocess.Popen(
                [str(script)] + [str(p) for p in paths],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
            )
        except Exception as exc:
            self.log(f"[on_log_reload] {exc}")
            return

        def relay():
            for line in proc.stdout:
                self.log(f"[on_log_reload] {line.rstrip()}")
            proc.wait()
        threading.Thread(target=relay, daemon=True).start()

    # ── UDP ───────────────────────────────────────────────────────────────────

    def _send(self, data: bytes, addr) -> None:
        try:
            self.tx_sock.sendto(data, addr)
        except OSError as exc:
            self.log(f"[udp] Send to {addr[0]}:{addr[1]} failed: {exc}")

    def connection_lost(self, exc) -> None:
        if self._stopped is not None and not self._stopped.is_set():
            self.udp_down = True
            self._emit(UdpDown(f"[udp] Receive loop stopped: {exc} —"
                               " no further decodes; restart jtcon."))

    def datagram_received(self, data: bytes, addr) -> None:
        # Forward to proxy destinations before any other processing.
        for dest in self.proxy_dests:
            try:
                self.tx_sock.sendto(data, dest)
            except OSError:
                pass
        self.check_inputs()
        if self._reloading:
            self._held.append((data, addr))
        else:
            self._handle_datagram(data, addr)

    def _handle_datagram(self, data: bytes, addr) -> None:
        parsed = parse_message(data)
        if parsed is None:
            return
        schema, msg = parsed
        try:
            if isinstance(msg, Decode):
                self._on_decode(msg, data, schema, addr)
            elif isinstance(msg, Status):
                self._on_status(msg)
            elif isinstance(msg, (QsoLogged, LoggedAdif)):
                self._on_logged(msg)
            elif isinstance(msg, Close):
                self.highlights.forget(msg.id)
                self.log(f"[wsjt-x] {msg.id} closed")
        except Exception as exc:
            self.log(f"[error] packet handler: {exc}")

    def _on_status(self, st: Status) -> None:
        self.transmitting = st.transmitting
        if st.dial_freq and self.dial_freq and st.dial_freq != self.dial_freq:
            # Decodes from the old frequency can't be replied to usefully, and
            # whether a station is wanted can depend on the band.
            dropped = self.stations.clear()
            self.highlights.clear_all()
            if dropped:
                self.log(f"[buffer] Dial frequency changed — cleared {dropped} entries.")
        if st.dial_freq:
            self.dial_freq = st.dial_freq
        if st.de_call.strip():
            self.de_call = st.de_call.strip()
        self._emit(StatusChanged(st))

    def _on_logged(self, msg) -> None:
        if isinstance(msg, QsoLogged) and msg.dx_call:
            self.log(f"[qso] Logged {msg.dx_call}")
            self.highlights.clear(msg.dx_call.upper())
            if self.adif_input:
                # Worked from now on, even before the log file is written; the
                # file's record is merged with this one when it's reloaded.
                q = qso_from_logged(msg, self.cty, self.hamdat)
                self.adif_input.extra.append(q)
                self.adif_log.add(self.logbook.add(q))
        # Usually the log file is already written; if not, the periodic check
        # catches it.  Nothing is read until the file's mtime changes.
        self.check_inputs()

    def context(self) -> DecodeContext:
        return DecodeContext(
            config=self.config, cty=self.cty, adif_log=self.adif_log,
            json_fh=self.json_fh, call_patterns=self.call_patterns,
            msg_patterns=self.msg_patterns, state_set=self.state_set,
            hamdat=self.hamdat, alerted=self.alerted, dial_freq=self.dial_freq,
            de_call=self.de_call, log=self.log,
        )

    def _on_decode(self, d: Decode, data: bytes, schema: int, addr) -> None:
        if self.config.display.debug:
            self.log(debug_text(data, schema, d))
        self.cycle_tracker.record(d.time_ms, d.message.strip().upper().startswith("CQ"),
                                  d.new, mode_name(d))
        ctx = self.context()
        r = evaluate(d, ctx)
        if r is None:
            return
        is_hit = bool(r.needed_flags or r.match_flags)
        station = None
        if r.callsign and (r.is_cq or is_hit):
            station = self.stations.store(r.callsign, d, addr, r.needed_flags, r.match_flags)
            if self.config.actions.highlight:
                if is_hit:
                    self.highlights.set(station.callsign, d.id, addr)
                else:
                    self.highlights.clear(station.callsign)
        self._emit(SpotEvent(r, ctx, station))

    # ── Commands ──────────────────────────────────────────────────────────────

    def reply(self, station) -> None:
        """Send WSJT-X a Reply for *station*'s newest decode."""
        if station.decode is None or station.addr is None:
            self.log(f"[cmd] No decode data for {station.callsign}.")
            return
        age = time.monotonic() - station.last_seen
        if age > STALE_REPLY_S:
            self.log(f"[cmd] Warning: {station.callsign} last heard {fmt_age(age)} ago"
                     " — WSJT-X may no longer have that decode.")
        try:
            self.tx_sock.sendto(encode_reply(station.decode), station.addr)
        except OSError as exc:
            self.log(f"[cmd] Reply to {station.callsign} failed: {exc}")
            return
        self.log(f"[cmd] ► Reply sent to {station.callsign} [{station.idx:02d}]"
                 f"  msg={station.decode.message!r}"
                 f"  → {station.addr[0]}:{station.addr[1]}")

    def clear_buffer(self) -> int:
        self.highlights.clear_all()
        return self.stations.clear()

