"""The rolling buffer: one slot per recently heard station, numbered 00-99."""

import time
from dataclasses import dataclass, field


BUF_SIZE = 100   # two-digit indexes


def fmt_age(seconds: float) -> str:
    """Format an elapsed time as e.g. '45s', '3m05s', '1h12m'."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


@dataclass
class Station:
    idx:          int
    callsign:     str
    first_seen:   float
    last_seen:    float
    count:        int   = 1
    decode:       object = None     # newest Decode, used for replies
    addr:         tuple | None = None   # WSJT-X address it came from
    needed_flags: list  = field(default_factory=list)
    match_flags:  list  = field(default_factory=list)
    display:      object = None     # the front end's rendering of the newest decode

    @property
    def is_hit(self) -> bool:
        return bool(self.needed_flags or self.match_flags)


class StationTable:
    """
    Each callsign owns one slot.  A repeat decode keeps the station's index and
    refreshes the entry so replies use the newest decode.  A new station takes
    the next free slot, or evicts the least recently heard station.
    """

    def __init__(self, size: int = BUF_SIZE):
        self.size = size
        self._by_idx:  dict = {}
        self._by_call: dict = {}
        self._next = 0

    def __len__(self) -> int:
        return len(self._by_idx)

    def store(self, callsign: str, decode, addr, needed_flags: list,
              match_flags: list) -> Station:
        now = time.monotonic()
        callsign = callsign.upper()
        st = self.find(callsign)
        if st is not None:
            st.count += 1
        else:
            idx = self._alloc()
            old = self._by_idx.get(idx)
            if old:
                del self._by_call[old.callsign]
            st = Station(idx, callsign, first_seen=now, last_seen=now)
            self._by_idx[idx] = self._by_call[callsign] = st
        st.decode, st.addr, st.last_seen = decode, addr, now
        st.needed_flags, st.match_flags = list(needed_flags), list(match_flags)
        return st

    def _alloc(self) -> int:
        for i in range(self.size):
            idx = (self._next + i) % self.size
            if idx not in self._by_idx:
                self._next = (idx + 1) % self.size
                return idx
        return min(self._by_idx.values(), key=lambda s: s.last_seen).idx

    def get(self, idx: int) -> Station | None:
        return self._by_idx.get(idx)

    def find(self, callsign: str) -> Station | None:
        return self._by_call.get(callsign.upper())

    def hits(self) -> list:
        """Stations with NEEDED/MATCH flags, most recently heard last."""
        return sorted((s for s in self._by_idx.values() if s.is_hit),
                      key=lambda s: s.last_seen)

    def clear(self) -> int:
        """Empty the table; return how many stations were dropped."""
        n = len(self._by_idx)
        self._by_idx.clear()
        self._by_call.clear()
        self._next = 0
        return n
