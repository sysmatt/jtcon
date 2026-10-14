# Migrating from v2

jtcon v3 moves its settings into a config file, so the long v2 command lines are
gone.  Most v2 options became a setting in `~/.config/jtcon/config.toml`.  A few
stayed as command-line options, and the config file is the place for the rest.

Start with `jtcon --init-config`, then move your options across using the table
below.  `jtcon --show-config` shows the result.

## What changed in how jtcon behaves

- **NEEDED is now WANTED**, and every WANTED line names the rule behind it:
  `*** WANTED: ❌DXCC(VK) · ❓WAS(TX) · POTA ***`.  MATCH is gone too; watchlist
  hits are WANTED lines, shown in yellow.
- **Confirmations:** award rules count only LoTW confirmations by default, which
  was v2's `--qsl-only`.  A station you've worked but not had confirmed shows ❓
  and is still wanted.  For v2's default (any QSO counts), set `confirm = []` on
  that rule.  Each rule can also count QRZ, eQSL or paper cards.
- **Recently worked stations** are left alone for 5 days by default (v2: today
  only), and only for what that QSO covers.  See
  [Wanted rules](wanted-rules.md#stations-youve-worked-recently).
- **Status columns:** each award rule has its own column, with its mark next to
  its value (v2 printed each mark in front of the wrong column).
- **The column header** stays fixed at the top instead of repeating.
- **Highlighting:** wanted stations are now coloured in WSJT-X's Band Activity
  window.  Turn it off with `enabled = false` under `[actions.highlight]`.
- **QRZ** is built in: confirmations, daily backups and uploads, without qrzlog.
  See [Online logbooks](online-logbooks.md).
- **Commands:** `ignore`, `unignore` and `sync` are new.  Commands can be
  shortened to any unique start (`l` for `list`).

## v2 options and their replacements

| v2 | v3 |
|---|---|
| `--host`, `--port` | `[udp] host`, `port`.  `--port` stays |
| `--proxy 2238,2239` | `[udp] proxy = [2238, 2239]` |
| `--raw` | `[display] show = "all"` |
| `--all` | `[scope] decodes = "all"`, or `--scope all` |
| `--show-time` | `[display] show_time = true` |
| `--no-color`, `--mouse`, `--debug` | `[display] color`, `mouse`, `debug`.  The options stay |
| `--cty`, `--hamdat` | `[inputs] cty`, `hamdat` |
| `--adif F1 F2` | `[inputs] adif = ["F1", "F2"]` |
| The default NEEDED checks | `[[wanted]]` rules `dxcc`, `cqz`, `country`: the default when you have none |
| `--qsl-only` | The default now |
| `--no-need-entity`, `--no-need-cqz`, `--no-need-country` | Leave that rule out of your `[[wanted]]` list |
| `--was`, `--5bwas` | `[[wanted]] rule = "was"` / `"5bwas"`.  You can now have both |
| `--pota`, `--sota`, `--iota` | `[[wanted]] rule = "pota"` and so on |
| `--match-calls F` | `[[wanted]] rule = "callsigns"`, `files = ["F"]` |
| `--match-message F` | `[[wanted]] rule = "message"`, `files = ["F"]` |
| `--match-state TX,CA` | `[[wanted]] rule = "states"`, `states = ["TX", "CA"]` (or `file`) |
| `--worked-lag-days N` | `[filters] suppress_worked_within_days = N` |
| `--alert-command S` | `[actions.script] on_wanted = "S"` |
| `--alert-ntfy T` | `[actions.ntfy] topic = "T"` |
| `--adif-change-script S` | `[actions.script] on_log_reload = "S"` |
| `--save F` | `[actions.json_log] file = "F"`.  `--save` stays |
| `--stats` | Removed (it did nothing) |

For one run, `--wanted` replaces your rules: `jtcon --wanted dxcc,was,pota`.

## An example

This v2 command line:

```bash
jtcon --adif ~/ham/old.adi --qsl-only --5bwas --pota --match-calls ~/ham/wanted.txt \
      --all --worked-lag-days 7 --alert-ntfy my-topic --proxy 2238
```

becomes this config file:

```toml
[udp]
proxy = [2238]

[scope]
decodes = "all"

[inputs]
adif = ["~/ham/old.adi"]

[filters]
suppress_worked_within_days = 7

[[wanted]]
rule = "dxcc"

[[wanted]]
rule = "cqz"

[[wanted]]
rule = "country"

[[wanted]]
rule = "5bwas"

[[wanted]]
rule = "pota"

[[wanted]]
rule  = "callsigns"
files = ["~/ham/wanted.txt"]

[actions.ntfy]
topic = "my-topic"
```

and then just `jtcon`.
