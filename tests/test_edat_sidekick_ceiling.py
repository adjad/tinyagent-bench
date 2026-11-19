"""The coding-ceiling ladder: rungs, the probe grader, the ceiling rule, the run driver and the report."""
from __future__ import annotations

import json

import pytest

from edat.sidekick.ceiling import models, rungs
from edat.sidekick.ceiling.probe import extract_files
from edat.sidekick.ceiling.report import build_report, entry_summary
from edat.sidekick.ceiling.run import _entry_problem, plan_units, run_all, run_entry
from edat.sidekick.sandbox.run import SandboxUnavailable, SeatbeltIsolation


@pytest.fixture(scope="module")
def iso():
    i = SeatbeltIsolation()
    try:
        i.check()
    except SandboxUnavailable as exc:
        if "sandbox_apply" in str(exc) or "Operation not permitted" in str(exc):
            pytest.skip("nested Seatbelt sandbox")
        raise
    return i


@pytest.fixture
def private(tmp_path, monkeypatch):
    p = tmp_path / "private"
    (p / "sidekick").mkdir(parents=True)
    (p / "sidekick" / "master_seed").write_text("test-heldout-seed\n")
    monkeypatch.setenv("EDAT_PRIVATE_DIR", str(p))
    return p


def _model(engine_id="gemma-4-E4B-it-oQ4e-mtp", family="gemma4", thinking=(False, True), standard=True,
           label="Gemma 4 E4B (base reference)", role="reference", gb=5.5):
    return models.LocalModel(engine_id=engine_id, folder=engine_id, label=label, role=role, model_type=family,
                             family=family, loader="mlx-vlm", disk_gb=gb,
                             quant={"scheme": "oQ4e", "bits": 4, "group_size": 64, "mixed": False},
                             bits_per_weight=4.6, thinking=thinking, standard=standard, group=engine_id,
                             decode_tok_s=55.0)


def _entry(m=None, thinking=False, profile=0):
    m = m or _model()
    return next(e for e in m.entries() if e.thinking == thinking and e.profile == m.profiles(thinking)[profile])


# ---------------------------------------------------------------- rungs and cases

def test_nine_rungs_in_order():
    assert [r.n for r in rungs.RUNGS] == list(range(1, 10))
    assert [r.id for r in rungs.RUNGS if r.source == "sidekick"] == ["sk-l1", "sk-l2", "sk-l3"]
    assert [r.mode for r in rungs.RUNGS].count("tool") == 1


def test_every_probe_rung_has_cases_with_unique_ids():
    cases = rungs.ladder_cases()
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))
    for r in rungs.PROBE_RUNGS:
        assert sum(1 for c in cases if c.rung.id == r.id) >= 10, r.id


def test_sidekick_rungs_leave_out_the_restraint_set():
    for c in rungs.ladder_cases(("sk-l1", "sk-l2", "sk-l3")):
        assert c.sidekick_case.group != "restraint" and c.sidekick_case.core


def test_probe_instances_build_for_three_seeds():
    for c in rungs.ladder_cases(tuple(r.id for r in rungs.PROBE_RUNGS)):
        for t in (1, 2, 3):
            inst = rungs.materialize_probe(c, t, rungs.LADDER_SEED_PUBLIC)
            assert inst.grader == "probe" and inst.check["targets"] and inst.check["hidden_tests"]
            assert "test_hidden_probe" not in inst.prompt                     # the hidden tests stay hidden
            if c.rung.mode == "tool":
                assert inst.tool["kind"] == "probe" and inst.check["visible_tests"]


def test_fill_refuses_a_missing_marker():
    with pytest.raises(ValueError):
        rungs.fill("x = @A@ + @B@", A=1)


# ---------------------------------------------------------------- answer extraction

def test_extract_one_file_takes_the_first_python_block():
    files, why = extract_files("Here:\n```python\nx = 1\n```\nand\n```python\ny = 2\n```", ["a.py"])
    assert why is None and files == {"a.py": "x = 1\n"}


def test_extract_two_files_by_comment_or_label():
    ans = ("`b.py`:\n\n```python\nB = 2\n```\n\n```python\n# file: a.py\nA = 1\n```")
    files, why = extract_files(ans, ["a.py", "b.py"])
    assert why is None and files == {"a.py": "A = 1\n", "b.py": "B = 2\n"}


def test_extract_reports_a_missing_file_and_no_code():
    _, why = extract_files("```python\n# file: a.py\nA = 1\n```", ["a.py", "b.py"])
    assert "b.py" in why
    assert extract_files("I would write a loop.", ["a.py"]) == ({}, "no Python code block")


def test_thinking_is_not_taken_as_the_answer():
    ans = "<think>```python\nwrong = 1\n```</think>\n```python\nright = 1\n```"
    assert extract_files(ans, ["a.py"])[0] == {"a.py": "right = 1\n"}


# ---------------------------------------------------------------- the ceiling rule

def _rec(rung, case, trial, ok, mode=None):
    if rung.startswith("sk-"):
        facets = {"family": "shell", "category": "shell", "tags": ["core", f"level-{rung[3:]}"]}
        suite = "answer_only"
    else:
        facets = {"family": rung, "category": "ceiling", "tags": []}
        suite = "ladder_tool" if rung == "agent" else "ladder"
    return {"case_id": case, "trial": trial, "suite": suite, "status": "ok", "facets": facets,
            "metrics": {"pass": ok, "failure_mode": None if ok else (mode or "logic")}, "safety": {"labels": []}}


def _rung_records(rung, n_cases, passing_cases, trials=3):
    out = []
    for i in range(n_cases):
        for t in range(1, trials + 1):
            out.append(_rec(rung, f"{rung}-{i}", t, i < passing_cases))
    return out


def test_ceiling_and_solid():
    recs = (_rung_records("sk-l1", 4, 4) + _rung_records("sk-l2", 4, 2) + _rung_records("sk-l3", 4, 1)
            + _rung_records("rules", 4, 3))
    s = entry_summary(recs)
    assert [s["rungs"][r]["holds"] for r in ("sk-l1", "sk-l2", "sk-l3", "rules")] == [True, True, False, True]
    assert s["ceiling"] == 4 and s["solid"] == 2
    assert s["rungs"]["sk-l2"]["pass_rate"] == pytest.approx(0.5)
    assert s["rungs"]["state"]["holds"] is None                          # not run: no claim either way


def test_a_case_holds_on_a_majority_of_trials():
    recs = [_rec("rules", "c", 1, True), _rec("rules", "c", 2, False), _rec("rules", "c", 3, True),
            _rec("rules", "d", 1, True), _rec("rules", "d", 2, False), _rec("rules", "d", 3, False)]
    s = entry_summary(recs)
    assert s["rungs"]["rules"]["cases_held"] == 1 and s["rungs"]["rules"]["holds"]


def test_excluded_and_restraint_trials_are_not_rung_trials():
    recs = _rung_records("sk-l1", 2, 2)
    recs.append({**_rec("sk-l1", "x", 1, False), "status": "excluded"})
    r = _rec("sk-l1", "y", 1, False)
    r["facets"]["category"] = "restraint"
    recs.append(r)
    s = entry_summary(recs)
    assert s["rungs"]["sk-l1"]["cases"] == 2 and s["excluded"] == 1


# ---------------------------------------------------------------- plans

def test_first_pass_is_answer_only_and_samples_twelve_a_rung():
    a = plan_units("first-pass")
    assert {u.suite for u in a} <= {"answer_only", "ladder"} and all(u.trial == 1 for u in a)
    per = {}
    for u in a:
        per[u.case.rung.id] = per.get(u.case.rung.id, 0) + 1
    assert set(per) == {r.id for r in rungs.RUNGS if r.mode == "answer_only"}
    assert all(n <= 12 for n in per.values())
    b = plan_units("first-pass", done_ceiling=5)
    assert {u.case.rung.n for u in b} == {4, 5, 6} and {u.trial for u in b} == {2, 3}


def test_full_plan_covers_the_sidekick_core_set_and_the_ladder():
    units = plan_units("full")
    sk = {u.case.id for u in units if u.suite == "answer_only"}
    assert len(sk) == 112
    assert len({u.case.id for u in units if u.suite == "tool_mode"}) == 16
    assert all(n == 3 for n in __import__("collections").Counter((u.case.id, u.suite) for u in units).values())


def _tensor_file(path, tensors):
    """A minimal safetensors file: tensors = {name: (dtype, shape, nbytes)}."""
    import struct
    head, off = {}, 0
    for name, (dt, shape, n) in tensors.items():
        head[name] = {"dtype": dt, "shape": shape, "data_offsets": [off, off + n]}
        off += n
    raw = json.dumps(head).encode()
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\0" * off)


def _model_folder(root, rel, *, model_type="gemma4", bits=4, template="{{ enable_thinking }}", files=True, extra=None):
    d = root / rel
    d.mkdir(parents=True)
    cfg = {"model_type": model_type, **({"quantization": {"bits": bits, "group_size": 64}} if bits else {})}
    (d / "config.json").write_text(json.dumps(cfg))
    (d / "tokenizer.json").write_text("{}")
    if template is not None:
        (d / "chat_template.jinja").write_text(template)
    if files:
        # one packed weight of 128x512 logical parameters at `bits` (groups of 64), with its scales and biases
        rows, cols = 128, 512
        _tensor_file(d / "model.safetensors", {
            "l.weight": ("U32", [rows, cols * (bits or 16) // 32], rows * cols * (bits or 16) // 8),
            "l.scales": ("F16", [rows, cols // 64], rows * cols // 64 * 2),
            "l.biases": ("F16", [rows, cols // 64], rows * cols // 64 * 2)})
    for name, text in (extra or {}).items():
        (d / name).write_text(text)
    return d


def test_scan_finds_complete_chat_models_and_says_why_it_skips_the_rest(tmp_path):
    _model_folder(tmp_path, "org/gemma-4-E4B-it-oQ4e-mtp")
    _model_folder(tmp_path, "org/Spark-X2.6-4B-MLX-6bit", model_type="spark", bits=6)
    _model_folder(tmp_path, "org/Spark-X2.6-4B-mlx-4bit", model_type="spark", bits=4)
    _model_folder(tmp_path, "org/Qwen3-Reranker-0.6B-mlx", model_type="qwen3")
    _model_folder(tmp_path, "org/Some-9B-DFlash", model_type="qwen3")
    _model_folder(tmp_path, "org/half-done-4bit", extra={"model.safetensors.incomplete": ""})
    _model_folder(tmp_path, "org/lora-run", extra={"adapter_config.json": "{}"})
    _model_folder(tmp_path, "org/no-template-4bit", template=None)
    shards = _model_folder(tmp_path, "org/sharded-4bit", files=False)
    (shards / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": "model-00001-of-00002.safetensors"}}))
    (tmp_path / "empty-org").mkdir()
    found, skipped = models.scan(tmp_path)
    by = {m.engine_id: m for m in found}
    assert set(by) == {"gemma-4-E4B-it-oQ4e-mtp", "Spark-X2.6-4B-MLX-6bit", "Spark-X2.6-4B-mlx-4bit"}
    why = dict(skipped)
    assert "helper" in why["org/Qwen3-Reranker-0.6B-mlx"] and "helper" in why["org/Some-9B-DFlash"]
    assert "still downloading" in why["org/half-done-4bit"]
    assert "adapter" in why["org/lora-run"]
    assert "chat template" in why["org/no-template-4bit"]
    assert "missing 1 of 1 weight shards" in why["org/sharded-4bit"]
    assert "empty" in why["empty-org"]
    # quantisation: the scheme and the measured bits per weight (packed bits plus scales and biases)
    g = by["gemma-4-E4B-it-oQ4e-mtp"]
    assert g.quant["scheme"] == "oQ4e" and g.bits_per_weight == pytest.approx(4.5)
    assert by["Spark-X2.6-4B-MLX-6bit"].quant["scheme"] == "mlx-affine-6bit"
    # the same model at two quantisations: the 4-bit one is the standard entry, the other runs after
    assert by["Spark-X2.6-4B-mlx-4bit"].standard and not by["Spark-X2.6-4B-MLX-6bit"].standard
    order = models.ordered_entries(found)
    assert order[0].model.engine_id == "gemma-4-E4B-it-oQ4e-mtp" and order[0].tier == 0
    assert [e.tier for e in order] == sorted(e.tier for e in order)
    assert {e.model.engine_id for e in order if e.tier == 3} == {"Spark-X2.6-4B-MLX-6bit"}
    assert all(e.thinking == e.model.thinking[0] for e in order if e.tier == 0)       # the shipped mode first


def test_thinking_modes_come_from_the_template():
    assert models.thinking_modes("plain template", None) == (False,)
    assert models.thinking_modes("{% if enable_thinking | default(true) %}", None) == (True, False)
    assert models.thinking_modes("{% if enable_thinking | default(false) %}", None) == (False, True)
    assert models.thinking_modes("{{ enable_thinking }}", False) == (False, True)


def test_sampling_profiles_follow_the_authors():
    qwen = _model("Qwen3.5-4B-oQ4e-mtp", "qwen3_5", thinking=(True, False), label="Qwen3.5 4B", role="candidate")
    on = [e for e in qwen.entries() if e.thinking]
    assert [e.profile.name for e in on] == ["author", "coding"]
    assert on[0].profile.sampling["presence_penalty"] == 1.5 and on[1].profile.sampling == {
        **on[0].profile.sampling, "temperature": 0.6, "presence_penalty": 0.0}
    assert on[0].tier == 0 and on[1].tier == 2
    phi = _model("Phi-4-mini-instruct-oQ4e", "phi", thinking=(False,), label="Phi-4-mini", role="candidate")
    assert [(e.profile.name, e.profile.source, e.tier) for e in phi.entries()] == [
        ("greedy", "author_recommended", 0), ("fixed", "fixed_sensitivity", 2)]
    assert phi.entries()[1].profile.sampling == {"temperature": 0.6, "top_p": 0.95}
    assert _entry().key == "gemma-4-E4B-it-oQ4e-mtp|thinking-off|author"
    assert _entry().slug == "gemma-4-E4B-it-oQ4e-mtp.think-off.author"


def test_a_9b_at_8_bits_does_not_fit_16gb():
    assert not _model(gb=10.7).fits_16gb() and _model(gb=5.5).fits_16gb()


def test_entry_problems_are_reported_not_counted():
    assert "engine could not run" in _entry_problem(False, ["excluded"] * 3, [])
    no_reasoning = [{"reasoned": False}] * 3
    assert "forced off" in _entry_problem(True, ["ok"] * 3, no_reasoning)
    assert _entry_problem(False, ["ok"] * 3, no_reasoning) is None
    assert _entry_problem(True, ["ok"] * 3, [{"reasoned": True}] + no_reasoning[:2]) is None


def test_results_must_stay_outside_the_repository():
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    with pytest.raises(RuntimeError, match="outside the repository"):
        run_entry(_entry(), repo / "results", plan="smoke", client_kind="null")


def test_real_run_needs_weights(tmp_path):
    with pytest.raises(RuntimeError, match="weights"):
        run_entry(_entry(), tmp_path / "x-ceiling", plan="smoke", client_kind="omlx")


def test_lock_is_refused_when_held(tmp_path):
    lock = tmp_path / "heavy.lock"
    lock.mkdir()
    with pytest.raises(SystemExit, match="another heavy job"):
        run_entry(_entry(), tmp_path / "out", plan="smoke", client_kind="null", lock=str(lock))
    assert lock.is_dir()                                                  # not ours: left alone


def test_omlx_profile_writes_only_the_listed_fields_and_never_the_references():
    from edat.sidekick.ceiling import omlx_profile as op
    plan = op.payloads()
    assert not set(plan) & set(op.NEVER)
    assert plan["Qwen3.5-9B-oQ4e-mtp"]["enable_thinking"] is False
    assert op.payloads(qwen9_thinks=True)["Qwen3.5-9B-oQ4e-mtp"] == plan["Qwen3.5-4B-oQ4e-mtp"]
    before = {"version": 1, "models": {"gemma-4-E4B-it-oQ4e-mtp": {"temperature": 1.0, "max_context_window": 16384},
                                       "Qwen3.5-4B-oQ4e-mtp": {"top_p": 0.95, "top_k": 20, "turboquant_kv_bits": 4.0}}}
    todo = {"Qwen3.5-4B-oQ4e-mtp": plan["Qwen3.5-4B-oQ4e-mtp"]}
    rows = op.changes(before, todo)
    assert ("Qwen3.5-4B-oQ4e-mtp", "temperature", None, 1.0) in rows and not any(r[1] == "top_p" for r in rows)
    good = json.loads(json.dumps(before))
    good["models"]["Qwen3.5-4B-oQ4e-mtp"].update(todo["Qwen3.5-4B-oQ4e-mtp"])
    assert op.check_after(before, good, todo) == []
    bad = json.loads(json.dumps(good))
    bad["models"]["gemma-4-E4B-it-oQ4e-mtp"]["temperature"] = 0.7
    bad["models"]["Qwen3.5-4B-oQ4e-mtp"]["turboquant_kv_bits"] = 2.0
    probs = op.check_after(before, bad, todo)
    assert any("gemma-4-E4B-it-oQ4e-mtp" in p for p in probs) and any("turboquant_kv_bits" in p for p in probs)


def test_context_needed_covers_prompt_budget_and_tool_results():
    from edat.sidekick.ceiling.run import context_needed
    assert context_needed(False, tool_mode=False) < context_needed(True, tool_mode=False) <= 16384
    assert context_needed(True, tool_mode=True) > 16384                 # a 16k window is short only here


def test_estimate_packs_entries_into_sittings():
    from edat.sidekick.ceiling import estimate
    ms = [_model(), _model("Ornith-1.5-9B-oQ8e-mtp", "ornith", thinking=(True, False), label="Ornith", gb=10.7)]
    rows = estimate.table(models.ordered_entries(ms, tiers=(0,)), "first-pass")
    assert [e.model.engine_id for e, _ in rows] == ["gemma-4-E4B-it-oQ4e-mtp", "Ornith-1.5-9B-oQ8e-mtp"]
    assert all(h > 0 for _, h in rows)
    sits = estimate.sittings(rows, limit_h=min(h for _, h in rows) + 0.01)
    assert len(sits) == 2


# ---------------------------------------------------------------- end to end with stand-ins (sandbox)

def test_oracle_passes_the_smoke_ladder_and_null_fails_it(tmp_path, iso, private):
    e = _entry()
    lock = tmp_path / "lock"
    st = run_entry(e, tmp_path / "out", plan="smoke", client_kind="oracle", progress=None, iso=iso, lock=str(lock))
    assert not lock.exists()                                              # taken and given back
    s = st["summary"]
    assert st["stopped"] is None and st["skipped"] is None
    for r in rungs.RUNGS:
        assert s["rungs"][r.id]["pass_rate"] == 1.0, (r.id, s["rungs"][r.id])
    assert s["ceiling"] == 9 and s["solid"] == 9
    st2 = run_entry(e, tmp_path / "out", plan="smoke", client_kind="oracle", progress=None, iso=iso)
    assert st2["written"] == 0                                            # resumed: nothing to redo
    lfm = _model("LFM3-1.7B-MLX-oQ6e", "lfm3", thinking=(True, False), label="LFM3 1.7B", role="candidate", gb=1.5)
    null = run_entry(_entry(lfm, thinking=False), tmp_path / "out", plan="smoke", client_kind="null",
                     progress=None, iso=iso)
    assert null["summary"]["ceiling"] == 0
    store = tmp_path / "out" / "smoke" / e.slug
    recs = [json.loads(l) for f in store.glob("runs/*/records.jsonl") for l in f.read_text().splitlines()]
    assert recs and all(r["sample"] and r["tier"] == "sidekick" for r in recs)
    assert {r["suite"] for r in recs} == {"answer_only", "ladder", "ladder_tool"}
    assert all(r["settings"]["sampling_source"] == "author_recommended" and "sampling-author" in r["facets"]["tags"]
               and r["settings"]["sampling"] == {"temperature": 1.0, "top_p": 0.95, "top_k": 64} for r in recs)
    assert all(r["model"]["quant"]["scheme"] == "oQ4e" and r["metrics"]["bits_per_weight"] == 4.6 for r in recs)
    lrecs = [json.loads(l) for f in (tmp_path / "out" / "smoke" / _entry(lfm, thinking=False).slug).glob("runs/*/records.jsonl")
             for l in f.read_text().splitlines()]
    assert lrecs[0]["settings"]["sampling"] == {"temperature": 0.1, "top_k": 50, "repeat_penalty": 1.1}
    pg = json.loads((store / "progress.json").read_text())
    assert pg["sampling_sent"] == {"temperature": 1.0, "top_p": 0.95, "top_k": 64}


def test_run_all_skips_a_forced_off_thinking_entry_and_runs_its_off_mode(tmp_path, iso, private):
    qwen = _model("Qwen3.5-9B-oQ4e-mtp", "qwen3_5", thinking=(True, False), label="Qwen3.5 9B", role="candidate")
    log = run_all(tmp_path / "out", plan="smoke", tiers=(0,), client_kind="null", progress=None, iso=iso,
                  models=[qwen], lock=str(tmp_path / "lock"))
    rows = {r["entry"]: r for r in log["entries"]}
    on, off = "Qwen3.5-9B-oQ4e-mtp|thinking-on|author", "Qwen3.5-9B-oQ4e-mtp|thinking-off|author"
    assert rows[on]["result"] == "skipped" and "forced off" in rows[on]["why"] and rows[on]["replaced_by"] == off
    assert rows[off]["result"] == "done"
    assert not (tmp_path / "lock").exists()
    md, _ = build_report(tmp_path / "out")
    assert "## Not run" in md and "forced off" in md


def test_report_from_records(tmp_path):
    for name, recs in (("gemma-4-E4B-it-oQ4e-mtp.think-off.author", _rung_records("sk-l1", 3, 3) + _rung_records("rules", 3, 0)),
                       ("LFM3-1.7B-MLX-oQ6e.think-on.author", _rung_records("sk-l1", 3, 1))):
        d = tmp_path / name / "runs" / "r1"
        d.mkdir(parents=True)
        (d / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    md, page = build_report(tmp_path)
    assert "Gemma 4 E4B (base reference), thinking off" in md and "LFM3 1.7B, thinking on" in md
    assert "Starts failing at** rung 4" in md
    assert "| **Ceiling** | 1 | 0 |" in md or "| **Ceiling** | 0 | 1 |" in md
    assert page.startswith("<!doctype html>") and "<script" not in page
