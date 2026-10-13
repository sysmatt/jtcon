"""Command line: argument parsing and startup."""

import argparse
import socket
import sys
from pathlib import Path

from jtcon import __version__
from jtcon.config import (CONFIG_FILE, CONFIG_TEMPLATE, RULES, ConfigError,
                          default_config_dir, list_rules, load_config, show_config)


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

_EPILOG = """\
examples:
  jtcon --wanted dxcc,was,pota        only these rules for this run
  jtcon --wanted WAS-ARRL,callsigns   a [[wanted]] name works as well as a rule
  jtcon --wanted none                 no rules: just show the decodes
  jtcon --wanted pota,callsigns:~/ham/wanted.txt+~/ham/club.txt
                                      RULE:VALUE gives a rule its files or list,
  jtcon --wanted was,states:AK+DE     several values joined with +
  jtcon --profile parks --scope all   profiles/parks.toml, checking every decode
  jtcon --show-config --wanted was    see what a run would use, without running

lists in the config file (TOML):
  adif   = ["~/ham/qrzlog.adif", "/data/old.adi"]
  states = ["AK", "DE"]
  proxy  = [2238, 2239]

jtcon --list-rules shows each rule's settings and the watchlist file format.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jtcon",
        description="Monitor WSJT-X decodes via UDP with a prompt_toolkit TUI.  Settings come "
                    f"from {default_config_dir() / CONFIG_FILE} (see --init-config); "
                    "the options below override it for one run.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )

    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    cfg = parser.add_argument_group("configuration")
    cfg.add_argument("--config", metavar="FILE",
                     help=f"Config file to use instead of {CONFIG_FILE} in the config directory")
    cfg.add_argument("--profile", metavar="NAME",
                     help="Layer profiles/NAME.toml (next to the config file) on top of the "
                          "config; a path to a .toml file works too")
    cfg.add_argument("--show-config", action="store_true",
                     help="Print the effective configuration and exit")
    cfg.add_argument("--init-config", action="store_true",
                     help="Write a commented starter config file and exit")
    cfg.add_argument("--list-rules", action="store_true",
                     help="List the wanted rules and their settings, and exit")

    run = parser.add_argument_group("overrides for this run")
    run.add_argument("--wanted", metavar="RULES",
                     help="Comma-separated rules or [[wanted]] names to use instead of the "
                          "configured list, e.g. dxcc,was,pota; RULE:VALUE gives a rule its "
                          "files or list (callsigns:a.txt+b.txt); 'none' for no rules "
                          f"(rules: {', '.join(RULES)})")
    run.add_argument("--scope", choices=("cq", "all"),
                     help="Check CQ decodes only, or all decodes")
    run.add_argument("--port", type=int,
                     help="UDP port matching WSJT-X Settings → Reporting (default 2237)")
    run.add_argument("--save", metavar="FILE",
                     help="Append each CQ record as a JSON line to FILE")
    run.add_argument("--mouse", action="store_const", const=True,
                     help="Mouse wheel scrolls the decode pane; hold Shift to select text")
    run.add_argument("--no-color", dest="color", action="store_const", const=False,
                     help="Disable ANSI colours in the decode output")
    run.add_argument("--debug", action="store_const", const=True,
                     help="Dump raw packet hex and parsed fields to the decode pane")
    return parser


def cli_overrides(args: argparse.Namespace) -> dict:
    """The command-line options as a config layer (only the ones given)."""
    layer: dict = {}

    def put(table, key, value):
        if value is not None:
            layer.setdefault(table, {})[key] = value

    put("udp", "port", args.port)
    put("scope", "decodes", args.scope)
    put("display", "mouse", args.mouse)
    put("display", "color", args.color)
    put("display", "debug", args.debug)
    if args.save:
        layer["actions"] = {"json_log": {"file": args.save}}
    return layer


def _init_config(path: Path) -> int:
    if path.exists():
        print(f"jtcon: {path} already exists; not overwriting it", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    print(f"Wrote {path}")
    return 0


def main(argv: list | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.list_rules:
        print(list_rules())
        sys.exit(0)
    if args.init_config:
        path = Path(args.config).expanduser() if args.config else default_config_dir() / CONFIG_FILE
        sys.exit(_init_config(path))

    try:
        cfg = load_config(args.config, args.profile, cli_overrides(args), args.wanted)
    except ConfigError as exc:
        print(f"jtcon: config error: {exc}", file=sys.stderr)
        sys.exit(2)
    if args.show_config:
        print(show_config(cfg), end="")
        sys.exit(0)

    from jtcon.core.engine import Engine
    from jtcon.tui.app import JtconTUI   # prompt_toolkit only when the TUI runs

    # ── UDP sockets (must bind before TUI starts so the port is held) ─────────
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
    sock.bind((cfg.udp.host, cfg.udp.port))
    tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    # ── Launch TUI — all remaining startup happens inside, logged to the pane ─
    tui = JtconTUI(Engine(cfg, sock, tx_sock))
    try:
        status = tui.run()
    finally:
        sock.close()
        tx_sock.close()
    sys.exit(status)
