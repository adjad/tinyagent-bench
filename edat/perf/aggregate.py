"""The EDAT performance aggregator: an ``edat.perf/1.0`` run document in, the same
document with ``cells[]`` and ``verdicts[]`` out (spec 04 §3.9, §3.10).

Runners never write cells or verdicts. The Mac runner and the iPhone importer both
call :func:`aggregate`, so every published number comes from this one piece of code.

    python -m edat.perf.aggregate <run.json> [--out <path>]

What it computes, with the section that defines it:

- per sample (§6.2, §13): ``ttft_s``, ``ttfa_s``, ``prefill_tps``, ``decode_tps``
  (``(n − 1) / (t_last − t_first)``), ``e2e_s``; load times (§7); speculation counts;
- ``cells[]``: one per (model, mode, entry, test, level), and per (step, replay_cache)
  for ``agent_replay`` and per ``app_load_gib`` for the pressure tests, with the
  §6.4 statistics, memory, energy (from ``energy_pair`` windows, §4.2), ratios to the
  baseline and to the reference (§1.3, §1.5), ``baseline_drift`` and ``fits`` (§5.4);
- ``verdicts[]``: one per ``models[]`` entry with the fit verdicts (§5.5–§5.7, via
  :mod:`edat.perf.fit`), KV slope fit and formula, overhead, bandwidth, summary
  indices and "fits where the baseline does not" (§5.2, §8, §1.3);
- a run-level ``reference_pair_check`` (§1.5) and ``baseline_drift``.

Choices the spec leaves open are listed in ``CHOICES`` and written into the output
document under ``aggregation.choices`` so a reader of the record can see them.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import constants as C
from . import fit as F
from .stats import STATS_METADATA, geometric_mean, linear_fit, median, summary

AGGREGATOR_VERSION = "0.1.0"

CHOICES = (
    "median, IQR and percentiles are nearest-rank, as scripts/release_performance.py",
    "status = worst over the cell's non-voided, non-superseded samples (warm-ups included); "
    "voided samples (cached prefix on level/energy_pair, voided_* statuses) are counted, not ranked",
    "an iPhone sample retried after a kill or a background void (higher `attempt`, same order_index) "
    "supersedes the earlier attempt",
    "load_warm, load_cold and first_token samples count in any phase; other tests exclude warm-ups",
    "prefill tokens = prompt_tokens - cached_tokens (newly prefilled)",
    "mem_peak uses footprint_peak_bytes (the per-request 20 Hz max), not the lifetime ledger peak",
    "bracket split is by the numerator model's own first and last sample (order_index), any test",
    "baseline_drift = |start - end| / mean(start, end) > 5 %",
    "tok_s_per_resident_GB = decode_tps / (mem_steady at that level, in GB)",
    "reference-pair e_pre/e_dec combined error bound = root-sum-square of the two error_bound_pct",
    "rerun_session counts distinct failed metrics, not levels",
    "as-shipped prediction: plain ratio x tokens-per-cycle ratio, cycles = (n - 1) - accepted",
    "weight bytes = resident_weight_bytes, else the weight files (.safetensors/.gguf/.bin/.npz/.pt/.pth), "
    "else every file",
    "16 GB fit uses the emulated mode's samples; a model entry without emulation samples of its own mode "
    "borrows another mode's (recorded as fit_mode)",
)

BETTER = {
    "ttft_s": "lower", "ttfa_s": "lower", "prefill_tps": "higher", "decode_tps": "higher", "e2e_s": "lower",
    "mem_peak": "lower", "mem_steady": "lower", "mem_loaded": "lower",
    "e_pre_mJ_per_tok": "lower", "e_dec_mJ_per_tok": "lower",
    "t_load_warm_s": "lower", "t_load_cold_s": "lower", "t_first_token_possible_s": "lower",
    "load_read_GBps": "higher", "spec_acceptance": "higher", "tokens_per_cycle": "higher",
    "drafts_per_cycle": "higher",
    "net_ttft_s": "lower", "net_total_s": "lower", "cost_usd_per_request": "lower",
    "tokens_in": "none", "tokens_out": "none", "tokens_reasoning": "none", "tokens_cached": "none",
    "prompt_tokens": "none", "output_tokens": "none", "reasoning_tokens": "none",
}
UNITS = {"mem_peak": "bytes", "mem_steady": "bytes", "mem_loaded": "bytes", "e_pre_mJ_per_tok": "mJ/token",
         "e_dec_mJ_per_tok": "mJ/token", "cost_usd_per_request": "USD"}

SPEED = ("ttft_s", "ttfa_s", "prefill_tps", "decode_tps", "e2e_s")
MEMORY = ("mem_peak", "mem_steady")
ENERGY = ("e_pre_mJ_per_tok", "e_dec_mJ_per_tok")
SPEC = ("spec_acceptance", "drafts_per_cycle", "tokens_per_cycle")
TOKENS = ("prompt_tokens", "output_tokens", "reasoning_tokens")
CLOUD_METRICS = ("net_ttft_s", "net_total_s", "tokens_in", "tokens_out", "tokens_reasoning", "tokens_cached",
                 "cost_usd_per_request")
LOAD_TESTS = ("load_warm", "load_cold")
ANY_PHASE_TESTS = ("load_warm", "load_cold", "first_token")
CACHE_VOID_TESTS = ("level", "energy_pair")
RATIO_METRICS = {
    "level": SPEED + MEMORY, "first_token": SPEED + MEMORY, "agent_replay": SPEED + MEMORY,
    "energy_pair": ENERGY,
    "load_warm": ("t_load_warm_s", "t_first_token_possible_s"),
    "load_cold": ("t_load_cold_s", "t_first_token_possible_s", "load_read_GBps"),
}
DRIFT_METRICS = ("decode_tps", "e_dec_mJ_per_tok")
INDEX_LEVELS = tuple(lv for lv in C.LEVELS if 512 <= lv <= 10240)
WEIGHT_SUFFIXES = (".safetensors", ".gguf", ".bin", ".npz", ".pt", ".pth")
OK = set(C.OK_STATUSES) | {"provider_fallback"}
COLD_CHECK_FRACTION = 0.90
EPS = 1e-9

KV_DTYPE_BYTES = {"bf16": 2, "bfloat16": 2, "fp16": 2, "float16": 2, "f16": 2, "fp32": 4, "float32": 4,
                  "f32": 4, "fp8": 1, "int8": 1, "q8": 1, "8bit": 1, "q4": 0.5, "int4": 0.5, "4bit": 0.5}

EnergyFn = Callable[..., Mapping]


# --- small helpers -----------------------------------------------------------------

def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _sec(a, b) -> float | None:
    a, b = _int(a), _int(b)
    if a is None or b is None or b < a:
        return None
    return (b - a) / 1e9


def _is_void(status: str | None) -> bool:
    return bool(status) and status.startswith("voided_")


def _fmt_level(level) -> str:
    return "none" if level is None else str(level)


# --- KV formula (§5.2) ---------------------------------------------------------------

def kv_formula_detail(config: Mapping, kv_dtype_bytes: float = 2) -> dict:
    """KV-cache growth from the architecture: 2 × Σ_layers(kv_heads × head_dim) × bytes, per token.

    ``config`` keys: ``layer_types`` (one string per layer), ``num_key_value_heads`` and
    ``head_dim`` (an int, or one value per layer), optional ``global_head_dim`` for full-attention
    layers, ``sliding_window`` and ``num_kv_shared_layers``.

    - Full/global attention layers grow with every token: they make the slope.
    - Sliding-window layers are capped at their window: a fixed block, no slope.
    - The last ``num_kv_shared_layers`` layers reuse an earlier layer's cache: counted once, i.e.
      not again.
    - Other layer types (linear attention, SSM) hold a fixed state and add no slope.
    """
    types = list(config["layer_types"])
    n = len(types)
    heads = config["num_key_value_heads"]
    hdim = config["head_dim"]
    gdim = config.get("global_head_dim")
    window = config.get("sliding_window")
    shared = int(config.get("num_kv_shared_layers") or 0)
    if not 0 <= shared <= n:
        raise ValueError("num_kv_shared_layers outside 0..len(layer_types)")
    own = n - shared
    per_token = 0.0
    block = 0.0
    full_layers = sliding_layers = 0
    for i, t in enumerate(types[:own]):
        h = heads[i] if isinstance(heads, (list, tuple)) else heads
        tl = str(t).lower()
        is_sliding = "sliding" in tl or tl in ("local", "local_attention", "chunked_attention")
        is_full = not is_sliding and ("full" in tl or "global" in tl or tl == "attention")
        d = hdim[i] if isinstance(hdim, (list, tuple)) else (gdim if (is_full and gdim) else hdim)
        b = 2 * h * d * kv_dtype_bytes
        if is_sliding:
            if not window:
                raise ValueError("a sliding-window layer needs sliding_window")
            block += b * window
            sliding_layers += 1
        elif is_full:
            per_token += b
            full_layers += 1
    return {"MiB_per_1k": per_token * 1024 / C.MIB, "bytes_per_token": per_token,
            "sliding_block_MiB": block / C.MIB, "full_layers_counted": full_layers,
            "sliding_layers_counted": sliding_layers, "shared_layers_not_counted": shared,
            "kv_dtype_bytes": kv_dtype_bytes}


def kv_formula_MiB_per_1k(config: Mapping, kv_dtype_bytes: float = 2) -> float:
    return kv_formula_detail(config, kv_dtype_bytes)["MiB_per_1k"]


# --- per-sample metrics (§6.2, §7, §13) ---------------------------------------------

def token_offsets(s: Mapping) -> list[int]:
    offs = [o for o in (s.get("token_times_ns") or ()) if _int(o) is not None]
    if offs and offs[0] != 0:
        offs = [0] + offs
    return offs


def decode_tps(s: Mapping) -> float | None:
    """(n − 1) / (t_last − t_first): the first token is excluded (§6.2)."""
    n = _int(s.get("output_tokens"))
    tf, tl = _int(s.get("t_first_token_ns")), _int(s.get("t_last_token_ns"))
    offs = token_offsets(s)
    if n is None and offs:
        n = len(offs)
    if tl is None and tf is not None and offs:
        tl = tf + offs[-1]
    if n is None or n < 2 or tf is None or tl is None or tl <= tf:
        return None
    return (n - 1) / ((tl - tf) / 1e9)


def median_decode_interval_s(s: Mapping) -> float | None:
    offs = token_offsets(s)
    if len(offs) >= 2:
        return median([b - a for a, b in zip(offs, offs[1:])]) / 1e9
    n = _int(s.get("output_tokens"))
    d = _sec(s.get("t_first_token_ns"), s.get("t_last_token_ns"))
    if n and n >= 2 and d:
        return d / (n - 1)
    return None


def prefill(s: Mapping) -> tuple[float | None, str | None]:
    """prompt tokens / prefill time: server stamp when present, else TTFT − one median decode interval."""
    pt = _int(s.get("prompt_tokens"))
    if pt is None:
        return None, None
    pt -= _int(s.get("cached_tokens")) or 0
    if pt <= 0:
        return None, None
    t = _sec(s.get("t_submit_ns"), s.get("t_prefill_done_ns"))
    if t:
        return pt / t, "server"
    ttft = _sec(s.get("t_submit_ns"), s.get("t_first_token_ns"))
    iv = median_decode_interval_s(s)
    if ttft is not None and iv is not None and ttft - iv > 0:
        return pt / (ttft - iv), "ttft_minus_decode_interval"
    return None, None


def peak_bytes(s: Mapping) -> int | None:
    p = _int(s.get("footprint_peak_bytes"))
    return None if p is None else p + (_int(s.get("neural_bytes")) or 0)


def fit_peak_bytes(s: Mapping) -> int | None:
    p = _int(s.get("footprint_model_bytes"))
    if p is None:
        p = _int(s.get("footprint_peak_bytes"))
    return None if p is None else p + (_int(s.get("neural_bytes")) or 0)


def steady_bytes(s: Mapping) -> int | None:
    p = _int(s.get("footprint_steady_bytes"))
    return None if p is None else p + (_int(s.get("neural_bytes")) or 0)


def speculation(s: Mapping) -> dict:
    drafted, accepted = _int(s.get("drafted_tokens")), _int(s.get("accepted_tokens"))
    n = _int(s.get("output_tokens"))
    if drafted is None or accepted is None or n is None or n < 2:
        return {}
    cycles = (n - 1) - accepted
    out = {"spec_acceptance": accepted / drafted if drafted else None}
    if cycles > 0:
        out["drafts_per_cycle"] = drafted / cycles
        out["tokens_per_cycle"] = (n - 1) / cycles
    return out


def sample_metrics(s: Mapping, *, weight_bytes: int | None = None) -> dict:
    test = s.get("test")
    m: dict[str, Any] = {}
    if test in LOAD_TESTS:
        ld = s.get("load") or {}
        t = _sec(ld.get("t_load_start_ns"), ld.get("t_ready_ns"))
        m["t_load_cold_s" if test == "load_cold" else "t_load_warm_s"] = t
        m["t_first_token_possible_s"] = _sec(ld.get("t_load_start_ns"), ld.get("t_first_token_possible_ns"))
        rb = _int(ld.get("disk_read_bytes"))
        m["load_read_GBps"] = (rb / 1e9 / t) if rb is not None and t else None
        if test == "load_cold":
            if rb is not None and weight_bytes:
                m["cold_verified"] = rb >= COLD_CHECK_FRACTION * weight_bytes
            elif ld.get("cold_verified") is not None:
                m["cold_verified"] = bool(ld["cold_verified"])
            else:
                m["cold_verified"] = None
        return m
    if test in ("cloud_level", "cloud_task"):
        for k in ("net_ttft_s", "net_total_s", "tokens_in", "tokens_out", "tokens_reasoning", "tokens_cached"):
            m[k] = s.get(k)
        m["cost_usd_per_request"] = s.get("cost_usd")
        return m
    m["ttft_s"] = _sec(s.get("t_submit_ns"), s.get("t_first_token_ns"))
    m["ttfa_s"] = _sec(s.get("t_submit_ns"), s.get("t_first_answer_ns"))
    m["prefill_tps"], m["_prefill_source"] = prefill(s)
    m["decode_tps"] = decode_tps(s)
    m["e2e_s"] = _sec(s.get("t_submit_ns"), s.get("t_end_ns"))
    m["mem_peak"] = peak_bytes(s)
    m["mem_steady"] = steady_bytes(s)
    m["_fit_peak"] = fit_peak_bytes(s)
    m["_swap_growth"] = F._growth(s)
    for k in TOKENS:
        m[k] = _int(s.get(k))
    m.update(speculation(s))
    return m


# --- models -------------------------------------------------------------------------

def _role(m: Mapping) -> str:
    if m.get("role"):
        return m["role"]
    if m.get("id") == C.BASELINE_ID:
        return "baseline"
    if m.get("id") == C.REFERENCE_ID:
        return "reference"
    return "candidate"


def _entry_id(m: Mapping) -> str:
    return m.get("entry_id") or m["id"]


def weight_bytes(m: Mapping) -> tuple[int | None, str | None]:
    if _int(m.get("resident_weight_bytes")) is not None:
        return m["resident_weight_bytes"], "resident_weight_bytes"
    files = m.get("files") or []
    if not files:
        return None, None
    wf = [f for f in files if str(f.get("name", "")).lower().endswith(WEIGHT_SUFFIXES)]
    use = wf or files
    return sum(int(f.get("bytes") or 0) for f in use), "files"


def disk_weight_bytes(m: Mapping) -> int | None:
    files = m.get("files") or []
    if not files:
        return None
    wf = [f for f in files if str(f.get("name", "")).lower().endswith(WEIGHT_SUFFIXES)]
    return sum(int(f.get("bytes") or 0) for f in (wf or files))


class _Models:
    def __init__(self, models: Sequence[Mapping]):
        self.entries = list(models)
        self.by_full: dict = {}
        self.by_mode: dict = {}
        self.by_id: dict = {}
        for i, m in enumerate(self.entries):
            self.by_full.setdefault((m["id"], m.get("mode") or "as_shipped", _entry_id(m)), m)
            self.by_mode.setdefault((m["id"], m.get("mode") or "as_shipped"), m)
            self.by_id.setdefault(m["id"], m)
        self.order = {m["id"]: i for i, m in reversed(list(enumerate(self.entries)))}

    def find(self, s: Mapping) -> Mapping | None:
        mid, mode = s.get("model_id"), s.get("mode") or "as_shipped"
        if s.get("entry_id"):
            m = self.by_full.get((mid, mode, s["entry_id"]))
            if m is not None:
                return m
        return self.by_mode.get((mid, mode)) or self.by_id.get(mid)

    def key(self, s: Mapping) -> tuple:
        m = self.find(s)
        mode = s.get("mode") or (m or {}).get("mode") or "as_shipped"
        entry = s.get("entry_id") or (_entry_id(m) if m else s.get("model_id"))
        return s.get("model_id"), mode, entry


# --- cells --------------------------------------------------------------------------

@dataclass
class _Cell:
    model_id: str
    mode: str
    entry_id: str
    test: str
    level: int | None
    extras: tuple
    model: Mapping | None
    samples: list = field(default_factory=list)
    counted: list = field(default_factory=list)       # in stats: ok and not voided
    values: list = field(default_factory=list)        # [(order_index, metrics)]
    windows: list = field(default_factory=list)       # [(first_order, last_order, energy dict)]
    out: dict = field(default_factory=dict)

    @property
    def role(self) -> str:
        return _role(self.model or {"id": self.model_id})

    @property
    def match_key(self) -> tuple:
        return self.mode, self.test, self.level, self.extras

    def metric_values(self, metric: str) -> list:
        return [(o, m.get(metric)) for o, m in self.values if m.get(metric) is not None]


def _extras(s: Mapping) -> tuple:
    test = s.get("test")
    ex = []
    if test == "agent_replay":
        ex = [("replay_cache", s.get("replay_cache")), ("step", s.get("step"))]
    elif test in ("pressure_sweep", "fit16_under_load"):
        ex = [("app_load_gib", s.get("app_load_gib"))]
    return tuple(ex)


def _cell_id(c: _Cell) -> str:
    parts = [c.model_id, c.mode, c.entry_id, c.test, _fmt_level(c.level)]
    parts += [f"{k}={v}" for k, v in c.extras]
    return "|".join(str(p) for p in parts)


def _superseded(samples: Sequence[Mapping]) -> set:
    best: dict = {}
    for i, s in enumerate(samples):
        a = _int(s.get("attempt"))
        if a is None or _int(s.get("order_index")) is None:
            continue
        k = (s.get("model_id"), s.get("mode"), s.get("test"), s["order_index"])
        if k not in best or a > best[k][0]:
            best[k] = (a, i)
    out = set()
    for i, s in enumerate(samples):
        a = _int(s.get("attempt"))
        if a is None or _int(s.get("order_index")) is None:
            continue
        k = (s.get("model_id"), s.get("mode"), s.get("test"), s["order_index"])
        if best[k][1] != i:
            out.add(i)
    return out


def _effective_status(s: Mapping, kind: str) -> str:
    st = s.get("status") or "ok"
    if kind == "cloud_reference" and s.get("response_cache_source_id") is not None:
        return "voided_response_cache"
    return st


def _build_cells(doc: Mapping, models: _Models, kind: str) -> tuple[list[_Cell], dict]:
    samples = list(doc.get("samples") or ())
    sup = _superseded(samples)
    cells: dict[tuple, _Cell] = {}
    span: dict[str, list] = {}
    for i, s in enumerate(samples):
        if i in sup:
            continue
        mid, mode, entry = models.key(s)
        o = _int(s.get("order_index"))
        if o is not None:
            span.setdefault(mid, []).append(o)
        k = (mid, mode, entry, s.get("test"), s.get("level_tokens"), _extras(s))
        if k not in cells:
            cells[k] = _Cell(mid, mode, entry, s.get("test"), s.get("level_tokens"), _extras(s), models.find(s))
        cells[k].samples.append(s)
    # superseded counts
    for i in sup:
        s = samples[i]
        mid, mode, entry = models.key(s)
        k = (mid, mode, entry, s.get("test"), s.get("level_tokens"), _extras(s))
        if k in cells:
            cells[k].out["n_superseded"] = cells[k].out.get("n_superseded", 0) + 1
    spans = {mid: (min(v), max(v)) for mid, v in span.items()}
    ordered = sorted(cells.values(), key=lambda c: (models.order.get(c.model_id, 1 << 30), c.mode, c.entry_id,
                                                     c.test or "", c.level if c.level is not None else -1,
                                                     json.dumps(c.extras, default=str)))
    return ordered, spans


def _fill_cell(c: _Cell, kind: str) -> None:
    out = c.out
    wb = disk_weight_bytes(c.model or {})
    statuses_ranked, counts = [], defaultdict(int)
    n_warm = n_meas = n_ok = n_failed = n_void_cache = n_void = 0
    prefill_src: dict = defaultdict(int)
    hits_when_defeated = 0
    for s in c.samples:
        st = _effective_status(s, kind)
        counts[st] += 1
        warm = s.get("phase") == "warmup" and c.test not in ANY_PHASE_TESTS
        cached = _int(s.get("cached_tokens")) or 0
        if c.test in CACHE_VOID_TESTS and cached > 0:
            n_void_cache += 1
            continue
        if c.test == "agent_replay" and s.get("replay_cache") == "defeated" and cached > 0:
            hits_when_defeated += 1
        if _is_void(st):
            n_void += 1
            continue
        statuses_ranked.append(st)
        if warm:
            n_warm += 1
            continue
        n_meas += 1
        if st in OK:
            n_ok += 1
            c.counted.append(s)
            m = sample_metrics(s, weight_bytes=wb)
            if m.get("_prefill_source"):
                prefill_src[m["_prefill_source"]] += 1
            c.values.append((_int(s.get("order_index")) if _int(s.get("order_index")) is not None else -1, m))
        else:
            n_failed += 1
    status = F.worst_status(statuses_ranked) or F.worst_status(counts) or None
    out.update({"cell_id": _cell_id(c), "model_id": c.model_id, "mode": c.mode, "entry_id": c.entry_id,
                "role": c.role, "test": c.test, "level_tokens": c.level})
    for k, v in c.extras:
        out[k] = v
    out.update({"n": len(c.samples), "n_measured": n_meas, "n_ok": n_ok, "n_failed": n_failed,
                "n_warmup": n_warm, "n_voided_cache": n_void_cache, "n_voided": n_void,
                "n_superseded": out.get("n_superseded", 0), "status": status, "statuses": dict(counts)})
    if hits_when_defeated:
        out["n_cache_hits_when_defeated"] = hits_when_defeated
    if c.test in ("fit16_emulation", "fit16_under_load", "pressure_sweep"):
        out["label"] = "constrained run on mac-ref; never reported as 16 GB Mac speed"
    if c.test in ("cloud_level", "cloud_task"):
        names = CLOUD_METRICS
    elif c.test in LOAD_TESTS:
        names = RATIO_METRICS[c.test]
    else:
        names = SPEED + MEMORY + TOKENS + SPEC
    for name in names:
        vals = [v for _, v in c.metric_values(name)]
        if not vals and name in SPEC + ("ttfa_s",):
            continue
        out[name] = _metric(name, vals)
    if prefill_src:
        out["prefill_tps_source"] = dict(prefill_src)
    if c.test == "load_cold":
        ver = [m.get("cold_verified") for _, m in c.values]
        known = [v for v in ver if v is not None]
        out["n_cold_verified"] = sum(1 for v in known if v)
        out["cold_verified_rate"] = (out["n_cold_verified"] / len(known)) if known else None
        cold = [m for _, m in c.values if m.get("cold_verified") is not False]
        vals = [m.get("t_load_cold_s") for m in cold]
        out["t_load_cold_s"] = _metric("t_load_cold_s", vals)
        out["load_read_GBps"] = _metric("load_read_GBps", [m.get("load_read_GBps") for m in cold])
        out["t_first_token_possible_s"] = _metric("t_first_token_possible_s",
                                                  [m.get("t_first_token_possible_s") for m in cold])
        if not [v for v in vals if v is not None]:
            out["t_load_cold_s_reason"] = "cold_not_verified"
    if c.test == "first_token":
        loaded = [steady for steady in (_loaded_bytes(s) for s in c.counted) if steady is not None]
        out["mem_loaded"] = _metric("mem_loaded", loaded)


def _loaded_bytes(s: Mapping) -> int | None:
    p = _int(s.get("footprint_end_bytes"))
    return None if p is None else p + (_int(s.get("neural_bytes")) or 0)


def _metric(name: str, vals: Iterable) -> dict:
    d = summary(vals)
    d["better"] = BETTER.get(name, "none")
    if name in UNITS:
        d["unit"] = UNITS[name]
    return d


def _value(c: _Cell, metric: str):
    v = c.out.get(metric)
    if isinstance(v, Mapping):
        return v.get("median")
    return v


# --- energy (§4.2) --------------------------------------------------------------------

def _default_energy_fn():
    from .energy import pair_cell  # lazy: written in parallel, needed only when there are windows
    return pair_cell


def _default_trace_reader():
    from .power import read_trace
    return read_trace


def _fill_energy(doc: Mapping, cells: Sequence[_Cell], models: _Models, run_dir: Path | None,
                 energy_fn: EnergyFn | None, trace_reader: Callable | None) -> None:
    windows = list(doc.get("energy_windows") or ())
    if not windows:
        return
    by_sample: dict = {}
    for c in cells:
        if c.test != "energy_pair":
            continue
        for s in c.counted:
            by_sample[s.get("sample_id")] = c
    traces: dict = {}
    for w in windows:
        groups: dict = defaultdict(list)
        for sid in w.get("sample_ids") or ():
            c = by_sample.get(sid)
            if c is not None:
                groups[id(c)].append((c, next(s for s in c.counted if s.get("sample_id") == sid)))
        if not groups:
            continue
        name = w.get("trace_attachment")
        trace, reason = None, None
        if not name:
            reason = "no_trace_attachment"
        elif run_dir is None:
            reason = "no_run_dir"
        else:
            if name not in traces:
                try:
                    reader = trace_reader or _default_trace_reader()
                    traces[name] = (reader(Path(run_dir) / name), None)
                except Exception as e:  # noqa: BLE001 - a bad trace voids the window, not the run
                    traces[name] = (None, f"trace_unreadable: {type(e).__name__}")
            trace, reason = traces[name]
        for _, pairs in groups.items():
            c = pairs[0][0]
            ss = [s for _, s in pairs]
            orders = [_int(s.get("order_index")) for s in ss if _int(s.get("order_index")) is not None]
            first, last = (min(orders), max(orders)) if orders else (-1, -1)
            entry: dict[str, Any] = {"window_id": w.get("window_id"), "n_samples": len(ss)}
            if trace is None:
                entry["energy_reason"] = reason or "no_trace"
            else:
                try:
                    fn = energy_fn or _default_energy_fn()
                    entry.update(dict(fn(ss, trace, w, level_tokens=c.level)))
                except Exception as e:  # noqa: BLE001
                    entry["energy_reason"] = f"energy_error: {type(e).__name__}: {e}"[:200]
            c.windows.append((first, last, entry))
    for c in cells:
        if c.test != "energy_pair" or not c.windows:
            continue
        good = [e for _, _, e in c.windows if e.get("e_dec_mJ_per_tok") is not None or e.get("e_pre_mJ_per_tok") is not None]
        out = c.out
        out["energy_windows"] = [e for _, _, e in c.windows]
        for k in ENERGY:
            vals = [e.get(k) for e in good if e.get(k) is not None]
            out[k] = (sum(vals) / len(vals)) if vals else None
        bounds = [e.get("error_bound_pct") for e in good if e.get("error_bound_pct") is not None]
        out["error_bound_pct"] = max(bounds) if bounds else None
        scopes = sorted({e.get("scope") for e in good if e.get("scope")})
        out["scope"] = scopes[0] if len(scopes) == 1 else (scopes or None)
        sources = sorted({e.get("source") for e in good if e.get("source")})
        out["energy_source"] = sources[0] if len(sources) == 1 else (sources or None)
        out["is_estimate"] = any(bool(e.get("is_estimate")) for e in good) if good else None
        flags = []
        for e in good:
            flags.extend(e.get("flags") or ())
        if len(scopes) > 1:
            flags.append("scope_mixed")
        out["energy_flags"] = sorted(set(flags))
        if not good:
            out["energy_reason"] = c.windows[0][2].get("energy_reason")
        out["energy_better"] = "lower"


def _window_values(c: _Cell, metric: str) -> list:
    return [(a, b, e.get(metric)) for a, b, e in c.windows if e.get(metric) is not None]


# --- ratios (§1.3, §1.4, §1.5) ----------------------------------------------------------

def _bracket(den: _Cell, metric: str, span: tuple | None) -> dict | None:
    if metric in ENERGY:
        wv = _window_values(den, metric)
        if not wv:
            return None
        if span:
            start = [v for a, b, v in wv if b < span[0]]
            end = [v for a, b, v in wv if a > span[1]]
            if start and end:
                ms, me = median(start), median(end)
                return {"value": (ms + me) / 2, "bracket": "two", "start": ms, "end": me}
        return {"value": _value(den, metric), "bracket": "single", "start": None, "end": None}
    vals = den.metric_values(metric)
    if not vals:
        return None
    if span:
        start = [v for o, v in vals if o < span[0]]
        end = [v for o, v in vals if o > span[1]]
        if start and end:
            ms, me = median(start), median(end)
            return {"value": (ms + me) / 2, "bracket": "two", "start": ms, "end": me}
    return {"value": median([v for _, v in vals]), "bracket": "single", "start": None, "end": None}


def _pick_den(cands: Sequence[_Cell], num: _Cell) -> _Cell | None:
    if not cands:
        return None
    thinking = (num.model or {}).get("thinking")
    for c in cands:
        if thinking and (c.model or {}).get("thinking") == thinking:
            return c
    for c in cands:
        if (c.model or {}).get("headline_entry"):
            return c
    return cands[0]


def _ratios(num: _Cell, den: _Cell | None, span: tuple | None, label: str) -> tuple[dict | None, str | None, dict]:
    """Per-metric ratios num ÷ den, each with its own direction. Returns (ratios, reason, drift)."""
    metrics = RATIO_METRICS.get(num.test, ())
    if den is None:
        return None, f"{label}_n/a", {}
    if den.out.get("status") not in OK:
        return None, f"{label}_not_ok", {}
    if num.out.get("status") not in OK:
        return None, "candidate_not_ok", {}
    out, drift = {}, {}
    for m in metrics:
        nv = _value(num, m)
        b = _bracket(den, m, span)
        dv = b["value"] if b else None
        row: dict[str, Any] = {"better": BETTER.get(m, "none")}
        if nv is None or dv is None or dv == 0:
            row.update({"ratio": None, "reason": "metric_missing"})
        else:
            row.update({"ratio": nv / dv, "value": nv, label: dv, "bracket": b["bracket"]})
            if b["bracket"] == "two":
                row[f"{label}_start"], row[f"{label}_end"] = b["start"], b["end"]
        out[m] = row
        if m in DRIFT_METRICS and b and b["bracket"] == "two":
            s, e = b["start"], b["end"]
            mean = (s + e) / 2
            pct = abs(s - e) / mean if mean else 0.0
            drift[m] = {"start": s, "end": e, "drift": pct,
                        "flag": pct > C.BASELINE_DRIFT_LIMIT + EPS}
    return out, None, drift


def _denominators(doc: Mapping, models: _Models, is_iphone: bool) -> tuple[str, str, dict]:
    base, ref, notes = C.BASELINE_ID, C.REFERENCE_ID, {}
    if is_iphone:
        for m in models.entries:
            if _role(m) == "variant" and m.get("variant_of") == C.BASELINE_ID:
                base, notes["baseline"] = m["id"], "iphone_fallback_variant"
            if _role(m) == "variant" and m.get("variant_of") == C.REFERENCE_ID:
                ref, notes["reference"] = m["id"], "iphone_fallback_variant"
    return base, ref, notes


def _fill_ratios(cells: Sequence[_Cell], spans: dict, base_id: str, ref_id: str, notes: dict, kind: str,
                 models: _Models) -> list:
    idx: dict = defaultdict(list)
    for c in cells:
        idx[(c.model_id,) + c.match_key].append(c)
    drift_cells = []
    has_as_shipped = {(c.model_id, c.entry_id) for c in cells if c.mode == "as_shipped"}
    for c in cells:
        out = c.out
        out["headline_ratio"] = c.mode == "plain"
        if kind == "cloud_reference":
            out.update({"ratio_to_baseline": None, "ratio_reason": "cloud_not_comparable",
                        "ratio_to_reference": None, "ratio_to_reference_reason": "cloud_not_comparable",
                        "baseline_drift": False})
            continue
        if c.test not in RATIO_METRICS:
            out.update({"ratio_to_baseline": None, "ratio_reason": "not_compared_for_test",
                        "ratio_to_reference": None, "ratio_to_reference_reason": "not_compared_for_test",
                        "baseline_drift": False})
            continue
        span = spans.get(c.model_id)
        for label, den_id, field_, reason_field in (("baseline", base_id, "ratio_to_baseline", "ratio_reason"),
                                                    ("reference", ref_id, "ratio_to_reference",
                                                     "ratio_to_reference_reason")):
            if c.model_id == den_id:
                out[field_], out[reason_field] = None, f"is_{label}"
                continue
            den = _pick_den(idx.get((den_id,) + c.match_key, []), c)
            r, reason, drift = _ratios(c, den, span, label)
            out[field_], out[reason_field] = r, reason
            out[f"{label}_model_id"] = den_id
            out[f"{label}_entry_id"] = den.entry_id if den else None
            if label in notes:
                out[f"{label}_note"] = notes[label]
            if label == "baseline":
                out["baseline_drift"] = any(d["flag"] for d in drift.values())
                if drift:
                    out["baseline_drift_detail"] = drift
                if out["baseline_drift"]:
                    drift_cells.append(out["cell_id"])
            else:
                out["reference_drift"] = any(d["flag"] for d in drift.values())
            # §1.4 D6b: a model with no drafter has as_shipped = plain; record that ratio too.
            if c.mode == "plain" and (c.model_id, c.entry_id) not in has_as_shipped and \
                    (c.model or {}).get("speculation", "none") in ("none", None):
                key = (den_id, "as_shipped", c.test, c.level, c.extras)
                den_as = _pick_den(idx.get(key, []), c)
                if den_as is not None:
                    r2, reason2, _ = _ratios(c, den_as, span, label)
                    out[f"{field_}_as_shipped"] = r2
                    if reason2:
                        out[f"{field_}_as_shipped_reason"] = reason2
                    out["as_shipped_equals_plain"] = True
        out.setdefault("baseline_drift", False)
    return drift_cells


# --- model-level derivations (§5.2, §8, §1.3) -------------------------------------------

def _kv_dtype_bytes(doc: Mapping, m: Mapping) -> tuple[float, str]:
    cfg = m.get("kv_config") or {}
    if cfg.get("kv_dtype_bytes"):
        return float(cfg["kv_dtype_bytes"]), "kv_config"
    dt = str((doc.get("runtime") or {}).get("kv_dtype") or "").lower()
    if dt in KV_DTYPE_BYTES:
        return float(KV_DTYPE_BYTES[dt]), f"runtime.kv_dtype={dt}"
    return 2.0, "default_16bit"


def _level_cells(cells: Sequence[_Cell], key: tuple, test: str = "level") -> dict:
    return {c.level: c for c in cells if (c.model_id, c.mode, c.entry_id) == key and c.test == test
            and c.level is not None and not c.extras}


def _unconstrained(level_cells: Mapping[int, _Cell]) -> dict:
    out = {}
    for lv, c in level_cells.items():
        out[lv] = {"status": c.out.get("status"),
                   "peak_bytes": median([m.get("_fit_peak") for _, m in c.values]),
                   "decode_tps": _value(c, "decode_tps")}
    return out


def _observations(samples: Iterable[Mapping], kind: str) -> list:
    obs = []
    for s in samples:
        st = _effective_status(s, kind)
        if _is_void(st) or s.get("phase") == "warmup":
            continue
        m = sample_metrics(s)
        obs.append({"level": s.get("level_tokens"), "status": st, "swap_delta_bytes": s.get("swap_delta_bytes"),
                    "compressed_delta_bytes": s.get("compressed_delta_bytes"), "decode_tps": m.get("decode_tps"),
                    "ttft_s": m.get("ttft_s"), "app_load_gib": s.get("app_load_gib"),
                    "app_load_swapped_bytes": s.get("app_load_swapped_bytes"),
                    "swap_in_bytes": s.get("swap_in_bytes")})
    return obs


def _emulation_conditions(doc: Mapping, samples: Sequence[Mapping]) -> dict:
    for s in samples:
        e = s.get("emulation") or (s.get("conditions") or {}).get("emulation")
        if e:
            return dict(e)
    for k in ("conditions_start", "conditions_end"):
        e = (doc.get(k) or {}).get("emulation")
        if e:
            return dict(e)
    return {}


def _aux_allowance(doc: Mapping, kind: str) -> tuple[int, str]:
    vals = []
    for s in doc.get("samples") or ():
        if s.get("test") != "aux_baseline" or _effective_status(s, kind) not in OK:
            continue
        v = _int(s.get("aux_footprint_bytes"))
        if v is None:
            v = _int(s.get("footprint_peak_bytes"))
        if v is not None:
            vals.append(v)
    if vals:
        return max(vals), "measured_aux_baseline"
    return C.AUX_ALLOWANCE_BYTES, "fixed-0.4GiB"


def _test_samples(doc: Mapping, models: _Models, key: tuple, test: str, sup: set) -> list:
    out = []
    for i, s in enumerate(doc.get("samples") or ()):
        if i in sup or s.get("test") != test:
            continue
        if models.key(s) == key:
            out.append(s)
    return out


def _local_verdict(doc: Mapping, m: Mapping, cells: Sequence[_Cell], models: _Models, kind: str, is_iphone: bool,
                   sup: set, aux: tuple[int, str]) -> dict:
    key = (m["id"], m.get("mode") or "as_shipped", _entry_id(m))
    v: dict[str, Any] = {"model_id": m["id"], "entry_id": _entry_id(m), "mode": key[1], "role": _role(m),
                         "headline_entry": bool(m.get("headline_entry"))}
    lc = _level_cells(cells, key)
    unc = _unconstrained(lc)
    wb, wsrc = weight_bytes(m)
    disk_wb = disk_weight_bytes(m)
    run_id = doc.get("run_id")
    # --- 16 GB Mac (§5.5) and under load (§5.6)
    if is_iphone:
        v["fits_16gb_mac"], v["fits_16gb_mac_reason"] = None, "not_a_mac_run"
        v["fits_16gb_mac_under_load"], v["fits_16gb_mac_under_load_reason"] = None, "not_a_mac_run"
    else:
        emu_key, emu = key, _test_samples(doc, models, key, "fit16_emulation", sup)
        if not emu:
            for other in models.entries:
                ok_ = (other["id"] == m["id"] and _entry_id(other) == _entry_id(m))
                k2 = (other["id"], other.get("mode") or "as_shipped", _entry_id(other))
                if ok_ and k2 != key:
                    e2 = _test_samples(doc, models, k2, "fit16_emulation", sup)
                    if e2:
                        emu_key, emu = k2, e2
                        break
        unc_emu = unc if emu_key == key else _unconstrained(_level_cells(cells, emu_key))
        cond = _emulation_conditions(doc, emu)
        verdict, detail = F.fit16_verdict(unc_emu, _observations(emu, kind), run_id=run_id,
                                          wired_limit_mb=cond.get("wired_limit_mb"),
                                          balloon_gib=cond.get("balloon_gib", C.BALLOON_GIB),
                                          aux_allowance_bytes=aux[0], aux_allowance_source=aux[1])
        detail["fit_mode"] = emu_key[1]
        v["fits_16gb_mac"], v["fits_16gb_mac_detail"] = verdict, detail
        ul = _test_samples(doc, models, key, "fit16_under_load", sup)
        if ul:
            cond_ul = _emulation_conditions(doc, ul)
            v["fits_16gb_mac_under_load"], v["fits_16gb_mac_under_load_detail"] = F.fit16_under_load_verdict(
                unc, _observations(ul, kind), run_id=run_id, wired_limit_mb=cond_ul.get("wired_limit_mb"),
                balloon_gib=cond_ul.get("balloon_gib", C.BALLOON_UNDER_LOAD_GIB),
                app_load_gib=cond_ul.get("app_load_gib", C.APP_LOAD_GIB),
                aux_allowance_bytes=aux[0], aux_allowance_source=aux[1])
        else:
            v["fits_16gb_mac_under_load"] = "not_measured"
        sweep = _test_samples(doc, models, key, "pressure_sweep", sup)
        if sweep:
            v["pressure_curve"], v["pressure_knee_gib"] = F.pressure_curve(unc, _observations(sweep, kind))
        else:
            v["pressure_curve"], v["pressure_knee_gib"] = [], None
    # --- iPhone Air (§2.9, §5.5)
    if is_iphone:
        load_killed = any(_effective_status(s, kind) == "oom_killed_at_load"
                          for s in (doc.get("samples") or ()) if models.key(s) == key)
        bal = doc.get("balloon")
        v["fits_iphone_air"], v["fits_iphone_air_detail"] = F.fits_iphone_air_verdict(
            unc, balloon=bal, balloon_run_id=(bal or {}).get("run_id", run_id) if bal else None,
            load_killed=load_killed)
    else:
        v["fits_iphone_air"], v["fits_iphone_air_reason"] = None, "not_an_iphone_run"
    gate = v["fits_iphone_air"] if is_iphone else v["fits_16gb_mac"]
    v["ranked"] = gate in F.RANKED_VERDICTS
    if is_iphone:
        v["ranked_gate"] = "fits_iphone_air"
    # --- memory (§5.2)
    ft = [c for c in cells if (c.model_id, c.mode, c.entry_id) == key and c.test == "first_token"]
    loaded = ft[0].out.get("mem_loaded", {}).get("median") if ft else None
    v["mem_loaded_bytes"] = loaded
    if loaded is None:
        v["mem_loaded_reason"] = "no_ok_first_token_sample"
    v["weight_bytes"], v["weight_bytes_source"] = wb, wsrc
    v["disk_weight_bytes"] = disk_wb
    v["overhead_bytes"] = (loaded - wb) if loaded is not None and wb is not None else None
    scratch = {}
    for lv, c in sorted(lc.items()):
        p, st = _value(c, "mem_peak"), _value(c, "mem_steady")
        if p is not None and st is not None:
            scratch[str(lv)] = p - st
    v["prefill_scratch_bytes"] = scratch
    v["prefill_step_tokens"] = (doc.get("runtime") or {}).get("prefill_step_tokens")
    ok_levels = sorted(lv for lv, c in lc.items() if c.out.get("status") in OK)
    v["max_level_ok"] = max(ok_levels) if ok_levels else None
    pts = [(lv, _value(lc[lv], "mem_steady")) for lv in ok_levels if _value(lc[lv], "mem_steady") is not None]
    if len(pts) >= 2:
        lf = linear_fit([p[0] for p in pts], [p[1] for p in pts])
        v["kv_MiB_per_1k_fit"] = lf.slope * 1024 / C.MIB
        v["kv_fit"] = {"slope_bytes_per_token": lf.slope, "intercept_bytes": lf.intercept, "r2": lf.r2,
                       "levels": [p[0] for p in pts]}
    else:
        v["kv_MiB_per_1k_fit"], v["kv_fit_reason"] = None, "fewer_than_two_ok_levels"
    if m.get("kv_config"):
        kvb, kvsrc = _kv_dtype_bytes(doc, m)
        try:
            det = kv_formula_detail(m["kv_config"], kvb)
            det["kv_dtype_bytes_source"] = kvsrc
            v["kv_MiB_per_1k_formula"], v["kv_formula_detail"] = det["MiB_per_1k"], det
        except (KeyError, ValueError, TypeError) as e:
            v["kv_MiB_per_1k_formula"], v["kv_formula_reason"] = None, f"bad_kv_config: {e}"[:120]
    else:
        v["kv_MiB_per_1k_formula"], v["kv_formula_reason"] = None, "no_kv_config"
    fit_, form = v.get("kv_MiB_per_1k_fit"), v.get("kv_MiB_per_1k_formula")
    v["kv_formula_mismatch"] = (abs(fit_ - form) / form > C.KV_FORMULA_FLAG) if fit_ is not None and form else None
    # --- bandwidth (§8)
    active = _int(m.get("active_weight_bytes"))
    bw, per_gb = {}, {}
    peak_bw = (doc.get("device") or {}).get("peak_bandwidth_GBps")
    for lv in ok_levels:
        d = _value(lc[lv], "decode_tps")
        st = _value(lc[lv], "mem_steady")
        if d is not None and active:
            bw[str(lv)] = active * d / 1e9
        if d is not None and st:
            per_gb[str(lv)] = d / (st / 1e9)
    v["achieved_bw_GBps"] = bw if active else None
    if not active:
        v["achieved_bw_reason"] = "no_active_weight_bytes"
    elif peak_bw:
        v["achieved_bw_pct_of_peak"] = {k: 100 * x / peak_bw for k, x in bw.items()}
    if active and key[1] == "as_shipped" and (m.get("speculation") not in (None, "none")):
        v["achieved_bw_note"] = "as_shipped decode counts verified tokens; per-weight-read bandwidth is lower"
    v["tok_s_per_resident_GB"] = per_gb
    # --- 8 GB (informational, §5.4)
    if not is_iphone:
        v["fits_8gb_mac_estimate"] = F.estimate_8gb_mac(disk_wb)
    # --- agent replay headline (§6.3)
    ar = [c for c in cells if (c.model_id, c.mode, c.entry_id) == key and c.test == "agent_replay"]
    if ar:
        pc = (doc.get("runtime") or {}).get("prefix_cache")
        groups: dict = defaultdict(list)
        for c in ar:
            rc = dict(c.extras).get("replay_cache") or ("defeated" if pc == "none" else None)
            groups[rc].extend(v_ for _, v_ in c.metric_values("ttft_s"))
        v["agent_replay"] = {"ttft_cached_s": median(groups.get("on", [])),
                             "ttft_uncached_s": median(groups.get("defeated", []))}
    return v


def _summary_index(cells: Sequence[_Cell], key: tuple, field_: str) -> dict:
    per: dict = defaultdict(list)
    for c in cells:
        if (c.model_id, c.mode, c.entry_id) != key or c.test not in ("level", "energy_pair") or c.extras:
            continue
        if c.level not in INDEX_LEVELS:
            continue
        r = c.out.get(field_)
        if not r:
            continue
        for metric, row in r.items():
            if row.get("ratio") is not None and row["ratio"] > 0:
                per[metric].append((c.level, row["ratio"]))
    out = {}
    for metric, rows in per.items():
        rows.sort()
        out[metric] = {"geomean": geometric_mean([x for _, x in rows]), "n_levels": len(rows),
                       "levels": [lv for lv, _ in rows], "better": BETTER.get(metric, "none")}
    return out


_RANK = {"fits": 3, "fits_small_context": 2, "does_not_fit": 1, "not_measured": 0}


def _fits_where_baseline_does_not(cells: Sequence[_Cell], key: tuple, v: Mapping, base_v: Mapping | None,
                                  base_id: str) -> dict:
    levels = []
    for c in cells:
        if (c.model_id, c.mode, c.entry_id) != key or c.test != "level" or c.extras:
            continue
        if c.out.get("status") not in OK:
            continue
        reason = c.out.get("ratio_reason")
        if reason == "baseline_not_ok":
            levels.append(c.level)
    out = {"levels": sorted(levels), "baseline_model_id": base_id}
    if base_v is not None:
        for f in ("fits_16gb_mac", "fits_iphone_air"):
            a, b = v.get(f), base_v.get(f)
            if a in _RANK and b in _RANK and b != "not_measured" and a != "not_measured":
                out[f] = _RANK[a] > _RANK[b]
    out["any"] = bool(out["levels"]) or any(out.get(f) for f in ("fits_16gb_mac", "fits_iphone_air"))
    return out


# --- reference pair (§1.5) ----------------------------------------------------------

REF_LIMITS = {"decode_tps": (0.97, 1.03), "prefill_tps": (0.97, 1.03), "ttft_s": (0.97, 1.03),
              "mem_peak": (0.98, 1.02), "kv_slope": (0.98, 1.02),
              "t_load_warm_s": (0.95, 1.05), "t_load_cold_s": (0.90, 1.10)}
ENERGY_HALF_WIDTH = 0.05
MEM_LOADED_TOLERANCE = 0.01
AS_SHIPPED_TOLERANCE = 0.05


def _check(ratio: float, lo: float, hi: float) -> dict:
    return {"ratio": ratio, "limit": {"min": lo, "max": hi}, "pass": lo - EPS <= ratio <= hi + EPS}


def _reference_pair(cells: Sequence[_Cell], verdicts: Mapping, base_id: str, ref_id: str, is_iphone: bool,
                    kind: str) -> dict:
    out: dict[str, Any] = {"baseline_model_id": base_id, "reference_model_id": ref_id, "mode": "plain"}
    if kind == "cloud_reference":
        out.update({"status": "not_applicable", "checks": {}, "reference_pair_mismatch": None,
                    "rerun_session": False, "failed": []})
        return out
    ref_cells = [c for c in cells if c.model_id == ref_id and c.mode == "plain"]
    base_cells = [c for c in cells if c.model_id == base_id and c.mode == "plain"]
    if not ref_cells or not base_cells:
        out.update({"status": "not_run", "checks": {}, "reference_pair_mismatch": None, "rerun_session": False,
                    "failed": []})
        return out
    entries = sorted({c.entry_id for c in ref_cells})
    multi = len(entries) > 1
    checks: dict[str, dict] = {}
    per_cell: dict[str, dict] = defaultdict(dict)

    def add(name: str, metric: str, row: dict, cell_ids: Sequence[str] = ()):
        row["metric"] = metric
        checks[name] = row
        for cid in cell_ids:
            per_cell[cid][name] = row

    for c in ref_cells:
        r = c.out.get("ratio_to_baseline") or {}
        sfx = f"[{c.entry_id}]" if multi else ""
        den_cid = None
        if c.out.get("baseline_entry_id"):
            den_cid = "|".join(str(p) for p in [base_id, c.mode, c.out["baseline_entry_id"], c.test,
                                                 _fmt_level(c.level)] + [f"{k}={v}" for k, v in c.extras])
        ids = [c.out["cell_id"]] + ([den_cid] if den_cid else [])
        if c.test == "level" and not c.extras:
            for metric in ("decode_tps", "prefill_tps", "ttft_s", "mem_peak"):
                ratio = (r.get(metric) or {}).get("ratio")
                if ratio is not None:
                    add(f"{metric}@{c.level}{sfx}", metric, _check(ratio, *REF_LIMITS[metric]), ids)
        elif c.test == "energy_pair":
            den = next((d for d in base_cells if d.out["cell_id"] == den_cid), None)
            for metric in ENERGY:
                ratio = (r.get(metric) or {}).get("ratio")
                if ratio is None:
                    continue
                eb = [x for x in (c.out.get("error_bound_pct"), den.out.get("error_bound_pct") if den else None)
                      if x is not None]
                comb = math.sqrt(sum(x * x for x in eb)) / 100 if eb else 0.0
                hw = max(ENERGY_HALF_WIDTH, comb)
                row = _check(ratio, 1 - hw, 1 + hw)
                row["combined_error_bound_pct"] = comb * 100 if eb else None
                add(f"{metric}@{c.level}{sfx}", metric, row, ids)
        elif c.test in LOAD_TESTS:
            lim = REF_LIMITS["t_load_warm_s" if c.test == "load_warm" else "t_load_cold_s"]
            for metric in ("t_load_warm_s" if c.test == "load_warm" else "t_load_cold_s", "t_first_token_possible_s"):
                ratio = (r.get(metric) or {}).get("ratio")
                if ratio is not None:
                    add(f"{metric}@{c.test}{sfx}", metric, _check(ratio, *lim), ids)
    # model-level: mem_loaded, KV slope, highest level that fits (iPhone)
    for e in entries:
        rv = verdicts.get((ref_id, "plain", e))
        sample = next((c for c in ref_cells if c.entry_id == e and c.out.get("baseline_entry_id")), None)
        be = sample.out["baseline_entry_id"] if sample else None
        bv = verdicts.get((base_id, "plain", be)) if be else None
        if bv is None:
            bv = next((vv for k, vv in verdicts.items() if k[0] == base_id and k[1] == "plain"), None)
        if rv is None or bv is None:
            continue
        sfx = f"[{e}]" if multi else ""
        a, b = rv.get("mem_loaded_bytes"), bv.get("mem_loaded_bytes")
        wa, wb_ = rv.get("disk_weight_bytes"), bv.get("disk_weight_bytes")
        if a and b and wa and wb_:
            ratio, wr = a / b, wa / wb_
            dev = abs(ratio - wr) / wr
            add(f"mem_loaded{sfx}", "mem_loaded",
                {"ratio": ratio, "limit": {"expected": wr, "tolerance": MEM_LOADED_TOLERANCE},
                 "deviation": dev, "pass": dev <= MEM_LOADED_TOLERANCE + EPS})
        a, b = rv.get("kv_MiB_per_1k_fit"), bv.get("kv_MiB_per_1k_fit")
        if a is not None and b:
            add(f"kv_slope{sfx}", "kv_slope", _check(a / b, *REF_LIMITS["kv_slope"]))
        if is_iphone:
            a = (rv.get("fits_iphone_air_detail") or {}).get("max_level_fits")
            b = (bv.get("fits_iphone_air_detail") or {}).get("max_level_fits")
            if a is not None or b is not None:
                add(f"max_level_fits{sfx}", "max_level_fits",
                    {"ratio": None, "reference": a, "baseline": b, "limit": {"same_level": True}, "pass": a == b})
    failed = sorted(k for k, row in checks.items() if not row["pass"])
    failed_metrics = sorted({checks[k]["metric"] for k in failed})
    out.update({"status": "checked" if checks else "no_comparable_cells", "checks": checks,
                "reference_pair_mismatch": bool(failed) if checks else None, "failed": failed,
                "failed_metrics": failed_metrics, "rerun_session": len(failed_metrics) >= 2})
    out["as_shipped_decode"] = _as_shipped_prediction(cells, base_id, ref_id)
    out["_per_cell"] = per_cell
    return out


def _spec_totals(c: _Cell) -> dict | None:
    dec = acc = drafted = 0
    for s in c.counted:
        n, a, d = _int(s.get("output_tokens")), _int(s.get("accepted_tokens")), _int(s.get("drafted_tokens"))
        if n is None or a is None or d is None or n < 2:
            continue
        dec += n - 1
        acc += a
        drafted += d
    cycles = dec - acc
    if dec <= 0 or cycles <= 0 or drafted <= 0:
        return None
    return {"drafts_per_cycle": drafted / cycles, "acceptance": acc / drafted, "tokens_per_cycle": dec / cycles}


def _as_shipped_prediction(cells: Sequence[_Cell], base_id: str, ref_id: str) -> list:
    rows = []
    plain = {(c.model_id, c.level): c for c in cells if c.mode == "plain" and c.test == "level" and not c.extras}
    for c in cells:
        if c.model_id != ref_id or c.mode != "as_shipped" or c.test != "level" or c.extras:
            continue
        measured = ((c.out.get("ratio_to_baseline") or {}).get("decode_tps") or {}).get("ratio")
        den_entry = c.out.get("baseline_entry_id")
        den = next((d for d in cells if d.model_id == base_id and d.mode == "as_shipped" and d.test == "level"
                    and d.level == c.level and not d.extras and d.entry_id == den_entry), None)
        if den is None or measured is None:
            continue
        sr, sb = _spec_totals(c), _spec_totals(den)
        if not sr or not sb:
            continue
        pr = plain.get((ref_id, c.level))
        plain_ratio = ((pr.out.get("ratio_to_baseline") or {}).get("decode_tps") or {}).get("ratio") if pr else None
        predicted = (plain_ratio if plain_ratio is not None else 1.0) * sr["tokens_per_cycle"] / sb["tokens_per_cycle"]
        gap = abs(measured / predicted - 1)
        rows.append({"level": c.level, "measured_ratio": measured, "predicted_ratio": predicted,
                     "plain_ratio": plain_ratio, "reference": sr, "baseline": sb, "gap": gap,
                     "flag": gap > AS_SHIPPED_TOLERANCE + EPS})
    return rows


# --- cloud (§1.7, §3.11) -------------------------------------------------------------

def _fill_cloud_cell(c: _Cell) -> None:
    out = c.out
    out["label"] = "includes network and provider queue"
    dates = sorted({str(s["t_wall_utc"])[:10] for s in c.samples if s.get("t_wall_utc")})
    out["run_dates"] = dates
    out["on_device_reason"] = "not_applicable_cloud"
    out["n_provider_fallback"] = sum(1 for s in c.samples if s.get("status") == "provider_fallback")
    if c.test == "cloud_task":
        tasks: dict = defaultdict(list)
        for s in c.samples:
            if _is_void(_effective_status(s, "cloud_reference")):
                continue
            tasks[s.get("task_id")].append(s)
        attempted, completed = [], []
        for tid, ss in tasks.items():
            costs = [s.get("cost_usd") for s in ss if s.get("cost_usd") is not None]
            if not costs:
                continue
            total = sum(costs)
            attempted.append(total)
            explicit = [s.get("task_success", s.get("task_pass")) for s in ss
                        if s.get("task_success", s.get("task_pass")) is not None]
            success = all(explicit) if explicit else all((s.get("status") or "ok") in OK for s in ss)
            if success:
                completed.append(total)
        out["cost_usd_per_completed_task"] = median(completed)
        out["cost_usd_per_attempted_task"] = median(attempted)
        out["n_tasks_attempted"], out["n_tasks_completed"] = len(attempted), len(completed)


def _cloud_verdict(m: Mapping, cells: Sequence[_Cell]) -> dict:
    key = (m["id"], m.get("mode") or "as_shipped", _entry_id(m))
    v = {"model_id": m["id"], "entry_id": _entry_id(m), "mode": key[1], "role": "cloud_reference",
         "headline_entry": bool(m.get("headline_entry")),
         "fits_16gb_mac": "not_applicable", "fits_iphone_air": "not_applicable",
         "fits_16gb_mac_under_load": "not_applicable", "ranked": False, "reference": "cloud",
         "ratio_reason": "cloud_not_comparable", "on_device_reason": "not_applicable_cloud"}
    tasks = [c for c in cells if (c.model_id, c.mode, c.entry_id) == key and c.test == "cloud_task"]
    if tasks:
        v["cost_usd_per_completed_task"] = tasks[0].out.get("cost_usd_per_completed_task")
    dates = sorted({d for c in cells if (c.model_id, c.mode, c.entry_id) == key for d in c.out.get("run_dates", [])})
    v["run_dates"] = dates
    return v


# --- the entry point ----------------------------------------------------------------

def _public(c: _Cell) -> dict:
    out = c.out
    for name in list(out):
        if name.startswith("_"):
            del out[name]
    return out


def aggregate(doc: Mapping, *, run_dir: str | Path | None = None, energy_fn: EnergyFn | None = None,
              extra_derivers: Sequence[Callable] = (), trace_reader: Callable | None = None) -> dict:
    """Return a NEW run document with ``cells[]``, ``verdicts[]`` and the run-level checks filled.

    ``energy_fn(samples, trace, window, level_tokens=L)`` defaults to
    :func:`edat.perf.energy.pair_cell`; ``trace_reader(path)`` defaults to
    :func:`edat.perf.power.read_trace`. Both are imported only when the run has energy windows.
    Each ``extra_deriver(doc, verdicts_by_entry)`` is called last; ``verdicts_by_entry`` maps
    ``(model_id, mode, entry_id)`` to that entry's verdict dict, which the deriver may extend in place.
    """
    if doc.get("schema") != C.PERF_SCHEMA:
        raise ValueError(f"schema {doc.get('schema')!r} is not {C.PERF_SCHEMA}")
    out = copy.deepcopy(dict(doc))
    kind = out.get("kind") or "local"
    models = _Models(out.get("models") or ())
    device = out.get("device") or {}
    is_iphone = str(device.get("class") or "").startswith("iphone") or \
        (out.get("runner") or {}).get("name") == "iphone-mlx-swift"
    cells, spans = _build_cells(out, models, kind)
    for c in cells:
        _fill_cell(c, kind)
        if kind == "cloud_reference":
            _fill_cloud_cell(c)
    run_dir = Path(run_dir) if run_dir is not None else None
    if kind != "cloud_reference":
        _fill_energy(out, cells, models, run_dir, energy_fn, trace_reader)
    base_id, ref_id, notes = _denominators(out, models, is_iphone)
    drift_cells = _fill_ratios(cells, spans, base_id, ref_id, notes, kind, models)

    sup = _superseded(list(out.get("samples") or ()))
    aux = _aux_allowance(out, kind)
    verdicts: list[dict] = []
    vmap: dict = {}
    for m in models.entries:
        v = _cloud_verdict(m, cells) if kind == "cloud_reference" else \
            _local_verdict(out, m, cells, models, kind, is_iphone, sup, aux)
        verdicts.append(v)
        vmap[(v["model_id"], v["mode"], v["entry_id"])] = v
    if kind != "cloud_reference":
        for v in verdicts:
            key = (v["model_id"], v["mode"], v["entry_id"])
            v["summary_index"] = {"vs_baseline": _summary_index(cells, key, "ratio_to_baseline"),
                                  "vs_reference": _summary_index(cells, key, "ratio_to_reference"),
                                  "levels": [INDEX_LEVELS[0], INDEX_LEVELS[-1]]}
            base_v = next((vv for k, vv in vmap.items() if k[0] == base_id and k[1] == v["mode"]), None)
            v["fits_where_baseline_does_not"] = None if v["model_id"] == base_id else \
                _fits_where_baseline_does_not(cells, key, v, base_v, base_id)
            if is_iphone and base_id != C.BASELINE_ID:
                v["baseline_note"] = "iphone_fallback_variant"
    # per-cell fits (§3.9, §5.4)
    for c in cells:
        if c.test != "level" or c.extras or kind == "cloud_reference":
            continue
        v = vmap.get((c.model_id, c.mode, c.entry_id)) or {}
        detail = v.get("fits_16gb_mac_detail") or {}
        idet = v.get("fits_iphone_air_detail") or {}
        growths = [m.get("_swap_growth") for _, m in c.values if m.get("_swap_growth") is not None]
        fits, est = F.cell_fits(device_class=device.get("class") or ("iphone-ref" if is_iphone else "mac-ref"),
                                status=c.out.get("status"), swap_growth_bytes=max(growths) if growths else None,
                                level=c.level, peak_bytes=_value(c, "mem_peak"),
                                fit16_level_fits=detail.get("level_fits") if v.get("fits_16gb_mac") != "not_measured" else None,
                                iphone_threshold_bytes=idet.get("threshold_bytes"),
                                weight_bytes=v.get("disk_weight_bytes"))
        c.out["fits"] = fits
        if est:
            c.out["fits_estimate"] = est
        c.out["mem_loaded_bytes"] = v.get("mem_loaded_bytes")
        for k in ("kv_MiB_per_1k_fit", "kv_MiB_per_1k_formula"):
            c.out[k] = v.get(k)
    rpc = _reference_pair(cells, vmap, base_id, ref_id, is_iphone, kind)
    per_cell = rpc.pop("_per_cell", {})
    mismatch = rpc.get("reference_pair_mismatch")
    for c in cells:
        o = c.out
        o["reference_pair_mismatch"] = mismatch
        if o["cell_id"] in per_cell:
            o["reference_pair_check"] = per_cell[o["cell_id"]]
        greyed = []
        if o.get("baseline_drift"):
            greyed.append("baseline_drift")
        if mismatch and c.role not in ("baseline", "reference"):
            greyed.append("reference_pair_mismatch")
        o["ratios_greyed"] = bool(greyed)
        if greyed:
            o["greyed_reasons"] = greyed

    out["cells"] = [_public(c) for c in cells]
    out["verdicts"] = verdicts
    out["reference_pair_check"] = rpc
    out["baseline_drift"] = bool(drift_cells)
    out["baseline_drift_cells"] = drift_cells
    out["aggregation"] = {"aggregator": "edat.perf.aggregate", "version": AGGREGATOR_VERSION,
                          "stats": STATS_METADATA, "baseline_model_id": base_id, "reference_model_id": ref_id,
                          "denominator_notes": notes, "choices": list(CHOICES)}
    if extra_derivers:
        # Derivers read side files (power traces, battery and sim-day records) next to the document.
        out["_run_dir"] = str(run_dir) if run_dir is not None else None
        try:
            for derive in extra_derivers:
                derive(out, vmap)
        finally:
            out.pop("_run_dir", None)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.perf.aggregate",
                                 description="Fill cells[] and verdicts[] of an edat.perf/1.0 run document.")
    ap.add_argument("run", type=Path, help="run-<run_id>.json")
    ap.add_argument("--out", type=Path, help="where to write the aggregated document (default: stdout)")
    args = ap.parse_args(argv)
    try:
        doc = json.loads(args.run.read_text())
        from .derive import EXTRA_DERIVERS
        result = aggregate(doc, run_dir=args.run.parent, extra_derivers=EXTRA_DERIVERS)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    text = json.dumps(result, indent=2, sort_keys=False, allow_nan=False)
    if args.out:
        args.out.write_text(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
