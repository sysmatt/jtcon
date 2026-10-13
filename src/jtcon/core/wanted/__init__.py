"""Wanted rules: which stations are worth calling, and why.

build_rules() turns the config's [[wanted]] entries into rule instances;
evaluate_rules() runs them on a Spot and applies the worked-recently filter.
"""

from datetime import date, timedelta

from jtcon.core.wanted.awards import (AwardRule, CountryRule, CqzRule, DxccRule,
                                      FiveBandWasRule, GridRule, ItuzRule, WasRule)
from jtcon.core.wanted.base import (CONFIRMED, LABELS, MARKS, NEW, WORKED, Hit, Spot,
                                    WantedRule, rule_label)
from jtcon.core.wanted.watch import ActivationRule, CallsignsRule, MessageRule, StatesRule

__all__ = ["AwardRule", "CONFIRMED", "Hit", "LABELS", "MARKS", "NEW", "RULE_CLASSES", "Spot",
           "WORKED", "WantedRule", "build_rules", "evaluate_rules", "rule_label"]


RULE_CLASSES = {
    "dxcc": DxccRule, "cqz": CqzRule, "ituz": ItuzRule, "country": CountryRule,
    "was": WasRule, "5bwas": FiveBandWasRule, "grid": GridRule,
    "pota": ActivationRule, "sota": ActivationRule, "iota": ActivationRule,
    "callsigns": CallsignsRule, "message": MessageRule, "states": StatesRule,
}


def build_rules(specs) -> list:
    """One rule instance per [[wanted]] entry, in config order."""
    return [RULE_CLASSES[s.rule](s) for s in specs]


def evaluate_rules(rules, spot: Spot, logbook, suppress_days: int,
                   today: date) -> tuple:
    """
    Run *rules* on *spot*.  Returns (hits, wanted): every hit, for the status
    columns, and those that make the station wanted.  Award rules need a
    logbook; without one they're skipped.

    A hit doesn't count if this call was worked within *suppress_days* days
    (-1: never suppress) by a QSO that accounts for it: the same entity, the
    same state and band, and so on; for watch rules, any QSO with the call.
    """
    hits, wanted = [], []
    recent = []
    if logbook is not None and spot.call and suppress_days >= 0:
        since = today - timedelta(days=suppress_days)
        recent = [q for q in logbook.by_call.get(spot.call, ()) if q.date and q.date >= since]
    for rule in rules:
        if rule.needs_logbook and logbook is None:
            continue
        for hit in rule.evaluate(spot):
            hits.append(hit)
            if hit.fires and not (recent and rule.worked_recently(hit, spot, recent, since)):
                wanted.append(hit)
    return hits, wanted
