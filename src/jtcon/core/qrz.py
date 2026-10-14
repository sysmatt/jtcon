"""QRZ Logbook: a small API client, the local backup store, and the sync between them.

The store keeps one *set* per UTC day that jtcon runs: a full download, written
once and never changed, plus an updates file that each refresh appends to.

    qrz-2026-10-14.adif            the full logbook as QRZ returned it
    qrz-2026-10-14-updates.adif    records QRZ reported as new or changed since

jtcon reads the newest set only, later copies of a record (same
app_qrzlog_logid) replacing earlier ones.  Older sets are backups; `keep` sets
are kept.  API: https://www.qrz.com/docs/logbook/QRZLogbookAPI.html
"""

import configparser
import html
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jtcon import __version__
from jtcon.core.adif import iter_records

QRZ_API_URL   = "https://logbook.qrz.com/api"
QRZ_DATA_DIR  = Path.home() / ".local" / "share" / "jtcon" / "qrz"
QRZLOG_INI    = Path.home() / ".config" / "qrzlog" / "qrzlog.ini"
PAGE_SIZE     = 250           # records per FETCH request, as QRZ suggests
PENDING_FILE  = "pending-uploads.adif"   # uploads waiting for QRZ, in the data dir
_LOGID        = "app_qrzlog_logid"
_EOR_END_RE   = re.compile(r"<eor>\s*$", re.IGNORECASE)


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


# ══════════════════════════════════════════════════════════════════════════════
#  API key
# ══════════════════════════════════════════════════════════════════════════════

def resolve_key(qcfg) -> tuple:
    """(key, where it came from) or (None, None).  The jtcon config wins, then
    the QRZ_API_KEY environment variable, then qrzlog's config file."""
    if qcfg.api_key:
        where = qcfg.api_key_file.name if qcfg.api_key_file else "the config"
        return qcfg.api_key, where
    if os.environ.get("QRZ_API_KEY"):
        return os.environ["QRZ_API_KEY"], "QRZ_API_KEY"
    ini = configparser.ConfigParser()
    try:
        ini.read(QRZLOG_INI)
    except configparser.Error:
        return None, None
    key = ini.get("qrzlog", "api_key", fallback="").strip()
    return (key, str(QRZLOG_INI)) if key else (None, None)


def key_file_warning(qcfg) -> str | None:
    """A warning when the config file holding the key is readable by others."""
    path = qcfg.api_key_file
    if not (qcfg.api_key and path):
        return None
    try:
        mode = path.stat().st_mode
    except OSError:
        return None
    if mode & 0o077:
        return (f"[qrz] Warning: {path} holds your API key and other users can read it;"
                f" chmod 600 {path}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  API client
# ══════════════════════════════════════════════════════════════════════════════

class QrzError(Exception):
    """QRZ refused the request (RESULT=FAIL, or an unexpected answer)."""


class QrzAuthError(QrzError):
    """RESULT=AUTH: the key is wrong, or lacks the subscription the action needs."""


class QrzNetworkError(QrzError):
    """QRZ couldn't be reached, or answered with an HTTP error."""


def parse_response(body: str) -> dict:
    """KEY=value pairs separated by &; ADIF, when present, comes last and may
    contain & and newlines, so it is cut off first."""
    result = {}
    body = body.strip()
    head = body
    at = body.upper().find("ADIF=")
    if at != -1 and (at == 0 or body[at - 1] in "&\n"):
        head = body[:max(0, at - 1)]
        result["ADIF"] = html.unescape(body[at + 5:])
    for part in head.replace("\n", "&").split("&"):
        key, sep, value = part.partition("=")
        if sep and key.strip():
            result[key.strip().upper()] = urllib.parse.unquote(value.strip())
    return result


def _no_records(res: dict) -> bool:
    """QRZ answers a FETCH that matches nothing with RESULT=FAIL."""
    reason = res.get("REASON", "").lower()
    return res.get("COUNT") == "0" or ("no " in reason and ("found" in reason or
                                                             "match" in reason))


class QrzClient:
    def __init__(self, key: str, agent=None, url: str | None = None, timeout: float = 60):
        self.key, self.url, self.timeout = key, url or QRZ_API_URL, timeout
        self.agent = agent or (lambda: f"jtcon/{__version__}")

    def _post(self, timeout: float | None = None, **params) -> dict:
        data = urllib.parse.urlencode({"KEY": self.key, **params}).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, method="POST", headers={
            "User-Agent": self.agent(),
            "Content-Type": "application/x-www-form-urlencoded",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise QrzNetworkError(f"HTTP {exc.code} {exc.reason}") from None
        except urllib.error.URLError as exc:
            raise QrzNetworkError(str(exc.reason)) from None
        except OSError as exc:           # timeouts, resets
            raise QrzNetworkError(str(exc) or type(exc).__name__) from None
        res = parse_response(body)
        if res.get("RESULT") == "AUTH":
            raise QrzAuthError(res.get("REASON") or "API key not accepted")
        return res

    def fetch(self, option: str = "", on_page=None) -> list:
        """Every matching record's raw ADIF text, fetched PAGE_SIZE at a time;
        *on_page* is called after each page."""
        records, after = [], 0
        while True:
            opt = f"MAX:{PAGE_SIZE},AFTERLOGID:{after}" + (f",{option}" if option else "")
            res = self._post(ACTION="FETCH", OPTION=opt)
            if res.get("RESULT") != "OK":
                if _no_records(res):
                    break
                raise QrzError(res.get("REASON") or f"unexpected answer {res.get('RESULT')!r}")
            page = list(iter_records(res.get("ADIF", "")))
            records += [raw for _, raw in page]
            if on_page:
                on_page()
            ids = [int(f[_LOGID]) for f, _ in page if f.get(_LOGID, "").isdigit()]
            if len(page) < PAGE_SIZE or not ids:
                break
            after = max(ids) + 1
        return records

    def fetch_changed(self, since: date) -> list:
        return self.fetch(f"MODSINCE:{since.isoformat()}")

    def insert(self, record: str, timeout: float | None = None) -> tuple:
        """("added", logid) or ("duplicate", ""); raises QrzError otherwise."""
        res = self._post(timeout, ACTION="INSERT", ADIF=record)
        result, reason = res.get("RESULT"), res.get("REASON", "")
        if result in ("OK", "REPLACE"):
            if res.get("COUNT") == "0":
                return "duplicate", ""
            return "added", res.get("LOGID", "")
        if result == "FAIL" and "duplicate" in reason.lower():
            return "duplicate", ""
        raise QrzError(reason or f"unexpected answer {result!r}")


# ══════════════════════════════════════════════════════════════════════════════
#  Backup store
# ══════════════════════════════════════════════════════════════════════════════

_FULL_RE = re.compile(r"^qrz-(\d{4}-\d{2}-\d{2})\.adif$")


def _header(kind: str, when: datetime) -> str:
    stamp = when.strftime("%Y%m%d %H%M%S")
    return (f"QRZ logbook {kind}, saved by jtcon {__version__} at"
            f" {when:%Y-%m-%d %H:%M:%S}Z\n"
            f"<adif_ver:5>3.1.4 <programid:5>jtcon"
            f" <programversion:{len(__version__)}>{__version__}"
            f" <created_timestamp:{len(stamp)}>{stamp} <eoh>\n\n")


class QrzStore:
    def __init__(self, directory: Path, keep: int = 5):
        self.dir, self.keep = Path(directory), keep
        self._days: list | None = None      # cached: files() runs on every packet

    def full_path(self, day: date) -> Path:
        return self.dir / f"qrz-{day.isoformat()}.adif"

    def updates_path(self, day: date) -> Path:
        return self.dir / f"qrz-{day.isoformat()}-updates.adif"

    def days(self) -> list:
        """The days with a full download, oldest first."""
        if self._days is None:
            self._days = self._scan()
        return list(self._days)

    def _scan(self) -> list:
        try:
            names = [p.name for p in self.dir.iterdir()]
        except OSError:
            return []
        out = []
        for name in names:
            m = _FULL_RE.match(name)
            if m:
                try:
                    out.append(date.fromisoformat(m.group(1)))
                except ValueError:
                    pass
        return sorted(out)

    def current(self) -> date | None:
        days = self.days()
        return days[-1] if days else None

    def files(self) -> list:
        """The newest set's files that exist: what jtcon reads."""
        day = self.current()
        if day is None:
            return []
        return [p for p in (self.full_path(day), self.updates_path(day)) if p.exists()]

    def records(self) -> dict:
        """The newest set's records: logid (or a position) -> (fields, raw), a
        later copy replacing an earlier one.  A record cut short by a write in
        progress (no <EOR>) is skipped."""
        out: dict = {}
        for path in self.files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for i, (fields, raw) in enumerate(iter_records(text)):
                if _EOR_END_RE.search(raw):
                    out[fields.get(_LOGID) or f"{path.name}#{i}"] = (fields, raw)
        return out

    def write_full(self, day: date, records: list) -> Path:
        """Save a full download as *day*'s set, replacing any set of that day,
        then prune old sets."""
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.full_path(day)
        tmp = path.with_name(path.name + ".part")
        text = _header("download", datetime.now(timezone.utc))
        text += "".join(raw.strip() + "\n" for raw in records)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        self.updates_path(day).unlink(missing_ok=True)
        os.replace(tmp, path)
        self._days = None
        self.prune()
        return path

    def append_updates(self, records: list) -> list:
        """Append the records that are new or whose field values differ from the
        newest set's copy (QRZ may send the same record with different text).
        Returns (old fields or None, new fields) for each appended record."""
        day = self.current()
        if day is None:
            raise QrzError("no full download to update")
        have = self.records()
        changed, texts = [], []
        for raw in records:
            fields = next((f for f, _ in iter_records(raw)), {})
            key = fields.get(_LOGID)
            old = have.get(key) if key else None
            if old is not None and old[0] == fields:
                continue
            changed.append((old[0] if old else None, fields))
            texts.append(raw.strip() + "\n")
            if key:
                have[key] = (fields, raw)
        if texts:
            path = self.updates_path(day)
            text = "" if path.exists() else _header("updates", datetime.now(timezone.utc))
            with open(path, "a", encoding="utf-8") as f:
                f.write(text + "".join(texts))
                f.flush()
                os.fsync(f.fileno())
        return changed

    def prune(self) -> list:
        """Delete all but the newest `keep` sets; returns the days removed."""
        old = self.days()[:-self.keep] if self.keep > 0 else []
        for day in old:
            self.full_path(day).unlink(missing_ok=True)
            self.updates_path(day).unlink(missing_ok=True)
        self._days = None
        return old


# ══════════════════════════════════════════════════════════════════════════════
#  Sync: refreshes and uploads (worker threads)
# ══════════════════════════════════════════════════════════════════════════════

def _confirmed(fields: dict | None) -> bool:
    return bool(fields) and fields.get("app_qrzlog_status", "").upper() == "C"


class QrzSync:
    """Keeps the store up to date and uploads logged QSOs.  Methods run in
    worker threads and report through *log*; only one refresh runs at a time."""

    def __init__(self, client: QrzClient, store: QrzStore | None, log, today=utc_today,
                 progress=None, pending_path: Path | None = None):
        """*progress*, if given, shows a full download's progress: each call
        replaces the line from the call before.  Uploads that can't reach QRZ
        are kept in *pending_path* until they go through, across restarts."""
        self.client, self.store, self.log, self.today = client, store, log, today
        self.progress = progress
        self.pending_path = pending_path
        self.pending: list = self._load_pending()    # (call, record) uploads to retry
        self._lock = threading.Lock()
        self._pending_lock = threading.Lock()

    def refresh(self, full: bool = False, quiet: bool = True) -> None:
        """A full download if forced or the newest set isn't today's (UTC);
        otherwise fetch what changed since yesterday."""
        if self.store is None:
            return
        if not self._lock.acquire(blocking=False):
            self.log("[qrz] A refresh is already running.")
            return
        try:
            day = self.today()
            if full or self.store.current() != day:
                self._full(day)
            else:
                self._incremental(day, quiet)
        except QrzAuthError as exc:
            self.log(f"[qrz] QRZ refused the API key: {exc}")
        except QrzNetworkError as exc:
            self.log(f"[qrz] Can't reach QRZ: {exc} — {self.describe()}")
        except QrzError as exc:
            self.log(f"[qrz] Refresh failed: {exc}")
        except OSError as exc:
            self.log(f"[qrz] Can't write to {self.store.dir}: {exc}")
        finally:
            self._lock.release()

    def _full(self, day: date) -> None:
        head, ticks = "[qrz] Downloading the full logbook ", ""
        (self.progress or self.log)(head + "…")

        def page():
            nonlocal ticks
            ticks += "✓"
            if self.progress:
                self.progress(head + ticks)

        records = self.client.fetch(on_page=page)
        if self.progress:
            self.progress(head + ticks + " done")
        path = self.store.write_full(day, records)
        self.log(f"[qrz] Full download: {len(records)} QSOs saved to {path.name}"
                 f" (keeping {self.store.keep})")

    def _incremental(self, day: date, quiet: bool) -> None:
        changed = self.store.append_updates(self.client.fetch_changed(day - timedelta(days=1)))
        if not changed:
            if not quiet:
                self.log("[qrz] No changes.")
            return
        new = sum(1 for old, _ in changed if old is None)
        confirmed = sum(1 for old, f in changed if _confirmed(f) and not _confirmed(old))
        parts = [f"{new} new"] if new else []
        if len(changed) > new:
            parts.append(f"{len(changed) - new} changed")
        if confirmed:
            parts.append(f"{confirmed} newly confirmed")
        self.log(f"[qrz] Updates: {', '.join(parts)}")

    def describe(self) -> str:
        """Which saved set is in use, and how old it is."""
        day = self.store.current() if self.store else None
        if day is None:
            return "no saved download yet"
        age = (self.today() - day).days
        when = "today" if age == 0 else "1 day old" if age == 1 else f"{age} days old"
        n = len(self.store.files()) - 1
        extra = " + updates" if n else ""
        return f"using {self.store.full_path(day).name}{extra} ({when})"

    # ── Uploads ───────────────────────────────────────────────────────────────

    def upload(self, call: str, record: str) -> None:
        """Upload one logged QSO; if QRZ can't be reached, keep it to retry."""
        try:
            self._send(call, record, None, self.log)
        except QrzNetworkError as exc:
            with self._pending_lock:
                self.pending.append((call, record))
                self._save_pending()
            self.log(f"[qrz] Upload of {call} failed: {exc} — saved, will retry at the next sync")

    def retry_uploads(self, timeout: float | None = None, log=None) -> None:
        """Retry the waiting uploads, oldest first, stopping at the first that
        can't reach QRZ.  *log* replaces self.log (used at exit)."""
        log = log or self.log
        with self._pending_lock:
            waiting = list(self.pending)
        for i, (call, record) in enumerate(waiting):
            try:
                self._send(call, record, timeout, log)
            except QrzNetworkError as exc:
                left = len(waiting) - i
                log(f"[qrz] Can't reach QRZ: {exc} — {left} upload{'s' * (left != 1)}"
                    " still waiting")
                return
            with self._pending_lock:
                if (call, record) in self.pending:    # a sync may run beside a tick
                    self.pending.remove((call, record))
                    self._save_pending()

    def _send(self, call: str, record: str, timeout, log) -> None:
        """Upload and report; raises QrzNetworkError if QRZ can't be reached."""
        try:
            result, logid = self.client.insert(record, timeout)
        except QrzAuthError as exc:
            log(f"[qrz] Upload of {call} refused: {exc}"
                " (uploads need a QRZ logbook subscription and a valid key)")
            return
        except QrzNetworkError:
            raise
        except QrzError as exc:
            log(f"[qrz] Upload of {call} failed: {exc}")
            return
        if result == "added":
            log(f"[qrz] ▲ {call} uploaded to QRZ" + (f" (logid {logid})" if logid else ""))
        else:
            log(f"[qrz] {call} is already in QRZ (duplicate)")

    def _load_pending(self) -> list:
        try:
            text = self.pending_path.read_text(encoding="utf-8") if self.pending_path else ""
        except FileNotFoundError:
            return []
        except OSError as exc:
            self.log(f"[qrz] Can't read the waiting uploads in {self.pending_path}: {exc}")
            return []
        return [(f.get("call", "?").upper(), raw.strip()) for f, raw in iter_records(text)
                if _EOR_END_RE.search(raw)]

    def _save_pending(self) -> None:
        """Rewrite the pending file from self.pending (hold _pending_lock)."""
        path = self.pending_path
        if path is None:
            return
        try:
            if not self.pending:
                path.unlink(missing_ok=True)
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".part")
            text = _header("uploads waiting", datetime.now(timezone.utc))
            text += "".join(record.strip() + "\n" for _, record in self.pending)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            self.log(f"[qrz] Can't save the waiting uploads to {path}: {exc}")
