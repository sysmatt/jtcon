"""Fixed-width, ANSI-coloured text for the decode pane and status line."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from jtcon.core.bands import _5BWAS_BANDS, _WAS_STATES

if TYPE_CHECKING:
    from jtcon.config import Config
    from jtcon.core.cycle import CycleTracker
    from jtcon.core.decode import DecodeContext, DecodeResult


_W_MSG  = 24
_W_CALL = 12
_W_GRID = 6
_W_DAYS = 5    # days-since-last-worked column width
_W_WAS  = 4   # WAS / 5BWAS column: emoji(2) + state(2)


# ANSI colour codes used in the decode output pane.
_RST  = "\033[0m"
_DIM  = "\033[2m"
_BOLD = "\033[1m"
_GRN  = "\033[32m"    # green      — worked grid/entity
_BGRN = "\033[1;32m"  # bold green  — worked callsign (same band + mode)
_CYN  = "\033[36m"    # cyan       — entity block
_BRED = "\033[1;31m"  # bold red    — NEEDED alert (CQ)
_BYEL = "\033[1;33m"  # bold yellow — MATCH alert / callsign worked on another band or mode
_BMAG = "\033[1;35m"  # bold magenta — NEEDED/MATCH alert (non-CQ, --all)


def _col(text: str, code: str, on: bool) -> str:
    return f"{code}{text}{_RST}" if on else text


_BAR_WIDTH = 14   # characters wide for the cycle bargraph


def _render(ctx: DecodeContext, r: DecodeResult) -> list:
    """Return the fixed-width display line plus any NEEDED/MATCH alert line."""
    cfg, adif_log, color = ctx.config, ctx.adif_log, ctx.config.display.color
    decoded   = r.decoded
    checkable = r.is_cq or cfg.evaluate_all
    is_hit    = bool(r.needed_flags or r.match_flags)
    band      = ctx.band
    cols: list = []

    if cfg.display.show_time:
        cols.append(_col(f"[{datetime.now().strftime('%H:%M:%S')}]", _DIM, color))

    cols.append(_col(r.period_utc,              _DIM, color))
    cols.append(_col(f"{decoded['snr']:+4d}",   _DIM, color))
    cols.append(_col(f"{decoded['dt']:+6.1f}s", _DIM, color))
    cols.append(_col(f"{decoded['df']:5d}",     _DIM, color))
    cols.append(_col(f"{r.mode:<4}",            _DIM, color))

    msg_col = f"{r.msg_text:<{_W_MSG}}"
    cols.append(_col(msg_col, _BMAG, color) if (not r.is_cq and is_hit) else msg_col)

    call     = r.callsign or ""
    call_str = f"{call:<{_W_CALL}}"
    if call and adif_log:
        band_match = call in adif_log.worked_calls_by_band.get(band or "", set())
        mode_match = call in adif_log.worked_calls_by_mode.get(r.mode, set())
        if band_match and mode_match:
            call_color = _BGRN       # worked on this band and mode
        elif call in adif_log.worked_calls:
            call_color = _BYEL       # worked, but different band or mode
        else:
            call_color = _BOLD
        cols.append(_col(call_str, call_color, color))
    else:
        cols.append(call_str)

    grid        = r.grid or ""
    grid_str    = f"{grid:<{_W_GRID}}"
    grid_worked = bool(adif_log and grid and grid[:4] in adif_log.worked_grids)
    cols.append(_col(grid_str, _GRN, color) if grid_worked else grid_str)

    if adif_log:
        days_str = " " * _W_DAYS
        if call in adif_log.last_worked:
            delta    = (datetime.now(timezone.utc).date() - adif_log.last_worked[call]).days
            days_str = f"{delta:>4d}d"
        cols.append(days_str)

    if ctx.hamdat:
        st_val    = (r.station_state or "") if checkable else ""
        st_str    = f"{st_val:<2}"
        st_worked = bool(adif_log and st_val and st_val in adif_log.worked_states_all)
        cols.append(_col(st_str, _GRN, color) if st_worked else st_str)

    # Status icons are 2 cells wide; reserve the space even when blank so the
    # columns after the entity block stay aligned with the header.
    icon_on = [bool(adif_log) and on
               for on in (cfg.wants("dxcc"), cfg.wants("cqz"), cfg.wants("country"))]
    ei = r.entity_info
    if ei:
        icons = ["", "", ""]
        if adif_log and checkable:
            wf     = set(adif_log.worked_flags(ei, cfg))
            wf_all = set(adif_log.worked_flags_all(ei, cfg))
            for i, kind in enumerate(("dxcc", "cqz", "country")):
                if icon_on[i]:
                    icons[i] = "✅" if kind in wf else ("❓" if kind in wf_all else "❌")
        entity_str = (
            f"| {ei['entity']:<30}"
            f"  {icons[0]}CQ{ei['cq_zone']:<3}"
            f" {icons[1]}ITU{ei['itu_zone']:<3}"
            f" {icons[2]}{ei['continent']:<2}"
        )
        cols.append(_col(entity_str, _CYN, color))
    else:
        pad = ["  " if on else "" for on in icon_on]
        cols.append(f"| {'':<30}  {pad[0]}{'':5} {pad[1]}{'':6} {pad[2]}{'':2}")

    st = r.station_state
    need_was, need_5bwas = cfg.wants("was"), cfg.wants("5bwas")
    if adif_log and (need_was or need_5bwas):
        was_em = ""
        if checkable and st in _WAS_STATES:
            if need_was:
                if   st in adif_log.worked_states_lotw: was_em = "✅"
                elif st in adif_log.worked_states_all:  was_em = "❓"
                else:                                   was_em = "❌"
            elif band in _5BWAS_BANDS:
                confirmed = adif_log.worked_states_bands_lotw.get(st, set())
                worked    = adif_log.worked_states_bands_all.get(st, set())
                if   band in confirmed: was_em = "✅"
                elif band in worked:    was_em = "❓"
                else:                   was_em = "❌"
        cols.append(f"{was_em}{st:<2}" if was_em else " " * _W_WAS)

    if adif_log:
        if need_5bwas:
            qrz  = adif_log.qrz_confirmed_by_band.get(band or "", set())
            lotw = adif_log.lotw_rcvd_by_band.get(band or "", set())
        else:
            qrz  = adif_log.qrz_confirmed_calls
            lotw = adif_log.lotw_qsl_rcvd_calls
        cols.append("⭐" if call in qrz  else "  ")
        cols.append("🌎" if call in lotw else "  ")

    lines = ["  ".join(cols)]

    alert_parts: list = []
    if adif_log and r.needed_flags:
        alert_color = _BRED if r.is_cq else _BMAG
        alert_parts.append(_col(f"*** NEEDED: {', '.join(r.needed_flags)} ***", alert_color, color))
    if r.match_flags:
        match_color = _BYEL if r.is_cq else _BMAG
        alert_parts.append(_col(f"*** MATCH: {', '.join(r.match_flags)} ***", match_color, color))
    if alert_parts:
        lines.append("  " + "  ".join(alert_parts))
    return lines


# ══════════════════════════════════════════════════════════════════════════════
#  Column header line
# ══════════════════════════════════════════════════════════════════════════════

def _header_line(cfg: Config, has_adif: bool = False,
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
    if has_adif:
        parts.append(f"{'days':>{_W_DAYS}}")
    if has_hamdat:
        parts.append(f"{'ST':<2}")
    w = 2 if has_adif and cfg.wants("dxcc")    else 0
    x = 2 if has_adif and cfg.wants("cqz")     else 0
    y = 2 if has_adif and cfg.wants("country") else 0
    parts.append(f"| {'entity':<30}  {'':{w}}CQz   {'':{x}}ITUz   {'':{y}}ct")
    if has_adif and (cfg.wants("was") or cfg.wants("5bwas")):
        label = "WAS " if cfg.wants("was") else "5BW "
        parts.append(f"{label:<{_W_WAS}}")
    if has_adif:
        parts.append("QRZ")
        parts.append("LoW")
    header = "  ".join(parts)
    sep    = "─" * min(len(header), 160)
    return header + "\n" + sep


# ══════════════════════════════════════════════════════════════════════════════
#  Cycle bargraph helper
# ══════════════════════════════════════════════════════════════════════════════

def _bargraph(cycle_tracker: CycleTracker | None) -> str:
    """Return a bargraph string showing position within the current TX cycle."""
    now = datetime.now(timezone.utc)
    ms = (
        now.hour   * 3_600_000
        + now.minute *    60_000
        + now.second *     1_000
        + now.microsecond // 1_000
    )
    mode = cycle_tracker.current_mode if cycle_tracker else "FT8"
    cycle_ms = 7500 if mode == "FT4" else 15000
    fraction = (ms % cycle_ms) / cycle_ms
    filled   = int(fraction * _BAR_WIDTH)
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def _fmt_age(seconds: float) -> str:
    """Format an elapsed time as e.g. '45s', '3m05s', '1h12m'."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"
