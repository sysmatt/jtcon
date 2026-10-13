"""WSJT-X UDP messages: parsing every type we use, and the Reply/Highlight encoders."""

import struct
from datetime import datetime, timedelta, timezone

from jtcon.core.protocol import (Clear, Close, Decode, Heartbeat, LoggedAdif, QsoLogged, Reader,
                                 Status, _encode_utf8, encode_highlight, encode_reply,
                                 mode_name, parse_message)
import wsjtx_packets as pk


def test_decode_packet_fields():
    data = pk.decode("CQ W1ABC FN42", mode="+", time_ms=45_015_000, snr=-12,
                     dt=0.3, df=1500, low_conf=True, off_air=True)
    assert parse_message(data) == (2, Decode(
        id="WSJT-X", new=True, time_ms=45_015_000, snr=-12, dt=0.3, df=1500, mode="+",
        message="CQ W1ABC FN42", low_conf=True, off_air=True))


def test_decode_without_off_air_field():
    _, d = parse_message(pk.decode("CQ W1ABC", off_air=None))
    assert d.off_air is False


def test_null_qstring_reads_as_empty():
    data = pk.header(2) + struct.pack(">BIid", 1, 0, 0, 0.0) + struct.pack(">I", 0) \
        + pk.qstring(None) + pk.qstring(None) + b"\x00\x00"
    _, d = parse_message(data)
    assert d.mode == "" and d.message == ""


def test_rejects_other_packets():
    assert parse_message(b"") is None
    assert parse_message(b"\x00" * 11) is None
    bad_magic = struct.pack(">III", 0x12345678, 2, 2) + b"\x00" * 40
    assert parse_message(bad_magic) is None
    assert parse_message(pk.header(99) + b"\x00" * 8) is None        # unknown type
    assert parse_message(pk.decode("CQ W1ABC")[:30]) is None         # truncated decode


def test_status_packet_fields():
    _, st = parse_message(pk.status(14_074_000, mode="FT4", de_call="K1ME", transmitting=True))
    assert isinstance(st, Status)
    assert (st.dial_freq, st.mode, st.transmitting, st.de_call, st.de_grid) == \
        (14_074_000, "FT4", True, "K1ME", "FN42")


def test_status_truncated_keeps_what_it_read():
    _, st = parse_message(pk.status(7_074_000)[:30])
    assert st == Status(id="WSJT-X", dial_freq=7_074_000)


def test_qso_logged():
    _, q = parse_message(pk.qso_logged("VK3XY", dx_grid="QF22", tx_freq=7_074_123))
    when = datetime(2026, 10, 12, 12, 30, tzinfo=timezone.utc)
    assert q == QsoLogged(id="WSJT-X", time_off=when, dx_call="VK3XY", dx_grid="QF22",
                          tx_freq=7_074_123, mode="FT8", report_sent="-10", report_rcvd="-12",
                          tx_power="100", time_on=when, my_call="N0CALL", my_grid="FN42")


def test_qso_logged_from_an_older_wsjtx():
    # WSJT-X 1.x stopped after the comments field.
    data = pk.qso_logged("VK3XY")
    _, q = parse_message(data[:data.index(b"100") + 3 + 8])
    assert q.dx_call == "VK3XY" and q.tx_power == "100" and q.time_on is None


def test_qdatetime_variants():
    jd = datetime(2026, 1, 2).date().toordinal() + 1721425
    local = struct.pack(">qIB", jd, 3_600_000, 0)
    assert Reader(local).datetime_() == datetime(2026, 1, 2, 1, 0)
    offset = struct.pack(">qIBi", jd, 0, 2, 3600)
    assert Reader(offset).datetime_() == datetime(2026, 1, 2, tzinfo=timezone(timedelta(hours=1)))
    assert Reader(pk.qdatetime(None)).datetime_() is None


def test_other_messages():
    assert parse_message(pk.logged_adif("<call:5>VK3XY <eor>"))[1] == \
        LoggedAdif(id="WSJT-X", adif="<call:5>VK3XY <eor>")
    assert parse_message(pk.close("WSJT-X - rig2"))[1] == Close(id="WSJT-X - rig2")
    assert parse_message(pk.heartbeat())[1] == Heartbeat("WSJT-X", 3, "2.7.0", "abc")
    assert parse_message(pk.header(3) + b"\x01")[1] == Clear("WSJT-X", 1)


def test_mode_name():
    assert mode_name(Decode(mode="~")) == "FT8"
    assert mode_name(Decode(mode="+")) == "FT4"
    assert mode_name(Decode(mode="?x")) == "?x"
    assert mode_name(Decode(mode="")) == "?"


def test_reply_mirrors_the_decode():
    _, d = parse_message(pk.decode("CQ DX VK3XY QF22", mode="~", time_ms=45_030_000, snr=-3,
                                   dt=-0.4, df=987, low_conf=True))
    assert pk.parse_reply(encode_reply(d)) == {
        "magic": pk.MAGIC, "schema": 2, "type": 4, "id": "WSJT-X",
        "time_ms": 45_030_000, "snr": -3, "dt": -0.4, "df": 987, "mode": "~",
        "message": "CQ DX VK3XY QF22", "low_conf": True, "modifiers": 0, "trailing": 0,
    }


def test_reply_encodes_empty_string_as_null():
    assert _encode_utf8("") == struct.pack(">I", 0xFFFFFFFF)
    assert _encode_utf8("ab") == struct.pack(">I", 2) + b"ab"


def test_highlight():
    h = pk.parse_highlight(encode_highlight("WSJT-X", "VK3XY", (255, 165, 0), (0, 0, 0)))
    assert h == {"magic": pk.MAGIC, "schema": 2, "type": 13, "id": "WSJT-X",
                 "callsign": "VK3XY", "background": (255, 165, 0), "foreground": (0, 0, 0),
                 "last_only": False, "trailing": 0}
    data = encode_highlight("WSJT-X", "VK3XY", (255, 165, 0), (0, 0, 0))
    # QColor on the wire: spec 1 (RGB), then 16-bit alpha, red, green, blue, padding.
    assert data[-23:-12] == struct.pack(">bHHHHH", 1, 0xFFFF, 0xFFFF, 0xA5A5, 0, 0)
    assert data[-12:-1] == struct.pack(">bHHHHH", 1, 0xFFFF, 0, 0, 0, 0)


def test_highlight_clear_sends_invalid_colours():
    h = pk.parse_highlight(encode_highlight("WSJT-X", "VK3XY", None, None, last_only=True))
    assert (h["background"], h["foreground"], h["last_only"]) == (None, None, True)
