"""ADIF parsing, the ADIF input, and the per-call index built from it."""

from datetime import date
from pathlib import Path

from jtcon.core.adif import WSJT_LOG_SEARCH_PATHS, parse_adif
from jtcon.core.inputs import AdifInput
from conftest import LOG_PATH, FakeHamdat


def test_parse_adif_records():
    qsos = parse_adif(LOG_PATH)
    assert len(qsos) == 9
    assert qsos[0] == {
        "call": "W1ABC", "band": "20m", "mode": "FT8", "qso_date": "20250101",
        "lotw_qsl_rcvd": "Y", "state": "MA", "gridsquare": "FN42", "app_qrzlog_status": "C",
    }
    # Upper-case tags and a type indicator (<CALL:6:S>) parse the same way;
    # values are taken by length, so embedded spaces survive.
    assert qsos[7]["call"] == "EA8ABC"
    assert qsos[7]["cq_zone"] == "33"
    assert qsos[7]["comment"] == "two words"


def test_parse_adif_without_header_or_final_eor(tmp_path):
    f = tmp_path / "bare.adi"
    f.write_text("<call:4>K1AA <band:3>20m <eor>\n<call:4>K2BB <band:3>40m\n")
    assert parse_adif(f) == [
        {"call": "K1AA", "band": "20m"},
        {"call": "K2BB", "band": "40m"},
    ]


def test_parse_adif_empty(tmp_path):
    f = tmp_path / "empty.adi"
    f.write_text("header only <EOH>\n")
    assert parse_adif(f) == []


def test_call_index(load_log):
    _, a = load_log()
    assert a.worked_calls == {"W1ABC", "K2XYZ", "JA1XYZ", "DL1ABC", "W9ZZZ",
                              "G4ABC", "EA8ABC", "K5TX"}
    assert a.worked_grids == {"FN42", "JO62"}          # 6-char grids cut to 4


def test_band_and_mode_index(load_log):
    _, a = load_log()
    assert a.worked_calls_by_band["20m"] == {"W1ABC", "JA1XYZ", "DL1ABC", "K5TX"}
    assert a.worked_calls_by_band["30m"] == {"W1ABC"}
    # FT4 is logged as MODE=MFSK SUBMODE=FT4; the submode wins.
    assert a.worked_calls_by_mode["FT4"] == {"K2XYZ"}
    assert "MFSK" not in a.worked_calls_by_mode


def test_last_worked_keeps_newest_date(load_log):
    _, a = load_log()
    assert a.last_worked["W1ABC"] == date(2025, 3, 1)   # two QSOs; newest wins
    assert a.last_worked["DL1ABC"] == date(2026, 10, 12)


def test_confirmed_calls(load_log):
    _, a = load_log()
    assert a.confirmed_calls["lotw"] == {"W1ABC", "DL1ABC", "W9ZZZ"}
    assert a.confirmed_calls["qrz"] == {"W1ABC", "EA8ABC"}
    assert a.confirmed_calls["eqsl"] == {"G4ABC"}
    assert a.confirmed("W1ABC", "lotw", "20m") and not a.confirmed("W1ABC", "lotw", "30m")
    assert a.confirmed("EA8ABC", "qrz") and not a.confirmed("EA8ABC", "lotw")


def test_states_from_adif_and_hamdat(load_log):
    # MA, NJ, TX come from the ADIF state field; IL (W9ZZZ) from hamdat.
    assert load_log()[1].worked_states == {"MA", "NJ", "TX", "IL"}
    assert load_log(hamdat=None)[1].worked_states == {"MA", "NJ", "TX"}


def test_unreadable_file_is_logged_and_skipped(cty, tmp_path):
    logged = []
    logbook, _ = AdifInput([tmp_path / "missing.adi", LOG_PATH], cty, FakeHamdat(),
                           log=logged.append).load()
    assert len(logbook) == 9
    assert len(logged) == 1 and logged[0].startswith("[adif] Cannot read missing.adi")


def test_log_search_covers_wsjtx_and_ws_suite():
    home = Path.home()
    for app in ("WSJT-X", "WSJT-X Exp", "WS"):
        for base in (".local/share", "AppData/Local", "Library/Application Support"):
            assert home / base / app / "wsjtx_log.adi" in WSJT_LOG_SEARCH_PATHS
    assert home / "AppData/Roaming/WSJT-X/wsjtx_log.adi" in WSJT_LOG_SEARCH_PATHS   # v2's
