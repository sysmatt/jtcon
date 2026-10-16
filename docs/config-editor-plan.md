# Config editor plan

The desktop window's settings editor: edit the startup settings in
`config.toml` (and later the profiles) from the window, save, and carry on with
the new settings without quitting.  Part of the [GUI](gui-plan.md); the classic
and Textual screens keep `--edit-config`.

## Decisions (Matt, 2026-10-15)

| # | Question | Decision |
|---|---|---|
| E1 | Writing TOML | **tomlkit**, pinned, in the `gui` extra only: it edits the file in place and keeps its comments, order and layout.  (Python itself only reads TOML.) |
| E2 | "Reload on save" | **Restart in the window**: a new Engine with the new settings, in the same window.  Not live changes setting by setting (the Engine reads its settings once) |
| E3 | The command line | **Always wins**, after a reload too, profiles included.  The editor shows which settings the command line overrides this run |
| E4 | Remembering | Nothing between runs: no default profile, no "last profile".  Each start is what the command line and the config say |
| E5 | Profiles | Later (stage D), with a profile selector and a profile editor.  In a profile, each setting has a **"set here"** checkbox |
| E6 | The API key | An ordinary field, shown as typed: it's plain text in the file anyway.  Still never written to the log |
| E7 | File › Edit config file | Stays, for editing by hand |

## Saving

- The file is changed in place (tomlkit): only the settings you changed are
  touched; comments and everything else stay as they are.  A setting that's
  only a commented-out default in the file is added to its table.
- Before anything is written, the new file is checked with jtcon's own checks
  (the ones `--show-config` runs), so a bad value is shown in the editor and
  the file stays as it was.
- The old file is kept as `config.toml.bak`; both are private (0600).  The new
  file replaces the old in one step, so a crash mid-save can't leave half a file.

## Reloading (restart in the window)

**File › Reload config** (Ctrl-R), and the editor's Save, do the same:

1. Read the config again, with the same command line (`--port`, `--scope`,
   `--wanted`, `--ignore`, `--profile` … still win).  A config error is shown
   and jtcon carries on as it was.
2. Bind the UDP port for the new settings first; if that fails (the port is
   taken), say so and carry on as it was.
3. Stop the Engine, and start a new one with the new settings in a new window
   in the same place: the panels, columns, text size and theme come back as
   they were (window.ini), the decodes start empty, the log carries over.
4. Kept for the session: the stations ignored this session, and rules switched
   off (where the rule still exists).  QRZ uploads still waiting are saved and
   retried by the new Engine, as at a restart.

## The editor (File › Settings…)

A dialog with a page per section, built from the config's own list of settings
(`_SCHEMA` in config.py), so new settings appear without new editor code:

| Setting kind | Field |
|---|---|
| true / false | checkbox |
| one of a few values | drop-down |
| number | number box |
| path | text box and Browse… |
| colour | colour button (colour picker) |
| list (ignore, adif, proxy) | small list editor: Add, Remove |

- Each field has its explanation from the starter config's comments, and its
  default.  A setting left at its default isn't written to the file.
- **Overridden by the command line:** a setting the command line sets this run
  is marked, e.g. *"--port 2238 on the command line wins this run"*, with the
  value in use.  It can still be edited: the file changes, the command line
  keeps winning until jtcon starts without it.
- Save (check, write, reload), Cancel; errors shown beside the field.
- **Wanted rules page** (stage C): the `[[wanted]]` list.  Add, remove, move
  up and down; each rule's own settings (name, confirm sources, files, states).
  `--wanted` on the command line overrides the whole list and is marked so.

## Profiles (stage D)

- **Profile menu:** the profiles in `profiles/` and "(none)".  Picking one
  reloads with it, for this session; the next start is the command line's
  again (E3, E4).
- **The editor on a profile:** the same pages, editing the profile file; each
  setting has a **set here** checkbox.  Unticked: the main config's value, greyed.
  Ticked: written to the profile.  The Wanted rules page has one checkbox for
  the whole list, because a profile with any `[[wanted]]` entries replaces the
  main config's whole list (as now); ticking it starts the profile's list as a
  copy of the main config's rules.
- New profile (a copy of the config, as `--edit-config --profile` makes one),
  Duplicate, Delete.

## Stages

A. **Save and reload.** ✅ (2026-10-15)  tomlkit; the file editing and checking code (no Qt),
   saving with the backup; restart in the window; File › Reload config;
   Edit config file says to use it.
B. **Settings dialog.**  The section pages, explanations, command-line marks.
C. **Wanted rules page.**
D. **Profiles.**  Selector, editor with "set here", new / duplicate / delete.

## Testing

- The file editing code is tested without Qt: comments survive, only changed
  settings change, bad values are refused before writing, the backup, 0600.
- Reload: the new Engine and window, the command line still winning, the
  session's ignores and rule switches kept, a bad config or a taken port
  leaving jtcon running as it was.
- Tests never touch the real config folder (`XDG_CONFIG_HOME` in tmp) and never
  read a real API key.
