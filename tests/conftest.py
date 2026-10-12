"""Shared fixtures for the jtcon test suite."""

import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

import jtcon.core.adif
import jtcon.core.decode
import jtcon.tui.render
from jtcon.cli import build_parser
from jtcon.core.adif import AdifLog
from jtcon.core.cty import CtyDat
from jtcon.core.decode import DecodeContext
from jtcon.core.hamdat import load_state_set

FIXTURES = Path(__file__).parent / "fixtures"
CTY_PATH = FIXTURES / "cty.dat"
LOG_PATH = FIXTURES / "log.adi"

# "Today" for every test, so day counts and worked-recently checks are stable.
FROZEN_NOW = datetime(2026, 10, 12, 12, 34, 56)

# Callsign → US state, served by FakeHamdat and by make_hamdat_db().
HAMDAT_STATES = {
    "W1ABC": "MA", "K2XYZ": "NJ", "W3QQQ": "PA", "W9ZZZ": "IL",
    "N1NEW": "NH", "K5TX": "TX",
}

MY_CALL = "N0CALL"


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW.replace(tzinfo=tz)


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    for mod in (jtcon.core.adif, jtcon.core.decode, jtcon.tui.render):
        monkeypatch.setattr(mod, "datetime", _FrozenDatetime)


class FakeHamdat:
    def state(self, callsign):
        return HAMDAT_STATES.get(callsign.upper())

    def close(self):
        pass


def make_hamdat_db(path: Path, states: dict = HAMDAT_STATES) -> Path:
    """Write a minimal hamdat SQLite database with the tables HamdatLookup queries."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE license_header (id INTEGER PRIMARY KEY, call_sign TEXT,
                                     license_status TEXT, unique_system_identifier INTEGER);
        CREATE TABLE entity (unique_system_identifier INTEGER, entity_type TEXT, state TEXT);
    """)
    for usi, (call, state) in enumerate(states.items(), 1):
        conn.execute("INSERT INTO license_header (call_sign, license_status,"
                     " unique_system_identifier) VALUES (?, 'A', ?)", (call, usi))
        conn.execute("INSERT INTO entity VALUES (?, 'L', ?)", (usi, state))
    conn.commit()
    conn.close()
    return path


@pytest.fixture(scope="session")
def cty():
    c = CtyDat()
    c.load(CTY_PATH)
    return c


@pytest.fixture
def make_adif(cty):
    def _make(paths=(LOG_PATH,), hamdat=FakeHamdat(), qsl_only=False):
        return AdifLog([Path(p) for p in paths], cty, hamdat,
                       qsl_only=qsl_only, log=lambda m: None)
    return _make


def make_args(*argv: str):
    """Parse a v2 command line with jtcon's real parser."""
    return build_parser().parse_args(list(argv))


@pytest.fixture
def make_ctx(cty, make_adif):
    """
    Build a DecodeContext the way startup does: adif, hamdat and patterns are
    derived from the command line.  Pass adif=False / hamdat=None to drop them.
    """
    def _make(*argv, adif=True, hamdat=FakeHamdat(), dial_freq=14_074_000,
              call_patterns=(), alerted=None, json_fh=None, log=None):
        args = make_args(*argv)
        msg_patterns = []
        for flag, keyword in ((args.pota, "POTA"), (args.sota, "SOTA"), (args.iota, "IOTA")):
            if flag:
                msg_patterns.append((re.compile(rf"\b{keyword}\b", re.IGNORECASE), None))
        return DecodeContext(
            args=args, cty=cty,
            adif_log=make_adif(hamdat=hamdat, qsl_only=args.qsl_only) if adif else None,
            json_fh=json_fh,
            call_patterns=list(call_patterns),
            msg_patterns=msg_patterns,
            state_set=load_state_set(args.match_state) if args.match_state else set(),
            hamdat=hamdat, cycle_tracker=None,
            alerted={} if alerted is None else alerted,
            dial_freq=dial_freq, de_call=MY_CALL,
            log=log or (lambda m: None),
        )
    return _make


def watch_patterns(*regexes: str, source: str = "watch.txt") -> list:
    return [(re.compile(r, re.IGNORECASE), source) for r in regexes]
