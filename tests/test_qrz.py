"""QRZ: the API client against a fake QRZ server, the backup store, the sync,
the API key, and the Engine's refreshes and uploads.  Nothing here reaches the
real QRZ (see conftest's no_real_qrz)."""

import asyncio
import html
import http.server
import threading
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

import pytest

import jtcon.core.qrz as qrz
from jtcon.config import ConfigError, load_config, show_config
from jtcon.core.events import LogLine
from jtcon.core.qrz import (QrzAuthError, QrzClient, QrzError, QrzNetworkError, QrzStore,
                            QrzSync, key_file_warning, parse_response, resolve_key)
import wsjtx_packets as pk
from conftest import CTY_PATH, FakeSock, make_config
from jtcon.core.engine import Engine

KEY = "ABCD-1234-EFGH-5678"
DAY = date(2026, 10, 14)


# ══════════════════════════════════════════════════════════════════════════════
#  A fake QRZ Logbook API
# ══════════════════════════════════════════════════════════════════════════════

def adif_record(fields: dict) -> str:
    return " ".join(f"<{k}:{len(v)}>{v}" for k, v in fields.items()) + " <eor>"


class FakeQrz:
    """The logbook as a dict of logid -> fields; *modified* holds the logids a
    MODSINCE fetch returns."""

    def __init__(self):
        self.qsos: dict = {}
        self.modified: set = set()
        self.requests: list = []           # (params, User-Agent)
        self.next_id = 1000
        self.down = False                  # answer HTTP 503
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, call, status="N", **extra) -> str:
        logid = str(self.next_id)
        self.next_id += 1
        self.qsos[logid] = {"call": call, "qso_date": "20261001", "time_on": "1200",
                            "band": "20m", "mode": "FT8", "app_qrzlog_status": status,
                            **extra, "app_qrzlog_logid": logid}
        return logid

    def answer(self, params: dict) -> str:
        if params.get("KEY") != KEY:
            return "RESULT=AUTH&REASON=invalid api key"
        action = params.get("ACTION")
        if action == "FETCH":
            opts = dict(o.split(":", 1) for o in params["OPTION"].split(","))
            ids = sorted(int(i) for i in self.qsos if int(i) >= int(opts["AFTERLOGID"]))
            if "MODSINCE" in opts:
                ids = [i for i in ids if str(i) in self.modified]
            if not ids:
                return "RESULT=FAIL&REASON=no log entries found&COUNT=0"
            page = ids[:int(opts["MAX"])]
            adif = "\n".join(adif_record(self.qsos[str(i)]) for i in page)
            return (f"RESULT=OK&COUNT={len(ids)}&LOGIDS={','.join(map(str, page))}"
                    f"&ADIF={html.escape(adif)}")
        if action == "INSERT":
            fields = dict(qrz.iter_records(params["ADIF"]).__next__()[0])
            if any(q["call"] == fields["call"].upper() and q["qso_date"] == fields["qso_date"]
                   for q in self.qsos.values()):
                return "RESULT=FAIL&REASON=Unable to add QSO to database: duplicate&COUNT=0"
            if fields["call"].upper() == "BAD1X":
                return "RESULT=FAIL&REASON=missing band&COUNT=0"
            logid = self.add(fields["call"].upper(), qso_date=fields["qso_date"])
            return f"RESULT=OK&LOGID={logid}&COUNT=1"
        return "RESULT=FAIL&REASON=unknown action"

    def _handler(self):
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                params = dict(urllib.parse.parse_qsl(body))
                fake.requests.append((params, self.headers["User-Agent"]))
                if fake.down:
                    self.send_error(503)
                    return
                data = fake.answer(params).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        return Handler

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake(monkeypatch):
    f = FakeQrz()
    monkeypatch.setattr(qrz, "QRZ_API_URL", f.url)
    yield f
    f.close()


@pytest.fixture
def client(fake):
    return QrzClient(KEY, agent=lambda: "jtcon/test (N0CALL)")


class Log(list):
    def __call__(self, text):
        self.append(text)


# ══════════════════════════════════════════════════════════════════════════════
#  Client
# ══════════════════════════════════════════════════════════════════════════════

def test_fetch_pages_through_the_logbook(fake, client, monkeypatch):
    monkeypatch.setattr(qrz, "PAGE_SIZE", 3)
    ids = [fake.add(f"K{i}AA") for i in range(7)]
    records = client.fetch()
    assert [qrz.iter_records(r).__next__()[0]["app_qrzlog_logid"] for r in records] == ids
    options = [p["OPTION"] for p, _ in fake.requests]
    assert options == ["MAX:3,AFTERLOGID:0", "MAX:3,AFTERLOGID:1003", "MAX:3,AFTERLOGID:1006"]
    assert {agent for _, agent in fake.requests} == {"jtcon/test (N0CALL)"}


def test_fetch_changed_and_empty_answers(fake, client):
    fake.add("K1AA")
    fake.modified = {fake.add("K2BB")}
    assert ["K2BB" in r for r in client.fetch_changed(DAY)] == [True]
    assert fake.requests[-1][0]["OPTION"] == "MAX:250,AFTERLOGID:0,MODSINCE:2026-10-14"
    fake.modified = set()
    assert client.fetch_changed(DAY) == []


def test_insert(fake, client):
    fake.add("W1ABC")
    assert client.insert(adif_record({"call": "vk3xy", "qso_date": "20261014"})) == (
        "added", "1001")
    assert client.insert(adif_record({"call": "W1ABC", "qso_date": "20261001"})) == (
        "duplicate", "")
    with pytest.raises(QrzError, match="missing band"):
        client.insert(adif_record({"call": "BAD1X", "qso_date": "20261014"}))


def test_errors(fake, client):
    with pytest.raises(QrzAuthError, match="invalid api key"):
        QrzClient("WRONG").fetch()
    fake.down = True
    with pytest.raises(QrzNetworkError, match="HTTP 503"):
        client.fetch()
    with pytest.raises(QrzNetworkError):
        QrzClient(KEY, url="http://127.0.0.1:9/api", timeout=2).fetch()


def test_parse_response_keeps_adif_whole():
    body = "RESULT=OK&COUNT=2&ADIF=<call:4>K1AA<comment:5>a&b=c<eor>\n<call:4>K2BB<eor>"
    res = parse_response(body)
    assert res["RESULT"] == "OK" and res["COUNT"] == "2"
    assert res["ADIF"] == "<call:4>K1AA<comment:5>a&b=c<eor>\n<call:4>K2BB<eor>"
    assert parse_response("RESULT=FAIL&REASON=no%20such") == {"RESULT": "FAIL",
                                                               "REASON": "no such"}


# ══════════════════════════════════════════════════════════════════════════════
#  Store
# ══════════════════════════════════════════════════════════════════════════════

def rec(logid, call="K1AA", status="N"):
    return adif_record({"call": call, "app_qrzlog_status": status,
                        "app_qrzlog_logid": str(logid)})


def calls_and_status(store):
    return {k: (f["call"], f["app_qrzlog_status"]) for k, (f, _) in store.records().items()}


def test_store_full_then_updates_latest_record_wins(tmp_path):
    store = QrzStore(tmp_path / "qrz")
    assert store.files() == [] and store.current() is None
    store.write_full(DAY, [rec(1), rec(2, "K2BB")])
    assert [p.name for p in store.files()] == ["qrz-2026-10-14.adif"]
    full = store.full_path(DAY).read_text()
    assert full.startswith("QRZ logbook download, saved by jtcon") and "<eoh>" in full

    changed = store.append_updates([rec(1), rec(2, "K2BB", "C"), rec(3, "K3CC")])
    assert [(old and old["app_qrzlog_status"], new["call"]) for old, new in changed] == [
        ("N", "K2BB"), (None, "K3CC")]                   # rec(1) is unchanged: skipped
    assert store.append_updates([rec(2, "K2BB", "C")]) == []    # already have it
    assert calls_and_status(store) == {"1": ("K1AA", "N"), "2": ("K2BB", "C"),
                                       "3": ("K3CC", "N")}
    assert store.full_path(DAY).read_text() == full    # the full download never changes
    updates = store.updates_path(DAY).read_text()
    assert updates.startswith("QRZ logbook updates") and updates.count("<eor>") == 2


def test_store_same_fields_in_other_text_are_not_a_change(tmp_path):
    # QRZ can send an unchanged record with its fields in another order.
    store = QrzStore(tmp_path)
    store.write_full(DAY, [rec(1)])
    shuffled = "<APP_QRZLOG_LOGID:1>1 <APP_QRZLOG_STATUS:1>N\n<CALL:4>K1AA <EOR>"
    assert store.append_updates([shuffled]) == []
    assert not store.updates_path(DAY).exists()


def test_store_skips_a_record_cut_short(tmp_path):
    store = QrzStore(tmp_path)
    store.write_full(DAY, [rec(1)])
    store.updates_path(DAY).write_text(rec(1, status="C") + "\n<call:4>K9ZZ<app_qrzlog_lo")
    assert calls_and_status(store) == {"1": ("K1AA", "C")}


def test_store_keeps_n_sets_and_a_new_full_starts_a_fresh_set(tmp_path):
    store = QrzStore(tmp_path, keep=2)
    for i in range(4):
        store.write_full(DAY + timedelta(days=i), [rec(1)])
        store.append_updates([rec(1, status="C")])
    assert [p.name for p in sorted(tmp_path.iterdir())] == [
        "qrz-2026-10-16-updates.adif", "qrz-2026-10-16.adif",
        "qrz-2026-10-17-updates.adif", "qrz-2026-10-17.adif"]
    store.write_full(DAY + timedelta(days=3), [rec(1), rec(2)])     # "qrz full" again today
    assert [p.name for p in store.files()] == ["qrz-2026-10-17.adif"]
    assert not list(tmp_path.glob("*.part"))


# ══════════════════════════════════════════════════════════════════════════════
#  Sync
# ══════════════════════════════════════════════════════════════════════════════

def test_sync_full_once_a_day_then_incremental(fake, client, tmp_path):
    today = [DAY]
    log = Log()
    store = QrzStore(tmp_path, keep=5)
    sync = QrzSync(client, store, log, today=lambda: today[0])
    a = fake.add("K1AA")
    fake.add("K2BB")
    sync.refresh()
    assert log == ["[qrz] Downloading the full logbook …",
                   "[qrz] Full download: 2 QSOs saved to qrz-2026-10-14.adif (keeping 5)"]
    sync.refresh(quiet=False)
    assert log[-1] == "[qrz] No changes."
    fake.qsos[a]["app_qrzlog_status"] = "C"
    fake.modified = {a, fake.add("K3CC")}
    sync.refresh()
    assert log[-2] == "[qrz] Updates: 1 new, 1 changed, 1 newly confirmed (QRZ)"
    assert log[-1] == "[qrz] Confirmed: K1AA 20m (QRZ)"
    assert fake.requests[-1][0]["OPTION"].endswith("MODSINCE:2026-10-13")
    n = len(log)
    sync.refresh()                                       # same answer again: quiet
    assert len(log) == n
    today[0] += timedelta(days=1)
    sync.refresh()
    assert log[-1].startswith("[qrz] Full download: 3 QSOs saved to qrz-2026-10-15.adif")
    assert sync.describe() == "using qrz-2026-10-15.adif (today)"


def test_sync_full_download_shows_a_tick_per_page(fake, client, tmp_path, monkeypatch):
    monkeypatch.setattr(qrz, "PAGE_SIZE", 3)
    for i in range(7):
        fake.add(f"K{i}AA")
    log, progress = Log(), []
    QrzSync(client, QrzStore(tmp_path), log, today=lambda: DAY,
            progress=progress.append).refresh()
    head = "[qrz] Downloading the full logbook "
    assert progress == [head + "…", head + "✓", head + "✓✓", head + "✓✓✓",
                        head + "✓✓✓ done"]
    assert log == ["[qrz] Full download: 7 QSOs saved to qrz-2026-10-14.adif (keeping 5)"]


def test_sync_counts_new_confirmations_by_source(fake, client, tmp_path):
    log = Log()
    sync = QrzSync(client, QrzStore(tmp_path), log, today=lambda: DAY)
    ids = [fake.add(c) for c in ("K1AA", "K2BB", "K3CC", "K4DD")]
    sync.refresh()
    fake.qsos[ids[0]]["lotw_qsl_rcvd"] = "Y"                    # LoTW import on QRZ
    fake.qsos[ids[1]].update(lotw_qsl_rcvd="Y", app_qrzlog_status="C")
    fake.qsos[ids[2]]["app_qrzlog_status"] = "C"
    fake.qsos[ids[3]]["comment"] = "edited"
    fake.modified = set(ids)
    sync.refresh()
    assert log[-2:] == ["[qrz] Updates: 4 changed, 3 newly confirmed (2 LoTW, 2 QRZ)",
                        "[qrz] Confirmed: K1AA 20m (LoTW), K2BB 20m (LoTW, QRZ), K3CC 20m (QRZ)"]
    fake.qsos[ids[3]]["lotw_qsl_rcvd"] = "Y"
    sync.refresh()
    assert log[-2:] == ["[qrz] Updates: 1 changed, 1 newly confirmed (LoTW)",
                        "[qrz] Confirmed: K4DD 20m (LoTW)"]
    many = [({"call": f"k{i}aa"}, {"QRZ"}) for i in range(23)]
    assert qrz._confirmed_calls(many).endswith("K19AA (QRZ) … and 3 more")


def test_sync_reports_failures_and_keeps_the_saved_set(fake, client, tmp_path):
    log = Log()
    store = QrzStore(tmp_path)
    store.write_full(DAY - timedelta(days=2), [rec(1)])
    sync = QrzSync(client, store, log, today=lambda: DAY)
    fake.down = True
    sync.refresh()
    assert log[-1] == ("[qrz] Can't reach QRZ: HTTP 503 Service Unavailable —"
                       " using qrz-2026-10-12.adif (2 days old)")
    sync.client = QrzClient("WRONG")
    fake.down = False
    sync.refresh()
    assert log[-1] == "[qrz] QRZ refused the API key: invalid api key"
    assert store.current() == DAY - timedelta(days=2)


def test_uploads_and_retries(fake, client):
    log = Log()
    sync = QrzSync(client, None, log)
    sync.upload("VK3XY", adif_record({"call": "VK3XY", "qso_date": "20261014"}))
    assert log[-1] == "[qrz] ▲ VK3XY uploaded to QRZ (logid 1000)"
    sync.upload("VK3XY", adif_record({"call": "VK3XY", "qso_date": "20261014"}))
    assert log[-1] == "[qrz] VK3XY is already in QRZ (duplicate)"
    sync.upload("BAD1X", adif_record({"call": "BAD1X", "qso_date": "20261014"}))
    assert log[-1] == "[qrz] Upload of BAD1X failed: missing band"
    fake.down = True
    sync.upload("ZL1AA", adif_record({"call": "ZL1AA", "qso_date": "20261014"}))
    assert log[-1].endswith("— saved, will retry at the next sync") and len(sync.pending) == 1
    fake.down = False
    sync.retry_uploads()
    assert log[-1] == "[qrz] ▲ ZL1AA uploaded to QRZ (logid 1001)" and sync.pending == []


def test_waiting_uploads_are_saved_across_restarts(fake, client, tmp_path):
    path = tmp_path / "qrz" / qrz.PENDING_FILE
    log = Log()
    sync = QrzSync(client, None, log, pending_path=path)
    fake.down = True
    for call in ("ZL1AA", "VK3XY", "JA1ZZ"):
        sync.upload(call, adif_record({"call": call, "qso_date": "20261014", "band": "20m"}))
    assert path.read_text().startswith("QRZ logbook uploads waiting, saved by jtcon")
    n = len(fake.requests)
    sync.retry_uploads()                                 # still down: stops at the first
    assert log[-1] == "[qrz] Can't reach QRZ: HTTP 503 Service Unavailable — 3 uploads still waiting"
    assert len(fake.requests) == n + 1

    again = QrzSync(client, None, log, pending_path=path)    # jtcon restarted
    assert [call for call, _ in again.pending] == ["ZL1AA", "VK3XY", "JA1ZZ"]
    fake.down = False
    fake.add("VK3XY", qso_date="20261014")              # already there: logid 1000
    again.retry_uploads(timeout=5)
    assert log[-3:] == ["[qrz] ▲ ZL1AA uploaded to QRZ (logid 1001)",
                        "[qrz] VK3XY is already in QRZ (duplicate)",
                        "[qrz] ▲ JA1ZZ uploaded to QRZ (logid 1002)"]
    assert again.pending == [] and not path.exists()


# ══════════════════════════════════════════════════════════════════════════════
#  API key and config
# ══════════════════════════════════════════════════════════════════════════════

def write(path: Path, text: str, mode=0o600) -> Path:
    path.write_text(text)
    path.chmod(mode)
    return path


def test_key_comes_from_config_then_env_then_qrzlog(tmp_path, monkeypatch):
    ini = write(tmp_path / "qrzlog.ini", "[qrzlog]\napi_key = FROM-INI\n")
    monkeypatch.setattr(qrz, "QRZLOG_INI", ini)
    assert resolve_key(make_config({}).inputs.qrz) == ("FROM-INI", str(ini))
    monkeypatch.setenv("QRZ_API_KEY", "FROM-ENV")
    assert resolve_key(make_config({}).inputs.qrz) == ("FROM-ENV", "QRZ_API_KEY")
    write(tmp_path / "config.toml", f'[inputs.qrz]\napi_key = "{KEY}"\n')
    cfg = load_config(config_dir=tmp_path)
    assert resolve_key(cfg.inputs.qrz) == (KEY, "config.toml")


def test_key_file_must_be_private(tmp_path):
    path = write(tmp_path / "config.toml", f'[inputs.qrz]\napi_key = "{KEY}"\n', 0o644)
    cfg = load_config(config_dir=tmp_path)
    assert key_file_warning(cfg.inputs.qrz) == (
        f"[qrz] Warning: {path} holds your API key and other users can read it;"
        f" chmod 600 {path}")
    path.chmod(0o600)
    assert key_file_warning(cfg.inputs.qrz) is None


def test_qrz_config_and_show_config_hides_the_key(tmp_path):
    write(tmp_path / "config.toml", f"""
[inputs.qrz]
enabled = true
api_key = "{KEY}"
dir     = "qrzdir"
keep    = 3
[actions.qrz]
upload = true
""")
    cfg = load_config(config_dir=tmp_path)
    q = cfg.inputs.qrz
    assert (q.enabled, q.dir, q.keep, q.refresh_minutes) == (True, tmp_path / "qrzdir", 3, 15)
    assert cfg.actions.qrz_upload
    shown = show_config(cfg)
    assert KEY not in shown and 'api_key = "****5678"' in shown
    defaults = make_config({})
    assert not defaults.inputs.qrz.enabled and not defaults.actions.qrz_upload
    with pytest.raises(ConfigError, match="inputs.qrz.keep must be 1 or more, not 0"):
        make_config({"inputs": {"qrz": {"keep": 0}}})


# ══════════════════════════════════════════════════════════════════════════════
#  Engine
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def engine(tmp_path, monkeypatch):
    import jtcon.core.adif
    monkeypatch.setattr(jtcon.core.adif, "WSJT_LOG_SEARCH_PATHS", [])

    def _make(**qrz_settings):
        layer = {"inputs": {"cty": str(CTY_PATH), "hamdat": str(tmp_path / "no.db"),
                            "qrz": {"enabled": True, "api_key": KEY,
                                    "dir": str(tmp_path / "qrz"), **qrz_settings}},
                 "wanted": [{"rule": "dxcc", "confirm": ["qrz"]}]}
        e = Engine(make_config(layer), None, FakeSock())
        e.events = []
        e.subscribe(e.events.append)
        return e
    return _make


async def refresh(e):
    """The sync command; without e._loop the refresh itself runs at once, and
    the reload it starts runs on this loop."""
    e.sync()
    for _ in range(200):
        if not e._reloading:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("reload did not finish")


def logs(e):
    return [ev.text for ev in e.events if isinstance(ev, LogLine)]


def test_engine_loads_the_saved_set_then_refreshes_into_the_logbook(fake, engine, tmp_path):
    store = QrzStore(tmp_path / "qrz")
    store.write_full(qrz.utc_today() - timedelta(days=1), [rec(1, "VK3XY", "C")])
    e = engine()
    assert e._load_inputs()
    assert "[qrz] API key from the config" in logs(e)
    assert any(t.startswith("[qrz] Using qrz-") and "(1 day old); refreshed every 15 min" in t
               for t in logs(e))
    assert [r.confirmed for r in e.rules] == [{"VK"}]
    fake.add("VK3XY", "C")
    fake.add("ZL1AA", "C")
    asyncio.run(refresh(e))
    assert logs(e)[-3].startswith("[qrz] Full download: 2 QSOs")
    assert logs(e)[-2].startswith("[adif] Reloaded 2 QSOs from 1 file(s)")
    assert logs(e)[-1] == "[wanted] ✅ Now confirmed: DXCC ZL"     # VK was already
    assert e.rules[0].confirmed == {"VK", "ZL"}
    assert {a for _, a in fake.requests} == {f"jtcon/{qrz.__version__}"}
    e.de_call = "K2TTA"
    asyncio.run(refresh(e))
    assert fake.requests[-1][1] == f"jtcon/{qrz.__version__} (K2TTA)"


def test_engine_without_a_key_turns_qrz_off(engine):
    e = engine(api_key="")
    assert e._load_inputs() and e.qrz is None
    assert "[qrz] No API key: set api_key in [inputs.qrz], or QRZ_API_KEY — QRZ is off" in logs(e)
    e.sync()
    assert logs(e)[-1] == ("[sync] Nothing to sync: QRZ is off (see [inputs.qrz] and"
                           " [actions.qrz]).")


def test_engine_uploads_logged_qsos_when_asked(fake, engine, tmp_path):
    adif = ("<adif_ver:5>3.1.0 <programid:6>WSJT-X <eoh>\n"
            + adif_record({"call": "VK3XY", "qso_date": "20261014", "band": "20m"}))
    e = engine(enabled=False)
    e.config.actions.qrz_upload = True
    assert e._load_inputs()
    assert "[qrz] Logged QSOs are uploaded to QRZ" in logs(e)
    e.datagram_received(pk.logged_adif(adif), ("127.0.0.1", 2237))
    assert logs(e)[-1] == "[qrz] ▲ VK3XY uploaded to QRZ (logid 1000)"
    assert fake.requests[-1][0]["ADIF"].startswith("<call:5>VK3XY")
    e2 = engine()                                        # upload off: nothing sent
    assert e2._load_inputs()
    n = len(fake.requests)
    e2.datagram_received(pk.logged_adif(adif), ("127.0.0.1", 2237))
    assert len(fake.requests) == n


def test_engine_retries_saved_uploads_at_sync_and_exit(fake, engine, tmp_path):
    path = tmp_path / "qrz" / qrz.PENDING_FILE
    e = engine(enabled=False)
    e.config.actions.qrz_upload = True
    assert e._load_inputs()
    e.sync()
    assert logs(e)[-1] == ("[sync] Nothing to sync: no uploads are waiting, and the QRZ"
                           " download is off (set enabled = true in [inputs.qrz]).")
    fake.down = True
    adif = adif_record({"call": "ZL1AA", "qso_date": "20261014", "band": "20m"})
    e.datagram_received(pk.logged_adif(adif), ("127.0.0.1", 2237))
    assert path.exists()

    e2 = engine(enabled=False)                           # restarted
    e2.config.actions.qrz_upload = True
    assert e2._load_inputs()
    assert "[qrz] 1 upload waiting from last time — retrying now" in logs(e2)
    e2.sync()                                            # QRZ still down
    assert logs(e2)[-1].endswith("— 1 upload still waiting")
    fake.down = False
    out = []
    e2.retry_uploads_at_exit(out.append)
    assert out == ["[qrz] Trying 1 waiting upload before exit …",
                   "[qrz] ▲ ZL1AA uploaded to QRZ (logid 1000)"]
    assert not path.exists()
    e2.retry_uploads_at_exit(out.append)                 # nothing waiting: silent
    assert len(out) == 2


def test_engine_first_start_with_only_qrz_says_the_download_is_pending(engine):
    e = engine()
    assert e._load_inputs()
    assert "[adif] No QSOs yet: the QRZ logbook loads after its first download" in logs(e)


def test_engine_reports_award_items_newly_confirmed(fake, engine, tmp_path):
    import jtcon.core.engine as eng
    store = QrzStore(tmp_path / "qrz")
    day = qrz.utc_today()
    store.write_full(day, [rec(1, "VK3XY", "N"), rec(2, "ZL1AA", "N"), rec(3, "JA1ZZ", "C")])
    e = engine()                     # dxcc counted by QRZ confirmations
    assert e._load_inputs()
    assert not any("Now confirmed" in t for t in logs(e))         # not at startup
    store.append_updates([rec(1, "VK3XY", "C"), rec(2, "ZL1AA", "C")])
    e._apply_reload(e.adif_input, e._load_input(e.adif_input))
    assert logs(e)[-1] == "[wanted] ✅ Now confirmed: DXCC VK, ZL"
    n = len(logs(e))
    e._apply_reload(e.adif_input, e._load_input(e.adif_input))    # nothing new: quiet
    assert len(logs(e)) == n + 1 and logs(e)[-1].startswith("[adif] Reloaded")
    assert eng._now_confirmed([("DXCC", ["A", "B", "C"]), ("WAS", ["TX", "MA"])]) == (
        "[wanted] ✅ Now confirmed: DXCC A, B, C · WAS TX, MA")
    many = [("GRID", [f"FN{i:02d}" for i in range(25)]), ("WAS", ["TX"])]
    assert eng._now_confirmed(many).endswith("FN18, FN19 … and 6 more")


def test_reload_script_runs_for_log_files_not_qrz_refreshes(engine, tmp_path, monkeypatch):
    calls = []
    e = engine()
    monkeypatch.setattr(e, "_run_log_reload_script", calls.append)
    inp_store = QrzStore(tmp_path / "qrz")
    inp_store.write_full(qrz.utc_today(), [rec(1)])
    assert e._load_inputs()
    inp_store.append_updates([rec(2)])
    assert e.adif_input.changed()                        # the updates file is new
    e._apply_reload(e.adif_input, e._load_input(e.adif_input))
    assert calls == [] and len(e.logbook) == 2
