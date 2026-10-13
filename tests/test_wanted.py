"""The wanted rules on their own: indexes, statuses, labels and the
worked-recently filter, with hand-made Logbooks."""

import re
from datetime import date

from jtcon.config import WantedSpec
from jtcon.core.logbook import QSO, Logbook
from jtcon.core.wanted import (CONFIRMED, NEW, WORKED, Hit, Spot, build_rules,
                               evaluate_rules, rule_label)

TODAY = date(2026, 10, 12)


def spec(rule, name=None, **settings):
    if rule in ("dxcc", "cqz", "ituz", "country", "was", "5bwas", "grid"):
        settings.setdefault("confirm", ["lotw"])
    if rule == "states":
        settings.setdefault("states", [])
        settings.setdefault("file", None)
    if rule in ("callsigns", "message"):
        settings.setdefault("files", [])
    return WantedSpec(rule, name or rule, settings)


def qso(call="W1ABC", when=date(2025, 1, 1), confirmations=(), **kw):
    kw.setdefault("band", "20m")
    return QSO(call=call, date=when, mode="FT8", confirmations=set(confirmations),
               sources={"log"}, **kw)


def spot(call="W1ABC", state="MA", band="20m", grid="FN42", message=None, **entity):
    entity = {"primary_prefix": "K", "entity": "United States", "cq_zone": 5,
              "itu_zone": 8, "continent": "NA", **entity}
    return Spot(call=call, grid=grid, entity=entity, state=state, band=band,
                message=message or f"CQ {call} {grid}", is_cq=True)


def judge(rules, s, logbook, days=-1):
    for r in rules:
        if logbook is not None:
            r.rebuild(logbook)
    return evaluate_rules(rules, s, logbook, days, TODAY)


def test_status_by_confirm_sources():
    lb = Logbook([qso(state="MA", dxcc="K", confirmations={"card"})])
    rules = build_rules([spec("was"), spec("was", "WAS-CARD", confirm=["card", "qrz"]),
                         spec("was", "WAS-ANY", confirm=[])])
    hits, wanted = judge(rules, spot(), lb)
    assert [(h.rule, h.status) for h in hits] == [
        ("WAS", WORKED), ("WAS-CARD", CONFIRMED), ("WAS-ANY", CONFIRMED)]
    assert wanted == [Hit("WAS", "MA", WORKED, "MA")]


def test_add_matches_rebuild():
    q = qso(state="TX", dxcc="K", cqz=5, ituz=7, grid="EM12", confirmations={"lotw"})
    for key in ("dxcc", "cqz", "ituz", "country", "was", "5bwas", "grid"):
        a, b = build_rules([spec(key)]) + build_rules([spec(key)])
        a.rebuild(Logbook([q]))
        b.rebuild(Logbook())
        b.add(q)
        assert (a.worked, a.confirmed) == (b.worked, b.confirmed), key


def test_each_award_rule_key():
    lb = Logbook([qso(state="MA", dxcc="K", cqz=5, ituz=8, grid="FN42",
                      country="UNITED STATES", confirmations={"lotw"})])
    rules = build_rules([spec(k) for k in
                         ("dxcc", "cqz", "ituz", "country", "was", "5bwas", "grid")])
    hits, _ = judge(rules, spot(), lb)
    assert [h.mark() for h in hits] == ["✅DXCC(K)", "✅CQZ(5)", "✅ITUZ(8)",
                                        "✅CTRY(United States)", "✅WAS(MA)",
                                        "✅5BWAS(MA/20m)", "✅GRID(FN42)"]
    hits, _ = judge(rules, spot(band="40m", grid="FN43"), lb)
    assert [h.mark() for h in hits if h.status == NEW] == ["❌5BWAS(MA/40m)", "❌GRID(FN43)"]


def test_rules_that_do_not_apply_give_no_hit():
    rules = build_rules([spec("was"), spec("5bwas"), spec("grid"), spec("dxcc")])
    hits, _ = judge(rules, spot(state=None, band="30m", grid=None, primary_prefix=""),
                    Logbook())
    assert hits == []


def test_country_counts_the_logged_name_too():
    rule, = build_rules([spec("country")])
    lb = Logbook([qso(call="G4ABC", log_country="ENGLAND", confirmations={"lotw"})])
    hits, _ = judge([rule], spot(call="G4ABC", entity="England"), lb)
    assert hits[0].status == CONFIRMED


def test_watch_rules():
    calls, msg, states, pota = build_rules([
        spec("callsigns", "VIPS"), spec("message"), spec("states", states=["MA"]),
        spec("pota")])
    calls.patterns = [(re.compile("^W1", re.I), "vips.txt")]
    msg.patterns = [(re.compile("DX", re.I), "m.txt")]
    hits, wanted = judge([calls, msg, states, pota],
                         spot(message="CQ DX POTA W1ABC FN42"), None)
    assert [h.mark() for h in wanted] == ["VIPS(^W1 [vips.txt])", "MSG(DX [m.txt])",
                                          "STATE(MA)", "POTA"]
    assert all(h.status is None and h.fires for h in hits)


def test_award_rules_need_a_logbook():
    hits, _ = judge(build_rules([spec("dxcc"), spec("pota")]),
                    spot(message="CQ POTA W1ABC"), None)
    assert hits == [Hit("POTA")]


def test_worked_recently_only_hides_what_the_qso_covers():
    lb = Logbook([qso(state="MA", dxcc="K", when=date(2026, 10, 10))])   # 2 days ago, 20m
    rules = build_rules([spec("dxcc"), spec("5bwas"), spec("states", states=["MA"])])
    _, wanted = judge(rules, spot(band="20m"), lb, days=5)
    assert wanted == []                               # all covered by the QSO
    _, wanted = judge(rules, spot(band="40m"), lb, days=5)
    assert [h.mark() for h in wanted] == ["❌5BWAS(MA/40m)"]
    _, wanted = judge(rules, spot(band="20m"), lb, days=1)                # too long ago
    assert [h.mark() for h in wanted] == ["❓DXCC(K)", "❓5BWAS(MA/20m)", "STATE(MA)"]
    _, wanted = judge(rules, spot(band="20m"), lb, days=-1)               # never hide
    assert len(wanted) == 3


def test_another_call_in_the_entity_does_not_hide_it():
    lb = Logbook([qso(call="K1OTHER", dxcc="K", when=TODAY)])
    _, wanted = judge(build_rules([spec("dxcc")]), spot(), lb, days=5)
    assert [h.mark() for h in wanted] == ["❓DXCC(K)"]


def test_labels_and_text():
    assert rule_label(spec("5bwas")) == "5BWAS"
    assert rule_label(spec("callsigns")) == "CALLS"
    assert rule_label(spec("was", "WAS-QRZ")) == "WAS-QRZ"
    h = Hit("WAS-QRZ", "MA", WORKED, "MA")
    assert (h.mark(), h.text()) == ("❓WAS-QRZ(MA)", "WAS-QRZ(MA) unconfirmed")
    assert h.as_dict() == {"rule": "WAS-QRZ", "detail": "MA", "status": "worked"}
    assert Hit("POTA").text() == "POTA" and Hit("POTA").mark() == "POTA"
    assert not Hit("DXCC", "K", CONFIRMED).fires
