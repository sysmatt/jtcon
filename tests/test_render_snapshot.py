"""Golden-output test: replay a matrix of v2 options × bands × messages and compare
every rendered line (and the NEEDED/MATCH/bell metadata) with a stored snapshot.

This guards the refactor phases that must not change behaviour.  When output
changes on purpose, regenerate the snapshot and review the diff:

    JTCON_UPDATE_SNAPSHOTS=1 python3 -m pytest tests/test_render_snapshot.py
    git diff tests/snapshots/
"""

import os
from pathlib import Path

from jtcon.core.bands import _freq_to_band
from jtcon.core.decode import handle_packet
from jtcon.tui.render import _BGRN, _BMAG, _BOLD, _BRED, _BYEL, _CYN, _DIM, _GRN, _header_line, _RST
import wsjtx_packets as pk
from conftest import watch_patterns

SNAPSHOT = Path(__file__).parent / "snapshots" / "render_v2.txt"

DIALS = (14_074_000, 7_074_000, 10_136_000)   # 20m (5BWAS band), 40m, 30m (not 5BWAS)

OPTION_SETS = (
    (),
    ("--raw",),
    ("--all",),
    ("--all", "--raw"),
    ("--was",),
    ("--5bwas",),
    ("--qsl-only", "--show-time"),
    ("--no-color",),
    ("--no-need-cqz", "--no-need-country", "--qsl-only"),
    ("--worked-lag-days", "-1", "--pota", "--match-state", "MA"),
    ("--worked-lag-days", "400", "--qsl-only"),
)

MESSAGES = (
    "CQ W1ABC FN42",               # worked, confirmed, MA
    "CQ DX VK3XY QF22",            # never worked, watchlist hit
    "CQ POTA K2XYZ FN20",          # NJ, worked FT4 on 40m, unconfirmed
    "CQ KH6/W1XX",                 # compound call, no grid
    "CQ JA1XYZ PM95",              # worked, unconfirmed
    "CQ DL1ABC JO62",              # worked today
    "CQ N1NEW FN43",               # NH, never worked
    "CQ K5TX EM12",                # TX, worked 20m unconfirmed
    "CQ W9ZZZ EN52",               # IL via hamdat, confirmed 15m
    "CQ Q0ZZZ",                    # unknown prefix
    "W1ABC JA1XYZ -12",            # directed
    "K2XYZ <W1ABC/P> R-12",        # hashed sender
    "N0CALL W9ZZZ -05",            # calling me
    "W9ZZZ N0CALL R-05",           # me calling
    "TU; W1ABC K2XYZ R 579 MA",    # contest exchange
    "W1ABC VK3XY RR73",            # directed, needed sender
    "TNX RR73 GL",                 # free text
)

_CODE_NAMES = {
    _RST: "{RST}", _DIM: "{DIM}", _BOLD: "{BOLD}", _GRN: "{GRN}",
    _BGRN: "{BGRN}", _CYN: "{CYN}", _BRED: "{BRED}",
    _BYEL: "{BYEL}", _BMAG: "{BMAG}",
}


def _readable(line: str) -> str:
    for code, name in _CODE_NAMES.items():
        line = line.replace(code, name)
    assert "\x1b" not in line, f"unknown escape code in {line!r}"
    return line


def _render_all(make_ctx) -> str:
    out = []
    watch = watch_patterns("^VK", "^ZL")

    def section(title, ctx, msgs=MESSAGES, modes=("~", "+")):
        out.append(f"#### {title}")
        has_hamdat = ctx.hamdat is not None
        out.append(_readable(_header_line(ctx.config, ctx.adif_log is not None, has_hamdat)))
        for msg in msgs:
            for mode in modes:
                meta = {}
                lines = handle_packet(pk.decode(msg, mode=mode), ctx, ("127.0.0.1", 2237), meta)
                out.append(f"## {mode} {msg!r}")
                out.extend(_readable(l) for l in lines)
                if meta:
                    out.append(f"   meta call={meta['callsign']} cq={meta['is_cq']}"
                               f" need={meta['needed_flags']} match={meta['match_flags']}"
                               f" bell={meta['bell']}")

    for dial in DIALS:
        for opts in OPTION_SETS:
            ctx = make_ctx(*opts, dial_freq=dial, call_patterns=watch)
            section(f"{_freq_to_band(dial)} {' '.join(opts) or '(defaults)'}", ctx)

    section("no ADIF log", make_ctx(adif=False, call_patterns=watch), modes=("~",))
    section("no ADIF log, no hamdat", make_ctx(adif=False, hamdat=None), modes=("~",))
    section("ADIF log, no hamdat, --was", make_ctx("--was", hamdat=None), modes=("~",))
    section("--debug", make_ctx("--debug"), msgs=MESSAGES[:2], modes=("~",))
    return "\n".join(out) + "\n"


def test_render_matches_snapshot(make_ctx):
    actual = _render_all(make_ctx)
    if os.environ.get("JTCON_UPDATE_SNAPSHOTS") or not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(actual, encoding="utf-8")
    expected = SNAPSHOT.read_text(encoding="utf-8")
    if actual != expected:
        # Point at the first differing line rather than dumping 100 kB.
        a, e = actual.splitlines(), expected.splitlines()
        for i, (la, le) in enumerate(zip(a, e)):
            if la != le:
                ctx = "\n".join(e[max(0, i - 15):i])
                raise AssertionError(f"snapshot differs at line {i + 1}:\n{ctx}\n"
                                     f"- expected: {le}\n+ actual:   {la}")
        raise AssertionError(f"snapshot length differs: {len(e)} expected, {len(a)} actual lines")
