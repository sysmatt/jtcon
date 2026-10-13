"""Regex patterns: watchlist files and activation keywords."""

import re
import sys
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════════════
#  Pattern matching helpers
# ══════════════════════════════════════════════════════════════════════════════

def load_regex_patterns(file_paths: list, log=None) -> list:
    """
    Return a list of (compiled_pattern, source_filename) tuples.
    Raises OSError if a file can't be read; invalid regexes are logged and skipped.
    """
    _log = log or (lambda m: print(m, file=sys.stderr))
    patterns = []
    for path_str in file_paths:
        p = Path(path_str)
        for i, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                patterns.append((re.compile(line, re.IGNORECASE), p.name))
            except re.error as exc:
                _log(f"[match] {p.name}:{i}: invalid regex {line!r}: {exc}")
    return patterns


ACTIVATION_KEYWORDS = {"pota": "POTA", "sota": "SOTA", "iota": "IOTA"}



def _regex_matches(value: str, patterns: list) -> list:
    """Return label strings for each (pattern, source) tuple that matches value."""
    results = []
    for pat, src in patterns:
        if pat.search(value):
            results.append(f"{pat.pattern} [{src}]" if src else pat.pattern)
    return results
