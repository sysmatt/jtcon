"""The Textual front end's Wanted table: one row per station some rule wants now.

The rows come from the Engine's rolling buffer (stations.hits()), so a station
leaves the table when it stops being wanted, is ignored, or drops out of the
buffer; and after display.wanted_timeout_minutes unheard.  Each station's
newest evaluated decode (result and context) supplies the facts.
"""

import time
from datetime import datetime, timezone

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable

from jtcon.core.messages import STAGE_73, STAGE_ANSWER, STAGE_CQ, STAGE_REPORT
from jtcon.core.stations import fmt_age
from jtcon.core.wanted import MARKS
from jtcon.tui.render import _status_columns

# Sort orders: (name, key on a Row).  ctrl+s cycles them; a header click picks one.
SORTS = [
    ("last heard", lambda row: -row.last_seen),
    ("SNR",        lambda row: -row.snr),
    ("call",       lambda row: row.call),
    ("entity",     lambda row: (row.entity, row.call)),
]
# Where the station is in a QSO, from its latest message.
STAGE_MARKS = {STAGE_CQ: "📣", STAGE_ANSWER: "🤝", STAGE_REPORT: "📶", STAGE_73: "👋"}

CHANGE_SHOWN_S = 2.0    # a row that just changed is highlighted this long

_SORT_BY_COLUMN = {"call": "call", "entity": "entity", "db": "SNR", "seen": "last heard"}


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
    def style(self) -> str:
        """Red when an award rule wants it, yellow for watchlists alone,
        magenta when it was heard outside a CQ."""
        if not self.result.is_cq:
            return "bold magenta"
        return "bold red" if any(h.status for h in self.station.wanted) else "bold yellow"

    def cells(self, cfg, now: float) -> list:
        r, ctx, calls = self.result, self.ctx, self.ctx.calls
        ei = r.entity_info or {}
        days = ""
        if calls and self.call in calls.last_worked:
            delta = (datetime.now(timezone.utc).date() - calls.last_worked[self.call]).days
            days = f"{delta}d"
        cells = [
            Text(f"{self.station.idx:02d}", style="reverse" if self.changed else ""),
            Text(self.call, style=self.style),
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
            Text(days, style="dim"),
        ]
        for label, _, _ in _status_columns(cfg) if calls else ():
            hit = r.hit(label)
            cells.append(f"{MARKS[hit.status]}{hit.value}" if hit else "")
        if calls:
            per_band = ctx.band if any(w.rule == "5bwas" for w in cfg.active_wanted) else None
            cells.append("⭐" if calls.confirmed(self.call, "qrz", per_band) else "")
            cells.append("🌎" if calls.confirmed(self.call, "lotw", per_band) else "")
        why = " ".join(h.rule if not h.detail or h.status else f"{h.rule}({h.detail})"
                       for h in self.station.wanted)
        cells.append(Text(why, style=self.style))
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


class WantedTable(DataTable):
    """The table.  refresh_rows() rebuilds it from the Engine, keeping the
    selected station selected."""

    BINDINGS = [
        Binding("i", "ignore", "Ignore", show=False),
    ]

    class Answer(Message):
        """Answer this station (Enter on its row, or a double-click)."""
        def __init__(self, call: str):
            super().__init__()
            self.call = call

    def __init__(self, engine, **kw):
        super().__init__(cursor_type="row", zebra_stripes=True, **kw)
        self.engine = engine
        self.latest: dict = {}          # call -> (result, ctx), newest evaluated decode
        self.sort_index = 0
        self._columns: list = []
        self._in_click = False
        self._seen: dict = {}           # call -> (signature, monotonic time it changed)

    @property
    def sort_name(self) -> str:
        return SORTS[self.sort_index][0]

    def note(self, result, ctx, station) -> None:
        """Remember *station*'s newest evaluated decode (SpotEvent, StationUpdated)."""
        self.latest[station.callsign] = (result, ctx)

    def wanted_rows(self, now: float | None = None) -> list:
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

    def refresh_rows(self) -> None:
        cfg = self.engine.config
        cols = columns(cfg, self.engine.logbook is not None)
        selected = self.selected_call
        if cols != self._columns:
            self.clear(columns=True)
            for key, label in cols:
                self.add_column(label, key=key)
            self._columns = cols
        else:
            self.clear()
        now = time.monotonic()
        rows = self.wanted_rows(now)
        for row in rows:
            self.add_row(*row.cells(cfg, now), key=row.call)
        if selected:
            for i, row in enumerate(rows):
                if row.call == selected:
                    self.move_cursor(row=i, animate=False)
                    break

    @property
    def selected_call(self) -> str | None:
        if not self.row_count:
            return None
        try:
            key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
        except Exception:
            return None
        return key.value

    def selected_station(self):
        call = self.selected_call
        return self.engine.stations.find(call) if call else None

    def cycle_sort(self, name: str | None = None) -> str:
        names = [n for n, _ in SORTS]
        self.sort_index = (names.index(name) if name in names
                           else (self.sort_index + 1) % len(SORTS))
        self.refresh_rows()
        return self.sort_name

    # A single click only selects a row; Enter or a double-click answers it.
    async def _on_click(self, event: events.Click) -> None:
        self._in_click = True
        try:
            await super()._on_click(event)
        finally:
            self._in_click = False
        event.prevent_default()      # Textual would run DataTable's handler again
        if event.chain == 2 and event.style.meta.get("row", -1) >= 0 and self.selected_call:
            self.post_message(self.Answer(self.selected_call))

    def _post_selected_message(self):
        if not self._in_click:
            super()._post_selected_message()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.post_message(self.Answer(event.row_key.value))

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        name = _SORT_BY_COLUMN.get(event.column_key.value)
        if name:
            self.cycle_sort(name)
            self.app.log_strip.add(f"[wanted] Sorted by {name}")

    def action_ignore(self) -> None:
        station = self.selected_station()
        if station:
            self.engine.ignore(station.callsign)
            self.refresh_rows()
