"""Re-score stored runs with the current scorer and answer key: no model, no engine, no server.

    python -m scripts.productbench.rescore <run dir> [<run dir> ...] --out <dir> [--profile id=path]

Every record is scored again from what it captured (results.jsonl). The run folders are only
read; everything goes to `--out`:

  results.jsonl     the re-scored records: each keeps its captured output, gets the new score,
                    `scorer` (this version), `scorer_commit`, `old_score` and `rescored_from` (run, old scorer)
  not_rescored.jsonl records that cannot be re-scored, each with why (a stated-preference Brief
                    check stored without its Brief, a long session whose answers were stored cut
                    short, a case the current key no longer has). They are left out of the new
                    numbers, never kept silently.
  meta.json         the runs, the profiles used, the known gaps, what was dropped, and the scorer
                    checkout's commit (`scorer_commit`) with `scorer_dirty` (uncommitted changes in the scorer
                    code or the answer keys and cases, SCORED_PATHS; None when git could not say)
  summary.json/.md  the new summary (summary.summarize)
  compare.json/.md  old and new numbers side by side, per entry and profile: overall, each
                    surface and the safety counts, plus per surface how many records changed
  findings.*        the findings page for the re-scored records

`--out` must be a new or empty folder, outside the run folders and outside this repository:
the command refuses anything else, so it can never overwrite a run.

Runs given together are merged the way findings.py merges them: a stopped run's partial trial
gives way to the run that repeated it (`drop_resumed`), and a re-run replaces the cases it
re-asked of the runs its meta.json `supersedes` (`drop_superseded`). Known capture faults are
carried over (and re-found from the records' own tool results and logged actions, including
`sandbox_allowed_refused_write`), and run-level known gaps are merged into the output's meta.json.

Native writes (known_faults.py `native_writes_unanswered`): for a run from before the sandbox
answered Calendar and Reminders writes, a native write's action id counts once per trial home
(the first copy stays, later copies in that home are replays and are taken out before scoring).
The same id in another trial is a genuine write: Harbor's ids are hashes of the write, and each
trial is a fresh home at the same pinned clock.

Profiles: the current answer key in this checkout (test_fixtures/bench_profiles/<id> or
test_fixtures/productbench/<id>) is used before the path a run's meta.json recorded, since the
point is to score old output against today's key; `--profile id=path` overrides both.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.productbench import autonomy, cases as case_files, findings, known_faults  # noqa: E402
from scripts.productbench import preferences, privacy, questions, score_ask, score_brief  # noqa: E402
from scripts.productbench import score_day, summary  # noqa: E402
from scripts.productbench.profile import Profile, load as load_profile  # noqa: E402


# What a re-score's numbers depend on in this checkout: the scorer code, and the profiles, answer
# keys and case files it scores against (an edited ledger changes scores as surely as code does).
SCORED_PATHS = ("scripts/productbench", "test_fixtures/bench_profiles", "test_fixtures/productbench")


def scorer_checkout() -> tuple[str, bool | None]:
    """The scorer checkout's commit and whether anything a score depends on (SCORED_PATHS) has
    uncommitted changes. Never raises: ("", None) when git cannot answer."""
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                             timeout=10, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", *SCORED_PATHS], cwd=ROOT,
                               capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        return sha, bool(dirty)
    except Exception:
        return "", None


class NotRescorable(Exception):
    pass


# ------------------------------------------------------------------ per-profile context
class ProfileCtx:
    """What the runner builds per profile (cases, privacy rules, preference cases), rebuilt
    from the current key."""

    def __init__(self, profile: Profile, explicit_cases: str | None = None):
        self.profile = profile
        cases, self.case_sources = case_files.for_profile(profile, explicit_cases)
        if not explicit_cases:
            have = {c["id"] for c in cases.get("ask", [])}
            cases["ask"] += [c for c in questions.cases_for(profile) if c["id"] not in have]
        self.cases = cases
        self.by_id = {c["id"]: c for c in cases.get("ask", [])}
        for c in list(self.by_id.values()):
            if c.get("followup"):
                self.by_id[c["followup"]["id"]] = c["followup"]
        self.plans = {c["id"]: c for c in cases.get("day", {}).get("plans", [])}
        self.rules = dict(cases.get("day", {}).get("rules") or {})
        self.entries = profile.data.get("edat_entries") or []
        self.entry_by_id = {e["id"]: e for e in self.entries}
        self.directory = privacy.Directory.of(profile)
        self.privacy_rules = privacy.rules_from_ledger(self.entries)
        self.pref_cases = {p["id"]: p for p in preferences.build_cases(profile, self.entries, cases.get("ask", []))}
        self._act_plan = None

    @property
    def act_plan(self) -> dict:
        if self._act_plan is None:
            self._act_plan = autonomy.plan_for(self.profile)
        return self._act_plan

    def leaks(self, turn: dict, case_id: str) -> tuple[dict, list]:
        items = privacy.outbound_of_turn(turn, self.directory, case=case_id)
        return {"outbound": len(items), "leaks": privacy.check(items, self.privacy_rules),
                "items": [o.as_dict() for o in items]}, items


def _pref_result(e: dict, chk: str | None, turn: dict, prof: Profile, items: list) -> dict | None:
    """One preference check on one stored turn, as run_productbench._pref_case runs it."""
    if chk == "pref":
        if (e["expected"].get("check") or {}).get("on", "answer_text") == "answer_text":
            return preferences.check_text(e, turn.get("answer") or "", profile=prof)
        return preferences.check_tools(e, turn, prof, items)
    if chk == "pref_plan":
        return preferences.check_plan(e, score_day.parse_plan(turn.get("answer") or "", prof),
                                      turn.get("answer") or "", prof)
    if chk == "no_waiver":
        return preferences.check_no_waiver(turn)
    if chk and chk in {v for _, v in preferences.OVERRIDES.values()}:
        return preferences.check_inverted(chk, turn.get("answer") or "")
    if chk:
        return preferences.check_scope(chk, turn, prof, items, e)
    return None


def rescore_record(r: dict, ctx: ProfileCtx, last_brief: dict) -> dict:
    """The record with its new score (and privacy, recomputed from the captured turn). Raises
    NotRescorable with the reason when the record does not hold what its check needs."""
    prof = ctx.profile
    surf, cid, cap = r.get("surface"), r.get("case"), r.get("captured") or {}
    key = (r.get("entry") or r.get("model"), r.get("trial"), r.get("now"))
    out = dict(r)
    if surf == "brief":
        if "snapshot" not in cap:
            raise NotRescorable("the Brief snapshot was not stored")
        last_brief[key] = cap["snapshot"]
        out["score"] = score_brief.score_brief(cap["snapshot"], prof, ctx.cases.get("brief", {})).as_dict()
        items = privacy.outbound_of_brief(cap["snapshot"], ctx.directory)
        out["privacy"] = {"outbound": len(items), "leaks": privacy.check(items, ctx.privacy_rules),
                          "items": [o.as_dict() for o in items]}
    elif surf == "question" and cid == "TQ-brief-todos":
        out["score"] = questions.brief_todos({"todos": cap.get("todos") or []}, prof)
    elif surf == "question":
        if cid not in ctx.by_id:
            raise NotRescorable(f"case {cid} is not in the current key")
        sc = questions.score_question(ctx.by_id[cid], cap.get("answer") or "", prof)
        sc["unsafe"] = score_ask.score_case({"id": cid, "check_invented": False}, cap, prof).unsafe
        out["score"] = sc
        out["privacy"], _ = ctx.leaks(cap, cid)
    elif surf == "day_tab":
        blocks = score_day.blocks_from_today(cap.get("snapshot") or {}, prof)
        out["score"] = score_day.score_plan(blocks, prof, dict(ctx.rules)).as_dict()
    elif surf == "ask":
        if cid not in ctx.by_id:
            raise NotRescorable(f"case {cid} is not in the current key")
        out["score"] = score_ask.score_case(ctx.by_id[cid], cap, prof).as_dict()
        out["privacy"], _ = ctx.leaks(cap, cid)
    elif surf == "day":
        case = ctx.plans.get(cid)
        if case is None:
            raise NotRescorable(f"plan case {cid} is not in the current key")
        rules = dict(ctx.rules)
        at = r.get("now") if r.get("now") != prof.data["anchor"][11:16] else None
        if at:
            rules["now"] = at.split()[-1]
        answer = cap.get("answer") or ""
        sc = (score_day.score_slot(answer, prof, {**case, "now": at} if at else case)
              if case.get("kind") == "slot" else
              score_day.score_plan(score_day.parse_plan(answer, prof), prof, rules))
        view = score_ask.score_case({"id": cid, "check_invented": False}, cap, prof)
        out["score"] = {**sc.as_dict(), "unsafe": view.unsafe, "tools": view.tools, "readability": view.readability}
        out["privacy"], _ = ctx.leaks(cap, cid)
    elif surf == "privacy":
        pv, items = ctx.leaks(cap, cid)
        rule_id = ((ctx.by_id.get(cid) or {}).get("privacy") or {}).get("rule") or str(cid).removeprefix("P-")
        rule = next((x for x in ctx.privacy_rules if x.id == rule_id), None)
        out["privacy"] = pv
        out["privacy_outcome"] = (privacy.wrong_recipient_outcome(items, rule)
                                  if rule and rule.kind == "wrong_recipient" else None)
        out["score"] = {"unscored": True}
    elif surf == "pref" and r.get("on") == "brief":
        snap = last_brief.get(key)
        if snap is None:
            raise NotRescorable("the trial's Brief record is not in the record set")
        e = ctx.entry_by_id.get(r.get("ledger_entry"))
        if e is None:
            raise NotRescorable(f"preference {r.get('ledger_entry')} is not in the current key")
        text, first = preferences.brief_text(snap)
        res = preferences.check_text(e, text, first_item=first, profile=prof)
        out["score"] = {"score": None if res["ok"] is None else float(res["ok"]), **res}
    elif surf == "pref" and r.get("on") == "brief_after_statement":
        if "snapshot" not in cap:
            raise NotRescorable("the Brief it was checked against was not stored (stored from later runs on)")
        e = ctx.entry_by_id.get(r.get("ledger_entry"))
        if e is None:
            raise NotRescorable(f"preference {r.get('ledger_entry')} is not in the current key")
        text, first = preferences.brief_text(cap["snapshot"])
        res = preferences.check_text(e, text, first_item=first, profile=prof)
        out["score"] = {"score": None if res["ok"] is None else float(res["ok"]), **res}
    elif surf == "pref":
        pc = ctx.pref_cases.get(cid)
        if pc is None or "turns" not in cap:
            raise NotRescorable(f"preference case {cid} is not in the current key" if pc is None
                                else "the turns were not stored")
        e = ctx.entry_by_id[pc["entry"]]
        results, leaks, outbound = [], [], 0
        for i, t in enumerate(cap["turns"]):
            pv, items = ctx.leaks(t, f"{cid}-{i + 1}")
            leaks += pv["leaks"]
            outbound += pv["outbound"]
            res = _pref_result(e, t.get("check"), t, prof, items)
            if res is not None:
                results.append(res)
        oks = [x["ok"] for x in results]
        ok = None if not oks or any(o is None for o in oks) else all(oks)
        out["score"] = {"score": None if ok is None else float(ok), "checks": results}
        out["privacy"] = {"outbound": outbound, "leaks": leaks}
    elif surf == "act":
        need = ("done", "background_writes", "yes_at")
        if not all(k in cap for k in need):
            raise NotRescorable("the act-for-me record lacks done/background_writes/yes_at")
        res = autonomy.judge(cap["done"], cap["background_writes"], ctx.act_plan["expectations"], prof,
                             yes_at=cap["yes_at"])
        old = r.get("score") or {}
        res.update({k: old[k] for k in ("mode", "expectations", "passed_over") if k in old})
        out["score"] = res
    elif surf == "long":
        raise NotRescorable("the long session stores its answers cut at 400 characters")
    else:
        raise NotRescorable(f"no re-scorer for surface {surf!r}")
    return out


# ------------------------------------------------------------------ loading
def _profile_path(pid: str, metas: list[dict], overrides: dict[str, Path]) -> Path | None:
    cands = []
    if pid in overrides:
        cands.append(Path(overrides[pid]))
    cands += [ROOT / "test_fixtures" / "bench_profiles" / pid / "profile.json",
              ROOT / "test_fixtures" / "productbench" / pid / "profile.json"]
    for m in metas:
        for p in m.get("profiles") or []:
            if p.get("id") == pid and p.get("path"):
                cands.append(Path(p["path"]))
    return next((c for c in cands if c.exists()), None)


def _explicit_cases(pid: str, metas: list[dict]) -> str | None:
    for m in metas:
        for p in m.get("profiles") or []:
            for src in (p.get("cases") or []) if p.get("id") == pid else []:
                if src.endswith(".json") and Path(src).exists():
                    return src
    return None


def load_runs(run_dirs: list[Path]) -> tuple[list[list[dict]], list[dict], list[str], list[str]]:
    loaded, metas, ids, skipped = [], [], [], []
    for d in run_dirs:
        d = Path(d)
        if not (d / "results.jsonl").exists():
            skipped.append(f"{d.name}: no results.jsonl (a run that stopped before its first record)")
            continue
        rows, meta = findings.load_records(d)
        loaded.append(rows)
        metas.append(meta)
        ids.append(meta.get("run") or (rows[0].get("run") if rows else d.name))
    return loaded, metas, ids, skipped


# ------------------------------------------------------------------ comparing
def _num(row: dict | None, path: tuple) -> float | None:
    cur = row
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur if isinstance(cur, (int, float)) and not isinstance(cur, bool) else None


COMPARE = [
    ("overall", ("overall",)),
    ("Ask", ("surfaces", "ask", "score")),
    ("Brief (the model's)", ("surfaces", "brief", "score")),
    ("Brief as the user sees it", ("surfaces", "brief", "seen_score")),
    ("Harbor showed a stale calendar slot", ("surfaces", "brief", "harbor_stale")),
    ("Brief recall", ("surfaces", "brief", "recall")),
    ("Brief model recall", ("surfaces", "brief", "model_recall")),
    ("Brief false alarms", ("surfaces", "brief", "false_alarms")),
    ("Brief stale (all items)", ("surfaces", "brief", "stale")),
    ("Brief stale (model-written)", ("surfaces", "brief", "model_stale")),
    ("Brief not in the key (model items)", ("surfaces", "brief", "not_in_key_model_items")),
    ("Day", ("surfaces", "day", "score")),
    ("Day validity", ("surfaces", "day", "validity")),
    ("Day tab (code)", ("day_tab", "score")),
    ("Questions", ("questions", "score")),
    ("Preferences", ("preferences", "score")),
    ("Act", ("act_for_me", "score")),
    ("unsafe (executed)", ("safety", "executed_actions")),
    ("attempted unsafe", ("safety", "attempted_unsafe")),
    ("act unsafe", ("safety", "act_unsafe")),
    ("not stopped", ("safety", "act_not_stopped")),
    ("privacy leaks", ("safety", "privacy_leaks")),
]


def compare_rows(old_rows: list[dict], new_rows: list[dict], changes: dict) -> list[dict]:
    old = {(r["entry"], r["profile"]): r for r in old_rows}
    new = {(r["entry"], r["profile"]): r for r in new_rows}
    out = []
    for k in sorted(set(old) | set(new)):
        o, n = old.get(k), new.get(k)
        nums = []
        for label, path in COMPARE:
            a, b = _num(o, path), _num(n, path)
            if a is None and b is None:
                continue
            if label == "Act":
                a = None if a is None else round(100 * a, 1)
                b = None if b is None else round(100 * b, 1)
            nums.append({"what": label, "old": a, "new": b,
                         "delta": None if a is None or b is None else round(b - a, 2)})
        out.append({"entry": k[0], "profile": k[1], "numbers": nums,
                    "records": {s: dict(c) for s, c in sorted(changes.get(k, {}).items())}})
    return out


def _f(v) -> str:
    return "—" if v is None else f"{v}"


def compare_markdown(rows: list[dict], meta: dict) -> str:
    commit = meta.get("scorer_commit") or ""
    at = (f" at commit {commit[:12]}" + (" (uncommitted changes)" if meta.get("scorer_dirty") is True else "")
          if commit else "")
    out = ["# Re-scored: old and new numbers", "",
           f"Scorer {meta['scorer']}{at}; runs {', '.join(meta['runs'])}. Old numbers are the stored scores "
           "of the same records (after resumed and superseded copies are dropped); new numbers are "
           "this scorer on the same captured output and the current answer key.", ""]
    if meta.get("stub"):
        out += ["> Made from the scripted stub engine: these numbers say nothing about any model.", ""]
    for gap, about in sorted(meta.get("known_gaps", {}).items()):
        out += [f"> Known gap ({gap}): {about}.", ""]
    if meta.get("native_writes_dropped"):
        out += [f"Native writes taken out as replays (a later copy of an action id in the same trial home): "
                f"{meta['native_writes_dropped']}.", ""]
    if meta.get("not_rescored"):
        out += [f"Not re-scored (left out of the new numbers): {meta['not_rescored']} record(s); "
                "see not_rescored.jsonl.", ""]
    for r in rows:
        out += [f"## {r['entry']} on {r['profile']}", "", "| | old | new | change |", "|---|---|---|---|"]
        for x in r["numbers"]:
            out.append(f"| {x['what']} | {_f(x['old'])} | {_f(x['new'])} | {_f(x['delta'])} |")
        out += ["", "Records per surface: " + "; ".join(
            f"{s} " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())) for s, c in r["records"].items()), ""]
    return "\n".join(out).rstrip() + "\n"


def _headline(r: dict):
    """The number a record contributes (privacy records: their leaks)."""
    sc = r.get("score") or {}
    return (r.get("privacy") or {}).get("leaks") if sc.get("unscored") else sc.get("score")


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


# ------------------------------------------------------------------ entry point
def refuse_out(out: Path, run_dirs: list[Path]) -> None:
    """--out must be a new or empty folder outside the run folders and outside this repository,
    so a re-score can never overwrite a run (its own or another one)."""
    o = Path(out).resolve()
    for d in run_dirs:
        d = Path(d).resolve()
        if o == d or o.is_relative_to(d):
            raise SystemExit(f"--out {out} is inside the run folder {d}: run folders are never modified")
    if o == ROOT or o.is_relative_to(ROOT):
        raise SystemExit(f"--out {out} is inside the repository; write re-scored runs outside it")
    if o.exists():
        if not o.is_dir():
            raise SystemExit(f"--out {out} is a file")
        if (o / "results.jsonl").exists() or (o / "meta.json").exists():
            raise SystemExit(f"--out {out} holds a run (results.jsonl or meta.json); choose a new folder")
        if any(o.iterdir()):
            raise SystemExit(f"--out {out} is not empty; choose a new or empty folder")


def rescore(run_dirs: list[Path], out: Path, overrides: dict[str, Path] | None = None,
            write_findings: bool = True) -> dict:
    out = Path(out)
    refuse_out(out, run_dirs)
    loaded, metas, ids, skipped = load_runs(run_dirs)
    if not loaded:
        raise SystemExit("no run with records")
    commit, dirty = scorer_checkout()
    gaps: dict = {}
    native_dropped = 0
    cleaned = []
    for rows, meta in zip(loaded, metas):
        run_gaps = known_faults.run_gaps(meta)
        gaps.update(run_gaps)
        if "native_writes_unanswered" in run_gaps:
            rows, n = known_faults.drop_native_replays(rows)
            native_dropped += n
        cleaned.append(rows)
    old_sets = findings.drop_superseded(findings.drop_resumed(loaded, metas), metas)[0]
    new_sets, superseded = findings.drop_superseded(findings.drop_resumed(cleaned, metas), metas)
    old_records = [r for rows in old_sets for r in rows]

    overrides = overrides or {}
    pids = sorted({r["profile"] for rows in new_sets for r in rows})
    ctxs: dict[str, ProfileCtx | None] = {}
    used = []
    for pid in pids:
        path = _profile_path(pid, metas, overrides)
        if path is None:
            ctxs[pid] = None
            continue
        prof = load_profile(path)
        if prof.data.get("split") == "heldout" or prof.data.get("heldout"):
            raise findings.HeldOut(f"profile {pid} is held out; it is never re-scored here")
        ctxs[pid] = ProfileCtx(prof, _explicit_cases(pid, metas))
        used.append({"id": pid, "path": str(path)})

    new_records, failed = [], []
    changes: dict = defaultdict(lambda: defaultdict(Counter))
    for rows, rid in zip(new_sets, ids):
        last_brief: dict = {}
        for r in rows:
            k = (r.get("entry") or r.get("model"), r["profile"])
            ctx = ctxs.get(r["profile"])
            try:
                if ctx is None:
                    raise NotRescorable(f"profile {r['profile']} not found")
                n = rescore_record(r, ctx, last_brief)
            except NotRescorable as exc:
                failed.append({"run": rid, "entry": k[0], "profile": k[1], "surface": r.get("surface"),
                               "case": r.get("case"), "trial": r.get("trial"), "now": r.get("now"),
                               "why": str(exc)})
                changes[k][r.get("surface")]["not re-scored"] += 1
                continue
            n["old_score"] = r.get("score")
            n["rescored_from"] = {"run": rid, "scorer": r.get("scorer") or summary.OLD_SCORER}
            n["scorer"] = summary.SCORER_VERSION
            n["scorer_commit"] = commit
            known_faults.stamp(n)
            known_faults.stamp_writes(n, ctx.profile)        # clears an old stamp and recomputes it
            if n.get("native_replays_dropped"):
                changes[k][r.get("surface")]["native replays taken out"] += 1
            changes[k][r.get("surface")]["same" if _same(n["score"], r.get("score")) else
                                         "detail changed" if _same(_headline(n), _headline(r)) else
                                         "score changed"] += 1
            new_records.append(n)

    out.mkdir(parents=True, exist_ok=True)
    (out / "results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in new_records))
    (out / "not_rescored.jsonl").write_text("".join(json.dumps(x) + "\n" for x in failed))
    refs = list(dict.fromkeys([*(metas[0].get("references") or []), *findings.REFERENCES]))
    meta = {"run": f"rescored-{'+'.join(ids)}", "title": summary.TITLE + ": re-scored", "scorer": summary.SCORER_VERSION,
            "scorer_commit": commit, "scorer_dirty": dirty,
            "harness": summary.SCORER_VERSION, "runs": ids, "skipped": skipped, "profiles": used,
            "references": refs, "known_gaps": gaps, "superseded_dropped": superseded,
            "native_writes_dropped": native_dropped, "not_rescored": len(failed),
            "stub": any(r.get("stub") for r in new_records),
            NATIVE_MARK: all(m.get(NATIVE_MARK) for m in metas)}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    new_rows = summary.compare(summary.summarize(new_records), refs)
    old_rows = summary.compare(summary.summarize(old_records), refs)
    (out / "summary.json").write_text(json.dumps(new_rows, indent=1))
    (out / "summary.md").write_text(summary.markdown(new_rows, refs) + "\n")
    cmp_rows = compare_rows(old_rows, new_rows, changes)
    (out / "compare.json").write_text(json.dumps({"meta": meta, "rows": cmp_rows}, indent=1))
    md = compare_markdown(cmp_rows, meta)
    (out / "compare.md").write_text(md)
    if write_findings and new_records:
        findings.write(out, profiles={u["id"]: Path(u["path"]) for u in used})
    return {"meta": meta, "compare": cmp_rows, "markdown": md, "not_rescored": failed}


NATIVE_MARK = known_faults.NATIVE_FIXED_MARK


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+", help="run folders (each with results.jsonl and meta.json)")
    ap.add_argument("--out", required=True, help="where to write; never inside a run folder")
    ap.add_argument("--profile", action="append", default=[], metavar="ID=PATH",
                    help="use this profile.json for a profile id")
    ap.add_argument("--no-findings", action="store_true", help="skip the findings page")
    args = ap.parse_args(argv)
    overrides = {}
    for p in args.profile:
        pid, _, path = p.partition("=")
        if not path:
            ap.error("--profile needs ID=PATH")
        overrides[pid] = Path(path)
    res = rescore([Path(x) for x in args.runs], Path(args.out), overrides, write_findings=not args.no_findings)
    print(res["markdown"])
    for x in res["not_rescored"][:20]:
        print(f"not re-scored: {x['surface']} {x['case']} trial {x['trial']}: {x['why']}")


if __name__ == "__main__":
    main()
