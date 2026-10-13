"""ADIF log files: discovery and parsing."""

import re
from pathlib import Path


# WSJT-X and its forks keep wsjtx_log.adi in Qt's per-application data directory,
# named after the program: ~/.local/share/<app> on Linux, AppData/Local/<app> on
# Windows, ~/Library/Application Support/<app> on macOS.
_WSJT_APPS = (
    "WSJT-X",        # WSJT-X 2.x
    "WSJT-X Exp",    # experimental / nightly builds
    "WS",            # WS suite (WSJT-X Improved's successor, DG2YCB)
)
_APP_DATA_DIRS = (
    Path.home() / ".local" / "share",
    Path.home() / "AppData" / "Local",
    Path.home() / "Library" / "Application Support",
)

# Candidate locations, all loaded if they exist (besides any configured adif files).
WSJT_LOG_SEARCH_PATHS: list[Path] = [
    *(base / app / "wsjtx_log.adi" for app in _WSJT_APPS for base in _APP_DATA_DIRS),
    # v2 looked in AppData/Roaming on Windows; kept in case a log was put there
    Path.home() / "AppData" / "Roaming" / "WSJT-X" / "wsjtx_log.adi",
    # Older Linux installs / portable installs
    Path.home() / "WSJT-X" / "wsjtx_log.adi",
]


def _find_wsjt_logs() -> list[Path]:
    """Return all WSJT_LOG_SEARCH_PATHS entries that currently exist on disk."""
    found = []
    for p in WSJT_LOG_SEARCH_PATHS:
        try:
            if p.exists():
                found.append(p)
        except OSError:
            pass
    return found


# ══════════════════════════════════════════════════════════════════════════════
#  ADIF parser
# ══════════════════════════════════════════════════════════════════════════════

_ADIF_FIELD_RE = re.compile(
    r"<([A-Za-z_][A-Za-z0-9_]*)(?::[^:>]*)?:(\d+)(?::[A-Za-z])?>",
    re.IGNORECASE,
)
_ADIF_EOH_RE = re.compile(r"<EOH>", re.IGNORECASE)
_ADIF_EOR_RE = re.compile(r"<EOR>", re.IGNORECASE)


def parse_adif(path: Path) -> list:
    text = path.read_text(encoding="utf-8", errors="replace")
    eoh  = _ADIF_EOH_RE.search(text)
    body = text[eoh.end():] if eoh else text
    qsos: list = []
    pos = 0
    current: dict = {}
    while pos < len(body):
        m   = _ADIF_FIELD_RE.search(body, pos)
        eor = _ADIF_EOR_RE.search(body, pos)
        if eor and (not m or eor.start() < m.start()):
            if current:
                qsos.append(current)
                current = {}
            pos = eor.end()
            continue
        if not m:
            break
        field  = m.group(1).lower()
        flen   = int(m.group(2))
        vstart = m.end()
        current[field] = body[vstart : vstart + flen].strip()
        pos = vstart + flen
    if current:
        qsos.append(current)
    return qsos
