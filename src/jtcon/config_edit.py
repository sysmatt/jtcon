"""Changing a config file in place, for the desktop window's settings editor.

tomlkit edits the file's own text, so its comments, order and layout stay as
they are and only the settings changed are touched.  Nothing is written until
the new file passes the same checks jtcon runs when it starts.  tomlkit comes
with jtcon[gui]; nothing else imports this module.
"""

import re
import tomllib
from datetime import datetime
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import Whitespace

from jtcon.config import (CONFIG_TEMPLATE, Config, ConfigError, build_config, check_layer,
                          merge, replace_private)

BACKUPS_KEPT = 10       # dated copies of the file as it was before each save


class ConfigFile:
    """The config file at *path* (the starter config's text if there's none
    yet), to read and change setting by setting, check, and save.

    A setting is named by its keys: ("udp", "port"), ("inputs", "qrz", "api_key").
    *base* is the layer this file goes on top of (a profile's main config), for
    the checks; None for the main config."""

    def __init__(self, path: Path, base: dict | None = None):
        self.path = Path(path)
        self.base = base
        self.existed = self.path.exists()
        try:
            text = self.path.read_text(encoding="utf-8") if self.existed else CONFIG_TEMPLATE
        except OSError as exc:
            raise ConfigError(f"{self.path}: {exc.strerror}") from None
        self.original = text
        try:
            self.doc = tomlkit.parse(text)
        except TOMLKitError as exc:
            raise ConfigError(f"{self.path}: {exc}") from None

    # ── Settings ──────────────────────────────────────────────────────────────

    def get(self, keys: tuple, default=None):
        """The setting's value in this file, or *default* if it isn't set here."""
        table = self.doc
        for key in keys[:-1]:
            table = table.get(key)
            if not isinstance(table, dict):
                return default
        value = table.get(keys[-1], default)
        return value.unwrap() if hasattr(value, "unwrap") else value

    def set(self, keys: tuple, value) -> None:
        """Set the setting, adding its tables if the file hasn't got them;
        None removes it."""
        if value is None:
            self.remove(keys)
            return
        table = self.doc
        for key in keys[:-1]:
            if key not in table:
                table[key] = tomlkit.table()
                table[key].add(tomlkit.nl())    # a blank line before the next table
            table = table[key]
        key = keys[-1]
        if key in table:
            if self.get(keys) != value:
                table[key] = value              # in its place, its comment kept
            return
        # A new setting goes after the table's last one, before the blank line
        # that ends it.
        body = table.value.body
        trail = body.pop()[1] if body and body[-1][0] is None and isinstance(
            body[-1][1], Whitespace) else None
        table[key] = value
        if trail is not None:
            table.add(trail)

    def remove(self, keys: tuple) -> None:
        table = self.doc
        for key in keys[:-1]:
            table = table.get(key)
            if not isinstance(table, dict):
                return
        if keys[-1] in table:
            del table[keys[-1]]

    def text(self) -> str:
        return tomlkit.dumps(self.doc)

    @property
    def changed(self) -> bool:
        return not self.existed or self.text() != self.original

    # ── Checking and saving ───────────────────────────────────────────────────

    def check(self) -> Config:
        """The Config this file makes (on top of *base*); ConfigError if it
        can't be used, with the message --show-config would give."""
        try:
            raw = tomllib.loads(self.text())
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{self.path}: {exc}") from None
        try:
            layer = check_layer(raw, self.path.parent)
            return build_config(merge(self.base, layer) if self.base is not None else layer)
        except ConfigError as exc:
            raise ConfigError(f"{self.path}: {exc}") from None

    def save(self) -> Path | None:
        """Check, then write the file, keeping the old one as a dated copy
        beside it (config.toml.20261015-142233.bak; the newest BACKUPS_KEPT).
        All private (0600).  The copy made, if any; False if there was
        nothing to write."""
        self.check()
        if not self.changed:
            return False
        backup = None
        if self.existed:
            backup = self._backup_path()
            replace_private(backup, self.original)
            for old in self.backups()[:-BACKUPS_KEPT]:
                old.unlink(missing_ok=True)
        replace_private(self.path, self.text())
        self.original, self.existed = self.text(), True
        return backup or None

    def backups(self) -> list:
        """The dated copies, oldest first."""
        pattern = re.compile(re.escape(self.path.name) + r"\.\d{8}-\d{6}(-\d+)?\.bak")
        found = [p for p in self.path.parent.glob(self.path.name + ".*.bak")
                 if pattern.fullmatch(p.name)]
        return sorted(found, key=lambda p: (p.stat().st_mtime_ns, p.name))

    def _backup_path(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path, n = self.path.with_name(f"{self.path.name}.{stamp}.bak"), 1
        while path.exists():            # two saves in one second
            n += 1
            path = self.path.with_name(f"{self.path.name}.{stamp}-{n}.bak")
        return path
