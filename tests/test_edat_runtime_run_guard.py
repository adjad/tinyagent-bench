"""The shared run guard (edat/runtime/run_guard.py) against a fake engine.

No real engine, model, port or Harbor: oMLX's model API is an httpx
MockTransport over an in-memory state, the Harbor and memory probes are
functions, and the lock and save live in tmp_path. Each exit path (normal end,
stop, exception, Ctrl-C, SIGTERM, SIGHUP, SIGQUIT, restore failure) is checked
for the same three things: the engine is back as it was, the report is
written, and the lock is gone.
"""
import json
import os
import signal
import subprocess

import httpx
import pytest

from edat.runtime import run_guard as rg

URL = "http://127.0.0.1:8000"


class FakeEngine:
    """oMLX's /v1/models/status, /health, unload and load, in memory."""

    def __init__(self, loaded=("user-model",), pinned=("user-pinned",), models=("gemma-a", "gemma-b"),
                 helpers=("bge-embed",), default="user-model"):
        ids = {*loaded, *pinned, *models, *helpers}
        self.models = {m: {"id": m, "loaded": m in loaded or m in pinned or m in helpers, "pinned": m in pinned,
                           "is_helper": m in helpers} for m in ids}
        self.default = default
        self.calls: list[str] = []
        self.headers: list[dict] = []
        self.unload_202 = 0            # the next N unloads answer 202 and finish after one status read
        self.pending: set[str] = set()
        self.fail = None               # (method, path-part) answered with 500
        self.on_call = None            # hook(method, path) before answering

    @property
    def transport(self):
        return httpx.MockTransport(self.handle)

    def loaded(self):
        return sorted(m for m, r in self.models.items() if r["loaded"] and not r["is_helper"])

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append(f"{method} {path}")
        self.headers.append(dict(request.headers))
        if self.on_call:
            self.on_call(method, path)
        if self.fail and self.fail[0] == method and self.fail[1] in path:
            return httpx.Response(500, json={"detail": "boom"})
        if path == "/v1/models/status":
            for m in list(self.pending):
                self.models[m]["loaded"] = False
                self.pending.discard(m)
            return httpx.Response(200, json={"models": [dict(r) for r in self.models.values()]})
        if path == "/health":
            return httpx.Response(200, json={"status": "healthy", "default_model": self.default})
        mid = path.split("/")[3]
        if mid not in self.models:
            return httpx.Response(404, json={"detail": "not found"})
        if path.endswith("/unload"):
            if not self.models[mid]["loaded"]:
                return httpx.Response(400, json={"detail": "not loaded"})
            if self.unload_202:
                self.unload_202 -= 1
                self.pending.add(mid)
                return httpx.Response(202, json={"status": "unloading"})
            self.models[mid]["loaded"] = False
            return httpx.Response(200, json={"status": "ok"})
        if path.endswith("/load"):
            self.models[mid]["loaded"] = True
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)


@pytest.fixture
def env(tmp_path):
    fake = FakeEngine()
    state = {"harbor": None, "free": 80}

    def guard(**kw):
        args = dict(lock=str(tmp_path / "heavy.lock"),
                    engine=rg.Engine(URL, api_key="k-test", transport=fake.transport, sleep=lambda s: None),
                    harbor=lambda: state["harbor"], free_pct=lambda: state["free"])
        args.update(kw)
        return rg.RunGuard(tmp_path / "out", **args)
    return fake, guard, tmp_path, state


def report(tmp_path):
    return json.loads((tmp_path / "out" / "run-report.json").read_text())


def back_as_before(fake, tmp_path):
    assert fake.loaded() == ["user-model", "user-pinned"]
    assert not (tmp_path / "heavy.lock").exists()
    assert not (tmp_path / "heavy.lock.engine-save.json").exists()      # removed once restored
    before = report(tmp_path)["engine_before"]
    assert before["loaded"] == ["user-model", "user-pinned"]
    assert before["pinned"] == ["user-pinned"] and before["default_model"] == "user-model"


# ------------------------------------------------------------------ exit paths

def test_normal_end_runs_one_model_and_restores_loaded_and_pinned(env):
    fake, guard, tmp_path, _ = env
    with guard() as g:
        g.prepare("gemma-a")
        assert fake.loaded() == ["gemma-a"]                 # one chat model; the helper stays
        assert fake.models["bge-embed"]["loaded"]
        g.prepare("gemma-b")
        assert fake.loaded() == ["gemma-b"]
        g.note("ok")
    assert g.outcome == "finished" and g.exit_code == 0
    back_as_before(fake, tmp_path)
    r = report(tmp_path)
    assert r["outcome"] == "finished" and r["engine"].startswith("engine restored")
    assert r["engine_before"]["pinned"] == ["user-pinned"]


@pytest.mark.parametrize("raise_it", [RuntimeError("boom"), KeyboardInterrupt()])
def test_exception_and_ctrl_c_restore_and_report(env, raise_it):
    fake, guard, tmp_path, _ = env
    with pytest.raises(type(raise_it)):
        with guard() as g:
            g.prepare("gemma-a")
            raise raise_it
    back_as_before(fake, tmp_path)
    r = report(tmp_path)
    assert r["outcome"] == "failed" and r["failed"].startswith(type(raise_it).__name__)


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT])
def test_signals_restore_report_and_release(env, sig):
    fake, guard, tmp_path, _ = env
    before = signal.getsignal(sig)
    with pytest.raises(rg.RunInterrupted) as ei:
        with guard() as g:
            g.prepare("gemma-a")
            os.kill(os.getpid(), sig)
            pytest.fail("the signal did not interrupt the run")
    assert ei.value.signum == sig
    back_as_before(fake, tmp_path)
    assert report(tmp_path)["outcome"] == "failed"
    assert signal.getsignal(sig) == before                  # handlers put back


def test_signal_from_a_terminal_closing_in_a_real_process(tmp_path):
    """The reviewer's case: caffeinate in a terminal gets SIGHUP when the terminal closes."""
    script = tmp_path / "child.py"
    script.write_text(f"""
import os, signal, sys, time
sys.path.insert(0, {str(rg.REPO)!r})
from tests.test_edat_runtime_run_guard import FakeEngine
from edat.runtime import run_guard as rg
fake = FakeEngine()
g = rg.RunGuard({str(tmp_path / 'out')!r}, lock={str(tmp_path / 'heavy.lock')!r},
                engine=rg.Engine({URL!r}, transport=fake.transport), harbor=lambda: None, free_pct=lambda: 80)
try:
    with g:
        g.prepare("gemma-a")
        print("ready", flush=True)
        time.sleep(30)
except rg.RunInterrupted:
    print("loaded", ",".join(fake.loaded()), flush=True)
    sys.exit(129)
""")
    p = subprocess.Popen([os.sys.executable, str(script)], stdout=subprocess.PIPE, text=True, cwd=rg.REPO)
    assert p.stdout.readline().strip() == "ready"
    p.send_signal(signal.SIGHUP)
    out, _ = p.communicate(timeout=30)
    assert p.returncode == 129 and "loaded user-model,user-pinned" in out
    assert not (tmp_path / "heavy.lock").exists()
    assert json.loads((tmp_path / "out" / "run-report.json").read_text())["outcome"] == "failed"


def test_signals_are_ignored_while_restoring(env):
    fake, guard, tmp_path, _ = env
    sent = []

    def hook(method, path):
        if method == "POST" and path.endswith("/load") and "user" in path and not sent:
            sent.append(path)
            os.kill(os.getpid(), signal.SIGTERM)             # arrives mid-restore
    with guard() as g:
        g.prepare("gemma-a")
        fake.on_call = hook
    assert sent
    back_as_before(fake, tmp_path)


def test_restore_failure_is_recorded_reraised_and_still_reported(env):
    fake, guard, tmp_path, _ = env
    with pytest.raises(httpx.HTTPStatusError):
        with guard() as g:
            g.prepare("gemma-a")
            fake.fail = ("POST", "/load")
    r = report(tmp_path)
    assert r["outcome"] == "failed" and r["engine"].startswith("restore FAILED")
    assert "run_guard restore" in r["restore_with"]
    assert not (tmp_path / "heavy.lock").exists()
    save = json.loads((tmp_path / "heavy.lock.engine-save.json").read_text())
    assert save["loaded"] == ["user-model", "user-pinned"]
    # the leftover save refuses the next start and says how to restore
    fake.fail = None
    with pytest.raises(rg.GuardError, match="never restored.*run_guard restore"):
        guard().__enter__()
    assert not (tmp_path / "heavy.lock").exists()
    assert rg.restore(rg.Engine(URL, transport=fake.transport), tmp_path / "heavy.lock.engine-save.json",
                      harbor=lambda: None).startswith("engine restored")
    assert fake.loaded() == ["user-model", "user-pinned"]
    assert not (tmp_path / "heavy.lock.engine-save.json").exists()
    with guard():
        pass                                               # a restored save allows the next start


def test_restore_failure_under_another_exception_keeps_the_first(env):
    fake, guard, tmp_path, _ = env
    with pytest.raises(ValueError):
        with guard() as g:
            g.prepare("gemma-a")
            fake.fail = ("POST", "/load")
            raise ValueError("first")
    assert report(tmp_path)["engine"].startswith("restore FAILED")


def test_each_start_saves_fresh_state(env):
    """The reviewer's case: a second run restored the first run's saved state."""
    fake, guard, tmp_path, _ = env
    with guard() as g:
        g.prepare("gemma-a")
    first = report(tmp_path)["engine_before"]
    fake.models["user-model"]["loaded"] = False             # the user changes what is loaded between runs
    fake.models["gemma-b"]["loaded"] = True
    with guard() as g:
        g.prepare("gemma-a")
    second = report(tmp_path)["engine_before"]
    assert second["token"] != first["token"] and second["loaded"] == ["gemma-b", "user-pinned"]
    assert fake.loaded() == ["gemma-b", "user-pinned"]


def test_unload_waits_for_a_queued_unload(env):
    fake, guard, tmp_path, _ = env
    fake.unload_202 = 2
    with guard() as g:
        g.prepare("gemma-a")
        assert fake.loaded() == ["gemma-a"]
    back_as_before(fake, tmp_path)


# ------------------------------------------------------------------ lock and folder

def test_lock_rules(env, tmp_path):
    fake, guard, _, _ = env
    for bad in ("", "   ", None):
        with pytest.raises(rg.GuardError, match="lock path is required"):
            guard(lock=bad)
    (tmp_path / "heavy.lock").mkdir()
    with pytest.raises(rg.GuardError, match="exists"):
        guard().__enter__()
    assert (tmp_path / "heavy.lock").is_dir()                # someone else's lock stays
    assert fake.calls == []                                  # nothing touched


def test_output_inside_the_repository_writes_nothing(tmp_path):
    target = rg.REPO / "results-here"
    with pytest.raises(rg.GuardError, match="inside the repository"):
        rg.RunGuard(target, lock=str(tmp_path / "l"), engine=None)
    assert not target.exists() and not (tmp_path / "l").exists()


# ------------------------------------------------------------------ Harbor

def test_harbor_refuses_start_and_every_unload_load_and_restore(env):
    fake, guard, tmp_path, state = env
    state["harbor"] = "process matching x"
    with pytest.raises(rg.GuardError, match="Harbor"):
        guard().__enter__()
    assert fake.calls == [] and not (tmp_path / "heavy.lock").exists()
    state["harbor"] = None
    with pytest.raises(rg.GuardError, match="not unloading"):
        with guard() as g:
            state["harbor"] = "a process listening on port 8765"
            g.prepare("gemma-a")
    assert "POST /v1/models/user-model/unload" not in fake.calls
    r = report(tmp_path)                                     # restore refused too: nothing touched, said so
    assert r["engine"].startswith("restore FAILED") and "Harbor" in r["engine"]
    assert fake.loaded() == ["user-model", "user-pinned"]


def test_harbor_opening_before_the_second_model_stops_before_any_unload(env):
    fake, guard, tmp_path, state = env
    with pytest.raises(rg.GuardError):
        with guard() as g:
            g.prepare("gemma-a")
            state["harbor"] = "process matching '/MacOS/(Harbor|Wisp)( |$)'"
            g.prepare("gemma-b")
    assert fake.loaded() == ["gemma-a"]                      # left for the user: restore refused while Harbor runs


class Proc:
    def __init__(self, rc=1, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_harbor_detection_covers_app_swift_run_and_backend_and_fails_closed():
    def runner(answers):
        def run(argv):
            key = argv[2] if argv[0] == "pgrep" else argv[2]
            a = answers.get(key, Proc())
            if isinstance(a, Exception):
                raise a
            return a
        return run
    assert rg.harbor_running(runner({})) is None
    pats = rg.HARBOR_PROCESS_PATTERNS
    import re
    assert re.search(pats[0], "/Applications/Harbor.app/Contents/MacOS/Harbor")
    assert re.search(pats[1], "/Users/x/src/app/.build/arm64-apple-macosx/debug/Harbor")
    assert re.search(pats[2], "python -m service.main --port 8765") or re.search(pats[2], "uvicorn service.app:app")
    assert not any(re.search(p, "python -m edat.cases.agentic_run run") for p in pats)
    assert "process" in rg.harbor_running(runner({pats[1]: Proc(0, "4242\n")}))
    assert "8765" in rg.harbor_running(runner({"-iTCP:8765": Proc(0, "4242\n")}))
    assert "unknown" in rg.harbor_running(runner({pats[0]: Proc(2, "", "pgrep: bad")}))
    assert "unknown" in rg.harbor_running(runner({pats[0]: OSError("no pgrep")}))
    assert "unknown" in rg.harbor_running(runner({"-iTCP:8791": Proc(1, "", "lsof: permission denied")}))


# ------------------------------------------------------------------ engine URL and key

def test_engine_url_is_loopback_before_any_header(monkeypatch):
    for bad in ("http://10.0.0.2:8000", "https://127.0.0.1:8000", "http://127.0.0.1", "http://example.com:8000",
                "http://127.0.0.1:8000/v1", "http://user:pw@127.0.0.1:8000", ""):
        with pytest.raises(rg.GuardError):
            rg.Engine(bad, api_key="k-test")
    assert rg.loopback_base_url("http://localhost:8001/") == "http://localhost:8001"


def test_proxies_ignored_and_key_only_in_headers(env, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.invalid:3128")
    fake, guard, tmp_path, _ = env
    e = rg.Engine(URL, api_key="k-test", transport=fake.transport)
    assert e._client._trust_env is False and not e._client._mounts
    e.status()
    assert fake.headers[-1]["authorization"] == "Bearer k-test"
    assert "k-test" not in repr(e)
    with guard() as g:
        g.prepare("gemma-a")
    assert all("k-test" not in v for v in os.environ.values())
    for f in tmp_path.rglob("*.json"):
        assert "k-test" not in f.read_text()
    real = rg.Engine(URL, api_key="k-test")                  # no transport given: still no proxy
    assert real._client._trust_env is False and not real._client._mounts


def test_read_engine_key(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"auth": {"api_key": "k-test"}}))
    assert rg.read_engine_key(s) == "k-test"
    assert rg.read_engine_key(tmp_path / "missing.json") is None


# ------------------------------------------------------------------ stops and outcomes

def test_stops_and_outcomes(env):
    fake, guard, tmp_path, state = env
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "STOP").write_text("")
    with guard() as g:
        assert g.should_stop() == "the STOP file exists"
    assert g.outcome == "stopped" and g.exit_code == 3
    (tmp_path / "out" / "STOP").unlink()
    with guard(max_minutes=1e-9) as g:
        assert "minute limit" in g.should_stop()
    assert g.outcome == "stopped"
    state["free"] = 9
    with guard() as g:
        assert "free memory" in g.should_stop()
    assert g.outcome == "failed"
    state["free"] = 80
    with guard() as g:
        for _ in range(49):
            g.note("ok")
        g.note("excluded")                                  # 1 of 50: exactly 2%, not over
        assert g.should_stop() is None
        g.note("excluded")                                  # 2 of 51
        assert "engine faults" in g.should_stop()
    assert g.outcome == "failed"


@pytest.mark.parametrize("n", [1, 2, 7])
def test_every_trial_excluded_is_a_failure_at_any_count(env, n):
    fake, guard, tmp_path, _ = env
    with guard() as g:
        for _ in range(n):
            g.note("excluded")
        g.note("not_applicable")
    assert g.outcome == "failed" and "every model trial" in report(tmp_path)["why"]


def test_not_applicable_only_is_finished(env):
    fake, guard, tmp_path, _ = env
    with guard() as g:
        g.note("not_applicable")
    assert g.outcome == "finished"


def test_restore_cli_refuses_another_runs_save(env, tmp_path):
    fake, guard, _, _ = env
    with pytest.raises(httpx.HTTPStatusError):
        with guard() as g:
            g.prepare("gemma-a")
            fake.fail = ("POST", "/load")
    fake.fail = None
    save = tmp_path / "heavy.lock.engine-save.json"
    with pytest.raises(rg.GuardError, match="token"):
        rg.restore(rg.Engine(URL, transport=fake.transport), save, harbor=lambda: None, token="other")
    with pytest.raises(rg.GuardError, match="saved from"):
        rg.restore(rg.Engine("http://127.0.0.1:9000", transport=fake.transport), save, harbor=lambda: None)
