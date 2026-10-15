"""Start-up: one event loop (qasync) runs Qt, the window and the Engine."""

import asyncio
import signal
import sys

import qasync
from PySide6.QtWidgets import QApplication

from jtcon.core.engine import Engine
from jtcon.gui.window import MainWindow


class JtconGui:
    """Runs the Engine and the window together."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.window: MainWindow | None = None

    async def run_async(self) -> int:
        closed = asyncio.get_running_loop().create_future()
        self.window = window = MainWindow(self.engine)
        window.closing.connect(lambda: closed.done() or closed.set_result(None))
        # Ctrl-C in the terminal closes the window as Ctrl-Q does.
        signal.signal(signal.SIGINT, lambda *_: window.close())
        window.show()
        window.start()
        try:
            await closed
        finally:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            self.engine.stop()
            if window.engine_task is not None:
                try:
                    await window.engine_task
                except Exception:
                    pass                # reported by MainWindow._engine_done
            self.engine.retry_uploads_at_exit(print)
            self.engine.close()
        if window.fatal_msg:
            print(window.fatal_msg, file=sys.stderr)
            return 1
        return 0

    def run(self) -> int:
        app = QApplication.instance() or QApplication(sys.argv[:1])
        app.setApplicationName("jtcon")
        app.setOrganizationName("jtcon")
        app.setQuitOnLastWindowClosed(False)    # run_async finishes up after it closes
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
        try:
            with loop:
                return loop.run_until_complete(self.run_async())
        finally:
            asyncio.set_event_loop(None)
