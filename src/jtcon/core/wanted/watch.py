"""Watch rules: activations, callsign and message watchlists, and state lists.

They hit or they don't.  Watchlist patterns and state files are loaded by the
engine (and reloaded when edited); the rule just holds the current list.
"""

import re

from jtcon.core.patterns import ACTIVATION_KEYWORDS, _regex_matches
from jtcon.core.wanted.base import Hit, WantedRule


class ActivationRule(WantedRule):
    """POTA / SOTA / IOTA as a word in the message."""

    def __init__(self, spec):
        super().__init__(spec)
        self.word = ACTIVATION_KEYWORDS[spec.rule]
        self._re = re.compile(rf"\b{self.word}\b", re.IGNORECASE)

    def evaluate(self, spot) -> list:
        return [Hit(self.label)] if self._re.search(spot.message) else []


class CallsignsRule(WantedRule):
    """Regex watchlist files searched in the callsign."""

    def __init__(self, spec):
        super().__init__(spec)
        self.patterns: list = []      # [(compiled, source file name)]

    def evaluate(self, spot) -> list:
        if not spot.call:
            return []
        return [Hit(self.label, m) for m in _regex_matches(spot.call, self.patterns)]


class MessageRule(CallsignsRule):
    """Regex watchlist files searched in the whole message."""

    def evaluate(self, spot) -> list:
        return [Hit(self.label, m) for m in _regex_matches(spot.message, self.patterns)]


class StatesRule(WantedRule):
    """A list of US states (from hamdat)."""

    def __init__(self, spec):
        super().__init__(spec)
        self.states: set = set(spec.settings["states"])

    def evaluate(self, spot) -> list:
        return [Hit(self.label, spot.state)] if spot.state in self.states else []
