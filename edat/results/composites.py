"""The output-quality headlines (spec 03 §13.2): Text understanding, Readability, Writing.

Each headline is a function of per-case rows (each case's trials averaged
first), so its interval is a bootstrap over cases (cases resampled, the
headline recomputed) and a difference between two entries is a paired
bootstrap over the cases both answered. Cases that were `beyond_window` for
either entry are dropped from both sides (03 §13.2).

Inputs, as the quality harness writes them (`tier: quality`, `facets.family`):
- `summary`, `point`: core_recall, atoms, fabricated_atoms, hygiene, trap_count, trap_failures;
- `extraction`, `schema`: extraction_field_acc, schema_valid;
- `numeric`: numeric_ok, atoms, fabricated_atoms;
- `writing` (category W1..W7): point_recall, invented_atoms, atoms, invented_rate, forbidden_rate,
  constraint_ok, fact_keep_rate (W3), fix_rate, flag_rate, overedit (W4), grammar_errors_per_100w.

Rates are fractions 0-1 except `hygiene`, which 03 defines as 0-100 points.
Headline values here are fractions 0-1 (the website shows them as 0-100).

Judge preferences are entry-level, not per case. They arrive as one record per
entry with `case_id` `entry/readability_judge` or `entry/writing_judge` and
metrics `pref_style_ctrl` (0-100, win rate against the tuned baseline, which
is 50 by definition), `judge_validated` (bool) and, for writing,
`grammar_reference_per_100w` (the human reference drafts' grammar rate).
Without a validated judge, Readability is hygiene alone ("structure only") and
Writing renormalises over its checks ("checks only"), as 03 says.
"""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Callable, Iterable

ENTRY_PREFIX = "entry/"
TU_WEIGHTS = {"core_recall": 0.30, "extraction_numeric": 0.20, "faithfulness": 0.25,
              "trap_resistance": 0.15, "schema_valid": 0.10}
READ_WEIGHTS = {"hygiene": 0.35, "pref": 0.65}
WRITING_WEIGHTS = {"points": 0.25, "faithfulness": 0.20, "constraints": 0.15, "editing": 0.10, "pref": 0.30}
QUALITY_HEADLINES = ("text_understanding", "readability", "writing")
_SUMS = ("atoms", "fabricated_atoms", "trap_count", "trap_failures", "invented_atoms")


def is_entry_record(rec: dict) -> bool:
    return rec["case_id"].startswith(ENTRY_PREFIX)


def entry_values(records: Iterable[dict]) -> dict[str, dict]:
    """case_id -> metrics of the entry-level records (judge preferences)."""
    return {r["case_id"]: dict(r["metrics"]) for r in records if is_entry_record(r) and r["status"] == "ok"}


def case_rows(records: Iterable[dict]) -> tuple[dict[str, dict], set[str]]:
    """Per-case means of every numeric or boolean metric over the case's graded
    trials, plus the set of cases that were beyond the window."""
    by: dict[str, list[dict]] = defaultdict(list)
    dnf: set[str] = set()
    for r in records:
        if is_entry_record(r):
            continue
        if r["status"] == "beyond_window":
            dnf.add(r["case_id"])
        elif r["status"] == "ok":
            by[r["case_id"]].append(r)
    rows = {}
    for cid, rs in by.items():
        row: dict = {"family": rs[0].get("facets", {}).get("family"),
                     "category": rs[0].get("facets", {}).get("category"), "tier": rs[0]["tier"]}
        names = {k for r in rs for k, v in r["metrics"].items() if isinstance(v, (int, float))}
        for k in names:
            vals = [float(r["metrics"][k]) for r in rs if isinstance(r["metrics"].get(k), (int, float))]
            if vals:
                row[k] = sum(vals) / len(vals)
        rows[cid] = row
    return rows, dnf


def _mean(vals) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _weighted(parts: dict[str, float | None], weights: dict[str, float]) -> tuple[float | None, list[str]]:
    have = {k: v for k, v in parts.items() if v is not None and weights.get(k)}
    if not have:
        return None, sorted(parts)
    w = sum(weights[k] for k in have)
    return sum(weights[k] * v for k, v in have.items()) / w, sorted(k for k in parts if k not in have)


def _fam(rows: list[dict], *families: str) -> list[dict]:
    return [r for r in rows if r.get("family") in families]


# --- the three headlines ---------------------------------------------------

def tu_components(rows: list[dict]) -> dict:
    sp = _fam(rows, "summary", "point")
    ex = _fam(rows, "extraction", "schema")
    nu = _fam(rows, "numeric")
    fa = _fam(rows, "summary", "point", "numeric")
    efa = _mean(r.get("extraction_field_acc") for r in ex)
    num = _mean(r.get("numeric_ok") for r in nu)
    atoms = sum(r.get("atoms") or 0 for r in fa)
    fab = sum(r.get("fabricated_atoms") or 0 for r in fa)
    tr = _fam(rows, "summary", "point", "numeric", "extraction", "schema", "noisy")
    traps = sum(r.get("trap_count") or 0 for r in tr)
    tfail = sum(r.get("trap_failures") or 0 for r in tr)
    fdr = fab / atoms if atoms else None
    return {"core_recall": _mean(r.get("core_recall") for r in sp),
            "extraction_numeric": _mean([efa, num]),
            "faithfulness": None if fdr is None else max(0.0, 1 - 2 * fdr),
            "trap_resistance": None if not traps else 1 - tfail / traps,
            "schema_valid": _mean(r.get("schema_valid") for r in ex),
            "fabricated_detail_rate": fdr, "trap_failure_rate": None if not traps else tfail / traps}


def text_understanding(rows: list[dict], entry: dict) -> dict | None:
    c = tu_components(rows)
    value, missing = _weighted({k: c[k] for k in TU_WEIGHTS}, TU_WEIGHTS)
    if value is None:
        return None
    return {"value": value, "components": c, "label": "partial" if missing else "full", "missing": missing}


def readability(rows: list[dict], entry: dict) -> dict | None:
    hyg = _mean(r.get("hygiene") for r in rows if r.get("family") != "writing")
    if hyg is None:
        return None
    h = hyg / 100.0
    judge = entry.get(ENTRY_PREFIX + "readability_judge") or {}
    pref = judge.get("pref_style_ctrl")
    if judge.get("judge_validated") is True and isinstance(pref, (int, float)):
        return {"value": READ_WEIGHTS["hygiene"] * h + READ_WEIGHTS["pref"] * pref / 100.0,
                "components": {"hygiene": h, "pref_judge_style_ctrl": pref / 100.0}, "label": "judge"}
    return {"value": h, "components": {"hygiene": h}, "label": "structure only"}


def writing_components(rows: list[dict], entry: dict) -> dict:
    w = _fam(rows, "writing") or [r for r in rows if r.get("tier") == "writing"]
    if not w:
        return {}
    w3 = [r for r in w if r.get("category") == "W3"]
    w4 = [r for r in w if r.get("category") == "W4"]
    atoms = sum(r.get("atoms") or 0 for r in w)
    inv = sum(r.get("invented_atoms") or 0 for r in w)
    invented = inv / atoms if atoms else _mean(r.get("invented_rate") for r in w)
    forbidden = _mean(r.get("forbidden_rate") for r in w)
    keep = _mean(r.get("fact_keep_rate") for r in w3)
    fact_change = None if keep is None else 1 - keep
    if invented is None and forbidden is None and fact_change is None:
        faith = None
    else:
        faith = max(0.0, 1 - 2 * ((invented or 0) + (forbidden or 0) + (fact_change or 0)))
    constraint = _mean(r.get("constraint_ok") for r in w)
    judge = entry.get(ENTRY_PREFIX + "writing_judge") or {}
    grammar = _mean(r.get("grammar_errors_per_100w") for r in w)
    ref = judge.get("grammar_reference_per_100w")
    if constraint is not None and grammar is not None and isinstance(ref, (int, float)):
        constraint = max(0.0, constraint - 0.01 * max(0.0, grammar - ref))
    editing = None
    if w4:
        over = _mean(r.get("overedit") for r in w4)
        editing = _mean([_mean(r.get("fix_rate") for r in w4), _mean(r.get("flag_rate") for r in w4),
                         None if over is None else max(0.0, 1 - 50 * over)])
    pref = judge.get("pref_style_ctrl")
    return {"points": _mean(r.get("point_recall") for r in w), "faithfulness": faith,
            "constraints": constraint, "editing": editing,
            "pref": pref / 100.0 if judge.get("judge_validated") is True and isinstance(pref, (int, float)) else None,
            "invented_rate": invented, "forbidden_rate": forbidden, "fact_change_rate": fact_change,
            "grammar_errors_per_100w": grammar, "grammar_penalty_applied": isinstance(ref, (int, float))}


def writing(rows: list[dict], entry: dict) -> dict | None:
    c = writing_components(rows, entry)
    if not c:
        return None
    value, missing = _weighted({k: c[k] for k in WRITING_WEIGHTS}, WRITING_WEIGHTS)
    if value is None:
        return None
    return {"value": value, "components": c, "label": "checks only" if c["pref"] is None else "judge",
            "missing": missing}


HEADLINES: dict[str, Callable[[list[dict], dict], dict | None]] = {
    "text_understanding": text_understanding, "readability": readability, "writing": writing}


# --- intervals and differences ---------------------------------------------

def _boot(case_ids: list[str], fn: Callable[[list[str]], float | None], resamples: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    out = []
    for _ in range(resamples):
        v = fn(rng.choices(case_ids, k=len(case_ids)))
        if v is not None:
            out.append(v)
    out.sort()
    return out


def _pct(sorted_vals: list[float], q: float) -> float:
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def headlines(records: list[dict], *, resamples: int, seed: int) -> dict[str, dict]:
    """Every quality headline that has data, with a case-bootstrap 95 % interval."""
    rows, dnf = case_rows(records)
    entry = entry_values(records)
    ids = sorted(c for c in rows if c not in dnf)
    out = {}
    for name, fn in HEADLINES.items():
        res = fn([rows[c] for c in ids], entry)
        if res is None:
            continue

        def f(sample, fn=fn):
            r = fn([rows[c] for c in sample], entry)
            return None if r is None else r["value"]

        boot = _boot(ids, f, resamples, seed) if len(ids) > 1 else []
        res.update(metric=name, interval=[_pct(boot, 0.025), _pct(boot, 0.975)] if boot else [res["value"]] * 2,
                   interval_kind="bootstrap_cases", n_cases=len(ids), does_not_fit=len(dnf),
                   does_not_fit_rate=len(dnf) / (len(ids) + len(dnf)) if ids or dnf else 0.0)
        if res.get("label") == "judge":
            res["note"] = "judge preference held fixed in the interval"
        out[name] = res
    return out


def paired_headlines(a_records: list[dict], b_records: list[dict], *, resamples: int, seed: int) -> dict[str, dict]:
    """Δ = headline(a) − headline(b) on the cases both answered, beyond-window cases dropped from both."""
    ra, da = case_rows(a_records)
    rb, db = case_rows(b_records)
    ea, eb = entry_values(a_records), entry_values(b_records)
    common = sorted((set(ra) & set(rb)) - da - db)
    out = {}
    if not common:
        return out
    for name, fn in HEADLINES.items():
        va, vb = fn([ra[c] for c in common], ea), fn([rb[c] for c in common], eb)
        if va is None or vb is None:
            continue

        def f(sample, fn=fn):
            x, y = fn([ra[c] for c in sample], ea), fn([rb[c] for c in sample], eb)
            return None if x is None or y is None else x["value"] - y["value"]

        boot = _boot(common, f, resamples, seed)
        delta = va["value"] - vb["value"]
        interval = [_pct(boot, 0.025), _pct(boot, 0.975)] if boot else [delta, delta]
        tag = "better" if interval[0] > 0 else "worse" if interval[1] < 0 else "no_clear_difference"
        out[name] = {"delta": delta, "interval": interval, "tag": tag, "n_cases": len(common),
                     "labels": [va.get("label"), vb.get("label")]}
        if va.get("label") != vb.get("label"):
            out[name]["warning"] = "the two sides are labelled differently (judge vs checks or structure only)"
    return out
