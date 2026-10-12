"""cty.dat parsing and callsign → entity lookup."""

import pytest

from jtcon.core.cty import _strip_overrides
from jtcon.core.decode import _entity_info


def _summary(cty, call):
    r = cty.lookup(call)
    return r and (r["primary_prefix"], r["country"], r["cqz"], r["ituz"], r["continent"])


@pytest.mark.parametrize("call, expected", [
    ("W1ABC",      ("K",    "United States", 5, 8, "NA")),
    ("w1abc",      ("K",    "United States", 5, 8, "NA")),
    ("VK3XY",      ("VK",   "Australia", 30, 59, "OC")),
    ("JA1XYZ",     ("JA",   "Japan", 25, 45, "AS")),
    ("KL7ABC",     ("KL",   "Alaska", 1, 1, "NA")),
    ("EA8ABC",     ("EA8",  "Canary Islands", 33, 36, "AF")),
    ("UA9ABC",     ("UA9",  "Asiatic Russia", 17, 30, "AS")),
    ("4U1UN",      ("4U1U", "United Nations HQ", 5, 8, "NA")),
    # Portable / operating-condition suffixes and call-area digits are ignored.
    ("W1ABC/P",    ("K",    "United States", 5, 8, "NA")),
    ("W1ABC/QRP",  ("K",    "United States", 5, 8, "NA")),
    ("K1ABC/MM",   ("K",    "United States", 5, 8, "NA")),
    ("W1ABC/7",    ("K",    "United States", 5, 8, "NA")),
    # Compound calls: the shorter part is the location prefix, on either side.
    ("KH6/W1XX",   ("KH6",  "Hawaii", 31, 61, "OC")),
    ("W1XX/KH6",   ("KH6",  "Hawaii", 31, 61, "OC")),
    ("VP2E/W1ABC", ("VP2E", "Anguilla", 8, 11, "NA")),
    ("W1ABC/VP2E", ("VP2E", "Anguilla", 8, 11, "NA")),
    ("F/G4ABC",    ("F",    "France", 14, 27, "EU")),
    # Unknown prefix, nothing left after stripping.
    ("Q0ZZZ",      None),
    ("/P",         None),
])
def test_lookup(cty, call, expected):
    assert _summary(cty, call) == expected


def test_prefix_overrides(cty):
    # Canada is CQ 5 / ITU 9, but VE2 carries an [4] ITU-zone override.
    assert _summary(cty, "VE3ABC") == ("VE", "Canada", 5, 9, "NA")
    assert _summary(cty, "VE2ABC") == ("VE", "Canada", 5, 4, "NA")
    # CH2(2): CQ-zone override.
    assert _summary(cty, "CH2ABC")[2] == 2


def test_exact_callsign_entries(cty):
    # =VE2FK[9] is an exact-match entry with its own ITU zone.
    assert _summary(cty, "VE2FK") == ("VE", "Canada", 5, 9, "NA")
    assert _summary(cty, "VE2FKX") == ("VE", "Canada", 5, 4, "NA")


def test_lookup_returns_a_copy(cty):
    cty.lookup("W1ABC")["country"] = "changed"
    assert cty.lookup("W1ABC")["country"] == "United States"


def test_loaded_counts(cty):
    assert len(cty._prefixes) == 4798
    assert len(cty._exact) == 2708


def test_strip_overrides():
    assert _strip_overrides("VE2[4]") == ("VE2", {"ituz": 4})
    assert _strip_overrides("CI0(2)[4]") == ("CI0", {"cqz": 2, "ituz": 4})
    assert _strip_overrides("XX9{AS}") == ("XX9", {"continent": "AS"})
    assert _strip_overrides("K") == ("K", {})


def test_entity_info_shape(cty):
    assert _entity_info(cty, "VK3XY") == {
        "primary_prefix": "VK", "entity": "Australia", "cq_zone": 30,
        "itu_zone": 59, "continent": "OC",
    }
    assert _entity_info(cty, None) == {}
    assert _entity_info(cty, "Q0ZZZ") == {}
