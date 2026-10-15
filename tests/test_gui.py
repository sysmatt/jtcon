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
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication

import jtcon.gui.models
from jtcon.core.events import Fatal, LogLine, Ready
from jtcon.gui.app import JtconGui
from jtcon.gui.models import COLOURS, DECODE_ROWS
from jtcon.ui.wanted import CHANGE_SHOWN_S
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


def wanted(win) -> list:
    """The Wanted table as {label: text} per row, after a refresh."""
    win.refresh_wanted(force=True)
    model = win.wanted.wanted
    return [{label: model.text(r, key) for key, label in model.cols}
            for r in range(model.rowCount())]


def wanted_cell(win, row: int, label: str, role=Qt.ItemDataRole.DisplayRole):
    model = win.wanted.wanted
    col = [c[1] for c in model.cols].index(label)
    return model.data(model.index(row, col), role)


def replies(tx) -> list:
    return [pk.parse_reply(data)["message"] for data, _ in tx.sent
            if data[8:12] == b"\x00\x00\x00\x04"]


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
    command(win, "   ")                  # a blank line answers the selected station
    assert win.log_view.lines() == ["[cmd] No station selected in the Wanted table."]


def test_answering_by_index_and_call(window):
    tx = FakeSock()
    win = window(tx_sock=tx)
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    command(win, "00")
    command(win, "vk3xy")
    assert replies(tx) == ["CQ VK3XY QF22"] * 2


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


# ── The Wanted table ──────────────────────────────────────────────────────────

def test_the_wanted_table_shows_only_wanted_stations(window):
    win = window()
    win.engine.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)      # worked: not wanted
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22", snr=-12), ADDR)
    rows = wanted(win)
    assert [r["Call"] for r in rows] == ["VK3XY"]
    vk = rows[0]
    assert (vk["##"], vk["QSO"], vk["Entity"], vk["CQz"], vk["dB"], vk["DXCC"], vk["Wanted"]) == (
        "01", "📣", "Australia", "30", "-12", "❌VK", "DXCC CQZ CTRY")
    labels = [label for _, label in win.wanted.wanted.cols]
    assert labels[:3] == ["##", "Call", "QSO"] and labels[-1] == "Wanted"
    assert wanted_cell(win, 0, "Call", Qt.ItemDataRole.ForegroundRole) == COLOURS["red"]
    assert wanted_cell(win, 0, "Wanted", Qt.ItemDataRole.FontRole).bold()
    assert wanted_cell(win, 0, "Grid", Qt.ItemDataRole.ToolTipRole).startswith("❌DXCC(VK)")
    # Heard sending reports: the QSO column follows.
    win.engine.datagram_received(pk.decode("W1ABC VK3XY -10"), ADDR)
    assert wanted(win)[0]["QSO"] == "📶"


def test_a_changed_row_lights_up_for_a_moment(window):
    win = window()
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    model = win.wanted.wanted
    model.refresh()
    idx = model.index(0, 0)
    assert model.data(idx, Qt.ItemDataRole.BackgroundRole) == COLOURS["red"]
    model.refresh(model.rows[0].last_seen + CHANGE_SHOWN_S + 0.1)
    assert model.data(idx, Qt.ItemDataRole.BackgroundRole) is None


def test_stations_leave_the_table_after_the_timeout(window):
    win = window()
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    model = win.wanted.wanted
    st = win.engine.stations.find("VK3XY")
    model.refresh(st.last_seen + 599)
    assert model.rowCount() == 1
    model.refresh(st.last_seen + 601)
    assert model.rowCount() == 0


def test_the_selection_follows_the_station_as_rows_move(window):
    win = window()
    for call in ("CQ VK3XY QF22", "CQ ZL2ABC RF80", "CQ ZS6AA KG33"):
        win.engine.datagram_received(pk.decode(call), ADDR)
    wanted(win)
    win.wanted.select_call("VK3XY")
    win.wanted.sort_by("call")
    assert [r["Call"] for r in wanted(win)] == ["VK3XY", "ZL2ABC", "ZS6AA"]
    assert win.wanted.selected_call == "VK3XY"
    win.wanted.sort_by("call", reverse=True)
    assert [r["Call"] for r in wanted(win)] == ["ZS6AA", "ZL2ABC", "VK3XY"]
    assert win.wanted.selected_call == "VK3XY"
    # A station leaves; the selection stays on VK3XY.
    command(win, "ignore ZS6AA")
    assert [r["Call"] for r in wanted(win)] == ["ZL2ABC", "VK3XY"]
    assert win.wanted.selected_call == "VK3XY"
    # A new one arrives.
    win.engine.datagram_received(pk.decode("CQ PY2AA GG66"), ADDR)
    assert [r["Call"] for r in wanted(win)] == ["ZL2ABC", "VK3XY", "PY2AA"]
    assert win.wanted.selected_call == "VK3XY"


def test_sorting_by_header_and_ctrl_s(window):
    win = window()
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22", snr=-20), ADDR)
    win.engine.datagram_received(pk.decode("CQ ZL2ABC RF80", snr=-5), ADDR)
    header = win.wanted.horizontalHeader()
    col = lambda label: [c[1] for c in win.wanted.wanted.cols].index(label)
    assert header.sortIndicatorSection() == col("Seen")                   # last heard
    header.sectionClicked.emit(col("dB"))
    assert [r["Call"] for r in wanted(win)] == ["ZL2ABC", "VK3XY"]       # strongest first
    assert win.log_view.lines()[-1] == "[wanted] Sorted by SNR"
    assert header.sortIndicatorSection() == col("dB")
    header.sectionClicked.emit(col("dB"))                                 # again: reversed
    assert [r["Call"] for r in wanted(win)] == ["VK3XY", "ZL2ABC"]
    assert win.log_view.lines()[-1] == "[wanted] Sorted by SNR, reversed"
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
    header.sectionClicked.emit(col("Grid"))                               # doesn't sort
    assert win.wanted.wanted.source.sort_name == "SNR"
    assert header.sortIndicatorSection() == col("dB")
    ctrl_s = [s for s in win.findChildren(QShortcut) if s.key() == QKeySequence("Ctrl+S")]
    ctrl_s[0].activated.emit()
    assert win.log_view.lines()[-1] == "[wanted] Sorted by call"
    assert [r["Call"] for r in wanted(win)] == ["VK3XY", "ZL2ABC"]
    assert header.sortIndicatorSection() == col("Call")


def test_answering_from_the_table(window, qtbot):
    tx = FakeSock()
    win = window(tx_sock=tx)
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    win.engine.datagram_received(pk.decode("CQ ZL2ABC RF80"), ADDR)
    wanted(win)                                     # newest first: ZL2ABC, VK3XY
    # ↑↓ at CMD> select; Enter on an empty line answers.
    qtbot.keyClick(win.cmd.edit, Qt.Key.Key_Down)
    assert win.wanted.selected_call == "ZL2ABC"
    qtbot.keyClick(win.cmd.edit, Qt.Key.Key_Down)
    qtbot.keyClick(win.cmd.edit, Qt.Key.Key_Down)    # stops at the last row
    assert win.wanted.selected_call == "VK3XY"
    qtbot.keyClick(win.cmd.edit, Qt.Key.Key_Return)
    assert replies(tx) == ["CQ VK3XY QF22"]
    # Enter in the table answers; a double-click too.
    qtbot.keyClick(win.cmd.edit, Qt.Key.Key_Up)
    qtbot.keyClick(win.wanted, Qt.Key.Key_Return)
    assert replies(tx)[-1] == "CQ ZL2ABC RF80"
    rect = win.wanted.visualRect(win.wanted.wanted.index(1, 1))
    qtbot.mouseClick(win.wanted.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    qtbot.mouseDClick(win.wanted.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert replies(tx)[-1] == "CQ VK3XY QF22"
    # A single click only selects.
    rect = win.wanted.visualRect(win.wanted.wanted.index(0, 1))
    qtbot.mouseClick(win.wanted.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert win.wanted.selected_call == "ZL2ABC" and len(replies(tx)) == 3


def test_i_ignores_the_selected_station(window, qtbot):
    win = window()
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    wanted(win)
    win.wanted.select_call("VK3XY")
    qtbot.keyClick(win.wanted, Qt.Key.Key_I)
    assert "VK3XY" in win.engine.ignored and wanted(win) == []
    assert win.log_view.lines()[-1].startswith("[ignore] VK3XY ignored for this session")


def test_the_right_click_menu(window, monkeypatch):
    tx = FakeSock()
    win = window(tx_sock=tx)
    win.engine.datagram_received(pk.decode("CQ VK3XY/P QF22"), ADDR)
    wanted(win)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    menu = win.wanted.menu_for("VK3XY/P")
    actions = {a.text(): a for a in menu.actions() if a.text()}
    assert list(actions) == ["Answer VK3XY/P", "Ignore for this session", "Copy call",
                             "Look up on QRZ.com"]
    actions["Copy call"].trigger()
    assert QApplication.clipboard().text() == "VK3XY/P"
    actions["Look up on QRZ.com"].trigger()
    assert opened == ["https://www.qrz.com/db/VK3XY"]          # the home call
    actions["Answer VK3XY/P"].trigger()
    assert replies(tx) == ["CQ VK3XY/P QF22"]
    actions["Ignore for this session"].trigger()
    assert "VK3XY" in win.engine.ignored and wanted(win) == []
