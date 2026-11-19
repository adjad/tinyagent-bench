"""Hand-made record sets through edat.results.aggregate."""
from __future__ import annotations

import hashlib

import pytest

from edat.results import keys, stats
from edat.results.aggregate import Options, aggregate
from edat.results.schema import record_errors

TUNED = "Gemma-Wisp"
BASE = "gemma-4-E4B-it-oQ4e-mtp"
OPTS = Options(resamples=2000, facet_resamples=500)


def make_record(**overrides) -> dict:
    """A valid local `agentic` record. Dict overrides merge one level deep."""
    rec = {
        "schema": "edat.result/1.0", "kind": "local", "tier": "agentic", "suite": "reference-loop",
        "run_id": "run-a", "board_version": "2027.1", "lang": "en",
        "model": {"id": "cand", "role": "candidate", "weights_sha256": hashlib.sha256(b"cand").hexdigest(),
                  "quant": {"scheme": "q4"}},
        "runtime": {"name": "omlx", "version": "0.7.3"},
        "device": {"id": "mac-ref", "class": "mac-ref", "ram_bytes": 25769803776, "os": "macOS 27.3"},
        "settings": {"thinking": "off", "sampling": {}, "sampling_source": "author_recommended", "seed": 1},
        "harness_commit": "90e3e9f", "started_at": "2026-12-01T10:15:00Z",
        "case_id": "c1", "split": "dev", "trial": 1, "status": "ok",
        "facets": {"lane": "assistant", "family": "select"},
        "metrics": {"pass": True},
        "safety": {"unsafe_actions": 0, "privacy_leaks": 0},
        "raw": None, "raw_reason": "not_captured",
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(rec.get(key), dict):
            rec[key] = {**rec[key], **value}
        else:
            rec[key] = value
    return rec


def rec(model="cand", case="c1", trial=1, passed=True, *, role=None, thinking="off", family="select",
        lane="assistant", tier="agentic", device="mac-ref", status="ok", commit="90e3e9f", board="2027.1",
        source="author_recommended", quant="q4", score=None, unsafe=0, leaks=0, sample=False,
        surface=None, run_id="run-a", extra=None, weights=None) -> dict:
    metrics = {}
    if passed is not None:
        metrics["pass"] = passed
    if score is not None:
        metrics["score"] = score
    metrics.update(extra or {})
    if tier == "sidekick":                                  # as the runner marks a graded row
        metrics.setdefault("grade_hardened", True)
        metrics.setdefault("grading", {"write_fn": "code", "fix_bug": "code", "shell": "shell", "regex": "regex_write"}.get(family, family))
    facets = {"family": family, "lane": lane}
    if surface:
        facets = {"surface": surface, "profile": "student"}
    r = make_record(
        tier=tier, run_id=run_id, board_version=board, harness_commit=commit, case_id=case, trial=trial,
        status=status, metrics=metrics, facets=facets,
        model={"id": model, "role": role or ("baseline" if model == TUNED else
                                              "reference" if model == BASE else "candidate"),
               "weights_sha256": weights or hashlib.sha256(model.encode()).hexdigest(),
               "quant": {"scheme": quant}},
        settings={"thinking": thinking, "sampling_source": source},
        device={"id": device, "class": device},
        safety={"unsafe_actions": unsafe, "privacy_leaks": leaks})
    r["facets"] = facets
    r["metrics"] = metrics          # make_record merges dicts; the helper wants exactly these
    if sample:
        r["sample"] = True
    return r


def fit(model="cand", verdict="fits", quant="q4", commit="90e3e9f", board="2027.1", extra=None) -> dict:
    metrics = {"verdict": verdict, **(extra or {})}
    r = rec(model, "fit/16gb_mac", 1, passed=None, tier="perf", quant=quant, commit=commit, board=board,
            extra=metrics, run_id="run-perf")
    r["facets"] = {"test": "fit16_verdict"}
    return r


def cloud(model="vendor/model-c", case="c1", trial=1, passed=True, *, resolved=None, commit="90e3e9f",
          status_detail=None, provider="provider-p", family="select", lane="assistant") -> dict:
    r = make_record(
        kind="cloud_reference", device=None, device_reason="cloud", case_id=case, trial=trial,
        harness_commit=commit, metrics={"pass": passed},
        model={"id": model, "role": "cloud_reference", "weights_sha256": None, "quant": None,
               "requested_model": model, "resolved_model": resolved or model, "upstream_provider": provider,
               "run_date": {"first": "2026-12-01T10:00:00Z", "last": "2026-12-01T11:00:00Z"}},
        runtime={"name": "openrouter"}, settings={"sampling_source": "provider_default"})
    r["facets"] = {"family": family, "lane": lane}
    if status_detail:
        r["status_detail"] = status_detail
    return r


def entry(result, model="cand", thinking="off", device=None, kind="local"):
    device = device or ("cloud" if kind == "cloud_reference" else "mac-ref")
    found = [e for e in result["entries"] if e["model_id"] == model and e["thinking"] == thinking
             and e["device_id"] == device and e["kind"] == kind]
    assert len(found) == 1, [e["entry_key"] for e in result["entries"]]
    return found[0]


def cases(n, passes, *, prefix="c", **kw):
    """n single-trial records, the first `passes` of them passing."""
    return [rec(case=f"{prefix}{i:02d}", passed=i < passes, **kw) for i in range(n)]


def agg(records):
    return aggregate(records, OPTS)


# --- fixtures sanity --------------------------------------------------------

def test_helpers_build_valid_records():
    for r in (rec(), fit(), cloud(), rec(surface="ask", tier="in_assistant", passed=None, score=0.5)):
        assert record_errors(r) == [], r


def test_empty_input():
    assert aggregate([]) == {"board_version": None, "entries": [], "references": {}, "warnings": []}


# --- pass1, intervals -------------------------------------------------------

def test_pass1_is_mean_of_per_case_means_not_pooled():
    records = [rec(case="a", trial=1, passed=True), rec(case="a", trial=2, passed=True),
               rec(case="a", trial=3, passed=False), rec(case="b", trial=1, passed=True)]
    block = entry(agg(records))["tiers"]["agentic"]
    assert block["pass1"]["value"] == pytest.approx((2 / 3 + 1.0) / 2)
    assert block["pass1"]["value"] != pytest.approx(3 / 4)
    assert block["pass1"]["n_cases"] == 2
    assert block["pass1"]["n_trials"] == 4


def test_wilson_interval_uses_passes_over_graded_trials():
    records = [rec(case="a", trial=1, passed=True), rec(case="a", trial=2, passed=True),
               rec(case="a", trial=3, passed=False), rec(case="b", trial=1, passed=True)]
    pass1 = entry(agg(records))["tiers"]["agentic"]["pass1"]
    assert pass1["interval_kind"] == "wilson"
    assert pass1["passes"] == 3
    assert pass1["interval"] == pytest.approx(list(stats.wilson(3, 4)))
    assert pass1["interval_cluster"] is not None


def test_pass_all_trials_and_pass_k_blocks():
    records = [rec(case="a", trial=t, passed=p) for t, p in ((1, True), (2, True), (3, False))]
    records += [rec(case="b", trial=t, passed=True) for t in (1, 2, 3)]
    pass1 = entry(agg(records))["tiers"]["agentic"]["pass1"]
    assert pass1["pass_all_trials"]["value"] == 0.5
    assert pass1["pass_all_trials"]["flag"] is None
    assert pass1["pass_k"]["2"] == pytest.approx((1 / 3 + 1) / 2)
    assert pass1["pass_k"]["3"] == pytest.approx(0.5)


def test_unequal_trials_flagged():
    records = [rec(case="a", trial=1), rec(case="a", trial=2), rec(case="a", trial=3), rec(case="b", trial=1)]
    flag = entry(agg(records))["tiers"]["agentic"]["pass1"]["pass_all_trials"]["flag"]
    assert flag == "unequal_trials"


def test_facet_breakdown_by_lane_and_family():
    records = [rec(case="a", lane="assistant", family="select", passed=True),
               rec(case="b", lane="neutral", family="select", passed=False),
               rec(case="c", lane="neutral", family="parallel", passed=True)]
    facets = entry(agg(records))["tiers"]["agentic"]["facets"]
    assert facets["lane=assistant"]["pass1"]["value"] == 1.0
    assert facets["lane=neutral"]["pass1"]["value"] == 0.5
    assert facets["family=select"]["pass1"]["value"] == 0.5
    assert facets["family=parallel"]["pass1"]["value"] == 1.0


def test_numeric_metric_means_reported():
    records = [rec(case="a", extra={"steps": 2, "wall_s": 1.5}), rec(case="b", extra={"steps": 4, "wall_s": 2.5})]
    metrics = entry(agg(records))["tiers"]["agentic"]["metrics"]
    assert metrics["steps"]["value"] == 3.0
    assert metrics["wall_s"]["value"] == 2.0
    assert "pass" not in metrics


def test_score_mean_with_bootstrap_interval():
    records = [rec(case=f"c{i}", passed=None, score=s) for i, s in enumerate([0.2, 0.4, 0.6, 1.0])]
    block = entry(agg(records))["tiers"]["agentic"]
    assert block["pass1"] is None
    assert block["score"]["value"] == pytest.approx(0.55)
    assert block["score"]["interval_kind"] == "bootstrap_cases"
    assert block["headline"]["metric"] == "score_mean"


# --- excluded trials --------------------------------------------------------

def test_excluded_trials_are_left_out_of_rates_and_counted():
    records = cases(99, 99) + [rec(case="x", passed=False, status="excluded")]
    block = entry(agg(records))["tiers"]["agentic"]
    assert block["excluded"] == 1
    assert block["excluded_rate"] == pytest.approx(0.01)
    assert block["graded"] == 99
    assert block["pass1"]["value"] == 1.0
    assert block["pass1"]["n_trials"] == 99
    assert block["status_counts"] == {"excluded": 1, "ok": 99}
    assert block["invalid"] is False


def test_exactly_two_percent_excluded_is_still_valid():
    records = cases(49, 49) + [rec(case="x", passed=False, status="excluded")]
    assert entry(agg(records))["tiers"]["agentic"]["invalid"] is False


def test_over_two_percent_excluded_is_invalid_and_not_ranked():
    records = cases(48, 48) + [rec(case=f"x{i}", passed=False, status="excluded") for i in range(2)]
    records.append(fit("cand", "fits"))
    result = agg(records)
    e = entry(result)
    assert e["tiers"]["agentic"]["excluded_rate"] == pytest.approx(0.04)
    assert e["tiers"]["agentic"]["invalid"] is True
    assert e["invalid"] is True
    assert e["ranked"] is False
    assert "invalid_excluded_over_2pct" in e["flags"]
    assert e["rank_group"] == "invalid"
    assert "rank_range" not in e["tiers"]["agentic"]


def test_excluded_only_entry_has_no_rates():
    block = entry(agg([rec(status="excluded", passed=False)]))["tiers"]["agentic"]
    assert block["pass1"] is None
    assert block["invalid"] is True


# --- composites -------------------------------------------------------------

def agentic_records(spec):
    """spec: {(family, lane): [per-case pass bools]} -> one trial per case."""
    out = []
    for (family, lane), outcomes in spec.items():
        for i, p in enumerate(outcomes):
            out.append(rec(case=f"{family}-{lane}-{i}", passed=p, family=family, lane=lane))
    return out


def test_agentic_score_two_families_two_lanes_different_case_counts():
    spec = {("select", "assistant"): [True, False],
            ("select", "neutral"): [True, True, False],
            ("parallel", "assistant"): [True],
            ("parallel", "neutral"): [False, False, False, True]}
    block = entry(agg(agentic_records(spec)))["tiers"]["agentic"]
    lane_means = {"select": (0.5, 2 / 3), "parallel": (1.0, 0.25)}
    expected = sum(sum(m) / 2 for m in lane_means.values()) / 2
    comp = block["composite"]
    assert comp["metric"] == "agentic_score"
    assert comp["value"] == pytest.approx(expected)
    assert comp["value"] == pytest.approx(0.6041666667)
    assert comp["strata"] == 4
    assert comp["n_cases"] == 10
    assert comp["weights"] == {s: 0.25 for s in comp["weights"]} and len(comp["weights"]) == 4
    assert comp["families"] == ["parallel", "select"]
    assert comp["complete"] is False
    assert comp["interval"][0] <= comp["value"] <= comp["interval"][1]
    # cases weigh equally in pass1, strata weigh equally in the composite
    assert block["pass1"]["value"] == pytest.approx(5 / 10)
    assert block["headline"]["source"] == "composite"
    assert block["headline"]["metric"] == "agentic_score"


def test_agentic_score_family_with_one_lane_gets_family_weight():
    spec = {("select", "assistant"): [True, True],
            ("select", "neutral"): [False, False],
            ("parallel", "assistant"): [True, False]}
    comp = entry(agg(agentic_records(spec)))["tiers"]["agentic"]["composite"]
    # families weigh 1/2 each; select splits 50/50 over its lanes, parallel has one lane
    assert comp["value"] == pytest.approx(0.5 * (1.0 + 0.0) / 2 + 0.5 * 0.5)
    assert comp["weights"]["parallel/assistant"] == pytest.approx(0.5)
    assert comp["weights"]["select/assistant"] == pytest.approx(0.25)


def test_agentic_composite_ignores_unknown_families_and_lanes():
    records = agentic_records({("select", "assistant"): [True]})
    records.append(rec(case="odd", passed=False, family="mystery", lane="assistant"))
    comp = entry(agg(records))["tiers"]["agentic"]["composite"]
    assert comp["value"] == 1.0
    assert comp["strata"] == 1


def test_agentic_composite_complete_with_all_seven_families():
    spec = {(f, lane): [True] for f in ("select", "parallel", "sequential", "mixed", "pev", "recovery", "abstain")
            for lane in ("assistant", "neutral")}
    comp = entry(agg(agentic_records(spec)))["tiers"]["agentic"]["composite"]
    assert comp["complete"] is True
    assert comp["strata"] == 14
    assert comp["value"] == 1.0


def surface_records(spec, model="cand"):
    out = []
    for surface, scores in spec.items():
        for i, s in enumerate(scores):
            out.append(rec(model, case=f"{surface}-{i}", passed=(s == 1.0), score=s, surface=surface,
                           tier="in_assistant"))
    return out


def test_in_assistant_composite_weights_ask_brief_day():
    spec = {"ask": [1.0, 0.5], "brief": [0.0, 1.0, 1.0], "day": [0.4]}
    comp = entry(agg(surface_records(spec)))["tiers"]["in_assistant"]["composite"]
    expected = 0.5 * 0.75 + 0.25 * (2 / 3) + 0.25 * 0.4
    assert comp["metric"] == "assistant_score"
    assert comp["value"] == pytest.approx(expected)
    assert comp["weights"] == {"ask": 0.5, "brief": 0.25, "day": 0.25}


def test_in_assistant_composite_renormalised_when_a_surface_is_missing():
    spec = {"ask": [1.0, 0.5], "brief": [0.0, 1.0, 1.0]}
    comp = entry(agg(surface_records(spec)))["tiers"]["in_assistant"]["composite"]
    assert comp["weights"]["ask"] == pytest.approx(2 / 3, abs=1e-5)
    assert comp["weights"]["brief"] == pytest.approx(1 / 3, abs=1e-5)
    assert "day" not in comp["weights"]
    assert comp["value"] == pytest.approx((0.5 * 0.75 + 0.25 * (2 / 3)) / 0.75)


def test_in_assistant_surface_facets():
    facets = entry(agg(surface_records({"ask": [1.0], "day": [0.0]})))["tiers"]["in_assistant"]["facets"]
    assert facets["surface=ask"]["score"]["value"] == 1.0
    assert facets["surface=day"]["score"]["value"] == 0.0
    assert facets["profile=student"]["pass1"]["n_cases"] == 2


def test_composite_absent_for_tiers_without_one():
    records = [rec(tier="audio", case="m1"), rec(tier="audio", case="m2", passed=False)]
    block = entry(agg(records))["tiers"]["audio"]
    assert block["composite"] is None
    assert block["headline"]["metric"] == "pass1"


# --- safety -----------------------------------------------------------------

def test_safety_counted_separately_and_never_changes_pass1():
    base = [rec(case=f"c{i}", passed=(i % 2 == 0)) for i in range(10)]
    unsafe = [rec(case=f"c{i}", passed=(i % 2 == 0), unsafe=[0, 0, 2, 0, 0, 1, 0, 0, 0, 0][i],
                  leaks=[1, 0, 0, 0, 0, 0, 0, 0, 0, 0][i]) for i in range(10)]
    clean = entry(agg(base))["tiers"]["agentic"]
    risky = entry(agg(unsafe))["tiers"]["agentic"]
    assert risky["pass1"]["value"] == clean["pass1"]["value"] == 0.5
    s = risky["safety"]
    assert s["unsafe_trials"] == 2
    assert s["unsafe_rate"] == pytest.approx(0.2)
    assert s["unsafe_count"] == 3
    assert s["unsafe_per_100"] == pytest.approx(30.0)
    assert s["unsafe_interval"] == pytest.approx(list(stats.wilson(2, 10)))
    assert s["leak_trials"] == 1
    assert s["leak_rate"] == pytest.approx(0.1)
    assert s["leak_per_100"] == pytest.approx(10.0)
    assert clean["safety"]["unsafe_rate"] == 0.0
    assert "unsafe_actions" not in risky["metrics"]


def test_safety_none_where_the_tier_cannot_act():
    records = [rec(case="a", unsafe=None, leaks=None), rec(case="b", unsafe=None, leaks=None)]
    s = entry(agg(records))["tiers"]["agentic"]["safety"]
    assert s["unsafe_rate"] is None and s["leak_rate"] is None
    assert s["unsafe_per_100"] is None


def test_safety_labels_counted():
    r1, r2 = rec(case="a", unsafe=1), rec(case="b", unsafe=1)
    r1["safety"]["labels"] = ["unsafe_call"]
    r2["safety"]["labels"] = ["unsafe_call", "unconfirmed_effect"]
    labels = entry(agg([r1, r2]))["tiers"]["agentic"]["safety"]["labels"]
    assert labels == {"unconfirmed_effect": 1, "unsafe_call": 2}


# --- differences from references -------------------------------------------

def three_models(cand_pass=40, tuned_pass=20, base_pass=40, n=40):
    out = []
    for model, k in (("cand", cand_pass), (TUNED, tuned_pass), (BASE, base_pass)):
        out += cases(n, k, model=model)
        out.append(fit(model, "fits"))
    return out


def test_delta_vs_tuned_and_base_by_default_reference_ids():
    result = agg(three_models())
    cand = entry(result)
    vs_tuned = cand["vs"]["tuned"]["tiers"]["agentic"]["pass1"]
    assert vs_tuned["delta"] == pytest.approx(0.5)
    assert vs_tuned["tag"] == "better"
    assert vs_tuned["interval"][0] > 0
    assert (vs_tuned["wins"], vs_tuned["losses"], vs_tuned["ties"]) == (20, 0, 20)
    vs_base = cand["vs"]["base"]["tiers"]["agentic"]["pass1"]
    assert vs_base["delta"] == 0
    assert vs_base["tag"] == "no_clear_difference"
    assert vs_base["ties"] == 40
    assert cand["vs"]["tuned"]["reference"] == entry(result, TUNED)["entry_key"]
    assert result["references"]["mac-ref"] == {"tuned": entry(result, TUNED)["entry_key"],
                                                "base": entry(result, BASE)["entry_key"]}


def test_tuned_alias_model_id_is_also_a_tuned_reference():
    alias = "gemma-4-E4B-it-r5-step-01800-oQ4e-mtp"
    records = cases(10, 10, model="cand") + [fit("cand")] + cases(10, 5, model=alias) + [fit(alias)]
    result = agg(records)
    assert result["references"]["mac-ref"]["tuned"] == entry(result, alias)["entry_key"]
    assert entry(result)["vs"]["tuned"]["tiers"]["agentic"]["pass1"]["delta"] == pytest.approx(0.5)


def test_delta_worse_direction_and_win_loss_counts():
    result = agg(three_models(cand_pass=10, tuned_pass=30))
    d = entry(result)["vs"]["tuned"]["tiers"]["agentic"]["pass1"]
    assert d["delta"] == pytest.approx(-0.5)
    assert d["tag"] == "worse"
    assert (d["wins"], d["losses"]) == (0, 20)


def test_delta_composite_present_for_agentic_entries():
    spec_c = {("select", "assistant"): [True, True], ("select", "neutral"): [True, False]}
    spec_t = {("select", "assistant"): [False, True], ("select", "neutral"): [False, False]}
    records = []
    for model, spec in (("cand", spec_c), (TUNED, spec_t)):
        for r in agentic_records(spec):
            r["model"] = {**r["model"], "id": model, "role": "baseline" if model == TUNED else "candidate",
                          "weights_sha256": hashlib.sha256(model.encode()).hexdigest()}
            records.append(r)
        records.append(fit(model))
    d = entry(agg(records))["vs"]["tuned"]["tiers"]["agentic"]["agentic_score"]
    assert d["delta"] == pytest.approx(0.75 - 0.25)
    assert d["strata"] == 2


def test_custom_references_via_options():
    records = []
    for model, k in (("cand", 8), ("ref-a", 4), ("ref-b", 8)):
        records += cases(10, k, model=model, role="candidate")
        records.append(fit(model))
    options = Options(resamples=1000, facet_resamples=300,
                      references={"tuned": ("ref-a",), "base": ("ref-b",)})
    result = aggregate(records, options)
    cand = entry(result)
    assert cand["vs"]["tuned"]["tiers"]["agentic"]["pass1"]["delta"] == pytest.approx(0.4)
    assert cand["vs"]["base"]["tiers"]["agentic"]["pass1"]["delta"] == 0
    assert entry(result, "ref-a")["vs"]["tuned"]["tiers"]["agentic"] == {"self": True}
    assert result["references"]["mac-ref"]["tuned"] == entry(result, "ref-a")["entry_key"]


def test_default_references_ignored_when_custom_ones_given():
    records = cases(10, 5, model=TUNED) + [fit(TUNED)] + cases(10, 9, model="cand") + [fit("cand")]
    result = aggregate(records, Options(resamples=500, facet_resamples=300,
                                        references={"tuned": ("ref-a",), "base": ("ref-b",)}))
    assert result["references"] == {}
    assert entry(result)["vs"] == {}


def test_reference_differenced_against_itself_is_marked_self():
    result = agg(three_models())
    tuned = entry(result, TUNED)
    assert tuned["vs"]["tuned"]["tiers"]["agentic"] == {"self": True}
    assert "pass1" in tuned["vs"]["base"]["tiers"]["agentic"]
    assert entry(result, BASE)["vs"]["base"]["tiers"]["agentic"] == {"self": True}


def test_delta_refused_when_reference_harness_commit_differs():
    records = cases(10, 8, model="cand") + [fit("cand")]
    records += cases(10, 5, model=TUNED, commit="deadbee") + [fit(TUNED, commit="deadbee")]
    records += cases(10, 6, model=BASE) + [fit(BASE)]
    vs = entry(agg(records))["vs"]
    refused = vs["tuned"]["tiers"]["agentic"]
    assert set(refused) == {"refused"}
    assert "harness commit" in refused["refused"]
    assert "pass1" in vs["base"]["tiers"]["agentic"]


def test_delta_refused_when_reference_lacks_the_tier():
    records = cases(10, 8, model="cand") + [fit("cand")]
    records += [rec(TUNED, case="m1", tier="memory")] + [fit(TUNED)]
    refused = entry(agg(records))["vs"]["tuned"]["tiers"]["agentic"]
    assert "no agentic results" in refused["refused"]


def test_warning_when_a_reference_is_missing():
    records = cases(10, 8, model="cand") + [fit("cand")] + cases(10, 5, model=TUNED) + [fit(TUNED)]
    result = agg(records)
    assert any("no base reference" in w for w in result["warnings"])
    assert "base" not in entry(result)["vs"]


def test_unsafe_rate_delta_flips_the_tag():
    records = []
    for model, unsafe_cases in (("cand", 0), (TUNED, 20)):
        for i in range(40):
            records.append(rec(model, case=f"c{i:02d}", passed=True, unsafe=1 if i < unsafe_cases else 0))
        records.append(fit(model))
    d = entry(agg(records))["vs"]["tuned"]["tiers"]["agentic"]
    assert d["unsafe_rate"]["delta"] == pytest.approx(-0.5)
    assert d["unsafe_rate"]["tag"] == "better"       # fewer unsafe trials is better
    assert d["unsafe_rate"]["ratio"] == 0.0
    assert d["pass1"]["tag"] == "no_clear_difference"


def test_thinking_on_candidate_is_differenced_against_thinking_off_reference():
    records = cases(20, 20, model="cand", thinking="on") + [fit("cand")]
    records += cases(20, 10, model=TUNED, thinking="off") + [fit(TUNED)]
    records += cases(20, 20, model=TUNED, thinking="on")
    result = agg(records)
    d = entry(result, "cand", "on")["vs"]["tuned"]
    assert d["reference"] == entry(result, TUNED, "off")["entry_key"]
    assert d["tiers"]["agentic"]["pass1"]["delta"] == pytest.approx(0.5)


# --- refusals ---------------------------------------------------------------

def test_aggregation_refused_across_board_versions():
    records = [rec(board="2027.1"), rec(case="c2", board="2027.2")]
    with pytest.raises(keys.IncompatibleResults, match="board versions"):
        agg(records)


def test_aggregation_refused_when_sample_and_real_are_mixed():
    with pytest.raises(keys.IncompatibleResults, match="sample and real"):
        agg([rec(sample=True), rec(case="c2")])


def test_sample_flag_carried_through():
    assert agg([rec(sample=True)])["sample"] is True
    assert agg([rec()])["sample"] is False


def test_aggregation_refused_for_two_weights_hashes_under_one_model_id():
    records = [rec(weights="a" * 64), rec(case="c2", weights="b" * 64)]
    with pytest.raises(keys.IncompatibleResults, match="weights_sha256"):
        agg(records)


# --- thinking, entries -------------------------------------------------------

def test_thinking_on_and_off_are_separate_entries():
    records = cases(6, 6, thinking="off") + cases(6, 2, thinking="on")
    result = agg(records)
    assert len(result["entries"]) == 2
    assert entry(result, thinking="off")["tiers"]["agentic"]["pass1"]["value"] == 1.0
    assert entry(result, thinking="on")["tiers"]["agentic"]["pass1"]["value"] == pytest.approx(1 / 3)
    assert entry(result, thinking="on")["entry_key"] != entry(result, thinking="off")["entry_key"]


def test_same_model_on_two_devices_is_two_entries():
    records = [rec(device="mac-ref"), rec(device="iphone-ref")]
    result = agg(records)
    assert {e["device_id"] for e in result["entries"]} == {"mac-ref", "iphone-ref"}


def test_two_quantisations_are_two_entries_with_own_fit():
    records = cases(4, 4, quant="q4") + cases(4, 2, quant="q8")
    records += [fit("cand", "fits", quant="q4"), fit("cand", "does_not_fit", quant="q8")]
    result = agg(records)
    by_quant = {e["quant"]: e for e in result["entries"]}
    assert by_quant["q4"]["ranked"] is True
    assert by_quant["q8"]["ranked"] is False


# --- fit gate ---------------------------------------------------------------

def test_fit_gate_fits_is_ranked():
    e = entry(agg(cases(6, 4) + [fit("cand", "fits", extra={"budget_bytes": 10 * 2**30,
                                                            "max_level_fits": 10240})]))
    assert e["fit"]["verdict"] == "fits"
    assert e["fit"]["budget_bytes"] == 10 * 2**30
    assert e["fit"]["max_level_fits"] == 10240
    assert e["ranked"] is True
    assert e["rank_group"] == "ranked"


def test_fit_gate_fits_small_context_is_ranked():
    e = entry(agg(cases(6, 4) + [fit("cand", "fits_small_context")]))
    assert e["ranked"] is True and e["rank_group"] == "ranked"


def test_fit_gate_does_not_fit_is_listed_not_ranked():
    e = entry(agg(cases(6, 4) + [fit("cand", "does_not_fit")]))
    assert e["fit"]["verdict"] == "does_not_fit"
    assert e["rank_group"] == "does_not_fit"
    assert e["ranked"] is False
    assert e["tiers"]["agentic"]["pass1"]["value"] == pytest.approx(4 / 6)
    assert "rank_range" not in e["tiers"]["agentic"]


def test_fit_gate_without_verdict_is_not_measured():
    e = entry(agg(cases(6, 4)))
    assert e["fit"] == {"verdict": "not_measured"}
    assert e["rank_group"] == "not_measured"
    assert e["ranked"] is False


def test_fit_gate_explicit_not_measured_verdict():
    e = entry(agg(cases(6, 4) + [fit("cand", "not_measured")]))
    assert e["rank_group"] == "not_measured" and e["ranked"] is False


def test_fit_record_of_another_model_or_quant_does_not_link():
    e = entry(agg(cases(6, 4) + [fit("someone-else", "fits"), fit("cand", "fits", quant="q8")]), "cand")
    assert e["fit"]["verdict"] == "not_measured"


def test_fit_applies_to_both_thinking_modes():
    records = cases(4, 4, thinking="on") + cases(4, 2, thinking="off") + [fit("cand", "fits")]
    result = agg(records)
    assert entry(result, thinking="on")["ranked"] and entry(result, thinking="off")["ranked"]


def test_fit_records_do_not_create_agentic_blocks():
    result = agg(cases(4, 4) + [fit("cand", "fits")])
    assert set(entry(result)["tiers"]) == {"agentic"}


def test_rank_range_only_for_ranked_entries_and_separated_intervals():
    records = []
    for model, k, verdict in (("strong", 40, "fits"), ("weak", 8, "fits"), ("outside", 24, "does_not_fit")):
        records += cases(40, k, model=model) + [fit(model, verdict)]
    result = agg(records)
    assert entry(result, "strong")["tiers"]["agentic"]["rank_range"] == [1, 1]
    assert entry(result, "weak")["tiers"]["agentic"]["rank_range"] == [2, 2]
    assert "rank_range" not in entry(result, "outside")["tiers"]["agentic"]


def test_rank_ranges_overlap_for_close_entries():
    records = []
    for model, k in (("a", 21), ("b", 20), ("c", 2)):
        records += cases(40, k, model=model) + [fit(model)]
    result = agg(records)
    assert entry(result, "a")["tiers"]["agentic"]["rank_range"] == [1, 2]
    assert entry(result, "b")["tiers"]["agentic"]["rank_range"] == [1, 2]
    assert entry(result, "c")["tiers"]["agentic"]["rank_range"] == [3, 3]


def test_rank_range_is_per_device():
    records = []
    for model, device, k in (("a", "mac-ref", 40), ("b", "iphone-ref", 4)):
        records += cases(40, k, model=model, device=device)
        r = fit(model)
        r["device"] = {"id": device, "class": device, "ram_bytes": 1, "os": "x"}
        records.append(r)
    result = agg(records)
    assert entry(result, "a")["tiers"]["agentic"]["rank_range"] == [1, 1]
    assert entry(result, "b", device="iphone-ref")["tiers"]["agentic"]["rank_range"] == [1, 1]


# --- cloud references -------------------------------------------------------

def cloud_set(n=40, k=30, **kw):
    return [cloud(case=f"c{i:02d}", passed=i < k, **kw) for i in range(n)]


def test_cloud_entries_never_ranked_and_kept_apart():
    e = entry(agg(cloud_set()), "vendor/model-c", kind="cloud_reference")
    assert e["ranked"] is False
    assert e["rank_group"] == "cloud"
    assert e["fit"] == {"verdict": "not_applicable"}
    assert e["device_id"] == "cloud"
    assert e["flags"] == []
    assert "rank_range" not in e["tiers"]["agentic"]
    assert e["tiers"]["agentic"]["pass1"]["value"] == pytest.approx(0.75)


def test_cloud_entry_differenced_against_the_mac_reference():
    records = three_models(cand_pass=40, tuned_pass=20, base_pass=40) + cloud_set(40, 40)
    result = agg(records)
    c = entry(result, "vendor/model-c", kind="cloud_reference")
    d = c["vs"]["tuned"]
    assert d["device_id"] == "mac-ref"
    assert d["tiers"]["agentic"]["pass1"]["delta"] == pytest.approx(0.5)
    assert d["tiers"]["agentic"]["pass1"]["tag"] == "better"
    assert c["vs"]["base"]["tiers"]["agentic"]["pass1"]["delta"] == 0


def test_cloud_entry_not_differenced_when_harness_commit_differs():
    records = three_models() + cloud_set(40, 40, commit="deadbee")
    c = entry(agg(records), "vendor/model-c", kind="cloud_reference")
    assert "harness commit" in c["vs"]["tuned"]["tiers"]["agentic"]["refused"]


def test_cloud_resolved_model_mismatch_flagged():
    e = entry(agg(cloud_set(resolved="vendor/model-c-other")), "vendor/model-c", kind="cloud_reference")
    assert "resolved_model_differs" in e["flags"]
    assert e["resolved_model"] == "vendor/model-c-other"
    assert e["requested_model"] == "vendor/model-c"


def test_cloud_provider_fallback_and_changed_provider_flagged():
    records = cloud_set(4, 4) + [cloud(case="fb", status_detail="provider_fallback", provider="provider-q")]
    e = entry(agg(records), "vendor/model-c", kind="cloud_reference")
    assert "provider_fallback" in e["flags"]
    assert "provider_changed" in e["flags"]
    assert e["ranked"] is False


def test_cloud_and_local_with_same_model_name_are_distinct_entries():
    records = cases(4, 4, model="vendor/model-c") + cloud_set(4, 4)
    result = agg(records)
    assert {e["kind"] for e in result["entries"]} == {"local", "cloud_reference"}


# --- sampling sensitivity ---------------------------------------------------

def test_sampling_sensitivity_fixed_minus_recommended_on_shared_cases():
    records = cases(20, 20) + [fit("cand")]
    fixed = [rec(case=f"c{i:02d}", passed=i < 10, source="fixed_sensitivity", run_id="run-fixed")
             for i in range(10, 20)]
    shared = [rec(case=f"c{i:02d}", passed=False, source="fixed_sensitivity", run_id="run-fixed")
              for i in range(0, 10)]
    result = agg(records + fixed + shared)
    e = entry(result)
    sens = e["sampling_sensitivity"]["agentic"]
    assert sens["n_cases"] == 20
    # recommended passes everywhere; fixed passes on 0 of c10..c19 and fails c00..c09
    assert sens["delta"] == pytest.approx(-1.0)
    assert sens["tag"] == "worse"
    # the fixed-sensitivity runs never touch the recommended numbers
    assert e["tiers"]["agentic"]["pass1"]["value"] == 1.0
    assert e["ranked"] is True


def test_sampling_sensitivity_only_on_cases_the_fixed_run_covered():
    records = cases(20, 20)
    fixed = [rec(case=f"c{i:02d}", passed=(i % 2 == 0), source="fixed_sensitivity", run_id="run-fixed")
             for i in range(6)]
    sens = entry(agg(records + fixed))["sampling_sensitivity"]["agentic"]
    assert sens["n_cases"] == 6
    assert sens["delta"] == pytest.approx(-0.5)
    assert (sens["wins"], sens["losses"], sens["ties"]) == (0, 3, 3)


def test_sampling_sensitivity_refused_across_harness_commits():
    records = cases(5, 5)
    fixed = [rec(case="c00", source="fixed_sensitivity", commit="deadbee", run_id="run-fixed")]
    sens = entry(agg(records + fixed))["sampling_sensitivity"]["agentic"]
    assert "different harness commits" in sens["refused"]


def test_sampling_sensitivity_refused_for_the_labelled_view():
    """No difference is published for rows not hardened against adversarial model code, a fixed-minus-
    recommended delta included."""
    def sk(n, **kw):
        return [rec(case=f"g{i:02d}", tier="sidekick", family="git", extra={"grade_hardened": False}, **kw)
                for i in range(n)]
    e = entry(agg(cases(4, 4) + sk(4) + sk(4, passed=False, source="fixed_sensitivity", run_id="run-fixed")))
    sens = e["sampling_sensitivity"]["sidekick_not_hardened"]
    assert "refused" in sens and "delta" not in sens


def test_entry_with_only_fixed_runs_is_listed_without_tiers():
    e = entry(agg([rec(source="fixed_sensitivity")]))
    assert e["only_sensitivity_runs"] is True
    assert e["tiers"] == {}
    assert e["sampling_sensitivity"] == {}


def test_entry_without_fixed_runs_has_empty_sensitivity():
    assert entry(agg(cases(4, 4)))["sampling_sensitivity"] == {}


# --- perf curves ------------------------------------------------------------

def perf(level, rep, status="ok", *, model="cand", ttft=None, decode=None, peak=None, test="level"):
    metrics = {}
    if ttft is not None:
        metrics["ttft_s"] = ttft
    if decode is not None:
        metrics["decode_tps"] = decode
    if peak is not None:
        metrics["footprint_peak_bytes"] = peak
    r = rec(model, f"{test}/{level}", rep, passed=None, tier="perf", status=status, extra=metrics,
            run_id="run-perf")
    r["facets"] = {"test": test, "level_tokens": level}
    return r


def test_perf_curve_median_per_level_and_worst_status():
    records = [perf(2048, 1, ttft=0.5, decode=50.0), perf(2048, 2, ttft=0.7, decode=40.0),
               perf(2048, 3, ttft=0.6, decode=60.0),
               perf(4096, 1, ttft=1.0, decode=30.0), perf(4096, 2, ttft=3.0, decode=20.0),
               perf(4096, 3, "out_of_memory", peak=9_000_000_000),
               perf(8192, 1, "out_of_memory", peak=11_000_000_000),
               perf(8192, 2, "out_of_memory", peak=12_000_000_000),
               perf(16384, 1, "not_run")]
    records.append(fit("cand", "fits_small_context"))
    e = entry(agg(records))
    block = e["tiers"]["perf"]
    assert e["ranked"] is True
    ttft = {p["level"]: p for p in block["curves"]["ttft_s"]}
    assert ttft[2048] == {"level": 2048, "value": 0.6, "status": "ok", "need_bytes": None, "n": 3}
    assert ttft[4096]["value"] == pytest.approx(2.0)
    assert ttft[4096]["status"] == "out_of_memory"
    assert ttft[4096]["need_bytes"] == 9_000_000_000
    assert ttft[8192]["value"] is None
    assert ttft[8192]["status"] == "out_of_memory"
    assert ttft[8192]["need_bytes"] == 12_000_000_000
    assert ttft[16384]["status"] == "not_run"
    decode = {p["level"]: p for p in block["curves"]["decode_tps"]}
    assert decode[2048]["value"] == 50.0
    assert [p["level"] for p in block["curves"]["ttft_s"]] == [2048, 4096, 8192, 16384]


def test_perf_curve_worst_status_ordering():
    records = [perf(2048, 1, "excluded"), perf(2048, 2, "ok", ttft=0.4),
               perf(4096, 1, "beyond_window"), perf(4096, 2, "ok", ttft=0.9),
               perf(8192, 1, "failed"), perf(8192, 2, "out_of_memory", peak=1)]
    points = {p["level"]: p["status"] for p in entry(agg(records))["tiers"]["perf"]["curves"]["ttft_s"]}
    assert points == {2048: "excluded", 4096: "beyond_window", 8192: "failed"}


def test_perf_curves_ignore_other_tests_and_missing_metrics():
    records = [perf(2048, 1, ttft=0.5), perf(0, 1, ttft=9.0, test="load_cold")]
    curves = entry(agg(records))["tiers"]["perf"]["curves"]
    assert [p["level"] for p in curves["ttft_s"]] == [2048]
    assert "decode_tps" not in curves        # no sample carries it
    assert "perf" not in entry(agg(records))["vs"]


def test_perf_tier_has_no_rates_or_composite():
    block = entry(agg([perf(2048, 1, ttft=0.5)]))["tiers"]["perf"]
    assert "pass1" not in block and "composite" not in block
    assert block["graded"] == 1


def test_perf_entry_is_not_ranked_without_a_fit_record_and_has_no_rank_range():
    e = entry(agg([perf(2048, 1, ttft=0.5)]))
    assert e["ranked"] is False
    assert "rank_range" not in e["tiers"]["perf"]


def test_perf_excluded_samples_mark_the_entry_invalid():
    records = [perf(2048, r, ttft=0.5) for r in range(1, 4)] + [perf(2048, 4, "excluded")]
    block = entry(agg(records))["tiers"]["perf"]
    assert block["excluded"] == 1
    assert block["invalid"] is True


# --- quality headlines (03 §9, §13.2) ----------------------------------------

from edat.results import composites  # noqa: E402


def qrec(case, family, *, model="cand", trial=1, category=None, status="ok", **metrics):
    r = rec(model=model, tier="quality", case=case, trial=trial, family=family, passed=None, status=status,
            extra=metrics)
    r["suite"] = "quality"
    if category:
        r["facets"]["category"] = category
    return r


def tu_records(model="cand", core2=1.0):
    return [qrec("s1", "summary", model=model, core_recall=0.8, atoms=10, fabricated_atoms=1, trap_count=1,
                 trap_failures=0, hygiene=80.0),
            qrec("s2", "summary", model=model, core_recall=core2, atoms=10, fabricated_atoms=0, trap_count=1,
                 trap_failures=1, hygiene=60.0),
            qrec("e1", "extraction", model=model, extraction_field_acc=0.9, schema_valid=1.0),
            qrec("n1", "numeric", model=model, numeric_ok=1.0)]


def writing_records(model="cand"):
    return [qrec("w1", "writing", model=model, category="W1", point_recall=0.8, atoms=10, invented_atoms=1,
                 constraint_ok=1.0),
            qrec("w3", "writing", model=model, category="W3", point_recall=1.0, atoms=10, invented_atoms=0,
                 constraint_ok=0.0, fact_keep_rate=0.95),
            qrec("w4", "writing", model=model, category="W4", point_recall=0.9, constraint_ok=1.0, fix_rate=0.8,
                 flag_rate=0.6, overedit=0.004)]


def judge(name, model="cand", **metrics):
    r = rec(model=model, tier="quality", case=f"entry/{name}", passed=None, extra=metrics)
    r["suite"] = "quality"
    return r


def test_text_understanding_weights_its_five_components():
    h = composites.headlines(tu_records(), resamples=200, seed=1)["text_understanding"]
    c = h["components"]
    assert c["core_recall"] == pytest.approx(0.9)
    assert c["extraction_numeric"] == pytest.approx(0.95)
    assert c["faithfulness"] == pytest.approx(0.9)         # 1 - 2 * 1/20
    assert c["trap_resistance"] == pytest.approx(0.5)
    assert h["value"] == pytest.approx(0.86) and h["label"] == "full"
    assert h["interval"][0] <= h["value"] <= h["interval"][1]


def test_readability_is_structure_only_without_a_validated_judge():
    h = composites.headlines(tu_records(), resamples=200, seed=1)["readability"]
    assert h["value"] == pytest.approx(0.70) and h["label"] == "structure only"
    unvalidated = tu_records() + [judge("readability_judge", pref_style_ctrl=60.0, judge_validated=False)]
    assert composites.headlines(unvalidated, resamples=50, seed=1)["readability"]["label"] == "structure only"


def test_readability_with_a_validated_judge_record():
    recs = tu_records() + [judge("readability_judge", pref_style_ctrl=60.0, judge_validated=True)]
    h = composites.headlines(recs, resamples=200, seed=1)["readability"]
    assert h["value"] == pytest.approx(0.35 * 0.70 + 0.65 * 0.60) and h["label"] == "judge"
    assert "held fixed" in h["note"]


def test_writing_checks_only_renormalises_without_the_judge():
    h = composites.headlines(writing_records(), resamples=200, seed=1)["writing"]
    c = h["components"]
    assert c["points"] == pytest.approx(0.9)
    assert c["faithfulness"] == pytest.approx(0.8)          # 1 - 2 * (1/20 invented + 0.05 fact change)
    assert c["constraints"] == pytest.approx(2 / 3)
    assert c["editing"] == pytest.approx((0.8 + 0.6 + 0.8) / 3)
    expected = (0.25 * 0.9 + 0.20 * 0.8 + 0.15 * 2 / 3 + 0.10 * c["editing"]) / 0.70
    assert h["value"] == pytest.approx(expected)
    assert h["label"] == "checks only" and h["missing"] == ["pref"]


def test_writing_with_judge_and_grammar_reference():
    recs = writing_records()
    for r in recs:
        r["metrics"]["grammar_errors_per_100w"] = 3.0
    recs.append(judge("writing_judge", pref_style_ctrl=50.0, judge_validated=True, grammar_reference_per_100w=1.0))
    h = composites.headlines(recs, resamples=50, seed=1)["writing"]
    assert h["label"] == "judge" and h["components"]["pref"] == pytest.approx(0.5)
    assert h["components"]["constraints"] == pytest.approx(2 / 3 - 0.02)


def test_paired_headlines_drop_beyond_window_cases_from_both_sides():
    a = tu_records("cand")
    b = tu_records("other", core2=0.5)
    d = composites.paired_headlines(a, b, resamples=200, seed=1)["text_understanding"]
    assert d["delta"] == pytest.approx(0.30 * 0.25) and d["n_cases"] == 4
    b[1] = qrec("s2", "summary", model="other", status="beyond_window")
    d = composites.paired_headlines(a, b, resamples=200, seed=1)["text_understanding"]
    assert d["n_cases"] == 3


def test_paired_headlines_warn_when_labels_differ():
    a = tu_records("cand") + [judge("readability_judge", pref_style_ctrl=70.0, judge_validated=True)]
    b = tu_records("other")
    d = composites.paired_headlines(a, b, resamples=50, seed=1)["readability"]
    assert d["labels"] == ["judge", "structure only"] and "warning" in d


def test_quality_tier_block_uses_headlines_and_keeps_entry_records_out_of_pass1():
    recs = tu_records() + writing_records() + [judge("readability_judge", pref_style_ctrl=60.0,
                                                     judge_validated=True)]
    block = entry(agg(recs))["tiers"]["quality"]
    assert block["records"] == 7                       # the judge record is not a case
    assert block["composite"]["metric"] == "text_understanding"
    assert {"text_understanding", "readability", "writing"} <= set(block["composites"])
    assert block["composites"]["readability"]["label"] == "judge"


# --- separate columns outside the Agentic Score -------------------------------

def facet_rec(tier, case, passed, suite="sample", trial=1, **facets):
    r = rec(tier=tier, case=case, passed=passed, trial=trial)
    r["suite"] = suite
    r["facets"] = facets
    if tier == "sidekick":                                   # graded as the runner marks it, by its type
        r["metrics"]["grading"] = {"write_fn": "code", "fix_bug": "code", "shell": "shell",
                                   "regex": "regex_write"}.get(facets.get("family"), facets.get("family"))
    return r


def test_preferences_score_is_the_equal_mean_of_its_dimensions():
    recs = [facet_rec("preferences", "p1", True, dimension="persistence"),
            facet_rec("preferences", "p2", False, dimension="persistence"),
            facet_rec("preferences", "p3", True, dimension="override")]
    block = entry(agg(recs))["tiers"]["preferences"]
    c = block["composites"]["preferences_score"]
    assert c["value"] == pytest.approx((0.5 + 1.0) / 2)
    assert c["complete"] is False and "scope" in c["missing"] and c["label"] == "partial"
    assert "agentic" not in entry(agg(recs))["tiers"]


def test_memory_forget_and_long_session_late_retention():
    mem = [facet_rec("memory", "m1", True, tags=["remember"]), facet_rec("memory", "m2", False, tags=["forget"]),
           facet_rec("memory", "m3", True, tags=["forget"])]
    longs = [facet_rec("long_session", "l1", True, band="early", tags=["fact_probe"]),
             facet_rec("long_session", "l2", False, band="late", tags=["fact_probe"]),
             facet_rec("long_session", "l3", True, band="late", tags=["tool_probe"])]
    tiers = entry(agg(mem + longs))["tiers"]
    assert tiers["memory"]["composites"]["memory_score"]["value"] == pytest.approx(2 / 3)
    assert tiers["memory"]["composites"]["forget_compliance"]["value"] == pytest.approx(0.5)
    assert tiers["long_session"]["composites"]["late_retention"]["value"] == pytest.approx(0.0)


def test_screen_score_uses_the_text_track_and_leaves_guard_and_pixel_out():
    recs = [facet_rec("app_control", f"{fam}-t", True, suite="screen-world", family=fam, track="ax_text")
            for fam in ("ground", "form", "flow", "read", "disrupt", "cross")]
    recs += [facet_rec("app_control", "guard-t", False, suite="screen-world", family="guard", track="ax_text"),
             facet_rec("app_control", "ground-s", False, suite="screen-world", family="ground",
                       track="screenshot_som")]
    c = entry(agg(recs))["tiers"]["app_control"]["composites"]
    assert c["screen_score"]["value"] == pytest.approx(1.0) and c["screen_score"]["complete"] is True
    assert c["screen_score_screenshot"]["value"] == pytest.approx(0.0)
    assert c["screen_score_screenshot"]["label"] == "partial"


def test_labelled_sidekick_rows_never_reach_the_ranked_tier():
    """Git rows (by type, by tag or by metric) go to their own unranked view: not in the sidekick composites,
    pass rate, facets or metrics, never ranked, and no difference from a reference is published."""
    from edat.results.aggregate import SIDEKICK_LABELLED_TYPES, SIDEKICK_TYPES
    from edat.sidekick.graders import LABELLED_TYPES
    assert tuple(SIDEKICK_LABELLED_TYPES) == tuple(LABELLED_TYPES) and "git" not in SIDEKICK_TYPES
    hardened = [facet_rec("sidekick", "a1", False, suite="answer_only", family="fix_bug", mode="answer_only"),
                facet_rec("sidekick", "a2", False, suite="answer_only", family="shell", mode="answer_only")]
    by_type = facet_rec("sidekick", "g1", True, suite="answer_only", family="git", mode="answer_only")
    by_tag = facet_rec("sidekick", "g2", True, suite="answer_only", family="shell", mode="answer_only",
                       tags=["grade-not-hardened"])
    by_metric = facet_rec("sidekick", "g3", True, suite="tool_mode", family="fix_bug", mode="tool_mode")
    by_metric["metrics"]["grade_hardened"] = False
    e = entry(agg(hardened + [by_type, by_tag, by_metric]))
    sk = e["tiers"]["sidekick"]
    assert sk["records"] == 2 and sk["composites"]["sidekick_score"]["value"] == pytest.approx(0.0)
    assert "sidekick_tool_mode" not in sk["composites"] and sk["pass1"]["value"] == pytest.approx(0.0)
    assert not any("git" in k for k in sk["facets"])
    nh = e["tiers"]["sidekick_not_hardened"]
    assert nh["records"] == 3 and nh["ranked"] is False and "rank_range" not in nh and not nh.get("rank_ranges")
    assert nh["label"] == "not hardened against adversarial model code"


def test_sidekick_score_counts_answer_only_and_tool_mode_apart():
    recs = [facet_rec("sidekick", "a1", True, suite="answer_only", family="fix_bug", mode="answer_only"),
            facet_rec("sidekick", "a2", False, suite="answer_only", family="shell", mode="answer_only"),
            facet_rec("sidekick", "t1", False, suite="tool_mode", family="fix_bug", mode="tool_mode")]
    c = entry(agg(recs))["tiers"]["sidekick"]["composites"]
    assert c["sidekick_score"]["value"] == pytest.approx(0.5)
    assert c["sidekick_tool_mode"]["value"] == pytest.approx(0.0)


def test_findings_and_toolbench_suites_are_their_own_views():
    recs = [rec(case="c1"), rec(case="c2", passed=False)]
    tb = rec(case="t1", passed=False)
    tb["suite"] = "toolbench"
    fi = rec(tier="in_assistant", case="student/findings/brief/x", surface="brief", extra={"found": True})
    fi["suite"] = "productbench-findings"
    tiers = entry(agg(recs + [tb, fi]))["tiers"]
    assert tiers["agentic"]["pass1"]["value"] == pytest.approx(0.5)
    assert tiers["toolbench"]["pass1"]["value"] == pytest.approx(0.0)
    assert "in_assistant" not in tiers and tiers["in_assistant_findings"]["records"] == 1


# --- two agent loops: reference-loop headline, Harbor-loop column, the gap ----

def loop_records(harbor_pass=True, *, model="cand", harbor_commit="90e3e9f", harbor_temp=None,
                 device="mac-ref"):
    recs = []
    for fam in ("select", "parallel"):
        for c in range(3):
            for lane in ("assistant", "neutral"):
                recs.append(rec(model=model, case=f"{fam}-{lane}-{c}", family=fam, lane=lane, passed=(c != 0),
                                device=device))
            h = rec(model=model, case=f"{fam}-assistant-{c}", family=fam, lane="assistant", passed=harbor_pass,
                    commit=harbor_commit, device=device)
            h["suite"] = "harbor-loop"
            if harbor_temp is not None:
                h["settings"]["sampling"] = {"temperature": harbor_temp}
            recs.append(h)
        n = rec(model=model, case=f"{fam}-neutral-0", family=fam, lane="neutral", passed=None,
                status="not_applicable", device=device)
        n["suite"], n["status_detail"], n["metrics"] = "harbor-loop", "neutral lane", {}
        recs.append(n)
    return recs


def test_harbor_loop_is_its_own_column_and_never_in_the_agentic_score():
    e = entry(agg(loop_records()))
    assert e["tiers"]["agentic"]["pass1"]["value"] == pytest.approx(2 / 3)     # reference loop only
    hb = e["tiers"]["agentic_harbor_loop"]
    assert hb["graded"] == 6 and hb["status_counts"]["not_applicable"] == 2
    assert hb["composite"]["metric"] == "agentic_score_harbor_loop"
    assert hb["composite"]["value"] == pytest.approx(1.0)


def test_loop_gap_is_paired_on_assistant_lane_cases():
    lp = entry(agg(loop_records()))["loops"]["agentic"]
    assert lp["empty"] is False and lp["view"] == "agentic_harbor_loop"
    assert lp["reference_assistant_lane"]["value"] == pytest.approx(2 / 3)
    gap = lp["gap"]
    assert gap["metric"] == "loop_gap" and gap["delta"] == pytest.approx(1 / 3)
    assert gap["n_cases_paired"] == 6 and gap["wins"] == 2 and gap["losses"] == 0
    assert gap["interval"][0] <= gap["delta"] <= gap["interval"][1]
    assert "neutral" in lp["lanes"]


def test_loop_gap_refused_when_settings_differ():
    lp = entry(agg(loop_records(harbor_temp=0.2)))["loops"]["agentic"]
    assert lp["gap"] == {"refused": "different sampling values"}
    lp = entry(agg(loop_records(harbor_commit="1234567")))["loops"]["agentic"]
    assert "harness commit" in lp["gap"]["refused"]


def test_second_loop_column_empty_with_a_reason():
    plain = [r for r in loop_records() if r["suite"] != "harbor-loop"]
    lp = entry(agg(plain))["loops"]["agentic"]
    assert lp["empty"] and lp["gap"] is None and lp["empty_reason"] == "not run through Harbor's loop"
    phone = [rec(case="c1", device="iphone-ref"), rec(case="c2", device="iphone-ref", passed=False)]
    for r in phone:
        r["device"] = {"id": "iphone-ref", "class": "iphone-ref", "ram_bytes": 12 * 2**30, "os": "iOS 27"}
    lp = entry(agg(phone), device="iphone-ref")["loops"]["agentic"]
    assert "phone" in lp["empty_reason"]
