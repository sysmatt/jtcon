"""The wanted-rule interface: Spot in, Hits out.

Award rules (dxcc, was, …) report a status for every spot they apply to: NEW
(never worked), WORKED (worked, but not confirmed by the rule's confirm sources)
or CONFIRMED.  NEW and WORKED make the station wanted.  Watch rules (callsigns,
pota, …) report a hit only when they match.
"""

from dataclasses import dataclass
from datetime import date


NEW, WORKED, CONFIRMED = "new", "worked", "confirmed"

MARKS = {NEW: "❌", WORKED: "❓", CONFIRMED: "✅"}
_WORDS = {NEW: "new", WORKED: "unconfirmed", CONFIRMED: "confirmed"}

# Each rule's label on screen, unless its [[wanted]] entry has its own name.
LABELS = {"dxcc": "DXCC", "cqz": "CQZ", "ituz": "ITUZ", "country": "CTRY", "was": "WAS",
          "5bwas": "5BWAS", "grid": "GRID", "pota": "POTA", "sota": "SOTA", "iota": "IOTA",
          "callsigns": "CALLS", "message": "MSG", "states": "STATE"}


def rule_label(spec) -> str:
    """"WAS" for a plain [[wanted]] rule = "was"; its name if it has its own."""
    return LABELS[spec.rule] if spec.name == spec.rule else spec.name


@dataclass
class Spot:
    """What the rules look at: one decode, after station lookup."""
    call:    str | None
    grid:    str | None
    entity:  dict           # cty.dat: primary_prefix, entity, cq_zone, itu_zone, continent
    state:   str | None     # US state from hamdat
    band:    str | None     # from the dial frequency
    message: str
    is_cq:   bool


@dataclass(frozen=True)
class Hit:
    rule:   str               # the rule's label: "DXCC", "WAS-QRZ"
    detail: str = ""          # "VK", "MA/40m", "^VK [watch.txt]"
    status: str | None = None  # award rules: NEW / WORKED / CONFIRMED; watch rules: None
    value:  str = ""          # award rules: the short value for the status column

    @property
    def fires(self) -> bool:
        """Does this hit make the station wanted?"""
        return self.status != CONFIRMED

    def _name(self) -> str:
        return f"{self.rule}({self.detail})" if self.detail else self.rule

    def mark(self) -> str:
        """For the screen: ❌DXCC(VK), ❓WAS(MA), CALLS(^VK [watch.txt])."""
        return MARKS.get(self.status, "") + self._name()

    def text(self) -> str:
        """For alerts and scripts: "DXCC(VK) new", "WAS(MA) unconfirmed"."""
        return f"{self._name()} {_WORDS[self.status]}" if self.status else self._name()

    def as_dict(self) -> dict:
        return {"rule": self.rule, "detail": self.detail, "status": self.status}


class WantedRule:
    """A rule instance, made from one [[wanted]] entry (a WantedSpec)."""
    kind = "watch"
    needs_logbook = False

    def __init__(self, spec):
        self.spec  = spec
        self.name  = spec.name
        self.label = rule_label(spec)

    def evaluate(self, spot: Spot) -> list:
        """The rule's Hits for *spot* (award rules: at most one, with a status)."""
        raise NotImplementedError

    def worked_recently(self, hit: Hit, spot: Spot, qsos: list, since: date) -> bool:
        """Does one of *qsos* (this call's QSOs) since *since* account for *hit*?
        Watch rules: any QSO with the call does."""
        return any(q.date and q.date >= since for q in qsos)

    # Award rules keep an index of the logbook.
    def rebuild(self, logbook) -> None:
        pass

    def add(self, qso) -> None:
        pass
