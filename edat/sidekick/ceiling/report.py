"""The ceiling report: one page, plain language, private.

    python -m edat.sidekick.ceiling report OUT [--out OUT/report]

Reads every entry folder under OUT (written by run.py) and writes
`report.md` and `report.html` (self-contained: no scripts, nothing fetched).
For each model entry: what it can reliably do, where it starts failing and
how, two or three short real examples, then one table of rung against model.
"""
from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from pathlib import Path

from edat.sidekick.ceiling.rungs import NOT_HARDENED, NOT_HARDENED_WHY, RUNG_BY_ID, RUNGS

FAILURE_WORDS = {
    "no_code": "gave no usable code (ignored the answer format or only talked about the task)",
    "wrong_files": "left out a file it was asked for",
    "unsafe": "proposed an unsafe or destructive action (blocked before it ran)",
    "syntax": "wrote code that does not run (broken syntax)",
    "interface": "did not follow the instructions on names or files, so the code could not be used",
    "logic": "wrote code that runs but gives wrong results",
    "timeout": "wrote code that hangs or is far too slow",
    "too_long": "ran out of room: it kept going until it hit the length limit",
    "tool_loop": "went round in circles with the tools until it ran out of calls",
    "tool_format": "could not use the tools properly (broken or made-up tool calls)",
    "instruction": "left placeholders instead of the names it was given",
    "grader_error": "left the grading unable to finish (counted as a fail)",
}


def rung_of(rec: dict) -> str | None:
    """The ladder rung a record belongs to, or None (Restraint set, sidekick tool mode)."""
    fam = (rec.get("facets") or {}).get("family")
    if rec.get("suite") in ("ladder", "ladder_tool"):
        return fam if fam in RUNG_BY_ID else None
    if rec.get("suite") != "answer_only" or (rec.get("facets") or {}).get("category") == "restraint":
        return None
    tags = (rec.get("facets") or {}).get("tags") or []
    for lv in ("l1", "l2", "l3"):
        if f"level-{lv}" in tags:
            return f"sk-{lv}"
    return None


def entry_summary(records: list[dict]) -> dict:
    """Per-rung pass rates, which rungs hold, the ceiling and 'solid up to' for one entry."""
    by_rung: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    modes: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        if r.get("status") != "ok":
            continue
        rid = rung_of(r)
        if rid is None:
            continue
        ok = bool((r.get("metrics") or {}).get("pass"))
        by_rung[rid][r["case_id"]].append(ok)
        if not ok:
            modes[rid][(r.get("metrics") or {}).get("failure_mode") or "logic"] += 1
    rungs = {}
    for rg in RUNGS:
        cases = by_rung.get(rg.id)
        if not cases:
            rungs[rg.id] = {"n": rg.n, "cases": 0, "trials": 0, "pass_rate": None, "cases_held": 0, "holds": None,
                            "failures": {}}
            continue
        trials = sum(len(v) for v in cases.values())
        passes = sum(sum(v) for v in cases.values())
        held = sum(1 for v in cases.values() if 2 * sum(v) > len(v))
        rungs[rg.id] = {"n": rg.n, "cases": len(cases), "trials": trials, "passes": passes, "pass_rate": passes / trials,
                        "cases_held": held, "holds": 2 * held >= len(cases), "failures": dict(modes[rg.id])}
    held = [rungs[r.id]["n"] for r in RUNGS if rungs[r.id]["holds"]]
    ceiling = max(held) if held else 0
    solid = 0
    for r in RUNGS:
        if rungs[r.id]["holds"]:
            solid = r.n
        else:
            break
    restraint = [r for r in records if r.get("status") == "ok" and (r.get("facets") or {}).get("category") == "restraint"]
    tool = [r for r in records if r.get("status") == "ok" and r.get("suite") == "tool_mode"]
    unsafe = sum(1 for r in records if r.get("status") == "ok" and (r.get("safety") or {}).get("labels"))
    hardened = [rungs[r.id]["n"] for r in RUNGS if r.hardened and rungs[r.id]["holds"]]
    flagged = [{"case": r["case_id"], "rung": rung_of(r), "trial": r.get("trial"),
                "pass": bool((r.get("metrics") or {}).get("pass")), "tripwire": r["metrics"]["tripwire"]}
               for r in records if r.get("status") == "ok" and (r.get("metrics") or {}).get("tripwire")]
    return {"rungs": rungs, "ceiling": ceiling, "solid": solid,
            "hardened_ceiling": max(hardened) if hardened else 0, "tripwire": flagged,
            "excluded": sum(1 for r in records if r.get("status") == "excluded"),
            "trials": sum(1 for r in records if r.get("status") == "ok"),
            "restraint_pass_rate": (sum(1 for r in restraint if r["metrics"].get("pass")) / len(restraint)) if restraint else None,
            "tool_mode_pass_rate": (sum(1 for r in tool if r["metrics"].get("pass")) / len(tool)) if tool else None,
            "unsafe_trials": unsafe}


# ------------------------------------------------------------------ reading a results folder

def load_entries(out: Path) -> dict[str, dict]:
    """entry folder name -> {"records": [...], "store": path}; smoke runs are left out."""
    entries = {}
    for d in sorted(p for p in Path(out).iterdir() if p.is_dir() and (p / "runs").is_dir()):
        recs = []
        for f in sorted(d.glob("runs/*/records.jsonl")):
            recs += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        if recs:
            entries[d.name] = {"records": recs, "store": d}
    return entries


def _raw(store: Path, rec: dict) -> dict:
    ref = rec.get("raw") or {}
    for run in store.glob("runs/*"):
        p = run / ref.get("path", "")
        if ref.get("path") and p.is_file():
            try:
                return json.loads(p.read_text())
            except ValueError:
                return {}
    return {}


def _excerpt(text: str, lines: int = 10, width: int = 110) -> str:
    rows = [ln[:width] for ln in (text or "").strip().splitlines()]
    return "\n".join(rows[:lines]) + ("\n…" if len(rows) > lines else "")


def examples(entry: dict, summary: dict, k: int = 3) -> list[dict]:
    """Short real failures: at the first rung that does not hold, plus one unsafe answer if any."""
    first_bad = next((r for r in RUNGS if summary["rungs"][r.id]["holds"] is False), None)
    picked, seen_modes = [], set()
    pool = [r for r in entry["records"] if r.get("status") == "ok" and not r["metrics"].get("pass")]
    unsafe = [r for r in pool if r["metrics"].get("failure_mode") == "unsafe"]
    at_bad = [r for r in pool if first_bad and rung_of(r) == first_bad.id]
    above = [r for r in pool if (rung_of(r) or "") in RUNG_BY_ID and first_bad and RUNG_BY_ID[rung_of(r)].n > first_bad.n]
    for r in at_bad + above + unsafe:
        mode = r["metrics"].get("failure_mode") or "logic"
        if mode in seen_modes and len(picked) < k - 1:
            continue
        raw = _raw(entry["store"], r)
        text = raw.get("answer") or raw.get("final_text") or ""
        why = "; ".join((raw.get("grade") or {}).get("reasons", [])[:1])
        picked.append({"rung": rung_of(r), "case": r["case_id"], "mode": mode, "task": _excerpt(raw.get("prompt_head", ""), 3),
                       "answer": _excerpt(text), "why": why})
        seen_modes.add(mode)
        if len(picked) >= k:
            break
    return picked


def _pct(x) -> str:
    return "–" if x is None else f"{round(100 * x)}%"


def _progress(store: Path | None) -> dict:
    try:
        return json.loads((store / "progress.json").read_text()) if store else {}
    except (OSError, ValueError):
        return {}


def _label(name: str, store: Path | None = None) -> str:
    """The entry's label as the run recorded it; else rebuilt from the folder name `<id>.think-<on|off>.<profile>`."""
    lab = _progress(store).get("label")
    if lab:
        return lab
    mid, _, rest = name.partition(".think-")
    mode, _, prof = rest.partition(".")
    from edat.sidekick.ceiling.models import KNOWN
    base = KNOWN.get(mid, (mid,))[0]
    return f"{base}, thinking {mode}" + (f", {prof} sampling" if prof and prof != "author" else "")


def _setup_line(store: Path | None) -> str | None:
    pg = _progress(store)
    if not pg:
        return None
    q = pg.get("quant") or {}
    bits = pg.get("bits_per_weight")
    samp = ", ".join(f"{k} {v}" for k, v in (pg.get("sampling_sent") or {}).items())
    return (f"Quantisation {q.get('scheme', '?')}" + (f", {bits:.2f} bits a weight" if bits else "")
            + (f". Sampling sent: {samp} ({pg.get('sampling_basis')})." if samp else "."))


def _quick_of(entries: dict) -> dict | None:
    """The quick-look settings when this results folder is a quick look, else None."""
    return next((pg["quick"] for pg in (_progress(e["store"]) for e in entries.values()) if pg.get("quick")), None)


def _ceiling_words(ceiling: int, solid: int, top_run: int | None) -> str:
    """'rung 5'; at the top rung a quick look ran, below rung 9: 'rung 8 or higher (rung 9 not run)'."""
    notes = []
    capped = top_run is not None and ceiling == top_run and top_run < RUNGS[-1].n
    if capped:
        above = [r.n for r in RUNGS if r.n > top_run]
        notes.append((f"rung {above[0]}" if len(above) == 1 else f"rungs {above[0]}-{above[-1]}") + " not run")
    if ceiling > solid:
        notes.append("a pass higher up than its solid range")
    return f"rung {ceiling}" + (" or higher" if capped else "") + (f" ({'; '.join(notes)})" if notes else "")


def _cell(x: dict, quick: bool) -> str:
    if x["pass_rate"] is None:
        return "–"
    shown = f"{x['passes']}/{x['trials']}" if quick else _pct(x["pass_rate"])
    return shown + (" ✓" if x["holds"] else "")


def build_report(out: Path) -> tuple[str, str]:
    entries = load_entries(out)
    summaries = {name: entry_summary(e["records"]) for name, e in entries.items()}
    q = _quick_of(entries)
    top_run = max(q["rungs"]) if q else None
    md = ["# Where each small model's coding stops", ""]
    if q:
        md += ["**A quick look with wide error bars, not a ranking.** Each model tried "
               f"{q['per_rung']} tasks a rung, one try each; read what follows as a first sketch.", ""]
    md += ["Private. Each model climbed a ladder of coding tasks, easiest first. A rung **holds** when the model "
           + ("solves at least half of its tasks (here on a single try each). " if q else
              "solves at least half of its tasks on most tries. ")
           + "The **ceiling** is the highest rung that holds; "
           "**solid up to** is how far it holds with no gap below. Every answer was checked by hidden tests in a "
           "sealed sandbox.", "", "## The rungs", ""]
    md += [f"{r.n}. {r.title}" + ("" if r.hardened else f" ({NOT_HARDENED})") for r in RUNGS]
    md += ["", NOT_HARDENED_WHY]
    md += _quick_limits(entries)
    for name, e in entries.items():
        s = summaries[name]
        rows = s["rungs"]
        can = [r.title.split(":")[0].lower() for r in RUNGS if r.n <= s["solid"]]
        first_bad = next((r for r in RUNGS if rows[r.id]["holds"] is False), None)
        md += ["", f"## {_label(name, e['store'])}", ""]
        setup = _setup_line(e["store"])
        if setup:
            md += [setup, ""]
        does = "**Passed in this look**" if q else "**Reliably does**"
        if s["solid"]:
            md.append(f"{does} rungs 1 to {s['solid']}: {', '.join(can)}.")
        elif q:
            md.append(f"{does} none of the rungs: it solved fewer than half of the simplest tasks.")
        else:
            md.append(f"{does} none of the rungs: even the simplest tasks fail more often than not.")
        if first_bad:
            x = rows[first_bad.id]
            fails = x["failures"]
            total = sum(fails.values()) or 1
            how = "; ".join(f"{FAILURE_WORDS.get(m, m)} " + (f"({n} of its {total} misses)" if q else
                                                            f"({round(100 * n / total)}% of its misses)")
                            for m, n in Counter(fails).most_common(3))
            solved = (f"{x['passes']} of {x['trials']} tasks, one try each" if q else
                      f"{_pct(x['pass_rate'])} of tries")
            md.append(f"**Starts failing at** rung {first_bad.n} ({first_bad.title.lower()}): it solves "
                      f"{solved}. Mostly it {how}.")
        md.append(f"**Ceiling:** {_ceiling_words(s['ceiling'], s['solid'], top_run)}"
                  + (f"; on the hardened rungs 1 to 3 it is rung {s['hardened_ceiling']}." if s["ceiling"] > 3 else "."))
        if s["tripwire"]:
            md.append(f"**Tripwire** (for a person to read; the grade is unchanged): {len(s['tripwire'])} "
                      "answers on rungs 4 to 9 touch the test machinery or the process: "
                      + "; ".join(f"{x['case']} t{x['trial']} ({'passed' if x['pass'] else 'failed'}): {x['tripwire']}"
                                  for x in s["tripwire"][:8]) + ".")
        if s["unsafe_trials"]:
            md.append(f"**Safety:** {s['unsafe_trials']} answers proposed something unsafe or destructive (blocked).")
        if s["excluded"]:
            md.append(f"{s['excluded']} tries were lost to engine faults and are not counted.")
        ex = examples(e, s)
        if ex:
            md += ["", "Examples:"]
            for x in ex:
                md += ["", f"- Rung {RUNG_BY_ID[x['rung']].n if x['rung'] in RUNG_BY_ID else '?'}: {FAILURE_WORDS.get(x['mode'], x['mode'])}. "
                       + (f"Checker: {x['why']}" if x["why"] else ""), "", "  ```", *("  " + ln for ln in x["answer"].splitlines()), "  ```"]
    names = list(entries)
    md += ["", "## Rung by model (" + ("tasks solved, one try each" if q else "share of tries solved")
           + "; ✓ = the rung holds)", ""]
    if q:
        md += ["Columns are in alphabetical order, not ranked. With one try per task, a difference of a "
               "task or two between models, or a rung, is within the noise.", ""]
    md.append("| Rung | " + " | ".join(_label(n, entries[n]["store"]) for n in names) + " |")
    md.append("|---|" + "---|" * len(names))
    for r in RUNGS:
        cells = []
        for n in names:
            x = summaries[n]["rungs"][r.id]
            cells.append(_cell(x, bool(q)))
        md.append(f"| {r.n}. {r.title.split(':')[0]}{'' if r.hardened else ' †'} | " + " | ".join(cells) + " |")
    capped = lambda c: f"{c}+" if top_run is not None and c == top_run and top_run < RUNGS[-1].n else str(c)  # noqa: E731
    md.append("| **Ceiling** | " + " | ".join(capped(summaries[n]["ceiling"]) for n in names) + " |"
              + (f"\n\n{top_run}+ means at least rung {top_run}: nothing higher was run." if q and top_run < RUNGS[-1].n
                 and any(summaries[n]["ceiling"] == top_run for n in names) else ""))
    md.append("| Solid up to | " + " | ".join(str(summaries[n]["solid"]) for n in names) + " |")
    md.append("| Ceiling on rungs 1 to 3 | " + " | ".join(str(summaries[n]["hardened_ceiling"]) for n in names) + " |")
    md += ["", f"† {NOT_HARDENED[0].upper() + NOT_HARDENED[1:]} (see The rungs); not for publishing or ranking."]
    skipped = _skipped(out)
    if skipped:
        md += ["", "## Not run", ""]
        md += [f"- {what}: {why}" for what, why in skipped]
    text = "\n".join(md) + "\n"
    return text, _html(text)


def _quick_limits(entries: dict) -> list[str]:
    """For a quick look: what it cannot tell, stated plainly on the page."""
    quick = [(n, _progress(e["store"])) for n, e in entries.items() if _progress(e["store"]).get("quick")]
    if not quick:
        return []
    q = quick[0][1]["quick"]
    per, rungs = q["per_rung"], q["rungs"]
    understated = sorted({_label(n, entries[n]["store"]).split(" [")[0] for n, pg in quick
                          if pg.get("ships_thinking") and pg["quick"]["thinking"] == "off"})
    left = [r for r in RUNGS if r.mode == "answer_only" and r.n not in rungs]
    out = ["", "## What this quick look cannot tell", "",
           f"This was a quick look: {per} tasks a rung, one try each"
           + (", answers only" if 9 not in rungs else "") + ". It has wide error bars and is not a ranking.", "",
           f"- **No repeat tries.** Each task was tried once, so a lucky or unlucky answer counts fully, and a rung "
           f"holds on {(per + 1) // 2} of {per} tasks. A ceiling here is good to about one rung either way.",
           "- **Thinking.** " + (
               "Every model ran with thinking off. Models that ship with thinking on are likely understated: "
               + ", ".join(understated) + "." if understated else
               "Each model ran in the mode it ships in." if q["thinking"] == "shipped" else
               "Every model ran with thinking off; none of these ships with thinking on."),
           ("- **Tool mode on a single try.** Rung 9 (read, edit, run the tests, fix) ran with one try per task."
            if 9 in rungs else
            "- **No tool mode, and the top rung was not run.** Rung 9 (read, edit, run the tests, fix) was left "
            "out, so nothing here says how a model does with tools, and a model whose ceiling shows as "
            f"{max(rungs)} or higher may go further."),
           "- **No safety set.** The Restraint cases were not run, so nothing here says whether a model refuses "
           "or flags unsafe requests.",
           ]
    if left:
        out.append("- **Rungs left out:** " + ", ".join(f"{r.n} ({r.title.split(':')[0].lower()})" for r in left)
                   + "; a ceiling cannot be above a rung that was not run.")
    out += ["", "The first pass (12 tasks a rung, then repeat tries around each ceiling) and the full plan answer "
            "these.", ""]
    return out


def _skipped(out: Path) -> list[tuple[str, str]]:
    """Entries the run skipped (the engine could not serve them, thinking forced off) and folders left out."""
    try:
        log = json.loads((Path(out) / "run-all.json").read_text())
    except (OSError, ValueError):
        return []
    rows = [(r["label"], r.get("why") or "skipped") for r in log.get("entries", []) if r.get("result") == "skipped"]
    rows += [(f"folder {name}", why) for name, why in log.get("skipped_folders", [])]
    return rows


def _html(md: str) -> str:
    """A small Markdown subset to HTML: headings, paragraphs, lists, code blocks, one table."""
    out, in_code, in_table = [], False, False
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("```"):
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line.removeprefix("  ")))
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if set("".join(cells)) <= set("-"):
                continue
            if not in_table:
                out.append("<table>")
                in_table = True
                out.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if s.startswith("# "):
            out.append(f"<h1>{_inline(s[2:])}</h1>")
        elif s.startswith("## "):
            out.append(f"<h2>{_inline(s[3:])}</h2>")
        elif s.startswith("- "):
            out.append(f"<p class=ex>{_inline(s[2:])}</p>")
        elif s:
            out.append(f"<p>{_inline(s)}</p>")
    if in_table:
        out.append("</table>")
    css = ("body{font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:880px;margin:24px auto;padding:0 16px;"
           "color:#1d1d1f;background:#fff}pre{background:#f4f4f6;padding:8px 10px;overflow-x:auto;font-size:12.5px}"
           "table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #ddd;padding:4px 8px;text-align:left}"
           "@media (prefers-color-scheme: dark){body{color:#eee;background:#161618}pre{background:#24242a}td,th{border-color:#444}}")
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Coding ceiling</title><style>{css}</style>\n" + "\n".join(out) + "\n")


def _inline(s: str) -> str:
    import re
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.sidekick.ceiling report")
    ap.add_argument("results")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    md, page = build_report(Path(a.results))
    dest = Path(a.out) if a.out else Path(a.results) / "report"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "report.md").write_text(md)
    (dest / "report.html").write_text(page)
    print(dest / "report.html")
    return 0
