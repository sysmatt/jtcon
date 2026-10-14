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
