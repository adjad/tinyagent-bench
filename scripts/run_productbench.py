#!/usr/bin/env python3
"""tinyagent-bench in-assistant evaluation: judge a model inside Harbor on Ask, Brief and Day.

For each model entry, profile and trial this starts a fresh isolated Harbor
(backend on 8775, sandbox on 8766, a scratch HARBOR_HOME, the clock pinned to
the profile's "now"), loads the profile's world, captures each surface and
scores it against the profile's ledger. The Study tab is never touched.

Model entries live in test_fixtures/productbench/models.json: local models on
the Mac's oMLX, and cloud references through OpenRouter (Harbor's remote mode,
shown apart and never ranked with local models). Two references are pinned
(the tuned Gemma-Wisp first, base Gemma 4 E4B second); every other entry is
shown as a difference from each, per surface and profile.

    # dry run with the scripted stub engine (no model, no oMLX, no network)
    python scripts/run_productbench.py --model Gemma-Wisp --profile standin \
        --stub-script test_fixtures/productbench/standin/stub.json --trials 1

    # a cloud reference, still on the stub (exercises remote mode and spend accounting)
    python scripts/run_productbench.py --model cloud/claude-haiku-5.5 --profile standin \
        --stub-script test_fixtures/productbench/standin/stub.json --trials 1 --max-spend-usd 1

    # real runs: see the live-run plan in docs/benchmark/08-product-eval-design.md
    python scripts/run_productbench.py --model Gemma-Wisp --profile <profile.json> --trials 3
    python scripts/run_productbench.py --model cloud/gemini-4-carbon --profile <profile.json> \
        --cloud-ok --max-spend-usd 5 --reference-run <pinned run dir>

    # re-summarise an existing run (optionally against pinned reference runs)
    python scripts/run_productbench.py --report <run dir> [--reference-run <dir> ...]

Output (one directory per run, id edat-iae-<time>): results.jsonl (one record
per entry, profile, surface, case and trial, with the captured output),
usage.jsonl per trial home, meta.json, summary.json and summary.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Nothing in this process should import service.*, but if anything ever does, its
# import-time stores must open in a scratch home, never the real ~/.moe.
import tempfile  # noqa: E402
os.environ["HARBOR_HOME"] = tempfile.mkdtemp(prefix="productbench-runner-")
sys.stdout.reconfigure(line_buffering=True)

import httpx  # noqa: E402

from scripts.productbench import cases as case_files  # noqa: E402
from scripts.productbench import models as model_files  # noqa: E402
from scripts.productbench import known_faults  # noqa: E402
from scripts.productbench import loader as world_loader  # noqa: E402
from scripts.productbench import autonomy, long_session, preferences, privacy, questions, simday  # noqa: E402
from scripts.productbench import engine_prep  # noqa: E402
from scripts.productbench import score_ask, score_brief, score_day, summary  # noqa: E402
from scripts.productbench.capture import Bench, TeeTransport  # noqa: E402
from scripts.productbench.profile import load as load_profile  # noqa: E402

SURFACES = ("ask", "brief", "day", "pref", "long", "act")
DEFAULT_SURFACES = "ask,brief,day,pref,act"
HARNESS_VERSION = summary.SCORER_VERSION     # edat-iae-0.3.3: audit fixes, key strings scoped, two Brief figures, one Day mistake once, Day plans read by day and clause
RUN_PREFIX = "edat-iae"


class SpendLimit(RuntimeError):
    pass


PREFLIGHT_STUB = ROOT / "test_fixtures" / "productbench" / "edat_stub.json"


def calendar_problems(profile) -> list[str]:
    """Calendar rows Harbor's sync would refuse: an end before the start (no process started)."""
    world = world_loader.build_world(profile)
    rows = (world.get("state") or world).get("calendar") or {}
    return [f"{profile.id}: {r['id']} ends before it starts"
            for r in rows.values()
            if r.get("end_ts_off") is not None and r["end_ts_off"] < r["ts_off"]]


def sync_preflight(profiles: list, args) -> dict:
    """Before any model is touched: each profile's isolated backend, with the stand-in engine,
    must take every sandbox source. Returns {profile id: sync counts or an error}."""
    out = {}
    scratch = Path(tempfile.mkdtemp(prefix="preflight-", dir=os.environ.get("TMPDIR")))
    for profile in profiles:
        problems = calendar_problems(profile)
        if problems:
            out[profile.id] = {"ok": False, "error": "; ".join(problems)}
            continue
        bench = Bench(profile, "preflight-stand-in", scratch / profile.id, port=args.port,
                      sandbox_port=args.sandbox_port, engine_port=args.engine_port,
                      stub_script=str(PREFLIGHT_STUB))
        (scratch / profile.id).mkdir(parents=True, exist_ok=True)
        try:
            bench.start()
            out[profile.id] = {"ok": True, "sync": bench.startup.get("sync")}
        except (RuntimeError, SystemExit) as exc:
            out[profile.id] = {"ok": False, "error": str(exc)[:600]}
        finally:
            bench.stop()
    return out


class StopRun(RuntimeError):
    """A stop condition held between trials (host memory, the error share)."""


class Terminated(BaseException):
    """SIGTERM or SIGHUP: unwind so every `finally` runs (the trial's servers stop) and the run
    folder records why it stopped. A BaseException, so no `except Exception` swallows it."""

    def __init__(self, signum: int):
        super().__init__(f"terminated by signal {signal.Signals(signum).name}")
        self.signum = signum


def install_signal_handlers() -> list[int]:
    """Turn SIGTERM, and SIGHUP unless it is ignored (a nohup run keeps running when its terminal
    goes away), into Terminated. Returns the signals handled."""
    def handler(signum, frame):
        raise Terminated(signum)
    handled = []
    for sig in (signal.SIGTERM, signal.SIGHUP):
        if sig == signal.SIGHUP and signal.getsignal(sig) == signal.SIG_IGN:
            continue
        signal.signal(sig, handler)
        handled.append(sig)
    return handled


def inside_repo(path: Path | str | None) -> bool:
    if not path:
        return False
    try:
        Path(path).expanduser().resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def refuse_inside_repo(out: str | None, tmpdir: str | None) -> str | None:
    """Why a live run must not start: results or scratch homes (the preflight's too) would land
    inside the repository. None when both are outside it."""
    for what, path in (("--out", out), ("$TMPDIR", tmpdir)):
        if inside_repo(path):
            return f"{what} {path} is inside the repository; results and scratch homes stay outside it"
    return None


def engine_fault(rec: dict) -> bool:
    """A record the engine or transport failed (it says nothing about the model)."""
    return bool((rec.get("captured") or {}).get("error"))


def all_faults(log: Path, n: int) -> bool:
    """Whether the last `n` records written are every one an engine fault (n of 0 is not)."""
    if n <= 0:
        return False
    try:
        rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        return False
    tail = rows[-n:]
    return len(tail) == n and all(engine_fault(r) for r in tail)


def act_engine_fault(usage: dict | None, cards: list, resident: dict | None, model: str, *, stub: bool) -> str | None:
    """Why an act-for-me record cannot be judged: its Brief made no engine call, showed no card, or
    the engine did not hold the model. Such a record is excluded, never read as "missed it all"."""
    if not (usage or {}).get("calls"):
        return "the Brief made no engine call"
    if not cards:
        return "the Brief showed no card"
    if not stub and resident is not None and model not in (resident.get("loaded") or []):
        return f"the engine did not hold {model}"
    return None


MAX_ERROR_SHARE = 0.02          # as the agentic spec: over 2% excluded trials invalidates a run
MIN_FREE_PCT = 15               # memory_pressure's free percentage below this stops the run


def memory_free_pct() -> int | None:
    """macOS memory_pressure's system-wide free percentage (read-only), or None."""
    try:
        out = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"free percentage:\s*(\d+)%", out)
    return int(m.group(1)) if m else None


def check_stop(log: Path, *, stub: bool, min_free: int = MIN_FREE_PCT, max_errors: float = MAX_ERROR_SHARE,
               free_pct=memory_free_pct) -> str | None:
    """Why the run must stop now, or None. Checked between trials."""
    pct = None if stub else free_pct()
    if pct is not None and pct < min_free:
        return f"memory pressure: {pct}% free, under {min_free}%"
    try:
        rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        rows = []
    errors = sum(1 for r in rows if (r.get("captured") or {}).get("error"))
    if len(rows) >= 50 and errors / len(rows) > max_errors:
        return f"engine or transport errors in {errors} of {len(rows)} records, over {max_errors:.0%}"
    return None


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def sandbox_marks(root: Path = ROOT) -> dict:
    """Run-meta marks read from the checkout's code (the meta's `git_sha`), so no run needs one set
    by hand: whether its sandbox answers Calendar and Reminders writes (known_faults.py)."""
    return {known_faults.NATIVE_FIXED_MARK: known_faults.sandbox_answers_native_writes(root)}


def _sysctl(name: str) -> str | None:
    try:
        out = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def device_info() -> dict:
    """The machine a result came from (hardware facts only, nothing personal)."""
    mem = _sysctl("hw.memsize")
    try:
        os_version = subprocess.run(["sw_vers", "-productVersion"], capture_output=True,
                                    text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        os_version = None
    return {"model": _sysctl("hw.model"), "chip": _sysctl("machdep.cpu.brand_string"),
            "memory_gb": round(int(mem) / 2**30) if mem and mem.isdigit() else None,
            "arch": platform.machine(), "os": "macOS " + os_version if os_version else platform.platform()}


def runtime_info(entry, *, stub: bool, engine_port: int) -> dict:
    if stub:
        return {"engine": "stub", "note": "scripted stub engine; not a model",
                "as_if": "openrouter" if entry.cloud else "omlx"}
    if entry.cloud:
        return {"engine": "openrouter", "origin": "https://openrouter.ai",
                "harbor_mode": "remote-only with remote_parity (Settings > Cloud > Everything)"}
    info: dict = {"engine": "omlx", "port": engine_port, "version": None}
    for path in ("/health", "/v1/health"):
        try:
            r = httpx.get(f"http://127.0.0.1:{engine_port}{path}", timeout=3)
            if r.status_code == 200 and isinstance(r.json(), dict):
                info["version"] = r.json().get("version")
                break
        except (httpx.HTTPError, ValueError):
            continue
    return info


def _record(out: Path, rec: dict) -> None:
    known_faults.stamp(rec)          # inputs the harness or sandbox got wrong, from the record's own tool results
    rec.setdefault("scorer", summary.SCORER_VERSION)     # never mix old and new scores silently
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class Spend:
    """Cloud spend for the run: the provider's reported cost, else tokens x the entry's price.
    When neither is known the limit cannot be enforced, so the run stops. Stub calls count
    at the entry's price when it has one (so a dry run exercises the limit) and are
    otherwise free."""

    def __init__(self, limit: float | None):
        self.limit = limit
        self.spent = 0.0

    def add(self, entry, usage: dict, *, stub: bool = False) -> None:
        if not entry.cloud or self.limit is None or not usage.get("calls"):
            return
        cost = usage.get("cost_reported_usd")
        if cost is None:
            cost = entry.cost_usd(usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0)
        if cost is None:
            if stub:
                return
            raise SpendLimit(f"{entry.id}: no reported cost and no price in the models file; "
                             "the spend limit cannot be enforced")
        self.spent += cost
        if self.spent > self.limit:
            raise SpendLimit(f"spend limit ${self.limit:.2f} reached (${self.spent:.4f})")


class Ctx:
    """Per-profile helpers shared by every trial: privacy rules, preference cases, long session."""

    def __init__(self, profile, cases: dict):
        self.profile = profile
        self.entries = profile.data.get("edat_entries") or []
        self.directory = privacy.Directory.of(profile)
        self.rules = privacy.rules_from_ledger(self.entries)
        self.rule_by_id = {r.id: r for r in self.rules}
        self.entry_by_id = {e["id"]: e for e in self.entries}
        self.cases_by_id = {c["id"]: c for c in cases.get("ask", [])}
        self.pref_cases = preferences.build_cases(profile, self.entries, cases.get("ask", []))
        self.brief_prefs = preferences.brief_entries(self.entries)
        self.long = long_session.build(profile, cases.get("ask", []), self.entries) if self.entries else None

    def leaks(self, turn: dict, case_id: str) -> dict:
        items = privacy.outbound_of_turn(turn, self.directory, case=case_id)
        return {"outbound": len(items), "leaks": privacy.check(items, self.rules),
                "items": [o.as_dict() for o in items]}, items


def _compact(turn: dict) -> dict:
    keep = ("prompt", "answer", "tools", "tool_calls", "confirms", "drafts", "actions", "error",
            "elapsed_s", "first_token_s", "bench_now", "usage", "model")
    return {k: turn.get(k) for k in keep}


async def run_trial(bench: Bench, cases: dict, surfaces: list[str], base: dict, log: Path,
                    only: set[str] | None, spend: Spend, ctx: Ctx, *, at: str | None = None) -> int:
    """One clock setting of one trial. Without `at`: Brief, Day tab, Ask, Day plans, preferences,
    the long session; with `at`: only the cases asked at that time."""
    profile, entry = bench.profile, base["_entry"]
    rec_base = {k: v for k, v in base.items() if not k.startswith("_")}
    rec_base["now"] = at or profile.data["anchor"][11:16]
    rules = dict(cases.get("day", {}).get("rules") or {})
    if at:
        rules["now"] = at.split()[-1]
    n = 0

    def write(rec: dict) -> None:
        nonlocal n
        known_faults.stamp_writes(rec, profile)      # a move or cancel a real Mac would refuse
        _record(log, rec)
        n += 1
        usage = (rec.get("captured") or {}).get("usage") or rec.get("usage") or {}
        spend.add(entry, usage, stub=rec_base["stub"])

    def wanted(case: dict) -> bool:
        return (not only or case["id"] in only) and case.get("at") == at

    tee = TeeTransport()
    async with httpx.AsyncClient(transport=tee) as client:

        async def ask(cid, prompt, session_id=None):
            turn = await bench.ask(client, tee, cid, prompt, session_id=session_id)
            pv, items = ctx.leaks(turn, cid)
            return turn, pv, items

        warm = await bench.ask(client, tee, "warmup", "What time is it?")     # not scored
        spend.add(entry, warm.get("usage") or {}, stub=rec_base["stub"])
        first_brief = None
        if not (at is None and "brief" in surfaces) and {"ask", "day"} & set(surfaces):
            # Harbor analyses mail and texts in its Brief cycle. A home that asks without a scored
            # Brief first (each pinned clock) runs one unscored, so the model sees what the
            # assistant would have read by then. Without it, every tool there says "Mail and
            # texts have not been analyzed yet".
            cap = await bench.brief(client)
            spend.add(entry, cap.get("usage") or {}, stub=rec_base["stub"])
            bench.startup["analysis_brief"] = {"elapsed_s": cap["elapsed_s"],
                                               "calls": (cap.get("usage") or {}).get("calls")}
        if at is None and "brief" in surfaces:
            cap = await bench.brief(client)
            first_brief = cap["snapshot"]
            sc = score_brief.score_brief(cap["snapshot"], profile, cases.get("brief", {}))
            items = privacy.outbound_of_brief(cap["snapshot"], ctx.directory)
            pv = {"outbound": len(items), "leaks": privacy.check(items, ctx.rules),
                  "items": [o.as_dict() for o in items]}
            write({**rec_base, "surface": "brief", "case": "brief", "captured": cap, "score": sc.as_dict(),
                   "privacy": pv})
            bt = questions.brief_todos(cap["snapshot"], profile)
            write({**rec_base, "surface": "question", "case": "TQ-brief-todos", "family": "todo",
                   "captured": {"todos": cap["snapshot"].get("todos") or []}, "score": bt})
            print(f"  brief  {100 * sc.score:5.1f}  recall={sc.recall['rate']} "
                  f"hidden={sc.hidden_recall['rate']} noise={len(sc.false_alarms)} ({cap['elapsed_s']}s)"
                  f"{' LEAK x' + str(len(pv['leaks'])) if pv['leaks'] else ''}")
        if at is None and "day" in surfaces:
            cap = await bench.day_tab(client)
            blocks = score_day.blocks_from_today(cap["snapshot"] or {}, profile)
            sc = score_day.score_plan(blocks, profile, rules)
            write({**rec_base, "surface": "day_tab", "case": "day_tab", "captured": cap,
                   "score": sc.as_dict(), "model_independent": True})
            print(f"  daytab {100 * sc.score:5.1f}  blocks={len(blocks)} violations={len(sc.violations)}")
        if "ask" in surfaces:
            for case in cases.get("ask", []):
                if not wanted(case):
                    continue
                turn, pv, items = await ask(case["id"], case["prompt"])
                if case.get("question"):
                    qs = questions.score_question(case, turn["answer"], profile)
                    ask_view = score_ask.score_case({"id": case["id"], "check_invented": False}, turn, profile)
                    qs["unsafe"] = ask_view.unsafe
                    write({**rec_base, "surface": "question", "case": case["id"], "family": case["family"],
                           "prompt": case["prompt"], "captured": turn, "score": qs, "privacy": pv})
                    c = qs.get("counts") or {}
                    shown = "  —  " if qs["score"] is None else f"{100 * qs['score']:5.1f}"
                    print(f"  {case['id']:<16} {shown}  found={c.get('found')} missed={c.get('missed')} "
                          f"wrong={c.get('wrongly_raised')} stale={c.get('stale')}")
                    continue
                if case.get("privacy"):
                    rule = ctx.rule_by_id.get(case["privacy"]["rule"])
                    outcome = (privacy.wrong_recipient_outcome(items, rule)
                               if rule and rule.kind == "wrong_recipient" else None)
                    write({**rec_base, "surface": "privacy", "case": case["id"], "family": "privacy",
                           "prompt": case["prompt"], "captured": turn, "privacy": pv,
                           "privacy_outcome": outcome, "score": {"unscored": True}})
                    print(f"  {case['id']:<6} privacy  outbound={pv['outbound']} leaks={len(pv['leaks'])}"
                          f"{' ' + outcome if outcome else ''}")
                    continue
                sc = score_ask.score_case(case, turn, profile)
                write({**rec_base, "surface": "ask", "case": case["id"], "family": case.get("family"),
                       "prompt": case["prompt"], "captured": turn, "score": sc.as_dict(), "privacy": pv})
                _print_ask(case["id"], sc, turn, pv)
                fu = case.get("followup")
                if fu:
                    t2, pv2, _ = await ask(fu["id"], fu["prompt"], session_id=turn["session_id"])
                    s2 = score_ask.score_case(fu, t2, profile)
                    write({**rec_base, "surface": "ask", "case": fu["id"], "family": "followup",
                           "prompt": fu["prompt"], "captured": t2, "score": s2.as_dict(), "follows": case["id"],
                           "privacy": pv2})
                    _print_ask(fu["id"], s2, t2, pv2)
        if "day" in surfaces:
            for case in cases.get("day", {}).get("plans", []):
                if not wanted(case):
                    continue
                turn, pv, _ = await ask(case["id"], case["prompt"])
                if case.get("kind") == "slot":
                    sc = score_day.score_slot(turn["answer"], profile, {**case, "now": at} if at else case)
                else:
                    sc = score_day.score_plan(score_day.parse_plan(turn["answer"], profile), profile, rules)
                ask_view = score_ask.score_case({"id": case["id"], "check_invented": False}, turn, profile)
                score = {**sc.as_dict(), "unsafe": ask_view.unsafe, "tools": ask_view.tools,
                         "readability": ask_view.readability}
                write({**rec_base, "surface": "day", "case": case["id"], "family": case.get("kind"),
                       "prompt": case["prompt"], "captured": turn, "score": score, "privacy": pv})
                print(f"  {case['id']:<6} {100 * sc.score:5.1f}  blocks={len(sc.blocks)} "
                      f"violations={[v['rule'] for v in sc.violations]}")
        if at is None and "pref" in surfaces:
            for pc in ctx.pref_cases:
                if only and pc["id"] not in only:
                    continue
                write(await _pref_case(pc, ask, ctx, profile, rec_base))
            stored, stated = ctx.brief_prefs
            if first_brief is not None:
                text, first = preferences.brief_text(first_brief)
                for e in stored:
                    res = preferences.check_text(e, text, first_item=first, profile=profile)
                    write({**rec_base, "surface": "pref", "case": f"PF-{e['id']}-brief", "pref_case": True,
                           "ledger_entry": e["id"], "pref": e["expected"]["pref"], "dimension": "direct",
                           "credits": ["persistence"], "arrival": "stored", "on": "brief",
                           "score": {"score": None if res["ok"] is None else float(res["ok"]), **res}})
            if stated and "brief" in surfaces and (not only or any(f"PF-{e['id']}-brief" in only for e in stated)):
                session = None
                said = []
                for e in stated:
                    turn, pv, _ = await ask(f"PF-{e['id']}-say", e["expected"]["say"], session_id=session)
                    session = turn["session_id"]
                    said.append(_compact(turn))
                cap = await bench.brief(client)
                text, first = preferences.brief_text(cap["snapshot"])
                for e in stated:
                    res = preferences.check_text(e, text, first_item=first, profile=profile)
                    write({**rec_base, "surface": "pref", "case": f"PF-{e['id']}-brief", "pref_case": True,
                           "ledger_entry": e["id"], "pref": e["expected"]["pref"], "dimension": "direct",
                           "credits": ["persistence"], "arrival": "stated", "on": "brief_after_statement",
                           # the Brief it was checked against, so the check can be re-scored from the record
                           "captured": {"said": said, "snapshot": cap["snapshot"],
                                        "brief_elapsed_s": cap["elapsed_s"], "usage": cap["usage"]},
                           "score": {"score": None if res["ok"] is None else float(res["ok"]), **res}})
        if at is None and "long" in surfaces and ctx.long:
            write(await _long_session(ctx, ask, profile, rec_base))
    return n


async def run_act_trial(bench: Bench, plan: dict, base: dict, log: Path, spend: Spend) -> int:
    """Act-for-me on (autonomy.py): new messages arrive after the yes (or the profile stands, when
    its key assumes an earlier yes), one Brief runs, and every action Harbor took by itself is
    read from its own store and judged. No Ask turn runs, so every sandbox write is background."""
    profile, entry = bench.profile, base["_entry"]
    rec_base = {k: v for k, v in base.items() if not k.startswith("_")}
    rec_base["now"] = profile.data["anchor"][11:16]
    async with httpx.AsyncClient() as client:
        posted = (await bench.arrive(client, autonomy.arrival_posts(plan["expectations"]))
                  if plan["mode"] == "arrivals" else [])
        cap = await bench.brief(client)
        spend.add(entry, cap.get("usage") or {}, stub=rec_base["stub"])
        await asyncio.sleep(2.0)
        done = autonomy.read_done(bench.home.harbor_home)
        passed_over = autonomy.read_notes(bench.run_dir / "act_notes.jsonl")
        state = await bench.act_state(client)
        writes = await bench.actions(client)
    result = autonomy.judge(done, writes, plan["expectations"], profile, yes_at=plan["yes_at"])
    result.update({"mode": plan["mode"], "expectations": len(plan["expectations"]), "passed_over": passed_over})
    cards = [{"title": c.get("title"), "kind": (c.get("action") or {}).get("method")}
             for c in (cap["snapshot"].get("cards") or [])]
    fault = act_engine_fault(cap.get("usage"), cards, simday.engine_resident(bench.engine_port),
                             bench.model, stub=rec_base["stub"])
    _record(log, known_faults.stamp_writes({**rec_base, "surface": "act", "case": "act-for-me", "family": "act_for_me",
                  "captured": {"mode": plan["mode"], "yes_at": plan["yes_at"], "arrivals": autonomy.arrival_posts(
                      plan["expectations"]), "posted": posted, "done": done, "act_state": state,
                      "background_writes": writes, "brief_cards": cards, "brief_elapsed_s": cap["elapsed_s"],
                      "usage": cap.get("usage"), "error": fault},
                  "score": result}, profile))
    c = result["counts"]
    print(f"  act    {'—' if result['score'] is None else round(100 * result['score'], 1)}  right={c['right']} "
          f"wrong={c['done_wrongly']} should_not={c['should_not']} missed={c['missed']} "
          f"unsafe={len(result['unsafe'])} ({plan['mode']})")
    return 1


async def _pref_case(pc: dict, ask, ctx: Ctx, profile, rec_base: dict) -> dict:
    e = ctx.entry_by_id[pc["entry"]]
    session, turns, results, leaks, outbound = None, [], [], [], 0
    usage_calls = 0
    for i, t in enumerate(pc["turns"]):
        turn, pv, items = await ask(f"{pc['id']}-{i + 1}", t["prompt"], session_id=session)
        session = turn["session_id"]
        leaks += pv["leaks"]
        outbound += pv["outbound"]
        usage_calls += (turn.get("usage") or {}).get("calls") or 0
        chk = t.get("check")
        res = None
        if chk == "pref":
            res = (preferences.check_text(e, turn["answer"], profile=profile)
                   if (e["expected"].get("check") or {}).get("on", "answer_text") == "answer_text"
                   else preferences.check_tools(e, turn, profile, items))
        elif chk == "pref_plan":
            blocks = score_day.parse_plan(turn["answer"], profile)
            res = preferences.check_plan(e, blocks, turn["answer"], profile)
        elif chk == "no_waiver":
            res = preferences.check_no_waiver(turn)
        elif chk and chk in {v for _, v in preferences.OVERRIDES.values()}:
            res = preferences.check_inverted(chk, turn["answer"])
        elif chk:
            res = preferences.check_scope(chk, turn, profile, items, e)
        if res is not None:
            results.append(res)
        turns.append({**_compact(turn), "check": chk, "result": res})
    oks = [r["ok"] for r in results]
    ok = None if not oks or any(o is None for o in oks) else all(oks)
    print(f"  {pc['id']:<26} pref {pc['pref']:>2} {pc['dimension']:<16} "
          f"{'unchecked' if ok is None else ('pass' if ok else 'FAIL')}{' LEAK' if leaks else ''}")
    usage = {"calls": usage_calls}
    for k in ("prompt_tokens", "completion_tokens", "cost_reported_usd"):
        vals = [(t.get("usage") or {}).get(k) for t in turns]
        usage[k] = sum(v for v in vals if isinstance(v, (int, float))) if any(
            isinstance(v, (int, float)) for v in vals) else None
    return {**rec_base, "surface": "pref", "case": pc["id"], "pref_case": True, "ledger_entry": pc["entry"],
            "pref": pc["pref"], "dimension": pc["dimension"], "credits": pc.get("credits", []),
            "arrival": pc["arrival"], "on": pc["surface"], "captured": {"turns": turns, "usage": usage},
            "privacy": {"outbound": outbound, "leaks": leaks},
            "score": {"score": None if ok is None else float(ok), "checks": results}}


async def _long_session(ctx: Ctx, ask, profile, rec_base: dict) -> dict:
    sess = ctx.long
    session, results, leaks, outbound, compact = None, [], [], 0, []
    t0 = time.monotonic()
    for t in sess["turns"]:
        turn, pv, items = await ask(f"{sess['id']}-{t['n']}", t["prompt"], session_id=session)
        session = turn["session_id"] or session
        leaks += pv["leaks"]
        outbound += pv["outbound"]
        row = {"n": t["n"], "role": t["role"], "band": t.get("band"),
               "prompt_tokens": (turn.get("usage") or {}).get("prompt_tokens_max"), "error": turn.get("error")}
        if t["role"] == "tool_task":
            case = ctx_case(ctx, t["case"])
            if case:
                sc = score_ask.score_case(case, turn, profile)
                row.update({"ok": sc.score >= 0.999, "score": sc.score, "case": t["case"]})
        elif t["role"] == "fact_probe":
            fact = next(f for f in sess["facts"] if f["id"] == t["fact"])
            row.update({"fact": t["fact"], "fact_outcome": long_session.score_fact(fact, turn["answer"], profile.today)})
        elif t["role"] == "pref_check":
            prs = []
            for eid in t.get("checks") or []:
                p = sess["_prefs"][eid]
                res = preferences.check_text(p["entry_obj"], turn["answer"], profile=profile)
                if res["ok"] is not None:
                    prs.append({"entry": eid, "pref": p["pref"], "ok": res["ok"],
                                "arrival": "stated" if "say" in p else "stored"})
            row["pref_results"] = prs
        results.append(row)
        compact.append({"n": t["n"], "role": t["role"], "prompt": t["prompt"], "answer": turn["answer"][:400],
                        "tools": turn["tools"], "usage": turn.get("usage"), "elapsed_s": turn.get("elapsed_s")})
    for row, flag in zip(results, long_session.drop_flags([r["prompt_tokens"] for r in results])):
        row["prompt_drop"] = flag
    summ = long_session.summarize(sess, results)
    print(f"  {sess['id']:<10} long  tool {summ['tool_early']}->{summ['tool_late']}  facts late "
          f"{summ['fact_retention_late']}  fabricated {summ['fabricated_late']}  prompt drops {summ['prompt_drops']}"
          f" ({round(time.monotonic() - t0)}s)")
    usage = {"calls": sum((c.get("usage") or {}).get("calls") or 0 for c in compact)}
    for k in ("prompt_tokens", "completion_tokens", "cost_reported_usd"):
        vals = [(c.get("usage") or {}).get(k) for c in compact]
        usage[k] = sum(v for v in vals if isinstance(v, (int, float))) if any(
            isinstance(v, (int, float)) for v in vals) else None
    return {**rec_base, "surface": "long", "case": sess["id"],
            "captured": {"turns": compact, "usage": usage, "facts": sess["facts"], "stated": sess["stated"],
                         "stored": sess["stored"], "pairs": sess["pairs"]},
            "privacy": {"outbound": outbound, "leaks": leaks},
            "score": {"turn_results": results, **summ}}


def ctx_case(ctx: Ctx, cid: str):
    return ctx.cases_by_id.get(cid)


def _print_ask(cid: str, sc, turn: dict, pv: dict | None = None) -> None:
    missing = [r["fact"] for r in sc.required if not r["found"]]
    print(f"  {cid:<6} {100 * sc.score:5.1f}  tools={turn['tools']} "
          f"{'missing=' + str(missing) if missing else ''}"
          f"{' contradicted' if sc.contradicted else ''}{' invented=' + str(sc.invented) if sc.invented else ''}"
          f"{' LEAK x' + str(len(pv['leaks'])) if pv and pv['leaks'] else ''}"
          f"{' ERROR ' + turn['error'] if turn.get('error') else ''}")


def _resolve(path: str) -> Path:
    p = Path(path)
    if p.exists():
        return p.resolve()
    for folder in ("bench_profiles", "productbench"):
        named = ROOT / "test_fixtures" / folder / path / "profile.json"
        if named.exists():
            return named
    sys.exit(f"no profile at {path}")


def _load_records(run_dir: Path) -> list[dict]:
    path = run_dir / "results.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def write_summary(run_dir: Path, recs: list[dict], refs: list[str], pinned: list[Path],
                  meta: dict | None = None) -> str:
    rows = summary.summarize(recs)
    if meta:
        for row in rows:
            row["safety"]["escapes"] = sum(len(t["escapes"]) for t in meta["trials_meta"]
                                           if t.get("entry", t.get("model")) == row["entry"] and t["profile"] == row["profile"])
    ref_rows = []
    for p in pinned:
        prow = summary.summarize(_load_records(p))
        for r in prow:
            r["run"] = p.name
        ref_rows += prow
    summary.compare(rows, refs, ref_rows)
    (run_dir / "summary.json").write_text(json.dumps(rows, indent=1))
    md = summary.markdown(rows, refs)
    (run_dir / "summary.md").write_text(md + "\n")
    return md


def check_cloud(chosen, *, stub: bool, cloud_ok: bool, max_spend: float | None) -> str | None:
    """Why a run with these entries must not start, or None."""
    cloud = [e for e in chosen if e.cloud]
    if not cloud:
        return None
    if max_spend is None:
        return "a cloud reference needs --max-spend-usd (even on the stub, to exercise the limit)"
    if stub:
        return None
    if not cloud_ok:
        return ("cloud references send the synthetic profile to OpenRouter and cost money: "
                "pass --cloud-ok once the user has said yes")
    unconfirmed = [e.id for e in cloud if not e.id_confirmed]
    if unconfirmed:
        return f"model ids not confirmed yet in the models file: {unconfirmed}"
    return None


def play_sim_day(args, chosen, run: str, run_dir: Path, device: dict, stub: bool) -> None:
    """One entry, one profile: play the scripted day against the isolated assistant."""
    if len(chosen) != 1 or len(args.profile) != 1:
        sys.exit("--sim-day takes exactly one --model and one --profile")
    entry = chosen[0]
    profile = load_profile(_resolve(args.profile[0]))
    cases, _ = case_files.for_profile(profile, args.cases)
    script = simday.default_script(profile, cases) if args.sim_day == "default" else simday.load(args.sim_day)
    if script.get("profile_id") not in (None, profile.id):
        sys.exit(f"the day script is for {script['profile_id']}, not {profile.id}")
    (run_dir / "sim_day_script.json").write_text(json.dumps(script, indent=1))
    engine_port = None if (stub or entry.cloud) else args.engine_port
    bench = Bench(profile, entry.model_id, run_dir / "home", port=args.port, sandbox_port=args.sandbox_port,
                  engine_port=args.engine_port, stub_script=args.stub_script, cloud=entry.cloud,
                  context_window=entry.context_window, credential_ref=args.credential_ref,
                  at=script.get("day_start", "08:00"), narration_thinking=entry.thinking == "narration-on")
    meta = {"run": run, "title": summary.TITLE + ": simulated day", "harness": HARNESS_VERSION,
            **sandbox_marks(),
            "git_sha": _git_sha(), "date": time.strftime("%Y-%m-%d"), "entry": entry.id, "kind": entry.kind,
            "variant": entry.variant(), "device": device, "stub": stub, "profile": profile.id,
            "pace": args.pace, "gap_s": args.gap_s, "scale": args.scale, "events": len(script["events"]),
            "runtime": runtime_info(entry, stub=stub, engine_port=args.engine_port),
            "log": "day_log.jsonl: one line per event and idle gap, epoch and bench-clock times"}
    print(f"{meta['title']}: {entry.id} / {profile.id}, {len(script['events'])} events, pace {args.pace} "
          f"-> {run_dir}")
    log = run_dir / "day_log.jsonl"
    meta["engine_resident_start"] = simday.engine_resident(engine_port)
    meta["started_epoch"] = time.time()
    try:
        bench.start()
        rows = asyncio.run(simday.play(
            bench, script, cases, log, pace=args.pace, scale=args.scale, gap_s=args.gap_s,
            engine_port=engine_port,
            on_event=lambda r: print(f"  {r['t']} {r['kind']:<14} {r.get('status')} {r.get('elapsed_s')}s")))
    finally:
        bench.stop()
    meta.update({"finished_epoch": time.time(), "summary": simday.summarize(rows), "escapes": bench.escapes(),
                 "startup": bench.startup, "engine_resident_end": simday.engine_resident(engine_port)})
    (run_dir / "day_meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta["summary"], indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", default=[], help="entry id (or model id); repeat for several")
    ap.add_argument("--models-file", default=None, help="model entries (default test_fixtures/productbench/models.json)")
    ap.add_argument("--reference", action="append", default=[],
                    help="reference entry for the differences (default: the models file's references)")
    ap.add_argument("--reference-run", action="append", default=[],
                    help="a pinned earlier run directory that holds reference results")
    ap.add_argument("--profile", action="append", default=[], help="profile.json path or a name "
                    "under test_fixtures/productbench/; repeat for several")
    ap.add_argument("--cases", default=None, help="question file (default: the profile's own set)")
    ap.add_argument("--supersedes", action="append", default=[], metavar="RUN_ID",
                    help="this run re-asks cases of an earlier run (its id, edat-iae-…): the findings page "
                         "uses these records instead of that run's records of the same cases")
    ap.add_argument("--first-trial", type=int, default=1,
                    help="number the trials from here (to resume: --first-trial 3 --trials 1 runs trial 3 only)")
    ap.add_argument("--surface", default=DEFAULT_SURFACES,
                    help="ask, brief, day, pref (standing preferences), long (the 60-turn session; heavy)")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--only", default="", help="comma list of case ids")
    ap.add_argument("--stub-script", default=None, help="run the scripted stub engine (dry run)")
    ap.add_argument("--preflight-only", action="store_true",
                    help="only check that every profile's sources sync into an isolated backend "
                         "with the stand-in engine; no model is used")
    ap.add_argument("--no-sync-preflight", action="store_true",
                    help="skip that check before a live run (it runs by default)")
    ap.add_argument("--cloud-ok", action="store_true",
                    help="allow real requests to OpenRouter (they cost money); needs --max-spend-usd")
    ap.add_argument("--max-spend-usd", type=float, default=None, help="stop the run past this cloud spend")
    ap.add_argument("--credential-ref", default="keychain:openrouter",
                    help="the Keychain item Harbor reads the OpenRouter token from (never the token)")
    ap.add_argument("--port", type=int, default=8775)
    ap.add_argument("--sandbox-port", type=int, default=8766)
    ap.add_argument("--engine-port", type=int, default=engine_prep.PORT,
                    help="the running oMLX the backend uses (local real runs only); the same default as "
                         "engine_prep ($TINYAGENT_ENGINE_PORT, else 8000)")
    ap.add_argument("--out", default=None, help="results root (default $TMPDIR/productbench)")
    ap.add_argument("--report", default=None, help="re-summarise a run directory")
    ap.add_argument("--sim-day", default=None, metavar="SCRIPT",
                    help="play a scripted day (edat-sim-day-v1 JSON, or 'default' to build one from the "
                         "profile's cases) and log each job's start and end for the power samples")
    ap.add_argument("--pace", default="compressed", choices=("real", "scaled", "compressed"))
    ap.add_argument("--gap-s", type=float, default=30.0, help="compressed pace: seconds between jobs")
    ap.add_argument("--scale", type=float, default=0.2, help="scaled pace: factor on every gap")
    ap.add_argument("--write-sim-day", default=None, metavar="PATH",
                    help="write the default day script for --profile to PATH and exit")
    args = ap.parse_args()
    install_signal_handlers()

    entries, file_refs = model_files.load(args.models_file)
    refs = args.reference or file_refs
    pinned = [Path(p) for p in args.reference_run]
    if args.report:
        run_dir = Path(args.report)
        meta_path = run_dir / "meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
        print(write_summary(run_dir, _load_records(run_dir), refs, pinned, meta))
        return
    if args.write_sim_day:
        profile = load_profile(_resolve(args.profile[0]))
        cases, _ = case_files.for_profile(profile, args.cases)
        Path(args.write_sim_day).write_text(json.dumps(simday.default_script(profile, cases), indent=1))
        print(f"wrote {args.write_sim_day}")
        return

    if not args.model or not args.profile:
        sys.exit("--model and --profile are required")
    inside = refuse_inside_repo(args.out, os.environ.get("TMPDIR"))
    if inside:
        sys.exit(inside + "; nothing was run")
    for pr in pinned:          # refuse now, not after hours of trials, if a pinned run is of an older scorer
        older = [v for v in summary.scorer_of(_load_records(pr)) if v != summary.SCORER_VERSION]
        if older:
            sys.exit(f"pinned reference run {pr} was scored by {', '.join(older)}; re-score it first "
                     "(python -m scripts.productbench.rescore <run dir> --out <new dir>) and pin that")
    surfaces = [s.strip() for s in args.surface.split(",") if s.strip()]
    if set(surfaces) - set(SURFACES):
        sys.exit(f"unknown surface in {surfaces}; choose from {SURFACES} (Study is not benchmarked)")
    try:
        chosen = [model_files.resolve(m, entries) for m in args.model]
    except model_files.ModelFileError as exc:
        sys.exit(str(exc))
    stub = bool(args.stub_script)
    refusal = check_cloud(chosen, stub=stub, cloud_ok=args.cloud_ok, max_spend=args.max_spend_usd)
    if refusal:
        sys.exit(refusal)
    preflight = None
    if args.preflight_only or (not stub and not args.no_sync_preflight):
        profiles_pf = [load_profile(_resolve(pp)) for pp in args.profile]
        preflight = sync_preflight(profiles_pf, args)
        for pid, res in preflight.items():
            print(f"preflight {pid}: " + (f"ok {res['sync']}" if res["ok"] else f"FAILED {res['error']}"))
        if not all(r["ok"] for r in preflight.values()):
            sys.exit("preflight failed: a profile's sources do not sync; nothing was run")
        if args.preflight_only:
            return
    if not stub and any(not e.cloud for e in chosen):
        try:
            listed = httpx.get(f"http://127.0.0.1:{args.engine_port}/v1/models", timeout=3).json()
        except (httpx.HTTPError, ValueError):
            sys.exit(f"no engine answers on :{args.engine_port}; this harness never starts one")
        available = [m.get("id") for m in (listed.get("data") or []) if isinstance(m, dict)]
        for e in chosen:
            if e.cloud:
                continue
            served = model_files.pick_served(e, available)
            if served is None:
                sys.exit(f"{e.id}: the engine lists none of {[e.model_id, *e.served_as]}; nothing was run")
            e.model_id = served

    run = f"{RUN_PREFIX}-{time.strftime('%Y%m%d-%H%M%S')}"
    root = Path(args.out) if args.out else Path(os.environ.get("TMPDIR", "/tmp")) / "productbench"
    run_dir = root / run
    run_dir.mkdir(parents=True, exist_ok=True)
    log = run_dir / "results.jsonl"
    only = {x.strip() for x in args.only.split(",") if x.strip()} or None
    device = device_info()
    if args.sim_day:
        play_sim_day(args, chosen, run, run_dir, device, stub)
        return
    meta = {"run": run, "title": summary.TITLE, "harness": HARNESS_VERSION, "git_sha": _git_sha(),
            "date": time.strftime("%Y-%m-%d"), "entries": [e.id for e in chosen], "references": refs,
            "pinned_reference_runs": [str(p) for p in pinned], "profiles": [], "surfaces": surfaces,
            "trials": args.trials, "stub": stub, "device": device, "language": "en",
            "max_spend_usd": args.max_spend_usd, "started": time.time(), "trials_meta": [],
            "sync_preflight": preflight,
            # whether the sandbox here answers Calendar/Reminders writes (else replays: known_faults.py)
            **sandbox_marks(),
            "supersedes": args.supersedes,
            "known_gaps": ({} if "act" in surfaces else {"act_not_run": known_faults.GAPS["act_not_run"]}),
            "sampling": "Harbor's own requests: model-only Ask sends no temperature, so a local model's "
                        "oMLX per-model profile applies; what each call actually sent is in usage.jsonl "
                        "and in every record's captured.usage.sampling_sent"}
    spend = Spend(args.max_spend_usd)
    print(f"{summary.TITLE}: run {run} -> {run_dir}")
    stopped = None
    try:
        for ppath in args.profile:
            profile = load_profile(_resolve(ppath))
            cases, sources = case_files.for_profile(profile, args.cases)
            if "ask" in surfaces and not args.cases:
                have = {c["id"] for c in cases.get("ask", [])}
                cases["ask"] += [c for c in questions.cases_for(profile) if c["id"] not in have]
            act_plan = autonomy.plan_for(profile) if "act" in surfaces else None
            times = sorted({c["at"] for c in cases.get("ask", []) + cases.get("day", {}).get("plans", [])
                            if c.get("at")})
            meta["profiles"].append({"id": profile.id, "path": str(_resolve(ppath)),
                                     "anchor": profile.data["anchor"], "timezone": profile.data["timezone"],
                                     "cases": sources, "ask_cases": len(cases.get("ask", [])),
                                     "later_clock_times": times})
            ctx = Ctx(profile, cases)
            meta["profiles"][-1].update({"preference_cases": len(ctx.pref_cases),
                                         "privacy_rules": len(ctx.rules),
                                         "long_session_turns": len(ctx.long["turns"]) if ctx.long else 0})
            for entry in chosen:
                runtime = runtime_info(entry, stub=stub, engine_port=args.engine_port)
                engine_port = None if (stub or entry.cloud) else args.engine_port
                for trial in range(args.first_trial, args.first_trial + args.trials):
                    passes = ([(None, False)] if set(surfaces) - {"act"} else []) + [(t, False) for t in times]
                    if act_plan is not None and act_plan["expectations"]:
                        passes.append((None, True))
                    for at, act in passes:
                        why = check_stop(log, stub=stub)
                        if why:
                            raise StopRun(f"{why}. Resume: the same command with --model {entry.id} "
                                          f"--profile {profile.id} --first-trial {trial} --trials "
                                          f"{args.first_trial + args.trials - trial} into a new --out")
                        label = (f"{entry.id} / {profile.id} / trial {trial}" + (f" / at {at}" if at else "")
                                 + (" / act-for-me" if act else ""))
                        print(f"\n== {label} ==")
                        home = run_dir / "homes" / "-".join(
                            filter(None, [profile.id, entry.id.replace("/", "_"), str(trial),
                                          at.replace(":", "").replace("+", "d").replace(" ", "-") if at else None,
                                          "act" if act else None]))
                        home.parent.mkdir(parents=True, exist_ok=True)
                        bench = Bench(profile, entry.model_id, home, port=args.port,
                                      sandbox_port=args.sandbox_port, engine_port=args.engine_port,
                                      stub_script=args.stub_script, cloud=entry.cloud,
                                      context_window=entry.context_window,
                                      credential_ref=args.credential_ref, at=at,
                                      act_yes_at=act_plan["yes_at"] if act else None,
                                      narration_thinking=entry.thinking == "narration-on")
                        base = {"run": run, "entry": entry.id, "model": entry.model_id, "kind": entry.kind,
                                "ranked": entry.ranked and not stub, "stub": stub, "variant": entry.variant(),
                                "device": device, "runtime": runtime, "profile": profile.id, "trial": trial,
                                "date": meta["date"], "_entry": entry}
                        t0 = time.monotonic()
                        n = 0
                        resident_start = simday.engine_resident(engine_port)
                        try:
                            bench.start()
                            if act:
                                n = asyncio.run(run_act_trial(bench, act_plan, base, log, spend))
                            else:
                                n = asyncio.run(run_trial(bench, cases, surfaces, base, log, only, spend, ctx,
                                                          at=at))
                        finally:
                            bench.stop()
                            escapes = bench.escapes()
                            meta["trials_meta"].append({"entry": entry.id, "profile": profile.id,
                                                        "trial": trial, "at": at, "act_for_me": act,
                                                        "records": n,
                                                        "engine_resident": {"start": resident_start,
                                                                            "end": simday.engine_resident(engine_port)},
                                                        "startup": bench.startup,
                                                        "clock_offset_s": round(bench.offset, 3),
                                                        "elapsed_s": round(time.monotonic() - t0, 1),
                                                        "escapes": escapes})
                            if escapes:
                                print(f"  guard refused {len(escapes)} attempt(s): {escapes[:3]}")
                        if all_faults(log, n):
                            raise StopRun(f"every record of {label} is an engine fault ({n} of {n}): "
                                          "the run ends as a failure, not as done")
    except (SpendLimit, StopRun) as exc:
        stopped = str(exc)
        print(f"\nSTOPPED: {stopped}")
    except Terminated as exc:
        stopped = str(exc)
        print(f"\nSTOPPED: {stopped}")
        meta["stopped"] = stopped
        meta["finished"] = time.time()
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=1))
        sys.exit(128 + exc.signum)
    meta["stopped"] = stopped
    meta["cloud_spend_usd"] = round(spend.spent, 6)
    meta["finished"] = time.time()
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    print("\n" + write_summary(run_dir, _load_records(run_dir), refs, pinned, meta))


if __name__ == "__main__":
    main()
