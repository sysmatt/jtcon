"""Build and parse WSJT-X UDP datagrams for tests (Qt QDataStream, big-endian)."""

import struct
from datetime import datetime

MAGIC  = 0xADBCCBDA
SCHEMA = 2


def qstring(s: str | None) -> bytes:
    """QByteArray as WSJT-X sends it: length + UTF-8 bytes; None is the null string."""
    if s is None:
        return struct.pack(">I", 0xFFFFFFFF)
    b = s.encode("utf-8")
    return struct.pack(">I", len(b)) + b


def header(msg_type: int, client_id: str = "WSJT-X") -> bytes:
    return struct.pack(">III", MAGIC, SCHEMA, msg_type) + qstring(client_id)


def decode(message: str, mode: str = "~", time_ms: int = 45_015_000, snr: int = -7,
           dt: float = 0.2, df: int = 1234, new: bool = True, low_conf: bool = False,
           off_air: bool | None = False, client_id: str = "WSJT-X") -> bytes:
    """Decode message (type 2).  off_air=None omits the trailing field (older schema)."""
    pkt = (header(2, client_id)
           + struct.pack(">B", new)
           + struct.pack(">Iid", time_ms, snr, dt)
           + struct.pack(">I", df)
           + qstring(mode)
           + qstring(message)
           + struct.pack(">B", low_conf))
    if off_air is not None:
        pkt += struct.pack(">B", off_air)
    return pkt


def status(dial_freq: int, mode: str = "FT8", de_call: str = "N0CALL",
           transmitting: bool = False, client_id: str = "WSJT-X") -> bytes:
    """Status message (type 1), fields up to dx_grid."""
    return (header(1, client_id)
            + struct.pack(">Q", dial_freq)
            + qstring(mode)            # mode
            + qstring("")              # dx_call
            + qstring("")              # report
            + qstring(mode)            # tx_mode
            + struct.pack(">BBB", False, transmitting, False)   # tx_enabled, transmitting, decoding
            + struct.pack(">II", 1500, 1500)                    # rx_df, tx_df
            + qstring(de_call)
            + qstring("FN42")          # de_grid
            + qstring(""))             # dx_grid


def qdatetime(dt: datetime | None, spec: int = 1) -> bytes:
    """QDateTime: Julian day, ms since midnight, time spec (1 = UTC)."""
    if dt is None:
        return struct.pack(">qIB", 0, 0xFFFFFFFF, spec)
    jd = dt.date().toordinal() + 1721425
    ms = ((dt.hour * 60 + dt.minute) * 60 + dt.second) * 1000 + dt.microsecond // 1000
    return struct.pack(">qIB", jd, ms, spec)


def qso_logged(dx_call: str, dx_grid: str = "FN42", tx_freq: int = 14_074_000,
               mode: str = "FT8", when: datetime = datetime(2026, 10, 12, 12, 30),
               client_id: str = "WSJT-X") -> bytes:
    """QSO Logged (type 5), all fields of WSJT-X 2.x."""
    return (header(5, client_id) + qdatetime(when)
            + qstring(dx_call) + qstring(dx_grid)
            + struct.pack(">Q", tx_freq)
            + qstring(mode) + qstring("-10") + qstring("-12") + qstring("100")
            + qstring("") + qstring("")           # comments, name
            + qdatetime(when)
            + qstring("") + qstring("N0CALL") + qstring("FN42")   # operator, my call, grid
            + qstring("") + qstring("") + qstring(""))            # exchanges, prop mode


def logged_adif(adif: str, client_id: str = "WSJT-X") -> bytes:
    return header(12, client_id) + qstring(adif)


def close(client_id: str = "WSJT-X") -> bytes:
    return header(6, client_id)


def heartbeat(client_id: str = "WSJT-X") -> bytes:
    return header(0, client_id) + struct.pack(">I", 3) + qstring("2.7.0") + qstring("abc")


class _Reader:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def take(self, fmt: str):
        size = struct.calcsize(fmt)
        vals = struct.unpack(fmt, self.data[self.pos:self.pos + size])
        self.pos += size
        return vals[0] if len(vals) == 1 else vals

    def qstring(self) -> str | None:
        n = self.take(">I")
        if n == 0xFFFFFFFF:
            return None
        s = self.data[self.pos:self.pos + n].decode("utf-8")
        self.pos += n
        return s


def parse_reply(data: bytes) -> dict:
    """Parse a Reply message (type 4) as jtcon sends it."""
    r = _Reader(data)
    magic, schema, msg_type = r.take(">III")
    d = {"magic": magic, "schema": schema, "type": msg_type, "id": r.qstring()}
    d["time_ms"], d["snr"], d["dt"], d["df"] = r.take(">IidI")
    d["mode"]      = r.qstring()
    d["message"]   = r.qstring()
    d["low_conf"]  = bool(r.take(">B"))
    d["modifiers"] = r.take(">B")
    d["trailing"]  = len(data) - r.pos
    return d


def _color(r: "_Reader"):
    spec, a, red, green, blue, _pad = r.take(">bHHHHH")
    return None if spec == 0 else (red >> 8, green >> 8, blue >> 8)


def parse_highlight(data: bytes) -> dict:
    """Parse a Highlight Callsign message (type 13) as jtcon sends it."""
    r = _Reader(data)
    magic, schema, msg_type = r.take(">III")
    d = {"magic": magic, "schema": schema, "type": msg_type, "id": r.qstring()}
    d["callsign"]   = r.qstring()
    d["background"] = _color(r)
    d["foreground"] = _color(r)
    d["last_only"]  = bool(r.take(">B"))
    d["trailing"]   = len(data) - r.pos
    return d


def parse_sent(data: bytes) -> dict:
    """Parse anything jtcon sends to WSJT-X: a Reply or a Highlight."""
    msg_type = struct.unpack(">I", data[8:12])[0]
    return parse_reply(data) if msg_type == 4 else parse_highlight(data)
