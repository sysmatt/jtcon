# Wanted rules

Each rule you turn on looks at every CQ (or every decode, with `scope all`) and
decides whether you want that station.  When one or more rules want it, jtcon
prints a WANTED line naming each of them:

```
  *** WANTED: ❌DXCC(VK) · ❌CQZ(30) ***
  *** WANTED: ❓WAS(TX) · POTA ***
  *** WANTED: CALLS(^VK [wanted.txt]) ***
```

Wanted stations are also highlighted in WSJT-X's Band Activity window and ring the
terminal bell, and can run a script or send a push notification (see
[Configuration](configuration.md#actions)).

`jtcon --list-rules` prints this list with each rule's settings.

## Turning rules on

Each `[[wanted]]` entry in the config file turns on one rule:

```toml
[[wanted]]
rule = "dxcc"

[[wanted]]
rule = "was"

[[wanted]]
rule = "pota"
```

With no `[[wanted]]` entries, jtcon uses `dxcc`, `cqz` and `country`.

For one run, `--wanted` replaces the list:

```bash
jtcon --wanted dxcc,was,pota
jtcon --wanted none                       # no rules: just show the decodes
```

## Award rules

Award rules track your progress towards an award.  Each one has a column on the
screen showing where you stand for this station:

| Mark | Meaning | Wanted? |
|---|---|---|
| ✅ | Confirmed | No |
| ❓ | Worked, but not confirmed yet | Yes |
| ❌ | Never worked | Yes |

❓ is the one to watch: you have a QSO that would count if it were confirmed, so it
may be worth working this station, or chasing that confirmation.

| Rule | Shown as | Wants | Needs |
|---|---|---|---|
| `dxcc` | `DXCC` | A DXCC entity | |
| `cqz` | `CQZ` | A CQ zone (WAZ) | |
| `ituz` | `ITUZ` | An ITU zone | |
| `country` | `CTRY` | A country name | |
| `was` | `WAS` | A US state (Worked All States) | hamdat |
| `5bwas` | `5BWAS` | A US state on the current band (5-Band WAS: 80, 40, 20, 15 and 10 m) | hamdat |
| `grid` | `GRID` | A 4-character grid square.  Busy on FT8, where most stations send one | |

`was`, `5bwas` and `states` look up US stations' states in the
[hamdat](https://github.com/ad1c/hamdat) FCC database (`~/.hamdat/hamdat.db`, or
set `hamdat` under `[inputs]`).

### Which confirmations count

Each award rule has a `confirm` setting listing the confirmations that count.  The
default is LoTW alone, which is what the ARRL accepts for DXCC and WAS.

| Source | Comes from |
|---|---|
| `lotw` | `LOTW_QSL_RCVD = Y` in your logs |
| `qrz` | A QSO confirmed in your QRZ logbook (see [Online logbooks](online-logbooks.md)) |
| `eqsl` | `EQSL_QSL_RCVD = Y` |
| `card` | `QSL_RCVD = Y`: a paper card |

```toml
[[wanted]]
rule    = "dxcc"
confirm = ["lotw", "card"]      # LoTW or a card

[[wanted]]
rule    = "grid"
confirm = []                    # worked is enough: no ❓, just ✅ or ❌
```

On the command line: `--wanted dxcc:lotw+card` or `--wanted grid:worked`.

The same QSO often turns up in several places, such as the WSJT-X log and the QRZ
download.  jtcon matches them up (same call, band and mode, close in time), so a
confirmation from any of them counts.

### One rule, two awards

A rule can appear twice with different settings if each has its own `name`.  For
example, WAS for the ARRL award (LoTW) and for QRZ's own award:

```toml
[[wanted]]
rule = "was"                    # shown as WAS, counted by LoTW

[[wanted]]
rule    = "was"
name    = "WAS-QRZ"             # shown as WAS-QRZ, counted by QRZ
confirm = ["qrz"]
```

A name works with `--wanted` too: `jtcon --wanted WAS-QRZ,pota`.

## Watch rules

Watch rules have no column.  They want a station or they don't.

| Rule | Shown as | Wants | Settings |
|---|---|---|---|
| `pota` | `POTA` | `POTA` as a word in the message (`CQ POTA K5TX EM10`) | |
| `sota` | `SOTA` | `SOTA` as a word in the message | |
| `iota` | `IOTA` | `IOTA` as a word in the message | |
| `callsigns` | `CALLS` | A callsign matching a pattern in your watchlist files | `files` (required) |
| `message` | `MSG` | A message matching a pattern in your watchlist files | `files` (required) |
| `states` | `STATE` | A station in one of these US states (needs hamdat) | `states`, or `file` |

```toml
[[wanted]]
rule  = "callsigns"
files = ["~/ham/wanted.txt", "~/ham/club.txt"]   # one file is fine too: files = "x.txt"

[[wanted]]
rule   = "states"
states = ["AK", "DE"]           # or a file:  file = "~/ham/states.txt"
```

On the command line, `RULE:VALUE`, with several values joined by `+`:

```bash
jtcon --wanted callsigns:~/ham/wanted.txt+~/ham/club.txt
jtcon --wanted 5bwas,states:AK+DE
```

### Watchlist files

One entry per line.  Lines starting with `#`, and blank lines, are skipped.  jtcon
reloads a file as soon as you save it.

`callsigns` files hold regular expressions, searched in the callsign; upper and
lower case are the same:

```
# wanted.txt
^VK             any VK station
^K1ABC$         exactly K1ABC
/P$             portable stations
```

`message` files hold regular expressions, searched in the whole decoded message:

```
\bPOTA\b        POTA as a word anywhere in the message
```

`states` files hold two-letter state codes, one per line.

## Stations you've worked recently

A station you've worked in the last 5 days isn't wanted again for what that QSO
already covers.  For example:

- After working VK3XY, jtcon stops flagging VK3XY for DXCC while you wait for the
  confirmation.  Another VK station still shows as wanted.
- A 20 m QSO with a Texas station doesn't hide that station for 5-Band WAS on 40 m.
- Watch rules (POTA, watchlists) stay quiet for any station worked in that time.

Change the number of days with `suppress_worked_within_days` under `[filters]`:
`0` means worked today only, `-1` turns this off.

## Ignoring stations

`ignore` under `[filters]` lists stations that are never shown or alerted on.
`W1ABC` covers `W1ABC/P` and its other forms.  They still show if they call you.
In the TUI, `ignore 07` or `ignore W1ABC` adds one for the session, and `unignore`
takes it off.

## Alerts

The bell, script and ntfy alerts fire once when a station becomes wanted.  They
fire again if the reasons change, or if the station comes back after being unheard
for an hour.
