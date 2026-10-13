"""Inputs: files jtcon reads at startup and reloads when they change.

An input is checked by comparing its files' modification times; nothing is read
until one changes.  load() runs in a worker thread and returns the new value, or
raises and leaves the caller with the previous one.
"""

from pathlib import Path

from jtcon.core.adif import parse_adif
from jtcon.core.calls import CallIndex
from jtcon.core.hamdat import load_state_file
from jtcon.core.logbook import Logbook, qso_from_adif
from jtcon.core.patterns import load_regex_patterns


def mtimes(paths) -> dict:
    out = {}
    for p in paths:
        try:
            out[p] = p.stat().st_mtime
        except OSError:
            pass
    return out


class Input:
    name = ""        # log tag, e.g. "adif" → "[adif] Reloaded …"

    def __init__(self, paths):
        self.paths = [Path(p) for p in paths]
        self._mtimes: dict | None = None    # as of the last load; None before it

    def changed(self) -> bool:
        return self._mtimes is not None and mtimes(self.paths) != self._mtimes

    def load(self):
        before = mtimes(self.paths)         # a write during read() is seen next time
        value = self.read()
        self._mtimes = before
        return value

    def skip(self) -> None:
        """The load failed: don't try again until a file changes again."""
        self._mtimes = mtimes(self.paths)

    def read(self):
        raise NotImplementedError


class AdifInput(Input):
    """ADIF logs, read into a Logbook plus its per-call index (CallIndex).

    Only files that changed are parsed again.  QSOs that WSJT-X reported as
    logged (*extra*) are merged in on every load, so they stay worked until the
    log file catches up, and are then merged with the file's record.
    """
    name = "adif"

    def __init__(self, paths, cty, hamdat, log=None):
        super().__init__(paths)
        self.cty, self.hamdat = cty, hamdat
        self.log = log or (lambda m: None)
        self.extra: list = []
        self._cache: dict = {}              # path -> (mtime, [QSO])

    def _file_qsos(self, path: Path) -> list:
        mtime = path.stat().st_mtime
        cached = self._cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        qsos = [qso_from_adif(rec, self.cty, self.hamdat, path.name)
                for rec in parse_adif(path)]
        self._cache[path] = (mtime, qsos)
        return qsos

    def read(self) -> tuple:
        logbook = Logbook()
        for path in self.paths:
            try:
                qsos = self._file_qsos(path)
            except Exception as exc:
                self._cache.pop(path, None)
                self.log(f"[adif] Cannot read {path.name}: {exc}")
                continue
            for q in qsos:
                logbook.add(q)
        duplicates = logbook.merged
        for q in list(self.extra):
            if logbook.add(q).sources == {"wsjt-x"}:
                logbook.unsaved += 1
        logbook.merged = duplicates   # a logged QSO meeting its file record isn't one
        return logbook, CallIndex(logbook.qsos)


class PatternInput(Input):
    """A watchlist: regex files for a callsigns or message rule (*rule*)."""

    def __init__(self, name: str, files, rule=None, log=None):
        super().__init__(files)
        self.name, self.rule = name, rule
        self.log = log or (lambda m: None)

    def read(self) -> list:
        return load_regex_patterns(self.paths, log=self.log)

    def describe(self, patterns: list) -> str:
        return f"{len(patterns)} pattern(s) from {', '.join(str(p) for p in self.paths)}"


class StatesInput(Input):
    """A states rule (*rule*): states listed in the config plus those in its file."""

    def __init__(self, states, file, rule=None):
        super().__init__([file])
        self.states = set(states)
        self.rule = rule
        self.name = rule.name if rule else "states"

    def read(self) -> set:
        return self.states | load_state_file(self.paths[0])

    @staticmethod
    def describe(states: set) -> str:
        return f"{len(states)} state(s): {', '.join(sorted(states))}"
