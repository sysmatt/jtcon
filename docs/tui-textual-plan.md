# Textual TUI plan

Phase 9 of the [v3 plan](v3-plan.md): a second terminal front end built on
[Textual](https://textual.textualize.io/), alongside the current prompt_toolkit TUI.
Developed on `dev`, in stages, each one usable and committed.

## Goals

Today's screen is a scrolling log: a wanted station appears once and scrolls away.
The new front end's main view is a **live table of the stations you want right
now**, one row each, updated in place.  The decode stream stays available beside
it or on its own.

- Keep the current TUI, unchanged, as the default until the new one has proven
  itself in daily use (Matt).  Then the new one becomes the default and the old
  one stays as a fallback for a release or two.
- Use the Engine as it is: it already runs on asyncio and reports everything as
  events.  Engine additions are small and tested on their own.

## Decisions (Matt, 2026-10-14)

| # | Question | Decision |
|---|---|---|
| T1 | Main view | The wanted table; decodes in a separate pane |
| T2 | Layouts | Four: Wanted, Decodes (each full screen), side by side, stacked |
| T3 | Wanted table content | No message text: the call and the looked-up facts, in columns like today's |
| T4 | Decodes in the split layouts | A compact line (time, dB, Hz, message, ★ when wanted).  Full screen keeps today's full line |
| T5 | jtcon's messages (`[qrz]`, `[wanted]`, …) | A small log strip, always visible |
| T6 | Live controls | Rules on/off, and decode scope and display, as **separate** toggles |
| T7 | Starting layout | `[display] layout` if set; otherwise chosen from the terminal size |
| T8 | Wanted table order | Sortable; most recently heard first by default |
| T9 | Status line | Keep today's, plus band and frequency |
| T10 | Old TUI | Kept, and stays the default for now |
| T11 | Cycling layouts | Ctrl-arrows step through them, as well as the F-keys |

## Screen

```
┌ jtcon ── 14.074 MHz  20m  FT8 ── de K2TTA ─────────────── RX [██████░░░░░░░░] odd ┐
│ ##  Call    Entity          CQz ITUz ct ST Grid  dB  Seen  ×  days DXCC CQZ WAS ⭐🌎 Wanted  │
│ 01  VK3XY   Australia        30  59  OC     QF22 -7   12s  6       ❌VK ❌30          DXCC CQZ│
│▶07  K5TX    United States     5   8  NA TX  EM10 +3   15s  3   41d ✅K  ✅5  ❓TX     WAS POTA│
│ ...                                                                               │
├ log ──────────────────────────────────────────────────────────────────────────────┤
│ [qrz] Updates: 3 changed, 1 newly confirmed (LoTW)                                │
│ [wanted] ✅ Now confirmed: WAS TX                                                  │
├───────────────────────────────────────────────────────────────────────────────────┤
│ CMD> _                                                                            │
│ RX [██████░░░] odd FT8 dec=42 cq=18 … de=K2TTA ign:2      F1 help  F2-F5 layout   │
└───────────────────────────────────────────────────────────────────────────────────┘
```

### Wanted table

One row per station that at least one rule wants now.

- **Columns:** `##` (the station's number, as today), call, entity, CQ zone, ITU
  zone, continent, state, grid, SNR, time since last heard, times heard, days since
  last worked, one ✅❓❌ column per award rule (today's status columns), QRZ ⭐,
  LoTW 🌎, and the rules that want it.
- **Updated in place** from the Engine's events; a row is highlighted briefly when
  it changes.  Colours as today: red for award rules, yellow for watchlists only,
  magenta when heard outside a CQ.
- **Leaves the table** when it's no longer wanted (a QSO, a confirmation, an
  ignore), or when it hasn't been heard for `wanted_timeout_minutes` (default 10;
  a config setting under `[display]`).
- **Sorting:** last heard (default), SNR, call, entity; a key cycles them, or click
  a column header.
- **Actions on the selected row:** Enter (or double-click) answers it, `i` ignores
  it.  Typing a number or call at `CMD>` still works.

### Decodes

- **Full screen:** today's lines, with the WANTED line under a wanted station.
- **Side by side and stacked:** a compact line, so the split fits about 200
  columns: time, dB, dt, Hz, message, and ★ for a wanted station.
- Scrolls back without losing its place, as today.

### Layouts

| Key | Layout |
|---|---|
| F2 | Wanted, full screen |
| F3 | Decodes, full screen |
| F4 | Side by side: wanted left, decodes right |
| F5 | Stacked: wanted on top, decodes below |

Ctrl-→ and Ctrl-↓ step forward through the layouts in that order, Ctrl-← and
Ctrl-↑ step back (Matt).  Ctrl-←/→ then no longer jump words at `CMD>`; commands
are short.  Some terminals don't pass Ctrl-arrows through (tmux without
`xterm-keys`, macOS Terminal's desktop switching), so the F-keys stay.

The log strip, command line and status line are in every layout.

Starting layout: `[display] layout = "wanted" | "decodes" | "side" | "stacked"`.
Unset (or `"auto"`), jtcon picks from the terminal size: side by side when it's at
least 200 columns wide, stacked when it's at least 50 rows tall, otherwise Wanted.
Thresholds to be tuned in use.

### Live controls

- **Rules panel** (F6): the `[[wanted]]` rules with a switch each.  Turning one
  off removes its column and its hits at once and re-checks the stations in the
  table; turning it on adds them back.
- **Scope** (F7): check CQs only, or all decodes (`[scope] decodes`).
- **Show** (F8): show CQs and wanted stations, or every decode (`[display] show`).
- Changes last for the session; the config file isn't changed.  The status line
  shows when a setting differs from the config.

### Commands and keys

The command line takes today's commands (`list`, `ignore`, `unignore`, `sync`,
`clear`, `quit`, numbers and calls), with the same unique abbreviations.  F1 shows
the keys.  Ctrl-Q quits.

## Config and command line

```toml
[display]
ui = "classic"                 # or "textual"
layout = "auto"                # "wanted", "decodes", "side", "stacked"
wanted_timeout_minutes = 10
```

`--ui textual` picks it for one run.

## Engine changes

- `set_rule_enabled(name, on)`: rebuild the active rule list from the configured
  one, then re-check the stations in the buffer (the path a watchlist reload
  already takes).  Status columns follow the active rules.
- Live scope and show: setters that change the session's config and re-check.
- Whatever the wanted table needs beyond `SpotEvent` / `StationUpdated` (likely a
  "station no longer wanted" event when it leaves).
- The command table and matching move from `tui/app.py` to a module both front
  ends share.

## Dependencies

Textual becomes a dependency, pinned to an exact, recent version at the time of
stage 1.  pipx installs it with jtcon.  It's pure Python; with its own dependencies
it adds a few MB.  The classic TUI doesn't import it.

## Stages

Each stage ends with tests passing on Python 3.11, 3.12 and 3.14, and a live check
by Matt.

1. **Skeleton.** ✅  `--ui textual`; the app wired to the Engine; full-screen
   Decodes pane, log strip, command line with the shared commands, status line;
   quit and exit cleanup (upload retry, highlight clearing).  Textual 8.2.8.
   Commands moved to `tui/commands.py` and the status text to `render.py`, both
   shared; the classic TUI's tests pass unchanged.  `tests/test_textual.py` runs
   the app headless (skipped where Textual is older than 8.2)
2. **Wanted table.**  Rows from Engine events, columns, colours, leaving the table,
   sorting, Enter to answer, `i` to ignore.
3. **Layouts.**  The four layouts and their keys, the compact decode line, auto
   choice, `[display] layout`.
4. **Live controls.**  Rules panel, scope and show toggles, the Engine setters.
5. **Polish.**  F1 help, mouse, the docs (README section, screenshot), then
   daily use to decide when it becomes the default.

## Testing

- Textual's headless test runner (`App.run_test()` with a pilot): drive keys,
  feed the Engine recorded packets, and check the table rows, selection, layouts
  and toggles.
- Engine additions get their own unit tests, front-end free.
- The classic TUI's tests stay untouched; both front ends run in the suite.
