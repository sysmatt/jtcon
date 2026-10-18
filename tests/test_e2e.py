"""End-to-end: run the real ./jtcon in a pseudo-terminal with a throwaway HOME,
feed it UDP packets like WSJT-X, type commands, and check what comes back.

Black-box on purpose: it doesn't import jtcon, so it keeps guarding behaviour
while the code is reorganized.  POSIX only (needs a pty).
"""

import json
import os
import re
import select
import shutil
import signal
import site
import socket
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import wsjtx_packets as pk
from conftest import CTY_PATH, LOG_PATH, make_hamdat_db

pty = pytest.importorskip("pty")
fcntl = pytest.importorskip("fcntl")
termios = pytest.importorskip("termios")

REPO     = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "jtcon"
ROWS, COLS = 60, 250

_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][0-9A-Za-z]|\x1b[=>78]|\r")


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(home: Path, **extra) -> dict:
    env = dict(os.environ)
    env.pop("XDG_CONFIG_HOME", None)        # config comes from HOME/.config only
    env.pop("VISUAL", None)                 # --edit-config: tests choose the editor
    env.pop("EDITOR", None)
    env.update(HOME=str(home), TERM="xterm", COLUMNS=str(COLS), LINES=str(ROWS),
               PYTHONUSERBASE=site.getuserbase(),   # keep user-installed packages
               **extra)
    return env


class JtconProcess:
    """./jtcon running in a pty; collects everything it draws."""

    def __init__(self, args, env: dict):
        self._raw = b""
        self._lock = threading.Lock()
        self.pid, self.fd = pty.fork()
        if self.pid == 0:   # child
            os.execve(sys.executable, [sys.executable, str(LAUNCHER), *args], env)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        self.status = None
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            try:
                r, _, _ = select.select([self.fd], [], [], 0.1)
                if r:
                    chunk = os.read(self.fd, 65536)
                    if not chunk:
                        return
                    with self._lock:
                        self._raw += chunk
            except OSError:
                return

    def screen(self) -> str:
        """Everything drawn so far, with terminal control codes removed."""
        with self._lock:
            raw = self._raw
        return _ESCAPES.sub("", raw.decode("utf-8", "replace"))

    def wait_for(self, pattern: str, timeout: float = 10) -> re.Match:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            m = re.search(pattern, self.screen())
            if m:
                return m
            if self.poll() is not None:
                break
            time.sleep(0.05)
        tail = "\n".join(self.screen().splitlines()[-40:])
        raise AssertionError(f"timed out waiting for {pattern!r}; screen tail:\n{tail}")

    def type(self, text: str):
        for ch in text:
            os.write(self.fd, ch.encode())
            time.sleep(0.01)

    def poll(self):
        if self.status is None:
            pid, st = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.status = os.waitstatus_to_exitcode(st)
        return self.status

    def wait_exit(self, timeout: float = 10) -> int:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.poll() is not None:
                return self.status
            time.sleep(0.05)
        raise AssertionError("jtcon did not exit")

    def kill(self):
        if self.poll() is None:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        os.close(self.fd)


@pytest.fixture
def home(tmp_path):
    """A HOME with the files jtcon auto-discovers: cty.dat, WSJT-X log, hamdat."""
    h = tmp_path / "home"
    h.mkdir()
    shutil.copy(CTY_PATH, h / ".jtcon_cty.dat")    # fresh mtime: no refresh
    log_dir = h / ".local" / "share" / "WSJT-X"
    log_dir.mkdir(parents=True)
    shutil.copy(LOG_PATH, log_dir / "wsjtx_log.adi")
    (h / ".hamdat").mkdir()
    make_hamdat_db(h / ".hamdat" / "hamdat.db")
    return h


def write_config(home: Path, text: str, name: str = "config.toml") -> Path:
    path = home / ".config" / "jtcon" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def launch(home):
    procs = []

    def _launch(*args, **env):
        p = JtconProcess(args, _env(home, **env))
        procs.append(p)
        return p

    yield _launch
    for p in procs:
        p.kill()


@pytest.fixture
def wsjtx():
    """A UDP socket playing WSJT-X: sends packets, receives jtcon's replies."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(5)
    yield s
    s.close()


def test_live_session(launch, home, wsjtx, tmp_path):
    port = _free_udp_port()
    watch = tmp_path / "watch.txt"
    watch.write_text("# watchlist\n^VK\n^ZL\n")
    saved = tmp_path / "cq.jsonl"
    config = write_config(home, f"""
        [udp]
        host = "127.0.0.1"
        [scope]
        decodes = "all"
        [[wanted]]
        rule = "dxcc"
        [[wanted]]
        rule = "cqz"
        [[wanted]]
        rule = "country"
        [[wanted]]
        rule = "was"
        [[wanted]]
        rule = "pota"
        [[wanted]]
        rule = "callsigns"
        files = "{watch}"
        """)
    app = launch("--port", str(port), "--save", str(saved))   # CLI overrides
    jt = ("127.0.0.1", port)

    # ── Startup: everything auto-discovered from HOME ─────────────────────────
    app.wait_for(rf"\[ready\] Listening on 127\.0\.0\.1:{port}  \(CQ \+ matched\)")
    s = app.screen()
    assert "[cty.dat] Loaded 4798 prefixes + 2708 exact callsigns" in s
    assert f"[adif] Auto-discovered: {home}/.local/share/WSJT-X/wsjtx_log.adi" in s
    assert f"[config] Loaded {config}" in s
    assert "[config] Wanted rules: dxcc, cqz, country, was, pota, callsigns" in s
    assert "[adif] 9 QSOs across 1 file(s) — 8 calls" in s
    assert "[dxcc] 5 worked, 2 confirmed (lotw)" in s
    assert "[was] 4 worked, 2 confirmed (lotw)" in s
    assert f"[hamdat] Auto-detected: {home}/.hamdat/hamdat.db" in s
    assert f"[callsigns] 2 pattern(s) from {watch}" in s
    assert "[pota] POTA in the message" in s
    assert f"[save] Appending JSON records to: {saved}" in s
    assert "downloading" not in s                      # fresh cty.dat: no refresh

    # ── Status, then decodes ──────────────────────────────────────────────────
    wsjtx.sendto(pk.status(14_074_000, de_call="N0CALL"), jt)
    app.wait_for(r"de=N0CALL")
    for i, msg in enumerate(("CQ DX VK3XY QF22", "CQ W1ABC FN42",
                             "CQ POTA K2XYZ FN20", "CQ N1NEW FN43")):
        wsjtx.sendto(pk.decode(msg, time_ms=45_015_000, df=1000 + i), jt)
    wsjtx.sendto(pk.decode("W1ABC ZL2AB RF70", time_ms=45_015_000), jt)   # --all: directed hit
    wsjtx.sendto(pk.decode("W1ABC W9ZZZ -05", time_ms=45_015_000), jt)    # --all: no hit, hidden
    app.wait_for(r"00  123015z.{0,60}CQ DX VK3XY QF22")
    app.wait_for(r"\*\*\* WANTED: ❌DXCC\(VK\) · ❌CQZ\(30\) · ❌CTRY\(Australia\)"
                 r" · CALLS\(\^VK \[watch\.txt\]\) \*\*\*")
    app.wait_for(r"01  123015z.{0,60}CQ W1ABC FN42")
    app.wait_for(r"\*\*\* WANTED: ❓WAS\(NJ\) · POTA \*\*\*")      # NJ worked, unconfirmed
    app.wait_for(r"\*\*\* WANTED: ❌WAS\(NH\) \*\*\*")
    app.wait_for(r"04  123015z.{0,60}W1ABC ZL2AB RF70")
    assert "W1ABC W9ZZZ -05" not in app.screen()

    # ── Wanted stations are highlighted in WSJT-X ─────────────────────────────
    orange = ((255, 165, 0), (0, 0, 0))
    assert [highlight(wsjtx) for _ in range(4)] == [
        ("VK3XY", *orange), ("K2XYZ", *orange), ("N1NEW", *orange), ("ZL2AB", *orange)]

    # ── Replies: by callsign and by index ─────────────────────────────────────
    app.type("vk3xy\r")
    data, _ = wsjtx.recvfrom(4096)
    reply = pk.parse_reply(data)
    assert (reply["type"], reply["message"], reply["df"]) == (4, "CQ DX VK3XY QF22", 1000)
    app.wait_for(r"\[cmd\] ► Reply sent to VK3XY \[00\]  msg='CQ DX VK3XY QF22'")

    app.type("1\r")
    assert pk.parse_reply(wsjtx.recvfrom(4096)[0])["message"] == "CQ W1ABC FN42"
    app.wait_for(r"Reply sent to W1ABC \[01\]")

    app.type("4\r")
    assert pk.parse_reply(wsjtx.recvfrom(4096)[0])["message"] == "W1ABC ZL2AB RF70"

    # ── QSO Logged: that station's highlight is cleared ───────────────────────
    # Logged today (UTC): "recently worked" whenever the tests run.
    today = datetime.now(timezone.utc).replace(hour=12, minute=30, second=0, microsecond=0,
                                               tzinfo=None)
    wsjtx.sendto(pk.qso_logged("K2XYZ", when=today), jt)
    app.wait_for(r"\[qso\] Logged K2XYZ")
    assert highlight(wsjtx) == ("K2XYZ", None, None)

    # ── list: K2XYZ was just worked, so it's no longer listed ─────────────────
    app.type("list\r")
    m = app.wait_for(r"\[list\] 3 wanted \(last heard at bottom\)")
    app.wait_for(r"\(last heard \d+s ago, ×1\)")
    time.sleep(0.2)
    listed = app.screen()[m.end():]
    assert "VK3XY" in listed and "K2XYZ" not in listed

    # ── ADIF hot reload: WSJT-X writes K2XYZ's QSO (merged with the one it
    #    reported), and a LoTW-confirmed VK3XY QSO arrives: no longer needed ──
    adif = home / ".local" / "share" / "WSJT-X" / "wsjtx_log.adi"
    with adif.open("a") as f:
        f.write(f"<call:5>K2XYZ <band:3>20m <mode:3>FT8 <qso_date:8>{today:%Y%m%d}"
                " <time_on:6>123000 <EOR>\n")
        f.write("<call:5>VK3XY <band:3>20m <mode:3>FT8 <qso_date:8>20240101"
                " <lotw_qsl_rcvd:1>Y <EOR>\n")
    wsjtx.sendto(pk.decode("CQ VK3XY QF22", time_ms=45_030_000), jt)
    app.wait_for(r"\[adif\] Reloaded 11 QSOs from 1 file\(s\)\.")
    app.wait_for(r"00  123030z.{0,60}CQ VK3XY QF22")     # same station keeps index 00

    # ── Dial change clears the buffer ─────────────────────────────────────────
    wsjtx.sendto(pk.status(7_074_000, de_call="N0CALL"), jt)
    app.wait_for(r"\[buffer\] Dial frequency changed — cleared 5 entries\.")
    assert sorted(highlight(wsjtx) for _ in range(3)) == [
        ("N1NEW", None, None), ("VK3XY", None, None), ("ZL2AB", None, None)]
    app.type("VK3XY\r")
    app.wait_for(r"\[cmd\] VK3XY not found in rolling buffer\.")

    # ── Quit ──────────────────────────────────────────────────────────────────
    app.type("quit\r")
    assert app.wait_exit() == 0

    records = [json.loads(l) for l in saved.read_text().splitlines()]
    assert [r["callsign"] for r in records] == ["VK3XY", "W1ABC", "K2XYZ", "N1NEW", "VK3XY"]
    assert [(w["rule"], w["status"]) for w in records[0]["wanted"]] == [
        ("DXCC", "new"), ("CQZ", "new"), ("CTRY", "new"), ("CALLS", None)]
    # Confirmed after the reload: only the watchlist still wants it.
    assert [w["rule"] for w in records[-1]["wanted"]] == ["CALLS"]


def highlight(sock) -> tuple:
    h = pk.parse_highlight(sock.recvfrom(4096)[0])
    assert (h["type"], h["id"], h["last_only"], h["trailing"]) == (13, "WSJT-X", False, 0)
    return h["callsign"], h["background"], h["foreground"]


def test_tx_status_and_ctrl_q(launch, wsjtx):
    port = _free_udp_port()
    app = launch("--port", str(port))
    app.wait_for(r"\[ready\] Listening on 0\.0\.0\.0:\d+  \(CQ calls only\)")
    wsjtx.sendto(pk.status(14_074_000, transmitting=True), ("127.0.0.1", port))
    app.wait_for(r" TX ")      # only changed cells are redrawn, so match little
    app.type("\x11")                     # Ctrl-Q quits
    assert app.wait_exit() == 0


def test_fatal_startup_error_exits_1(launch, home, tmp_path):
    missing = tmp_path / "nope.adi"
    write_config(home, f'[inputs]\nadif = ["{missing}"]\n')
    app = launch("--port", str(_free_udp_port()))
    assert app.wait_exit() == 1
    assert f"[adif] File not found: {missing}" in app.screen()


def test_stale_cty_is_refreshed(launch, home):
    old = time.time() - 40 * 86400
    os.utime(home / ".jtcon_cty.dat", (old, old))
    # A dead proxy makes the download fail fast, without touching the network.
    app = launch("--port", str(_free_udp_port()),
                 https_proxy="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9")
    app.wait_for(r"\[cty\.dat\] older than 30 days — downloading from "
                 r"https://www\.country-files\.com/cty/cty\.dat")
    app.wait_for(r"\[cty\.dat\] Download failed: .* — using cached copy")
    app.wait_for(r"\[cty\.dat\] Loaded 4798 prefixes")
    app.wait_for(r"\[ready\] Listening")
    app.type("quit\r")
    assert app.wait_exit() == 0


def _run_cli(*args, home, **env):
    return subprocess.run([sys.executable, str(LAUNCHER), *args], capture_output=True,
                          text=True, env=_env(home, **env), timeout=30)


def test_cli_version(home):
    r = _run_cli("--version", home=home)
    assert r.returncode == 0
    assert re.fullmatch(r"jtcon \S+\n", r.stdout)


def test_cli_help(home):
    r = _run_cli("--help", home=home)
    assert r.returncode == 0
    assert r.stdout.startswith("usage: jtcon ")
    for flag in ("--config", "--profile", "--wanted", "--scope", "--show-config",
                 "--init-config", "--edit-config", "--list-rules", "--save", "--mouse",
                 "--no-color", "--ui"):
        assert flag in r.stdout
    assert "jtcon --wanted dxcc,was,pota" in r.stdout
    assert 'states = ["AK", "DE"]' in r.stdout
    for gone in ("--adif", "--qsl-only", "--alert-ntfy", "--5bwas"):
        assert gone not in r.stdout


def test_cli_v2_flag_is_refused(home):
    r = _run_cli("--qsl-only", home=home)
    assert r.returncode == 2
    assert "unrecognized arguments: --qsl-only" in r.stderr


def test_cli_list_rules(home):
    r = _run_cli("--list-rules", home=home)
    assert r.returncode == 0
    assert "5bwas" in r.stdout and "calls: default []" in r.stdout
    assert 'files = ["~/ham/wanted.txt", "~/ham/club.txt"]' in r.stdout
    assert r"\bPOTA\b     POTA as a word" in r.stdout
    assert "jtcon --wanted dxcc,callsigns:~/ham/wanted.txt+~/ham/club.txt" in r.stdout


def test_cli_init_config_then_show_config(home):
    r = _run_cli("--init-config", home=home)
    path = home / ".config" / "jtcon" / "config.toml"
    assert r.returncode == 0 and r.stdout == f"Wrote {path}\n"
    assert _run_cli("--init-config", home=home).returncode == 1   # won't overwrite

    r = _run_cli("--show-config", "--wanted", "was,pota", "--scope", "all", home=home)
    assert r.returncode == 0
    assert f"# from: {path}" in r.stdout
    assert 'decodes = "all"' in r.stdout
    assert '[[wanted]]\nrule = "was"\nname = "was"\nconfirm = ["lotw"]' in r.stdout
    assert '[[wanted]]\nrule = "pota"\nname = "pota"' in r.stdout
    assert 'rule = "dxcc"' not in r.stdout


# An "editor" that appends its first argument's text to the file it's given.
_APPEND = "sh -c 'printf \"%s\" \"$0\" >> \"$1\"'"


def test_cli_edit_config_needs_an_editor(home):
    r = _run_cli("--edit-config", home=home)
    assert r.returncode == 1
    assert r.stderr == "jtcon: set VISUAL or EDITOR to your editor first, e.g.  export EDITOR=nano\n"
    r = _run_cli("--edit-config", home=home, EDITOR="no-such-editor-xyz")
    assert r.returncode == 1 and "can't run the editor 'no-such-editor-xyz'" in r.stderr


def test_cli_edit_config_makes_a_starter_file_then_checks_it(home):
    path = home / ".config" / "jtcon" / "config.toml"
    r = _run_cli("--edit-config", home=home, EDITOR="true")
    assert r.returncode == 0
    assert r.stdout == f"Wrote a starter config: {path}\nconfig OK: {path}\n"
    assert (path.stat().st_mode & 0o777) == 0o600
    # VISUAL wins over EDITOR.
    r = _run_cli("--edit-config", home=home, VISUAL="true", EDITOR="false")
    assert r.returncode == 0 and r.stderr == ""          # "false" would have complained
    r = _run_cli("--edit-config", home=home, EDITOR="false")
    assert r.stderr == "jtcon: the editor exited with status 1\n"
    r = _run_cli("--edit-config", home=home, EDITOR=f"{_APPEND} '\nbogus = 1\n'")
    assert r.returncode == 2
    assert "jtcon: config error:" in r.stderr and "bogus" in r.stderr
    assert r.stderr.endswith("Fix it with:  jtcon --edit-config\n")


def test_cli_edit_config_profile_starts_as_a_copy(home):
    write_config(home, '[udp]\nport = 2240\n[inputs.qrz]\nenabled = true\n'
                       '  api_key = "SECRET-KEY-1234"\n')
    profile = home / ".config" / "jtcon" / "profiles" / "parks.toml"
    r = _run_cli("--edit-config", "--profile", "parks", home=home, EDITOR="true")
    assert r.returncode == 0
    assert r.stdout == (f"Made profile {profile} from config.toml\nconfig OK: {profile}\n")
    text = profile.read_text()
    assert text.startswith('# Profile "parks": copied from config.toml on ')
    assert "Keep only what you want to be\n# different, and delete the rest" in text
    assert "SECRET-KEY" not in text
    assert '  # api_key: set in config.toml, not copied here\n' in text
    assert "port = 2240" in text
    assert (profile.stat().st_mode & 0o777) == 0o600
    r = _run_cli("--show-config", "--profile", "parks", home=home)
    assert r.returncode == 0 and 'api_key = "****1234"' in r.stdout   # still from config.toml

    # An existing profile is opened as it is, not copied again.
    profile.write_text("[udp]\nport = 2241\n")
    r = _run_cli("--edit-config", "--profile", "parks", home=home, EDITOR="true")
    assert r.stdout == f"config OK: {profile}\n" and profile.read_text() == "[udp]\nport = 2241\n"


def test_cli_profile(home):
    write_config(home, '[udp]\nport = 2240\n[[wanted]]\nrule = "dxcc"\n')
    write_config(home, '[[wanted]]\nrule = "pota"\n', name="profiles/parks.toml")
    r = _run_cli("--profile", "parks", "--show-config", home=home)
    assert r.returncode == 0
    assert "port = 2240" in r.stdout                 # from the base config
    assert 'rule = "pota"' in r.stdout and 'rule = "dxcc"' not in r.stdout


def test_cli_config_error_exits_2(home):
    path = write_config(home, '[[wanted]]\nrule = "was"\n[[wanted]]\nrule = "was"\n')
    r = _run_cli(home=home)
    assert r.returncode == 2
    assert r.stderr == ("jtcon: config error: rule 'was' is used twice; give each [[wanted]]"
                        ' entry its own name, e.g. name = "WAS-QRZ"\n')
    path.write_text("[udp]\nprot = 1\n")
    r = _run_cli(home=home)
    assert r.returncode == 2
    assert f"{path}: unknown setting 'udp.prot'" in r.stderr
