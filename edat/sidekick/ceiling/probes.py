"""Host-driven probes for the ladder's probe rungs: the verdict never comes from the candidate's process.

A probe computes values and never compares. The kinds:

    {"kind": "call", "mod": m, "fn": f, "args": [...], "kwargs": {...}}
    {"kind": "ops", "mod": m, "cls": C, "init": [...], "steps": [[method, [args]], ...]}
    {"kind": "run", "argv": ["report.py", "input.csv"]}        a script run as `python3 <argv>`
    {"kind": "script", "src": "<python>"}    host-authored; records with rec(value) / rec_call(fn, *a, **k)

Host-only keys, stripped before the driver sees a probe: "raises" (an exception name the candidate may raise
for the probe to match: any class in its MRO), "tol_abs" (absolute tolerance, as `pytest.approx(abs=...)`),
"seq" ("loose" where the original test compared tuples and lists alike), "allow" (constants the leak gate
accepts in this probe's source, each one reviewed), "id", and "test" and "covers" (which test of the
original hidden file, and which of its assertions, the probe stands for: see `rungs.coverage`).

The comparison rule, once: a value matches the solution's as the original test's `==` compared them, on the
values as Python had them. So a bool equals the number it stands for (True == 1), an int equals the float
of the same value, floats are compared exactly unless the probe has "tol_abs", NaN equals nothing, and a
tuple never equals a list, except under "seq": "loose" (rung 5's old tests turned every tuple into a list,
through lists and tuples only, before comparing). A script run's output is compared with one trailing
newline removed from each side, as rung 6's old `_run` did. An exception matches by its class, or with
"raises" by any class in its MRO; a built-in class is named only when it is the built-in itself, as the
driver captured it before any of the candidate's code ran, so a class of the candidate's own that is named
`ValueError` is reported as `~<module>.ValueError` and never matches the built-in.

Values cross the pipe in an envelope, both ways: each is `[tag, payload]` (none, bool, int, float, fspecial,
str, list, tuple, dict, set, obj, type, exc, deep), and a container's items are envelopes nested inside its
payload, never beside a tag. So the inputs keep their types on the way in (a tuple stays a tuple), and no
value the candidate returns, a dict with odd keys included, can pass for a tag on the way out.

The driver runs in the sandbox, once on the candidate's files in the trial's area and once on the template's
own solution in a fresh area of its own (cached). It never holds an expected value, a reference or a
hidden test: there is nothing in its process for the candidate to read but the inputs. Its reply is
untrusted: sizes and depth are capped, only well-formed envelopes are accepted, and a malformed outcome is a
failed probe, never an error.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math

from edat.sidekick.sandbox.run import Isolation, Limits, TrialArea

HOST_ONLY = ("raises", "tol_abs", "seq", "allow", "id", "checks", "test", "covers", "hand")
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

import builtins as _builtins
_REAL_EXC = {n: c for n, c in vars(_builtins).items() if isinstance(c, type) and issubclass(c, BaseException)}

def exc_names(e):
    out = []
    for c in type(e).__mro__[:20]:
        n = getattr(c, "__name__", "?")
        if _REAL_EXC.get(n) is c or c is object:
            out.append(n)
        else:
            out.append("~" + str(getattr(c, "__module__", "?"))[:80] + "." + str(getattr(c, "__qualname__", n))[:80])
    return out

def norm(v, d=0):
    if d > 30:
        return ["deep"]
    if v is None:
        return ["none"]
    if v is True or v is False:
        return ["bool", v]
    if isinstance(v, int):
        return ["int", int(v)]
    if isinstance(v, float):
        f = float(v)
        return ["float", f] if math.isfinite(f) else ["fspecial", repr(f)]
    if isinstance(v, str):
        return ["str", str(v)[:100000]]
    if isinstance(v, tuple):
        return ["tuple", [norm(x, d + 1) for x in list(v)[:5000]]]
    if isinstance(v, list):
        return ["list", [norm(x, d + 1) for x in list(v)[:5000]]]
    if isinstance(v, dict):
        return ["dict", [[norm(k, d + 1), norm(x, d + 1)] for k, x in list(v.items())[:5000]]]
    if isinstance(v, (set, frozenset)):
        return ["set", [norm(x, d + 1) for x in list(v)[:5000]]]
    if isinstance(v, BaseException):
        return ["exc", exc_names(v)]
    if hasattr(v, "__dict__") and not callable(v):
        return ["obj", str(type(v).__name__)[:200], norm({k: x for k, x in vars(v).items() if not str(k).startswith("__")}, d + 1)]
    return ["type", str(type(v).__name__)[:200]]

def dec(e):
    t = e[0]
    if t == "none":
        return None
    if t in ("bool", "int", "float", "str"):
        return e[1]
    if t == "fspecial":
        return float(e[1])
    if t == "list":
        return [dec(x) for x in e[1]]
    if t == "tuple":
        return tuple(dec(x) for x in e[1])
    if t == "set":
        return set(dec(x) for x in e[1])
    if t == "dict":
        return {dec(k): dec(x) for k, x in e[1]}
    raise ValueError("bad input envelope")

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
        return {"raise": exc_names(e)}

def run(p):
    fresh()
    k = p["kind"]
    if k == "call":
        def f():
            fn = getattr(importlib.import_module(p["mod"]), p["fn"])
            return fn(*dec(p.get("args", ["list", []])), **dec(p.get("kwargs", ["dict", []])))
        return outcome(f)
    if k == "ops":
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                obj = getattr(importlib.import_module(p["mod"]), p["cls"])(*dec(p.get("init", ["list", []])))
        except BaseException as e:
            return {"raise": exc_names(e)}
        return {"ok": [outcome(lambda s=s: getattr(obj, s[0])(*dec(s[1]))) for s in p["steps"]]}
    if k == "run":
        try:
            r = subprocess.run([sys.executable, *p["argv"]], capture_output=True, text=True,
                               timeout=float(p.get("timeout", 10)))
        except subprocess.TimeoutExpired:
            return {"raise": ["TimeoutExpired"]}
        return {"ok": norm({"rc": r.returncode, "out": r.stdout[:200000]})}
    if k == "script":
        recs = []
        def rec(v, i=0):
            if len(recs) < 20000:
                recs.append([i, {"ok": norm(v)}])
        def rec_exc(e, i=0):
            recs.append([i, {"raise": exc_names(e)} if e is not None else {"ok": ["str", "<no exception>"]}])
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
            recs.append([-1, {"raise": exc_names(e)}])
        return {"ok": recs}
    return {"raise": ["UnknownProbe"]}

results = []
for p in spec["probes"]:
    try:
        results.append(run(p))
    except BaseException as e:
        results.append({"raise": exc_names(e)})
with open(out_path, "w") as fh:
    json.dump({"probes": results}, fh)
'''


def enc(v):
    """A host value in the driver's input envelope (the driver's `dec` rebuilds it, tuples and sets included)."""
    if v is None:
        return ["none"]
    if v is True or v is False:
        return ["bool", v]
    if isinstance(v, int):
        return ["int", int(v)]
    if isinstance(v, float):
        return ["float", v] if math.isfinite(v) else ["fspecial", repr(v)]
    if isinstance(v, str):
        return ["str", v]
    if isinstance(v, tuple):
        return ["tuple", [enc(x) for x in v]]
    if isinstance(v, list):
        return ["list", [enc(x) for x in v]]
    if isinstance(v, (set, frozenset)):
        return ["set", [enc(x) for x in sorted(v, key=repr)]]
    if isinstance(v, dict):
        return ["dict", [[enc(k), enc(x)] for k, x in v.items()]]
    raise TypeError(f"a probe input of type {type(v).__name__} cannot cross to the driver")


def probe_inputs(p: dict) -> dict:
    """A probe without its host-only keys, its inputs as host values (what the leak gate reads)."""
    return {k: v for k, v in p.items() if k not in HOST_ONLY}


def driver_probe(p: dict) -> dict:
    """A probe as the driver gets it: no host-only key, its inputs in envelopes."""
    d = probe_inputs(p)
    if "args" in d:
        d["args"] = enc(list(d["args"]))
    if "kwargs" in d:
        d["kwargs"] = enc(dict(d["kwargs"]))
    if "init" in d:
        d["init"] = enc(list(d["init"]))
    if "steps" in d:
        d["steps"] = [[name, enc(list(args))] for name, args in d["steps"]]
    return d


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
    out = parse_reply(raw, probes)
    if res.killed:
        out = [{"killed": res.killed}] * len(probes)
    return out


class _Bad(Exception):
    pass


class Obj:
    """An object the driver reported by its class name and attributes."""
    __slots__ = ("name", "attrs")

    def __init__(self, name: str, attrs: dict):
        self.name, self.attrs = name, attrs

    def __hash__(self):
        return hash(("Obj", self.name))

    def __eq__(self, other):
        return isinstance(other, Obj) and self.name == other.name and _eq(self.attrs, other.attrs, None)

    def __repr__(self):
        return f"<{self.name} object>"


class Tagged:
    """A value the driver could only name: a type (`type`), an exception value (`exc`), or too deep (`deep`)."""
    __slots__ = ("kind", "what")

    def __init__(self, kind: str, what):
        self.kind, self.what = kind, what

    def __hash__(self):
        return hash((self.kind, self.what))

    def __eq__(self, other):
        return isinstance(other, Tagged) and (self.kind, self.what) == (other.kind, other.what) and self.kind != "deep"

    def __repr__(self):
        return f"<{self.kind} {self.what}>"


def _names_ok(names) -> bool:
    return (isinstance(names, list) and 0 < len(names) <= 20 and all(isinstance(x, str) and len(x) <= 200 for x in names)
            and mro_plausible(names))


def decode(e, depth: int = 0, budget: list[int] | None = None):
    """An envelope from the driver as a host value; raises _Bad on anything malformed."""
    budget = budget if budget is not None else [MAX_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_DEPTH or not isinstance(e, list) or not e or not isinstance(e[0], str):
        raise _Bad
    t, n = e[0], len(e)
    if t == "none" and n == 1:
        return None
    if t == "bool" and n == 2 and isinstance(e[1], bool):
        return e[1]
    if t == "int" and n == 2 and isinstance(e[1], int) and not isinstance(e[1], bool) and abs(e[1]) < 1 << 256:
        return e[1]
    if t == "float" and n == 2 and isinstance(e[1], float):
        return e[1]
    if t == "fspecial" and n == 2 and e[1] in ("inf", "-inf", "nan"):
        return float(e[1])
    if t == "str" and n == 2 and isinstance(e[1], str) and len(e[1]) <= MAX_STR:
        return e[1]
    if t in ("list", "tuple", "set") and n == 2 and isinstance(e[1], list):
        items = [decode(x, depth + 1, budget) for x in e[1]]
        if t == "list":
            return items
        if t == "tuple":
            return tuple(items)
        try:
            return frozenset(items)
        except TypeError:
            raise _Bad from None
    if t == "dict" and n == 2 and isinstance(e[1], list):
        out = {}
        for pair in e[1]:
            if not (isinstance(pair, list) and len(pair) == 2):
                raise _Bad
            k = decode(pair[0], depth + 1, budget)
            try:
                if k in out:
                    raise _Bad
                out[k] = decode(pair[1], depth + 1, budget)
            except TypeError:
                raise _Bad from None
        return out
    if t == "obj" and n == 3 and isinstance(e[1], str) and len(e[1]) <= 200:
        attrs = decode(e[2], depth + 1, budget)
        if not isinstance(attrs, dict):
            raise _Bad
        return Obj(e[1], attrs)
    if t == "type" and n == 2 and isinstance(e[1], str) and len(e[1]) <= 200:
        return Tagged("type", e[1])
    if t == "exc" and n == 2 and _names_ok(e[1]):
        return Tagged("exc", tuple(e[1]))
    if t == "deep" and n == 1:
        return Tagged("deep", None)
    raise _Bad


def _outcome(o, budget: list[int]) -> dict:
    """One {"ok": envelope} or {"raise": names}, decoded; raises _Bad."""
    if not isinstance(o, dict) or len(o) != 1:
        raise _Bad
    if "raise" in o:
        if not _names_ok(o["raise"]):
            raise _Bad
        return {"raise": list(o["raise"])}
    if "ok" not in o:
        raise _Bad
    return {"ok": decode(o["ok"], 0, budget)}


def _reply_for(p: dict, o, budget: list[int]) -> dict:
    kind = p.get("kind")
    if isinstance(o, dict) and "raise" in o:
        return _outcome(o, budget)
    if kind == "ops":
        if not (isinstance(o, dict) and len(o) == 1 and isinstance(o.get("ok"), list)
                and len(o["ok"]) == len(p.get("steps") or [])):
            raise _Bad
        return {"ok": [_outcome(x, budget) for x in o["ok"]]}
    if kind == "script":
        if not (isinstance(o, dict) and len(o) == 1 and isinstance(o.get("ok"), list) and len(o["ok"]) <= 20001):
            raise _Bad
        recs = []
        for x in o["ok"]:
            if not (isinstance(x, list) and len(x) == 2 and isinstance(x[0], int) and not isinstance(x[0], bool)):
                raise _Bad
            recs.append([x[0], _outcome(x[1], budget)])
        return {"ok": recs}
    out = _outcome(o, budget)
    if kind == "run" and not (isinstance(out["ok"], dict) and set(out["ok"]) == {"rc", "out"}
                              and isinstance(out["ok"]["rc"], int) and isinstance(out["ok"]["out"], str)):
        raise _Bad
    return out


def parse_reply(raw: bytes | None, probes: list[dict]) -> list[dict | None]:
    """The driver's reply, trusted for nothing: one decoded outcome per probe, each {"ok": value} or
    {"raise": names}, or None where it is malformed (sizes, depth and node count capped)."""
    n = len(probes)
    if not raw:
        return [None] * n
    try:
        r = json.loads(raw)
    except (ValueError, RecursionError):
        return [None] * n
    if not isinstance(r, dict) or not isinstance(r.get("probes"), list) or len(r["probes"]) != n:
        return [None] * n
    budget = [MAX_NODES]
    out: list[dict | None] = []
    for p, o in zip(probes, r["probes"]):
        try:
            out.append(_reply_for(p, o, budget))
        except (_Bad, RecursionError):
            out.append(None)
    return out


def _builtin_exceptions() -> dict[str, type]:
    import builtins
    return {n: c for n, c in vars(builtins).items() if isinstance(c, type) and issubclass(c, BaseException)}


_BUILTIN_EXC = _builtin_exceptions()
MAX_BUILTIN_LEAVES = 3


def mro_plausible(names: list[str]) -> bool:
    """Could `names` be a real exception class's MRO (as the driver reports it)? Each built-in exception
    class named must come before all its built-in ancestors, which must be there too (unless the list was
    cut at 20), no name twice, and at most MAX_BUILTIN_LEAVES built-in classes that are not an ancestor of
    another one named (a class may inherit from two or three; a reply naming every exception is refused).
    Any other name is the candidate's own class, as `~<module>.<name>`."""
    if len(set(names)) != len(names):
        return False
    if any(n not in _BUILTIN_EXC and n != "object" and not n.startswith("~") for n in names):
        return False
    pos = {n: i for i, n in enumerate(names)}
    named = [n for n in names if n in _BUILTIN_EXC]
    for n in named:
        for anc in _BUILTIN_EXC[n].__mro__[1:]:
            a = anc.__name__
            if a in pos:
                if pos[a] < pos[n]:
                    return False
            elif len(names) < 20 and (a in _BUILTIN_EXC or a == "object"):
                return False
    ancestors = {a.__name__ for n in named for a in _BUILTIN_EXC[n].__mro__[1:]}
    return sum(1 for n in named if n not in ancestors) <= MAX_BUILTIN_LEAVES


def _loose(v):
    """Rung 5's old `_norm`: tuples become lists, through lists and tuples only."""
    if isinstance(v, (list, tuple)):
        return [_loose(x) for x in v]
    return v


def _num(v) -> bool:
    return isinstance(v, (bool, int, float))


def same(a, b, tol_abs: float | None = None, seq: str = "strict") -> bool:
    """The comparison rule (top of this module): the original test's `==`, on host values."""
    if seq == "loose":
        a, b = _loose(a), _loose(b)
    return _eq(a, b, tol_abs)


def _eq(a, b, tol_abs) -> bool:
    if _num(a) and _num(b):
        if tol_abs is not None:
            return abs(a - b) <= tol_abs
        return a == b
    if isinstance(a, list) and isinstance(b, list) or isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(_eq(x, y, tol_abs) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_eq(a[k], b[k], tol_abs) for k in a)
    if isinstance(a, frozenset) and isinstance(b, frozenset):
        return a == b
    if isinstance(a, (Obj, Tagged)) or isinstance(b, (Obj, Tagged)):
        return a == b
    return type(a) is type(b) and a == b


def _strip_nl(s: str) -> str:
    return s[:-1] if s.endswith("\n") else s


def outcome_matches(got: dict | None, want: dict, probe: dict) -> bool:
    """One outcome against the solution's. An exception matches by its class (or, with "raises", any class
    in its MRO), built-ins by identity; an op sequence or a script's records match step by step; a script
    run by its exit code and its output with one trailing newline removed from each side."""
    if got is None or "killed" in got:
        return False
    if "raise" in want or "raise" in got:
        if not ("raise" in want and "raise" in got):
            return False
        if not mro_plausible(got["raise"]):
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
        sub = {k: v for k, v in probe.items() if k in ("raises", "tol_abs", "seq")}
        return all(isinstance(x, dict) and outcome_matches(x, y, sub) for x, y in zip(g, w))
    if probe.get("kind") == "run":
        g, w = got["ok"], want["ok"]
        return g["rc"] == w["rc"] and _strip_nl(g["out"]) == _strip_nl(w["out"])
    return same(got["ok"], want["ok"], probe.get("tol_abs"), probe.get("seq", "strict"))


def _script_matches(g, w, probe: dict) -> bool:
    """A script's records against the solution's: the same sequence of checks, each record passing its
    host-only check (a literal, a comparison, an exception class) or, for "ref", equal to the solution's."""
    checks = probe.get("checks")
    if not (isinstance(g, list) and isinstance(w, list) and len(g) == len(w)):
        return False
    for x, y in zip(g, w):
        if x[0] != y[0]:
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
    want = host_value(c.get("value"))
    if op == "eq":
        return same(v, want)
    if op == "is":
        return (v is None and want is None) or (isinstance(v, bool) and isinstance(want, bool) and v == want)
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
    if isinstance(container, (list, tuple, frozenset)):
        return any(same(item, x) for x in container)
    if isinstance(container, dict):
        try:
            return item in container
        except TypeError:
            return False
    return False


def host_value(v):
    """A host literal as a host value: lists of the old tagged forms ({"__tuple__": [...]}, {"__set__": [...]})
    become tuples and frozensets; anything else is itself."""
    if isinstance(v, dict) and len(v) == 1 and next(iter(v)) in ("__tuple__", "__set__") and isinstance(next(iter(v.values())), list):
        (k, items), = v.items()
        items = [host_value(x) for x in items]
        return tuple(items) if k == "__tuple__" else frozenset(items)
    if isinstance(v, list):
        return [host_value(x) for x in v]
    if isinstance(v, tuple):
        return tuple(host_value(x) for x in v)
    if isinstance(v, dict):
        return {k: host_value(x) for k, x in v.items()}
    return v


host_norm = host_value


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
    elif isinstance(v, (list, tuple, frozenset, set)):
        for x in v:
            _scalars(x, out)
    elif isinstance(v, dict):
        for k, x in v.items():
            _scalars(k, out)
            _scalars(x, out)
    elif isinstance(v, Obj):
        _scalars(v.attrs, out)


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
        _scalars(probe_inputs(p), out)
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
