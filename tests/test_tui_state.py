"""TUI-side state: rolling buffer, CMD> commands, output pane trimming and scrolling.

Runs JtconTUI headless (no terminal); the app itself is never started.
"""

import re
import time

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from jtcon.core.decode import handle_packet
from jtcon.tui.app import _BUF_SIZE, _MAX_OUTPUT_LINES, _STALE_REPLY_S, _TRIM_OUTPUT_TO, JtconTUI
import wsjtx_packets as pk
from conftest import v2_config

ADDR = ("127.0.0.1", 2237)


class _FakeSock:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


class _Buffer:
    def __init__(self, text):
        self.text = text


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    return now


@pytest.fixture
def make_tui():
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        def _make(*argv):
            return JtconTUI(v2_config("--no-color", *argv), None, _FakeSock())
        yield _make


@pytest.fixture
def tui(make_tui):
    return make_tui()


def store(tui, ctx, msg, addr=ADDR):
    """Feed one decode through handle_packet into the buffer, as the UDP loop does."""
    meta = {}
    lines = handle_packet(pk.decode(msg), ctx, addr, meta)
    return tui._store_entry(meta["callsign"], meta, lines, addr)


def command(tui, text):
    tui._handle_command(_Buffer(text))
    tui._flush_output()
    return tui._lines


# ── Rolling buffer ────────────────────────────────────────────────────────────

def test_each_station_keeps_its_index(tui, make_ctx, clock):
    ctx = make_ctx("--no-color")
    assert [store(tui, ctx, m) for m in ("CQ W1ABC FN42", "CQ VK3XY QF22", "CQ JA1XYZ PM95")] \
        == [0, 1, 2]
    clock[0] += 15
    assert store(tui, ctx, "CQ DX VK3XY QF22") == 1
    entry = tui._find_callsign("VK3XY")
    assert entry["count"] == 2
    assert entry["first_seen"] == 1000.0 and entry["last_seen"] == 1015.0
    assert entry["decoded"]["message"] == "CQ DX VK3XY QF22"     # newest decode wins
    assert tui._find_callsign("ZL1AA") is None


def test_full_buffer_evicts_least_recently_heard(tui, clock):
    meta = {"decoded": {}, "needed_flags": [], "match_flags": []}
    for i in range(_BUF_SIZE):
        clock[0] += 1
        tui._store_entry(f"K{i}AA", meta, ["line"], ADDR)
    clock[0] += 1
    tui._store_entry("K5AA", meta, ["line"], ADDR)     # K0AA is now the oldest
    clock[0] += 1
    idx = tui._store_entry("ZL1NEW", meta, ["line"], ADDR)
    assert idx == 0
    assert tui._find_callsign("K0AA") is None
    assert tui._find_callsign("ZL1NEW")["idx"] == 0
    assert len(tui._cq_buffer) == _BUF_SIZE


def test_clear_buffer(tui, make_ctx):
    ctx = make_ctx()
    store(tui, ctx, "CQ W1ABC FN42")
    store(tui, ctx, "CQ VK3XY QF22")
    assert tui._clear_buffer() == 2
    assert tui._cq_buffer == {} and tui._call_idx == {}
    assert store(tui, ctx, "CQ JA1XYZ PM95") == 0


# ── Commands ──────────────────────────────────────────────────────────────────

def test_reply_by_index(tui, make_ctx):
    ctx = make_ctx()
    store(tui, ctx, "CQ W1ABC FN42")
    store(tui, ctx, "CQ VK3XY QF22")
    lines = command(tui, "01")
    assert lines[-1] == "[cmd] ► Reply sent to VK3XY [01]  msg='CQ VK3XY QF22'  → 127.0.0.1:2237"
    (data, addr), = tui.tx_sock.sent
    assert addr == ADDR
    assert pk.parse_reply(data)["message"] == "CQ VK3XY QF22"
    command(tui, "1")
    assert len(tui.tx_sock.sent) == 2


def test_reply_by_callsign(tui, make_ctx):
    store(tui, make_ctx(), "CQ VK3XY QF22")
    assert command(tui, "vk3xy")[-1].startswith("[cmd] ► Reply sent to VK3XY [00]")
    assert command(tui, "ZL1AA")[-1] == "[cmd] ZL1AA not found in rolling buffer."
    assert command(tui, "42")[-1] == "[cmd] No CQ at index 42."


def test_stale_reply_warns(tui, make_ctx, clock):
    store(tui, make_ctx(), "CQ VK3XY QF22")
    clock[0] += _STALE_REPLY_S + 5
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


def test_list(tui, make_ctx, clock):
    ctx = make_ctx("--no-color")
    assert command(tui, "list")[-1] == "[list] No NEEDED/MATCH entries in rolling buffer."
    store(tui, ctx, "CQ VK3XY QF22")         # 00, hit
    clock[0] += 10
    store(tui, ctx, "CQ W1ABC FN42")         # 01, no hit: not listed
    clock[0] += 10
    store(tui, ctx, "CQ KH6/W1XX")           # 02, hit
    clock[0] += 10
    store(tui, ctx, "CQ VK3XY QF22")         # 00 heard again: now the most recent
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

    assert text().startswith(" RX  [")
    tui._de_call = "N0CALL"
    tui._transmitting = True
    assert text().startswith(" TX  [") and "de=N0CALL" in text()
    tui._scroll_top = 3
    assert " SCROLL " in text()
    tui._udp_down = True
    assert text().startswith(" UDP DOWN ")


def test_header_columns(make_tui):
    t = make_tui("--was")
    assert "ST" not in t._header()                  # no hamdat loaded
    t.adif_log, t.hamdat = object(), object()
    h = t._header()
    assert re.search(r"days\s+ST\s+\| entity", h) and "WAS" in h and "QRZ  LoW" in h
