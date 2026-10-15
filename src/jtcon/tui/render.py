"""Fixed-width, ANSI-coloured text for the decode pane."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from jtcon.core.wanted import MARKS, RULE_CLASSES, AwardRule, rule_label

if TYPE_CHECKING:
    from jtcon.config import Config
    from jtcon.core.decode import DecodeContext, DecodeResult


_W_MSG  = 24
_W_CALL = 12
_W_GRID = 6
_W_DAYS = 5    # days-since-last-worked column width
_W_ENTITY = 24 # the longest cty.dat entity names ("Central African Republic")


# ANSI colour codes used in the decode output pane.
_RST  = "\033[0m"
_DIM  = "\033[2m"
_BOLD = "\033[1m"
_GRN  = "\033[32m"    # green      — worked grid/entity
_BGRN = "\033[1;32m"  # bold green  — worked callsign (same band + mode)
_CYN  = "\033[36m"    # cyan       — entity block
_BRED = "\033[1;31m"  # bold red    — WANTED by an award rule (CQ)
_BYEL = "\033[1;33m"  # bold yellow — WANTED by watchlists only / call worked on another band or mode
_BMAG = "\033[1;35m"  # bold magenta — WANTED, non-CQ (scope all)


def _col(text: str, code: str, on: bool) -> str:
    return f"{code}{text}{_RST}" if on else text


def _status_columns(cfg: Config) -> list:
    """(label, value width, column width) for each award rule, in config order."""
    cols = []
    for spec in cfg.active_wanted:
        cls = RULE_CLASSES[spec.rule]
        if issubclass(cls, AwardRule):
            label = rule_label(spec)
            cols.append((label, cls.value_width, max(len(label), 2 + cls.value_width)))
    return cols


def _status_cell(hit, value_width: int, width: int) -> str:
    """✅VK / ❓30 / ❌ — the mark is two cells wide on screen."""
    if hit is None:
        return " " * width
    return f"{MARKS[hit.status]}{hit.value:<{value_width}}" + " " * (width - 2 - value_width)


def _render(ctx: DecodeContext, r: DecodeResult) -> list:
    """Return the fixed-width display line plus a WANTED line when wanted."""
    cfg, calls, color = ctx.config, ctx.calls, ctx.config.display.color
    decoded   = r.decoded
    band      = ctx.band
    cols: list = []

    if cfg.display.show_time:
        cols.append(_col(f"[{datetime.now().strftime('%H:%M:%S')}]", _DIM, color))

    cols.append(_col(r.period_utc,              _DIM, color))
    cols.append(_col(f"{decoded.snr:+4d}",      _DIM, color))
    cols.append(_col(f"{decoded.dt:+6.1f}s",    _DIM, color))
    cols.append(_col(f"{decoded.df:5d}",        _DIM, color))
    cols.append(_col(f"{r.mode:<4}",            _DIM, color))

    msg_col = f"{r.msg_text:<{_W_MSG}}"
    cols.append(_col(msg_col, _BMAG, color) if (not r.is_cq and r.wanted) else msg_col)

    call     = r.callsign or ""
    call_str = f"{call:<{_W_CALL}}"
    if call and calls:
        band_match = call in calls.worked_calls_by_band.get(band or "", set())
        mode_match = call in calls.worked_calls_by_mode.get(r.mode, set())
        if band_match and mode_match:
            call_color = _BGRN       # worked on this band and mode
        elif call in calls.worked_calls:
            call_color = _BYEL       # worked, but different band or mode
        else:
            call_color = _BOLD
        cols.append(_col(call_str, call_color, color))
    else:
        cols.append(call_str)

    grid        = r.grid or ""
    grid_str    = f"{grid:<{_W_GRID}}"
    grid_worked = bool(calls and grid and grid[:4] in calls.worked_grids)
    cols.append(_col(grid_str, _GRN, color) if grid_worked else grid_str)

    if calls:
        days_str = " " * _W_DAYS
        if call in calls.last_worked:
            delta    = (datetime.now(timezone.utc).date() - calls.last_worked[call]).days
            days_str = f"{delta:>4d}d"
        cols.append(days_str)

    if ctx.hamdat:
        st_val    = (r.station_state or "") if r.checked else ""
        st_str    = f"{st_val:<2}"
        st_worked = bool(calls and st_val and st_val in calls.worked_states)
        cols.append(_col(st_str, _GRN, color) if st_worked else st_str)

    ei = r.entity_info
    if ei:
        entity_str = (f"| {ei['entity']:<{_W_ENTITY}}  CQ{ei['cq_zone']:<3} ITU{ei['itu_zone']:<3}"
                      f" {ei['continent']:<2}")
        cols.append(_col(entity_str, _CYN, color))
    else:
        cols.append(f"| {'':<{_W_ENTITY}}  {'':5} {'':6} {'':2}")

    if calls:
        status = _status_columns(cfg)
        if status:
            cells = [_status_cell(r.hit(label) if r.checked else None, vw, w)
                     for label, vw, w in status]
            cols.append("| " + "  ".join(cells))
        # Per call: has this station confirmed with us via QRZ / LoTW (on this
        # band, when 5-Band WAS is on)?
        per_band = band if any(w.rule == "5bwas" for w in cfg.active_wanted) else None
        cols.append("⭐" if calls.confirmed(call, "qrz", per_band) else "  ")
        cols.append("🌎" if calls.confirmed(call, "lotw", per_band) else "  ")

    lines = ["  ".join(cols)]

    if r.wanted:
        if not r.is_cq:
            code = _BMAG
        else:   # red when an award rule wants it; yellow for watchlists alone
            code = _BRED if any(h.status for h in r.wanted) else _BYEL
        text = " · ".join(h.mark() for h in r.wanted)
        lines.append("  " + _col(f"*** WANTED: {text} ***", code, color))
    return lines


def _render_compact(ctx: DecodeContext, r: DecodeResult) -> str:
    """The short decode line for the split layouts: time, dB, dt, Hz, message,
    and ★ for a wanted station (the Wanted table says why)."""
    color = ctx.config.display.color
    d = r.decoded
    code = (_BMAG if not r.is_cq else
            _BRED if any(h.status for h in r.wanted) else _BYEL) if r.wanted else ""
    msg = f"{r.msg_text:<{_W_MSG}}"
    return "  ".join([
        _col(r.period_utc, _DIM, color),
        _col(f"{d.snr:+4d}", _DIM, color),
        _col(f"{d.dt:+5.1f}", _DIM, color),
        _col(f"{d.df:5d}", _DIM, color),
        _col(msg, code, color) if code else msg,
    ]) + (" " + _col("★", code, color) if code else "")


def _header_compact() -> str:
    header = "  ".join(["##", "HHMMSSZ", f"{'dB':>4}", f"{'dt':>5}", f"{'Hz':>5}", "message"])
    return header + "\n" + "─" * (len(header) + _W_MSG - len("message") + 2)


# ══════════════════════════════════════════════════════════════════════════════
#  Column header line
# ══════════════════════════════════════════════════════════════════════════════

def _header_line(cfg: Config, has_log: bool = False,
                 has_hamdat: bool = False) -> str:
    parts = ["##"]
    if cfg.display.show_time:
        parts.append(f"{'[time]':<10}")
    parts.append(f"{'HHMMSSZ':<7}")
    parts.append(f"{'dB':>4}")
    parts.append(f"{'dt(s)':>7}")
    parts.append(f"{'Hz':>5}")
    parts.append(f"{'mode':<4}")
    parts.append(f"{'message':<{_W_MSG}}")
    parts.append(f"{'callsign':<{_W_CALL}}")
    parts.append(f"{'grid':<{_W_GRID}}")
    if has_log:
        parts.append(f"{'days':>{_W_DAYS}}")
    if has_hamdat:
        parts.append(f"{'ST':<2}")
    parts.append(f"| {'entity':<{_W_ENTITY}}  CQz   ITUz   ct")
    if has_log:
        status = _status_columns(cfg)
        if status:
            parts.append("| " + "  ".join(f"{label:<{w}}" for label, _, w in status))
        parts.append("QRZ")
        parts.append("LoW")
    header = "  ".join(parts)
    sep    = "─" * min(len(header), 160)
    return header + "\n" + sep
