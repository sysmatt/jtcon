"""Build and parse WSJT-X UDP datagrams for tests (Qt QDataStream, big-endian)."""

import struct

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
