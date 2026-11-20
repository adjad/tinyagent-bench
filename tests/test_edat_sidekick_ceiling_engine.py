"""The ceiling run's engine safety, against a fake engine on loopback (no model, no oMLX).

The engine address is loopback only and checked before any header (oMLX's key) is built; redirects and
proxies are never followed. A run with the engine goes through the shared run guard
(edat/runtime/run_guard.py): one chat model resident, the engine state saved fresh and put back on every
way out, signals included, Harbor checked failing closed, the lock always released. The tests marked P1
to P11 are the regression tests for PR 27's review findings. Every model id is invented.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from edat.sidekick.ceiling import engine as eng
from edat.sidekick.ceiling import models
from edat.sidekick.ceiling import run as run_mod
from edat.runtime import run_guard as rg
from edat.sidekick.ceiling.run import Quick, run_all


class FakeEngine:
    """Enough of oMLX's API for the run guard: status (with is_loading), list, load, unload; records every
    request. `unload_lag` and `load_lag` (seconds) make unloads answer 202 and finish later and loads show
    is_loading first, as oMLX does; `fail_loads` answers 500 to loads; `hook(method, path)` runs before each
    request is answered."""

    def __init__(self, loaded=(), pinned=(), ids=("model-a", "model-b", "model-c", "helper-embed"), redirect_to=None,
                 unload_lag=0.0, load_lag=0.0, hook=None):
        self.loaded, self.pinned, self.ids = set(loaded), set(pinned), list(ids)
        self.loading: set[str] = set()
        self.requests: list[tuple[str, str, str | None]] = []
        self.redirect_to = redirect_to
        self.unload_lag, self.load_lag, self.hook, self.fail_loads = unload_lag, load_lag, hook, False
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
                if fake.hook:
                    fake.hook(method, self.path)
                if fake.redirect_to:
                    return self._send(307, {}, [("Location", fake.redirect_to + self.path)])
                if method == "GET" and self.path == "/v1/models":
                    return self._send(200, {"data": [{"id": i} for i in fake.ids]})
                if method == "GET" and self.path == "/v1/models/status":
                    return self._send(200, {"models": [{"id": i, "loaded": i in fake.loaded, "pinned": i in fake.pinned,
                                                        "is_loading": i in fake.loading} for i in fake.ids]})
                parts = self.path.strip("/").split("/")
                if method == "POST" and len(parts) == 4 and parts[3] in ("load", "unload"):
                    mid = parts[2]
                    if parts[3] == "load" and fake.fail_loads:
                        return self._send(500)
                    if parts[3] == "unload" and fake.unload_lag:
                        threading.Timer(fake.unload_lag, lambda: fake.loaded.discard(mid)).start()
                        return self._send(202, {"status": "unloading"})
                    if parts[3] == "load" and fake.load_lag:
                        fake.loading.add(mid)

                        def done():
                            fake.loading.discard(mid)
                            fake.loaded.add(mid)
                        threading.Timer(fake.load_lag, done).start()
                        return self._send(200, {"status": "ok"})
                    (fake.loaded.add if parts[3] == "load" else fake.loaded.discard)(mid)
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
    """oMLX's key as engine_prep and the run guard read it, counted: a refused address must never build it,
    and no test reads the real one."""
    calls = []
    import scripts.productbench.engine_prep as ep
    monkeypatch.setattr(ep, "_headers", lambda: calls.append(1) or {"Authorization": "Bearer test-key"})
    monkeypatch.setattr(rg, "read_engine_key", lambda settings=None: calls.append(1) or "test-key")
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


# ------------------------------------------------------------------ the run guard: one model, put back on every way out

BEFORE = {"model-a", "model-b", "helper-embed"}


def _entry_stub(calls, then=None):
    def run_entry(e, out, **kw):
        calls.append(e.model.engine_id)
        if then:
            then()
        if kw.get("guard") is not None:
            kw["guard"].note("ok")
        return {"skipped": None, "stopped": None, "written": 1, "sitting": {"ok": 1, "excluded": 0, "error": 0},
                "summary": {"ceiling": 1, "solid": 1, "excluded": 0}}
    return run_entry


@pytest.fixture(autouse=True)
def own_save(monkeypatch, tmp_path):
    """The host-wide engine save in the test's own folder: no test writes the host's."""
    monkeypatch.setattr(run_mod, "ENGINE_SAVE", str(tmp_path / "lock.engine-save.json"))


@pytest.fixture
def quiet(monkeypatch):
    """Harbor closed and memory free, as this module's checks see them (the guard is given these)."""
    monkeypatch.setattr(run_mod, "harbor_running", lambda: None)
    monkeypatch.setattr(run_mod, "memory_free_pct", lambda: 90)
    monkeypatch.setattr(run_mod, "weights_for", lambda out, mdir, m: "0" * 64)


def _guarded(tmp_path, f, models=None):
    return run_all(tmp_path / "out", plan="quick", client_kind="omlx", progress=None, models=models or [_model()],
                   base_url=f.url, lock=str(tmp_path / "lock"))


def _save(tmp_path) -> Path:
    return tmp_path / "lock.engine-save.json"           # the guard's host-wide save (ENGINE_SAVE, set by quiet)


def _posts(f, since=0):
    return [p for m, p, _ in f.requests[since:] if m == "POST"]


def test_one_chat_model_resident_then_loaded_and_pinned_put_back(tmp_path, fake, key, quiet, monkeypatch):
    seen = []
    monkeypatch.setattr(run_mod, "_run_entry", _entry_stub([], lambda: seen.append(set(fake.loaded))))
    log = _guarded(tmp_path, fake)
    assert seen == [{"model-c", "helper-embed"}]        # the pinned model-a was unloaded too: one chat model
    assert fake.loaded == BEFORE and fake.pinned == {"model-a"}
    assert log["outcome"] == "finished" and log["failed"] is False and log["exit_code"] == 0
    assert log["engine_restored"].startswith("engine restored")
    report = json.loads((tmp_path / "out" / "run-guard-quick.json").read_text())
    assert report["engine_before"]["loaded"] == ["model-a", "model-b"] and report["engine_before"]["pinned"] == ["model-a"]
    assert not _save(tmp_path).exists() and not (tmp_path / "lock").exists()


def test_an_engine_run_needs_the_lock(tmp_path, fake, key, quiet):
    with pytest.raises(rg.GuardError, match="lock"):
        run_all(tmp_path / "out", plan="quick", client_kind="omlx", progress=None, models=[_model()], base_url=fake.url)
    assert fake.requests == []


def test_harbor_open_at_the_start_refuses_and_touches_nothing(tmp_path, fake, key, quiet, monkeypatch):
    monkeypatch.setattr(run_mod, "harbor_running", lambda: "process matching 'Harbor'")
    with pytest.raises(rg.GuardError, match="Harbor"):
        _guarded(tmp_path, fake)
    assert fake.requests == [] and not (tmp_path / "lock").exists() and not _save(tmp_path).exists()


def test_p1_p2_an_unrestored_save_refuses_the_next_start_until_it_is_restored(tmp_path, fake, key, quiet, monkeypatch):
    """P1, P2: a sitting killed before its restore leaves the host-wide save. The next start refuses before any
    engine call (it never saves the damaged state as the user's), preflight fails on it, nothing is set aside
    under another name, and the guard's restore command puts the engine back."""
    monkeypatch.setattr(run_mod, "_run_entry", _entry_stub([]))
    earlier = {"schema": rg.SAVE_SCHEMA, "token": "an-earlier-sitting", "created_at": "2026-01-01T00:00:00+00:00",
               "base_url": fake.url, "loaded": ["model-a", "model-b"], "pinned": ["model-a"],
               "helpers_loaded": ["helper-embed"], "default_model": None}
    _save(tmp_path).write_text(json.dumps(earlier))
    fake.loaded = {"model-c", "helper-embed"}           # what the killed sitting left loaded
    with pytest.raises(rg.GuardError, match="never restored"):
        _guarded(tmp_path, fake)
    assert fake.requests == [] and fake.loaded == {"model-c", "helper-embed"}
    assert json.loads(_save(tmp_path).read_text()) == earlier and not (tmp_path / "lock").exists()
    assert not list(tmp_path.rglob("*stale*"))
    monkeypatch.setattr(run_mod, "require_sandbox", lambda: type("R", (), {"passed": True, "probes": []})())
    monkeypatch.setattr(run_mod, "model_dir_from_settings", lambda: None)
    rows = {n: ok for n, ok, _ in run_mod.preflight(tmp_path / "out", lock=str(tmp_path / "lock"))}
    assert rows["no unrestored engine state from an earlier run"] is False
    assert rg.restore_cli(_save(tmp_path), fake.url, str(tmp_path / "restore.lock"), harbor=lambda: None) == 0
    assert fake.loaded == BEFORE and not _save(tmp_path).exists()
    assert _guarded(tmp_path, fake)["outcome"] == "finished" and fake.loaded == BEFORE


def test_p3_the_harbor_check_fails_closed(monkeypatch):
    monkeypatch.setattr(rg.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 3, "", "pgrep: cannot list processes"))
    assert run_mod.harbor_running().startswith("unknown")

    def no_pgrep(*a, **k):
        raise OSError("no pgrep")
    monkeypatch.setattr(rg.subprocess, "run", no_pgrep)
    assert run_mod.harbor_running().startswith("unknown")


def test_p4_harbor_opening_mid_run_stops_it_and_nothing_more_is_sent(tmp_path, fake, key, quiet, monkeypatch):
    """Harbor opens during the first model: the second is not prepared, the restore sends no unload or load,
    the save is kept with the command to restore it, and the run is failed."""
    state = {"open": False, "n": 0}
    monkeypatch.setattr(run_mod, "harbor_running", lambda: "process matching 'Harbor'" if state["open"] else None)

    def opens():
        state.update(open=True, n=len(fake.requests))
    monkeypatch.setattr(run_mod, "_run_entry", _entry_stub([], opens))
    log = _guarded(tmp_path, fake, models=[_model(), _model("model-b")])
    assert [r["result"] for r in log["entries"]] == ["done", "not started"] and "Harbor" in log["entries"][1]["why"]
    assert _posts(fake, state["n"]) == []
    assert log["outcome"] == "failed" and log["failed"] is True and "Harbor" in log["engine_restored"]
    assert _save(tmp_path).exists() and "run_guard restore" in log["restore_with"] and not (tmp_path / "lock").exists()


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT])
def test_p5_each_signal_interrupts_restores_and_releases(tmp_path, fake, key, quiet, monkeypatch, sig):
    calls, seen = [], {}
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGINT)}

    def hit():
        seen["handler"] = signal.getsignal(sig)
        os.kill(os.getpid(), sig)
    monkeypatch.setattr(run_mod, "_run_entry", _entry_stub(calls, hit))
    with pytest.raises(rg.RunInterrupted):
        _guarded(tmp_path, fake, models=[_model(), _model("model-b")])
    assert len(calls) == 1 and seen["handler"] not in (signal.SIG_DFL, signal.SIG_IGN, before[sig])
    assert fake.loaded == BEFORE and not _save(tmp_path).exists() and not (tmp_path / "lock").exists()
    log = json.loads(next((tmp_path / "out").rglob("run-all.json")).read_text())
    assert log["outcome"] == "failed" and log["engine_restored"].startswith("engine restored")
    assert {s: signal.getsignal(s) for s in before} == before


def test_p6_a_second_signal_during_the_restore_is_ignored(tmp_path, key, quiet, monkeypatch):
    state = {"armed": False, "fired": False}

    def hook(method, path):
        if state["armed"] and method == "POST" and path.endswith("/load") and not state["fired"]:
            state["fired"] = True
            os.kill(os.getpid(), signal.SIGTERM)        # while the restore waits on its first load
            time.sleep(0.3)
    f = FakeEngine(loaded=("model-a", "model-b", "helper-embed"), pinned=("model-a",), hook=hook)
    try:
        def first():
            state["armed"] = True
            os.kill(os.getpid(), signal.SIGTERM)
        monkeypatch.setattr(run_mod, "_run_entry", _entry_stub([], first))
        with pytest.raises(rg.RunInterrupted):
            _guarded(tmp_path, f)
        assert state["fired"] and f.loaded == BEFORE
        assert not _save(tmp_path).exists() and not (tmp_path / "lock").exists()
        report = json.loads((tmp_path / "out" / "run-guard-quick.json").read_text())
        assert report["engine"].startswith("engine restored") and "SIGTERM" in report["signals_ignored"]
        log = json.loads(next((tmp_path / "out").rglob("run-all.json")).read_text())
        assert "finished_at" in log and log["engine_restored"].startswith("engine restored")
    finally:
        f.close()


def test_p7_queued_unloads_and_unfinished_loads_are_waited_for(tmp_path, key, quiet, monkeypatch):
    """oMLX answers 202 to an unload and finishes later, and a load shows is_loading first: the model runs
    with no other chat model left, and the restore reports success only once the engine matches."""
    f = FakeEngine(loaded=("model-a", "model-b", "helper-embed"), pinned=("model-a",), unload_lag=0.3, load_lag=0.3)
    seen = []
    try:
        monkeypatch.setattr(run_mod, "_run_entry", _entry_stub([], lambda: seen.append(set(f.loaded) | f.loading)))
        log = _guarded(tmp_path, f)
        assert seen == [{"model-c", "helper-embed"}]
        assert f.loaded == BEFORE and f.loading == set() and log["engine_restored"].startswith("engine restored")
        time.sleep(0.5)                                 # nothing queued lands afterwards
        assert f.loaded == BEFORE
    finally:
        f.close()


def test_p8_a_failed_restore_or_a_changed_pin_fails_the_run_and_keeps_the_save(tmp_path, key, quiet, monkeypatch):
    f = FakeEngine(loaded=("model-a", "model-b", "helper-embed"), pinned=("model-a",))
    try:
        f.fail_loads = True                             # the model cannot be loaded, nor the user's put back
        monkeypatch.setattr(run_mod, "_run_entry", _entry_stub([]))
        log = _guarded(tmp_path, f)
        assert log["entries"][0]["result"] == "not started" and "500" in log["failed_because"]
        assert log["outcome"] == "failed" and log["failed"] is True and log["exit_code"] == 1
        assert log["engine_restored"].startswith("restore FAILED") and log["restore_failed"]
        assert _save(tmp_path).exists() and "run_guard restore" in log["restore_with"]
        assert not (tmp_path / "lock").exists()
        f.fail_loads = False
        assert rg.restore_cli(_save(tmp_path), f.url, str(tmp_path / "restore.lock"), harbor=lambda: None) == 0
        assert f.loaded == BEFORE

        monkeypatch.setattr(run_mod, "_run_entry", _entry_stub([], lambda: f.pinned.discard("model-a")))
        log = _guarded(tmp_path, f)
        assert log["outcome"] == "failed" and "pinned" in log["engine_restored"] and _save(tmp_path).exists()
    finally:
        f.close()


def _sitting_stub(ok, excluded, other):
    def run_entry(e, out, **kw):
        g = kw.get("guard")
        for note, n in (("ok", ok), ("excluded", excluded), ("ok", other)):     # as _run_entry notes them
            for _ in range(n):
                if g is not None:
                    g.note(note)
        return {"skipped": None, "stopped": None, "written": ok + excluded + other,
                "sitting": {"ok": ok, "excluded": excluded, "other": other},
                "summary": {"ceiling": 1, "solid": 1, "excluded": 7}}   # old records' faults: not this sitting's
    return run_entry


@pytest.mark.parametrize("with_lock", [False, True])
@pytest.mark.parametrize("ok, excluded, other, failed", [
    (0, 0, 0, False),        # a resume with every trial already recorded
    (2, 0, 0, False),        # a resume: two new graded trials (the old faults are not this sitting's)
    (1, 2, 0, False),
    (0, 3, 0, True),         # only engine faults this sitting
    (0, 0, 3, True),         # trials that were not graded
])
def test_p9_a_failed_run_is_judged_by_this_sittings_trials(tmp_path, quiet, monkeypatch, with_lock, ok, excluded,
                                                             other, failed):
    monkeypatch.setattr(run_mod, "_run_entry", _sitting_stub(ok, excluded, other))
    log = run_all(tmp_path / "out", plan="quick", client_kind="oracle", progress=None, models=[_model()],
                  lock=str(tmp_path / "lock") if with_lock else None)
    assert log["failed"] is failed and log["outcome"] == ("failed" if failed else "finished")


def _skipped(e, out, **kw):
    return {"skipped": "the engine does not list it", "stopped": None, "written": 0,
            "sitting": {"ok": 0, "excluded": 0, "other": 0}, "summary": {"ceiling": 0, "solid": 0, "excluded": 0}}


@pytest.mark.parametrize("how", ["no lock", "lock", "engine"])
def test_b_a_sitting_in_which_every_entry_was_skipped_fails(tmp_path, fake, key, quiet, monkeypatch, how):
    monkeypatch.setattr(run_mod, "_run_entry", _skipped)
    kw = {"client_kind": "omlx", "base_url": fake.url} if how == "engine" else {"client_kind": "oracle"}
    log = run_all(tmp_path / "out", plan="quick", progress=None, models=[_model(), _model("model-b")],
                  lock=None if how == "no lock" else str(tmp_path / "lock"), **kw)
    assert [r["result"] for r in log["entries"]] == ["skipped", "skipped"]
    assert log["failed"] is True and log["outcome"] == "failed" and "skipped" in log["why_failed"]
    if how == "engine":
        assert log["exit_code"] == 1 and fake.loaded == BEFORE


def test_f_the_save_is_one_host_wide_path_whatever_the_lock(tmp_path, fake, key, quiet, monkeypatch):
    """A save left by a sitting on another lock path is still seen; a lock inside the repository is refused."""
    monkeypatch.setattr(run_mod, "_run_entry", _entry_stub([]))
    _save(tmp_path).write_text(json.dumps({"token": "an-earlier-sitting", "base_url": fake.url, "loaded": []}))
    with pytest.raises(rg.GuardError, match="never restored"):
        run_all(tmp_path / "out", plan="quick", client_kind="omlx", progress=None, models=[_model()],
                base_url=fake.url, lock=str(tmp_path / "another.lock"))
    assert fake.requests == [] and not (tmp_path / "another.lock").exists()
    repo = Path(run_mod.__file__).resolve().parents[3]
    with pytest.raises(RuntimeError, match="lock goes outside the repository"):
        run_all(tmp_path / "out", plan="quick", client_kind="oracle", progress=None, models=[_model()],
                lock=str(repo / "a-lock-test"))
    assert not (repo / "a-lock-test").exists()


def test_a_sandbox_failure_after_the_start_is_a_failed_run_not_a_refusal(tmp_path, fake, key, quiet, monkeypatch,
                                                                         capsys):
    from edat.sidekick.sandbox.run import SandboxUnavailable

    def broken(e, out, **kw):
        raise SandboxUnavailable("the sealed sandbox failed its self-test")
    monkeypatch.setattr(run_mod, "_run_entry", broken)
    monkeypatch.setattr(run_mod, "model_dir_from_settings", lambda: tmp_path)
    monkeypatch.setattr(run_mod, "scan", lambda mdir: ([_model()], []))
    code = run_mod.run_all_main(["--plan", "quick", "--out", str(tmp_path / "out"), "--base-url", fake.url,
                                 "--lock", str(tmp_path / "lock")])
    err = capsys.readouterr().err
    assert code == 1 and "not started" not in err and "FAILED: SandboxUnavailable" in err
    assert "engine: engine restored" in err and fake.loaded == BEFORE and _posts(fake)
    entry = _model().entries()[0]
    with pytest.raises(SandboxUnavailable) as exc:
        run_mod.run_entry(entry, tmp_path / "one", plan="quick", base_url=fake.url, weights_sha256="0" * 64,
                          progress=None, lock=str(tmp_path / "lock"))
    assert exc.value.run_log["outcome"] == "failed" and fake.loaded == BEFORE


def test_p10_a_run_off_the_main_thread_fails_and_leaves_no_lock(tmp_path, quiet, monkeypatch):
    monkeypatch.setattr(run_mod, "_run_entry", _entry_stub([]))
    lock, err = tmp_path / "lock", {}

    def go():
        try:
            run_all(tmp_path / "out", plan="quick", client_kind="oracle", progress=None, models=[_model()],
                    lock=str(lock))
        except BaseException as exc:  # noqa: BLE001
            err["e"] = exc
    th = threading.Thread(target=go)
    th.start()
    th.join()
    assert isinstance(err.get("e"), ValueError) and not lock.exists()


def test_p11_another_spelling_of_the_repository_is_refused(tmp_path, quiet):
    repo = Path(run_mod.__file__).resolve().parents[3]
    alt = Path(str(repo)[:-1] + str(repo)[-1].swapcase())
    (tmp_path / "link").symlink_to(repo)
    for p in (repo / "results", alt / "results", tmp_path / "link" / "results"):
        assert run_mod.inside_repo(p), p
    assert not run_mod.inside_repo(tmp_path / "results")
    with pytest.raises(RuntimeError, match="outside the repository"):
        run_all(alt / "results", plan="quick", client_kind="oracle", progress=None, models=[_model()],
                lock=str(tmp_path / "lock"))
    assert not (repo / "results").exists() and not (tmp_path / "lock").exists()


def test_run_all_main_exit_codes(tmp_path, monkeypatch):
    for outcome, code in (("finished", 0), ("stopped", 3), ("failed", 1)):
        monkeypatch.setattr(run_mod, "run_all", lambda *a, **k: {"entries": [], "outcome": outcome})
        assert run_mod.run_all_main(["--plan", "smoke", "--out", str(tmp_path)]) == code

    def raises(exc):
        def f(*a, **k):
            raise exc
        return f
    monkeypatch.setattr(run_mod, "run_all", raises(rg.RunInterrupted(signal.SIGTERM)))
    assert run_mod.run_all_main(["--plan", "smoke", "--out", str(tmp_path)]) == 1
    monkeypatch.setattr(run_mod, "run_all", raises(rg.GuardError("a lock exists")))
    assert run_mod.run_all_main(["--plan", "smoke", "--out", str(tmp_path)]) == 2


def test_c_one_entry_with_the_engine_goes_through_the_guard(tmp_path, fake, key, quiet, monkeypatch):
    seen = []
    monkeypatch.setattr(run_mod, "_run_entry", _entry_stub([], lambda: seen.append(set(fake.loaded))))
    entry = _model().entries()[0]

    def one(**kw):
        return run_mod.run_entry(entry, tmp_path / "out", plan="quick", base_url=fake.url, weights_sha256="0" * 64,
                                 progress=None, **kw)
    with pytest.raises(rg.GuardError, match="lock"):
        one()
    monkeypatch.setattr(run_mod, "harbor_running", lambda: "process matching 'Harbor'")
    with pytest.raises(rg.GuardError, match="Harbor"):
        one(lock=str(tmp_path / "lock"))
    assert fake.requests == [] and seen == []
    monkeypatch.setattr(run_mod, "harbor_running", lambda: None)
    st = one(lock=str(tmp_path / "lock"))
    assert seen == [{"model-c", "helper-embed"}] and fake.loaded == BEFORE and fake.pinned == {"model-a"}
    assert st["outcome"] == "finished" and st["exit_code"] == 0 and st["engine_restored"].startswith("engine restored")
    assert not _save(tmp_path).exists() and not (tmp_path / "lock").exists()
    assert (tmp_path / "out" / "run-guard-entry.json").exists()


def test_preflight_says_which_models_the_run_unloads_and_which_are_pinned(tmp_path, fake, key, quiet, monkeypatch):
    monkeypatch.setattr(run_mod, "require_sandbox", lambda: type("R", (), {"passed": True, "probes": []})())
    monkeypatch.setattr(run_mod, "model_dir_from_settings", lambda: None)
    rows = {n: d for n, _, d in run_mod.preflight(tmp_path / "out", engine=True, base_url=fake.url)}
    assert rows["engine: the run will unload (and load back when it ends)"] == "model-a, model-b"
    pinned = next(n for n in rows if n.startswith("engine: pinned"))
    assert rows[pinned] == "model-a" and "failed restore" in pinned
    assert _posts(fake) == []                           # preflight only reads


def test_e_after_a_signal_the_message_says_what_the_restore_did(tmp_path, monkeypatch, capsys):
    report = tmp_path / "run-guard-smoke.json"

    def interrupted(report_body):
        def run_all(*a, **k):
            report.write_text(json.dumps(report_body))
            raise rg.RunInterrupted(signal.SIGTERM)
        return run_all
    monkeypatch.setattr(run_mod, "run_all", interrupted({
        "engine": "restore FAILED: GuardError: Harbor appears to be running (process matching 'Harbor')",
        "restore_with": "close Harbor, then: python -m edat.runtime.run_guard restore --save S --lock L"}))
    assert run_mod.run_all_main(["--plan", "smoke", "--out", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "put back" not in err and "restore FAILED" in err and "run_guard restore --save S" in err
    monkeypatch.setattr(run_mod, "run_all", interrupted({"engine": "engine restored (model-a)"}))
    assert run_mod.run_all_main(["--plan", "smoke", "--out", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "engine: engine restored (model-a)" in err and "restore the engine with" not in err


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
    monkeypatch.setattr(run_mod, "harbor_running", lambda: None)
    rows = run_mod.preflight(out, weights=True)
    assert not out.exists()
    assert ("results folder outside the repository", False) in [(n, ok) for n, ok, _ in rows]
