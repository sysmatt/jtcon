"""The desktop front end (--ui gui), on Qt through PySide6.  See docs/gui-plan.md.

PySide6, qasync and tomlkit (for the settings editor) come with the optional
install, jtcon[gui]; nothing else in jtcon imports them.
"""

import importlib

INSTALL_HINT = """\
Add them with:  pipx install --force "jtcon[gui] @ git+https://github.com/sysmatt/jtcon"
(from a clone:  pip install -e ".[gui]")"""


def missing() -> str | None:
    """Why the GUI can't run here, or None when it can."""
    try:
        for module in ("PySide6.QtWidgets", "qasync", "tomlkit"):
            importlib.import_module(module)
    except ImportError as exc:      # not installed, or a system library is missing
        return (f"jtcon: the desktop window needs PySide6, qasync and tomlkit, which the plain "
                f"install leaves out ({exc}).\n{INSTALL_HINT}")
    return None
