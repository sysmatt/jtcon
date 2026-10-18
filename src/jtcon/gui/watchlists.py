"""Watch this station: the callsign watchlists (callsigns rules) in the
config, for the station menus.

The lists are the `calls` of the callsigns rules in the file whose rules are
in use: the profile's if it has its own, else the config file's.  A change is
saved as the settings editor saves (only that list changes, a dated backup
first), and used at once if the rule is running; a new list needs a reload."""

from jtcon.config import DEFAULT_WANTED, ConfigError, _load_layer
from jtcon.config_edit import ConfigFile
from jtcon.core.patterns import _PLAIN_CALL_RE


class Watchlists:
    """The watchlists in *startup*'s files, as they are now.  ConfigError if
    the file can't be read."""

    def __init__(self, startup):
        self.startup = startup
        main = startup.file
        path, base = main, None
        if startup.profile_file is not None and startup.profile_file.exists():
            profile = ConfigFile(startup.profile_file)
            if profile.wanted() is not None:        # its rules replace the config's
                path = startup.profile_file
                base = _load_layer(main) if main.exists() else {}
        self.file = ConfigFile(path, base=base)
        entries = self.file.wanted()
        self.from_file = entries is not None
        self.entries = entries if entries is not None else [{"rule": r} for r in DEFAULT_WANTED]

    @property
    def path(self):
        return self.file.path

    def lists(self) -> list:
        """[(place in the rules, name, its calls)] for each callsigns rule."""
        return [(i, e.get("name") or e["rule"], list(e.get("calls") or []))
                for i, e in enumerate(self.entries) if e["rule"] == "callsigns"]

    def holding(self, call: str) -> list:
        """The places of the lists that have *call* itself (not a pattern)."""
        return [i for i, _, calls in self.lists()
                if call.upper() in (c.strip().upper() for c in calls)]

    def names(self) -> set:
        return {(e.get("name") or e["rule"]).lower() for e in self.entries}

    # ── Changes ───────────────────────────────────────────────────────────────

    def add(self, call: str, place: int | None, name: str | None = None) -> int:
        """Add *call* to the list at *place*, or (None) to a new list *name*.
        Its place."""
        call = call.upper()
        if not _PLAIN_CALL_RE.fullmatch(call):
            raise ConfigError(f"{call} isn't a callsign")
        if place is None:
            entry = {"rule": "callsigns"}
            if name:
                entry["name"] = name
            entry["calls"] = [call]
            self.entries.append(entry)
            return len(self.entries) - 1
        entry = self.entries[place]
        calls = list(entry.get("calls") or [])
        if call not in (c.strip().upper() for c in calls):
            entry["calls"] = calls + [call]
        return place

    def remove(self, call: str, place: int) -> None:
        entry = self.entries[place]
        entry["calls"] = [c for c in entry.get("calls") or []
                          if c.strip().upper() != call.upper()]

    def save(self):
        """Write the rules (each changed in place; comments kept), check, save.
        The backup made, as ConfigFile.save."""
        self.file.set_wanted([(i if self.from_file else None, e)
                              for i, e in enumerate(self.entries)])
        return self.file.save()
