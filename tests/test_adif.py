"""ADIF parsing and the worked/confirmed index built from it (AdifLog)."""

from datetime import date

from jtcon.core.adif import AdifLog, parse_adif
from jtcon.core.decode import _entity_info
from conftest import LOG_PATH, FakeHamdat, v2_config


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


def test_worked_index(make_adif):
    a = make_adif()
    assert a.total_qsos == 9
    assert a.worked_calls == {"W1ABC", "K2XYZ", "JA1XYZ", "DL1ABC", "W9ZZZ",
                              "G4ABC", "EA8ABC", "K5TX"}
    assert a.worked_grids == {"FN42", "JO62"}          # 6-char grids cut to 4
    assert a.worked_dxcc == {"K", "JA", "DL", "G", "EA8"}
    assert a.worked_cqz == {5, 25, 14, 33}
    assert a.worked_country == {"UNITED STATES", "JAPAN", "FED. REP. OF GERMANY",
                                "ENGLAND", "CANARY ISLANDS"}


def test_band_and_mode_index(make_adif):
    a = make_adif()
    assert a.worked_calls_by_band["20m"] == {"W1ABC", "JA1XYZ", "DL1ABC", "K5TX"}
    assert a.worked_calls_by_band["30m"] == {"W1ABC"}
    # FT4 is logged as MODE=MFSK SUBMODE=FT4; the submode wins.
    assert a.worked_calls_by_mode["FT4"] == {"K2XYZ"}
    assert "MFSK" not in a.worked_calls_by_mode


def test_last_worked_keeps_newest_date(make_adif):
    a = make_adif()
    assert a.last_worked["W1ABC"] == date(2025, 3, 1)   # two QSOs; newest wins
    assert a.last_worked["DL1ABC"] == date(2026, 10, 12)


def test_confirmation_sets(make_adif):
    a = make_adif()
    assert a.lotw_qsl_rcvd_calls == {"W1ABC", "DL1ABC", "W9ZZZ"}
    assert a.qrz_confirmed_calls == {"W1ABC", "EA8ABC"}
    assert a.lotw_rcvd_by_band == {"20m": {"W1ABC", "DL1ABC"}, "15m": {"W9ZZZ"}}
    assert a.qrz_confirmed_by_band == {"20m": {"W1ABC"}, "10m": {"EA8ABC"}}


def test_qsl_only_counts_only_lotw(make_adif):
    a = make_adif(qsl_only=True)
    assert a.worked_dxcc == {"K", "DL"}
    assert a.worked_cqz == {5, 14}
    assert a.worked_country == {"UNITED STATES", "FED. REP. OF GERMANY"}
    # The *_all sets ignore --qsl-only.
    assert a.worked_dxcc_all == {"K", "JA", "DL", "G", "EA8"}
    assert a.worked_cqz_all == {5, 25, 14, 33}
    # v2: QRZ and eQSL confirmations do not count (EA8ABC is QRZ-confirmed,
    # G4ABC eQSL-confirmed).  v3 makes the confirmation sources per rule.
    assert "EA8" not in a.worked_dxcc and "G" not in a.worked_dxcc


def test_states_from_adif_and_hamdat(make_adif):
    a = make_adif()
    # MA, NJ, TX come from the ADIF state field; IL (W9ZZZ) from hamdat.
    assert a.worked_states_all == {"MA", "NJ", "TX", "IL"}
    assert a.worked_states_lotw == {"MA", "IL"}
    assert a.worked_states_bands_all == {"MA": {"20m"}, "NJ": {"40m"}, "TX": {"20m"},
                                         "IL": {"15m"}}
    assert a.worked_states_bands_lotw == {"MA": {"20m"}, "IL": {"15m"}}


def test_states_without_hamdat(make_adif):
    a = make_adif(hamdat=None)
    assert a.worked_states_all == {"MA", "NJ", "TX"}


def test_unreadable_file_is_logged_and_skipped(cty, tmp_path):
    logged = []
    a = AdifLog([tmp_path / "missing.adi", LOG_PATH], cty, FakeHamdat(),
                      log=logged.append)
    assert a.total_qsos == 9
    assert len(logged) == 1 and logged[0].startswith("[adif] Cannot read missing.adi")


def test_needed_and_worked_flags(make_adif, cty):
    a = make_adif(qsl_only=True)
    args = v2_config("--qsl-only")
    ja = _entity_info(cty, "JA1XYZ")    # worked, not LoTW-confirmed
    vk = _entity_info(cty, "VK3XY")     # never worked
    dl = _entity_info(cty, "DL1ABC")    # LoTW-confirmed
    assert a.needed_flags(ja, args) == ["NEW-DXCC(JA)", "NEW-CQZ(25)", "NEW-CTRY(Japan)"]
    assert a.worked_flags(ja, args) == []
    assert a.worked_flags_all(ja, args) == ["dxcc", "cqz", "country"]
    assert a.needed_flags(vk, args) == ["NEW-DXCC(VK)", "NEW-CQZ(30)", "NEW-CTRY(Australia)"]
    assert a.worked_flags_all(vk, args) == []
    assert a.needed_flags(dl, args) == []
    assert a.worked_flags(dl, args) == ["dxcc", "cqz", "country"]

    only_dxcc = v2_config("--no-need-cqz", "--no-need-country")
    assert a.needed_flags(vk, only_dxcc) == ["NEW-DXCC(VK)"]
