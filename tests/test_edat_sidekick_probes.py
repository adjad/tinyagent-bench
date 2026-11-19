"""Host-driven probes for the ladder's probe rungs: forging a pass fails, and nothing the candidate's side
returns is trusted. The forgery tests passed before the conversion (the hidden tests then ran in the
candidate's own pytest process). Candidate code below is invented."""
from __future__ import annotations

import json
import re

import pytest

from edat.sidekick.ceiling import probes as P
from edat.sidekick.ceiling.rungs import LADDER_SEED_PUBLIC, answer_files, ladder_cases, materialize_probe
from edat.sidekick.sandbox.run import SandboxUnavailable, SeatbeltIsolation, TrialArea


@pytest.fixture(scope="module")
def iso():
    i = SeatbeltIsolation()
    try:
        i.check()
    except SandboxUnavailable as exc:
        if "sandbox_apply" in str(exc) or "Operation not permitted" in str(exc):
            pytest.skip("already inside another Seatbelt sandbox (cannot nest); run outside it")
        raise
    return i


def _inst(template_id: str, trial: int = 1):
    case = next(c for c in ladder_cases() if c.template is not None and c.template.id == template_id)
    return materialize_probe(case, trial, LADDER_SEED_PUBLIC)


def _grade(inst, files: dict[str, str], iso):
    from edat.sidekick.ceiling.probe import grade_probe
    return grade_probe(inst, answer_files(files), iso)


# ------------------------------------------------------------------ one forgery per rung

def test_rung4_forgery_by_running_the_hidden_reference_fails(iso):
    """Imports the hidden test module (which held `_ref`) and answers with it."""
    inst = _inst("rules_weekly_pay")
    cand = ("import importlib\n"
            "def weekly_pay(hours, rate_cents, holidays=()):\n"
            "    try:\n"
            "        m = importlib.import_module('test_hidden_probe')\n"
            "    except Exception:\n"
            "        return 0\n"
            "    return m._ref(hours, rate_cents, holidays)\n")
    g = _grade(inst, {"payroll.py": cand}, iso)
    assert not g.passed and g.metrics["grading"] == "host_probes"


def test_rung5_forgery_by_patching_pytest_fails(iso):
    """A class that does nothing, plus a patch that makes every pytest test pass."""
    inst = _inst("state_parking_meter")
    cand = ("import sys\n"
            "class ParkingMeter:\n"
            "    def __init__(self, rate, daily_cap):\n        pass\n"
            "if '_pytest.python' in sys.modules:\n"
            "    sys.modules['_pytest.python'].Function.runtest = lambda self: None\n")
    g = _grade(inst, {"parking.py": cand}, iso)
    assert not g.passed


def test_rung6_forgery_by_answering_from_the_hidden_reference_fails(iso):
    """The script imports the hidden test module and prints what its `_ref` returns for the input."""
    inst = _inst("data_sales_by_region")
    cand = ("import sys\n"
            "try:\n"
            "    import test_hidden_probe as T\n"
            "    print(T._ref(open(sys.argv[1]).read()))\n"
            "except Exception:\n"
            "    print('no sales')\n")
    g = _grade(inst, {"report.py": cand}, iso)
    assert not g.passed


def test_an_honest_answer_still_passes_each_rung(iso):
    for tid in ("rules_weekly_pay", "state_parking_meter", "data_sales_by_region"):
        inst = _inst(tid)
        g = _grade(inst, inst.check["solution"], iso)
        assert g.passed, (tid, g.reasons)
        assert g.metrics["hidden_total"] == len(inst.check["probes"]) and g.metrics["partial"] == 1.0


# ------------------------------------------------------------------ nothing to learn in the candidate's process

def test_the_candidates_process_holds_no_expected_value(iso):
    """The candidate gathers every string it can reach (its process's objects, the driver's spec, every
    readable file near it) and leaves them in a file. None of the solution's outputs for the hidden inputs is
    among them (the sample's output is in the prompt, so it is left out of the check)."""
    inst = _inst("data_sales_by_region")
    spy = ("import gc, glob, json, os, sys\n"
           "found = set()\n"
           "def take(x):\n"
           "    if isinstance(x, str):\n"
           "        found.add(x); found.update(x.splitlines())\n"
           "for o in gc.get_objects():\n"
           "    try:\n"
           "        if isinstance(o, dict):\n"
           "            for v in list(o.values()): take(v)\n"
           "        elif isinstance(o, (list, tuple)):\n"
           "            for v in o: take(v)\n"
           "    except Exception:\n"
           "        pass\n"
           "for a in sys.argv: take(a)\n"
           "for pat in ('*', '../*', '../*/*', '../../*'):\n"
           "    for p in glob.glob(pat):\n"
           "        try: take(open(p).read())\n"
           "        except Exception: pass\n"
           "with open('found.tmp', 'w') as fh: json.dump(sorted(found), fh)\n"
           "os.replace('found.tmp', 'found.json')\n"                    # each probe run rewrites it whole
           "print('no sales')\n")
    check = inst.check
    with TrialArea(keep=False) as area:
        from edat.sidekick.ceiling.probe import run_probe
        g = run_probe(iso, area, check, {"report.py": spy})
        raw = area.read_bytes_inside("found.json", 1 << 27)             # the default read cap would cut it short
        found = set(json.loads(raw.decode("utf-8")) if raw else [])
    assert not g.passed and found
    want = P.solution_outcomes(iso, check)
    secrets = set()
    for p, o in list(zip(check["probes"], want))[1:]:            # [0] is the sample, shown in the prompt
        out = o["ok"]["out"].rstrip("\n")
        secrets.add(out)
        secrets.update(ln for ln in out.splitlines() if len(ln) > 3)
    secrets = {x for x in secrets if x not in inst.prompt}          # what the prompt states is not secret
    leaked = secrets & found
    assert secrets and not leaked, sorted(leaked)[:5]


# ------------------------------------------------------------------ the reply is untrusted

def test_a_malformed_reply_is_failed_probes_never_an_error():
    two = [{"kind": "call"}, {"kind": "call"}]
    deep = "[" * 5000 + "]" * 5000
    for raw in (None, b"", b"not json", deep.encode(), b"[]", b'{"probes": 3}', b'{"probes": [1, 2]}',
                json.dumps({"probes": [{"ok": ["dict", [[["str", "a"], ["x"]]]], "extra": 1}, {"ok": 1}]}).encode()):
        out = P.parse_reply(raw, two)
        assert len(out) == 2 and all(o is None for o in out)
    mro = ["ValueError", "Exception", "BaseException", "object"]
    one = json.dumps({"probes": [{"ok": ["int", 1]}, {"raise": mro}]}).encode()
    assert P.parse_reply(one, two) == [{"ok": 1}, {"raise": mro}]
    assert P.parse_reply(b'{"probes": [{"ok": ["int", 1]}, {"raise": ["ValueError"]}]}', two) == [{"ok": 1}, None]
    assert P.parse_reply(b'{"probes": [{"ok": ["int", 1]}, {"raise": [7]}]}', two) == [{"ok": 1}, None]
    assert P.parse_reply(b'{"probes": [{"ok": 1}, {"ok": ["int", true]}]}', two) == [None, None]   # bare, or a bool as int
    huge = json.dumps({"probes": [{"ok": ["str", "x" * (P.MAX_STR + 1)]}]}).encode()
    assert P.parse_reply(huge, [{"kind": "call"}]) == [None]
    unhashable = json.dumps({"probes": [{"ok": ["set", [["list", []]]]}]}).encode()
    assert P.parse_reply(unhashable, [{"kind": "call"}]) == [None]
    assert not P.outcome_matches(None, {"ok": 1}, {"kind": "call"})


def test_no_value_can_pass_for_a_tag():
    """Every value is nested inside an envelope, so a dict whose keys look like the host's old tags decodes
    as a dict, never as a tuple, a set or a number (N4)."""
    two = [{"kind": "call"}] * 2
    forged = ["dict", [[["str", "__tuple__"], ["list", [["int", 1], ["int", 2]]]]]]
    real = ["tuple", [["int", 1], ["int", 2]]]
    got, want = P.parse_reply(json.dumps({"probes": [{"ok": forged}, {"ok": real}]}).encode(), two)
    assert got == {"ok": {"__tuple__": [1, 2]}} and want == {"ok": (1, 2)}
    assert not P.outcome_matches(got, want, {"kind": "call"})
    nan = P.parse_reply(json.dumps({"probes": [{"ok": ["dict", [[["str", "__num__"], ["str", "nan"]]]]},
                                               {"ok": ["fspecial", "nan"]}]}).encode(), two)
    assert nan[0] == {"ok": {"__num__": "nan"}} and nan[1]["ok"] != nan[1]["ok"]


def test_inputs_keep_their_types_on_the_way_in():
    """Tuples, sets and dicts with tuple keys cross to the driver in envelopes and are rebuilt there (N1)."""
    p = {"kind": "call", "mod": "m", "fn": "f", "args": [(1, (2, 3)), {4, 5}, {(1, 2): "a"}], "kwargs": {"k": (6,)},
         "test": "test_x", "covers": "all"}
    d = P.driver_probe(p)
    assert d["args"][0] == "list" and d["args"][1][0][0] == "tuple" and d["kwargs"][0] == "dict"
    ns: dict = {}
    exec(P.DRIVER.split("def fresh():")[0].split("sys.dont_write_bytecode = True", 1)[1], ns)   # norm, dec
    assert ns["dec"](d["args"]) == [(1, (2, 3)), {4, 5}, {(1, 2): "a"}]
    assert ns["dec"](d["kwargs"]) == {"k": (6,)}
    assert P.probe_inputs(p)["args"] == p["args"]                           # the leak gate reads host values


def test_a_candidate_that_overwrites_the_reply_fails_the_probes(iso):
    """It writes its own reply at exit (deeply nested, then 'all ok'); every probe fails, nothing raises."""
    inst = _inst("rules_weekly_pay")
    cand = ("import atexit, json, sys\n"
            "def weekly_pay(hours, rate_cents, holidays=()):\n    return 0\n"
            "def _w():\n"
            "    n = len(json.load(open(sys.argv[1]))['probes'])\n"
            "    json.dump({'probes': [{'ok': 0}] * n}, open(sys.argv[2], 'w'))\n"
            "if len(sys.argv) > 2:\n    atexit.register(_w)\n")
    g = _grade(inst, {"payroll.py": cand}, iso)
    assert not g.passed and g.metrics["failure_mode"] in ("logic", "interface")


# ------------------------------------------------------------------ the leak gate, per variant

def _converted() -> list[str]:
    out = []
    for c in ladder_cases():
        if c.template is None:
            continue
        try:
            if "probes" in materialize_probe(c, 1, LADDER_SEED_PUBLIC).check:
                out.append(c.template.id)
        except ValueError:
            out.append(c.template.id)            # a conversion that does not build fails its gate test below
    return sorted(set(out))


GATE_TEMPLATES = _converted()


@pytest.mark.parametrize("tid", GATE_TEMPLATES)
def test_no_probe_carries_an_expected_value(tid, iso):
    """Hard gate: no value the solution records appears as a constant in a probe's source (trivial values
    and reviewed "allow" entries aside), and no probe carries reference code."""
    inst = _inst(tid)
    check = inst.check
    assert "hidden_tests" not in check
    for p in check["probes"]:
        text = json.dumps(P.driver_probe(p))
        assert not re.search(r"\b_ref\w*\(|\bRef\(|\bSRC\b|\bREF\[|test_hidden_probe", text), text[:200]
    outs = P.solution_outcomes(iso, check)
    assert P.leaked_constants(check["probes"], outs) == []


def test_the_gate_catches_a_leaked_expectation():
    probes = [{"kind": "script", "src": "rec(price(3), 0)\nx = 'total: 41.75'\n", "checks": [{"op": "ref"}]}]
    outs = [{"ok": [[0, {"ok": "total: 41.75"}]]}]
    assert P.leaked_constants(probes, outs) == [(0, "total: 41.75")]
    probes[0]["allow"] = ["total: 41.75"]
    assert P.leaked_constants(probes, outs) == []


def test_probe_rungs_keep_their_label_until_reviewed():
    from edat.sidekick.ceiling.rungs import RUNGS
    assert [r.n for r in RUNGS if not r.hardened] == [4, 5, 6, 7, 8, 9]


# ------------------------------------------------------------------ the completeness gate, per variant

@pytest.mark.parametrize("tid", GATE_TEMPLATES)
def test_every_hidden_test_and_assertion_maps_to_a_probe(tid):
    """Hard gate, every variant (trials 1 to 3 here; `probe_instance` enforces it on every build): every test
    function and every assertion of the original hidden file, its helpers' included, has a probe."""
    for trial in (1, 2, 3):
        inst = _inst(tid, trial)                 # raises ValueError, naming what is dropped, if anything is
        cov = inst.check["coverage"]
        assert cov["unmapped"] == [] and cov["tests"] >= 1 and cov["probes"] == len(inst.check["probes"])
        assert all(d["probes"] >= 1 and d["assertions"] for d in cov["map"].values()), cov["map"]


ORIGINAL = """import pytest
from history import History as SUT


def _check(x):
    assert x


def test_constructor():
    for bad in (0, -1):
        try:
            SUT("x", bad)
        except ValueError:
            continue
        raise AssertionError("accepted")
    assert SUT().state() == ["", 0, 0]


def test_ops():
    _check(SUT().state())


assert SUT is not None
"""


def test_the_gate_names_a_dropped_test_and_a_dropped_assertion():
    from edat.sidekick.ceiling.rungs import coverage
    ops = {"kind": "ops", "mod": "history", "cls": "History", "init": [], "steps": []}
    full = [dict(ops, test="test_constructor", covers=[0]), dict(ops, test="test_constructor", covers=[1]),
            dict(ops, test="test_ops", covers="all"), dict(ops, test="<module>", covers="all")]
    cov = coverage(ORIGINAL, full)
    assert cov["unmapped"] == [] and cov["tests"] == 2 and cov["assertions"] == 4
    assert cov["map"]["test_ops"]["assertions"] == ["_check: assert at line 6"]
    dropped = coverage(ORIGINAL, [p for p in full if p["test"] != "test_constructor"])["unmapped"]
    assert dropped == ["test_constructor has no probe"]
    half = coverage(ORIGINAL, [p for p in full if p.get("covers") != [1]])["unmapped"]
    assert len(half) == 1 and half[0].startswith("test_constructor: assert at line")
    vague = coverage(ORIGINAL, [dict(p, covers=None) if p["test"] == "test_ops" else p for p in full])["unmapped"]
    assert vague[0] == "probe 2 (test_ops) does not say which assertions it covers" and len(vague) == 2
    stray = coverage(ORIGINAL, full + [dict(ops, test="test_gone", covers="all")])["unmapped"]
    assert stray == ["probe 4 names 'test_gone', which is not a test of the original file"]
    assert coverage(ORIGINAL + "\n\ndef test_empty():\n    SUT()\n", full)["unmapped"] == ["test_empty has no assertion"]


def test_probe_instance_refuses_a_conversion_that_drops_a_test():
    from edat.sidekick.ceiling.rungs import probe_instance
    ops = {"kind": "ops", "mod": "history", "cls": "History", "init": [], "steps": [], "test": "test_ops",
           "covers": "all"}
    with pytest.raises(ValueError, match="test_constructor has no probe"):
        probe_instance(prompt="p", targets=["history.py"], hidden_tests=ORIGINAL, references=["r"], wrong=["w"],
                       probes=[ops], solution={"history.py": "class History: pass\n"})
    with pytest.raises(ValueError, match="original hidden tests"):
        probe_instance(prompt="p", targets=["history.py"], hidden_tests=None, references=["r"], wrong=["w"],
                       probes=[ops], solution={"history.py": "class History: pass\n"})


# ------------------------------------------------------------------ the dropped constructor tests (F1)

def test_a_constructor_without_its_value_error_fails(iso):
    """Passed with 1.0 at 1f0c095: the conversion had dropped test_constructor."""
    for trial in (1, 2):
        inst = _inst("state_retry_tracker", trial)
        sol = inst.check["solution"]["retry.py"]
        bad = sol.replace('            raise ValueError("bad settings")\n', '            pass\n')
        assert bad != sol
        g = _grade(inst, {"retry.py": bad}, iso)
        assert not g.passed and g.metrics["partial"] < 1.0


def test_a_history_without_defaults_or_limit_check_fails(iso):
    for trial in (1, 2):
        inst = _inst("state_undo_history", trial)
        sol = inst.check["solution"]["history.py"]
        bad = re.sub(r'def __init__\(self, text="", limit=\d+\):\n        if limit < 1:\n'
                     r'            raise ValueError\("limit must be at least 1"\)\n', 'def __init__(self, text, limit):\n', sol)
        assert bad != sol
        g = _grade(inst, {"history.py": bad}, iso)
        assert not g.passed


# ------------------------------------------------------------------ the comparison rule (F2, F3)

def test_the_comparison_rule_is_the_original_tests_equality():
    T = lambda *xs: tuple(xs)
    assert P.same(True, 1) and P.same(False, 0.0) and P.same(1, 1.0)           # == between bool and number
    assert not P.same(True, 2) and not P.same("1", 1)
    assert not P.same(0.1 + 0.2, 0.3) and P.same(0.1 + 0.2, 0.3, tol_abs=1e-9)  # exact, unless approx
    assert not P.same(T(1, "B"), [1, "B"]) and P.same(T(1, "B"), T(1, "B"))   # a tuple is not a list
    assert P.same(T(1, T(2)), [1, [2]], seq="loose")                            # rung 5's old _norm
    assert not P.same({"k": T(1)}, {"k": [1]}, seq="loose")                     # ... not inside dicts
    assert not P.same(float("nan"), float("nan")) and P.same(float("inf"), float("inf"))
    assert P.same(frozenset({1, 2}), frozenset({2, 1})) and not P.same(frozenset({1}), [1])
    assert P.host_value({"__tuple__": [1, {"__tuple__": [2]}]}) == (1, (2,))
    run = {"kind": "run"}
    assert P.outcome_matches({"ok": {"rc": 0, "out": "a\nb"}}, {"ok": {"rc": 0, "out": "a\nb\n"}}, run)   # N2
    assert not P.outcome_matches({"ok": {"rc": 0, "out": "a\nb\n\n"}}, {"ok": {"rc": 0, "out": "a\nb\n"}}, run)
    assert not P.outcome_matches({"ok": {"rc": 1, "out": "a\nb\n"}}, {"ok": {"rc": 0, "out": "a\nb\n"}}, run)


def test_a_list_where_the_prompt_asks_for_a_tuple_fails(iso):
    """rules_course_grade returns `(points, letter)`; the old test's == failed a list, and so do the probes."""
    inst = _inst("rules_course_grade")
    sol = inst.check["solution"]["grading.py"]
    wrapped = (sol.replace("def final_grade(", "def _final_grade(")
               + "\n\ndef final_grade(*a, **k):\n    return list(_final_grade(*a, **k))\n")
    g = _grade(inst, {"grading.py": wrapped}, iso)
    assert not g.passed
    assert _grade(inst, inst.check["solution"], iso).passed


# ------------------------------------------------------------------ replies naming every exception (F4)

def test_an_exception_list_that_is_no_real_mro_never_matches():
    class Both(ValueError, KeyError):
        pass
    real = ["~test.Both"] + [c.__name__ for c in Both.__mro__[1:]]            # as the driver names it
    assert P.mro_plausible(real) and not P.mro_plausible([c.__name__ for c in Both.__mro__]) and P.mro_plausible([c.__name__ for c in KeyError.__mro__])
    every = ["ValueError", "KeyError", "LookupError", "IndexError", "RuntimeError", "TypeError", "Exception",
             "BaseException", "object"]
    assert not P.mro_plausible(every) and not P.mro_plausible(every[::-1])
    assert not P.mro_plausible(["ValueError", "ValueError", "Exception", "BaseException", "object"])
    want = {"raise": ["ValueError", "Exception", "BaseException", "object"]}
    probe = {"kind": "call", "raises": "ValueError"}
    assert P.outcome_matches(want, want, probe)
    assert not P.outcome_matches({"raise": every}, want, probe)
    assert P.parse_reply(json.dumps({"probes": [{"raise": every}]}).encode(), [{"kind": "call"}]) == [None]


# ------------------------------------------------------------------ no expected value in a record (F5)

def test_a_failure_reason_carries_no_expected_output(iso):
    inst = _inst("data_sales_by_region")
    want = P.solution_outcomes(iso, inst.check)
    g = _grade(inst, {"report.py": "print('no sales')\n"}, iso)
    assert not g.passed
    text = json.dumps(g.to_record())
    for o in want[1:]:                                       # [0] is the sample, shown in the prompt
        for line in o["ok"]["out"].splitlines():
            if len(line) > 6 and line not in inst.prompt:
                assert line not in text, line


# ------------------------------------------------------------------ the label follows the grading method

def test_the_not_hardened_label_follows_how_a_row_was_graded():
    from edat.sidekick.ceiling.rungs import HARDENED_GRADING, RUNG_BY_ID, row_hardened
    assert "host_probes" not in HARDENED_GRADING             # until its conversion has merged and been reviewed
    assert not row_hardened(RUNG_BY_ID["state"], {"grading": "host_probes"})
    assert not row_hardened(RUNG_BY_ID["state"], {})          # failed before grading
    assert row_hardened(RUNG_BY_ID["rules"], {"grading": "x"}) is False
    sidekick = next(r for r in RUNG_BY_ID.values() if r.source == "sidekick")
    assert row_hardened(sidekick, {})


# ------------------------------------------------------------------ round 2 (N1 to N5)

def test_a_candidate_that_needs_a_list_where_the_tests_pass_a_tuple_fails(iso):
    """N1: `holidays += [6]` breaks on the tuple the old tests passed; the probes pass it as a tuple too."""
    inst = _inst("rules_weekly_pay")
    sol = inst.check["solution"]["payroll.py"]
    bad = sol.replace("    if len(hours) != 7", "    holidays += []\n    if len(hours) != 7", 1)
    assert bad != sol
    assert not _grade(inst, {"payroll.py": bad}, iso).passed
    assert _grade(inst, inst.check["solution"], iso).passed


def test_a_report_without_its_final_newline_passes(iso):
    """N2: the old `_run` removed one trailing newline before comparing; so do the probes."""
    inst = _inst("data_sales_by_region")
    sol = inst.check["solution"]["report.py"]
    head = ("import atexit as _ae, io as _io, sys as _s\n_buf, _real = _io.StringIO(), _s.stdout\n_s.stdout = _buf\n"
            "def _fl():\n    o = _buf.getvalue()\n    _real.write(o[:-1] if o.endswith('\\n') else o)\n    _real.flush()\n"
            "_ae.register(_fl)\n")
    assert not sol.lstrip().startswith("from __future__")
    assert _grade(inst, {"report.py": head + sol}, iso).passed
    two = head.replace("o[:-1] if o.endswith('\\n') else o", "o + '\\n'")
    assert not _grade(inst, {"report.py": two + sol}, iso).passed


def test_an_exception_class_of_the_candidates_own_named_like_a_builtin_fails(iso):
    """N3: pytest.raises(ValueError) failed a home-made class named ValueError; so does a raising probe."""
    inst = _inst("rules_weekly_pay")
    sol = inst.check["solution"]["payroll.py"]
    g = _grade(inst, {"payroll.py": "class ValueError(Exception):\n    pass\n\n\n" + sol}, iso)
    assert not g.passed


def test_a_dict_shaped_like_a_tag_does_not_pass_for_a_tuple(iso):
    """N4: rules_course_grade returns `(points, letter)`; a dict that mimics the old tuple tag fails."""
    inst = _inst("rules_course_grade")
    sol = inst.check["solution"]["grading.py"]
    forged = (sol.replace("def final_grade(", "def _final_grade(")
              + "\n\ndef final_grade(*a, **k):\n    return {'__tuple__': list(_final_grade(*a, **k))}\n")
    assert not _grade(inst, {"grading.py": forged}, iso).passed


def test_the_gate_fails_closed_on_what_it_does_not_recognise():
    """N5: tests pytest would collect that the map cannot see, or assertions reached other than by a direct
    call, refuse the conversion even though no hidden file does any of these today."""
    from edat.sidekick.ceiling.rungs import coverage
    probe = [{"kind": "call", "test": "test_a", "covers": "all"}]
    base = "import pytest\nfrom m import f\n\ndef test_a():\n    assert f(1) == 1\n"
    assert coverage(base, probe)["unmapped"] == []
    cases = {
        "a test named without the underscore": base + "\ndef testb():\n    assert f(2) == 2\n",
        "a test made by a factory": base + "\ndef _make(n):\n    def t():\n        assert f(n) == n\n    return t\n\ntest_c = _make(3)\n",
        "a helper reached through map": base + "\ndef _chk(n):\n    assert f(n) == n\n\ndef test_d():\n    list(map(_chk, [4]))\n",
        "a fixture": base + "\n@pytest.fixture\ndef thing():\n    return 1\n",
        "an unrecognised decorator": base.replace("def test_a", "@pytest.mark.skip\ndef test_a"),
        "a dynamic definition": base + "\nglobals()['test_e'] = lambda: None\n",
        "a test imported under a test name": base + "\nfrom other import test_f\n",
        "a test bound in a loop": base + "\nfor _i in range(2):\n    def test_g():\n        assert f(_i) == _i\n",
        "a test generator hook": base + "\ndef pytest_generate_tests(metafunc):\n    pass\n",
    }
    for why, src in cases.items():
        assert coverage(src, probe + [{"kind": "call", "test": n, "covers": "all"} for n in ("test_d",) if "test_d" in src])["unmapped"], why
    param = "import pytest\nfrom m import f\nCASES = [1, 2, 3]\n\n@pytest.mark.parametrize('n', CASES)\ndef test_p(n):\n    assert f(n) == n\n"
    p3 = [{"kind": "call", "test": "test_p", "covers": "all"}] * 3
    assert coverage(param, p3)["unmapped"] == [] and coverage(param, p3[:2])["unmapped"]   # one probe per set
    built = param.replace("CASES = [1, 2, 3]", "CASES = [1] + [n for n in (2, 3)]")
    assert coverage(built, p3)["unmapped"] == []
    unknown = param.replace("CASES = [1, 2, 3]", "CASES = load_cases()")
    assert coverage(unknown, p3)["unmapped"]


def test_host_probe_rows_carry_their_own_method_and_reason():
    from edat.sidekick.labels import REASONS, grading_label, may_score
    lab = grading_label("probe", {"probes": [{"kind": "call"}]})
    assert lab == {"grading": "host_probes", "grade_hardened": False, "not_hardened_reason": "host_probes_unreviewed"}
    assert lab["not_hardened_reason"] in REASONS
    assert grading_label("probe", {"hidden_tests": "x"})["not_hardened_reason"] == "probe_in_process"
    row = {"status": "ok", "facets": {"family": "rules", "tags": []}, "metrics": {"grading": "host_probes", "grade_hardened": True}}
    assert not may_score(row)                       # not on the allow-list, whatever the mark says
