"""CMD> commands, shared by the front ends.

A front end makes a Commands with its Engine and three callbacks, and passes
each line typed at CMD> to handle().
"""

import re
import time

from jtcon.core.messages import _CALLSIGN_RE
from jtcon.core.stations import fmt_age
from jtcon.tui.render import _DIM, _col

# Commands and the argument each takes ("": none; "[...]": optional).  Any
# unique abbreviation works; a word with a digit is a callsign, never a command.
COMMANDS = {
    "quit":     "",
    "exit":     "",
    "clear":    "",
    "list":     "",
    "ignore":   "[NN|CALL]",
    "unignore": "CALL",
    "sync":     "[full]",
}


def match_command(word: str) -> list:
    """The commands *word* abbreviates: one, several (ambiguous) or none."""
    word = word.lower()
    if word in COMMANDS:
        return [word]
    if any(c.isdigit() for c in word):
        return []
    return [name for name in COMMANDS if name.startswith(word)]


class Commands:
    """
    Runs CMD> lines against *engine*.  *out(text)* shows a line of command
    output; *quit()* and *clear()* are the front end's own.  *listing(text)*,
    if given, takes the decode rows that "list" prints (else *out*).
    """

    def __init__(self, engine, out, quit, clear, listing=None):
        self.engine, self.out, self.quit, self.clear = engine, out, quit, clear
        self.listing = listing or out

    def handle(self, raw: str) -> None:
        raw = raw.strip()
        if not raw:
            return
        word, _, arg = raw.partition(" ")
        arg = arg.strip()
        names = match_command(word)

        if re.fullmatch(r"\d{1,2}", raw):
            idx = int(raw)
            station = self.engine.stations.get(idx)
            if station:
                self.engine.reply(station)
            else:
                self.out(f"[cmd] No CQ at index {idx:02d}.")

        elif len(names) > 1:
            self.out(f"[cmd] {word!r} is ambiguous: {', '.join(names)}.")

        elif names:
            name = names[0]
            takes = COMMANDS[name]
            if arg and not takes:
                self.out(f"[cmd] {name} takes no argument.")
            elif not arg and takes and not takes.startswith("["):
                self.out(f"[cmd] usage: {name} {takes}")
            else:
                getattr(self, f"_cmd_{name}")(arg)

        elif _CALLSIGN_RE.match(raw):
            station = self.engine.stations.find(raw.upper())
            if station:
                self.engine.reply(station)
            else:
                self.out(f"[cmd] {raw.upper()} not found in rolling buffer.")

        else:
            commands = ", ".join(f"{n} {a}".rstrip() for n, a in COMMANDS.items())
            self.out(f"[cmd] Unknown: {raw!r}  (commands: {commands}; any unique abbreviation"
                     " works; or enter index 00-99 / callsign to reply)")

    def _cmd_quit(self, arg: str) -> None:
        self.quit()

    _cmd_exit = _cmd_quit

    def _cmd_clear(self, arg: str) -> None:
        self.clear()

    def _cmd_ignore(self, arg: str) -> None:
        """ignore NN / ignore CALL: ignore for this session; ignore: list them."""
        if not arg:
            self._show_ignored()
            return
        if re.fullmatch(r"\d{1,2}", arg):
            station = self.engine.stations.get(int(arg))
            if station is None:
                self.out(f"[cmd] No station at index {int(arg):02d}.")
                return
            arg = station.callsign
        if self._is_call(arg, "ignore"):
            self.engine.ignore(arg)

    def _cmd_sync(self, arg: str) -> None:
        """sync: fetch what changed in the online logbook and retry waiting
        uploads now; sync full: a fresh full download."""
        if arg.lower() not in ("", "full"):
            self.out(f"[cmd] usage: sync {COMMANDS['sync']}")
            return
        self.engine.sync(full=bool(arg))

    def _cmd_unignore(self, arg: str) -> None:
        if self._is_call(arg, "unignore"):
            self.engine.unignore(arg)

    def _is_call(self, arg: str, name: str) -> bool:
        if _CALLSIGN_RE.match(arg.strip("<>")):
            return True
        self.out(f"[cmd] {arg!r} is not a callsign (usage: {name} {COMMANDS[name]}).")
        return False

    def _show_ignored(self) -> None:
        ignored = self.engine.ignored
        if not ignored:
            self.out("[ignore] Nothing ignored.  ignore NN or ignore CALL ignores a station.")
            return
        self.out(f"[ignore] {len(ignored)} ignored ——")
        for source in ("config", "session"):
            calls = [c for c, s in ignored.items() if s == source]
            if calls:
                self.out(f"  {source + ':':<9} {' '.join(calls)}")

    def _cmd_list(self, arg: str = "") -> None:
        """Print the wanted stations in the rolling buffer, most recently heard last."""
        hits = self.engine.stations.hits()
        if not hits:
            self.out("[list] No wanted stations in the rolling buffer.")
            return
        now = time.monotonic()
        color = self.engine.config.display.color
        self.listing(f"[list] {len(hits)} wanted (last heard at bottom) ——")
        for st in hits:
            lines = st.display or [st.callsign]
            # Keep the decode row aligned with the column header; the heard
            # stats go on the WANTED line below it.
            seen = _col(f"(last heard {fmt_age(now - st.last_seen)} ago, ×{st.count})",
                        _DIM, color)
            self.listing(f"{st.idx:02d}  {lines[0]}")
            if len(lines) > 1:
                self.listing(f"{lines[1]}  {seen}")
            else:
                self.listing(f"  {seen}")
            for line in lines[2:]:
                self.listing(line)
