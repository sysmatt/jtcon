"""The status line's text, shared by the front ends; each adds its own badge
styling, frequency and scroll marker."""

from datetime import datetime, timezone

from jtcon.core.wanted import rule_label

_BAR_WIDTH = 14   # characters wide for the cycle bargraph


def status_badge(engine) -> str:
    """"UDP DOWN", "TX" or "RX"."""
    return "UDP DOWN" if engine.udp_down else "TX" if engine.transmitting else "RX"


def status_text(engine) -> str:
    """The status line after its badge: the cycle bargraph, parity, decode
    counts and averages, your call and the ignore count."""
    ct  = engine.cycle_tracker
    bar = _bargraph(ct)
    if ct and ct.current_time_ms is not None:
        parity = ct._parity
        ev, od = ct._sums["even"], ct._sums["odd"]
        all_n   = ev[2] + od[2]
        avg_dec = (ev[0] + od[0]) / all_n if all_n else 0.0
        avg_cq  = (ev[1] + od[1]) / all_n if all_n else 0.0
        ev_d = ev[0] / ev[2] if ev[2] else 0.0
        od_d = od[0] / od[2] if od[2] else 0.0
        ev_c = ev[1] / ev[2] if ev[2] else 0.0
        od_c = od[1] / od[2] if od[2] else 0.0
        stats = (
            f"  {ct.current_mode}"
            f"  dec={ct.cycle_total} cq={ct.cycle_cq}"
            f"  avg dec={avg_dec:.1f} cq={avg_cq:.1f}"
            f"  even dec={ev_d:.1f} cq={ev_c:.1f}"
            f"  odd dec={od_d:.1f} cq={od_c:.1f}"
        )
    else:
        parity = "even"
        stats  = "  (waiting for decodes…)"
    de  = f"  de={engine.de_call}" if engine.de_call else ""
    ign = f"  ign:{len(engine.ignored)}" if engine.ignored else ""
    return f" [{bar}] {parity}{stats}{de}{ign} "


def _bargraph(cycle_tracker) -> str:
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


def session_changes(cfg, configured: tuple) -> str:
    """What differs from the config file for this session, for the end of the
    status line: *configured* is (scope.decodes, display.show) as loaded."""
    out = []
    if cfg.scope.decodes != configured[0]:
        out.append(f"scope:{cfg.scope.decodes}")
    if cfg.display.show != configured[1]:
        out.append(f"show:{cfg.display.show}")
    if cfg.rules_off:
        out.append("off:" + ",".join(rule_label(w) for w in cfg.wanted
                                     if w.name in cfg.rules_off))
    return " ".join(out)
