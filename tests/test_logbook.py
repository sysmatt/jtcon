"""The Logbook: QSO records from ADIF and QSO Logged, and merging across sources."""

import os
from datetime import date, datetime, time, timedelta, timezone

import jtcon.core.inputs
from jtcon.core.inputs import AdifInput
from jtcon.core.logbook import QSO, Logbook, qso_from_adif, qso_from_logged
from jtcon.core.protocol import QsoLogged
from conftest import LOG_PATH, FakeHamdat


def test_qso_from_adif(cty):
    rec = {"call": "w1abc", "band": "20M", "mode": "MFSK", "submode": "FT4",
           "qso_date": "20250101", "time_on": "1230", "gridsquare": "fn42qm",
           "lotw_qsl_rcvd": "Y", "app_qrzlog_status": "C", "eqsl_qsl_rcvd": "y",
           "qsl_rcvd": "N", "state": "ma"}
    q = qso_from_adif(rec, cty, FakeHamdat(), "log.adi")
    assert q == QSO(call="W1ABC", date=date(2025, 1, 1), time=time(12, 30), band="20m",
                    mode="FT4", grid="FN42", dxcc="K", country="UNITED STATES",
                    cqz=5, ituz=8, state="MA", confirmations={"lotw", "qrz", "eqsl"},
                    sources={"log.adi"})


def test_qso_from_adif_fallbacks(cty):
    # State from hamdat; zone as logged wins over cty.dat; a bad zone is dropped.
    q = qso_from_adif({"call": "W9ZZZ", "cq_zone": "4", "time_on": "123456"},
                      cty, FakeHamdat(), "x")
    assert (q.state, q.cqz, q.time) == ("IL", 4, time(12, 34, 56))
    q = qso_from_adif({"call": "W9ZZZ", "cqz": "x"}, cty, None, "x")
    assert (q.state, q.cqz, q.date, q.time) == ("", None, None, None)


def test_qso_from_logged(cty):
    msg = QsoLogged(dx_call="vk3xy", dx_grid="QF22lb", tx_freq=7_074_000, mode="FT8",
                    time_on=datetime(2026, 10, 12, 23, 59, 30, 500_000,
                                     tzinfo=timezone(timedelta(hours=-4))))
    q = qso_from_logged(msg, cty, FakeHamdat())
    assert q == QSO(call="VK3XY", date=date(2026, 10, 13), time=time(3, 59, 30),
                    band="40m", mode="FT8", grid="QF22", dxcc="VK", country="AUSTRALIA",
                    cqz=30, ituz=59, sources={"wsjt-x"})


def qso(source, when="20261012 1230", **kw):
    d, t = when.split()
    return QSO(call=kw.pop("call", "VK3XY"), band="20m", mode="FT8",
               date=datetime.strptime(d, "%Y%m%d").date(),
               time=datetime.strptime(t, "%H%M").time(), sources={source}, **kw)


def test_same_qso_from_two_sources_is_merged():
    wsjt = qso("wsjtx_log.adi", grid="QF22")
    qrz = qso("qrz.adi", when="20261012 1240", state="", confirmations={"lotw", "qrz"},
              log_country="AUSTRALIA")
    lb = Logbook([wsjt, qrz])
    (rec,) = lb.qsos
    assert lb.merged == 1
    assert rec.confirmations == {"lotw", "qrz"} and rec.sources == {"wsjtx_log.adi", "qrz.adi"}
    assert (rec.grid, rec.log_country, rec.time) == ("QF22", "AUSTRALIA", time(12, 30))
    assert wsjt.confirmations == set() and wsjt.sources == {"wsjtx_log.adi"}   # untouched


def test_records_that_are_not_the_same_qso():
    base = qso("a")
    for other in (qso("b", when="20261012 1246"),          # outside 15 minutes
                  qso("b", call="ZL2AB"),
                  qso("a"),                                # same source: two QSOs
                  QSO(call="VK3XY", band="40m", mode="FT8", date=base.date,
                      time=base.time, sources={"b"}),
                  QSO(call="VK3XY", band="20m", mode="FT4", date=base.date,
                      time=base.time, sources={"b"})):
        assert len(Logbook([base, other])) == 2


def test_without_a_time_the_same_day_matches():
    timeless = QSO(call="VK3XY", band="20m", mode="FT8", date=date(2026, 10, 12),
                   sources={"b"})
    assert len(Logbook([qso("a"), timeless])) == 1
    timeless.date = date(2026, 10, 11)
    assert len(Logbook([qso("a"), timeless])) == 2


def test_records_without_a_call_are_kept_apart():
    assert len(Logbook([QSO(call="", sources={"a"}), QSO(call="", sources={"b"})])) == 2


def test_adif_input_merges_files_and_logged_qsos(cty, tmp_path):
    other = tmp_path / "qrz.adi"
    other.write_text("<call:6>JA1XYZ <band:3>20m <mode:3>FT8 <qso_date:8>20251010"
                     " <lotw_qsl_rcvd:1>Y <eor>\n")
    inp = AdifInput([LOG_PATH, other], cty, FakeHamdat(), qsl_only=True)
    inp.extra.append(qso("wsjt-x", call="ZL2AB"))
    logbook, index = inp.load()
    assert (len(logbook), logbook.merged, logbook.unsaved) == (10, 1, 1)
    assert "JA" in index.worked_dxcc                   # confirmed only in qrz.adi


def test_adif_input_parses_only_changed_files(cty, tmp_path, monkeypatch):
    a, b = tmp_path / "a.adi", tmp_path / "b.adi"
    a.write_text("<call:4>K1AA <eor>")
    b.write_text("<call:4>K2BB <eor>")
    parsed = []
    real = jtcon.core.inputs.parse_adif
    monkeypatch.setattr(jtcon.core.inputs, "parse_adif",
                        lambda p: parsed.append(p.name) or real(p))
    inp = AdifInput([a, b], cty, None)
    inp.load()
    assert not inp.changed()
    b.write_text("<call:4>K2BB <eor><call:4>K3CC <eor>")
    st = b.stat()
    os.utime(b, (st.st_atime, st.st_mtime + 5))
    assert inp.changed()
    logbook, _ = inp.load()
    assert parsed == ["a.adi", "b.adi", "b.adi"] and len(logbook) == 3
