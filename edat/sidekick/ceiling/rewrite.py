"""Rewrite a hand-written hidden pytest file into host-driven script probes (rungs 7 to 9).

Each test function becomes one `script` probe that only records values; what the old asserts expected moves
to host-only checks the candidate's process never sees:

    assert E == <literal>            rec(E)                    host: equal to the literal
    assert E <op> <literal>          rec(E)                    host: E <op> literal  (op: < <= > >= != in, not in)
    assert <literal> in E            rec(E)                    host: literal in E
    assert E is True/False/None      rec(E)                    host: identity
    assert E == pytest.approx(L, abs=T)   rec(E)               host: within T of L
    assert E == <reference expression>   rec(E)                host: equal to the solution's own record
    assert E                         rec(bool(E))              host: true   (E holds no expected value)
    with pytest.raises(X): body      try: body / record the exception      host: X in its MRO

A name bound by `@pytest.mark.parametrize` and used only as an expectation is kept on the host. Module-level
names of the test file are inputs (copied into the probe) unless they are the reference (`Ref`, `_ref*`) or
helpers used only for expectations, which are never copied.

The rewrite REFUSES what it does not know (fixtures such as monkeypatch, `pytest.raises(...) as e`,
expectations it cannot place on one side, reference code needed on the candidate's side, ...): `Refused`
names the test and the reason, and that test is converted by hand. The leak gate in the tests is the
backstop: no value the solution records may appear as a constant in a probe's source.
"""
from __future__ import annotations

import ast
import copy
import itertools

_CMP = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.NotEq: "!=", ast.In: "in", ast.NotIn: "not in"}
_FLIP = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "!=": "!="}
ALLOWED_FIXTURES: set[str] = set()


class Refused(Exception):
    def __init__(self, test: str, why: str):
        super().__init__(f"{test}: {why}")
        self.test, self.why = test, why


def _literal(node: ast.AST, env: dict):
    """The value of a literal expression (or a parametrize-bound name), else raise ValueError."""
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_literal(e, env) for e in node.elts]
    if isinstance(node, ast.Dict):
        return {_literal(k, env): _literal(v, env) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Set):
        return {"__set__": sorted((_literal(e, env) for e in node.elts), key=repr)}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _literal(node.operand, env)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return -v
    if isinstance(node, ast.Constant) and not isinstance(node.value, bytes):
        return node.value
    raise ValueError("not a literal")


def _is_literal(node, env) -> bool:
    try:
        _literal(node, env)
        return True
    except ValueError:
        return False


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _approx(node, env):
    """(value, abs) for pytest.approx(L, abs=T) with literal arguments, else None."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "approx"
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "pytest" and len(node.args) == 1):
        val = _literal(node.args[0], env)
        tol = None
        for kw in node.keywords:
            if kw.arg == "abs":
                tol = _literal(kw.value, env)
            else:
                raise ValueError("approx with other than abs=")
        return val, tol if tol is not None else 1e-6
    return None


class _Rewriter:
    def __init__(self, test: str, env: dict, expect_only: set[str]):
        self.test, self.env, self.expect_only = test, env, expect_only
        self.checks: list[dict] = []

    def no(self, why: str):
        raise Refused(self.test, why)

    def _rec(self, expr: ast.AST, check: dict) -> ast.stmt:
        if _names(expr) & self.expect_only:
            self.no("an expectation-only name is needed on the candidate's side")
        self.checks.append(check)
        return ast.Expr(ast.Call(ast.Name("rec", ast.Load()), [expr, ast.Constant(len(self.checks) - 1)], []))

    def stmts(self, body: list[ast.stmt]) -> list[ast.stmt]:
        out = []
        for st in body:
            out.extend(self.stmt(st))
        return out

    def stmt(self, st: ast.stmt) -> list[ast.stmt]:
        if isinstance(st, ast.Assert):
            if isinstance(st.test, ast.BoolOp) and isinstance(st.test.op, ast.And):
                return [x for v in st.test.values for x in self.stmt(ast.Assert(v))]   # one check per part
            return [self.assert_(st.test)]
        if (isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name)
                and st.targets[0].id in self.expect_only):
            self.env[st.targets[0].id] = _literal(st.value, self.env)     # an expected literal: kept on the host
            return []
        if isinstance(st, ast.With):
            return self.with_(st)
        if isinstance(st, (ast.For, ast.While, ast.If)):
            st = copy.copy(st)
            if _names(getattr(st, "test", None) or getattr(st, "iter", ast.Constant(0))) & self.expect_only:
                self.no("an expectation-only name steers the control flow")
            st.body = self.stmts(st.body) or [ast.Pass()]
            st.orelse = self.stmts(st.orelse)
            return [st]
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) and isinstance(st.value.func, ast.Name) \
                and st.value.func.id == "same" and "same" in self.helpers:
            c = st.value
            if not c.args or c.keywords or any(isinstance(a, ast.Starred) for a in c.args[:1]):
                self.no("same() with an unexpected shape")
            if _names(c) & (self.helpers - {"same"}):
                self.no("same() with reference values in its arguments")
            call = ast.Call(ast.Name("call_outcome", ast.Load()),
                            [ast.Call(ast.Name("getattr", ast.Load()), [ast.Name("M", ast.Load()), c.args[0]], []),
                             ast.List(c.args[1:], ast.Load())], [])
            return [self._rec(call, {"op": "ref"})]
        if isinstance(st, ast.Assign) and _names(st.value) & self.helpers:
            for t in st.targets:                         # the expected side, computed by reference code: dropped
                self.helpers = self.helpers | _names(t)
            return []
        if isinstance(st, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr)):
            if _names(st) & self.helpers:
                self.no("reference code used outside an assert")
            if _names(st) & self.expect_only:
                self.no("an expectation-only name is used outside an assert")
            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) and _names(st.value.func) & {"pytest"}:
                self.no("a pytest call outside an assert")
            return [st]
        if isinstance(st, ast.Pass):
            return [st]
        self.no(f"a statement the rewrite does not know ({type(st).__name__})")

    def with_(self, st: ast.With) -> list[ast.stmt]:
        if len(st.items) != 1:
            self.no("a with statement with several items")
        it = st.items[0]
        ce = it.context_expr
        if not (isinstance(ce, ast.Call) and isinstance(ce.func, ast.Attribute) and ce.func.attr == "raises"
                and isinstance(ce.func.value, ast.Name) and ce.func.value.id == "pytest"):
            self.no("a with statement other than pytest.raises")
        if it.optional_vars is not None:
            self.no("pytest.raises(...) as e")
        if len(ce.args) != 1 or ce.keywords:
            self.no("pytest.raises with match= or several classes")
        exc = ce.args[0]
        if not isinstance(exc, ast.Name):
            self.no("pytest.raises of something other than a class name")
        inner = _Rewriter(self.test, self.env, self.expect_only)
        body = inner.stmts(st.body)
        if inner.checks:
            self.no("an assert inside pytest.raises")
        self.checks.append({"op": "raises", "name": exc.id})
        i = len(self.checks) - 1
        tree = ast.parse(f"try:\n    pass\nexcept BaseException as _e:\n    rec_exc(_e, {i})\nelse:\n    rec_exc(None, {i})\n").body[0]
        tree.body = body or [ast.Pass()]
        return [tree]

    def assert_(self, t: ast.AST) -> ast.stmt:
        env = self.env
        if isinstance(t, ast.Compare) and len(t.ops) == 1:
            left, op, right = t.left, t.ops[0], t.comparators[0]
            ap = None
            try:
                ap = _approx(right, env)
            except ValueError as exc:
                self.no(str(exc))
            if ap is not None and isinstance(op, ast.Eq):
                return self._rec(left, {"op": "approx", "value": ap[0], "abs": ap[1]})
            if isinstance(op, (ast.Eq, ast.Is)) and _is_literal(right, env):
                return self._rec(left, {"op": "eq" if isinstance(op, ast.Eq) else "is", "value": _literal(right, env)})
            if isinstance(op, (ast.Eq, ast.Is)) and _is_literal(left, env):
                return self._rec(right, {"op": "eq" if isinstance(op, ast.Eq) else "is", "value": _literal(left, env)})
            if type(op) in _CMP and _is_literal(right, env):
                return self._rec(left, {"op": "cmp", "cmp": _CMP[type(op)], "value": _literal(right, env)})
            if isinstance(op, (ast.In, ast.NotIn)) and _is_literal(left, env):
                return self._rec(right, {"op": "contains" if isinstance(op, ast.In) else "lacks",
                                         "value": _literal(left, env)})
            if type(op) in _CMP and type(op) not in (ast.In, ast.NotIn) and _is_literal(left, env):
                return self._rec(right, {"op": "cmp", "cmp": _FLIP[_CMP[type(op)]], "value": _literal(left, env)})
            if isinstance(op, ast.Eq) and _names(right) & self.helpers and not _names(left) & self.helpers:
                return self._rec(left, {"op": "ref"})
            if isinstance(op, ast.Eq) and _names(left) & self.helpers and not _names(right) & self.helpers:
                return self._rec(right, {"op": "ref"})
        if _names(t) & (self.helpers | self.expect_only):
            self.no("an assert that needs reference code or an expectation on the candidate's side")
        if any(isinstance(n, ast.Compare) for n in ast.walk(t)):
            # Both sides computed in the test: the expected side might be computed by a helper the template has
            # not named as reference code, in the candidate's own process. Never guessed; converted by hand.
            self.no("an assert comparing two computed values")
        return self._rec(ast.Call(ast.Name("bool", ast.Load()), [t], []), {"op": "truthy"})

    helpers: set[str] = set()


def _param_sets(fn: ast.FunctionDef, module_env: dict, test: str) -> tuple[list[str], list[dict]]:
    """The parametrize names and every combination of their values (literals only)."""
    names: list[str] = []
    axes: list[list[dict]] = []
    for d in fn.decorator_list:
        if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "parametrize"):
            raise Refused(test, "a decorator other than pytest.mark.parametrize")
        if len(d.args) != 2 or d.keywords:
            raise Refused(test, "parametrize with ids= or other options")
        ns = [n.strip() for n in _literal(d.args[0], {}).split(",")]
        try:
            vals = _literal(d.args[1], module_env)
        except ValueError:
            raise Refused(test, "parametrize values that are not literals")
        axis = []
        for v in vals:
            if len(ns) == 1:
                axis.append({ns[0]: v})
            else:
                if not isinstance(v, list) or len(v) != len(ns):
                    raise Refused(test, "parametrize values that do not match the names")
                axis.append(dict(zip(ns, v)))
        names += ns
        axes.append(axis)
    combos = [dict(itertools.chain.from_iterable(d.items() for d in c)) for c in itertools.product(*axes)] if axes else [{}]
    return names, combos


def _expect_only(fn: ast.FunctionDef, pnames: list[str]) -> set[str]:
    """Parametrize names used only as the expected side of a comparison in an assert."""
    used_expected, used_other = set(), set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare) and len(node.test.ops) == 1:
            r = node.test.comparators[0]
            if isinstance(r, ast.Name):
                used_expected.add(r.id)
    for node in ast.walk(ast.Module(body=fn.body, type_ignores=[])):
        if isinstance(node, ast.Name) and node.id in pnames:
            used_other.add(node.id)
    out = set()
    for n in pnames:
        exp_uses = sum(1 for node in ast.walk(fn) if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare)
                       and len(node.test.ops) == 1 and isinstance(node.test.comparators[0], ast.Name)
                       and node.test.comparators[0].id == n)
        all_uses = sum(1 for node in ast.walk(ast.Module(body=fn.body, type_ignores=[]))
                       if isinstance(node, ast.Name) and node.id == n)
        if exp_uses and exp_uses == all_uses:
            out.add(n)
    return out


def rewrite(tests: str, *, reference_names: tuple[str, ...] = ("Ref",)) -> tuple[list[dict], list[Refused]]:
    """(script probes, refusals) for a hidden test file. `reference_names`: module-level functions and
    classes of the test file that are reference code (besides `Ref` and `_ref*`), named by the template so a
    reviewer sees the list; they never reach the candidate's process. A probe's host-only `checks` say what each recorded
    value must be; its `test` names the old test (with the parametrize values) for the report."""
    mod = ast.parse(tests)
    preamble: list[ast.stmt] = []
    helpers: set[str] = set()
    module_env: dict = {}
    fns: list[ast.FunctionDef] = []
    helpers |= set(reference_names) | {"Ref"}
    for st in mod.body:
        if isinstance(st, ast.FunctionDef) and st.name.startswith("test_"):
            fns.append(st)
        elif isinstance(st, (ast.FunctionDef, ast.ClassDef)):
            if st.name in helpers or st.name.startswith("_ref") or _names(st) & helpers:
                helpers.add(st.name)                     # reference code, or code that uses it: never copied
            else:
                preamble.append(st)
        elif _names(st) & helpers or (isinstance(st, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in helpers for t in st.targets)):
            for t in getattr(st, "targets", []):
                helpers |= _names(t)
            continue
        elif isinstance(st, (ast.Import, ast.ImportFrom)):
            if any(a.name.split(".")[0] in ("pytest", "_pytest") for a in st.names) or (
                    isinstance(st, ast.ImportFrom) and (st.module or "").split(".")[0] in ("pytest", "_pytest")):
                continue
            preamble.append(st)
        elif isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            try:
                module_env[st.targets[0].id] = _literal(st.value, module_env)
            except ValueError:
                pass
            preamble.append(st)
        elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            continue
        else:
            preamble.append(st)
    for st in preamble:
        if _names(st) & helpers:
            raise Refused("<module>", "module-level code uses reference code")
    probes, refused = [], []
    for fn in fns:
        try:
            if fn.args.args:
                pn, combos = _param_sets(fn, module_env, fn.name)
                extra = [a.arg for a in fn.args.args if a.arg not in pn]
                if extra:
                    raise Refused(fn.name, "fixtures: " + ", ".join(extra))
            else:
                pn, combos = _param_sets(fn, module_env, fn.name)
            eo = _expect_only(fn, pn) | _local_expected(fn)
            for combo in combos:
                rw = _Rewriter(fn.name, {k: v for k, v in combo.items() if k in eo}, eo)
                rw.helpers = helpers
                body = rw.stmts(copy.deepcopy(fn.body))
                binds = [ast.parse(f"{k} = {v!r}").body[0] for k, v in combo.items() if k not in eo]
                src = ast.unparse(ast.Module(body=preamble + binds + body, type_ignores=[]))
                label = fn.name + ("[" + ",".join(f"{k}={combo[k]!r}" for k in pn)[:80] + "]" if pn else "")
                probes.append({"kind": "script", "src": src, "checks": rw.checks, "test": label})
        except Refused as r:
            refused.append(r)
    return probes, refused


def _local_expected(fn: ast.FunctionDef) -> set[str]:
    """Names assigned exactly once, at the top of the test, to a literal, and used only as the expected side
    of a comparison in an assert (`empty = {...}` ... `assert f() == empty`): kept on the host."""
    out = set()
    for st in fn.body:
        if not (isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name)
                and _is_literal(st.value, {})):
            continue
        n = st.targets[0].id
        assigns = sum(1 for x in ast.walk(fn) if isinstance(x, (ast.Assign, ast.AugAssign, ast.For, ast.With,
                                                                 ast.comprehension, ast.NamedExpr))
                      and n in _names(getattr(x, "target", None) or ast.Tuple(getattr(x, "targets", []), ast.Load())))
        exp, total = 0, 0
        for x in ast.walk(fn):
            if isinstance(x, ast.Compare) and len(x.ops) == 1:
                for side in (x.left, x.comparators[0]):
                    if isinstance(side, ast.Name) and side.id == n:
                        exp += 1
            if isinstance(x, ast.Name) and x.id == n and isinstance(x.ctx, ast.Load):
                total += 1
        if assigns == 1 and exp and exp == total:
            out.add(n)
    return out
