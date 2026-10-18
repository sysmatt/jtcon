"""The Engine: packets in, events and WSJT-X messages out; log reloads; startup."""

import asyncio
import os
import shutil
import socket
import sys
import threading

import pytest

import jtcon.core.adif
import jtcon.core.inputs
from jtcon.config import merge
from jtcon.core.engine import Engine
from jtcon.core.events import (Fatal, InputReloaded, LogLine, Ready, SpotEvent, StationUpdated,
                               StatusChanged)
import wsjtx_packets as pk
from conftest import CTY_PATH, LOG_PATH, FakeSock, load_adif, make_config

ADDR = ("127.0.0.1", 2237)
ORANGE = ((255, 165, 0), (0, 0, 0))


def sent(engine) -> list:
    """Everything sent to WSJT-X, parsed, as (type, callsign or message, colours)."""
    out = []
    for data, addr in engine.tx_sock.sent:
        m = pk.parse_sent(data)
        if m["type"] == 13:
            out.append(("highlight", m["callsign"], (m["background"], m["foreground"])))
        else:
            out.append(("reply", m["message"]))
    return out


def spots(engine) -> list:
    return [e for e in engine.events if isinstance(e, SpotEvent)]


def logs(engine) -> list:
    return [e.text for e in engine.events if isinstance(e, LogLine)]


# ── Decodes ───────────────────────────────────────────────────────────────────

def test_cq_becomes_a_spot_with_a_station(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("CQ DX VK3XY QF22"), ADDR)
    (ev,) = spots(e)
    assert ev.result.callsign == "VK3XY" and ev.result.new_alert
    assert ev.station is e.stations.find("VK3XY") and ev.station.addr == ADDR
    assert ev.station.decode.message == "CQ DX VK3XY QF22"
    assert ev.ctx.dial_freq == 14_074_000


def test_buffered_stations_keep_their_qso_stage(make_engine, monkeypatch):
    import time
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    e = make_engine()                                   # scope "cq"
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    st = e.stations.find("VK3XY")
    assert (st.stage, st.heard) == ("cq", 1000.0)
    for t, msg, stage in ((1015, "W1ABC VK3XY QF22", "answer"),
                          (1030, "W1ABC VK3XY -12", "report"),
                          (1045, "W1ABC VK3XY RR73", "73")):
        now[0] = t
        e.datagram_received(pk.decode(msg), ADDR)
        assert (st.stage, st.heard) == (stage, t)
    # Nothing the rules or replies use has changed: still the CQ, heard at 1000.
    assert st.decode.message == "CQ VK3XY QF22" and st.last_seen == 1000.0 and st.count == 1
    assert len(spots(e)) == 1
    e.datagram_received(pk.decode("VK3XY ZL1AA -05"), ADDR)    # VK3XY receiving: no change
    assert st.stage == "73"


def test_rules_scope_and_show_switch_for_the_session(make_engine):
    e = make_engine()                                   # dxcc, cqz, country
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    st = e.stations.find("VK3XY")
    assert [h.rule for h in st.wanted] == ["DXCC", "CQZ", "CTRY"]
    e.set_rule_enabled("dxcc", False)
    assert [h.rule for h in st.wanted] == ["CQZ", "CTRY"]      # judged again at once
    assert logs(e)[-1] == "[rules] dxcc off"
    assert [w.name for w in e.config.active_wanted] == ["cqz", "country"]
    assert [w.name for w in e.config.wanted] == ["dxcc", "cqz", "country"]   # config intact
    e.set_rule_enabled("cqz", False)
    e.set_rule_enabled("country", False)
    assert st.wanted == []
    e.set_rule_enabled("dxcc", True)
    assert [h.rule for h in st.wanted] == ["DXCC"] and logs(e)[-1] == "[rules] dxcc on"
    n = len(logs(e))
    e.set_rule_enabled("dxcc", True)                    # already on: nothing happens
    assert len(logs(e)) == n
    assert "rules_off" not in e.config.as_dict()

    e.datagram_received(pk.decode("W1ABC ZL1AA -12"), ADDR)   # not a CQ: not checked
    assert e.stations.find("ZL1AA") is None
    e.set_scope("all")
    assert logs(e)[-1] == "[scope] Checking all decodes" and e.config.evaluate_all
    e.datagram_received(pk.decode("W1ABC ZL1AA -12"), ADDR)
    assert e.stations.find("ZL1AA") is not None
    e.set_show("all")
    assert logs(e)[-1] == "[show] Showing every decode" and e.config.show_all
    e.set_scope("cq")
    e.set_show("cq")
    assert logs(e)[-2:] == ["[scope] Checking CQs only", "[show] Showing CQs and wanted stations"]


def test_hidden_decodes_still_count_in_the_cycle_stats(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("W1ABC VK3XY -12"), ADDR)    # not shown
    e.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)
    assert (e.cycle_tracker.cycle_total, e.cycle_tracker.cycle_cq) == (2, 1)
    assert len(spots(e)) == 1


def test_debug_dump_comes_before_the_spot(make_engine):
    e = make_engine("--debug")
    e.datagram_received(pk.decode("W1ABC VK3XY -12"), ADDR)     # dumped though hidden
    assert len(logs(e)) == 1 and logs(e)[0].startswith("[debug] schema=2")


def test_other_packets_are_ignored(make_engine):
    e = make_engine()
    for data in (b"junk", pk.heartbeat(), pk.header(99)):
        e.datagram_received(data, ADDR)
    assert e.events == [] and e.tx_sock.sent == []


def test_proxy_forwards_every_packet(make_engine):
    e = make_engine("--proxy", "2238,2239")
    e.proxy_dests = [("127.0.0.1", 2238), ("127.0.0.1", 2239)]
    e.datagram_received(b"anything", ADDR)
    assert e.tx_sock.sent == [(b"anything", ("127.0.0.1", 2238)),
                              (b"anything", ("127.0.0.1", 2239))]


def test_packet_handler_errors_are_logged(make_engine, monkeypatch):
    e = make_engine()
    monkeypatch.setattr("jtcon.core.engine.evaluate", lambda d, ctx: 1 / 0)
    e.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)
    assert logs(e) == ["[error] packet handler: division by zero"]


# ── Status ────────────────────────────────────────────────────────────────────

def test_status_tracks_tx_and_operator(make_engine):
    e = make_engine()
    e.datagram_received(pk.status(14_074_000, de_call="K1ME", transmitting=True), ADDR)
    assert e.transmitting and e.de_call == "K1ME"
    assert isinstance(e.events[-1], StatusChanged)


def test_dial_change_clears_buffer_and_highlights(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    e.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)
    e.datagram_received(pk.status(14_074_000), ADDR)             # same dial: nothing
    assert len(e.stations) == 2
    e.datagram_received(pk.status(7_074_000), ADDR)
    assert len(e.stations) == 0 and e.dial_freq == 7_074_000
    assert "[buffer] Dial frequency changed — cleared 2 entries." in logs(e)
    assert sent(e) == [("highlight", "VK3XY", ORANGE), ("highlight", "VK3XY", (None, None))]


# ── Highlight Callsign ────────────────────────────────────────────────────────

def test_wanted_station_is_highlighted_once(make_engine):
    e = make_engine()
    for _ in range(3):
        e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    e.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)        # not wanted
    assert sent(e) == [("highlight", "VK3XY", ORANGE)]
    assert e.tx_sock.sent[0][1] == ADDR


def test_highlight_cleared_when_no_longer_wanted(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    for rule in e.rules:                                 # as if confirmed meanwhile
        rule.confirmed |= {"VK", 30, "AUSTRALIA"}
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    assert sent(e) == [("highlight", "VK3XY", ORANGE), ("highlight", "VK3XY", (None, None))]


def test_highlight_cleared_when_the_qso_is_logged(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    e.datagram_received(pk.qso_logged("VK3XY"), ADDR)
    assert "[qso] Logged VK3XY" in logs(e)
    assert sent(e)[-1] == ("highlight", "VK3XY", (None, None))
    e.datagram_received(pk.logged_adif("<call:5>VK3XY <eor>"), ADDR)   # nothing more to clear
    assert len(sent(e)) == 2


def test_wsjtx_close_forgets_its_highlights(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    e.datagram_received(pk.close(), ADDR)
    assert "[wsjt-x] WSJT-X closed" in logs(e)
    e.close()                                            # nothing left to clear
    assert len(sent(e)) == 1


def test_close_clears_all_highlights(make_engine):
    e = make_engine(config={})          # v3 defaults: unconfirmed JA is wanted too
    for msg in ("CQ VK3XY QF22", "CQ JA1XYZ PM95", "CQ W1ABC FN42"):
        e.datagram_received(pk.decode(msg), ADDR)
    e.close()
    assert sent(e)[2:] == [("highlight", "VK3XY", (None, None)),
                           ("highlight", "JA1XYZ", (None, None))]


def test_highlight_per_wsjtx_instance(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("CQ VK3XY QF22", client_id="WSJT-X - rig2"), ("10.0.0.2", 2237))
    data, addr = e.tx_sock.sent[0]
    assert pk.parse_highlight(data)["id"] == "WSJT-X - rig2" and addr == ("10.0.0.2", 2237)


def test_highlight_colours_and_off_switch(make_engine):
    colours = {"highlight": {"background": "#102030", "foreground": "#FFFFFF"}}
    e = make_engine(config={"actions": colours})
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    assert sent(e) == [("highlight", "VK3XY", ((16, 32, 48), (255, 255, 255)))]

    off = make_engine(config={"actions": {"highlight": {"enabled": False}}})
    off.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    assert off.tx_sock.sent == [] and len(spots(off)) == 1


def test_send_failures_are_logged(make_engine):
    e = make_engine(tx_sock=FakeSock(fail=True))
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    assert logs(e) == ["[udp] Send to 127.0.0.1:2237 failed: network down"]


# ── Replies ───────────────────────────────────────────────────────────────────

def test_reply(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)
    e.reply(e.stations.find("W1ABC"))
    assert sent(e) == [("reply", "CQ W1ABC FN42")]
    assert logs(e)[-1] == "[cmd] ► Reply sent to W1ABC [00]  msg='CQ W1ABC FN42'  → 127.0.0.1:2237"


def test_reply_failure_is_logged(make_engine):
    e = make_engine(tx_sock=FakeSock(fail=True))
    e.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)
    e.reply(e.stations.find("W1ABC"))
    assert logs(e)[-1] == "[cmd] Reply to W1ABC failed: network down"


def test_clear_buffer_command(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    assert e.clear_buffer() == 1
    assert sent(e)[-1] == ("highlight", "VK3XY", (None, None))


# ── Log reload ────────────────────────────────────────────────────────────────

def attach(engine):
    """Run *engine*'s events on the current loop, as run() does."""
    engine._loop = asyncio.get_running_loop()
    engine._loop_thread = threading.get_ident()


def _touch_later(path):
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 5))


def test_decodes_wait_for_a_reload(make_engine, tmp_path):
    log = tmp_path / "log.adi"
    shutil.copy(LOG_PATH, log)
    out = tmp_path / "script.out"
    script = tmp_path / "up.sh"
    script.write_text(f'#!/bin/sh\necho "uploading $1"\necho "$@" > {out}\n')
    script.chmod(0o755)
    e = make_engine(config={"actions": {"script": {"on_log_reload": str(script)}}})
    load_adif(e, [log])

    async def scenario():
        attach(e)
        with log.open("a") as f:
            f.write("<call:5>VK3XY <band:3>20m <mode:3>FT8 <qso_date:8>20240101"
                    " <lotw_qsl_rcvd:1>Y <EOR>\n")
        _touch_later(log)
        e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
        assert e._reloading and spots(e) == []          # held until the reload is done
        for _ in range(200):
            await asyncio.sleep(0.01)
            if spots(e) and out.exists() and any("uploading" in l for l in logs(e)):
                break

    asyncio.run(scenario())
    assert "[adif] Reloaded 10 QSOs from 1 file(s)." in logs(e)
    (ev,) = spots(e)
    assert ev.result.wanted == []                        # judged against the new log
    assert f"[on_log_reload] uploading {log}" in logs(e)
    assert out.read_text() == f"{log}\n"


def test_qso_logged_checks_the_log_at_once(make_engine, tmp_path):
    log = tmp_path / "log.adi"
    shutil.copy(LOG_PATH, log)
    e = make_engine()
    load_adif(e, [log])

    async def scenario():
        attach(e)
        e.datagram_received(pk.qso_logged("VK3XY"), ADDR)          # file not written yet
        assert not e._reloading
        with log.open("a") as f:
            f.write("<call:5>VK3XY <band:3>20m <mode:3>FT8 <qso_date:8>20261012"
                    " <time_on:6>123000 <EOR>\n")
        _touch_later(log)
        e.datagram_received(pk.logged_adif("<call:5>VK3XY <eor>"), ADDR)
        assert e._reloading
        while e._reloading:
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    # The file's record and the one WSJT-X reported are the same QSO.
    assert logs(e)[-1] == "[adif] Reloaded 10 QSOs from 1 file(s)."
    assert len(e.logbook) == 10 and "VK3XY" in e.calls.worked_calls


def test_qso_logged_marks_the_call_worked_at_once(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    assert spots(e)[-1].result.wanted                      # wanted
    e.datagram_received(pk.qso_logged("VK3XY"), ADDR)      # the file isn't written yet
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    assert spots(e)[-1].result.wanted == []                # worked today: suppressed
    assert "VK" in e.rules[0].worked and len(e.logbook) == 10
    assert e.calls.worked_calls_by_band["20m"] >= {"VK3XY"}


def test_qso_logged_updates_the_buffered_station(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    e.datagram_received(pk.decode("CQ ZL2AB RF70"), ADDR)
    alerted = dict(e.alerted)
    e.datagram_received(pk.qso_logged("VK3XY"), ADDR)
    assert [s.callsign for s in e.stations.hits()] == ["ZL2AB"]
    updated = {ev.station.callsign: ev for ev in e.events if isinstance(ev, StationUpdated)}
    assert updated["VK3XY"].result.wanted == [] and updated["ZL2AB"].result.wanted
    assert len(spots(e)) == 2 and e.alerted == alerted     # nothing new heard, no alerts
    assert sent(e).count(("highlight", "VK3XY", (None, None))) == 1


def test_qso_logged_without_any_log_is_just_reported(make_engine):
    e = make_engine(adif=False)
    e.datagram_received(pk.qso_logged("VK3XY"), ADDR)
    assert logs(e) == ["[qso] Logged VK3XY"] and e.logbook is None


def test_unreadable_reload_is_reported_once(make_engine, tmp_path, monkeypatch):
    log = tmp_path / "log.adi"
    shutil.copy(LOG_PATH, log)
    e = make_engine()
    load_adif(e, [log])

    def broken(*a, **kw):
        raise ValueError("bad file")

    async def scenario():
        attach(e)
        monkeypatch.setattr(e.adif_input, "read", broken)
        _touch_later(log)
        e.check_inputs()
        while e._reloading:
            await asyncio.sleep(0.01)
        e.check_inputs()                                 # same file: no retry
        assert not e._reloading

    asyncio.run(scenario())
    assert logs(e) == ["[adif] Reload failed: bad file — keeping the previous data"]
    assert len(e.logbook) == 9


# ── Startup and run() ─────────────────────────────────────────────────────────

@pytest.fixture
def no_wsjt_logs(monkeypatch):
    monkeypatch.setattr(jtcon.core.adif, "WSJT_LOG_SEARCH_PATHS", [])


def _engine(tmp_path, layer: dict, sock=None) -> Engine:
    inputs = {"cty": str(CTY_PATH), "hamdat": str(tmp_path / "no-hamdat.db")}
    e = Engine(make_config(merge({"inputs": inputs}, layer)), sock, FakeSock())
    e.events = []
    e.subscribe(e.events.append)
    return e


def test_startup_loads_inputs(tmp_path, no_wsjt_logs):
    e = _engine(tmp_path, {"inputs": {"adif": [str(LOG_PATH)]},
                           "wanted": [{"rule": "dxcc"}, {"rule": "pota"},
                                      {"rule": "was", "name": "WAS-QRZ", "confirm": ["qrz"]},
                                      {"rule": "cqz", "confirm": []}]})
    assert e._load_inputs()
    assert logs(e) == [
        "[config] Wanted rules: dxcc, pota, WAS-QRZ, cqz",
        "[cty.dat] Loaded 4798 prefixes + 2708 exact callsigns",
        "[adif] Loaded: log.adi",
        "[adif] 9 QSOs across 1 file(s) — 8 calls",
        "[dxcc] 5 worked, 2 confirmed (lotw)",
        "[WAS-QRZ] 3 worked, 1 confirmed (qrz)",      # no hamdat: states as logged
        "[cqz] 4 worked, 4 confirmed (any QSO)",
        "[pota] POTA in the message",
        "[highlight] Wanted stations are highlighted in WSJT-X (#ffa500 on #000000)",
    ]


@pytest.mark.parametrize("layer, fatal", [
    ({"inputs": {"adif": ["/nope/x.adi"]}}, "[adif] File not found: /nope/x.adi"),
    ({"wanted": [{"rule": "states", "states": ["AK"]}]}, "[states] hamdat database not found"),
    ({"wanted": [{"rule": "callsigns", "files": ["/nope/w.txt"]}]},
     "[wanted] Cannot read pattern file"),
    ({"actions": {"json_log": {"file": "/nope/dir/s.jsonl"}}}, "[save] Cannot open"),
])
def test_startup_fatal_errors(tmp_path, no_wsjt_logs, layer, fatal):
    e = _engine(tmp_path, layer)
    assert not e._load_inputs()
    assert isinstance(e.events[-1], Fatal) and e.events[-1].text.startswith(fatal)


def test_run_serves_wsjtx_until_stopped(tmp_path, no_wsjt_logs):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    e = _engine(tmp_path, {"udp": {"host": "127.0.0.1", "port": port}}, sock)

    async def scenario():
        task = asyncio.ensure_future(e.run())
        while not any(isinstance(ev, Ready) for ev in e.events):
            await asyncio.sleep(0.01)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wsjtx:
            wsjtx.sendto(pk.decode("CQ VK3XY QF22"), ("127.0.0.1", port))
            for _ in range(200):
                await asyncio.sleep(0.01)
                if spots(e):
                    break
        e.stop()
        await asyncio.wait_for(task, 5)
        e.close()

    asyncio.run(scenario())
    ready = [ev.text for ev in e.events if isinstance(ev, Ready)]
    assert ready == [f"[ready] Listening on 127.0.0.1:{port}  (CQ calls only)"]
    assert spots(e)[0].result.callsign == "VK3XY"
    assert sock.fileno() == -1                           # closed with the engine


def test_run_stops_after_a_fatal_error(tmp_path, no_wsjt_logs):
    e = _engine(tmp_path, {"inputs": {"adif": ["/nope/x.adi"]}})
    asyncio.run(asyncio.wait_for(e.run(), 5))
    assert isinstance(e.events[-1], Fatal)


def test_log_from_a_worker_thread_reaches_the_loop(tmp_path):
    e = _engine(tmp_path, {})
    seen = []

    async def scenario():
        attach(e)
        e.subscribe(lambda ev: seen.append(threading.get_ident()))
        await asyncio.to_thread(e.log, "from a thread")
        await asyncio.sleep(0.01)
        return threading.get_ident()

    loop_thread = asyncio.run(scenario())
    assert logs(e) == ["from a thread"] and seen == [loop_thread]


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script")
def test_log_reload_script_failure_is_logged(make_engine, tmp_path):
    e = make_engine(config={"actions": {"script": {"on_log_reload": str(tmp_path / "missing")}}})
    e._run_log_reload_script([LOG_PATH])
    assert logs(e)[0].startswith("[on_log_reload] [Errno 2]")



# ── Watchlist reloads ─────────────────────────────────────────────────────────

def _reload(e):
    async def scenario():
        attach(e)
        e.check_inputs()
        while e._reloading:
            await asyncio.sleep(0.01)
    asyncio.run(scenario())


def _rewrite(path, text):
    path.write_text(text)
    _touch_later(path)


def test_watchlists_reload_when_edited(tmp_path, no_wsjt_logs):
    calls, msgs = tmp_path / "calls.txt", tmp_path / "msgs.txt"
    calls.write_text("^VK\n")
    msgs.write_text("DX\n")
    e = _engine(tmp_path, {"wanted": [{"rule": "callsigns", "files": [str(calls)]},
                                      {"rule": "message", "files": [str(msgs)]},
                                      {"rule": "pota"}]})
    assert e._load_inputs()
    _rewrite(calls, "^VK\n^ZL\n")
    _rewrite(msgs, "JA\n")
    e.events.clear()
    _reload(e)
    assert logs(e) == [f"[callsigns] Reloaded 2 pattern(s) from {calls}",
                       f"[message] Reloaded 1 pattern(s) from {msgs}"]
    assert [ev.name for ev in e.events if isinstance(ev, InputReloaded)] == [
        "callsigns", "message"]
    calls_rule, msg_rule, pota = e.rules
    assert [p.pattern for p, _ in calls_rule.patterns] == ["^VK", "^ZL"]
    assert [p.pattern for p, _ in msg_rule.patterns] == ["JA"]
    e.events.clear()
    _reload(e)                                         # nothing changed
    assert e.events == []


def test_states_file_reloads(tmp_path, no_wsjt_logs, monkeypatch):
    monkeypatch.setattr("jtcon.core.engine.HamdatLookup", lambda path: object())
    states = tmp_path / "states.txt"
    states.write_text("DE\n")
    hamdat = tmp_path / "no-hamdat.db"
    hamdat.write_text("")
    e = _engine(tmp_path, {"wanted": [{"rule": "states", "states": ["AK"],
                                       "file": str(states)}]})
    assert e._load_inputs() and e.rules[0].states == {"AK", "DE"}
    _rewrite(states, "DE\nRI\n")
    _reload(e)
    assert e.rules[0].states == {"AK", "DE", "RI"}
    assert logs(e)[-1] == "[states] Reloaded 3 state(s): AK, DE, RI"


def test_a_broken_watchlist_keeps_the_previous_one(tmp_path, no_wsjt_logs):
    calls = tmp_path / "calls.txt"
    calls.write_text("^VK\n")
    e = _engine(tmp_path, {"wanted": [{"rule": "callsigns", "files": [str(calls)]}]})
    assert e._load_inputs()
    calls.unlink()
    e.events.clear()
    _reload(e)
    assert logs(e)[0].startswith("[callsigns] Reload failed: [Errno 2]")
    assert logs(e)[0].endswith("— keeping the previous data")
    assert [p.pattern for p, _ in e.rules[0].patterns] == ["^VK"]
    e.events.clear()
    _reload(e)                                         # still missing: not retried
    assert e.events == []
    calls.write_text("^ZL\n")                          # back again
    _reload(e)
    assert [p.pattern for p, _ in e.rules[0].patterns] == ["^ZL"]


def test_watchlist_edit_rejudges_buffered_stations(make_engine, tmp_path):
    calls = tmp_path / "calls.txt"
    calls.write_text("^ZZ\n")
    e = make_engine(config={"wanted": [{"rule": "callsigns", "files": [str(calls)]}]})
    rule = e.rules[0]
    inp = jtcon.core.inputs.PatternInput("callsigns", [calls], rule)
    rule.patterns = inp.load()
    e.inputs.append(inp)
    e.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)
    assert e.stations.hits() == [] and sent(e) == []
    _rewrite(calls, "^W1\n")
    _reload(e)
    (st,) = e.stations.hits()
    assert [h.mark() for h in st.wanted] == ["CALLS(^W1 [calls.txt])"]
    assert sent(e) == [("highlight", "W1ABC", ORANGE)]     # highlighted, not alerted
    assert e.alerted == {} and len(spots(e)) == 1


def test_log_reload_unhighlights_a_station_now_worked(make_engine, tmp_path):
    log = tmp_path / "log.adi"
    shutil.copy(LOG_PATH, log)
    e = make_engine()
    load_adif(e, [log])
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    with log.open("a") as f:
        f.write("<call:5>VK3XY <band:3>20m <mode:3>FT8 <qso_date:8>20240101"
                " <lotw_qsl_rcvd:1>Y <EOR>\n")
    _touch_later(log)
    _reload(e)
    assert e.stations.hits() == []
    assert sent(e) == [("highlight", "VK3XY", ORANGE), ("highlight", "VK3XY", (None, None))]


# ── Ignore ────────────────────────────────────────────────────────────────────

def test_ignore_drops_the_station_and_its_highlight(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("CQ VK3XY/P QF22"), ADDR)
    e.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)
    e.ignore("vk3xy")
    assert [s.callsign for s in e.stations.all()] == ["W1ABC"]
    assert sent(e) == [("highlight", "VK3XY/P", ORANGE), ("highlight", "VK3XY/P", (None, None))]
    assert logs(e)[-1] == "[ignore] VK3XY ignored for this session (1 ignored; unignore VK3XY to undo)."
    n = len(spots(e))
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    assert len(spots(e)) == n and len(sent(e)) == 2 and len(e.stations) == 1
    e.ignore("VK3XY/QRP")
    assert logs(e)[-1] == "[ignore] VK3XY is already ignored."


def test_unignore(make_engine):
    e = make_engine(config={"filters": {"ignore": ["VK3XY"]}})
    assert e.ignored == {"VK3XY": "config"}
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    assert spots(e) == []
    e.unignore("VK3XY")
    assert logs(e)[-1] == "[ignore] VK3XY is no longer ignored (0 ignored)."
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    assert len(spots(e)) == 1
    e.unignore("ZL1AA")
    assert logs(e)[-1] == "[ignore] ZL1AA is not ignored."


def test_refresh_drops_stations_ignored_meanwhile(make_engine):
    e = make_engine()
    e.datagram_received(pk.decode("CQ VK3XY QF22"), ADDR)
    e.ignored["VK3XY"] = "session"           # not through ignore(): refresh catches it
    e.refresh_stations()
    assert len(e.stations) == 0 and sent(e)[-1] == ("highlight", "VK3XY", (None, None))


def test_a_watchlist_in_the_config(tmp_path, no_wsjt_logs):
    e = _engine(tmp_path, {"wanted": [{"rule": "callsigns", "calls": ["K1ABC", "^VK"]},
                                      {"rule": "message", "name": "MSGS"}]})
    assert e._load_inputs()
    assert "[callsigns] 2 in the config" in logs(e)
    assert "[MSGS] nothing to watch yet" in logs(e)
    assert e.inputs == [i for i in e.inputs if not isinstance(i, jtcon.core.inputs.PatternInput)]


def test_watching_a_station_judges_the_buffer_again(make_engine):
    e = make_engine(config={"wanted": [{"rule": "callsigns", "name": "Friends",
                                        "calls": ["K9XX"]}]})
    e.datagram_received(pk.decode("CQ W1ABC FN42"), ADDR)
    assert e.stations.hits() == []
    assert not e.set_watch_calls("nope", ["W1ABC"])
    assert e.set_watch_calls("Friends", ["K9XX", "W1ABC"])
    assert [s.callsign for s in e.stations.hits()] == ["W1ABC"]
    assert e.config.wanted[0].settings["calls"] == ["K9XX", "W1ABC"]
