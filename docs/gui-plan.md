# GUI plan

Phase 9 of the [v3 plan](v3-plan.md), second part: a native desktop front end on
Qt, through **PySide6**, alongside the classic and Textual screens.  Developed on
`dev`, in stages, each one usable and committed.  Linux first (Matt's daily
driver); Windows is the main target after that; macOS best effort.

## Goals

- A **native, good-looking desktop application** (Matt: not a browser page, not
  Tk): a real window beside WSJT-X, with the system's look on each platform.
- The same Engine as the other front ends, unchanged apart from small additions;
  everything the Textual screen does, done the desktop way.
- Use what a desktop window adds: panels you arrange yourself, menus, a
  right-click menu, proper fonts and colours, dark mode.
- Installed with pipx on every platform, Windows included.

## Decisions (Matt, 2026-10-14)

| # | Question | Decision |
|---|---|---|
| G1 | Toolkit | Qt 6 through **PySide6** (official binding, LGPL).  `PySide6-Essentials` only, the core modules |
| G2 | Native or browser | Native window |
| G3 | Platforms | Linux first; then Windows (the main target); macOS best effort |
| G4 | Alongside the other screens | Yes: `--ui gui` or `[display] ui = "gui"`; the classic screen stays the default |
| G5 | Install size | **Optional**: `pipx install "jtcon[gui] @ git+https://github.com/sysmatt/jtcon"`.  The plain install stays as it is (PySide6-Essentials is ~75 MB to download, 200+ MB installed).  `--ui gui` without it says how to add it |
| G6 | Joining Qt to asyncio | `qasync`: one event loop that runs both Qt and the Engine, so the Engine needs no threads or changes.  `PySide6.QtAsyncio` (official) is the fallback if `qasync` gives trouble |
| G7 | The decode stream | A **table** (sortable columns that line up by themselves) rather than text lines; the facts columns can be hidden and shown, which replaces the Textual screen's compact line |
| G8 | Layout | Dockable panels: Wanted, Decodes, Log.  Drag them anywhere, tab them together, or float them; presets for the four Textual layouts; the window remembers its arrangement |
| G9 | Window state | Size, panel arrangement, column widths and sort order are saved by Qt (`QSettings`), not in `config.toml`: they're the window's, not jtcon's settings |
| G10 | Tray icon, desktop notifications | **No** (parking lot) |
| G11 | Theme | **Dark by default**.  `[display] theme = "dark"` (or `"light"`, or `"system"` to follow the desktop) in `config.toml`; the View menu switches it for the session |
| G12 | Right-click extras | **Look up on QRZ.com** (opens `https://www.qrz.com/db/<call>` in the browser) to start; more later as they come up |
| G13 | Installers | None for now (parking lot): Windows users install with pipx, as Matt will test it |

## The window

```
┌ jtcon ─ 14.074 MHz 20m FT8 ─ K2TTA ─────────────────────────────────────── _ □ × ┐
│ File  View  Station  Rules  Logbook  Help                                         │
├───────────────────────────────────────────────────────┬───────────────────────────┤
│ Wanted                                                │ Decodes                   │
│ ##  Call   QSO Entity        CQz ITUz ST Grid dB Seen │ Time    dB  dt   Hz  Msg  │
│ 02  VK4AB  📶  Australia     30  55     QG62 -14 12s  │ 1230 -14 0.2 1234 W1XYZ … │
│▶07  K5TX   📣  United States 5   8   TX EM10  +3 15s  │ 1230  +3 0.3 2210 CQ POT… │
│ …   (sortable; red / yellow / magenta as today)       │ …  (★ rows tinted)        │
├───────────────────────────────────────────────────────┴───────────────────────────┤
│ Log                                                                               │
│ [qrz] Updates: 3 changed, 1 newly confirmed (LoTW)                                │
│ [wanted] ✅ Now confirmed: WAS TX                                                  │
├───────────────────────────────────────────────────────────────────────────────────┤
│ ● RX  [██████░░░░] odd  FT8  dec=42 cq=18 …  de=K2TTA  ign:2        off:CQZ       │
└───────────────────────────────────────────────────────────────────────────────────┘
```

### Wanted panel

The Textual screen's table, as a Qt table view: the same columns, colours,
QSO stage marks and status columns.

- Click a column header to sort, again to reverse; the selected station stays
  selected when rows move.
- **Double-click or Enter answers**; a single click selects; Delete (or i)
  ignores the station for the session.
- **Right-click menu** on a row: Answer, Ignore for this session, Copy call,
  Look up on QRZ.com.  The Station menu has the same for the selected row.
- A changed row flashes briefly.
- Columns can be hidden, shown and reordered (right-click the header).

### Decodes panel

A table of decodes, newest at the bottom, following new ones until you scroll
back.  Columns: index, time, dB, dt, Hz, mode, message, call, grid, entity, zones,
state, the award status columns.  Wanted rows are tinted (red, yellow, magenta),
with the reasons as a tooltip and in a column.  Hide the columns you don't want;
narrow, it's the Textual compact line.  A CQ row answers like a Wanted row
(double-click, Enter, right-click), so any station calling CQ can be answered,
wanted or not; any row with a call can be ignored.  Right-click › Clear decodes.

### Log panel

The Textual log strip: jtcon's own messages, with the QRZ download ticks on one
line.  Right-click › Copy, Select all, Clear log.

### No command line (Matt, 2026-10-15)

The window has no CMD>: it's a terminal idea, and every command has a desktop
way instead.  The classic and Textual screens keep theirs.

| CMD> | In the window |
|---|---|
| Enter, an index or a call: answer | Double-click or Enter on a Wanted or Decodes row; right-click › Answer; Station › Answer |
| `ignore NN/CALL` | Delete or i on a row; right-click › Ignore; Station › Ignore |
| `ignore` (list), `unignore CALL` | Station › Ignored stations…: the list (from the config or this session), Unignore, Add |
| `sync`, `sync full` | Logbook › Sync, Sync full download |
| `clear` | Right-click › Clear on the Decodes and Log panels |
| `list` | Not needed: the Decodes panel shows it |
| `quit` | File › Quit, Ctrl-Q |
| ↑↓ through Wanted | Click or Tab into a table, then the arrow keys |

### Locked panels (Matt, 2026-10-15)

The panels' title bars take room once the panels are where you want them.
**View › Lock panels** (on by default, remembered) hides them: the panels sit
flush and can't be dragged or floated by accident; the dividers between them
still resize.  Unlock to move panels, then lock again.

### Status bar

RX / TX / UDP DOWN badge, the cycle bargraph, counts, `de=`, `ign:`, and at the
end what differs from the config file this session (`scope:all off:CQZ`).

### Menus

| Menu | Items |
|---|---|
| File | Settings (Ctrl-,), Edit config file (opens it in the system's editor), Reload config (Ctrl-R; see the [config editor plan](config-editor-plan.md)), Quit |
| View | Layout presets (Wanted, Decodes, Side by side, Stacked), show or hide each panel, lock panels, font size, theme (dark, light, system; for this session), clear decodes and log, reset the window layout |
| Station | For the selected row (Wanted or Decodes): Answer, Ignore for this session, Copy call, Look up on QRZ.com; Ignored stations… |
| Rules | A check item per `[[wanted]]` rule; Check all decodes (scope); Show all decodes (show).  For this session only, as on the Textual screen |
| Logbook | Sync, Sync full download (shown when QRZ is set up) |
| Help | Keys, About (version, licence, links) |

### Keys

As close to the Textual screen as the desktop allows: F2–F5 and Ctrl-arrows for
layouts, F6 rules menu, F7 scope, F8 show, Ctrl-S sort, F1 keys, Ctrl-Q quit;
Enter answers and Delete ignores in the tables.

### Look and feel

- Dark theme by default (G11), set with Qt's Fusion style and a dark palette so
  it looks the same on every platform; light uses Fusion's light palette;
  system leaves the platform's own style alone.
- The Wanted colours (red, yellow, magenta) are chosen to read well on both.
- An application icon (a placeholder until there's a real one).

## Shared code

Some of the Textual screen's code isn't Textual-specific and the GUI needs it
too: the Wanted table's rows (`Row`, columns, sort orders, QSO stage marks), the
status line text, and the commands (which the window ended up not using).  Stage 1 moves those into a package both use,
`src/jtcon/ui/` (with `tui/commands.py` and the status helpers in `tui/render.py`),
leaving the Textual and classic screens behaving exactly as now; their tests
show it.

```
src/jtcon/ui/          shared by the front ends: commands, wanted rows, status text
src/jtcon/gui/         app.py (start-up, qasync loop), window.py, models.py (table
                       models), widgets.py, resources (icon)
```

## Engine changes

None expected beyond what the Textual screen added.  If the GUI needs something
(say, an event for the bargraph's timing), it's added with its own tests.

## Dependencies

```toml
[project.optional-dependencies]
gui = ["PySide6-Essentials==6.11.2", "qasync==0.28.0"]
```

Exact versions.  PySide6's wheels are abi3, one per platform for every Python
from 3.10, on Linux, Windows and macOS (13 and later).  Tests add `pytest-qt==4.5.0`.
The classic and Textual screens never import Qt.

**Not 6.12.0** (2026-10-08, the newest): on Python 3.11 every Qt call that
returns nothing takes a reference from `None`, and the interpreter aborts
("deallocating None") within minutes.  Python 3.12 and later aren't affected
(their `None` is immortal), and 6.11.2 is fine on all of them.  Before moving
up, run the suite on 3.11; the check is a loop of `bar.setValue(0)` watching
`sys.getrefcount(None)`.

## Testing

- Qt's `offscreen` platform runs the window with no display, so the suite runs
  here and anywhere: `pytest-qt` drives keys and clicks, and checks the models,
  selection, menus and layouts.
- The table models are tested directly, without a window.
- GUI tests skip where PySide6 isn't installed.
- Live checks by Matt on Linux at each stage; on Windows and macOS in stage 7.

## Stages

1. **Shared code and skeleton.** ✅ (2026-10-15)  Move the shared pieces to `jtcon/ui/`.  `--ui gui`
   (and the message when PySide6 is missing); the window on a `qasync` loop with
   the Engine; Decodes panel, Log panel, command line, status bar; quit and exit
   cleanup (upload retry, highlight clearing); fatal errors in a message box.
2. **Wanted panel.** ✅ (2026-10-15)  Model and view, columns, colours, sorting, selection, answer,
   ignore, right-click menu, changed rows.
3. **Panels and layouts.** ✅ (2026-10-15)  Docking, presets and their keys, hide and show
   columns, the window remembering itself.
4. **Menus and live controls.** ✅ (2026-10-15)  File, View, Rules (rules, scope, show), Logbook,
   Help; the keys.  Tooltips on the panels' title bars and buttons, and a hint in
   the log at startup (Matt, 2026-10-15: the panels' drag-and-drop and F2–F5
   weren't easy to find).  A row of layout buttons came and went: View › Layout
   does the job without taking up room (Matt).
5. **Look and feel.** ✅ (2026-10-15)  Themes (`[display] theme` and the View menu), fonts,
   icon, About.
6. **A desktop app, not a terminal.** ✅ (2026-10-15)  No command line (Matt: "make
   the gui work more like a native gui app"): answering and ignoring from the
   Decodes panel too, a Station menu, Station › Ignored stations…, Clear on the
   panels' right-click menus, Delete to ignore.  View › Lock panels hides the
   panels' title bars.
7. **Windows and macOS.**  Deferred until Matt has a Windows machine and a Mac
   to test on: run and fix with pipx; docs (README, configuration).

## Parking lot

- Tray icon and desktop notifications (G10).
- Binary installers: PyInstaller, an Inno Setup installer for Windows, a macOS
  app bundle (G13).
- More right-click extras (G12): other lookups, logbook views.
