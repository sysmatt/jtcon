"""Award rules: is this entity / zone / state / grid new, worked, or confirmed?

Each rule maps a spot to one key (an entity prefix, a zone, a state on a band)
and each QSO to the keys it counts toward.  A key is CONFIRMED when a QSO for it
has one of the rule's confirm sources (or any QSO, with confirm = []).
"""

from jtcon.core.bands import _5BWAS_BANDS, _WAS_STATES
from jtcon.core.wanted.base import CONFIRMED, NEW, WORKED, Hit, WantedRule


class AwardRule(WantedRule):
    kind = "award"
    needs_logbook = True
    value_width = 2      # width of the value in the status column; 0: the mark only

    def __init__(self, spec):
        super().__init__(spec)
        self.confirm = set(spec.settings["confirm"])
        self.worked: set = set()
        self.confirmed: set = set()

    def spot_key(self, spot):
        raise NotImplementedError

    def qso_keys(self, qso) -> tuple:
        raise NotImplementedError

    def detail(self, key, spot) -> str:
        return str(key)

    def value(self, key, spot) -> str:
        """The short value shown in the status column."""
        return self.detail(key, spot)

    def _index(self, qso, worked: set, confirmed: set) -> None:
        keys = [k for k in self.qso_keys(qso) if k is not None and k != ""]
        worked.update(keys)
        if not self.confirm or self.confirm & qso.confirmations:
            confirmed.update(keys)

    def rebuild(self, logbook) -> None:
        worked, confirmed = set(), set()
        for q in logbook.qsos:
            self._index(q, worked, confirmed)
        self.worked, self.confirmed = worked, confirmed

    def add(self, qso) -> None:
        self._index(qso, self.worked, self.confirmed)

    def status(self, key) -> str:
        return (CONFIRMED if key in self.confirmed else
                WORKED if key in self.worked else NEW)

    def evaluate(self, spot) -> list:
        key = self.spot_key(spot)
        if key is None or key == "":
            return []
        value = self.value(key, spot) if self.value_width else ""
        return [Hit(self.label, self.detail(key, spot), self.status(key), value)]

    def worked_recently(self, hit, spot, qsos, since) -> bool:
        """Only a recent QSO that counts toward this same key: a station worked
        on 20m doesn't hide a 5BWAS hit on 40m."""
        key = self.spot_key(spot)
        return any(q.date and q.date >= since and key in self.qso_keys(q) for q in qsos)


class DxccRule(AwardRule):
    value_width = 5

    def spot_key(self, spot):
        return (spot.entity.get("primary_prefix") or "").upper() or None

    def qso_keys(self, q):
        return (q.dxcc,)


class CqzRule(AwardRule):
    def spot_key(self, spot):
        z = spot.entity.get("cq_zone")
        return z if isinstance(z, int) else None

    def qso_keys(self, q):
        return (q.cqz,)


class ItuzRule(AwardRule):
    def spot_key(self, spot):
        z = spot.entity.get("itu_zone")
        return z if isinstance(z, int) else None

    def qso_keys(self, q):
        return (q.ituz,)


class CountryRule(AwardRule):
    value_width = 0      # the entity column already shows the name

    def spot_key(self, spot):
        return (spot.entity.get("entity") or "").upper() or None

    def qso_keys(self, q):
        # cty.dat's name, and the COUNTRY field as logged if it differs.
        return (q.country, q.log_country)

    def detail(self, key, spot):
        return spot.entity.get("entity") or key


class WasRule(AwardRule):
    def spot_key(self, spot):
        return spot.state if spot.state in _WAS_STATES else None

    def qso_keys(self, q):
        return (q.state,) if q.state in _WAS_STATES else ()


class FiveBandWasRule(AwardRule):
    """5-Band WAS: the spot's state on the current band."""

    def spot_key(self, spot):
        if spot.state in _WAS_STATES and spot.band in _5BWAS_BANDS:
            return (spot.state, spot.band)
        return None

    def qso_keys(self, q):
        if q.state in _WAS_STATES and q.band in _5BWAS_BANDS:
            return ((q.state, q.band),)
        return ()

    def detail(self, key, spot):
        return f"{key[0]}/{key[1]}"

    def value(self, key, spot):
        return key[0]


class GridRule(AwardRule):
    """4-character grid squares."""
    value_width = 0      # the grid column already shows it

    def spot_key(self, spot):
        return spot.grid[:4].upper() if spot.grid else None

    def qso_keys(self, q):
        return (q.grid,)
