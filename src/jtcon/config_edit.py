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
from tomlkit.items import AoT, Whitespace

from jtcon.config import (CONFIG_TEMPLATE, Config, ConfigError, build_config, check_layer,
                          merge, replace_private)

BACKUPS_KEPT = 10       # dated copies of the file as it was before each save


def _put(table, key: str, value) -> None:
    """Set *key* in *table*: in its place if it's there (its comment kept),
    else after the table's last setting, before the blank line that ends it."""
    if key in table:
        if table[key] != value:
            table[key] = value
        return
    body = table.value.body
    trail = body.pop()[1] if body and body[-1][0] is None and isinstance(
        body[-1][1], Whitespace) else None
    table[key] = value
    if trail is not None:
        table.add(trail)


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
        _put(table, keys[-1], value)

    def remove(self, keys: tuple) -> None:
        table = self.doc
        for key in keys[:-1]:
            table = table.get(key)
            if not isinstance(table, dict):
                return
        if keys[-1] in table:
            del table[keys[-1]]

    # ── The [[wanted]] list ───────────────────────────────────────────────────

    def wanted(self) -> list | None:
        """The [[wanted]] entries as dicts, in order; None if the file has none."""
        entries = self.doc.get("wanted")
        if entries is None:
            return None
        return [dict(e.unwrap() if hasattr(e, "unwrap") else e) for e in entries]

    def set_wanted(self, entries: list) -> None:
        """Make the [[wanted]] list *entries*: [(origin, {"rule": …, …})], where
        origin is the entry's place in wanted() (it's changed in place, its
        comments kept) or None for a new one.  The comments after the last
        entry (the starter config's examples, say) stay at the end."""
        old = self.doc.get("wanted")
        tables = list(old) if isinstance(old, AoT) else []
        tail = []
        if tables:
            body = tables[-1].value.body
            keyed = [i for i, (key, _) in enumerate(body) if key is not None]
            cut = keyed[-1] + 1 if keyed else 0
            tail, body[cut:] = body[cut:], []
        new = []
        for origin, entry in entries:
            table = tables[origin] if origin is not None and origin < len(tables) else None
            if table is None:
                table = tomlkit.table()
            for key in [k for k in table if k not in entry]:
                del table[key]
            for key, value in entry.items():
                if table.get(key) != value:
                    _put(table, key, value)
            body = table.value.body         # one blank line between entries
            while body and body[-1][0] is None and isinstance(body[-1][1], Whitespace):
                body.pop()
            new.append(table)
        if not new:
            self.doc["wanted"] = []         # no rules at all
            return
        new[-1].value.body.extend(tail)
        aot = tomlkit.aot()
        for table in new:
            aot.append(table)
        self.doc["wanted"] = aot

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
