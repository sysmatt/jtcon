"""Watch rules: activations, callsign and message watchlists, and state lists.

They hit or they don't.  Watchlist patterns and state files are loaded by the
engine (and reloaded when edited); the rule just holds the current list.
"""

import re

from jtcon.core.patterns import ACTIVATION_KEYWORDS, _regex_matches, inline_patterns
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
    """A callsign watchlist: the config's own list (calls), and regex files."""

    LISTED = "calls"

    def __init__(self, spec):
        super().__init__(spec)
        self.inline = inline_patterns(spec.settings.get(self.LISTED) or [],
                                      calls=self.LISTED == "calls")   # [(compiled, text)]
        self.patterns: list = []      # from the files: [(compiled, source file name)]

    def matches(self, value: str) -> list:
        return ([text for pat, text in self.inline if pat.search(value)]
                + _regex_matches(value, self.patterns))

    def evaluate(self, spot) -> list:
        if not spot.call:
            return []
        return [Hit(self.label, m) for m in self.matches(spot.call)]


class MessageRule(CallsignsRule):
    """A message watchlist: the config's own patterns, and regex files,
    searched in the whole message."""

    LISTED = "patterns"

    def evaluate(self, spot) -> list:
        return [Hit(self.label, m) for m in self.matches(spot.message)]


class StatesRule(WantedRule):
    """A list of US states (from hamdat)."""

    def __init__(self, spec):
        super().__init__(spec)
        self.states: set = set(spec.settings["states"])

    def evaluate(self, spot) -> list:
        return [Hit(self.label, spot.state)] if spot.state in self.states else []
