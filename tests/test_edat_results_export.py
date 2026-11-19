"""Export, snapshot diff and command-line refusals of the tinyagent-bench results store.

All data is invented. No model, no network.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from edat.results import cli, keys
from edat.results.aggregate import Options
from edat.results.export import diff_snapshots, export, load_snapshot, scrub
from edat.results.reader import read_set
from edat.results.sample import build_sample_export
from edat.results.schema import validate
from edat.results.writer import RunWriter

FAST = Options(references={"tuned": ("ref-tuned",), "base": ("ref-base",)}, resamples=500, facet_resamples=200)
H = "ab" * 32


def rec(model="cand-a", case="c1", trial=1, ok=True, *, run_id="run-a", board="b1", commit="abcdef1",
        source="author_recommended", thinking="off", split="dev", tier="agentic", role=None, unsafe=0,
        weights=H, device="mac-ref", sample=False, family="select", lane="assistant"):
    r = {"schema": "edat.result/1.0", "kind": "local", "tier": tier, "run_id": run_id, "board_version": board,
         "lang": "en",
         "model": {"id": model, "role": role or ("baseline" if model == "ref-tuned" else
                                                  "reference" if model == "ref-base" else "candidate"),
                   "weights_sha256": weights, "quant": {"scheme": "q4"}},
         "runtime": {"name": "omlx", "version": "x"},
         "device": {"id": device, "class": device, "ram_bytes": 24 * 2**30, "os": "macOS 27"},
         "settings": {"thinking": thinking, "sampling": {"temperature": 0.6, "top_p": 0.95},
                      "sampling_source": source, "seed": trial},
         "harness_commit": commit, "started_at": "2026-11-18T10:00:00Z", "case_id": case, "split": split,
         "trial": trial, "status": "ok", "facets": {"family": family, "lane": lane},
         "metrics": {"pass": ok}, "safety": {"unsafe_actions": unsafe, "privacy_leaks": 0},
         "raw": None, "raw_reason": "test"}
    if sample:
        r["sample"] = True
    return r


def write_run(store: Path, run_id: str, records: list[dict], tier: str = "agentic") -> Path:
    for r in records:
        r["run_id"] = run_id
    with RunWriter.open(store, run_id, tier=tier) as w:
        w.append_many(records)
        return w.dir


def three_models(**kw) -> list[dict]:
    out = []
    for m, p in (("ref-tuned", 0.7), ("ref-base", 0.5), ("cand-a", 0.9)):
        for c in range(10):
            for t in (1, 2, 3):
                out.append(rec(m, f"c{c}", t, ok=(c / 10) < p, **kw))
    return out


# --- the shipped sample ----------------------------------------------------

@pytest.fixture(scope="module")
def sample_export(tmp_path_factory):
    root = tmp_path_factory.mktemp("sample")
    res = build_sample_export(root / "store", root / "data", root / "site", resamples=300)
    return root, res


def test_sample_files_validate_against_their_schemas(sample_export):
    root, _ = sample_export
    for board_file in (root / "site" / "sample-0").glob("*.json"):
        if board_file.name.endswith(".trajectories.json"):
            continue
        validate(json.loads(board_file.read_text()), "site_board")
    for snap in (root / "data" / "sample-0").glob("*/snapshot.json"):
        validate(json.loads(snap.read_text()), "snapshot")
    index = json.loads((root / "site" / "index.json").read_text())
    assert index["sample"] is True and "SAMPLE" in index["notice"]


def test_sample_is_labelled_and_invented(sample_export):
    root, _ = sample_export
    text = "".join(p.read_text() for p in (root / "site").rglob("*.json"))
    assert "Gemma" not in text and "/Users/" not in text
    board = json.loads((root / "site" / "sample-0" / "mac-ref.json").read_text())
    assert board["sample"] is True and board["notice"].startswith("SAMPLE DATA")
    assert all(m["id"].startswith(("Sample ", "sample-")) for m in board["models"])


def test_sample_fit_gate_and_groups(sample_export):
    root, _ = sample_export
    board = json.loads((root / "site" / "sample-0" / "mac-ref.json").read_text())
    runs = {r["variant"]: r for r in board["runs"]}
    g = runs["local|Sample Model G 14B|q4|omlx|off"]
    assert (g["fit"]["verdict"], g["ranked"], g["rank_group"], g["status"]) == \
        ("does_not_fit", False, "does_not_fit", "does_not_fit")
    h = runs["local|Sample Model H 6B|q4|omlx|off"]
    assert (h["fit"]["verdict"], h["ranked"], h["rank_group"]) == ("not_measured", False, "not_measured")
    e = runs["local|Sample Model E 12B MoE|q4|omlx|off"]
    assert (e["fit"]["verdict"], e["ranked"]) == ("fits_small_context", True)
    f = runs["local|Sample Model F 1B|q4|omlx|off"]
    assert f["status"] == "invalid" and f["ranked"] is False and f["excluded_rate"] > 2
    ranked = {r["variant"] for r in board["ranking"]}
    assert "local|Sample Model G 14B|q4|omlx|off" not in ranked
    assert "local|Sample Model D 8B|q4|omlx|on" in runs and "local|Sample Model D 8B|q4|omlx|off" in runs
    cloud = json.loads((root / "site" / "sample-0" / "cloud.json").read_text())
    assert cloud["device"] is None and cloud["ranking"] == []
    assert all(not r["ranked"] and r["rank_group"] == "cloud" for r in cloud["runs"])


def test_sample_curves_show_failures(sample_export):
    root, _ = sample_export
    board = json.loads((root / "site" / "sample-0" / "mac-ref.json").read_text())
    statuses = {p["status"] for c in board["curves"] for p in c["points"]}
    assert {"ok", "out_of_memory", "beyond_window"} <= statuses
    oom = [p for c in board["curves"] for p in c["points"] if p["status"] == "out_of_memory"]
    assert all(p["value"] is None and p["need_bytes"] for p in oom)


def test_sample_regenerates_identically(tmp_path, sample_export):
    root, _ = sample_export
    build_sample_export(tmp_path / "store", tmp_path / "data", tmp_path / "site", resamples=300)
    for p in (root / "site").rglob("*.json"):
        assert p.read_bytes() == (tmp_path / "site" / p.relative_to(root / "site")).read_bytes(), p


def test_repository_sample_matches_the_generator(tmp_path):
    """site/data in the repository is exactly what `python -m edat.results sample` writes."""
    repo = Path(__file__).resolve().parents[1]
    shipped = repo / "site" / "data" / "index.json"
    if not shipped.exists():
        pytest.skip("no shipped sample")
    build_sample_export(tmp_path / "store", tmp_path / "data", tmp_path / "site", write_records=False)
    for p in (tmp_path / "site").rglob("*.json"):
        assert p.read_bytes() == (repo / "site" / "data" / p.relative_to(tmp_path / "site")).read_bytes(), p


# --- export rules ----------------------------------------------------------

def test_heldout_records_are_never_exported(tmp_path):
    recs = three_models()
    for r in recs:
        if r["case_id"] in ("c8", "c9"):
            r["split"] = "heldout"
    run = write_run(tmp_path / "store", "run-a", recs)
    export([read_set(run)], data_dir=tmp_path / "data", site_dir=tmp_path / "site", options=FAST)
    lines = (tmp_path / "data" / "b1" / "mac-ref" / "records.jsonl").read_text().splitlines()
    assert lines and all(json.loads(x)["split"] == "dev" for x in lines)
    snap = load_snapshot(tmp_path / "data" / "b1" / "mac-ref")
    assert snap["records_withheld_heldout"] == 3 * 2 * 3 and snap["records_exported"] == 3 * 8 * 3
    traj = json.loads((tmp_path / "site" / "b1" / "mac-ref.trajectories.json").read_text())
    assert all(t["case_id"] not in ("c8", "c9") for t in traj["trajectories"])
    # The held-out cases still count in the published score.
    e = next(e for e in snap["entries"] if e["model_id"] == "cand-a")
    assert e["tiers"]["agentic"]["pass1"]["n_cases"] == 10


def test_sample_and_real_never_share_a_site_index(tmp_path, sample_export):
    root, _ = sample_export
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.json").write_text((root / "site" / "index.json").read_text())
    run = write_run(tmp_path / "store", "run-a", three_models())
    with pytest.raises(ValueError, match="sample and real"):
        export([read_set(run)], data_dir=tmp_path / "data", site_dir=site, options=FAST)


def test_site_rows_carry_percent_and_reference_differences(tmp_path):
    run = write_run(tmp_path / "store", "run-a", three_models())
    export([read_set(run)], data_dir=tmp_path / "data", site_dir=tmp_path / "site", options=FAST)
    board = json.loads((tmp_path / "site" / "b1" / "mac-ref.json").read_text())
    row = next(r for r in board["results"] if r["variant"].startswith("local|cand-a") and r["metric"] == "pass1"
               and r["facet"] is None)
    assert row["value"] == 90.0 and row["unit"] == "%"
    assert row["vs"]["tuned"]["delta"] == pytest.approx(20.0) and row["vs"]["base"]["delta"] == pytest.approx(40.0)
    # Only 2 of 10 cases differ, so 0.8 ** 10 ≈ 11 % of resamples hold neither:
    # the 95 % interval reaches 0 and the honest tag is "no clear difference".
    assert row["vs"]["tuned"]["tag"] == "no_clear_difference" and row["vs"]["tuned"]["interval"][0] == 0.0
    assert row["vs"]["base"]["tag"] == "better"
    ref_row = next(r for r in board["results"] if r["variant"].startswith("local|ref-tuned") and r["metric"] == "pass1"
                   and r["facet"] is None)
    assert ref_row["vs"]["tuned"] == {"self": True}
    assert board["references"] == {"tuned": "local|ref-tuned|q4|omlx|off", "base": "local|ref-base|q4|omlx|off"}


def test_scrub_removes_home_paths_and_shortens():
    assert scrub("opened /Users/someone/notes.txt and /home/x/y") == "opened /Users/…/notes.txt and /home/…/y"
    assert len(scrub("a" * 2000, 100)) == 100


# --- snapshot diff ---------------------------------------------------------

def _snapshot(tmp_path: Path, name: str, **kw) -> dict:
    run = write_run(tmp_path / f"store-{name}", f"run-{name}", three_models(**kw))
    export([read_set(run)], data_dir=tmp_path / f"data-{name}", site_dir=None, options=FAST)
    board = kw.get("board", "b1")
    return load_snapshot(tmp_path / f"data-{name}" / board / "mac-ref")


def test_diff_same_board_reports_changes(tmp_path):
    a = _snapshot(tmp_path, "a")
    b = copy.deepcopy(a)
    e = next(x for x in b["entries"] if x["model_id"] == "cand-a")
    e["tiers"]["agentic"]["headline"]["value"] -= 0.1
    d = diff_snapshots(a, b)
    ch = [c for c in d["changes"] if c["entry"].startswith("local|cand-a")]
    assert ch and ch[0]["change"] == pytest.approx(-0.1)
    assert d["refused"] == [] and d["added"] == [] and d["removed"] == []


def test_diff_refuses_different_board_versions(tmp_path):
    a = _snapshot(tmp_path, "a")
    b = _snapshot(tmp_path, "b", board="b2")
    with pytest.raises(keys.IncompatibleResults, match="board version"):
        diff_snapshots(a, b)
    listed = diff_snapshots(a, b, allow_board_change=True)
    assert all("change" not in c for c in listed["changes"])


def test_diff_refuses_different_devices_and_sample_mix(tmp_path):
    a = _snapshot(tmp_path, "a")
    b = copy.deepcopy(a)
    b["device_id"] = "iphone-ref"
    with pytest.raises(keys.IncompatibleResults, match="devices"):
        diff_snapshots(a, b)
    c = copy.deepcopy(a)
    c["sample"] = True
    with pytest.raises(keys.IncompatibleResults, match="sample"):
        diff_snapshots(a, c)


def test_diff_does_not_compare_entries_whose_harness_or_sampling_changed(tmp_path):
    a = _snapshot(tmp_path, "a")
    b = _snapshot(tmp_path, "b", commit="1234567")
    d = diff_snapshots(a, b)
    assert d["refused"] and all("harness commit" in r["reason"] for r in d["refused"])
    assert not [c for c in d["changes"] if "change" in c]


# --- command line: every refusal exits 2 with a reason --------------------

def _cli(capsys, *argv) -> tuple[int, str]:
    code = cli.main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out + out.err


@pytest.mark.parametrize("field,kw,word", [
    ("sampling", {"source": "fixed_sensitivity"}, "sampling"),
    ("harness", {"commit": "1234567"}, "harness commit"),
    ("board", {"board": "b2"}, "board version"),
])
def test_cli_compare_refuses_incomparable_runs(tmp_path, capsys, field, kw, word):
    a = write_run(tmp_path / "s", "run-a", three_models())
    b = write_run(tmp_path / "s", "run-b", three_models(**kw))
    code, out = _cli(capsys, "compare", a, b, "--tuned", "ref-tuned", "--base", "ref-base", "--resamples", "200")
    assert code == cli.REFUSED and "REFUSED" in out and word in out


def test_cli_compare_refuses_different_thinking_modes(tmp_path, capsys):
    a = write_run(tmp_path / "s", "run-a", three_models())
    b = write_run(tmp_path / "s", "run-b", three_models(thinking="on"))
    code, out = _cli(capsys, "compare", a, b)
    assert code == cli.REFUSED and "thinking" in out


def test_cli_compare_accepts_comparable_runs(tmp_path, capsys):
    recs = three_models()
    a = write_run(tmp_path / "s", "run-a", [r for r in recs if r["model"]["id"] != "cand-a"])
    b = write_run(tmp_path / "s", "run-b", [r for r in recs if r["model"]["id"] == "cand-a"])
    code, out = _cli(capsys, "compare", a, b, "--tuned", "ref-tuned", "--base", "ref-base", "--resamples", "200")
    assert code == cli.OK and "cand-a" in out and "Δtuned +20.0 pp" in out


def test_cli_merge_refusals(tmp_path, capsys):
    a = write_run(tmp_path / "s", "run-a", three_models())
    b = write_run(tmp_path / "s", "run-b", three_models(source="fixed_sensitivity"))
    code, out = _cli(capsys, "merge", a, b, "--out", tmp_path / "m")
    assert code == cli.REFUSED and "sampling" in out
    c = write_run(tmp_path / "s", "run-c", three_models())
    code, out = _cli(capsys, "merge", a, c, "--out", tmp_path / "m2")
    assert code == cli.REFUSED and "both" in out          # duplicate trials


def test_cli_aggregate_refuses_two_board_versions(tmp_path, capsys):
    a = write_run(tmp_path / "s", "run-a", three_models())
    b = write_run(tmp_path / "s", "run-b", three_models(board="b2"))
    code, out = _cli(capsys, "aggregate", a, b)
    assert code == cli.REFUSED and "board versions" in out


def test_cli_validate_flags_a_damaged_run(tmp_path, capsys):
    a = write_run(tmp_path / "s", "run-a", three_models())
    assert _cli(capsys, "validate", a)[0] == cli.OK
    path = a / "records.jsonl"
    path.write_text(path.read_text().replace('"pass":true', '"pass":false', 1))
    code, out = _cli(capsys, "validate", a)
    assert code == cli.INVALID and "does not match its manifest" in out


def test_cli_export_and_diff_round_trip(tmp_path, capsys):
    a = write_run(tmp_path / "s", "run-a", three_models())
    code, _ = _cli(capsys, "export", a, "--data-dir", tmp_path / "data", "--site-dir", tmp_path / "site",
                   "--tuned", "ref-tuned", "--base", "ref-base", "--resamples", "200")
    assert code == cli.OK
    snap = tmp_path / "data" / "b1" / "mac-ref"
    code, out = _cli(capsys, "diff", snap, snap)
    assert code == cli.OK and "device mac-ref" in out


def test_cli_convert_reports_missing_information(tmp_path, capsys):
    src = tmp_path / "tb"
    src.mkdir()
    (src / "results.jsonl").write_text(json.dumps({
        "run": "20261118-100000", "model": "cand-a", "base_model": "cand-a", "variant": "default",
        "case": "c1", "category": "x", "split": "dev", "trial": 1, "steps": [], "final": "",
        "v": {"passed": True, "labels": []}}) + "\n")
    (src / "meta.json").write_text(json.dumps({"run": "20261118-100000", "git": "abcdef1",
                                               "started": 1795000000, "seeds": {"1": 1001}}))
    code, out = _cli(capsys, "convert", "toolbench", src, "--board-version", "b1", "--store", tmp_path / "st")
    assert code == cli.USAGE and "--default-thinking" in out
    code, out = _cli(capsys, "convert", "toolbench", src, "--board-version", "b1", "--store", tmp_path / "st",
                     "--default-thinking", "off")
    assert code == cli.USAGE and "--weights" in out
    code, out = _cli(capsys, "convert", "toolbench", src, "--board-version", "b1", "--store", tmp_path / "st",
                     "--weights", f"cand-a={H}", "--default-thinking", "off")
    assert code == cli.OK and "1 records" in out
