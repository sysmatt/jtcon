"""Judging decodes: the wanted rules on real decodes, display filtering, the
worked-recently filter, alerts and the JSON record.

The test log (fixtures/log.adi) and FakeHamdat give: W1ABC MA (20m LoTW + QRZ
confirmed), K2XYZ NJ (40m FT4, unconfirmed), JA1XYZ (unconfirmed, 367 days ago),
DL1ABC (LoTW, worked today), W9ZZZ IL (15m LoTW), G4ABC (eQSL), EA8ABC (QRZ),
K5TX TX (20m, 41 days ago, unconfirmed).  "Today" is 2026-10-12.
"""

import io
import json
import subprocess
import threading
import time
import urllib.request
from datetime import date

import pytest

from jtcon.core.logbook import QSO
from jtcon.tui.render import _BMAG, _BRED, _BYEL
import wsjtx_packets as pk
from conftest import handle_packet, watch_patterns

ADDR = ("127.0.0.1", 2237)
ALL_NEW_VK = ["❌DXCC(VK)", "❌CQZ(30)", "❌CTRY(Australia)"]


def run(ctx, msg, mode="~"):
    meta = {}
    lines = handle_packet(pk.decode(msg, mode=mode), ctx, ADDR, meta)
    return lines, meta


def wanted(ctx, msg):
    return run(ctx, msg)[1].get("wanted")


def rules(*specs, **top):
    """A config layer with these [[wanted]] entries (strings are bare rules)."""
    entries = [{"rule": s} if isinstance(s, str) else s for s in specs]
    return {"wanted": entries, **top}


def days(n):
    return {"filters": {"suppress_worked_within_days": n}}


# ── Award rules ───────────────────────────────────────────────────────────────

def test_new_entity_is_wanted(make_ctx):
    lines, meta = run(make_ctx(config={}), "CQ DX VK3XY QF22")
    assert meta["callsign"] == "VK3XY" and meta["is_cq"] is True
    assert meta["wanted"] == ALL_NEW_VK
    assert meta["bell"] is True
    assert meta["wsjt_addr"] == ADDR and meta["decoded"].message == "CQ DX VK3XY QF22"
    assert len(lines) == 2
    assert "*** WANTED: ❌DXCC(VK) · ❌CQZ(30) · ❌CTRY(Australia) ***" in lines[1]
    assert _BRED in lines[1]


def test_confirmed_entity_is_not_wanted(make_ctx):
    _, meta = run(make_ctx(config={}), "CQ W1ABC FN42")
    assert meta["wanted"] == []
    assert [(h.rule, h.status) for h in meta["hits"]] == [
        ("DXCC", "confirmed"), ("CQZ", "confirmed"), ("CTRY", "confirmed")]


def test_worked_but_unconfirmed_is_wanted(make_ctx):
    # The v3 default: dxcc, cqz and country count only LoTW confirmations.
    assert wanted(make_ctx(config={}), "CQ JA1XYZ PM95") == \
        ["❓DXCC(JA)", "❓CQZ(25)", "❓CTRY(Japan)"]
    worked = rules(*({"rule": r, "confirm": []} for r in ("dxcc", "cqz", "country")))
    assert wanted(make_ctx(config=worked), "CQ JA1XYZ PM95") == []


@pytest.mark.parametrize("confirm, call, grid, status", [
    (["lotw"], "EA8ABC", "IL18", "worked"),      # EA8ABC: QRZ-confirmed only
    (["qrz"],  "EA8ABC", "IL18", "confirmed"),
    (["eqsl"], "G4ABC",  "IO91", "confirmed"),   # G4ABC: eQSL only
    (["lotw", "qrz"], "G4ABC", "IO91", "worked"),
    (["card"], "W1ABC",  "FN42", "worked"),
])
def test_confirm_sources_per_rule(make_ctx, confirm, call, grid, status):
    ctx = make_ctx(config=rules({"rule": "dxcc", "confirm": confirm}))
    (hit,) = run(ctx, f"CQ {call} {grid}")[1]["hits"]
    assert hit.status == status


def test_rules_can_be_left_out(make_ctx):
    assert wanted(make_ctx(config=rules("dxcc")), "CQ VK3XY QF22") == ["❌DXCC(VK)"]
    assert wanted(make_ctx(config=rules("cqz")), "CQ VK3XY QF22") == ["❌CQZ(30)"]


def test_ituz_and_grid(make_ctx):
    ctx = make_ctx(config=rules("ituz", "grid"))
    assert wanted(ctx, "CQ VK3XY QF22") == ["❌ITUZ(59)", "❌GRID(QF22)"]
    # W1ABC: ITU 8 and FN42 confirmed by LoTW; no grid in the CQ, no grid hit.
    assert wanted(ctx, "CQ W1ABC FN42") == []
    assert wanted(ctx, "CQ KH6/W1XX") == ["❌ITUZ(61)"]


def test_without_a_log_award_rules_are_off(make_ctx):
    lines, meta = run(make_ctx(config={}, adif=False), "CQ VK3XY QF22")
    assert meta["wanted"] == [] and meta["hits"] == []
    assert len(lines) == 1


# ── WAS / 5BWAS ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("msg, want", [
    ("CQ N1NEW FN43", ["❌WAS(NH)"]),      # never worked
    ("CQ K2XYZ FN20", ["❓WAS(NJ)"]),      # worked, not LoTW-confirmed
    ("CQ W1ABC FN42", []),                # LoTW-confirmed
    ("CQ W9ZZZ EN52", []),                # confirmed; state came from hamdat
])
def test_was(make_ctx, msg, want):
    assert wanted(make_ctx(config=rules("was")), msg) == want


@pytest.mark.parametrize("dial, msg, want", [
    (14_074_000, "CQ W1ABC FN42", []),                      # MA confirmed on 20m
    (7_074_000,  "CQ W1ABC FN42", ["❌5BWAS(MA/40m)"]),     # never on 40m
    (10_136_000, "CQ W1ABC FN42", []),                      # 30m is not a 5BWAS band
    (14_074_000, "CQ K5TX EM12",  ["❓5BWAS(TX/20m)"]),     # worked 20m, unconfirmed
    (21_074_000, "CQ W9ZZZ EN52", []),                      # IL confirmed on 15m
])
def test_5bwas(make_ctx, dial, msg, want):
    assert wanted(make_ctx(config=rules("5bwas", **days(-1)), dial_freq=dial), msg) == want


def test_was_and_5bwas_together_and_twice(make_ctx):
    ctx = make_ctx(config=rules("was", {"rule": "was", "name": "WAS-QRZ", "confirm": ["qrz"]},
                                "5bwas", **days(-1)), dial_freq=7_074_000)
    assert wanted(ctx, "CQ W1ABC FN42") == ["❌5BWAS(MA/40m)"]   # WAS: LoTW and QRZ
    assert wanted(ctx, "CQ K5TX EM12") == ["❓WAS(TX)", "❓WAS-QRZ(TX)", "❌5BWAS(TX/40m)"]


def test_was_needs_hamdat(make_ctx):
    assert wanted(make_ctx(config=rules("was"), hamdat=None), "CQ N1NEW FN43") == []


# ── Watch rules ───────────────────────────────────────────────────────────────

def test_states(make_ctx):
    ctx = make_ctx(config=rules({"rule": "states", "states": ["MA", "NH"]}, **days(-1)))
    assert wanted(ctx, "CQ W1ABC FN42") == ["STATE(MA)"]
    assert wanted(ctx, "CQ K2XYZ FN20") == []


def test_callsign_watchlist(make_ctx):
    ctx = make_ctx(config={}, call_patterns=watch_patterns("^VK", "^ZL"))
    assert wanted(ctx, "CQ VK3XY QF22") == ALL_NEW_VK + ["CALLS(^VK [watch.txt])"]


def test_activation_keywords(make_ctx):
    assert wanted(make_ctx(config=rules("pota", **days(-1))), "CQ POTA K2XYZ FN20") == ["POTA"]
    assert wanted(make_ctx(config=rules("sota")), "CQ POTA K2XYZ FN20") == []


def test_watch_only_hits_are_yellow(make_ctx):
    lines, _ = run(make_ctx(config=rules("pota", **days(-1))), "CQ POTA K2XYZ FN20")
    assert _BYEL in lines[1] and _BRED not in lines[1]


# ── Worked-recently filter ────────────────────────────────────────────────────

def test_worked_today_hides_watchlist_hits(make_ctx):
    # DL1ABC was worked today.
    ctx = make_ctx(config=days(0), call_patterns=watch_patterns("^DL"))
    assert wanted(ctx, "CQ DL1ABC JO62") == []
    ctx = make_ctx(config=days(-1), call_patterns=watch_patterns("^DL"))
    assert wanted(ctx, "CQ DL1ABC JO62") == ["CALLS(^DL [watch.txt])"]


def test_worked_recently_window(make_ctx):
    # JA1XYZ, unconfirmed, last worked 2025-10-10: 367 days ago.
    assert wanted(make_ctx(config=rules("dxcc", **days(400))), "CQ JA1XYZ PM95") == []
    assert wanted(make_ctx(config=rules("dxcc", **days(300))), "CQ JA1XYZ PM95") == ["❓DXCC(JA)"]


def test_worked_recently_follows_each_rule(make_ctx):
    # K5TX: worked on 20m 41 days ago, TX unconfirmed.  That QSO accounts for
    # 5BWAS TX/20m, but not TX/40m: the station is still wanted on 40m.
    cfg = rules("5bwas", **days(50))
    assert wanted(make_ctx(config=cfg, dial_freq=14_074_000), "CQ K5TX EM12") == []
    assert wanted(make_ctx(config=cfg, dial_freq=7_074_000), "CQ K5TX EM12") == \
        ["❌5BWAS(TX/40m)"]
    # K2XYZ: NJ worked on 40m.  The recent QSO hides WAS(NJ) but not 5BWAS NJ/20m.
    ctx = make_ctx(config=rules("was", "5bwas", **days(600)))
    assert wanted(ctx, "CQ K2XYZ FN20") == ["❌5BWAS(NJ/20m)"]


def test_default_hides_stations_worked_within_5_days(make_ctx):
    ctx = make_ctx(config=rules("dxcc"))                      # v3 default: 5 days
    vk = dict(call="VK3XY", band="20m", mode="FT8", dxcc="VK", sources={"x"})
    ctx.logbook.add(QSO(date=date(2026, 10, 7), **vk))        # 5 days ago
    for r in ctx.rules:
        r.rebuild(ctx.logbook)
    assert wanted(ctx, "CQ VK3XY QF22") == []
    ctx.logbook.by_call["VK3XY"][0].date = date(2026, 10, 6)  # 6 days ago
    assert wanted(ctx, "CQ VK3XY QF22") == ["❓DXCC(VK)"]


# ── Which decodes are shown / checked ─────────────────────────────────────────

def test_non_cq_hidden_by_default(make_ctx):
    assert run(make_ctx(), "W1ABC VK3XY -12") == ([], {})


def test_all_checks_directed_messages(make_ctx):
    lines, meta = run(make_ctx(config={"scope": {"decodes": "all"}}), "W1ABC VK3XY -12")
    assert meta["callsign"] == "VK3XY" and meta["is_cq"] is False
    assert meta["wanted"] == ALL_NEW_VK
    assert _BMAG in lines[0] and _BMAG in lines[1]   # magenta: non-CQ hit


def test_all_hides_directed_messages_without_a_hit(make_ctx):
    assert run(make_ctx("--all"), "W1ABC K5TX -05") == ([], {})


def test_all_skips_my_own_qsos(make_ctx):
    assert run(make_ctx("--all"), "N0CALL VK3XY -05") == ([], {})


def test_show_all_shows_non_cq_without_checking(make_ctx):
    lines, meta = run(make_ctx("--raw"), "W1ABC VK3XY -12")
    assert meta["callsign"] is None and meta["wanted"] == []
    assert len(lines) == 1 and "W1ABC VK3XY -12" in lines[0]


def test_rules_only_check_what_the_scope_says(make_ctx):
    # v2 quirk Q5, fixed: show = "all" displays non-CQ decodes but no rule
    # checks them (v2 applied the message patterns); scope = "all" checks them.
    assert wanted(make_ctx("--raw", "--pota"), "TNX POTA 73") == []
    assert wanted(make_ctx("--raw", "--all", "--pota"), "TNX POTA 73") == ["POTA"]


def test_status_packet_is_not_a_decode(make_ctx):
    meta = {}
    assert handle_packet(pk.status(14_074_000), make_ctx(), ADDR, meta) == []
    assert meta == {}


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


ALERT_VK = ("VK3XY | Australia CQ30 | WANTED: DXCC(VK) new · CQZ(30) new"
            " · CTRY(Australia) new")


def test_alert_fires_once_then_after_an_hour_unheard(make_ctx, clock, popen):
    ctx = make_ctx("--alert-command", "/bin/alert")
    assert run(ctx, "CQ VK3XY QF22")[1]["bell"] is True
    clock[0] += 600
    assert run(ctx, "CQ VK3XY QF22")[1]["bell"] is False
    clock[0] += 3000          # heard 50 min ago: still within the hour
    assert run(ctx, "CQ VK3XY QF22")[1]["bell"] is False
    clock[0] += 3601          # unheard for over an hour
    assert run(ctx, "CQ VK3XY QF22")[1]["bell"] is True
    assert popen == [["/bin/alert", ALERT_VK]] * 2


def test_alert_fires_again_when_the_reasons_change(make_ctx, clock, popen):
    ctx = make_ctx("--alert-command", "/bin/alert", call_patterns=watch_patterns("^ZL"))
    run(ctx, "CQ VK3XY QF22")
    next(r for r in ctx.rules if r.label == "CALLS").patterns = watch_patterns("^VK")
    assert run(ctx, "CQ VK3XY QF22")[1]["bell"] is True
    assert popen[1] == ["/bin/alert", ALERT_VK + " · CALLS(^VK [watch.txt])"]


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
    assert req.data.decode() == ALERT_VK
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
        "cq_zone": 30, "itu_zone": 59, "continent": "OC",
        "wanted": [{"rule": "DXCC", "detail": "VK", "status": "new"},
                   {"rule": "CQZ", "detail": "30", "status": "new"},
                   {"rule": "CTRY", "detail": "Australia", "status": "new"}],
        "ignored": False,
    }


def test_json_wanted_without_a_log_has_watchlist_hits_only(make_ctx):
    fh = io.StringIO()
    run(make_ctx(adif=False, json_fh=fh, call_patterns=watch_patterns("^VK")), "CQ VK3XY QF22")
    assert json.loads(fh.getvalue())["wanted"] == [
        {"rule": "CALLS", "detail": "^VK [watch.txt]", "status": None}]


# ── Ignored calls ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("argv", [(), ("--all",), ("--raw",), ("--all", "--raw")])
def test_ignored_calls_are_not_shown_or_alerted(make_ctx, argv):
    log = []
    ctx = make_ctx(*argv, ignored=["VK3XY", "W1ABC"], log=log.append,
                   call_patterns=watch_patterns("^VK"))
    for msg in ("CQ DX VK3XY QF22", "CQ VK3XY/P", "CQ VK9/VK3XY", "W1ABC VK3XY RR73",
                "K2XYZ <W1ABC/P> R-12", "TU; K2XYZ W1ABC R 579 MA"):
        assert run(ctx, msg) == ([], {}), msg
    assert ctx.alerted == {} and log == []
    lines, meta = run(ctx, "CQ ZL1AA RF70")                   # others are unaffected
    assert lines and meta["callsign"] == "ZL1AA"


def test_an_ignored_call_calling_me_is_still_shown(make_ctx):
    ctx = make_ctx("--raw", ignored=["W9ZZZ"])
    lines, _ = run(ctx, "N0CALL W9ZZZ -05")
    assert "N0CALL W9ZZZ -05" in lines[0]
    assert run(ctx, "W9ZZZ N0CALL R-05")[0]                   # me calling it, too


def test_ignored_cqs_are_saved_marked_ignored(make_ctx):
    fh = io.StringIO()
    run(make_ctx(json_fh=fh, ignored=["VK3XY"]), "CQ DX VK3XY QF22")
    record = json.loads(fh.getvalue())
    assert record["ignored"] is True and record["wanted"] == []
    assert record["callsign"] == "VK3XY" and record["entity"] == "Australia"
