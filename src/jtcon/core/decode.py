"""Evaluate one Decode: who is calling, and is the station WANTED (and why)?"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from jtcon.config import Config
from jtcon.core.alerts import _build_alert_msg, _send_alerts
from jtcon.core.bands import _freq_to_band
from jtcon.core.calls import CallIndex
from jtcon.core.cty import CtyDat
from jtcon.core.hamdat import HamdatLookup
from jtcon.core.logbook import Logbook
from jtcon.core.messages import _GRID_RE, _directed_sender, base_call, parse_cq_message
from jtcon.core.protocol import Decode, mode_name
from jtcon.core.wanted import Spot, evaluate_rules


_ALERT_REPEAT_S = 3600  # re-alert a station after it has gone unheard this long


# ══════════════════════════════════════════════════════════════════════════════
#  Evaluation
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class DecodeContext:
    """Everything evaluate() needs besides the decode itself."""
    config:        Config
    cty:           CtyDat
    logbook:       Logbook | None     # None: no log found; award rules are off
    calls:         CallIndex | None   # per-call facts for the display
    rules:         list               # wanted rule instances
    json_fh:       object
    hamdat:        HamdatLookup | None
    alerted:       dict          # alert key -> monotonic time last heard
    dial_freq:     int = 0       # Hz, from WSJT-X Status messages
    de_call:       str = ""      # operator callsign, from WSJT-X Status messages
    log:           object = None
    ignored:       object = frozenset()   # base calls never shown (a set or dict)

    @property
    def band(self) -> str | None:
        return _freq_to_band(self.dial_freq)


@dataclass
class DecodeResult:
    """One decoded message after station lookup and the wanted rules."""
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
    checked:       bool = False   # the rules ran (a CQ, or any decode with scope "all")
    hits:          list = field(default_factory=list)   # every Hit, for status columns
    wanted:        list = field(default_factory=list)   # Hits that make it wanted
    new_alert:     bool = False   # first alert for this station (rings the bell)
    ignored:       bool = False   # sent by an ignored call: not shown, no rules

    def hit(self, rule_label: str):
        """This decode's Hit from the rule labelled *rule_label*, or None."""
        return next((h for h in self.hits if h.rule == rule_label), None)


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
    """Fill r.station_state, and r.hits / r.wanted when the decode is checked."""
    if ctx.hamdat and r.callsign:
        r.station_state = ctx.hamdat.state(r.callsign)
    r.checked = r.is_cq or ctx.config.evaluate_all
    if not r.checked:
        return
    spot = Spot(call=r.callsign, grid=r.grid, entity=r.entity_info, state=r.station_state,
                band=ctx.band, message=r.msg_text, is_cq=r.is_cq)
    r.hits, r.wanted = evaluate_rules(
        ctx.rules, spot, ctx.logbook, ctx.config.filters.suppress_worked_within_days,
        datetime.now(timezone.utc).date())


def _maybe_alert(ctx: DecodeContext, r: DecodeResult) -> bool:
    """
    Fire external alerts for a wanted station; return True if this is a new alert.

    A station re-alerts only after it has gone unheard for _ALERT_REPEAT_S, or
    when the reasons it's wanted change.
    """
    if not r.wanted:
        return False
    key  = (r.callsign, frozenset(r.wanted))
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
            _build_alert_msg(r.callsign, r.entity_info, r.wanted),
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
        "wanted":         [h.as_dict() for h in r.wanted],
        "ignored":        r.ignored,
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
    Judge one decode as it arrives, then fire alerts and the JSON log for it.
    Returns None when the decode isn't shown (see judge()), or is from an
    ignored call (still written to the JSON log, marked "ignored").
    """
    r = judge(decoded, ctx)
    if r is None:
        return None
    if r.ignored:
        if ctx.json_fh and r.is_cq:
            _write_json(ctx, r)
        return None
    r.new_alert = _maybe_alert(ctx, r)
    if ctx.json_fh and r.is_cq:
        _write_json(ctx, r)
    return r


def judge(decoded: Decode, ctx: DecodeContext) -> DecodeResult | None:
    """
    Identify and classify one decode, with no side effects, so a decode can be
    judged again when the logbook or a watchlist changes.  Returns None when the
    decode isn't shown: a non-CQ message, unless the config shows all decodes or
    (with scope "all") the station is wanted.  A decode sent by an ignored call
    comes back with r.ignored set and no hits; one addressed to us never does.
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
    if ctx.ignored:
        sender = r.callsign if is_cq else _directed_sender(msg_text, ctx.de_call)
        if sender and base_call(sender) in ctx.ignored:
            r.ignored = True
            return r

    _classify(ctx, r)

    # Non-CQ: only shown when showing all, or the station is wanted.
    if not is_cq and not cfg.show_all and not r.wanted:
        return None
    return r
