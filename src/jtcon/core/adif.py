"""ADIF log files: discovery, parsing, and the worked/confirmed index (AdifLog)."""

import re
import sys
from datetime import datetime
from pathlib import Path

from jtcon.core.bands import _5BWAS_BANDS, _WAS_STATES
from jtcon.core.cty import CtyDat


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
#  ADIF parser + worked-log tracker
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


class AdifLog:
    def __init__(self, paths: list[Path], cty: CtyDat, hamdat=None,
                 qsl_only: bool = False, log=None):
        self.paths     = [paths] if isinstance(paths, Path) else list(paths)
        self.cty       = cty
        self._hamdat   = hamdat
        self._qsl_only = qsl_only
        self._log      = log or (lambda m: print(m, file=sys.stderr))

        self.worked_calls:        set  = set()
        self.worked_grids:        set  = set()
        self.worked_dxcc:         set  = set()   # QSL-filtered when qsl_only
        self.worked_cqz:          set  = set()   # QSL-filtered when qsl_only
        self.worked_country:      set  = set()   # QSL-filtered when qsl_only
        self.worked_dxcc_all:     set  = set()   # all QSOs regardless of QSL
        self.worked_cqz_all:      set  = set()
        self.worked_country_all:  set  = set()
        self.qrz_confirmed_calls:    set  = set()   # app_qrzlog_status=C
        self.lotw_qsl_rcvd_calls:    set  = set()   # lotw_qsl_rcvd=Y
        self.qrz_confirmed_by_band:  dict = {}       # band -> {call} QRZ-confirmed
        self.lotw_rcvd_by_band:      dict = {}       # band -> {call} LoTW-confirmed
        self.worked_calls_by_band:   dict = {}       # band -> {call} any QSO
        self.worked_calls_by_mode:   dict = {}       # mode -> {call} any QSO
        self.last_worked:            dict = {}       # call -> most recent date worked
        # WAS / 5BWAS — always LOTW-only regardless of --qsl-only
        self.worked_states_lotw:       set  = set()   # states with lotw_qsl_rcvd=Y
        self.worked_states_all:        set  = set()   # states from any QSO
        self.worked_states_bands_lotw: dict = {}       # state -> {band} LoTW-confirmed
        self.worked_states_bands_all:  dict = {}       # state -> {band} any QSO
        self.total_qsos = 0
        self._load()

    def _load(self) -> None:
        """Populate the worked sets.  Called once; a reload builds a new AdifLog."""
        for path in self.paths:
            try:
                qsos = parse_adif(path)
            except Exception as exc:
                self._log(f"[adif] Cannot read {path.name}: {exc}")
                continue
            self.total_qsos += len(qsos)
            for q in qsos:
                call    = q.get("call", "").strip().upper()
                cqz_raw = (q.get("cqz") or q.get("cq_zone") or "").strip()
                country = (q.get("country") or q.get("entity") or "").strip().upper()
                grid    = q.get("gridsquare", "").strip().upper()[:4]

                qrz_status = q.get("app_qrzlog_status", "").strip().upper()
                lotw_rcvd  = q.get("lotw_qsl_rcvd",    "").strip().upper()
                was_band   = q.get("band", "").strip().lower()
                # FT4, FST4, Q65 etc. are logged as MODE=MFSK with a SUBMODE.
                qso_mode   = (q.get("submode") or q.get("mode") or "").strip().upper()
                if call and qrz_status == "C":
                    self.qrz_confirmed_calls.add(call)
                    if was_band:
                        self.qrz_confirmed_by_band.setdefault(was_band, set()).add(call)
                if call and lotw_rcvd == "Y":
                    self.lotw_qsl_rcvd_calls.add(call)
                    if was_band:
                        self.lotw_rcvd_by_band.setdefault(was_band, set()).add(call)

                # WAS / 5BWAS tracking — always LOTW-only for _lotw sets
                # WSJT-X does not populate the state field in its ADIF; fall back
                # to hamdat so per-band LoTW data is captured for those logs.
                was_state = q.get("state", "").strip().upper()
                if not was_state and self._hamdat and call:
                    was_state = self._hamdat.state(call) or ""
                if was_state and was_state in _WAS_STATES:
                    self.worked_states_all.add(was_state)
                    if was_band in _5BWAS_BANDS:
                        self.worked_states_bands_all.setdefault(was_state, set()).add(was_band)
                    if lotw_rcvd == "Y":
                        self.worked_states_lotw.add(was_state)
                        if was_band in _5BWAS_BANDS:
                            self.worked_states_bands_lotw.setdefault(was_state, set()).add(was_band)

                qso_date_str = q.get("qso_date", "").strip()
                if call and len(qso_date_str) == 8:
                    try:
                        qso_date = datetime.strptime(qso_date_str, "%Y%m%d").date()
                        existing = self.last_worked.get(call)
                        if existing is None or qso_date > existing:
                            self.last_worked[call] = qso_date
                    except ValueError:
                        pass

                counts_as_worked = (not self._qsl_only) or (lotw_rcvd == "Y")

                try:
                    cqz_int = int(cqz_raw)
                    self.worked_cqz_all.add(cqz_int)
                    if counts_as_worked:
                        self.worked_cqz.add(cqz_int)
                except ValueError:
                    pass
                if country:
                    self.worked_country_all.add(country)
                    if counts_as_worked:
                        self.worked_country.add(country)
                if grid:
                    self.worked_grids.add(grid)
                if call:
                    self.worked_calls.add(call)
                    if was_band:
                        self.worked_calls_by_band.setdefault(was_band, set()).add(call)
                    if qso_mode:
                        self.worked_calls_by_mode.setdefault(qso_mode, set()).add(call)
                    info = self.cty.lookup(call)
                    if info:
                        self.worked_dxcc_all.add(info["primary_prefix"].upper())
                        if counts_as_worked:
                            self.worked_dxcc.add(info["primary_prefix"].upper())
                        if not cqz_raw:
                            self.worked_cqz_all.add(info["cqz"])
                            if counts_as_worked:
                                self.worked_cqz.add(info["cqz"])
                        # Always use cty.dat country name for consistency with decode-time lookups
                        self.worked_country_all.add(info["country"].upper())
                        if counts_as_worked:
                            self.worked_country.add(info["country"].upper())

    def needed_flags(self, entity_info: dict, cfg) -> list:
        flags = []
        prefix  = (entity_info.get("primary_prefix") or "").upper()
        cqz     = entity_info.get("cq_zone")
        country = (entity_info.get("entity") or "").upper()
        if cfg.wants("dxcc") and prefix and prefix not in self.worked_dxcc:
            flags.append(f"NEW-DXCC({prefix})")
        if cfg.wants("cqz") and isinstance(cqz, int) and cqz not in self.worked_cqz:
            flags.append(f"NEW-CQZ({cqz})")
        if cfg.wants("country") and country and country not in self.worked_country:
            flags.append(f"NEW-CTRY({entity_info.get('entity', '?')})")
        return flags

    def worked_flags(self, entity_info: dict, cfg) -> list:
        flags = []
        prefix  = (entity_info.get("primary_prefix") or "").upper()
        cqz     = entity_info.get("cq_zone")
        country = (entity_info.get("entity") or "").upper()
        if cfg.wants("dxcc") and prefix and prefix in self.worked_dxcc:
            flags.append("dxcc")
        if cfg.wants("cqz") and isinstance(cqz, int) and cqz in self.worked_cqz:
            flags.append("cqz")
        if cfg.wants("country") and country and country in self.worked_country:
            flags.append("country")
        return flags

    def worked_flags_all(self, entity_info: dict, cfg) -> list:
        """Return which categories were worked in any QSO (ignores qsl_only filter)."""
        flags = []
        prefix  = (entity_info.get("primary_prefix") or "").upper()
        cqz     = entity_info.get("cq_zone")
        country = (entity_info.get("entity") or "").upper()
        if cfg.wants("dxcc") and prefix and prefix in self.worked_dxcc_all:
            flags.append("dxcc")
        if cfg.wants("cqz") and isinstance(cqz, int) and cqz in self.worked_cqz_all:
            flags.append("cqz")
        if cfg.wants("country") and country and country in self.worked_country_all:
            flags.append("country")
        return flags
