"""WSJT-X UDP protocol: typed messages, the parser, and the Reply/Highlight encoders.

Messages are Qt QDataStream, big-endian: magic, schema, type, client id, then
the type's fields.  See WSJT-X's Network/NetworkMessage.hpp.
"""

import struct
from dataclasses import dataclass, fields
from datetime import date, datetime, time, timedelta, timezone


MAGIC  = 0xADBCCBDA
SCHEMA = 2           # what we send; WSJT-X 2.x and its forks all accept it

MSG_HEARTBEAT   = 0
MSG_STATUS      = 1
MSG_DECODE      = 2
MSG_CLEAR       = 3
MSG_REPLY       = 4
MSG_QSO_LOGGED  = 5
MSG_CLOSE       = 6
MSG_LOGGED_ADIF = 12
MSG_HIGHLIGHT   = 13


# WSJT-X Decode "mode" field is a single character per mode.
_MODE_NAMES = {
    "~": "FT8",
    "+": "FT4",
    "`": "FST4",
    ":": "Q65",
    "#": "JT65",
    "@": "JT9",
    "&": "MSK144",
    "$": "JT4",
}


# ══════════════════════════════════════════════════════════════════════════════
#  Messages
# ══════════════════════════════════════════════════════════════════════════════
#
#  Field defaults let a message from an older WSJT-X, which stops early, parse
#  with whatever it carried.

@dataclass
class Heartbeat:
    id:         str = ""
    max_schema: int = 0
    version:    str = ""
    revision:   str = ""


@dataclass
class Status:
    id:           str  = ""
    dial_freq:    int  = 0
    mode:         str  = ""
    dx_call:      str  = ""
    report:       str  = ""
    tx_mode:      str  = ""
    tx_enabled:   bool = False
    transmitting: bool = False
    decoding:     bool = False
    rx_df:        int  = 0
    tx_df:        int  = 0
    de_call:      str  = ""
    de_grid:      str  = ""
    dx_grid:      str  = ""


@dataclass
class Decode:
    id:       str   = ""
    new:      bool  = True
    time_ms:  int   = 0       # milliseconds since midnight UTC
    snr:      int   = 0
    dt:       float = 0.0
    df:       int   = 0
    mode:     str   = ""      # one character, see _MODE_NAMES
    message:  str   = ""
    low_conf: bool  = False
    off_air:  bool  = False


@dataclass
class Clear:
    id:     str = ""
    window: int = 0


@dataclass
class QsoLogged:
    id:            str             = ""
    time_off:      datetime | None = None
    dx_call:       str             = ""
    dx_grid:       str             = ""
    tx_freq:       int             = 0
    mode:          str             = ""
    report_sent:   str             = ""
    report_rcvd:   str             = ""
    tx_power:      str             = ""
    comments:      str             = ""
    name:          str             = ""
    time_on:       datetime | None = None
    operator_call: str             = ""
    my_call:       str             = ""
    my_grid:       str             = ""
    exchange_sent: str             = ""
    exchange_rcvd: str             = ""
    prop_mode:     str             = ""


@dataclass
class Close:
    id: str = ""


@dataclass
class LoggedAdif:
    id:   str = ""
    adif: str = ""


# ══════════════════════════════════════════════════════════════════════════════
#  Qt binary stream reader
# ══════════════════════════════════════════════════════════════════════════════

_JULIAN_EPOCH = 1721425   # QDate's Julian day number minus date.toordinal()


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos  = 0

    def _read(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError("Packet underflow")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def u8(self)    -> int:   return struct.unpack(">B", self._read(1))[0]
    def u32(self)   -> int:   return struct.unpack(">I", self._read(4))[0]
    def u64(self)   -> int:   return struct.unpack(">Q", self._read(8))[0]
    def i32(self)   -> int:   return struct.unpack(">i", self._read(4))[0]
    def i64(self)   -> int:   return struct.unpack(">q", self._read(8))[0]
    def f64(self)   -> float: return struct.unpack(">d", self._read(8))[0]
    def bool_(self) -> bool:  return self.u8() != 0

    def utf8(self) -> str:
        n = self.u32()
        if n == 0xFFFFFFFF:
            return ""
        return self._read(n).decode("utf-8", errors="replace")

    def datetime_(self) -> datetime | None:
        """QDateTime: Julian day, ms since midnight, time spec (+ offset)."""
        jd, ms, spec = self.i64(), self.u32(), self.u8()
        offset = self.i32() if spec == 2 else 0
        if spec == 3:
            raise ValueError("QTimeZone date/times are not supported")
        if jd <= 0 or ms == 0xFFFFFFFF:
            return None            # null date or time
        dt = datetime.combine(date.fromordinal(jd - _JULIAN_EPOCH), time()) \
            + timedelta(milliseconds=ms)
        if spec == 0:
            return dt                                    # local time, naive
        return dt.replace(tzinfo=timezone(timedelta(seconds=offset)))


_FIELD_READERS = {str: Reader.utf8, int: Reader.u32, bool: Reader.bool_,
                  float: Reader.f64, datetime | None: Reader.datetime_}

# Fields whose wire type differs from the default for their Python type.
_WIDE = {(Status, "dial_freq"): Reader.u64, (QsoLogged, "tx_freq"): Reader.u64,
         (Decode, "snr"): Reader.i32, (Heartbeat, "max_schema"): Reader.u32,
         (Clear, "window"): Reader.u8}

_TYPES = {MSG_HEARTBEAT: Heartbeat, MSG_STATUS: Status, MSG_DECODE: Decode,
          MSG_CLEAR: Clear, MSG_QSO_LOGGED: QsoLogged, MSG_CLOSE: Close,
          MSG_LOGGED_ADIF: LoggedAdif}

# Decode is the one message whose core fields must all be present.
_REQUIRED = {Decode: "low_conf"}


def _read_message(cls, r: Reader):
    values: dict = {}
    for f in fields(cls):
        reader = _WIDE.get((cls, f.name)) or _FIELD_READERS[f.type]
        try:
            values[f.name] = reader(r)
        except (ValueError, struct.error):
            required = _REQUIRED.get(cls)
            if required and required not in values:
                raise
            break                  # an older WSJT-X: keep what it sent
    return cls(**values)


def parse_message(data: bytes):
    """Return (schema, message) for a WSJT-X datagram we understand, else None."""
    if len(data) < 12:
        return None
    r = Reader(data)
    try:
        if r.u32() != MAGIC:
            return None
        schema, msg_type = r.u32(), r.u32()
        cls = _TYPES.get(msg_type)
        if cls is None:
            return None
        return schema, _read_message(cls, r)
    except (ValueError, struct.error):
        return None


def mode_name(decode: Decode) -> str:
    return _MODE_NAMES.get(decode.mode, decode.mode) or "?"


# ══════════════════════════════════════════════════════════════════════════════
#  Encoders: Reply (4) and Highlight Callsign (13)
# ══════════════════════════════════════════════════════════════════════════════

def _encode_utf8(s: str) -> bytes:
    if not s:
        return struct.pack(">I", 0xFFFFFFFF)
    b = s.encode("utf-8")
    return struct.pack(">I", len(b)) + b


def _header(msg_type: int, client_id: str) -> bytes:
    return struct.pack(">III", MAGIC, SCHEMA, msg_type) + _encode_utf8(client_id)


def encode_reply(d: Decode) -> bytes:
    """A Reply: WSJT-X acts as if the operator double-clicked the decode."""
    return (_header(MSG_REPLY, d.id)
            + struct.pack(">IidI", d.time_ms, d.snr, d.dt, d.df)
            + _encode_utf8(d.mode)
            + _encode_utf8(d.message)
            + struct.pack(">BB", 1 if d.low_conf else 0, 0))   # low confidence, modifiers


def _encode_color(rgb: tuple | None) -> bytes:
    """QColor: spec, then alpha/red/green/blue/pad as 16-bit.  None is an invalid
    colour, which tells WSJT-X to remove that highlight."""
    if rgb is None:
        return struct.pack(">bHHHHH", 0, 0, 0, 0, 0, 0)
    r, g, b = rgb
    return struct.pack(">bHHHHH", 1, 0xFFFF, r * 257, g * 257, b * 257, 0)


def encode_highlight(client_id: str, callsign: str, background: tuple | None,
                     foreground: tuple | None, last_only: bool = False) -> bytes:
    """Highlight Callsign: colour *callsign* in WSJT-X's Band Activity window.
    Colours are (r, g, b) 0-255; None for both clears the highlight."""
    return (_header(MSG_HIGHLIGHT, client_id)
            + _encode_utf8(callsign)
            + _encode_color(background)
            + _encode_color(foreground)
            + struct.pack(">B", 1 if last_only else 0))
