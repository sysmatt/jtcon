"""WSJT-X UDP packet parsing and the Reply encoder."""

import struct

from jtcon.core.protocol import _encode_utf8, _parse_decode_packet, _send_reply, try_parse_status
import wsjtx_packets as pk


def test_decode_packet_fields():
    data = pk.decode("CQ W1ABC FN42", mode="+", time_ms=45_015_000, snr=-12,
                     dt=0.3, df=1500, low_conf=True, off_air=True)
    schema, d = _parse_decode_packet(data)
    assert schema == 2
    assert d == {
        "id": "WSJT-X", "new": True, "time_ms": 45_015_000, "snr": -12,
        "dt": 0.3, "df": 1500, "mode": "+", "message": "CQ W1ABC FN42",
        "low_conf": True, "off_air": True,
    }


def test_decode_without_off_air_field():
    _, d = _parse_decode_packet(pk.decode("CQ W1ABC", off_air=None))
    assert d["off_air"] is False


def test_null_qstring_reads_as_empty():
    data = pk.header(2) + struct.pack(">BIid", 1, 0, 0, 0.0) + struct.pack(">I", 0) \
        + pk.qstring(None) + pk.qstring(None) + b"\x00\x00"
    _, d = _parse_decode_packet(data)
    assert d["mode"] == "" and d["message"] == ""


def test_decode_rejects_other_packets():
    assert _parse_decode_packet(b"") is None
    assert _parse_decode_packet(b"\x00" * 11) is None
    bad_magic = struct.pack(">III", 0x12345678, 2, 2) + b"\x00" * 40
    assert _parse_decode_packet(bad_magic) is None
    assert _parse_decode_packet(pk.status(14_074_000)) is None
    assert _parse_decode_packet(pk.decode("CQ W1ABC")[:30]) is None


def test_status_packet_fields():
    st = try_parse_status(pk.status(14_074_000, mode="FT4", de_call="K1ME",
                                          transmitting=True))
    assert st["dial_freq"] == 14_074_000
    assert st["mode"] == "FT4"
    assert st["transmitting"] is True
    assert st["de_call"] == "K1ME"
    assert st["de_grid"] == "FN42"


def test_status_truncated_keeps_what_it_read():
    st = try_parse_status(pk.status(7_074_000)[:30])
    assert st == {"id": "WSJT-X", "dial_freq": 7_074_000}


def test_status_rejects_decode():
    assert try_parse_status(pk.decode("CQ W1ABC")) is None
    assert try_parse_status(b"\x00" * 4) is None


class _FakeSock:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def sendto(self, data, addr):
        if self.fail:
            raise OSError("network down")
        self.sent.append((data, addr))


def test_reply_mirrors_the_decode():
    _, d = _parse_decode_packet(
        pk.decode("CQ DX VK3XY QF22", mode="~", time_ms=45_030_000, snr=-3,
                  dt=-0.4, df=987, low_conf=True))
    sock = _FakeSock()
    _send_reply(sock, ("127.0.0.1", 2237), d)
    (data, addr), = sock.sent
    assert addr == ("127.0.0.1", 2237)
    r = pk.parse_reply(data)
    assert r == {
        "magic": pk.MAGIC, "schema": 2, "type": 4, "id": "WSJT-X",
        "time_ms": 45_030_000, "snr": -3, "dt": -0.4, "df": 987, "mode": "~",
        "message": "CQ DX VK3XY QF22", "low_conf": True, "modifiers": 0, "trailing": 0,
    }


def test_reply_encodes_empty_string_as_null():
    assert _encode_utf8("") == struct.pack(">I", 0xFFFFFFFF)
    assert _encode_utf8("ab") == struct.pack(">I", 2) + b"ab"


def test_reply_send_failure_is_logged():
    _, d = _parse_decode_packet(pk.decode("CQ W1ABC"))
    logged = []
    _send_reply(_FakeSock(fail=True), ("127.0.0.1", 2237), d, log=logged.append)
    assert logged == ["[call] Reply send failed: network down"]
