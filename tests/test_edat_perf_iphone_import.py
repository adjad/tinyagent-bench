"""edat.perf.iphone_import on synthetic iPhone exports (shape of 10 §8.2; every value invented)."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from edat.perf import iphone_import as I
from edat.perf.constants import BASELINE_ID, GIB

RUN_ID = "2026-01-05T10-00-00Z-1a2b"
NS = 1_000_000_000
VARIANT = "Gemma-Wisp-q3-iphone"
CAND = "cand-phone-q4"


def smp(model, order, level, *, status="ok", n=257, decode_s=2.56, peak=4_000_000_000, **kw):
    t0 = (order + 1) * 1000 * NS
    s = {"sample_id": f"s{order:04d}", "model_id": model, "mode": "plain", "test": "level", "phase": "measured",
         "round": 1, "rep": 1, "order_index": order, "level_tokens": level, "prompt_tokens": level,
         "cached_tokens": None, "t_submit_ns": t0, "t_prefill_done_ns": t0 + NS, "t_first_token_ns": t0 + NS + 1000,
         "t_last_token_ns": t0 + NS + 1000 + int(decode_s * NS), "t_end_ns": t0 + 5 * NS, "token_times_ns": None,
         "output_tokens": n, "reasoning_tokens": 0, "footprint_peak_bytes": peak,
         "footprint_steady_bytes": peak - 100_000_000, "footprint_end_bytes": peak - 200_000_000,
         "neural_bytes": None, "available_min_bytes": 1_000_000_000, "memory_warning": False, "status": status,
         "error": None, "kill_evidence": None, "attempt": 1, "device_model_id": "iPhoneXX,Y", "os": "iOS 0.0"}
    s.update(kw)
    return s


def run_doc(**kw):
    samples = [
        smp(BASELINE_ID, 0, 2048, status="killed"),                     # older status name
        smp(VARIANT, 1, 2048), smp(VARIANT, 2, 10240, peak=5_000_000_000),
        smp(CAND, 10, 2048, n=321, decode_s=2.56), smp(CAND, 11, 10240, status="skipped_after_oom"),
        smp(CAND, 12, 2048, status="ok_memory_warning", n=321),
        smp(CAND, 13, 2048, status="invalid_not_on_battery"),
        smp(VARIANT, 20, 2048), smp(VARIANT, 21, 10240, peak=5_000_000_000),
    ]
    files = [{"name": "model.safetensors", "sha256": "0" * 64, "bytes": 3_000_000_000}]
    d = {"schema": "edat.perf/1.0", "kind": "local", "run_id": RUN_ID,
         "runner": {"name": "iphone-mlx-swift", "version": "0.1.0", "commit": "abc1234"},
         "clock": "mach_continuous_time", "started_utc": "2026-01-05T10:00:00Z", "ended_utc": "2026-01-05T11:00:00Z",
         "device": {"class": "iphone-ref", "model_id": "iPhoneXX,Y", "ram_bytes": 12 * GIB, "gpu_wired_limit_mb": None},
         "runtime": {"name": "mlx-swift", "version": "0.0.0", "prefix_cache": "none", "kv_dtype": "bf16"},
         "models": [{"id": BASELINE_ID, "entry_id": BASELINE_ID, "role": "baseline", "mode": "plain", "files": files,
                     "variant_of": None, "headline_entry": True},
                    {"id": VARIANT, "entry_id": VARIANT, "role": "variant", "mode": "plain", "files": files,
                     "variant_of": BASELINE_ID, "headline_entry": True},
                    {"id": CAND, "entry_id": CAND, "role": "candidate", "mode": "plain", "files": files,
                     "variant_of": None, "headline_entry": True}],
         "samples": samples, "events": [], "energy_windows": [],
         "balloon": {"runs": [{"last_survived_bytes": 6_000_000_000}], "budget_bytes": 6_000_000_000},
         "attachments": [{"name": f"mem-{RUN_ID}.jsonl", "sha256": "x", "kind": "footprint_log"}]}
    d.update(kw)
    return d


def write_export(folder: Path, doc=None, *, manifest_extra=None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    files = {f"run-{RUN_ID}.json": json.dumps(doc if doc is not None else run_doc()).encode(),
             f"mem-{RUN_ID}.jsonl": b'{"t_ns": 1, "footprint": 100, "available": 200, "sample_id": "s0000"}\n'}
    for name, data in files.items():
        (folder / name).write_bytes(data)
    manifest = {"schema": "edat.export-manifest/1", "run_id": RUN_ID, "record_schema": "edat.perf/1.0",
                "files": [{"name": n, "sha256": hashlib.sha256(d).hexdigest(), "bytes": len(d)} for n, d in files.items()]}
    manifest.update(manifest_extra or {})
    (folder / f"manifest-{RUN_ID}.json").write_text(json.dumps(manifest))
    return folder


def zip_folder(folder: Path, zpath: Path, extra=()) -> Path:
    with zipfile.ZipFile(zpath, "w") as z:
        for p in sorted(folder.iterdir()):
            z.write(p, f"EDAT-{RUN_ID}/{p.name}")      # the system archiver keeps the folder name
        for name, data in extra:
            z.writestr(name, data)
    return zpath


def test_import_zip_aggregates_and_normalises(tmp_path):
    z = zip_folder(write_export(tmp_path / "src"), tmp_path / f"EDAT-{RUN_ID}.zip")
    dest = I.import_export(z, tmp_path / "store")
    assert dest == tmp_path / "store" / "perf" / RUN_ID
    out = json.loads((dest / f"run-{RUN_ID}.json").read_text())
    assert (dest / f"mem-{RUN_ID}.jsonl").exists() and (dest / f"manifest-{RUN_ID}.json").exists()
    exported = (dest / f"run-{RUN_ID}.export.json").read_bytes()
    assert hashlib.sha256(exported).hexdigest() == out["import"]["record_sha256"]
    assert "cells" not in json.loads(exported)                      # the record as exported, untouched
    by_id = {s["sample_id"]: s for s in out["samples"]}
    assert (by_id["s0000"]["status"], by_id["s0000"]["status_original"]) == ("oom_killed", "killed")
    assert by_id["s0011"]["status"] == "not_run_above_oom"
    assert by_id["s0012"]["status"] == "ok" and by_id["s0012"]["memory_warning"] is True
    assert by_id["s0013"]["status"] == "voided_not_on_battery"
    assert out["import"]["statuses_normalised"] == 4
    cells = {(c["model_id"], c["level_tokens"]): c for c in out["cells"]}
    cand = cells[(CAND, 2048)]
    assert (cand["n_ok"], cand["n_voided"], cand["status"]) == (2, 1, "ok")
    # §1.6: the shipped baseline was killed at S, so iPhone ratios use the fallback variant.
    # candidate 320 / 2.56 = 125 tok/s; variant 256 / 2.56 = 100 (brackets 100 and 100) -> 1.25
    assert cand["baseline_model_id"] == VARIANT and cand["baseline_note"] == "iphone_fallback_variant"
    assert cand["ratio_to_baseline"]["decode_tps"]["ratio"] == pytest.approx(1.25)
    assert cand["fits"] == {"iphone-ref": "yes"}
    v = {x["model_id"]: x for x in out["verdicts"]}
    # balloon budget 6.0e9 -> threshold 5.4e9: variant peaks 4.0e9 (S) and 5.0e9 (W) -> fits
    assert v[VARIANT]["fits_iphone_air"] == "fits" and v[VARIANT]["ranked"] is True
    assert v[BASELINE_ID]["fits_iphone_air"] == "does_not_fit"
    assert v[CAND]["fits_iphone_air"] == "fits_small_context"       # W not run above the OOM
    assert v[CAND]["fits_16gb_mac"] is None and v[CAND]["fits_16gb_mac_reason"] == "not_a_mac_run"
    assert v[CAND]["fits_where_baseline_does_not"]["baseline_model_id"] == VARIANT


def test_import_folder_and_existing_refused(tmp_path):
    src = write_export(tmp_path / "src")
    I.import_export(src, tmp_path / "store")
    with pytest.raises(I.ImportRejected, match="already exists"):
        I.import_export(src, tmp_path / "store")
    assert I.import_export(src, tmp_path / "store", overwrite=True).exists()


def test_manifest_hash_mismatch_rejected(tmp_path):
    src = write_export(tmp_path / "src")
    (src / f"mem-{RUN_ID}.jsonl").write_bytes(b"tampered\n")
    with pytest.raises(I.ImportRejected, match="sha256 mismatch"):
        I.import_export(src, tmp_path / "store")
    assert not (tmp_path / "store" / "perf" / RUN_ID).exists()


def test_manifest_missing_file_rejected(tmp_path):
    src = write_export(tmp_path / "src")
    (src / f"mem-{RUN_ID}.jsonl").unlink()
    with pytest.raises(I.ImportRejected, match="missing"):
        I.import_export(src, tmp_path / "store")


@pytest.mark.parametrize("member", ["../evil.txt", "EDAT-x/../../evil.txt", "/abs/evil.txt", "C:/evil.txt",
                                    "..\\evil.txt"])
def test_zip_traversal_rejected(tmp_path, member):
    z = zip_folder(write_export(tmp_path / "src"), tmp_path / "bad.zip", extra=[(member, b"x")])
    with pytest.raises(I.ImportRejected, match="refused"):
        I.import_export(z, tmp_path / "store")
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "store").exists()


def test_zip_symlink_rejected(tmp_path):
    z = tmp_path / "link.zip"
    with zipfile.ZipFile(z, "w") as zf:
        info = zipfile.ZipInfo(f"EDAT-{RUN_ID}/link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/hosts")
    with pytest.raises(I.ImportRejected, match="symbolic link"):
        I.import_export(z, tmp_path / "store")


def test_old_schema_rejected(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "run.json").write_text(json.dumps({"schema": "edgebench.run/0.1", "samples": []}))
    with pytest.raises(I.ImportRejected, match="must export edat.perf/1.0"):
        I.import_export(old, tmp_path / "store")
    # the same, announced by a manifest or inside the record
    with pytest.raises(I.ImportRejected, match="must export edat.perf/1.0"):
        I.import_export(write_export(tmp_path / "m", manifest_extra={"record_schema": "edgebench.run/0.1"}),
                        tmp_path / "store")
    with pytest.raises(I.ImportRejected, match="must export edat.perf/1.0"):
        I.import_export(write_export(tmp_path / "r", doc={"schema": "edgebench.run/0.1", "run_id": RUN_ID}),
                        tmp_path / "store")


def test_wrong_runner_and_schema_rejected(tmp_path):
    d = run_doc(runner={"name": "mac-omlx", "version": "0", "commit": "x"})
    with pytest.raises(I.ImportRejected, match="runner"):
        I.import_export(write_export(tmp_path / "a", d), tmp_path / "store")
    with pytest.raises(I.ImportRejected, match="schema"):
        I.import_export(write_export(tmp_path / "b", run_doc(schema="edat.perf/0.9")), tmp_path / "store")
    with pytest.raises(I.ImportRejected, match="manifest schema"):
        I.import_export(write_export(tmp_path / "c", manifest_extra={"schema": "other/1"}), tmp_path / "store")


def test_unsafe_names_rejected(tmp_path):
    with pytest.raises(I.ImportRejected, match="unsafe or missing run_id"):
        I.import_export(write_export(tmp_path / "a", manifest_extra={"run_id": "../x"}), tmp_path / "store")
    src = write_export(tmp_path / "b")
    m = json.loads((src / f"manifest-{RUN_ID}.json").read_text())
    m["files"].append({"name": "../secret", "sha256": "0" * 64})
    (src / f"manifest-{RUN_ID}.json").write_text(json.dumps(m))
    with pytest.raises(I.ImportRejected, match="unsafe file name"):
        I.import_export(src, tmp_path / "store")


def test_cli(tmp_path, capsys):
    z = zip_folder(write_export(tmp_path / "src"), tmp_path / "e.zip")
    assert I.main([str(z), "--store", str(tmp_path / "store")]) == 0
    assert capsys.readouterr().out.strip().endswith(f"perf/{RUN_ID}")
    assert I.main([str(z), "--store", str(tmp_path / "store")]) == 2     # already imported
    assert "already exists" in capsys.readouterr().err
