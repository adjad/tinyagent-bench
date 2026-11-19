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

All of it runs inside the sandbox in one process per language with a 10 s
limit for the whole file.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from edat.sidekick.graders.common import GradeResult
from edat.sidekick.sandbox.run import Isolation, Limits, TrialArea

PY_RUNNER = r'''
import copy, io, json, math, sys, contextlib
spec = json.load(open(sys.argv[1]))
cand_src = open(sys.argv[2]).read()
TOL = spec.get("float_tol", 1e-9)

def norm(v):
    try:
        return json.loads(json.dumps(v))
    except (TypeError, ValueError):
        return {"__repr__": repr(v)}

def eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) or isinstance(b, float):
            if math.isnan(a) and math.isnan(b):
                return True
            return abs(a - b) <= TOL * max(1.0, abs(a), abs(b))
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(eq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(eq(a[k], b[k]) for k in a)
    return a == b

def load(src):
    ns = {"__name__": "candidate"}
    with contextlib.redirect_stdout(io.StringIO()):
        exec(compile((spec.get("prelude") or "") + "\n" + src, "solution.py", "exec"), ns)
    return ns

def run_test(src, t):
    ns = load(src)
    with contextlib.redirect_stdout(io.StringIO()):
        if "script" in t:
            exec(t["script"], ns)
            return ns.get("result")
        fn = ns[spec["entry"]]
        calls = t["calls"] if "calls" in t else [t.get("args", [])]
        res = None
        for args in calls:
            res = fn(*copy.deepcopy(args))
        return res

def check(src, t):
    try:
        got = run_test(src, t)
    except Exception as e:
        names = [c.__name__ for c in type(e).__mro__]
        if "raises" in t and t["raises"] in names:
            return True, None
        return False, f"{type(e).__name__}: {str(e)[:120]}"
    if "raises" in t:
        return False, f"expected {t['raises']}, returned {norm(got)!r}"[:200]
    ok = eq(norm(got), t["expect"])
    return ok, None if ok else f"got {norm(got)!r}, expected {t['expect']!r}"[:200]

out = {"load_error": None, "visible": [], "hidden": [], "diff": {"n": 0, "mismatch": 0, "first": None}}
try:
    load(cand_src)
except BaseException as e:
    out["load_error"] = f"{type(e).__name__}: {str(e)[:200]}"
if out["load_error"] is None:
    for key, src_key in (("visible", "visible_tests"), ("hidden", "tests")):
        for t in spec.get(src_key) or []:
            ok, why = check(cand_src, t)
            out[key].append({"ok": ok, "why": why})
    ref = spec.get("reference")
    inputs = spec.get("diff_inputs") or []
    if ref and inputs:
        try:
            cns, rns = load(cand_src), load(ref)
            cf, rf = cns[spec["entry"]], rns[spec["entry"]]
        except Exception as e:
            out["diff"]["first"] = f"load: {type(e).__name__}"
            out["diff"]["mismatch"] = len(inputs)
            out["diff"]["n"] = len(inputs)
            inputs = []
        for args in inputs:
            out["diff"]["n"] += 1
            def call(f):
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        return ("ok", norm(f(*copy.deepcopy(args))))
                except Exception as e:
                    return ("raise", type(e).__name__)
            a, b = call(cf), call(rf)
            same = a[0] == b[0] and (eq(a[1], b[1]) if a[0] == "ok" else a[1] == b[1])
            if not same:
                out["diff"]["mismatch"] += 1
                if out["diff"]["first"] is None:
                    out["diff"]["first"] = f"input {args!r}: got {a!r}, reference {b!r}"[:200]
json.dump(out, open(sys.argv[3], "w"))
'''

JS_RUNNER = r'''
const fs = require('fs');
const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const candSrc = fs.readFileSync(process.argv[3], 'utf8');
const TOL = spec.float_tol ?? 1e-9;
const silent = { log() {}, error() {}, warn() {}, info() {}, debug() {} };

function stripEsm(src) {
  return src.replace(/^\s*import\s+[^;\n]*;?\s*$/gm, '')
            .replace(/^(\s*)export\s+default\s+/gm, '$1')
            .replace(/^(\s*)export\s+(?=(async\s+)?function|const|let|var|class)/gm, '$1');
}
function load(src) {
  const module = { exports: {} };
  const name = spec.entry;
  const body = (spec.prelude || '') + '\n' + stripEsm(src) +
    `\n;return (typeof ${name} !== 'undefined') ? ${name} : (module.exports && (module.exports.${name} || module.exports));`;
  const f = new Function('module', 'exports', 'require', 'console', body);
  const fn = f(module, module.exports, require, silent);
  return fn;
}
function norm(v) {
  if (v === undefined) return null;
  // Infinity, -Infinity and NaN would all become null in JSON: keep them apart.
  const rep = (k, x) => (typeof x === 'number' && !Number.isFinite(x)) ? { __num__: String(x) } : x;
  try { return JSON.parse(JSON.stringify(v, rep)); } catch (e) { return { __repr__: String(v) }; }
}
function eq(a, b) {
  if (typeof a === 'number' && typeof b === 'number') {
    if (Number.isNaN(a) && Number.isNaN(b)) return true;
    if (Number.isInteger(a) && Number.isInteger(b)) return a === b;
    return Math.abs(a - b) <= TOL * Math.max(1, Math.abs(a), Math.abs(b));
  }
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => eq(x, b[i]));
  if (a && b && typeof a === 'object' && typeof b === 'object' && !Array.isArray(a) && !Array.isArray(b)) {
    const ka = Object.keys(a).sort(), kb = Object.keys(b).sort();
    return ka.length === kb.length && ka.every((k, i) => k === kb[i] && eq(a[k], b[k]));
  }
  return a === b;
}
const clone = (x) => JSON.parse(JSON.stringify(x));
async function runTest(src, t) {
  const fn = load(src);
  if (t.script !== undefined) {
    const f = new Function(spec.entry, 'console', `return (async () => { let result; ${t.script}; return result; })();`);
    return await f(fn, silent);
  }
  const calls = t.calls || [t.args || []];
  let res;
  for (const args of calls) { res = await fn(...clone(args)); }
  return res;
}
function errName(e) { return (e && (e.name || (e.constructor && e.constructor.name))) || typeof e; }
async function check(src, t) {
  let got;
  try { got = await runTest(src, t); }
  catch (e) {
    if (t.raises !== undefined && (errName(e) === t.raises || (t.raises === 'Error' && e instanceof Error))) return [true, null];
    return [false, `${errName(e)}: ${String(e && e.message).slice(0, 120)}`];
  }
  if (t.raises !== undefined) return [false, `expected ${t.raises}, returned ${JSON.stringify(norm(got))}`.slice(0, 200)];
  const ok = eq(norm(got), t.expect);
  return [ok, ok ? null : `got ${JSON.stringify(norm(got))}, expected ${JSON.stringify(t.expect)}`.slice(0, 200)];
}
(async () => {
  const out = { load_error: null, visible: [], hidden: [], diff: { n: 0, mismatch: 0, first: null } };
  try { const fn = load(candSrc); if (typeof fn !== 'function') throw new Error(`no function named ${spec.entry}`); }
  catch (e) { out.load_error = `${errName(e)}: ${String(e && e.message).slice(0, 200)}`; }
  if (out.load_error === null) {
    for (const [key, sk] of [['visible', 'visible_tests'], ['hidden', 'tests']]) {
      for (const t of spec[sk] || []) { const [ok, why] = await check(candSrc, t); out[key].push({ ok, why }); }
    }
    const inputs = spec.diff_inputs || [];
    if (spec.reference && inputs.length) {
      let cf, rf;
      try { cf = load(candSrc); rf = load(spec.reference); } catch (e) { out.diff = { n: inputs.length, mismatch: inputs.length, first: 'load' }; }
      if (cf && rf) for (const args of inputs) {
        out.diff.n++;
        const call = async (f) => { try { return ['ok', norm(await f(...clone(args)))]; } catch (e) { return ['raise', errName(e)]; } };
        const a = await call(cf), b = await call(rf);
        const same = a[0] === b[0] && (a[0] === 'ok' ? eq(a[1], b[1]) : a[1] === b[1]);
        if (!same) { out.diff.mismatch++; if (out.diff.first === null) out.diff.first = `input ${JSON.stringify(args)}: got ${JSON.stringify(a)}, reference ${JSON.stringify(b)}`.slice(0, 200); }
      }
    }
  }
  fs.writeFileSync(process.argv[4], JSON.stringify(out));
})().catch((e) => { fs.writeFileSync(process.argv[4], JSON.stringify({ load_error: 'runner: ' + String(e) })); });
'''


def run_code_tests(iso: Isolation, area: TrialArea, check: dict, candidate_src: str) -> dict:
    """Run visible + hidden tests and the differential check; returns the runner's JSON."""
    lang = check["lang"]
    if lang == "python":
        gd = area.grade_dir({"spec.json": json.dumps(check), "solution.py": candidate_src, "runner.py": PY_RUNNER})
        out = gd / "result.json"
        script = f"python3 -I {gd}/runner.py {gd}/spec.json {gd}/solution.py {out}"
    elif lang == "javascript":
        gd = area.grade_dir({"spec.json": json.dumps(check), "solution.js": candidate_src, "runner.js": JS_RUNNER})
        out = gd / "result.json"
        script = f"node {gd}/runner.js {gd}/spec.json {gd}/solution.js {out}"
    else:
        raise ValueError(f"unknown language {lang}")
    res = iso.run(area, script, cwd=gd, limits=Limits(wall_s=float(check.get("time_limit_s", 10.0))))
    if res.killed:
        return {"load_error": f"stopped: {res.killed}", "visible": [], "hidden": [], "diff": {}}
    try:
        return json.loads(area.read_result(out) or b"")  # never through a link
    except (OSError, json.JSONDecodeError):
        return {"load_error": "runner gave no result: " + res.stderr.strip()[-300:], "visible": [], "hidden": [], "diff": {}}


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
