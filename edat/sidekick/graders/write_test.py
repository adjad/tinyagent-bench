"""Grader for model-written unit tests (spec 12 §3.2, `write_test`).

Check spec:
    lang:       python | javascript
    module:     module file stem the tests import (leap -> leap.py / leap.js)
    entry:      the function under test
    impl:       correct module source
    mutants:    5 module sources, each a plausible bug (same API)
    test_file:  file name the model's tests are saved as (test_leap.py / leap.test.js)
    min_caught: 3

Pass: the tests pass on the correct code and at least `min_caught` of the 5 mutants make some test
fail. Each run is decided by its exit code alone. The mutation score
(caught / 5) is partial credit (SK-9).
"""
from __future__ import annotations

import random
import re

from edat.sidekick.graders.common import GradeResult
from edat.sidekick.sandbox.run import ExecResult, Isolation, Limits

_rng = random.SystemRandom()


def _command(check: dict) -> str:
    if check["lang"] == "python":
        return f"python3 -m pytest -q -p no:cacheprovider -x {check['test_file']}"
    return f"node --test {check['test_file']}"


def _count_passed(check: dict, res: ExecResult) -> int:
    """The test runner's own count, for the metrics only: the output is the written tests' to forge, so the
    verdict never rests on it."""
    text = (res.stdout[-20000:] + "\n" + res.stderr[-20000:])
    if check["lang"] == "python":
        m = re.search(r"(\d{1,6}) passed", text)
        return int(m.group(1)) if m else 0
    m = re.search(r"^# pass (\d{1,6})", text, re.M) or re.search(r"ℹ pass (\d{1,6})", text)
    return int(m.group(1)) if m else 0


def _run_with(iso: Isolation, area_factory, check: dict, module_src: str, tests_src: str) -> ExecResult:
    """One run of the written tests, in an area of its own: nothing a run leaves (in the folder, the home
    folder, the temp folder) reaches the next."""
    ext = ".py" if check["lang"] == "python" else ".js"
    files = {f"{check['module']}{ext}": module_src, check["test_file"]: tests_src}
    if check["lang"] == "javascript":
        files["package.json"] = '{"type": "commonjs"}\n'
    with area_factory() as area:
        for rel, text in files.items():
            if not area.write_inside(rel, text):
                raise RuntimeError(f"could not lay out {rel} for the written tests")
        res = iso.run(area, _command(check), limits=Limits(wall_s=10))
        area.seal()
    return res


def grade_write_test(iso: Isolation, area_factory, check: dict, tests_src: str) -> GradeResult:
    """The correct module and every mutant each get a fresh area, in an order drawn on the host side, so the
    tests cannot learn which run is which from anything but the code they test. A run's verdict is its exit
    code (and whether it was stopped), nothing it prints."""
    gr = GradeResult(True)
    if not tests_src.strip():
        return gr.fail("no tests")
    mutants = list(check["mutants"])
    order = [None] + list(range(len(mutants)))
    _rng.shuffle(order)
    runs: dict = {}
    for k in order:
        runs[k] = _run_with(iso, area_factory, check, check["impl"] if k is None else mutants[k], tests_src)
    res = runs[None]
    n = _count_passed(check, res)
    gr.metrics["tests_on_correct"] = n
    # The count can only be forged upward, so a zero still fails the trial safely.
    if res.exit_code != 0 or res.killed or n == 0:
        gr.metrics["mutation_score"] = None
        why = "no test ran" if n == 0 and res.exit_code in (0, 5) else "tests fail on the correct code"
        tail = (res.stdout + res.stderr).strip().splitlines()[-1:] or [""]
        return gr.fail(f"{why}: {tail[0][:160]}")
    caught = sum(1 for i in range(len(mutants)) if runs[i].exit_code != 0 or runs[i].killed)
    total = len(mutants)
    gr.metrics["mutants_caught"] = caught
    gr.metrics["mutation_score"] = caught / total if total else None
    if caught < check.get("min_caught", 3):
        gr.fail(f"caught {caught} of {total} mutants")
    return gr
