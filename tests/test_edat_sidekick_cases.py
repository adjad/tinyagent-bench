"""Cases: counts, splits, the validator over every template, and the public export (spec 12 §3.5, S6, S7, S11)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from edat.sidekick.gen.core import (COUNTS, PUBLIC_MASTER_SEED, TOTAL_CORE, TOTAL_FULL, build_cases, counts_table,
                                    materialize)
from edat.sidekick.gen.registry import all_templates
from edat.sidekick.sandbox.run import SandboxUnavailable, SeatbeltIsolation
from edat.sidekick.splits import DEV_DIR, _slim

TEMPLATES = all_templates()
# EDAT_SIDEKICK_FULL_VALIDATE=1 checks all 3 trials of every template (slow); default is trial 1.
TRIALS = 3 if os.environ.get("EDAT_SIDEKICK_FULL_VALIDATE") else 1


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


def test_counts_match_spec():
    t = counts_table()
    for group, (full, core, tool_full, tool_core) in COUNTS.items():
        row = t[group]
        assert (row["full"], row["core"], row["tool_full"], row["tool_core"]) == (full, core, tool_full, tool_core), group
    assert t["total"]["full"] == TOTAL_FULL == 228
    assert t["total"]["core"] == TOTAL_CORE == 112
    assert (t["total"]["tool_full"], t["total"]["tool_core"]) == (32, 16)


def test_split_is_a_quarter_and_disjoint():
    cases = build_cases()
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))
    dev = [c for c in cases if c.split == "dev"]
    assert 0.24 <= len(dev) / len(cases) <= 0.27
    for group in COUNTS:
        g = [c for c in cases if c.group == group]
        assert any(c.split == "dev" for c in g) and any(c.split == "heldout" for c in g)


def test_languages_and_variants():
    cases = build_cases()
    by = lambda typ, attr: sorted({getattr(c.template, attr) for c in cases if c.type == typ})
    assert by("write_fn", "lang") == ["javascript", "python"]
    assert by("fix_bug", "lang") == ["javascript", "python"]
    assert by("regex", "variant") == ["read", "write"]
    assert by("diff_review", "variant") == ["bug", "clean", "what"]
    assert by("write_test", "variant") == ["node", "pytest"]
    restraint = {c.template.restraint for c in cases if c.group == "restraint"}
    assert restraint == {"bait", "legit", "injection", "secret"}


def test_trials_differ_and_are_deterministic():
    case = next(c for c in build_cases() if c.type == "write_fn")
    a1 = materialize(case, 1, PUBLIC_MASTER_SEED).to_dict()
    a1b = materialize(case, 1, PUBLIC_MASTER_SEED).to_dict()
    a2 = materialize(case, 2, PUBLIC_MASTER_SEED).to_dict()
    assert a1 == a1b
    assert a1 != a2
    assert materialize(case, 1, "another-seed").to_dict() != a1


def test_write_fn_has_differential_inputs():
    for c in build_cases():
        if c.type == "write_fn":
            inst = materialize(c, 1, PUBLIC_MASTER_SEED)
            assert len(inst.check["diff_inputs"]) == 200, c.id
            assert 8 <= len(inst.check["tests"]) <= 15, c.id


def test_regex_write_string_counts():
    for c in build_cases():
        if c.type == "regex" and c.template.variant == "write":
            ch = materialize(c, 1, PUBLIC_MASTER_SEED).check
            assert (len(ch["positives"]), len(ch["negatives"]), len(ch["hidden_pos"]), len(ch["hidden_neg"])) == (10, 10, 20, 20), c.id


def test_write_test_has_five_mutants():
    for c in build_cases():
        if c.type == "write_test":
            assert len(materialize(c, 1, PUBLIC_MASTER_SEED).check["mutants"]) == 5, c.id


@pytest.mark.parametrize("template", TEMPLATES, ids=[t.id for t in TEMPLATES])
def test_template_validates(template, iso):
    from edat.sidekick.validate import validate_templates
    rep = validate_templates([template], trials=TRIALS, iso=iso)
    assert rep.ok, [p.what for p in rep.problems]


def test_dev_export_matches_generator():
    files = sorted(DEV_DIR.glob("*.json"))
    assert files, "run `python -m edat.sidekick export-dev`"
    exported = {}
    for f in files:
        if f.name == "counts.json":
            continue
        for row in json.loads(f.read_text())["cases"]:
            exported[row["meta"]["id"]] = row
    dev = build_cases("dev")
    assert sorted(exported) == sorted(c.id for c in dev)
    for c in dev:
        row = exported[c.id]
        assert row["meta"] == c.meta()
        for t in (1, 2, 3):
            assert row["trials"][str(t)] == _slim(materialize(c, t, PUBLIC_MASTER_SEED).to_dict()), (c.id, t)


def test_no_private_material_in_fixtures():
    root = Path(__file__).resolve().parents[1] / "test_fixtures" / "sidekick"
    heldout = {c.id for c in build_cases("heldout")}
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        text = f.read_text(errors="replace")
        assert not re.search(r"/Users/[A-Za-z]", text), f
        assert "edge-bench-private" not in text, f
        for cid in re.findall(r'"id": "(sk_[^"]+)"', text):
            assert cid not in heldout, (f, cid)
