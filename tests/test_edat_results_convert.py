"""Converters: legacy and fairness-branch Toolbench, edat.perf/1.0, in-assistant (08)."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone

import pytest

from edat.results.aggregate import Options, aggregate
from edat.results.convert import (ConversionError, convert_perf, convert_productbench, convert_toolbench,
                                  device_from_host, iso_utc, weights_sha256)
from edat.results.reader import read_set
from edat.results.schema import record_errors

W64 = "a" * 64
FP64 = "c" * 64
STARTED_EPOCH = 1796120100  # 2026-12-01T10:15:00Z
STARTED_ISO = datetime.fromtimestamp(STARTED_EPOCH, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
BOARD = "2027.1"


def make_record(**overrides) -> dict:
    """A valid local `agentic` record (used to feed converted records into aggregate)."""
    rec = {
        "schema": "edat.result/1.0", "kind": "local", "tier": "agentic", "suite": "toolbench",
        "run_id": "run-a", "board_version": BOARD, "lang": "en",
        "model": {"id": "model-x", "role": "candidate", "weights_sha256": W64, "quant": {"scheme": "q4"}},
        "runtime": {"name": "omlx", "version": "0.7.3"},
        "device": {"id": "mac-ref", "class": "mac-ref", "ram_bytes": 25769803776, "os": "macOS 27.3"},
        "settings": {"thinking": "off", "sampling": {}, "sampling_source": "author_recommended", "seed": 1},
        "harness_commit": "90e3e9f", "started_at": STARTED_ISO,
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


# --- Toolbench source builders ---------------------------------------------

def verdict(passed=True, **kw) -> dict:
    v = {"passed": passed, "labels": [], "notes": [], "selection_ok": passed, "args_ok": True, "safe": True,
         "format_ok": True, "answer_ok": passed, "model_calls": 2, "discovery_calls": 0, "steps": 2,
         "seconds": 4.0, "model_seconds": 3.5, "excluded": False, "ttft": 0.8, "prompt_tokens_est": 900,
         "output_chars": 120, "reasoning_chars": 0, "guard_retries": 0}
    v.update(kw)
    return v


def tb_row(case="select-weather", trial=1, passed=True, *, variant="default", model="model-x",
           split="gate", key=None, **vkw) -> dict:
    label = model if variant == "default" else f"{model} +{variant}"
    row = {"run": "20261118-101500", "model": label, "base_model": model, "variant": variant,
           "profile_as": None, "access": "granted", "split": split, "case": case, "category": "select",
           "trial": trial, "no_tool": False, "path": "direct", "route": {"preload": ["get_weather"]},
           "steps": [{"calls": [{"name": "get_weather"}], "error": None, "ttft": 0.8, "seconds": 2.0,
                      "content": "checking the forecast"}],
           "final": "It is sunny in Testville.", "effects": [], "asked": None, "escapes": None,
           "message_draft": None, "error": None, "timeout": False, "v": verdict(passed, **vkw)}
    if key is not None:
        row["key"] = key
    return row


def write_tb(tmp_path, rows, meta=None, name="tb-run"):
    src = tmp_path / name
    src.mkdir()
    (src / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    if meta is not False:
        (src / "meta.json").write_text(json.dumps(meta if meta is not None else legacy_meta()))
    return src


def legacy_meta(**kw) -> dict:
    meta = {"run": "20261118-101500", "git": "90e3e9f1c2d3", "started": STARTED_EPOCH,
            "seeds": {"1": 1001, "2": 1002, "3": 1003}, "world_seed": 7, "thinking": ["default"]}
    meta.update(kw)
    return meta


def convert(tmp_path, rows, meta=None, **kw):
    src = write_tb(tmp_path, rows, meta)
    kw.setdefault("board_version", BOARD)
    return convert_toolbench(src, tmp_path / "store", **kw)


# --- (a) legacy Toolbench ---------------------------------------------------

def test_legacy_conversion_without_weights_refused_naming_the_flag(tmp_path):
    with pytest.raises(ConversionError, match="--weights"):
        convert(tmp_path, [tb_row()], default_thinking="off")
    assert not (tmp_path / "store").exists()


def test_legacy_default_variant_needs_default_thinking(tmp_path):
    with pytest.raises(ConversionError, match="--default-thinking"):
        convert(tmp_path, [tb_row()], weights={"model-x": FP64})


def test_legacy_conversion_with_weights_and_default_thinking(tmp_path):
    rows = [tb_row("select-weather", 1), tb_row("select-weather", 2, False), tb_row("select-mail", 1)]
    run_dir = convert(tmp_path, rows, weights={"model-x": FP64}, quant={"model-x": "q4"},
                      default_thinking="off")
    assert run_dir == tmp_path / "store" / "runs" / "20261118-101500"
    rs = read_set(run_dir, deep=True)
    assert len(rs.records) == 3
    for rec in rs.records:
        assert record_errors(rec) == []
        assert rec["settings"]["thinking"] == "off"
        assert rec["settings"]["thinking_detail"] == "default"
        assert rec["kind"] == "local" and rec["tier"] == "agentic" and rec["suite"] == "toolbench"
        assert rec["model"]["id"] == "model-x"
        assert rec["model"]["weights_fingerprint"] == FP64
        assert "weights_sha256" not in rec["model"]
        assert rec["model"]["quant"] == {"scheme": "q4"}
        assert rec["split"] == "dev"
        assert rec["facets"]["source_split"] == "gate"
        assert rec["facets"]["lane"] == "assistant"
        assert rec["started_at"] == STARTED_ISO
        assert rec["harness_commit"] == "90e3e9f1c2d3"
        assert rec["board_version"] == BOARD
        assert rec["source"]["converter"] == "toolbench"
    by = {(r["case_id"], r["trial"]): r for r in rs.records}
    assert by[("select-weather", 1)]["metrics"]["pass"] is True
    assert by[("select-weather", 2)]["metrics"]["pass"] is False
    assert by[("select-weather", 1)]["metrics"]["wall_s"] == 4.0
    assert by[("select-weather", 1)]["metrics"]["ttft_s"] == 0.8
    assert by[("select-weather", 1)]["settings"]["seed"] == 1001
    assert by[("select-weather", 2)]["settings"]["seed"] == 1002
    assert by[("select-weather", 1)]["settings"]["world_seed"] == 7
    assert "pass_within_budget" not in by[("select-weather", 1)]["metrics"]


def test_legacy_variant_off_and_on_need_no_default_thinking(tmp_path):
    rows = [tb_row(variant="off"), tb_row("c2", variant="on")]
    rs = read_set(convert(tmp_path, rows, weights={"model-x": FP64}))
    assert {r["settings"]["thinking"] for r in rs.records} == {"on", "off"}
    assert all("thinking_detail" not in r["settings"] for r in rs.records)


def test_legacy_narration_variant_keeps_its_word_as_detail(tmp_path):
    rs = read_set(convert(tmp_path, [tb_row(variant="narration-off")], weights={"model-x": FP64},
                          default_thinking="on"))
    rec = rs.records[0]
    assert rec["settings"]["thinking"] == "on"
    assert rec["settings"]["thinking_detail"] == "narration-off"


def test_excluded_verdict_becomes_excluded_status(tmp_path):
    rows = [tb_row("c1", passed=False, excluded=True, error="engine died"), tb_row("c2")]
    rs = read_set(convert(tmp_path, rows, weights={"model-x": FP64}, default_thinking="off"))
    by = {r["case_id"]: r for r in rs.records}
    assert by["c1"]["status"] == "excluded"
    assert by["c1"]["status_detail"] == "engine_error"
    assert by["c2"]["status"] == "ok"


def test_unsafe_call_label_counts_one_unsafe_action(tmp_path):
    rows = [tb_row("c1", passed=False, labels=["unsafe_call", "wrong_tool"]), tb_row("c2")]
    rs = read_set(convert(tmp_path, rows, weights={"model-x": FP64}, default_thinking="off"))
    by = {r["case_id"]: r for r in rs.records}
    assert by["c1"]["safety"]["unsafe_actions"] == 1
    assert by["c1"]["safety"]["labels"] == ["unsafe_call"]       # only the safety vocabulary is kept
    assert by["c2"]["safety"] == {"unsafe_actions": 0, "privacy_leaks": None, "labels": []}
    assert "unsafe_actions" not in by["c1"]["metrics"]


def test_two_safety_labels_count_two(tmp_path):
    rows = [tb_row("c1", passed=False, labels=["unsafe_call", "unconfirmed_effect"])]
    rec = read_set(convert(tmp_path, rows, weights={"model-x": FP64}, default_thinking="off")).records[0]
    assert rec["safety"]["unsafe_actions"] == 2


def test_raw_files_hold_steps_and_final_but_records_do_not(tmp_path):
    rs = read_set(convert(tmp_path, [tb_row()], weights={"model-x": FP64}, default_thinking="off"), deep=True)
    rec = rs.records[0]
    text = json.dumps(rec)
    assert "checking the forecast" not in text
    assert "sunny in Testville" not in text
    assert "steps" not in rec and "final" not in rec
    raw = json.loads(rs.raw_path(rec).read_text())
    assert raw["final"] == "It is sunny in Testville."
    assert raw["steps"][0]["content"] == "checking the forecast"
    assert raw["route"] == {"preload": ["get_weather"]}
    assert raw["labels"] == [] and raw["timeout"] is False


def test_meta_json_is_attached(tmp_path):
    run_dir = convert(tmp_path, [tb_row()], weights={"model-x": FP64}, default_thinking="off")
    rs = read_set(run_dir)
    attachments = {a["name"]: a for a in rs.manifest["attachments"]}
    assert set(attachments) == {"toolbench-meta.json"}
    assert attachments["toolbench-meta.json"]["kind"] == "toolbench-meta"
    data = (run_dir / "toolbench-meta.json").read_bytes()
    assert attachments["toolbench-meta.json"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert json.loads(data)["world_seed"] == 7
    assert rs.manifest["source"] == {"converter": "toolbench", "rows": 1}


def test_legacy_device_without_host_is_generic_mac(tmp_path):
    rec = read_set(convert(tmp_path, [tb_row()], weights={"model-x": FP64}, default_thinking="off")).records[0]
    assert rec["device"]["id"] == "mac" and rec["device"]["ram_bytes"] is None


def test_device_id_override_and_host_block(tmp_path):
    meta = legacy_meta(host={"chip": "Apple M5 Pro", "memory_gb": 24.0, "os": "27.3", "model": "Mac17,8",
                             "os_build": "27A1", "git_dirty": True})
    rec = read_set(convert(tmp_path, [tb_row()], meta, weights={"model-x": FP64},
                           default_thinking="off")).records[0]
    assert rec["device"] == {"id": "mac-ref", "class": "mac-ref", "model_id": "Mac17,8", "soc": "M5 Pro",
                             "ram_bytes": 24 * 2**30, "os": "macOS 27.3", "os_build": "27A1"}
    assert rec["harness_dirty"] is True


def test_device_from_host_other_machine_is_plain_mac():
    dev = device_from_host({"chip": "Apple M3", "memory_gb": 16.0, "os": "macOS 26.1"}, device_id="mac16")
    assert dev["class"] == "mac" and dev["id"] == "mac16" and dev["os"] == "macOS 26.1"


def test_harness_commit_must_be_a_sha_or_overridden(tmp_path):
    meta = legacy_meta(git="not-a-sha")
    with pytest.raises(ConversionError, match="--harness-commit"):
        convert(tmp_path, [tb_row()], meta, weights={"model-x": FP64}, default_thinking="off")


def test_harness_commit_override_accepted(tmp_path):
    src = write_tb(tmp_path, [tb_row()], legacy_meta(git="not-a-sha"))
    run_dir = convert_toolbench(src, tmp_path / "store", board_version=BOARD, weights={"model-x": FP64},
                                default_thinking="off", harness_commit="abc1234")
    assert read_set(run_dir).records[0]["harness_commit"] == "abc1234"


def test_empty_results_refused(tmp_path):
    src = tmp_path / "empty"
    src.mkdir()
    (src / "results.jsonl").write_text("\n")
    with pytest.raises(ConversionError, match="no rows"):
        convert_toolbench(src, tmp_path / "store", board_version=BOARD)


def test_converting_the_same_source_twice_is_refused(tmp_path):
    src = write_tb(tmp_path, [tb_row()])
    kw = dict(board_version=BOARD, weights={"model-x": FP64}, default_thinking="off")
    convert_toolbench(src, tmp_path / "store", **kw)
    with pytest.raises(ValueError, match="duplicate trial"):
        convert_toolbench(src, tmp_path / "store", **kw)
    assert len(read_set(tmp_path / "store" / "runs" / "20261118-101500").records) == 1


def test_sample_flag_marks_every_record(tmp_path):
    rs = read_set(convert(tmp_path, [tb_row()], weights={"model-x": FP64}, default_thinking="off", sample=True))
    assert rs.manifest["sample"] is True
    assert rs.records[0]["sample"] is True


def test_iso_utc_forms():
    assert iso_utc(STARTED_EPOCH) == STARTED_ISO
    assert iso_utc("2026-12-01T10:15:00Z") == "2026-12-01T10:15:00Z"
    assert iso_utc("2026-12-01T12:15:00+02:00") == "2026-12-01T10:15:00Z"
    assert iso_utc("2026-12-01T10:15:00") == "2026-12-01T10:15:00Z"
    with pytest.raises(ConversionError):
        iso_utc(None)


# --- (b) fairness-branch rows ----------------------------------------------

def fair_key(**kw) -> dict:
    key = {"model": "model-x", "role": "baseline", "kind": "local", "quantization": "oQ4e",
           "weights_fingerprint": FP64, "runtime": "omlx", "runtime_version": "0.7.3",
           "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 64, "stream": True},
           "sampling_source": "author_recommended", "harness": "90e3e9f", "policy": "pol-fingerprint-01"}
    key.update(kw)
    return key


def fair_meta(**kw) -> dict:
    meta = legacy_meta(policy={"latency_budget_s": 5.0, "kind": "local"},
                       host={"chip": "Apple M5 Pro", "memory_gb": 24.0, "os": "macOS 27.3"},
                       engine={"kind": "omlx", "version": "0.7.3"})
    meta.update(kw)
    return meta


def test_fairness_rows_use_their_key_block_and_need_no_weights_argument(tmp_path):
    rows = [tb_row("fast-pass", 1, True, seconds=3.0, key=fair_key()),
            tb_row("slow-pass", 1, True, seconds=9.0, key=fair_key()),
            tb_row("fast-fail", 1, False, seconds=1.0, key=fair_key())]
    rs = read_set(convert(tmp_path, rows, fair_meta(), default_thinking="off"))
    by = {r["case_id"]: r for r in rs.records}
    assert by["fast-pass"]["metrics"]["pass_within_budget"] is True
    assert by["slow-pass"]["metrics"]["pass_within_budget"] is False
    assert by["fast-fail"]["metrics"]["pass_within_budget"] is False
    assert by["slow-pass"]["metrics"]["pass"] is True
    rec = by["fast-pass"]
    assert rec["model"]["role"] == "baseline"
    assert rec["model"]["weights_fingerprint"] == FP64
    assert rec["model"]["quant"] == {"scheme": "oQ4e"}
    assert rec["runtime"] == {"name": "omlx", "version": "0.7.3"}
    assert rec["settings"]["sampling"] == {"temperature": 1.0, "top_p": 0.95, "top_k": 64}
    assert rec["settings"]["sampling_source"] == "author_recommended"
    assert rec["settings"]["policy_fingerprint"] == "pol-fingerprint-01"
    assert rec["harness_commit"] == "90e3e9f"
    assert rec["device"]["class"] == "mac-ref"


def test_budget_comparison_is_inclusive(tmp_path):
    rows = [tb_row("edge", 1, True, seconds=5.0, key=fair_key())]
    assert read_set(convert(tmp_path, rows, fair_meta(), default_thinking="off")
                    ).records[0]["metrics"]["pass_within_budget"] is True


def test_no_budget_means_no_pass_within_budget(tmp_path):
    meta = fair_meta()
    meta["policy"] = {"kind": "local"}
    rec = read_set(convert(tmp_path, [tb_row(key=fair_key())], meta, default_thinking="off")).records[0]
    assert "pass_within_budget" not in rec["metrics"]


def test_policy_fingerprint_falls_back_to_meta_policy(tmp_path):
    meta = fair_meta()
    meta["policy"]["fingerprint"] = "meta-policy-fp"
    row = tb_row(key=fair_key(policy=None))
    rec = read_set(convert(tmp_path, [row], meta, default_thinking="off")).records[0]
    assert rec["settings"]["policy_fingerprint"] == "meta-policy-fp"


def cloud_key(**kw) -> dict:
    key = {"model": "vendor/model-c", "role": "cloud-reference", "kind": "cloud-reference",
           "sampling": {"temperature": 1.0}, "sampling_source": "provider_default", "harness": "90e3e9f",
           "runtime": "openrouter", "requested_model": "vendor/model-c",
           "resolved_model": "vendor/model-c-2027", "upstream_provider": "provider-p",
           "run_date": {"first": "2026-12-01T10:00:00Z", "last": "2026-12-01T11:00:00Z"}}
    key.update(kw)
    return key


def test_cloud_reference_hyphen_kind_maps_to_cloud_reference(tmp_path):
    rows = [tb_row(variant="minimal", model="vendor/model-c", key=cloud_key())]
    meta = fair_meta()
    meta["policy"] = {"kind": "cloud-reference"}
    rs = read_set(convert(tmp_path, rows, meta))
    rec = rs.records[0]
    assert record_errors(rec) == []
    assert rec["kind"] == "cloud_reference"
    assert rec["device"] is None and rec["device_reason"] == "cloud"
    assert rec["model"]["role"] == "cloud_reference"
    assert rec["model"]["requested_model"] == "vendor/model-c"
    assert rec["model"]["resolved_model"] == "vendor/model-c-2027"
    assert rec["model"]["upstream_provider"] == "provider-p"
    assert rec["model"]["run_date"]["first"] == "2026-12-01T10:00:00Z"
    assert rec["settings"]["thinking"] == "minimal"
    assert rec["settings"]["sampling_source"] == "provider_default"
    assert rec["runtime"]["name"] == "openrouter"
    assert "weights_fingerprint" not in rec["model"]


def test_cloud_kind_from_meta_policy_only(tmp_path):
    key = cloud_key()
    key.pop("kind")
    meta = fair_meta()
    meta["policy"] = {"kind": "cloud-reference"}
    rec = read_set(convert(tmp_path, [tb_row(variant="off", model="vendor/model-c", key=key)], meta)).records[0]
    assert rec["kind"] == "cloud_reference"


@pytest.mark.parametrize("missing", ["requested_model", "resolved_model", "upstream_provider", "run_date"])
def test_cloud_reference_missing_hosted_field_refused(tmp_path, missing):
    key = cloud_key()
    key.pop(missing)
    with pytest.raises(ConversionError, match=missing):
        convert(tmp_path, [tb_row(variant="off", model="vendor/model-c", key=key)], fair_meta())


def test_cloud_sampling_source_defaults_to_provider_default(tmp_path):
    key = cloud_key()
    key.pop("sampling_source")
    rec = read_set(convert(tmp_path, [tb_row(variant="off", model="vendor/model-c", key=key)],
                           fair_meta())).records[0]
    assert rec["settings"]["sampling_source"] == "provider_default"


# --- (c) edat.perf/1.0 ------------------------------------------------------

FILES_X = [{"name": "model-00002.safetensors", "sha256": "2" * 64, "bytes": 1_000},
           {"name": "model-00001.safetensors", "sha256": "1" * 64, "bytes": 2_000}]
FILES_Y = [{"name": "weights.safetensors", "sha256": "3" * 64, "bytes": 9_000}]
PERF_RUN_ID = "2026-12-01T10-15-00Z-7f3c"


def sample(model="model-x", test="level", level=2048, rep=1, status="ok", phase="measured", **kw) -> dict:
    s = {"sample_id": f"{model}-{test}-{level}-{rep}-{phase}-{status}", "model_id": model, "mode": "as_shipped",
         "test": test, "phase": phase, "rep": rep, "level_tokens": level, "prompt_tokens": 2048,
         "t_submit_ns": 1_000_000_000, "t_prefill_done_ns": 1_400_000_000, "t_first_token_ns": 1_500_000_000,
         "t_last_token_ns": 3_500_000_000, "t_end_ns": 3_600_000_000, "output_tokens": 101,
         "reasoning_tokens": 0, "footprint_peak_bytes": 6_000_000_000, "swap_delta_bytes": 0, "status": status}
    s.update(kw)
    return s


def perf_doc(**kw) -> dict:
    doc = {
        "schema": "edat.perf/1.0", "kind": "local", "run_id": PERF_RUN_ID,
        "runner": {"name": "mac-omlx", "version": "1.0", "commit": "90e3e9f"},
        "started_utc": "2026-12-01T10:15:00Z", "ended_utc": "2026-12-01T11:00:00Z",
        "device": {"class": "mac-ref", "model_id": "Mac17,8", "soc": "M5 Pro", "ram_bytes": 25769803776,
                   "os": "macOS 27.3", "os_build": "27A1"},
        "runtime": {"name": "omlx", "version": "0.7.3", "backend": "metal"},
        "models": [
            {"id": "model-x", "role": "candidate", "mode": "as_shipped", "files": copy.deepcopy(FILES_X),
             "quant": {"scheme": "q4", "bits": 4.5, "group_size": 64, "mixed": False},
             "architecture": "dense", "params_total": 4_000_000_000, "params_active": 4_000_000_000,
             "thinking": "off"},
            {"id": "model-y", "role": "variant", "mode": "as_shipped", "files": copy.deepcopy(FILES_Y),
             "quant": {"scheme": "q8", "bits": 8.0}, "architecture": "moe", "thinking": "off",
             "variant_of": "model-x"}],
        "samples": [
            sample(phase="warmup", rep=0),
            sample(rep=1),
            sample(rep=2, t_first_token_ns=1_700_000_000, t_last_token_ns=4_700_000_000, output_tokens=61),
            sample(level=4096, rep=1, status="ok_extended"),
            sample("model-y", level=8192, rep=1, status="oom_killed", footprint_peak_bytes=None,
                   kill_evidence={"last_footprint_bytes": 11_000_000_000}),
            sample("model-y", level=16384, rep=1, status="exceeds_native_window"),
            sample("model-y", level=32768, rep=1, status="not_run_above_oom"),
            sample("model-y", level=2048, rep=1, status="oom_killed_at_load", footprint_peak_bytes=None),
            sample("model-x", test="load_cold", level=None, rep=1, prompt_tokens=None,
                   t_prefill_done_ns=None),
        ],
        "events": [], "energy_windows": [],
        "verdicts": [
            {"model_id": "model-x", "fits_16gb_mac": "fits",
             "fits_16gb_mac_detail": {"budget_bytes": 10 * 2**30, "budget_source": "derived-v1",
                                      "peak_bytes_at_2048": 6_000_000_000, "peak_bytes_at_10240": 8_000_000_000,
                                      "max_level_fits_16gb": 10240},
             "fits_iphone_air": "fits_small_context", "fits_iphone_air_detail": {"max_level_fits": 2048}},
            {"model_id": "model-y", "fits_16gb_mac": "does_not_fit", "fits_16gb_mac_detail": {},
             "fits_iphone_air": "not_applicable"}],
    }
    doc.update(kw)
    return doc


def write_perf(tmp_path, doc=None, name="perf-run.json"):
    path = tmp_path / name
    path.write_text(json.dumps(doc or perf_doc()))
    return path


def convert_perf_doc(tmp_path, doc=None, **kw):
    kw.setdefault("board_version", BOARD)
    return convert_perf(write_perf(tmp_path, doc), tmp_path / "store", **kw)


def perf_records(tmp_path, doc=None, **kw):
    return read_set(convert_perf_doc(tmp_path, doc, **kw), deep=True).records


def find(records, model, case, trial=1):
    found = [r for r in records if r["model"]["id"] == model and r["case_id"] == case and r["trial"] == trial]
    assert len(found) == 1, (model, case, trial, [(r["model"]["id"], r["case_id"], r["trial"]) for r in records])
    return found[0]


def test_perf_non_edat_perf_schema_refused(tmp_path):
    doc = perf_doc(schema="edgebench.run/0.1")
    with pytest.raises(ConversionError, match="edat.perf/1.0"):
        convert_perf_doc(tmp_path, doc)
    assert not (tmp_path / "store").exists()


def test_perf_local_run_without_device_refused(tmp_path):
    doc = perf_doc()
    doc["device"] = None
    with pytest.raises(ConversionError, match="device"):
        convert_perf_doc(tmp_path, doc)


def test_perf_all_records_validate_and_carry_run_identity(tmp_path):
    records = perf_records(tmp_path)
    assert records
    for rec in records:
        assert record_errors(rec) == []
        assert rec["tier"] == "perf" and rec["kind"] == "local"
        assert rec["run_id"] == PERF_RUN_ID
        assert rec["board_version"] == BOARD
        assert rec["harness_commit"] == "90e3e9f"
        assert rec["device"]["id"] == "mac-ref" and rec["device"]["soc"] == "M5 Pro"
        assert rec["runtime"] == {"name": "omlx", "version": "0.7.3", "backend": "metal"}
        assert rec["started_at"] == "2026-12-01T10:15:00Z"
        assert rec["split"] == "dev"
        assert rec["safety"] == {"unsafe_actions": None, "privacy_leaks": None}
        assert rec["settings"]["thinking"] == "off"
        assert rec["settings"]["sampling_source"] == "engine_default"


def test_perf_measured_samples_become_level_records_and_warmups_are_skipped(tmp_path):
    records = perf_records(tmp_path)
    level = [r for r in records if r["case_id"].startswith("level/") and r["model"]["id"] == "model-x"]
    assert sorted((r["case_id"], r["trial"]) for r in level) == [("level/2048", 1), ("level/2048", 2),
                                                                  ("level/4096", 1)]
    rec = find(records, "model-x", "level/2048", 1)
    assert rec["facets"] == {"test": "level", "level_tokens": 2048}
    assert rec["status"] == "ok"
    assert "status_detail" not in rec


def test_perf_trial_numbering_counts_per_model_and_case_from_one_ignoring_rep(tmp_path):
    doc = perf_doc()
    doc["samples"] = [sample(rep=7), sample(rep=3), sample("model-y", rep=9), sample(level=4096, rep=5)]
    records = perf_records(tmp_path, doc)
    level = {(r["model"]["id"], r["case_id"], r["trial"]) for r in records if r["case_id"].startswith("level")}
    assert level == {("model-x", "level/2048", 1), ("model-x", "level/2048", 2),
                     ("model-y", "level/2048", 1), ("model-x", "level/4096", 1)}


def test_perf_metrics_from_nanosecond_timestamps(tmp_path):
    records = perf_records(tmp_path)
    m = find(records, "model-x", "level/2048", 1)["metrics"]
    assert m["ttft_s"] == pytest.approx(0.5)                    # (1.5e9 - 1.0e9) ns
    assert m["e2e_s"] == pytest.approx(2.6)
    assert m["decode_tps"] == pytest.approx(50.0)               # (101 - 1) tokens / (3.5e9 - 1.5e9) ns
    assert m["prefill_tps"] == pytest.approx(2048 / 0.4)        # prompt tokens / (1.4e9 - 1.0e9) ns
    assert m["prompt_tokens"] == 2048 and m["output_tokens"] == 101
    assert m["footprint_peak_bytes"] == 6_000_000_000
    second = find(records, "model-x", "level/2048", 2)["metrics"]
    assert second["ttft_s"] == pytest.approx(0.7)
    assert second["decode_tps"] == pytest.approx(60 / 3.0)


def test_perf_prefill_falls_back_to_first_token_when_no_prefill_timestamp(tmp_path):
    doc = perf_doc()
    doc["samples"] = [sample(t_prefill_done_ns=None)]
    m = perf_records(tmp_path, doc)[0]["metrics"]
    assert m["prefill_tps"] == pytest.approx(2048 / 0.5)


def test_perf_missing_timestamps_leave_metrics_out(tmp_path):
    doc = perf_doc()
    doc["samples"] = [sample(t_first_token_ns=None, t_last_token_ns=None, output_tokens=1)]
    m = perf_records(tmp_path, doc)[0]["metrics"]
    assert "ttft_s" not in m and "decode_tps" not in m


def test_perf_status_mapping_keeps_the_tiers_own_word(tmp_path):
    records = perf_records(tmp_path)
    oom = find(records, "model-y", "level/8192")
    assert oom["status"] == "out_of_memory" and oom["status_detail"] == "oom_killed"
    assert oom["metrics"]["footprint_peak_bytes"] == 11_000_000_000          # from kill_evidence
    load = find(records, "model-y", "level/2048")
    assert load["status"] == "out_of_memory" and load["status_detail"] == "oom_killed_at_load"
    window = find(records, "model-y", "level/16384")
    assert window["status"] == "beyond_window" and window["status_detail"] == "exceeds_native_window"
    skipped = find(records, "model-y", "level/32768")
    assert skipped["status"] == "not_run" and skipped["status_detail"] == "not_run_above_oom"
    extended = find(records, "model-x", "level/4096")
    assert extended["status"] == "ok" and extended["status_detail"] == "ok_extended"


@pytest.mark.parametrize("word,mapped", [
    ("truncated", "ok"), ("memory_pressure", "ok"), ("timeout", "ok"),
    ("voided_backgrounded", "excluded"), ("voided_foreign_work", "excluded"), ("engine_refused", "excluded"),
    ("killed_unknown", "excluded"), ("unsupported_by_runtime", "unsupported_by_runtime"),
])
def test_perf_other_status_mappings(tmp_path, word, mapped):
    doc = perf_doc()
    doc["samples"] = [sample(status=word)]
    rec = perf_records(tmp_path, doc)[0]
    assert rec["status"] == mapped
    if word == mapped:
        assert "status_detail" not in rec      # the tier's word is kept only when it differs
    else:
        assert rec["status_detail"] == word


def test_perf_unknown_status_refused(tmp_path):
    doc = perf_doc()
    doc["samples"] = [sample(status="exploded")]
    with pytest.raises(ConversionError, match="unknown perf status"):
        convert_perf_doc(tmp_path, doc)


def test_perf_sample_for_unknown_model_refused(tmp_path):
    doc = perf_doc()
    doc["samples"] = [sample("ghost")]
    with pytest.raises(ConversionError, match="ghost"):
        convert_perf_doc(tmp_path, doc)


def test_perf_level_none_uses_level_zero(tmp_path):
    rec = find(perf_records(tmp_path), "model-x", "load_cold/0")
    assert rec["facets"] == {"test": "load_cold", "level_tokens": 0}
    assert "prefill_tps" not in rec["metrics"]


def test_perf_model_identity_hash_quant_and_bytes(tmp_path):
    rec = find(perf_records(tmp_path), "model-x", "level/2048")
    lines = "".join(f"{f['name']}\0{f['sha256']}\n" for f in sorted(FILES_X, key=lambda f: f["name"]))
    assert rec["model"]["weights_sha256"] == hashlib.sha256(lines.encode()).hexdigest()
    assert weights_sha256(FILES_X) == rec["model"]["weights_sha256"]
    assert weights_sha256(list(reversed(FILES_X))) == rec["model"]["weights_sha256"]
    assert rec["model"]["weights_bytes"] == 3_000
    assert rec["model"]["quant"] == {"scheme": "q4", "bits": 4.5, "group_size": 64, "mixed": False}
    assert rec["model"]["architecture"] == "dense"
    assert rec["model"]["role"] == "candidate"
    other = find(read_set(tmp_path / "store" / "runs" / PERF_RUN_ID).records, "model-y", "level/8192")
    assert other["model"]["role"] == "variant" and other["model"]["variant_of"] == "model-x"


def test_weights_sha256_none_when_a_file_lacks_a_hash():
    assert weights_sha256([{"name": "a", "sha256": None, "bytes": 1}]) is None
    assert weights_sha256([]) is None
    assert weights_sha256(None) is None


def test_perf_verdicts_become_fit_records(tmp_path):
    records = perf_records(tmp_path)
    fit16 = find(records, "model-x", "fit/16gb_mac")
    assert fit16["status"] == "ok"
    assert fit16["facets"] == {"test": "fit16_verdict"}
    assert fit16["metrics"] == {"verdict": "fits", "max_level_fits": 10240, "peak_bytes_at_2048": 6_000_000_000,
                                "peak_bytes_at_10240": 8_000_000_000, "budget_bytes": 10 * 2**30,
                                "budget_source": "derived-v1"}
    iphone = find(records, "model-x", "fit/iphone_air")
    assert iphone["metrics"]["verdict"] == "fits_small_context"
    assert iphone["metrics"]["max_level_fits"] == 2048
    assert iphone["facets"] == {"test": "fit_iphone_verdict"}
    assert find(records, "model-y", "fit/16gb_mac")["metrics"] == {"verdict": "does_not_fit"}
    assert not [r for r in records if r["model"]["id"] == "model-y" and r["case_id"] == "fit/iphone_air"]


def test_perf_attaches_the_document_and_raw_holds_the_sample(tmp_path):
    path = write_perf(tmp_path)
    run_dir = convert_perf(path, tmp_path / "store", board_version=BOARD)
    rs = read_set(run_dir, deep=True)
    attachment = rs.manifest["attachments"][0]
    assert attachment["kind"] == "edat.perf/1.0"
    assert attachment["name"] == f"run-{PERF_RUN_ID}.json"
    assert (run_dir / attachment["name"]).read_bytes() == path.read_bytes()
    assert attachment["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    rec = find(rs.records, "model-x", "level/2048", 1)
    raw = json.loads(rs.raw_path(rec).read_text())
    assert raw["t_first_token_ns"] == 1_500_000_000
    assert "t_first_token_ns" not in json.dumps(rec)
    assert rs.manifest["source"] == {"converter": "perf", "samples": 9, "verdicts": 2}


def test_perf_device_id_override(tmp_path):
    records = perf_records(tmp_path, device_id="mac24")
    assert {r["device"]["id"] for r in records} == {"mac24"}
    assert {r["device"]["class"] for r in records} == {"mac-ref"}


def test_perf_cloud_run_has_no_device_and_no_verdict_records(tmp_path):
    doc = perf_doc(kind="cloud_reference", device=None)
    doc["models"] = [{"id": "vendor/model-c", "role": "cloud_reference", "mode": "as_shipped",
                      "resolved_model": "vendor/model-c-2027", "upstream_provider": "provider-p",
                      "run_date": {"first": "2026-12-01T10:00:00Z", "last": "2026-12-01T11:00:00Z"}}]
    doc["samples"] = [sample("vendor/model-c", test="cloud_level", net_ttft_s=0.4, cost_usd=0.001,
                             footprint_peak_bytes=None)]
    doc["verdicts"] = [{"model_id": "vendor/model-c", "fits_16gb_mac": "not_applicable"}]
    records = perf_records(tmp_path, doc)
    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "cloud_reference" and rec["device"] is None and rec["device_reason"] == "cloud"
    assert rec["settings"]["sampling_source"] == "provider_default"
    assert rec["metrics"]["cost_usd"] == 0.001 and rec["metrics"]["net_ttft_s"] == 0.4
    assert rec["model"]["requested_model"] == "vendor/model-c"


# --- perf + agentic through aggregate: the fit gate ------------------------

def agentic_for(model, quant, n=6, k=4, weights=None):
    out = []
    for i in range(n):
        out.append(make_record(run_id="run-agentic", case_id=f"c{i}", metrics={"pass": i < k},
                               model={"id": model, "weights_sha256": weights,
                                      "quant": {"scheme": quant}}))
    return out


def test_converted_perf_verdicts_link_to_agentic_entries(tmp_path):
    perf = perf_records(tmp_path)
    sha_x = find(perf, "model-x", "level/2048")["model"]["weights_sha256"]
    sha_y = find(perf, "model-y", "level/8192")["model"]["weights_sha256"]
    agentic = agentic_for("model-x", "q4", weights=sha_x) + agentic_for("model-y", "q8", weights=sha_y)
    result = aggregate(agentic + perf, Options(resamples=500, facet_resamples=200))
    by_model = {e["model_id"]: e for e in result["entries"] if e["device_id"] == "mac-ref"}
    x, y = by_model["model-x"], by_model["model-y"]
    assert x["fit"]["verdict"] == "fits"
    assert x["fit"]["max_level_fits"] == 10240
    assert x["ranked"] is True and x["rank_group"] == "ranked"
    # Toolbench rows are their own view, never averaged with the reference loop's agentic cases.
    assert "agentic" not in x["tiers"]
    assert x["tiers"]["toolbench"]["pass1"]["value"] == pytest.approx(4 / 6)
    assert "perf" in x["tiers"]
    assert y["fit"]["verdict"] == "does_not_fit"
    assert y["ranked"] is False and y["rank_group"] == "does_not_fit"
    curve = {p["level"]: p for p in y["tiers"]["perf"]["curves"]["ttft_s"]}
    assert curve[8192]["status"] == "out_of_memory"
    assert curve[8192]["need_bytes"] == 11_000_000_000
    assert curve[16384]["status"] == "beyond_window"


def test_fit_gate_does_not_link_when_the_quant_scheme_differs(tmp_path):
    perf = perf_records(tmp_path)
    sha_x = find(perf, "model-x", "level/2048")["model"]["weights_sha256"]
    result = aggregate(agentic_for("model-x", "q8", weights=sha_x) + perf, Options(resamples=300,
                                                                                     facet_resamples=100))
    e = [x for x in result["entries"] if x["model_id"] == "model-x" and x["quant"] == "q8"][0]
    assert e["fit"]["verdict"] == "not_measured" and e["ranked"] is False


# --- (d) in-assistant (08) --------------------------------------------------

PB_RUN = "edat-iae-20261118-175000"


def pb_row(**kw) -> dict:
    row = {"run": PB_RUN, "entry": "model-x", "model": "model-x", "kind": "local", "ranked": True, "stub": False,
           "date": "2026-11-18", "profile": "student", "trial": 2, "now": "07:45",
           "variant": {"model_id": "model-x", "folder": "model-x-q4", "quantisation": "oQ4e",
                       "weights_sha256": W64, "engine": "omlx", "thinking": "harbor", "fits_16gb": True},
           "device": {"model": "Mac17,8", "chip": "Apple M5 Pro", "memory_gb": 24, "arch": "arm64",
                      "os": "macOS 27.3"},
           "runtime": {"engine": "omlx", "port": 8000, "version": "0.7.3"},
           "surface": "ask", "case": "Q-L-student-07", "family": "changed", "prompt": "What changed today?",
           "captured": {"answer": "Your 9am moved to 10am (invented answer).", "tools": ["view_emails"],
                        "tool_calls": [{"name": "view_emails", "args": {}, "result": "three invented emails"}],
                        "confirms": [], "drafts": [], "actions": [], "model": "model-x", "elapsed_s": 9.4,
                        "first_token_s": 2.1, "bench_now": 1791880000.0, "error": "",
                        "usage": {"calls": 3, "prompt_tokens": 18210, "completion_tokens": 412,
                                  "reasoning_tokens": None, "cost_reported_usd": None, "first_byte_s_p50": 0.9,
                                  "call_s_total": 8.7, "models": ["model-x"], "providers": [], "errors": 0,
                                  "sampling_sent": ["{}"]}},
           "score": {"score": 1.0, "required": [], "contradicted": [], "invented": [], "tools": {},
                     "action": None, "unsafe": [], "readability": {"score": 92.3, "checks": {}}}}
    row.update(kw)
    return row


def write_pb(tmp_path, rows, meta=None, name="pb-run"):
    src = tmp_path / name
    src.mkdir()
    (src / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (src / "meta.json").write_text(json.dumps(meta or {"git_sha": "90e3e9f", "run_date": "2026-11-18",
                                                          "language": "en"}))
    return src


def convert_pb(tmp_path, rows, meta=None, name="pb-run", **kw):
    kw.setdefault("board_version", BOARD)
    kw.setdefault("default_thinking", "off")
    kw.setdefault("device_id", "mac-ref")
    return convert_productbench(write_pb(tmp_path, rows, meta, name), tmp_path / "store", **kw)


def test_productbench_row_converts_and_validates(tmp_path):
    run_dir = convert_pb(tmp_path, [pb_row()])
    rs = read_set(run_dir, deep=True)
    rec = rs.records[0]
    assert record_errors(rec) == []
    assert rs.run_ids == [PB_RUN]
    assert rec["tier"] == "in_assistant" and rec["suite"] == "productbench" and rec["kind"] == "local"
    assert rec["case_id"] == "student/ask/Q-L-student-07@07:45"
    assert rec["trial"] == 2
    assert rec["facets"] == {"surface": "ask", "profile": "student", "family": "changed"}
    assert rec["model"]["weights_sha256"] == W64
    assert rec["model"]["quant"] == {"scheme": "oQ4e"}
    assert rec["settings"]["thinking"] == "off"
    assert rec["settings"]["thinking_detail"] == "harbor"
    assert rec["settings"]["sampling_source"] == "author_recommended"
    assert rec["harness_commit"] == "90e3e9f"
    assert rec["started_at"] == "2026-11-18T00:00:00Z"
    assert rec["status"] == "ok"
    assert rec["device"] == {"id": "mac-ref", "class": "mac-ref", "model_id": "Mac17,8", "soc": "M5 Pro",
                             "ram_bytes": 24 * 2**30, "os": "macOS 27.3"}
    assert rec["runtime"] == {"name": "omlx", "version": "0.7.3"}
    assert rec["metrics"] == {"score": 1.0, "pass": True, "readability": 92.3, "invented": 0, "wall_s": 9.4,
                              "ttft_s": 2.1, "prompt_tokens": 18210, "completion_tokens": 412}
    assert rec["safety"] == {"unsafe_actions": 0, "privacy_leaks": None, "labels": []}


def test_productbench_captured_output_lives_only_in_raw(tmp_path):
    rs = read_set(convert_pb(tmp_path, [pb_row()]), deep=True)
    rec = rs.records[0]
    text = json.dumps(rec)
    for secret in ("invented answer", "three invented emails", "What changed today"):
        assert secret not in text
    assert "captured" not in rec and "prompt" not in rec
    raw = json.loads(rs.raw_path(rec).read_text())
    assert raw["captured"]["answer"].startswith("Your 9am moved")
    assert raw["captured"]["tool_calls"][0]["result"] == "three invented emails"
    assert raw["prompt"] == "What changed today?"
    assert raw["score"]["readability"]["score"] == 92.3
    assert (tmp_path / "store" / "runs" / PB_RUN / "productbench-meta.json").exists()


def test_productbench_needs_default_thinking_for_harness_words(tmp_path):
    with pytest.raises(ConversionError, match="--default-thinking"):
        convert_pb(tmp_path, [pb_row()], default_thinking=None)


def test_productbench_needs_a_full_weights_hash(tmp_path):
    row = pb_row()
    row["variant"]["weights_sha256"] = "…"
    with pytest.raises(ConversionError, match="weights_sha256"):
        convert_pb(tmp_path, [row])
    row["variant"]["weights_sha256"] = None
    with pytest.raises(ConversionError, match="weights_sha256"):
        convert_pb(tmp_path, [row], name="pb-run-2")


def test_productbench_partial_score_is_not_a_pass(tmp_path):
    rec = read_set(convert_pb(tmp_path, [pb_row(score={"score": 0.75, "unsafe": [], "invented": ["x", "y"]})])
                   ).records[0]
    assert rec["metrics"]["score"] == 0.75
    assert rec["metrics"]["pass"] is False
    assert rec["metrics"]["invented"] == 2


def test_productbench_unsafe_actions_counted_in_safety_block_only(tmp_path):
    row = pb_row(score={"score": 0.0, "unsafe": ["sent_email"], "invented": []})
    rec = read_set(convert_pb(tmp_path, [row])).records[0]
    assert rec["safety"]["unsafe_actions"] == 1
    assert rec["safety"]["labels"] == ["unconfirmed_effect"]
    assert "unsafe" not in json.dumps(rec["metrics"])


def test_productbench_capture_error_without_score_is_failed(tmp_path):
    row = pb_row(score={})
    row["captured"]["error"] = "engine timeout"
    rec = read_set(convert_pb(tmp_path, [row])).records[0]
    assert rec["status"] == "failed"
    assert "pass" not in rec["metrics"] and "score" not in rec["metrics"]


def test_productbench_case_without_now_and_odd_characters(tmp_path):
    row = pb_row(now=None, case="Q one/two", family="Mixed Family")
    rec = read_set(convert_pb(tmp_path, [row])).records[0]
    assert rec["case_id"] == "student/ask/Q_one/two"
    assert rec["facets"]["family"] == "mixed_family"


def test_productbench_several_rows_keep_trial_and_case_identity(tmp_path):
    rows = [pb_row(trial=t, case=c) for t in (1, 2) for c in ("Q-1", "Q-2")]
    rs = read_set(convert_pb(tmp_path, rows))
    assert sorted((r["case_id"], r["trial"]) for r in rs.records) == sorted(
        (f"student/ask/{c}@07:45", t) for t in (1, 2) for c in ("Q-1", "Q-2"))


def test_productbench_empty_refused_and_commit_override(tmp_path):
    with pytest.raises(ConversionError, match="no rows"):
        convert_pb(tmp_path, [])
    src = write_pb(tmp_path, [pb_row()], {"run_date": "2026-11-18"}, name="pb-run-2")
    with pytest.raises(ConversionError, match="harness commit"):
        convert_productbench(src, tmp_path / "store2", board_version=BOARD, default_thinking="off")
    run_dir = convert_productbench(src, tmp_path / "store2", board_version=BOARD, default_thinking="off",
                                   harness_commit="abc1234")
    assert read_set(run_dir).records[0]["harness_commit"] == "abc1234"


def test_productbench_cloud_row(tmp_path):
    row = pb_row(kind="cloud_reference", model="vendor/model-c")
    row["variant"] = {"model_id": "vendor/model-c", "thinking": "minimal"}
    row["cloud"] = {"resolved_model": "vendor/model-c-2027", "upstream_provider": "provider-p",
                    "run_date": {"first": "2026-11-18T10:00:00Z", "last": "2026-11-18T11:00:00Z"}}
    rec = read_set(convert_pb(tmp_path, [row])).records[0]
    assert rec["kind"] == "cloud_reference" and rec["device"] is None
    assert rec["model"]["requested_model"] == "vendor/model-c"
    assert rec["settings"]["thinking"] == "minimal"
    assert rec["settings"]["sampling_source"] == "provider_default"


# --- the iPhone runner's export (tinyagent-bench-<run_id>.zip) -------------

PHONE_RUN_ID = "2026-12-02T08-00-00Z-ab12"


def phone_doc() -> dict:
    """The runner's run document shape: mixed-bit weights as a bit-width -> layer-count map,
    a speculation field and a load block on each sample (invented values)."""
    doc = perf_doc(run_id=PHONE_RUN_ID)
    doc["device"] = {"class": "iphone-ref", "model_id": "iPhone99,1", "soc": "A99 Pro", "ram_bytes": 12 * 2**30,
                     "os": "iOS 27.1", "os_build": "27B1"}
    doc["runtime"] = {"name": "mlx-swift", "version": "0.30.0", "backend": "metal"}
    doc["models"] = [copy.deepcopy(doc["models"][0])]
    doc["models"][0]["quant"] = {"scheme": "mlx-affine", "bits": 4, "group_size": 64, "mixed": {"5": 84, "6": 18}}
    doc["models"][0]["speculation"] = "mtp"
    doc["samples"] = [s for s in doc["samples"] if s["model_id"] == "model-x"]
    doc["samples"][1]["load"] = {"t_load_start_ns": 1_000_000_000, "t_ready_ns": 3_500_000_000,
                                 "t_first_token_possible_ns": 4_250_000_000, "cold_verified": False}
    doc["verdicts"] = [doc["verdicts"][0]]
    return doc


def write_export(tmp_path, *, doc=None, tamper=None, extra_entry=None, manifest=True, nested=True):
    import zipfile
    files = {f"run-{PHONE_RUN_ID}.json": json.dumps(doc or phone_doc()).encode(),
             f"mem-{PHONE_RUN_ID}.jsonl": b'{"t_ns": 1, "footprint_bytes": 1000}\n',
             f"phone-{PHONE_RUN_ID}.jsonl": b'{"schema": "edat-phone-result/v1", "case_id": "x", "pass": true}\n'}
    listed = [{"name": n, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)} for n, b in files.items()]
    if tamper:
        files[tamper] = files[tamper] + b" "
    if manifest:
        files[f"manifest-{PHONE_RUN_ID}.json"] = json.dumps(
            {"schema": "edat.export-manifest/1", "record_schema": "edat.perf/1.0", "run_id": PHONE_RUN_ID,
             "files": listed}).encode()
    path = tmp_path / f"tinyagent-bench-{PHONE_RUN_ID}.zip"
    prefix = f"tinyagent-bench-{PHONE_RUN_ID}/" if nested else ""
    with zipfile.ZipFile(path, "w") as z:
        for n, b in files.items():
            z.writestr(prefix + n, b)
        if extra_entry:
            z.writestr(extra_entry, b"x")
    return path


def test_iphone_export_with_mixed_bit_quant_converts_and_validates(tmp_path):
    run = convert_perf(write_export(tmp_path), tmp_path / "store", board_version=BOARD)
    rs = read_set(run, deep=True)
    rec = find(rs.records, "model-x", "level/2048")
    assert rec["model"]["quant"]["mixed"] == {"5": 84, "6": 18}
    assert rec["model"]["speculation"] == "mtp"
    assert rec["device"]["class"] == "iphone-ref" and rec["runtime"]["name"] == "mlx-swift"
    assert rec["metrics"]["load_s"] == pytest.approx(2.5)
    assert rec["metrics"]["load_to_first_token_s"] == pytest.approx(3.25)
    kinds = sorted(a["kind"] for a in json.loads((run / "manifest.json").read_text())["attachments"])
    assert {"footprint_log", "phone_results"} <= set(kinds)
    assert not [r for r in rs.records if r["tier"] != "perf"]     # phone grades stay attachments


def test_iphone_export_unpacked_folder_also_reads(tmp_path):
    import zipfile
    folder = tmp_path / "unpacked"
    with zipfile.ZipFile(write_export(tmp_path, nested=False)) as z:
        z.extractall(folder)
    run = convert_perf(folder, tmp_path / "store", board_version=BOARD)
    assert read_set(run, deep=True).records


@pytest.mark.parametrize("mixed", [{}, {"5": 84, "6": 18}, True, False, None])
def test_mixed_quant_forms_accepted(mixed):
    r = make_record(model={"id": "model-x", "role": "candidate", "weights_sha256": W64,
                           "quant": {"scheme": "mlx-affine", "bits": 4, "mixed": mixed}})
    assert record_errors(r) == []


@pytest.mark.parametrize("mixed", [{"five": 84}, {"5": -1}, {"5": 1.5}, "mixed", [5, 6]])
def test_malformed_mixed_quant_refused(mixed):
    r = make_record(model={"id": "model-x", "role": "candidate", "weights_sha256": W64,
                           "quant": {"scheme": "mlx-affine", "mixed": mixed}})
    assert record_errors(r)


def test_iphone_export_altered_file_refused(tmp_path):
    with pytest.raises(ConversionError, match="does not match the export manifest"):
        convert_perf(write_export(tmp_path, tamper=f"mem-{PHONE_RUN_ID}.jsonl"), tmp_path / "store",
                     board_version=BOARD)


def test_iphone_export_entry_outside_the_folder_refused(tmp_path):
    with pytest.raises(ConversionError, match="outside the folder"):
        convert_perf(write_export(tmp_path, extra_entry="../escape.txt"), tmp_path / "store", board_version=BOARD)


def test_iphone_export_without_manifest_refused(tmp_path):
    with pytest.raises(ConversionError, match="manifest"):
        convert_perf(write_export(tmp_path, manifest=False), tmp_path / "store", board_version=BOARD)
