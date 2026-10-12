"""cty.dat (AD1C Big CTY): callsign prefix → DXCC entity, zones, continent."""

import re
import urllib.request
from pathlib import Path

from jtcon import __version__


CTY_DAT_URL     = "https://www.country-files.com/cty/cty.dat"
CTY_DAT_DEFAULT = Path.home() / ".jtcon_cty.dat"
CTY_MAX_AGE_DAYS = 30   # re-download cty.dat when the cached copy is older


# ══════════════════════════════════════════════════════════════════════════════
#  cty.dat parser
# ══════════════════════════════════════════════════════════════════════════════

_OVERRIDE_RE = re.compile(r"[\(\[\{<~][^\)\]\}>~]*[\)\]\}>~]")


def _strip_overrides(token: str) -> tuple:
    overrides = {}
    m = re.search(r"\((\d+)\)", token)
    if m:
        overrides["cqz"] = int(m.group(1))
    m = re.search(r"\[(\d+)\]", token)
    if m:
        overrides["ituz"] = int(m.group(1))
    m = re.search(r"\{([A-Z]+)\}", token)
    if m:
        overrides["continent"] = m.group(1)
    clean = _OVERRIDE_RE.sub("", token).strip()
    return clean, overrides


_PORTABLE_SUFFIXES = frozenset({"P", "M", "MM", "AM", "QRP", "QRPP", "A", "B"})


class CtyDat:
    def __init__(self):
        self._exact:    dict = {}
        self._prefixes: list = []

    def load(self, path: Path) -> None:
        self._parse(path.read_text(encoding="utf-8", errors="replace"))

    def _parse(self, text: str) -> None:
        for record in re.split(r"\n(?=[A-Z*])", text):
            lines = record.strip().splitlines()
            if not lines:
                continue
            parts = [p.strip() for p in lines[0].split(":")]
            if len(parts) < 8:
                continue
            try:
                base = {
                    "country":        parts[0].lstrip("*"),
                    "cqz":            int(parts[1]),
                    "ituz":           int(parts[2]),
                    "continent":      parts[3],
                    "latitude":       float(parts[4]),
                    "longitude":      -float(parts[5]),
                    "primary_prefix": parts[7].rstrip(";").strip(),
                }
            except (ValueError, IndexError):
                continue
            body = " ".join(lines[1:]).split(";")[0]
            for raw in body.split(","):
                raw = raw.strip()
                if not raw:
                    continue
                exact = raw.startswith("=")
                clean, overrides = _strip_overrides(raw.lstrip("="))
                if not clean:
                    continue
                entry = {**base, **overrides}
                if exact:
                    self._exact[clean.upper()] = entry
                else:
                    self._prefixes.append((clean.upper(), entry))
        self._prefixes.sort(key=lambda x: len(x[0]), reverse=True)

    def lookup(self, callsign: str) -> dict | None:
        call = callsign.upper().strip()
        if call in self._exact:
            return dict(self._exact[call])
        # Drop operating-condition suffixes (/P, /QRP) and call-area digits (/7).
        parts = [p for p in call.split("/")
                 if p and p not in _PORTABLE_SUFFIXES and not p.isdigit()]
        if not parts:
            return None
        if len(parts) == 1:
            return self._prefix_match(parts[0])
        # Compound call such as KH6/W1ABC or W1ABC/VP2E: the shorter part is
        # the location prefix and decides the entity.
        a, b = parts[0], parts[-1]
        prefix, home = (a, b) if len(a) <= len(b) else (b, a)
        return self._prefix_match(prefix) or self._prefix_match(home)

    def _prefix_match(self, call: str) -> dict | None:
        for prefix, entry in self._prefixes:
            if call.startswith(prefix):
                return dict(entry)
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  cty.dat download
# ══════════════════════════════════════════════════════════════════════════════

def download_cty(path: Path) -> None:
    req = urllib.request.Request(
        CTY_DAT_URL, headers={"User-Agent": f"jtcon/{__version__}"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
