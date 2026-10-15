"""The Wanted table's rows, shared by the front ends: one row per station some
rule wants now.

The rows come from the Engine's rolling buffer (stations.hits()), so a station
leaves the table when it stops being wanted, is ignored, or drops out of the
buffer; and after display.wanted_timeout_minutes unheard.  Each station's
newest evaluated decode (result and context) supplies the facts.
"""

import time
from datetime import datetime, timezone

from jtcon.core.messages import STAGE_73, STAGE_ANSWER, STAGE_CQ, STAGE_REPORT
from jtcon.core.stations import fmt_age
from jtcon.core.wanted import MARKS
from jtcon.tui.render import _status_columns

# Sort orders: (name, key on a Row).  Ctrl-S cycles them; a header click picks one.
SORTS = [
    ("last heard", lambda row: -row.last_seen),
    ("SNR",        lambda row: -row.snr),
    ("call",       lambda row: row.call),
    ("entity",     lambda row: (row.entity, row.call)),
]
# The sort a column header picks, by column key.
SORT_BY_COLUMN = {"call": "call", "entity": "entity", "db": "SNR", "seen": "last heard"}

# Where the station is in a QSO, from its latest message.
STAGE_MARKS = {STAGE_CQ: "📣", STAGE_ANSWER: "🤝", STAGE_REPORT: "📶", STAGE_73: "👋"}

CHANGE_SHOWN_S = 2.0    # a row that just changed is highlighted this long


def wanted_colour(result, hits) -> str:
    """"red" when an award rule wants it, "yellow" for watchlists alone,
    "magenta" when it was heard outside a CQ."""
    if not result.is_cq:
        return "magenta"
    return "red" if any(h.status for h in hits) else "yellow"


def why(hits) -> str:
    """The rules that want it, e.g. "dxcc was pota(K-1234)"."""
    return " ".join(h.rule if not h.detail or h.status else f"{h.rule}({h.detail})"
                    for h in hits)


class Row:
    """One station's line in the table, from its newest evaluated decode."""

    def __init__(self, station, result, ctx, changed: bool = False):
        self.station, self.result, self.ctx = station, result, ctx
        self.changed   = changed        # just heard again, or its stage or reasons changed
        self.call      = station.callsign
        self.last_seen = max(station.heard, station.last_seen)   # heard sending anything
        self.snr       = result.decoded.snr
        self.entity    = (result.entity_info or {}).get("entity", "")

    @property
    def colour(self) -> str:
        return wanted_colour(self.result, self.station.wanted)

    def values(self, cfg, now: float) -> list:
        """The cells as text, in columns() order."""
        r, ctx, calls = self.result, self.ctx, self.ctx.calls
        ei = r.entity_info or {}
        days = ""
        if calls and self.call in calls.last_worked:
            delta = (datetime.now(timezone.utc).date() - calls.last_worked[self.call]).days
            days = f"{delta}d"
        cells = [
            f"{self.station.idx:02d}",
            self.call,
            STAGE_MARKS.get(self.station.stage, ""),
            ei.get("entity", ""),
            str(ei.get("cq_zone", "")),
            str(ei.get("itu_zone", "")),
            ei.get("continent", ""),
            r.station_state or "",
            r.grid or "",
            f"{self.snr:+d}",
            fmt_age(now - self.last_seen),
            str(self.station.count),
            days,
        ]
        for label, _, _ in _status_columns(cfg) if calls else ():
            hit = r.hit(label)
            cells.append(f"{MARKS[hit.status]}{hit.value}" if hit else "")
        if calls:
            per_band = ctx.band if any(w.rule == "5bwas" for w in cfg.active_wanted) else None
            cells.append("⭐" if calls.confirmed(self.call, "qrz", per_band) else "")
            cells.append("🌎" if calls.confirmed(self.call, "lotw", per_band) else "")
        cells.append(why(self.station.wanted))
        return cells


def columns(cfg, has_log: bool) -> list:
    """(key, label) for each column, in order."""
    cols = [("idx", "##"), ("call", "Call"), ("qso", "QSO"), ("entity", "Entity"), ("cqz", "CQz"),
            ("ituz", "ITUz"), ("ct", "ct"), ("st", "ST"), ("grid", "Grid"), ("db", "dB"),
            ("seen", "Seen"), ("count", "×"), ("days", "days")]
    if has_log:
        cols += [(f"rule:{label}", label) for label, _, _ in _status_columns(cfg)]
        cols += [("qrz", "QRZ"), ("lotw", "LoW")]
    return cols + [("why", "Wanted")]


class WantedRows:
    """Which stations the table shows, and in what order.  A front end passes
    each station's newest evaluated decode to note() (from SpotEvent and
    StationUpdated) and asks rows() when it redraws."""

    def __init__(self, engine):
        self.engine = engine
        self.latest: dict = {}          # call -> (result, ctx), newest evaluated decode
        self.sort_index = 0
        self._seen: dict = {}           # call -> (signature, monotonic time it changed)

    @property
    def sort_name(self) -> str:
        return SORTS[self.sort_index][0]

    def set_sort(self, name: str | None = None) -> str:
        """Sort by *name*, or the next sort order; return its name."""
        names = [n for n, _ in SORTS]
        self.sort_index = (names.index(name) if name in names
                           else (self.sort_index + 1) % len(SORTS))
        return self.sort_name

    def note(self, result, ctx, station) -> None:
        self.latest[station.callsign] = (result, ctx)

    def rows(self, now: float | None = None) -> list:
        """The stations to show, sorted."""
        now = time.monotonic() if now is None else now
        timeout = self.engine.config.display.wanted_timeout_minutes * 60
        out = []
        for st in self.engine.stations.hits():
            if now - max(st.heard, st.last_seen) > timeout or st.callsign not in self.latest:
                continue
            sig = (st.heard, st.stage, tuple(h.mark() for h in st.wanted))
            old = self._seen.get(st.callsign)
            changed_at = now if old is None or old[0] != sig else old[1]
            self._seen[st.callsign] = (sig, changed_at)
            out.append(Row(st, *self.latest[st.callsign],
                           changed=now - changed_at < CHANGE_SHOWN_S))
        live = {r.call for r in out}
        for call in [c for c in self.latest if c not in live and
                     self.engine.stations.find(c) is None]:
            del self.latest[call]          # gone from the buffer
            self._seen.pop(call, None)
        return sorted(out, key=SORTS[self.sort_index][1])
