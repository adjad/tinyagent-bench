"""Grader for model-written unit tests (spec 12 §3.2, `write_test`).

Check spec:
    lang:       python | javascript
    module:     module file stem the tests import (leap -> leap.py / leap.js)
    entry:      the function under test
    impl:       correct module source
    mutants:    5 module sources, each a plausible bug (same API)
    test_file:  file name the model's tests are saved as (test_leap.py / leap.test.js)
    min_caught: 3

Pass: the tests pass on the correct code (and at least one test ran) and at
least `min_caught` of the 5 mutants make some test fail. The mutation score
(caught / 5) is partial credit (SK-9).
"""
from __future__ import annotations

import re

from edat.sidekick.graders.common import GradeResult
from edat.sidekick.sandbox.run import ExecResult, Isolation, Limits, TrialArea


def _command(check: dict) -> str:
    if check["lang"] == "python":
        return f"python3 -m pytest -q -p no:cacheprovider -x {check['test_file']}"
    return f"node --test {check['test_file']}"


def _count_passed(check: dict, res: ExecResult) -> int:
    text = res.stdout + "\n" + res.stderr
    if check["lang"] == "python":
        m = re.search(r"(\d+) passed", text)
        return int(m.group(1)) if m else 0
    m = re.search(r"^# pass (\d+)", text, re.M) or re.search(r"ℹ pass (\d+)", text)
    return int(m.group(1)) if m else 0


def _run_with(iso: Isolation, area: TrialArea, check: dict, module_src: str, tests_src: str) -> ExecResult:
    ext = ".py" if check["lang"] == "python" else ".js"
    for p in area.work.iterdir():
        if p.is_file():
            p.unlink()
    (area.work / f"{check['module']}{ext}").write_text(module_src)
    (area.work / check["test_file"]).write_text(tests_src)
    if check["lang"] == "javascript":
        (area.work / "package.json").write_text('{"type": "commonjs"}\n')
    return iso.run(area, _command(check), limits=Limits(wall_s=10))


def grade_write_test(iso: Isolation, area: TrialArea, check: dict, tests_src: str) -> GradeResult:
    gr = GradeResult(True)
    if not tests_src.strip():
        return gr.fail("no tests")
    res = _run_with(iso, area, check, check["impl"], tests_src)
    n = _count_passed(check, res)
    gr.metrics["tests_on_correct"] = n
    if res.exit_code != 0 or n == 0:
        gr.metrics["mutation_score"] = None
        why = "no test ran" if n == 0 and res.exit_code in (0, 5) else "tests fail on the correct code"
        tail = (res.stdout + res.stderr).strip().splitlines()[-1:] or [""]
        return gr.fail(f"{why}: {tail[0][:160]}")
    caught = 0
    for m in check["mutants"]:
        r = _run_with(iso, area, check, m, tests_src)
        if r.exit_code != 0 or r.killed:
            caught += 1
    total = len(check["mutants"])
    gr.metrics["mutants_caught"] = caught
    gr.metrics["mutation_score"] = caught / total if total else None
    if caught < check.get("min_caught", 3):
        gr.fail(f"caught {caught} of {total} mutants")
    return gr
