"""Code grader for `write_fn` and `fix_bug` (spec 12 §3.2).

Check spec (`Instance.check`):

    lang:          python | javascript
    entry:         the function's name
    module:        file stem used in tool mode and in the visible test file
    prelude:       optional source placed before the candidate (imports the prompt shows)
    tests:         hidden tests (8-15 for write_fn; regression tests for fix_bug)
    visible_tests: tests shown to the model (fix_bug: the failing test); also must pass
    reference:     hidden correct source (same language)
    diff_inputs:   argument lists for the differential check (200 for write_fn), may be empty
    float_tol:     optional absolute tolerance for floats (default 1e-9)
    keep_names:    fix_bug, Python: {name: source} of other top-level definitions that
                   must still exist unchanged

A test is one of:
    {"args": [...], "expect": value}
    {"args": [...], "raises": "ValueError"}            # exception class name (or JS error name)
    {"calls": [[...], [...]], "expect": value}         # several calls on one fresh module; last result
    {"script": "<code that sets result>", "expect": value}   # free-form; may use the entry name
Every test runs on a fresh load of the code, so state does not leak between tests.

The code runs inside the sandbox, in a driver that only reports what each call returned or raised; the
expected values, the reference and the verdict stay on the host side (the reference runs in a fresh area of
its own). One driver process per language, with a 10 s limit for the whole file.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path

from edat.sidekick.graders.common import GradeResult
from edat.sidekick.sandbox.run import Isolation, Limits, TrialArea

# The driver runs the code under test and reports what each call returned or raised, nothing more. It never
# sees an expected value or the reference: the host keeps those and decides (the candidate's code runs in
# the driver's process, so anything the driver could compute, the candidate could forge). The reference's
# outputs for the differential check come from the same driver run on the reference in a fresh area.
PY_DRIVER = r"""
import copy, io, json, sys, contextlib
spec = json.load(open(sys.argv[1]))
src = open(sys.argv[2]).read()

def norm(v):
    try:
        return json.loads(json.dumps(v))
    except (TypeError, ValueError):
        return {"__repr__": repr(v)[:200]}

def load():
    ns = {"__name__": "candidate"}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile((spec.get("prelude") or "") + "\n" + src, "solution.py", "exec"), ns)
    return ns

def outcome(thunk):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return {"ok": norm(thunk())}
    except Exception as e:
        return {"raise": [c.__name__ for c in type(e).__mro__][:20], "msg": str(e)[:120]}

def run_test(t):
    ns = load()
    if "script" in t:
        exec(t["script"], ns)
        return ns.get("result")
    fn = ns[spec["entry"]]
    res = None
    for args in (t["calls"] if "calls" in t else [t.get("args", [])]):
        res = fn(*copy.deepcopy(args))
    return res

out = {"load_error": None, "tests": [], "diff": []}
try:
    load()
except BaseException as e:
    out["load_error"] = f"{type(e).__name__}: {str(e)[:200]}"
if out["load_error"] is None:
    out["tests"] = [outcome(lambda t=t: run_test(t)) for t in spec.get("tests") or []]
    inputs = spec.get("diff_inputs") or []
    if inputs:
        try:
            fn = load()[spec["entry"]]
        except Exception as e:
            out["diff"] = [{"raise": ["load"], "msg": type(e).__name__}] * len(inputs)
        else:
            out["diff"] = [outcome(lambda a=a: fn(*copy.deepcopy(a))) for a in inputs]
json.dump(out, open(sys.argv[3], "w"))
"""

JS_DRIVER = r"""
const fs = require('fs');
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const src = fs.readFileSync(process.argv[3], 'utf8');
const silent = { log() {}, error() {}, warn() {}, info() {}, debug() {} };

function stripEsm(s) {
  return s.replace(/^\s*import\s+[^;\n]*;?\s*$/gm, '')
          .replace(/^(\s*)export\s+default\s+/gm, '$1')
          .replace(/^(\s*)export\s+(?=(async\s+)?function|const|let|var|class)/gm, '$1');
}
function load() {
  const module = { exports: {} };
  const name = spec.entry;
  const body = (spec.prelude || '') + '\n' + stripEsm(src) +
    `\n;return (typeof ${name} !== 'undefined') ? ${name} : (module.exports && (module.exports.${name} || module.exports));`;
  return new Function('module', 'exports', 'require', 'console', body)(module, module.exports, require, silent);
}
function norm(v) {
  if (v === undefined) return null;
  // Infinity, -Infinity and NaN would all become null in JSON: keep them apart.
  const rep = (k, x) => (typeof x === 'number' && !Number.isFinite(x)) ? { __num__: String(x) } : x;
  try { return JSON.parse(JSON.stringify(v, rep)); } catch (e) { return { __repr__: String(v).slice(0, 200) }; }
}
function errName(e) { return (e && (e.name || (e.constructor && e.constructor.name))) || typeof e; }
async function outcome(thunk) {
  try { return { ok: norm(await thunk()) }; }
  catch (e) {
    const names = [String(errName(e))];
    if (e instanceof Error) names.push('Error');
    return { raise: names, msg: String(e && e.message).slice(0, 120) };
  }
}
const clone = (x) => JSON.parse(JSON.stringify(x));
async function runTest(t) {
  const fn = load();
  if (t.script !== undefined) {
    const f = new Function(spec.entry, 'console', `return (async () => { let result; ${t.script}; return result; })();`);
    return await f(fn, silent);
  }
  let res;
  for (const args of (t.calls || [t.args || []])) { res = await fn(...clone(args)); }
  return res;
}
(async () => {
  const out = { load_error: null, tests: [], diff: [] };
  try { if (typeof load() !== 'function') throw new Error(`no function named ${spec.entry}`); }
  catch (e) { out.load_error = `${errName(e)}: ${String(e && e.message).slice(0, 200)}`; }
  if (out.load_error === null) {
    for (const t of spec.tests || []) out.tests.push(await outcome(() => runTest(t)));
    const inputs = spec.diff_inputs || [];
    if (inputs.length) {
      let fn = null;
      try { fn = load(); } catch (e) { out.diff = inputs.map(() => ({ raise: ['load'], msg: errName(e) })); }
      if (fn) for (const a of inputs) out.diff.push(await outcome(() => fn(...clone(a))));
    }
  }
  fs.writeFileSync(process.argv[4], JSON.stringify(out));
})().catch((e) => { fs.writeFileSync(process.argv[4], JSON.stringify({ load_error: 'driver: ' + String(e).slice(0, 200) })); });
"""


def _call_only(t: dict) -> dict:
    """A test as the driver gets it: what to call, never what to expect."""
    return {k: t[k] for k in ("args", "calls", "script") if k in t}


def _drive(iso: Isolation, area: TrialArea, check: dict, source: str, tests: list[dict], inputs: list) -> dict:
    """Run the driver on `source` in `area`; its report, checked for shape (a malformed report is a load
    error, never a pass)."""
    lang = check["lang"]
    spec = json.dumps({"entry": check["entry"], "prelude": check.get("prelude") or "",
                       "tests": [_call_only(t) for t in tests], "diff_inputs": inputs})
    if lang == "python":
        gd = area.grade_dir({"spec.json": spec, "solution.py": source, "driver.py": PY_DRIVER})
        script = f"{area.bin}/python3 -I {gd}/driver.py {gd}/spec.json {gd}/solution.py {gd}/out.json"
    elif lang == "javascript":
        gd = area.grade_dir({"spec.json": spec, "solution.js": source, "driver.js": JS_DRIVER})
        script = f"{area.bin}/node {gd}/driver.js {gd}/spec.json {gd}/solution.js {gd}/out.json"
    else:
        raise ValueError(f"unknown language {lang}")
    res = iso.run(area, script, cwd=gd, limits=Limits(wall_s=float(check.get("time_limit_s", 10.0))))
    if res.killed:
        return {"load_error": f"stopped: {res.killed}", "tests": [], "diff": []}
    try:
        r = json.loads(area.read_result(gd / "out.json") or b"")  # never through a link
    except (OSError, ValueError):
        return {"load_error": "the code gave no result: " + res.stderr.strip()[-300:], "tests": [], "diff": []}
    if not isinstance(r, dict):
        return {"load_error": "the code gave a malformed result", "tests": [], "diff": []}
    if r.get("load_error"):
        return {"load_error": str(r["load_error"])[:300], "tests": [], "diff": []}
    t_out, d_out = r.get("tests"), r.get("diff")
    if (not isinstance(t_out, list) or len(t_out) != len(tests) or not isinstance(d_out, list)
            or len(d_out) != len(inputs) or not all(_outcome_ok(o) for o in t_out + d_out)):
        return {"load_error": "the code gave a malformed result", "tests": [], "diff": []}
    return {"load_error": None, "tests": t_out, "diff": d_out}


def _outcome_ok(o) -> bool:
    if not isinstance(o, dict):
        return False
    if "raise" in o:
        return isinstance(o["raise"], list) and all(isinstance(x, str) for x in o["raise"])
    return "ok" in o


def _eq(a, b, tol: float) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) or isinstance(b, float):
            if math.isnan(a) and math.isnan(b):
                return True
            if math.isinf(a) or math.isinf(b):
                return a == b
            return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_eq(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_eq(a[k], b[k], tol) for k in a)
    return type(a) is type(b) and a == b


def _short(v) -> str:
    return json.dumps(v)[:80] if not isinstance(v, str) else repr(v[:80])


def _judge(t: dict, o: dict, tol: float) -> tuple[bool, str | None]:
    """Host side: one test's outcome against its expectation."""
    if "raise" in o:
        if "raises" in t and t["raises"] in o["raise"]:
            return True, None
        return False, f"{(o['raise'] or ['?'])[0]}: {str(o.get('msg', ''))[:120]}"[:200]
    if "raises" in t:
        return False, f"expected {t['raises']}, returned {_short(o['ok'])}"[:200]
    ok = _eq(o["ok"], t.get("expect"), tol)
    return ok, None if ok else f"got {_short(o['ok'])}, expected {_short(t.get('expect'))}"[:200]


_REF_CACHE: dict[str, list] = {}


def _reference_outputs(iso: Isolation, check: dict) -> list | None:
    """The reference's outcomes on the differential inputs, from its own fresh area (and process). Cached:
    the same case's reference gives the same outcomes on every trial."""
    inputs = check.get("diff_inputs") or []
    key = hashlib.sha256(json.dumps([check["lang"], check["entry"], check.get("prelude"), check["reference"],
                                     inputs], sort_keys=True).encode()).hexdigest()
    if key not in _REF_CACHE:
        with TrialArea() as ra:
            r = _drive(iso, ra, check, check["reference"], [], inputs)
        if r["load_error"]:
            return None
        _REF_CACHE[key] = r["diff"]
    return _REF_CACHE[key]


def run_code_tests(iso: Isolation, area: TrialArea, check: dict, candidate_src: str) -> dict:
    """Visible + hidden tests and the differential check, decided on the host side."""
    tol = float(check.get("float_tol", 1e-9))
    vis_t, hid_t = list(check.get("visible_tests") or []), list(check.get("tests") or [])
    ref, inputs = check.get("reference"), list(check.get("diff_inputs") or [])
    if not (ref and inputs):
        inputs = []
    r = _drive(iso, area, check, candidate_src, vis_t + hid_t, inputs)
    out = {"load_error": r["load_error"], "visible": [], "hidden": [], "diff": {"n": 0, "mismatch": 0, "first": None}}
    if r["load_error"]:
        return out
    judged = [_judge(t, o, tol) for t, o in zip(vis_t + hid_t, r["tests"])]
    out["visible"] = [{"ok": ok, "why": why} for ok, why in judged[:len(vis_t)]]
    out["hidden"] = [{"ok": ok, "why": why} for ok, why in judged[len(vis_t):]]
    if inputs:
        want = _reference_outputs(iso, check)
        if want is None or len(want) != len(inputs):
            raise RuntimeError("the reference did not run")      # a harness fault: a grader_error, not a pass
        d = out["diff"]
        for args, a, b in zip(inputs, r["diff"], want):
            d["n"] += 1
            same = (("raise" in a) == ("raise" in b)
                    and (a["raise"][:1] == b["raise"][:1] if "raise" in a else _eq(a["ok"], b["ok"], tol)))
            if not same:
                d["mismatch"] += 1
                if d["first"] is None:
                    d["first"] = f"input {json.dumps(args)[:60]}: got {json.dumps(a)[:60]}, reference {json.dumps(b)[:60]}"
    return out


def _python_toplevel(src: str) -> dict[str, str] | None:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = ast.dump(node)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = ast.dump(node.value)
    return out


def grade_code(iso: Isolation, area: TrialArea, check: dict, candidate_src: str) -> GradeResult:
    gr = GradeResult(True)
    if not candidate_src.strip():
        return gr.fail("empty code")
    r = run_code_tests(iso, area, check, candidate_src)
    vis = r.get("visible") or []
    hid = r.get("hidden") or []
    diff = r.get("diff") or {}
    gr.metrics.update({
        "load_error": bool(r.get("load_error")),
        "visible_total": len(check.get("visible_tests") or []), "visible_passed": sum(1 for t in vis if t["ok"]),
        "hidden_total": len(check.get("tests") or []), "hidden_passed": sum(1 for t in hid if t["ok"]),
        "diff_n": diff.get("n", 0), "diff_mismatch": diff.get("mismatch", 0),
    })
    gr.metrics["visible_pass"] = gr.metrics["visible_passed"] == gr.metrics["visible_total"] and not r.get("load_error")
    gr.metrics["hidden_pass"] = (gr.metrics["hidden_passed"] == gr.metrics["hidden_total"]
                                 and diff.get("mismatch", 0) == 0 and not r.get("load_error"))
    if r.get("load_error"):
        return gr.fail("code did not load: " + r["load_error"])
    for kind, results in (("visible", vis), ("hidden", hid)):
        bad = [t for t in results if not t["ok"]]
        if bad:
            gr.fail(f"{len(bad)} {kind} test(s) failed; first: {bad[0]['why']}")
    expected_diff = len(check.get("diff_inputs") or [])
    if diff.get("mismatch"):
        gr.fail(f"differential check: {diff['mismatch']}/{diff.get('n')} differ; first: {diff.get('first')}")
    elif expected_diff and diff.get("n", 0) != expected_diff:
        gr.fail("differential check did not finish")
    keep = check.get("keep_names") or {}
    if keep and check.get("lang") == "python":
        top = _python_toplevel(candidate_src) or {}
        for name, src in keep.items():
            want = (_python_toplevel(src) or {}).get(name)
            if name in top and want is not None and top[name] != want:
                gr.fail(f"changed {name}, which the fix may not touch")
    return gr


# ------------------------------------------------------------- visible test files for tool mode

def _py_lit(v) -> str:
    return repr(json.loads(json.dumps(v)))


def visible_test_file(check: dict) -> tuple[str, str, str]:
    """(file name, text, command) for the visible tests, runnable with pytest or node --test."""
    mod, entry, lang = check["module"], check["entry"], check["lang"]
    tests = check.get("visible_tests") or []
    if lang == "python":
        lines = ["import pytest", f"from {mod} import {entry}", ""]
        for i, t in enumerate(tests, 1):
            lines.append(f"def test_{entry}_{i}():")
            if "script" in t:
                body = t["script"].strip().splitlines()
                lines += ["    " + l for l in body]
                lines.append(f"    assert result == {_py_lit(t.get('expect'))}")
            elif "raises" in t:
                lines.append(f"    with pytest.raises({t['raises']}):")
                lines.append(f"        {entry}(*{_py_lit(t.get('args', []))})")
            elif "calls" in t:
                for args in t["calls"][:-1]:
                    lines.append(f"    {entry}(*{_py_lit(args)})")
                lines.append(f"    assert {entry}(*{_py_lit(t['calls'][-1])}) == {_py_lit(t['expect'])}")
            else:
                lines.append(f"    assert {entry}(*{_py_lit(t.get('args', []))}) == {_py_lit(t['expect'])}")
            lines.append("")
        name = f"test_{mod}.py"
        return name, "\n".join(lines), f"python3 -m pytest -q -p no:cacheprovider {name}"
    lines = ["const test = require('node:test');", "const assert = require('node:assert');",
             f"const {{ {entry} }} = require('./{mod}.js');", ""]
    for i, t in enumerate(tests, 1):
        lines.append(f"test('{entry} {i}', async () => {{")
        if "script" in t:
            lines.append(f"  let result; {t['script']}")
            lines.append(f"  assert.deepStrictEqual(result, {json.dumps(t.get('expect'))});")
        elif "raises" in t:
            lines.append(f"  await assert.rejects(async () => {entry}(...{json.dumps(t.get('args', []))}), {{ name: {json.dumps(t['raises'])} }});")
        elif "calls" in t:
            for args in t["calls"][:-1]:
                lines.append(f"  {entry}(...{json.dumps(args)});")
            lines.append(f"  assert.deepStrictEqual(await {entry}(...{json.dumps(t['calls'][-1])}), {json.dumps(t['expect'])});")
        else:
            lines.append(f"  assert.deepStrictEqual(await {entry}(...{json.dumps(t.get('args', []))}), {json.dumps(t['expect'])});")
        lines.append("});")
        lines.append("")
    name = f"{mod}.test.js"
    return name, "\n".join(lines), f"node --test {name}"
