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
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QToolBar

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


def test_no_command_line_and_the_clear_items(window):
    from PySide6.QtWidgets import QLineEdit
    win = window()
    assert not win.findChildren(QLineEdit) and not win.findChildren(QToolBar)
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    win.log_view.add("[test] a line")
    view = items(menu(win, "View"))
    view["Clear decodes"].trigger()
    assert table(win) == [] and win.log_view.lines() == ["[test] a line"]
    view["Clear log"].trigger()
    assert win.log_view.lines() == []
    # And on the panels' right-click menus.
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    win.log_view.add("[test] a line")
    items(win.decodes.menu_for(None))["Clear decodes"].trigger()
    assert table(win) == []
    log_menu = items(win.log_view.menu())
    assert "Select All" in "".join(log_menu)                    # Qt's own items too
    log_menu["Clear log"].trigger()
    assert win.log_view.lines() == []


def test_answering_and_ignoring_from_the_decodes(window, qtbot):
    tx = FakeSock()
    win = window(tx_sock=tx)
    win.engine.set_scope("all")
    win.engine.set_show("all")
    win.engine.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)       # not wanted
    win.engine.datagram_received(pk.decode("K2TTA JA1ABC PM95"), ADDR)  # not a CQ
    win.engine.datagram_received(pk.decode("73"), ADDR)                 # no call
    assert [r["Call"] for r in table(win)] == ["W1ABC", "JA1ABC", ""]
    # Double-click a CQ: answered, wanted or not.
    rect = win.decodes.visualRect(win.decodes.decodes.index(0, 0))
    qtbot.mouseClick(win.decodes.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    qtbot.mouseDClick(win.decodes.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert replies(tx) == ["CQ W1ABC FN42"]
    win.decodes.selectRow(0)
    qtbot.keyClick(win.decodes, Qt.Key.Key_Return)
    assert replies(tx) == ["CQ W1ABC FN42"] * 2
    # No CQ from JA1ABC: Answer is greyed out; Enter says why.
    actions = items(win.decodes.menu_for("JA1ABC"))
    assert not actions["Answer JA1ABC\tEnter"].isEnabled()
    win.decodes.selectRow(1)
    qtbot.keyClick(win.decodes, Qt.Key.Key_Return)
    assert win.log_view.lines()[-1] == "[answer] No CQ from JA1ABC to answer."
    # A row with no call has only Clear.
    win.decodes.selectRow(2)
    assert win.decodes.selected_call is None
    assert list(items(win.decodes.menu_for(win.decodes.selected_call))) == ["Clear decodes"]
    # Delete ignores.
    win.decodes.selectRow(1)
    qtbot.keyClick(win.decodes, Qt.Key.Key_Delete)
    assert "JA1ABC" in win.engine.ignored
    actions = items(win.decodes.menu_for("W1ABC"))
    actions["Ignore W1ABC for this session\tDel"].trigger()
    assert "W1ABC" in win.engine.ignored


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


def test_quit_and_ctrl_q_close_the_window(window, qtbot):
    win = window()
    with qtbot.waitSignal(win.closing):
        items(menu(win, "File"))["Quit"].trigger()
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

    waited = []

    def check_and_close():
        """Every 50 ms until the decode is in (or 5 s): a busy machine is slow."""
        seen["rows"] = table(gui.window)
        waited.append(1)
        if seen["rows"] or len(waited) > 100:
            gui.window.close()
        else:
            QTimer.singleShot(50, check_and_close)

    QTimer.singleShot(200, send)
    QTimer.singleShot(250, check_and_close)
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


def test_reload_restarts_in_the_window(make_engine, qapp, monkeypatch, tmp_path):
    """File › Reload config: the config read again, a new Engine in a new
    window; the session's ignores and rule switches kept; the old Engine's
    socket closed."""
    import qasync
    from jtcon.config import Startup
    engine = make_engine("--no-color")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    engine.sock = sock
    monkeypatch.setattr(engine, "_load_inputs", lambda: True)
    conf = tmp_path / "config.toml"
    free = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    conf.write_text(f'[udp]\nhost = "127.0.0.1"\nport = {port}\n'
                    '[[wanted]]\nrule = "dxcc"\n[[wanted]]\nrule = "was"\n')

    class Gui(JtconGui):
        def new_engine(self, config, sock, tx_sock):
            new = super().new_engine(config, sock, tx_sock)
            monkeypatch.setattr(new, "_load_inputs", lambda: True)
            return new

    gui = Gui(engine, startup=Startup(conf))
    seen = {}

    def first():
        win = seen["first"] = gui.window
        win.ignore("VK3XY")
        win.engine.set_rule_enabled("dxcc", False)
        win.engine.set_rule_enabled("cqz", False)          # not in the new config
        assert items(menu(win, "File"))["Reload config"].isEnabled()
        press(win, "Ctrl+R")

    def second():
        win = seen["second"] = gui.window
        seen["log"] = win.log_view.lines()
        seen["engine"] = (win.engine, dict(win.engine.ignored), set(win.config.rules_off),
                          [w.name for w in win.config.wanted])
        seen["port"] = win.engine.sock.getsockname()[1]
        win.close()

    QTimer.singleShot(200, first)
    QTimer.singleShot(600, second)
    qapp.setQuitOnLastWindowClosed(False)               # as JtconGui.run sets it
    loop = qasync.QEventLoop(qapp)
    asyncio.set_event_loop(loop)
    try:
        with loop:
            status = loop.run_until_complete(gui.run_async())
    finally:
        asyncio.set_event_loop(None)
    assert status == 0
    assert seen["second"] is not seen["first"]
    new, ignored, off, rules = seen["engine"]
    assert new is not engine and gui.engine is new
    assert seen["port"] == port                                       # the new config's
    assert ignored == {"VK3XY": "session"} and off == {"dxcc"} and rules == ["dxcc", "was"]
    assert seen["log"][0].startswith("[keys] F1 help")                # the log carried over
    assert "[config] Reloading the config …" in seen["log"]
    reloaded = [i for i, line in enumerate(seen["log"]) if line.startswith("[config] Reloaded")]
    assert reloaded and seen["log"][-1].startswith("[ready] Listening on 127.0.0.1")
    assert sock.fileno() == -1 and new.sock.fileno() == -1           # both closed


def test_reload_problems_leave_jtcon_as_it_was(window, tmp_path):
    from jtcon.config import Startup
    win = window()
    assert not items(menu(win, "File"))["Reload config"].isEnabled()  # no reload given
    conf = tmp_path / "config.toml"
    conf.write_text('[udp]\nport = "x"\n')
    gui = JtconGui(win.engine, startup=Startup(conf))
    gui.window, win.reload = win, gui.reload_config
    win.reload_config()
    assert win.isVisible() and gui._next is None
    assert win.log_view.lines()[-1] == (f"[config] Config error: {conf}: udp.port must be"
                                        " an integer, not 'x'  (carrying on as before)")
    # The port taken by another program.
    taken = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    taken.bind(("127.0.0.1", 0))
    conf.write_text(f'[udp]\nhost = "127.0.0.1"\nport = {taken.getsockname()[1]}\n')
    win.reload_config()
    taken.close()
    assert win.isVisible() and gui._next is None
    assert win.log_view.lines()[-1].startswith("[config] Can't listen on 127.0.0.1:")


def _no_dialogs(init):
    def wrapped(self, engine, start_engine=True, dialogs=True, **kw):
        init(self, engine, start_engine, dialogs=False, **kw)
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
    win.ignore("ZS6AA")
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
    # The arrow keys select; Enter answers; a double-click too.
    assert win.focusWidget() is win.wanted          # where the keys go at first
    win.wanted.selectRow(0)
    qtbot.keyClick(win.wanted, Qt.Key.Key_Down)
    assert win.wanted.selected_call == "VK3XY"
    qtbot.keyClick(win.wanted, Qt.Key.Key_Return)
    assert replies(tx) == ["CQ VK3XY QF22"]
    qtbot.keyClick(win.wanted, Qt.Key.Key_Up)
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


def test_delete_or_i_ignores_the_selected_station(window, qtbot):
    win = window()
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    win.engine.datagram_received(pk.decode("CQ ZL2ABC RF80"), ADDR)
    wanted(win)
    win.wanted.select_call("VK3XY")
    qtbot.keyClick(win.wanted, Qt.Key.Key_Delete)
    assert "VK3XY" in win.engine.ignored and [r["Call"] for r in wanted(win)] == ["ZL2ABC"]
    assert win.log_view.lines()[-1].startswith("[ignore] VK3XY ignored for this session")
    win.wanted.select_call("ZL2ABC")
    qtbot.keyClick(win.wanted, Qt.Key.Key_I)
    assert wanted(win) == []


def test_the_right_click_menu(window, monkeypatch):
    tx = FakeSock()
    win = window(tx_sock=tx)
    win.engine.datagram_received(pk.decode("CQ VK3XY/P QF22"), ADDR)
    wanted(win)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    actions = items(win.wanted.menu_for("VK3XY/P"))
    assert list(actions) == ["Answer VK3XY/P\tEnter", "Ignore VK3XY/P for this session\tDel",
                             "Copy call", "Look up on QRZ.com"]
    actions["Copy call"].trigger()
    assert QApplication.clipboard().text() == "VK3XY/P"
    actions["Look up on QRZ.com"].trigger()
    assert opened == ["https://www.qrz.com/db/VK3XY"]          # the home call
    actions["Answer VK3XY/P\tEnter"].trigger()
    assert replies(tx) == ["CQ VK3XY/P QF22"]
    actions["Ignore VK3XY/P for this session\tDel"].trigger()
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


def test_ctrl_arrows_in_the_tables_are_left_to_the_layout_keys(window):
    from PySide6.QtGui import QKeyEvent
    win = window()
    for view in (win.wanted, win.decodes):
        for key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            ev = QKeyEvent(QEvent.Type.ShortcutOverride, key, Qt.KeyboardModifier.ControlModifier)
            ev.ignore()
            view.event(ev)
            assert not ev.isAccepted()


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


def test_lock_panels(window, window_settings):
    from PySide6.QtWidgets import QDockWidget
    win = window()
    lock = items(menu(win, "View"))["Lock panels"]
    # Locked at first: no title bars, no dragging or floating.
    assert win.locked and lock.isChecked()
    for dock in win.docks.values():
        assert dock.titleBarWidget() is not None
        assert not dock.features() & QDockWidget.DockWidgetFeature.DockWidgetMovable
    assert win.docks["decodes"].widget().y() < 5                       # no room taken
    lock.trigger()
    assert not win.locked
    for dock in win.docks.values():
        assert dock.titleBarWidget() is None
        assert dock.features() & QDockWidget.DockWidgetFeature.DockWidgetMovable
    # A floating panel keeps its title while locked, to be put back.
    win.docks["log"].setFloating(True)
    lock.trigger()
    assert win.locked and win.docks["log"].titleBarWidget() is None
    assert win.docks["wanted"].titleBarWidget() is not None
    win.docks["log"].setFloating(False)
    assert win.docks["log"].titleBarWidget() is not None
    # Remembered.
    lock.trigger()
    win.close()
    assert not window().locked


def test_the_station_menu(window, monkeypatch):
    tx = FakeSock()
    win = window(tx_sock=tx)
    station = menu(win, "Station")
    win.menus.fill_station()
    assert list(items(station)) == ["Select a station in the Wanted or Decodes panel",
                                    "Ignored stations…"]
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    win.engine.datagram_received(pk.decode("CQ ZL2ABC RF80"), ADDR)
    wanted(win)
    win.wanted.select_call("VK3XY")
    win.decodes.selectRow(1)                          # ZL2ABC
    win.menus.fill_station()
    assert list(items(station))[:2] == ["Answer VK3XY\tEnter", "Ignore VK3XY for this session\tDel"]
    win.decodes.focused.emit()                        # the decodes used last
    win.menus.fill_station()
    items(station)["Answer ZL2ABC\tEnter"].trigger()
    assert replies(tx) == ["CQ ZL2ABC RF80"]
    win.set_layout("wanted")                          # the decodes hidden: the Wanted row
    win.menus.fill_station()
    items(station)["Ignore VK3XY for this session\tDel"].trigger()
    assert "VK3XY" in win.engine.ignored


def test_the_ignored_stations_box(window):
    win = window()
    win.engine.ignored["W1AW"] = "config"            # as from [filters] ignore
    win.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    win.ignore("VK3XY")
    items(menu(win, "Station"))["Ignored stations…"].trigger()
    box = win.menus.dialogs["ignored"]
    assert box.isVisible() and box.calls() == ["VK3XY", "W1AW"]
    assert box.list.topLevelItem(1).text(1) == "the config file"
    assert not box.unignore_button.isEnabled()
    # Unignore; ignoring elsewhere shows up here; Add.
    box.list.topLevelItem(0).setSelected(True)
    assert box.unignore_button.isEnabled()
    box.unignore_button.click()
    assert box.calls() == ["W1AW"] and "VK3XY" not in win.engine.ignored
    win.ignore("ZL2ABC")
    assert box.calls() == ["W1AW", "ZL2ABC"]
    box.entry.setText("k1abc")
    box.entry.returnPressed.emit()
    assert box.calls() == ["K1ABC", "W1AW", "ZL2ABC"] and box.entry.text() == ""
    box.entry.setText("hello")
    box.entry.returnPressed.emit()
    assert box.message.text() == "HELLO isn't a callsign." and len(box.calls()) == 3
    win._tick()
    assert "ign:3" in win.status.text()


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


# ── Menus and live controls ───────────────────────────────────────────────────

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
        "File", "View", "Station", "Rules", "Logbook", "Help"]
    assert win.first_log[0].startswith("[keys] F1 help · F2–F5 layouts")
    assert "double-click or Enter answers" in win.first_log[0]
    assert list(items(menu(win, "View")))[:5] == [
        "Layout", "Wanted panel", "Decodes panel", "Log panel", "Lock panels"]


def test_the_layout_menu(window):
    win = window(layout="stacked")
    layouts = items(menu(win, "View"))["Layout"].menu()
    assert list(items(layouts)) == ["Wanted", "Decodes", "Side by side", "Stacked",
                                    "Next layout", "Previous layout"]
    assert win.menus.layout_actions["stacked"].isChecked()
    items(layouts)["Side by side"].trigger()
    assert win.layout_name == "side" and win.menus.layout_actions["side"].isChecked()
    press(win, "F3")                                  # the keys tick the menu too
    assert win.menus.layout_actions["decodes"].isChecked()
    assert not win.menus.layout_actions["side"].isChecked()
    assert "F4" in win.menus.layout_actions["side"].toolTip()
    assert not win.findChildren(QToolBar, "layout-bar")             # no button row (Matt)


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
    win.set_locked(False)                             # the titles show
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
    assert "File › Reload config" in win.log_view.lines()[-1]
    # With a profile in use, the profile.
    win.config.sources = [path, tmp_path / "jtcon" / "profiles" / "pota.toml"]
    edit.trigger()
    assert opened[-1].endswith("pota.toml")
    monkeypatch.setattr(gm, "open_in_editor", lambda path: False)
    edit.trigger()
    assert win.log_view.lines()[-1].startswith("[config] Couldn't open an editor")


# ── Look and feel ─────────────────────────────────────────────────────────────

def test_dark_by_default_and_the_theme_menu(window):
    from jtcon.gui.theme import is_dark
    app = QApplication.instance()
    win = window()
    assert win.theme == "dark" and is_dark(app) and app.style().name() == "fusion"
    themes = items(menu(win, "View"))["Theme"].menu()
    assert list(items(themes))[:3] == ["Dark", "Light", "System"]
    themes.aboutToShow.emit()
    assert win.menus.theme_actions["dark"].isChecked()
    items(themes)["Light"].trigger()
    assert win.theme == "light" and not is_dark(app)
    items(themes)["System"].trigger()
    import jtcon.gui.theme as th
    assert app.style().name() == th._original["style"].lower()
    assert app.palette().color(app.palette().ColorRole.Window) == \
        th._original["palette"].color(app.palette().ColorRole.Window)
    assert win.config.display.theme == "dark"          # the session only


def test_the_configured_theme(window):
    from jtcon.gui.theme import is_dark
    win = window(config={"display": {"theme": "light"}})
    assert win.theme == "light" and not is_dark(QApplication.instance())
    win.set_theme("dark")


def test_text_size(window):
    win = window()
    base = win.decodes.font().pointSizeF()
    row = win.decodes.verticalHeader().defaultSectionSize()
    press(win, "Ctrl++")
    press(win, "Ctrl++")
    assert win.text_size == 2 and win.decodes.font().pointSizeF() == base + 2
    assert win.decodes.verticalHeader().defaultSectionSize() > row
    assert win.log_view.font().pointSizeF() > base and win.badge.font().bold()
    press(win, "Ctrl+-")
    assert win.text_size == 1
    for _ in range(30):
        press(win, "Ctrl+-")
    assert win.text_size == -4                          # no smaller than that
    press(win, "Ctrl+0")
    assert win.text_size == 0 and win.decodes.font().pointSizeF() == base
    # Remembered with the window.
    win.set_text_size(3)
    win.close()
    win = window()
    assert win.text_size == 3 and win.decodes.font().pointSizeF() == base + 3


def test_the_icon(window):
    win = window()
    assert not win.windowIcon().isNull()
    assert win.windowIcon().pixmap(32).width() == 32


# ── Settings (File › Settings) ────────────────────────────────────────────────

SHACK = """\
# My shack.
[udp]
port = 2238   # WSJT-X sends here

[display]
theme = "light"

[filters]
ignore = ["W1AW"]

[inputs]
adif = ["~/logs/old.adi", "~/logs/older.adi"]
"""


def settings(win, tmp_path, text=SHACK, **startup):
    from jtcon.config import Startup
    from jtcon.gui.settings import SettingsDialog
    conf = tmp_path / "config.toml"
    if text is not None:
        conf.write_text(text)
    win.startup = Startup(conf, **startup)
    reloads = win.reloads = []
    win.reload = lambda: reloads.append(1)
    return SettingsDialog(win), conf


def test_every_setting_has_a_field_on_one_page():
    from jtcon.gui.settings import FIELDS, PAGES, schema_keys
    on_pages = [keys for _, keys in PAGES for keys in keys]
    assert sorted(on_pages) == sorted(FIELDS) == sorted(schema_keys())


def test_the_settings_show_the_file_and_the_defaults(window, tmp_path):
    win = window()
    box, _ = settings(win, tmp_path)
    f = box.fields
    assert f["udp", "port"].value() == 2238 and f["udp", "host"].value() == "0.0.0.0"
    assert f["display", "theme"].value() == "light" and f["display", "show"].value() == "cq"
    assert f["filters", "ignore"].edit.text() == "W1AW"
    assert f["inputs", "adif"].value() == ["~/logs/old.adi", "~/logs/older.adi"]
    assert f["actions", "highlight", "background"].value() == "#ffa500"
    assert f["actions", "highlight", "enabled"].value() is True
    assert f["inputs", "qrz", "api_key"].value() is None
    assert not box.changes() and box.save_button.isEnabled()
    assert [box.pages.item(i).text() for i in range(box.pages.count())] == [
        "WSJT-X", "Display", "Wanted rules", "Checking", "Logs and data", "QRZ logbook",
        "Actions"]
    assert str(tmp_path / "config.toml") in box.windowTitle()


def test_saving_writes_only_the_changes_then_reloads(window, tmp_path):
    win = window()
    box, conf = settings(win, tmp_path)
    f = box.fields
    f["udp", "port"].set(2240)
    f["udp", "proxy"].edit.setText("2238, 2239")
    f["filters", "ignore"].edit.setText("")                 # empty: back to the default
    f["actions", "highlight", "background"].set("#00ff00")
    f["inputs", "qrz", "api_key"].edit.setText("ABCD-1234")
    box.save()
    text = conf.read_text()
    assert text.startswith("# My shack.\n[udp]\nport = 2240   # WSJT-X sends here\n")
    assert "proxy = [2238, 2239]" in text and "ignore" not in text
    assert '[actions.highlight]\nbackground = "#00ff00"' in text
    assert 'api_key = "ABCD-1234"' in text
    assert 'theme = "light"' in text and "host" not in text      # untouched: not written
    from jtcon.config import load_config
    cfg = load_config(conf)
    assert (cfg.udp.port, cfg.udp.proxy, cfg.filters.ignore) == (2240, [2238, 2239], [])
    assert box.result() == QDialog.DialogCode.Accepted
    assert win.reloads == [1]
    backup = next(tmp_path.glob("config.toml.*.bak"))
    assert backup.read_text() == SHACK
    assert win.log_view.lines()[-2] == f"[config] Saved {conf} (the old one: {backup.name})"
    assert "ABCD" not in "\n".join(win.log_view.lines())       # the key never logged


def test_nothing_changed_saves_nothing(window, tmp_path):
    win = window()
    box, conf = settings(win, tmp_path)
    box.save()
    assert win.log_view.lines()[-1] == "[config] Settings: nothing changed."
    assert win.reloads == [] and not list(tmp_path.glob("*.bak"))


def test_no_config_file_yet(window, tmp_path):
    win = window()
    box, conf = settings(win, tmp_path, text=None)
    assert box.fields["udp", "port"].value() == 2237
    box.fields["display", "theme"].set("system")
    box.save()
    assert conf.read_text().startswith("# jtcon configuration.")    # the starter, commented
    from jtcon.config import load_config
    assert load_config(conf).display.theme == "system"
    assert win.reloads == [1]


def test_bad_values_are_shown_and_nothing_is_written(window, tmp_path):
    win = window()
    box, conf = settings(win, tmp_path)
    box.fields["udp", "proxy"].edit.setText("2238 abc")
    box.save()
    assert box.message.text() == "Forward to ports: 'abc' isn't a port number"
    assert box.pages.currentRow() == 0
    box.fields["udp", "proxy"].edit.setText("")
    box.fields["display", "wanted_timeout_minutes"].set(0)
    box.save()
    assert "display.wanted_timeout_minutes must be 1 or more" in box.message.text()
    assert box.pages.currentRow() == 1                          # the Display page
    box.fields["display", "wanted_timeout_minutes"].set(10)
    box.fields["filters", "ignore"].edit.setText("W1AW hello")
    box.save()
    assert box.message.text() == "Ignore: HELLO isn't a callsign"
    assert conf.read_text() == SHACK and win.reloads == []
    assert box.result() != QDialog.DialogCode.Accepted


def test_a_file_edited_meanwhile_keeps_those_edits(window, tmp_path):
    win = window()
    box, conf = settings(win, tmp_path)
    conf.write_text(SHACK + '\n[scope]\ndecodes = "all"\n')        # in an editor, say
    box.fields["udp", "port"].set(2241)
    box.save()
    text = conf.read_text()
    assert "port = 2241" in text and 'decodes = "all"' in text


def test_settings_the_command_line_or_a_profile_sets_are_marked(window, tmp_path):
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "pota.toml").write_text('[display]\ntheme = "dark"\n')
    win = window()
    box, _ = settings(win, tmp_path, profile="pota",
                      flags={("udp", "port"): "--port 2240",
                             ("filters", "ignore"): "--ignore K1ABC"})
    assert box.notes(("udp", "port")) == ["The command line's --port 2240 wins this run."]
    assert box.notes(("filters", "ignore")) == [
        "The command line adds to this list this run: --ignore K1ABC."]
    assert box.notes(("display", "theme")) == ["Profile pota sets this: its value wins."]
    assert box.notes(("udp", "host")) == []
    marks = [w.text() for w in box.findChildren(QLabel) if w.text().startswith("⚑")]
    assert len(marks) == 3


def test_the_settings_menu_item(window, tmp_path):
    win = window()
    assert not items(menu(win, "File"))["Settings…"].isEnabled()     # no startup given
    settings(win, tmp_path)
    win.menus.settings.setEnabled(True)                       # as with a startup
    press(win, "Ctrl+,")
    box = win.menus.dialogs["settings"]
    assert box.isVisible()
    press(win, "Ctrl+,")                                       # a fresh one
    assert win.menus.dialogs["settings"] is not box


MY_RULES = SHACK + """
# My rules.
[[wanted]]
rule = "dxcc"   # the big one

[[wanted]]
rule = "was"

[[wanted]]
rule = "pota"
"""


def rule_rows(page) -> list:
    return [page.list.item(i).text() for i in range(page.list.count())]


def test_the_wanted_rules_page_with_the_default_rules(window, tmp_path):
    win = window()
    box, conf = settings(win, tmp_path)               # SHACK has no [[wanted]]
    page = box.wanted_page
    assert rule_rows(page) == ["DXCC  —  DXCC entity", "CQZ  —  CQ zone (WAZ)",
                               "CTRY  —  Country name"]
    assert not page.from_file and not page.changed
    box.fields["udp", "port"].set(2240)
    box.save()
    assert "[[wanted]]" not in conf.read_text()        # unchanged: not written
    # Add WAS by QRZ, named, and move it to the top.
    box, conf = settings(win, tmp_path, text=conf.read_text())
    page = box.wanted_page
    page.add("was")
    page.editors["name"].setText("WAS-QRZ")
    assert rule_rows(page)[-1].startswith("WAS-QRZ  —")             # as it's typed
    page.editors["confirm"].boxes["lotw"].setChecked(False)
    page.editors["confirm"].boxes["qrz"].setChecked(True)
    for _ in range(3):
        page.move(-1)
    assert rule_rows(page)[0] == "WAS-QRZ  —  Worked All States; needs hamdat"
    assert not page.up_button.isEnabled() and page.down_button.isEnabled()
    box.save()
    from jtcon.config import load_config
    wanted = load_config(conf).wanted
    assert [(w.name, w.settings.get("confirm")) for w in wanted] == [
        ("WAS-QRZ", ["qrz"]), ("dxcc", ["lotw"]), ("cqz", ["lotw"]), ("country", ["lotw"])]
    assert conf.read_text().endswith('[[wanted]]\nrule = "country"\n')


def test_changing_the_files_rules_keeps_their_comments(window, tmp_path):
    win = window()
    box, conf = settings(win, tmp_path, text=MY_RULES)
    page = box.wanted_page
    assert page.from_file and len(rule_rows(page)) == 3
    page.list.setCurrentRow(1)                         # WAS: worked is enough
    page.editors["confirm"].boxes["lotw"].setChecked(False)
    page.list.setCurrentRow(2)
    page.remove()                                      # POTA
    assert rule_rows(page) == ["DXCC  —  DXCC entity", "WAS  —  Worked All States; needs hamdat"]
    assert page.list.currentRow() == 1
    box.save()
    text = conf.read_text()
    assert '# My rules.\n[[wanted]]\nrule = "dxcc"   # the big one\n' in text
    assert '[[wanted]]\nrule = "was"\nconfirm = []\n' in text and "pota" not in text
    from jtcon.config import load_config
    assert load_config(conf).wanted[1].settings["confirm"] == []


def test_a_rule_missing_its_settings_is_shown(window, tmp_path):
    win = window()
    box, conf = settings(win, tmp_path, text=MY_RULES)
    box.pages.setCurrentRow(0)
    box.wanted_page.add("callsigns")                   # needs files
    box.save()
    assert "needs a 'files' setting" in box.message.text()
    assert box.pages.currentRow() == box.wanted_row
    assert conf.read_text() == MY_RULES
    (tmp_path / "wanted.txt").write_text("^K1\n")
    box.wanted_page.editors["files"].edit.setPlainText("wanted.txt")   # beside the config
    box.save()
    assert '[[wanted]]\nrule = "callsigns"\nfiles = ["wanted.txt"]' in conf.read_text()


def test_rules_changed_in_the_file_meanwhile(window, tmp_path):
    win = window()
    box, conf = settings(win, tmp_path, text=MY_RULES)
    conf.write_text(MY_RULES.replace('rule = "pota"', 'rule = "sota"'))
    box.wanted_page.remove()
    box.save()
    assert box.message.text().startswith("The rules in the config file changed")
    assert "sota" in conf.read_text()


def test_the_command_line_and_a_profiles_rules_are_marked(window, tmp_path):
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "pota.toml").write_text('[[wanted]]\nrule = "pota"\n')
    win = window()
    box, _ = settings(win, tmp_path, profile="pota", flags={("wanted",): "--wanted dxcc"})
    assert box.notes(("wanted",)) == [
        "The command line's --wanted dxcc wins this run: these rules are used when jtcon"
        " starts without it.",
        "Profile pota has its own rules, which win over these."]
    marks = [w.text() for w in box.wanted_page.findChildren(QLabel) if w.text().startswith("⚑")]
    assert len(marks) == 2


def test_a_rule_file_that_isnt_there_is_shown(window, tmp_path):
    win = window()
    box, conf = settings(win, tmp_path, text=MY_RULES)
    box.wanted_page.add("callsigns")
    box.wanted_page.editors["files"].edit.setPlainText(str(tmp_path / "nope.txt"))
    box.save()
    assert box.message.text() == (f"Wanted rule callsigns: {tmp_path / 'nope.txt'}:"
                                  " no such file")
    assert conf.read_text() == MY_RULES and win.reloads == []
    (tmp_path / "nope.txt").write_text("^K1\n")
    box.save()
    assert "nope.txt" in conf.read_text() and win.reloads == [1]


def test_after_a_reload_a_fatal_error_keeps_the_window(make_engine, qtbot):
    engine = make_engine("--no-color")
    win = MainWindow(engine, start_engine=False, dialogs=False, reload=lambda: None,
                     log=["[ready] earlier"])
    win.show()
    engine._emit(Fatal("[wanted] Cannot read pattern file: nope.txt"))
    assert win.isVisible() and win.fatal_msg
    assert win.log_view.lines()[-1].startswith("[config] jtcon has stopped: fix the settings")
    win.close()
