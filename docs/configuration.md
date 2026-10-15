# Configuration

jtcon reads its settings from `~/.config/jtcon/config.toml` (or
`$XDG_CONFIG_HOME/jtcon/config.toml`).  Every setting is optional.  Without the
file jtcon listens on port 2237 and wants new DXCC entities, CQ zones and
countries, counting LoTW confirmations.

```bash
jtcon --init-config      # write a starter file, every setting explained (never overwrites)
jtcon --edit-config      # open it in your editor, then check it
jtcon --show-config      # print the settings jtcon will actually use
```

`--edit-config` uses `$VISUAL`, or else `$EDITOR`; set one first, for example
`export EDITOR=nano` in your `~/.bashrc`.  If there's no config file yet, it writes
the starter file first.  When you close the editor, jtcon checks the file and
says `config OK` or what's wrong.  Files jtcon creates are readable by you alone,
as they may hold your QRZ API key.

The file is [TOML](https://toml.io/).  A mistake (a misspelt setting, a word where a
number belongs) stops jtcon with a message naming the file and the setting.

- **Paths** can start with `~`.  A relative path is relative to the file it's
  written in.
- **Lists** use brackets: `adif = ["~/ham/old.adi", "/data/contest.adi"]`,
  `proxy = [2238, 2239]`.

## Profiles

A profile is a second file layered on top of the main one, for a different way of
operating: chasing DX, hunting parks, a contest.  Put it in `profiles/` next to
`config.toml` and pick it at startup:

```bash
jtcon --profile parks            # ~/.config/jtcon/profiles/parks.toml
jtcon --profile ~/elsewhere.toml # a path works too
```

The easiest way to make one:

```bash
jtcon --edit-config --profile parks
```

If `profiles/parks.toml` doesn't exist yet, this starts it as a copy of
`config.toml`, with a note at the top, and opens it.  Delete everything you don't
want to change: whatever stays in the profile overrides `config.toml`, even after
you change `config.toml` later.  Your QRZ API key isn't copied; the profile uses the
one in `config.toml`.

A profile only needs the settings it changes.  Tables merge setting by setting, so
`[display] show_time = true` in a profile leaves your other display settings
alone.  If a profile has any `[[wanted]]` entries, they replace the main file's
whole list; if it has none, the main file's rules stay.

```toml
# profiles/parks.toml: just POTA, checking every decode
[scope]
decodes = "all"

[[wanted]]
rule = "pota"
```

Settings are applied in order, later ones winning: built-in defaults, then
`config.toml` (or `--config FILE`), then the profile, then command-line options.

## Settings

### `[udp]`

| Setting | Default | |
|---|---|---|
| `host` | `"0.0.0.0"` | Address to listen on |
| `port` | `2237` | WSJT-X's UDP Server port (Settings → Reporting) |
| `proxy` | `[]` | Forward every packet to these ports on this machine, for GridTracker and other programs that want WSJT-X's broadcasts too |

### `[display]`

| Setting | Default | |
|---|---|---|
| `show` | `"cq"` | `"cq"`: CQs and wanted stations.  `"all"`: every decode |
| `show_time` | `false` | Start each line with the local time |
| `color` | `true` | |
| `mouse` | `false` | jtcon handles the mouse: the wheel scrolls the decode pane.  Hold Shift to select text |
| `debug` | `false` | Show raw packets |
| `ui` | `"classic"` | Which screen: `"classic"`, `"textual"` for the newer one with the Wanted table, or `"gui"` for the desktop window, which needs the `jtcon[gui]` install (see the README) |
| `layout` | `"auto"` | The newer screen's starting layout: `"wanted"`, `"decodes"`, `"side"` (side by side) or `"stacked"`.  `"auto"` picks from the terminal size: side by side from 200 columns wide, stacked from 50 rows tall, otherwise the Wanted table.  The desktop window uses it the first time only (after that it remembers how you left it); there `"auto"` means side by side on a screen 2400 pixels wide, else stacked |
| `theme` | `"dark"` | The desktop window's look: `"dark"`, `"light"`, or `"system"` for your desktop's own.  View › Theme changes it for the session |
| `wanted_timeout_minutes` | `10` | The newer screen: a station unheard this long leaves the Wanted table |

### `[scope]`

| Setting | Default | |
|---|---|---|
| `decodes` | `"cq"` | Check only CQs against the wanted rules, or `"all"` decodes.  With `"all"`, a wanted station in someone else's QSO shows in magenta and you can answer it; time your call for when their QSO ends.  Turn off **CQ only** in WSJT-X's Reporting settings, or WSJT-X can't answer them |

### `[inputs]`

| Setting | Default | |
|---|---|---|
| `adif` | `[]` | ADIF log files, besides the WSJT-X log, which jtcon finds itself |
| `hamdat` | `"~/.hamdat/hamdat.db"` | The [hamdat](https://github.com/ad1c/hamdat) FCC database, for US states.  Optional; needed by the `was`, `5bwas` and `states` rules |
| `cty` | `"~/.jtcon_cty.dat"` | The [Big CTY](https://www.country-files.com/) prefix file.  Downloaded when missing or over 30 days old; the old copy stays if the download fails |

jtcon looks for `wsjtx_log.adi` in the program's data folder and loads every one
it finds.  It checks the folders of WSJT-X, its experimental builds (`WSJT-X Exp`)
and the WS suite (`WS`):

| | |
|---|---|
| Linux | `~/.local/share/WSJT-X/` |
| macOS | `~/Library/Application Support/WSJT-X/` |
| Windows | `~/AppData/Local/WSJT-X/` (and `AppData/Roaming`, where v2 looked) |
| Older or portable installs | `~/WSJT-X/` |

Every log file is watched, and reloaded as soon as it changes.  A QSO you log in
WSJT-X counts straight away.

### `[inputs.qrz]`

Your QRZ logbook.  See [Online logbooks](online-logbooks.md).

| Setting | Default | |
|---|---|---|
| `enabled` | `false` | Download your QRZ logbook and keep it up to date |
| `api_key` | | Your QRZ Logbook API key.  If it's not set here, jtcon uses the `QRZ_API_KEY` environment variable, then qrzlog's `~/.config/qrzlog/qrzlog.ini` |
| `dir` | `"~/.local/share/jtcon/qrz"` | Where downloads and waiting uploads are kept |
| `keep` | `5` | How many daily downloads to keep |
| `refresh_minutes` | `15` | How often to fetch changes |

### `[filters]`

| Setting | Default | |
|---|---|---|
| `suppress_worked_within_days` | `5` | A station worked this recently isn't wanted again for what that QSO covers (see [Wanted rules](wanted-rules.md#stations-youve-worked-recently)).  `0`: today only.  `-1`: never |
| `ignore` | `[]` | Stations never shown or alerted on, unless they call you.  `W1ABC` covers `W1ABC/P` too |

### `[[wanted]]`

One entry per rule.  See [Wanted rules](wanted-rules.md).

| Setting | |
|---|---|
| `rule` | Which rule: `dxcc`, `cqz`, `ituz`, `country`, `was`, `5bwas`, `grid`, `pota`, `sota`, `iota`, `callsigns`, `message`, `states` |
| `name` | Its own name, to use a rule twice.  Shown on screen, and usable in `--wanted` |
| `confirm` | Award rules: the confirmations that count, from `lotw`, `qrz`, `eqsl`, `card`.  Default `["lotw"]`; `[]` means worked is enough |
| `files` | `callsigns` and `message`: watchlist files |
| `states`, `file` | `states`: a list of states, or a file of them |

### Actions

What jtcon does when a station is wanted, or when you log a QSO.

```toml
[actions.highlight]             # colour wanted stations in WSJT-X's Band Activity
enabled    = true               # on by default
background = "#ffa500"
foreground = "#000000"

[actions.ntfy]
topic = "my-topic"              # push wanted alerts to ntfy.sh/my-topic

[actions.script]
on_wanted     = "~/bin/alert.sh"   # gets the alert text as its argument
on_log_reload = "~/bin/backup.sh"  # gets the changed ADIF files as arguments

[actions.json_log]
file = "~/jtcon-spots.jsonl"    # one JSON line per CQ

[actions.qrz]
upload = true                   # send each QSO WSJT-X logs to your QRZ logbook
```

- **Highlighting** is cleared again when a station stops being wanted and when
  jtcon exits.
- **The alert text** for `on_wanted` and ntfy looks like
  `VK3XY | Australia CQ30 | WANTED: DXCC(VK) new · CQZ(30) new`.
- **`on_log_reload`** runs when one of your ADIF files changes, not when the QRZ
  download refreshes.
- **The JSON log** has one line per CQ: the decode, the station's entity and zones,
  the rules that wanted it, and whether it's ignored.
- **QRZ upload** is off by default.  See [Online logbooks](online-logbooks.md).

## Command-line options

Options override the config file for one run.

| Option | |
|---|---|
| `--config FILE` | Use this config file instead of `~/.config/jtcon/config.toml` |
| `--profile NAME` | Layer `profiles/NAME.toml` on top; a path to a `.toml` file works too |
| `--show-config` | Print the settings jtcon will use, and exit.  The QRZ API key shows as `****` plus its last four characters |
| `--init-config` | Write a starter config file, and exit |
| `--edit-config` | Open the config file in `$VISUAL` or `$EDITOR`, then check it.  With `--profile`, open the profile instead, starting a new one as a copy of the config file |
| `--list-rules` | List the wanted rules and their settings, and exit |
| `--wanted RULES` | Use these rules (or `[[wanted]]` names) instead of the configured ones: `dxcc,was,pota`.  `RULE:VALUE` gives a rule its files, states or confirmations: `callsigns:a.txt+b.txt`, `states:AK+DE`, `dxcc:lotw+qrz`, `grid:worked`.  `none` for no rules |
| `--scope cq\|all` | Check CQs only, or every decode |
| `--ignore CALL` | Ignore this station too; repeat for more |
| `--port PORT` | UDP port |
| `--save FILE` | Write the JSON log to FILE |
| `--mouse` | Turn on mouse support |
| `--ui classic\|textual\|gui` | Which screen to use |
| `--no-color` | No colours |
| `--debug` | Show raw packets |
| `--version` | Print the version |

Check what a run would use without starting it:
`jtcon --show-config --profile parks --wanted was`.
