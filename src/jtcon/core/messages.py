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


def base_call(call: str) -> str:
    """The home call inside a portable or prefixed one: W1ABC/P, VE3/W1ABC and
    <W1ABC/QRP> are all W1ABC.  A prefix (VE3, KH6) ends in a digit, a suffix (P,
    QRP, 7) has no digit and a letter both, so the base call is the longest part
    with a digit that ends in a letter."""
    parts = [p for p in call.strip().strip("<>").upper().split("/") if p]
    if not parts:
        return ""
    calls = [p for p in parts if p[-1].isalpha() and any(c.isdigit() for c in p)]
    return max(calls or parts, key=len)


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


# ══════════════════════════════════════════════════════════════════════════════
#  QSO stage
# ══════════════════════════════════════════════════════════════════════════════

# Where a station is in a QSO, from its own latest transmission.
STAGE_CQ, STAGE_ANSWER, STAGE_REPORT, STAGE_73 = "cq", "answer", "report", "73"

_REPORT_RE = re.compile(r"^R?[+-]\d{1,2}$", re.IGNORECASE)


def qso_stage(msg_text: str) -> tuple:
    """
    (sender, stage) for a decoded message, or (None, None).

        CQ K5TX EM10          K5TX, "cq"      calling CQ
        W1ABC K5TX EM10       K5TX, "answer"  answering a CQ (with or without a grid)
        W1ABC K5TX -12, R-12  K5TX, "report"  exchanging reports
        W1ABC K5TX RR73       K5TX, "73"      signing off (RRR, RR73, 73)

    Anything else from a station (contest exchanges, free text) is (sender, None).
    """
    call, _, _ = parse_cq_message(msg_text)
    if call:
        return call, STAGE_CQ
    tokens = msg_text.split()
    if tokens and tokens[0].upper() == "TU;":
        tokens = tokens[1:]
    if len(tokens) < 2:
        return None, None
    sender = tokens[1].strip("<>").upper()
    if not _STRICT_CALL_RE.match(sender):
        return None, None
    extra = tokens[2].upper() if len(tokens) == 3 else "" if len(tokens) == 2 else None
    if extra in ("RRR", "RR73", "73"):
        return sender, STAGE_73
    if extra is not None and _REPORT_RE.match(extra):
        return sender, STAGE_REPORT
    if extra == "" or (extra and re.fullmatch(r"[A-R]{2}\d{2}", extra)):
        return sender, STAGE_ANSWER
    return sender, None
