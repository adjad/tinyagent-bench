"""Noisy-input variants (spec 02 §3.18): make_variants, select_seeds, build_variants, consistency."""
from __future__ import annotations

import random
import re

from edat.cases.generators import _example
from edat.cases.oracle import check_case, oracle_turns
from edat.cases.bind import bind
from edat.cases.runner import run_trial
from edat.cases.schema import problems
from edat.cases.variants import (SEED_FAMILIES, build_variants, consistency, make_variants, num_words,
                                 rows_for, select_seeds, spoken_number, typo_word)
from edat.models.stub import ScriptedClient


def timer_case(prompt="Please start a 25 minute timer called pasta at 3:30, thanks.", **kw):
    c = {"id": "t_neu_timer_001", "lane": "neutral", "family": "select", "sub": "x", "prompt": prompt,
         "expect": [{"tool": "timer_start", "args": {"label": {"icontains": "pasta"}}}],
         "final_state": [{"in": "timers", "expect": {"count": 1}}],
         "optimal": {"calls": 1, "steps": 2},
         "oracle": [{"calls": [{"name": "timer_start", "arguments": {"minutes": 25, "label": "pasta"}}]},
                    {"text": "Started."}]}
    c.update(kw)
    return c


def example_cases():
    return _example.generate("dev", random.Random("variants|test"))


def by_kind(vs):
    return {v["variant"]: v for v in vs}


def test_shape_and_only_the_prompt_changes():
    for case in example_cases() + [timer_case()]:
        vs = make_variants(case, random.Random(5))
        assert [v["variant"] for v in vs] == ["typo", "transcript", "paraphrase"]
        assert [v["id"] for v in vs] == [f"{case['id']}__typo", f"{case['id']}__voice", f"{case['id']}__para"]
        for v in vs:
            assert v["variant_of"] == case["id"] and "noisy" in v["tags"]
            assert not problems(v), problems(v)
            assert v["prompt"] != case["prompt"]
            for key in ("oracle", "world", "facts", "forbid", "final_state", "optimal", "lane", "family"):
                assert v.get(key) == case.get(key) or key in ("final_state",), key
        assert "notes" not in by_kind(vs)["typo"]
        assert by_kind(vs)["paraphrase"]["notes"] == "review: pending"
        assert vs[0]["prompt"] != vs[1]["prompt"] != vs[2]["prompt"]


def test_deterministic_and_seed_dependent():
    case = timer_case()
    a = make_variants(case, random.Random(7))
    assert a == make_variants(case, random.Random(7))
    assert any(make_variants(case, random.Random(s)) != a for s in range(8, 14))


def test_core_and_pair_id_are_dropped_from_variants():
    case = timer_case(tags=["core", "datetime"], pair_id="p-1")
    for v in make_variants(case, random.Random(1)):
        assert "core" not in v["tags"] and "pair_id" not in v and "datetime" in v["tags"]


def test_typos_leave_numbers_ids_placeholders_and_quotes_alone():
    prompt = ("Please remind me about ticket r-901 and invoice 4821 at {when} or {rel:+1d 15:00}, "
              "title it \"quarterly review\" and mail ines.duarte@example.com about https://example.org/plan.")
    case = timer_case(prompt=prompt)
    for s in range(30):
        t = make_variants(case, random.Random(s))[0]["prompt"]
        for keep in ("r-901", "4821", "{when}", "{rel:+1d 15:00}", '"quarterly review"',
                     "ines.duarte@example.com", "https://example.org/plan"):
            assert keep in t, (s, t)
        assert t != prompt


def test_typo_word_always_changes_and_keeps_first_letter():
    rng = random.Random(3)
    for w in ["Zachariah", "garage", "prescription", "Lindqvist", "weather"]:
        for _ in range(40):
            t = typo_word(w, rng)
            assert t != w and t[0] == w[0] and abs(len(t) - len(w)) <= 1


def test_misspelled_name_is_still_accepted_by_the_matchers():
    contact = {"id": "c-9", "name": "Zachariah Lindqvist", "phone": "+1 555 0142"}
    case = {"id": "n_neu_msg_001", "lane": "neutral", "family": "select", "sub": "x", "prompt": "Call Zachariah",
            "world": {"seed_records": {"contacts": [contact]}},
            "expect": [{"tool": "messages_send", "args": {"to": {"contains_any": ["Zachariah", "0142"]},
                                                          "text": {"icontains": "Zachariah"}}}],
            "optimal": {"calls": 1, "steps": 2},
            "oracle": [{"text": "x"}]}
    hit = None
    for s in range(40):
        t = make_variants(case, random.Random(s))[0]
        bad = t["prompt"].split()[-1]
        if bad != "Zachariah":
            hit = (bad, t)
            break
    assert hit, "no seed misspelled the name in 40 tries"
    bad, t = hit
    to = t["expect"][0]["args"]["to"]["contains_any"]
    assert bad in to and "Zachariah" in to and "0142" in to and "Lindqvist" in to
    assert {"contains_any": [bad, "Zachariah", "Lindqvist", "0142"]} != t["expect"][0]["args"]["text"]
    assert bad in t["expect"][0]["args"]["text"]["contains_any"]
    assert case["expect"][0]["args"]["to"]["contains_any"] == ["Zachariah", "0142"]    # the seed is untouched


def test_words_inside_a_longer_matcher_are_never_misspelled():
    case = timer_case(prompt="Start the budget meeting timer", expect=[
        {"tool": "timer_start", "args": {"label": {"regex": "budget meeting"}}}])
    for s in range(40):
        t = make_variants(case, random.Random(s))[0]["prompt"]
        assert "budget" in t and "meeting" in t


def test_transcript_style():
    case = timer_case()
    for s in range(10):
        v = by_kind(make_variants(case, random.Random(s)))["transcript"]["prompt"]
        assert v == v.lower() and not re.search(r"[.,!?;:']", v)
        assert "25" not in v and "3:30" not in v
        assert "twenty five" in v and "three thirty" in v
    ph = timer_case(prompt="Text Mei that I'm {mins} minutes late. Meet at {rel:+1d 15:00}!")
    v = by_kind(make_variants(ph, random.Random(2)))["transcript"]["prompt"]
    assert "{mins}" in v and "{rel:+1d 15:00}" in v and "im" in v.split()
    assert v.count("!") == 0


def test_number_words():
    assert num_words(21) == "twenty one" and num_words(0) == "zero" and num_words(305) == "three hundred five"
    assert spoken_number("3:30") == "three thirty" and spoken_number("21") == "twenty one"
    assert spoken_number("9:05pm") == "nine oh five pm" and spoken_number("15:00") == "fifteen hundred"
    assert spoken_number("3pm") == "three pm" and spoken_number("21st") == "twenty first"
    assert spoken_number("2027") is None and spoken_number("r-901") is None


def test_digits_stay_when_a_matcher_needs_them_and_exact_numbers_accept_both():
    needs = timer_case(prompt="Start a 25 minute timer called pasta",
                       expect=[{"tool": "timer_start", "args": {"label": {"regex": "pasta 25"}}}])
    v = by_kind(make_variants(needs, random.Random(1)))["transcript"]
    assert "25" in v["prompt"]
    exact = timer_case(prompt="Start a 25 minute timer called pasta",
                       expect=[{"tool": "timer_start", "args": {"label": {"icontains": "25"}}}])
    v = by_kind(make_variants(exact, random.Random(1)))["transcript"]
    assert "twenty five" in v["prompt"]
    got = v["expect"][0]["args"]["label"]["contains_any"]
    assert "25" in got and "twenty five" in got and "twenty-five" in got


def test_paraphrase_rules():
    p = lambda text, s=1: by_kind(make_variants(timer_case(prompt=text), random.Random(s)))["paraphrase"]["prompt"]  # noqa: E731
    assert p("Please check the forecast for Oslo") == "Check the forecast for Oslo, please."
    assert p("Check the forecast for Oslo, please.") == "Please check the forecast for Oslo."
    assert p("Can you tell me what's the weather") != "Can you tell me what's the weather"
    assert p("Weather in {city}") != "Weather in {city}" and "{city}" in p("Weather in {city}")
    # a word a matcher names is never reworded
    c = timer_case(prompt="Can you set a timer", expect=[{"tool": "timer_start", "args": {"label": {"icontains": "timer"}}}])
    assert "start a timer" not in by_kind(make_variants(c, random.Random(1)))["paraphrase"]["prompt"]


def test_multi_turn_cases_change_every_user_turn():
    case = {"id": "m_neu_x_001", "lane": "neutral", "family": "select", "sub": "x",
            "turns": [{"user": "Please check the weather in Porto", "oracle": [{"text": "x"}]},
                      {"user": "Thanks, now convert twelve miles to kilometres", "oracle": [{"text": "y"}]}]}
    for v in make_variants(case, random.Random(3)):
        assert [t["user"] for t in v["turns"]] != [t["user"] for t in case["turns"]]
        assert all(a != b for a, b in zip((t["user"] for t in v["turns"]), (t["user"] for t in case["turns"])))


def test_variants_pass_the_oracle_and_fail_the_null_model():
    vs = build_variants(example_cases() + [timer_case()], random.Random(11), seeds=(1,))
    assert not vs.dropped, vs.dropped
    assert len(vs.variants) == 9
    for v in vs.variants:
        assert check_case(v, seeds=(1,)).ok


def test_unchanged_and_failing_variants_are_dropped_and_reported():
    short = timer_case(prompt="ok go")
    short["id"] = "t_neu_short_001"
    vs = build_variants([short], random.Random(1), seeds=(1,))
    assert ("t_neu_short_001__typo", "prompt unchanged") in vs.dropped
    broken = timer_case()
    broken["id"] = "t_neu_broken_001"
    broken["oracle"] = [{"text": "I did nothing."}]
    vs = build_variants([broken], random.Random(1), seeds=(1,))
    assert not vs.variants and len(vs.dropped) == 3 and "oracle fails" in vs.dropped[0][1]


def test_a_variant_runs_through_the_trial_and_rows_carry_the_variant_keys():
    case = example_cases()[1]
    v = make_variants(case, random.Random(4))[1]
    bound = bind(v, 1)
    res = run_trial(v, ScriptedClient(oracle_turns(bound), name="oracle"), world_seed=1)
    assert res.verdict.success
    row = rows_for([res])[0]
    assert row["variant_of"] == case["id"] and row["variant"] == "transcript" and row["success"]


# ------------------------------------------------------------------ seeds


def fake_pool(per=60):
    cases = []
    for fam in SEED_FAMILIES + ("mixed", "recovery"):
        for lane in ("assistant", "neutral"):
            for i in range(per):
                c = {"id": f"{fam[:3]}_{lane[:3]}_{i:03d}", "lane": lane, "family": fam, "sub": f"s{i % 3}",
                     "prompt": "do the thing", "tags": ["core"] if i % 5 < 2 else []}
                cases.append(c)
    cases.append({"id": "sel_neu_multi_001", "lane": "neutral", "family": "select", "turns": [{"user": "a"}]})
    cases.append({"id": "sel_neu_follow_001", "lane": "neutral", "family": "select", "prompt": "a", "followup_turn": {"user": "b"}})
    cases.append({"id": "sel_neu_var_001", "lane": "neutral", "family": "select", "prompt": "a", "variant_of": "x"})
    return cases


def test_select_seeds_is_stratified_and_single_turn():
    cases = fake_pool()
    seeds, core = select_seeds(cases, random.Random(2))
    assert len(seeds) == 150 and len(core) == 30
    assert {c["id"] for c in core} <= {c["id"] for c in seeds}
    assert len({c["id"] for c in seeds}) == 150
    for fam in SEED_FAMILIES:
        assert sum(c["family"] == fam for c in seeds) == 30
        assert sum(c["family"] == fam for c in core) == 6
    assert all(c["family"] in SEED_FAMILIES and "prompt" in c and "turns" not in c and "followup_turn" not in c
               and "variant_of" not in c for c in seeds)
    for fam in SEED_FAMILIES:                       # both lanes represented within a family
        assert {c["lane"] for c in seeds if c["family"] == fam} == {"assistant", "neutral"}
    assert sum("core" in c["tags"] for c in core) >= 20     # core-tagged cases are preferred
    again = select_seeds(list(reversed(cases)), random.Random(2))
    assert [c["id"] for c in again.seeds] == [c["id"] for c in seeds]


def test_select_seeds_small_families_pass_their_share_on():
    cases = [c for c in fake_pool(per=60) if c["family"] != "abstain"]
    cases = [c for c in cases if c["family"] != "restraint" or c["id"].endswith(("000", "001", "002"))]
    seeds, core = select_seeds(cases, random.Random(2))
    assert len(seeds) == 150 and len(core) == 30
    assert sum(c["family"] == "restraint" for c in seeds) == 6
    assert not any(c["family"] == "abstain" for c in seeds)


# ------------------------------------------------------------------ metrics


def rows(table):
    out = []
    for (cid, variant, trial), ok in table.items():
        out.append({"case_id": cid if not variant else f"{cid}__{variant}", "variant_of": cid if variant else None,
                    "variant": variant, "trial": trial, "success": ok})
    return out


def test_consistency_and_robustness():
    t = {}
    for trial in (1, 2):
        t[("a", None, trial)] = True
        t[("b", None, trial)] = trial == 1
        for k in ("typo", "transcript", "paraphrase"):
            t[("a", k, trial)] = k != "typo" or trial == 1
            t[("b", k, trial)] = trial == 1
    out = consistency(rows(t))
    # pairs: (a,1) same, (a,2) typo differs, (b,1) same, (b,2) same
    assert out["consistency"] == {"pairs": 4, "same": 3, "value": 0.75}
    r = out["robustness"]
    assert r["typo"]["clean_rate"] == 0.75 and r["typo"]["variant_rate"] == 0.5 and abs(r["typo"]["robustness"] - 2 / 3) < 1e-9
    assert r["transcript"]["robustness"] == 1.0 and r["paraphrase"]["n"] == 4


def test_consistency_edge_cases():
    out = consistency([{"case_id": "a", "trial": 1, "success": False, "variant_of": None, "variant": None}])
    assert out["consistency"]["value"] is None and out["robustness"]["typo"]["robustness"] is None
    t = {("a", None, 1): True, ("a", "typo", 1): True, ("a", "transcript", 1): True}      # a dropped paraphrase
    out = consistency(rows(t))
    assert out["consistency"]["pairs"] == 0 and out["robustness"]["typo"]["robustness"] == 1.0
    ex = rows({("a", None, 1): True, ("a", "typo", 1): False})
    ex[1]["excluded"] = True
    assert consistency(ex)["robustness"]["typo"]["n"] == 0
