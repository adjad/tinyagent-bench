"""Live-run safety in the in-assistant runner: its servers stop on a signal, one engine port, no
results or scratch inside the repository, an all-faults trial ends as a failure, and a live run
holds the shared run guard (edat/runtime/run_guard.py, tested in test_edat_runtime_run_guard.py).
No engine, server or port is used."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import signal
from pathlib import Path

import pytest

from scripts.productbench import engine_prep as ep

ROOT = Path(__file__).resolve().parents[1]


def _runner():
    spec = importlib.util.spec_from_file_location("rpb_safety", ROOT / "scripts" / "run_productbench.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_engine_prep_client_is_the_guards_loopback_client():
    with ep._client(8123) as c:
        assert str(c.base_url) == "http://127.0.0.1:8123" and c.trust_env is False


def test_a_live_run_holds_the_run_guard_and_a_stub_or_cloud_run_does_not(tmp_path):
    rpb = _runner()
    local = type("E", (), {"cloud": False})()
    cloud = type("E", (), {"cloud": True})()
    args = argparse.Namespace(out=str(tmp_path), lock=str(tmp_path / "heavy.lock"), engine_port=8123,
                              start_engine=False)
    assert isinstance(rpb.guard_for(args, [local], True, "r.json"), contextlib.nullcontext)
    assert isinstance(rpb.guard_for(args, [cloud], False, "r.json"), contextlib.nullcontext)
    g = rpb.guard_for(args, [local, cloud], False, "r.json")
    assert isinstance(g, rpb.run_guard.RunGuard) and g.engine.base_url == "http://127.0.0.1:8123"
    assert g.lock == Path(args.lock) and g.engine_service is None and g.report_path.name == "r.json"
    args.start_engine = True
    g = rpb.guard_for(args, [local], False, "r.json")
    assert isinstance(g.engine_service, rpb.run_guard.OmlxService) and g.engine_service.port == 8123
    with pytest.raises(rpb.run_guard.GuardError, match="inside the repository"):
        rpb.guard_for(argparse.Namespace(out=str(ROOT / "results"), lock=args.lock, engine_port=8123,
                                         start_engine=False), [local], False, "r.json")


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
