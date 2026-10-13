# jtcon v3 plan

Working document for the v3 rewrite, developed on the `v3` branch.  Tick items off as
they land; update the design sections when a decision changes.

## Goals

1. **Split the core from the UI.**  The core knows nothing about screens.  The TUI is
   one front end; a GUI can be another, built on the same core.
2. **Make "wanted" logic modular.**  Adding a new award or watch type means writing one
   rule class, not editing six places.
3. **Model confirmations properly.**  Each rule decides which confirmation sources
   count (LoTW, QRZ, eQSL, paper card).
4. **Configuration files with profiles**, so an operator can keep e.g. a DX profile
   and a POTA profile.
5. **Built-in QRZ Logbook integration**, replacing the `--adif-change-script` → qrzlog
   hop, and giving faster confirmation updates.
6. Keep the TUI first-class, and improve it.

v3 is a **breaking release**: the command-line flags are redesigned and the old ones
are dropped.  It ships as `v3.0.0` when the branch merges to `main`.

## Architecture

```
        INPUTS                         EVENTS (UDP from WSJT-X)
  cty.dat, ADIF logs, QRZ           Status, Decode, QSO Logged,
  logbook, hamdat, watchlists       Logged ADIF, Clear, Close…
           │                                 │
           │                          enrich: Decode → Spot
           │                          (call, grid, entity, zones, state)
           └────────────►  WANTED RULES  ◄───┘
                           each returns Hits tagged with their source
                                     │
                           filters (worked-recently, alert dedupe)
                                     │
               ┌─────────────────────┼─────────────────────┐
            ACTIONS            STATION TABLE              UI(s)
   reply / highlight in WSJT-X, (rolling buffer)     TUI now,
   QRZ upload, scripts, ntfy,                         GUI later
   bell, JSON log, proxy
```

### Inputs

Reference data the core reads, watches, and reloads.  Common interface:
`load()`, change detection, and an `InputReloaded` event when the data changes.

| Input | Provides |
|---|---|
| cty.dat | Prefix → DXCC entity, CQ/ITU zone, continent (auto-download, 30-day refresh) |
| ADIF file(s) | QSO records (WSJT-X log auto-discovered, plus any configured files) |
| QRZ Logbook | QSO records with confirmation status, fetched via the QRZ API |
| hamdat | US callsign → state |
| Watchlist files | Callsign / message regex lists, state lists (reloaded on edit) |

**Logbook** merges every QSO source into one list of normalized QSO records:

```
QSO(call, date, time, band, mode, grid, dxcc, cqz, ituz, state, country,
    confirmations={"lotw", "qrz", "eqsl", "card"})
```

The same contact often appears in several sources (the WSJT-X log *and* the QRZ
download).  Records are matched (call + band + mode + time within a window) and their
confirmations are merged, so a QSO confirmed only in the QRZ download still counts as
confirmed.  Rules build their own indexes from this list; the Logbook does not grow a
new set per award.

Confirmation sources, from ADIF fields:

| Source | ADIF field |
|---|---|
| `lotw` | `lotw_qsl_rcvd = Y` |
| `qrz`  | `app_qrzlog_status = C` |
| `eqsl` | `eqsl_qsl_rcvd = Y` |
| `card` | `qsl_rcvd = Y` |

### Events

Typed dataclasses for the WSJT-X UDP protocol, plus internal events.

| WSJT-X message | Use |
|---|---|
| 0 Heartbeat | Liveness; detect WSJT-X instance id |
| 1 Status | TX/RX, dial frequency, mode, DE call |
| 2 Decode | The main feed |
| 3 Clear | Band Activity cleared |
| 5 QSO Logged | Mark a call worked immediately (new in v3) |
| 6 Close | WSJT-X exited |
| 12 Logged ADIF | The logged QSO as ADIF text, ready to upload to QRZ (new in v3) |

Internal events: `SpotEvaluated` (a decode after enrichment and rules), `DialChanged`,
`InputReloaded`, `ActionResult`, `Message` (log line for the UI).

### Wanted rules

One concept, one label: **WANTED**.  Every hit names the rule that produced it, so
the user always sees *why* a station is wanted:

```
*** WANTED: DXCC(VK) · WAS-ARRL(MA) · POTA · CALLS:VK.*[watchlist.txt] ***
```

Sketch of the rule interface (details settle in phase 5):

```python
class WantedRule:
    key   = "was"                     # used in config and --wanted
    label = "Worked All States"       # shown in a GUI checklist / help
    status_column = True              # award rules show ✅❓❌ in a column

    def configure(self, settings: dict) -> None: ...
    def rebuild(self, logbook: Logbook, inputs: Inputs) -> None: ...   # on load/reload
    def evaluate(self, spot: Spot, ctx: Context) -> Hit | None: ...

@dataclass
class Hit:
    rule:   str            # instance name, e.g. "WAS-ARRL"
    detail: str            # "MA", "VK", "MA/20m", "VK.*[watchlist.txt]"
    status: Status | None  # NEW / WORKED / CONFIRMED for award rules; None for watch rules
    fires:  bool           # True → the station is WANTED by this rule
```

- **Award rules** report a status.  NEW or WORKED-but-unconfirmed means wanted, and
  the status drives the ✅❓❌ icons.  Each rule takes a `confirm` setting listing the
  confirmation sources that count, with a sensible default.
- **Watch rules** (activations, watchlists) just hit or don't.
- A rule can appear **more than once** with different settings and its own `name`,
  e.g. WAS counted by LoTW (ARRL award) and WAS counted by QRZ (QRZ award).
- Each rule implements its own logic.  The framework only supplies the Spot, the
  Logbook, and the plumbing.

Planned built-in rules:

| Rule | Kind | Default `confirm` | Notes |
|---|---|---|---|
| `dxcc` | award | lotw | Per entity |
| `cqz` | award | lotw | Per CQ zone |
| `ituz` | award | lotw | New |
| `country` | award | lotw | Country name |
| `was` | award | lotw | Per US state; needs hamdat |
| `5bwas` | award | lotw | Per state × band, 5 WAS bands |
| `grid` | award | any | New; 4-character grid |
| `pota` / `sota` / `iota` | watch | — | Keyword in the CQ |
| `callsigns` | watch | — | Regex list file |
| `message` | watch | — | Regex list over the decoded text |
| `states` | watch | — | List of states |

Later candidates: per-band / per-mode variants ("new DXCC on this band"), continent,
WAZ, VUCC.

**Filters** run after the rules: suppress a station worked within N days, and
de-duplicate repeated alerts (re-fire after an hour unheard).  Scope setting: check
CQ decodes only, or all decodes (today's `--all`).

### Actions

Everything jtcon does outward.  Actions are triggered by events and routed by config.

| Action | Trigger | Notes |
|---|---|---|
| Reply (WSJT-X 4) | User command | Same as double-clicking the decode |
| Highlight Callsign (WSJT-X 13) | WANTED hit | **New.** Colours the station in WSJT-X's Band Activity; cleared when no longer wanted |
| QRZ upload | QSO Logged / Logged ADIF | **New.** Replaces the change-script hop |
| Script | WANTED hit, QSO logged, input reloaded | Generalized `--alert-command` / `--adif-change-script` |
| ntfy | WANTED hit | Background thread, as today |
| Bell | WANTED hit | |
| JSON log | Every evaluated spot | Today's `--save` |
| Proxy | Every packet | Today's `--proxy`; multicast may make it unnecessary |

### QRZ integration

QRZ is both an **input** and an **action**:

- **Input:** fetch the QRZ logbook (with `app_qrzlog_status` and the LoTW fields QRZ
  carries), cache it locally, and refresh it periodically with an incremental fetch
  (records modified since the last fetch, to verify against the QRZ API docs).  New
  confirmations reach the rules without a manual download.
- **Action:** on a Logged ADIF event, INSERT the QSO into the QRZ logbook.  Duplicate
  responses are treated as success.  Results are shown in the UI.

The QRZ API code already exists in the sister project `qrzlog` (`../qrzlog`).  See
open decision D2 on sharing it.

### Engine

`Engine` connects inputs, events, rules, filters and actions, and owns the station
table (rolling buffer) and alert state.  It runs on asyncio (prompt_toolkit and
Textual both run on asyncio, so either UI can share the loop).

- Front ends **subscribe** to events (`SpotEvaluated`, `Status`, `Message`, …).
- Front ends **call commands**: `reply(call_or_index)`, `clear_buffer()`,
  `reload_inputs()`, `set_rule_enabled(name, on)`.
- No display formatting in the core.  Columns, colours and icons are the UI's job,
  driven by structured data (Spot + Hits).

### Configuration

TOML, with profiles.  Implemented in phase 2 (`src/jtcon/config.py`).

Layers, later ones winning: built-in defaults → `~/.config/jtcon/config.toml` (or
`--config FILE`; `$XDG_CONFIG_HOME` is honoured) → `profiles/<name>.toml` next to
it (`--profile NAME`, or a path) → command-line flags.

- **Merging:** tables merge setting by setting; every other value, lists included,
  replaces.  A profile with `[[wanted]]` entries replaces the whole rule list; a
  profile without any keeps the base file's rules.
- **Paths** expand `~`; relative paths are relative to the file they're written in
  (CLI paths: the current directory).
- **Validation:** unknown settings and wrong types are errors naming the file and
  the setting, so typos never pass silently.  Exit status 2.
- **No config file:** rules dxcc, cqz and country, counted by LoTW confirmation.
- **`confirm`** lists the confirmation sources that count; `[]` means worked is
  enough.  Default `["lotw"]` for every award rule (Matt: the ❓ icon then shows
  worked-but-unconfirmed).

Current shape (phase 7 adds `[inputs.qrz]` and the QRZ upload action; phase 6 may
add bell/highlight routing under `[actions]`):

```toml
[udp]
host  = "0.0.0.0"
port  = 2237
proxy = [2238]                # forward packets to localhost ports
# multicast_group = "224.0.0.73"    (D3, later)

[display]
show      = "cq"              # "cq": CQs plus wanted hits; "all": every decode
show_time = false
color     = true
mouse     = false
debug     = false

[scope]
decodes = "cq"                # check "cq" decodes only, or "all"

[inputs]
adif   = ["~/logs/old_contest.adi"]   # WSJT-X log is auto-discovered as well
hamdat = "~/.hamdat/hamdat.db"
cty    = "~/.jtcon_cty.dat"

[filters]
suppress_worked_within_days = 5       # 0 = today only; -1 = never suppress

[[wanted]]
rule = "dxcc"                 # confirm = ["lotw"] by default

[[wanted]]
rule    = "was"
name    = "WAS-ARRL"
confirm = ["lotw"]

[[wanted]]
rule  = "callsigns"
files = ["~/watchlist.txt"]

[[wanted]]
rule   = "states"
states = ["AK", "DE"]         # or file = "states.txt"

[actions.ntfy]
topic = "my-topic"

[actions.script]
on_wanted     = "~/bin/alert.sh"      # alert text as $1
on_log_reload = "~/bin/upload.sh"     # ADIF paths as arguments

[actions.json_log]
file = "~/jtcon-spots.jsonl"
```

CLI (the v2 flags are gone):

```
jtcon [--config FILE] [--profile NAME]
      [--wanted dxcc,was,pota,...]       rules or [[wanted]] names for this run; "none"
                                         RULE:VALUE sets the rule's files/list/confirm:
                                         callsigns:a.txt+b.txt, states:AK+DE,
                                         states:my.txt, dxcc:worked
      [--scope cq|all]
      [--port N] [--save FILE] [--mouse] [--no-color] [--debug]
jtcon --list-rules                       available rules and their settings
jtcon --show-config                      the merged effective config, as TOML
jtcon --init-config                      write a commented starter config.toml
```

v2 → v3 migration:

| v2 flag | v3 |
|---|---|
| `--host`, `--port`, `--proxy 2238,2239` | `[udp] host`, `port`, `proxy = [2238, 2239]`; `--port` stays |
| `--raw` | `[display] show = "all"` |
| `--all` | `[scope] decodes = "all"`, or `--scope all` |
| `--show-time` | `[display] show_time = true` |
| `--no-color`, `--mouse`, `--debug` | `[display]`; the flags stay |
| `--cty`, `--adif`, `--hamdat` | `[inputs] cty`, `adif = [...]`, `hamdat` |
| default NEEDED checks | `[[wanted]]` dxcc, cqz, country, now `confirm = ["lotw"]` by default; `confirm = []` is v2's behaviour |
| `--qsl-only` | the default now |
| `--no-need-entity/-cqz/-country` | leave that rule out of `[[wanted]]` |
| `--was`, `--5bwas` | `[[wanted]] rule = "was"` / `"5bwas"` |
| `--pota`, `--sota`, `--iota` | `[[wanted]] rule = "pota"` etc. |
| `--match-calls F...` | `[[wanted]] rule = "callsigns"`, `files = [...]` |
| `--match-message F...` | `[[wanted]] rule = "message"`, `files = [...]` |
| `--match-state AK,DE` | `[[wanted]] rule = "states"`, `states = [...]` or `file` |
| `--worked-lag-days N` | `[filters] suppress_worked_within_days = N`; default now 5 (v2: 0) |
| `--alert-command S` | `[actions.script] on_wanted` |
| `--alert-ntfy T` | `[actions.ntfy] topic` |
| `--adif-change-script S` | `[actions.script] on_log_reload` |
| `--save F` | `[actions.json_log] file`; `--save` stays |
| `--stats` | removed (did nothing) |

`tests/v2flags.py` implements this table; the render snapshot runs through it, which
proves each translation renders exactly as v2 did.

## Package layout

Target layout (`src/` layout, adopted in phase 1; later phases fill it in):

```
jtcon                    launcher: run from a checkout (puts src/ on the path)
src/jtcon/
  __init__.py  __main__.py
  cli.py                 argparse → Config, picks a front end
  config.py              TOML load, profiles, defaults, validation
  core/
    engine.py            the hub: event loop, station table, alert state
    events.py            event dataclasses
    protocol.py          WSJT-X packet parse/encode (all message types)
    spot.py              Decode → Spot enrichment
    stations.py          rolling buffer
    inputs/              cty.py, adif.py, logbook.py, qrz.py, hamdat.py, watchlist.py
    wanted/              base.py, awards.py, activations.py, watchlists.py, registry.py
    filters.py
    actions/             wsjtx.py, qrz.py, script.py, ntfy.py, jsonlog.py, proxy.py
  tui/                   prompt_toolkit UI (Textual later, see phase 9)
tests/
  fixtures/              recorded UDP packets, sample ADIF, cty.dat snapshot
```

## Phases

Each phase ends with the tests passing and jtcon runnable.

### Phase 0: safety net ✅
- [x] pytest set up (`python3 -m pytest`), `tests/` with fixtures: a sample ADIF log,
      a pinned cty.dat, a fake and a real (SQLite) hamdat, and a WSJT-X packet builder
      (`tests/wsjtx_packets.py`)
- [x] Characterization tests: packet parsing and Reply encoding, cty lookup
      (compound calls, overrides), ADIF parsing and the worked index, NEEDED/MATCH
      rules, alerts, JSON records, rolling buffer, CMD> commands, output pane
- [x] Rendered-output snapshot (`tests/snapshots/render_v2.txt`): 11 option sets ×
      3 bands × 17 messages × FT8/FT4.  Regenerate on purpose with
      `JTCON_UPDATE_SNAPSHOTS=1 python3 -m pytest tests/test_render_snapshot.py`
- [x] End-to-end test: real `./jtcon` in a pseudo-terminal with a throwaway HOME, fed
      UDP packets; checks startup, replies received, `list`, ADIF hot reload, dial
      change, quit and exit codes.  Black-box, so it guards every later phase
- [x] Checked the net catches breakage: a column-width change and an off-by-one in
      worked-lag each fail a test
- [x] `build_parser()` extracted from `main()` so tests use the real flag defaults

### Phase 1: package split (mechanical) ✅
- [x] `jtcon.py` split into the `src/jtcon/` package with no behaviour change; code
      moved by line range, names kept (renaming waits for the rewrite phases):

      | Module | From v2 |
      |---|---|
      | `__init__.py` | usage docstring, `__version__` |
      | `cli.py` | `build_parser`, `main` |
      | `core/protocol.py` | packet constants, reader, Decode/Status parsing, Reply encoder |
      | `core/cty.py` | cty.dat parser and download |
      | `core/adif.py` | log discovery, ADIF parser, `AdifLog` |
      | `core/hamdat.py` | hamdat lookup, state lists |
      | `core/bands.py` | band table, WAS states, 5BWAS bands |
      | `core/messages.py` | callsign patterns, CQ parser, directed sender |
      | `core/patterns.py` | regex pattern files |
      | `core/alerts.py` | alert message, script, ntfy |
      | `core/cycle.py` | cycle tracker |
      | `core/decode.py` | `DecodeContext`/`DecodeResult`, classify, alert dedupe, JSON, `handle_packet` |
      | `tui/render.py` | colours, column layout, `_render`, header, bargraph |
      | `tui/app.py` | `JtconTUI` |

- [x] `src/` layout keeps the `./jtcon` launcher name (it puts `src/` on the path);
      `python3 -m jtcon` works too.  Version detection follows the new depth
- [x] pyproject: package discovery under `src/`, entry point `jtcon.cli:main`;
      wheel builds, installs and runs (`jtcon --version`, `python3 -m jtcon`)
- [x] Tests import from the new modules; all 159 pass, render snapshot unchanged
- [x] Known wart, on purpose: `core/decode.py` imports `tui/render.py` because
      `handle_packet` still renders.  Phase 3 removes it
- [x] **Checkpoint: real-world test by Matt** on a live WSJT-X session; no issues found

### Phase 2: configuration and new CLI ✅
- [x] `Config` dataclass (`config.py`) replaces `argparse.Namespace` everywhere; the
      core reads it through `cfg.wants(rule)`, `cfg.rule(rule)`, `cfg.evaluate_all`,
      `cfg.show_all` and `cfg.entity_needs_lotw` until phase 5 replaces the v2 checks
- [x] TOML loading, profiles, CLI overrides, validation, `--show-config`,
      `--init-config` (commented template), `--list-rules` (rule registry)
- [x] New CLI; v2 flags removed (they now fail as unrecognized arguments)
- [x] Python 3.11 minimum (D1)
- [x] Startup log shows the config files loaded and the active rules; the ADIF
      summary adds LoTW-confirmed counts
- [x] Labels stay NEEDED/MATCH until phase 5.  Settings v2 code can't honour yet are
      refused with a clear error rather than ignored: a rule used twice, `was` with
      `5bwas`, different `confirm` on dxcc/cqz/country, any `confirm` other than
      `[]`/`["lotw"]` (`["lotw"]` only for was/5bwas)
- [x] Tests: 207 pass.  Render snapshot unchanged via the v2 translator; new
      `test_config.py`; end-to-end tests run from a config file in the temp HOME
- [x] Matt's v2 command line translated (below); `test_config.py` checks the profile
      equals the v2 flags exactly
- [x] **Checkpoint: Matt runs the translated config live** and confirms the
      behaviour matches v2.  His line already used `--qsl-only`, so the new LoTW
      default changes nothing for him; only some startup log lines read differently
- [x] After testing: `--wanted RULE:VALUE` (files/lists on the command line),
      format examples in `--help` / `--list-rules`, and `suppress_worked_within_days`
      defaults to 5

Matt's v2 command line:

```
jtcon --adif /home/k2tta/ham/data/qrzlog.adif /home/k2tta/.local/share/WS/wsjtx_log.adi
      --alert-ntfy k2tta-jtwatch --match-state AK,DE --qsl-only
      --adif-change-script /home/k2tta/ham/up.sh
      --match-calls /home/k2tta/ham/data/wanted.txt /home/k2tta/ham/data/SCARC.txt
      --worked-lag-days 5 --5bwas --all
```

as `~/.config/jtcon/config.toml`: see `K2TTA_V3` in `tests/test_config.py`.

### Phase 3: events and engine
- [ ] **First, from Matt:** auto-discover the WS suite log (WSJT-X Improved's fork,
      https://sourceforge.net/projects/wsjt-x-improved/files/WS_v3.2.1/).  Linux:
      `~/.local/share/WS/wsjtx_log.adi` (seen on Matt's machine); confirm the
      macOS/Windows locations from the release docs/source before adding them
- [ ] Typed events for all WSJT-X messages we use; encoders for Reply and Highlight
- [ ] `Engine` extracted from `JtconTUI`: UDP, station table, dial-change clearing,
      input reload, alert state
- [ ] TUI becomes a subscriber; snapshot tests still pass

### Phase 4: inputs and logbook
- [ ] Input interface with change detection and reload events
- [ ] Logbook of normalized QSO records with confirmation sets, merging duplicates
      across sources
- [ ] Watchlist files reload on edit
- [ ] QSO Logged (type 5) marks a call worked immediately

### Phase 5: wanted rules
- [ ] Rule interface, registry, `--list-rules`
- [ ] Port dxcc, cqz, country, was, 5bwas, pota, sota, iota, callsigns, message, states
- [ ] Per-rule `confirm`; multiple named instances of one rule
- [ ] Unified WANTED display with sources; ✅❓❌ icons driven by Hit status
- [ ] Filters: worked-recently suppression, alert dedupe, scope
- [ ] Display columns built from the enabled rules
- [ ] Snapshot tests updated deliberately for the new output

### Phase 6: actions
- [ ] Action interface and routing from config (`on_wanted`, `on_qso_logged`, …)
- [ ] Reply, Highlight Callsign (set and clear), script, ntfy, bell, JSON log, proxy

### Phase 7: QRZ integration
- [ ] QRZ logbook as an input: full fetch, local cache, periodic incremental refresh
- [ ] QRZ upload action on Logged ADIF
- [ ] API key handling

### Phase 8: docs and release
- [ ] README rewritten for v3: concepts, config reference, rule reference, migration
      notes from v2 flags
- [ ] Merge `v3` into `main`, tag `v3.0.0`

### Phase 9: front ends (separate plans)
- [ ] TUI on Textual: stations table plus live log, select-and-Enter to reply, rule
      toggles
- [ ] GUI, on the same Engine

## v2 quirks found while writing tests

Pinned by the Phase 0 tests so the refactor doesn't change them by accident.  Fix
them deliberately (phase 5 for most), updating the tests as part of the fix.

| # | Quirk | Where |
|---|---|---|
| Q1 | `CQ RR73` is parsed as a CQ from callsign "RR73" | `parse_cq_message` |
| Q2 | A hashed callsign in a CQ (`CQ <W1ABC/P> FN42`) isn't recognised | `parse_cq_message` |
| Q3 | The ✅❓❌ icons sit in front of the *next* label: the DXCC icon is printed before `CQ5`, the CQ-zone icon before `ITU8`, the country icon before the continent | `_render` |
| Q4 | Only LoTW counts as a confirmation; QRZ, eQSL and card are ignored (v3 confirmation sources fix this) | `AdifLog` |
| Q5 | `--raw` applies message patterns (POTA etc.) to non-CQ decodes, but not callsign or entity checks | `_classify` |
| Q6 | cty.dat's `=VERSION` marker line is loaded as an exact callsign (harmless) | `CtyDat._parse` |

## Open decisions

| # | Question | Leaning |
|---|---|---|
| D1 | Minimum Python: 3.11 (built-in `tomllib`) or stay on 3.10 and add `tomli` | **Decided: 3.11** (phase 2) |
| D2 | QRZ code: make `qrzlog` an importable library that jtcon depends on, or copy a small QRZ client into jtcon | Undecided |
| D3 | Add UDP multicast support in v3 | Yes, small |
| D4 | QRZ API key: jtcon's own config, env var, or read qrzlog's `~/.config/qrzlog/qrzlog.ini` | Env var + jtcon config, fall back to qrzlog's |
| D5 | Keep the JSON log (`--save`) | Yes, as an action |
| D6 | Station memory across sessions (SQLite of when/where stations were heard) | Later |

## Git workflow for this branch

All work happens on `v3`; `main` stays as the released v2.

```bash
git switch v3                 # make sure you're on the branch
git status                    # see what changed
git add -A && git commit      # commit as usual
git push                      # goes to origin/v3 (tracking set by "push -u")
```

Bringing a fix from `main` into `v3`:

```bash
git switch v3
git merge main
```

Trying the branch with pipx:

```bash
pipx install --force "git+https://github.com/sysmatt/jtcon@v3"
```

Releasing (phase 8):

```bash
git switch main
git merge v3
git tag v3.0.0
git push && git push --tags
```
