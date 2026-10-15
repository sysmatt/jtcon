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
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QLabel

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


@pytest.fixture(autouse=True)
def window_settings(tmp_path, monkeypatch):
    """The window remembers itself in tmp_path/jtcon/window.ini, never the real one."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "jtcon" / "window.ini"


@pytest.fixture
def window(qtbot, make_engine):
    """A MainWindow on a ready Engine; make(*argv, **kw) as for make_engine."""
    made = []

    def make(*argv, layout="stacked", **kw):
        engine = make_engine("--no-color", *argv, **kw)
        engine.config.display.layout = layout
        win = MainWindow(engine, start_engine=False, dialogs=False)
        win.show()
        win.first_log = win.log_view.lines()
        win.log_view.reset()                    # tests start with an empty log
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


def press(win, keys: str) -> None:
    """Run what *keys* does in the window: a menu action's or a shortcut's.
    (Offscreen the window never has the keyboard, so keys can't be sent.)"""
    seq = QKeySequence(keys)
    for action in win.findChildren(QAction):
        if seq in action.shortcuts():
            action.trigger()
            return
    for shortcut in win.findChildren(QShortcut):
        if shortcut.key() == seq:
            shortcut.activated.emit()
            return
    raise KeyError(keys)


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
    with qtbot.waitSignal(win.closing):
        press(win, "Ctrl+Q")


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
    press(win, "Ctrl+S")
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


# ── Panels, layouts, columns, remembering the window ─────────────────────────

def shown(win) -> set:
    return {name for name, dock in win.docks.items() if dock.isVisibleTo(win)}


def visible_columns(view) -> list:
    """Labels of the columns showing, in screen order."""
    header, cols = view.horizontalHeader(), view.model().cols
    logical = sorted(range(len(cols)), key=header.visualIndex)
    return [cols[i][1] for i in logical if not header.isSectionHidden(i)]


def test_the_layout_presets(window):
    win = window(layout="decodes")
    assert win.layout_name == "decodes" and shown(win) == {"decodes", "log"}
    assert "Entity" in visible_columns(win.decodes)                  # all the columns
    win.set_layout("stacked")
    assert shown(win) == {"wanted", "decodes", "log"}
    assert visible_columns(win.decodes) == ["##", "UTC", "dB", "dt", "Hz", "Message"]
    w, d = win.docks["wanted"].geometry(), win.docks["decodes"].geometry()
    assert d.top() > w.bottom() - 5                                   # one above the other
    win.set_layout("side")
    w, d = win.docks["wanted"].geometry(), win.docks["decodes"].geometry()
    assert d.left() > w.right() - 5                                   # side by side
    log = win.docks["log"].geometry()
    assert log.top() > w.bottom() and log.width() >= w.width() + d.width()
    win.set_layout("wanted")
    assert shown(win) == {"wanted", "log"}


def test_layout_keys_step_through_the_presets(window):
    win = window(layout="wanted")
    press(win, "F4")
    assert win.layout_name == "side"
    press(win, "Ctrl+Right")
    assert win.layout_name == "stacked"
    press(win, "Ctrl+Down")
    assert win.layout_name == "wanted"                                # round again
    press(win, "Ctrl+Left")
    assert win.layout_name == "stacked"
    press(win, "F3")
    press(win, "Ctrl+Up")
    assert win.layout_name == "wanted"
    press(win, "F2")
    press(win, "F5")
    assert win.layout_name == "stacked"


def test_ctrl_arrows_at_cmd_are_left_to_the_layout_keys(window):
    from PySide6.QtGui import QKeyEvent
    win = window()
    for key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
        ev = QKeyEvent(QEvent.Type.ShortcutOverride, key, Qt.KeyboardModifier.ControlModifier)
        win.cmd.edit.event(ev)
        assert not ev.isAccepted()
    ev = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier)
    win.cmd.edit.event(ev)
    assert ev.isAccepted()                       # plain ← still moves the cursor


def test_arrows_scroll_the_decodes_when_the_table_is_hidden(window, qtbot):
    win = window(layout="decodes")
    for _ in range(80):
        win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    bar = win.decodes.verticalScrollBar()
    end = bar.value()
    qtbot.keyClick(win.cmd.edit, Qt.Key.Key_Up)
    assert bar.value() == end - 1 and win.wanted.selected_call is None


def test_hiding_and_showing_columns(window):
    win = window(layout="decodes")
    view = win.decodes
    menu = view.columns_menu()
    actions = {a.text(): a for a in menu.actions() if a.text()}
    assert actions["Mode"].isChecked()
    actions["Mode"].trigger()                                         # hide Mode
    assert "Mode" not in visible_columns(view)
    actions = {a.text(): a for a in view.columns_menu().actions() if a.text()}
    actions["Short: time, dB, dt, Hz, message"].trigger()
    assert visible_columns(view) == ["##", "UTC", "dB", "dt", "Hz", "Message"]
    # Each layout keeps its own decode columns.
    win.set_layout("side")
    view.set_hidden(view.hidden - {"call"})
    assert visible_columns(view)[-1] == "Call"
    win.set_layout("decodes")
    assert visible_columns(view) == ["##", "UTC", "dB", "dt", "Hz", "Message"]
    actions = {a.text(): a for a in view.columns_menu().actions() if a.text()}
    actions["All columns"].trigger()
    assert "Mode" in visible_columns(view) and "Wanted" in visible_columns(view)
    win.set_layout("side")
    assert visible_columns(view)[-1] == "Call"
    # The last column can't go.
    view.set_hidden(set(view.keys()))
    assert visible_columns(view)[-1] == "Call"
    # The Wanted table has no Short choice.
    texts = [a.text() for a in win.wanted.columns_menu().actions()]
    assert "All columns" in texts and not any(t.startswith("Short") for t in texts)


def test_moved_columns_keep_their_places_as_columns_come_and_go(window):
    win = window(layout="wanted")
    view = win.wanted
    header = view.horizontalHeader()
    keys = view.keys()
    header.moveSection(header.visualIndex(keys.index("grid")), 2)     # Grid after Call
    assert visible_columns(view)[:3] == ["##", "Call", "Grid"]
    assert view.order[:3] == ["idx", "call", "grid"]
    # A rule goes off: its column goes, the rest stay put.
    win.engine.set_rule_enabled("cqz", False)
    win.refresh_wanted(force=True)
    labels = visible_columns(view)
    assert labels[:3] == ["##", "Call", "Grid"] and "CQZ" not in labels
    win.engine.set_rule_enabled("cqz", True)
    win.refresh_wanted(force=True)
    labels = visible_columns(view)
    assert labels[:3] == ["##", "Call", "Grid"] and "CQZ" in labels
    view.reset_columns()
    assert visible_columns(view)[:3] == ["##", "Call", "QSO"] and view.order == []


def test_column_widths_are_the_users_only_when_dragged(window):
    win = window(layout="wanted")
    view = win.wanted
    col = view.keys().index("entity")
    view.setColumnWidth(col, 300)                     # Qt, not the user
    assert view.widths == {}
    view._dragging = True                             # as while the mouse is down
    view.setColumnWidth(col, 280)
    view._dragging = False
    assert view.widths == {"entity": 280}


def test_the_window_remembers_itself(window, window_settings):
    win = window(layout="stacked")
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    win.set_layout("side")
    win.resize(700, 560)                             # the offscreen screen is 800 wide
    win.wanted.set_hidden({"ct"})
    win.wanted.widths["entity"] = 250
    win.wanted.sort_by("call", reverse=True)
    win.decodes.set_hidden(win.decodes.hidden - {"call"})
    win.close()
    assert window_settings.exists()
    win = window(layout="stacked")                    # the config's layout: not used now
    assert win.layout_name == "side"
    assert win.size().width() == 700
    assert "ct" in win.wanted.hidden and win.wanted.widths == {"entity": 250}
    assert win.wanted.columnWidth(win.wanted.keys().index("entity")) == 250
    assert (win.wanted.wanted.source.sort_name, win.wanted.wanted.source.reverse) == ("call", True)
    assert visible_columns(win.decodes)[-1] == "Call"
    # Reset: as the first time, the configured layout.
    win.reset_layout()
    assert win.layout_name == "stacked"
    assert win.wanted.hidden == set() and win.wanted.widths == {}
    assert visible_columns(win.decodes) == ["##", "UTC", "dB", "dt", "Hz", "Message"]


def test_unreadable_window_settings_are_ignored(window, window_settings):
    window_settings.parent.mkdir(parents=True)
    window_settings.write_text('[General]\nwanted_columns=not json\nlayout=bogus\nstate=xyz\n')
    win = window(layout="decodes")
    assert win.layout_name == "decodes" and win.wanted.hidden == set()


def test_the_first_layout(window, monkeypatch):
    import jtcon.gui.window as gw
    assert gw.auto_layout(2560) == "side" and gw.auto_layout(1920) == "stacked"
    monkeypatch.setattr(gw, "auto_layout", lambda width: "decodes")
    assert window(layout="auto").layout_name == "decodes"


# ── Menus, toolbar, live controls ────────────────────────────────────────────

def menu(win, title: str):
    for action in win.menuBar().actions():
        if action.text().replace("&", "") == title:
            return action.menu()
    raise KeyError(title)


def items(m) -> dict:
    return {a.text().replace("&", ""): a for a in m.actions() if a.text()}


def test_the_menus_and_the_startup_hint(window):
    win = window()
    assert [a.text().replace("&", "") for a in win.menuBar().actions()] == [
        "File", "View", "Rules", "Logbook", "Help"]
    assert win.first_log[0].startswith("[keys] F1 help · F2–F5 layouts")
    assert list(items(menu(win, "View")))[:6] == [
        "Wanted", "Decodes", "Side by side", "Stacked", "Next layout", "Previous layout"]


def test_the_layout_toolbar(window):
    win = window(layout="stacked")
    bar = win.menus.toolbar
    buttons = [a for a in bar.actions() if a.text()]
    assert [a.text().replace("&", "") for a in buttons] == [
        "Wanted", "Decodes", "Side by side", "Stacked"]
    assert win.menus.layout_actions["stacked"].isChecked()
    win.menus.layout_actions["side"].trigger()
    assert win.layout_name == "side" and win.menus.layout_actions["side"].isChecked()
    press(win, "F3")                                  # the keys tick the buttons too
    assert win.menus.layout_actions["decodes"].isChecked()
    assert not win.menus.layout_actions["side"].isChecked()
    assert "F4" in win.menus.layout_actions["side"].toolTip()


def test_panels_can_be_closed_and_brought_back(window):
    win = window(layout="stacked")
    view = items(menu(win, "View"))
    view["Decodes panel"].trigger()
    assert shown(win) == {"wanted", "log"}
    view["Decodes panel"].trigger()
    assert shown(win) == {"wanted", "decodes", "log"}
    win.docks["log"].close()
    press(win, "F5")
    assert shown(win) == {"wanted", "decodes", "log"}
    win.set_layout("side")
    view["Reset window layout"].trigger()
    assert win.layout_name == "stacked"


def test_panel_titles_and_buttons_have_tooltips(window, qtbot):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QAbstractButton, QToolTip
    win = window()
    dock = win.docks["wanted"]
    tips = {b.objectName(): b.toolTip() for b in dock.findChildren(QAbstractButton)}
    assert tips["qt_dockwidget_floatbutton"].startswith("Float this panel")
    assert tips["qt_dockwidget_closebutton"].startswith("Close this panel")
    title = QHelpEvent(QEvent.Type.ToolTip, QPoint(20, 3), dock.mapToGlobal(QPoint(20, 3)))
    assert win.eventFilter(dock, title)
    assert QToolTip.text().startswith("Drag this title to move the panel")
    QToolTip.hideText()
    below = QPoint(20, dock.widget().y() + 20)
    inside = QHelpEvent(QEvent.Type.ToolTip, below, dock.mapToGlobal(below))
    assert not win.eventFilter(dock, inside)          # the table's own tooltips there


def test_the_rules_menu_switches_rules_for_the_session(window):
    win = window()
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    rules = items(menu(win, "Rules"))
    assert [t for t in rules][:3] == ["DXCC", "CQZ", "CTRY  (country)"]
    assert all(rules[t].isChecked() for t in ("DXCC", "CQZ", "CTRY  (country)"))
    rules["CQZ"].trigger()                                            # off
    assert "cqz" in win.config.rules_off and not rules["CQZ"].isChecked()
    assert "CQZ" not in [label for _, label in win.wanted.wanted.cols]
    assert "CQZ" not in [label for _, label in win.decodes.decodes.cols]
    assert wanted(win)[0]["Wanted"] == "DXCC CTRY"
    win._tick()
    assert win.changes.text() == "off:CQZ"
    rules["CQZ"].trigger()                                            # on again
    assert win.config.rules_off == set() and rules["CQZ"].isChecked()
    assert "CQZ" in [label for _, label in win.wanted.wanted.cols]


def test_scope_and_show_f7_f8(window):
    win = window()
    rules = items(menu(win, "Rules"))
    press(win, "F7")
    assert win.config.scope.decodes == "all" and rules["Check all decodes"].isChecked()
    press(win, "F8")
    assert win.config.display.show == "all" and rules["Show all decodes"].isChecked()
    win._tick()
    assert win.changes.text() == "scope:all show:all"
    press(win, "F7")
    press(win, "F8")
    assert not rules["Check all decodes"].isChecked() and not rules["Show all decodes"].isChecked()
    assert win.log_view.lines()[-1] == "[show] Showing CQs and wanted stations"


def test_f6_pops_up_the_rules_menu(window, qtbot):
    win = window()
    press(win, "F6")
    assert win.menus.rules_menu.isVisible()
    win.menus.rules_menu.close()


def test_the_logbook_menu(window, monkeypatch):
    win = window()
    logbook = menu(win, "Logbook")
    logbook.aboutToShow.emit()
    assert not items(logbook)["Sync"].isEnabled()                    # QRZ isn't set up
    calls = []
    win.engine.qrz = object()
    monkeypatch.setattr(win.engine, "sync", lambda full=False: calls.append(full))
    logbook.aboutToShow.emit()
    assert items(logbook)["Sync"].isEnabled()
    items(logbook)["Sync"].trigger()
    items(logbook)["Sync, full download"].trigger()
    assert calls == [False, True]


def test_help_keys_and_about(window):
    from jtcon import __version__
    win = window()
    press(win, "F1")
    keys = win.menus.dialogs["keys"]
    assert keys.isVisible() and "F2" in keys.findChild(QLabel).text()
    items(menu(win, "Help"))["About jtcon"].trigger()
    about = win.menus.dialogs["about"]
    assert about.isVisible() and f"jtcon {__version__}" in about.findChild(QLabel).text()
    press(win, "F1")                                  # the same box, not another
    assert win.menus.dialogs["keys"] is keys


def test_edit_config_file(window, monkeypatch, tmp_path):
    import jtcon.gui.menus as gm
    opened = []
    monkeypatch.setattr(gm, "open_in_editor", lambda path: opened.append(path) or True)
    win = window()
    edit = items(menu(win, "File"))["Edit config file"]
    # No config file yet: a starter one, private, then opened.
    edit.trigger()
    path = tmp_path / "jtcon" / "config.toml"
    assert opened == [str(path)] and path.exists() and (path.stat().st_mode & 0o777) == 0o600
    assert win.log_view.lines()[-1].startswith(f"[config] Opened {path}")
    assert "restart jtcon" in win.log_view.lines()[-1]
    # With a profile in use, the profile.
    win.config.sources = [path, tmp_path / "jtcon" / "profiles" / "pota.toml"]
    edit.trigger()
    assert opened[-1].endswith("pota.toml")
    monkeypatch.setattr(gm, "open_in_editor", lambda path: False)
    edit.trigger()
    assert win.log_view.lines()[-1].startswith("[config] Couldn't open an editor")
