"""
jtcon
=====
Listen for WSJT-X UDP broadcasts, display CQ calls in a scrollable TUI pane
enriched with entity / CQ-zone / country / grid info from cty.dat, optionally
save as newline-delimited JSON, and optionally load an ADIF logbook so that
contacts whose entity, CQ zone, or country have NOT been worked before are
flagged as NEEDED.

TUI layout
----------
  ┌─────────────────────────────────────┐
  │  scrollable decode output           │  ← top pane, ↑↓ PgUp PgDn to scroll
  ├─────────────────────────────────────┤
  │  CMD>                               │  ← single-line command input
  ├─────────────────────────────────────┤
  │  RX [████████░░] even  dec=…  cq=…  │  ← status line (cycle bar + stats)
  └─────────────────────────────────────┘

Key bindings
------------
  ↑ / ↓             Scroll the output pane one line (focus stays at CMD>)
  PgUp / PgDn       Scroll the output pane one page
  Esc / Ctrl-End    Return to the newest output
  Tab               Toggle focus between output pane and command input
  Ctrl-C / Ctrl-Q   Quit

Commands (type in CMD> line, press Enter)
-----------------------------------------
  quit / exit / q    Exit the program
  clear              Clear the decode output
  list (or l/li/lis) Show all NEEDED/MATCH entries from the rolling buffer,
                     sorted by last heard (most recent at the bottom)
  00-99              Reply to the contact at that rolling-buffer index
  <CALLSIGN>         Reply to that callsign's rolling-buffer entry

Rolling buffer
--------------
  One slot per callsign.  A station keeps its index while it is heard; each
  repeat decode refreshes the entry so replies use the newest decode.  When
  all 100 slots are full the least recently heard station is evicted.  The
  buffer is cleared when the dial frequency changes.

Dependencies
------------
  prompt_toolkit  (python3-prompt-toolkit)
  Everything else is Python standard library.

First run
---------
  cty.dat (AD1C Big CTY prefix database) is downloaded automatically from
  country-files.com and cached at ~/.jtcon_cty.dat; it is refreshed when the
  cached copy is more than 30 days old.

Install
-------
  Option 1, pipx (see README for details):
    pipx install git+https://github.com/sysmatt/jtcon                  # your user
    sudo pipx install --global git+https://github.com/sysmatt/jtcon    # all users
  Option 2, run from a clone:
    git clone https://github.com/sysmatt/jtcon.git && cd jtcon && ./jtcon

Usage
-----
  jtcon
  jtcon --adif ~/Documents/wsjtx_log.adi
  jtcon --adif ~/wsjtx_log.adi --save cq.jsonl
  jtcon --raw --show-time
  jtcon --match-calls watchlist.txt
  jtcon --pota --sota

Options
-------
  --version             Show the version number and exit
  --host HOST           UDP bind address           (default: 0.0.0.0)
  --port PORT           UDP port                   (default: 2237)
  --show-time           Prepend local wall-clock HH:MM:SS to every line
  --raw                 Show ALL decoded messages, not just CQ
  --save FILE           Append each CQ record as a JSON line to FILE
  --cty PATH            Path to cty.dat            (default: ~/.jtcon_cty.dat)
  --adif FILE [...]     Additional ADIF log file(s) for NEEDED detection
                        (auto-discovered WSJT-X logs are always loaded)
  --no-need-entity      Disable DXCC-entity NEEDED check
  --no-need-cqz         Disable CQ-zone NEEDED check
  --no-need-country     Disable country-name NEEDED check
  --qsl-only            Require LoTW confirmation for NEEDED checks; ❓ icon
                        marks worked-but-unconfirmed entities
  --worked-lag-days N   Suppress NEEDED/MATCH for a callsign worked within N days
                        (default 0 = suppress today; -1 = disable suppression)
  --match-calls FILE    File(s) of callsign regex patterns
  --match-message FILE  File(s) of decoded-message regex patterns
  --alert-command SCRIPT  Script to exec on NEEDED/MATCH events
  --alert-ntfy TOPIC    POST NEEDED/MATCH alerts to ntfy.sh topic
  --adif-change-script SCRIPT  Script to exec when ADIF log file(s) are reloaded
  --proxy PORT[,...]    Forward received packets to additional UDP ports on localhost
  --pota                Flag CQ POTA calls as MATCH
  --sota                Flag CQ SOTA calls as MATCH
  --iota                Flag CQ IOTA calls as MATCH
  --match-state STATES  Comma-separated US state abbreviations or file
  --hamdat DB           Path to hamdat SQLite database
  --was                 Flag US states not yet LoTW-confirmed as NEEDED (WAS award)
  --5bwas               Flag US states not yet LoTW-confirmed on the current band
                        as NEEDED (5-Band WAS award); mutually exclusive with --was
  --all                 Apply NEEDED/MATCH logic to ALL decoded messages, not just CQ;
                        non-CQ hits are shown in magenta and added to the reply buffer.
                        Combine with --raw to also show non-CQ messages with no hits.
                        Requires WSJT-X Settings → Reporting → "CQ only" to be UNCHECKED
                        so that WSJT-X retains non-CQ decodes for UDP reply.
  --debug               Dump raw hex + parsed fields to the decode pane
  --no-color            Disable ANSI colorization of the decode output
  --mouse               Mouse wheel scrolls the decode pane; Shift+drag selects text
"""

import importlib.metadata
import re
import subprocess
from pathlib import Path


def _get_version() -> str:
    """
    Version string matching what setuptools-scm assigns at build time.

    From a git checkout (./jtcon or an editable install) ask git directly so the
    version tracks every commit; otherwise use the installed package metadata.
    """
    pkg  = Path(__file__).resolve().parent          # <checkout>/src/jtcon
    here = pkg.parent.parent
    if pkg.parent.name == "src" and (here / ".git").exists():
        try:
            out = subprocess.run(
                ["git", "describe", "--tags", "--long", "--match", "v[0-9]*"],
                cwd=here, capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            m = re.match(r"^v(.+)-(\d+)-g[0-9a-f]+$", out)
            if m:
                tag, distance = m.group(1), int(m.group(2))
                return tag if distance == 0 else f"{tag}.post{distance}"
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        return importlib.metadata.version("jtcon")
    except importlib.metadata.PackageNotFoundError:
        return "0+unknown"


__version__ = _get_version()
