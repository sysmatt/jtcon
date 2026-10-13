"""Decoded-message text: callsign patterns, CQ parsing, directed-message sender."""

import re


# Matches any string that looks like an amateur callsign: ≥3 chars, at least one digit,
# alphanumeric plus optional slash (for portable/country prefixes).
_CALLSIGN_RE = re.compile(r'^(?=[A-Z0-9/]*\d)[A-Z0-9/]{3,}$', re.IGNORECASE)


# Stricter than _CALLSIGN_RE: prefix + digit + suffix ending in a letter, with
# optional /PREFIX and /SUFFIX.  Rejects RR73, grids, reports and free text.
_STRICT_CALL_RE = re.compile(
    r'^(?:[A-Z0-9]{1,4}/)?[A-Z0-9]{1,3}\d[A-Z0-9]{0,3}[A-Z](?:/[A-Z0-9]{1,4})?$'
)


# ══════════════════════════════════════════════════════════════════════════════
#  CQ message parser
# ══════════════════════════════════════════════════════════════════════════════

_GRID_RE = re.compile(r"\b([A-R]{2}\d{2}(?:[A-X]{2})?)\b", re.IGNORECASE)

_CQ_RE = re.compile(
    r"^CQ\s+"
    r"(?:(?P<modifier>[A-Z]{1,13})\s+)?"
    r"(?P<lt><)?(?P<callsign>(?=[A-Z0-9/]*\d)[A-Z0-9/]{3,})(?(lt)>)"
    r"(?:\s+(?P<grid>[A-R]{2}\d{2}(?:[A-X]{2})?))?\s*$",
    re.IGNORECASE,
)


def parse_cq_message(msg: str) -> tuple:
    msg = msg.strip()
    if not msg.upper().startswith("CQ"):
        return None, None, None
    m = _CQ_RE.match(msg)
    if not m:
        return None, None, None
    modifier = (m.group("modifier") or "").upper()
    callsign = (m.group("callsign") or "").upper()
    # The longest part of a call ends in a letter; "CQ RR73" is not a CQ from "RR73".
    if not max(callsign.split("/"), key=len)[-1:].isalpha():
        return None, None, None
    grid     = (m.group("grid")     or "").upper() or None
    return callsign or None, grid, modifier or None


def _directed_sender(msg_text: str, de_call: str) -> str | None:
    """
    Return the transmitting station of a directed message, or None.

    Directed message: "<TO_CALL> <FROM_CALL> <report/exchange>".  FROM_CALL is
    the station we can hear.  Messages involving our own callsign are skipped:
    our own transmissions, and stations calling us (already engaged).
    """
    tokens = msg_text.split()
    if tokens and tokens[0].upper() == "TU;":     # contest "TU; <TO> <FROM> ..."
        tokens = tokens[1:]
    if len(tokens) < 2:
        return None
    to_call, from_call = (t.strip("<>").upper() for t in tokens[:2])
    dc = de_call.upper()
    if dc and dc in (to_call, from_call):
        return None
    # Rejects free text, reports, RR73 and unresolved hashes ("<...>").
    if not _STRICT_CALL_RE.match(from_call):
        return None
    return from_call
