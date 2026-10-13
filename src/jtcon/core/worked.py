"""What has been worked and confirmed, indexed from the Logbook for the current rules.

Phase 5 replaces this with per-rule indexes; until then it keeps v2's sets, and
only LoTW counts as a confirmation.
"""

from jtcon.core.bands import _5BWAS_BANDS, _WAS_STATES


class WorkedIndex:
    def __init__(self, qsos=(), qsl_only: bool = False):
        self._qsl_only = qsl_only

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
        for q in qsos:
            self.add(q)

    def add(self, q) -> None:
        """Index one Logbook QSO.  Adding a record again (after a merge) is harmless."""
        call, band = q.call, q.band
        lotw = "lotw" in q.confirmations
        if call and "qrz" in q.confirmations:
            self.qrz_confirmed_calls.add(call)
            if band:
                self.qrz_confirmed_by_band.setdefault(band, set()).add(call)
        if call and lotw:
            self.lotw_qsl_rcvd_calls.add(call)
            if band:
                self.lotw_rcvd_by_band.setdefault(band, set()).add(call)

        st = q.state
        if st in _WAS_STATES:
            self.worked_states_all.add(st)
            if band in _5BWAS_BANDS:
                self.worked_states_bands_all.setdefault(st, set()).add(band)
            if lotw:
                self.worked_states_lotw.add(st)
                if band in _5BWAS_BANDS:
                    self.worked_states_bands_lotw.setdefault(st, set()).add(band)

        if call and q.date:
            last = self.last_worked.get(call)
            if last is None or q.date > last:
                self.last_worked[call] = q.date

        counts_as_worked = (not self._qsl_only) or lotw
        if q.cqz is not None:
            self.worked_cqz_all.add(q.cqz)
            if counts_as_worked:
                self.worked_cqz.add(q.cqz)
        for country in {q.log_country, q.country} - {""}:
            self.worked_country_all.add(country)
            if counts_as_worked:
                self.worked_country.add(country)
        if q.dxcc:
            self.worked_dxcc_all.add(q.dxcc)
            if counts_as_worked:
                self.worked_dxcc.add(q.dxcc)
        if q.grid:
            self.worked_grids.add(q.grid)
        if call:
            self.worked_calls.add(call)
            if band:
                self.worked_calls_by_band.setdefault(band, set()).add(call)
            if q.mode:
                self.worked_calls_by_mode.setdefault(q.mode, set()).add(call)

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
