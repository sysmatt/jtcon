"""The Textual front end's Wanted table: one row per station some rule wants
now.  Which rows, their order and their text come from jtcon.ui.wanted."""

import time

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable

from jtcon.ui.wanted import SORT_BY_COLUMN, WantedRows, columns

_STYLES = {"red": "bold red", "yellow": "bold yellow", "magenta": "bold magenta"}


def cells(row, cols: list, cfg, now: float) -> list:
    """*row*'s values, styled: the call and reasons in its colour, a changed
    row's index reversed, days dim."""
    style = _STYLES[row.colour]
    out = []
    for (key, _), value in zip(cols, row.values(cfg, now)):
        if key in ("call", "why"):
            value = Text(value, style=style)
        elif key == "idx":
            value = Text(value, style="reverse" if row.changed else "")
        elif key == "days":
            value = Text(value, style="dim")
        out.append(value)
    return out


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
        self.source = WantedRows(engine)
        self._columns: list = []
        self._in_click = False

    @property
    def sort_name(self) -> str:
        return self.source.sort_name

    def note(self, result, ctx, station) -> None:
        """Remember *station*'s newest evaluated decode (SpotEvent, StationUpdated)."""
        self.source.note(result, ctx, station)

    def wanted_rows(self, now: float | None = None) -> list:
        """The stations to show, sorted."""
        return self.source.rows(now)

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
            self.add_row(*cells(row, cols, cfg, now), key=row.call)
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
        self.source.set_sort(name)
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
        name = SORT_BY_COLUMN.get(event.column_key.value)
        if name:
            self.cycle_sort(name)
            self.app.log_strip.add(f"[wanted] Sorted by {name}")

    def action_ignore(self) -> None:
        station = self.selected_station()
        if station:
            self.engine.ignore(station.callsign)
            self.refresh_rows()
