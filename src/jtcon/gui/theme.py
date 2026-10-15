"""Themes: dark (the default), light, or the system's own look.

Dark and light use Qt's Fusion style with jtcon's palettes, so they look the
same on Linux, Windows and macOS.  "system" leaves the platform's style and
palette alone (and follows the desktop's light or dark mode as far as the
platform does)."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QStyleFactory

THEMES = {"dark": "&Dark", "light": "&Light", "system": "&System"}

_DARK = {
    "Window": "#2b2d30", "WindowText": "#dfe1e5", "Base": "#1e1f22",
    "AlternateBase": "#26282b", "ToolTipBase": "#3c3f41", "ToolTipText": "#dfe1e5",
    "PlaceholderText": "#7a7e85", "Text": "#dfe1e5", "Button": "#3c3f41",
    "ButtonText": "#dfe1e5", "BrightText": "#ff6b68", "Light": "#4e5157",
    "Midlight": "#43454a", "Mid": "#323438", "Dark": "#1e1f22", "Shadow": "#111214",
    "Highlight": "#2f65ca", "HighlightedText": "#ffffff", "Link": "#589df6",
    "LinkVisited": "#a571e6",
}
_DARK_DISABLED = {"WindowText": "#6f737a", "Text": "#6f737a", "ButtonText": "#6f737a",
                  "Highlight": "#3d4148", "HighlightedText": "#a0a4ab"}

_original: dict = {}        # the platform's own style and palette, for "system"


def _palette(colours: dict, disabled: dict) -> QPalette:
    pal = QPalette()
    for role, colour in colours.items():
        pal.setColor(getattr(QPalette.ColorRole, role), QColor(colour))
    for role, colour in disabled.items():
        pal.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role),
                     QColor(colour))
    return pal


def apply_theme(app: QApplication, name: str) -> None:
    """Switch the whole application to theme *name* ("dark", "light", "system")."""
    if not _original:
        _original["style"] = app.style().name()
        _original["palette"] = QPalette(app.palette())
    if name == "system":
        app.setStyle(QStyleFactory.create(_original["style"]))
        app.setPalette(_original["palette"])
        return
    fusion = QStyleFactory.create("Fusion")
    app.setStyle(fusion)
    app.setPalette(_palette(_DARK, _DARK_DISABLED) if name == "dark"
                   else fusion.standardPalette())


def is_dark(app: QApplication) -> bool:
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def app_icon() -> QIcon:
    """jtcon's icon, drawn here until there's a real one: signal bars on a
    dark rounded square, the one a wanted station's red."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1e1f22"))
        p.drawRoundedRect(0, 0, size, size, size * 0.2, size * 0.2)
        bars = (0.35, 0.6, 0.85, 0.5)
        width = size * 0.14
        gap = (size - 4 * width) / 5
        for i, height in enumerate(bars):
            p.setBrush(QColor("#e5484d" if i == 2 else "#3fa66b"))
            x = gap + i * (width + gap)
            h = size * 0.7 * height
            p.drawRoundedRect(x, size * 0.85 - h, width, h, width * 0.3, width * 0.3)
        p.end()
        icon.addPixmap(pix)
    return icon
