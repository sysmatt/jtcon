# jtcon

> **⚠️ EXTREMELY EXPERIMENTAL — NOT YET COMPLETELY FUNCTIONAL — USE AT YOUR OWN RISK ⚠️**
>
> This software is under active development. Expect bugs, missing features,
> crashes, and breaking changes between versions. It has not been extensively
> tested against live WSJT-X instances. Do not rely on it for anything important.

---

A full-screen TUI monitor for [WSJT-X](https://wsjt.sourceforge.io/) UDP broadcasts.
Displays CQ calls in real time, enriched with DXCC entity, CQ zone, country, and grid
information from the [AD1C Big CTY](https://www.country-files.com/) prefix database.
Optionally compares against an ADIF logbook to flag contacts not yet worked.

## Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  HHMMSSZ   dB   dt(s)    Hz  mode  message           callsign  │  ← column header
│  ──────────────────────────────────────────────────────────────  │
│  123045z  +05   +0.1s  1234  FT8   CQ W1ABC FN42     W1ABC     │  ← scrollable
│  123100z  -12   -0.3s  2100  FT8   CQ DX VK3XY QF22  VK3XY    │    decode pane
│  ...                                                            │    (Tab to focus
│                                                                 │     and scroll)
├─────────────────────────────────────────────────────────────────┤
│  CMD>                                                           │  ← command input
├─────────────────────────────────────────────────────────────────┤
│  RX  [████████░░░░░░] even  FT8  dec=42 cq=18  avg dec=…      │  ← status line
└─────────────────────────────────────────────────────────────────┘
```

**Status line fields:**

| Field | Description |
|---|---|
| `RX` / `TX` | Current transmit/receive state reported by WSJT-X (TX shown in red) |
| `[████████░░░░░░]` | Bargraph showing position within the current 15 s (FT8) or 7.5 s (FT4) cycle |
| `even` / `odd` | Parity of the current TX slot |
| `FT8` / `FT4` | Active mode as reported by WSJT-X |
| `dec=N` | Decodes in the current cycle |
| `cq=N` | CQ calls in the current cycle |
| `avg dec=N.N cq=N.N` | Running average decodes and CQ calls per cycle |
| `even dec=N.N cq=N.N` | Per-parity running averages |
| `odd dec=N.N cq=N.N` | Per-parity running averages |

## Requirements

- Python 3.10+
- `prompt-toolkit` >= 3.0  (`pip install prompt-toolkit` or your OS package manager)
- `cty.dat` — downloaded automatically from country-files.com on first run

Optional:
- An ADIF log file (`--adif`) for NEEDED detection
- [hamdat](https://github.com/ad1c/hamdat) SQLite database for US state lookups (`--match-state`)

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

# With ADIF log — bell + NEEDED flag for new entities/zones/countries
./jtcon --adif ~/Documents/wsjtx_log.adi

# Save every CQ record to a JSON-lines file
./jtcon --save cq.jsonl

# Show all decodes (not just CQ) with wall-clock timestamps
./jtcon --raw --show-time

# Flag POTA and SOTA activations
./jtcon --pota --sota

# Match specific callsigns from a file
./jtcon --match-calls watchlist.txt

# Only alert on new DXCC entities, suppress zone/country checks
./jtcon --adif ~/wsjtx_log.adi --no-need-cqz --no-need-country
```

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
| `L` | Send a WSJT-X reply to the last NEEDED contact this session |
| `W1ABC` *(any callsign)* | Send a WSJT-X reply to the most recent decode from that callsign |

Replies use the WSJT-X UDP Reply message (type 4), equivalent to double-clicking a
decode in the WSJT-X UI.  WSJT-X must have **Settings → Reporting → Accept UDP requests**
checked, and must have received at least one decode from the target callsign this session.

## Options

```
--host HOST           UDP bind address (default: 0.0.0.0)
--port PORT           UDP port matching WSJT-X Settings → Reporting (default: 2237)
--show-time           Prepend local wall-clock HH:MM:SS to each line
--raw                 Show ALL decoded messages, not just CQ
--save FILE           Append each CQ record as JSON to FILE
--cty PATH            Path to cty.dat (default: ~/.jtcon_cty.dat)
--adif FILE           ADIF log file for NEEDED detection
--no-need-entity      Disable DXCC-entity NEEDED check
--no-need-cqz         Disable CQ-zone NEEDED check
--no-need-country     Disable country-name NEEDED check
--match-calls FILE    File(s) of callsign regex patterns (one per line)
--match-message FILE  File(s) of decoded-message regex patterns (one per line)
--pota                Flag CQ POTA calls as MATCH
--sota                Flag CQ SOTA calls as MATCH
--iota                Flag CQ IOTA calls as MATCH
--match-state STATES  Comma-separated US state abbreviations or a file
--hamdat DB           Path to hamdat SQLite database
--alert-command SCRIPT  Script to exec on NEEDED/MATCH events
--alert-ntfy TOPIC    POST NEEDED/MATCH alerts to ntfy.sh topic TOPIC
--debug               Dump raw packet hex and parsed fields to decode pane
```

## WSJT-X Configuration

In WSJT-X: **Settings → Reporting → UDP Server**

- Enable UDP server
- Set port to match `--port` (default 2237)
- Check **"Accept UDP requests"** if you plan to use reply/TX features

## Known Limitations / TODO

- `--call` interactive prompt (auto-reply to WSJT-X) not yet implemented in TUI mode
- ADIF reload on file change works but may lag one cycle
- No mouse support
- Color/highlighting in the decode pane not yet implemented (ANSI codes stripped)
- TX state indicator depends on WSJT-X sending Status messages (type 1); if WSJT-X
  is not running or the port is wrong, it will always show RX

## License

See [LICENSE](LICENSE).
