"""Message parsing and small helpers.

Tests marked "v2 quirk" pin current behaviour that is probably wrong; they are
listed in docs/v3-plan.md to revisit, not to preserve.
"""

import re

import pytest

from jtcon.core.alerts import _build_alert_msg
from jtcon.core.bands import _freq_to_band
from jtcon.core.cycle import CycleTracker
from jtcon.core.hamdat import load_state_set
from jtcon.core.messages import _directed_sender, parse_cq_message
from jtcon.core.patterns import _regex_matches, load_regex_patterns
from jtcon.tui.render import _fmt_age


@pytest.mark.parametrize("msg, expected", [
    ("CQ W1ABC FN42",          ("W1ABC", "FN42", None)),
    ("cq w1abc fn42",          ("W1ABC", "FN42", None)),
    ("CQ DX VK3XY QF22",       ("VK3XY", "QF22", "DX")),
    ("CQ POTA K2XYZ FN20",     ("K2XYZ", "FN20", "POTA")),
    ("CQ TEST K1ABC FN42",     ("K1ABC", "FN42", "TEST")),
    ("CQ NA W1ABC",            ("W1ABC", None, "NA")),
    ("CQ KH6/W1XX",            ("KH6/W1XX", None, None)),
    ("CQ W1ABC FN42AB",        ("W1ABC", "FN42AB", None)),
    ("CQ 1A0KM JN61",          ("1A0KM", "JN61", None)),
    ("CQ 3D2CCC",              ("3D2CCC", None, None)),
    ("CQ",                     (None, None, None)),
    ("CQ W1ABC R-12",          (None, None, None)),
    ("CQ ABCDEFGHIJKLMN W1ABC", (None, None, None)),   # modifier too long
    ("CQCQ W1ABC",             (None, None, None)),
    ("W1ABC K2XYZ FN20",       (None, None, None)),
])
def test_parse_cq_message(msg, expected):
    assert parse_cq_message(msg) == expected


def test_parse_cq_v2_quirks():
    # v2 quirk: "RR73" passes the callsign pattern (it contains a digit).
    assert parse_cq_message("CQ RR73") == ("RR73", None, None)
    # v2 quirk: a hashed (<...>) callsign in a CQ is not recognised.
    assert parse_cq_message("CQ <W1ABC/P> FN42") == (None, None, None)


@pytest.mark.parametrize("msg, expected", [
    ("W1ABC JA1XYZ -12",          "JA1XYZ"),
    ("W1ABC W3QQQ FN20",          "W3QQQ"),
    ("K2XYZ <W1ABC/P> R-12",      "W1ABC/P"),    # hashed call, brackets stripped
    ("TU; W1ABC K2XYZ R 579 MA",  "K2XYZ"),      # contest prefix skipped
    ("N0CALL W9ZZZ -05",          None),         # calling me: already engaged
    ("W9ZZZ N0CALL R-05",         None),         # my own transmission
    ("W1ABC <...> RR73",          None),         # unresolved hash
    ("TNX RR73 GL",               None),         # free text
    ("W1ABC 73",                  None),
    ("W1ABC",                     None),
])
def test_directed_sender(msg, expected):
    assert _directed_sender(msg, "N0CALL") == expected


def test_directed_sender_without_my_call():
    assert _directed_sender("W1ABC W9ZZZ -05", "") == "W9ZZZ"


@pytest.mark.parametrize("hz, band", [
    (1_840_000, "160m"), (3_573_000, "80m"), (5_357_000, "60m"), (7_074_000, "40m"),
    (10_136_000, "30m"), (14_074_000, "20m"), (18_100_000, "17m"), (21_074_000, "15m"),
    (24_915_000, "12m"), (28_074_000, "10m"), (50_313_000, "6m"), (70_100_000, "4m"),
    (144_174_000, "2m"), (432_000_000, "70cm"),
    (13_999_999, None), (0, None),
    (14_350_000, None),   # upper band edge is exclusive
])
def test_freq_to_band(hz, band):
    assert _freq_to_band(hz) == band


def test_fmt_age():
    assert [_fmt_age(s) for s in (0, 59.9, 60, 185, 3599, 3600, 4320, 90000)] == \
        ["0s", "59s", "1m00s", "3m05s", "59m59s", "1h00m", "1h12m", "25h00m"]


def test_cycle_tracker():
    ct = CycleTracker()
    ct.record(45_000_000, True, True, "FT8")     # 45_000_000 / 15000 = 3000: even
    ct.record(45_000_000, False, True, "FT8")
    ct.record(45_000_000, True, False, "FT8")    # replayed (not new): ignored
    assert (ct.cycle_total, ct.cycle_cq, ct._parity) == (2, 1, "even")
    ct.record(45_015_000, False, True, "FT8")    # next slot: odd
    assert (ct.cycle_total, ct.cycle_cq, ct._parity) == (1, 0, "odd")
    assert ct._sums["even"] == [2, 1, 1]
    ct.record(45_022_500, True, True, "FT4")     # FT4 uses 7.5 s slots: 6003 → odd
    assert ct._parity == "odd" and ct.current_mode == "FT4"
    assert ct._sums["odd"] == [1, 0, 1]


def test_load_state_set(tmp_path):
    assert load_state_set("tx, ca,,NY") == {"TX", "CA", "NY"}
    f = tmp_path / "states.txt"
    f.write_text("# my states\nma\n\n  nh  \n")
    assert load_state_set(str(f)) == {"MA", "NH"}


def test_load_regex_patterns(tmp_path):
    f = tmp_path / "watch.txt"
    f.write_text("# comment\n^VK\n\n[bad\nZL.*\n")
    logged = []
    pats = load_regex_patterns([str(f)], log=logged.append)
    assert [(p.pattern, src) for p, src in pats] == [("^VK", "watch.txt"), ("ZL.*", "watch.txt")]
    assert all(p.flags & re.IGNORECASE for p, _ in pats)
    assert len(logged) == 1 and "watch.txt:4: invalid regex '[bad'" in logged[0]


def test_load_regex_patterns_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        load_regex_patterns([str(tmp_path / "nope.txt")])


def test_regex_matches_labels():
    pats = [(re.compile("^VK", re.I), "watch.txt"), (re.compile(r"\bPOTA\b", re.I), None)]
    assert _regex_matches("vk3xy", pats) == ["^VK [watch.txt]"]
    assert _regex_matches("CQ POTA K2XYZ", pats) == [r"\bPOTA\b"]
    assert _regex_matches("W1ABC", pats) == []


def test_build_alert_msg():
    ei = {"entity": "Australia", "cq_zone": 30}
    assert _build_alert_msg("VK3XY", ei, ["NEW-DXCC(VK)"], ["CALL:^VK [w.txt]"]) == \
        "VK3XY | Australia CQ30 | NEEDED: NEW-DXCC(VK)  MATCH: CALL:^VK [w.txt]"
    assert _build_alert_msg(None, {}, [], ["MSG:x"]) == "? | MATCH: MSG:x"
