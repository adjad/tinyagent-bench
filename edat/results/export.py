"""Publish a snapshot and the website's compact files.

    data/results/<board_version>/<device_id>/snapshot.json   edat.snapshot/1.0
    data/results/<board_version>/<device_id>/records.jsonl   dev + public_dataset records only
    data/results/<board_version>/cloud/...                   cloud references
    site/data/index.json                                     edat.site-index/1.0
    site/data/<board_version>/<device_id>.json               edat.site-board/1.0
    site/data/<board_version>/<device_id>.trajectories.json  dev split only
    site/data/<board_version>/cloud.json

Held-out records never leave the private store: their aggregates are
published, their case ids, records and trajectories are not.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from edat.results import keys
from edat.results.aggregate import Options, aggregate
from edat.results.reader import RunSet
from edat.results.schema import SITE_BOARD_SCHEMA_ID, SITE_INDEX_SCHEMA_ID, SNAPSHOT_SCHEMA_ID, validate
from edat.results.writer import atomic_write, record_line, utc_now
from edat.sidekick.labels import may_score

PUBLIC_SPLITS = ("dev", "public_dataset")
SAMPLE_NOTICE = ("SAMPLE DATA. Every model name and number in this file is invented to exercise the "
                 "website; nothing describes a real model.")
RATE_BLOCKS = {"pass1", "pass_all_trials", "agentic_score", "assistant_score", "score_mean"}
_HOME = re.compile(r"/(Users|home)/[^/\s\"']+")


def _r(x: Any, nd: int = 4) -> Any:
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, list):
        return [_r(v, nd) for v in x]
    if isinstance(x, dict):
        return {k: _r(v, nd) for k, v in x.items()}
    return x


def _pct(x: Any) -> Any:
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return round(100.0 * x, 2)
    if isinstance(x, list):
        return [_pct(v) for v in x]
    return x


def _dumps(obj: Any) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=False, ensure_ascii=False) + "\n").encode()


def scrub(text: Any, limit: int = 600) -> Any:
    """Shorten and remove home-directory paths from text bound for a public file."""
    if not isinstance(text, str):
        return text
    text = _HOME.sub(r"/\1/…", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- website rows ----------------------------------------------------------

def _vs_row(e: dict, tier: str, key: str, as_pct: bool) -> dict:
    out = {}
    for role in ("tuned", "base"):
        v = (e.get("vs") or {}).get(role) or {}
        t = (v.get("tiers") or {}).get(tier)
        if not t:
            out[role] = None
        elif t.get("refused"):
            out[role] = {"refused": t["refused"]}
        elif t.get("self"):
            out[role] = {"self": True}
        elif key in t and t[key]:
            d = t[key]
            conv = _pct if as_pct else _r
            out[role] = {"delta": conv(d["delta"]), "interval": conv(d["interval"]), "tag": d["tag"],
                         "n_cases": d.get("n_cases"), "wins": d.get("wins"), "losses": d.get("losses")}
            if d.get("warning"):
                out[role]["warning"] = d["warning"]
            if t.get("cross_selection"):
                out[role]["cross_selection"] = t["cross_selection"]
            if "ratio" in d:
                out[role]["ratio"] = _r(d["ratio"])
        else:
            out[role] = None
    return out


def _result(e: dict, tier: str, metric: str, block: dict, *, facet: str | None, as_pct: bool,
            vs_key: str | None = None, unit: str | None = None) -> dict:
    conv = _pct if as_pct else _r
    row = {"variant": e["entry_key"], "tier": tier, "facet": facet, "metric": metric,
           "unit": "%" if as_pct else unit, "value": conv(block.get("value")),
           "interval": conv(block.get("interval")), "interval_kind": block.get("interval_kind") or
           ("wilson" if metric == "pass_all_trials" else "none"),
           "n_cases": int(block.get("n_cases") or 0), "n_trials": int(block.get("n_trials") or 0),
           "status": None}
    if block.get("label"):
        row["label"] = block["label"]
    if row["interval"] is None:
        row["interval_kind"] = "none"
    if vs_key:
        row["vs"] = _vs_row(e, tier, vs_key, as_pct)
    return row


def site_rows(e: dict) -> tuple[list, list, list]:
    results, safety, curves = [], [], []
    for tier, b in sorted(e["tiers"].items()):
        if b.get("ranked") is False:
            continue                       # labelled rows: their own block in the snapshot, no site figure
        if tier == "perf":
            for metric, points in sorted((b.get("curves") or {}).items()):
                curves.append({"variant": e["entry_key"], "metric": metric,
                               "unit": "bytes" if metric.endswith("_bytes") else ("s" if metric.endswith("_s")
                                                                                    else "tokens/s"),
                               "points": [_r(p) for p in points]})
            continue
        comp = b.get("composite")
        if comp:
            n_trials = (b.get("pass1") or b.get("score") or {}).get("n_trials", 0)
            results.append(_result(e, tier, comp["metric"], {**comp, "n_trials": n_trials}, facet=None,
                                   as_pct=True, vs_key=comp["metric"]))
        for name, c in sorted((b.get("composites") or {}).items()):
            if comp and name == comp.get("metric"):
                continue
            n_trials = (b.get("pass1") or b.get("score") or {}).get("n_trials", 0)
            results.append(_result(e, tier, name, {**c, "n_trials": n_trials}, facet=None, as_pct=True,
                                   vs_key=name))
        if b.get("pass1"):
            results.append(_result(e, tier, "pass1", b["pass1"], facet=None, as_pct=True, vs_key="pass1"))
            pat = b["pass1"].get("pass_all_trials")
            if pat:
                results.append(_result(e, tier, "pass_all_trials", {**pat, "n_trials": b["pass1"]["n_trials"],
                                                                    "interval_kind": "wilson"},
                                       facet=None, as_pct=True))
        if b.get("score"):
            results.append(_result(e, tier, "score_mean", b["score"], facet=None, as_pct=True, vs_key="score"))
        for facet, fb in sorted((b.get("facets") or {}).items()):
            if fb.get("pass1"):
                results.append(_result(e, tier, "pass1", fb["pass1"], facet=facet, as_pct=True))
            elif fb.get("score"):
                results.append(_result(e, tier, "score_mean", fb["score"], facet=facet, as_pct=True))
        for metric, mb in sorted((b.get("metrics") or {}).items()):
            if mb:
                results.append(_result(e, tier, metric, mb, facet=None, as_pct=mb.get("interval_kind") == "wilson"))
        s = b.get("safety")
        if s:
            row = {"variant": e["entry_key"], "tier": tier, "trials": s["trials"]}
            for name in ("unsafe", "leak"):
                row[f"{name}_trials"] = s.get(f"{name}_trials")
                row[f"{name}_rate"] = _pct(s.get(f"{name}_rate"))
                row[f"{name}_interval"] = _pct(s.get(f"{name}_interval"))
            row["unsafe_per_100"] = _r(s.get("unsafe_per_100"))
            row["leaks_per_100"] = _r(s.get("leak_per_100"))
            row["vs"] = {"unsafe_rate": _vs_row(e, tier, "unsafe_rate", True),
                         "leak_rate": _vs_row(e, tier, "leak_rate", True)}
            safety.append(row)
    results += _loop_empty_rows(e)
    return results, safety, curves


def _empty_row(e: dict, tier: str, metric: str, facet: str | None, status: str, reason: str) -> dict:
    return {"variant": e["entry_key"], "tier": tier, "facet": facet, "metric": metric, "unit": "%",
            "value": None, "interval": None, "interval_kind": "none", "n_cases": 0, "n_trials": 0,
            "status": status, "empty_reason": reason}


def _loop_empty_rows(e: dict) -> list[dict]:
    """The second-loop column's empty cells, each with its reason (never a silent gap)."""
    from edat.results.aggregate import COMPOSITE_DEFS
    rows = []
    for tier, lp in sorted((e.get("loops") or {}).items()):
        metric = COMPOSITE_DEFS[lp["view"]][0].name
        if lp.get("empty"):
            status = "not_applicable" if lp["view"] in e["tiers"] else "not_run"
            rows.append(_empty_row(e, lp["view"], metric, None, status, lp["empty_reason"]))
        for lane, why in sorted((lp.get("lanes") or {}).items()):
            rows.append(_empty_row(e, lp["view"], "pass1", f"lane={lane}", "not_applicable",
                                   why.split(": ", 1)[-1]))
    return rows


def loop_rows(e: dict) -> list[dict]:
    """Both loops' columns and the per-model gap, one row per entry and loop tier."""
    from edat.results.aggregate import COMPOSITE_DEFS
    rows = []
    for tier, lp in sorted((e.get("loops") or {}).items()):
        hb = e["tiers"].get(lp["view"]) or {}
        head = (e["tiers"].get(tier) or {}).get("composite")
        harbor = None if lp.get("empty") else hb.get("composite")
        ref_al = lp.get("reference_assistant_lane")
        gap = lp.get("gap")
        if gap and not gap.get("refused"):
            gap = {"metric": gap["metric"], "of": gap["of"], "delta": _pct(gap["delta"]),
                   "interval": _pct(gap["interval"]), "tag": gap["tag"], "n_cases": gap.get("n_cases_paired"),
                   "wins": gap.get("wins"), "losses": gap.get("losses")}
        rows.append({
            "variant": e["entry_key"], "tier": tier, "loop": lp["loop"], "view": lp["view"],
            "headline": None if not head else {"metric": head["metric"], "value": _pct(head["value"]),
                                               "interval": _pct(head["interval"])},
            "reference_assistant_lane": None if not ref_al else {
                "metric": ref_al["metric"], "value": _pct(ref_al["value"]), "interval": _pct(ref_al["interval"])},
            "second": None if not harbor else {"metric": COMPOSITE_DEFS[lp["view"]][0].name,
                                               "value": _pct(harbor["value"]), "interval": _pct(harbor["interval"]),
                                               "n_cases": harbor.get("n_cases")},
            "gap": gap, "empty_reason": lp.get("empty_reason"),
            "lanes": {k: v.split(": ", 1)[-1] for k, v in (lp.get("lanes") or {}).items()}})
    return rows


def _run_row(e: dict) -> dict:
    blocks = [b for b in e["tiers"].values()]
    if e["fit"]["verdict"] == "does_not_fit":
        status = "does_not_fit"
    elif e.get("invalid"):
        status = "invalid"
    elif not blocks or not any(b.get("graded") or b.get("curves") for b in blocks):
        status = "not_run"
    else:
        status = "ok"
    ex = [b["excluded_rate"] for b in blocks if b.get("excluded_rate") is not None]
    commits = sorted({c for b in blocks for c in b.get("harness_commits") or ()})
    fit = {k: e["fit"].get(k) for k in ("verdict", "max_level_fits", "peak_bytes_at_2048", "peak_bytes_at_10240",
                                         "budget_bytes") if k in e["fit"]}
    return {"variant": e["entry_key"], "status": status, "fit": fit, "ranked": bool(e["ranked"]),
            "rank_group": e["rank_group"], "excluded_rate": _pct(max(ex)) if ex else None,
            "run_ids": sorted({r for b in blocks for r in b.get("run_ids") or ()}),
            "harness_commit": commits[0] if len(commits) == 1 else None,
            "date": ((e.get("run_date") or {}).get("last") or "")[:10] or None}


def device_labels(device_id: str, dev: dict) -> dict:
    """Display names the website shows for a device, derived from the record's device block."""
    cls = str(dev.get("class") or device_id)
    ram = dev.get("ram_bytes")
    gb = f"{round(ram / 2**30)} GB" if isinstance(ram, int) and ram > 0 else None
    soc = dev.get("soc")
    if cls.startswith("mac"):
        kind, family, fit = "laptop", "Mac", "fits a 16 GB Mac"
    elif cls.startswith("iphone"):
        kind, family, fit = "phone", "iPhone", "fits the iPhone Air"
    elif cls == "simulator":
        kind, family, fit = "simulator", "Simulator", "simulator: not a measurement"
    else:
        kind, family, fit = "device", cls, "fits this device"
    detail = ", ".join(x for x in (soc, gb) if x)
    return {"name": f"{family} ({detail})" if detail else family,
            "short": f"{family} {gb}" if gb else family, "kind": kind, "fit_label": fit}


FINDINGS_VIEW = "in_assistant_findings"
FINDINGS_SCHEMA_ID = "edat.site-findings/0.1"
FINDINGS_MARKS = {"found": "found", "missed": "missed", "wrongly_raised": "wrong", "stale": "stale",
                  "right": "right", "done_wrongly": "wrong", "should_not": "wrong", "unsafe": "unsafe",
                  "not_done": "right", "not_in_key": "not_in_key", "offered": "offered", "answer": "answer",
                  "passed_over": "passed_over"}


def findings_file(records: list[dict], raw_of, *, board: str, device_id: str, sample: bool,
                  notice: str | None, now: str) -> dict | None:
    """What each model found in the assistant, per variant, profile and trial (dev split only).

    Reads the in-assistant findings records (`suite: productbench-findings`).
    `mark` is the site's word (found, missed, wrong, stale, unsafe, ...);
    `outcome` keeps the record's own word (`wrongly_raised`, `done_wrongly`, ...)."""
    from edat.results.aggregate import view_of
    recs = [r for r in records if view_of(r) == FINDINGS_VIEW and r["split"] in PUBLIC_SPLITS]
    if not recs:
        return None
    runs: dict[tuple, dict] = {}
    profiles: set[str] = set()
    for r in sorted(recs, key=lambda r: (keys.entry_key(r), r["case_id"], r["trial"])):
        profile = r.get("facets", {}).get("profile") or "-"
        profiles.add(profile)
        k = (keys.entry_key(r), profile, r["trial"])
        run = runs.setdefault(k, {"variant": k[0], "profile": profile, "trial": r["trial"], "sections": {},
                                  "counts": {}})
        raw = raw_of(r) or {}
        outcome = r["metrics"].get("outcome") or raw.get("outcome") or "unknown"
        section = r.get("facets", {}).get("family") or "other"
        item = {"mark": FINDINGS_MARKS.get(outcome, outcome), "outcome": outcome,
                "text": scrub(raw.get("text"), 240), "item": scrub(raw.get("item"), 160),
                "at": raw.get("now"), "case_id": r["case_id"], "scored": r["status"] == "ok",
                "unsafe": (r["safety"].get("unsafe_actions") or 0) > 0}
        if r.get("facets", {}).get("zone"):
            item["zone"] = r["facets"]["zone"]
        run["sections"].setdefault(section, []).append(item)
        run["counts"][item["mark"]] = run["counts"].get(item["mark"], 0) + 1
    out = {"schema": FINDINGS_SCHEMA_ID, "sample": sample, "notice": notice, "board_version": board,
           "device_id": device_id, "generated_at": now, "profiles": sorted(profiles),
           "runs": list(runs.values())}
    validate(out, "site_findings")
    return out


def _split_selection(e: dict, rows: list[dict]) -> None:
    """Rows of a selection block keep the plain tier name and carry `selection`
    ({tier, manifest, n_items, ci95_half_width_pts}), so the board can print
    "core, ±x points" beside the number and never puts two selections in one cell."""
    from edat.results.aggregate import SELECTION_SEP, base_view
    for row in rows:
        key = row["tier"]
        if SELECTION_SEP not in key:
            continue
        sel = (e["tiers"].get(key) or {}).get("selection") or {}
        row["tier"] = base_view(key)
        row["selection"] = {"tier": sel.get("tier") or key.split(SELECTION_SEP, 1)[1],
                            "manifest": (sel.get("manifest_sha256") or "")[:12] or None,
                            "n_items": sel.get("n_items"), "ci95_half_width_pts": _r(sel.get("ci95_half_width_pts"))}


def site_board(agg: dict, device_id: str, entries: list[dict], *, sample: bool, notice: str | None,
               now: str | None = None) -> dict:
    models, variants, runs, results, safety, curves, ranking, loops = {}, [], [], [], [], [], [], []
    for e in entries:
        models.setdefault(e["model_id"], {
            "id": e["model_id"], "kind": e["kind"], "role": e["role"], "params_total": e.get("params_total"),
            "params_active": e.get("params_active"), "architecture": e.get("architecture"),
            "moe": (e.get("architecture") == "moe") if e.get("architecture") else None,
            "resolved_model": e.get("resolved_model"), "upstream_provider": e.get("upstream_provider"),
            "run_date": e.get("run_date")})
        variants.append({"key": e["entry_key"], "model_id": e["model_id"], "quant": e.get("quant"),
                         "runtime": e.get("runtime"), "runtime_version": e.get("runtime_version"),
                         "thinking": e["thinking"], "headline_entry": e.get("headline_entry"),
                         "weights": e.get("weights"),
                         "sampling_source": (e.get("sampling_sources") or [None])[0]})
        runs.append(_run_row(e))
        loops += loop_rows(e)
        r, s, c = site_rows(e)
        rk = [{"variant": e["entry_key"], "tier": tier, "metric": metric, "rank_range": rr}
              for tier, b in e["tiers"].items() for metric, rr in sorted((b.get("rank_ranges") or {}).items())]
        _split_selection(e, r + s + rk)
        results += r
        safety += s
        curves += c
        ranking += rk
    dev = next((e.get("device") for e in entries if e.get("device")), None)
    device = None if device_id == "cloud" else {
        "id": device_id, "class": (dev or {}).get("class") or device_id, "ram_bytes": (dev or {}).get("ram_bytes"),
        "soc": (dev or {}).get("soc"), "os": (dev or {}).get("os"), **device_labels(device_id, dev or {})}
    refs = (agg.get("references") or {}).get(device_id) or {}
    board = {"schema": SITE_BOARD_SCHEMA_ID, "sample": sample, "notice": notice,
             "board_version": agg["board_version"], "generated_at": now or utc_now(), "device": device,
             "references": {"tuned": refs.get("tuned"), "base": refs.get("base")},
             "models": list(models.values()), "variants": variants, "runs": runs, "results": results,
             "safety": safety, "curves": curves, "ranking": ranking, "loops": loops}
    validate(board, "site_board")
    return board


def _steps_from_raw(raw: Any) -> tuple[list, str | None]:
    steps = []
    if not isinstance(raw, dict):
        return steps, None
    for s in raw.get("steps") or ():
        if not isinstance(s, dict):
            continue
        for c in s.get("calls") or ():
            steps.append({"who": "model", "call": scrub(c.get("name"), 120),
                          "args": {k: scrub(v, 200) for k, v in (c.get("args") or c.get("arguments") or {}).items()}
                          if isinstance(c.get("args") or c.get("arguments"), dict) else scrub(str(c.get("args")), 300),
                          "result": scrub(c.get("result"), 400), "error": scrub(c.get("error"), 200)})
        if s.get("content"):
            steps.append({"who": "model", "text": scrub(s["content"], 400)})
    final = raw.get("final")
    if final is None and isinstance(raw.get("captured"), dict):
        final = raw["captured"].get("answer")
        for c in raw["captured"].get("tool_calls") or ():
            steps.append({"who": "model", "call": scrub(c.get("name"), 120), "args": c.get("args") or {},
                          "result": scrub(c.get("result"), 400), "error": None})
    return steps, scrub(final, 1200)


def trajectories(entries_records: dict[str, list[dict]], raw_of, limit_per_entry: int) -> list[dict]:
    out = []
    for ek, recs in sorted(entries_records.items()):
        picked = [r for r in recs if r["split"] in PUBLIC_SPLITS and r["tier"] != "perf" and r["status"] == "ok"
                  and r.get("raw") and (r["tier"] != "sidekick" or may_score(r))]
        picked.sort(key=lambda r: (r["tier"], r["case_id"], r["trial"]))
        for r in picked[:limit_per_entry]:
            raw = raw_of(r)
            steps, final = _steps_from_raw(raw)
            labels = (raw or {}).get("labels") if isinstance(raw, dict) else None
            unsafe = (r["safety"].get("unsafe_actions") or 0) > 0 or (r["safety"].get("privacy_leaks") or 0) > 0
            ok = r["metrics"].get("pass")
            if ok is None and isinstance(r["metrics"].get("score"), (int, float)):
                ok = r["metrics"]["score"] == 1.0
            out.append({"variant": ek, "tier": r["tier"], "case_id": r["case_id"], "trial": r["trial"],
                        "steps": steps, "final": final,
                        "checks": [{"ok": False, "text": scrub(x, 120)} for x in (labels or ())],
                        "outcome": "unsafe" if unsafe else ("pass" if ok else "fail")})
    return out


# --- the export ------------------------------------------------------------

def export(sets: Iterable[RunSet], *, data_dir: Path | str, site_dir: Path | str | None,
           options: Options | None = None, notice: str | None = None,
           trajectories_per_entry: int = 60, now: str | None = None, write_records: bool = True) -> dict:
    """Aggregate `sets` (one board version) and write the snapshot per device
    and, unless site_dir is None, the website files. `now` fixes generated_at
    (the sample uses it so regenerating gives identical files)."""
    stamp = now or utc_now()
    sets = list(sets)
    records = [r for s in sets for r in s.records]
    if not records:
        raise ValueError("nothing to export")
    sample = bool(records[0].get("sample"))
    if sample and notice is None:
        notice = SAMPLE_NOTICE
    agg = aggregate(records, options)
    board = agg["board_version"]
    raw_index: dict[int, RunSet] = {}
    for s in sets:
        for r in s.records:
            raw_index[id(r)] = s

    def raw_of(rec: dict) -> Any:
        s = raw_index.get(id(rec))
        p = s.raw_path(rec) if s else None
        if p is None or not p.exists():
            return None
        text = p.read_text()
        return json.loads(text) if p.suffix == ".json" else {"final": text}

    by_device: dict[str, list[dict]] = defaultdict(list)
    for e in agg["entries"]:
        by_device[e["device_id"]].append(e)
    recs_by_device: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        recs_by_device[keys.device_id(r)].append(r)
    data_dir, written = Path(data_dir), []
    run_rows = [{"run_id": src["run_id"], "tier": s.tier, "records": src["records"],
                 "records_sha256": src["records_sha256"],
                 "harness_commit": (s.comparison_key or {}).get("harness_commit"),
                 "sampling_class": (s.comparison_key or {}).get("sampling_class")}
                for s in sets for src in s.sources]
    for dev, entries in sorted(by_device.items()):
        out = data_dir / board / dev
        recs = recs_by_device.get(dev, [])
        public = sorted((r for r in recs if r["split"] in PUBLIC_SPLITS),
                        key=lambda r: (r["tier"], keys.entry_key(r), r["case_id"], r["trial"]))
        dev_runs = sorted({r["run_id"] for r in recs})
        snap = {"schema": SNAPSHOT_SCHEMA_ID, "board_version": board, "device_id": dev, "sample": sample,
                "notice": notice, "generated_at": stamp,
                "references": {"tuned": (agg["references"].get(dev) or {}).get("tuned"),
                               "base": (agg["references"].get(dev) or {}).get("base")},
                "runs": [r for r in run_rows if r["run_id"] in dev_runs],
                "records_exported": len(public), "records_withheld_heldout": len(recs) - len(public),
                "entries": [_r(e, 6) for e in entries], "warnings": agg["warnings"]}
        snap.pop("warnings")
        validate(snap, "snapshot")
        atomic_write(out / "snapshot.json", _dumps(snap))
        written.append(out / "snapshot.json")
        if write_records:
            atomic_write(out / "records.jsonl", b"".join(record_line(r) for r in public))
            written.append(out / "records.jsonl")
    index_boards = []
    if site_dir is not None:
        site_dir = Path(site_dir)
        devices = []
        for dev, entries in sorted(by_device.items()):
            name = "cloud" if dev == "cloud" else dev
            sb = site_board(agg, dev, entries, sample=sample, notice=notice, now=stamp)
            atomic_write(site_dir / board / f"{name}.json", _dumps(sb))
            written.append(site_dir / board / f"{name}.json")
            if dev != "cloud":
                er: dict[str, list[dict]] = defaultdict(list)
                for r in recs_by_device.get(dev, []):
                    er[keys.entry_key(r)].append(r)
                traj = {"schema": "edat.site-trajectories/1.0", "sample": sample, "notice": notice,
                        "board_version": board, "device_id": dev,
                        "trajectories": trajectories(er, raw_of, trajectories_per_entry)}
                atomic_write(site_dir / board / f"{name}.trajectories.json", _dumps(traj))
                written.append(site_dir / board / f"{name}.trajectories.json")
                fnd = findings_file(recs_by_device.get(dev, []), raw_of, board=board, device_id=dev,
                                    sample=sample, notice=notice, now=stamp)
                if fnd is not None:
                    atomic_write(site_dir / board / f"{name}.findings.json", _dumps(fnd))
                    written.append(site_dir / board / f"{name}.findings.json")
                devices.append({"id": dev, "class": (sb["device"] or {}).get("class"),
                                "name": (sb["device"] or {}).get("name"),
                                "path": f"{board}/{name}.json", "trajectories": f"{board}/{name}.trajectories.json",
                                "findings": f"{board}/{name}.findings.json" if fnd is not None else None})
        index_path = site_dir / "index.json"
        index = json.loads(index_path.read_text()) if index_path.exists() else {
            "schema": SITE_INDEX_SCHEMA_ID, "boards": []}
        if index.get("boards") and any(b.get("sample") != sample for b in index["boards"]
                                       if b["board_version"] != board):
            raise ValueError(f"{index_path} lists {'real' if sample else 'sample'} boards; "
                             "sample and real exports never share one site index")
        entry = {"board_version": board, "sample": sample, "generated_at": stamp, "devices": devices,
                 "cloud": f"{board}/cloud.json" if "cloud" in by_device else None,
                 "references": agg["references"]}
        index["boards"] = [b for b in index.get("boards", []) if b["board_version"] != board] + [entry]
        index["boards"].sort(key=lambda b: b["board_version"])
        index["sample"] = all(b.get("sample") for b in index["boards"])
        index["notice"] = SAMPLE_NOTICE if index["sample"] else None
        index["generated_at"] = stamp
        atomic_write(index_path, _dumps(index))
        written.append(index_path)
        index_boards = index["boards"]
    return {"board_version": board, "written": [str(p) for p in written], "aggregate": agg,
            "index_boards": index_boards}


# --- snapshot diff ---------------------------------------------------------

def load_snapshot(path: Path | str) -> dict:
    p = Path(path)
    if p.is_dir():
        p = p / "snapshot.json"
    snap = json.loads(p.read_text())
    validate(snap, "snapshot")
    return snap


def diff_snapshots(a: dict, b: dict, *, allow_board_change: bool = False) -> dict:
    """Headline changes per entry and tier between two snapshots. Refuses
    snapshots of different devices, and of different board versions unless
    allowed (then it is a listing of changes, never a difference claim);
    refuses entries whose harness commit or sampling class changed."""
    if a["device_id"] != b["device_id"]:
        raise keys.IncompatibleResults(f"refusing to diff snapshots of different devices "
                                       f"({a['device_id']} vs {b['device_id']})")
    if a["sample"] != b["sample"]:
        raise keys.IncompatibleResults("refusing to diff a sample snapshot against a real one")
    if a["board_version"] != b["board_version"] and not allow_board_change:
        raise keys.IncompatibleResults(
            f"refusing to diff snapshots of different board versions ({a['board_version']} vs "
            f"{b['board_version']}): differences are valid only within one board version. "
            "Pass --allow-board-change to list what changed without comparing scores.")
    ea = {e["entry_key"]: e for e in a["entries"]}
    eb = {e["entry_key"]: e for e in b["entries"]}
    changes, refused = [], []
    for k in sorted(set(ea) & set(eb)):
        for tier in sorted(set(ea[k]["tiers"]) & set(eb[k]["tiers"])):
            ta, tb = ea[k]["tiers"][tier], eb[k]["tiers"][tier]
            for field_, label in (("harness_commits", "harness commit"), ("sampling_sources", "sampling")):
                if ta.get(field_) != tb.get(field_):
                    refused.append({"entry": k, "tier": tier,
                                    "reason": f"different {label}: {ta.get(field_)} vs {tb.get(field_)}"})
                    break
            else:
                ha, hb = ta.get("headline"), tb.get("headline")
                if ha and hb and ha["metric"] == hb["metric"]:
                    changes.append({"entry": k, "tier": tier, "metric": ha["metric"], "before": ha["value"],
                                    "after": hb["value"], "change": hb["value"] - ha["value"],
                                    "intervals_overlap": not (ha["interval"][1] < hb["interval"][0]
                                                              or hb["interval"][1] < ha["interval"][0])})
        if ea[k]["fit"].get("verdict") != eb[k]["fit"].get("verdict"):
            changes.append({"entry": k, "tier": "fit", "metric": "fits_16gb_mac",
                            "before": ea[k]["fit"].get("verdict"), "after": eb[k]["fit"].get("verdict")})
    if a["board_version"] != b["board_version"]:
        changes = [{k: v for k, v in c.items() if k not in ("change", "intervals_overlap")} for c in changes]
    return {"board_versions": [a["board_version"], b["board_version"]], "device_id": a["device_id"],
            "added": sorted(set(eb) - set(ea)), "removed": sorted(set(ea) - set(eb)),
            "changes": changes, "refused": refused}
