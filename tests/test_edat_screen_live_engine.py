"""The real-run wrapper against a fake oMLX on a random loopback port: saving and restoring the resident
models, refusals before anything is sent, and SIGTERM / SIGHUP. No real engine, no model."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from edat.screen import live
from tests.test_edat_screen_live import DEV, HELDOUT, fake_model, records

REF = live.REFERENCES[0]
ROOT = Path(__file__).resolve().parents[1]


class FakeEngine:
    """oMLX's /v1/models/status, unload and load, in memory. Records every call and whether a key came."""

    def __init__(self, models: dict[str, dict]):
        self.models = {k: dict(v) for k, v in models.items()}
        self.calls: list[str] = []
        self.keys_seen = 0
        eng = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _seen(self):
                if self.headers.get("Authorization") or self.headers.get("x-api-key"):
                    eng.keys_seen += 1
                eng.calls.append(f"{self.command} {self.path}")

            def do_GET(self):
                self._seen()
                if self.path == "/v1/models/status":
                    return self._reply(200, {"models": [{"id": k, **v} for k, v in eng.models.items()]})
                return self._reply(404, {})

            def do_POST(self):
                self._seen()
                parts = self.path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["v1", "models"] and parts[3] in ("load", "unload"):
                    m = eng.models.get(parts[2])
                    if m is None:
                        return self._reply(404, {})
                    m["loaded"] = parts[3] == "load"
                    return self._reply(200, {"ok": True})
                return self._reply(404, {})

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        except OSError as exc:            # a sandbox that forbids binding a local port
            pytest.skip(f"cannot bind a loopback port here: {exc}")
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def loaded(self) -> set[str]:
        return {k for k, v in self.models.items() if v["loaded"]}

    def close(self):
        self.server.shutdown()


START = {"user-chat-a": {"loaded": True, "pinned": True}, "user-chat-b": {"loaded": True, "pinned": False},
         "bge-embed-small": {"loaded": True, "pinned": False}, REF: {"loaded": False, "pinned": False}}


@pytest.fixture
def eng():
    e = FakeEngine(START)
    yield e
    e.close()


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(live, "harbor_running", lambda: False)
    monkeypatch.setattr(live, "memory_free_pct", lambda: 60)


def go(tmp_path, eng, **kw):
    kw.setdefault("plan", "smoke")
    kw.setdefault("tracks", ("ax_text",))
    return live.run(tmp_path / "out", client_kind="oracle", engine=True, base_url=eng.url, models=[fake_model()],
                    lock=str(tmp_path / "heavy.lock"), max_minutes=5, dev=DEV, heldout=HELDOUT, progress=None, **kw)


def test_a_sitting_unloads_the_others_then_puts_back_loaded_and_pinned_models(tmp_path, eng, host):
    log = go(tmp_path, eng)
    assert live.run_ok(log) and log["restored"] is True
    assert eng.loaded() == {"user-chat-a", "user-chat-b", "bge-embed-small"}          # as before the sitting
    assert "POST /v1/models/user-chat-a/unload" in eng.calls and "POST /v1/models/user-chat-a/load" in eng.calls
    assert not any("bge-embed-small" in c for c in eng.calls if c.startswith("POST"))  # helpers left alone
    st = json.loads((tmp_path / "out" / "engine-state.json").read_text())
    assert st["restored"] and st["pinned_before"] == ["user-chat-a"] and st["not_back"] == []
    assert eng.keys_seen == 0                                                        # never a key
    assert not (tmp_path / "heavy.lock").exists()


def test_a_crash_mid_run_still_restores_and_releases_the_lock(tmp_path, eng, host, monkeypatch):
    real, n = live.run_trial, {"i": 0}

    def crash(*a, **k):
        n["i"] += 1
        if n["i"] == 3:
            raise RuntimeError("simulated crash")
        return real(*a, **k)
    monkeypatch.setattr(live, "run_trial", crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        go(tmp_path, eng)
    assert eng.loaded() == {"user-chat-a", "user-chat-b", "bge-embed-small"}
    assert not (tmp_path / "heavy.lock").exists() and len(records(tmp_path)) == 2


def test_an_earlier_unrestored_sitting_blocks_the_next_one_until_restore(tmp_path, eng, host):
    out = tmp_path / "out"
    out.mkdir()
    (out / "engine-state.json").write_text(json.dumps({"sitting": "old", "base_url": eng.url, "restored": False,
                                                       "rows": [{"id": "user-chat-a", "loaded": True, "pinned": True,
                                                                 "is_loading": False}]}))
    with pytest.raises(SystemExit, match="never restored"):
        go(tmp_path, eng)
    assert not [c for c in eng.calls if c.startswith("POST")] and not (tmp_path / "heavy.lock").exists()
    eng.models["user-chat-a"]["loaded"] = False
    assert live.main(["restore", "--out", str(out), "--base-url", eng.url]) == 0
    assert "user-chat-a" in eng.loaded() and json.loads((out / "engine-state.json").read_text())["restored"]


def test_a_non_loopback_engine_address_is_refused_before_anything_is_sent(tmp_path, eng, host):
    with pytest.raises(Exception, match="loopback"):
        live.run(tmp_path / "out", plan="smoke", client_kind="oracle", engine=True, base_url="http://evil.example:9",
                 models=[fake_model()], lock=str(tmp_path / "heavy.lock"), dev=DEV, progress=None)
    assert not (tmp_path / "heavy.lock").exists() and not (tmp_path / "out").exists()
    lines = []
    assert live.preflight(tmp_path / "out", lock=str(tmp_path / "l"), engine=True, weights=True,
                          base_url="http://evil.example:9", print_=lines.append) is False
    assert "no further checks" in lines[-1] and not (tmp_path / "out").exists()


def test_preflight_with_results_inside_the_repository_stops_before_writing(tmp_path):
    lines = []
    target = live.REPO / "preflight-probe-out"
    assert live.preflight(target, lock=str(tmp_path / "l"), weights=True, print_=lines.append) is False
    assert len(lines) == 1 and not target.exists()


def test_a_run_where_every_trial_is_an_engine_fault_is_not_done(tmp_path, eng, host, monkeypatch):
    monkeypatch.setattr(live, "_client", lambda kind, m, url: (lambda: _AlwaysFails()))
    log = go(tmp_path, eng)
    assert log["units"][0]["result"].startswith("failed") and not live.run_ok(log)


class _AlwaysFails:
    def describe(self):
        return {"kind": "local"}

    def chat(self, messages, **kw):
        from edat.screen.adapter import EngineError
        raise EngineError("transport", "connection refused", retryable=False)


def test_keyless_engine_reads_omlx_settings(tmp_path):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"server": {"host": "127.0.0.1"}, "auth": {"api_key": "x", "skip_api_key_verification": True}}))
    assert live.keyless_engine(f) is None
    f.write_text(json.dumps({"server": {"host": "127.0.0.1"}, "auth": {"api_key": "x"}}))
    assert "requires its API key" in live.keyless_engine(f)
    f.write_text(json.dumps({"server": {"host": "0.0.0.0"}, "auth": {}}))
    assert "not only on loopback" in live.keyless_engine(f)


def test_harbor_check_fails_closed(monkeypatch):
    class R:
        def __init__(self, code):
            self.returncode, self.stdout = code, ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R(1))
    assert live.harbor_running() is False
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R(2))
    assert live.harbor_running() is True

    def boom(*a, **k):
        raise OSError("no pgrep")
    monkeypatch.setattr(subprocess, "run", boom)
    assert live.harbor_running() is True


DRIVER = r"""
import sys, time
sys.path.insert(0, {root!r})
from pathlib import Path
from edat.screen import live
from tests.test_edat_screen_live import DEV, HELDOUT, fake_model
live.harbor_running = lambda: False
live.memory_free_pct = lambda: 60
real = live.run_trial
def slow(*a, **k):
    Path({ready!r}).write_text("x")
    time.sleep(0.2)
    return real(*a, **k)
live.run_trial = slow
live.run(Path({out!r}), plan="smoke", tracks=("ax_text",), client_kind="oracle", engine=True, base_url={url!r},
         models=[fake_model()], lock={lock!r}, max_minutes=5, dev=DEV, heldout=HELDOUT, progress=None)
"""


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT])
def test_sigterm_sighup_and_sigquit_restore_the_engine_and_release_the_lock(tmp_path, eng, sig):
    ready, lock = tmp_path / "ready", tmp_path / "heavy.lock"
    code = DRIVER.format(root=str(ROOT), ready=str(ready), out=str(tmp_path / "out"), url=eng.url, lock=str(lock))
    p = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(300):
            if ready.exists():
                break
            time.sleep(0.05)
        assert ready.exists(), p.stderr.read().decode() if p.poll() is not None else "driver never started a trial"
        assert eng.loaded() == {"bge-embed-small"}                     # mid-run: the user's models are unloaded
        os.kill(p.pid, sig)
        assert p.wait(timeout=60) == 128 + sig
    finally:
        if p.poll() is None:
            p.kill()
    assert eng.loaded() == {"user-chat-a", "user-chat-b", "bge-embed-small"}
    assert not lock.exists()
    assert json.loads((tmp_path / "out" / "engine-state.json").read_text())["restored"] is True


def test_engine_calls_ignore_proxy_settings(tmp_path, eng, host, monkeypatch):
    for v in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(v, "http://proxy.invalid:3128")
    monkeypatch.setenv("NO_PROXY", "")
    log = go(tmp_path, eng)
    assert live.run_ok(log) and eng.loaded() == {"user-chat-a", "user-chat-b", "bge-embed-small"}


def test_restore_waits_while_harbor_runs_and_the_next_sitting_refuses(tmp_path, eng, monkeypatch):
    monkeypatch.setattr(live, "memory_free_pct", lambda: 60)
    state = {"harbor": False}
    monkeypatch.setattr(live, "harbor_running", lambda: state["harbor"])
    real = live.run_trial

    def harbor_opens(*a, **k):
        state["harbor"] = True
        return real(*a, **k)
    monkeypatch.setattr(live, "run_trial", harbor_opens)
    log = go(tmp_path, eng)
    assert "Harbor" in str(log["restored"]) and not live.run_ok(log)
    assert not any(c.endswith("/load") for c in eng.calls)                 # the engine was left alone
    assert not (tmp_path / "heavy.lock").exists()
    state["harbor"] = False                                                  # Harbor closed again
    monkeypatch.setattr(live, "run_trial", real)
    with pytest.raises(SystemExit, match="never restored"):
        go(tmp_path, eng)
    assert live.main(["restore", "--out", str(tmp_path / "out"), "--base-url", eng.url]) == 0
    assert eng.loaded() == {"user-chat-a", "user-chat-b", "bge-embed-small"}
