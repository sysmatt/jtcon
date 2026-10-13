"""The rolling buffer: one numbered slot per recently heard station."""

import time

import pytest

from jtcon.core.stations import BUF_SIZE, StationTable
from jtcon.core.wanted import NEW, Hit

DXCC_VK = Hit("DXCC", "VK", NEW)


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    return now


def store(table, call, wanted=(), decode="d"):
    return table.store(call, decode, ("127.0.0.1", 2237), list(wanted))


def test_each_station_keeps_its_index(clock):
    t = StationTable()
    assert [store(t, c).idx for c in ("W1ABC", "VK3XY", "JA1XYZ")] == [0, 1, 2]
    clock[0] += 15
    st = store(t, "vk3xy", decode="newer")
    assert st.idx == 1 and st.callsign == "VK3XY" and st.count == 2
    assert (st.first_seen, st.last_seen) == (1000.0, 1015.0)
    assert st.decode == "newer"                       # newest decode wins
    assert t.find("VK3XY") is st and t.find("vk3xy") is st and t.get(1) is st
    assert t.find("ZL1AA") is None and t.get(42) is None


def test_full_table_evicts_least_recently_heard(clock):
    t = StationTable()
    for i in range(BUF_SIZE):
        clock[0] += 1
        store(t, f"K{i}AA")
    clock[0] += 1
    store(t, "K5AA")                     # K0AA is now the oldest
    clock[0] += 1
    assert store(t, "ZL1NEW").idx == 0
    assert t.find("K0AA") is None and t.find("ZL1NEW").idx == 0
    assert len(t) == BUF_SIZE


def test_freed_slots_are_reused_in_order():
    t = StationTable(size=3)
    for c in ("A1A", "B1B", "C1C"):
        store(t, c)
    assert t.clear() == 3 and len(t) == 0 and t.find("A1A") is None
    assert store(t, "D1D").idx == 0


def test_hits_most_recent_last(clock):
    t = StationTable()
    store(t, "VK3XY", wanted=[DXCC_VK])
    clock[0] += 1
    store(t, "W1ABC")
    clock[0] += 1
    store(t, "K2XYZ", wanted=[Hit("POTA")])
    clock[0] += 1
    store(t, "VK3XY", wanted=[DXCC_VK])
    assert [s.callsign for s in t.hits()] == ["K2XYZ", "VK3XY"]
    store(t, "VK3XY")                                 # no longer a hit
    assert [s.callsign for s in t.hits()] == ["K2XYZ"]
