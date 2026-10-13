"""TUI: CMD> commands, the decode pane, the status line, and engine events.

Runs JtconTUI headless (no terminal) on top of a real Engine; packets go
straight into the engine, and the app itself is never started.
"""

import re
import time

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from jtcon.core.engine import STALE_REPLY_S
from jtcon.core.events import Fatal, LogLine, Ready
from jtcon.tui.app import _MAX_OUTPUT_LINES, _TRIM_OUTPUT_TO, JtconTUI
import wsjtx_packets as pk

ADDR = ("127.0.0.1", 2237)


class _Buffer:
    def __init__(self, text):
        self.text = text


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    return now


@pytest.fixture
def make_tui(make_engine):
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        def _make(*argv, **kw):
            return JtconTUI(make_engine("--no-color", *argv, **kw))
        yield _make


@pytest.fixture
def tui(make_tui):
    return make_tui()


def store(tui, msg, addr=ADDR):
    """Feed one decode to the engine, as if WSJT-X sent it; return its index."""
    tui.engine.datagram_received(pk.decode(msg), addr)
    tui._flush_output()
    return tui.engine.events[-1].station.idx


def command(tui, text):
    tui._handle_command(_Buffer(text))
    tui._flush_output()
    return tui._lines


def test_spots_are_shown_with_their_index(tui):
    tui.engine.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)
    tui.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    tui._flush_output()
    assert tui._lines[0].startswith("00  123015z") and "CQ W1ABC FN42" in tui._lines[0]
    assert tui._lines[1].startswith("01  123015z") and "CQ VK3XY QF22" in tui._lines[1]
    assert tui._lines[2].startswith("  *** NEEDED: NEW-DXCC(VK)")
    # The station keeps its rendering, without the index, for "list".
    assert tui.engine.stations.find("VK3XY").display == [tui._lines[1][4:], tui._lines[2]]


def test_raw_non_cq_is_indented(make_tui):
    t = make_tui("--raw")
    t.engine.datagram_received(pk.decode("W1ABC K5TX -05"), ADDR)
    t._flush_output()
    assert t._lines[0].startswith("    123015z")


def test_header_repeats_every_15_spots(tui):
    for i in range(15):
        tui.engine.datagram_received(pk.decode(f"CQ K{i}AA FN42"), ADDR)
    tui._flush_output()
    assert tui._lines[-2].startswith("##") and tui._lines[-1].startswith("─")


def test_ready_prints_header_then_banner(tui):
    tui._on_event(Ready("[ready] Listening"))
    tui._on_event(LogLine("a\nb"))
    tui._flush_output()
    assert tui._lines[0].startswith("##") and tui._lines[2:] == ["[ready] Listening", "a", "b"]


def test_fatal_event_exits(tui, monkeypatch):
    calls = []
    monkeypatch.setattr(tui, "_exit_app", lambda: calls.append(1))
    tui._on_event(Fatal("[adif] File not found: x"))
    tui._flush_output()
    assert tui._fatal_msg == "[adif] File not found: x" and calls == [1]


# ── Commands ──────────────────────────────────────────────────────────────────

def replies(tui) -> list:
    return [pk.parse_reply(d)["message"] for d, _ in tui.engine.tx_sock.sent
            if pk.parse_sent(d)["type"] == 4]


def test_reply_by_index(tui):
    store(tui, "CQ W1ABC FN42")
    store(tui, "CQ VK3XY QF22")
    lines = command(tui, "01")
    assert lines[-1] == "[cmd] ► Reply sent to VK3XY [01]  msg='CQ VK3XY QF22'  → 127.0.0.1:2237"
    assert replies(tui) == ["CQ VK3XY QF22"]
    command(tui, "1")
    assert len(replies(tui)) == 2


def test_reply_by_callsign(tui):
    store(tui, "CQ VK3XY QF22")
    assert command(tui, "vk3xy")[-1].startswith("[cmd] ► Reply sent to VK3XY [00]")
    assert command(tui, "ZL1AA")[-1] == "[cmd] ZL1AA not found in rolling buffer."
    assert command(tui, "42")[-1] == "[cmd] No CQ at index 42."


def test_stale_reply_warns(tui, clock):
    store(tui, "CQ VK3XY QF22")
    clock[0] += STALE_REPLY_S + 5
    lines = command(tui, "0")
    assert lines[-2] == ("[cmd] Warning: VK3XY last heard 2m05s ago"
                         " — WSJT-X may no longer have that decode.")
    assert lines[-1].startswith("[cmd] ► Reply sent to VK3XY")


def test_unknown_command(tui):
    assert command(tui, "foo bar")[-1] == (
        "[cmd] Unknown: 'foo bar'  "
        "(commands: quit, clear, list; or enter index 00-99 / callsign to reply)")
    n = len(tui._lines)
    assert len(command(tui, "   ")) == n      # blank input does nothing


def test_list(tui, clock):
    assert command(tui, "list")[-1] == "[list] No NEEDED/MATCH entries in rolling buffer."
    store(tui, "CQ VK3XY QF22")         # 00, hit
    clock[0] += 10
    store(tui, "CQ W1ABC FN42")         # 01, no hit: not listed
    clock[0] += 10
    store(tui, "CQ KH6/W1XX")           # 02, hit
    clock[0] += 10
    store(tui, "CQ VK3XY QF22")         # 00 heard again: now the most recent
    clock[0] += 5
    before = len(tui._lines)
    lines = command(tui, "l")[before:]
    assert lines[0] == "[list] 2 NEEDED/MATCH entries (last heard at bottom) ——"
    assert lines[1].startswith("02  123015z") and "CQ KH6/W1XX" in lines[1]
    assert lines[2] == "  *** NEEDED: NEW-DXCC(KH6), NEW-CQZ(31), NEW-CTRY(Hawaii) ***" \
                       "  (last heard 15s ago, ×1)"
    assert lines[3].startswith("00  123015z") and "CQ VK3XY QF22" in lines[3]
    assert lines[4].endswith("  (last heard 5s ago, ×2)")
    assert len(lines) == 5
    for abbrev in ("li", "lis", "LIST"):
        assert command(tui, abbrev)[-5].startswith("[list] 2 NEEDED/MATCH")


def test_clear_command(tui):
    tui._append_line("old output")
    tui._flush_output()
    assert command(tui, "clear") == tui._header().split("\n")


def test_quit_commands(tui, monkeypatch):
    calls = []
    monkeypatch.setattr(tui, "_exit_app", lambda: calls.append(1))
    for cmd in ("quit", "exit", "q", "Q"):
        command(tui, cmd)
    assert len(calls) == 4


# ── Output pane ───────────────────────────────────────────────────────────────

def test_output_is_trimmed(tui):
    for i in range(_MAX_OUTPUT_LINES + 1):
        tui._append_line(f"line {i}")
    tui._flush_output()
    assert len(tui._lines) == _TRIM_OUTPUT_TO
    assert tui._lines[-1] == f"line {_MAX_OUTPUT_LINES}"


def test_multiline_append_splits(tui):
    tui._append_line("a\nb")
    tui._flush_output()
    assert tui._lines[-2:] == ["a", "b"]
    assert tui.output_area.text.endswith("a\nb\n")


def _fill(tui, n):
    """Add n lines and put the window at the bottom, as a render would."""
    for i in range(n):
        tui._append_line(f"line {i}")
    tui._flush_output()
    bottom = n + 1 - 20               # headless window height defaults to 20
    tui.output_area.window.vertical_scroll = bottom
    return bottom


def test_scroll_back_and_return_to_live(tui):
    bottom = _fill(tui, 100)
    tui._scroll_output(-5)
    assert tui._scroll_top == bottom - 5
    tui._append_line("new while scrolled")
    tui._flush_output()
    assert tui._scroll_top == bottom - 5          # view stays put
    tui._scroll_output(+50)
    assert tui._scroll_top is None                # reached the bottom: live again
    tui._scroll_output(-500)
    assert tui._scroll_top == 0


def test_scrolled_view_shifts_when_output_is_trimmed(tui):
    bottom = _fill(tui, _MAX_OUTPUT_LINES)
    tui._scroll_output(-3000)
    top = tui._scroll_top
    assert top == bottom - 3000
    tui._append_line("one more")      # pushes past the cap: trims 1001 lines
    tui._flush_output()
    assert tui._scroll_top == top - (_MAX_OUTPUT_LINES + 1 - _TRIM_OUTPUT_TO)


def test_command_returns_to_live(tui):
    _fill(tui, 100)
    tui._scroll_output(-5)
    assert tui._scroll_top is not None
    command(tui, "foo")
    assert tui._scroll_top is None


def test_status_line(tui):
    def text():
        return "".join(t for _, t in tui._get_status_text())

    tui.engine.de_call = ""
    assert text().startswith(" RX  [")
    tui.engine.datagram_received(pk.status(14_074_000, de_call="K1ME", transmitting=True), ADDR)
    assert text().startswith(" TX  [") and "de=K1ME" in text()
    tui._scroll_top = 3
    assert " SCROLL " in text()
    tui.engine.udp_down = True
    assert text().startswith(" UDP DOWN ")


def test_header_columns(make_tui):
    t = make_tui("--was", adif=False, hamdat=None)
    assert "ST" not in t._header()                  # no hamdat loaded
    t.engine.adif_log, t.engine.hamdat = object(), object()
    h = t._header()
    assert re.search(r"days\s+ST\s+\| entity", h) and "WAS" in h and "QRZ  LoW" in h
