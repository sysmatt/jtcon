"""What the Engine tells front ends.  Subscribers get these on the event loop."""

from dataclasses import dataclass

from jtcon.core.protocol import Status


@dataclass
class LogLine:
    """A message for the operator: startup, reloads, command results, errors."""
    text: str


@dataclass
class Ready:
    """Inputs are loaded and the UDP listener is running."""
    text: str


@dataclass
class Fatal:
    """Startup failed; the front end should show *text* and exit with status 1."""
    text: str


@dataclass
class SpotEvent:
    """A decode worth showing, after evaluation.

    *ctx* is the DecodeContext it was evaluated with (logbook, band, config) so a
    front end can render it as of that moment.  *station* is its rolling-buffer
    entry, or None for a non-CQ decode without hits (shown only with show = "all").
    """
    result:  object     # DecodeResult
    ctx:     object     # DecodeContext
    station: object     # Station | None


@dataclass
class StationUpdated:
    """A station in the rolling buffer was judged again, against a changed
    logbook or watchlist, from its newest decode.  Like SpotEvent, but nothing
    is new: no alert, and the front end updates the station rather than showing
    a new line."""
    result:  object     # DecodeResult
    ctx:     object     # DecodeContext
    station: object     # Station


@dataclass
class StatusChanged:
    """WSJT-X Status: TX/RX, dial frequency, operator call."""
    status: Status


@dataclass
class UdpDown:
    """The receive socket failed; no more decodes will arrive."""
    text: str


@dataclass
class InputReloaded:
    """An input (*name*: "adif", "callsigns", "message", "states") changed on disk
    and was loaded again."""
    name: str
