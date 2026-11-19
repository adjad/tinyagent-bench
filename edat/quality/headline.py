"""Headline scores and differences from both reference models (spec 03 s13.2).

Computed from `edat.result/1.0` records written by edat.quality.runner.

Text understanding (0-100) =
    0.30 core_recall (summaries + point questions)
  + 0.20 mean(extraction_field_acc, numeric_acc)
  + 0.25 faithfulness = max(0, 100 - 2 x fabricated_detail_rate)
  + 0.15 trap resistance = 100 - trap_failure_rate
  + 0.10 schema_valid_rate (free decoding)

Readability (0-100) = 0.35 hygiene + 0.65 pref_judge_style_ctrl (marked †) when
the judge passed validation; otherwise hygiene alone, labelled "structure only".

Differences: for each candidate, Δ_tuned against Gemma-Wisp (think-off) and
Δ_base against base Gemma 4 E4B, by a paired bootstrap over the cases both
entries answered (cases resampled; each case's trials averaged first).
`beyond_window` (does_not_fit) cases are dropped from both sides. An interval
that includes 0 reads "approx_baseline".
"""
from __future__ import annotations

import random
import statistics
from collections import defaultdict
from typing import Iterable

HEADLINE_VERSION = "edat-quality-headline/1"
BASELINE_ID = "Gemma-Wisp"
BASE_ID = "gemma-4-E4B-it-oQ4e-mtp"
TU_WEIGHTS = {"core_recall": 0.30, "extraction_numeric": 0.20, "faithfulness": 0.25, "trap_resistance": 0.15,
              "schema_valid": 0.10}
READ_WEIGHTS = {"hygiene": 0.35, "pref": 0.65}


def entry_key(rec: dict) -> tuple:
    m = rec["model"]
    return (rec["kind"], m["id"], rec["settings"]["thinking"], rec["settings"].get("sampling_source"))


def group_by_entry(records: Iterable[dict]) -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("tier") == "quality":
            out[entry_key(r)].append(r)
    return out


def _per_case(recs: list[dict]) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by[r["case_id"]].append(r)
    return by


def case_table(recs: list[dict]) -> tuple[dict[str, dict], set[str]]:
    """case_id -> averaged component inputs; plus the set of beyond_window cases."""
    table: dict[str, dict] = {}
    dnf: set[str] = set()
    for cid, rs in _per_case(recs).items():
        ok = [r for r in rs if r["status"] == "ok"]
        if any(r["status"] == "beyond_window" for r in rs):
            dnf.add(cid)
        if not ok:
            continue
        fam = ok[0]["facets"].get("family")
        row: dict = {"family": fam}

        def mean(key):
            vals = [r["metrics"].get(key) for r in ok if r["metrics"].get(key) is not None]
            vals = [float(v) for v in vals]
            return sum(vals) / len(vals) if vals else None

        def total(key):
            return sum(float(r["metrics"].get(key) or 0) for r in ok) / len(ok)

        if fam in ("summary", "point"):
            row["core_recall"] = mean("core_recall")
        if fam in ("extraction", "schema"):
            row["extraction_field_acc"] = mean("extraction_field_acc")
            row["schema_valid"] = mean("schema_valid")
        if fam == "numeric":
            row["numeric_ok"] = mean("numeric_ok")
        if fam in ("summary", "point", "numeric"):
            row["atoms"] = total("atoms")
            row["fabricated_atoms"] = total("fabricated_atoms")
            row["hygiene"] = mean("hygiene")
        row["trap_count"] = total("trap_count")
        row["trap_failures"] = total("trap_failures")
        row["injection_planted"] = total("injection_planted")
        row["injection_followed"] = total("injection_followed")
        row["ref_tokens"] = mean("ref_tokens")
        row["verbosity_ratio"] = mean("verbosity_ratio")
        table[cid] = row
    return table, dnf


def _mean(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def components(rows: list[dict]) -> dict:
    cr = _mean([r.get("core_recall") for r in rows if "core_recall" in r])
    efa = _mean([r.get("extraction_field_acc") for r in rows if "extraction_field_acc" in r])
    num = _mean([r.get("numeric_ok") for r in rows if "numeric_ok" in r])
    sv = _mean([r.get("schema_valid") for r in rows if "schema_valid" in r])
    atoms = sum(r.get("atoms") or 0 for r in rows)
    fab = sum(r.get("fabricated_atoms") or 0 for r in rows)
    traps = sum(r.get("trap_count") or 0 for r in rows)
    tfail = sum(r.get("trap_failures") or 0 for r in rows)
    inj = sum(r.get("injection_planted") or 0 for r in rows)
    injf = sum(r.get("injection_followed") or 0 for r in rows)
    hyg = _mean([r.get("hygiene") for r in rows if "hygiene" in r])
    fdr = 100 * fab / atoms if atoms else 0.0
    tfr = 100 * tfail / traps if traps else 0.0
    en = _mean([x for x in (efa, num) if x is not None])
    return {
        "core_recall": None if cr is None else 100 * cr,
        "extraction_field_acc": None if efa is None else 100 * efa,
        "numeric_acc": None if num is None else 100 * num,
        "extraction_numeric": None if en is None else 100 * en,
        "fabricated_detail_rate": fdr, "faithfulness": max(0.0, 100 - 2 * fdr),
        "trap_failure_rate": tfr, "trap_resistance": 100 - tfr,
        "injection_followed_rate": (100 * injf / inj) if inj else None,
        "schema_valid": None if sv is None else 100 * sv,
        "hygiene": hyg,
    }


def text_understanding(c: dict) -> float | None:
    parts = {k: c.get(k) for k in TU_WEIGHTS}
    if any(v is None for v in parts.values()):
        # renormalize over the components that exist (e.g. a run with no schema cases); flagged by caller
        have = {k: v for k, v in parts.items() if v is not None}
        if not have:
            return None
        w = sum(TU_WEIGHTS[k] for k in have)
        return sum(TU_WEIGHTS[k] * v for k, v in have.items()) / w
    return sum(TU_WEIGHTS[k] * v for k, v in parts.items())


def readability(c: dict, pref_style_ctrl: float | None, judge_validated: bool) -> tuple[float | None, str]:
    h = c.get("hygiene")
    if h is None:
        return None, "no data"
    if judge_validated and pref_style_ctrl is not None:
        return READ_WEIGHTS["hygiene"] * h + READ_WEIGHTS["pref"] * pref_style_ctrl, "judge †"
    return h, "structure only"


def _paired(cand: dict[str, dict], ref: dict[str, dict], fn, *, n: int, seed: int) -> dict:
    common = sorted(set(cand) & set(ref))
    if not common:
        return {"delta": None, "lo": None, "hi": None, "n_cases": 0, "label": "no_shared_cases"}
    point = fn([cand[c] for c in common]), fn([ref[c] for c in common])
    if point[0] is None or point[1] is None:
        return {"delta": None, "lo": None, "hi": None, "n_cases": len(common), "label": "missing_component"}
    rng = random.Random(seed)
    deltas = []
    for _ in range(n):
        s = [common[rng.randrange(len(common))] for _ in common]
        a, b = fn([cand[c] for c in s]), fn([ref[c] for c in s])
        if a is not None and b is not None:
            deltas.append(a - b)
    deltas.sort()
    lo = deltas[int(0.025 * (len(deltas) - 1))] if deltas else None
    hi = deltas[int(0.975 * (len(deltas) - 1))] if deltas else None
    d = point[0] - point[1]
    label = "approx_baseline" if lo is None or (lo <= 0 <= hi) else ("better" if d > 0 else "worse")
    return {"delta": d, "lo": lo, "hi": hi, "n_cases": len(common), "label": label}


def tokens_vs(cand: dict[str, dict], ref: dict[str, dict]) -> float | None:
    ratios = [cand[c]["ref_tokens"] / ref[c]["ref_tokens"] for c in set(cand) & set(ref)
              if cand[c].get("ref_tokens") and ref[c].get("ref_tokens")]
    return statistics.median(ratios) if ratios else None


def headline_report(records: Iterable[dict], *, pref: dict[str, float] | None = None, judge_validated: bool = False,
                    baseline_id: str = BASELINE_ID, base_id: str = BASE_ID, n_boot: int = 2000,
                    seed: int = 0) -> dict:
    """Headline scores for every entry, with Δ_tuned and Δ_base where the references are present.

    `pref` maps model id -> pref_judge_style_ctrl (0-100) from a validated judge.
    The references are matched in their think-off entry (spec 03 s13.2).
    """
    groups = group_by_entry(records)
    tables = {k: case_table(v) for k, v in groups.items()}
    pref = pref or {}

    def find_ref(mid: str):
        for k in tables:
            if k[1] == mid and k[2] == "off" and k[0] == "local":
                return k
        return None

    out = {"version": HEADLINE_VERSION, "judge_validated": judge_validated, "entries": []}
    refs = {"tuned": find_ref(baseline_id), "base": find_ref(base_id)}
    for k, (table, dnf) in sorted(tables.items(), key=lambda kv: str(kv[0])):
        rows = [r for c, r in table.items() if c not in dnf]
        comp = components(rows)
        tu = text_understanding(comp)
        rd, rd_label = readability(comp, pref.get(k[1]) if k[1] != baseline_id else 50.0, judge_validated)
        e = {"kind": k[0], "model_id": k[1], "thinking": k[2], "sampling_source": k[3],
             "text_understanding": tu, "readability": rd, "readability_label": rd_label,
             "components": comp, "cases": len(rows), "does_not_fit": len(dnf),
             "does_not_fit_rate": len(dnf) / len(set(table) | dnf) if (table or dnf) else 0.0,
             "ranked": k[0] == "local", "deltas": {}}
        for name, rk in refs.items():
            if rk is None or rk == k:
                continue
            rtable, rdnf = tables[rk]
            drop = dnf | rdnf
            cand = {c: r for c, r in table.items() if c not in drop}
            ref = {c: r for c, r in rtable.items() if c not in drop}
            e["deltas"][name] = {
                "text_understanding": _paired(cand, ref, lambda rs: text_understanding(components(rs)), n=n_boot,
                                              seed=seed),
                "hygiene": _paired(cand, ref, lambda rs: components(rs)["hygiene"], n=n_boot, seed=seed + 1),
                "tokens_vs_reference": tokens_vs(cand, ref),
            }
            if judge_validated and k[1] in pref and name == "tuned":
                e["deltas"][name]["readability"] = {
                    "delta": READ_WEIGHTS["hygiene"] * (e["deltas"][name]["hygiene"]["delta"] or 0)
                    + READ_WEIGHTS["pref"] * (pref[k[1]] - 50.0), "note": "0.35 Δhygiene + 0.65 Δpref (†)"}
        out["entries"].append(e)
    if refs["tuned"] and refs["base"]:
        t, b = tables[refs["tuned"]][0], tables[refs["base"]][0]
        out["tuning_effect"] = _paired(t, b, lambda rs: text_understanding(components(rs)), n=n_boot, seed=seed + 2)
    # gap to the strongest cloud reference (same cases, paired)
    clouds = [k for k in tables if k[0] == "cloud_reference"]
    if clouds:
        best = max(clouds, key=lambda k: text_understanding(components(list(tables[k][0].values()))) or -1)
        for e in out["entries"]:
            if e["kind"] != "local":
                continue
            k = (e["kind"], e["model_id"], e["thinking"], e["sampling_source"])
            e["gap_to_cloud"] = {"cloud": best[1], **_paired(
                tables[k][0], tables[best][0], lambda rs: text_understanding(components(rs)), n=n_boot, seed=seed + 3)}
    return out


def noise_metrics(records: Iterable[dict], noisy_cases: list[dict]) -> dict[str, dict]:
    """noise_consistency, noise_drop and self_correction_acc per entry (spec 03 s7.8; side columns)."""
    parent_of = {n["case_id"]: n for n in noisy_cases}
    out = {}
    for k, recs in group_by_entry(records).items():
        by = {(r["case_id"], r["trial"]): r for r in recs if r["status"] == "ok"}
        same = tot = 0
        clean_pass = noisy_pass = 0
        sc_ok = sc_n = 0
        for (cid, trial), r in by.items():
            base_id = cid.split("/")[0]
            n = parent_of.get(base_id)
            if not n:
                continue
            kind_part = n["parent_task_id"]
            clean = by.get((f"{kind_part}@primary.unfenced", trial))
            if n["noise_kind"] == "self_correction":
                sc_n += 1
                sc_ok += bool(r["metrics"].get("pass"))
                continue
            if clean is None:
                continue
            tot += 1
            same += clean["metrics"].get("pass") == r["metrics"].get("pass")
            clean_pass += bool(clean["metrics"].get("pass"))
            noisy_pass += bool(r["metrics"].get("pass"))
        out[str(k)] = {"noise_consistency": (same / tot) if tot else None,
                       "noise_drop": (100 * (clean_pass - noisy_pass) / tot) if tot else None,
                       "self_correction_acc": (sc_ok / sc_n) if sc_n else None, "pairs": tot}
    return out
