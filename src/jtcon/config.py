"""Settings: built-in defaults, the TOML config file, profiles and CLI overrides.

Layers, later ones winning:

    built-in defaults
    ~/.config/jtcon/config.toml        (or --config FILE)
    profiles/<name>.toml               (--profile NAME, next to the config file)
    command-line flags

Tables merge key by key; any other value, lists included, replaces the one
below it.  So a profile with [[wanted]] entries replaces the whole rule list.
"""

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from jtcon.core.cty import CTY_DAT_DEFAULT
from jtcon.core.hamdat import HAMDAT_DB_DEFAULT


CONFIG_FILE  = "config.toml"
PROFILES_DIR = "profiles"


class ConfigError(Exception):
    """A config file or override that can't be used; the message says why and where."""


def default_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "jtcon"


# ══════════════════════════════════════════════════════════════════════════════
#  Wanted rules registry
# ══════════════════════════════════════════════════════════════════════════════

CONFIRM_SOURCES = ("lotw", "qrz", "eqsl", "card")

_REQUIRED = object()   # marks a rule setting with no default


@dataclass(frozen=True)
class RuleInfo:
    key:      str
    kind:     str    # "award" (✅❓❌ status) or "watch" (hit or no hit)
    label:    str
    settings: dict = field(default_factory=dict)   # name -> default, or _REQUIRED


RULES = {r.key: r for r in (
    RuleInfo("dxcc",      "award", "DXCC entity",                  {"confirm": ["lotw"]}),
    RuleInfo("cqz",       "award", "CQ zone (WAZ)",                {"confirm": ["lotw"]}),
    RuleInfo("country",   "award", "Country name",                 {"confirm": ["lotw"]}),
    RuleInfo("was",       "award", "Worked All States; needs hamdat",
             {"confirm": ["lotw"]}),
    RuleInfo("5bwas",     "award", "5-Band WAS on the current band; needs hamdat",
             {"confirm": ["lotw"]}),
    RuleInfo("pota",      "watch", "POTA activators (POTA in the message)"),
    RuleInfo("sota",      "watch", "SOTA activators (SOTA in the message)"),
    RuleInfo("iota",      "watch", "IOTA stations (IOTA in the message)"),
    RuleInfo("callsigns", "watch", "Callsign watchlist: regex files",  {"files": _REQUIRED}),
    RuleInfo("message",   "watch", "Message watchlist: regex files",   {"files": _REQUIRED}),
    RuleInfo("states",    "watch", "US states; needs hamdat",
             {"states": [], "file": ""}),
)}

DEFAULT_WANTED = ("dxcc", "cqz", "country")

_ENTITY_RULES = ("dxcc", "cqz", "country")


@dataclass
class WantedSpec:
    """One [[wanted]] entry: which rule, its instance name, and its settings."""
    rule:     str
    name:     str
    settings: dict


# ══════════════════════════════════════════════════════════════════════════════
#  Effective configuration
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class UdpConfig:
    host:  str = "0.0.0.0"
    port:  int = 2237
    proxy: list = field(default_factory=list)   # localhost ports to forward to


@dataclass
class DisplayConfig:
    show:      str  = "cq"     # "cq": CQs and wanted hits; "all": every decode
    show_time: bool = False
    color:     bool = True
    mouse:     bool = False
    debug:     bool = False


@dataclass
class ScopeConfig:
    decodes: str = "cq"        # check "cq" decodes only, or "all"


@dataclass
class InputsConfig:
    cty:    Path = CTY_DAT_DEFAULT
    adif:   list = field(default_factory=list)  # besides the auto-discovered WSJT-X log
    hamdat: Path = HAMDAT_DB_DEFAULT


@dataclass
class FiltersConfig:
    # Stations worked within this many days aren't wanted; 0 = today only; -1 = never
    suppress_worked_within_days: int = 5


@dataclass
class ActionsConfig:
    ntfy_topic:           str | None  = None
    script_on_wanted:     Path | None = None
    script_on_log_reload: Path | None = None
    json_log:             Path | None = None


@dataclass
class Config:
    udp:     UdpConfig     = field(default_factory=UdpConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    scope:   ScopeConfig   = field(default_factory=ScopeConfig)
    inputs:  InputsConfig  = field(default_factory=InputsConfig)
    wanted:  list          = field(default_factory=list)   # [WantedSpec]
    filters: FiltersConfig = field(default_factory=FiltersConfig)
    actions: ActionsConfig = field(default_factory=ActionsConfig)
    sources: list          = field(default_factory=list)   # files loaded, in order

    def rule(self, key: str) -> WantedSpec | None:
        return next((w for w in self.wanted if w.rule == key), None)

    def wants(self, key: str) -> bool:
        return self.rule(key) is not None

    @property
    def evaluate_all(self) -> bool:
        return self.scope.decodes == "all"

    @property
    def show_all(self) -> bool:
        return self.display.show == "all"

    @property
    def entity_needs_lotw(self) -> bool:
        """DXCC/zone/country count only LoTW-confirmed QSOs (v2's --qsl-only)."""
        specs = [w for w in self.wanted if w.rule in _ENTITY_RULES]
        return not specs or specs[0].settings["confirm"] == ["lotw"]

    def as_dict(self) -> dict:
        """The effective settings in config-file shape (what --show-config prints)."""
        a = self.actions
        actions: dict = {}
        if a.ntfy_topic:
            actions["ntfy"] = {"topic": a.ntfy_topic}
        script = {k: str(v) for k, v in (("on_wanted", a.script_on_wanted),
                                         ("on_log_reload", a.script_on_log_reload)) if v}
        if script:
            actions["script"] = script
        if a.json_log:
            actions["json_log"] = {"file": str(a.json_log)}
        return {
            "udp":     {"host": self.udp.host, "port": self.udp.port, "proxy": list(self.udp.proxy)},
            "display": vars(self.display).copy(),
            "scope":   vars(self.scope).copy(),
            "inputs":  {"cty": str(self.inputs.cty), "adif": [str(p) for p in self.inputs.adif],
                        "hamdat": str(self.inputs.hamdat)},
            "filters": vars(self.filters).copy(),
            "actions": actions,
            "wanted":  [{"rule": w.rule, "name": w.name, **_settings_out(w.settings)}
                        for w in self.wanted],
        }


def _settings_out(settings: dict) -> dict:
    return {k: (str(v) if isinstance(v, Path) else
                [str(x) for x in v] if isinstance(v, list) else v)
            for k, v in settings.items()}


# ══════════════════════════════════════════════════════════════════════════════
#  Validation of one layer (a file or the CLI), before merging
# ══════════════════════════════════════════════════════════════════════════════

_PATH = "path"


def _choice(*values):
    return ("choice", values)


_SCHEMA = {
    "udp":     {"host": str, "port": int, "proxy": [int]},
    "display": {"show": _choice("cq", "all"), "show_time": bool, "color": bool,
                "mouse": bool, "debug": bool},
    "scope":   {"decodes": _choice("cq", "all")},
    "inputs":  {"cty": _PATH, "adif": [_PATH], "hamdat": _PATH},
    "filters": {"suppress_worked_within_days": int},
    "actions": {
        "ntfy":     {"topic": str},
        "script":   {"on_wanted": _PATH, "on_log_reload": _PATH},
        "json_log": {"file": _PATH},
    },
}

_RULE_SETTINGS = {
    "confirm": [_choice(*CONFIRM_SOURCES)],
    "files":   [_PATH],
    "states":  [str],
    "file":    _PATH,
}


def _type_name(kind) -> str:
    if isinstance(kind, list):
        return f"a list of {_type_name(kind[0])}s"
    if isinstance(kind, tuple):
        return " or ".join(json.dumps(v) for v in kind[1])
    return {str: "a string", int: "an integer", bool: "true or false",
            _PATH: "a path"}[kind]


def _check_value(value, kind, where: str, base_dir: Path):
    """Validate *value* against *kind*; returns it normalized (paths made absolute)."""
    if isinstance(kind, list):
        items = value if isinstance(value, list) else [value]   # a single value is fine
        return [_check_value(v, kind[0], where, base_dir) for v in items]
    if isinstance(kind, tuple):
        if value not in kind[1]:
            raise ConfigError(f"{where} must be {_type_name(kind)}, not {value!r}")
        return value
    if kind is _PATH:
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{where} must be a path")
        p = Path(value).expanduser()
        return str(p if p.is_absolute() else base_dir / p)
    # bool is an int subclass in Python; don't let true pass as a port.
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ConfigError(f"{where} must be {_type_name(kind)}, not {value!r}")
    return value


def _check_table(raw: dict, schema: dict, where: str, base_dir: Path) -> dict:
    out = {}
    for key, value in raw.items():
        here = f"{where}.{key}" if where else key
        if key not in schema:
            known = ", ".join(schema)
            raise ConfigError(f"unknown setting {here!r} (known here: {known})")
        kind = schema[key]
        if isinstance(kind, dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{here} must be a table ([{here}])")
            out[key] = _check_table(value, kind, here, base_dir)
        else:
            out[key] = _check_value(value, kind, here, base_dir)
    return out


def _check_wanted(entries, base_dir: Path) -> list:
    if not isinstance(entries, list):
        raise ConfigError("wanted must be a list of [[wanted]] tables")
    out = []
    for i, entry in enumerate(entries, 1):
        where = f"[[wanted]] #{i}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a table")
        rule = entry.get("rule")
        if rule not in RULES:
            raise ConfigError(f"{where}: rule must be one of {', '.join(RULES)}; got {rule!r}")
        where = f"{where} (rule {rule!r})"
        checked = {"rule": rule}
        for key, value in entry.items():
            if key == "rule":
                continue
            if key == "name":
                checked["name"] = _check_value(value, str, f"{where} name", base_dir)
            elif key in RULES[rule].settings:
                checked[key] = _check_value(value, _RULE_SETTINGS[key], f"{where} {key}",
                                            base_dir)
            else:
                known = ", ".join(["name", *RULES[rule].settings])
                raise ConfigError(f"{where}: unknown setting {key!r} (known: {known})")
        out.append(checked)
    return out


def check_layer(raw: dict, base_dir: Path) -> dict:
    """Validate one layer and make its relative paths absolute against *base_dir*."""
    wanted = raw.get("wanted")
    out = _check_table({k: v for k, v in raw.items() if k != "wanted"}, _SCHEMA, "", base_dir)
    if wanted is not None:
        out["wanted"] = _check_wanted(wanted, base_dir)
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Loading and merging
# ══════════════════════════════════════════════════════════════════════════════

def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path}: file not found") from None
    except OSError as exc:
        raise ConfigError(f"{path}: {exc.strerror}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None


def _load_layer(path: Path) -> dict:
    try:
        return check_layer(_read_toml(path), path.parent)
    except ConfigError as exc:
        if str(exc).startswith(str(path)):
            raise
        raise ConfigError(f"{path}: {exc}") from None


def merge(base: dict, over: dict) -> dict:
    """Tables merge key by key; every other value (lists too) replaces."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def profile_path(profile: str, config_file: Path) -> Path:
    """A profile name lives in profiles/ next to the config file; a path is used as is."""
    if "/" in profile or profile.endswith(".toml"):
        return Path(profile).expanduser()
    return config_file.parent / PROFILES_DIR / f"{profile}.toml"


def load_config(config_file: str | Path | None = None, profile: str | None = None,
                overrides: dict | None = None, wanted: str | None = None,
                config_dir: Path | None = None) -> Config:
    """
    Build the effective Config.  *config_file* None means the default file, which
    may be absent.  *overrides* is a layer in config-file shape (from the CLI,
    paths relative to the current directory).  *wanted* is a --wanted list.
    """
    raw: dict = {}
    sources: list = []
    if config_file is None:
        path = (config_dir or default_config_dir()) / CONFIG_FILE
        if path.exists():
            raw, sources = _load_layer(path), [path]
    else:
        path = Path(config_file).expanduser()
        raw, sources = _load_layer(path), [path]
    if profile:
        ppath = profile_path(profile, path)
        if not ppath.exists():
            raise ConfigError(f"profile {profile!r} not found: {ppath}")
        raw = merge(raw, _load_layer(ppath))
        sources.append(ppath)
    if overrides:
        raw = merge(raw, check_layer(overrides, Path.cwd()))
    cfg = build_config(raw, wanted)
    cfg.sources = sources
    return cfg


# ══════════════════════════════════════════════════════════════════════════════
#  Building the Config from a merged, validated layer
# ══════════════════════════════════════════════════════════════════════════════

# The setting a --wanted RULE:VALUE token sets, per rule.
_CLI_SETTING = {"callsigns": "files", "message": "files", "states": "states",
                **{k: "confirm" for k in ("dxcc", "cqz", "country", "was", "5bwas")}}


def _cli_value(rule: str, value: str, tok: str) -> dict:
    """Settings from the VALUE in --wanted RULE:VALUE; several values join with +."""
    setting = _CLI_SETTING.get(rule)
    if setting is None:
        raise ConfigError(f"--wanted {tok!r}: rule {rule!r} takes no value")
    parts = [v.strip() for v in value.split("+") if v.strip()]
    if not parts:
        raise ConfigError(f"--wanted {tok!r}: nothing after the ':'")
    where = f"--wanted {tok!r}"
    if setting == "confirm":
        if parts == ["worked"]:
            return {"confirm": []}
        bad = [v for v in parts if v not in CONFIRM_SOURCES]
        if bad:
            raise ConfigError(f"{where}: confirm must be 'worked', or sources joined with +"
                              f" ({'+'.join(CONFIRM_SOURCES)}); not {bad[0]!r}")
    elif setting == "states" and any("/" in v or "." in v for v in parts):
        if len(parts) > 1:
            raise ConfigError(f"{where}: give either state codes or one file")
        return {"file": _check_value(parts[0], _PATH, where, Path.cwd())}
    return {setting: _check_value(parts, _RULE_SETTINGS[setting], where, Path.cwd())}


def _select_wanted(entries: list, selection: str) -> list:
    """
    Apply --wanted: comma-separated rule keys or [[wanted]] names; "none" for no
    rules.  RULE:VALUE sets the rule's main setting, e.g. callsigns:a.txt+b.txt,
    states:AK+DE, dxcc:worked.
    """
    tokens = [t.strip() for t in selection.split(",") if t.strip()]
    if tokens == ["none"]:
        return []
    out = []
    for tok in tokens:
        key, _, value = tok.partition(":")
        low = key.strip().lower()
        match = (next((e for e in entries if e.get("name", "").lower() == low), None)
                 or next((e for e in entries if e["rule"] == low), None))
        if match is None:
            if low not in RULES:
                raise ConfigError(f"--wanted: {key!r} is not a rule ({', '.join(RULES)})"
                                  " or the name of a [[wanted]] entry")
            match = {"rule": low}
        if value or tok.endswith(":"):
            match = {k: v for k, v in match.items() if k not in ("file", "states", "files")}
            match.update(_cli_value(match["rule"], value, tok))
        out.append(match)
    return out


def _wanted_spec(entry: dict) -> WantedSpec:
    info = RULES[entry["rule"]]
    name = entry.get("name") or info.key
    settings = {}
    for key, default in info.settings.items():
        if key in entry:
            settings[key] = entry[key]
        elif default is _REQUIRED:
            raise ConfigError(f"wanted rule {name!r} needs a {key!r} setting")
        else:
            settings[key] = list(default) if isinstance(default, list) else default
    for key in ("files", "file"):
        if key in settings:
            v = settings[key]
            settings[key] = [Path(p) for p in v] if isinstance(v, list) else (Path(v) if v else None)
    if "states" in settings:
        settings["states"] = [s.upper() for s in settings["states"]]
    if entry["rule"] == "states" and not (settings["states"] or settings["file"]):
        raise ConfigError(f"wanted rule {name!r} needs 'states' or 'file'")
    return WantedSpec(info.key, name, settings)


def _check_supported(specs: list) -> None:
    """Refuse settings the v2 evaluation code can't honour yet (v3 phase 5 lifts these)."""
    names: set = set()
    rules: set = set()
    for w in specs:
        if w.name.lower() in names:
            raise ConfigError(f"two [[wanted]] entries are named {w.name!r}")
        names.add(w.name.lower())
        if w.rule in rules:
            raise ConfigError(f"rule {w.rule!r} appears twice; more than one instance"
                              " of a rule is not supported yet")
        rules.add(w.rule)
    if "was" in rules and "5bwas" in rules:
        raise ConfigError("rules 'was' and '5bwas' can't be used together yet")
    confirms = {tuple(w.settings["confirm"]) for w in specs if w.rule in _ENTITY_RULES}
    if len(confirms) > 1:
        raise ConfigError("dxcc, cqz and country must use the same confirm setting for now")
    for w in specs:
        c = w.settings.get("confirm")
        if c is None:
            continue
        ok = ([], ["lotw"]) if w.rule in _ENTITY_RULES else (["lotw"],)
        if c not in ok:
            allowed = " or ".join(json.dumps(o) for o in ok)
            raise ConfigError(f"wanted rule {w.name!r}: confirm = {json.dumps(c)} is not"
                              f" supported yet; use {allowed}")


def build_config(raw: dict, wanted: str | None = None) -> Config:
    entries = raw.get("wanted", [{"rule": r} for r in DEFAULT_WANTED])
    if wanted is not None:
        entries = _select_wanted(entries, wanted)
    specs = [_wanted_spec(e) for e in entries]
    _check_supported(specs)

    udp, disp = raw.get("udp", {}), raw.get("display", {})
    inp, act  = raw.get("inputs", {}), raw.get("actions", {})
    script    = act.get("script", {})

    def path(v):
        return Path(v) if v else None

    for port in udp.get("proxy", []):
        if not 0 < port < 65536:
            raise ConfigError(f"udp.proxy: {port} is not a port number")
    if not 0 < udp.get("port", 2237) < 65536:
        raise ConfigError(f"udp.port: {udp['port']} is not a port number")

    return Config(
        udp=UdpConfig(**udp),
        display=DisplayConfig(**disp),
        scope=ScopeConfig(**raw.get("scope", {})),
        inputs=InputsConfig(cty=path(inp.get("cty")) or CTY_DAT_DEFAULT,
                            adif=[Path(p) for p in inp.get("adif", [])],
                            hamdat=path(inp.get("hamdat")) or HAMDAT_DB_DEFAULT),
        wanted=specs,
        filters=FiltersConfig(**raw.get("filters", {})),
        actions=ActionsConfig(ntfy_topic=act.get("ntfy", {}).get("topic"),
                              script_on_wanted=path(script.get("on_wanted")),
                              script_on_log_reload=path(script.get("on_log_reload")),
                              json_log=path(act.get("json_log", {}).get("file"))),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Output: --show-config, --list-rules, --init-config
# ══════════════════════════════════════════════════════════════════════════════

def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)   # a JSON string is a valid TOML string
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"can't write {type(v).__name__} as TOML")


def to_toml(d: dict, prefix: str = "") -> str:
    """Write a dict of tables, arrays of tables and plain values as TOML."""
    lines, tables, arrays = [], [], []
    for key, value in d.items():
        if isinstance(value, dict):
            if value:                     # an empty table is the same as none
                tables.append((key, value))
        elif isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
            arrays.append((key, value))
        elif value is not None:
            lines.append(f"{key} = {_toml_value(value)}")
    out = "\n".join(lines)
    for key, value in tables:
        name = f"{prefix}{key}"
        body = to_toml(value, f"{name}.")
        has_plain = any(not isinstance(v, dict) for v in value.values())
        section = f"[{name}]\n{body}" if has_plain else body
        out += ("\n\n" if out else "") + section.strip("\n")
    for key, items in arrays:
        for item in items:
            out += ("\n\n" if out else "") + f"[[{prefix}{key}]]\n" + to_toml(item)
    return out


def show_config(cfg: Config) -> str:
    head = ["# Effective jtcon configuration"]
    if cfg.sources:
        head += [f"# from: {p}" for p in cfg.sources]
    else:
        head.append("# from: built-in defaults (no config file)")
    return "\n".join(head) + "\n\n" + to_toml(cfg.as_dict()) + "\n"


def list_rules() -> str:
    lines = ["Wanted rules (use in [[wanted]] rule = \"...\" or --wanted a,b,c):", ""]
    for r in RULES.values():
        lines.append(f"  {r.key:<10} {r.kind:<6} {r.label}")
        for key, default in r.settings.items():
            shown = ("(required)" if default is _REQUIRED else "(optional)" if default == ""
                     else f"default {_toml_value(default)}")
            lines.append(f"  {'':<10} {'':<6}   {key}: {shown}")
    lines += ["", f"Default rules: {', '.join(DEFAULT_WANTED)}",
              f"confirm sources: {', '.join(CONFIRM_SOURCES)}"
              "  (an empty list means worked is enough)",
              "", _RULE_EXAMPLES]
    return "\n".join(lines)


_RULE_EXAMPLES = r"""Values on the command line: RULE:VALUE, several values joined with +

  jtcon --wanted dxcc,callsigns:~/ham/wanted.txt+~/ham/club.txt
  jtcon --wanted message:~/ham/msgs.txt,pota
  jtcon --wanted 5bwas,states:AK+DE         (or states:~/ham/states.txt)
  jtcon --wanted dxcc:worked,cqz:worked     (confirm: worked = any QSO counts)

Examples (config.toml):

  [[wanted]]
  rule  = "callsigns"
  files = ["~/ham/wanted.txt", "~/ham/club.txt"]   # one file is fine too: files = "x.txt"

  [[wanted]]
  rule   = "states"
  states = ["AK", "DE"]           # or a file:  file = "~/ham/states.txt"

  [[wanted]]
  rule    = "dxcc"
  confirm = []                    # worked is enough; default ["lotw"]

Watchlist files: one entry per line; lines starting with # and blank lines
are skipped.

  callsigns  regex, searched in the callsign, upper/lower case alike
               ^VK          any VK station
               ^K1ABC$      exactly K1ABC
               /P$          portable stations
  message    regex, searched in the whole decoded message
               \bPOTA\b     POTA as a word anywhere in the message
  states     two-letter state codes, e.g.  AK"""


CONFIG_TEMPLATE = """\
# jtcon configuration.  Every setting is optional; the values shown commented
# out are the defaults.  Check the result with:  jtcon --show-config
#
# Profiles: files in profiles/<name>.toml next to this one, picked with
# --profile NAME, are layered on top of this file.  Tables merge setting by
# setting; [[wanted]] entries in a profile replace this file's whole list.
# Relative paths are relative to the file they're written in.

[udp]
# host  = "0.0.0.0"
# port  = 2237           # WSJT-X Settings → Reporting → UDP Server port
# proxy = [2238]         # forward every packet to these localhost ports (GridTracker etc.)

[display]
# show      = "cq"       # "cq": CQs plus wanted hits; "all": every decode
# show_time = false      # prefix each line with the local time
# color     = true
# mouse     = false      # mouse wheel scrolls; hold Shift to select text
# debug     = false      # dump raw packets

[scope]
# decodes = "cq"         # check CQ decodes only, or "all" (WSJT-X "CQ only" must be off)

[inputs]
# The WSJT-X log is found automatically; list any other ADIF files here.
# adif   = ["~/ham/qrzlog.adif"]
# hamdat = "~/.hamdat/hamdat.db"   # FCC database for US states
# cty    = "~/.jtcon_cty.dat"      # refreshed automatically when older than 30 days

[filters]
# suppress_worked_within_days = 5   # 0 = worked today only; -1 = never suppress

# ── Wanted rules ───────────────────────────────────────────────────────────────
# Each [[wanted]] entry turns on one rule.  Without any, jtcon uses dxcc, cqz and
# country.  See them all with:  jtcon --list-rules
#
# confirm lists the confirmations that count; an empty list means worked is
# enough.  Default: ["lotw"].

[[wanted]]
rule = "dxcc"

[[wanted]]
rule = "cqz"

[[wanted]]
rule = "country"

# [[wanted]]
# rule = "was"            # or "5bwas" for 5-Band WAS on the current band

# [[wanted]]
# rule = "pota"

# [[wanted]]
# rule  = "callsigns"
# files = ["~/ham/wanted.txt"]   # one regex per line, # for comments

# [[wanted]]
# rule   = "states"
# states = ["AK", "DE"]

# ── Actions ────────────────────────────────────────────────────────────────────

# [actions.ntfy]
# topic = "my-topic"              # push wanted alerts to ntfy.sh/my-topic

# [actions.script]
# on_wanted     = "~/bin/alert.sh"   # gets the alert text as its argument
# on_log_reload = "~/bin/upload.sh"  # gets the ADIF file paths as arguments

# [actions.json_log]
# file = "~/jtcon-spots.jsonl"    # one JSON line per CQ
"""
