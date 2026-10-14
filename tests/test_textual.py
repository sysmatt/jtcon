"""The Textual front end, headless: Textual's test pilot drives the app on top
of a real Engine (inputs loaded, no network); packets go straight into the engine."""

import asyncio

import pytest

pytest.importorskip("textual", minversion="8.2")

from jtcon.core.events import Fatal, LogLine, Ready
from jtcon.tui.textual_app import JtconApp
import wsjtx_packets as pk

ADDR = ("127.0.0.1", 2237)
SIZE = (200, 40)


def lines(widget) -> list:
    return [strip.text.rstrip() for strip in widget.lines]


def run(make_engine, test, *argv, **kw):
    """Run *test(app, pilot)* inside a headless JtconApp."""
    engine = make_engine("--no-color", *argv, **kw)

    async def main():
        app = JtconApp(engine, start_engine=False)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await test(app, pilot)
        return app
    return asyncio.run(main())


def test_spots_go_to_the_decode_pane_under_a_fixed_header(make_engine):
    async def test(app, pilot):
        app.engine.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)
        app.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
        await pilot.pause()
        out = lines(app.decodes)
        assert out[0].startswith("00  123015z") and "CQ W1ABC FN42" in out[0]
        assert out[1].startswith("01  123015z") and "CQ VK3XY QF22" in out[1]
        assert out[2].startswith("  *** WANTED: ❌DXCC(VK)")
        header = str(app.header.render()).splitlines()
        assert header[0].startswith("##  HHMMSSZ") and "days" in header[0]
        assert lines(app.log_strip) == []
    run(make_engine, test)


def test_messages_go_to_the_log_strip(make_engine):
    async def test(app, pilot):
        app.engine._emit(Ready("[ready] Listening"))
        app.engine.log("[qrz] Downloading ✓", key="qrz-full")
        app.engine.log("[qrz] Downloading ✓✓", key="qrz-full")     # replaces the line
        app.engine.log("[qrz] Downloading ✓✓ done", key="qrz-full")
        app.engine._emit(LogLine("a\nb"))
        await pilot.pause()
        assert lines(app.log_strip) == ["[ready] Listening", "[qrz] Downloading ✓✓ done",
                                        "a", "b"]
        assert lines(app.decodes) == []
    run(make_engine, test)


async def type_command(pilot, text):
    await pilot.press(*text, "enter")
    await pilot.pause()


def test_commands(make_engine):
    async def test(app, pilot):
        app.engine.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
        await type_command(pilot, "foo")
        assert lines(app.log_strip)[-1].startswith("[cmd] Unknown: 'foo'")
        await type_command(pilot, "l")
        assert lines(app.decodes)[-3].startswith("[list] 1 wanted")
        await type_command(pilot, "i 00")
        assert lines(app.log_strip)[-1].startswith("[ignore] VK3XY ignored for this session")
        assert app.query_one("#cmd").value == ""
        await type_command(pilot, "clear")
        assert lines(app.decodes) == [] and lines(app.log_strip) == []
        await type_command(pilot, "q")
        assert app._exit
    run(make_engine, test)


def test_status_line(make_engine):
    async def test(app, pilot):
        app._show_status()
        status = str(app.status.render())
        assert status.startswith(" RX  14.074 MHz 20m [")
        assert "de=N0CALL" in status and "SCROLL" not in status
        app.engine.transmitting = True
        app._show_status()
        assert str(app.status.render()).startswith(" TX ")
    run(make_engine, test)


def test_scrolling_back_stays_put_and_escape_returns(make_engine):
    async def test(app, pilot):
        for i in range(80):
            app.decodes.add(f"line {i}")
        await pilot.pause()
        assert app.decodes.is_vertical_scroll_end
        await pilot.press("pageup")
        await pilot.pause()
        top = app.decodes.scroll_y
        assert not app.decodes.is_vertical_scroll_end
        app._show_status()
        assert "SCROLL" in str(app.status.render())
        app.decodes.add("line 80")                       # doesn't pull the view down
        await pilot.pause()
        assert app.decodes.scroll_y == top
        await pilot.press("escape")
        await pilot.pause()
        assert app.decodes.is_vertical_scroll_end
    run(make_engine, test)


def test_fatal_closes_the_app(make_engine):
    async def test(app, pilot):
        app.engine._emit(Fatal("[adif] File not found: /x"))
        await pilot.pause()
        assert app.fatal_msg == "[adif] File not found: /x" and app._exit
    run(make_engine, test)


# ── The Wanted table ──────────────────────────────────────────────────────────

def table(app) -> list:
    """The table's rows as {column label: text}."""
    t = app.wanted
    labels = [str(c.label) for c in t.ordered_columns]
    return [{label: str(t.get_cell_at((r, c))) for c, label in enumerate(labels)}
            for r in range(t.row_count)]


def replies(app) -> list:
    """The Reply packets (type 4) sent to WSJT-X; highlights are sent too."""
    return [d for d, _ in app.engine.tx_sock.sent if d[8:12] == b"\x00\x00\x00\x04"]


async def hear(app, pilot, *messages, snr=-7):
    for m in messages:
        app.engine.datagram_received(pk.decode(m, snr=snr), ADDR)
    app._show_table(force=True)
    await pilot.pause()


def test_wanted_table_shows_only_wanted_stations(make_engine):
    async def test(app, pilot):
        await hear(app, pilot, "CQ W1ABC FN42", "CQ VK3XY QF22")
        rows = table(app)
        assert [r["Call"] for r in rows] == ["VK3XY"]          # W1ABC isn't wanted
        vk = rows[0]
        assert vk["##"] == "01" and vk["Entity"] == "Australia" and vk["CQz"] == "30"
        assert vk["Grid"] == "QF22" and vk["dB"] == "-7" and vk["×"] == "1"
        assert vk["DXCC"] == "❌VK" and vk["Wanted"] == "DXCC CQZ CTRY"
        assert [str(c.label) for c in app.wanted.ordered_columns][-3:] == ["QRZ", "LoW",
                                                                           "Wanted"]
        await hear(app, pilot, "CQ VK3XY QF22")
        assert table(app)[0]["×"] == "2"                       # one row, updated
    run(make_engine, test)


def test_wanted_table_sorts(make_engine):
    async def test(app, pilot):
        await hear(app, pilot, "CQ VK3XY QF22", snr=-20)
        await hear(app, pilot, "CQ ZL1AA RF70", snr=+5)
        await hear(app, pilot, "CQ VK4AB QG62", snr=-3)
        calls = lambda: [r["Call"] for r in table(app)]
        assert calls() == ["VK4AB", "ZL1AA", "VK3XY"]          # last heard first
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert calls() == ["ZL1AA", "VK4AB", "VK3XY"]          # best SNR first
        assert lines(app.log_strip)[-1] == "[wanted] Sorted by SNR"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert calls() == ["VK3XY", "VK4AB", "ZL1AA"]          # call
    run(make_engine, test)


def test_arrows_select_and_enter_answers(make_engine):
    async def test(app, pilot):
        await hear(app, pilot, "CQ VK3XY QF22", "CQ ZL1AA RF70")
        assert app.wanted.selected_call == "ZL1AA"             # newest on top
        await pilot.press("down")
        await pilot.pause()
        assert app.wanted.selected_call == "VK3XY"
        await pilot.press("enter")                             # empty CMD> line
        await pilot.pause()
        assert len(replies(app)) == 1 and lines(app.log_strip)[-1].startswith(
            "[cmd] ► Reply sent to VK3XY")
        await hear(app, pilot, "CQ ZL1AA RF70")                # resorts: VK3XY still selected
        assert app.wanted.selected_call == "VK3XY"
    run(make_engine, test)


def test_table_keys_ignore_and_answer(make_engine):
    async def test(app, pilot):
        await hear(app, pilot, "CQ VK3XY QF22", "CQ ZL1AA RF70")
        app.wanted.focus()
        await pilot.press("i")                                 # ignores the selected row
        await pilot.pause()
        assert [r["Call"] for r in table(app)] == ["VK3XY"]
        assert "ZL1AA" in app.engine.ignored
        await pilot.press("enter")
        await pilot.pause()
        assert lines(app.log_strip)[-1].startswith("[cmd] ► Reply sent to VK3XY")
    run(make_engine, test)


def test_single_click_selects_double_click_answers(make_engine):
    async def test(app, pilot):
        await hear(app, pilot, "CQ VK3XY QF22", "CQ ZL1AA RF70")
        await pilot.click("#wanted", offset=(10, 1))          # first row, already selected
        await pilot.pause()
        assert replies(app) == []
        await pilot.click("#wanted", offset=(10, 2))
        await pilot.pause()
        assert app.wanted.selected_call == "VK3XY" and replies(app) == []
        await pilot.click("#wanted", offset=(10, 2), times=2)
        await pilot.pause()
        assert len(replies(app)) == 1
    run(make_engine, test)


def test_stations_leave_the_table_after_the_timeout(make_engine):
    async def test(app, pilot):
        await hear(app, pilot, "CQ VK3XY QF22")
        st = app.engine.stations.find("VK3XY")
        assert [r.call for r in app.wanted.wanted_rows(st.last_seen + 599)] == ["VK3XY"]
        assert app.wanted.wanted_rows(st.last_seen + 601) == []
    run(make_engine, test)
