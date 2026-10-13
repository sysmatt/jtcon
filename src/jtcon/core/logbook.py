"""The Logbook: QSO records from every source, normalized, with duplicates merged.

Each source (an ADIF file, a QSO Logged message from WSJT-X) gives QSO records.
The same contact often appears in several sources, e.g. the WSJT-X log and a QRZ
download; those records are merged into one, and its confirmations are the union
of what each source says.
"""

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone

from jtcon.core.bands import _freq_to_band


# Confirmation source -> (ADIF field, value meaning confirmed).
CONFIRMATIONS = {
    "lotw": ("lotw_qsl_rcvd", "Y"),
    "qrz":  ("app_qrzlog_status", "C"),
    "eqsl": ("eqsl_qsl_rcvd", "Y"),
    "card": ("qsl_rcvd", "Y"),
}

# Records from different sources are the same QSO when call, band and mode agree
# and their start times are within this window (or either has no time, same day).
MATCH_WINDOW = timedelta(minutes=15)


@dataclass
class QSO:
    call:          str
    date:          date | None = None   # UTC
    time:          time | None = None   # UTC, time on
    band:          str = ""             # ADIF band, lower case: "20m"
    mode:          str = ""             # submode if logged, else mode: "FT4", "FT8"
    grid:          str = ""             # 4 characters
    dxcc:          str = ""             # cty.dat primary prefix
    country:       str = ""             # cty.dat country, upper case
    log_country:   str = ""             # COUNTRY as logged, upper case
    cqz:           int | None = None    # as logged, else from cty.dat
    ituz:          int | None = None
    state:         str = ""             # as logged, else from hamdat (US calls)
    confirmations: set = field(default_factory=set)   # see CONFIRMATIONS
    sources:       set = field(default_factory=set)   # file names, "wsjt-x"

    def start(self) -> datetime | None:
        if self.date is None or self.time is None:
            return None
        return datetime.combine(self.date, self.time)


def _int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y%m%d").date() if len(value) == 8 else None
    except ValueError:
        return None


def _parse_time(value: str) -> time | None:
    fmt = {4: "%H%M", 6: "%H%M%S"}.get(len(value))
    try:
        return datetime.strptime(value, fmt).time() if fmt else None
    except ValueError:
        return None


def _locate(q: QSO, info: dict | None, hamdat) -> QSO:
    """Fill the entity fields from cty.dat's *info* and a missing US state from hamdat."""
    if info:
        q.dxcc    = info["primary_prefix"].upper()
        q.country = info["country"].upper()
        q.ituz    = q.ituz if q.ituz is not None else info.get("ituz")
    if not q.state and hamdat and q.call:
        q.state = hamdat.state(q.call) or ""
    return q


def qso_from_adif(rec: dict, cty, hamdat, source: str) -> QSO:
    """A QSO from one parse_adif() record."""
    def get(*names):
        for name in names:
            value = rec.get(name, "").strip()
            if value:
                return value
        return ""

    call = get("call").upper()
    info = cty.lookup(call) if call else None
    cqz_raw = get("cqz", "cq_zone")
    cqz = _int(cqz_raw)
    if not cqz_raw and info:
        cqz = info["cqz"]
    q = QSO(
        call=call,
        date=_parse_date(get("qso_date")),
        time=_parse_time(get("time_on")),
        band=get("band").lower(),
        # FT4, FST4, Q65 etc. are logged as MODE=MFSK with a SUBMODE.
        mode=get("submode", "mode").upper(),
        grid=get("gridsquare").upper()[:4],
        log_country=get("country", "entity").upper(),
        cqz=cqz,
        ituz=_int(get("ituz", "itu_zone")),
        state=get("state").upper(),
        confirmations={src for src, (name, yes) in CONFIRMATIONS.items()
                       if get(name).upper() == yes},
        sources={source},
    )
    return _locate(q, info, hamdat)


def qso_from_logged(msg, cty, hamdat) -> QSO:
    """A QSO from WSJT-X's QSO Logged message, before it reaches any file."""
    when = msg.time_on or msg.time_off
    if when is not None and when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    call = msg.dx_call.strip().upper()
    info = cty.lookup(call) if call else None
    q = QSO(
        call=call,
        date=when.date() if when else None,
        time=when.time().replace(microsecond=0) if when else None,
        band=_freq_to_band(msg.tx_freq) or "",
        mode=msg.mode.strip().upper(),
        grid=msg.dx_grid.strip().upper()[:4],
        cqz=info["cqz"] if info else None,
        sources={"wsjt-x"},
    )
    return _locate(q, info, hamdat)


def _same_qso(a: QSO, b: QSO) -> bool:
    if a.sources & b.sources:
        return False            # one source logging a call twice: two QSOs
    sa, sb = a.start(), b.start()
    if sa is None or sb is None:
        return a.date == b.date
    return abs(sa - sb) <= MATCH_WINDOW


_FILLABLE = ("time", "grid", "dxcc", "country", "log_country", "cqz", "ituz", "state")


class Logbook:
    """Every QSO from every source, one record per contact."""

    def __init__(self, qsos=()):
        self.qsos: list = []
        self.merged = 0           # records that turned out to be duplicates
        self.unsaved = 0          # logged in WSJT-X, not yet found in any file
        self._by_key: dict = {}   # (call, band, mode) -> [QSO]
        for q in qsos:
            self.add(q)

    def __len__(self) -> int:
        return len(self.qsos)

    def add(self, qso: QSO) -> QSO:
        """Add *qso*, merging it into an existing record for the same contact.
        Returns the record it ended up in.  *qso* itself is never modified."""
        if qso.call:
            key = (qso.call, qso.band, qso.mode)
            for rec in self._by_key.get(key, ()):
                if _same_qso(rec, qso):
                    rec.confirmations |= qso.confirmations
                    rec.sources |= qso.sources
                    for name in _FILLABLE:
                        if getattr(rec, name) in ("", None):
                            setattr(rec, name, getattr(qso, name))
                    self.merged += 1
                    return rec
        rec = replace(qso, confirmations=set(qso.confirmations), sources=set(qso.sources))
        self.qsos.append(rec)
        if rec.call:
            self._by_key.setdefault((rec.call, rec.band, rec.mode), []).append(rec)
        return rec
