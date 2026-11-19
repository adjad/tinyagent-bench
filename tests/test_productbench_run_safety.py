"""Live-run safety (engine_prep, the runner, session.py): the engine and the lock are put back on
every exit path, a saved engine state from another run is never restored, and an all-faults
trial ends as a failure. No engine, server or port is used: every outside effect is a fake."""
from __future__ import annotations

import importlib.util
import json
import os
import signal
from pathlib import Path

import pytest

from scripts.productbench import engine_prep as ep
from scripts.productbench import session

ROOT = Path(__file__).resolve().parents[1]


def _runner():
    spec = importlib.util.spec_from_file_location("rpb_safety", ROOT / "scripts" / "run_productbench.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeEngine:
    """/v1/models/status, load and unload, like oMLX."""

    def __init__(self, loaded):
        self.loaded = list(loaded)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, path):
        rows = [{"id": m, "loaded": True} for m in self.loaded]
        return type("R", (), {"json": lambda s: {"models": rows}})()

    def post(self, path):
        mid, verb = path.split("/v1/models/")[1].rsplit("/", 1)
        self.calls.append((verb, mid))
        if verb == "unload" and mid in self.loaded:
            self.loaded.remove(mid)
        if verb == "load" and mid not in self.loaded:
            self.loaded.append(mid)
        return type("R", (), {"raise_for_status": lambda s: None})()


@pytest.fixture
def engine(monkeypatch, tmp_path):
    eng = FakeEngine(["Other-chat-model", "bge-m3-embed"])
    ports = []
    monkeypatch.setattr(ep, "_client", lambda port=None: ports.append(port) or eng)
    settings = tmp_path / "model_settings.json"
    settings.write_text(json.dumps({"models": {"Other-chat-model": {"is_pinned": True, "is_default": True}}}))
    monkeypatch.setattr(ep, "SETTINGS", settings)
    eng.ports, eng.settings = ports, settings
    return eng


# ------------------------------------------------------------------ engine_prep
def test_keep_saves_flags_and_token_and_restore_puts_them_back(engine, tmp_path):
    save = tmp_path / "before.json"
    assert ep.main(["keep", "Model-under-test", "--save", str(save), "--token", "t1", "--engine-port", "8123"]) == 0
    data = json.loads(save.read_text())
    assert data["loaded"] == ["Other-chat-model", "bge-m3-embed"] and data["token"] == "t1"
    assert data["flags"] == {"Other-chat-model": {"is_pinned": True, "is_default": True}}
    assert data["engine_port"] == 8123 and set(engine.ports) == {8123}       # one engine for every call
    assert engine.loaded == ["bge-m3-embed"]                                   # helper models stay
    # a second keep in the same session (the next model) reuses the file
    assert ep.main(["keep", "Second-model", "--save", str(save), "--token", "t1", "--engine-port", "8123"]) == 0
    assert ep.main(["restore", str(save), "--token", "t1", "--engine-port", "8123"]) == 0
    assert "Other-chat-model" in engine.loaded


def test_a_save_from_another_run_is_never_reused_or_restored(engine, tmp_path):
    save = tmp_path / "resident-before.json"
    save.write_text(json.dumps({"loaded": ["Yesterdays-model"], "token": "old"}))
    with pytest.raises(ep.StaleSave):
        ep.main(["keep", "Model-under-test", "--save", str(save), "--token", "new"])
    with pytest.raises(ep.StaleSave):
        ep.main(["restore", str(save), "--token", "new"])
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"loaded": ["Yesterdays-model"]}))           # no token at all
    with pytest.raises(ep.StaleSave):
        ep.main(["restore", str(legacy), "--token", "new"])
    assert engine.calls == []                                                 # the engine was never touched
    # a file saved from another engine's port is refused too
    other = tmp_path / "other-port.json"
    other.write_text(json.dumps({"loaded": [], "token": "new", "engine_port": 9000}))
    with pytest.raises(ep.StaleSave):
        ep.main(["restore", str(other), "--token", "new", "--engine-port", "8000"])


def test_restore_reports_changed_pin_flags_and_never_writes_them(engine, tmp_path):
    save = tmp_path / "before.json"
    assert ep.main(["keep", "Model-under-test", "--save", str(save), "--token", "t"]) == 0
    before = engine.settings.read_text()
    engine.settings.write_text(json.dumps({"models": {"Other-chat-model": {"is_pinned": False}}}))
    assert ep.main(["restore", str(save), "--token", "t"]) == 3
    assert ep.flag_changes({"a": {"is_pinned": True}}, {}) == ["a"]
    assert ep.flag_changes({"a": {"is_pinned": True}}, {"a": {"is_pinned": True, "is_default": False}}) == []
    assert before != engine.settings.read_text()                             # only the test changed it


def test_without_a_token_keep_still_shares_one_file_across_models(engine, tmp_path):
    save = tmp_path / "resident-before.json"
    assert ep.main(["keep", "A", "--save", str(save)]) == 0
    first = save.read_text()
    assert ep.main(["keep", "B", "--save", str(save)]) == 0                    # the sidekick tier's use
    assert save.read_text() == first


def test_the_engine_client_is_loopback_whatever_the_port():
    with ep._client(8123) as c:
        assert str(c.base_url) == "http://127.0.0.1:8123"


# ------------------------------------------------------------------ the runner
def test_runner_shares_engine_prep_port_default():
    rpb = _runner()
    assert rpb.engine_prep.PORT == ep.PORT


def test_runner_refuses_results_or_scratch_inside_the_repo(tmp_path):
    rpb = _runner()
    assert "inside the repository" in rpb.refuse_inside_repo(str(ROOT / "results"), None)
    assert "$TMPDIR" in rpb.refuse_inside_repo(str(tmp_path), str(ROOT / "tmp"))
    assert rpb.refuse_inside_repo(str(tmp_path), str(tmp_path)) is None


def test_an_all_faults_trial_is_a_failure_at_any_record_count(tmp_path):
    rpb = _runner()
    log = tmp_path / "results.jsonl"
    log.write_text(json.dumps({"captured": {"error": ""}}) + "\n" +
                   json.dumps({"captured": {"error": "engine down"}}) + "\n")
    assert rpb.all_faults(log, 1) and not rpb.all_faults(log, 2) and not rpb.all_faults(log, 0)
    f = rpb.act_engine_fault
    assert f({"calls": 0}, [{"t": 1}], None, "m", stub=False) == "the Brief made no engine call"
    assert f({"calls": 3}, [], None, "m", stub=False) == "the Brief showed no card"
    assert "did not hold" in f({"calls": 3}, [{"t": 1}], {"loaded": ["other"]}, "m", stub=False)
    assert f({"calls": 3}, [{"t": 1}], {"loaded": ["m"]}, "m", stub=False) is None
    assert f({"calls": 3}, [{"t": 1}], {"available": False}, "m", stub=True) is None


def test_signals_unwind_and_a_nohup_run_keeps_ignoring_hangup():
    rpb = _runner()
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        assert rpb.install_signal_handlers() == [signal.SIGTERM]
        with pytest.raises(rpb.Terminated) as exc:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert exc.value.signum == signal.SIGTERM and not isinstance(exc.value, Exception)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)
        assert signal.SIGHUP in rpb.install_signal_handlers()
    finally:
        for s, h in saved.items():
            signal.signal(s, h)


def test_act_engine_faults_are_excluded_and_counted():
    from scripts.productbench import findings, summary
    base = {"entry": "M", "model": "m", "kind": "local", "profile": "p", "scorer": summary.SCORER_VERSION,
            "surface": "act", "case": "act-for-me"}
    good = {**base, "trial": 1, "captured": {"error": None},
            "score": {"score": 0.5, "counts": {"right": 1, "done_wrongly": 0, "should_not": 0, "missed": 1},
                      "unsafe": [], "actions": [], "missed": []}}
    bad = {**base, "trial": 2, "captured": {"error": "the Brief made no engine call"},
           "score": {"score": 0.0, "counts": {"right": 0, "done_wrongly": 0, "should_not": 0, "missed": 2},
                     "unsafe": [], "actions": [], "missed": []}}
    rows = summary.summarize([good, bad])
    assert rows[0]["act_engine_faults"] == 1
    rep = findings.collect([good, bad], {})
    assert rep.faults[("M", "p")]["act_engine_fault"] == {"trial 2"}


# ------------------------------------------------------------------ session.py
@pytest.fixture
def host(monkeypatch, tmp_path):
    """A Mac with Harbor closed, free ports, and an engine that is stopped until started."""
    state = {"engine": False, "harbor": False, "cli": [], "keeps": [], "restores": []}
    monkeypatch.setattr(session, "harbor_running", lambda: state["harbor"])
    monkeypatch.setattr(session, "port_busy", lambda p: False)
    monkeypatch.setattr(session, "engine_prep_listening", lambda p: state["engine"])

    def cli(path, verb):
        state["cli"].append(verb)
        state["engine"] = verb == "restart"
        return 0
    monkeypatch.setattr(session, "engine_cli", cli)

    def prep(argv):
        (state["keeps"] if argv[0] == "keep" else state["restores"]).append(argv)
        if argv[0] == "keep":
            Path(argv[argv.index("--save") + 1]).write_text("{}")
        return 0
    monkeypatch.setattr(session.engine_prep, "main", prep)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    state["plan"] = {"out": str(tmp_path / "out"), "lock": str(tmp_path / "heavy.lock"), "engine_port": 8000,
                     "start_engine": True, "omlx_cli": "omlx-cli",
                     "steps": [{"engine_model": "A", "runner": ["--model", "A", "--profile", "p"]},
                               {"engine_model": "B", "runner": ["--model", "B", "--profile", "p"]}]}
    return state


class FakeChild:
    made = 0

    def __init__(self, out: Path, *, stopped=None, rc=0, raise_on_wait=None):
        FakeChild.made += 1
        run = out / f"edat-iae-20260101-{FakeChild.made:06d}"
        run.mkdir(parents=True, exist_ok=True)
        (run / "meta.json").write_text(json.dumps({"stopped": stopped}))
        self.rc, self.raise_on_wait, self.signals = rc, raise_on_wait, []

    def wait(self, timeout=None):
        if self.raise_on_wait:
            exc, self.raise_on_wait = self.raise_on_wait, None
            raise exc
        return self.rc

    def poll(self):
        return None if self.signals == [] else self.rc

    def send_signal(self, sig):
        self.signals.append(sig)

    def kill(self):
        self.signals.append("kill")


def test_session_runs_every_step_then_restores_and_releases(host, monkeypatch, tmp_path):
    children = []

    def popen(cmd, cwd):
        assert cmd[cmd.index("--engine-port") + 1] == "8000" and "--out" in cmd
        children.append(FakeChild(Path(cmd[cmd.index("--out") + 1])))
        return children[-1]
    monkeypatch.setattr(session.subprocess, "Popen", popen)
    log = session.run(host["plan"], wait_s=0)
    assert log["result"] == "done" and [s["result"] for s in log["steps"]] == ["done", "done"]
    token = log["token"]
    assert all(k[k.index("--token") + 1] == token for k in host["keeps"])
    assert host["restores"] and host["restores"][0][1].endswith(f"engine-before-{token}.json")
    assert host["cli"] == ["restart", "stop"]                     # found stopped, so stopped again
    assert not Path(host["plan"]["lock"]).exists() and log["lock_released"] is True


def test_a_signal_mid_step_still_restores_the_engine_and_the_lock(host, monkeypatch):
    child = {}

    def popen(cmd, cwd):
        child["c"] = FakeChild(Path(cmd[cmd.index("--out") + 1]),
                               raise_on_wait=session.Terminated(signal.SIGTERM))
        return child["c"]
    monkeypatch.setattr(session.subprocess, "Popen", popen)
    log = session.run(host["plan"], wait_s=0)
    assert "SIGTERM" in log["result"] and child["c"].signals[0] == signal.SIGTERM
    assert log["restore"] == "restored" and host["cli"][-1] == "stop"
    assert not Path(host["plan"]["lock"]).exists()


def test_a_stopped_step_ends_the_session_as_a_failure(host, monkeypatch):
    monkeypatch.setattr(session.subprocess, "Popen",
                        lambda cmd, cwd: FakeChild(Path(cmd[cmd.index("--out") + 1]), stopped="engine faults"))
    log = session.run(host["plan"], wait_s=0)
    assert log["result"].startswith("failed") and len(log["steps"]) == 1
    assert log["restore"] == "restored" and not Path(host["plan"]["lock"]).exists()


def test_session_refuses_before_touching_anything(host, monkeypatch, tmp_path):
    lock = Path(host["plan"]["lock"])
    lock.mkdir()
    log = session.run(host["plan"], wait_s=0)
    assert "exists" in log["result"] and lock.exists() and log["lock_released"] == "not taken"
    assert host["keeps"] == [] and host["cli"] == []
    lock.rmdir()
    host["harbor"] = True
    with pytest.raises(session.SessionError, match="Harbor is running"):
        session.run(host["plan"], wait_s=0)
    host["harbor"] = False
    bad = {**host["plan"], "out": str(ROOT / "results")}
    with pytest.raises(session.SessionError, match="inside the repository"):
        session.run(bad, wait_s=0)
    bad = {**host["plan"], "steps": [{"engine_model": "A", "runner": ["--engine-port", "9"]}]}
    with pytest.raises(session.SessionError, match="the session sets it"):
        session.run(bad, wait_s=0)
    assert host["keeps"] == [] and not lock.exists()
