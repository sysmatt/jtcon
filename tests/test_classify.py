"""NEEDED / MATCH classification, display filtering, alerts and the JSON record.

These pin the v2 rules that the v3 wanted-rule framework must reproduce.
"""

import io
import json
import subprocess
import threading
import time
import urllib.request

import pytest

from jtcon.core.cycle import CycleTracker
from jtcon.core.decode import handle_packet
from jtcon.tui.render import _BMAG
import wsjtx_packets as pk
from conftest import watch_patterns

ADDR = ("127.0.0.1", 2237)
ALL_NEW_VK = ["NEW-DXCC(VK)", "NEW-CQZ(30)", "NEW-CTRY(Australia)"]


def run(ctx, msg, mode="~"):
    meta = {}
    lines = handle_packet(pk.decode(msg, mode=mode), ctx, ADDR, meta)
    return lines, meta


def flags(ctx, msg):
    _, meta = run(ctx, msg)
    return meta.get("needed_flags"), meta.get("match_flags")


# ── DXCC / zone / country ─────────────────────────────────────────────────────

def test_new_entity_is_needed(make_ctx):
    lines, meta = run(make_ctx(), "CQ DX VK3XY QF22")
    assert meta["callsign"] == "VK3XY"
    assert meta["is_cq"] is True
    assert meta["needed_flags"] == ALL_NEW_VK
    assert meta["is_needed"] is True
    assert meta["match_flags"] == []
    assert meta["bell"] is True
    assert meta["wsjt_addr"] == ADDR
    assert meta["decoded"]["message"] == "CQ DX VK3XY QF22"
    assert len(lines) == 2 and "*** NEEDED: NEW-DXCC(VK), NEW-CQZ(30)" in lines[1]


def test_worked_entity_is_not_needed(make_ctx):
    assert flags(make_ctx(), "CQ W1ABC FN42") == ([], [])


def test_qsl_only_needs_unconfirmed(make_ctx):
    assert flags(make_ctx(), "CQ JA1XYZ PM95") == ([], [])
    assert flags(make_ctx("--qsl-only"), "CQ JA1XYZ PM95") == \
        (["NEW-DXCC(JA)", "NEW-CQZ(25)", "NEW-CTRY(Japan)"], [])


def test_need_checks_can_be_disabled(make_ctx):
    ctx = make_ctx("--no-need-cqz", "--no-need-country")
    assert flags(ctx, "CQ VK3XY QF22") == (["NEW-DXCC(VK)"], [])
    ctx = make_ctx("--no-need-entity", "--no-need-country")
    assert flags(ctx, "CQ VK3XY QF22") == (["NEW-CQZ(30)"], [])


def test_without_a_log_nothing_is_needed(make_ctx):
    lines, meta = run(make_ctx(adif=False), "CQ VK3XY QF22")
    assert meta["needed_flags"] == []
    assert len(lines) == 1


# ── WAS / 5BWAS ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg, needed", [
    ("CQ N1NEW FN43", ["NEW-WAS(NH)"]),   # never worked
    ("CQ K2XYZ FN20", ["NEW-WAS(NJ)"]),   # worked, not LoTW-confirmed
    ("CQ W1ABC FN42", []),                # LoTW-confirmed
    ("CQ W9ZZZ EN52", []),                # confirmed; state came from hamdat
])
def test_was(make_ctx, msg, needed):
    assert flags(make_ctx("--was"), msg) == (needed, [])


@pytest.mark.parametrize("dial, msg, needed", [
    (14_074_000, "CQ W1ABC FN42", []),                      # MA confirmed on 20m
    (7_074_000,  "CQ W1ABC FN42", ["NEW-5BWAS(MA/40m)"]),   # but not on 40m
    (10_136_000, "CQ W1ABC FN42", []),                      # 30m is not a 5BWAS band
    (14_074_000, "CQ K5TX EM12",  ["NEW-5BWAS(TX/20m)"]),   # worked 20m, unconfirmed
    (21_074_000, "CQ W9ZZZ EN52", []),                      # IL confirmed on 15m
])
def test_5bwas(make_ctx, dial, msg, needed):
    assert flags(make_ctx("--5bwas", dial_freq=dial), msg) == (needed, [])


def test_was_needs_hamdat(make_ctx):
    assert flags(make_ctx("--was", hamdat=None), "CQ N1NEW FN43") == ([], [])


# ── MATCH patterns ────────────────────────────────────────────────────────────

def test_match_state(make_ctx):
    assert flags(make_ctx("--match-state", "MA,NH"), "CQ W1ABC FN42") == ([], ["STATE:MA"])


def test_match_callsign_pattern(make_ctx):
    ctx = make_ctx(call_patterns=watch_patterns("^VK", "^ZL"))
    assert flags(ctx, "CQ VK3XY QF22") == (ALL_NEW_VK, ["CALL:^VK [watch.txt]"])


def test_match_pota(make_ctx):
    assert flags(make_ctx("--pota"), "CQ POTA K2XYZ FN20") == ([], [r"MSG:\bPOTA\b"])
    assert flags(make_ctx("--sota"), "CQ POTA K2XYZ FN20") == ([], [])


# ── Worked-recently suppression ───────────────────────────────────────────────

def test_worked_today_is_suppressed(make_ctx):
    # DL1ABC was worked "today"; the default --worked-lag-days 0 hides its hits.
    ctx = make_ctx(call_patterns=watch_patterns("^DL"))
    assert flags(ctx, "CQ DL1ABC JO62") == ([], [])
    ctx = make_ctx("--worked-lag-days", "-1", call_patterns=watch_patterns("^DL"))
    assert flags(ctx, "CQ DL1ABC JO62") == ([], ["CALL:^DL [watch.txt]"])


def test_worked_lag_days_window(make_ctx):
    # JA1XYZ last worked 2025-10-10: 367 days before the frozen today.
    assert flags(make_ctx("--qsl-only", "--worked-lag-days", "400"), "CQ JA1XYZ PM95") == ([], [])
    assert flags(make_ctx("--qsl-only", "--worked-lag-days", "300"), "CQ JA1XYZ PM95")[0] == \
        ["NEW-DXCC(JA)", "NEW-CQZ(25)", "NEW-CTRY(Japan)"]


# ── Which decodes are shown / checked ─────────────────────────────────────────

def test_non_cq_hidden_by_default(make_ctx):
    assert run(make_ctx(), "W1ABC VK3XY -12") == ([], {})


def test_all_checks_directed_messages(make_ctx):
    lines, meta = run(make_ctx("--all"), "W1ABC VK3XY -12")
    assert meta["callsign"] == "VK3XY"
    assert meta["is_cq"] is False
    assert meta["needed_flags"] == ALL_NEW_VK
    assert _BMAG in lines[0] and _BMAG in lines[1]   # magenta: non-CQ hit


def test_all_hides_directed_messages_without_a_hit(make_ctx):
    assert run(make_ctx("--all"), "W1ABC K5TX -05") == ([], {})


def test_all_skips_my_own_qsos(make_ctx):
    assert run(make_ctx("--all"), "N0CALL VK3XY -05") == ([], {})


def test_raw_shows_non_cq_without_checking(make_ctx):
    lines, meta = run(make_ctx("--raw"), "W1ABC VK3XY -12")
    assert meta["callsign"] is None and meta["needed_flags"] == []
    assert len(lines) == 1 and "W1ABC VK3XY -12" in lines[0]


def test_raw_applies_message_patterns_to_non_cq(make_ctx):
    assert flags(make_ctx("--raw", "--pota"), "TNX POTA 73") == ([], [r"MSG:\bPOTA\b"])


def test_status_packet_is_not_a_decode(make_ctx):
    meta = {}
    assert handle_packet(pk.status(14_074_000), make_ctx(), ADDR, meta) == []
    assert meta == {}


def test_cycle_tracker_counts_hidden_decodes(make_ctx):
    ctx = make_ctx()
    ctx.cycle_tracker = CycleTracker()
    run(ctx, "W1ABC VK3XY -12")
    run(ctx, "CQ W1ABC FN42")
    assert (ctx.cycle_tracker.cycle_total, ctx.cycle_tracker.cycle_cq) == (2, 1)


def test_debug_dumps_packet(make_ctx):
    lines, _ = run(make_ctx("--debug"), "CQ W1ABC FN42")
    assert lines[0].startswith("[debug] schema=2  len=")
    assert "message='CQ W1ABC FN42'" in lines[0]


def test_ft4_mode_name(make_ctx):
    lines, _ = run(make_ctx("--no-color"), "CQ W1ABC FN42", mode="+")
    assert "  FT4   CQ W1ABC FN42" in lines[0]


# ── Alerts ────────────────────────────────────────────────────────────────────

@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    return now


@pytest.fixture
def popen(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    return calls


def test_alert_fires_once_then_after_an_hour_unheard(make_ctx, clock, popen):
    ctx = make_ctx("--alert-command", "/bin/alert")
    assert run(ctx, "CQ VK3XY QF22")[1]["bell"] is True
    clock[0] += 600
    assert run(ctx, "CQ VK3XY QF22")[1]["bell"] is False
    clock[0] += 3000          # heard 50 min ago: still within the hour
    assert run(ctx, "CQ VK3XY QF22")[1]["bell"] is False
    clock[0] += 3601          # unheard for over an hour
    assert run(ctx, "CQ VK3XY QF22")[1]["bell"] is True
    assert popen == [["/bin/alert", "VK3XY | Australia CQ30 | NEEDED: "
                      "NEW-DXCC(VK), NEW-CQZ(30), NEW-CTRY(Australia)"]] * 2


def test_alert_fires_again_when_flags_change(make_ctx, clock, popen):
    ctx = make_ctx("--alert-command", "/bin/alert")
    run(ctx, "CQ VK3XY QF22")
    ctx.call_patterns = watch_patterns("^VK")
    assert run(ctx, "CQ VK3XY QF22")[1]["bell"] is True
    assert len(popen) == 2


def test_no_alert_without_a_hit(make_ctx, popen):
    assert run(make_ctx("--alert-command", "/bin/alert"), "CQ W1ABC FN42")[1]["bell"] is False
    assert popen == []


def test_ntfy_alert(make_ctx, monkeypatch, clock):
    sent = []

    class _SyncThread:
        def __init__(self, target, daemon=None):
            self.target = target

        def start(self):
            self.target()

    def _urlopen(req, timeout):
        sent.append(req)
        return io.BytesIO()

    monkeypatch.setattr(threading, "Thread", _SyncThread)
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    run(make_ctx("--alert-ntfy", "my-topic"), "CQ VK3XY QF22")
    (req,) = sent
    assert req.full_url == "https://ntfy.sh/my-topic"
    assert req.get_method() == "POST"
    assert req.data.decode().startswith("VK3XY | Australia CQ30 | NEEDED: NEW-DXCC(VK)")
    assert req.get_header("Title") == "jtcon"


# ── JSON record (--save) ──────────────────────────────────────────────────────

def test_json_record(make_ctx):
    fh = io.StringIO()
    ctx = make_ctx("--all", json_fh=fh)
    run(ctx, "CQ DX VK3XY QF22")
    run(ctx, "W1ABC VK3XY -12")          # non-CQ hits are not saved
    (line,) = fh.getvalue().splitlines()
    assert json.loads(line) == {
        "wall_utc": "2026-10-12T12:34:56Z", "period_utc": "123015z",
        "snr_db": -7, "dt_s": 0.2, "df_hz": 1234, "mode": "FT8",
        "message": "CQ DX VK3XY QF22", "callsign": "VK3XY", "modifier": "DX",
        "gridsquare": "QF22", "primary_prefix": "VK", "entity": "Australia",
        "cq_zone": 30, "itu_zone": 59, "continent": "OC", "needed": ALL_NEW_VK,
    }


def test_json_needed_is_null_without_a_log(make_ctx):
    fh = io.StringIO()
    run(make_ctx(adif=False, json_fh=fh), "CQ VK3XY QF22")
    assert json.loads(fh.getvalue())["needed"] is None
