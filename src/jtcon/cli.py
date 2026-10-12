"""Command line: argument parsing and startup."""

import argparse
import socket
import sys

from jtcon import __version__
from jtcon.core.cty import CTY_DAT_DEFAULT
from jtcon.core.hamdat import HAMDAT_DB_DEFAULT
from jtcon.tui.app import JtconTUI


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jtcon",
        description="Monitor WSJT-X CQ calls via UDP with a prompt_toolkit TUI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--host", default="0.0.0.0",
                        help="UDP bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=2237,
                        help="UDP port matching WSJT-X Settings → Reporting (default: 2237)")
    parser.add_argument("--show-time", action="store_true",
                        help="Prepend local wall-clock HH:MM:SS to each line")
    parser.add_argument("--raw", action="store_true",
                        help="Show ALL decoded messages, not just CQ")
    parser.add_argument("--all", action="store_true", dest="all_decodes",
                        help="Apply NEEDED/MATCH logic to all decoded messages, not just CQ; "
                             "non-CQ hits are shown in magenta and added to the reply buffer. "
                             "Combine with --raw to also show non-CQ messages with no hits. "
                             "Requires WSJT-X Settings → Reporting → 'CQ only' to be UNCHECKED "
                             "so that WSJT-X retains non-CQ decodes for UDP reply.")
    parser.add_argument("--save", metavar="FILE",
                        help="Append each CQ record as a JSON line to FILE")
    parser.add_argument("--cty", metavar="PATH", default=str(CTY_DAT_DEFAULT),
                        help=f"Path to cty.dat (default: {CTY_DAT_DEFAULT})")
    parser.add_argument("--adif", metavar="FILE", nargs="+",
                        help="Additional ADIF log file(s) for NEEDED detection "
                             "(auto-discovered WSJT-X logs are always loaded if found)")
    parser.add_argument("--no-need-entity",  dest="need_entity",  action="store_false", default=True,
                        help="Disable DXCC-entity NEEDED check")
    parser.add_argument("--no-need-cqz",     dest="need_cqz",     action="store_false", default=True,
                        help="Disable CQ-zone NEEDED check")
    parser.add_argument("--no-need-country", dest="need_country", action="store_false", default=True,
                        help="Disable country-name NEEDED check")
    parser.add_argument("--qsl-only", action="store_true", dest="qsl_only",
                        help="Require LoTW confirmation (lotw_qsl_rcvd=Y) for NEEDED checks; "
                             "unconfirmed QSOs shown with ❓ instead of ✅")
    parser.add_argument("--worked-lag-days", metavar="DAYS", type=int, default=0,
                        dest="worked_lag_days",
                        help="Suppress NEEDED/MATCH for a callsign worked within N days "
                             "(default 0 = suppress today; -1 = disable suppression entirely)")
    parser.add_argument("--match-calls", metavar="FILE", nargs="+",
                        help="File(s) of callsign regex patterns to flag as MATCH (one per line, # = comment)")
    parser.add_argument("--match-message", metavar="FILE", nargs="+",
                        help="File(s) of decoded-message regex patterns to flag as MATCH")
    parser.add_argument("--pota",  action="store_true",
                        help="Flag CQ POTA activations as MATCH")
    parser.add_argument("--sota",  action="store_true",
                        help="Flag CQ SOTA activations as MATCH")
    parser.add_argument("--iota",  action="store_true",
                        help="Flag CQ IOTA activations as MATCH")
    parser.add_argument("--match-state", metavar="STATES",
                        help="Comma-separated US state abbreviations (e.g. TX,CA) or a file of states to flag as MATCH")
    parser.add_argument("--hamdat", metavar="DB", default=str(HAMDAT_DB_DEFAULT),
                        help=f"Path to hamdat SQLite database (default: {HAMDAT_DB_DEFAULT})")
    parser.add_argument("--alert-command", metavar="SCRIPT",
                        help="Script to exec on NEEDED/MATCH events; alert message is passed as arg 1")
    parser.add_argument("--alert-ntfy", metavar="TOPIC",
                        help="POST NEEDED/MATCH alerts to ntfy.sh topic TOPIC")
    parser.add_argument("--adif-change-script", metavar="SCRIPT",
                        help="Script to exec each time the ADIF log file(s) are reloaded; "
                             "reloaded file path(s) are passed as arguments")
    parser.add_argument("--proxy", metavar="PORT[,PORT...]",
                        help="Forward every received WSJT-X packet to these additional UDP ports "
                             "on localhost (e.g. --proxy 2238 or --proxy 2238,2239)")
    was_grp = parser.add_mutually_exclusive_group()
    was_grp.add_argument("--was", dest="need_was", action="store_true", default=False,
                         help="Flag US states not yet LoTW-confirmed as NEEDED (ARRL WAS award); "
                              "requires --hamdat; mutually exclusive with --5bwas")
    was_grp.add_argument("--5bwas", dest="need_5bwas", action="store_true", default=False,
                         help="Flag US states not yet LoTW-confirmed on the current band as NEEDED "
                              "(ARRL 5-Band WAS award); requires --hamdat; mutually exclusive with --was")

    # Accepted for backward compatibility; cycle stats are always in the status bar.
    parser.add_argument("--stats", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true",
                        help="Dump raw packet hex and parsed fields to the decode pane")
    parser.add_argument("--no-color", dest="color", action="store_false", default=True,
                        help="Disable ANSI colorization of the decode output")
    parser.add_argument("--mouse", action="store_true",
                        help="Enable mouse support: the wheel scrolls the decode pane and "
                             "clicking focuses a pane (hold Shift to select text)")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    # ── UDP sockets (must bind before TUI starts so the port is held) ─────────
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
    sock.bind((args.host, args.port))
    tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    # ── Launch TUI — all remaining startup happens inside, logged to the pane ─
    tui = JtconTUI(args, sock, tx_sock)
    try:
        status = tui.run()
    finally:
        sock.close()
        tx_sock.close()
    sys.exit(status)
