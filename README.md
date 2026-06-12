# jtcon

A full-screen TUI monitor for [WSJT-X](https://wsjt.sourceforge.io/) UDP broadcasts.
Displays CQ calls in real time, enriched with DXCC entity, CQ zone, country, and grid
information from the [AD1C Big CTY](https://www.country-files.com/) prefix database.
Optionally compares against an ADIF logbook to flag contacts not yet worked.

## Layout

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  HHMMSSZ   dB   dt(s)    Hz  mode  message           callsign  grid   days   │...│
│  ──────────────────────────────────────────────────────────────────────────────  │
│  123045z  +05   +0.1s  1234  FT8   CQ W1ABC FN42     W1ABC     FN42     3d   │...│  ← scrollable
│  123100z  -12   -0.3s  2100  FT8   CQ DX VK3XY QF22  VK3XY    QF22           │...│     decode pane
│  ...                                                                             │     (Tab to focus
│                                                                                  │      and scroll)
├──────────────────────────────────────────────────────────────────────────────────┤
│  CMD>                                                                            │  ← command input
├──────────────────────────────────────────────────────────────────────────────────┤
│  RX  [████████░░░░░░] even  FT8  dec=42 cq=18  avg dec=…                         │  ← status line
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Decode pane columns

| Column | Description |
|---|---|
| `##` | Rolling buffer index 00–99 — use this number at the CMD> prompt to reply |
| `HHMMSSZ` | WSJT-X decode period (UTC) |
| `dB` | Signal-to-noise ratio |
| `dt(s)` | Time offset from the sync point |
| `Hz` | Audio frequency |
| `mode` | FT8, FT4, etc. |
| `message` | Raw decoded message |
| `callsign` | CQ callsign — **green** if worked on this band+mode, **yellow** if worked on a different band or mode, bold white if never worked *(with --adif)* |
| `grid` | Grid square (green if already worked) |
| `days` | Days since last QSO with this callsign *(with --adif)* |
| `ST` | FCC-licensed US state *(with --match-state)* |
| `\| entity CQz ITUz ct` | DXCC entity, CQ zone, ITU zone, continent (cyan) |
| `WAS` / `5BW` | WAS/5BWAS status icon + state *(with --was or --5bwas)* |
| `QRZ` | ⭐ if callsign has QRZ confirmation (`app_qrzlog_status=C`) |
| `LoW` | 🌎 if callsign has LoTW confirmation (`lotw_qsl_rcvd=Y`) |

The `##` index cycles 00–99 and wraps.  Each CQ call is assigned a fixed index at
arrival time; the index does not change as new calls arrive.  Non-CQ decodes that
produce a NEEDED or MATCH hit (with `--all`) are also indexed and replyable.
Non-CQ lines with no hit are indented with spaces and not assigned an index.

When `--adif` is active, each of the entity/zone/country fields is prefixed by a
status icon:

| Icon | Meaning |
|---|---|
| ✅ | Already worked and confirmed (or any QSO when `--qsl-only` is not set) |
| ❓ | Worked but not yet LoTW-confirmed *(only visible with `--qsl-only`)* |
| ❌ | Not yet worked |

### WAS / 5BW column icons

The `WAS`/`5BW` column uses the same three icons, but the confirmation rules are
different — and fixed.  The ARRL only accepts LoTW QSLs for the WAS and 5-Band WAS
awards, so the column **always** behaves as if `--qsl-only` is active, regardless of
whether you passed that flag.

**`--was` mode** — per state:

| Icon | Meaning |
|---|---|
| ✅ | State is LoTW-confirmed (`lotw_qsl_rcvd=Y` in your log) |
| ❓ | State is worked (QSO exists) but not yet LoTW-confirmed |
| ❌ | State has never been worked |

**`--5bwas` mode** — per state × current band:

| Icon | Meaning |
|---|---|
| ✅ | State is LoTW-confirmed on the current band |
| ❓ | State is worked on the current band but not yet LoTW-confirmed |
| ❌ | State has never been worked on the current band |

The `❓` icon is particularly useful here: it means you have a QSO in the log that
could satisfy the award if the other station uploads to LoTW — so it may be worth
working them again or chasing the confirmation rather than treating the contact as
a brand-new needed state.

### NEEDED alerts with pending (unconfirmed) QSOs

The `NEEDED` alert fires whenever a state/band combo is not yet LoTW-confirmed,
**even if you already have an unconfirmed QSO for that state in your log**.  The
NEEDED check only looks at LoTW-confirmed contacts — it does not matter which
callsign you previously worked or whether a confirmation is pending.

Example: you worked W1ABC in MA last week, but it's still showing `❓` (unconfirmed).
Today W1XYZ calls CQ from MA.  jtcon will still fire the NEEDED alert:

```
  *** NEEDED: NEW-WAS(MA) ***
```

This is intentional — until LoTW confirms the contact, MA is not in the bag and the
new decode is a legitimate opportunity to secure it.

NEEDED and MATCH alerts appear as indented lines below the relevant decode.
For CQ-triggered alerts the lines are **red** (NEEDED) and **yellow** (MATCH):
```
  *** NEEDED: NEW-DXCC(VK)  NEW-CQZ(29) ***
  *** MATCH: CALL:VK.* [watchlist.txt] ***
```

### Matching against all decodes (`--all`)

By default jtcon only applies NEEDED/MATCH logic to CQ calls.  Some operators rarely
call CQ and only appear in directed exchanges (e.g. `W1ABC W1XYZ -12`).  The `--all`
flag extends the full matching pipeline to every decoded message so those stations
are not missed.  Be careful with this mode, Try to call when the operator is ending
their current QSO.  Timing is everything. 

**How it works**: In a directed FT8 message the second token is the transmitting
station (the FROM_CALL) — the station whose signal you are actually receiving and
who is therefore a valid contact target.  jtcon looks up that callsign for entity,
CQ zone, country, and US state, then applies the same NEEDED/WAS/5BWAS/pattern
checks as it would for a CQ.

Decodes involving your own callsign are automatically excluded — both your own
transmissions (FROM_CALL = your call) and messages directed at you (TO_CALL = your
call, meaning you are already engaged with that station).  Your callsign is learned
from WSJT-X Status packets and shown as `de=CALL` in the status line.

**Display rules**:

| Mode | CQ decodes | Non-CQ decodes |
|---|---|---|
| default | always shown | suppressed |
| `--raw` | always shown | always shown, no NEEDED/MATCH |
| `--all` | always shown | shown only on a NEEDED or MATCH hit (magenta) |
| `--all --raw` | always shown | always shown; hits highlighted magenta |

Non-CQ hits are highlighted **magenta** — both the decoded message text and the
`*** NEEDED ***` / `*** MATCH ***` alert line — so they are instantly distinguishable
from CQ-sourced alerts.  They are also assigned a rolling buffer index and can be
called from the `CMD>` prompt exactly like a CQ entry.

Example — a needed DXCC entity heard in a directed exchange:

```
07  123045z  +05   +0.1s  1234  FT8   W1ABC VK3XY R-12           VK3XY   ...
      *** NEEDED: NEW-DXCC(VK) ***                                (magenta)
```

Usage examples:

```bash
# Alert on NEEDED contacts heard in any decode, not just CQ
./jtcon --adif ~/wsjtx_log.adi --all

# Same, but also show all non-CQ traffic (verbose)
./jtcon --adif ~/wsjtx_log.adi --all --raw

# Catch non-CQ WAS contacts on 20 m
./jtcon --adif ~/wsjtx_log.adi --5bwas --all
```

### Status line

| Field | Description |
|---|---|
| `RX` / `TX` | Current state from WSJT-X Status messages (TX shown in red) |
| `[████░░░░░░░░░░]` | Bargraph showing position in the current 15 s (FT8) or 7.5 s (FT4) cycle |
| `even` / `odd` | Parity of the current TX slot |
| `FT8` / `FT4` | Active mode |
| `dec=N` | Decodes in the current cycle |
| `cq=N` | CQ calls in the current cycle |
| `avg dec=N.N cq=N.N` | Running averages across all cycles |
| `even/odd dec=N.N cq=N.N` | Per-parity running averages |
| `de=CALL` | Your callsign as reported by WSJT-X (appears once the first Status packet is received) |

## Requirements

- Python 3.10+
- `prompt-toolkit` >= 3.0 — `pip install prompt-toolkit` or your OS package manager
- `cty.dat` — downloaded automatically from country-files.com on first run and cached at
  `~/.jtcon_cty.dat`

Optional:
- An ADIF log file (`--adif`) for NEEDED detection and worked-contact coloring
- [hamdat](https://github.com/ad1c/hamdat) SQLite database for US state lookups
  (`--match-state`, `--was`, `--5bwas`)

## Installation

```bash
git clone <repo>
cd jtcon
pip install -r requirements.txt
chmod +x jtcon
./jtcon
```

## Usage

```bash
# Basic live monitor
./jtcon

# With ADIF log — NEEDED flag + coloring for new entities/zones/countries
./jtcon --adif ~/Documents/wsjtx_log.adi

# ADIF mode with LoTW-only confirmation (❓ for worked-but-unconfirmed)
./jtcon --adif ~/wsjtx_log.adi --qsl-only

# Suppress NEEDED alerts for any callsign worked in the last 7 days
./jtcon --adif ~/wsjtx_log.adi --worked-lag-days 7

# Save every CQ record to a JSON-lines file
./jtcon --save cq.jsonl

# Show all decodes (not just CQ) with wall-clock timestamps
./jtcon --raw --show-time

# Flag POTA and SOTA activations
./jtcon --pota --sota

# Match specific callsigns from a watchlist file
./jtcon --match-calls watchlist.txt

# Only alert on new DXCC entities, suppress zone/country checks
./jtcon --adif ~/wsjtx_log.adi --no-need-cqz --no-need-country

# Forward packets to GridTracker on port 2238
./jtcon --proxy 2238

# Run a script whenever the ADIF log is reloaded
./jtcon --adif ~/wsjtx_log.adi --adif-change-script ~/bin/on_log_change.sh

# Flag US states not yet LoTW-confirmed (ARRL WAS award tracking)
./jtcon --adif ~/wsjtx_log.adi --was

# Flag states not yet LoTW-confirmed on the current band (ARRL 5-Band WAS)
./jtcon --adif ~/wsjtx_log.adi --5bwas

# Alert on NEEDED/MATCH contacts heard in any decode, not just CQ
./jtcon --adif ~/wsjtx_log.adi --all
```

## ADIF Auto-Discovery

When no `--adif` flag is given, jtcon automatically searches these paths for a WSJT-X
log and loads any it finds:

| Platform | Path |
|---|---|
| Linux | `~/.local/share/WSJT-X/wsjtx_log.adi` |
| Linux (experimental) | `~/.local/share/WSJT-X Exp/wsjtx_log.adi` |
| macOS | `~/Library/Application Support/WSJT-X/wsjtx_log.adi` |
| Windows | `~/AppData/Roaming/WSJT-X/wsjtx_log.adi` |
| Portable | `~/WSJT-X/wsjtx_log.adi` |

Explicitly listed `--adif` files are merged with any auto-discovered files.
All loaded log files are watched for changes and reloaded automatically mid-session.

## Key Bindings

| Key | Action |
|---|---|
| `Tab` | Toggle focus between decode pane and command input |
| `↑` `↓` `PgUp` `PgDn` | Scroll decode pane (when pane is focused) |
| `Ctrl-C` / `Ctrl-Q` | Quit |

## Commands

Type in the `CMD>` line and press Enter:

| Command | Action |
|---|---|
| `quit` / `exit` / `q` | Exit the program |
| `clear` | Clear the decode pane |
| `list` (or `l`, `li`, `lis`) | Show all NEEDED/MATCH entries from the rolling buffer |
| `7` / `42` / `00` *(any 1–2 digit number)* | Reply to the contact at that rolling-buffer index |
| `W1ABC` *(any callsign)* | Search the rolling buffer and reply to the most recent entry from that callsign |

**Rolling buffer** — the last 100 entries are kept in memory.  Each CQ call is assigned
a two-digit index (`##` column) that is stable once assigned.  With `--all`, non-CQ
decodes that produce a NEEDED or MATCH hit are also indexed and stored.  `list` lets
you review all flagged contacts at once; then call by index or callsign.

Replies use the WSJT-X UDP Reply message (type 4), equivalent to double-clicking a
decode in the WSJT-X UI.  WSJT-X must have **Settings → Reporting → Accept UDP requests**
checked.

## All Options

```
--host HOST             UDP bind address (default: 0.0.0.0)
--port PORT             UDP port matching WSJT-X Settings → Reporting (default: 2237)
--show-time             Prepend local wall-clock HH:MM:SS to each line
--raw                   Show ALL decoded messages, not just CQ
--all                   Apply NEEDED/MATCH logic to all decodes, not just CQ; non-CQ
                        hits shown in magenta and added to reply buffer (see --raw to
                        also show non-matching non-CQ traffic)
--save FILE             Append each CQ record as JSON to FILE
--cty PATH              Path to cty.dat (default: ~/.jtcon_cty.dat)
--no-color              Disable ANSI colorization of the decode output

ADIF / NEEDED detection:
--adif FILE [FILE ...]  Additional ADIF log file(s) for NEEDED detection
                        (auto-discovered WSJT-X logs are always loaded if found)
--no-need-entity        Disable DXCC-entity NEEDED check
--no-need-cqz           Disable CQ-zone NEEDED check
--no-need-country       Disable country-name NEEDED check
--qsl-only              Require LoTW confirmation (lotw_qsl_rcvd=Y) for NEEDED checks;
                        unconfirmed QSOs shown with ❓ instead of ✅
--worked-lag-days N     Suppress NEEDED/MATCH for a callsign worked within N days
                        (default 0 = suppress today; -1 = disable suppression entirely)
--adif-change-script S  Script to exec when ADIF log file(s) are reloaded;
                        reloaded file paths are passed as arguments

Pattern matching / alerts:
--match-calls FILE      File(s) of callsign regex patterns (one per line, # = comment)
--match-message FILE    File(s) of decoded-message regex patterns
--pota                  Flag CQ POTA calls as MATCH
--sota                  Flag CQ SOTA calls as MATCH
--iota                  Flag CQ IOTA calls as MATCH
--match-state STATES    Comma-separated US state abbreviations (e.g. TX,CA) or a file
--hamdat DB             Path to hamdat SQLite database (default: ~/.hamdat/hamdat.db)
--was                   Flag US states not yet LoTW-confirmed as NEEDED (ARRL WAS award);
                        requires --hamdat; mutually exclusive with --5bwas
--5bwas                 Flag US states not yet LoTW-confirmed on the current band as NEEDED
                        (ARRL 5-Band WAS award); requires --hamdat; mutually exclusive with --was
--alert-command SCRIPT  Script to exec on NEEDED/MATCH events; alert message is arg 1
--alert-ntfy TOPIC      POST NEEDED/MATCH alerts to ntfy.sh topic TOPIC

Networking:
--proxy PORT[,PORT...]  Forward every received packet to these additional UDP ports on
                        localhost (e.g. --proxy 2238 or --proxy 2238,2239)

Diagnostics:
--stats                 Cycle stats are always shown in the status bar
--debug                 Dump raw packet hex and parsed fields to decode pane
```

## WSJT-X Configuration

In WSJT-X: **Settings → Reporting → UDP Server**

- Enable UDP server
- Set port to match `--port` (default 2237)
- Check **"Accept UDP requests"** to enable the CMD> reply feature
- **Uncheck "CQ only"** if using `--all` — when this box is checked WSJT-X only
  retains CQ decodes in its internal call list, so attempting to reply to a
  non-CQ decode via the UDP Reply message will fail silently (WSJT-X broadcasts
  the decode over UDP but has nothing to double-click internally)

## Known Limitations

- ADIF hot-reload on file change may lag by one receive cycle (~0.2 s)
- No mouse support in the decode pane
- TX state indicator requires WSJT-X to send Status messages (type 1); always shows RX
  if WSJT-X is not running or the port is misconfigured

## License

See [LICENSE](LICENSE).
