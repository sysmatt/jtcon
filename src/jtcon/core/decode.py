"""Evaluate one Decode: who is calling, and is the station NEEDED or a MATCH?"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from jtcon.config import Config
from jtcon.core.worked import WorkedIndex
from jtcon.core.alerts import _build_alert_msg, _send_alerts
from jtcon.core.bands import _5BWAS_BANDS, _WAS_STATES, _freq_to_band
from jtcon.core.cty import CtyDat
from jtcon.core.hamdat import HamdatLookup
from jtcon.core.messages import _GRID_RE, _directed_sender, parse_cq_message
from jtcon.core.patterns import _regex_matches
from jtcon.core.protocol import Decode, mode_name


_ALERT_REPEAT_S = 3600  # re-alert a station after it has gone unheard this long


# ══════════════════════════════════════════════════════════════════════════════
#  Evaluation
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class DecodeContext:
    """Everything evaluate() needs besides the decode itself."""
    config:        Config
    cty:           CtyDat
    adif_log:      WorkedIndex | None
    json_fh:       object
    call_patterns: list
    msg_patterns:  list
    state_set:     set
    hamdat:        HamdatLookup | None
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
    decoded:       Decode
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
    new_alert:     bool = False   # first alert for this station (rings the bell)


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
    cfg, adif_log = ctx.config, ctx.adif_log
    checkable = r.is_cq or cfg.evaluate_all

    needed: list = []
    if adif_log and checkable and r.entity_info:
        needed = adif_log.needed_flags(r.entity_info, cfg)

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
        if cfg.wants("was"):
            if st not in adif_log.worked_states_lotw:
                needed.append(f"NEW-WAS({st})")
        elif cfg.wants("5bwas"):
            band = ctx.band
            if band in _5BWAS_BANDS:
                if band not in adif_log.worked_states_bands_lotw.get(st, set()):
                    needed.append(f"NEW-5BWAS({st}/{band})")

    # Worked-lag-days suppression
    lag = cfg.filters.suppress_worked_within_days
    if (adif_log and r.callsign and lag >= 0
            and r.callsign in adif_log.last_worked):
        elapsed = (datetime.now(timezone.utc).date() - adif_log.last_worked[r.callsign]).days
        if elapsed <= lag:
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
    actions = ctx.config.actions
    if actions.script_on_wanted or actions.ntfy_topic:
        _send_alerts(
            _build_alert_msg(r.callsign, r.entity_info, r.needed_flags, r.match_flags),
            actions,
            log=ctx.log,
        )
    return True


def _write_json(ctx: DecodeContext, r: DecodeResult) -> None:
    decoded = r.decoded
    ei      = r.entity_info
    record = {
        "wall_utc":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period_utc":     r.period_utc,
        "snr_db":         decoded.snr,
        "dt_s":           round(decoded.dt, 2),
        "df_hz":          decoded.df,
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


def debug_text(data: bytes, schema: int, d: Decode) -> str:
    """The display.debug dump of one Decode datagram."""
    return (
        f"[debug] schema={schema}  len={len(data)}\n"
        f"[debug] hex: {data.hex()}\n"
        f"[debug] id={d.id!r}  new={d.new}  "
        f"time_ms={d.time_ms}  snr={d.snr}\n"
        f"[debug] dt={d.dt:.3f}  df={d.df}  "
        f"mode={d.mode!r}  message={d.message!r}\n"
        f"[debug] low_conf={d.low_conf}  off_air={d.off_air}"
    )


def evaluate(decoded: Decode, ctx: DecodeContext) -> DecodeResult | None:
    """
    Identify and classify one decode, fire alerts and the JSON log.  Returns
    None when the decode isn't shown: a non-CQ message, unless the config shows
    all decodes or (with scope "all") it has a hit.
    """
    cfg      = ctx.config
    msg_text = decoded.message.strip()
    is_cq    = msg_text.upper().startswith("CQ")

    if not is_cq and not cfg.show_all and not cfg.evaluate_all:
        return None

    time_ms = decoded.time_ms
    hh = (time_ms // 3_600_000) % 24
    mm = (time_ms %  3_600_000) // 60_000
    ss = (time_ms %     60_000) //  1_000
    r = DecodeResult(decoded=decoded, mode=mode_name(decoded), msg_text=msg_text,
                     is_cq=is_cq, period_utc=f"{hh:02d}{mm:02d}{ss:02d}z")

    # ── Identify the station ──────────────────────────────────────────────────
    if is_cq:
        r.callsign, r.grid, r.modifier = parse_cq_message(msg_text)
        if not r.grid:
            gm = _GRID_RE.search(msg_text)
            if gm:
                r.grid = gm.group(1).upper()
    elif cfg.evaluate_all:
        r.callsign = _directed_sender(msg_text, ctx.de_call)
    r.entity_info = _entity_info(ctx.cty, r.callsign)

    _classify(ctx, r)

    # Non-CQ: only shown when showing all, or there is an actual hit.
    if not is_cq and not cfg.show_all and not (r.needed_flags or r.match_flags):
        return None

    r.new_alert = _maybe_alert(ctx, r)
    if ctx.json_fh and is_cq:
        _write_json(ctx, r)
    return r
