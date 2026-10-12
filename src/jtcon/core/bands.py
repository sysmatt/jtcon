"""Amateur bands and US states."""


# All 50 US states recognised for WAS/5BWAS tracking.
_WAS_STATES: frozenset = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
})

# The five classic HF bands required for the 5-Band WAS award.
_5BWAS_BANDS: frozenset = frozenset({"80m", "40m", "20m", "15m", "10m"})


# (low Hz, high Hz, ADIF band) — ADIF 3.1 band edges.
_BANDS: tuple = (
    (  1_800_000,   2_000_000, "160m"),
    (  3_500_000,   4_000_000, "80m"),
    (  5_060_000,   5_450_000, "60m"),
    (  7_000_000,   7_300_000, "40m"),
    ( 10_100_000,  10_150_000, "30m"),
    ( 14_000_000,  14_350_000, "20m"),
    ( 18_068_000,  18_168_000, "17m"),
    ( 21_000_000,  21_450_000, "15m"),
    ( 24_890_000,  24_990_000, "12m"),
    ( 28_000_000,  29_700_000, "10m"),
    ( 50_000_000,  54_000_000, "6m"),
    ( 70_000_000,  71_000_000, "4m"),
    (144_000_000, 148_000_000, "2m"),
    (420_000_000, 450_000_000, "70cm"),
)


def _freq_to_band(hz: int) -> str | None:
    """Convert a dial frequency in Hz to an ADIF band string, or None."""
    for lo, hi, band in _BANDS:
        if lo <= hz < hi:
            return band
    return None
