"""Turn one Decode packet into a classified, rendered result."""

import argparse
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from jtcon.core.adif import AdifLog
from jtcon.core.alerts import _build_alert_msg, _send_alerts
from jtcon.core.bands import _5BWAS_BANDS, _WAS_STATES, _freq_to_band
from jtcon.core.cty import CtyDat
from jtcon.core.cycle import CycleTracker
from jtcon.core.hamdat import HamdatLookup
from jtcon.core.messages import _GRID_RE, _directed_sender, parse_cq_message
from jtcon.core.patterns import _regex_matches
from jtcon.core.protocol import _MODE_NAMES, _parse_decode_packet
# v3 phase 3 moves rendering out of the core; until then handle_packet renders.
from jtcon.tui.render import _render


_ALERT_REPEAT_S = 3600  # re-alert a station after it has gone unheard this long


# ══════════════════════════════════════════════════════════════════════════════
#  Packet handler — returns list of display lines (no printing)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class DecodeContext:
    """Everything handle_packet needs besides the packet itself."""
    args:          argparse.Namespace
    cty:           CtyDat
    adif_log:      AdifLog | None
    json_fh:       object
    call_patterns: list
    msg_patterns:  list
    state_set:     set
    hamdat:        HamdatLookup | None
    cycle_tracker: CycleTracker | None
    alerted:       dict          # alert key -> monotonic time last heard
    dial_freq:     int = 0       # Hz, from WSJT-X Status messages
    de_call:       str = ""      # operator callsign, from WSJT-X Status messages
    log:           object = None

    @property
    def band(self) -> str | None:
        return _freq_to_band(self.dial_freq)


@dataclass
class DecodeResult:
    """One decoded message after station lookup and NEEDED/MATCH checks."""
    decoded:       dict
    mode:          str
    msg_text:      str
    is_cq:         bool
    period_utc:    str
    callsign:      str | None = None
    grid:          str | None = None
    modifier:      str | None = None
    entity_info:   dict = field(default_factory=dict)
    station_state: str | None = None
    needed_flags:  list = field(default_factory=list)
    match_flags:   list = field(default_factory=list)


def _entity_info(cty: CtyDat, callsign: str | None) -> dict:
    raw = cty.lookup(callsign) if callsign else None
    if not raw:
        return {}
    return {
        "primary_prefix": raw.get("primary_prefix", "?"),
        "entity":         raw.get("country",        "?"),
        "cq_zone":        raw.get("cqz",            "?"),
        "itu_zone":       raw.get("ituz",           "?"),
        "continent":      raw.get("continent",      "?"),
    }


def _classify(ctx: DecodeContext, r: DecodeResult) -> None:
    """Fill r.station_state, r.needed_flags and r.match_flags."""
    args, adif_log = ctx.args, ctx.adif_log
    checkable = r.is_cq or args.all_decodes

    needed: list = []
    if adif_log and checkable and r.entity_info:
        needed = adif_log.needed_flags(r.entity_info, args)

    match: list = []
    if ctx.call_patterns and r.callsign:
        match.extend(f"CALL:{m}" for m in _regex_matches(r.callsign, ctx.call_patterns))
    if ctx.msg_patterns and r.msg_text:
        match.extend(f"MSG:{m}" for m in _regex_matches(r.msg_text, ctx.msg_patterns))
    if ctx.hamdat and r.callsign:
        r.station_state = ctx.hamdat.state(r.callsign)
        if ctx.state_set and r.station_state in ctx.state_set:
            match.append(f"STATE:{r.station_state}")

    # WAS / 5BWAS
    st = r.station_state
    if adif_log and checkable and st in _WAS_STATES:
        if args.need_was:
            if st not in adif_log.worked_states_lotw:
                needed.append(f"NEW-WAS({st})")
        elif args.need_5bwas:
            band = ctx.band
            if band in _5BWAS_BANDS:
                if band not in adif_log.worked_states_bands_lotw.get(st, set()):
                    needed.append(f"NEW-5BWAS({st}/{band})")

    # Worked-lag-days suppression
    if (adif_log and r.callsign and args.worked_lag_days >= 0
            and r.callsign in adif_log.last_worked):
        elapsed = (datetime.now(timezone.utc).date() - adif_log.last_worked[r.callsign]).days
        if elapsed <= args.worked_lag_days:
            needed, match = [], []

    r.needed_flags, r.match_flags = needed, match


def _maybe_alert(ctx: DecodeContext, r: DecodeResult) -> bool:
    """
    Fire external alerts for a NEEDED/MATCH hit; return True if this is a new alert.

    A station re-alerts only after it has gone unheard for _ALERT_REPEAT_S.
    """
    if not (r.needed_flags or r.match_flags):
        return False
    key  = (r.callsign, frozenset(r.needed_flags + r.match_flags))
    now  = time.monotonic()
    last = ctx.alerted.get(key)
    ctx.alerted[key] = now
    if last is not None and now - last < _ALERT_REPEAT_S:
        return False
    if len(ctx.alerted) > 5000:
        for k in [k for k, t in ctx.alerted.items() if now - t >= _ALERT_REPEAT_S]:
            del ctx.alerted[k]
    if ctx.args.alert_command or ctx.args.alert_ntfy:
        _send_alerts(
            _build_alert_msg(r.callsign, r.entity_info, r.needed_flags, r.match_flags),
            ctx.args,
            log=ctx.log,
        )
    return True


def _write_json(ctx: DecodeContext, r: DecodeResult) -> None:
    decoded = r.decoded
    ei      = r.entity_info
    record = {
        "wall_utc":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period_utc":     r.period_utc,
        "snr_db":         decoded["snr"],
        "dt_s":           round(decoded["dt"], 2),
        "df_hz":          decoded["df"],
        "mode":           r.mode,
        "message":        r.msg_text,
        "callsign":       r.callsign,
        "modifier":       r.modifier,
        "gridsquare":     r.grid,
        "primary_prefix": ei.get("primary_prefix"),
        "entity":         ei.get("entity"),
        "cq_zone":        ei.get("cq_zone"),
        "itu_zone":       ei.get("itu_zone"),
        "continent":      ei.get("continent"),
        "needed":         r.needed_flags if ctx.adif_log else None,
    }
    ctx.json_fh.write(json.dumps(record) + "\n")
    ctx.json_fh.flush()


def handle_packet(
    data:      bytes,
    ctx:       DecodeContext,
    wsjt_addr: tuple,
    out_meta:  dict | None = None,
) -> list:
    """
    Decode one UDP datagram and return a list of formatted display strings.
    Never prints directly; caller appends lines to the TUI output pane.

    If *out_meta* is provided it is populated with:
      'decoded'      — raw parsed decode dict (for sending replies)
      'callsign'     — resolved station callsign or None
      'is_cq'        — True for CQ messages
      'needed_flags' / 'match_flags' — NEEDED / MATCH hits
      'is_needed'    — True when the contact is flagged NEEDED
      'wsjt_addr'    — source address of the packet
      'bell'         — True on the first alert for this station
    """
    parsed = _parse_decode_packet(data)
    if parsed is None:
        return []
    schema, decoded = parsed
    args  = ctx.args
    lines = []

    if args.debug:
        lines.append(
            f"[debug] schema={schema}  len={len(data)}\n"
            f"[debug] hex: {data.hex()}\n"
            f"[debug] id={decoded['id']!r}  new={decoded['new']}  "
            f"time_ms={decoded['time_ms']}  snr={decoded['snr']}\n"
            f"[debug] dt={decoded['dt']:.3f}  df={decoded['df']}  "
            f"mode={decoded['mode']!r}  message={decoded['message']!r}\n"
            f"[debug] low_conf={decoded['low_conf']}  off_air={decoded['off_air']}"
        )

    msg_text = decoded.get("message", "").strip()
    is_cq    = msg_text.upper().startswith("CQ")
    mode     = _MODE_NAMES.get(decoded["mode"], decoded["mode"]) or "?"

    if ctx.cycle_tracker is not None:
        ctx.cycle_tracker.record(decoded["time_ms"], is_cq, decoded["new"], mode)

    if not is_cq and not args.raw and not args.all_decodes:
        return lines

    time_ms = decoded["time_ms"]
    hh = (time_ms // 3_600_000) % 24
    mm = (time_ms %  3_600_000) // 60_000
    ss = (time_ms %     60_000) //  1_000
    r = DecodeResult(decoded=decoded, mode=mode, msg_text=msg_text, is_cq=is_cq,
                     period_utc=f"{hh:02d}{mm:02d}{ss:02d}z")

    # ── Identify the station ──────────────────────────────────────────────────
    if is_cq:
        r.callsign, r.grid, r.modifier = parse_cq_message(msg_text)
        if not r.grid:
            gm = _GRID_RE.search(msg_text)
            if gm:
                r.grid = gm.group(1).upper()
    elif args.all_decodes:
        r.callsign = _directed_sender(msg_text, ctx.de_call)
    r.entity_info = _entity_info(ctx.cty, r.callsign)

    _classify(ctx, r)

    # Non-CQ: only proceed to display if --raw or there is an actual hit.
    if not is_cq and not args.raw and not (r.needed_flags or r.match_flags):
        return lines

    is_new_alert = _maybe_alert(ctx, r)
    lines.extend(_render(ctx, r))

    if ctx.json_fh and is_cq:
        _write_json(ctx, r)

    if out_meta is not None:
        out_meta['decoded']      = decoded
        out_meta['callsign']     = r.callsign
        out_meta['is_cq']        = is_cq
        out_meta['needed_flags'] = r.needed_flags
        out_meta['match_flags']  = r.match_flags
        out_meta['is_needed']    = bool(r.needed_flags)
        out_meta['wsjt_addr']    = wsjt_addr
        out_meta['bell']         = is_new_alert

    return lines
