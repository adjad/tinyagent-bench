"""Schema and rule validation, RunWriter, read_set and merge_runs of edat.results."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from edat.results import keys, reader, schema, writer
from edat.results.reader import IntegrityError, merge_runs, read_set
from edat.results.schema import ValidationError, record_errors, validate_record
from edat.results.writer import RunWriter

W64 = "a" * 64
W64B = "b" * 64


def make_record(**overrides) -> dict:
    """A valid local `agentic` record. Top-level overrides replace the default;
    dict overrides are merged one level deep into the default block."""
    rec = {
        "schema": "edat.result/1.0", "kind": "local", "tier": "agentic", "suite": "toolbench",
        "run_id": "run-a", "board_version": "2027.1", "lang": "en",
        "model": {"id": "model-x", "role": "candidate", "weights_sha256": W64,
                  "quant": {"scheme": "q4"}},
        "runtime": {"name": "omlx", "version": "0.7.3"},
        "device": {"id": "mac-ref", "class": "mac-ref", "ram_bytes": 25769803776, "os": "macOS 27.3"},
        "settings": {"thinking": "off", "sampling": {"temperature": 1.0, "top_p": 0.95},
                     "sampling_source": "author_recommended", "seed": 1},
        "harness_commit": "90e3e9f", "started_at": "2026-12-01T10:15:00Z",
        "case_id": "case-1", "split": "dev", "trial": 1, "status": "ok",
        "facets": {"lane": "assistant", "family": "select"},
        "metrics": {"pass": True, "score": 1.0},
        "safety": {"unsafe_actions": 0, "privacy_leaks": 0},
        "raw": None, "raw_reason": "not_captured",
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(rec.get(key), dict):
            rec[key] = {**rec[key], **value}
        else:
            rec[key] = value
    return rec


def cloud_record(**overrides) -> dict:
    base = make_record(
        kind="cloud_reference", device=None, device_reason="cloud",
        model={"id": "vendor/model-c", "role": "cloud_reference", "weights_sha256": None, "quant": None,
               "requested_model": "vendor/model-c", "resolved_model": "vendor/model-c-2027",
               "upstream_provider": "provider-p",
               "run_date": {"first": "2026-12-01T10:00:00Z", "last": "2026-12-01T11:00:00Z"}},
        runtime={"name": "openrouter"},
        settings={"sampling_source": "provider_default"})
    base.update(overrides)
    return base


def store_with(tmp_path, records, run_id="run-a", **open_kw):
    """Write `records` into a fresh run and return the run directory."""
    with RunWriter.open(tmp_path / "store", run_id, tier=records[0]["tier"], **open_kw) as w:
        w.append_many(records)
        return w.dir


def only_metrics(metrics: dict, **overrides) -> dict:
    """A valid record whose `metrics` block is exactly `metrics` (make_record merges)."""
    rec = make_record(**overrides)
    rec["metrics"] = dict(metrics)
    return rec


def errs(rec) -> str:
    return " | ".join(record_errors(rec))


# --- schema and rules -----------------------------------------------------

def test_valid_local_record_passes():
    assert record_errors(make_record()) == []
    rec = make_record()
    assert validate_record(rec) is rec


def test_valid_cloud_record_passes():
    assert record_errors(cloud_record()) == []


def test_non_object_is_refused():
    assert record_errors([1, 2]) == ["$: a record is a JSON object"]


def test_missing_required_field_names_the_field():
    rec = make_record()
    del rec["harness_commit"]
    assert "$.harness_commit: required" in record_errors(rec)


def test_unknown_field_refused():
    rec = make_record(bogus=1)
    assert "$.bogus: unknown field" in record_errors(rec)


def test_unknown_nested_field_refused():
    rec = make_record()
    rec["safety"]["extra"] = 1
    assert "$.safety.extra: unknown field" in record_errors(rec)


def test_bad_enum_value_names_allowed_values():
    msg = errs(make_record(status="weird"))
    assert "$.status" in msg and "'weird'" in msg and "does_not_fit" in msg


def test_bad_tier_enum():
    assert "$.tier" in errs(make_record(tier="nonsense"))


@pytest.mark.parametrize("value", ["2026-12-01 10:15:00", "2026-12-01T10:15:00", "2026-12-01T10:15:00+00:00",
                                   "01/12/2026", ""])
def test_bad_started_at_format(value):
    assert "$.started_at" in errs(make_record(started_at=value))


def test_started_at_with_fractional_seconds_is_ok():
    assert record_errors(make_record(started_at="2026-12-01T10:15:00.250Z")) == []


def test_metrics_key_with_capitals_refused():
    msg = errs(make_record(metrics={"Pass": True}))
    assert "$.metrics key" in msg and "'Pass'" in msg


def test_metrics_string_over_64_chars_refused():
    msg = errs(make_record(metrics={"band": "x" * 65}))
    assert "$.metrics.band" in msg


def test_metrics_string_of_64_chars_allowed():
    assert record_errors(make_record(metrics={"band": "x" * 64})) == []


def test_metrics_nested_object_refused():
    assert "$.metrics.pass" not in errs(make_record(metrics={"pass": True, "nested": {"a": 1}}))
    assert "$.metrics.nested" in errs(make_record(metrics={"pass": True, "nested": {"a": 1}}))


def test_documented_metric_name_energy_j_is_accepted():
    rec = only_metrics({"energy_J": 12.5}, tier="perf")
    assert record_errors(rec) == []


def test_bad_weights_hash_pattern():
    assert "weights_sha256" in errs(make_record(model={"weights_sha256": "xyz"}))


def test_harness_commit_pattern():
    assert "$.harness_commit" in errs(make_record(harness_commit="NOTHEX"))


def test_trial_must_be_positive():
    assert "$.trial" in errs(make_record(trial=0))


def test_raw_path_pattern():
    rec = make_record(raw={"path": "../x.json", "sha256": W64, "bytes": 1})
    assert "$.raw.path" in errs(rec)


def test_score_outside_unit_range_refused():
    assert "$.metrics.score" in errs(make_record(metrics={"pass": True, "score": 1.5}))


def test_cloud_record_needs_null_device_and_reason():
    rec = cloud_record(device=make_record()["device"])
    assert "$.device: must be null" in errs(rec)
    rec = cloud_record()
    del rec["device_reason"]
    assert "$.device_reason" in errs(rec)


@pytest.mark.parametrize("field", ["requested_model", "resolved_model", "upstream_provider", "run_date"])
def test_cloud_record_needs_hosted_model_fields(field):
    rec = cloud_record()
    rec["model"][field] = None
    assert f"$.model.{field}: required for a cloud_reference" in record_errors(rec)


def test_cloud_record_role_must_be_cloud_reference():
    rec = cloud_record()
    rec["model"]["role"] = "candidate"
    assert "$.model.role" in errs(rec)


def test_cloud_record_on_heldout_refused():
    msg = errs(cloud_record(split="heldout"))
    assert "$.split" in msg and "held-out" in msg


def test_cloud_record_rejects_engine_default_sampling_source():
    rec = cloud_record()
    rec["settings"]["sampling_source"] = "engine_default"
    assert "provider_default" in errs(rec)


def test_cloud_run_date_order():
    rec = cloud_record()
    rec["model"]["run_date"] = {"first": "2026-12-02T00:00:00Z", "last": "2026-12-01T00:00:00Z"}
    assert "first is after last" in errs(rec)


def test_local_record_without_weights_hash_refused():
    rec = make_record()
    rec["model"].pop("weights_sha256")
    assert "needs weights_sha256 or weights_fingerprint" in errs(rec)
    rec["model"]["weights_fingerprint"] = W64B
    assert record_errors(rec) == []


def test_local_record_needs_a_device():
    assert "$.device: required for a local record" in errs(make_record(device=None))


def test_local_record_cannot_carry_hosted_fields():
    rec = make_record()
    rec["model"]["requested_model"] = "vendor/x"
    assert "only for cloud_reference" in errs(rec)


def test_local_record_cannot_use_provider_default():
    rec = make_record()
    rec["settings"]["sampling_source"] = "provider_default"
    assert "hosted models only" in errs(rec)


def test_local_record_with_cloud_role_refused():
    rec = make_record()
    rec["model"]["role"] = "cloud_reference"
    assert "not a local role" in errs(rec)


def test_status_ok_without_pass_or_score_refused():
    msg = errs(only_metrics({"steps": 3}))
    assert "needs pass (bool) or score" in msg


def test_status_ok_with_only_score_is_enough():
    assert record_errors(only_metrics({"score": 0.5})) == []


def test_status_ok_with_only_pass_is_enough():
    assert record_errors(only_metrics({"pass": False})) == []


def test_status_ok_perf_record_needs_no_pass():
    assert record_errors(only_metrics({"ttft_s": 0.4}, tier="perf")) == []


def test_non_ok_status_needs_no_pass():
    assert record_errors(only_metrics({}, status="excluded")) == []


def test_raw_null_without_reason_refused():
    rec = make_record()
    del rec["raw_reason"]
    assert "$.raw: null needs a raw_reason" in record_errors(rec)


def test_raw_present_needs_no_reason():
    rec = make_record(raw={"path": f"raw/{W64}.json", "sha256": W64, "bytes": 2})
    del rec["raw_reason"]
    assert record_errors(rec) == []


def test_validation_error_lists_every_problem():
    rec = make_record(status="weird", trial=0)
    rec["bogus"] = 1
    with pytest.raises(ValidationError) as exc:
        validate_record(rec)
    assert len(exc.value.errors) >= 3
    assert "status" in str(exc.value) and "trial" in str(exc.value)


# --- sampling class and keys ---------------------------------------------

def test_sampling_class_values():
    assert keys.sampling_class("author_recommended") == "recommended"
    assert keys.sampling_class("engine_default") == "recommended"
    assert keys.sampling_class("provider_default") == "recommended"
    assert keys.sampling_class("fixed_sensitivity") == "fixed_sensitivity"
    with pytest.raises(keys.IncompatibleResults):
        keys.sampling_class("mystery")


def test_entry_key_includes_thinking_and_quant():
    a = keys.entry_key(make_record())
    b = keys.entry_key(make_record(settings={"thinking": "on"}))
    c = keys.entry_key(make_record(model={"quant": {"scheme": "q8"}}))
    assert len({a, b, c}) == 3
    assert a == "local|model-x|q4|omlx|off"


def test_entry_key_cloud_ignores_quant():
    assert keys.entry_key(cloud_record()) == "cloud|vendor/model-c|-|openrouter|off"


def test_check_entry_weights_refuses_two_hashes_under_one_key():
    with pytest.raises(keys.IncompatibleResults, match="weights_sha256"):
        keys.check_entry_weights([make_record(), make_record(model={"weights_sha256": W64B}, trial=2)])


# --- writer: append and read back ----------------------------------------

def test_append_then_read_set_round_trip(tmp_path):
    recs = [make_record(trial=t) for t in (1, 2, 3)]
    run_dir = store_with(tmp_path, recs)
    rs = read_set(run_dir)
    assert rs.kind == "run"
    assert rs.run_ids == ["run-a"]
    assert len(rs.records) == 3
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["records"]["count"] == 3
    assert manifest["records"]["sha256"] == hashlib.sha256((run_dir / "records.jsonl").read_bytes()).hexdigest()
    assert manifest["schema"] == "edat.run-manifest/1.0"
    assert manifest["comparison_key"]["sampling_class"] == "recommended"
    assert manifest["entries"][0]["records"] == 3
    assert rs.records[0]["case_id"] == "case-1"


def test_manifest_updates_after_each_append(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        w.append(make_record(trial=1))
        first = json.loads((w.dir / "manifest.json").read_text())["records"]
        w.append(make_record(trial=2))
        second = json.loads((w.dir / "manifest.json").read_text())["records"]
    assert (first["count"], second["count"]) == (1, 2)
    assert first["sha256"] != second["sha256"]


def test_records_are_one_canonical_json_line_each(tmp_path):
    run_dir = store_with(tmp_path, [make_record(trial=1), make_record(trial=2)])
    lines = (run_dir / "records.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["schema"] == "edat.result/1.0" for line in lines)


def test_put_raw_is_content_addressed(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        r1 = w.put_raw({"final": "hello", "steps": []})
        r2 = w.put_raw({"steps": [], "final": "hello"})
        r3 = w.put_raw({"final": "other"})
        assert r1 == r2
        assert r1["sha256"] != r3["sha256"]
        assert r1["path"] == f"raw/{r1['sha256']}.json"
        assert (w.dir / r1["path"]).exists()
        assert hashlib.sha256((w.dir / r1["path"]).read_bytes()).hexdigest() == r1["sha256"]
        assert r1["bytes"] == (w.dir / r1["path"]).stat().st_size
        text = w.put_raw("plain text")
        assert text["path"].endswith(".txt")
        rec = make_record(raw=r1)
        del rec["raw_reason"]
        w.append(rec)
    rs = read_set(tmp_path / "runs" / "run-a", deep=True)
    assert rs.raw_path(rs.records[0]).exists()
    assert json.loads(rs.raw_path(rs.records[0]).read_text()) == {"final": "hello", "steps": []}


def test_record_pointing_at_missing_raw_is_refused(tmp_path):
    rec = make_record(raw={"path": f"raw/{W64}.json", "sha256": W64, "bytes": 1})
    del rec["raw_reason"]
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        with pytest.raises(ValidationError, match="put_raw"):
            w.append(rec)


def test_duplicate_trial_refused(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        w.append(make_record())
        with pytest.raises(ValueError, match="duplicate trial"):
            w.append(make_record())
        w.append(make_record(trial=2))
    assert len(read_set(tmp_path / "runs" / "run-a").records) == 2


def test_same_trial_different_thinking_is_a_different_entry(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        w.append(make_record())
        w.append(make_record(settings={"thinking": "on"}))
    manifest = json.loads((tmp_path / "runs" / "run-a" / "manifest.json").read_text())
    assert {e["thinking"] for e in manifest["entries"]} == {"on", "off"}


def test_duplicate_trial_detected_after_reopen(tmp_path):
    store_with(tmp_path, [make_record()])
    with RunWriter.open(tmp_path / "store", "run-a", tier="agentic") as w:
        with pytest.raises(ValueError, match="duplicate trial"):
            w.append(make_record())
        w.append(make_record(trial=2))
    assert read_set(tmp_path / "store" / "runs" / "run-a").manifest["records"]["count"] == 2


@pytest.mark.parametrize("override,field_name", [
    ({"board_version": "2027.2"}, "board version"),
    ({"harness_commit": "deadbee"}, "harness commit"),
    ({"settings": {"sampling_source": "fixed_sensitivity"}}, "sampling"),
])
def test_record_with_other_comparison_key_refused_and_names_the_field(tmp_path, override, field_name):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        w.append(make_record())
        with pytest.raises(keys.IncompatibleResults) as exc:
            w.append(make_record(trial=2, **override))
        assert field_name in str(exc.value)
    assert len(read_set(tmp_path / "runs" / "run-a").records) == 1


def test_recommended_sources_may_share_a_run(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        w.append(make_record())
        w.append(make_record(trial=2, settings={"sampling_source": "engine_default"}))
    assert len(read_set(tmp_path / "runs" / "run-a").records) == 2


def test_record_for_another_run_or_tier_is_refused(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        with pytest.raises(ValidationError, match="run_id"):
            w.append(make_record(run_id="run-zzz"))
        with pytest.raises(ValidationError, match="tier"):
            w.append(make_record(tier="memory"))


def test_invalid_run_id_refused(tmp_path):
    with pytest.raises(ValueError):
        with RunWriter.open(tmp_path, "bad run id", tier="agentic"):
            pass


def test_append_many_is_all_or_nothing_on_schema_error(tmp_path):
    good, bad = make_record(trial=1), make_record(trial=2, status="weird")
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        with pytest.raises(ValidationError):
            w.append_many([good, bad])
        assert not (w.dir / "records.jsonl").exists()
        w.append(good)
    assert len(read_set(tmp_path / "runs" / "run-a").records) == 1


def test_append_many_is_all_or_nothing_on_duplicate_inside_batch(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        w.append(make_record(trial=1))
        before = (w.dir / "records.jsonl").read_bytes()
        with pytest.raises(ValueError, match="duplicate"):
            w.append_many([make_record(trial=2), make_record(trial=3), make_record(trial=2)])
        assert (w.dir / "records.jsonl").read_bytes() == before
        # the rolled-back trials are free again
        assert w.append_many([make_record(trial=2), make_record(trial=3)]) == 2
    assert len(read_set(tmp_path / "runs" / "run-a").records) == 3


def test_append_many_is_all_or_nothing_on_comparison_key(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        w.append(make_record(trial=1))
        before = (w.dir / "records.jsonl").read_bytes()
        manifest_before = (w.dir / "manifest.json").read_bytes()
        with pytest.raises(keys.IncompatibleResults):
            w.append_many([make_record(trial=2), make_record(trial=3, board_version="2028.1")])
        assert (w.dir / "records.jsonl").read_bytes() == before
        assert (w.dir / "manifest.json").read_bytes() == manifest_before


def test_append_many_empty_is_a_no_op(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        assert w.append_many([]) == 0
        assert not (w.dir / "records.jsonl").exists()


def test_sample_then_real_never_share_a_run(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        w.append(make_record(sample=True))
        with pytest.raises(ValidationError, match="sample and real"):
            w.append(make_record(trial=2))


def test_real_then_sample_never_share_a_run(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        w.append(make_record())
        with pytest.raises(ValidationError, match="sample and real"):
            w.append(make_record(trial=2, sample=True))


def test_sample_and_real_in_one_batch_refused(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        with pytest.raises(ValidationError, match="sample and real"):
            w.append_many([make_record(sample=True), make_record(trial=2)])
        assert not (w.dir / "records.jsonl").exists()


def test_sample_run_stays_sample_after_reopen(tmp_path):
    store_with(tmp_path, [make_record(sample=True)])
    with RunWriter.open(tmp_path / "store", "run-a", tier="agentic") as w:
        with pytest.raises(ValidationError, match="sample and real"):
            w.append(make_record(trial=2))
        w.append(make_record(trial=2, sample=True))
    manifest = json.loads((tmp_path / "store" / "runs" / "run-a" / "manifest.json").read_text())
    assert manifest["sample"] is True


def test_failed_first_batch_does_not_decide_sample_flag(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        with pytest.raises(ValidationError):
            w.append_many([make_record(sample=True, status="weird")])
        w.append(make_record())  # nothing was written, so a real record must be accepted


def test_second_concurrent_open_raises_runtime_error(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic"):
        with pytest.raises(RuntimeError, match="open in another writer"):
            with RunWriter.open(tmp_path, "run-a", tier="agentic"):
                pass
    # the lock is released afterwards
    with RunWriter.open(tmp_path, "run-a", tier="agentic"):
        pass


def test_lock_released_when_body_raises(tmp_path):
    with pytest.raises(KeyError):
        with RunWriter.open(tmp_path, "run-a", tier="agentic"):
            raise KeyError("boom")
    with RunWriter.open(tmp_path, "run-a", tier="agentic"):
        pass


def test_reopen_with_other_tier_or_run_id_refused(tmp_path):
    store_with(tmp_path, [make_record()])
    with pytest.raises(ValueError, match="tier"):
        with RunWriter.open(tmp_path / "store", "run-a", tier="memory"):
            pass


def test_partial_last_line_blocks_reopen_and_read(tmp_path):
    run_dir = store_with(tmp_path, [make_record(trial=1), make_record(trial=2)])
    with open(run_dir / "records.jsonl", "ab") as fh:
        fh.write(b'{"schema": "edat.result/1.0", "ki')
    with pytest.raises(ValueError, match="partial line"):
        with RunWriter.open(tmp_path / "store", "run-a", tier="agentic"):
            pass
    with pytest.raises(IntegrityError, match="incomplete"):
        read_set(run_dir)
    with pytest.raises(IntegrityError):
        reader.parse_jsonl(run_dir / "records.jsonl")


def test_tampered_records_file_fails_the_checksum(tmp_path):
    run_dir = store_with(tmp_path, [make_record(case_id="case-1"), make_record(case_id="case-2")])
    path = run_dir / "records.jsonl"
    path.write_bytes(path.read_bytes().replace(b"case-1", b"case-9"))
    with pytest.raises(IntegrityError, match="does not match its manifest"):
        read_set(run_dir)


def test_removed_record_line_fails_the_checksum(tmp_path):
    run_dir = store_with(tmp_path, [make_record(trial=1), make_record(trial=2)])
    path = run_dir / "records.jsonl"
    path.write_bytes(path.read_bytes().splitlines(keepends=True)[0])
    with pytest.raises(IntegrityError, match="manifest"):
        read_set(run_dir)


def test_unverified_read_skips_the_checksum(tmp_path):
    run_dir = store_with(tmp_path, [make_record(case_id="case-1")])
    path = run_dir / "records.jsonl"
    path.write_bytes(path.read_bytes().replace(b"case-1", b"case-9"))
    assert read_set(run_dir, verify=False).records[0]["case_id"] == "case-9"


def test_missing_raw_file_is_detected(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        raw = w.put_raw({"final": "x"})
        rec = make_record(raw=raw)
        del rec["raw_reason"]
        w.append(rec)
        run_dir = w.dir
    (run_dir / raw["path"]).unlink()
    with pytest.raises(IntegrityError, match="missing"):
        read_set(run_dir)


def test_altered_raw_file_is_detected_only_by_deep_read(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        raw = w.put_raw({"final": "x"})
        rec = make_record(raw=raw)
        del rec["raw_reason"]
        w.append(rec)
        run_dir = w.dir
    (run_dir / raw["path"]).write_text('{"final":"y"}')
    read_set(run_dir)  # shallow read only checks existence
    with pytest.raises(IntegrityError, match="altered"):
        read_set(run_dir, deep=True)


def test_read_set_of_a_directory_without_records(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        read_set(tmp_path / "empty")


def test_read_set_refuses_record_that_breaks_the_rules(tmp_path):
    run_dir = store_with(tmp_path, [make_record()])
    manifest = json.loads((run_dir / "manifest.json").read_text())
    bad = make_record(status="weird")
    data = writer.record_line(bad)
    (run_dir / "records.jsonl").write_bytes(data)
    manifest["records"]["sha256"] = hashlib.sha256(data).hexdigest()
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValidationError, match="status"):
        read_set(run_dir)


def test_attach_records_checksum_in_manifest(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        w.append(make_record())
        info = w.attach("meta.json", '{"a": 1}', "toolbench-meta")
        with pytest.raises(ValueError):
            w.attach("records.jsonl", b"x", "bad")
        with pytest.raises(ValueError):
            w.attach("../escape.json", b"x", "bad")
        run_dir = w.dir
    assert info["sha256"] == hashlib.sha256(b'{"a": 1}').hexdigest()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["attachments"] == [info]
    assert (run_dir / "meta.json").read_text() == '{"a": 1}'
    read_set(run_dir)


def test_attachments_survive_reopen(tmp_path):
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        w.append(make_record())
        w.attach("meta.json", b"{}", "x")
    with RunWriter.open(tmp_path, "run-a", tier="agentic") as w:
        w.append(make_record(trial=2))
    manifest = json.loads((tmp_path / "runs" / "run-a" / "manifest.json").read_text())
    assert [a["name"] for a in manifest["attachments"]] == ["meta.json"]
    assert manifest["records"]["count"] == 2


# --- merge_runs -----------------------------------------------------------

def two_runs(tmp_path, a_over=None, b_over=None, a_extra=None, b_extra=None):
    a = [make_record(case_id="case-1", run_id="run-a", **(a_over or {}))] + (a_extra or [])
    b = [make_record(case_id="case-2", run_id="run-b", **(b_over or {}))] + (b_extra or [])
    da = store_with(tmp_path, a, run_id="run-a")
    db = store_with(tmp_path, b, run_id="run-b")
    return read_set(da), read_set(db)


def with_raw(tmp_path, run_id, case_id):
    store = tmp_path / "store"
    with RunWriter.open(store, run_id, tier="agentic") as w:
        raw = w.put_raw({"final": f"answer for {case_id}"})
        rec = make_record(run_id=run_id, case_id=case_id, raw=raw)
        del rec["raw_reason"]
        w.append(rec)
        return w.dir


def test_merge_compatible_runs_adds_counts_and_copies_raw(tmp_path):
    da, db = with_raw(tmp_path, "run-a", "case-1"), with_raw(tmp_path, "run-b", "case-2")
    a, b = read_set(da), read_set(db)
    out = tmp_path / "merged"
    merged = merge_runs([a, b], out)
    assert merged.kind == "merged"
    assert sorted(merged.run_ids) == ["run-a", "run-b"]
    assert len(merged.records) == 2
    meta = json.loads((out / "merge.json").read_text())
    assert meta["records"]["count"] == len(a.records) + len(b.records)
    assert meta["records"]["sha256"] == hashlib.sha256((out / "records.jsonl").read_bytes()).hexdigest()
    for rec in merged.records:
        assert (out / rec["raw"]["path"]).exists()
    again = read_set(out, deep=True)
    assert {r["case_id"] for r in again.records} == {"case-1", "case-2"}
    assert [s["run_id"] for s in again.sources] == ["run-a", "run-b"]


def test_merged_set_detects_tampering(tmp_path):
    a, b = two_runs(tmp_path)
    out = tmp_path / "merged"
    merge_runs([a, b], out)
    p = out / "records.jsonl"
    p.write_bytes(p.read_bytes().replace(b"case-2", b"case-7"))
    with pytest.raises(IntegrityError):
        read_set(out)


def test_merge_needs_two_runs(tmp_path):
    a, _ = two_runs(tmp_path)
    with pytest.raises(ValueError, match="at least two"):
        merge_runs([a], tmp_path / "m")


@pytest.mark.parametrize("override,field_name", [
    ({"board_version": "2027.2"}, "board version"),
    ({"harness_commit": "deadbee"}, "harness commit"),
    ({"settings": {"sampling_source": "fixed_sensitivity"}}, "sampling"),
    ({"tier": "preferences"}, "tier"),
])
def test_merge_refuses_different_comparison_key(tmp_path, override, field_name):
    a, b = two_runs(tmp_path, b_over=override)
    out = tmp_path / "merged"
    with pytest.raises(keys.IncompatibleResults) as exc:
        merge_runs([a, b], out)
    assert field_name in str(exc.value)
    assert not out.exists()


def test_merge_refuses_same_model_in_different_thinking_modes(tmp_path):
    a, b = two_runs(tmp_path, b_over={"settings": {"thinking": "on"}})
    with pytest.raises(keys.IncompatibleResults, match="thinking"):
        merge_runs([a, b], tmp_path / "merged")
    assert not (tmp_path / "merged").exists()


def test_merge_can_allow_thinking_mismatch_explicitly(tmp_path):
    a, b = two_runs(tmp_path, b_over={"settings": {"thinking": "on"}})
    merged = merge_runs([a, b], tmp_path / "merged", allow_thinking_mismatch=True)
    assert {r["settings"]["thinking"] for r in merged.records} == {"on", "off"}


def test_merge_allows_different_models_with_different_thinking(tmp_path):
    a, b = two_runs(tmp_path, b_over={"model": {"id": "model-y"}, "settings": {"thinking": "on"}})
    assert len(merge_runs([a, b], tmp_path / "merged").records) == 2


def test_merge_refuses_duplicate_trials(tmp_path):
    da = store_with(tmp_path, [make_record(run_id="run-a")], run_id="run-a")
    db = store_with(tmp_path, [make_record(run_id="run-b")], run_id="run-b")
    with pytest.raises(keys.IncompatibleResults, match="trial"):
        merge_runs([read_set(da), read_set(db)], tmp_path / "merged")
    assert not (tmp_path / "merged").exists()


def test_merge_refuses_to_write_into_existing_merged_dir(tmp_path):
    a, b = two_runs(tmp_path)
    out = tmp_path / "merged"
    merge_runs([a, b], out)
    before = (out / "records.jsonl").read_bytes()
    with pytest.raises(FileExistsError):
        merge_runs([a, b], out)
    assert (out / "records.jsonl").read_bytes() == before


def test_merge_of_a_merged_set_with_a_new_run(tmp_path):
    a, b = two_runs(tmp_path)
    m1 = merge_runs([a, b], tmp_path / "m1")
    dc = store_with(tmp_path, [make_record(case_id="case-3", run_id="run-c")], run_id="run-c")
    m2 = merge_runs([m1, read_set(dc)], tmp_path / "m2")
    assert len(m2.records) == 3
    assert sorted(m2.run_ids) == ["run-a", "run-b", "run-c"]


def test_merge_records_are_sorted_deterministically(tmp_path):
    a, b = two_runs(tmp_path)
    m1 = merge_runs([a, b], tmp_path / "m1")
    m2 = merge_runs([b, a], tmp_path / "m2")
    assert [r["case_id"] for r in m1.records] == [r["case_id"] for r in m2.records]
    assert (tmp_path / "m1" / "records.jsonl").read_bytes() == (tmp_path / "m2" / "records.jsonl").read_bytes()


def test_records_are_not_mutated_by_writing(tmp_path):
    rec = make_record()
    snapshot = copy.deepcopy(rec)
    store_with(tmp_path, [rec])
    assert rec == snapshot


def test_default_store_uses_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("EDAT_RESULTS_DIR", str(tmp_path / "somewhere"))
    assert writer.default_store() == tmp_path / "somewhere"
