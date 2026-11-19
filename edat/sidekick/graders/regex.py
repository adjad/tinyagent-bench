"""Regular-expression graders (spec 12 §3.2).

regex_write check spec:
    mode:       fullmatch | search  (per string)
    flags:      "" or letters from "IMSX"
    positives, negatives:            shown strings (10 + 10)
    hidden_pos, hidden_neg:          hidden strings (20 + 20)
The pattern runs inside the sandbox in its own process with a 1 s limit, so
catastrophic backtracking fails the trial instead of hanging the run.

regex_read check spec:
    labels:   {"A": string, ...}
    pattern, mode, flags
    answer:   sorted list of the labels that match (computed by the generator)
The answer must start with the labels ("B, D, E"); the first line is read.
"""
from __future__ import annotations

import json
import re

from edat.sidekick.graders.common import GradeResult
from edat.sidekick.sandbox.run import Isolation, Limits, TrialArea

RUNNER = r'''
import json, re, sys
spec = json.load(open(sys.argv[1])); pat = open(sys.argv[2], encoding="utf-8").read()
flags = 0
for ch in spec.get("flags") or "":
    flags |= getattr(re, ch)
out = {"compile_error": None, "fails": []}
try:
    rx = re.compile(pat, flags)
except re.error as e:
    out["compile_error"] = str(e)
else:
    f = rx.fullmatch if spec.get("mode", "fullmatch") == "fullmatch" else rx.search
    for key, want in (("positives", True), ("negatives", False), ("hidden_pos", True), ("hidden_neg", False)):
        for s in spec.get(key) or []:
            if bool(f(s)) != want:
                out["fails"].append([key, s])
json.dump(out, open(sys.argv[3], "w"))
'''


def compile_flags(flags: str) -> int:
    f = 0
    for ch in flags or "":
        f |= getattr(re, ch)
    return f


def true_labels(check: dict) -> list[str]:
    """Computed on the harness side from the case's own (trusted) pattern."""
    rx = re.compile(check["pattern"], compile_flags(check.get("flags", "")))
    f = rx.fullmatch if check.get("mode", "fullmatch") == "fullmatch" else rx.search
    return sorted(k for k, s in check["labels"].items() if f(s))


def grade_regex_write(iso: Isolation, area: TrialArea, check: dict, pattern: str) -> GradeResult:
    gr = GradeResult(True)
    if not pattern:
        return gr.fail("empty pattern")
    gd = area.root / "grade"
    gd.mkdir(exist_ok=True)
    (gd / "spec.json").write_text(json.dumps(check))
    (gd / "pattern.txt").write_text(pattern)
    (gd / "runner.py").write_text(RUNNER)
    out = gd / "out.json"
    res = iso.run(area, f"python3 -I {gd}/runner.py {gd}/spec.json {gd}/pattern.txt {out}", cwd=gd,
                  limits=Limits(wall_s=float(check.get("time_limit_s", 1.5)), cpu_s=2))
    if res.killed:
        gr.metrics["timeout"] = True
        return gr.fail("pattern timed out (catastrophic backtracking?)")
    try:
        r = json.loads(out.read_text())
    except (OSError, json.JSONDecodeError):
        return gr.fail("pattern check gave no result: " + res.stderr.strip()[-200:])
    if r.get("compile_error"):
        return gr.fail("pattern does not compile: " + r["compile_error"])
    fails = r.get("fails") or []
    shown = [f for f in fails if f[0] in ("positives", "negatives")]
    gr.metrics["shown_fails"] = len(shown)
    gr.metrics["hidden_fails"] = len(fails) - len(shown)
    if fails:
        k, s = fails[0]
        gr.fail(f"{len(fails)} string(s) wrong; first: {k} {s!r}")
    return gr


LABEL_RE = re.compile(r"\b([A-H])\b")


def parse_labels(answer: str) -> list[str] | None:
    for line in (answer or "").splitlines():
        line = re.sub(r"[*_`#>]", "", line).strip()
        if not line:
            continue
        if re.match(r"^(none|no ?ne of them|nothing)\b", line, re.I):
            return []
        line = re.sub(r"^(answer|matches|the matches are|matching)\s*[:\-]?\s*", "", line, flags=re.I)
        head = re.split(r"[.;:—–]|\s-\s|\bbecause\b", line, maxsplit=1)[0]
        labels = LABEL_RE.findall(head)
        return sorted(set(labels)) if labels else None
    return None


def grade_regex_read(check: dict, answer: str) -> GradeResult:
    gr = GradeResult(True)
    got = parse_labels(answer)
    if got is None:
        gr.metrics["extraction_failed"] = True
        return gr.fail("answer does not start with labels")
    want = sorted(check["answer"])
    gr.metrics["labels"] = ",".join(got)
    if got != want:
        gr.fail(f"named {got}, true set {want}")
    return gr
