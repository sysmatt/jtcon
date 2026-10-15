"""Command line: argument parsing and startup."""

import argparse
import os
import shlex
import socket
import subprocess
import sys
from datetime import date
from pathlib import Path

from jtcon import __version__
from jtcon.config import (CONFIG_FILE, CONFIG_TEMPLATE, RULES, ConfigError,
                          default_config_dir, list_rules, load_config, profile_from_config,
                          profile_path, show_config)


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
  jtcon --edit-config --profile parks edit profiles/parks.toml (made from the
                                      config file if it doesn't exist yet)

lists in the config file (TOML):
  adif   = ["~/ham/old.adi", "/data/contest.adi"]
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
    cfg.add_argument("--edit-config", action="store_true",
                     help="Open the config file in $VISUAL or $EDITOR, then check it; with "
                          "--profile, the profile (a new one starts as a copy of the config)")
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
    run.add_argument("--ignore", metavar="CALL", action="append",
                     help="Never show or alert on CALL (and its portable forms) unless it "
                          "calls you; repeatable, adds to [filters] ignore")
    run.add_argument("--port", type=int,
                     help="UDP port matching WSJT-X Settings → Reporting (default 2237)")
    run.add_argument("--save", metavar="FILE",
                     help="Append each CQ record as a JSON line to FILE")
    run.add_argument("--ui", choices=("classic", "textual", "gui"),
                     help="Front end: the classic screen, the new Textual one, or a desktop "
                          "window (gui, which needs the jtcon[gui] install)")
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
    put("display", "ui", args.ui)
    if args.save:
        layer["actions"] = {"json_log": {"file": args.save}}
    return layer


def _write_private(path: Path, text: str) -> None:
    """Create *path* readable by its owner only: it may come to hold an API key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def _config_file(args: argparse.Namespace) -> Path:
    return Path(args.config).expanduser() if args.config else default_config_dir() / CONFIG_FILE


def _init_config(path: Path) -> int:
    if path.exists():
        print(f"jtcon: {path} already exists; not overwriting it", file=sys.stderr)
        return 1
    _write_private(path, CONFIG_TEMPLATE)
    print(f"Wrote {path}")
    return 0


def _editor() -> list:
    """The user's editor command, from $VISUAL or $EDITOR ("code -w" works)."""
    for var in ("VISUAL", "EDITOR"):
        value = os.environ.get(var, "").strip()
        if value:
            return shlex.split(value)
    return []


def _edit_config(args: argparse.Namespace) -> int:
    """Open the config file (or, with --profile, the profile) in the user's
    editor, making it first if needed; then check the result."""
    editor = _editor()
    if not editor:
        print("jtcon: set VISUAL or EDITOR to your editor first, e.g.  export EDITOR=nano",
              file=sys.stderr)
        return 1
    main_file = _config_file(args)
    if not main_file.exists():
        _write_private(main_file, CONFIG_TEMPLATE)
        print(f"Wrote a starter config: {main_file}")
    target = main_file
    if args.profile:
        target = profile_path(args.profile, main_file)
        if not target.exists():
            _write_private(target, profile_from_config(
                main_file.read_text(encoding="utf-8"), args.profile, main_file.name,
                date.today().isoformat()))
            print(f"Made profile {target} from {main_file.name}")
    try:
        status = subprocess.call([*editor, str(target)])
    except OSError as exc:
        print(f"jtcon: can't run the editor {editor[0]!r}: {exc}", file=sys.stderr)
        return 1
    if status != 0:
        print(f"jtcon: the editor exited with status {status}", file=sys.stderr)
    try:
        load_config(args.config, args.profile)
    except ConfigError as exc:
        print(f"jtcon: config error: {exc}", file=sys.stderr)
        print(f"Fix it with:  jtcon --edit-config{_edit_args(args)}", file=sys.stderr)
        return 2
    print(f"config OK: {target}")
    return 0


def _edit_args(args: argparse.Namespace) -> str:
    """The --config/--profile options to repeat in a hint, quoted for the shell."""
    out = ""
    if args.config:
        out += f" --config {shlex.quote(args.config)}"
    if args.profile:
        out += f" --profile {shlex.quote(args.profile)}"
    return out


def main(argv: list | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.list_rules:
        print(list_rules())
        sys.exit(0)
    if args.init_config:
        sys.exit(_init_config(_config_file(args)))
    if args.edit_config:
        sys.exit(_edit_config(args))

    try:
        cfg = load_config(args.config, args.profile, cli_overrides(args), args.wanted,
                          ignore=args.ignore)
    except ConfigError as exc:
        print(f"jtcon: config error: {exc}", file=sys.stderr)
        sys.exit(2)
    if args.show_config:
        print(show_config(cfg), end="")
        sys.exit(0)

    if cfg.display.ui == "gui":
        from jtcon.gui import missing
        problem = missing()
        if problem:
            print(problem, file=sys.stderr)
            sys.exit(1)

    from jtcon.core.engine import Engine

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
    # (each front end's toolkit is imported only when it runs)
    if cfg.display.ui == "gui":
        from jtcon.gui.app import JtconGui
        tui = JtconGui(Engine(cfg, sock, tx_sock))
    elif cfg.display.ui == "textual":
        from jtcon.tui.textual_app import JtconTextual
        tui = JtconTextual(Engine(cfg, sock, tx_sock))
    else:
        from jtcon.tui.app import JtconTUI
        tui = JtconTUI(Engine(cfg, sock, tx_sock))
    try:
        status = tui.run()
    finally:
        sock.close()
        tx_sock.close()
    sys.exit(status)
