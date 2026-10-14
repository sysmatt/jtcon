# Online logbooks

jtcon can work with your logbook on an online service:

- **Download** it, so confirmations on the service count towards your awards, and
  keep a dated backup copy every day.
- **Upload** each QSO you log in WSJT-X, as you log it.

[QRZ](https://www.qrz.com) is the first logbook jtcon supports, and more may follow.
The `sync` command and the background refresh are written so other services can
join them.

## QRZ

### Setting up

You need your QRZ **Logbook API key**: on QRZ.com, open your logbook, then
**Settings**.  Uploading needs a QRZ subscription that includes the Logbook API.

```toml
[inputs.qrz]
enabled = true                   # download the logbook
api_key = "XXXX-XXXX-XXXX-XXXX"

[actions.qrz]
upload = true                    # and send each QSO you log (off by default)
```

The key gives full access to your QRZ logbook, so keep the config file private:

```bash
chmod 600 ~/.config/jtcon/config.toml
```

jtcon warns at startup if other users can read the file holding the key.
`jtcon --show-config` shows the key as `****` plus its last four characters.

Instead of the config file, the key can come from the `QRZ_API_KEY` environment
variable, or from qrzlog's `~/.config/qrzlog/qrzlog.ini` if you use qrzlog.  jtcon
checks them in that order and says at startup which one it used.

The two halves are independent: you can download without uploading, or upload
without downloading.

### Downloads and confirmations

The first time, jtcon downloads your whole logbook in the background, showing a
tick for each batch:

```
[qrz] Downloading the full logbook ✓✓✓✓✓✓✓✓✓✓✓✓✓✓ done
[qrz] Full download: 3300 QSOs saved to qrz-2026-10-14.adif (keeping 5)
[adif] Reloaded 3300 QSOs (1684 duplicates merged) from 4 file(s).
```

After that:

- **Every 15 minutes** (`refresh_minutes`), jtcon fetches only what changed and says
  what it found: `[qrz] Updates: 1 new, 2 changed, 1 newly confirmed`.  When
  nothing changed it says nothing.
- **Once a day**, on the first refresh after midnight UTC, it downloads the whole
  logbook again.  That also catches QSOs you deleted on QRZ.
- **`sync`** at the `CMD>` prompt fetches changes now; `sync full` downloads the
  whole logbook again.

QRZ confirmations then count wherever a rule includes `qrz` in its `confirm` list:

```toml
[[wanted]]
rule    = "dxcc"
confirm = ["lotw", "qrz"]        # LoTW or QRZ

[[wanted]]
rule    = "was"
name    = "WAS-QRZ"              # QRZ's own WAS award
confirm = ["qrz"]
```

The `QRZ` column shows ⭐ for a station that has confirmed with you on QRZ.

The QRZ download is just another log.  The same QSO in your WSJT-X log and in
the download is counted once, with the confirmations from both.

### Your backups

Each day's download is kept as a plain ADIF file you can import anywhere, in
`~/.local/share/jtcon/qrz/` (`dir`):

```
qrz-2026-10-14.adif             the full download, never changed afterwards
qrz-2026-10-14-updates.adif     the changes fetched during that day
```

jtcon keeps the newest 5 days (`keep`).  Dates are UTC.

If QRZ can't be reached, jtcon carries on with the newest copy it has and tells
you how old it is:
`[qrz] Can't reach QRZ: … — using qrz-2026-10-12.adif (2 days old)`.

### Uploads

With `upload = true`, each QSO WSJT-X logs goes to your QRZ logbook straight away:

```
[qrz] ▲ VK3XY uploaded to QRZ (logid 1514479670)
[qrz] W1ABC is already in QRZ (duplicate)
[qrz] Upload of K5TX failed: <QRZ's reason>
```

If QRZ can't be reached, nothing is lost:

```
[qrz] Upload of VK3XY failed: <reason> — saved, will retry at the next sync
```

The QSO is saved in `pending-uploads.adif` in the same folder, and sent:

- at the next refresh, or when you type `sync`;
- once more when you quit, waiting a few seconds at most;
- when jtcon next starts, if it still hasn't gone through.

Sending a QSO twice is harmless: QRZ recognises the duplicate.  A QSO that QRZ
refuses outright (bad data, say) isn't retried, because sending it again won't
change the answer; jtcon shows QRZ's reason instead.

### If you used qrzlog with v2

jtcon v2 couldn't talk to QRZ, so a common setup ran qrzlog from
`--adif-change-script` to upload, and loaded qrzlog's download as an extra ADIF
file.  jtcon now does both itself.  Once you're happy with it:

- remove qrzlog's download file from `adif` under `[inputs]`;
- stop uploading from your `on_log_reload` script.

Until then nothing breaks: duplicate QSOs are merged, and QRZ ignores duplicate
uploads.
