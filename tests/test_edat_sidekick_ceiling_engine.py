"""The ceiling run's engine safety, against a fake engine on loopback (no model, no oMLX).

The engine address is loopback only and checked before any header (oMLX's key) is built; redirects and
proxies are never followed; unloads, loads and chats use one address; the engine state saved by the run
(loaded and pinned models) is put back on every way out, SIGTERM and SIGHUP included, and the lock is
removed; a state left by an earlier run is never restored; preflight writes nothing inside the
repository; a run with no graded trial, or only engine faults, is a failed run. Every model id is invented.
"""
from __future__ import annotations

import json
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from edat.sidekick.ceiling import engine as eng
from edat.sidekick.ceiling import models
from edat.sidekick.ceiling import run as run_mod
from edat.sidekick.ceiling.run import Quick, run_all


class FakeEngine:
    """Enough of oMLX's API for keep and restore: status, list, load, unload; records every request."""

    def __init__(self, loaded=(), pinned=(), ids=("model-a", "model-b", "model-c", "helper-embed"), redirect_to=None):
        self.loaded, self.pinned, self.ids = set(loaded), set(pinned), list(ids)
        self.requests: list[tuple[str, str, str | None]] = []
        self.redirect_to = redirect_to
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=None, headers=()):
                self.send_response(code)
                for k, v in headers:
                    self.send_header(k, v)
                data = json.dumps(body or {}).encode()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _route(self, method):
                fake.requests.append((method, self.path, self.headers.get("Authorization")))
                if fake.redirect_to:
                    return self._send(307, {}, [("Location", fake.redirect_to + self.path)])
                if method == "GET" and self.path == "/v1/models":
                    return self._send(200, {"data": [{"id": i} for i in fake.ids]})
                if method == "GET" and self.path == "/v1/models/status":
                    return self._send(200, {"models": [{"id": i, "loaded": i in fake.loaded, "pinned": i in fake.pinned}
                                                       for i in fake.ids]})
                parts = self.path.strip("/").split("/")
                if method == "POST" and len(parts) == 4 and parts[3] in ("load", "unload"):
                    (fake.loaded.add if parts[3] == "load" else fake.loaded.discard)(parts[2])
                    return self._send(200, {"status": "ok"})
                return self._send(404)

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    f = FakeEngine(loaded=("model-a", "model-b", "helper-embed"), pinned=("model-a",))
    yield f
    f.close()


@pytest.fixture
def key(monkeypatch):
    """oMLX's key as engine_prep reads it, counted: a refused address must never build it."""
    calls = []
    import scripts.productbench.engine_prep as ep
    monkeypatch.setattr(ep, "_headers", lambda: calls.append(1) or {"Authorization": "Bearer test-key"})
    return calls


def _model(engine_id="model-c"):
    return models.LocalModel(engine_id=engine_id, folder=engine_id, label=engine_id, role="candidate", model_type="gemma4",
                             family="gemma4", loader="mlx-vlm", disk_gb=1.0,
                             quant={"scheme": "oQ4e", "bits": 4, "group_size": 64, "mixed": False},
                             bits_per_weight=4.6, thinking=(False,), standard=True, group=engine_id, decode_tok_s=55.0)


# ------------------------------------------------------------------ loopback only, checked before the key

@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://127.0.0.1:8000/", "http://[::1]:8000"])
def test_loopback_addresses_are_accepted(url):
    assert eng.loopback_url(url).endswith(":8000")


@pytest.mark.parametrize("url", [
    "http://example.test:8000",                  # another host
    "http://localhost:8000",                     # a name: resolved at call time, so maybe not loopback
    "http://127.0.0.1.example.test:8000",        # a name that starts like loopback
    "http://user:pw@127.0.0.1:8000",             # userinfo
    "http://127.0.0.1@example.test:8000",        # userinfo hiding the real host
    "http://127.0.0.1:8000@example.test:9000",
    "https://127.0.0.1:8000",                    # not plain http
    "http://127.0.0.1",                          # no port
    "http://127.0.0.1:8000/v1",                  # a path
    "http://127.0.0.1:8000/?next=http://example.test",
    "http://127.0.0.2:8000",                     # loopback range, not the engine's address
    "http://0x7f000001:8000",                    # another spelling of loopback
    "http://127.1:8000",
    "http://[::ffff:127.0.0.1]:8000",
    "http://10.0.0.5:8000",
    "",
])
def test_anything_else_is_refused_before_the_key_is_built(url, key):
    with pytest.raises(eng.EngineAddressError):
        eng.client(url)
    with pytest.raises(eng.EngineAddressError):
        run_mod.engine_check(_model(), url)
    assert key == []


def test_run_run_all_and_preflight_refuse_at_the_top(tmp_path, key):
    bad = "http://example.test:8000"
    with pytest.raises(ValueError):
        run_all(tmp_path / "out", plan="quick", client_kind="omlx", progress=None, models=[_model()], base_url=bad,
                lock=str(tmp_path / "lock"))
    with pytest.raises(ValueError):
        run_mod.run_entry(_model().entries()[0], tmp_path / "out", plan="quick", base_url=bad)
    with pytest.raises(ValueError):
        run_mod.preflight(tmp_path / "out", base_url=bad)
    assert run_mod.preflight_main(["--out", str(tmp_path / "out"), "--base-url", bad]) == 2
    assert key == [] and not (tmp_path / "lock").exists() and not (tmp_path / "out").exists()


def test_a_redirect_is_never_followed_and_the_key_goes_nowhere_else(key):
    other = FakeEngine()
    front = FakeEngine(redirect_to=other.url)
    try:
        with pytest.raises(RuntimeError, match="307"):
            with eng.client(front.url) as c:
                eng.status(c)
        assert run_mod.engine_check(_model(), front.url).startswith("the engine at")
        assert front.requests and other.requests == []
    finally:
        front.close()
        other.close()


def test_proxy_settings_in_the_environment_are_not_used(fake, key, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://example.test:3128")
    monkeypatch.setenv("ALL_PROXY", "http://example.test:3128")
    with eng.client(fake.url) as c:
        assert "model-a" in eng.listed(c)


# ------------------------------------------------------------------ saved state, put back on every way out

def _entry_stub(calls, then=None):
    def run_entry(e, out, **kw):
        calls.append(e.model.engine_id)
        if then:
            then()
        return {"skipped": None, "stopped": None, "written": 1, "summary": {"ceiling": 1, "solid": 1, "excluded": 0}}
    return run_entry


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(run_mod, "harbor_running", lambda: False)
    monkeypatch.setattr(run_mod, "weights_for", lambda out, mdir, m: "0" * 64)


def test_keep_unloads_on_the_same_engine_and_restore_puts_back_loaded_and_pinned(tmp_path, fake, key, quiet, monkeypatch):
    calls = []
    seen = []
    monkeypatch.setattr(run_mod, "_run_entry", _entry_stub(calls, lambda: seen.append(set(fake.loaded))))
    log = run_all(tmp_path / "out", plan="quick", client_kind="omlx", progress=None, models=[_model()],
                  base_url=fake.url, lock=str(tmp_path / "lock"))
    assert seen == [{"helper-embed"}]                       # only the helper stayed while the model ran
    assert fake.loaded == {"model-a", "model-b", "helper-embed"} and fake.pinned == {"model-a"}
    assert log["engine_restored"].startswith("restored") and not log["failed"]
    saved = json.loads((tmp_path / "out" / "resident-before.json").read_text())
    assert saved["loaded"] == ["helper-embed", "model-a", "model-b"] and saved["pinned"] == ["model-a"]
    assert all(p.startswith("/v1/") for _, p, _ in fake.requests) and not (tmp_path / "lock").exists()


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_sigterm_and_sighup_restore_the_engine_and_remove_the_lock(tmp_path, fake, key, quiet, monkeypatch, sig):
    calls = []
    before = {sig_: signal.getsignal(sig_) for sig_ in (signal.SIGTERM, signal.SIGHUP)}
    monkeypatch.setattr(run_mod, "_run_entry", _entry_stub(calls, lambda: os.kill(os.getpid(), sig)))
    with pytest.raises(SystemExit) as exc:
        run_all(tmp_path / "out", plan="quick", client_kind="omlx", progress=None, models=[_model(), _model("model-b")],
                base_url=fake.url, lock=str(tmp_path / "lock"))
    assert exc.value.code == 128 + sig and len(calls) == 1          # stopped in the first entry
    assert fake.loaded == {"model-a", "model-b", "helper-embed"} and not (tmp_path / "lock").exists()
    log = json.loads(next((tmp_path / "out").rglob("run-all.json")).read_text())
    assert log["engine_restored"].startswith("restored")
    assert {s: signal.getsignal(s) for s in before} == before


def test_a_state_left_by_an_earlier_run_is_never_restored(tmp_path, fake, key, quiet, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "resident-before.json").write_text(json.dumps({"loaded": ["model-c"], "pinned": []}))
    monkeypatch.setattr(run_mod, "_run_entry", _entry_stub([]))
    run_all(out, plan="quick", client_kind="omlx", progress=None, models=[_model("model-b")], base_url=fake.url)
    assert fake.loaded == {"model-a", "model-b", "helper-embed"}        # not model-c, the earlier run's state
    assert list(out.glob("resident-before.stale-*.json"))
    st = eng.EngineState(out, fake.url)
    st.saved = json.loads((out / "resident-before.json").read_text())
    with pytest.raises(RuntimeError, match="not this run's saved state"):
        st.restore()                                    # another run's token: refused


def test_a_failure_creating_the_results_store_still_frees_the_lock(tmp_path, quiet, monkeypatch):
    out = tmp_path / "out"
    out.write_text("a file where the folder should be")
    with pytest.raises(OSError):
        run_all(out, plan="quick", client_kind="oracle", progress=None, models=[_model()], lock=str(tmp_path / "lock"))
    assert not (tmp_path / "lock").exists()


def test_preflight_writes_nothing_inside_the_repository(monkeypatch):
    repo = Path(run_mod.__file__).resolve().parents[3]
    out = repo / "edat-preflight-out-test"
    monkeypatch.setattr(run_mod, "require_sandbox", lambda: type("R", (), {"passed": True, "probes": []})())
    monkeypatch.setattr(run_mod, "model_dir_from_settings", lambda: None)
    monkeypatch.setattr(run_mod, "harbor_running", lambda: False)
    rows = run_mod.preflight(out, weights=True)
    assert not out.exists()
    assert ("results folder outside the repository", False) in [(n, ok) for n, ok, _ in rows]


def test_a_run_of_only_engine_faults_is_a_failed_run(tmp_path, quiet, monkeypatch):
    monkeypatch.setattr(run_mod, "_run_entry", lambda e, out, **kw: {
        "skipped": "the engine could not run this model", "stopped": None, "written": 3,
        "summary": {"ceiling": 0, "solid": 0, "excluded": 3}})
    log = run_all(tmp_path / "out", plan="quick", client_kind="oracle", progress=None,
                  models=[_model(), _model("model-b")], lock=str(tmp_path / "lock"))
    assert log["failed"] is True
    monkeypatch.setattr(run_mod, "_run_entry", _entry_stub([]))
    log = run_all(tmp_path / "out2", plan="quick", client_kind="oracle", progress=None, models=[_model()])
    assert log["failed"] is False
