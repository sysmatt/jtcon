"""Shared fixtures for the jtcon test suite."""

import re
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

import jtcon.core.decode
import jtcon.tui.render
from jtcon.config import WantedSpec, build_config, check_layer
from jtcon.core.cty import CtyDat
from jtcon.core.decode import DecodeContext, debug_text, evaluate
from jtcon.core.engine import Engine
from jtcon.core.inputs import AdifInput
from jtcon.core.protocol import Decode, parse_message
from jtcon.core.wanted import build_rules
from jtcon.tui.render import _render
from v2flags import v2_to_config

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
    for mod in (jtcon.core.decode, jtcon.tui.render):
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


def load_adif(engine, paths=(LOG_PATH,)) -> AdifInput:
    """Load *paths* into *engine* as its startup would, and watch them."""
    inp = AdifInput(paths, engine.cty, engine.hamdat, log=engine.log)
    engine.logbook, engine.calls = inp.load()
    engine.rebuild_rules()
    engine.adif_input, engine.inputs = inp, [inp]
    return inp


@pytest.fixture
def load_log(cty):
    """(Logbook, CallIndex) for *paths*, as startup builds them."""
    def _load(paths=(LOG_PATH,), hamdat=FakeHamdat()):
        return AdifInput(paths, cty, hamdat).load()
    return _load


def add_watchlist(cfg, patterns) -> None:
    """Tests pass watchlist patterns directly; make sure a callsigns rule is on."""
    if patterns and not cfg.rule("callsigns"):
        cfg.wanted.append(WantedSpec("callsigns", "callsigns", {"files": [Path("watch.txt")]}))


def setup_rules(rules, logbook, call_patterns=(), states=None) -> list:
    """Give *rules* what the engine's inputs would: patterns, and the logbook."""
    for rule in rules:
        if rule.spec.rule == "callsigns" and call_patterns:
            rule.patterns = list(call_patterns)
        if logbook is not None:
            rule.rebuild(logbook)
    return rules


def make_config(layer: dict | None = None, wanted: str | None = None):
    """A Config from one config-file-shaped layer over the built-in defaults."""
    return build_config(check_layer(layer or {}, FIXTURES), wanted)


def v2_config(*argv: str):
    """The v3 Config equivalent to a v2 command line (see v2flags.py)."""
    return make_config(v2_to_config(*argv))


@pytest.fixture
def make_ctx(cty, load_log):
    """
    Build a DecodeContext the way startup does: adif, hamdat and patterns are
    derived from the config.  Positional arguments are a v2 command line; pass
    config= (a config-file-shaped dict) to use v3 settings instead.  Pass
    adif=False / hamdat=None to drop them.
    """
    def _make(*argv, config=None, adif=True, hamdat=FakeHamdat(), dial_freq=14_074_000,
              call_patterns=(), alerted=None, json_fh=None, log=None):
        cfg = make_config(config) if config is not None else v2_config(*argv)
        add_watchlist(cfg, call_patterns)
        logbook, calls = load_log(hamdat=hamdat) if adif else (None, None)
        return DecodeContext(
            config=cfg, cty=cty, logbook=logbook, calls=calls,
            rules=setup_rules(build_rules(cfg.wanted), logbook, call_patterns),
            json_fh=json_fh,
            hamdat=hamdat,
            alerted={} if alerted is None else alerted,
            dial_freq=dial_freq, de_call=MY_CALL,
            log=log or (lambda m: None),
        )
    return _make


class FakeSock:
    """Records what is sent; fails every send when fail=True."""
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def sendto(self, data, addr):
        if self.fail:
            raise OSError("network down")
        self.sent.append((data, addr))


@pytest.fixture
def make_engine(cty):
    """
    An Engine with the test inputs already loaded (as its startup would) and a
    FakeSock for sending.  Arguments as for make_ctx.  Its events are collected
    in engine.events.
    """
    def _make(*argv, config=None, adif=True, hamdat=FakeHamdat(), dial_freq=14_074_000,
              call_patterns=(), tx_sock=None):
        cfg = make_config(config) if config is not None else v2_config(*argv)
        add_watchlist(cfg, call_patterns)
        engine = Engine(cfg, None, tx_sock or FakeSock())
        engine.cty, engine.hamdat = cty, hamdat
        if adif:
            load_adif(engine)
        setup_rules(engine.rules, None, call_patterns)
        engine.dial_freq, engine.de_call = dial_freq, MY_CALL
        engine.events = []
        engine.subscribe(engine.events.append)
        return engine
    return _make


def watch_patterns(*regexes: str, source: str = "watch.txt") -> list:
    return [(re.compile(r, re.IGNORECASE), source) for r in regexes]


def handle_packet(data: bytes, ctx, addr, out_meta: dict | None = None) -> list:
    """
    v2's handle_packet, rebuilt from evaluate() and the renderer, so tests keep
    comparing the same display lines.  Fills *out_meta* like v2 did.
    """
    parsed = parse_message(data)
    if parsed is None or not isinstance(parsed[1], Decode):
        return []
    schema, d = parsed
    lines = [debug_text(data, schema, d)] if ctx.config.display.debug else []
    r = evaluate(d, ctx)
    if r is None:
        return lines
    lines.extend(_render(ctx, r))
    if out_meta is not None:
        out_meta.update(decoded=d, callsign=r.callsign, is_cq=r.is_cq,
                        wanted=[h.mark() for h in r.wanted], hits=r.hits,
                        wsjt_addr=addr, bell=r.new_alert)
    return lines
