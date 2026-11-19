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
        self.loading: dict[str, int] = {}   # model -> status reads it still shows is_loading, then loaded

    def start_loading(self, mid, reads=3):
        """A load whose request was cut off: oMLX goes on loading (loaded False, is_loading True)."""
        self.models[mid].update(loaded=False, is_loading=True)
        self.loading[mid] = reads

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
            for m, n in list(self.loading.items()):
                if n <= 0:
                    self.models[m].update(loaded=True, is_loading=False)
                    del self.loading[m]
                else:
                    self.loading[m] = n - 1
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
            if not self.models[mid]["loaded"]:          # oMLX: no engine yet, also while still loading
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
                "http://127.0.0.1:8000/v1", "http://user:pw@127.0.0.1:8000", "",
                "http://localhost:8000", "http://127.0.0.1:0"):       # a name, or port 0: refused
        with pytest.raises(rg.GuardError):
            rg.Engine(bad, api_key="k-test")
    assert rg.loopback_base_url("http://127.0.0.1:8001/") == "http://127.0.0.1:8001"
    assert rg.loopback_base_url("http://[::1]:8001") == "http://[::1]:8001"


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


# ------------------------------------------------------------------ the fix check's probes (second round)
# Each test is one of the reviewer's probes, kept as a regression test.

def test_probe_a_second_signal_during_exit_still_restores(env, monkeypatch):
    """A second SIGHUP while the exit switches handlers (closing a terminal can send two) used to escape
    __exit__: no restore, no report, lock and save left."""
    fake, guard, tmp_path, _ = env
    old = signal.signal(signal.SIGHUP, lambda *a: None)        # whatever runs after the guard is harmless here
    real = signal.signal
    fired = []

    def wrapped(s, h):
        r = real(s, h)
        if s == signal.SIGTERM and not fired:
            fired.append(1)
            os.kill(os.getpid(), signal.SIGHUP)
        return r
    try:
        with pytest.raises(rg.RunInterrupted) as ei:
            with guard() as g:
                g.prepare("gemma-a")
                monkeypatch.setattr(rg.signal, "signal", wrapped)
                os.kill(os.getpid(), signal.SIGTERM)
                for _ in range(10**6):
                    pass
    finally:
        monkeypatch.setattr(rg.signal, "signal", real)
        real(signal.SIGHUP, old)
    assert ei.value.signum == signal.SIGTERM and fired
    back_as_before(fake, tmp_path)
    assert report(tmp_path)["engine"].startswith("engine restored")


def test_two_quick_sighups_and_signals_during_restore_are_recorded(env):
    fake, guard, tmp_path, _ = env
    sent = []

    def during_restore(method, path):
        if method == "POST" and path.endswith("/unload") and not sent:
            sent.append(1)
            for s in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
                os.kill(os.getpid(), s)
    with pytest.raises(rg.RunInterrupted) as ei:
        with guard() as g:
            g.prepare("gemma-a")
            fake.on_call = during_restore
            try:
                os.kill(os.getpid(), signal.SIGHUP)
                for _ in range(10**6):
                    pass
            except rg.RunInterrupted as first:
                os.kill(os.getpid(), signal.SIGHUP)            # a second within 10 s: recorded, not raised
                for _ in range(10**5):
                    pass
                raise first
    assert ei.value.signum == signal.SIGHUP
    back_as_before(fake, tmp_path)
    assert report(tmp_path)["signals_ignored"].count("SIGHUP") >= 2
    assert {"SIGTERM", "SIGINT"} <= set(report(tmp_path)["signals_ignored"])
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_probe_c_a_load_cut_off_mid_request_is_unloaded_on_restore(env):
    """The run is cut while oMLX is still loading gemma-b: the restore waits for it and unloads it."""
    fake, guard, tmp_path, _ = env
    with guard() as g:
        g.prepare("gemma-a")
        fake.models["gemma-a"]["loaded"] = False
        fake.start_loading("gemma-b", reads=3)
    assert not fake.models["gemma-b"]["loaded"] and not fake.models["gemma-b"].get("is_loading")
    back_as_before(fake, tmp_path)


def test_a_load_that_never_settles_fails_the_restore_and_keeps_the_save(env):
    fake, guard, tmp_path, _ = env
    e = rg.Engine(URL, api_key="k-test", transport=fake.transport, sleep=lambda s: None, settle_wait_s=0)
    with pytest.raises(rg.GuardError, match="still loading"):
        with guard(engine=e) as g:
            g.prepare("gemma-a")
            fake.start_loading("gemma-b", reads=10**6)
    assert "restore FAILED" in report(tmp_path)["engine"] and report(tmp_path)["outcome"] == "failed"
    assert (tmp_path / "heavy.lock.engine-save.json").exists()
    assert not (tmp_path / "heavy.lock").exists()


def test_a_model_still_loading_at_start_is_not_saved_as_the_users(env):
    fake, guard, tmp_path, _ = env
    fake.start_loading("gemma-b", reads=2)                    # e.g. a load the user started a moment ago
    with guard() as g:
        assert "gemma-b" in g.report["engine_before"]["loaded"]
        g.prepare("gemma-a")
    assert fake.models["gemma-b"]["loaded"]                   # theirs: loaded again on restore


def test_probe_d_an_evicted_helper_is_loaded_again(env):
    fake, guard, tmp_path, _ = env
    with guard() as g:
        g.prepare("gemma-a")
        fake.models["bge-embed"]["loaded"] = False
    assert fake.models["bge-embed"]["loaded"]
    back_as_before(fake, tmp_path)
    assert report(tmp_path)["engine_before"]["helpers_loaded"] == ["bge-embed"]


def test_a_helper_that_cannot_be_loaded_keeps_the_save(env):
    fake, guard, tmp_path, _ = env
    with pytest.raises(Exception):
        with guard() as g:
            g.prepare("gemma-a")
            fake.models["bge-embed"]["loaded"] = False
            fake.fail = ("POST", "bge-embed/load")
    assert "restore FAILED" in report(tmp_path)["engine"]
    assert (tmp_path / "heavy.lock.engine-save.json").exists()


def test_probe_e_a_changed_default_model_keeps_the_save_until_put_back(env):
    fake, guard, tmp_path, _ = env
    with pytest.raises(rg.GuardError, match="default model"):
        with guard() as g:
            g.prepare("gemma-a")
            fake.default = "gemma-a"
    rep = report(tmp_path)
    assert rep["outcome"] == "failed" and "default model is 'gemma-a', was 'user-model'" in rep["engine"]
    save = tmp_path / "heavy.lock.engine-save.json"
    assert save.exists()
    fake.default = "user-model"                               # the person sets it back in oMLX
    e = rg.Engine(URL, transport=fake.transport, sleep=lambda s: None)
    assert rg.restore_cli(save, URL, str(tmp_path / "heavy.lock"), engine=e, harbor=lambda: None) == 0
    assert not save.exists() and not (tmp_path / "heavy.lock").exists()


def test_pinned_set_is_compared_after_restore(env):
    fake, guard, tmp_path, _ = env
    with pytest.raises(rg.GuardError, match="pinned"):
        with guard() as g:
            g.prepare("gemma-a")
            fake.models["gemma-b"]["pinned"] = True
    assert (tmp_path / "heavy.lock.engine-save.json").exists()


def test_probe_f_no_save_is_left_when_the_folder_cannot_be_made(tmp_path):
    fake = FakeEngine()
    (tmp_path / "blocker").write_text("a file, not a folder")
    g = rg.RunGuard(tmp_path / "blocker" / "out", lock=str(tmp_path / "heavy.lock"),
                    engine=rg.Engine(URL, transport=fake.transport), harbor=lambda: None, free_pct=lambda: 80)
    with pytest.raises(OSError):
        with g:
            pass
    assert not (tmp_path / "heavy.lock").exists()
    assert not (tmp_path / "heavy.lock.engine-save.json").exists()
    assert not any(c.startswith("POST") for c in fake.calls)


def test_probe_g_unreadable_memory_fails_closed_after_one_retry(env):
    fake, guard, tmp_path, state = env
    reads = iter([None, 80])
    with guard(free_pct=lambda: next(reads)) as g:
        assert g.should_stop() is None                        # one bad read is retried
    with guard(free_pct=lambda: None) as g:
        assert g.should_stop() == "free memory could not be read"
    assert report(tmp_path)["outcome"] == "failed"


def test_raised_trials_count_toward_the_all_failed_outcome(env):
    fake, guard, tmp_path, _ = env
    with guard() as g:
        g.prepare("gemma-a")
        g.note("error")
        g.note("error")
        g.note("not_applicable")
    rep = report(tmp_path)
    assert rep["outcome"] == "failed" and rep["trials_raised"] == 2 and rep["trials_recorded"] == 1
    with guard() as g:
        g.note("ok")
        g.note("error")
    assert report(tmp_path)["outcome"] == "finished"


def test_a_lock_that_cannot_be_removed_still_restores_handlers(env, monkeypatch):
    fake, guard, tmp_path, _ = env
    before = {s: signal.getsignal(s) for s in (*rg.GUARDED_SIGNALS, signal.SIGINT)}

    def rmdir(path):
        raise PermissionError("cannot remove")
    with pytest.raises(PermissionError):
        with guard() as g:
            g.prepare("gemma-a")
            monkeypatch.setattr(rg.os, "rmdir", rmdir)
    monkeypatch.undo()
    assert {s: signal.getsignal(s) for s in before} == before
    rep = report(tmp_path)
    assert rep["engine"].startswith("engine restored")
    assert rep["outcome"] == "failed" and rep["exit_code"] == 1 and "cannot remove" in rep["lock_release_failed"]
    assert g.exit_code == 1


def test_harbor_patterns_catch_suffixed_names():
    import re
    lines = ["/Applications/Harbor.app/Contents/MacOS/Harbor",
             "/Applications/Harbor Preview.app/Contents/MacOS/Harbor-preview --flag",
             "/Applications/Wisp.app/Contents/MacOS/Wisp",
             "/opt/src/app/.build/arm64-apple-macosx/debug/Harbor-dev",
             "python -m service.main", "uvicorn service.main:app"]
    for line in lines:
        assert any(re.search(p, line) for p in rg.HARBOR_PROCESS_PATTERNS), line
    assert any(re.search(p, "/Applications/Harbor.app/Contents/MacOS/HarborHelper") for p in rg.HARBOR_PROCESS_PATTERNS)
    for other in ("/usr/bin/harborview", "/Applications/Wispr Flow.app/Contents/MacOS/Wispr Flow"):
        assert not any(re.search(p, other) for p in rg.HARBOR_PROCESS_PATTERNS), other


def test_restore_from_the_command_line_takes_the_lock(env):
    fake, guard, tmp_path, _ = env
    with pytest.raises(Exception):
        with guard() as g:
            g.prepare("gemma-a")
            fake.fail = ("POST", "/load")                     # the restore cannot load the user's model
    save, lock = tmp_path / "heavy.lock.engine-save.json", tmp_path / "heavy.lock"
    assert save.exists()
    fake.fail = None
    e = rg.Engine(URL, transport=fake.transport, sleep=lambda s: None)
    os.mkdir(lock)                                            # a heavy job holds the lock
    calls = len(fake.calls)
    assert rg.restore_cli(save, URL, str(lock), engine=e, harbor=lambda: None) == 1
    assert len(fake.calls) == calls and save.exists() and lock.exists()
    os.rmdir(lock)
    assert rg.restore_cli(save, URL, str(lock), engine=e, harbor=lambda: None) == 0
    back_as_before(fake, tmp_path)


# ------------------------------------------------------------------ the fix check's notes (third round)

def test_n1_a_first_signal_on_the_first_line_of_exit_still_restores(env):
    """Probe A2: a signal delivered as __exit__ starts, before it marks the exit as begun."""
    import sys
    fake, guard, tmp_path, _ = env
    hit = []

    def prof(frame, event, arg):
        if event == "call" and frame.f_code is rg.RunGuard.__exit__.__code__ and not hit:
            hit.append(1)
            os.kill(os.getpid(), signal.SIGTERM)
    try:
        with guard() as g:
            g.prepare("gemma-a")
            sys.setprofile(prof)
    finally:
        sys.setprofile(None)
    assert hit
    back_as_before(fake, tmp_path)
    assert "SIGTERM" in report(tmp_path)["signals_ignored"]


def test_n2_a_bad_url_never_leaves_the_heavy_lock(env):
    fake, guard, tmp_path, _ = env
    save, lock = tmp_path / "s.json", tmp_path / "L"
    save.write_text(json.dumps({"token": "t", "base_url": URL, "loaded": ["user-model", "user-pinned"]}))
    for bad in ("http://localhost:8000", "http://10.0.0.2:8000", "not a url"):
        assert rg.restore_cli(save, bad, str(lock), harbor=lambda: None) == 1
        assert not lock.exists()
    assert rg.restore_cli(save, URL, str(tmp_path / "missing" / "L"), harbor=lambda: None,
                          engine=rg.Engine(URL, transport=fake.transport)) == 1   # lock cannot be made
    assert save.exists() and fake.calls == []


def test_an_unload_answered_400_while_loading_again_waits_and_retries(env):
    """Probe 400race: after settling, the model starts loading again; oMLX answers 400 (no engine yet)."""
    fake, guard, tmp_path, _ = env
    done = []

    def race(method, path):
        if method == "POST" and path.endswith("/user-model/unload") and not done:
            done.append(1)
            fake.start_loading("user-model", reads=2)
    fake.on_call = race
    with guard() as g:
        g.prepare("gemma-a")
    assert done
    back_as_before(fake, tmp_path)


# ------------------------------------------------------------------ the engine's server (engine_service)

class FakeService:
    """oMLX's server as the guard sees it: running or not, started and stopped by its command line."""

    def __init__(self, running=False, answers=True):
        self.up, self.answers, self.calls = running, answers, []

    def running(self):
        return self.up and self.answers

    def start(self):
        self.calls.append("start")
        self.up = True

    def stop(self):
        self.calls.append("stop")
        self.up = False


def test_service_found_running_is_neither_started_nor_stopped(env):
    fake, guard, tmp_path, _ = env
    svc = FakeService(running=True)
    with guard(engine_service=svc) as g:
        g.prepare("gemma-a")
        g.note("ok")
    assert svc.calls == [] and g.outcome == "finished"
    assert report(tmp_path)["engine_service"] == {"found_running": True}
    back_as_before(fake, tmp_path)


def test_service_found_stopped_is_started_then_stopped_after_the_restore(env):
    fake, guard, tmp_path, _ = env
    svc = FakeService(running=False)
    with guard(engine_service=svc) as g:
        save = json.loads((tmp_path / "heavy.lock.engine-save.json").read_text())
        assert save["engine_service"] == {"found_running": False}          # a killed run still leaves the note
        g.prepare("gemma-a")
        g.note("ok")
    assert svc.calls == ["start", "stop"] and not svc.up
    r = report(tmp_path)
    assert r["engine_service"]["found_running"] is False and r["engine_service"]["stopped"] == "run ended"
    back_as_before(fake, tmp_path)


def test_a_start_that_never_answers_is_refused_and_put_back(env):
    fake, guard, tmp_path, _ = env
    svc = FakeService(running=False, answers=False)
    with pytest.raises(rg.GuardError, match="did not answer"):
        with guard(engine_service=svc, service_wait_s=0, sleep=lambda s: None):
            pass
    assert svc.calls == ["start", "stop"]                                  # stopped again, as found
    assert not (tmp_path / "heavy.lock").exists()
    assert not (tmp_path / "heavy.lock.engine-save.json").exists()          # no save: no model was touched
    assert fake.loaded() == ["user-model", "user-pinned"]


def test_a_failed_restore_leaves_the_started_server_running_and_says_so(env):
    fake, guard, tmp_path, _ = env
    svc = FakeService(running=False)
    with pytest.raises(httpx.HTTPStatusError):
        with guard(engine_service=svc) as g:
            g.prepare("gemma-a")
            fake.fail = ("POST", "/load")                                   # the restore cannot load them back
    assert svc.calls == ["start"] and svc.up
    r = report(tmp_path)
    assert "left_running" in r["engine_service"] and "stops it again" in r["restore_with"]
    save = json.loads((tmp_path / "heavy.lock.engine-save.json").read_text())
    assert save["engine_service"] == {"found_running": False}
    # the restore by hand puts the models back, then stops the server it was found without
    fake.fail = None
    svc2 = FakeService(running=True)
    eng = rg.Engine(URL, transport=fake.transport, sleep=lambda s: None)
    assert rg.restore_cli(tmp_path / "heavy.lock.engine-save.json", URL, str(tmp_path / "lock2"),
                          engine=eng, harbor=lambda: None, service=svc2) == 0
    assert svc2.calls == ["stop"] and fake.loaded() == ["user-model", "user-pinned"]


def test_sighup_during_the_run_restores_then_stops_the_started_server(env):
    fake, guard, tmp_path, _ = env
    svc = FakeService(running=False)
    with pytest.raises(rg.RunInterrupted):
        with guard(engine_service=svc) as g:
            g.prepare("gemma-a")
            os.kill(os.getpid(), signal.SIGHUP)
    assert svc.calls == ["start", "stop"]
    back_as_before(fake, tmp_path)


def test_harbor_open_when_stopping_leaves_the_server_running(env):
    fake, guard, tmp_path, state = env
    svc = FakeService(running=False)
    g = guard(engine_service=svc, harbor=lambda: "app")
    g._service_started = True                           # as after a start at entry; Harbor appeared since
    g._stop_service("run ended")
    assert svc.calls == [] and "Harbor" in g.report["engine_service"]["left_running"]
