"""Selection tiers (spec 05 §17: core, extended, full): carried, never pooled, compared only when asked."""
from __future__ import annotations

import hashlib
import json

import pytest

from edat.results import cli, keys
from edat.results.aggregate import Options, aggregate
from edat.results.export import export
from edat.results.reader import merge_runs, read_set
from edat.results.schema import record_errors, validate
from edat.results.writer import RunWriter

CORE = "a" * 64
FULL = "b" * 64
OPTS = Options(resamples=500, facet_resamples=200, composite_resamples=200)


def weights(model: str) -> str:
    return hashlib.sha256(model.encode()).hexdigest()


def vrec(model="cand", case="v1-01", trial=1, ok=True, *, sel="core", manifest=CORE, n=50, hw=13.9,
         role=None, run_id="run-core") -> dict:
    """A local `vision` record (invented values) with an optional selection."""
    r = {"schema": "edat.result/1.0", "kind": "local", "tier": "vision", "suite": "v1", "run_id": run_id,
         "board_version": "2027.1", "lang": "en",
         "model": {"id": model, "role": role or ("baseline" if model == "Gemma-Wisp" else "candidate"),
                   "weights_sha256": weights(model), "quant": {"scheme": "q4"}},
         "runtime": {"name": "omlx", "version": "0.7.3"},
         "device": {"id": "mac-ref", "class": "mac-ref", "ram_bytes": 24 * 2**30, "os": "macOS 27"},
         "settings": {"thinking": "off", "sampling": {"temperature": 1.0}, "sampling_source": "author_recommended",
                      "seed": trial},
         "harness_commit": "90e3e9f", "started_at": "2026-12-01T10:15:00Z", "case_id": case, "split": "dev",
         "trial": trial, "status": "ok", "facets": {"family": "v1", "category": "charts"},
         "metrics": {"pass": ok}, "safety": {"unsafe_actions": None, "privacy_leaks": None},
         "raw": None, "raw_reason": "test"}
    if sel:
        r["selection"] = {"tier": sel, "manifest_sha256": manifest, "n_items": n, "ci95_half_width_pts": hw}
    return r


def run(store, run_id, records):
    for r in records:
        r["run_id"] = run_id
    with RunWriter.open(store, run_id, tier="vision", suite="v1") as w:
        w.append_many(records)
        return w.dir


def cases(model="cand", *, sel="core", manifest=CORE, n_cases=4, fail_first=True, **kw):
    return [vrec(model, f"v1-{c:02d}", ok=not (fail_first and c == 0), sel=sel, manifest=manifest, **kw)
            for c in range(n_cases)]


def entry(agg, model="cand"):
    (e,) = [e for e in agg["entries"] if e["model_id"] == model]
    return e


# --- the record --------------------------------------------------------------

def test_selection_is_an_optional_field_of_result_1_0():
    assert record_errors(vrec()) == []
    assert record_errors(vrec(sel=None)) == []          # every record written so far
    assert record_errors(vrec(n=None, hw=None)) == []


@pytest.mark.parametrize("bad", [{"tier": "core"}, {"tier": "Core", "manifest_sha256": CORE},
                                 {"tier": "core", "manifest_sha256": "abc"},
                                 {"tier": "core", "manifest_sha256": CORE, "n_items": 0},
                                 {"tier": "core", "manifest_sha256": CORE, "extra": 1}])
def test_malformed_selection_refused(bad):
    r = vrec()
    r["selection"] = bad
    assert record_errors(r)


def test_media_runner_metrics_shape_is_read_as_a_selection():
    r = vrec(sel=None)
    r["metrics"].update(media_tier="extended", manifest_sha256=FULL, tier_n=120, ci95_half_width_pts=8.9)
    assert keys.selection_of(r) == {"tier": "extended", "manifest_sha256": FULL, "n_items": 120,
                                    "ci95_half_width_pts": 8.9}
    assert keys.comparison_key(r)["selection"] == f"extended/{FULL}"
    assert "selection" not in keys.comparison_key(vrec(sel=None))


# --- round trip ----------------------------------------------------------------

def test_round_trip_write_read_aggregate_export(tmp_path):
    d = run(tmp_path / "store", "run-core", cases())
    s = read_set(d, deep=True)
    assert all(r["selection"]["tier"] == "core" for r in s.records)
    manifest = json.loads((d / "manifest.json").read_text())
    assert manifest["comparison_key"]["selection"] == f"core/{CORE}"
    block = entry(aggregate(s.records, OPTS))["tiers"]["vision@core"]
    assert block["selection"] == {"tier": "core", "manifest_sha256": CORE, "n_items": 50,
                                  "ci95_half_width_pts": 13.9}
    assert block["pass1"]["value"] == pytest.approx(0.75)
    export([s], data_dir=tmp_path / "data", site_dir=tmp_path / "site", options=OPTS,
           now="2026-12-01T12:00:00Z")
    board = json.loads((tmp_path / "site" / "2027.1" / "mac-ref.json").read_text())
    validate(board, "site_board")
    rows = [r for r in board["results"] if r["metric"] == "pass1" and r["facet"] is None]
    assert rows and rows[0]["tier"] == "vision"
    assert rows[0]["selection"] == {"tier": "core", "manifest": CORE[:12], "n_items": 50,
                                    "ci95_half_width_pts": 13.9}


def test_records_without_selection_keep_working(tmp_path):
    s = read_set(run(tmp_path / "store", "run-whole", cases(sel=None)))
    e = entry(aggregate(s.records, OPTS))
    assert list(e["tiers"]) == ["vision"] and "selection" not in e["tiers"]["vision"]


# --- never pooled ----------------------------------------------------------------

def test_two_selections_of_one_entry_are_two_blocks():
    recs = cases(sel="core") + [vrec(case=f"v1-{c:02d}", ok=True, sel="full", manifest=FULL, n=550, hw=4.2,
                                     run_id="run-full") for c in range(8)]
    tiers = entry(aggregate(recs, OPTS))["tiers"]
    assert set(tiers) == {"vision@core", "vision@full"}
    assert tiers["vision@core"]["pass1"]["value"] == pytest.approx(0.75)
    assert tiers["vision@full"]["pass1"]["value"] == pytest.approx(1.0)


def test_two_manifests_under_one_selection_name_refused():
    recs = cases(manifest=CORE) + cases(manifest=FULL, n_cases=2)
    with pytest.raises(keys.IncompatibleResults, match="selection manifests"):
        aggregate(recs, OPTS)


def test_a_run_holds_one_selection_and_runs_of_two_do_not_merge(tmp_path):
    with pytest.raises(keys.IncompatibleResults, match="selection tier"):
        run(tmp_path / "store", "run-mixed", cases() + [vrec(case="v1-99", sel="full", manifest=FULL)])
    a = read_set(run(tmp_path / "store", "run-core", cases()))
    b = read_set(run(tmp_path / "store", "run-full", cases(sel="full", manifest=FULL)))
    with pytest.raises(keys.IncompatibleResults, match="selection tier"):
        merge_runs([a, b], tmp_path / "merged")


# --- compared only when asked, and then labelled ----------------------------------

def ref_and_candidate(ref_sel="full"):
    ref = [vrec("Gemma-Wisp", f"v1-{c:02d}", ok=True, sel=ref_sel, manifest=FULL if ref_sel == "full" else CORE,
                run_id="run-ref") for c in range(4)]
    return cases("cand") + ref


def test_difference_across_selections_refused_by_default():
    vs = entry(aggregate(ref_and_candidate(), OPTS))["vs"]["tuned"]["tiers"]["vision@core"]
    assert "another selection" in vs["refused"]


def test_difference_across_selections_when_asked_is_labelled():
    opts = Options(resamples=500, facet_resamples=200, composite_resamples=200, cross_selection=True)
    vs = entry(aggregate(ref_and_candidate(), opts))["vs"]["tuned"]["tiers"]["vision@core"]
    assert vs["cross_selection"] == "core vs full"
    assert vs["pass1"]["delta"] == pytest.approx(-0.25)


def test_same_selection_differences_need_no_flag():
    vs = entry(aggregate(ref_and_candidate("core"), OPTS))["vs"]["tuned"]["tiers"]["vision@core"]
    assert "cross_selection" not in vs and vs["pass1"]["delta"] == pytest.approx(-0.25)


def test_cli_compare_refuses_then_labels(tmp_path, capsys):
    a = run(tmp_path / "store", "run-core", cases())
    b = run(tmp_path / "store", "run-full", cases(sel="full", manifest=FULL))
    assert cli.main(["compare", str(a), str(b), "--resamples", "200"]) == 2
    assert "selection tier" in capsys.readouterr().err
    assert cli.main(["compare", str(a), str(b), "--resamples", "200", "--cross-selection"]) == 0
    out = capsys.readouterr().out
    assert "CROSS-SELECTION" in out and "vision@core" in out and "vision@full" in out
