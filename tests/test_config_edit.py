"""Changing a config file in place (the window's settings editor): comments
kept, only changed settings touched, checked before writing, a backup."""

import re
import tomllib

import pytest

pytest.importorskip("tomlkit")

from jtcon.config import CONFIG_TEMPLATE, ConfigError, load_config
from jtcon.config_edit import ConfigFile

MINE = """\
# My shack's config.
[udp]
port = 2238   # WSJT-X sends here

[display]
# show = "cq"
ui = "gui"
"""


def test_a_change_keeps_the_comments_and_everything_else(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(MINE)
    f = ConfigFile(path)
    assert f.get(("udp", "port")) == 2238 and f.get(("udp", "host")) is None
    assert f.get(("udp", "host"), "0.0.0.0") == "0.0.0.0"
    f.set(("display", "theme"), "light")
    f.set(("udp", "port"), 2239)
    assert f.text() == MINE.replace("2238", "2239") + 'theme = "light"\n'
    f.set(("udp", "host"), "127.0.0.1")           # after the table's last setting
    assert '# WSJT-X sends here\nhost = "127.0.0.1"\n\n[display]' in f.text()
    assert f.changed


def test_setting_the_same_value_changes_nothing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(MINE)
    f = ConfigFile(path)
    f.set(("udp", "port"), 2238)
    f.set(("display", "show"), None)            # not set: nothing to remove
    assert f.text() == MINE and not f.changed
    assert f.save() is False
    assert f.backups() == []


def test_new_tables_and_removing(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(MINE)
    f = ConfigFile(path)
    f.set(("inputs", "qrz", "enabled"), True)
    f.set(("filters", "ignore"), ["W1AW", "K1ABC"])
    f.remove(("display", "ui"))
    data = tomllib.loads(f.text())
    assert data["inputs"] == {"qrz": {"enabled": True}}
    assert data["filters"] == {"ignore": ["W1AW", "K1ABC"]}
    assert data["display"] == {}
    assert "# My shack's config." in f.text() and '# show = "cq"' in f.text()


def test_the_starter_config(tmp_path):
    """No file yet: the starter config's text, with the setting added in its
    table; the commented-out [inputs.qrz] becomes a real one under [inputs]."""
    path = tmp_path / "config.toml"
    f = ConfigFile(path)
    assert not f.existed and f.changed
    f.set(("udp", "port"), 2240)
    f.set(("inputs", "qrz", "enabled"), True)
    text = f.text()
    assert text.startswith(CONFIG_TEMPLATE.split("[udp]")[0])
    assert text.index("port = 2240") < text.index("[display]")
    assert "\n[inputs.qrz]\nenabled = true\n\n[filters]\n" in text
    assert f.save() is None                               # nothing to back up
    assert f.backups() == []
    cfg = load_config(path)
    assert cfg.udp.port == 2240 and cfg.inputs.qrz.enabled


def test_save_checks_first_and_keeps_a_backup(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(MINE)
    path.chmod(0o644)
    f = ConfigFile(path)
    f.set(("udp", "port"), 70000)
    with pytest.raises(ConfigError, match=r"config.toml: udp.port: 70000 is not a port number"):
        f.save()
    f.set(("display", "theme"), "pink")
    f.set(("udp", "port"), 2237)
    with pytest.raises(ConfigError, match="display.theme must be"):
        f.check()
    assert path.read_text() == MINE                       # untouched
    f.set(("display", "theme"), "light")
    backup = f.save()
    assert re.fullmatch(r"config\.toml\.\d{8}-\d{6}\.bak", backup.name)
    assert load_config(path).display.theme == "light"
    assert backup.read_text() == MINE and f.backups() == [backup]
    for p in (path, backup):
        assert p.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.new"))
    # Again, in the same second: another copy, of the file as it was just before.
    f.set(("udp", "port"), 2241)
    again = f.save()
    assert again.name.endswith("-2.bak") and f.backups() == [backup, again]
    assert "light" in again.read_text() and "2241" in path.read_text()


def test_the_newest_backups_are_kept(tmp_path, monkeypatch):
    import jtcon.config_edit as ce
    path = tmp_path / "config.toml"
    path.write_text(MINE)
    (tmp_path / "config.toml.notes.bak").write_text("mine")     # not one of ours
    monkeypatch.setattr(ce, "BACKUPS_KEPT", 3)
    made = []
    for port in range(3000, 3005):
        f = ConfigFile(path)
        f.set(("udp", "port"), port)
        made.append(f.save())
    # The file as it was before each of the last three saves.
    assert [ConfigFile(p).get(("udp", "port")) for p in f.backups()] == [3001, 3002, 3003]
    assert (tmp_path / "config.toml.notes.bak").exists()


def test_a_profile_is_checked_on_top_of_its_base(tmp_path):
    path = tmp_path / "pota.toml"
    path.write_text('[display]\nshow = "all"\n')
    base = {"udp": {"port": 2240}}
    assert ConfigFile(path, base=base).check().udp.port == 2240
    assert ConfigFile(path).check().udp.port == 2237


def test_unreadable_files(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[udp\nport = 1\n")
    with pytest.raises(ConfigError, match="config.toml"):
        ConfigFile(path)
