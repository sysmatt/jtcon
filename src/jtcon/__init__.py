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

Configuration
-------------
  Settings live in ~/.config/jtcon/config.toml; profiles in
  ~/.config/jtcon/profiles/<name>.toml are layered on top with --profile.
  Start with:  jtcon --init-config   and check with:  jtcon --show-config

Usage
-----
  jtcon
  jtcon --profile pota
  jtcon --wanted dxcc,was,pota --scope all
  jtcon --save cq.jsonl

Options
-------
  --version             Show the version number and exit
  --config FILE         Config file instead of ~/.config/jtcon/config.toml
  --profile NAME        Layer profiles/NAME.toml on top of the config
  --show-config         Print the effective configuration and exit
  --init-config         Write a commented starter config file and exit
  --list-rules          List the wanted rules and their settings, and exit
  --wanted RULES        Rules or [[wanted]] names for this run, e.g. dxcc,was,pota
  --scope cq|all        Check CQ decodes only, or all decodes
  --port PORT           UDP port                   (default: 2237)
  --save FILE           Append each CQ record as a JSON line to FILE
  --mouse               Mouse wheel scrolls the decode pane; Shift+drag selects text
  --no-color            Disable ANSI colorization of the decode output
  --debug               Dump raw hex + parsed fields to the decode pane
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
