# jtcon

A full-screen terminal monitor for [WSJT-X](https://wsjt.sourceforge.io/).  jtcon
listens to WSJT-X's UDP broadcasts and shows every CQ with the station's DXCC
entity, zones, US state and your history with them.  It checks each one against
your logs and the awards you're chasing, and tells you which stations you want and
why.  Type a station's number to answer it in WSJT-X.

```
##  HHMMSSZ    dB    dt(s)     Hz  mode  message                   callsign      grid     days  ST  | entity                    CQz   ITUz   ct  | DXCC     CQZ   WAS   QRZ  LoW
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
00  123015z   -12    +0.2s   1234  FT8   CQ W1ABC FN42             W1ABC         FN42     590d  MA  | United States             CQ5   ITU8   NA  | ✅K      ✅5   ✅MA  ⭐  🌎
01  123015z    -7    +0.1s    842  FT8   CQ VK3XY QF22             VK3XY         QF22               | Australia                 CQ30  ITU59  OC  | ❌VK     ❌30
  *** WANTED: ❌DXCC(VK) · ❌CQZ(30) ***
02  123015z   +03    -0.3s   2210  FT8   CQ POTA K5TX EM10         K5TX          EM10      41d  TX  | United States             CQ5   ITU8   NA  | ✅K      ✅5   ❓TX
  *** WANTED: ❓WAS(TX) · POTA ***
CMD> 01
```

- **Wanted rules** for DXCC, CQ and ITU zones, countries, WAS, 5-Band WAS and grids,
  plus POTA/SOTA/IOTA and your own callsign and message watchlists.  Each rule says
  which confirmations count: LoTW, QRZ, eQSL or a paper card.
- **Your logs:** the WSJT-X log is found automatically; add any other ADIF files.
  They reload the moment they change.
- **Online logbooks:** jtcon can download your QRZ logbook for its confirmations,
  keep daily backups of it, and upload each QSO you log.
- **In WSJT-X:** wanted stations are highlighted in the Band Activity window, and
  you can answer any station from jtcon's command line.
- **Alerts:** the terminal bell, a script of your own, or a push notification
  through [ntfy](https://ntfy.sh).
- **Profiles:** keep a DX setup, a POTA setup and so on, and pick one at startup.

## Documentation

| | |
|---|---|
| [Configuration](docs/configuration.md) | The config file, profiles, every setting, and the command-line options |
| [Wanted rules](docs/wanted-rules.md) | Each rule, confirmations, watchlist files, and what the marks mean |
| [Online logbooks](docs/online-logbooks.md) | QRZ download, backups and uploads |
| [Migrating from v2](docs/migrating-from-v2.md) | Every v2 option and what replaced it |

## Install

jtcon needs Python 3.11 or newer.  Install it with [pipx](https://pipx.pypa.io/),
which gives it its own environment and puts the `jtcon` command on your PATH.  Get
pipx from your package manager (`sudo apt install pipx`, `sudo dnf install pipx`,
`brew install pipx`).

```bash
pipx install git+https://github.com/sysmatt/jtcon
pipx ensurepath        # one time: adds ~/.local/bin to your PATH; open a new terminal
jtcon --version
```

For every user on the machine (pipx 1.5 or newer):
`sudo pipx install --global git+https://github.com/sysmatt/jtcon`.

Upgrade and remove:

```bash
pipx upgrade jtcon
pipx uninstall jtcon
```

<details>
<summary>Running from a git clone instead</summary>

```bash
git clone https://github.com/sysmatt/jtcon.git
cd jtcon
python3 -m pip install --user -r requirements.txt   # or: sudo apt install python3-prompt-toolkit
./jtcon
```

Update with `git pull`.  To get a `jtcon` command that runs your checkout, use
`pipx install -e .` from the clone.
</details>

### Versions

Releases are `vX.Y.Z` tags.  Every commit after a release is numbered `.postN`
(N commits since the tag), so `pipx upgrade jtcon` always picks up the newest
commit and `jtcon --version` tells you exactly which one you're running.

## Set up WSJT-X

In WSJT-X, **File → Settings → Reporting**, under **UDP Server**:

- **UDP Server:** `127.0.0.1`, port `2237` (jtcon's default).
- **Accept UDP requests:** on.  jtcon needs it to answer stations for you.
- **CQ only** (if your version has it): off, if you set jtcon to check every decode
  (`--scope all`).  Otherwise WSJT-X can't answer a station that isn't calling CQ.

To use another program that listens on the same port, such as GridTracker, let
jtcon forward the packets to it: `proxy = [2238]` under `[udp]`, and point that
program at port 2238.

## Quick start

**1. Make a config file.**

```bash
jtcon --init-config
```

This writes `~/.config/jtcon/config.toml`, with every setting listed and
explained.  `jtcon --edit-config` opens it in your editor (`$VISUAL` or
`$EDITOR`) and checks it when you close the editor.  Out of the box jtcon wants new DXCC entities, CQ zones and countries,
counting LoTW confirmations, and finds your WSJT-X log by itself.

**2. Add your QRZ logbook** (optional, but recommended if you use QRZ).  Get your
API key from QRZ's [Logbook API page](https://www.qrz.com/docs/logbook30/api).  In the config file, find
the `[inputs.qrz]` block, remove the `#` from the lines below, and paste in your
key:

```toml
[inputs.qrz]
enabled = true
api_key = "XXXX-XXXX-XXXX-XXXX"

[actions.qrz]
upload = true            # also send each QSO you log to QRZ
```

The key gives full access to your logbook, so the file must be private.  jtcon
creates it readable by you alone; if you made it some other way, run
`chmod 600 ~/.config/jtcon/config.toml`.  jtcon warns at startup if others can
read it.

If your QRZ logbook is linked to LoTW, this also brings in your LoTW
confirmations (see [Online logbooks](docs/online-logbooks.md#lotw-confirmations-through-qrz)).

**3. Choose your rules.**  Further down the file, the `[[wanted]]` entries pick
what you're chasing.  Remove the `#` from the ones you want, such as `was` or
`pota`.  [Wanted rules](docs/wanted-rules.md) describes them all.

**4. Check and start.**

```bash
jtcon --show-config      # the settings jtcon will use (the key shows as ****1234)
jtcon                    # start; Ctrl-Q or "quit" to exit
```

With QRZ on, the first start downloads your whole logbook in the background.

To try something for one run without editing the file:

```bash
jtcon --wanted dxcc,was,pota      # just these rules
jtcon --scope all                 # check every decode, not only CQs
jtcon --profile parks             # layer profiles/parks.toml on top of the config
```

## The screen

The column header stays at the top.  Below it each decode gets a line, and
wanted stations get a second line saying why:

| Column | |
|---|---|
| `##` | The station's number, 00–99.  Type it at `CMD>` to answer |
| `HHMMSSZ` `dB` `dt(s)` `Hz` `mode` `message` | As WSJT-X shows them |
| `callsign` | **Green:** worked on this band and mode.  **Yellow:** worked on another band or mode |
| `grid` | Green if you've worked that grid |
| `days` | Days since your last QSO with this station |
| `ST` | US state, from the [hamdat](https://github.com/ad1c/hamdat) database if you have it; green if worked |
| `entity` `CQz` `ITUz` `ct` | DXCC entity, CQ zone, ITU zone, continent |
| One column per award rule | ✅ confirmed, ❓ worked but not confirmed, ❌ new |
| `QRZ` `LoW` | ⭐ this station has confirmed with you on QRZ, 🌎 on LoTW |

A `*** WANTED ***` line lists each rule that wants the station.  It's red when an
award rule wants it, yellow when only a watchlist does, and magenta for a station
that isn't calling CQ (with `--scope all`).  The terminal bell rings the first
time a station is wanted, and again if it comes back after an hour unheard.

Each station keeps its number while it's being heard, so a station calling CQ
over and over always has the same one.  jtcon remembers the last 100 stations,
and forgets them when you change frequency.

The status line at the bottom shows RX or TX, where you are in the current
cycle, decode and CQ counts, your call as WSJT-X reports it, and `ign:N` when
you're ignoring stations.  `SCROLL` means you've scrolled back; `UDP DOWN` means
jtcon lost its network socket.

## The new screen (`--ui textual`)

jtcon has a second, newer screen.  Its main view is a live **Wanted table**: one
row per station you want right now, updated in place, so a wanted station never
scrolls away.  Try it with `jtcon --ui textual`, or make it your default with
`ui = "textual"` under `[display]`.  The classic screen above stays the default
for now.

```
 ##  Call   QSO  Entity         CQz  ITUz  ct  ST  Grid  dB   Seen  ×   days  DXCC  CQZ   WAS  QRZ  LoW  Wanted
 22  K0AU   👋   United States  4    7     NA  CO  DN70  -20  4s    3         ✅K   ❌4   ✅CO            CQZ
 02  VK4AB  📶   Australia      30   55    OC      QG62  -14  12s   9         ❌VK  ❌30                  DXCC CQZ
 07  K5TX   📣   United States  5    8     NA  TX  EM10  +03  15s   2    41d  ✅K   ✅5   ❓TX            WAS POTA
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
##  HHMMSSZ    dB     dt     Hz  message
02  123015z   -14   +0.2   1234  W1XYZ VK4AB -12           ★
07  123015z    +3   -0.3   2210  CQ POTA K5TX EM10         ★
21  123015z   -19   +0.2    940  CQ K9AT EN52
─────────────────────────────────────────────────────────────
[wanted] ✅ Now confirmed: WAS CO
CMD>
 RX  14.074 MHz 20m [████████░░░░░░] odd  FT8  dec=42 cq=18 …  de=K2TTA
```

- **QSO** shows where each station is in a QSO, from its own latest message: 📣
  calling CQ, 🤝 answering a CQ, 📶 sending reports, 👋 signing off.  👋 means it
  will probably be free in a cycle or two.
- **Answer without leaving the command line:** ↑↓ select a station, then Enter on
  an empty `CMD>` line answers it.  A double-click answers too; a single click only
  selects.  Ctrl-S changes the sort order (last heard, SNR, call, entity).
- **Four layouts** of the table and the decode stream: F2 the table, F3 the
  decodes (today's full lines), F4 side by side, F5 stacked.  Ctrl-arrows step
  through them.  The split layouts use a short decode line, ★ marking a wanted
  station, since the table already has the details.  Without a `layout` setting,
  jtcon picks one from your terminal's size.
- **Live controls** for this session only: F6 switches rules on and off, F7 checks
  CQs only or every decode, F8 shows CQs only or every decode.  The end of the
  status line lists what you've changed, e.g. `off:CQZ`.
- **jtcon's own messages** (QRZ, confirmations, command replies) have their own
  strip above the command line, so they never scroll past in the decodes.
- **F1** lists every key.  The commands are the same as on the classic screen.

A station leaves the table when no rule wants it any more, when you ignore it,
or after 10 minutes unheard (`wanted_timeout_minutes`).  The screen uses the mouse,
so to select text for copying, hold Shift while dragging (Option in iTerm2 and
macOS Terminal); most terminals then select as usual.

## The desktop window (`--ui gui`, in progress)

A third front end is on its way: a desktop window, on Qt, for Linux, Windows and
macOS ([the plan](docs/gui-plan.md)).  It has the Wanted table, the decode
stream as a table and the log, as panels you arrange, plus menus and the status
bar.  It works like a desktop app: there's no `CMD>` line; the tables, the menus
and right-click do what the commands do on the other screens.  **F1** lists
every key.

Double-click a station, or select it and press Enter, to answer it, in the
Wanted table or in the decodes (any station calling CQ, wanted or not).  Delete
ignores it for the session.  Right-click a station to answer it, ignore it, copy
its call, or look it up on QRZ.com; the Station menu does the same for the
selected one, and Station › Ignored stations lists what's ignored, to unignore
a call or add one.  Click a Wanted column header to sort (again to reverse), or
Ctrl-S.  Right-click the decodes or the log to clear them.

View › Layout (or F2–F5) lays the panels out in the Textual screen's four
layouts: Wanted, Decodes, Side by side, Stacked; Ctrl-arrows step through them.
You can also arrange them yourself: the panels are locked at first, with no
title bars to take up room; turn View › Lock panels off, then drag a panel by
its title to put it beside, above or below another, onto one (as tabs), or out
on its own (on a second screen, say), and lock them again.  The View menu brings back a panel you
closed, and "Reset window layout" starts afresh.

The Rules menu (or F6) switches rules on and off for the session, and has the
scope (F7) and show (F8) switches; the Logbook menu runs a QRZ sync.

**File › Settings** (Ctrl-,) edits the config file's settings, a page per
subject, each with its explanation and default, and the Wanted rules: add,
remove and reorder rules, and set each one's confirmations, name and files.  It marks a setting the command
line (or a profile) sets this run, since theirs wins.  Save writes only what you
changed, keeps your comments, keeps a dated copy of the old file
(`config.toml.20261015-142233.bak`, the last 10), and reloads.  File › Edit
config file opens the config in your editor instead, and File › Reload config
(Ctrl-R) uses your changes without quitting: jtcon restarts in the same window, with the
command line's options still winning, and the stations you ignored and the rules
you switched off this session staying so.

It's dark by default.  `theme = "light"` under `[display]` makes it light, and
`theme = "system"` takes your desktop's own look; View › Theme switches for the
session.  View › Text size (Ctrl-plus, Ctrl-minus, Ctrl-0) makes the panels'
text bigger or smaller, and the window remembers it.  Right-click a table's column
headers to hide or show columns; drag a header to move its column.  The decodes
keep their columns per layout: side by side and stacked start with the short
ones.  The window remembers its size, panels and columns from one run to the
next, in `window.ini` beside your config file.

It needs Qt, which the plain install leaves out (it's a big download), so add it
with the `gui` extra and start jtcon with `--ui gui`:

```
pipx install --force "jtcon[gui] @ git+https://github.com/sysmatt/jtcon"
jtcon --ui gui
```

On Linux, if it says it can't load the Qt platform plugin "xcb", install
`libxcb-cursor0` (Debian, Ubuntu) or `xcb-util-cursor` (Fedora, Arch).

## Commands

Type at the `CMD>` prompt and press Enter.  Any unique start of a command works:
`l` for `list`, `s` for `sync`, `un` for `unignore`.

| Command | |
|---|---|
| `07`, `W1ABC` | Answer that station in WSJT-X, like double-clicking its decode.  A warning shows if it was last heard over two minutes ago |
| `list` | The wanted stations jtcon remembers, last heard at the bottom |
| `ignore 07`, `ignore W1ABC` | Hide a station for this session (and its `/P` and other forms).  It still shows if it calls you |
| `ignore` | List the stations you're ignoring |
| `unignore W1ABC` | Stop ignoring it |
| `sync` | Fetch what changed in your online logbook and retry any waiting uploads.  `sync full` downloads the whole logbook again |
| `clear` | Clear the screen |
| `quit`, `exit` | Quit |

To ignore a station permanently, add it to `ignore` under `[filters]`.

| Key | |
|---|---|
| `↑` `↓` `PgUp` `PgDn` | Scroll back while you type; the mouse wheel usually does the same |
| `Esc`, `Ctrl-End` | Back to the newest output |
| `Tab` | Move between the command line and the decode pane |
| `Ctrl-Q`, `Ctrl-C` | Quit |

While you're scrolled back the view stays put as new decodes arrive.  With
`--mouse` (or `mouse = true`), jtcon handles the mouse itself; hold **Shift** to
select text.

## Data jtcon keeps

| | |
|---|---|
| `~/.config/jtcon/config.toml` | Your settings, and `profiles/` beside it |
| `~/.jtcon_cty.dat` | The [Big CTY](https://www.country-files.com/) prefix file.  Downloaded on first run and refreshed when it's over 30 days old |
| `~/.local/share/jtcon/qrz/` | QRZ logbook backups and uploads waiting to be sent, if you use QRZ |

## Limits

- The decode pane keeps about the last 5000 lines.
- If jtcon can't start (a log file is missing, a setting is wrong) it says why and
  exits.

## License

GPLv3.  See [LICENSE](LICENSE).
