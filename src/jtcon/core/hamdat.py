"""US state lookups: the hamdat FCC database, and state lists."""

import sqlite3
from pathlib import Path


HAMDAT_DB_DEFAULT = Path.home() / ".hamdat" / "hamdat.db"


# ══════════════════════════════════════════════════════════════════════════════
#  State / hamdat helpers
# ══════════════════════════════════════════════════════════════════════════════

def load_state_set(value: str) -> set:
    p = Path(value)
    if p.is_file():
        states = set()
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            states.add(line.upper())
        return states
    return {s.strip().upper() for s in value.split(",") if s.strip()}


class HamdatLookup:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        self._cache: dict = {}

    def state(self, callsign: str) -> str | None:
        call = callsign.upper().strip()
        if call in self._cache:
            return self._cache[call]
        row = self._conn.execute(
            """SELECT e.state
               FROM license_header h
               JOIN entity e ON e.unique_system_identifier = h.unique_system_identifier
                   AND (e.entity_type = 'L' OR e.entity_type = '')
               WHERE h.call_sign = ? AND h.license_status = 'A'
               ORDER BY h.id DESC LIMIT 1""",
            (call,),
        ).fetchone()
        result = (row[0] or "").strip().upper() or None if row else None
        self._cache[call] = result
        return result

    def close(self):
        self._conn.close()
