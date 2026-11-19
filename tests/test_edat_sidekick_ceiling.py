"""The coding-ceiling ladder: rungs, the probe grader, the ceiling rule, the run driver and the report."""
from __future__ import annotations

import json

import pytest

from edat.sidekick.ceiling import models, rungs
from edat.sidekick.ceiling.probe import extract_files
from edat.sidekick.ceiling.report import build_report, entry_summary
from edat.sidekick.ceiling.run import Quick, _entry_problem, parse_rungs, plan_units, quick_entries, run_all, run_entry
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


def test_git_tasks_on_rungs_1_to_3_are_left_out_of_the_hardened_ceiling():
    """Rung 2 holds only through its git cases: it holds, but not on the hardened count."""
    recs = _rung_records("sk-l1", 2, 2) + _rung_records("sk-l2", 2, 0)
    for i in range(2):
        for t in (1, 2, 3):
            r = _rec("sk-l2", f"git-{i}", t, True)
            r["facets"] = {"family": "git", "category": "git", "tags": ["core", "level-l2", "grade-not-hardened"]}
            recs.append(r)
    s = entry_summary(recs)
    assert s["rungs"]["sk-l2"]["holds"] and s["ceiling"] == 2
    assert s["hardened_ceiling"] == 1


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


def test_quick_plan_takes_its_choices_as_flags():
    units = plan_units("quick")
    assert len(units) == 8 * 4 and {u.trial for u in units} == {1}
    assert {u.suite for u in units} <= {"answer_only", "ladder"}
    q = Quick(per_rung=3, rungs=parse_rungs("1-3,5"), thinking="shipped")
    assert sorted({u.case.rung.n for u in plan_units("quick", quick=q)}) == [1, 2, 3, 5]
    assert len(plan_units("quick", quick=q)) == 12
    assert parse_rungs("4-6,8,1") == (1, 4, 5, 6, 8)
    for bad in (dict(rungs=(10,)), dict(rungs=()), dict(per_rung=0), dict(thinking="on")):
        with pytest.raises(ValueError):
            Quick(**bad)


def test_quick_entries_force_thinking_off_or_keep_the_shipped_mode():
    lfm = _model("LFM3-1.7B-MLX-oQ6e", "lfm3", thinking=(True, False), label="LFM3 1.7B", role="candidate", gb=1.5)
    six = _model("Spark-X2.6-4B-MLX-6bit", "spark2_5", thinking=(True, False), standard=False, label="Spark", gb=3.3)
    ms = [lfm, _model(), six]
    off = quick_entries(ms, Quick(thinking="off"))
    assert [e.model.engine_id for e in off] == ["gemma-4-E4B-it-oQ4e-mtp", "LFM3-1.7B-MLX-oQ6e"]   # reference first
    assert all(not e.thinking and e.primary for e in off)
    shipped = quick_entries(ms, Quick(thinking="shipped"))
    assert [e.thinking for e in shipped] == [False, True]


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
    md, _ = build_report(tmp_path / "out" / "smoke")                 # smoke results and their log sit apart
    assert "## Not run" in md and "forced off" in md


def test_quick_look_runs_apart_and_its_report_says_what_it_cannot_tell(tmp_path, iso, private):
    from edat.sidekick.ceiling import estimate
    lfm = _model("LFM3-1.7B-MLX-oQ6e", "lfm3", thinking=(True, False), label="LFM3 1.7B", role="candidate", gb=1.5)
    q = Quick(per_rung=1, rungs=(1, 4), thinking="off")
    log = run_all(tmp_path / "out", plan="quick", quick=q, client_kind="oracle", progress=None, iso=iso,
                  models=[_model(), lfm])
    assert [r["result"] for r in log["entries"]] == ["done", "done"] and log["quick"]["per_rung"] == 1
    qdir = tmp_path / "out" / "quick"
    assert (qdir / "run-all.json").exists() and not (tmp_path / "out" / "run-all.json").exists()
    pg = json.loads((qdir / "LFM3-1.7B-MLX-oQ6e.think-off.author" / "progress.json").read_text())
    assert pg["quick"]["rungs"] == [1, 4] and pg["ships_thinking"] is True and pg["summary"]["ceiling"] == 4
    md, _ = build_report(qdir)
    assert "What this quick look cannot tell" in md and "good to about one rung" in md
    assert "likely understated: LFM3 1.7B" in md and "No tool mode" in md and "No safety set" in md
    assert "Rungs left out:** 2 (" in md and "9 (" not in md.split("Rungs left out")[1].split("\n")[0]
    # quick-look wording: plain about its limits, counts not shares, a capped ceiling
    assert md.split("\n")[2].startswith("**A quick look with wide error bars, not a ranking.**")
    assert "Passed in this look** rungs 1 to 1" in md and "Reliably does" not in md
    assert "**Ceiling:** rung 4 or higher (rungs 5-9 not run; a pass higher up than its solid range); on the hardened rungs 1 to 3 (git tasks left out) it is rung " in md
    assert "the top rung was not run" in md
    table_part = md.split("## Rung by model")[1].split("## Not run")[0]
    assert "tasks solved, one try each" in table_part and "not ranked" in table_part
    assert "1/1 ✓" in table_part and "%" not in table_part and "| **Ceiling** | 4+ | 4+ |" in table_part
    rows = estimate.table(quick_entries([_model(), lfm], q), "quick", quick=q)
    assert len(rows) == 2 and all(0 < h < 0.2 for _, h in rows)


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


def test_quick_takes_rung_9_only_when_asked():
    assert Quick().rungs == tuple(range(1, 9)) and Quick().to_dict()["mode"] == "answer_only"
    q = Quick(rungs=parse_rungs("1-9"))
    assert q.rungs == tuple(range(1, 10)) and q.to_dict()["mode"] == "answer_only+tool" and q.tool_rungs() == (9,)
    tool = [u for u in plan_units("quick", quick=Quick(per_rung=3, rungs=parse_rungs("1-9"))) if u.case.rung.n == 9]
    assert len(tool) == 3 and {u.suite for u in tool} == {"ladder_tool"} and {u.trial for u in tool} == {1}
    with pytest.raises(ValueError):
        Quick(rungs=(10,))
    from edat.sidekick.ceiling import estimate
    m = _model()
    with_9 = estimate.seconds(m, False, "quick", quick=Quick(rungs=parse_rungs("1-9")))
    assert with_9 > estimate.seconds(m, False, "quick", quick=Quick())


def test_quick_look_with_rung_9_runs_tool_mode_and_says_so(tmp_path, iso, private):
    q = Quick(per_rung=1, rungs=(1, 9), thinking="off")
    log = run_all(tmp_path / "out", plan="quick", quick=q, client_kind="oracle", progress=None, iso=iso,
                  models=[_model()])
    assert [r["result"] for r in log["entries"]] == ["done"]
    md, _ = build_report(tmp_path / "out" / "quick")
    assert "Tool mode on a single try" in md and "the top rung was not run" not in md
    assert "**Ceiling:** rung 9 (a pass higher up than its solid range); on the hardened rungs 1 to 3 (git tasks left out) it is rung " in md and "or higher" not in md


class _Unservable:
    """Every request fails the way an engine without a loader for the model does."""

    def chat(self, messages, **kw):
        from edat.models import EngineError
        raise EngineError("server", "no loader for model type", status=500, retryable=False)


def test_run_all_skips_a_model_the_engine_cannot_serve_and_goes_on(tmp_path, iso, private, monkeypatch):
    from edat.sidekick.ceiling import run as run_mod
    real = run_mod.make_client
    bad = "SenseNova-Loop-9B-MLX-4bit"
    monkeypatch.setattr(run_mod, "make_client",
                        lambda kind, model, base_url, thinking: _Unservable() if model.engine_id == bad
                        else real(kind, model, base_url, thinking))
    sense = _model(bad, "sensenova", thinking=(False,), label="SenseNova-Loop 9B", role="candidate")
    q = Quick(per_rung=1, rungs=(1, 2, 3), thinking="shipped")
    log = run_all(tmp_path / "out", plan="quick", quick=q, client_kind="oracle", progress=None, iso=iso,
                  models=[sense, _model()], lock=str(tmp_path / "lock"))
    rows = {r["entry"].split("|")[0]: r for r in log["entries"]}
    assert rows[bad]["result"] == "skipped" and "engine could not run this model" in rows[bad]["why"]
    assert rows["gemma-4-E4B-it-oQ4e-mtp"]["result"] == "done"
    assert not (tmp_path / "lock").exists()
    recs = [json.loads(l) for f in (tmp_path / "out" / "quick").glob(f"{bad}.*/runs/*/records.jsonl")
            for l in f.read_text().splitlines() if l.strip()]
    assert len(recs) == 3 and all(r["status"] == "excluded" for r in recs)   # 3 faults, then it is skipped
    md, _ = build_report(tmp_path / "out" / "quick")
    assert "## Not run" in md and "engine could not run this model" in md.split("## Not run")[1]


def test_a_card_default_that_differs_from_the_template_is_recorded(tmp_path, iso, private):
    _model_folder(tmp_path, "org/NVIDIA-Nemotron-3.5-Bolt-E4B-MLX-oQ4e-mtp",
                  template="{%- set enable_thinking = enable_thinking | default(false) -%}{{ enable_thinking }}")
    _model_folder(tmp_path, "org/gemma-4-E2B-it-oQ4e-mtp",
                  template="{%- set enable_thinking = enable_thinking | default(false) -%}")
    found, _ = models.scan(tmp_path)
    by = {m.engine_id: m for m in found}
    nem = by["NVIDIA-Nemotron-3.5-Bolt-E4B-MLX-oQ4e-mtp"]
    assert nem.thinking == (True, False) and nem.template_thinking is False
    assert "ships with thinking on per its model card; its chat template defaults to off" in nem.notes
    gem = by["gemma-4-E2B-it-oQ4e-mtp"]
    assert gem.thinking == (False, True) and gem.template_thinking is False and "chat template" not in gem.notes
    run_all(tmp_path / "out", plan="quick", quick=Quick(per_rung=1, rungs=(1,), thinking="shipped"),
            client_kind="oracle", progress=None, iso=iso, models=[nem])
    pg = json.loads(next((tmp_path / "out" / "quick").glob("NVIDIA-*/progress.json")).read_text())
    assert pg["ships_thinking"] is True and pg["template_thinking_default"] is False


def test_rung_9_grading_never_reads_through_a_planted_link(tmp_path, iso, monkeypatch):
    from edat.sidekick.ceiling import agent
    from edat.sidekick.ceiling.rungs import LADDER_SEED_PUBLIC, ladder_cases, materialize_probe
    from edat.sidekick.tool_mode import ToolTrial
    inst = materialize_probe(ladder_cases(("agent",))[0], 1, LADDER_SEED_PUBLIC)
    secret = tmp_path / "host-secret.py"
    secret.write_text("EDATHOSTSECRET = 1\n")

    def planted(client, inst, env, **kw):                  # the session ends with every target a link out
        for t in inst.check["targets"]:
            p = env.area.work / t
            p.unlink(missing_ok=True)
            p.symlink_to(secret)
        return ToolTrial(final_text="Done.")

    seen = []
    real_probe = agent.run_probe
    monkeypatch.setattr(agent, "run_tool_trial", planted)
    monkeypatch.setattr(agent, "run_probe", lambda iso_, area, check, final: seen.append(dict(final))
                        or real_probe(iso_, area, check, final))
    g, _, _ = agent.run_agent_trial(None, inst, iso)
    assert seen == [{}] and not g.passed                   # nothing read through the links
    assert "EDATHOSTSECRET" not in json.dumps(g.to_record(), default=str)
    assert g.metrics["edited"] is False and g.metrics["failure_mode"] == "unsafe"
    assert {(e.kind, e.rule) for e in g.events} == {("unsafe_action", "planted_link")}


def test_run_all_never_unloads_while_harbor_is_open(tmp_path, monkeypatch):
    from edat.sidekick.ceiling import run as run_mod
    from scripts.productbench import engine_prep
    keeps = []
    monkeypatch.setattr(engine_prep, "main", lambda argv: keeps.append(argv[1]) or 0)
    monkeypatch.setattr(run_mod, "weights_for", lambda out, mdir, m: "0" * 64)
    monkeypatch.setattr(run_mod, "_run_entry", lambda e, out, **kw: {"skipped": None, "stopped": None,
                                                                      "summary": {"ceiling": 1, "solid": 1}})
    two = [_model(), _model("gemma-4-E2B-it-oQ4e-mtp", label="Gemma 4 E2B", role="candidate", gb=3.9)]
    monkeypatch.setattr(run_mod, "harbor_running", lambda: True)
    with pytest.raises(SystemExit, match="Harbor is running"):
        run_all(tmp_path / "out", plan="quick", client_kind="omlx", progress=None, models=two,
                lock=str(tmp_path / "lock"))
    assert keeps == [] and not (tmp_path / "lock").exists()
    seen = iter([False, False, True])                      # closed at the start and for the first model only
    monkeypatch.setattr(run_mod, "harbor_running", lambda: next(seen))
    log = run_all(tmp_path / "out", plan="quick", client_kind="omlx", progress=None, models=two,
                  lock=str(tmp_path / "lock"))
    assert keeps == ["gemma-4-E4B-it-oQ4e-mtp"]
    assert [r["result"] for r in log["entries"]] == ["done", "not started (stopped)"]
    assert "Harbor is running" in log["entries"][1]["why"] and not (tmp_path / "lock").exists()


def test_first_pass_repeats_stay_on_pass_a_rungs():
    from edat.sidekick.ceiling.run import pass_a_ceiling
    assert sorted({u.case.rung.n for u in plan_units("first-pass", done_ceiling=0)}) == [1, 2]
    assert sorted({u.case.rung.n for u in plan_units("first-pass", done_ceiling=3)}) == [2, 3, 4]
    recs = (_rung_records("sk-l1", 3, 3, trials=1) + _rung_records("sk-l2", 3, 3, trials=1)
            + _rung_records("sk-l3", 3, 0, trials=1))
    repeats = [dict(r, trial=t) for r in _rung_records("sk-l3", 3, 3, trials=1) for t in (2, 3)]
    assert entry_summary(recs + repeats)["ceiling"] == 3            # all trials together would move it
    assert pass_a_ceiling(recs + repeats) == pass_a_ceiling(recs) == 2


def test_resume_reuses_a_trial_only_from_the_same_plan_and_ladder_split(tmp_path):
    from edat.sidekick.ceiling.run import _done_units, _unit_split, results_dir
    assert {results_dir(tmp_path, p) for p in ("smoke", "quick", "full")} == \
        {tmp_path / "smoke", tmp_path / "quick", tmp_path / "full"} and results_dir(tmp_path, "first-pass") == tmp_path
    d = tmp_path / "e" / "runs" / "r1"
    d.mkdir(parents=True)
    u = next(u for u in plan_units("quick") if u.case.sidekick_case is None)
    d.joinpath("records.jsonl").write_text(json.dumps({"suite": u.suite, "case_id": u.case.id, "trial": 1,
                                                        "split": "dev"}) + "\n")
    done = _done_units(tmp_path / "e")
    assert (u.case.id, 1, _unit_split(u, "dev")) in done[u.suite]
    assert (u.case.id, 1, _unit_split(u, "heldout")) not in done[u.suite]   # a private-seed run redoes it


def test_omlx_profile_stops_on_a_refusal_and_still_reads_back(tmp_path, monkeypatch, capsys):
    from edat.sidekick.ceiling import omlx_profile as op
    from scripts.productbench import engine_prep
    f = tmp_path / "model_settings.json"
    f.write_text(json.dumps({"version": 1, "models": {"gemma-4-E4B-it-oQ4e-mtp": {"temperature": 1.0}}}))
    order = list(op.payloads())

    class Engine:                                          # merges sent fields like oMLX; refuses the second model
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return type("R", (), {"json": lambda self: {"data": [{"id": m} for m in order]}})()

        def put(self, url, json):
            mid = url.split("/models/")[1].split("/")[0]
            if mid == order[1]:
                return type("R", (), {"status_code": 500, "text": "no"})()
            data = __import__("json").loads(f.read_text())
            data["models"].setdefault(mid, {}).update(json)
            f.write_text(__import__("json").dumps(data))
            return type("R", (), {"status_code": 200, "text": ""})()

    monkeypatch.setattr(engine_prep, "_client", lambda: Engine())
    assert op.main(["--apply", "--settings", str(f)]) == 2
    err = capsys.readouterr()
    assert f"stopped after 1 of {len(order)} models" in err.err and f"already written: {order[0]}" in err.err
    assert "nothing else changed" in err.out and list(tmp_path.glob("model_settings.json.bak-sampling-*"))


def test_an_error_in_grading_fails_the_trial_and_the_run_goes_on(monkeypatch):
    from edat.models import NullClient
    from edat.sidekick.ceiling import run as run_mod
    from edat.sidekick.sandbox.run import SandboxUnavailable
    inst = materialize_probe_inst()

    def broken(*a, **k):
        raise ValueError("cwd must be inside the trial folder")
    monkeypatch.setattr(run_mod, "grade_answer", broken)
    m, raw, events, status, _ = run_mod.answer_trial(NullClient(), inst, suite="ladder", seed=1, thinking=False,
                                                     sampling=None, iso=None)
    assert status == "ok" and m["pass"] is False and m["failure_mode"] == "grader_error"
    assert "could not finish" in raw["grade"]["reasons"][0]

    def no_sandbox(*a, **k):
        raise SandboxUnavailable("gone")
    monkeypatch.setattr(run_mod, "grade_answer", no_sandbox)
    with pytest.raises(SandboxUnavailable):                     # a broken sandbox still stops everything
        run_mod.answer_trial(NullClient(), inst, suite="ladder", seed=1, thinking=False, sampling=None, iso=None)


def materialize_probe_inst():
    from edat.sidekick.ceiling.rungs import LADDER_SEED_PUBLIC, ladder_cases, materialize_probe
    return materialize_probe(ladder_cases(("rules",))[0], 1, LADDER_SEED_PUBLIC)


def test_a_replaced_private_seed_never_resumes_an_old_results_folder(tmp_path, iso, private):
    from edat.sidekick.ceiling.run import bind_seeds
    bind_seeds(tmp_path / "f", {"ladder": "heldout:aaa"})
    bind_seeds(tmp_path / "f", {"ladder": "heldout:aaa"})                       # the same seeds resume
    with pytest.raises(SystemExit, match="other seeds"):
        bind_seeds(tmp_path / "f", {"ladder": "heldout:bbb"})
    q = Quick(per_rung=1, rungs=(4,), thinking="off")
    run_all(tmp_path / "out", plan="quick", quick=q, client_kind="oracle", progress=None, iso=iso, models=[_model()])
    seeds = json.loads((tmp_path / "out" / "quick" / "seeds.json").read_text())
    assert seeds["ladder"].startswith("heldout:")
    n = len(list((tmp_path / "out" / "quick").rglob("records.jsonl")))
    (private / "sidekick" / "ceiling_seed").write_text("a-replaced-seed\n")
    with pytest.raises(SystemExit, match="other seeds"):
        run_all(tmp_path / "out", plan="quick", quick=q, client_kind="oracle", progress=None, iso=iso,
                models=[_model()], lock=str(tmp_path / "lock"))
    assert len(list((tmp_path / "out" / "quick").rglob("records.jsonl"))) == n and not (tmp_path / "lock").exists()
