"""edat.perf.aggregate on small hand-made run documents. All values are synthetic.

Times are built in whole nanoseconds so every expected number below is exact:
decode tok/s = (output_tokens - 1) / (t_last - t_first), so 257 tokens over 2.56 s is 100.
"""
from __future__ import annotations

import json
import math

import pytest

from edat.perf import aggregate as A
from edat.perf.constants import BASELINE_ID, GIB, MIB, REFERENCE_ID

NS = 1_000_000_000
CAND = "cand-model-q4"


def ns(seconds: float) -> int:
    return int(round(seconds * NS))


def smp(model, order, *, level=2048, mode="plain", test="level", phase="measured", status="ok", ttft=1.0,
        n=257, decode_s=2.56, prompt=None, prefill_s=None, cached=None, peak=5 * GIB, steady=4 * GIB,
        end=4 * GIB, token_times=None, **extra):
    t0 = (order + 1) * 1000 * NS
    tf = t0 + ns(ttft)
    s = {"sample_id": f"s{order:04d}", "model_id": model, "mode": mode, "test": test, "phase": phase,
         "round": 1, "rep": 1, "order_index": order, "level_tokens": level,
         "prompt_tokens": level if prompt is None else prompt, "cached_tokens": cached, "variant": order % 16,
         "shape": None, "step": None, "t_submit_ns": t0,
         "t_prefill_done_ns": None if prefill_s is None else t0 + ns(prefill_s),
         "t_first_token_ns": tf, "t_last_token_ns": tf + ns(decode_s), "t_end_ns": tf + ns(decode_s) + 1_000_000,
         "token_times_ns": token_times, "output_tokens": n, "reasoning_tokens": 0,
         "drafted_tokens": None, "accepted_tokens": None, "load": None,
         "footprint_start_bytes": 3 * GIB, "footprint_peak_bytes": peak, "footprint_peak_ledger_bytes": None,
         "footprint_steady_bytes": steady, "footprint_end_bytes": end, "neural_bytes": None,
         "swap_delta_bytes": 0, "compressed_delta_bytes": 0, "status": status, "error": None}
    s.update(extra)
    return s


def mdl(mid, role, mode="plain", **kw):
    m = {"id": mid, "entry_id": kw.pop("entry_id", mid), "role": role, "mode": mode, "headline_entry": True,
         "thinking": "on", "speculation": "mtp" if mode == "as_shipped" else "none", "variant_of": None,
         "files": [{"name": "model.safetensors", "sha256": "0" * 64, "bytes": 5_000_000_000},
                   {"name": "tokenizer.json", "sha256": "1" * 64, "bytes": 10_000_000}]}
    m.update(kw)
    return m


def doc(models, samples, **kw):
    d = {"schema": "edat.perf/1.0", "kind": "local", "run_id": "2026-01-01T00-00-00Z-abcd",
         "runner": {"name": "mac-omlx", "version": "0.1.0", "commit": "abc1234"}, "clock": "mach_continuous_time",
         "started_utc": "2026-01-01T00:00:00Z", "ended_utc": "2026-01-01T01:00:00Z",
         "device": {"class": "mac-ref", "ram_bytes": 24 * GIB}, "runtime": {"name": "omlx", "kv_dtype": "bf16"},
         "models": models, "samples": samples, "events": [], "energy_windows": []}
    d.update(kw)
    return d


def cell(out, model, test="level", level=2048, mode="plain", **extras):
    found = [c for c in out["cells"] if c["model_id"] == model and c["test"] == test and c["level_tokens"] == level
             and c["mode"] == mode and all(c.get(k) == v for k, v in extras.items())]
    assert len(found) == 1, [c["cell_id"] for c in out["cells"]]
    return found[0]


def verdict(out, model, mode="plain"):
    return next(v for v in out["verdicts"] if v["model_id"] == model and v["mode"] == mode)


def bracket_doc(gw_start, gw_end, cand, *, base_start=None, base_end=None, level=2048):
    """B(0..) base(100..) cand(200..) base(300..) B(400..); each list holds output-token counts n
    (decode tok/s = (n - 1) / 2.0 with decode_s = 2.0)."""
    samples = []
    for i, n in enumerate(gw_start):
        samples.append(smp(BASELINE_ID, i, n=n, decode_s=2.0, level=level))
    for i, n in enumerate(base_start or []):
        samples.append(smp(REFERENCE_ID, 100 + i, n=n, decode_s=2.0, level=level))
    for i, n in enumerate(cand):
        samples.append(smp(CAND, 200 + i, n=n, decode_s=2.0, level=level))
    for i, n in enumerate(base_end or []):
        samples.append(smp(REFERENCE_ID, 300 + i, n=n, decode_s=2.0, level=level))
    for i, n in enumerate(gw_end):
        samples.append(smp(BASELINE_ID, 400 + i, n=n, decode_s=2.0, level=level))
    models = [mdl(BASELINE_ID, "baseline"), mdl(CAND, "candidate")]
    if base_start or base_end:
        models.insert(1, mdl(REFERENCE_ID, "reference"))
    return doc(models, samples)


# --- per-sample metrics ----------------------------------------------------------------

def test_decode_excludes_first_token():
    s = smp(CAND, 0, n=257, decode_s=2.56)
    assert A.decode_tps(s) == pytest.approx(100.0)          # 256 / 2.56, not 257 / 2.56
    # token_times only (no output_tokens, no t_last): offsets 0, 10 ms, ..., 2.56 s -> 257 tokens
    t = smp(CAND, 0, n=None, decode_s=2.56, token_times=[i * 10_000_000 for i in range(257)])
    t["t_last_token_ns"] = None
    assert A.decode_tps(t) == pytest.approx(100.0)
    assert A.decode_tps(smp(CAND, 0, n=1)) is None


def test_prefill_server_and_fallback():
    # server stamp: 2048 tokens in 1.024 s -> 2000 tok/s
    assert A.prefill(smp(CAND, 0, prefill_s=1.024)) == (pytest.approx(2000.0), "server")
    # fallback: TTFT 1.034 s minus one median decode interval (10 ms) = 1.024 s -> 2000 tok/s
    tt = [i * 10_000_000 for i in range(257)]
    assert A.prefill(smp(CAND, 0, ttft=1.034, token_times=tt)) == (pytest.approx(2000.0), "ttft_minus_decode_interval")
    # without token_times the interval is (t_last - t_first) / (n - 1) = 2.56 / 256 = 10 ms
    assert A.prefill(smp(CAND, 0, ttft=1.034))[0] == pytest.approx(2000.0)
    # cached prefix tokens are not prefill work: (2048 - 48) / 1.0
    assert A.prefill(smp(CAND, 0, prefill_s=1.0, cached=48))[0] == pytest.approx(2000.0)


def test_ttfa_and_cell_stats():
    samples = [smp(CAND, i, t_first_answer_ns=(i + 1) * 1000 * NS + ns(3.5)) for i in range(3)]
    samples.append(smp(CAND, 9, phase="warmup", ttft=99.0))       # warm-up never enters the stats
    out = A.aggregate(doc([mdl(CAND, "candidate")], samples))
    c = cell(out, CAND)
    assert c["ttfa_s"]["median"] == pytest.approx(3.5)
    assert c["ttft_s"]["median"] == pytest.approx(1.0) and c["ttft_s"]["max"] == pytest.approx(1.0)
    assert (c["n_ok"], c["n_warmup"], c["n_failed"]) == (3, 1, 0)
    assert c["decode_tps"]["better"] == "higher" and c["ttft_s"]["better"] == "lower"
    assert c["mem_peak"]["median"] == 5 * GIB and c["mem_peak"]["better"] == "lower"
    assert "p95" not in c["decode_tps"]
    # no t_first_answer_ns -> no ttfa_s statistics
    out2 = A.aggregate(doc([mdl(CAND, "candidate")], [smp(CAND, 0)]))
    assert "ttfa_s" not in cell(out2, CAND)


def test_voided_cache_rep_excluded():
    samples = [smp(CAND, 0), smp(CAND, 1), smp(CAND, 2, cached=512, ttft=0.1, n=513)]
    out = A.aggregate(doc([mdl(CAND, "candidate")], samples))
    c = cell(out, CAND)
    assert (c["n_ok"], c["n_voided_cache"]) == (2, 1)
    assert c["ttft_s"]["min"] == pytest.approx(1.0)                  # the cached 0.1 s rep is gone
    assert c["status"] == "ok"


def test_worst_status_rule():
    samples = [smp(CAND, 0), smp(CAND, 1, status="memory_pressure"), smp(CAND, 2, status="truncated")]
    c = cell(A.aggregate(doc([mdl(CAND, "candidate")], samples)), CAND)
    # severity: truncated is worse than memory_pressure, which is worse than ok
    assert c["status"] == "truncated"
    assert (c["n_ok"], c["n_failed"]) == (2, 1)                    # memory_pressure is kept (flagged)
    samples.append(smp(CAND, 3, status="oom_killed"))
    assert cell(A.aggregate(doc([mdl(CAND, "candidate")], samples)), CAND)["status"] == "oom_killed"
    # a voided rep does not rank
    v = [smp(CAND, 0), smp(CAND, 1, status="voided_foreign_work")]
    c2 = cell(A.aggregate(doc([mdl(CAND, "candidate")], v)), CAND)
    assert (c2["status"], c2["n_voided"]) == ("ok", 1)


def test_superseded_attempt():
    first = smp(CAND, 5, status="oom_killed", attempt=1)
    retry = smp(CAND, 5, attempt=2)
    retry["sample_id"] = "s0005-2"
    c = cell(A.aggregate(doc([mdl(CAND, "candidate")], [first, retry])), CAND)
    assert (c["status"], c["n_ok"], c["n_superseded"]) == ("ok", 1, 1)


# --- ratios ------------------------------------------------------------------------------

def test_ratio_two_bracket_mean_and_drift():
    # GW start: (201-1)/2 = 100; GW end: (221-1)/2 = 110 -> mean 105. Candidate (211-1)/2 = 105 -> ratio 1.0.
    # drift = |100 - 110| / 105 = 9.5 % > 5 % -> baseline_drift
    out = A.aggregate(bracket_doc([201, 201], [221, 221], [211, 211]))
    c = cell(out, CAND)
    r = c["ratio_to_baseline"]["decode_tps"]
    assert r["ratio"] == pytest.approx(1.0) and r["bracket"] == "two"
    assert (r["baseline_start"], r["baseline_end"]) == (pytest.approx(100), pytest.approx(110))
    assert c["baseline_drift"] is True and c["ratios_greyed"] is True
    assert c["baseline_drift_detail"]["decode_tps"]["drift"] == pytest.approx(10 / 105)
    assert out["baseline_drift"] is True and c["cell_id"] in out["baseline_drift_cells"]
    assert cell(out, BASELINE_ID)["ratio_reason"] == "is_baseline"


def test_baseline_drift_boundary_5pct():
    # start (196-1)/2 = 97.5, end (206-1)/2 = 102.5: |5| / 100 = exactly 5 % -> not flagged (needs > 5 %)
    out = A.aggregate(bracket_doc([196], [206], [201]))
    c = cell(out, CAND)
    assert c["baseline_drift_detail"]["decode_tps"]["drift"] == pytest.approx(0.05)
    assert c["baseline_drift"] is False
    assert c["ratio_to_baseline"]["decode_tps"]["ratio"] == pytest.approx(1.0)
    # start 97.5, end (207-1)/2 = 103: 5.5 / 100.25 = 5.49 % -> flagged
    assert cell(A.aggregate(bracket_doc([196], [207], [201])), CAND)["baseline_drift"] is True


def test_single_bracket_when_no_split():
    # GW only before the candidate: plain median of its samples, bracket "single"
    out = A.aggregate(bracket_doc([201, 221, 241], [], [221]))
    r = cell(out, CAND)["ratio_to_baseline"]["decode_tps"]
    assert r["bracket"] == "single" and r["ratio"] == pytest.approx(110 / 110)   # median of 100, 110, 120


def test_both_references():
    # base start 102, end 98 -> 100; GW 100/100; candidate 120 -> 1.2 vs both
    out = A.aggregate(bracket_doc([201], [201], [241], base_start=[205], base_end=[197]))
    c = cell(out, CAND)
    assert c["ratio_to_baseline"]["decode_tps"]["ratio"] == pytest.approx(1.2)
    assert c["ratio_to_reference"]["decode_tps"]["ratio"] == pytest.approx(1.2)
    assert c["ratio_to_reference"]["decode_tps"]["bracket"] == "two"
    assert c["reference_model_id"] == REFERENCE_ID
    # the reference's own ratio to the baseline (the repeatability pair) uses the reference cell's own
    # median: nearest-rank median of 102 and 98 is 98 -> 98 / 100
    ref = cell(out, REFERENCE_ID)
    assert ref["ratio_to_baseline"]["decode_tps"]["ratio"] == pytest.approx(0.98)
    assert ref["ratio_to_reference_reason"] == "is_reference"


def test_plain_vs_as_shipped():
    samples = [smp(BASELINE_ID, 0, n=201, decode_s=2.0), smp(BASELINE_ID, 1, mode="as_shipped", n=321, decode_s=2.0),
               smp(CAND, 10, n=241, decode_s=2.0),
               smp("cand-mtp", 20, n=241, decode_s=2.0), smp("cand-mtp", 21, mode="as_shipped", n=401, decode_s=2.0),
               smp(BASELINE_ID, 30, n=201, decode_s=2.0), smp(BASELINE_ID, 31, mode="as_shipped", n=321, decode_s=2.0)]
    models = [mdl(BASELINE_ID, "baseline"), mdl(BASELINE_ID, "baseline", mode="as_shipped"),
              mdl(CAND, "candidate"), mdl("cand-mtp", "candidate"), mdl("cand-mtp", "candidate", mode="as_shipped")]
    out = A.aggregate(doc(models, samples))
    # no-drafter candidate: plain 120 / plain 100 = 1.2 (headline); as_shipped = plain, so 120 / 160 = 0.75
    c = cell(out, CAND)
    assert c["ratio_to_baseline"]["decode_tps"]["ratio"] == pytest.approx(1.2)
    assert c["ratio_to_baseline_as_shipped"]["decode_tps"]["ratio"] == pytest.approx(0.75)
    assert c["as_shipped_equals_plain"] is True and c["headline_ratio"] is True
    # drafter candidate: as_shipped 200 / as_shipped 160 = 1.25; plain 120 / 100 = 1.2
    assert cell(out, "cand-mtp", mode="as_shipped")["ratio_to_baseline"]["decode_tps"]["ratio"] == pytest.approx(1.25)
    assert cell(out, "cand-mtp", mode="as_shipped")["headline_ratio"] is False
    assert "ratio_to_baseline_as_shipped" not in cell(out, "cand-mtp")


def test_ratio_nulls():
    samples = [smp(BASELINE_ID, 0, level=10240, status="oom_killed"), smp(BASELINE_ID, 1, level=2048),
               smp(CAND, 10, level=10240), smp(CAND, 11, level=2048), smp(CAND, 12, level=5120)]
    out = A.aggregate(doc([mdl(BASELINE_ID, "baseline"), mdl(CAND, "candidate")], samples))
    c = cell(out, CAND, level=10240)
    assert c["ratio_to_baseline"] is None and c["ratio_reason"] == "baseline_not_ok"
    assert c["decode_tps"]["median"] == pytest.approx(100.0)            # absolute value still shown
    assert cell(out, CAND, level=5120)["ratio_reason"] == "baseline_n/a"
    v = verdict(out, CAND)
    assert v["fits_where_baseline_does_not"]["levels"] == [10240]
    assert v["fits_where_baseline_does_not"]["any"] is True


def test_summary_index_geomean():
    # decode ratios: 512 -> 160/100 = 1.6; 2048 -> 90/100 = 0.9; 32768 -> 3.0 (outside 512..10,240)
    # index = sqrt(1.6 * 0.9) = 1.2
    samples = []
    for i, (lv, b, c) in enumerate(((512, 201, 321), (2048, 201, 181), (32768, 201, 601))):
        samples += [smp(BASELINE_ID, i, level=lv, n=b, decode_s=2.0), smp(CAND, 10 + i, level=lv, n=c, decode_s=2.0),
                    smp(BASELINE_ID, 20 + i, level=lv, n=b, decode_s=2.0)]
    out = A.aggregate(doc([mdl(BASELINE_ID, "baseline"), mdl(CAND, "candidate")], samples))
    idx = verdict(out, CAND)["summary_index"]["vs_baseline"]["decode_tps"]
    assert idx["geomean"] == pytest.approx(1.2) and idx["levels"] == [512, 2048] and idx["better"] == "higher"
    assert verdict(out, CAND)["summary_index"]["vs_reference"] == {}


# --- memory -------------------------------------------------------------------------------

TOY_KV = {  # 8 layers; the last 2 share KV with earlier layers
    "layer_types": ["sliding_attention", "sliding_attention", "full_attention",
                    "sliding_attention", "sliding_attention", "full_attention",
                    "sliding_attention", "full_attention"],
    "num_key_value_heads": 4, "head_dim": 512, "sliding_window": 512, "num_kv_shared_layers": 2}


def test_kv_formula_toy():
    # own layers: first 6 -> 2 full layers x (2 x 4 heads x 512 dim x 2 bytes = 8192 B) = 16384 B/token
    # = 16384 x 1024 / 2^20 = 16 MiB per 1k tokens; 4 sliding layers x 8192 B x 512 window = 16 MiB fixed block
    d = A.kv_formula_detail(TOY_KV, 2)
    assert d["MiB_per_1k"] == pytest.approx(16.0) and d["sliding_block_MiB"] == pytest.approx(16.0)
    assert (d["full_layers_counted"], d["shared_layers_not_counted"]) == (2, 2)
    assert A.kv_formula_MiB_per_1k(TOY_KV, 1) == pytest.approx(8.0)      # 8-bit cache halves it
    with pytest.raises(ValueError):
        A.kv_formula_detail({**TOY_KV, "sliding_window": None})


def _kv_doc(slope, **model_kw):
    samples = []
    for i, lv in enumerate((512, 2048, 10240)):
        samples.append(smp(CAND, i, level=lv, steady=4 * GIB + slope * lv, peak=4 * GIB + slope * lv + 64 * MIB))
    samples.append(smp(CAND, 9, test="first_token", level=8, end=6_000_000_000))
    return doc([mdl(CAND, "candidate", kv_config=TOY_KV, **model_kw)], samples)


def test_kv_slope_fit_and_formula():
    # steady = 4 GiB + 16384 x L -> 16 MiB per 1k; formula 16 -> no mismatch
    v = verdict(A.aggregate(_kv_doc(16384)), CAND)
    assert v["kv_MiB_per_1k_fit"] == pytest.approx(16.0) and v["kv_fit"]["r2"] == pytest.approx(1.0)
    assert v["kv_MiB_per_1k_formula"] == pytest.approx(16.0) and v["kv_formula_mismatch"] is False
    assert v["prefill_scratch_bytes"] == {"512": 64 * MIB, "2048": 64 * MIB, "10240": 64 * MIB}
    assert v["max_level_ok"] == 10240
    # 20 MiB/1k (20480 B/token) is exactly 25 % above 16 -> not flagged; 21 MiB/1k (21504) is -> flagged
    assert verdict(A.aggregate(_kv_doc(20480)), CAND)["kv_formula_mismatch"] is False
    assert verdict(A.aggregate(_kv_doc(21504)), CAND)["kv_formula_mismatch"] is True


def test_overhead_and_loaded():
    # mem_loaded = first_token footprint_end 6.0e9; weights = 5.0e9 (tokenizer.json is not a weight file)
    v = verdict(A.aggregate(_kv_doc(16384)), CAND)
    assert v["mem_loaded_bytes"] == 6_000_000_000 and v["weight_bytes"] == 5_000_000_000
    assert v["overhead_bytes"] == 1_000_000_000 and v["weight_bytes_source"] == "files"
    v2 = verdict(A.aggregate(_kv_doc(16384, resident_weight_bytes=4_500_000_000)), CAND)
    assert v2["overhead_bytes"] == 1_500_000_000 and v2["weight_bytes_source"] == "resident_weight_bytes"
    assert cell(A.aggregate(_kv_doc(16384)), CAND, level=10240)["mem_loaded_bytes"] == 6_000_000_000


def test_bandwidth_needs_active_bytes():
    v = verdict(A.aggregate(_kv_doc(16384)), CAND)
    assert v["achieved_bw_GBps"] is None and v["achieved_bw_reason"] == "no_active_weight_bytes"
    # 100 tok/s x 2.0e9 active bytes = 200 GB/s
    v2 = verdict(A.aggregate(_kv_doc(16384, active_weight_bytes=2_000_000_000)), CAND)
    assert v2["achieved_bw_GBps"]["2048"] == pytest.approx(200.0)
    steady = 4 * GIB + 16384 * 2048
    assert v2["tok_s_per_resident_GB"]["2048"] == pytest.approx(100.0 / (steady / 1e9))


# --- load (§7) ------------------------------------------------------------------------------

def load_smp(model, order, test, t_load, t_ftp, read=None, cold_verified=None, mode="plain", **kw):
    s = smp(model, order, test=test, level=None, mode=mode, **kw)
    t0 = s["t_submit_ns"]
    s["load"] = {"t_load_start_ns": t0, "t_ready_ns": t0 + ns(t_load), "t_first_token_possible_ns": t0 + ns(t_ftp),
                 "cold_verified": cold_verified, "disk_read_bytes": read}
    return s


def test_load_cells_and_cold_check():
    # weight files 5.0e9 B: the cold check needs >= 4.5e9 B read
    samples = [load_smp(CAND, 0, "load_cold", 2.3, 3.0, read=4_600_000_000),   # verified: 2.0 GB/s
               load_smp(CAND, 1, "load_cold", 1.0, 1.5, read=4_400_000_000),   # not cold: excluded from t_load_cold_s
               load_smp(CAND, 2, "load_warm", 0.5, 0.9), load_smp(CAND, 3, "load_warm", 0.7, 1.1, phase="warmup")]
    out = A.aggregate(doc([mdl(CAND, "candidate")], samples))
    cold = cell(out, CAND, test="load_cold", level=None)
    assert cold["n_cold_verified"] == 1 and cold["cold_verified_rate"] == 0.5
    assert cold["t_load_cold_s"]["n"] == 1 and cold["t_load_cold_s"]["median"] == pytest.approx(2.3)
    assert cold["load_read_GBps"]["n"] == 1 and cold["load_read_GBps"]["max"] == pytest.approx(4.6 / 2.3)
    warm = cell(out, CAND, test="load_warm", level=None)
    # load samples count in any phase: medians of 0.5 and 0.7 -> nearest-rank lower = 0.5
    assert warm["t_load_warm_s"]["n"] == 2 and warm["t_load_warm_s"]["median"] == pytest.approx(0.5)
    assert warm["t_first_token_possible_s"]["median"] == pytest.approx(0.9)


# --- energy (§4.2), injected -------------------------------------------------------------

def energy_doc(values, *, cached_sid=None):
    """values: {window_id: (model, first_order, e_pre, e_dec)}"""
    samples, windows = [], []
    for wid, (model, first, e_pre, e_dec) in values.items():
        ids = []
        for k in range(4):
            s = smp(model, first + k, test="energy_pair", n=1 if k % 2 == 0 else 257,
                    shape="A" if k % 2 == 0 else "B", energy_window_id=wid)
            s["sample_id"] = f"{wid}-{k}"
            if s["sample_id"] == cached_sid:
                s["cached_tokens"] = 100
            samples.append(s)
            ids.append(s["sample_id"])
        windows.append({"window_id": wid, "source": "smc_pstr", "scope": "whole_device_internal",
                        "sample_ids": ids, "trace_attachment": "power-trace.jsonl"})
    return doc([mdl(BASELINE_ID, "baseline"), mdl(CAND, "candidate")], samples, energy_windows=windows,
               attachments=[{"name": "power-trace.jsonl", "sha256": "2" * 64, "kind": "power_trace"}])


def fake_energy(table, calls):
    def fn(samples, trace, window, *, level_tokens):
        calls.append((window["window_id"], [s["sample_id"] for s in samples], trace, level_tokens))
        e_pre, e_dec = table[window["window_id"]]
        return {"e_pre_mJ_per_tok": e_pre, "e_dec_mJ_per_tok": e_dec, "error_bound_pct": 2.0,
                "scope": "whole_device_internal", "source": "smc_pstr", "is_estimate": False, "flags": []}
    return fn


def test_energy_injected_ratio_and_drift(tmp_path):
    vals = {"w-b1": (BASELINE_ID, 0, 0.5, 1.0), "w-c": (CAND, 100, 0.6, 1.26), "w-b2": (BASELINE_ID, 200, 0.5, 1.1)}
    calls = []
    out = A.aggregate(energy_doc(vals, cached_sid="w-c-1"), run_dir=tmp_path,
                      energy_fn=fake_energy({k: v[2:] for k, v in vals.items()}, calls),
                      trace_reader=lambda p: f"TRACE:{p.name}")
    assert {c[2] for c in calls} == {"TRACE:power-trace.jsonl"} and all(c[3] == 2048 for c in calls)
    assert "w-c-1" not in next(c[1] for c in calls if c[0] == "w-c")      # cached rep voided before energy
    c = cell(out, CAND, test="energy_pair")
    assert c["n_voided_cache"] == 1
    # e_dec: baseline brackets 1.0 and 1.1 -> 1.05; 1.26 / 1.05 = 1.2. drift 0.1/1.05 = 9.5 % -> flagged
    assert c["e_dec_mJ_per_tok"] == pytest.approx(1.26)
    assert c["ratio_to_baseline"]["e_dec_mJ_per_tok"]["ratio"] == pytest.approx(1.2)
    assert c["ratio_to_baseline"]["e_dec_mJ_per_tok"]["better"] == "lower"
    assert c["ratio_to_baseline"]["e_pre_mJ_per_tok"]["ratio"] == pytest.approx(1.2)
    assert c["baseline_drift"] is True and c["scope"] == "whole_device_internal"
    assert c["error_bound_pct"] == 2.0 and c["is_estimate"] is False


def test_energy_without_run_dir_is_reasoned():
    vals = {"w-c": (CAND, 100, 0.6, 1.2)}
    out = A.aggregate(energy_doc(vals), energy_fn=fake_energy({"w-c": (0.6, 1.2)}, []))
    c = cell(out, CAND, test="energy_pair")
    assert c["e_dec_mJ_per_tok"] is None and c["energy_reason"] == "no_run_dir"


# --- reference pair (§1.5) --------------------------------------------------------------------

def pair_doc(*, dec=(201, 201), prefill=(2000, 2000), ttft=(1.0, 1.0), peak=(5_000_000_000, 5_000_000_000),
             loaded=(6_000_000_000, 6_000_000_000), slope=(16000, 16000), energy=((1.0, 1.0), (1.0, 1.0)),
             eb=(2.0, 2.0), warm=(2.0, 2.0), cold=(2.0, 2.0), ftp_warm=(3.0, 3.0), ftp_cold=(3.0, 3.0)):
    """GW at orders 0.. and 900..; base in between. Index 0 is GW, 1 is base."""
    samples, windows, table = [], [], {}

    def add(mi, order):
        model = (BASELINE_ID, REFERENCE_ID)[mi]
        for j, lv in enumerate((512, 2048, 10240)):
            samples.append(smp(model, order + j, level=lv, n=dec[mi], decode_s=2.0, prompt=prefill[mi],
                               prefill_s=1.0, ttft=ttft[mi], peak=peak[mi], steady=4 * GIB + slope[mi] * lv))
        samples.append(smp(model, order + 5, test="first_token", level=8, end=loaded[mi]))
        samples.append(load_smp(model, order + 6, "load_warm", warm[mi], ftp_warm[mi]))
        samples.append(load_smp(model, order + 7, "load_cold", cold[mi], ftp_cold[mi], read=5_000_000_000))
        wid = f"w{order}"
        ids = []
        for k in range(2):
            s = smp(model, order + 10 + k, test="energy_pair", n=1 if k == 0 else 257)
            s["sample_id"] = f"{wid}-{k}"
            samples.append(s)
            ids.append(s["sample_id"])
        windows.append({"window_id": wid, "sample_ids": ids, "trace_attachment": "t.jsonl"})
        table[wid] = (energy[mi][0], energy[mi][1], eb[mi])

    add(0, 0)
    add(1, 100)
    add(0, 900)
    d = doc([mdl(BASELINE_ID, "baseline"), mdl(REFERENCE_ID, "reference")], samples, energy_windows=windows)

    def fn(ss, trace, window, *, level_tokens):
        e_pre, e_dec, b = table[window["window_id"]]
        return {"e_pre_mJ_per_tok": e_pre, "e_dec_mJ_per_tok": e_dec, "error_bound_pct": b,
                "scope": "whole_device_internal", "source": "smc_pstr", "is_estimate": False, "flags": []}
    return d, fn


def run_pair(**kw):
    d, fn = pair_doc(**kw)
    return A.aggregate(d, run_dir="/nonexistent-run-dir", energy_fn=fn, trace_reader=lambda p: "T")


def checks(out):
    return out["reference_pair_check"]["checks"]


def test_reference_pair_all_pass():
    out = run_pair()
    rpc = out["reference_pair_check"]
    assert rpc["status"] == "checked" and rpc["reference_pair_mismatch"] is False and rpc["rerun_session"] is False
    names = set(checks(out))
    for want in ("decode_tps@512", "decode_tps@2048", "decode_tps@10240", "prefill_tps@2048", "ttft_s@2048",
                 "mem_peak@2048", "mem_loaded", "kv_slope", "e_pre_mJ_per_tok@2048", "e_dec_mJ_per_tok@2048",
                 "t_load_warm_s@load_warm", "t_load_cold_s@load_cold", "t_first_token_possible_s@load_warm",
                 "t_first_token_possible_s@load_cold"):
        assert want in names, want
    assert cell(out, REFERENCE_ID, level=2048)["reference_pair_check"]["decode_tps@2048"]["pass"] is True
    assert cell(out, BASELINE_ID, level=2048)["reference_pair_check"]["decode_tps@2048"]["ratio"] == pytest.approx(1.0)


@pytest.mark.parametrize("kw,name,ok", [
    # decode 0.97-1.03: base (n-1)/2 vs GW 100
    (dict(dec=(201, 207)), "decode_tps@2048", True),       # 103 / 100 = 1.03
    (dict(dec=(201, 208)), "decode_tps@2048", False),      # 103.5
    (dict(dec=(201, 195)), "decode_tps@2048", True),       # 97
    (dict(dec=(201, 194)), "decode_tps@2048", False),      # 96.5
    (dict(prefill=(2000, 2060)), "prefill_tps@2048", True),    # 1.03
    (dict(prefill=(2000, 2061)), "prefill_tps@2048", False),
    (dict(ttft=(1.0, 0.97)), "ttft_s@2048", True),
    (dict(ttft=(1.0, 0.969)), "ttft_s@2048", False),
    # mem_peak: more than 2 % apart fails
    (dict(peak=(5_000_000_000, 5_100_000_000)), "mem_peak@2048", True),
    (dict(peak=(5_000_000_000, 5_100_000_001 + 1_000_000)), "mem_peak@2048", False),
    # mem_loaded vs the on-disk weight ratio (1.0 here): 1 % tolerance
    (dict(loaded=(6_000_000_000, 6_060_000_000)), "mem_loaded", True),
    (dict(loaded=(6_000_000_000, 6_060_100_000)), "mem_loaded", False),
    # KV slope 0.98-1.02: 16320 / 16000 = 1.02
    (dict(slope=(16000, 16320)), "kv_slope", True),
    (dict(slope=(16000, 16321)), "kv_slope", False),
    # e_dec 0.95-1.05 (combined error bound sqrt(2^2 + 2^2) = 2.8 % is narrower, so 5 % applies)
    (dict(energy=((1.0, 1.0), (1.0, 1.05))), "e_dec_mJ_per_tok@2048", True),
    (dict(energy=((1.0, 1.0), (1.0, 1.06))), "e_dec_mJ_per_tok@2048", False),
    # with 5 % bounds on each the combined bound is sqrt(50) = 7.07 %: 1.06 now passes
    (dict(energy=((1.0, 1.0), (1.0, 1.06)), eb=(5.0, 5.0)), "e_dec_mJ_per_tok@2048", True),
    (dict(energy=((1.0, 1.0), (0.95, 1.0))), "e_pre_mJ_per_tok@2048", True),
    (dict(energy=((1.0, 1.0), (0.94, 1.0))), "e_pre_mJ_per_tok@2048", False),
    # warm load 0.95-1.05, cold 0.90-1.10, first-token-possible likewise
    (dict(warm=(2.0, 2.1)), "t_load_warm_s@load_warm", True),
    (dict(warm=(2.0, 2.11)), "t_load_warm_s@load_warm", False),
    (dict(cold=(2.0, 2.2)), "t_load_cold_s@load_cold", True),
    (dict(cold=(2.0, 2.21)), "t_load_cold_s@load_cold", False),
    (dict(ftp_warm=(3.0, 3.15)), "t_first_token_possible_s@load_warm", True),
    (dict(ftp_warm=(3.0, 3.16)), "t_first_token_possible_s@load_warm", False),
    (dict(ftp_cold=(3.0, 2.7)), "t_first_token_possible_s@load_cold", True),
    (dict(ftp_cold=(3.0, 2.69)), "t_first_token_possible_s@load_cold", False),
])
def test_reference_pair_limits(kw, name, ok):
    out = run_pair(**kw)
    assert checks(out)[name]["pass"] is ok, checks(out)[name]
    assert out["reference_pair_check"]["reference_pair_mismatch"] is (not ok)


def test_reference_pair_rerun_needs_two_metrics():
    one = run_pair(dec=(201, 211))                          # decode fails at all three levels: one metric
    assert one["reference_pair_check"]["failed_metrics"] == ["decode_tps"]
    assert one["reference_pair_check"]["rerun_session"] is False
    two = run_pair(dec=(201, 211), peak=(5_000_000_000, 5_500_000_000))
    assert two["reference_pair_check"]["failed_metrics"] == ["decode_tps", "mem_peak"]
    assert two["reference_pair_check"]["rerun_session"] is True


def test_reference_pair_mismatch_greys_candidates():
    d, fn = pair_doc(dec=(201, 211))
    d["models"].append(mdl(CAND, "candidate"))
    d["samples"].append(smp(CAND, 500, n=201, decode_s=2.0))
    out = A.aggregate(d, run_dir="/nonexistent-run-dir", energy_fn=fn, trace_reader=lambda p: "T")
    c = cell(out, CAND)
    assert c["reference_pair_mismatch"] is True and "reference_pair_mismatch" in c["greyed_reasons"]
    assert cell(out, BASELINE_ID)["ratios_greyed"] is False


def test_as_shipped_prediction():
    # tokens per cycle = D / (D - accepted), D = n - 1. GW as_shipped: D = 400, accepted 96 -> 400/304.
    # base as_shipped: D, accepted 128 -> D/(D - 128). Plain ratio is 1.0 (both 100 tok/s), so
    # predicted = (D/(D-128)) / (400/304); measured = (D/2) / (400/2) = D/400.
    # measured / predicted = (D - 128) / 304: D = 432 -> 1.0; D = 447 -> 1.049 (inside 5 %); D = 448 -> 1.053 (flag)
    def asm(model, order, n, accepted):
        return smp(model, order, mode="as_shipped", n=n, decode_s=2.0, drafted_tokens=200, accepted_tokens=accepted)
    for base_n, flag in ((433, False), (448, False), (449, True)):
        samples = [smp(BASELINE_ID, 0, n=201, decode_s=2.0), asm(BASELINE_ID, 1, 401, 96),
                   smp(REFERENCE_ID, 10, n=201, decode_s=2.0), asm(REFERENCE_ID, 11, base_n, 128),
                   smp(BASELINE_ID, 20, n=201, decode_s=2.0), asm(BASELINE_ID, 21, 401, 96)]
        models = [mdl(BASELINE_ID, "baseline"), mdl(BASELINE_ID, "baseline", mode="as_shipped"),
                  mdl(REFERENCE_ID, "reference"), mdl(REFERENCE_ID, "reference", mode="as_shipped")]
        rows = A.aggregate(doc(models, samples))["reference_pair_check"]["as_shipped_decode"]
        assert len(rows) == 1
        r = rows[0]
        gw_tpc, base_tpc = 400 / 304, (base_n - 1) / (base_n - 1 - 128)
        assert r["predicted_ratio"] == pytest.approx(base_tpc / gw_tpc)
        assert r["measured_ratio"] == pytest.approx((base_n - 1) / 400)
        assert r["flag"] is flag


def test_reference_pair_not_run():
    out = A.aggregate(doc([mdl(CAND, "candidate")], [smp(CAND, 0)]))
    assert out["reference_pair_check"]["status"] == "not_run"
    assert out["reference_pair_check"]["reference_pair_mismatch"] is None


# --- fit verdicts end to end -----------------------------------------------------------------

def emu(model, order, level, *, status="ok", n=257, swap=0, wired=10900, **kw):
    s = smp(model, order, level=level, test="fit16_emulation", status=status, n=n, swap_delta_bytes=swap,
            compressed_delta_bytes=0, **kw)
    s["emulation"] = {"wired_limit_mb": wired, "balloon_gib": 10.5}
    return s


def test_fit16_end_to_end_with_aux_baseline():
    # W peak 8.7 GiB: + fixed 0.4 GiB = 9.1 GiB > 9.0 GiB -> small; + measured 0.2 GiB = 8.9 GiB -> fits
    samples = [smp(CAND, 0, level=2048, peak=6 * GIB), smp(CAND, 1, level=10240, peak=int(8.7 * GIB)),
               emu(CAND, 10, 2048), emu(CAND, 11, 10240)]
    out = A.aggregate(doc([mdl(CAND, "candidate")], samples))
    v = verdict(out, CAND)
    assert v["fits_16gb_mac"] == "fits_small_context" and v["ranked"] is True
    assert v["fits_16gb_mac_detail"]["aux_allowance_source"] == "fixed-0.4GiB"
    aux = smp("embedder-aux", 20, test="aux_baseline", level=None, aux_footprint_bytes=int(0.2 * GIB))
    out2 = A.aggregate(doc([mdl(CAND, "candidate")], samples + [aux]))
    v2 = verdict(out2, CAND)
    assert v2["fits_16gb_mac"] == "fits"
    assert v2["fits_16gb_mac_detail"]["aux_allowance_bytes"] == int(0.2 * GIB)
    assert v2["fits_16gb_mac_detail"]["max_level_fits_16gb"] == 10240
    assert v2["fits_16gb_mac_detail"]["peak_bytes_at_10240"] == int(8.7 * GIB)
    assert v2["fits_iphone_air"] is None and v2["fits_iphone_air_reason"] == "not_an_iphone_run"
    c = cell(out2, CAND, level=10240)
    assert c["fits"]["mac-ref"] == "yes" and c["fits"]["mac-16gb"] == "yes" and c["fits"]["mac-8gb"] == "estimated"
    # 5.0e9 x 1.05 + 0.5 GiB = 5.79e9 B > 8 GiB - 3 GiB = 5.37e9 B -> refused on 8 GB
    assert c["fits_estimate"]["mac-8gb"]["fits"] is False
    # the emulated cells carry their label and no ratios
    ec = cell(out2, CAND, test="fit16_emulation", level=10240)
    assert ec["ratio_reason"] == "not_compared_for_test" and "never" in ec["label"]


def test_fit16_not_measured_unranked():
    v = verdict(A.aggregate(doc([mdl(CAND, "candidate")], [smp(CAND, 0)])), CAND)
    assert v["fits_16gb_mac"] == "not_measured" and v["ranked"] is False
    assert v["fits_16gb_mac_under_load"] == "not_measured"


def test_fit16_borrows_other_mode_emulation():
    samples = [smp(CAND, 0, level=2048, mode="as_shipped"), smp(CAND, 1, level=10240, mode="as_shipped"),
               emu(CAND, 10, 2048, mode="as_shipped"), emu(CAND, 11, 10240, mode="as_shipped"), smp(CAND, 20)]
    out = A.aggregate(doc([mdl(CAND, "candidate"), mdl(CAND, "candidate", mode="as_shipped")], samples))
    v = verdict(out, CAND, mode="plain")
    assert v["fits_16gb_mac"] == "fits" and v["fits_16gb_mac_detail"]["fit_mode"] == "as_shipped"


# --- cloud ------------------------------------------------------------------------------------

def cloud_smp(order, level, ttft, total, cost, **kw):
    s = {"sample_id": f"c{order}", "model_id": "vendor/cloud-model", "mode": "as_shipped", "test": "cloud_level",
         "phase": "measured", "order_index": order, "level_tokens": level, "status": "ok", "net_ttft_s": ttft,
         "net_total_s": total, "tokens_in": 2100, "tokens_out": 250, "tokens_reasoning": None, "tokens_cached": 0,
         "cost_usd": cost, "t_wall_utc": "2026-01-02T10:00:00Z", "response_cache_source_id": None}
    s.update(kw)
    return s


def test_cloud_cells_and_verdict():
    samples = [cloud_smp(0, 2048, 0.8, 3.0, 0.001), cloud_smp(1, 2048, 0.9, 3.2, 0.001),
               cloud_smp(2, 2048, 0.1, 0.2, 0.0, response_cache_source_id="gen-cached"),
               cloud_smp(3, 2048, 1.0, 3.4, 0.002, status="provider_fallback", t_wall_utc="2026-01-03T09:00:00Z")]
    d = doc([{"id": "vendor/cloud-model", "entry_id": "vendor/cloud-model", "role": "cloud_reference",
              "mode": "as_shipped", "architecture": "unknown", "files": None}], samples,
            kind="cloud_reference", device=None, device_reason="cloud",
            runner={"name": "cloud-openrouter", "version": "0", "commit": "x"})
    out = A.aggregate(d)
    c = cell(out, "vendor/cloud-model", test="cloud_level", mode="as_shipped")
    assert c["n_voided"] == 1 and c["n_ok"] == 3                       # the cached response is voided
    assert c["net_ttft_s"]["median"] == pytest.approx(0.9) and c["net_total_s"]["max"] == pytest.approx(3.4)
    assert c["cost_usd_per_request"]["median"] == pytest.approx(0.001)
    assert c["label"] == "includes network and provider queue"
    assert c["run_dates"] == ["2026-01-02", "2026-01-03"] and c["n_provider_fallback"] == 1
    assert c["ratio_to_baseline"] is None and c["ratio_reason"] == "cloud_not_comparable"
    assert "decode_tps" not in c
    v = out["verdicts"][0]
    assert (v["fits_16gb_mac"], v["fits_iphone_air"], v["ranked"], v["reference"]) == \
        ("not_applicable", "not_applicable", False, "cloud")
    assert out["reference_pair_check"]["status"] == "not_applicable"


def test_cloud_cost_per_completed_task():
    s = [cloud_smp(i, None, 1.0, 2.0, cost, test="cloud_task", task_id=t, task_success=ok)
         for i, (t, cost, ok) in enumerate((("t1", 0.01, True), ("t1", 0.02, True), ("t2", 0.05, False),
                                             ("t3", 0.04, True)))]
    d = doc([{"id": "vendor/cloud-model", "role": "cloud_reference", "mode": "as_shipped"}], s,
            kind="cloud_reference", device=None)
    c = cell(A.aggregate(d), "vendor/cloud-model", test="cloud_task", level=None, mode="as_shipped")
    # completed tasks t1 (0.03) and t3 (0.04): nearest-rank median = 0.03; attempted 0.03, 0.04, 0.05 -> 0.04
    assert c["cost_usd_per_completed_task"] == pytest.approx(0.03)
    assert c["cost_usd_per_attempted_task"] == pytest.approx(0.04)


# --- misc --------------------------------------------------------------------------------------

def test_extra_derivers_called_last_and_cli(tmp_path, capsys):
    seen = []

    def deriver(d, by_entry):
        seen.append(sorted(by_entry))
        by_entry[(CAND, "plain", CAND)]["sustained"] = {"stability_ratio": 0.9}
    out = A.aggregate(doc([mdl(CAND, "candidate")], [smp(CAND, 0)]), extra_derivers=[deriver])
    assert seen == [[(CAND, "plain", CAND)]] and verdict(out, CAND)["sustained"]["stability_ratio"] == 0.9
    p = tmp_path / "run-x.json"
    p.write_text(json.dumps(doc([mdl(CAND, "candidate")], [smp(CAND, 0)])))
    assert A.main([str(p), "--out", str(tmp_path / "agg.json")]) == 0
    agg = json.loads((tmp_path / "agg.json").read_text())
    assert agg["cells"][0]["decode_tps"]["median"] == pytest.approx(100.0)
    assert agg["aggregation"]["stats"]["ci95_median"]["resamples"] == 2000
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "edgebench.run/0.1"}))
    assert A.main([str(bad)]) == 2


def test_input_not_mutated_and_json_clean():
    d = bracket_doc([201], [201], [211])
    before = json.dumps(d, sort_keys=True)
    out = A.aggregate(d)
    assert json.dumps(d, sort_keys=True) == before
    json.dumps(out, allow_nan=False)
    assert all(not k.startswith("_") for c in out["cells"] for k in c)
    assert math.isclose(cell(out, CAND)["decode_tps"]["median"], 105.0)
