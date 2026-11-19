"""Media selection tiers (spec 05 §17): manifests, nested sampling, id lists, stamps, hours."""
from __future__ import annotations

import json
from collections import Counter

import pytest

from edat.media import tiers as T


@pytest.fixture(scope="module")
def manifests():
    return {t: T.load_manifest(t) for t in T.TIERS}


def test_manifests_load_and_nest(manifests):
    assert T.DEFAULT_TIER == "core"
    for t, m in manifests.items():
        assert m.tier == t and m.seed == 20261119 and len(m.sha256) == 64
    assert T.check_nesting(manifests["core"], manifests["extended"]) == []
    assert T.check_nesting(manifests["extended"], manifests["full"]) == []
    assert manifests["core"]["thinking_entries"] == ["off"]


def test_check_nesting_reports_a_larger_count(manifests):
    core = manifests["core"]
    bigger = T.Manifest("core", dict(core.data, v3={"n": 5000}), core.sha256, core.path)
    assert any("v3" in p for p in T.check_nesting(bigger, manifests["extended"]))


def test_count_reads_all_off_and_numbers():
    assert T.count("all", 7) == 7 and T.count("on", 7) == 7
    assert T.count("off", 7) == 0 and T.count(None, 7) == 0 and T.count(3, 7) == 3
    with pytest.raises(ValueError):
        T.count(8, 7)


def listing(n=600, strata=12):
    return [(f"utt-{i:05d}", f"spk-{i % strata:02d}") for i in range(n)]


def test_nested_order_is_nested_proportional_and_deterministic():
    items = listing()
    order = T.nested_order(items, seed=1, label="x")
    assert sorted(order) == sorted(k for k, _ in items)
    assert order == T.nested_order(list(reversed(items)), seed=1, label="x")
    assert order != T.nested_order(items, seed=2, label="x")
    small, large = T.take(items, 60, seed=1, label="x"), T.take(items, 240, seed=1, label="x")
    assert large[:60] == small
    stratum = dict(items)
    per = Counter(stratum[k] for k in small)
    assert set(per.values()) == {5}          # 60 over 12 equal strata


def test_nested_order_keeps_unequal_strata_in_proportion():
    items = [(f"a{i}", "a") for i in range(300)] + [(f"b{i}", "b") for i in range(100)]
    got = Counter(k[0] for k in T.take(items, 40, seed=3, label="y"))
    assert got == {"a": 30, "b": 10}


def test_given_order_keeps_each_stratum_in_order():
    items = [((f, j), f) for f in "pq" for j in range(10)]
    picked = T.take(items, 6, seed=1, label="z", within="given")
    for f in "pq":
        js = [j for g, j in picked if g == f]
        assert js == list(range(len(js)))


def test_sample_ids_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "IDS_DIR", tmp_path)
    src = tmp_path / "listing.tsv"
    src.write_text("".join(f"{k}\t{s}\n" for k, s in listing(3000, 40)))
    rows = T.read_listing(src)
    core = T.sample_ids("librispeech_clean", rows, "core")
    ext = T.sample_ids("librispeech_clean", rows, "extended")
    assert len(core) == 150 and len(ext) == 600 and ext[:150] == core
    path = T.write_id_list("librispeech_clean", rows, "core")
    assert path.parent == tmp_path and T.read_id_list("librispeech_clean", "core") == core
    assert T.read_id_list("librispeech_other", "core") is None


def test_half_width():
    assert T.half_width(0) is None
    assert T.half_width(100) == pytest.approx(9.8, abs=0.05)
    assert T.half_width(150) == pytest.approx(8.0, abs=0.05)


def test_core_hours_follow_the_pace_assumptions():
    h = T.estimate_hours("core")
    assert h["trials"] == 3
    assert h["hours_per_model"] == pytest.approx(7.86, abs=0.05)
    assert h["hours_per_model"] < T.estimate_hours("extended")["hours_per_model"]


def test_question_order_is_a_prefix_order():
    qids = [f"q{i}" for i in range(4)]
    order = T.question_order(qids, seed=1, item_id="i")
    assert sorted(order) == qids and order == T.question_order(list(reversed(qids)), seed=1, item_id="i")


def stamped_records():
    run = T.TierRun("core", "a" * 64, {"v1/charts": 50, "v1": 550, "a3": 195})
    return run, [{"metrics": run.stamp("v1", "charts")}, {"metrics": run.stamp("a3", None)}]


def test_stamps_carry_tier_n_and_interval():
    run, (a, b) = stamped_records()
    assert a["metrics"] == {"media_tier": "core", "manifest_sha256": "a" * 64, "tier_n": 50,
                            "ci95_half_width_pts": T.half_width(50)}
    assert b["metrics"]["tier_n"] == 195
    assert T.assert_one_tier([a, b]) == "core"
    other = {"metrics": T.TierRun("extended", "b" * 64, {}).stamp("v1", None)}
    with pytest.raises(ValueError):
        T.assert_one_tier([a, other])


def test_runner_stamps_pass_the_results_schema():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from edat.media.runner import RunConfig, base_record
    from edat.results.schema import validate_record
    run, _ = stamped_records()
    cfg = RunConfig(model={"id": "stub", "role": "candidate", "weights_fingerprint": "0" * 64},
                    runtime={"name": "stub", "version": "0"}, thinking=False,
                    board_version="2027.1", harness_commit="abc1234",
                    run_id="2027-01-01T00-00-00Z-0000", tier_run=run,
                    device={"id": "mac-ref", "class": "mac-ref", "ram_bytes": 1, "os": "test"})
    rec = base_record(cfg, tier="vision", suite="v1", case_id="c", trial=1, status="ok",
                      facets={"family": "v1", "category": "charts", "tags": ["bar"]},
                      metrics={"pass": True, "score": 1.0})
    validate_record(rec)
    assert rec["metrics"]["tier_n"] == 50 and "tier-core" in rec["facets"]["tags"]


# ---------------------------------------------------------------- selections (need the generators)

@pytest.fixture(scope="module")
def selections():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    return {t: T.select(t) for t in ("core", "extended")}


def test_committed_selections_match_the_sampler(selections):
    for tier, sel in selections.items():
        committed = json.loads(T.selection_path(tier).read_text())
        assert committed == json.loads(json.dumps(T.generated_selection(sel))), tier


def test_core_is_inside_extended(selections):
    c, e = selections["core"], selections["extended"]
    for fam, s in c.vision["v1"].items():
        assert e.vision["v1"][fam]["indices"][:len(s["indices"])] == s["indices"]
    assert e.vision["v3"][:len(c.vision["v3"])] == c.vision["v3"]
    for skill, idx in c.vision["v7"].items():
        assert set(idx) <= set(e.vision["v7"].get(skill, []))
    assert set(c.vision["v5"]["conditions"]) <= set(e.vision["v5"]["conditions"])
    for key in ("singles", "followups", "corrections", "disfluency"):
        assert set(c.audio["a3"][key]) <= set(e.audio["a3"][key]), key
    assert set(c.audio["a5"]["non_speech"]) <= set(e.audio["a5"]["non_speech"])
    assert set(c.audio["a7"]["conversations"]) <= set(e.audio["a7"]["conversations"])


def test_core_group_sizes(selections):
    g = T.group_sizes(selections["core"])
    assert g["v1/charts"] == 50 and g["v3"] == 80 and g["v5/dark"] == 40
    assert g["a3/single"] == 150 and g["a3/noise_snr10"] == 40 and g["a5/non_speech"] == 100
    assert set(selections["core"].pending) >= {"librispeech_clean", "ocrbench", "cremad"}


def test_a3_and_a4_plans_follow_the_tier(selections):
    sel = selections["core"]
    clips = T.a3_clips(sel)
    fams = Counter(c.family for c in clips)
    assert fams["single"] == 150 and fams["noisy"] == 40 and "voice_variance" not in fams
    assert {c.condition["snr_db"] for c in clips if c.family == "noisy"} == {10}
    rows = T.a4_plan(sel, ["u1", "u2"])
    assert len(rows) == 2 * 7
    with pytest.raises(LookupError):
        T.a4_plan(sel)                        # the test-clean id list is not written yet


def test_v5_tier_cuts_conditions_and_questions():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from edat.media.vision import robustness as r
    fam = r.BASE_FAMILIES[0]
    index, full = next(r.base_indices(fam, split="dev", count=1))
    cut = r.build_base(fam, index, split="dev", conditions=["dark", "crop_cut"],
                       questions_per_image=1)
    assert sorted(m["condition"] for m, _ in cut) == ["crop_cut", "dark"]
    assert all(len(m["questions"]) == 1 for m, _ in cut)
    crop = next(m for m, _ in cut if m["condition"] == "crop_cut")
    full_crop = next(m for m, _ in full if m["condition"] == "crop_cut")
    if any("cut_from" in q for q in full_crop["questions"]):
        assert "cut_from" in crop["questions"][0]
    with pytest.raises(ValueError):
        r.build_base(fam, index, split="dev", conditions=["nope"])


def test_v1_tier_keeps_a_question_prefix():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from edat.media.vision.generate import build, tier_questions
    meta, _, _ = build("tables", 0, split="dev")
    two = tier_questions(meta, questions_per_image=2, reading=False, seed=1)
    four = tier_questions(meta, questions_per_image=4, reading=True, seed=1)
    assert len(two["questions"]) == 2 and all(q["kind"] != "reading" for q in two["questions"])
    assert {q["qid"] for q in two["questions"]} <= {q["qid"] for q in four["questions"]}
    assert any(q["kind"] == "reading" for q in four["questions"])
