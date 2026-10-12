"""Per-cycle decode counts and running averages for the status line."""


# ══════════════════════════════════════════════════════════════════════════════
#  Cycle tracker
# ══════════════════════════════════════════════════════════════════════════════

class CycleTracker:
    """Counts decodes per WSJT-X cycle and maintains running averages."""

    _SLOT_MS = {"FT4": 7500}   # default 15000 ms for FT8

    def __init__(self):
        self.current_time_ms: int | None = None
        self._parity: str = "even"
        self.cycle_total: int = 0
        self.cycle_cq:    int = 0
        self.current_mode: str = "FT8"
        self._sums: dict = {"even": [0, 0, 0], "odd": [0, 0, 0]}

    @staticmethod
    def _slot_parity(time_ms: int, mode: str) -> str:
        slot_ms = CycleTracker._SLOT_MS.get(mode, 15000)
        return "even" if (time_ms // slot_ms) % 2 == 0 else "odd"

    def _close_cycle(self) -> None:
        s = self._sums[self._parity]
        s[0] += self.cycle_total
        s[1] += self.cycle_cq
        s[2] += 1

    def record(self, time_ms: int, is_cq: bool, is_new: bool, mode: str) -> None:
        if not is_new:
            return
        if self.current_time_ms is not None and time_ms != self.current_time_ms:
            self._close_cycle()
            self.cycle_total = 0
            self.cycle_cq    = 0
        if self.current_time_ms is None or time_ms != self.current_time_ms:
            self.current_time_ms = time_ms
            self._parity      = self._slot_parity(time_ms, mode)
            self.current_mode = mode
        self.cycle_total += 1
        if is_cq:
            self.cycle_cq += 1
