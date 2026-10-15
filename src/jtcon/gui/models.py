"""Qt table models.  The facts for each cell come from the Engine's results, as
on the other screens; the models only hold them and say how to show them."""

import time
from collections import deque
from datetime import datetime, timezone

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont

from jtcon.core.wanted import MARKS
from jtcon.tui.render import _status_columns
from jtcon.ui.wanted import WantedRows, columns, wanted_colour, why

DECODE_ROWS = 5000      # decodes kept

# Text colours by name, readable on light and dark backgrounds.
COLOURS = {
    "red":     QColor("#e5484d"),
    "yellow":  QColor("#d4a017"),
    "magenta": QColor("#c15dd6"),
    "green":   QColor("#3fa66b"),
    "cyan":    QColor("#2fa4b8"),
    "dim":     QColor("#8a8f98"),
}

_RIGHT = {"db", "dt", "hz", "days", "cqz", "ituz", "seen", "count"}  # numbers, right-aligned


def decode_columns(cfg, has_log: bool, has_hamdat: bool) -> list:
    """(key, label) for each decode column, in order: the same facts as the
    other screens' decode line."""
    cols = [("idx", "##")]
    if cfg.display.show_time:
        cols.append(("local", "Local"))
    cols += [("time", "UTC"), ("db", "dB"), ("dt", "dt"), ("hz", "Hz"), ("mode", "Mode"),
             ("msg", "Message"), ("call", "Call"), ("grid", "Grid")]
    if has_log:
        cols.append(("days", "days"))
    if has_hamdat:
        cols.append(("st", "ST"))
    cols += [("entity", "Entity"), ("cqz", "CQz"), ("ituz", "ITUz"), ("ct", "ct")]
    if has_log:
        cols += [(f"rule:{label}", label) for label, _, _ in _status_columns(cfg)]
        cols += [("qrz", "QRZ"), ("lotw", "LoW")]
    return cols + [("why", "Wanted")]


class DecodeRow:
    """One decode's cells (key -> text) and their colours (key -> colour name),
    as of when it arrived."""

    def __init__(self, ctx, r, station):
        cfg, calls, d = ctx.config, ctx.calls, r.decoded
        ei = r.entity_info or {}
        call = r.callsign or ""
        self.colour = wanted_colour(r, r.wanted) if r.wanted else None
        self.tooltip = " · ".join(h.mark() for h in r.wanted)
        self.cells = {
            "idx":    f"{station.idx:02d}" if station is not None else "",
            "local":  datetime.now().strftime("%H:%M:%S"),
            "time":   r.period_utc,
            "db":     f"{d.snr:+d}",
            "dt":     f"{d.dt:+.1f}",
            "hz":     str(d.df),
            "mode":   r.mode,
            "msg":    r.msg_text,
            "call":   call,
            "grid":   r.grid or "",
            "entity": ei.get("entity", ""),
            "cqz":    str(ei.get("cq_zone", "")),
            "ituz":   str(ei.get("itu_zone", "")),
            "ct":     ei.get("continent", ""),
            "st":     (r.station_state or "") if r.checked else "",
            "why":    why(r.wanted),
        }
        self.colours = {key: self.colour for key in ("msg", "call", "why")} if self.colour else {}
        if calls:
            if call in calls.last_worked:
                delta = (datetime.now(timezone.utc).date() - calls.last_worked[call]).days
                self.cells["days"] = f"{delta}d"
            if call and not self.colour:
                if (call in calls.worked_calls_by_band.get(ctx.band or "", set())
                        and call in calls.worked_calls_by_mode.get(r.mode, set())):
                    self.colours["call"] = "green"       # worked on this band and mode
                elif call in calls.worked_calls:
                    self.colours["call"] = "yellow"      # worked, another band or mode
            if r.grid and r.grid[:4] in calls.worked_grids:
                self.colours["grid"] = "green"
            if self.cells["st"] and self.cells["st"] in calls.worked_states:
                self.colours["st"] = "green"
            for label, _, _ in _status_columns(cfg):
                hit = r.hit(label) if r.checked else None
                self.cells[f"rule:{label}"] = f"{MARKS[hit.status]}{hit.value}" if hit else ""
            per_band = ctx.band if any(w.rule == "5bwas" for w in cfg.active_wanted) else None
            self.cells["qrz"] = "⭐" if calls.confirmed(call, "qrz", per_band) else ""
            self.cells["lotw"] = "🌎" if calls.confirmed(call, "lotw", per_band) else ""
        for key in ("idx", "local", "time", "db", "dt", "hz", "mode", "days"):
            self.colours.setdefault(key, "dim")


class DecodeModel(QAbstractTableModel):
    """The decode stream, oldest first, DECODE_ROWS at most."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cols: list = []            # (key, label)
        self.rows: deque = deque()
        self._bold = QFont()
        self._bold.setBold(True)

    def set_columns(self, cols: list) -> None:
        if cols != self.cols:
            self.beginResetModel()
            self.cols = cols
            self.endResetModel()

    def add(self, row: DecodeRow) -> None:
        if len(self.rows) >= DECODE_ROWS:
            self.beginRemoveRows(QModelIndex(), 0, 0)
            self.rows.popleft()
            self.endRemoveRows()
        n = len(self.rows)
        self.beginInsertRows(QModelIndex(), n, n)
        self.rows.append(row)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self.rows.clear()
        self.endResetModel()

    def text(self, row: int, key: str) -> str:
        return self.rows[row].cells.get(key, "")

    # ── QAbstractTableModel ───────────────────────────────────────────────────

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.cols)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.cols[section][1]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = self.cols[index.column()][0]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.cells.get(key, "")
        if role == Qt.ItemDataRole.ForegroundRole:
            colour = row.colours.get(key)
            return COLOURS[colour] if colour else None
        if role == Qt.ItemDataRole.FontRole:
            return self._bold if row.colour and key in ("msg", "call", "why") else None
        if role == Qt.ItemDataRole.ToolTipRole:
            return row.tooltip or None
        if role == Qt.ItemDataRole.TextAlignmentRole and key in _RIGHT:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class WantedModel(QAbstractTableModel):
    """The Wanted table: one row per station some rule wants now (jtcon.ui.wanted).

    refresh() updates the rows in place, removing, adding and moving them, so
    Qt's selection follows each station as the rows move."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.source = WantedRows(engine)
        self.cols: list = []            # (key, label)
        self.rows: list = []            # Row
        self.values: list = []          # each row's cell texts, in cols order
        self._bold = QFont()
        self._bold.setBold(True)

    def note(self, result, ctx, station) -> None:
        """Remember *station*'s newest evaluated decode (SpotEvent, StationUpdated)."""
        self.source.note(result, ctx, station)

    def row_of(self, call: str) -> int | None:
        for i, row in enumerate(self.rows):
            if row.call == call:
                return i
        return None

    def refresh(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        cfg = self.engine.config
        cols = columns(cfg, self.engine.logbook is not None)
        new = self.source.rows(now)
        if cols != self.cols:
            self.beginResetModel()
            self.cols, self.rows = cols, new
            self.values = [r.values(cfg, now) for r in new]
            self.endResetModel()
            return
        # Gone stations out, new ones in at the end, then into the new order.
        calls = {r.call for r in new}
        for i in reversed(range(len(self.rows))):
            if self.rows[i].call not in calls:
                self.beginRemoveRows(QModelIndex(), i, i)
                del self.rows[i], self.values[i]
                self.endRemoveRows()
        have = {r.call for r in self.rows}
        added = [r for r in new if r.call not in have]
        if added:
            n = len(self.rows)
            self.beginInsertRows(QModelIndex(), n, n + len(added) - 1)
            self.rows += added
            self.values += [r.values(cfg, now) for r in added]
            self.endInsertRows()
        if [r.call for r in self.rows] != [r.call for r in new]:
            self.layoutAboutToBeChanged.emit()
            where = {r.call: i for i, r in enumerate(new)}
            for index in self.persistentIndexList():
                call = self.rows[index.row()].call
                self.changePersistentIndex(index, self.index(where[call], index.column()))
            self.rows = new
            self.values = [r.values(cfg, now) for r in new]
            self.layoutChanged.emit()
        else:
            self.rows = new
            self.values = [r.values(cfg, now) for r in new]
        if self.rows:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self.rows) - 1, len(self.cols) - 1))

    def text(self, row: int, key: str) -> str:
        return self.values[row][[k for k, _ in self.cols].index(key)]

    # ── QAbstractTableModel ───────────────────────────────────────────────────

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.cols)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.cols[section][1]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = self.cols[index.column()][0]
        if role == Qt.ItemDataRole.DisplayRole:
            return self.values[index.row()][index.column()]
        if role == Qt.ItemDataRole.ForegroundRole:
            if key in ("call", "why"):
                return COLOURS[row.colour]
            if key == "idx" and row.changed:
                return QColor("black")
            return COLOURS["dim"] if key == "days" else None
        if role == Qt.ItemDataRole.BackgroundRole:
            # A row that just changed: its index lights up for a moment.
            return COLOURS[row.colour] if key == "idx" and row.changed else None
        if role == Qt.ItemDataRole.FontRole:
            return self._bold if key in ("call", "why") else None
        if role == Qt.ItemDataRole.ToolTipRole:
            return " · ".join(h.mark() for h in row.station.wanted) or None
        if role == Qt.ItemDataRole.TextAlignmentRole and key in _RIGHT:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None
