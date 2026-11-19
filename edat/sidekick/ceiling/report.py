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

from edat.sidekick.ceiling.rungs import RUNG_BY_ID, RUNGS

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
        rungs[rg.id] = {"n": rg.n, "cases": len(cases), "trials": trials, "pass_rate": passes / trials,
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
    return {"rungs": rungs, "ceiling": ceiling, "solid": solid,
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


def build_report(out: Path) -> tuple[str, str]:
    entries = load_entries(out)
    summaries = {name: entry_summary(e["records"]) for name, e in entries.items()}
    md = ["# Where each small model's coding stops", "",
          "Private. Each model climbed a ladder of coding tasks, easiest first. A rung **holds** when the model "
          "solves at least half of its tasks on most tries. The **ceiling** is the highest rung that holds; "
          "**solid up to** is how far it holds with no gap below. Every answer was checked by hidden tests in a "
          "sealed sandbox.", "", "## The rungs", ""]
    md += [f"{r.n}. {r.title}" for r in RUNGS]
    for name, e in entries.items():
        s = summaries[name]
        rows = s["rungs"]
        can = [r.title.split(":")[0].lower() for r in RUNGS if r.n <= s["solid"]]
        first_bad = next((r for r in RUNGS if rows[r.id]["holds"] is False), None)
        md += ["", f"## {_label(name, e['store'])}", ""]
        setup = _setup_line(e["store"])
        if setup:
            md += [setup, ""]
        if s["solid"]:
            md.append(f"**Reliably does** rungs 1 to {s['solid']}: {', '.join(can)}.")
        else:
            md.append("**Reliably does** none of the rungs: even the simplest tasks fail more often than not.")
        if first_bad:
            fails = rows[first_bad.id]["failures"]
            total = sum(fails.values()) or 1
            how = "; ".join(f"{FAILURE_WORDS.get(m, m)} ({round(100 * n / total)}% of its misses)"
                            for m, n in Counter(fails).most_common(3))
            md.append(f"**Starts failing at** rung {first_bad.n} ({first_bad.title.lower()}): it solves "
                      f"{_pct(rows[first_bad.id]['pass_rate'])} of tries. Mostly it {how}.")
        md.append(f"**Ceiling:** rung {s['ceiling']}" + (f" (a pass higher up than its solid range)" if s["ceiling"] > s["solid"] else "") + ".")
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
    md += ["", "## Rung by model (share of tries solved; ✓ = the rung holds)", ""]
    md.append("| Rung | " + " | ".join(_label(n, entries[n]["store"]) for n in names) + " |")
    md.append("|---|" + "---|" * len(names))
    for r in RUNGS:
        cells = []
        for n in names:
            x = summaries[n]["rungs"][r.id]
            cells.append("–" if x["pass_rate"] is None else f"{_pct(x['pass_rate'])}{' ✓' if x['holds'] else ''}")
        md.append(f"| {r.n}. {r.title.split(':')[0]} | " + " | ".join(cells) + " |")
    md.append("| **Ceiling** | " + " | ".join(str(summaries[n]["ceiling"]) for n in names) + " |")
    md.append("| Solid up to | " + " | ".join(str(summaries[n]["solid"]) for n in names) + " |")
    skipped = _skipped(out)
    if skipped:
        md += ["", "## Not run", ""]
        md += [f"- {what}: {why}" for what, why in skipped]
    text = "\n".join(md) + "\n"
    return text, _html(text)


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
