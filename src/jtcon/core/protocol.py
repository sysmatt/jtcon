"""WSJT-X UDP protocol: message types, packet parsing, and the Reply encoder."""

import struct
import sys


MAGIC      = 0xADBCCBDA
MSG_STATUS = 1
MSG_DECODE = 2
MSG_REPLY  = 4


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
#  WSJT-X reply helper
# ══════════════════════════════════════════════════════════════════════════════

def _encode_utf8(s: str) -> bytes:
    if not s:
        return struct.pack(">I", 0xFFFFFFFF)
    b = s.encode("utf-8")
    return struct.pack(">I", len(b)) + b


def _send_reply(tx_sock, wsjt_addr: tuple, decoded: dict, log=None) -> None:
    _log = log or (lambda m: print(m, file=sys.stderr))
    buf = bytearray()
    buf += struct.pack(">III", MAGIC, 2, MSG_REPLY)
    buf += _encode_utf8(decoded["id"])
    buf += struct.pack(">I",  decoded["time_ms"])
    buf += struct.pack(">i",  decoded["snr"])
    buf += struct.pack(">d",  decoded["dt"])
    buf += struct.pack(">I",  decoded["df"])
    buf += _encode_utf8(decoded["mode"])
    buf += _encode_utf8(decoded["message"])
    buf += struct.pack(">B",  1 if decoded.get("low_conf") else 0)
    buf += struct.pack(">B",  0)
    try:
        tx_sock.sendto(bytes(buf), wsjt_addr)
    except OSError as exc:
        _log(f"[call] Reply send failed: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
#  Qt binary stream reader
# ══════════════════════════════════════════════════════════════════════════════

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
    def f64(self)   -> float: return struct.unpack(">d", self._read(8))[0]
    def bool_(self) -> bool:  return self.u8() != 0

    def utf8(self) -> str:
        n = self.u32()
        if n == 0xFFFFFFFF:
            return ""
        return self._read(n).decode("utf-8", errors="replace")


def parse_decode_msg(r: Reader) -> dict:
    d = {
        "id":       r.utf8(),
        "new":      r.bool_(),
        "time_ms":  r.u32(),
        "snr":      r.i32(),
        "dt":       r.f64(),
        "df":       r.u32(),
        "mode":     r.utf8(),
        "message":  r.utf8(),
        "low_conf": r.bool_(),
    }
    try:
        d["off_air"] = r.bool_()
    except ValueError:
        d["off_air"] = False
    return d


def parse_status_msg(r: Reader) -> dict:
    """Parse a WSJT-X Status message (type 1)."""
    d: dict = {}
    try:
        d["id"]           = r.utf8()
        d["dial_freq"]    = r.u64()
        d["mode"]         = r.utf8()
        d["dx_call"]      = r.utf8()
        d["report"]       = r.utf8()
        d["tx_mode"]      = r.utf8()
        d["tx_enabled"]   = r.bool_()
        d["transmitting"] = r.bool_()
        d["decoding"]     = r.bool_()
        d["rx_df"]        = r.u32()
        d["tx_df"]        = r.u32()
        d["de_call"]      = r.utf8()
        d["de_grid"]      = r.utf8()
        d["dx_grid"]      = r.utf8()
    except (ValueError, struct.error):
        pass
    return d


def try_parse_status(data: bytes) -> dict | None:
    """Return parsed Status dict if *data* is a WSJT-X Status message, else None."""
    if len(data) < 12:
        return None
    r = Reader(data)
    try:
        if r.u32() != MAGIC:
            return None
        r.u32()  # schema — ignored
        if r.u32() != MSG_STATUS:
            return None
        return parse_status_msg(r)
    except (ValueError, struct.error):
        return None


def _parse_decode_packet(data: bytes) -> tuple | None:
    """Return (schema, decoded dict) for a WSJT-X Decode message, else None."""
    if len(data) < 12:
        return None
    r = Reader(data)
    try:
        if r.u32() != MAGIC:
            return None
        schema = r.u32()
        if r.u32() != MSG_DECODE:
            return None
        return schema, parse_decode_msg(r)
    except (ValueError, struct.error):
        return None
