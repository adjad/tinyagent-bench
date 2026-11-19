"""Tests for the EDAT judge harness. A scripted stub stands in for every model; nothing real runs."""

import json
import re

import pytest

from edat.quality import judge as J

# --------------------------------------------------------------------------
# Expected rubric text, copied from spec 03 section 4.4
# --------------------------------------------------------------------------

EXPECTED_PAIRWISE = r'''You are comparing two answers from an on-device assistant. The answers appear in
the display named below. Decide which answer a typical user would rather receive.

Judge readability and writing quality only. Both answers have already passed
automatic checks for leaks and broken formatting. Treat facts as correct unless
one answer plainly contradicts the request or the other answer.

Look at these, in this order:
1. Answer first. The thing the user asked for appears at the start, not after
   background or a restatement of the question.
2. Shape fits the content. One fact is a sentence. Three or more parallel items
   are a list. Items with several fields lead with a short bold label. Steps that
   must happen in order are numbered. Text meant to be sent to someone or read
   aloud has no formatting. In the "panel" display, tables, headings and nested
   lists are wrong.
3. Just enough. It includes what the user needs and nothing they did not ask
   for. A longer answer is NOT better. Extra caveats, repeated points, a
   restated question, an offer to help further, or a friendly opener with no
   content all count against an answer.
4. Plain, clear wording. Short sentences, common words, concrete details (names,
   times, amounts) instead of vague phrases. No jargon the user did not use.
5. Tone fits the request. Where the request sets no tone, calm, kind and
   direct. Where it does (a formal note, a quick yes or no, a playful message),
   follow it. Not gushing, not robotic, no needless emoji or exclamation marks.

If both answers are about equally good, or both are poor, say so.

Reply with exactly one line of JSON:
{"winner": "A" | "B" | "tie_good" | "tie_bad", "deciding_point": 1-5,
 "reason": "<one sentence, under 25 words>"}'''

EXPECTED_POINTWISE = r'''Score the answer from 1 to 5 on each line. 5 = a careful human writer would not
change it; 3 = usable but a reader would notice a flaw; 1 = hard to use.
answer_first, shape_fit, concision, clarity, tone
Do not reward length. An answer that is twice as long as needed scores at most 3
on concision even if every sentence is true.
Reply with JSON: {"answer_first": n, "shape_fit": n, "concision": n,
"clarity": n, "tone": n}'''


# --------------------------------------------------------------------------
# Scripted stub with the adapter's chat() signature
# --------------------------------------------------------------------------


class Result:
    def __init__(self, text):
        self.text = text


class StubClient:
    def __init__(self, policy):
        self.policy = policy
        self.calls = []

    def chat(self, messages, *, tools=None, sampling=None, seed=None, max_tokens, thinking):
        self.calls.append({"messages": messages, "seed": seed, "max_tokens": max_tokens, "thinking": thinking, "sampling": sampling})
        return Result(self.policy(messages))


def shown_answers(messages):
    content = messages[-1]["content"]
    m = re.search(r"Answer A:\n(.*)\n\nAnswer B:\n(.*)\Z", content, re.S)
    return m.group(1), m.group(2)


def verdict_line(winner, point=3, reason="Clearer."):
    return json.dumps({"winner": winner, "deciding_point": point, "reason": reason})


def shorter_wins(messages):
    a, b = shown_answers(messages)
    return verdict_line("A" if len(a) < len(b) else "B", 3)


def plainer_wins(messages):
    a, b = shown_answers(messages)
    da, db = a.count("**") + len(re.findall(r"[^\x00-\u2000]", a)), b.count("**") + len(re.findall(r"[^\x00-\u2000]", b))
    return verdict_line("A" if da < db else "B", 5)


def always_a(messages):
    return verdict_line("A", 1)


SPEC = J.JudgeSpec("Qwen-Judge-72B", "qwen", "ab" * 32, "q4", {"temperature": 0}, 7)


def mkpair(pid="p1", a_text="Short answer.", b_text="A much longer answer that goes on.", a_model="m1", b_model="Gemma-Wisp", **extra):
    p = {
        "pair_id": pid,
        "case_id": "c1",
        "split": "heldout",
        "block": "validation",
        "request": "When is the call?",
        "profile": "panel",
        "a": {"answer_id": pid + "a", "model_id": a_model, "text": a_text},
        "b": {"answer_id": pid + "b", "model_id": b_model, "text": b_text},
    }
    p.update(extra)
    return p


# --------------------------------------------------------------------------
# Rubrics, identity, family
# --------------------------------------------------------------------------


def test_rubrics_match_spec_verbatim():
    assert J.PAIRWISE_RUBRIC == EXPECTED_PAIRWISE
    assert J.POINTWISE_RUBRIC == EXPECTED_POINTWISE
    assert "A longer answer is NOT better." in J.PAIRWISE_RUBRIC


def test_rubric_hash_is_sha256_of_text():
    import hashlib

    assert J.rubric_hash() == hashlib.sha256(EXPECTED_PAIRWISE.encode()).hexdigest()
    assert J.rubric_hash(J.POINTWISE_RUBRIC) != J.rubric_hash()
    assert SPEC.rubric_hash == J.rubric_hash()


def test_judge_id_stable_and_changes_with_any_field():
    base = J.judge_id(SPEC)
    assert base == J.judge_id(J.JudgeSpec("Qwen-Judge-72B", "qwen", "ab" * 32, "q4", {"temperature": 0}, 7))
    for changed in (
        J.JudgeSpec("Qwen-Judge-32B", "qwen", "ab" * 32, "q4", {"temperature": 0}, 7),
        J.JudgeSpec("Qwen-Judge-72B", "qwen", "cd" * 32, "q4", {"temperature": 0}, 7),
        J.JudgeSpec("Qwen-Judge-72B", "qwen", "ab" * 32, "q8", {"temperature": 0}, 7),
        J.JudgeSpec("Qwen-Judge-72B", "qwen", "ab" * 32, "q4", {"temperature": 0.3}, 7),
        J.JudgeSpec("Qwen-Judge-72B", "qwen", "ab" * 32, "q4", {"temperature": 0}, 8),
        J.JudgeSpec("Qwen-Judge-72B", "qwen", "ab" * 32, "q4", {"temperature": 0}, 7, rubric_hash="0" * 64),
    ):
        assert J.judge_id(changed) != base


def test_default_sampling_is_temperature_zero():
    assert J.JudgeSpec("m", "f", "h", "q").sampling == {"temperature": 0}


def test_family_rule_refuses_gemma_judge():
    for spec in (
        J.JudgeSpec("gemma-4-27b", "gemma", "h", "q4"),
        J.JudgeSpec("Gemma-Wisp", "tuned", "h", "q4"),
        J.JudgeSpec("some-model", "Gemma", "h", "q4"),
    ):
        with pytest.raises(J.FamilyRuleError):
            J.check_family(spec, ["x", "y"], {})
    with pytest.raises(J.FamilyRuleError):
        J.judge_pair(StubClient(always_a), J.JudgeSpec("gemma-4-27b", "gemma", "h", "q4"), mkpair())


def test_family_rule_pair_with_own_model_or_family_is_routed():
    fam = {"Qwen-Judge-72B": "qwen", "qwen-small": "qwen", "Gemma-Wisp": "gemma", "other": "other"}
    ok = J.check_family(SPEC, ["other", "Gemma-Wisp"], fam)
    assert ok == {"ok": True, "reason": "", "route": None}
    own = J.check_family(SPEC, ["Qwen-Judge-72B", "other"], fam)
    assert not own["ok"] and own["route"] == "second_judge"
    sibling = J.check_family(SPEC, ["other", "qwen-small"], fam)
    assert not sibling["ok"] and "qwen" in sibling["reason"]
    client = StubClient(always_a)
    with pytest.raises(J.FamilyRuleError):
        J.judge_pair(client, SPEC, mkpair(a_model="qwen-small"), cache={}, model_family=fam)
    assert client.calls == []


# --------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------


def test_pairwise_messages_order_and_content():
    msgs = J.build_pairwise_messages("When is the call?", "panel", "AAA text", "BBB text")
    assert len(msgs) == 1 and msgs[0]["role"] == "user"
    text = msgs[0]["content"]
    assert text.startswith(EXPECTED_PAIRWISE)
    positions = [text.index(s) for s in ("When is the call?", "Display: panel", "Answer A:\nAAA text", "Answer B:\nBBB text")]
    assert positions == sorted(positions)
    assert text.index("Reply with exactly one line of JSON") < positions[0]
    assert "preferences" not in text[len(EXPECTED_PAIRWISE):]


def test_pairwise_messages_list_active_preferences():
    msgs = J.build_pairwise_messages("q", "panel", "a", "b", active_prefs=["At most three sentences", "No tables"])
    text = msgs[0]["content"][len(EXPECTED_PAIRWISE):]
    assert "- At most three sentences" in text and "- No tables" in text
    assert "Answers that follow them are not marked down for brevity." in text
    assert text.index("No tables") < text.index("Answer A:")


def test_pointwise_messages():
    msgs = J.build_pointwise_messages("q", "window", "the answer")
    assert msgs[0]["content"].startswith(EXPECTED_POINTWISE)
    assert msgs[0]["content"].endswith("Answer:\nthe answer")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parse_verdict_good_json():
    v = J.parse_verdict('{"winner": "B", "deciding_point": 3, "reason": "Shorter and direct."}')
    assert v == {"winner": "B", "deciding_point": 3, "reason": "Shorter and direct.", "parse_ok": True}
    assert J.parse_verdict(verdict_line("tie_bad", 5))["winner"] == "tie_bad"


def test_parse_verdict_tolerates_code_fence():
    for text in ("```json\n" + verdict_line("A", 1) + "\n```", "```\n" + verdict_line("A", 1) + "\n```", "  " + verdict_line("A", 1) + "\n"):
        v = J.parse_verdict(text)
        assert v["parse_ok"] and v["winner"] == "A" and v["deciding_point"] == 1


@pytest.mark.parametrize(
    "text",
    [
        "I think A is better.",
        "",
        None,
        "{not json}",
        '{"winner": "C", "deciding_point": 2, "reason": "x"}',
        '{"winner": "A", "deciding_point": 9, "reason": "x"}',
        '{"winner": "A", "deciding_point": "2", "reason": "x"}',
        '{"winner": "A"}',
        '["A"]',
        'Sure.\n{"winner": "A", "deciding_point": 2, "reason": "x"}',
        '{"winner": "A",\n "deciding_point": 2, "reason": "x"}',
    ],
)
def test_parse_verdict_garbage_is_parse_not_ok_and_a_tie(text):
    v = J.parse_verdict(text)
    assert v["parse_ok"] is False
    assert v["winner"] in J.TIES


def test_parse_pointwise():
    good = '{"answer_first": 5, "shape_fit": 4, "concision": 3, "clarity": 4, "tone": 5}'
    assert J.parse_pointwise(good) == {"scores": {"answer_first": 5, "shape_fit": 4, "concision": 3, "clarity": 4, "tone": 5}, "parse_ok": True}
    assert J.parse_pointwise("```json\n" + good + "\n```")["parse_ok"]
    assert not J.parse_pointwise('{"answer_first": 5}')["parse_ok"]
    assert not J.parse_pointwise('{"answer_first": 6, "shape_fit": 4, "concision": 3, "clarity": 4, "tone": 5}')["parse_ok"]
    assert not J.parse_pointwise("nope")["parse_ok"]


def test_judge_pointwise_uses_client():
    good = '{"answer_first": 5, "shape_fit": 4, "concision": 3, "clarity": 4, "tone": 5}'
    client = StubClient(lambda m: good)
    out = J.judge_pointwise(client, SPEC, "q", "panel", "an answer")
    assert out["parse_ok"] and out["scores"]["concision"] == 3 and out["judge_id"] == J.judge_id(SPEC)
    assert client.calls[0]["thinking"] is False and client.calls[0]["seed"] == 7


# --------------------------------------------------------------------------
# judge_pair: swap order, ties, cache, leaks
# --------------------------------------------------------------------------


def test_both_orders_agree_gives_the_verdict():
    client = StubClient(shorter_wins)
    rec = J.judge_pair(client, SPEC, mkpair(), cache={})
    assert rec["outcome"] == "A" and rec["position_consistent"] is True
    assert rec["judge_id"] == J.judge_id(SPEC) and rec["pair_id"] == "p1"
    assert [o["order"] for o in rec["orders"]] == ["AB", "BA"]
    ab, ba = rec["orders"]
    assert ab["winner"] == "A" and ab["shown_winner"] == "A"
    assert ba["shown_winner"] == "B" and ba["winner"] == "A"  # mapped back to the pair's own sides
    assert all(o["parse_ok"] and o["deciding_point"] == 3 and o["raw"] for o in rec["orders"])
    # second call showed the answers swapped
    assert shown_answers(client.calls[1]["messages"]) == ("A much longer answer that goes on.", "Short answer.")
    assert all(c["max_tokens"] and c["thinking"] is False and c["seed"] == 7 for c in client.calls)


def test_position_bias_becomes_a_tie_and_is_recorded():
    rec = J.judge_pair(StubClient(always_a), SPEC, mkpair(), cache={})
    assert rec["outcome"] == "tie_good"
    assert rec["position_consistent"] is False
    assert [o["winner"] for o in rec["orders"]] == ["A", "B"]


def test_disagreement_label_is_tie_bad_when_any_order_says_so():
    replies = iter([verdict_line("tie_bad", 3), verdict_line("A", 3)])
    rec = J.judge_pair(StubClient(lambda m: next(replies)), SPEC, mkpair(), cache={})
    assert rec["outcome"] == "tie_bad" and rec["position_consistent"] is False


def test_agreed_ties_keep_their_label():
    rec = J.judge_pair(StubClient(lambda m: verdict_line("tie_bad", 3)), SPEC, mkpair(), cache={})
    assert rec["outcome"] == "tie_bad" and rec["position_consistent"] is True
    rec = J.judge_pair(StubClient(lambda m: verdict_line("tie_good", 3)), SPEC, mkpair("p2"), cache={})
    assert rec["outcome"] == "tie_good" and rec["position_consistent"] is True


def test_unparseable_reply_counts_as_tie():
    rec = J.judge_pair(StubClient(lambda m: "no idea"), SPEC, mkpair(), cache={})
    assert rec["outcome"] in J.TIES
    assert [o["parse_ok"] for o in rec["orders"]] == [False, False]
    replies = iter(["no idea", verdict_line("A", 2)])
    rec = J.judge_pair(StubClient(lambda m: next(replies)), SPEC, mkpair("p3"), cache={})
    assert rec["outcome"] in J.TIES and rec["position_consistent"] is False


def test_cache_prevents_second_call(tmp_path):
    client = StubClient(shorter_wins)
    cache = {}
    first = J.judge_pair(client, SPEC, mkpair(), cache=cache)
    assert len(client.calls) == 2 and len(cache) == 2
    again = J.judge_pair(client, SPEC, mkpair(), cache=cache)
    assert len(client.calls) == 2
    assert again == first
    # the same content under another pair id is also a hit; changed text is not
    J.judge_pair(client, SPEC, mkpair("other-id"), cache=cache)
    assert len(client.calls) == 2
    J.judge_pair(client, SPEC, mkpair(a_text="Different text."), cache=cache)
    assert len(client.calls) == 4
    # a new judge id (any spec change) re-runs
    J.judge_pair(client, J.JudgeSpec("Qwen-Judge-72B", "qwen", "ab" * 32, "q4", {"temperature": 0}, 8), mkpair(), cache=cache)
    assert len(client.calls) == 6


def test_jsonl_cache_persists_across_instances(tmp_path):
    path = tmp_path / "sub" / "cache.jsonl"
    client = StubClient(shorter_wins)
    cache = J.JsonlCache(path)
    first = J.judge_pair(client, SPEC, mkpair(), cache=cache)
    assert len(client.calls) == 2 and len(cache) == 2
    reopened = J.JsonlCache(path)
    assert len(reopened) == 2
    again = J.judge_pair(client, SPEC, mkpair(), cache=reopened)
    assert len(client.calls) == 2 and again == first
    lines = path.read_text().splitlines()
    assert len(lines) == 2 and all(set(json.loads(x)) == {"key", "value"} for x in lines)


def test_jsonl_cache_ignores_torn_last_line(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text(json.dumps({"key": "k", "value": {"raw": "x"}}) + '\n{"key": "bro')
    c = J.JsonlCache(path)
    assert c.get("k") == {"raw": "x"} and "k" in c and len(c) == 1


def test_leaking_answers_are_never_sent_and_lose():
    client = StubClient(always_a)
    pair = mkpair()
    pair["a"]["leak"] = True
    rec = J.judge_pair(client, SPEC, pair, cache={})
    assert rec["outcome"] == "B" and client.calls == [] and rec["orders"] == []
    pair = mkpair()
    pair["b"]["leak"] = "think_leak"
    assert J.judge_pair(client, SPEC, pair, cache={})["outcome"] == "A"
    both = mkpair()
    both["a"]["leak"] = both["b"]["leak"] = True
    assert J.judge_pair(client, SPEC, both, cache={})["outcome"] == "tie_bad"
    assert client.calls == []


def test_active_prefs_reach_the_prompt_and_the_cache_key():
    client = StubClient(shorter_wins)
    cache = {}
    J.judge_pair(client, SPEC, mkpair(), cache=cache)
    J.judge_pair(client, SPEC, mkpair(), cache=cache, active_prefs=["No tables"])
    assert len(client.calls) == 4
    assert "No tables" in client.calls[2]["messages"][0]["content"]


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------

ANSWER = "The call is at 15:00 on Tue 19 Nov.\n- Bring the report.\n- Join from the side room."


def test_padding_probe_shape():
    p = J.make_padding_probe(ANSWER, "When is the call?")
    assert ANSWER in p and len(p) > len(ANSWER) * 1.5
    assert p.startswith(J.FILLER_OPENERS[0]) and p.endswith(J.FILLER_CLOSERS[0])
    assert "When is the call?" in p and "To restate" in p
    assert J.make_padding_probe(ANSWER, "q", variant=1) != J.make_padding_probe(ANSWER, "q", variant=0)


def test_decoration_probe_adds_bold_and_emoji_only():
    d = J.make_decoration_probe(ANSWER)
    assert d != ANSWER and "**" in d and re.search(r"[\u2728\U0001F44D\U0001F4A1\U0001F680]", d)
    stripped = re.sub(r"[\u2728\U0001F44D\U0001F4A1\U0001F680]", "", d).replace("**", "")
    assert " ".join(stripped.split()) == " ".join(ANSWER.split())
    # list markers survive
    assert d.splitlines()[1].startswith("- ") and d.splitlines()[2].startswith("- ")


def _answers(k):
    return [
        {"answer_id": f"a{i}", "model_id": "m1", "case_id": f"c{i}", "request": f"Question {i}?", "profile": "panel", "text": f"Answer {i} is here.\n- one {i}\n- two {i}"}
        for i in range(k)
    ]


def test_probe_set_builds_50_plus_50_with_expected_winners():
    ps = J.probe_set(_answers(20), n=50, seed=3)
    assert len(ps["padding"]) == 50 and len(ps["decoration"]) == 50
    for kind in ("padding", "decoration"):
        for p in ps[kind]:
            assert p["probe"] == kind and p["block"] == "probe" and p["expected_winner"] in "AB"
            orig = p["a"] if p["expected_winner"] == "A" else p["b"]
            other = p["b"] if p["expected_winner"] == "A" else p["a"]
            assert other["answer_id"] == orig["answer_id"] + "+" + kind
            if kind == "padding":
                assert len(other["text"]) > len(orig["text"])
    sides = {p["expected_winner"] for p in ps["padding"]}
    assert sides == {"A", "B"}  # sides are shuffled
    assert len({p["pair_id"] for p in ps["padding"] + ps["decoration"]}) == 100
    assert J.probe_set(_answers(20), n=50, seed=3) == ps
    assert J.probe_set(_answers(20), n=50, seed=4) != ps


def test_probe_set_skips_leaks_and_reuses_small_pools():
    answers = _answers(3)
    answers[0]["leak"] = True
    ps = J.probe_set(answers, n=50, seed=1)
    assert len(ps["padding"]) == 50
    assert all("a0" not in (p["a"]["answer_id"] + p["b"]["answer_id"]) for p in ps["padding"])
    with pytest.raises(ValueError):
        J.probe_set([{"leak": True, "text": "x", "request": "q"}])


def test_probes_run_through_the_judge_and_join_to_results():
    ps = J.probe_set(_answers(10), n=10, seed=2)
    cache = {}
    pad = [J.judge_pair(StubClient(shorter_wins), SPEC, p, cache=cache) for p in ps["padding"]]
    dec = [J.judge_pair(StubClient(plainer_wins), SPEC, p, cache=cache) for p in ps["decoration"]]
    pr = J.probe_results(ps["padding"], pad)
    dr = J.probe_results(ps["decoration"], dec)
    assert len(pr) == len(dr) == 10
    assert all(r["outcome"] == r["expected_winner"] for r in pr + dr)


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


def test_wilson_interval_known_values():
    lo, hi = J.wilson_interval(10, 20)
    assert lo == pytest.approx(0.299, abs=0.002) and hi == pytest.approx(0.701, abs=0.002)
    lo, hi = J.wilson_interval(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.278, abs=0.002)
    assert J.wilson_interval(0, 0) == (0.0, 1.0)


def test_kappa_known_values():
    assert J.cohens_kappa([("A", "A"), ("B", "B")]) == 1.0
    assert J.cohens_kappa([("A", "B"), ("B", "A")]) == pytest.approx(-1.0)
    # 2x2 textbook case: po = 0.7, pe = 0.5 -> kappa 0.4
    xy = [("A", "A")] * 20 + [("A", "B")] * 5 + [("B", "A")] * 5 + [("B", "B")] * 20
    assert J.cohens_kappa(xy) == pytest.approx(0.6)
    assert J.cohens_kappa([]) is None


# --------------------------------------------------------------------------
# pref_judge
# --------------------------------------------------------------------------


def test_pref_judge_counts_ties_half():
    pairs = [
        mkpair("p1", a_model="m1", b_model="Gemma-Wisp"),
        mkpair("p2", a_model="Gemma-Wisp", b_model="m1"),
        mkpair("p3", a_model="m1", b_model="Gemma-Wisp"),
        mkpair("p4", a_model="m2", b_model="Gemma-Wisp"),
        mkpair("p5", a_model="m1", b_model="m2"),  # candidate vs candidate: not counted
    ]
    verdicts = [
        {"pair_id": "p1", "outcome": "A"},  # m1 wins
        {"pair_id": "p2", "outcome": "A"},  # baseline wins
        {"pair_id": "p3", "outcome": "tie_bad"},  # half
        {"pair_id": "p4", "outcome": "B"},
        {"pair_id": "p5", "outcome": "A"},
    ]
    out = J.pref_judge(verdicts, pairs)
    assert out["m1"] == {"win_rate": pytest.approx(0.5), "n": 3, "non_tie": 2}
    assert out["m2"] == {"win_rate": 0.0, "n": 1, "non_tie": 1}
    assert set(out) == {"m1", "m2"}


# --------------------------------------------------------------------------
# validate_judge on constructed vote sets
# --------------------------------------------------------------------------

BASE = "Gemma-Wisp"


def build_world():
    """6 models x 40 pairs against the baseline (author: 30% model wins, 30% baseline wins,
    40% tie_good) plus 60 candidate-against-candidate pairs. The judge copies the author."""
    pairs, votes = [], []
    models = [f"m{i}" for i in range(6)]
    for mi, m in enumerate(models):
        for j in range(40):
            base_first = j % 2 == 0
            a, b = (BASE, m) if base_first else (m, BASE)
            model_side = "B" if base_first else "A"
            other = "A" if model_side == "B" else "B"
            r = j % 10
            outcome = model_side if r < 3 else other if r < 6 else "tie_good"
            pid = f"v-{m}-{j}"
            pairs.append(mkpair(pid, a_model=a, b_model=b))
            votes.append({"pair_id": pid, "outcome": outcome, "block": "validation"})
    for k in range(60):
        a, b = models[k % 6], models[(k + 1) % 6]
        pid = f"cvc-{k}"
        pairs.append(mkpair(pid, a_model=a, b_model=b))
        votes.append({"pair_id": pid, "outcome": "tie_good" if k % 10 == 0 else ("A" if k % 2 else "B"), "block": "validation"})
    verdicts = [{"pair_id": v["pair_id"], "judge_id": "j", "outcome": v["outcome"], "position_consistent": True, "orders": []} for v in votes]
    return pairs, votes, verdicts


GOOD_PROBES = {
    "padding": [{"expected_winner": "A", "outcome": "A"}] * 50,
    "decoration": [{"expected_winner": "B", "outcome": "B"}] * 50,
}


def run(verdicts, votes, pairs, self_consistency=0.9, probes=GOOD_PROBES, **kw):
    return J.validate_judge(verdicts, votes, pairs, self_consistency=self_consistency, probes=probes, **kw)


def by_name(report):
    return {t["name"]: t for t in report["tests"]}


def flip(outcome):
    return {"A": "B", "B": "A"}.get(outcome, outcome)


def test_validate_passes_when_judge_matches_the_author():
    pairs, votes, verdicts = build_world()
    rep = run(verdicts, votes, pairs)
    t = by_name(rep)
    assert rep["passed"] is True and rep["fallback"] is None
    assert set(t) == {
        "agreement_non_tie",
        "agreement_vs_self_consistency",
        "cohens_kappa",
        "candidate_vs_candidate_agreement",
        "per_model_win_rate",
        "position_consistency",
        "padding_probe",
        "decoration_probe",
    }
    assert all(x["passed"] for x in t.values())
    assert t["agreement_non_tie"]["value"] == 1.0 and t["agreement_non_tie"]["threshold"] == 0.80
    assert t["cohens_kappa"]["threshold"] == 0.6 and t["cohens_kappa"]["value"] == pytest.approx(1.0)
    assert t["candidate_vs_candidate_agreement"]["n"] > 0
    assert set(t["per_model_win_rate"]["models"]) == {f"m{i}" for i in range(6)}
    for x in t.values():
        assert {"name", "value", "threshold", "passed"} <= set(x)
    assert t["padding_probe"]["threshold"] == 0.90 and t["decoration_probe"]["threshold"] == 0.85


def test_validate_fails_on_low_agreement():
    pairs, votes, verdicts = build_world()
    n = 0
    for v in verdicts:
        if v["outcome"] in ("A", "B"):
            n += 1
            if n % 4 == 0:
                v["outcome"] = flip(v["outcome"])
    rep = run(verdicts, votes, pairs)
    t = by_name(rep)
    assert t["agreement_non_tie"]["value"] < 0.80 and not t["agreement_non_tie"]["passed"]
    assert rep["passed"] is False and rep["fallback"] == "structure only"


def test_validate_fails_when_agreement_trails_self_consistency_by_more_than_five_points():
    pairs, votes, verdicts = build_world()
    n = 0
    for v in verdicts:
        if v["outcome"] in ("A", "B"):
            n += 1
            if n % 7 == 0:
                v["outcome"] = flip(v["outcome"])
    rep = run(verdicts, votes, pairs, self_consistency=0.95)
    t = by_name(rep)
    assert 0.80 <= t["agreement_non_tie"]["value"] < 0.90
    assert t["agreement_non_tie"]["passed"]
    assert t["agreement_vs_self_consistency"]["threshold"] == pytest.approx(0.90)
    assert not t["agreement_vs_self_consistency"]["passed"] and not rep["passed"]
    # a rater who is no more consistent than the judge makes it pass
    rep2 = run(verdicts, votes, pairs, self_consistency=0.86)
    assert by_name(rep2)["agreement_vs_self_consistency"]["passed"]
    # a percent is read as a percent
    assert by_name(run(verdicts, votes, pairs, self_consistency=86))["agreement_vs_self_consistency"]["passed"]
    assert not by_name(run(verdicts, votes, pairs, self_consistency=None))["agreement_vs_self_consistency"]["passed"]


def test_validate_fails_on_low_kappa_even_with_perfect_non_tie_agreement():
    pairs, votes, verdicts = build_world()
    for v in verdicts:
        if v["outcome"] == "tie_good":
            v["outcome"] = "tie_bad"
    rep = run(verdicts, votes, pairs)
    t = by_name(rep)
    assert t["agreement_non_tie"]["value"] == 1.0 and t["agreement_non_tie"]["passed"]
    assert t["cohens_kappa"]["value"] < 0.6 and not t["cohens_kappa"]["passed"]
    assert not rep["passed"]


def test_validate_fails_on_low_candidate_vs_candidate_agreement():
    pairs, votes, verdicts = build_world()
    for v in verdicts:
        if v["pair_id"].startswith("cvc-") and v["outcome"] in ("A", "B"):
            v["outcome"] = flip(v["outcome"])
    rep = run(verdicts, votes, pairs)
    t = by_name(rep)
    assert t["candidate_vs_candidate_agreement"]["value"] == 0.0
    assert not t["candidate_vs_candidate_agreement"]["passed"] and not rep["passed"]
    # reported and passing just above the line: flip about a third
    pairs, votes, verdicts = build_world()
    n = 0
    for v in verdicts:
        if v["pair_id"].startswith("cvc-") and v["outcome"] in ("A", "B"):
            n += 1
            if n % 4 == 0:
                v["outcome"] = flip(v["outcome"])
    assert by_name(run(verdicts, votes, pairs))["candidate_vs_candidate_agreement"]["passed"]


def test_validate_fails_when_one_model_is_outside_the_wilson_interval():
    pairs, votes, verdicts = build_world()
    pm = {p["pair_id"]: p for p in pairs}
    for v in verdicts:
        if v["pair_id"].startswith("v-m3-"):
            p = pm[v["pair_id"]]
            baseline_side = "A" if p["a"]["model_id"] == BASE else "B"
            v["outcome"] = baseline_side  # the judge always prefers the baseline over m3
    rep = run(verdicts, votes, pairs)
    t = by_name(rep)["per_model_win_rate"]
    assert not t["passed"] and t["failing"] == ["m3"]
    assert t["models"]["m3"]["judge_win_rate"] == 0.0 and t["models"]["m3"]["inside"] is False
    assert t["models"]["m0"]["inside"] is True
    assert t["models"]["m3"]["interval"][0] < t["models"]["m3"]["author_win_rate"] < t["models"]["m3"]["interval"][1]
    assert not rep["passed"]


def test_validate_per_model_tolerates_noise_inside_the_interval():
    pairs, votes, verdicts = build_world()
    pm = {p["pair_id"]: p for p in pairs}
    # swap two model wins to ties for m1: judge rate moves a little, still inside the interval
    changed = 0
    for v, vote in zip(verdicts, votes):
        if v["pair_id"].startswith("v-m1-") and vote["outcome"] in ("A", "B") and changed < 2:
            p = pm[v["pair_id"]]
            model_side = "B" if p["a"]["model_id"] == BASE else "A"
            if vote["outcome"] == model_side:
                v["outcome"] = "tie_good"
                changed += 1
    assert changed == 2
    assert by_name(run(verdicts, votes, pairs))["per_model_win_rate"]["models"]["m1"]["inside"]


def test_validate_fails_on_low_position_consistency():
    pairs, votes, verdicts = build_world()
    for i, v in enumerate(verdicts):
        if i % 4 == 0:
            v["position_consistent"] = False
    rep = run(verdicts, votes, pairs)
    t = by_name(rep)["position_consistency"]
    assert t["value"] == pytest.approx(0.75) and not t["passed"] and not rep["passed"]
    for i, v in enumerate(verdicts):
        v["position_consistent"] = i % 5 != 0  # exactly 80 %
    assert by_name(run(verdicts, votes, pairs))["position_consistency"]["passed"]


def test_validate_fails_on_padding_and_decoration_probes():
    pairs, votes, verdicts = build_world()
    pad_bad = {"padding": [{"expected_winner": "A", "outcome": "A"}] * 44 + [{"expected_winner": "A", "outcome": "B"}] * 6, "decoration": GOOD_PROBES["decoration"]}
    rep = run(verdicts, votes, pairs, probes=pad_bad)
    t = by_name(rep)
    assert t["padding_probe"]["value"] == pytest.approx(0.88) and not t["padding_probe"]["passed"]
    assert t["decoration_probe"]["passed"] and not rep["passed"]
    pad_edge = {"padding": [{"expected_winner": "A", "outcome": "A"}] * 45 + [{"expected_winner": "A", "outcome": "tie_good"}] * 5, "decoration": GOOD_PROBES["decoration"]}
    assert by_name(run(verdicts, votes, pairs, probes=pad_edge))["padding_probe"]["passed"]  # exactly 90 %

    dec_bad = {"padding": GOOD_PROBES["padding"], "decoration": [{"expected_winner": "A", "outcome": "A"}] * 41 + [{"expected_winner": "A", "outcome": "B"}] * 9}
    rep = run(verdicts, votes, pairs, probes=dec_bad)
    t = by_name(rep)
    assert t["decoration_probe"]["value"] == pytest.approx(0.82) and not t["decoration_probe"]["passed"]
    dec_edge = {"padding": GOOD_PROBES["padding"], "decoration": [{"expected_winner": "A", "outcome": "A"}] * 43 + [{"expected_winner": "A", "outcome": "tie_bad"}] * 7}
    assert by_name(run(verdicts, votes, pairs, probes=dec_edge))["decoration_probe"]["passed"]  # exactly 86 %


def test_validate_missing_probes_or_data_fails_instead_of_passing():
    pairs, votes, verdicts = build_world()
    rep = run(verdicts, votes, pairs, probes=None)
    t = by_name(rep)
    assert not t["padding_probe"]["passed"] and t["padding_probe"]["value"] is None
    assert rep["fallback"] == "structure only"
    rep = run([], [], [], probes=None)
    assert rep["passed"] is False and not any(x["passed"] for x in rep["tests"])


def test_validate_excludes_cloud_pairs():
    pairs, votes, verdicts = build_world()
    for k in range(20):
        pid = f"cloud-{k}"
        pairs.append(mkpair(pid, a_model="cloud-x", b_model=BASE, block="cloud"))
        votes.append({"pair_id": pid, "outcome": "A", "block": "cloud"})
        verdicts.append({"pair_id": pid, "judge_id": "j", "outcome": "B", "position_consistent": False, "orders": []})
    rep = run(verdicts, votes, pairs)
    t = by_name(rep)
    assert rep["passed"] is True
    assert "cloud-x" not in t["per_model_win_rate"]["models"]
    assert t["position_consistency"]["value"] == 1.0
    # the pair's own block marks it as cloud even when the vote lacks the field
    votes[-1] = {"pair_id": votes[-1]["pair_id"], "outcome": "A"}
    assert run(verdicts, votes, pairs)["passed"] is True


def test_validate_include_blocks_restricts_the_set():
    pairs, votes, verdicts = build_world()
    pairs.append(mkpair("standing-1", a_model="Gemma", b_model=BASE, block="standing"))
    votes.append({"pair_id": "standing-1", "outcome": "A", "block": "standing"})
    verdicts.append({"pair_id": "standing-1", "judge_id": "j", "outcome": "B", "position_consistent": True, "orders": []})
    assert not by_name(run(verdicts, votes, pairs))["agreement_non_tie"]["value"] == 1.0
    assert by_name(run(verdicts, votes, pairs, include_blocks=["validation"]))["agreement_non_tie"]["value"] == 1.0


def test_validate_with_real_judge_pair_records():
    """End to end: stub-judged records feed validate_judge."""
    pairs = [mkpair(f"e{i}", a_text="Short.", b_text="A rather long winded reply here.", a_model=f"m{i % 6}", b_model=BASE) for i in range(12)]
    votes = [{"pair_id": p["pair_id"], "outcome": "A", "block": "validation"} for p in pairs]
    cache = {}
    verdicts = [J.judge_pair(StubClient(shorter_wins), SPEC, p, cache=cache) for p in pairs]
    assert all(v["outcome"] == "A" for v in verdicts)
    rep = run(verdicts, votes, pairs)
    assert by_name(rep)["agreement_non_tie"]["value"] == 1.0
    assert rep["judge_id"] == J.judge_id(SPEC)


# --------------------------------------------------------------------------
# claim_faithfulness side column
# --------------------------------------------------------------------------


def test_claim_faithfulness_prompt_and_parse():
    msgs = J.claim_faithfulness_prompt("The bill is 40 dollars, due Tue 19 Nov.", "The bill is 50 dollars.")
    text = msgs[0]["content"]
    assert "supported" in text and "unsupported" in text and "contradicted" in text
    assert "Source:\nThe bill is 40 dollars, due Tue 19 Nov." in text and text.endswith("Answer:\nThe bill is 50 dollars.")
    good = json.dumps({"claims": [{"claim": "The bill is 50 dollars.", "label": "contradicted"}, {"claim": "It is a bill.", "label": "supported"}]})
    out = J.parse_claims("```json\n" + good + "\n```")
    assert out["parse_ok"] and out["counts"] == {"supported": 1, "unsupported": 0, "contradicted": 1}
    assert out["validation"] == "not validated"
    for bad in ("nope", '{"claims": [{"claim": "x", "label": "maybe"}]}', '{"claims": 3}', "[]"):
        res = J.parse_claims(bad)
        assert res["parse_ok"] is False and res["claims"] == [] and res["validation"] == "not validated"
