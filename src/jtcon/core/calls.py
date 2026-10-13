"""Per-callsign facts from the Logbook, for the display columns.

When did we last work this call, on which bands and modes, is its grid or state
worked, and has it confirmed via QRZ or LoTW.  Whether a station is *wanted* is
the wanted rules' job (core/wanted), not this index's.
"""

from jtcon.core.bands import _WAS_STATES


class CallIndex:
    def __init__(self, qsos=()):
        self.worked_calls:          set  = set()
        self.worked_grids:          set  = set()
        self.worked_states:         set  = set()
        self.worked_calls_by_band:  dict = {}    # band -> {call}
        self.worked_calls_by_mode:  dict = {}    # mode -> {call}
        self.last_worked:           dict = {}    # call -> most recent date worked
        self.confirmed_calls:       dict = {}    # source -> {call}: "qrz", "lotw"
        self.confirmed_by_band:     dict = {}    # source -> band -> {call}
        for q in qsos:
            self.add(q)

    def add(self, q) -> None:
        """Index one Logbook QSO.  Adding a record again (after a merge) is harmless."""
        if q.grid:
            self.worked_grids.add(q.grid)
        if q.state in _WAS_STATES:
            self.worked_states.add(q.state)
        call = q.call
        if not call:
            return
        self.worked_calls.add(call)
        if q.band:
            self.worked_calls_by_band.setdefault(q.band, set()).add(call)
        if q.mode:
            self.worked_calls_by_mode.setdefault(q.mode, set()).add(call)
        if q.date:
            last = self.last_worked.get(call)
            if last is None or q.date > last:
                self.last_worked[call] = q.date
        for source in q.confirmations:
            self.confirmed_calls.setdefault(source, set()).add(call)
            if q.band:
                self.confirmed_by_band.setdefault(source, {}) \
                                      .setdefault(q.band, set()).add(call)

    def confirmed(self, call: str, source: str, band: str | None = None) -> bool:
        """Has *call* confirmed a QSO via *source* (on *band*, if given)?"""
        if band is None:
            return call in self.confirmed_calls.get(source, ())
        return call in self.confirmed_by_band.get(source, {}).get(band, ())
