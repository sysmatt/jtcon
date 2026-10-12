"""External alerts: --alert-command script and ntfy.sh."""

import argparse
import subprocess
import sys
import threading
import urllib.request


# ══════════════════════════════════════════════════════════════════════════════
#  Alert helpers
# ══════════════════════════════════════════════════════════════════════════════

def _build_alert_msg(callsign, entity_info: dict,
                     needed_flags: list, match_flags: list) -> str:
    segments = [callsign or "?"]
    if entity_info:
        segments.append(f"{entity_info['entity']} CQ{entity_info['cq_zone']}")
    flag_parts = []
    if needed_flags:
        flag_parts.append("NEEDED: " + ", ".join(needed_flags))
    if match_flags:
        flag_parts.append("MATCH: " + ", ".join(match_flags))
    if flag_parts:
        segments.append("  ".join(flag_parts))
    return " | ".join(segments)


def _send_alerts(msg: str, args: argparse.Namespace, log=None) -> None:
    _log = log or (lambda m: print(m, file=sys.stderr))
    if args.alert_command:
        try:
            subprocess.Popen(
                [args.alert_command, msg],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            _log(f"[alert-command] {exc}")
    if args.alert_ntfy:
        # Network I/O runs in its own thread so a slow server can't stall decoding.
        def _post():
            try:
                req = urllib.request.Request(
                    f"https://ntfy.sh/{args.alert_ntfy}",
                    data=msg.encode("utf-8"),
                    method="POST",
                )
                req.add_header("Title", "jtcon")
                urllib.request.urlopen(req, timeout=10).close()
            except Exception as exc:
                _log(f"[alert-ntfy] {exc}")
        threading.Thread(target=_post, daemon=True).start()
