"""Our aggregated run document goes through the results store's converter unchanged."""
from __future__ import annotations

import json

import pytest

from edat.perf.aggregate import aggregate
from tests.test_edat_perf_runner import entry, rig

convert = pytest.importorskip("edat.results.convert")


def test_converter_accepts_our_document(tmp_path):
    entries = [entry("Gemma-Wisp", role="baseline"), entry("gemma-4-E4B-it-oQ4e-mtp", role="reference"),
               entry("Cand", role="candidate")]
    from edat.perf.testing import FakeModel
    models = {e.id: FakeModel() for e in entries}
    r, eng, clock, _, w = rig(tmp_path / "runs", models=models, entries=entries)
    for e in entries:
        r.first_token(e, "as_shipped")
        r.levels(e, "as_shipped", (512, 2048), reps=2)
    doc_path = w.finalize(ended_utc=clock.utc(),
                          device={"class": "mac-ref", "model_id": "Mac00,0", "soc": "test", "ram_bytes": 24 << 30,
                                  "os": "macOS test", "os_build": "0"},
                          runtime={"name": "omlx", "version": "test"}, models=r.finish_models(),
                          prompt_pack={}, conditions_start={}, conditions_end={})
    agg = aggregate(json.loads(doc_path.read_text()), run_dir=w.dir)
    out = tmp_path / "run-agg.json"
    out.write_text(json.dumps(agg))
    run_dir = convert.convert_perf(out, tmp_path / "store", board_version="test.0", harness_commit="abcdef1",
                                   sample=True)
    recs = [json.loads(x) for x in (run_dir / "records.jsonl").read_text().splitlines() if x.strip()]
    measured = [s for s in agg["samples"] if s["phase"] == "measured"]
    perf = [x for x in recs if not x["case_id"].startswith("fit/")]
    assert len(perf) == len(measured)
    assert {x["status"] for x in perf} == {"ok"}
    assert all(x["tier"] == "perf" and x["schema"] == "edat.result/1.0" for x in recs)
    assert any(x["case_id"] == "level/2048" and x["metrics"]["decode_tps"] == pytest.approx(50.0) for x in perf)
    fits = [x for x in recs if x["case_id"] == "fit/16gb_mac"]
    assert fits and all(x["metrics"]["verdict"] == "not_measured" for x in fits)   # no emulation run here
