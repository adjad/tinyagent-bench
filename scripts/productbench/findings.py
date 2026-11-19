"""What the model found: a report a person can read, built from a run's stored output.

    python -m scripts.productbench.findings <run dir> [--reference-run <dir> ...] [--out <dir>]

For every entry and profile it lists, item by item and side by side with the two reference
models (Gemma-Wisp, then base Gemma 4 E4B, from the same run or from pinned runs):

  Brief            every attention item in the key: found (and where: a card, needs
                   attention, a to-do), missed, or stale; every noise item it raised; and
                   everything the Brief showed, zone by zone, with its own words
  done by itself   every action Harbor took under "act for me": right, done wrongly, should
                   not have, plus what it should have handled and did not, and anything
                   beyond what the setting allows (counted with the unsafe actions)
  to-do list       the Brief's to-dos and the answer to "What's on my to-do list?"
  day questions    "What's in my email?", "What's in my messages?" and "Is there anything in
                   the next 3 hours?" at each pinned time

Each mark carries the captured text that decided it. Outcomes are counted over trials
("found 2/3"). Nothing is re-run: the report reads results.jsonl (and meta.json) only, so it can
be regenerated at any time. Held-out material never appears: records or profiles marked
`heldout` are dropped and counted. A run made with the scripted stub says so at the top.

Writes findings.md and findings.html (self-contained, no scripts or external files).
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.productbench import matcher, score_brief, summary  # noqa: E402
from scripts.productbench.profile import Profile, load as load_profile  # noqa: E402

REFERENCES = ("Gemma-Wisp", "gemma-4-E4B-it-oQ4e-mtp")   # models.json "references", in order
ZONE_NAMES = {"card": "Brief card", "thread": "needs attention", "todo": "to-do"}
LABEL = {"found": "found", "missed": "missed", "wrongly_raised": "wrongly raised", "stale": "stale",
         "not_in_key": "not in the key", "right": "right to do", "done_wrongly": "done wrongly",
         "should_not": "should not have", "unsafe": "beyond what's allowed", "not_done": "not done",
         "offered": "left for the person", "answer": "the answer", "passed_over": "passed over"}
GOOD = {"found", "right", "not_done"}
BAD = {"missed", "wrongly_raised", "stale", "done_wrongly", "should_not", "unsafe"}
TEXT_MAX = 240
SECTIONS = [
    ("brief", "Brief: what it spotted"),
    ("act", "Done by itself (act for me): did the right thing"),
    ("act_gate", "Done by itself: behaved as Harbor's rules require"),
    ("brief_todos", "The Brief's to-do list"),
    ("todo", "“What's on my to-do list?”"),
    ("inbox", "“What's in my email?”"),
    ("messages", "“What's in my messages?”"),
    ("window", "“Is there anything in the next 3 hours?”"),
]


class HeldOut(ValueError):
    pass


def _cut(text: str | None, n: int = TEXT_MAX) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def load_records(run_dir: Path) -> tuple[list[dict], dict]:
    path = Path(run_dir) / "results.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    meta_path = Path(run_dir) / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return rows, meta


def find_profile(pid: str, metas: list[dict], extra: dict[str, Path] | None = None) -> Profile | None:
    candidates = []
    if extra and pid in extra:
        candidates.append(Path(extra[pid]))
    for m in metas:
        for p in m.get("profiles") or []:
            if p.get("id") == pid and p.get("path"):
                candidates.append(Path(p["path"]))
    candidates += [ROOT / "test_fixtures" / "bench_profiles" / pid / "profile.json",
                   ROOT / "test_fixtures" / "productbench" / pid / "profile.json"]
    for c in candidates:
        if c.exists():
            return load_profile(c)
    return None


# ------------------------------------------------------------------ collecting
class Cell:
    """One item for one entry, over trials."""

    def __init__(self) -> None:
        self.outcomes: Counter = Counter()
        self.text: str | None = None
        self.where: Counter = Counter()

    def add(self, outcome: str, text: str | None = None, where: str | None = None) -> None:
        self.outcomes[outcome] += 1
        if text and not self.text:
            self.text = _cut(text)
        if where:
            self.where[where] += 1

    def main(self) -> str:
        return self.outcomes.most_common(1)[0][0] if self.outcomes else "missed"

    def as_dict(self, trials: int) -> dict:
        return {"outcome": self.main(), "counts": dict(self.outcomes), "trials": trials, "text": self.text,
                "where": self.where.most_common(1)[0][0] if self.where else None}


class Report:
    def __init__(self) -> None:
        # (entry, profile) -> section -> key -> Cell; plus row labels and per-entry extras
        self.cells: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(Cell)))
        self.labels: dict = defaultdict(dict)        # (profile, section) -> key -> label
        self.shown: dict = defaultdict(list)         # (entry, profile) -> Brief items as shown (trial 1)
        self.trials: dict = defaultdict(set)
        self.meta: dict = {}                         # (entry, profile) -> {model, kind, stub}
        self.notes: dict = defaultdict(list)         # (entry, profile) -> lines
        self.dropped = 0
        self.stub = False

    def label(self, profile: str, section: str, key: str, text: str) -> None:
        self.labels[(profile, section)].setdefault(key, _cut(text, 160))


def collect(records: list[dict], profiles: dict[str, Profile | None], report: Report | None = None) -> Report:
    rep = report or Report()
    for r in records:
        if r.get("split") == "heldout" or r.get("heldout"):
            rep.dropped += 1
            continue
        pid = r["profile"]
        prof = profiles.get(pid)
        if prof is not None and (prof.data.get("split") == "heldout" or prof.data.get("heldout")):
            raise HeldOut(f"profile {pid} is held out; its findings are never published")
        key = (r.get("entry") or r.get("model"), pid)
        rep.trials[key].add(r.get("trial", 1))
        rep.meta.setdefault(key, {"model": r.get("model"), "kind": r.get("kind", "local"), "stub": bool(r.get("stub"))})
        rep.stub = rep.stub or bool(r.get("stub"))
        surface = r.get("surface")
        if surface == "brief" and prof is not None:
            _brief(rep, key, r, prof)
        elif surface == "question":
            _question(rep, key, r)
        elif surface == "act":
            _act(rep, key, r)
    return rep


def _brief(rep: Report, key, r: dict, prof: Profile) -> None:
    snap = (r.get("captured") or {}).get("snapshot") or {}
    items = score_brief.items_from_snapshot(snap)
    today = prof.anchor.date()
    cells = rep.cells[key]["brief"]
    pid = prof.id
    superseded = {it["supersedes"] for it in prof.ledger if it.get("supersedes")}
    marks = [[] for _ in items]
    for it in prof.ledger:
        anchors = it.get("anchors") or []
        if it.get("attention") and it["id"] not in superseded:
            rep.label(pid, "brief", it["id"], it["title"])
            pos = next((i for i, x in enumerate(items) if matcher.any_hit(anchors, x["text"], today)), None)
            if pos is None:
                cells[it["id"]].add("missed")
            else:
                cells[it["id"]].add("found", items[pos]["text"], ZONE_NAMES[items[pos]["zone"]])
                marks[pos].append(("found", it["id"]))
        elif it["type"] == "noise":
            pos = next((i for i, x in enumerate(items) if matcher.any_hit(anchors, x["text"], today)), None)
            if pos is not None:
                rep.label(pid, "brief", it["id"], f"(noise) {it['title']}")
                cells[it["id"]].add("wrongly_raised", items[pos]["text"], ZONE_NAMES[items[pos]["zone"]])
                marks[pos].append(("wrongly_raised", it["id"]))
        stale = it.get("stale_anchors") or []
        if stale:
            current = it.get("current_anchors") or anchors
            pos = next((i for i, x in enumerate(items) if matcher.any_hit(stale, x["text"], today)
                        and not matcher.any_hit(current, x["text"], today)), None)
            if pos is not None:
                rep.label(pid, "brief", it["id"] + ":stale", f"(old value) {it['title']}")
                cells[it["id"] + ":stale"].add("stale", items[pos]["text"], ZONE_NAMES[items[pos]["zone"]])
                marks[pos].append(("stale", it["id"]))
    if r.get("trial", 1) == min(rep.trials[key]):
        rep.shown[key] = [{"zone": ZONE_NAMES[x["zone"]], "text": _cut(x["text"]),
                           "source": x["source"], "marks": marks[i] or [("not_in_key", None)]}
                          for i, x in enumerate(items)]


def _question(rep: Report, key, r: dict) -> None:
    sc = r.get("score") or {}
    fam = r.get("family") or sc.get("question")
    section = "brief_todos" if r.get("case") == "TQ-brief-todos" else fam
    if section == "window":
        section = f"window@{sc.get('at') or r.get('now')}"
    pid = key[1]
    cells = rep.cells[key][section]
    for it in sc.get("items") or []:
        k = f"{it.get('outcome') if it.get('outcome') in ('wrongly_raised', 'stale') else 'key'}:{it.get('title')}"
        rep.label(pid, section, k, it.get("title") or "")
        cells[k].add(it["outcome"], it.get("line"))
    if sc.get("empty_window"):
        rep.label(pid, section, "empty", "(nothing is on: the answer should say so)")
        cells["empty"].add("found" if sc.get("said_nothing") else "missed",
                           (r.get("captured") or {}).get("answer"))
    for s in sc.get("must_not_include_hits") or []:
        rep.label(pid, section, f"bad:{s}", f"(must not say) {s}")
        cells[f"bad:{s}"].add("stale", s)
    if sc.get("approximate"):
        note = f"{section}: approximate key ({sc.get('key')}); the profile's own answer key has not landed"
        if note not in rep.notes[key]:
            rep.notes[key].append(note)
    answer = (r.get("captured") or {}).get("answer")
    if answer:
        rep.label(pid, section, "~answer", "(the whole answer)")
        cells["~answer"].add("answer", answer)


def _act(rep: Report, key, r: dict) -> None:
    """Two sections: against the key's `decision` (did the right thing) and against its
    `harbor_gate` (behaved as Harbor's rules require). Unsafe actions and Harbor's own reasons
    for passing items over go with the second."""
    sc = r.get("score") or {}
    pid = key[1]
    gate = sc.get("gate") or {}
    for section, verdict, missed in (("act", "verdict", sc.get("missed") or []),
                                     ("act_gate", "verdict_gate", gate.get("missed") or [])):
        if section == "act_gate" and not gate:
            break                      # a record from before the two lenses
        cells = rep.cells[key][section]
        for a in sc.get("actions") or []:
            k = a.get("expectation") or f"did:{a.get('kind')}:{a.get('title')}"
            rep.label(pid, section, k, a.get("truth") or f"{a.get('kind')}: {a.get('title')}")
            text = f"{a.get('kind')}: {a.get('title')}" + (f" at {a['at_local']}" if a.get("at_local") else "")
            if a.get("why"):
                text += f" ({a['why']})"
            cells[k].add(a.get(verdict) or a.get("verdict"), text)
        done = {m["expectation"] for m in missed}
        for m in missed:
            note = (f" (Harbor's rules give a {m['gate']} here)" if section == "act" and m.get("gate") not in (None, "act")
                    else "")
            rep.label(pid, section, m["expectation"], (m.get("truth") or m.get("title") or m["expectation"]) + note)
            cells[m["expectation"]].add("missed", None)
        for o in sc.get("offers") or []:
            if o["expectation"] in done:
                continue
            asks_action = (o.get("decision") == "act") if section == "act" else (o.get("gate") == "act")
            if asks_action:
                continue
            rep.label(pid, section, o["expectation"], o.get("truth") or o["expectation"])
            cells[o["expectation"]].add("not_done", None)
    target = "act_gate" if gate else "act"
    cells = rep.cells[key][target]
    for u in sc.get("unsafe") or []:
        k = f"unsafe:{u.get('why')}:{u.get('title') or u.get('type')}"
        rep.label(pid, target, k, f"(beyond what's allowed) {u.get('why')}")
        cells[k].add("unsafe", u.get("title") or json.dumps(u.get("payload") or {}))
    for n in sc.get("passed_over") or []:
        k = f"passed:{n.get('who')}:{n.get('reason')}"
        rep.label(pid, target, k, f"(Harbor passed over) {n.get('who') or 'an item'}")
        cells[k].add("passed_over", n.get("reason"))

    if sc.get("mode") == "arrivals":
        note = "act for me: the profile's key has no act_for_me entries, so four invented messages were used"
        if note not in rep.notes[key]:
            rep.notes[key].append(note)


# ------------------------------------------------------------------ views
def comparisons(rep: Report, references=REFERENCES) -> list[dict]:
    """One view per (entry, profile): the entry's column plus each reference's, per section."""
    entries = sorted({e for e, _ in rep.cells} | {e for e, _ in rep.meta})
    profiles = sorted({p for _, p in rep.meta})
    refs = [x for x in references if x in entries]
    focus = [e for e in entries if e not in refs] or refs[:1]
    views = []
    for pid in profiles:
        for entry in focus:
            if (entry, pid) not in rep.meta:
                continue
            cols = [entry] + [x for x in refs if x != entry and (x, pid) in rep.meta]
            sections = []
            names = sorted({s for (e, p) in rep.cells if p == pid and e in cols for s in rep.cells[(e, p)]},
                           key=_section_order)
            for s in names:
                keys = list(rep.labels[(pid, s)])
                rows = []
                for k in keys:
                    if not any(k in rep.cells[(c, pid)].get(s, {}) for c in cols):
                        continue
                    row = {"key": k, "label": rep.labels[(pid, s)][k], "cells": {}}
                    for c in cols:
                        cell = rep.cells[(c, pid)].get(s, {}).get(k)
                        trials = len(rep.trials[(c, pid)])
                        row["cells"][c] = cell.as_dict(trials) if cell else None
                    rows.append(row)
                rows.sort(key=lambda x: (x["key"].startswith("~"), _rank(x["cells"].get(entry))))
                sections.append({"id": s, "title": _section_title(s), "rows": rows})
            views.append({"profile": pid, "entry": entry, "columns": cols,
                          "meta": {c: rep.meta.get((c, pid)) for c in cols},
                          "trials": {c: len(rep.trials[(c, pid)]) for c in cols},
                          "shown": rep.shown.get((entry, pid), []), "notes": rep.notes.get((entry, pid), []),
                          "sections": sections})
    return views


def _section_order(s: str) -> tuple:
    base = s.split("@")[0]
    order = [x for x, _ in SECTIONS]
    return (order.index(base) if base in order else 99, s)


def _section_title(s: str) -> str:
    base, _, at = s.partition("@")
    title = dict(SECTIONS).get(base, base)
    if " " in at:
        day, hhmm = at.split()
        return f"{title} on day {day} at {hhmm}"
    return f"{title} at {at}" if at else title


def _rank(cell: dict | None) -> int:
    if cell is None:
        return 5
    o = cell["outcome"]
    return 0 if o in ("unsafe",) else 1 if o in BAD else 2 if o in GOOD else 3


def _cell_words(cell: dict | None) -> str:
    if cell is None:
        return "—"
    if cell["outcome"] == "answer":
        return "answer"
    t = cell["trials"]
    parts = sorted(cell["counts"].items(), key=lambda kv: (-kv[1], kv[0]))
    words = " · ".join(LABEL.get(o, o) + (f" {n}/{t}" if t > 1 else "") for o, n in parts)
    if cell.get("where"):
        words += f" ({cell['where']})"
    return words


# ------------------------------------------------------------------ rendering
STUB_NOTE = ("Made from the scripted stub engine, not a model: the text below is the stub's and the marks "
             "say nothing about any model. It shows what the report looks like.")


def _md_escape(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def markdown(views: list[dict], rep: Report, run_ids: list[str]) -> str:
    out = ["# What the model found", "",
           f"{summary.TITLE}. Run{'s' if len(run_ids) > 1 else ''}: {', '.join(run_ids)}.", ""]
    if rep.stub:
        out += [f"> {STUB_NOTE}", ""]
    if rep.dropped:
        out += [f"{rep.dropped} held-out record(s) left out.", ""]
    out += ["Marks: found · missed · wrongly raised (shown though it is noise, not the person's, or not a task) · "
            "stale (an old value or a done item shown as current) · right to do / done wrongly / should not have "
            "(things Harbor did by itself) · beyond what's allowed (counted with the unsafe actions, never "
            "scored). Counts are over trials.", ""]
    for v in views:
        main = v["entry"]
        out += [f"## {main} on the {v['profile']} profile", ""]
        out.append("Columns: " + ", ".join(f"{c} ({v['trials'][c]} trial{'s' if v['trials'][c] != 1 else ''})"
                                         for c in v["columns"]) + ".")
        out.append("")
        for n in v["notes"]:
            out += [f"- Note: {n}"]
        if v["notes"]:
            out.append("")
        for s in v["sections"]:
            out += [f"### {s['title']}", ""]
            head = ["Item"] + v["columns"] + [f"What {main} said"]
            out += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
            for row in s["rows"]:
                mine = row["cells"].get(main) or {}
                cells = [_md_escape(row["label"])] + [_md_escape(_cell_words(row["cells"].get(c)))
                                                      for c in v["columns"]]
                cells.append(_md_escape(mine.get("text") or ""))
                out.append("| " + " | ".join(cells) + " |")
            out.append("")
            if s["id"] == "brief" and v["shown"]:
                out += [f"Everything {main}'s Brief showed (first trial), by zone:", ""]
                for zone in ("Brief card", "needs attention", "to-do"):
                    rows = [x for x in v["shown"] if x["zone"] == zone]
                    if not rows:
                        continue
                    out.append(f"- **{zone}**")
                    for x in rows:
                        marks = ", ".join(LABEL[m] + (f" {lid}" if lid else "") for m, lid in x["marks"])
                        src = "" if x["source"] == "model" else " (written by Harbor's code)"
                        out.append(f"  - {_md_escape(x['text'])} — {marks}{src}")
                out.append("")
    return "\n".join(out).rstrip() + "\n"


CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--line:#e4e1d9;--card:#ffffff;
--good:#1f7a4d;--good-bg:#e5f3ea;--bad:#a5341f;--bad-bg:#fbe8e3;--warn:#8a5a00;--warn-bg:#fbf0d9;
--info:#3b5b8c;--info-bg:#e6edf7;--danger:#ffffff;--danger-bg:#b3261e}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161615;--fg:#ecebe6;--muted:#a3a19a;
--line:#34332f;--card:#1f1f1d;--good:#7fd1a4;--good-bg:#173526;--bad:#f2a594;--bad-bg:#3d1d16;--warn:#f0c46b;
--warn-bg:#3a2c0f;--info:#a9c2ea;--info-bg:#1c2a40;--danger:#fff;--danger-bg:#b3261e}}
:root[data-theme="dark"]{--bg:#161615;--fg:#ecebe6;--muted:#a3a19a;--line:#34332f;--card:#1f1f1d;--good:#7fd1a4;
--good-bg:#173526;--bad:#f2a594;--bad-bg:#3d1d16;--warn:#f0c46b;--warn-bg:#3a2c0f;--info:#a9c2ea;--info-bg:#1c2a40}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:26px;margin:0 0 4px}
h2{font-size:20px;margin:40px 0 4px;padding-top:16px;border-top:1px solid var(--line)}h3{font-size:16px;margin:24px 0 8px}
.muted{color:var(--muted)}.banner{background:var(--warn-bg);color:var(--warn);border-radius:8px;padding:10px 14px;margin:12px 0}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;min-width:640px}th,td{text-align:left;vertical-align:top;padding:8px 10px;
border-bottom:1px solid var(--line)}th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
tr:last-child td{border-bottom:0}td.said{color:var(--muted);font-size:13px;max-width:380px}
.chip{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12px;font-weight:600;white-space:nowrap}
.good{background:var(--good-bg);color:var(--good)}.bad{background:var(--bad-bg);color:var(--bad)}
.warn{background:var(--warn-bg);color:var(--warn)}.info{background:var(--info-bg);color:var(--info)}
.danger{background:var(--danger-bg);color:var(--danger)}.where{display:block;font-size:12px;color:var(--muted)}
ul.shown{padding-left:18px}ul.shown li{margin:4px 0}.legend .chip{margin-right:6px}
"""


def _chip(cell: dict | None) -> str:
    if cell is None:
        return '<span class="muted">—</span>'
    if cell["outcome"] == "answer":
        return '<span class="chip info">answer</span>'
    t = cell["trials"]
    chips = []
    for o, n in sorted(cell["counts"].items(), key=lambda kv: (-kv[1], kv[0])):
        cls = ("danger" if o == "unsafe" else "good" if o in GOOD else "warn" if o == "stale" else "bad" if o in BAD
               else "info")
        count = f" {n}/{t}" if t > 1 else ""
        chips.append(f'<span class="chip {cls}">{html.escape(LABEL.get(o, o))}{count}</span>')
    where = f'<span class="where">{html.escape(cell["where"])}</span>' if cell.get("where") else ""
    return " ".join(chips) + where


def html_page(views: list[dict], rep: Report, run_ids: list[str]) -> str:
    e = html.escape
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             f"<title>What it found</title><style>{CSS}</style></head><body><main>",
             "<h1>What the model found</h1>",
             f'<p class="muted">{e(summary.TITLE)} · run{"s" if len(run_ids) > 1 else ""} '
             f'{e(", ".join(run_ids))}</p>']
    if rep.stub:
        parts.append(f'<div class="banner">{e(STUB_NOTE)}</div>')
    if rep.dropped:
        parts.append(f'<p class="muted">{rep.dropped} held-out record(s) left out.</p>')
    parts.append('<p class="legend muted"><span class="chip good">found</span><span class="chip bad">missed</span>'
                 '<span class="chip bad">wrongly raised</span><span class="chip warn">stale</span>'
                 '<span class="chip good">right to do</span><span class="chip bad">should not have</span>'
                 '<span class="chip danger">beyond what\'s allowed</span> Counts are over trials.</p>')
    for v in views:
        main = v["entry"]
        parts.append(f"<h2>{e(main)} on the {e(v['profile'])} profile</h2>")
        parts.append('<p class="muted">' + e(" · ".join(
            f"{c}: {v['trials'][c]} trial{'s' if v['trials'][c] != 1 else ''}" for c in v["columns"])) + "</p>")
        for n in v["notes"]:
            parts.append(f'<p class="banner">{e(n)}</p>')
        for s in v["sections"]:
            parts.append(f"<h3>{e(s['title'])}</h3><div class=\"wrap\"><table><thead><tr><th>Item</th>")
            parts += [f"<th>{e(c)}</th>" for c in v["columns"]]
            parts.append(f"<th>What {e(main)} said</th></tr></thead><tbody>")
            for row in s["rows"]:
                mine = row["cells"].get(main) or {}
                parts.append(f"<tr><td>{e(row['label'])}</td>")
                parts += [f"<td>{_chip(row['cells'].get(c))}</td>" for c in v["columns"]]
                parts.append(f'<td class="said">{e(mine.get("text") or "")}</td></tr>')
            parts.append("</tbody></table></div>")
            if s["id"] == "brief" and v["shown"]:
                parts.append(f"<h3>Everything {e(main)}'s Brief showed (first trial)</h3>")
                for zone in ("Brief card", "needs attention", "to-do"):
                    rows = [x for x in v["shown"] if x["zone"] == zone]
                    if not rows:
                        continue
                    parts.append(f'<p class="muted">{e(zone)}</p><ul class="shown">')
                    for x in rows:
                        chips = " ".join(_chip({"outcome": m, "counts": {m: 1}, "trials": 1, "where": None})
                                         + (f' <span class="muted">{e(lid)}</span>' if lid else "")
                                         for m, lid in x["marks"])
                        src = "" if x["source"] == "model" else ' <span class="muted">(written by Harbor\'s code)</span>'
                        parts.append(f"<li>{e(x['text'])} {chips}{src}</li>")
                    parts.append("</ul>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


# ------------------------------------------------------------------ entry points
def build(run_dirs: list[Path], *, profiles: dict[str, Path] | None = None,
          references=REFERENCES) -> tuple[list[dict], Report, list[str]]:
    rep = Report()
    loaded, metas, ids = [], [], []
    for d in run_dirs:
        rows, meta = load_records(Path(d))
        loaded.append(rows)
        metas.append(meta)
        ids.append(meta.get("run") or (rows[0]["run"] if rows else Path(d).name))
    pids = {r["profile"] for rows in loaded for r in rows}
    profs = {pid: find_profile(pid, metas, profiles) for pid in pids}
    refs = list(dict.fromkeys([*(metas[0].get("references") or []), *references])) if metas else list(references)
    for rows in loaded:
        collect(rows, profs, rep)
    return comparisons(rep, refs), rep, ids


def write(run_dir: Path, reference_runs: list[Path] | None = None, out: Path | None = None,
          profiles: dict[str, Path] | None = None) -> tuple[Path, Path]:
    views, rep, ids = build([Path(run_dir), *(reference_runs or [])], profiles=profiles)
    out = Path(out or run_dir)
    out.mkdir(parents=True, exist_ok=True)
    md, page = out / "findings.md", out / "findings.html"
    md.write_text(markdown(views, rep, ids), encoding="utf-8")
    page.write_text(html_page(views, rep, ids), encoding="utf-8")
    (out / "findings.json").write_text(json.dumps({"runs": ids, "stub": rep.stub, "dropped_heldout": rep.dropped,
                                                   "views": views}, indent=1, ensure_ascii=False), encoding="utf-8")
    return md, page


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="What the model found, from a stored run")
    ap.add_argument("run_dir")
    ap.add_argument("--reference-run", action="append", default=[], help="a pinned run holding the references")
    ap.add_argument("--out", default=None, help="where to write (default: the run directory)")
    ap.add_argument("--profile", action="append", default=[], help="id=path, when a profile moved")
    args = ap.parse_args(argv)
    profiles = dict(p.split("=", 1) for p in args.profile) if args.profile else None
    md, page = write(Path(args.run_dir), [Path(x) for x in args.reference_run],
                     Path(args.out) if args.out else None, profiles)
    print(f"wrote {md.name} and {page.name}")


if __name__ == "__main__":
    main()
