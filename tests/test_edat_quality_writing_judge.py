"""Tests for the Writing vote block and the writing judge. Briefs and drafts are invented
stubs built by hand; every judge is a scripted callable. Nothing real runs."""
from __future__ import annotations

import json
import random
from collections import Counter

import pytest

from edat.quality import judge as J
from edat.quality import votes as V

WISP = "Gemma-Wisp"
BASE = "gemma-4-E4B-it-oQ4e-mtp"
CANDS = [BASE, "cand-1", "cand-2", "cand-3", "cand-4"]
CLOUD = [f"cloud-{i}" for i in range(6)]
SUBS = ("W1", "W2", "W3", "W4", "W5", "W6")


def brief(sub, idx, split, *, max_words=120, **cons):
    constraints = {"max_words": max_words, "min_words": None, "exact_words": None, "max_sentences": None,
                   "exact_sentences": None, "max_chars": None, "subject": True, "signoff": "Best, Ada"}
    constraints.update(cons)
    return {"family": "writing", "case_id": f"{sub}-X-{split[:1]}{idx:03d}", "sub": sub, "kind": "email",
            "split": split, "sender": {"name": "Ada Quill", "preferred_name": "Ada"},
            "recipient": {"name": "Bram Holt", "relation": "landlord"},
            "purpose": "ask for a repair date", "constraints": constraints,
            "active_prefs": ["P9"] if sub == "W5" else [],
            "turns": [f"Write an email to Bram Holt (landlord) for me. Purpose: repair date {idx}."]}


def make_items(n_held=30, n_dev=8, trials=1, seed=3, fail_rate=0.0, models=None):
    rng = random.Random(seed)
    models = models or [WISP, *CANDS, *CLOUD]
    items = []
    for sub in SUBS:
        for split, n in (("heldout", n_held), ("development", n_dev)):
            for i in range(n):
                case = brief(sub, i, split)
                for t in range(trials):
                    for m in models:
                        wc = rng.randint(40, 100)
                        items.append({"case": case, "model_id": m, "trial": t,
                                      "draft": " ".join(f"w{k}" for k in range(wc)),
                                      "gate_pass": rng.random() >= fail_rate, "word_count": wc,
                                      "split": split})
    return items


def build(items=None, seed=1, **kw):
    return V.build_writing_pairs(items or make_items(), seed=seed, baseline_id=WISP, base_id=BASE,
                                 cloud_ids=CLOUD, return_report=True, **kw)


# ---------------------------------------------------------------------------
# Plan and pairing
# ---------------------------------------------------------------------------

def test_plan_totals_and_strata():
    plan = V.V1_WRITING_PLAN
    assert V.writing_plan_total(plan) == 205
    assert sum(plan["validation_per_sub"].values()) == 120
    assert set(plan["validation_per_sub"]) == {"W1", "W2", "W3", "W5", "W6"}
    assert all(v == 24 for v in plan["validation_per_sub"].values())
    assert plan["standing"] == 40 and plan["cloud_per_model"] * plan["cloud_models"] == 30
    assert plan["repeats"] == 15


def test_build_hits_quotas_and_never_pairs_w4():
    pairs, rep = build()
    shown = [p for p in pairs if not p.get("auto_outcome")]
    assert rep["blocks"] == {"validation": 120, "standing": 40, "cloud": 30, "repeat": 15}
    assert len(shown) == 205 == rep["n_planned"]
    assert Counter(p["sub"] for p in shown if p["block"] == "validation") == {s: 24 for s in ("W1", "W2", "W3", "W5", "W6")}
    assert all(p["sub"] != "W4" for p in pairs) and rep["w4_pairs"] == 0
    assert rep["validation_models_ok"] and WISP in rep["validation_models"] and BASE in rep["validation_models"]


def test_split_rules_and_sides():
    pairs, _ = build()
    for p in pairs:
        if p["block"] == "cloud":
            assert p["split"] == "development" and p["b"]["model_id"] == WISP and p["a"]["model_id"] in CLOUD
        elif p["block"] == "standing":
            assert p["split"] == "heldout" and p["a"]["model_id"] == BASE and p["b"]["model_id"] == WISP
        elif p["block"] == "validation":
            assert p["split"] == "heldout"
        assert p["a"]["trial"] == p["b"]["trial"]
        assert p["a"]["answer_id"].split(":")[1] == p["b"]["answer_id"].split(":")[1] == p["case_id"]
    cloud = Counter(p["a"]["model_id"] for p in pairs if p["block"] == "cloud")
    assert set(cloud.values()) == {5} and len(cloud) == 6


def test_no_duplicate_pairs_and_repeats_swap_sides():
    pairs, _ = build()
    orig = {p["pair_id"]: p for p in pairs}
    keys = [(p["case_id"], p["a"]["trial"], frozenset((p["a"]["model_id"], p["b"]["model_id"])))
            for p in pairs if p["block"] != "repeat" and not p.get("auto_outcome")]
    assert len(keys) == len(set(keys))
    reps = [p for p in pairs if p["block"] == "repeat"]
    assert len(reps) == 15
    for r in reps:
        o = orig[r["repeat_of"]]
        assert r["a"]["answer_id"] == o["b"]["answer_id"] and r["b"]["answer_id"] == o["a"]["answer_id"]


def test_gate_one_fail_is_auto_and_both_fail_dropped():
    items = make_items(n_held=30, n_dev=8)
    # pick one held-out W1 case: baseline fails, base passes -> auto B wins? (gated draft wins)
    cid = "W1-X-h000"
    for it in items:
        if it["case"]["case_id"] == cid and it["model_id"] == WISP:
            it["gate_pass"] = False
        if it["case"]["case_id"] == cid and it["model_id"] == BASE:
            it["gate_pass"] = True
    cid2 = "W2-X-h001"
    for it in items:
        if it["case"]["case_id"] == cid2 and it["model_id"] in (WISP, "cand-1"):
            it["gate_pass"] = False
    pairs, rep = build(items)
    auto = [p for p in pairs if p.get("auto_outcome")]
    assert all(p["auto"] is True and p["auto_reason"] == "gate" for p in auto)
    mine = [p for p in auto if p["case_id"] == cid and p["a"]["model_id"] == BASE]
    assert mine and mine[0]["auto_outcome"] == "A" and mine[0]["block"] == "standing"
    assert not [p for p in auto if p["case_id"] == cid2 and p["a"]["model_id"] == "cand-1"]
    assert rep["dropped_both_fail"] >= 1
    shown = [p for p in pairs if not p.get("auto_outcome")]
    for p in shown:  # shown pairs have both drafts through the gate
        assert p["a"]["gate_pass"] and p["b"]["gate_pass"]
    assert rep["auto_gate"] == len(auto)
    # a gated candidate loses to a passing baseline
    items2 = make_items(n_held=3, n_dev=0, models=[WISP, BASE])
    for it in items2:
        if it["model_id"] == BASE and it["case"]["sub"] == "W1":
            it["gate_pass"] = False
    p2 = V.build_writing_pairs(items2, seed=0, baseline_id=WISP, base_id=BASE)
    a2 = [p for p in p2 if p.get("auto_outcome") and p["sub"] == "W1"]
    assert a2 and all(p["auto_outcome"] == "B" for p in a2)


def test_length_matched_share_at_least_30_percent():
    pairs, rep = build()
    assert rep["length_matched_share"] >= 0.30
    for p in pairs:
        if not p.get("auto_outcome"):
            ok = V.writing_length_matched(p["a"]["visible_tokens"], p["b"]["visible_tokens"], 0.15)
            assert p["length_matched"] == ok
    assert V.writing_length_matched(100, 90) and not V.writing_length_matched(100, 70)


def test_length_matched_preferred_when_natural_share_low():
    items = make_items(n_held=40, n_dev=8)
    # baseline always short, others long except 1 in 3 near the baseline length
    rng = random.Random(5)
    for it in items:
        if it["model_id"] == WISP:
            wc = 50
        elif rng.random() < 0.34:
            wc = 52
        else:
            wc = 95
        it["word_count"] = wc
    _, rep = build(items)
    assert rep["length_matched_share"] >= 0.30


def test_small_pool_gives_fewer_pairs_not_errors():
    items = make_items(n_held=2, n_dev=1, models=[WISP, BASE, "cand-1"])
    pairs, rep = V.build_writing_pairs(items, seed=0, baseline_id=WISP, base_id=BASE, return_report=True)
    assert rep["n_shown"] < 205 and rep["blocks"]["cloud"] == 0


def test_deterministic_for_seed():
    a, _ = build(seed=4)
    b, _ = build(seed=4)
    assert [p["pair_id"] + p["a"]["answer_id"] for p in a] == [p["pair_id"] + p["a"]["answer_id"] for p in b]


# ---------------------------------------------------------------------------
# Vote screen and summary
# ---------------------------------------------------------------------------

def test_vote_screen_shows_brief_recipient_prefs_and_drafts():
    pairs, _ = build()
    p = next(x for x in pairs if x["sub"] == "W5" and not x.get("auto_outcome"))
    out = []
    V.start_session([p], "/nonexistent/votes.jsonl", input_fn=lambda _: "q", output_fn=out.append, seed=1)
    text = "\n".join(out)
    assert "Brief:" in text and "Bram Holt (landlord)" in text and "P9" in text
    assert "Limit: at most 120 words" in text
    assert p["a"]["text"].split()[0] in text and "tinyagent-bench" in text and "EDAT" not in text


def test_session_votes_and_summary_writing(tmp_path):
    pairs, _ = build()
    votes_path = tmp_path / "votes.jsonl"
    answers = iter(["a", "", "b", "", "g", "", "x", "3", "a", "q"] * 20)
    V.start_session(pairs, votes_path, input_fn=lambda _: next(answers), output_fn=lambda _: None,
                    seed=2, max_pairs=12)
    votes = V.read_jsonl(votes_path)
    assert votes and all(v["outcome"] in ("A", "B", "tie_good", "tie_bad") for v in votes)
    assert {v["outcome"] for v in votes} & {"tie_good", "tie_bad"}
    s = V.summarize_writing(pairs, votes)
    assert set(s["by_sub"]) == {"W1", "W2", "W3", "W5", "W6"}
    assert "full" in s["ranking"] and "length_matched" in s["ranking"]
    assert s["length_matched_share"] >= 0.30


def test_rankings_full_and_length_matched_side_by_side():
    def pr(i, ma, mb, wa, wb, outcome):
        a = {"model_id": ma, "text": "x " * wa, "visible_tokens": wa}
        b = {"model_id": mb, "text": "x " * wb, "visible_tokens": wb}
        return ({"pair_id": f"p{i}", "a": a, "b": b, "length_matched": V.writing_length_matched(wa, wb)}, outcome)
    items = [pr(i, "m1", WISP, 100, 100, "A") for i in range(6)] + \
            [pr(10 + i, "m2", WISP, 100, 100, "B") for i in range(6)] + \
            [pr(20 + i, "m2", WISP, 200, 100, "A") for i in range(4)] + \
            [pr(30 + i, "m1", WISP, 200, 100, "tie_good") for i in range(2)]
    r = V.writing_rankings(items)
    assert r["full"]["n"] == len(items) and r["length_matched"]["n"] == 12
    assert r["length_matched"]["order"][0] == "m1"
    assert r["full"]["models"]["m1"]["bt_win_rate"] > 50


def test_cli_build_writing_and_help(tmp_path, capsys, monkeypatch):
    items = make_items(n_held=30, n_dev=8)
    src = tmp_path / "drafts.jsonl"
    V.write_jsonl(src, items)
    out = tmp_path / "votes" / "writing-pairs.jsonl"
    rc = V.main(["build-writing", "--drafts", str(src), "--out", str(out), "--cloud", ",".join(CLOUD),
                 "--allow-in-repo"])
    assert rc == 0 and out.exists()
    printed = capsys.readouterr().out
    assert '"n_shown": 205' in printed
    with pytest.raises(SystemExit):
        V.main(["--help"])
    helptext = capsys.readouterr().out
    assert "tinyagent-bench" in helptext and "EDAT" not in helptext and "build-writing" in helptext


# ---------------------------------------------------------------------------
# Rubric and prompt
# ---------------------------------------------------------------------------

def test_rubric_verbatim_lines():
    r = J.WRITING_PAIRWISE_RUBRIC
    assert r.startswith("You are comparing two drafts written for the same brief. Decide which one the\nsender would rather send.")
    assert "5. Nothing extra. A longer draft is NOT better. Extra apologies, repeated thanks,\n   filler pleasantries, or details the brief did not give all count against it." in r
    assert "3. It fits the recipient and the relationship. Hard messages are kind and clear:\n   no grovelling, no blame, no passive aggression, no false promises." in r
    assert r.endswith('"reason": "<one sentence, under 25 words>"}')
    assert r.count("\n1. ") == 1 and "Both drafts already contain every required point" in r


def test_messages_contain_brief_recipient_limit_prefs_and_drafts():
    case = brief("W5", 1, "heldout", max_words=80)
    (m,) = J.build_writing_pairwise_messages(case, "DRAFT-ONE text", "DRAFT-TWO text")
    c = m["content"]
    assert c.startswith(J.WRITING_PAIRWISE_RUBRIC)
    assert case["turns"][0] in c and "Bram Holt (landlord)" in c and "at most 80 words" in c
    assert "- P9" in c
    assert c.index("Draft A:\nDRAFT-ONE text") < c.index("Draft B:\nDRAFT-TWO text")
    (m2,) = J.build_writing_pairwise_messages(case, "x", "y", active_prefs=["no emoji"])
    assert "- no emoji" in m2["content"] and "- P9" not in m2["content"]
    nocase = dict(case, recipient=None)
    assert "none" in J.build_writing_pairwise_messages(nocase, "x", "y")[0]["content"]


def test_parse_writing_verdict():
    ok = J.parse_writing_verdict('{"winner": "tie_bad", "deciding_point": 5, "reason": "both stiff"}')
    assert ok["parse_ok"] and ok["winner"] == "tie_bad" and ok["deciding_point"] == 5
    for bad in ('{"winner": "C", "deciding_point": 1, "reason": ""}', "A", '{"winner": "A", "deciding_point": 9, "reason": ""}'):
        assert J.parse_writing_verdict(bad)["parse_ok"] is False


# ---------------------------------------------------------------------------
# judge_writing_pair
# ---------------------------------------------------------------------------

SPEC = J.JudgeSpec(model_id="big-judge", family="other", weights_sha256="0" * 64, quant="q8")


class ScriptedClient:
    """Prefers the draft containing a marker word; counts calls."""

    def __init__(self, prefer="GOOD", position_bias=None):
        self.prefer, self.bias, self.calls = prefer, position_bias, 0

    def chat(self, messages, **kw):
        self.calls += 1
        c = messages[0]["content"]
        a = c.split("Draft A:\n", 1)[1].split("\n\nDraft B:\n")[0]
        if self.bias:
            w = self.bias
        else:
            w = "A" if self.prefer in a else "B"
        return type("R", (), {"text": json.dumps({"winner": w, "deciding_point": 5, "reason": "r"})})()


def wpair(a_text="GOOD draft", b_text="plain draft", **kw):
    case = brief("W1", 1, "heldout")
    p = {"pair_id": "wp1", "case": case, "request": "r", "sub": "W1", "block": "validation",
         "a": {"model_id": "m1", "text": a_text}, "b": {"model_id": WISP, "text": b_text}}
    p.update(kw)
    return p


def test_judge_writing_pair_swap_consistent_and_cached():
    client, cache = ScriptedClient(), {}
    r = J.judge_writing_pair(client, SPEC, wpair(), cache=cache)
    assert r["outcome"] == "A" and r["position_consistent"] and client.calls == 2
    r2 = J.judge_writing_pair(client, SPEC, wpair("plain", "GOOD"), cache=cache)
    assert r2["outcome"] == "B"
    before = client.calls
    J.judge_writing_pair(client, SPEC, wpair(), cache=cache)
    assert client.calls == before  # cache hit in both orders


def test_judge_writing_pair_position_bias_is_a_tie():
    r = J.judge_writing_pair(ScriptedClient(position_bias="A"), SPEC, wpair())
    assert r["outcome"] == "tie_good" and r["position_consistent"] is False


def test_judge_writing_pair_auto_and_family_rule():
    client = ScriptedClient()
    r = J.judge_writing_pair(client, SPEC, wpair(auto_outcome="B"))
    assert r["outcome"] == "B" and client.calls == 0
    gem = J.JudgeSpec(model_id="gemma-x", family="gemma", weights_sha256="0" * 64, quant="q8")
    with pytest.raises(J.FamilyRuleError):
        J.judge_writing_pair(client, gem, wpair())


# ---------------------------------------------------------------------------
# Padding probe
# ---------------------------------------------------------------------------

def drafts(n=30, max_words=120, wc=60, **cons):
    out = []
    for i in range(n):
        case = brief("W1", i, "heldout", max_words=max_words, **cons)
        text = "Hello Bram,\n\n" + " ".join(f"w{k}" for k in range(wc)) + "\n\nBest, Ada"
        out.append({"case": case, "model_id": WISP, "draft": text, "gate_pass": True})
    return out


def shorter_wins(pair):
    return "A" if len(pair["a"]["text"]) < len(pair["b"]["text"]) else "B"


def test_padding_pairs_stay_within_limit_and_use_fixed_paragraphs():
    probe = J.writing_padding_probe(drafts(), n=50, seed=1)
    assert probe["n"] == 50 and probe["passed"] is None
    for p in probe["pairs"]:
        padded = p["b"] if p["expected_winner"] == "A" else p["a"]
        orig = p["a"] if p["expected_winner"] == "A" else p["b"]
        assert len(padded["text"].split()) <= 120 and len(padded["text"]) > len(orig["text"])
        assert any(par in padded["text"] for par in J.POLITE_PARAGRAPHS)
        assert padded["text"].rstrip().endswith("Best, Ada")  # inserted before the sign-off


def test_padding_probe_pass_and_fail():
    ok = J.writing_padding_probe(drafts(), n=50, seed=1, judge_fn=shorter_wins)
    assert ok["passed"] is True and ok["rate"] == 1.0
    longer = J.writing_padding_probe(drafts(), n=50, seed=1,
                                     judge_fn=lambda p: "A" if len(p["a"]["text"]) > len(p["b"]["text"]) else "B")
    assert longer["passed"] is False and longer["rate"] == 0.0
    rng = random.Random(0)
    n_wrong = 6  # 44/50 = 88 % < 90 %
    wrong = set(rng.sample(range(50), n_wrong))
    idx = {}

    def flaky(p):
        k = idx.setdefault(p["pair_id"], len(idx))
        return p["expected_winner"] if k not in wrong else ("B" if p["expected_winner"] == "A" else "A")
    r = J.writing_padding_probe(drafts(), n=50, seed=1, judge_fn=flaky)
    assert r["rate"] == pytest.approx(0.88) and r["passed"] is False
    wrong = set(list(wrong)[:5])  # 45/50 = 90 %
    idx.clear()
    r = J.writing_padding_probe(drafts(), n=50, seed=1, judge_fn=flaky)
    assert r["rate"] == pytest.approx(0.90) and r["passed"] is True


def test_padding_probe_skips_drafts_where_paragraph_does_not_fit():
    tight = J.writing_padding_probe(drafts(max_words=62), n=50, seed=1)
    assert tight["n"] == 0
    exact = J.writing_padding_probe(drafts(exact_words=62, max_words=None), n=50)
    assert exact["n"] == 0
    few = J.writing_padding_probe(drafts(n=3), n=50)
    assert few["n"] < 50
    assert J.writing_padding_probe(drafts(n=3), n=50, judge_fn=shorter_wins)["passed"] is False


# ---------------------------------------------------------------------------
# Writing validation
# ---------------------------------------------------------------------------

def validation_set(n_per_sub=24, wrong_every=None, subs=("W1", "W2", "W3", "W5", "W6", "W4")):
    pairs, votes = [], []
    for sub in subs:
        for i in range(n_per_sub):
            case = brief(sub, i, "heldout")
            p = {"pair_id": f"v-{sub}-{i}", "case": case, "sub": sub, "block": "validation",
                 "a": {"model_id": "m1", "text": "a"}, "b": {"model_id": WISP, "text": "b"}}
            pairs.append(p)
            votes.append({"pair_id": p["pair_id"], "outcome": "A" if i % 2 == 0 else "B"})
    return pairs, votes


def make_judge(pairs, votes, wrong):
    truth = {v["pair_id"]: v["outcome"] for v in votes}

    def fn(p):
        t = truth[p["pair_id"]]
        return t if p["pair_id"] not in wrong else ("B" if t == "A" else "A")
    return fn


PADDING_OK = {"passed": True, "rate": 0.96, "n": 50}


def test_validation_pass_ignores_w4_and_reports_per_sub():
    pairs, votes = validation_set()
    res = J.validate_writing_judge(make_judge(pairs, votes, set()), votes, pairs=pairs, padding=PADDING_OK)
    assert res["passed"] and res["n_pairs"] == 120 and res["n_excluded_w4"] == 24
    assert set(res["per_sub"]) == {"W1", "W2", "W3", "W5", "W6"}
    assert all(v["agreement"] == 1.0 and v["gating"] is False for v in res["per_sub"].values())
    assert any(t["name"] == "writing_padding_probe" and t["passed"] for t in res["tests"])


def test_validation_fails_below_80_percent_but_per_sub_does_not_gate():
    pairs, votes = validation_set(subs=("W1", "W2", "W3", "W5", "W6"))
    # W1 wrong on 12/24 (50 %), everything else right: overall 108/120 = 90 % -> pass
    wrong = {f"v-W1-{i}" for i in range(12)}
    res = J.validate_writing_judge(make_judge(pairs, votes, wrong), votes, pairs=pairs, padding=PADDING_OK)
    assert res["per_sub"]["W1"]["agreement"] == 0.5 and res["passed"]
    # 25 wrong of 120 = 79.2 % -> fail
    wrong = {f"v-W1-{i}" for i in range(24)} | {"v-W2-0"}
    res = J.validate_writing_judge(make_judge(pairs, votes, wrong), votes, pairs=pairs, padding=PADDING_OK)
    assert not res["passed"]
    assert [t for t in res["tests"] if t["name"] == "agreement_non_tie"][0]["passed"] is False
    # exactly 24 wrong = 80 % passes
    wrong = {f"v-W1-{i}" for i in range(24)}
    res = J.validate_writing_judge(make_judge(pairs, votes, wrong), votes, pairs=pairs, padding=PADDING_OK)
    assert res["passed"]


def test_validation_requires_padding_probe_and_ignores_ties():
    pairs, votes = validation_set(subs=("W1", "W2"))
    fn = make_judge(pairs, votes, set())
    assert not J.validate_writing_judge(fn, votes, pairs=pairs)["passed"]
    assert not J.validate_writing_judge(fn, votes, pairs=pairs, padding={"passed": False, "rate": 0.8, "n": 50})["passed"]
    votes[0]["outcome"] = "tie_good"
    res = J.validate_writing_judge(fn, votes, pairs=pairs, padding=PADDING_OK)
    assert res["n_non_tie"] == res["n_pairs"] - 1 and res["passed"]


def test_validation_uses_embedded_pairs_and_position_flag():
    pairs, votes = validation_set(subs=("W1",))
    emb = [{**v, "pair": p} for v, p in zip(votes, pairs)]
    base_fn = make_judge(pairs, votes, set())
    res = J.validate_writing_judge(lambda p: {"outcome": base_fn(p), "position_consistent": False}, emb,
                                   padding=PADDING_OK)
    assert not res["passed"] and any(t["name"] == "position_consistency" and not t["passed"] for t in res["tests"])


# ---------------------------------------------------------------------------
# writing_pref_judge
# ---------------------------------------------------------------------------

def test_writing_pref_judge_includes_auto_and_ties_half():
    def mk(i, m, outcome=None, **kw):
        p = {"pair_id": f"p{i}", "a": {"model_id": m, "text": "x " * 50, "visible_tokens": 50},
             "b": {"model_id": WISP, "text": "y " * 50, "visible_tokens": 50}, "length_matched": True, "block": "validation"}
        p.update(kw)
        return p
    pairs = [mk(0, "m1"), mk(1, "m1"), mk(2, "m1"), mk(3, "m1", auto_outcome="B", auto=True),
             mk(4, "m2"), mk(5, "m2", auto_outcome="A", auto=True)]
    verdicts = [{"pair_id": "p0", "outcome": "A"}, {"pair_id": "p1", "outcome": "tie_good"},
                {"pair_id": "p2", "outcome": "B"}, {"pair_id": "p4", "outcome": "B"}]
    res = J.writing_pref_judge(verdicts, pairs)
    # m1: win, tie, loss, auto loss -> 1.5 / 4
    assert res["m1"]["win_rate_raw"] == pytest.approx(37.5) and res["m1"]["n_auto"] == 1 and res["m1"]["n"] == 4
    assert res["m2"]["win_rate_raw"] == pytest.approx(50.0)
    assert res["m1"]["style_controlled"] is not None and 0 < res["m1"]["style_controlled"] < 100
    assert res["m1"]["style_controlled"] < res["m2"]["style_controlled"]
    assert "full" in res["_ranking"] and "length_matched" in res["_ranking"]
    # a pair with no verdict and no auto outcome is left out
    assert J.writing_pref_judge([], [mk(9, "m3")]).get("m3") is None
