"""The desktop front end, headless: Qt's offscreen platform, pytest-qt driving
the window on top of a real Engine (inputs loaded, no network); packets go
straight into the engine."""

import asyncio
import os
import socket

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pytestqt")
pytest.importorskip("qasync")

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication

import jtcon.gui.models
from jtcon.core.events import Fatal, LogLine, Ready
from jtcon.gui.app import JtconGui
from jtcon.gui.models import COLOURS, DECODE_ROWS
from jtcon.gui.widgets import ansi_segments
from jtcon.gui.window import MainWindow
from conftest import _FrozenDatetime, FakeSock
import wsjtx_packets as pk

ADDR = ("127.0.0.1", 2237)


@pytest.fixture(autouse=True)
def frozen_gui_today(monkeypatch):
    monkeypatch.setattr(jtcon.gui.models, "datetime", _FrozenDatetime)


@pytest.fixture
def window(qtbot, make_engine):
    """A MainWindow on a ready Engine; make(*argv, **kw) as for make_engine."""
    made = []

    def make(*argv, **kw):
        engine = make_engine("--no-color", *argv, **kw)
        win = MainWindow(engine, start_engine=False, dialogs=False)
        win.show()
        made.append(win)
        return win
    yield make
    # Delete the windows now, not at Python's last garbage collection.
    for win in made:
        win.engine._subscribers.clear()
        win.close()
        win.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def table(win) -> list:
    """The decode table as {label: text} per row."""
    model = win.decodes.decodes
    return [{label: model.text(r, key) for key, label in model.cols}
            for r in range(model.rowCount())]


def cell(win, row: int, label: str, role=Qt.ItemDataRole.DisplayRole):
    model = win.decodes.decodes
    col = [c[1] for c in model.cols].index(label)
    return model.data(model.index(row, col), role)


def command(win, text: str) -> None:
    win.cmd.edit.setText(text)
    win.cmd.edit.returnPressed.emit()


def test_spots_go_to_the_decode_table(window):
    win = window()
    win.engine.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    rows = table(win)
    assert [(r["##"], r["UTC"], r["Message"], r["Call"]) for r in rows] == [
        ("00", "123015z", "CQ W1ABC FN42", "W1ABC"), ("01", "123015z", "CQ VK3XY QF22", "VK3XY")]
    vk = rows[1]
    assert (vk["Entity"], vk["CQz"], vk["ITUz"], vk["ct"]) == ("Australia", "30", "59", "OC")
    assert vk["DXCC"] == "❌VK" and vk["Wanted"] == "DXCC CQZ CTRY"
    assert rows[0]["days"] == "590d" and rows[0]["Wanted"] == ""
    # A wanted row: red, bold, its reasons as a tooltip.  A worked call is green.
    assert cell(win, 1, "Call", Qt.ItemDataRole.ForegroundRole) == COLOURS["red"]
    assert cell(win, 1, "Message", Qt.ItemDataRole.FontRole).bold()
    assert cell(win, 1, "Grid", Qt.ItemDataRole.ToolTipRole).startswith("❌DXCC(VK)")
    assert cell(win, 0, "Call", Qt.ItemDataRole.ForegroundRole) == COLOURS["green"]
    assert cell(win, 0, "Call", Qt.ItemDataRole.ToolTipRole) is None
    assert win.log_view.lines() == []


def test_decode_columns_follow_the_config_and_inputs(window):
    win = window()
    labels = [label for _, label in win.decodes.decodes.cols]
    assert labels[:9] == ["##", "UTC", "dB", "dt", "Hz", "Mode", "Message", "Call", "Grid"]
    assert labels[-6:] == ["DXCC", "CQZ", "CTRY", "QRZ", "LoW", "Wanted"]
    assert "days" in labels and "Local" not in labels
    win = window(adif=False)
    labels = [label for _, label in win.decodes.decodes.cols]
    assert "days" not in labels and "DXCC" not in labels and labels[-1] == "Wanted"
    # The logbook loads after startup; the columns follow on the next tick.
    win.engine.logbook = object()
    win._tick()
    assert "DXCC" in [label for _, label in win.decodes.decodes.cols]


def test_the_decode_table_keeps_the_newest(window):
    win = window()
    model = win.decodes.decodes
    for i in range(DECODE_ROWS + 3):
        win.engine.datagram_received(pk.decode("CQ W1ABC FN42", snr=i % 20), ADDR)
    assert model.rowCount() == DECODE_ROWS


def test_the_decode_table_follows_until_scrolled_back(window, qtbot):
    win = window()
    for _ in range(60):
        win.engine.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)
    qtbot.wait(10)
    assert win.decodes.at_end
    win.decodes.page(-1)
    assert not win.decodes.at_end
    win._tick()
    assert win.scroll_note.isVisibleTo(win)
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    qtbot.wait(10)
    assert not win.decodes.at_end                  # stays where you scrolled
    win.decodes.to_end()
    assert win.decodes.at_end
    win._tick()
    assert not win.scroll_note.isVisibleTo(win)


def test_messages_go_to_the_log(window):
    win = window()
    win.engine._emit(Ready("[ready] Listening"))
    win.engine.log("[qrz] Downloading ✓", key="qrz-full")
    win.engine.log("[qrz] Downloading ✓✓", key="qrz-full")      # replaces the line
    win.engine.log("[qrz] Downloading ✓✓ done", key="qrz-full")
    win.engine._emit(LogLine("a\nb"))
    win.engine.log("\033[1;31mred\033[0m plain")
    assert win.log_view.lines() == ["[ready] Listening", "[qrz] Downloading ✓✓ done",
                                    "a", "b", "red plain"]
    assert table(win) == []


def test_ansi_segments():
    assert ansi_segments("plain") == [("plain", False, None)]
    assert ansi_segments("\033[2mdim\033[0m \033[1;33mbold yellow\033[0m!") == [
        ("dim", False, "dim"), (" ", False, None), ("bold yellow", True, "yellow"),
        ("!", False, None)]
    assert ansi_segments("\033[1;32mok") == [("ok", True, "green")]
    assert ansi_segments("") == []


def test_commands(window):
    win = window()
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    command(win, "foo")
    assert win.log_view.lines()[-1].startswith("[cmd] Unknown: 'foo'")
    command(win, "l")
    assert any(line.startswith("[list] 1 wanted") for line in win.log_view.lines())
    command(win, "i 00")
    assert win.log_view.lines()[-1].startswith("[ignore] VK3XY ignored for this session")
    win._tick()
    assert "ign:1" in win.status.text()
    command(win, "clear")
    assert win.log_view.lines() == [] and table(win) == []
    command(win, "   ")                              # nothing happens
    assert win.log_view.lines() == []


def test_answering_by_index_and_call(window):
    tx = FakeSock()
    win = window(tx_sock=tx)
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    command(win, "00")
    command(win, "vk3xy")
    replies = [pk.parse_reply(data) for data, _ in tx.sent if data[8:12] == b"\x00\x00\x00\x04"]
    assert [r["message"] for r in replies] == ["CQ VK3XY QF22"] * 2


def test_status_bar_and_title(window):
    win = window()
    win.engine.datagram_received(pk.status(14_074_000, de_call="K2TTA"), ADDR)
    win._tick()
    assert win.badge.text() == "RX"
    assert win.freq.text() == "14.074 MHz 20m"
    assert "de=K2TTA" in win.status.text()
    assert win.windowTitle().startswith("jtcon — 14.074 MHz 20m") and "K2TTA" in win.windowTitle()
    assert not win.changes.isVisibleTo(win)
    win.engine.datagram_received(pk.status(14_074_000, de_call="K2TTA", transmitting=True), ADDR)
    win.engine.set_scope("all")
    win._tick()
    assert win.badge.text() == "TX"
    assert win.changes.text() == "scope:all" and win.changes.isVisibleTo(win)


def test_quit_command_and_ctrl_q_close_the_window(window, qtbot):
    win = window()
    with qtbot.waitSignal(win.closing):
        command(win, "quit")
    assert not win.isVisible()
    win = window()
    ctrl_q = [s for s in win.findChildren(QShortcut) if s.key() == QKeySequence("Ctrl+Q")]
    with qtbot.waitSignal(win.closing):
        ctrl_q[0].activated.emit()      # offscreen, the window never has key focus


def test_fatal_closes_the_window(window, qtbot):
    win = window()
    with qtbot.waitSignal(win.closing):
        win.engine._emit(Fatal("[fatal] cty.dat: gone"))
    assert win.fatal_msg == "[fatal] cty.dat: gone"
    assert win.log_view.lines()[-1] == "[fatal] cty.dat: gone"


def test_the_gui_runs_the_engine_on_one_loop(make_engine, qapp, monkeypatch, capsys):
    """JtconGui end to end, on the qasync loop: the engine starts, a real UDP
    packet arrives, the window shows it; closing finishes up as on exit."""
    import qasync
    engine = make_engine("--no-color")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    engine.sock = sock
    monkeypatch.setattr(engine, "_load_inputs", lambda: True)    # loaded already
    gui = JtconGui(engine)
    seen = {}

    def send():
        out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        out.sendto(pk.decode("CQ VK3XY QF22"), sock.getsockname())
        out.close()

    def check_and_close():
        seen["rows"] = table(gui.window)
        gui.window.close()

    QTimer.singleShot(200, send)
    QTimer.singleShot(600, check_and_close)
    qapp.setQuitOnLastWindowClosed(False)
    loop = qasync.QEventLoop(qapp)
    asyncio.set_event_loop(loop)
    try:
        with loop:
            status = loop.run_until_complete(gui.run_async())
    finally:
        asyncio.set_event_loop(None)
    assert status == 0
    assert [r["Call"] for r in seen["rows"]] == ["VK3XY"]
    assert engine._stopped.is_set()
    assert sock.fileno() == -1                     # closed with the engine


def test_a_fatal_error_exits_with_status_1(make_engine, qapp, monkeypatch, capsys):
    import qasync
    engine = make_engine("--no-color")
    monkeypatch.setattr(engine, "_load_inputs",
                        lambda: engine._fatal("[fatal] cty.dat: can't download"))
    gui = JtconGui(engine)
    monkeypatch.setattr(MainWindow, "__init__", _no_dialogs(MainWindow.__init__))
    loop = qasync.QEventLoop(qapp)
    asyncio.set_event_loop(loop)
    try:
        with loop:
            status = loop.run_until_complete(gui.run_async())
    finally:
        asyncio.set_event_loop(None)
    assert status == 1
    assert "[fatal] cty.dat: can't download" in capsys.readouterr().err


def _no_dialogs(init):
    def wrapped(self, engine, start_engine=True, dialogs=True):
        init(self, engine, start_engine, dialogs=False)
    return wrapped
