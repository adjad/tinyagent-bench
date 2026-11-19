"""Toolbench's run policy, report statistics, run metadata and the --compare guard.

Pure functions, tmp_path run directories and synthetic files only: no model,
network, oMLX or tool execution; the Harbor home is a temp directory.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HARBOR_HOME", str(Path(tempfile.mkdtemp(prefix="toolbench-policy-")) / ".moe"))

from scripts.toolbench import policy as pol  # noqa: E402
from scripts.toolbench import report as rep  # noqa: E402
from scripts.toolbench import runmeta  # noqa: E402

GEMMA = "gemma-4-E4B-it-oQ4e-mtp"
CANDIDATE = "granite-4.0-micro-4bit"     # an invented candidate id


def row(model, case_id, passed=True, *, trial=1, labels=(), excluded=False, secs=2.0):
    return {"run": "r", "model": model, "base_model": model, "variant": "default", "case": case_id,
            "category": "mail", "split": "gate", "trial": trial, "no_tool": False, "steps": [], "final": "",
            "escapes": [],
            "v": {"passed": passed, "labels": list(labels), "notes": [], "selection_ok": passed, "args_ok": passed,
                  "safe": True, "format_ok": True, "answer_ok": True, "seconds": secs, "model_seconds": secs,
                  "steps": 1, "prompt_tokens_est": 10, "excluded": excluded}}


# ------------------------------------------------------------------ policy

def test_sampling_and_treatment_defaults_follow_the_kind_of_run():
    assert pol.sampling_name("", models=[GEMMA], engine="omlx") == "engine-default"
    assert pol.sampling_name("", models=[GEMMA, CANDIDATE], engine="omlx") == "author"
    assert pol.sampling_name("", models=[CANDIDATE], engine="openai") == "author"
    with pytest.raises(ValueError):
        pol.sampling_name("greedy", models=[GEMMA], engine="omlx")
    assert pol.treatment_name("", models=[GEMMA], engine="omlx", profile_as="") == "packaged"
    assert pol.treatment_name("", models=[GEMMA, CANDIDATE], engine="omlx", profile_as="") == "probe"
    assert pol.treatment_name("", models=[GEMMA, CANDIDATE], engine="omlx", profile_as=GEMMA) == "profile-as"
    assert pol.treatment_name("", models=[CANDIDATE], engine="openai", profile_as="") == "probe"
    assert pol.sampling_for("engine-default", GEMMA) == {}
    assert pol.sampling_for("fixed", GEMMA) == pol.FIXED == pol.sampling_for("fixed", CANDIDATE)
    # The author file kept with the benchmark covers both references; an unlisted model is refused.
    for ref in (*pol.DEFAULT_REFERENCES, *pol.REFERENCE_ALIASES):
        assert pol.sampling_for("author", ref)["temperature"] == 1.0
    with pytest.raises(ValueError, match="author_sampling.json has no entry"):
        pol.sampling_for("author", CANDIDATE)
    assert pol.sampling_source("author") == "author_recommended"
    assert pol.sampling_source("fixed") == "fixed_sensitivity"
    assert set(pol.author_notes()) >= set(pol.DEFAULT_REFERENCES)


def test_a_sampling_file_is_validated_and_falls_back_to_star(tmp_path):
    path = tmp_path / "cards.json"
    path.write_text(json.dumps({GEMMA: {"temperature": 1.0, "top_k": 64}, "*": {"temperature": 0.7}}))
    table = pol.load_sampling_file(path)
    assert pol.sampling_for("file:x", GEMMA, table) == {"temperature": 1.0, "top_k": 64}
    assert pol.sampling_for("file:x", CANDIDATE, table) == {"temperature": 0.7}
    path.write_text(json.dumps({GEMMA: {"temperature": 1.0, "mirostat": 2}}))
    with pytest.raises(ValueError, match="unknown keys"):
        pol.load_sampling_file(path)
    with pytest.raises(ValueError, match="no entry"):
        pol.sampling_for("file:x", CANDIDATE, {GEMMA: {}})


def _policy(**over):
    kw = dict(engine="omlx", port=8000, sampling="fixed", sampling_params={GEMMA: dict(pol.FIXED)},
              treatment="probe", profile_as=None, context_window=16384, max_output_tokens=None, timeout_s=600,
              latency_budget_s=180, max_steps=8, thinking=["default"], world_seed=None, seed_base=1000,
              case_sessions="cleared_per_case", probe="required")
    kw.update(over)
    return pol.build(**kw)


def test_a_run_without_a_policy_reads_as_the_legacy_harness():
    legacy = pol.of_run({"models": [GEMMA], "max_steps": 8, "thinking": ["default"], "profile_as": GEMMA})
    assert legacy["sampling"] == "engine-default" and legacy["seed_mode"] == "per_trial"
    assert legacy["timeout_s"] == 180.0 and legacy["treatment"] == "profile-as" and legacy["legacy"]
    assert pol.of_run({"policy": _policy()})["seed_mode"] == "per_request"


def test_conflicts_split_hard_from_soft():
    base = {"models": [GEMMA], "policy": _policy(), "git": "a", "today": "2026-11-18", "cases": ["x", "y"]}
    assert pol.conflicts(base, base) == ([], [])
    hard, soft = pol.conflicts(base, {**base, "policy": _policy(timeout_s=180, engine="openai"), "git": "b"})
    assert hard == ["timeout_s: 600.0 vs 180.0"]
    assert any(s.startswith("engine:") for s in soft) and any(s.startswith("git:") for s in soft)
    hard, _ = pol.conflicts(base, {**base, "policy": _policy(sampling_params={GEMMA: {"temperature": 1.0}})})
    assert hard and hard[0].startswith("sampling_params")
    # A model only one run has does not make sampling params differ.
    other = {**base, "models": [GEMMA, CANDIDATE],
             "policy": _policy(sampling_params={GEMMA: dict(pol.FIXED), CANDIDATE: {"temperature": 1}})}
    assert pol.conflicts(base, other)[0] == []
    legacy = {"models": [GEMMA], "max_steps": 8, "thinking": ["default"], "case_sessions": "cleared_per_case"}
    hard, soft = pol.conflicts(legacy, base)
    assert any(h.startswith("sampling_source:") for h in hard) and any("predates" in s for s in soft)
    # The same params from two file paths compare; different thinking variant sets are only a note.
    a = {**base, "policy": _policy(sampling="file:/a/cards.json")}
    b = {**base, "policy": _policy(sampling="file:/b/cards.json", thinking=["default", "off"])}
    hard, soft = pol.conflicts(a, b)
    assert hard == [] and any(s.startswith("thinking:") for s in soft)
    assert pol.fingerprint(_policy()) == pol.fingerprint(_policy(engine="openai", latency_budget_s=60))
    assert pol.fingerprint(_policy()) != pol.fingerprint(_policy(context_window=8192))


def test_resolve_policy_from_cli_arguments():
    from scripts import run_toolbench
    args = SimpleNamespace(sampling="", treatment="", profile_as="", engine="openai", omlx_port=8080,
                           context_window=0, max_output_tokens=0, timeout=600.0, latency_budget=180.0, max_steps=8,
                           thinking="default,off", world_seed=None, seed_base=1000, skip_probe=False,
                           allow_unqualified=False, references="", with_references=False,
                           reference_treatment="packaged")
    with pytest.raises(ValueError, match="no entry"):
        run_toolbench._resolve_policy(args, [CANDIDATE])
    args.sampling, args.treatment = "fixed", ""
    p = run_toolbench._resolve_policy(args, [CANDIDATE])
    assert p["sampling"] == "fixed" and p["treatment"] == "probe" and p["context_window"] == pol.DEFAULT_WINDOW
    assert p["sampling_source"] == "fixed_sensitivity" and p["kind"] == "local" and p["references"] == []
    assert p["thinking"] == ["default", "off"] and p["probe"] == "required" and p["engine"] == "openai"
    assert p["sampling_params"] == {CANDIDATE: pol.FIXED} and len(p["fingerprint"]) == 16


# --------------------------------------------------------------- statistics

def test_rate_interval_is_case_clustered_and_deterministic():
    pairs = [(3, 3)] * 40 + [(0, 3)] * 10 + [(1, 3)] * 10
    lo, hi = rep.rate_ci(pairs)
    point = sum(a for a, _ in pairs) / sum(b for _, b in pairs)
    assert lo < point < hi and 0 < lo and hi < 1 and (lo, hi) == rep.rate_ci(pairs)
    assert rep.rate_ci([]) == (0.0, 0.0) and rep.rate_ci([(2, 2)] * 5) == (1.0, 1.0)


def test_paired_test_exact_and_monte_carlo():
    a = {f"c{i}": (0, 1) for i in range(5)}
    b = {f"c{i}": (1, 1) for i in range(5)}
    p = rep.paired(a, b)
    assert p["n"] == 5 and p["better"] == 5 and p["worse"] == 0 and p["exact"]
    assert p["mean_diff"] == 1.0 and p["p"] == pytest.approx(2 / 32)
    assert rep.paired(b, a)["mean_diff"] == -1.0 and rep.paired(a, a)["p"] == 1.0
    many_a = {f"c{i}": (i % 2, 1) for i in range(60)}
    many_b = {f"c{i}": (1, 1) for i in range(60)}
    m = rep.paired(many_a, many_b)
    assert not m["exact"] and m["p"] < 0.001 and m["better"] == 30 and m["tied"] == 30
    assert m["ci"][0] > 0 and rep.paired(many_a, many_b) == m
    # Only shared, scored cases count; dropped ones (route faults for both) too.
    assert rep.paired(a, {**b, "extra": (1, 1)})["n"] == 5
    assert rep.paired(a, b, drop={"c0"})["n"] == 4


def test_scorecard_reports_intervals_budget_exclusions_and_a_paired_line():
    rows = []
    for i in range(12):
        rows += [row(GEMMA, f"c{i}", passed=i < 6, trial=t) for t in (1, 2)]
        rows += [row(CANDIDATE, f"c{i}", passed=i < 10, trial=t, secs=300.0 if i == 0 else 2.0) for t in (1, 2)]
    rows.append(row(CANDIDATE, "c99", passed=False, labels=["engine_error"], excluded=True))
    rows += [row(CANDIDATE, "c98", passed=False, labels=["engine_error"], excluded=True)]
    s = rep.summarize(rows)
    assert s[CANDIDATE]["within_budget"] == 18 and s[CANDIDATE]["pass"] == 20
    assert s[CANDIDATE]["excluded_share"] == pytest.approx(2 / 26)
    assert s[GEMMA]["consistent"] == 6 and s[GEMMA]["trials_per_case"] == [2]
    text = rep.render(rows, latency_budget=180)
    for needle in ("pass 95% CI (cases)", "every-trial share", "pass ≤ 180s", "pass excl. route flt",
                   "raw format clean", f"PAIRED vs {GEMMA[:26]}", "WARNING: granite"):
        assert needle in text, needle
    paired_line = text.split("PAIRED vs")[1].splitlines()[1]
    assert "+33.3 pts" in paired_line and "better/worse/tied 4/0/8 of 12" in paired_line


def test_compare_adds_a_paired_line_per_model():
    base = [row(GEMMA, f"c{i}", passed=False) for i in range(6)]
    cur = [row(GEMMA, f"c{i}", passed=True) for i in range(6)]
    text = rep.compare(base, cur)
    assert "paired (current - baseline): +100.0 pts" in text and "p=0.0312 *" in text


# ----------------------------------------------------------- compare guard

def _run(tmp_path: Path, name: str, meta: dict, rows: list[dict]) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "meta.json").write_text(json.dumps(meta))
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return d


def test_compare_refuses_runs_made_under_different_policies(tmp_path, monkeypatch, capsys):
    from scripts import run_toolbench
    common = {"models": [GEMMA], "case_sessions": "cleared_per_case", "cases": ["c0", "c1"]}
    base = _run(tmp_path, "base", {**common, "policy": _policy(timeout_s=180)}, [row(GEMMA, "c0", False)])
    cur = _run(tmp_path, "cur", {**common, "policy": _policy()}, [row(GEMMA, "c0", True)])
    argv = ["run_toolbench.py", "--report", str(cur), "--compare", str(base), "--stored"]
    monkeypatch.setattr(sys, "argv", argv)
    assert run_toolbench.main() == 6
    out = capsys.readouterr().out
    assert "REFUSING to compare" in out and "timeout_s: 180.0 vs 600.0" in out and "SCORECARD" not in out
    monkeypatch.setattr(sys, "argv", argv + ["--allow-policy-mismatch"])
    assert run_toolbench.main() == 0
    out = capsys.readouterr().out
    assert "comparing although" in out and "COMPARISON" in out and "paired" in out
    same = _run(tmp_path, "same", {**common, "policy": _policy()}, [row(GEMMA, "c0", False)])
    monkeypatch.setattr(sys, "argv", ["run_toolbench.py", "--report", str(cur), "--compare", str(same), "--stored"])
    assert run_toolbench.main() == 0 and "REFUSING" not in capsys.readouterr().out


# ------------------------------------------------------------ run metadata

def test_power_state_parsing():
    batt = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t83%; discharging; 4:10 remaining"
    st = runmeta.power_state(batt, " lowpowermode         1\n", "Note: No thermal warning level has been recorded")
    assert st == {"power_source": "battery", "battery_percent": 83, "low_power_mode": True,
                  "thermal_warning": "none", "cpu_speed_limit": None}
    st = runmeta.power_state("Now drawing from 'AC Power'", "lowpowermode 0", "CPU_Speed_Limit = 100")
    assert st["power_source"] == "ac" and st["low_power_mode"] is False and st["cpu_speed_limit"] == 100


def test_model_files_reads_an_mlx_dir_and_a_gguf_header(tmp_path):
    d = tmp_path / "Qwen3-30B-A3B-4bit"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"model_type": "qwen3_moe", "num_hidden_layers": 48,
                                               "num_experts": 128, "num_experts_per_tok": 8,
                                               "quantization": {"group_size": 64, "bits": 4}}))
    (d / "model.safetensors").write_bytes(b"\0" * 1000)
    info = runmeta.model_files(d)
    assert info["bytes"] == 1000 and info["format"] == "safetensors/mlx" and not info["multimodal"]
    assert info["config"]["num_experts"] == 128 and info["quantization"] == {"group_size": 64, "bits": 4}

    def kv_str(key, val):
        k, v = key.encode(), val.encode()
        return struct.pack("<Q", len(k)) + k + struct.pack("<I", 8) + struct.pack("<Q", len(v)) + v

    def kv_u32(key, val):
        k = key.encode()
        return struct.pack("<Q", len(k)) + k + struct.pack("<I", 4) + struct.pack("<I", val)
    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 3) + kv_str("general.architecture", "qwen3")
                     + kv_u32("qwen3.context_length", 40960) + kv_str("general.size_label", "1.7B"))
    g = runmeta.model_files(gguf)
    assert g["format"] == "gguf" and g["gguf"]["general.architecture"] == "qwen3"
    assert g["gguf"]["qwen3.context_length"] == 40960 and g["gguf"]["general.size_label"] == "1.7B"
    assert runmeta.model_files(tmp_path / "missing")["error"] == "not found"


def test_declared_info_and_model_paths(tmp_path):
    path = tmp_path / "info.json"
    path.write_text(json.dumps({CANDIDATE: {"params_b": 3.2, "active_params_b": 3.2, "quantization": "4bit"}}))
    assert runmeta.declared(path)[CANDIDATE]["params_b"] == 3.2
    path.write_text(json.dumps({CANDIDATE: {"favourite_colour": "blue"}}))
    with pytest.raises(ValueError, match="unknown fields"):
        runmeta.declared(path)
    assert runmeta.model_paths("a=/x/a, b=/y/b") == {"a": "/x/a", "b": "/y/b"}
    assert runmeta.model_paths("/x/only.gguf") == {"*": "/x/only.gguf"}


# ------------------------------------------------------- probe treatment bind

def test_bind_model_takes_probe_lists_and_a_window_policy(tmp_path, monkeypatch):
    from scripts.toolbench import runner
    from service import config
    (tmp_path / "ms.json").write_text(json.dumps({"models": {CANDIDATE: {"max_context_window": 32768}}}))
    monkeypatch.setattr(config, "OMLX_MODEL_SETTINGS", tmp_path / "ms.json")
    runner.pin_endpoint(runner.DEFAULT_OMLX_PORT)
    try:
        b = runner.bind_model(CANDIDATE, lists={"thinking_required_after_tool": True, "tool_capable": True},
                              context_window=12000)
        assert b["treatment"] == "probe" and b["context_window"] == 12000 and b["own_context_window"] == 32768
        assert b["lists"] == {"tool_capable": True, "no_thinking_capable": False,
                              "thinking_required_after_tool": True, "explicit_thinking": False}
        assert config.thinking_after_tool_required(CANDIDATE)
        with pytest.raises(ValueError):
            runner.bind_model(CANDIDATE, profile_as=GEMMA, lists={})
        b = runner.bind_model(CANDIDATE)
        assert b["treatment"] == "packaged" and b["context_window"] == 32768
        assert not config.thinking_after_tool_required(CANDIDATE)
    finally:
        runner.bind_model(CANDIDATE)
        runner._restore_aliases()


def test_this_file_is_in_the_simulation_manifest():
    path = ROOT / "scripts" / "run_simulation_qa.py"
    if not path.exists():
        pytest.skip("scripts/run_simulation_qa.py is Harbor's release tooling and is absent here, "
                    "so there is no Simulation QA manifest to check")
    manifest = path.read_text()
    assert '"tests/test_toolbench_policy.py"' in manifest


# ------------------------------------------------------ tinyagent-bench P0 additions

def test_unbiased_pass_k_and_wilson():
    assert rep.pass_hat_k([(3, 3), (2, 3), (0, 3)], 1) == pytest.approx(5 / 9)
    assert rep.pass_hat_k([(3, 3), (2, 3), (0, 3)], 2) == pytest.approx((1 + 1 / 3 + 0) / 3)
    assert rep.pass_hat_k([(3, 3), (2, 3), (0, 3)], 3) == pytest.approx(1 / 3)
    assert rep.pass_hat_k([(1, 1)], 2) is None
    lo, hi = rep.wilson(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    assert rep.wilson(0, 0) == (0.0, 0.0) and rep.wilson(10, 10)[1] == pytest.approx(1.0)


def test_references_pair_every_candidate_within_its_thinking_variant():
    ref, other = "gemma-ref", "phi-candidate"
    rows = []
    for i in range(6):
        for label, ok in ((ref, i < 3), (f"{ref} +off", i < 1), (CANDIDATE, i < 5), (f"{CANDIDATE} +off", i < 2),
                          (other, True)):
            rows.append(row(label, f"c{i}", passed=ok))
    text = rep.render(rows, references=[ref])
    section = text.split(f"PAIRED vs {ref}")[1].split("\n\n")[0]
    assert f"  {CANDIDATE}: +33.3 pts" in section and f"  {CANDIDATE} +off: +16.7 pts" in section
    assert f"  {other}: +50.0 pts" in section and f"  {ref} +off" not in section
    s = rep.summarize(rows)
    assert s[CANDIDATE]["pass_k"] == {1: pytest.approx(5 / 6)}


def test_a_result_key_names_weights_runtime_sampling_and_harness(tmp_path):
    model_dir = tmp_path / "m"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(b"x" * 3000)
    files = runmeta.model_files(model_dir)
    assert len(files["weights_fingerprint"]) == 64
    policy = _policy(sampling="file:/x/cards.json", sampling_params={CANDIDATE: {"temperature": 0.7}})
    info = {"runtime": "llama.cpp", "props": {"build_info": "b5000", "chat_template_sha256": "ab" * 32}}
    key = runmeta.result_key(policy, CANDIDATE, info, {"files": files, "declared": {"quantization": "Q4_K_M"}},
                             "abc1234")
    assert key["sampling_source"] == "author_recommended" and key["sampling"] == {"temperature": 0.7}
    assert key["runtime_version"] == "b5000" and key["template_sha256"] == "ab" * 32
    assert key["quantization"] == "Q4_K_M" and key["weights_fingerprint"] == files["weights_fingerprint"]
    assert key["harness"] == "abc1234" and key["lang"] == "en" and key["policy"] == policy["fingerprint"]
    assert runmeta.result_key(_policy(), GEMMA, {}, {}, "x")["sampling_source"] == "fixed_sensitivity"


def test_with_references_puts_both_edat_references_first_with_production_treatment():
    from scripts import run_toolbench
    args = SimpleNamespace(references="", with_references=True, reference_treatment="packaged", treatment="probe")
    refs = run_toolbench._references(args)
    assert refs == [pol.PRIMARY_REFERENCE, pol.BASE_REFERENCE]
    assert run_toolbench._treatment_for(args, pol.PRIMARY_REFERENCE, refs) == "packaged"
    assert run_toolbench._treatment_for(args, CANDIDATE, refs) == "probe"


def test_pinned_baseline_runs_pair_every_model_and_show_the_tuning_gap(tmp_path, monkeypatch, capsys):
    from scripts import run_toolbench
    tuned, base = pol.PRIMARY_REFERENCE, pol.BASE_REFERENCE
    common = {"case_sessions": "cleared_per_case", "cases": [f"c{i}" for i in range(8)], "today": "2026-11-18"}
    ref_rows = ([row(tuned, f"c{i}", passed=i < 6) for i in range(8)]
                + [row(base, f"c{i}", passed=i < 4) for i in range(8)])
    b = _run(tmp_path, "refs", {**common, "models": [tuned, base], "references": [tuned, base],
                                "policy": _policy(sampling_params={})}, ref_rows)
    cur = _run(tmp_path, "cand", {**common, "models": [CANDIDATE], "policy": _policy(sampling_params={})},
               [row(CANDIDATE, f"c{i}", passed=i < 5) for i in range(8)])
    monkeypatch.setattr(sys, "argv", ["run_toolbench.py", "--report", str(cur), "--baseline", str(b), "--stored"])
    assert run_toolbench.main() == 0
    out = capsys.readouterr().out
    vs_tuned = out.split(f"PAIRED vs {tuned}")[1].split("\n\n")[0]
    vs_base = out.split(f"PAIRED vs {base[:26]}")[1].split("\n\n")[0]
    assert "pinned baseline run refs" in vs_tuned and f"{CANDIDATE}: -12.5 pts" in vs_tuned
    assert f"{CANDIDATE}: +12.5 pts" in vs_base
    assert f"REFERENCE GAP {tuned}" in out and "+25.0 pts" in out.split("REFERENCE GAP")[1]
    other = _run(tmp_path, "other", {**common, "models": [tuned], "policy": _policy(timeout_s=180)}, ref_rows)
    monkeypatch.setattr(sys, "argv", ["run_toolbench.py", "--report", str(cur), "--baseline", str(other), "--stored"])
    assert run_toolbench.main() == 6 and "REFUSING to pair" in capsys.readouterr().out


def test_scorecard_reports_a_format_validity_rate_under_the_edat_heading():
    rows = [row(GEMMA, "c0"), row(GEMMA, "c1")]
    rows[1]["v"]["format_ok"] = False
    text = rep.render(rows)
    assert text.startswith("tinyagent-bench · TOOLBENCH SCORECARD") and "format valid" in text
    assert [l for l in text.splitlines() if l.startswith("format valid")][0].split()[-1] == "50%"


def test_probe_mode_keeps_the_plain_single_model_run_as_before():
    from scripts import run_toolbench
    a = SimpleNamespace(skip_probe=False, probe=False, engine="omlx", treatment="packaged", allow_unqualified=False)
    assert run_toolbench._probe_mode(a, [GEMMA]) == "skipped"
    a.probe = True
    assert run_toolbench._probe_mode(a, [GEMMA]) == "required"
    a.probe, a.treatment = False, "probe"
    assert run_toolbench._probe_mode(a, [GEMMA, CANDIDATE]) == "required"
    a.skip_probe = True
    assert run_toolbench._probe_mode(a, [GEMMA, CANDIDATE]) == "skipped"
    args = SimpleNamespace(sampling="fixed", treatment="probe", profile_as="", engine="omlx", omlx_port=8000,
                           context_window=0, max_output_tokens=0, timeout=600.0, latency_budget=180.0, max_steps=8,
                           thinking="default", world_seed=None, seed_base=1000, skip_probe=True, probe=False,
                           allow_unqualified=False, references="", with_references=False,
                           reference_treatment="packaged")
    with pytest.raises(ValueError, match="--skip-probe with --treatment probe"):
        run_toolbench._resolve_policy(args, [GEMMA, CANDIDATE])


def test_a_malformed_turn_is_scored_once_not_retried():
    import asyncio
    from scripts import run_toolbench
    calls = []

    async def run_case(*a, **k):
        calls.append(1)
        return {"error": "IncompleteStreamError: Incomplete tool arguments", "steps": [], "final": ""}

    async def recover(*a, **k):
        raise AssertionError("a model's malformed output must not trigger the engine retry")
    runner = SimpleNamespace(run_case=run_case)
    args = SimpleNamespace(timeout=5, max_steps=8, profile_as="", treatment="packaged")
    case = SimpleNamespace(id="c", split="gate", category="mail", no_tool=False)
    real = run_toolbench._recover
    run_toolbench._recover = recover
    try:
        out = asyncio.run(run_toolbench._one(runner, lambda *a, **k: SimpleNamespace(to_dict=lambda: {}), None,
                                             GEMMA, case, SimpleNamespace(today=None), set(), set(), args,
                                             "r", 1, "default"))
    finally:
        run_toolbench._recover = real
    assert calls == [1] and out["error"].endswith("Incomplete tool arguments")


def test_edat_device_runtime_and_conditions_use_the_perf_spec_names():
    info = {"chip": "Apple M5 Pro", "model": "Mac17,9", "memory_gb": 24.0, "ram_bytes": 24 * 2**30,
            "gpu_wired_limit_mb": "0", "os": "26.3", "os_build": "25D60", "omlx_version": "0.7.3rc1",
            "packages": {"mlx": "0.30.0"}, "power_source": "ac", "battery_percent": 97, "battery_state": "charged",
            "low_power_mode": False, "thermal_warning": "none", "uptime_s": 3600}
    b = runmeta.edat_blocks(info, engine="omlx")
    assert b["device"]["class"] == "mac-ref" and b["device"]["soc"] == "M5 Pro"
    assert b["device"]["ram_bytes"] == 24 * 2**30 and b["device"]["os"] == "macOS 26.3"
    assert b["device"]["gpu_wired_limit_mb"] == 0
    assert b["runtime"] == {"name": "omlx", "version": "0.7.3rc1", "commit": None, "packages": {"mlx": "0.30.0"},
                            "backend": "metal", "engine_profile": {}}
    assert b["conditions_start"]["battery_level"] == 0.97 and b["conditions_start"]["thermal_state"] == "none"
    assert runmeta.edat_blocks({**info, "memory_gb": 16.0}, engine="omlx")["device"]["class"] == "mac"
    assert runmeta.battery_state("-InternalBattery-0 (id=1)\t97%; charged; 0:00 remaining") == "charged"


def test_a_model_record_uses_the_perf_specs_models_names():
    key = {"model": "Gemma-Wisp", "role": "baseline", "kind": "local", "architecture": "dense",
           "quantization": "oQ4e", "weights_fingerprint": "ab12", "weights_bytes": 5, "template_sha256": None}
    r = runmeta.model_record(key, thinking=["default", "off"], window=16384)
    assert r["id"] == "Gemma-Wisp" and r["role"] == "baseline" and r["profile_window_tokens"] == 16384
    assert r["quant"] == {"scheme": "oQ4e"} and r["thinking"] == ["default", "off"]
    assert {"params_total", "tokenizer_sha256", "speculation", "native_window_tokens"} <= set(r)


def test_the_case_set_version_is_recorded_and_never_paired_across(tmp_path, monkeypatch, capsys):
    from scripts import run_toolbench
    from scripts.toolbench.cases import CASE_SET_VERSION, case_set
    cs = case_set()
    assert cs["version"] == CASE_SET_VERSION >= 2 and len(cs["sha256"]) == 16 and cs["cases"] > 300
    assert _policy()["case_set"] == CASE_SET_VERSION
    assert pol.of_run({"timeout": 180})["case_set"] == pol.LEGACY_CASE_SET == 1
    old, new = {"policy": _policy(case_set=1)}, {"policy": _policy()}
    assert pol.never_paired(old, new) == [f"case_set: 1 vs {CASE_SET_VERSION}"]
    assert any(h.startswith("case_set") for h in pol.conflicts(old, new)[0])
    assert pol.never_paired(new, {"policy": _policy()}) == []
    for name, meta in (("old", old), ("new", new)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "meta.json").write_text(json.dumps({**meta, "models": [], "cases": []}))
        (tmp_path / name / "results.jsonl").write_text("")
    for flag in ("--compare", "--baseline"):
        monkeypatch.setattr(sys, "argv", ["run_toolbench.py", "--report", str(tmp_path / "new"), flag,
                                          str(tmp_path / "old"), "--allow-policy-mismatch", "--stored"])
        assert run_toolbench.main() == 6
        assert "different case-set versions are never paired" in capsys.readouterr().out


def test_a_report_only_case_is_recorded_but_in_no_rate():
    v = {"passed": False, "labels": ["route_fault"], "seconds": 1.0, "selection_ok": False, "args_ok": False,
         "safe": True, "format_ok": True, "answer_ok": True, "steps": 1, "prompt_tokens_est": 10}
    ok = {**v, "passed": True, "labels": [], "selection_ok": True, "args_ok": True}
    rows = [{"model": "m", "case": "a", "trial": 1, "category": "t", "split": "gate", "v": ok},
            {"model": "m", "case": "b", "trial": 1, "category": "t", "split": "gate", "v": v,
             "report_only": "host refuses the tool for every model"}]
    s = rep.summarize(rows)["m"]
    assert s["pass"] == 1 and s["trials"] == 1 and s["cases"] == 1
    assert rep._scored(rows) == rows[:1]
    out = rep.render(rows)
    assert "REPORT-ONLY (not in any rate): 1 case(s), 0/1 trials passed: b" in out
    from scripts.toolbench.cases import parse_case
    assert parse_case({"id": "x", "prompt": "p", "report_only": "why"}, "t").report_only == "why"
