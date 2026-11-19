"""The PR 25 safety review's probes, kept as regression tests for the run guard.

A fake oMLX is served over real HTTP on an ephemeral loopback port, never
8000, 8765 or 8791. A second server records anything that reaches it, so it
catches a redirect or a proxy. HOME is a temp directory holding a fake
~/.omlx/settings.json with a fake key. The real CLI
(`edat.cases.agentic_run.main`) runs with stub model clients and stub Harbor
and memory probes; every engine call is real HTTP to the fake.

Skipped where binding a loopback port is not permitted (a sandbox).
"""
import json
import os
import signal
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from edat.cases import agentic_run as ar
from edat.models import NullClient
from edat.models.base import EngineError
from edat.runtime import run_guard as rg

KEY = "FAKEKEY-probe-0000"
TUNED = "gemma-4-E4B-it-r5-step-01800-oQ4e-mtp"


def _can_bind() -> bool:
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.close()
        return True
    except OSError:
        return False


class Fake:
    """oMLX's model API over HTTP (status, health, unload, load, chat), plus a recorder server."""

    def __init__(self):
        self.loaded: list[str] = []
        self.log: list[tuple] = []         # (server, method, path, Authorization)
        self.fail_load = False
        self.servers = [self._serve("engine"), self._serve("recorder")]
        self.port, self.rport = (s.server_address[1] for s in self.servers)
        assert not {self.port, self.rport} & {8000, 8765, 8791}

    def _serve(self, name):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=None, headers=()):
                data = json.dumps(body or {}).encode()
                self.send_response(code)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _rec(self):
                fake.log.append((name, self.command, self.path, self.headers.get("Authorization")))

            def do_GET(self):
                self._rec()
                if name == "recorder":
                    return self._send(200, {"caught": True})
                if self.path.startswith("/redir"):
                    return self._send(307, {}, [("Location", f"http://127.0.0.1:{fake.rport}/caught")])
                if self.path == "/v1/models/status":
                    ids = sorted({*fake.loaded, TUNED, "user-model-A", "user-model-B", "bge-embed-x"})
                    return self._send(200, {"models": [{"id": m, "loaded": m in fake.loaded} for m in ids]})
                if self.path == "/health":
                    return self._send(200, {"default_model": "user-model-A"})
                self._send(404)

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self._rec()
                if name == "recorder":
                    return self._send(200, {"caught": True})
                parts = self.path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["v1", "models"]:
                    m, op = parts[2], parts[3]
                    if op == "unload":
                        if m not in fake.loaded:
                            return self._send(400, {})
                        fake.loaded.remove(m)
                        return self._send(200, {})
                    if op == "load":
                        if fake.fail_load:
                            return self._send(500, {"error": "nope"})
                        if m not in fake.loaded:
                            fake.loaded.append(m)
                        return self._send(200, {})
                self._send(404)

        s = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        return s

    def close(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()

    def chat_models_loaded(self):
        return sorted(m for m in self.loaded if "embed" not in m)


INIT = ["user-model-A", "bge-embed-x"]


@pytest.fixture
def probe(tmp_path, monkeypatch):
    if not _can_bind():
        pytest.skip("cannot bind a loopback port here")
    fake = Fake()
    home = tmp_path / "home"
    (home / ".omlx").mkdir(parents=True)
    (home / ".omlx" / "settings.json").write_text(json.dumps({"auth": {"api_key": KEY}, "version": "0.0-fake"}))
    monkeypatch.setenv("HOME", str(home))
    for v in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY"):
        monkeypatch.delenv(v, raising=False)
    from edat.cases.loader import load_split
    from edat.cases.variants import single_turn
    cases = sorted((c for c in load_split("dev", lanes=["neutral"], families=["select"]) if single_turn(c)),
                   key=lambda c: c["id"])[:3]
    monkeypatch.setattr(ar, "select", lambda plan, splits=ar.SPLITS: list(cases))
    real_run = ar.run
    box = {"client": NullClient}

    def wrapped(out, **kw):      # the CLI's run(), with stub model clients and host probes
        kw.update(clients=lambda m, l: box["client"](), isolate=False, weights=lambda m: "0" * 64,
                  harbor=lambda: None, free_pct=lambda: 80, column=lambda c: True, progress=None)
        return real_run(out, **kw)
    monkeypatch.setattr(ar, "run", wrapped)

    def main(name, *extra, init=INIT):
        fake.loaded = list(init)
        fake.log.clear()
        work = tmp_path / name
        argv = ["run", "--out", str(work / "out"), "--plan", "smoke", "--board-version", "probe",
                "--lock", str(tmp_path / "heavy.lock"), "--url", f"http://127.0.0.1:{fake.port}", *extra]
        return ar.main(argv)
    yield fake, main, box, tmp_path
    fake.close()


def report(tmp_path, name):
    return json.loads((tmp_path / name / "out" / "smoke" / "run-report.json").read_text())


def clean(fake, tmp_path, init=INIT):
    assert sorted(fake.loaded) == sorted(init)
    assert not (tmp_path / "heavy.lock").exists()
    assert not (tmp_path / "heavy.lock.engine-save.json").exists()


def test_normal_end(probe):
    fake, main, box, tmp_path = probe
    assert main("normal") == 0
    clean(fake, tmp_path)
    assert f"POST /v1/models/{TUNED}/load" in [f"{m} {p}" for _, m, p, _ in fake.log]
    assert report(tmp_path, "normal")["outcome"] == "finished"


@pytest.mark.parametrize("sig", ["SIGHUP", "SIGTERM", "SIGQUIT"])
def test_signal_mid_trial(probe, sig):
    """Finding A: SIGHUP left the engine unrestored and the lock behind."""
    fake, main, box, tmp_path = probe
    signum = getattr(signal, sig)

    class SigC(NullClient):
        def chat(self, *a, **k):
            os.kill(os.getpid(), signum)
            return super().chat(*a, **k)
    box["client"] = SigC
    before = signal.getsignal(signum)
    assert main("sig") == 128 + signum
    clean(fake, tmp_path)
    assert report(tmp_path, "sig")["outcome"] == "failed"
    assert signal.getsignal(signum) == before


def test_keyboard_interrupt_mid_trial(probe):
    fake, main, box, tmp_path = probe

    class KI(NullClient):
        def chat(self, *a, **k):
            raise KeyboardInterrupt
    box["client"] = KI
    with pytest.raises(KeyboardInterrupt):
        main("ki")
    clean(fake, tmp_path)
    assert report(tmp_path, "ki")["failed"].startswith("KeyboardInterrupt")


def test_stale_state_is_never_restored(probe):
    """Finding B: run 2 in the same OUT restored run 1's saved state."""
    fake, main, box, tmp_path = probe
    assert main("stale") == 0
    assert main("stale", "--trials", "2", init=["user-model-B"]) == 0
    clean(fake, tmp_path, init=["user-model-B"])


def test_failed_restore_still_writes_the_report(probe):
    """Finding C: a restore raising in `finally` lost the report."""
    fake, main, box, tmp_path = probe

    class FailLoad(NullClient):
        def chat(self, *a, **k):
            fake.fail_load = True
            return super().chat(*a, **k)
    box["client"] = FailLoad
    assert main("rf") == 1                               # GuardError or HTTP error, reported, exit 1
    r = report(tmp_path, "rf")
    assert r["outcome"] == "failed" and r["engine"].startswith("restore FAILED") and "run_guard restore" in r["restore_with"]
    assert not (tmp_path / "heavy.lock").exists()
    assert (tmp_path / "heavy.lock.engine-save.json").exists()
    fake.fail_load = False
    box["client"] = NullClient
    assert main("after") == 1                            # the leftover refuses the next start
    assert rg.restore(rg.Engine(f"http://127.0.0.1:{fake.port}"), tmp_path / "heavy.lock.engine-save.json",
                      harbor=lambda: None).startswith("engine restored")
    assert sorted(fake.loaded) == sorted(INIT)


def test_all_faults_smoke_fails(probe):
    """Finding E: an all-excluded smoke reported 'finished' and exited 0."""
    fake, main, box, tmp_path = probe

    class Fault(NullClient):
        def chat(self, *a, **k):
            raise EngineError("transport", "connection refused")
    box["client"] = Fault
    assert main("faults") == 1
    assert "every model trial" in report(tmp_path, "faults")["why"]
    clean(fake, tmp_path)


def test_stop_file_mid_run_is_resumable(probe):
    fake, main, box, tmp_path = probe
    out = tmp_path / "stop" / "out" / "smoke"

    class StopC(NullClient):
        def chat(self, *a, **k):
            (out / "STOP").write_text("")
            return super().chat(*a, **k)
    box["client"] = StopC
    assert main("stop") == 3
    clean(fake, tmp_path)


def test_lock_held_and_empty_lock(probe):
    fake, main, box, tmp_path = probe
    (tmp_path / "heavy.lock").mkdir()
    assert main("held") == 1
    assert (tmp_path / "heavy.lock").is_dir() and fake.log == []
    (tmp_path / "heavy.lock").rmdir()
    argv = ["run", "--out", str(tmp_path / "e" / "out"), "--plan", "smoke", "--board-version", "p", "--lock", "",
            "--url", f"http://127.0.0.1:{fake.port}"]
    assert ar.main(argv) == 1 and fake.log == []


def test_key_goes_only_to_the_engine_and_is_never_stored(probe, monkeypatch):
    fake, main, box, tmp_path = probe
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{fake.rport}")      # proxies are ignored
    monkeypatch.setenv("ALL_PROXY", f"http://127.0.0.1:{fake.rport}")
    assert main("key") == 0
    assert fake.log and all(s == "engine" for s, *_ in fake.log)
    assert all(a == f"Bearer {KEY}" for *_, a in fake.log)
    assert all(KEY not in v for v in os.environ.values())
    for f in tmp_path.rglob("*"):
        if f.is_file() and "home" not in f.parts:
            assert KEY not in f.read_text(errors="ignore"), f


def test_no_redirect_is_followed(probe):
    fake, main, box, tmp_path = probe
    e = rg.Engine(f"http://127.0.0.1:{fake.port}", api_key=KEY)
    with pytest.raises(Exception):
        e._client.get("/redir").raise_for_status()
    assert not any(s == "recorder" for s, *_ in fake.log)


def test_url_tricks_refused():
    accepted = {"http://127.0.0.1:1", "http://[::1]:1", "http://127.0.0.1:65535/"}
    tricks = ["http://localhost:1", "http://LOCALHOST:1/", "http://127.0.0.1:0", "http://127.0.0.1.evil:1", "http://localhost@evil:1", "http://evil@127.0.0.1:1",
              "http://0.0.0.0:1", "http://[::ffff:127.0.0.1]:1", "http://127.0.0.1\\@evil:1",
              "https://127.0.0.1:1", "http://127.0.0.1", "http://2130706433:1", "http://127.1:1",
              "http://127.0.0.1:1#@evil:2", "http://evil.com:1/?x=127.0.0.1"]
    for u in tricks:
        with pytest.raises(rg.GuardError):
            rg.loopback_base_url(u)
    for u in accepted:
        rg.loopback_base_url(u)


def test_one_url_for_unloads_and_chats(probe):
    """Unloads used TINYAGENT_ENGINE_PORT while chats used --port; now one URL serves both."""
    fake, main, box, tmp_path = probe
    os.environ["TINYAGENT_ENGINE_PORT"] = "1"           # ignored: no engine call reads it any more
    try:
        assert main("oneurl") == 0
    finally:
        del os.environ["TINYAGENT_ENGINE_PORT"]
    assert fake.log
    get = ar._default_clients(f"http://127.0.0.1:{fake.port}", KEY)
    assert get(ar.references()[0], "reference-loop").base_url == f"http://127.0.0.1:{fake.port}"
