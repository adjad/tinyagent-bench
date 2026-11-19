"""Host-driven probes for the ladder's probe rungs: the verdict never comes from the candidate's process.

A probe computes values and never compares. The kinds:

    {"kind": "call", "mod": m, "fn": f, "args": [...], "kwargs": {...}}
    {"kind": "ops", "mod": m, "cls": C, "init": [...], "steps": [[method, [args]], ...]}
    {"kind": "run", "argv": ["report.py", "input.csv"]}        a script run as `python3 <argv>`
    {"kind": "script", "src": "<python>"}    host-authored; records with rec(value) / rec_call(fn, *a, **k)

Host-only keys, stripped before the driver sees a probe: "raises" (an exception name the candidate may raise
for the probe to match: any class in its MRO), "tol_abs" (absolute float tolerance), "allow" (constants the
leak gate accepts in this probe's source, each one reviewed), "id".

The driver runs in the sandbox, once on the candidate's files in the trial's area and once on the template's
own solution in a fresh area of its own (cached). It never holds an expected value, a reference or a
hidden test: there is nothing in its process for the candidate to read but the inputs. Its reply is
untrusted: sizes and depth are capped, only plain JSON values are accepted, and a malformed outcome is a
failed probe, never an error.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math

from edat.sidekick.sandbox.run import Isolation, Limits, TrialArea

HOST_ONLY = ("raises", "tol_abs", "allow", "id", "checks", "test", "hand")
REPLY_BYTES = 16 << 20
MAX_DEPTH = 40
MAX_NODES = 400_000
MAX_STR = 200_000

DRIVER = r'''
import contextlib, copy, importlib, io, json, math, os, random, subprocess, sys
spec = json.load(open(sys.argv[1]))
out_path = sys.argv[2]
MODS = spec["modules"]
sys.path.insert(0, os.getcwd())
sys.dont_write_bytecode = True

def norm(v, d=0):
    if d > 30:
        return {"__deep__": True}
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, str):
        return v[:100000]
    if isinstance(v, float):
        return v if math.isfinite(v) else {"__num__": repr(v)}
    if isinstance(v, (list, tuple)):
        return [norm(x, d + 1) for x in list(v)[:5000]]
    if isinstance(v, dict):
        items = [(norm(k, d + 1), norm(x, d + 1)) for k, x in list(v.items())[:5000]]
        if all(isinstance(k, str) for k, _ in items):
            return dict(items)
        return {"__map__": sorted([[k, x] for k, x in items], key=lambda p: json.dumps(p, sort_keys=True, default=str))}
    if isinstance(v, (set, frozenset)):
        return {"__set__": sorted([norm(x, d + 1) for x in list(v)[:5000]], key=lambda x: json.dumps(x, sort_keys=True, default=str))}
    if isinstance(v, BaseException):
        return {"__exc__": [c.__name__ for c in type(v).__mro__][:20]}
    if hasattr(v, "__dict__") and not callable(v):
        return {"__obj__": type(v).__name__, "attrs": norm({k: x for k, x in vars(v).items() if not k.startswith("__")}, d + 1)}
    return {"__type__": type(v).__name__}

def fresh():
    for m in list(sys.modules):
        if m.split(".")[0] in MODS:
            del sys.modules[m]
    importlib.invalidate_caches()

def outcome(thunk):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return {"ok": norm(thunk())}
    except BaseException as e:
        return {"raise": [c.__name__ for c in type(e).__mro__][:20]}

def run(p):
    fresh()
    k = p["kind"]
    if k == "call":
        def f():
            fn = getattr(importlib.import_module(p["mod"]), p["fn"])
            return fn(*copy.deepcopy(p.get("args", [])), **copy.deepcopy(p.get("kwargs", {})))
        return outcome(f)
    if k == "ops":
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                obj = getattr(importlib.import_module(p["mod"]), p["cls"])(*copy.deepcopy(p.get("init", [])))
        except BaseException as e:
            return {"raise": [c.__name__ for c in type(e).__mro__][:20]}
        return {"ok": [outcome(lambda s=s: getattr(obj, s[0])(*copy.deepcopy(s[1]))) for s in p["steps"]]}
    if k == "run":
        try:
            r = subprocess.run([sys.executable, *p["argv"]], capture_output=True, text=True,
                               timeout=float(p.get("timeout", 10)))
        except subprocess.TimeoutExpired:
            return {"raise": ["TimeoutExpired"]}
        return {"ok": {"rc": r.returncode, "out": r.stdout[:200000]}}
    if k == "script":
        recs = []
        def rec(v, i=0):
            if len(recs) < 20000:
                recs.append([i, {"ok": norm(v)}])
        def rec_exc(e, i=0):
            recs.append([i, {"raise": [c.__name__ for c in type(e).__mro__][:20]} if e is not None
                         else {"ok": {"__no_raise__": True}}])
        def rec_call(fn, *a, **kw):
            recs.append([0, outcome(lambda: fn(*a, **kw))])
        def call_outcome(fn, args):
            args = copy.deepcopy(args)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    return ["ok", fn(*args), args]
            except Exception as exc:
                return ["error", type(exc).__name__]
        ns = {"rec": rec, "rec_exc": rec_exc, "rec_call": rec_call, "call_outcome": call_outcome,
              "random": random, "copy": copy,
              "math": math, "importlib": importlib, "__name__": "probe"}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(p["src"], "probe", "exec"), ns)
        except BaseException as e:
            recs.append([-1, {"raise": [c.__name__ for c in type(e).__mro__][:20]}])
        return {"ok": recs}
    return {"raise": ["UnknownProbe"]}

results = []
for p in spec["probes"]:
    try:
        results.append(run(p))
    except BaseException as e:
        results.append({"raise": [type(e).__name__]})
with open(out_path, "w") as fh:
    json.dump({"probes": results}, fh)
'''


def driver_probe(p: dict) -> dict:
    """A probe as the driver gets it: no host-only key."""
    return {k: v for k, v in p.items() if k not in HOST_ONLY}


def modules_of(check: dict) -> list[str]:
    names = set(check.get("targets") or []) | set(check.get("files") or {}) | set(check.get("solution") or {})
    return sorted({n[:-3] for n in names if n.endswith(".py")})


def drive(iso: Isolation, area: TrialArea, check: dict, code: dict[str, str]) -> list[dict | None]:
    """Lay out the given files, `code` (the candidate's or the solution's) and the hidden inputs in `area`,
    run the driver there, and return one outcome per probe (None where the reply was malformed or absent)."""
    probes = check["probes"]
    area.write_files(dict(check.get("files") or {}))
    area.write_files(dict(code))
    area.write_files(dict(check.get("hidden_files") or {}))
    spec = json.dumps({"modules": modules_of(check), "probes": [driver_probe(p) for p in probes]})
    gd = area.grade_dir({"spec.json": spec, "driver.py": DRIVER})
    res = iso.run(area, f"{area.bin}/python3 -I {gd}/driver.py {gd}/spec.json {gd}/reply.json",
                  limits=Limits(wall_s=float(check.get("time_limit_s", 20.0)), cpu_s=30))
    raw = None if res.killed else area.read_result(gd / "reply.json", REPLY_BYTES)
    out = parse_reply(raw, len(probes))
    if res.killed:
        out = [{"killed": res.killed}] * len(probes)
    return out


def parse_reply(raw: bytes | None, n: int) -> list[dict | None]:
    """The driver's reply, trusted for nothing: n outcomes, each a plain {"ok": value} or {"raise": [names]},
    or None."""
    if not raw:
        return [None] * n
    try:
        r = json.loads(raw)
    except (ValueError, RecursionError):
        return [None] * n
    if not isinstance(r, dict) or not isinstance(r.get("probes"), list) or len(r["probes"]) != n:
        return [None] * n
    budget = [MAX_NODES]
    return [o if _outcome_ok(o, budget) else None for o in r["probes"]]


def _outcome_ok(o, budget: list[int]) -> bool:
    if not isinstance(o, dict) or len(o) != 1:
        return False
    if "raise" in o:
        return isinstance(o["raise"], list) and 0 < len(o["raise"]) <= 20 and all(
            isinstance(x, str) and len(x) <= 200 for x in o["raise"])
    return "ok" in o and _plain(o["ok"], 0, budget)


def _plain(v, depth: int, budget: list[int]) -> bool:
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_DEPTH:
        return False
    if v is None or isinstance(v, bool):
        return True
    if isinstance(v, int):
        return abs(v) < 1 << 256
    if isinstance(v, float):
        return True
    if isinstance(v, str):
        return len(v) <= MAX_STR
    if isinstance(v, list):
        return all(_plain(x, depth + 1, budget) for x in v)
    if isinstance(v, dict):
        return all(isinstance(k, str) and len(k) <= MAX_STR and _plain(x, depth + 1, budget) for k, x in v.items())
    return False


def same(a, b, tol_abs: float | None = None) -> bool:
    """Plain values equal, floats within a relative 1e-9 (or `tol_abs`), bool never equal to a number."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) or isinstance(b, float):
            if math.isnan(a) and math.isnan(b):
                return True
            if math.isinf(a) or math.isinf(b):
                return a == b
            if tol_abs is not None:
                return abs(a - b) <= tol_abs
            return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y, tol_abs) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k], tol_abs) for k in a)
    return type(a) is type(b) and a == b


def outcome_matches(got: dict | None, want: dict, probe: dict) -> bool:
    """One outcome against the solution's. An exception matches by its class name (or, with "raises", any
    class in its MRO); an op sequence or a script's records match step by step."""
    if got is None or "killed" in got:
        return False
    if "raise" in want or "raise" in got:
        if not ("raise" in want and "raise" in got):
            return False
        if probe.get("raises"):
            return probe["raises"] in got["raise"]
        return got["raise"][0] == want["raise"][0]
    if probe.get("kind") == "script":
        return _script_matches(got["ok"], want["ok"], probe)
    if probe.get("kind") == "ops":
        g, w = got["ok"], want["ok"]
        if not (isinstance(g, list) and isinstance(w, list) and len(g) == len(w)):
            return False
        sub = {k: v for k, v in probe.items() if k in ("raises", "tol_abs")}
        return all(isinstance(x, dict) and _outcome_ok(x, [MAX_NODES]) and outcome_matches(x, y, sub)
                   for x, y in zip(g, w))
    return same(got["ok"], want["ok"], probe.get("tol_abs"))


def _script_matches(g, w, probe: dict) -> bool:
    """A script's records against the solution's: the same sequence of checks, each record passing its
    host-only check (a literal, a comparison, an exception class) or, for "ref", equal to the solution's."""
    checks = probe.get("checks")
    if not (isinstance(g, list) and isinstance(w, list) and len(g) == len(w)):
        return False
    for x, y in zip(g, w):
        if not (isinstance(x, list) and len(x) == 2 and isinstance(x[0], int) and isinstance(x[1], dict)
                and _outcome_ok(x[1], [MAX_NODES]) and x[0] == y[0]):
            return False
        i, o = x
        if checks is None:                                   # a script without checks: equal to the solution's
            if not outcome_matches(o, y[1], {}):
                return False
            continue
        if not 0 <= i < len(checks):
            return False
        if not check_passes(checks[i], o, y[1]):
            return False
    return True


def check_passes(c: dict, o: dict, sol: dict) -> bool:
    op = c["op"]
    if op == "raises":
        return "raise" in o and c["name"] in o["raise"]
    if "raise" in o:
        return False
    v = o["ok"]
    if op == "ref":
        return "ok" in sol and same(v, sol["ok"])
    want = host_norm(c.get("value"))
    if op == "eq":
        return same(v, want)
    if op == "is":
        return v is want or (v is None and want is None) or (isinstance(v, bool) and v is want)
    if op == "approx":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v - want) <= c["abs"]
    if op == "truthy":
        return v is True
    try:
        if op == "contains":
            return _contains(v, want)
        if op == "lacks":
            return not _contains(v, want)
        if op == "cmp":
            cmp = c["cmp"]
            if cmp == "!=":
                return not same(v, want)
            if cmp in ("in", "not in"):
                inside = _contains(want, v)
                return inside if cmp == "in" else not inside
            if isinstance(v, bool) or not isinstance(v, (int, float, str)) or type(v) is not type(want) and not (
                    isinstance(v, (int, float)) and isinstance(want, (int, float))):
                return False
            return {"<": v < want, "<=": v <= want, ">": v > want, ">=": v >= want}[cmp]
    except TypeError:
        return False
    return False


def _contains(container, item) -> bool:
    if isinstance(container, str):
        return isinstance(item, str) and item in container
    if isinstance(container, list):
        return any(same(item, x) for x in container)
    if isinstance(container, dict):
        if "__set__" in container and len(container) == 1:
            return any(same(item, x) for x in container["__set__"])
        return isinstance(item, str) and item in container
    return False


def host_norm(v):
    """A host literal in the driver's normal form (tuples as lists, sets tagged and sorted)."""
    if isinstance(v, tuple):
        return [host_norm(x) for x in v]
    if isinstance(v, list):
        return [host_norm(x) for x in v]
    if isinstance(v, dict):
        if set(v) == {"__set__"}:
            return {"__set__": sorted((host_norm(x) for x in v["__set__"]), key=lambda x: json.dumps(x, sort_keys=True, default=str))}
        if all(isinstance(k, str) for k in v):
            return {k: host_norm(x) for k, x in v.items()}
        return {"__map__": sorted([[host_norm(k), host_norm(x)] for k, x in v.items()],
                                  key=lambda p: json.dumps(p, sort_keys=True, default=str))}
    if isinstance(v, (set, frozenset)):
        return {"__set__": sorted((host_norm(x) for x in v), key=lambda x: json.dumps(x, sort_keys=True, default=str))}
    if isinstance(v, float) and not math.isfinite(v):
        return {"__num__": repr(v)}
    return v


_SOLUTION_CACHE: dict[str, list[dict]] = {}


def solution_outcomes(iso: Isolation, check: dict) -> list[dict]:
    """The template's own solution, run by the same driver in a fresh area of its own. A malformed or
    missing outcome there is a harness fault (it raises), never a verdict."""
    key = hashlib.sha256(json.dumps([check.get("files"), check.get("hidden_files"), check["solution"],
                                     [driver_probe(p) for p in check["probes"]], check.get("time_limit_s")],
                                    sort_keys=True).encode()).hexdigest()
    if key not in _SOLUTION_CACHE:
        with TrialArea() as ra:
            out = drive(iso, ra, check, check["solution"])
        if any(o is None or "killed" in o for o in out):
            raise RuntimeError("the solution did not run every probe")
        _SOLUTION_CACHE[key] = out
    return _SOLUTION_CACHE[key]


# ------------------------------------------------------------------ the leak gate (tests only)

def _scalars(v, out: set) -> None:
    if isinstance(v, bool) or v is None:
        return
    if isinstance(v, (int, float, str)):
        out.add(v)
    elif isinstance(v, list):
        for x in v:
            _scalars(x, out)
    elif isinstance(v, dict):
        for k, x in v.items():
            _scalars(x, out)


def trivial(v) -> bool:
    """Values too common to leak anything: small whole numbers and very short strings."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v).is_integer() and -10 <= v <= 10
    return isinstance(v, str) and len(v.strip()) <= 1


def source_constants(p: dict) -> set:
    """The constants a probe carries into the candidate's process."""
    out: set = set()
    if p.get("kind") == "script":
        for node in ast.walk(ast.parse(p["src"])):
            if isinstance(node, ast.Constant) and not isinstance(node.value, (bool, type(None), bytes)):
                out.add(node.value)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.update(_words(node.value))
    else:
        _scalars(driver_probe(p), out)
    return out


def _words(s: str) -> set:
    return {s} | {w for w in s.split() if len(w) > 3}


def leaked_constants(probes: list[dict], outcomes: list[dict]) -> list[tuple[int, object]]:
    """(probe index, value) for every value the solution records in a script probe that also appears as a
    constant in that probe's source, other than trivial values and the probe's reviewed "allow" list. Call,
    op and run probes carry only their inputs by construction; a value they return that equals one of their
    own inputs (an echo) is not a leak."""
    bad = []
    for i, (p, o) in enumerate(zip(probes, outcomes)):
        if p.get("kind") != "script":
            continue
        rec: set = set()
        _scalars(o, rec)
        for c in source_constants(p) & rec:
            if not trivial(c) and c not in (p.get("allow") or []):
                bad.append((i, c))
    return bad
