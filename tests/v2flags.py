"""Translate a v2 command line into a v3 config layer.

The render snapshot and the older behaviour tests are written in v2 flags; this
maps them onto the v3 config so the snapshot proves each translation behaves
exactly as v2 did.  It doubles as the reference for the v2 → v3 migration notes.
"""

import argparse


def _v2_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jtcon-v2")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--show-time", action="store_true")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--all", action="store_true", dest="all_decodes")
    p.add_argument("--save")
    p.add_argument("--cty")
    p.add_argument("--adif", nargs="+")
    p.add_argument("--no-need-entity", dest="need_entity", action="store_false")
    p.add_argument("--no-need-cqz", dest="need_cqz", action="store_false")
    p.add_argument("--no-need-country", dest="need_country", action="store_false")
    p.add_argument("--qsl-only", action="store_true")
    p.add_argument("--worked-lag-days", type=int, default=0)
    p.add_argument("--match-calls", nargs="+")
    p.add_argument("--match-message", nargs="+")
    p.add_argument("--pota", action="store_true")
    p.add_argument("--sota", action="store_true")
    p.add_argument("--iota", action="store_true")
    p.add_argument("--match-state")
    p.add_argument("--hamdat")
    p.add_argument("--alert-command")
    p.add_argument("--alert-ntfy")
    p.add_argument("--adif-change-script")
    p.add_argument("--proxy")
    p.add_argument("--was", action="store_true")
    p.add_argument("--5bwas", dest="fivebwas", action="store_true")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--no-color", dest="color", action="store_false")
    p.add_argument("--mouse", action="store_true")
    return p


def v2_to_config(*argv: str) -> dict:
    a = _v2_parser().parse_args(list(argv))
    cfg: dict = {}

    def put(table, key, value):
        cfg.setdefault(table, {})[key] = value

    if a.host:
        put("udp", "host", a.host)
    if a.port:
        put("udp", "port", a.port)
    if a.proxy:
        put("udp", "proxy", [int(p) for p in a.proxy.split(",")])
    if a.raw:
        put("display", "show", "all")
    for key in ("show_time", "mouse", "debug"):
        if getattr(a, key):
            put("display", key, True)
    if not a.color:
        put("display", "color", False)
    if a.all_decodes:
        put("scope", "decodes", "all")
    if a.cty:
        put("inputs", "cty", a.cty)
    if a.adif:
        put("inputs", "adif", a.adif)
    if a.hamdat:
        put("inputs", "hamdat", a.hamdat)
    # v2 defaulted to 0 (today); v3 defaults to 5 days.
    put("filters", "suppress_worked_within_days", a.worked_lag_days)

    actions: dict = {}
    if a.alert_ntfy:
        actions["ntfy"] = {"topic": a.alert_ntfy}
    script = {}
    if a.alert_command:
        script["on_wanted"] = a.alert_command
    if a.adif_change_script:
        script["on_log_reload"] = a.adif_change_script
    if script:
        actions["script"] = script
    if a.save:
        actions["json_log"] = {"file": a.save}
    if actions:
        cfg["actions"] = actions

    # v2 counted any worked QSO unless --qsl-only; v3 defaults to LoTW.
    confirm = ["lotw"] if a.qsl_only else []
    wanted = [{"rule": rule, "confirm": confirm}
              for rule, on in (("dxcc", a.need_entity), ("cqz", a.need_cqz),
                               ("country", a.need_country)) if on]
    if a.was:
        wanted.append({"rule": "was"})
    if a.fivebwas:
        wanted.append({"rule": "5bwas"})
    for rule in ("pota", "sota", "iota"):
        if getattr(a, rule):
            wanted.append({"rule": rule})
    if a.match_calls:
        wanted.append({"rule": "callsigns", "files": a.match_calls})
    if a.match_message:
        wanted.append({"rule": "message", "files": a.match_message})
    if a.match_state:
        states = [s.strip().upper() for s in a.match_state.split(",") if s.strip()]
        if "/" in a.match_state or "." in a.match_state:   # v2 also took a file
            wanted.append({"rule": "states", "file": a.match_state})
        else:
            wanted.append({"rule": "states", "states": states})
    cfg["wanted"] = wanted
    return cfg
