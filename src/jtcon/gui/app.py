"""Start-up: one event loop (qasync) runs Qt, the window and the Engine.

File › Reload config restarts in the window: the config is read again (the
command line still winning), and a new Engine and window take over from the
old ones, which close.  The window comes back as it was (window.ini)."""

import asyncio
import signal
import sys

import qasync
from PySide6.QtWidgets import QApplication

from jtcon.config import ConfigError
from jtcon.core.engine import Engine
from jtcon.gui.theme import app_icon
from jtcon.gui.window import MainWindow


class JtconGui:
    """Runs the Engine and the window together.  *startup* is how jtcon was
    started, to read the config again the same way (None: no reloading, no
    settings editor)."""

    def __init__(self, engine: Engine, startup=None):
        self.engine = engine
        self.startup = startup
        self.window: MainWindow | None = None
        self._next = None               # (config, socket) to restart with

    async def run_async(self) -> int:
        log = None
        while True:
            closed = asyncio.get_running_loop().create_future()
            self.window = window = MainWindow(
                self.engine, reload=self.reload_config if self.startup else None, log=log,
                startup=self.startup)
            window.closing.connect(lambda: closed.done() or closed.set_result(None))
            # Ctrl-C in the terminal closes the window as Ctrl-Q does.
            signal.signal(signal.SIGINT, lambda *_: window.close())
            window.show()
            window.start()
            try:
                await closed
            finally:
                signal.signal(signal.SIGINT, signal.default_int_handler)
                await self._stop_engine(window)
            if self._next is None:
                break
            log = window.log_view.lines()
            self.engine = self._restarted(*self._next)
            self._next = None
            window.deleteLater()
        self.engine.retry_uploads_at_exit(print)
        self.engine.close()
        if window.fatal_msg:
            print(window.fatal_msg, file=sys.stderr)
            return 1
        return 0

    async def _stop_engine(self, window: MainWindow) -> None:
        self.engine.stop()
        if window.engine_task is not None:
            try:
                await window.engine_task
            except Exception:
                pass                    # reported by MainWindow._engine_done

    # ── Reloading ─────────────────────────────────────────────────────────────

    def reload_config(self, startup=None) -> str | None:
        """Read the config again and restart with it; or why not (jtcon then
        carries on as it was).  *startup*: start that way from now on instead
        (another profile, from the Profile menu)."""
        startup = startup or self.startup
        try:
            config = startup.load()
        except ConfigError as exc:
            return f"Config error: {exc}"
        from jtcon.cli import bind_udp
        try:
            sock = bind_udp(config)
        except OSError as exc:
            return f"Can't listen on {config.udp.host}:{config.udp.port}: {exc.strerror or exc}"
        self.startup = startup
        self._next = (config, sock)
        self.window.close()
        return None

    def _restarted(self, config, sock) -> Engine:
        """The new Engine, with what this session changed: the stations
        ignored, the rules switched off."""
        old = self.engine
        old.close()                     # releases the old socket, files, highlights
        engine = self.new_engine(config, sock, old.tx_sock)
        for call, source in old.ignored.items():
            if source == "session":
                engine.ignored.setdefault(call, source)
        names = {w.name for w in config.wanted}
        config.rules_off.update(old.config.rules_off & names)
        return engine

    def new_engine(self, config, sock, tx_sock) -> Engine:
        return Engine(config, sock, tx_sock)

    def run(self) -> int:
        app = QApplication.instance() or QApplication(sys.argv[:1])
        app.setApplicationName("jtcon")
        app.setOrganizationName("jtcon")
        app.setQuitOnLastWindowClosed(False)    # run_async finishes up after it closes
        app.setDesktopFileName("jtcon")
        app.setWindowIcon(app_icon())
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
        try:
            with loop:
                return loop.run_until_complete(self.run_async())
        finally:
            asyncio.set_event_loop(None)
