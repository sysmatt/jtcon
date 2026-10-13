"""Config: TOML layers, profiles, CLI overrides, validation and --show-config."""

import tomllib
from pathlib import Path

import pytest

from jtcon.cli import build_parser, cli_overrides
from jtcon.config import (CONFIG_TEMPLATE, ConfigError, build_config, check_layer,
                          load_config, merge, show_config)
from jtcon.core.cty import CTY_DAT_DEFAULT
from jtcon.core.hamdat import HAMDAT_DB_DEFAULT
from conftest import make_config, v2_config
from v2flags import v2_to_config


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def rules(cfg) -> list:
    return [(w.rule, w.name, w.settings) for w in cfg.wanted]


# ── Defaults ──────────────────────────────────────────────────────────────────

def test_defaults_without_a_config_file(tmp_path):
    cfg = load_config(config_dir=tmp_path)
    assert cfg.sources == []
    assert (cfg.udp.host, cfg.udp.port, cfg.udp.proxy) == ("0.0.0.0", 2237, [])
    assert cfg.inputs.cty == CTY_DAT_DEFAULT and cfg.inputs.hamdat == HAMDAT_DB_DEFAULT
    assert not cfg.evaluate_all and not cfg.show_all
    assert cfg.filters.suppress_worked_within_days == 0
    # dxcc, cqz and country, counted by LoTW confirmation.
    assert rules(cfg) == [("dxcc", "dxcc", {"confirm": ["lotw"]}),
                          ("cqz", "cqz", {"confirm": ["lotw"]}),
                          ("country", "country", {"confirm": ["lotw"]})]
    assert cfg.entity_needs_lotw


def test_worked_is_enough_with_an_empty_confirm():
    cfg = make_config({"wanted": [{"rule": "dxcc", "confirm": []}]})
    assert not cfg.entity_needs_lotw


# ── Layers ────────────────────────────────────────────────────────────────────

def test_profile_merges_tables_and_replaces_lists(tmp_path):
    write(tmp_path / "config.toml", """
        [udp]
        port = 2240
        proxy = [2238, 2239]
        [display]
        show_time = true
        [[wanted]]
        rule = "dxcc"
        [[wanted]]
        rule = "was"
        """)
    write(tmp_path / "profiles" / "parks.toml", """
        [udp]
        proxy = [2241]
        [[wanted]]
        rule = "pota"
        """)
    cfg = load_config(profile="parks", config_dir=tmp_path)
    assert cfg.sources == [tmp_path / "config.toml", tmp_path / "profiles" / "parks.toml"]
    assert cfg.udp.port == 2240                 # kept from the base file
    assert cfg.udp.proxy == [2241]              # list replaced, not appended
    assert cfg.display.show_time
    assert [w.rule for w in cfg.wanted] == ["pota"]


def test_profile_without_wanted_keeps_the_base_rules(tmp_path):
    write(tmp_path / "config.toml", '[[wanted]]\nrule = "was"\n')
    write(tmp_path / "profiles" / "quiet.toml", "[display]\ncolor = false\n")
    cfg = load_config(profile="quiet", config_dir=tmp_path)
    assert [w.rule for w in cfg.wanted] == ["was"] and not cfg.display.color


def test_profile_next_to_an_explicit_config_file(tmp_path):
    cfg_file = write(tmp_path / "elsewhere" / "my.toml", "")
    write(tmp_path / "elsewhere" / "profiles" / "p.toml", "[udp]\nport = 3000\n")
    assert load_config(cfg_file, "p").udp.port == 3000


def test_profile_given_as_a_path(tmp_path):
    p = write(tmp_path / "x" / "contest.toml", "[udp]\nport = 3001\n")
    assert load_config(profile=str(p), config_dir=tmp_path).udp.port == 3001


def test_missing_files(tmp_path):
    with pytest.raises(ConfigError, match="profile 'nope' not found"):
        load_config(profile="nope", config_dir=tmp_path)
    with pytest.raises(ConfigError, match="missing.toml: file not found"):
        load_config(tmp_path / "missing.toml")


def test_cli_overrides_win(tmp_path):
    write(tmp_path / "config.toml", """
        [udp]
        port = 2240
        [display]
        color = true
        """)
    args = build_parser().parse_args(["--port", "2250", "--no-color", "--scope", "all",
                                      "--save", "spots.jsonl"])
    cfg = load_config(overrides=cli_overrides(args), config_dir=tmp_path)
    assert cfg.udp.port == 2250 and not cfg.display.color and cfg.evaluate_all
    assert cfg.actions.json_log == Path.cwd() / "spots.jsonl"   # CLI paths: from cwd


def test_absent_cli_flags_override_nothing():
    assert cli_overrides(build_parser().parse_args([])) == {}


def test_merge():
    assert merge({"a": {"x": 1, "y": [1]}, "b": 1},
                 {"a": {"y": [2]}, "c": 3}) == {"a": {"x": 1, "y": [2]}, "b": 1, "c": 3}


# ── Paths ─────────────────────────────────────────────────────────────────────

def test_relative_paths_are_relative_to_their_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    write(tmp_path / "config.toml", """
        [inputs]
        adif = ["logs/a.adi", "~/b.adi", "/abs/c.adi"]
        hamdat = "db/hamdat.db"
        [[wanted]]
        rule = "callsigns"
        files = "watch.txt"
        """)
    cfg = load_config(config_dir=tmp_path)
    assert cfg.inputs.adif == [tmp_path / "logs/a.adi", tmp_path / "home/b.adi",
                               Path("/abs/c.adi")]
    assert cfg.inputs.hamdat == tmp_path / "db/hamdat.db"
    assert cfg.rule("callsigns").settings["files"] == [tmp_path / "watch.txt"]


# ── --wanted ──────────────────────────────────────────────────────────────────

BASE = {"wanted": [{"rule": "was", "name": "WAS-ARRL"},
                   {"rule": "callsigns", "files": ["/w.txt"]},
                   {"rule": "dxcc", "confirm": []}]}


def test_wanted_selects_by_name_or_rule_keeping_settings():
    cfg = make_config(BASE, wanted="was-arrl, callsigns")
    assert rules(cfg) == [("was", "WAS-ARRL", {"confirm": ["lotw"]}),
                          ("callsigns", "callsigns", {"files": [Path("/w.txt")]})]
    assert make_config(BASE, wanted="dxcc").rule("dxcc").settings["confirm"] == []


def test_wanted_unconfigured_rule_gets_defaults():
    assert rules(make_config(BASE, wanted="pota,cqz")) == [
        ("pota", "pota", {}), ("cqz", "cqz", {"confirm": ["lotw"]})]


def test_wanted_none_and_errors():
    assert make_config(BASE, wanted="none").wanted == []
    with pytest.raises(ConfigError, match="'bogus' is not a rule"):
        make_config(BASE, wanted="bogus")
    with pytest.raises(ConfigError, match="'message' needs a 'files' setting"):
        make_config(BASE, wanted="message")


# ── Validation ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("layer, error", [
    ({"udp": {"prot": 1}}, "unknown setting 'udp.prot'"),
    ({"udpp": {}}, "unknown setting 'udpp'"),
    ({"udp": {"port": "2237"}}, "udp.port must be an integer, not '2237'"),
    ({"udp": {"port": True}}, "udp.port must be an integer"),
    ({"udp": {"port": 70000}}, "udp.port: 70000 is not a port number"),
    ({"udp": {"proxy": [0]}}, "udp.proxy: 0 is not a port number"),
    ({"udp": 5}, r"udp must be a table \(\[udp\]\)"),
    ({"display": {"show": "some"}}, 'display.show must be "cq" or "all"'),
    ({"display": {"color": "no"}}, "display.color must be true or false"),
    ({"inputs": {"adif": [""]}}, "inputs.adif must be a path"),
    ({"wanted": {"rule": "dxcc"}}, r"wanted must be a list of \[\[wanted\]\] tables"),
    ({"wanted": [{"rule": "nope"}]}, r"rule must be one of dxcc, .*; got 'nope'"),
    ({"wanted": [{}]}, "got None"),
    ({"wanted": [{"rule": "pota", "files": "x"}]}, "unknown setting 'files' .known: name."),
    ({"wanted": [{"rule": "dxcc", "confirm": ["paper"]}]}, "confirm must be"),
    ({"wanted": [{"rule": "states"}]}, "'states' needs 'states' or 'file'"),
    ({"wanted": [{"rule": "dxcc"}, {"rule": "dxcc", "name": "d2"}]}, "appears twice"),
    ({"wanted": [{"rule": "dxcc"}, {"rule": "pota", "name": "DXCC"}]}, "two .* named 'DXCC'"),
    ({"wanted": [{"rule": "was"}, {"rule": "5bwas"}]}, "can't be used together"),
    ({"wanted": [{"rule": "dxcc"}, {"rule": "cqz", "confirm": []}]}, "same confirm"),
    ({"wanted": [{"rule": "dxcc", "confirm": ["qrz"]}]}, 'not supported yet; use \\[\\] or \\["lotw"\\]'),
    ({"wanted": [{"rule": "was", "confirm": []}]}, 'not supported yet; use \\["lotw"\\]'),
])
def test_invalid_settings(layer, error):
    with pytest.raises(ConfigError, match=error):
        make_config(layer)


def test_errors_name_the_file(tmp_path):
    write(tmp_path / "config.toml", "[display]\nshow = 3\n")
    with pytest.raises(ConfigError, match=f"^{tmp_path}/config.toml: display.show must be"):
        load_config(config_dir=tmp_path)
    write(tmp_path / "config.toml", "[display\n")
    with pytest.raises(ConfigError, match=f"^{tmp_path}/config.toml: "):
        load_config(config_dir=tmp_path)


def test_single_values_become_lists():
    cfg = make_config({"udp": {"proxy": 2238}, "inputs": {"adif": "/a.adi"}})
    assert cfg.udp.proxy == [2238] and cfg.inputs.adif == [Path("/a.adi")]


# ── Output ────────────────────────────────────────────────────────────────────

def test_show_config_round_trips(tmp_path):
    cfg = make_config({
        "udp": {"proxy": [2238]}, "display": {"show": "all"},
        "inputs": {"adif": ["/x/a.adi"]},
        "actions": {"ntfy": {"topic": "t"}, "script": {"on_log_reload": "/up.sh"},
                    "json_log": {"file": "/s.jsonl"}},
        "wanted": [{"rule": "dxcc", "confirm": []}, {"rule": "5bwas", "name": "5BWAS"},
                   {"rule": "states", "states": ["ak", "DE"]},
                   {"rule": "message", "files": ["/m.txt"]}],
    })
    text = show_config(cfg)
    assert text.startswith("# Effective jtcon configuration\n# from: built-in defaults")
    again = build_config(check_layer(tomllib.loads(text), tmp_path))
    assert again == cfg


def test_show_config_with_no_rules(tmp_path):
    cfg = make_config({"wanted": []})
    assert build_config(check_layer(tomllib.loads(show_config(cfg)), tmp_path)).wanted == []


def test_template_is_valid_and_matches_the_defaults(tmp_path):
    path = write(tmp_path / "config.toml", CONFIG_TEMPLATE)
    cfg = load_config(config_dir=tmp_path)
    assert cfg.sources == [path]
    cfg.sources = []
    assert cfg == load_config(config_dir=tmp_path / "empty")


# ── v2 → v3 translation ───────────────────────────────────────────────────────

# Matt's real v2 command line, and its hand-written v3 profile.
K2TTA_V2 = ("--adif", "/home/k2tta/ham/data/qrzlog.adif",
            "/home/k2tta/.local/share/WS/wsjtx_log.adi",
            "--alert-ntfy", "k2tta-jtwatch", "--match-state", "AK,DE", "--qsl-only",
            "--adif-change-script", "/home/k2tta/ham/up.sh",
            "--match-calls", "/home/k2tta/ham/data/wanted.txt",
            "/home/k2tta/ham/data/SCARC.txt",
            "--worked-lag-days", "5", "--5bwas", "--all")

K2TTA_V3 = """
[scope]
decodes = "all"

[inputs]
adif = ["/home/k2tta/ham/data/qrzlog.adif", "/home/k2tta/.local/share/WS/wsjtx_log.adi"]

[filters]
suppress_worked_within_days = 5

[[wanted]]
rule = "dxcc"

[[wanted]]
rule = "cqz"

[[wanted]]
rule = "country"

[[wanted]]
rule = "5bwas"

[[wanted]]
rule  = "callsigns"
files = ["/home/k2tta/ham/data/wanted.txt", "/home/k2tta/ham/data/SCARC.txt"]

[[wanted]]
rule   = "states"
states = ["AK", "DE"]

[actions.ntfy]
topic = "k2tta-jtwatch"

[actions.script]
on_log_reload = "/home/k2tta/ham/up.sh"
"""


def test_k2tta_profile_matches_the_v2_command_line(tmp_path):
    path = write(tmp_path / "config.toml", K2TTA_V3)
    cfg = load_config(config_dir=tmp_path)
    assert cfg.sources == [path]
    cfg.sources = []
    assert cfg == v2_config(*K2TTA_V2)


def test_v2_defaults_translate_to_worked_is_enough():
    assert v2_to_config()["wanted"] == [{"rule": r, "confirm": []}
                                        for r in ("dxcc", "cqz", "country")]
    assert not v2_config().entity_needs_lotw and v2_config("--qsl-only").entity_needs_lotw
