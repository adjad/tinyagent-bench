"""Tests for edat.quality.votes (vote-set builder, terminal session, summary)."""
from __future__ import annotations

import json
import random
from collections import Counter

import pytest

from edat.quality import stats as S
from edat.quality import votes as V

BASE = "gemma-4-E4B-it-oQ4e-mtp"
WISP = "Gemma-Wisp"
CANDS = ["cand-1", "cand-2", "cand-3", BASE]
CLOUD = ["cloud-1"]
QTYPES = ("records", "summary")
PROFILES = ("panel", "window")


def _answers(n_heldout=80, n_dev=12, leak_ids=(), seed=1):
    rng = random.Random(seed)
    out = []
    cases = [(f"h{i:03d}", "heldout") for i in range(n_heldout)] + \
            [(f"d{i:03d}", "development") for i in range(n_dev)]
    for i, (cid, split) in enumerate(cases):
        qtype, profile = QTYPES[i % 2], PROFILES[(i // 2) % 2]
        for model in [WISP, *CANDS, *CLOUD]:
            seeds = 3 if model == WISP else 1
            for s in range(seeds):
                n_words = rng.randint(20, 60)
                aid = f"{model}:{cid}:{s}"
                out.append({
                    "answer_id": aid, "model_id": model, "case_id": cid, "split": split,
                    "seed": s, "text": " ".join(f"w{j}" for j in range(n_words)),
                    "visible_tokens": n_words, "qtype": qtype, "profile": profile,
                    "request": f"Invented request {cid}", "leak": aid in leak_ids})
    return out


def _build(answers=None, seed=7, **kw):
    return V.build_pairs(answers or _answers(), candidate_ids=CANDS, baseline_id=WISP,
                         base_id=BASE, cloud_ids=CLOUD, seed=seed, **kw)


def _shown(pairs, block):
    return [p for p in pairs if p["block"] == block and not p.get("auto_outcome")]


# ---------------------------------------------------------------------------
# build_pairs
# ---------------------------------------------------------------------------

def test_build_pairs_counts_per_block():
    pairs = _build()
    counts = V.plan_report(pairs)
    assert counts["standing"] == 150
    assert counts["validation"] == 300
    assert counts["cloud"] == 20
    assert counts["repeat"] == 45
    assert counts["gold"] == 25
    assert counts["probe"] == 70
    assert counts["calibration"] == 6
    assert counts["auto_leak"] == 0
    ids = [p["pair_id"] for p in pairs]
    assert len(ids) == len(set(ids))
    # validation: 240 candidate-vs-baseline, 60 candidate-vs-candidate, held-out only
    val = _shown(pairs, "validation")
    vs_base = [p for p in val if WISP in (p["a"]["model_id"], p["b"]["model_id"])]
    assert len(vs_base) == 240 and len(val) - len(vs_base) == 60
    assert all(p["split"] == "heldout" for p in val + _shown(pairs, "standing") + _shown(pairs, "cloud"))
    # standing: base on side A, Gemma-Wisp on side B
    assert all(p["a"]["model_id"] == BASE and p["b"]["model_id"] == WISP
               for p in _shown(pairs, "standing"))
    assert all(p["a"]["model_id"] == "cloud-1" and p["b"]["model_id"] == WISP
               for p in _shown(pairs, "cloud"))
    # probe / calibration are not drawn from the held-out split when a development split exists
    assert all(p["split"] == "development" for p in _shown(pairs, "probe") + _shown(pairs, "calibration"))
    # same answer never paired twice inside the shown blocks
    keys = [frozenset((p["a"]["answer_id"], p["b"]["answer_id"]))
            for p in pairs if p["block"] in ("standing", "validation", "cloud")]
    assert len(keys) == len(set(keys))


def test_build_pairs_stratified_length_matched_and_baseline_seeds():
    pairs = _build()
    val = _shown(pairs, "validation")
    strata = Counter((p["qtype"], p["profile"]) for p in val)
    assert len(strata) == 4
    assert max(strata.values()) - min(strata.values()) <= 8
    matched = [p for p in val if p["length_matched"]]
    assert len(matched) >= 0.30 * len(val)
    for p in matched:
        r = p["a"]["visible_tokens"] / p["b"]["visible_tokens"]
        assert 0.8 <= r <= 1.25
    # all three baseline seeds appear; no baseline answer is used more than a few times
    base_answers = Counter(p[s]["answer_id"] for p in pairs
                           if p["block"] in ("standing", "validation", "cloud")
                           for s in "ab" if p[s]["model_id"] == WISP)
    assert {aid.rsplit(":", 1)[1] for aid in base_answers} == {"0", "1", "2"}
    assert max(base_answers.values()) <= 4


def test_build_pairs_deterministic():
    answers = _answers()
    assert _build(answers, seed=3) == _build(answers, seed=3)
    assert _build(answers, seed=3) != _build(answers, seed=4)


def test_leak_answers_are_never_shown_and_lose():
    answers = _answers()
    leaked = {a["answer_id"] for a in answers
              if a["model_id"] == "cand-1" and a["split"] == "heldout" and a["case_id"] < "h010"}
    assert leaked
    answers = _answers(leak_ids=leaked)
    pairs = _build(answers)
    for p in pairs:
        if p.get("auto_outcome"):
            continue
        assert p["a"]["answer_id"] not in leaked and p["b"]["answer_id"] not in leaked
    autos = [p for p in pairs if p.get("auto_outcome")]
    assert len(autos) == len(leaked)
    for p in autos:
        assert p["auto_reason"] == "leak"
        assert p["auto_outcome"] == "B"          # leaked candidate on A loses to the baseline
        assert p["a"]["answer_id"] in leaked
        assert "w1" not in p["a"]["text"]        # the leaked text is withheld
        assert p["b"]["model_id"] == WISP
    # auto losses count in the summary
    s = V.summarize(autos, [], baseline_id=WISP, base_id=BASE)
    assert s["pref_raw"]["cand-1"]["value"] == 0.0
    assert s["pref_raw"]["cand-1"]["n_auto_leak"] == len(leaked)


def test_repeats_have_swapped_sides():
    pairs = _build()
    by_id = {p["pair_id"]: p for p in pairs}
    reps = _shown(pairs, "repeat")
    assert len(reps) == 45
    for r in reps:
        orig = by_id[r["repeat_of"]]
        assert orig["block"] in ("standing", "validation", "cloud")
        assert r["a"]["answer_id"] == orig["b"]["answer_id"]
        assert r["b"]["answer_id"] == orig["a"]["answer_id"]
        assert r["case_id"] == orig["case_id"]
    assert len({r["repeat_of"] for r in reps}) == 45


def test_gold_pairs_built_correctly():
    pairs = _build()
    gold = _shown(pairs, "gold")
    assert len(gold) == 25
    assert len({g["case_id"] for g in gold}) == 25
    for g in gold:
        clean, padded = g["a"]["text"], g["b"]["text"]
        assert any(padded.startswith(o) for o in V.GOLD_OPENERS)
        assert padded.count(clean) == 2
        assert len(padded.split()) >= 2 * len(clean.split())
    one = V.make_gold_pair({"answer_id": "x1", "model_id": "m", "case_id": "c1", "split": "heldout",
                            "text": "Short clean answer.", "profile": "panel", "request": "r"})
    assert one["block"] == "gold" and one["a"]["text"] == "Short clean answer."
    assert one["b"]["model_id"] == "gold-padded"


def test_plan_is_data_and_can_change():
    plan = {**V.V1_PLAN, "standing": 10, "validation_vs_baseline": 8, "validation_cand_cand": 4,
            "cloud_per_model": 3, "repeats": 2, "gold": 2, "probe_sessions": 1}
    pairs = _build(plan=plan)
    c = V.plan_report(pairs)
    assert (c["standing"], c["validation"], c["cloud"], c["repeat"], c["gold"], c["probe"]) == \
        (10, 12, 3, 2, 2, 10)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def _mini_pairs():
    base = {"case_id": "c1", "split": "heldout", "request": "Invented request", "profile": "panel",
            "repeat_of": None, "qtype": "summary", "length_matched": True}
    pairs = []
    for i in range(3):
        pairs.append({**base, "pair_id": f"cal-{i}", "block": "calibration",
                      "a": {"answer_id": f"ca{i}", "model_id": "cand", "text": f"cal cand {i}"},
                      "b": {"answer_id": f"cb{i}", "model_id": WISP, "text": f"cal wisp {i}"}})
    for i in range(4):
        pairs.append({**base, "pair_id": f"probe-{i}", "block": "probe",
                      "a": {"answer_id": f"pa{i}", "model_id": "cand", "text": f"probe cand {i}"},
                      "b": {"answer_id": f"pb{i}", "model_id": WISP, "text": f"probe wisp {i}"}})
    for i in range(6):
        pairs.append({**base, "pair_id": f"val-{i}", "block": "validation",
                      "a": {"answer_id": f"va{i}", "model_id": "cand", "text": f"val cand {i}"},
                      "b": {"answer_id": f"vb{i}", "model_id": WISP, "text": f"val wisp {i}"}})
    return pairs


def _scripted(answers):
    it = iter(answers)

    def fn(prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    return fn


def _run(pairs, votes_path, answers, **kw):
    out = []
    res = V.start_session(pairs, votes_path, input_fn=_scripted(answers), output_fn=out.append,
                          session_id=kw.pop("session_id", "sess1"), seed=kw.pop("seed", 11),
                          now_fn=lambda: "2026-11-18T10:00:00Z", **kw)
    return res, out


def test_session_outcomes_map_back_to_canonical_sides(tmp_path):
    pairs = _mini_pairs()
    vp = tmp_path / "votes.jsonl"
    # 3 calibration, 4 probe guesses (left, right, left, right), then votes with flags
    probe = ["a", "b", "a", "b"]
    # calibration: a, b, g (no flags); four probe guesses; validation: a (no flag),
    # b + flag 3 on the left answer, x + flag 1 on both, g (no flag), then q
    inputs = ["a", "", "b", "", "g", ""] + probe + \
        ["a", "", "b", "3", "a", "x", "1", "", "g", "", "q"]
    res, out = _run(pairs, vp, inputs, max_pairs=90)
    votes = [json.loads(line) for line in vp.read_text().splitlines()]
    assert res["votes"] == len(votes) > 8
    non_probe = [v for v in votes if v["block"] != "probe"]
    for v in non_probe:
        left = v["shown_left"]
        assert left == V.shown_left_for(v["side_seed"])
        right = "B" if left == "A" else "A"
        if v["choice"] == "left":
            assert v["outcome"] == left
        elif v["choice"] == "right":
            assert v["outcome"] == right
        else:
            assert v["outcome"] == v["choice"] in ("tie_good", "tie_bad")
        assert v["ts"].endswith("Z") and v["session_id"] == "sess1"
    # both orientations were exercised
    assert {v["shown_left"] for v in votes} == {"A", "B"}
    # probe votes store the guess and correctness against the real baseline side
    probes = [v for v in votes if v["block"] == "probe"]
    assert len(probes) == 4
    for v in probes:
        assert v["choice"] is None and v["outcome"] is None
        base_side = "B"  # in the mini pairs the baseline is canonical side B
        base_pos = "left" if v["shown_left"] == base_side else "right"
        assert v["probe_correct"] == (v["probe_guess"] == base_pos)
    # flags are stored with the canonical side
    flagged = [v for v in votes if v["flag"]]
    assert flagged and all(v["flag"] in V.FLAGS for v in flagged)
    assert all(v["flag_side"] in ("A", "B", "both") for v in flagged)
    # the quit key saved and stopped
    assert res["stopped"] == "quit"


def test_session_hides_names_and_wraps_text(tmp_path):
    long_text = "word " * 60
    pair = {**_mini_pairs()[7], "a": {"answer_id": "x", "model_id": "secret-model", "text": long_text},
            "b": {"answer_id": "y", "model_id": WISP, "text": "- item one\n- item two"}}
    res, out = _run([pair], tmp_path / "v.jsonl", ["a", ""])
    shown = "\n".join(out)
    assert "secret-model" not in shown and WISP not in shown
    wrapped = V.render_text(long_text, "panel").splitlines()
    assert wrapped and max(len(x) for x in wrapped) <= 45
    assert max(len(x) for x in V.render_text(long_text, "window").splitlines()) <= 90
    assert max(len(x) for x in V.render_text(long_text, "window").splitlines()) > 45


def test_session_caps_and_resume_do_not_repeat_pairs(tmp_path):
    pairs = [p for p in _mini_pairs() if p["block"] == "validation"]
    vp = tmp_path / "votes.jsonl"
    res, _ = _run(pairs, vp, ["a", ""] * 10, max_pairs=4, session_id="s1", seed=1)
    assert res["shown"] == 4 and res["stopped"] == "pair_cap"
    res2, _ = _run(pairs, vp, ["b", ""] * 10, max_pairs=90, session_id="s2", seed=2)
    assert res2["votes"] == 2   # only the two never-voted pairs remain
    ids = [json.loads(line)["pair_id"] for line in vp.read_text().splitlines()]
    assert len(ids) == len(set(ids)) == 6
    # time cap via an injected clock
    ticks = iter([0.0, 0.0, 2000.0, 2000.0, 2000.0])
    res3, _ = _run(pairs, tmp_path / "t.jsonl", ["a", ""] * 10, clock=lambda: next(ticks), session_id="s3")
    assert res3["stopped"] == "time_cap" and res3["shown"] == 1


def test_repeat_only_after_original_voted_in_earlier_session(tmp_path):
    pairs = [p for p in _mini_pairs() if p["block"] == "validation"][:1]
    orig = pairs[0]
    rep = {**orig, "pair_id": "rep-1", "block": "repeat", "repeat_of": orig["pair_id"],
           "a": orig["b"], "b": orig["a"]}
    vp = tmp_path / "votes.jsonl"
    res, _ = _run(pairs + [rep], vp, ["a", ""] * 4, session_id="s1")
    assert res["votes"] == 1
    res, _ = _run(pairs + [rep], vp, ["a", ""] * 4, session_id="s2")
    assert res["votes"] == 1
    assert json.loads(vp.read_text().splitlines()[-1])["pair_id"] == "rep-1"


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

def _pair(pid, block, a_model, b_model, a_text="alpha beta", b_text="alpha beta", **kw):
    return {"pair_id": pid, "case_id": "c-" + pid, "split": "heldout", "block": block,
            "request": "r", "profile": "panel", "repeat_of": kw.pop("repeat_of", None),
            "a": {"answer_id": f"{pid}-a", "model_id": a_model, "text": a_text},
            "b": {"answer_id": f"{pid}-b", "model_id": b_model, "text": b_text},
            "length_matched": True, **kw}


def _vote(pid, sid, outcome, block, flag=None, flag_side=None, **kw):
    return {"vote_id": f"{pid}-{sid}", "pair_id": pid, "session_id": sid, "block": block,
            "outcome": outcome, "flag": flag, "flag_side": flag_side, "probe_guess": None,
            "probe_correct": None, **kw}


def test_summarize_hand_checked_example():
    pairs = [_pair(f"v{i}", "validation", "c", WISP) for i in range(1, 6)]
    auto = _pair("auto1", "validation", "c", WISP, auto_outcome="B", auto_reason="leak")
    pairs += [auto]
    pairs += [_pair(f"st{i}", "standing", BASE, WISP) for i in range(1, 5)]
    pairs += [_pair(f"g{i}", "gold", "m", "gold-padded") for i in (1, 2)]
    pairs += [_pair("r1", "repeat", WISP, "c", repeat_of="v1"),    # swapped copy of v1
              _pair("r2", "repeat", WISP, "c", repeat_of="v2")]
    # make repeat answers the swapped answers of their originals
    for r, o in ((pairs[-2], pairs[0]), (pairs[-1], pairs[1])):
        r["a"], r["b"] = dict(o["b"]), dict(o["a"])
    votes = [
        _vote("v1", "s1", "A", "validation"), _vote("v2", "s1", "A", "validation"),
        _vote("v3", "s1", "B", "validation"), _vote("v4", "s1", "tie_good", "validation"),
        _vote("v5", "s1", "A", "validation", flag="too_long", flag_side="B"),
        _vote("st1", "s1", "B", "standing", flag="odd_tone", flag_side="A"),
        _vote("st2", "s1", "B", "standing"), _vote("st3", "s1", "A", "standing"),
        _vote("st4", "s1", "tie_bad", "standing"),
        _vote("g1", "s1", "A", "gold"), _vote("g2", "s1", "B", "gold"),
        _vote("r1", "s1", "B", "repeat"),   # swapped: B = the originally-preferred answer -> consistent
        _vote("r2", "s1", "B", "repeat"),   # orig A (c won); swapped B = c's answer? see below
    ]
    s = V.summarize(pairs, votes, baseline_id=WISP, base_id=BASE)
    # candidate "c": wins v1 v2 v5, loss v3, tie v4, auto leak loss -> (3 + 0.5) / 6
    assert s["pref_raw"]["c"]["value"] == pytest.approx(100 * 3.5 / 6)
    assert s["pref_raw"]["c"]["n"] == 6 and s["pref_raw"]["c"]["n_ties"] == 1
    assert s["pref_raw"]["c"]["n_auto_leak"] == 1
    assert s["pref_votes"]["c"] == 5
    assert s["pref_raw"]["c"]["lo"] < s["pref_raw"]["c"]["value"] < s["pref_raw"]["c"]["hi"]
    assert 0.0 < s["pref_style_ctrl"]["c"] < 100.0 and WISP not in s["pref_style_ctrl"]
    # standing: tuned (B) wins st1 st2, loses st3, ties st4 -> 62.5, tuning_pref 12.5
    st = s["standing"]
    assert st["win_rate"] == pytest.approx(62.5) and st["tuning_pref"] == pytest.approx(12.5)
    assert st["n"] == 4 and st["n_non_tie"] == 3
    assert st["style_share"] is None                 # interval includes 0 with 4 votes
    assert st["tuning_pref_style_ctrl"] is not None
    assert st["flags_by_side"] == {BASE: {"odd_tone": 1}}
    # gold: one correct of two
    assert s["gold_accuracy"] == {"rate": 50.0, "n": 2, "correct": 1}
    # repeats: r1 -> orig v1 A (answer v1-a); repeat B = r1.b = v1-a: consistent.
    # r2 -> orig v2 A (v2-a); repeat B = v2-a: consistent too
    assert s["rater_self_consistency"]["n"] == 2 and s["rater_self_consistency"]["agree"] == 2
    # both bad: 1 tie_bad of 5 + 4 + 2 voted (non-gold, non-auto) = 11
    assert s["both_bad_rate"] == pytest.approx(100 / 11)
    assert s["flags"] == {"too_long": 1, "odd_tone": 1}
    assert s["excluded_sessions"] == []
    assert s["block_counts"]["validation"] == 6


def test_style_share_reported_when_interval_excludes_zero():
    # tuned wins 80 of 100 with a longer, bulleted answer: raw lead large, style explains it
    pairs, votes = [], []
    for i in range(100):
        pid = f"st{i}"
        tuned_wins = i % 5 != 0
        fancy = "- one\n- two\n- three\n**bold** " + "word " * 40
        plain = "word " * 10
        pairs.append(_pair(pid, "standing", BASE, WISP, a_text=plain, b_text=fancy))
        votes.append(_vote(pid, "s1", "B" if tuned_wins else "A", "standing"))
    st = V.summarize(pairs, votes, baseline_id=WISP, base_id=BASE)["standing"]
    assert st["excludes_zero"] and st["tuning_pref"] == pytest.approx(30.0)
    assert st["style_share"] is not None


def test_recognition_probe_excludes_session_baseline_pairs():
    pairs = [_pair(f"p{i}", "probe", "c", WISP) for i in range(20)]
    pairs += [_pair(f"v{i}", "validation", "c", WISP) for i in range(4)]
    pairs += [_pair("w1", "validation", "c", "d")]    # candidate vs candidate: not baseline
    votes = []
    # session s1: 8/10 correct -> excluded; session s2: 6/10 -> fine; session s3: 3 guesses only
    for i in range(10):
        votes.append(_vote(f"p{i}", "s1", None, "probe", probe_guess="left", probe_correct=i < 8))
    for i in range(10):
        votes.append(_vote(f"p{10 + i}", "s2", None, "probe", probe_guess="left", probe_correct=i < 6))
    votes += [_vote("v0", "s1", "A", "validation"), _vote("v1", "s1", "A", "validation"),
              _vote("w1", "s1", "A", "validation"),
              _vote("v2", "s2", "B", "validation"), _vote("v3", "s2", "A", "validation")]
    s = V.summarize(pairs, votes, baseline_id=WISP, base_id=BASE)
    rec = s["baseline_recognition"]
    assert s["excluded_sessions"] == ["s1"]
    assert rec["per_session"]["s1"]["rate"] == 80.0 and rec["per_session"]["s1"]["excluded"]
    assert rec["per_session"]["s2"]["rate"] == 60.0 and not rec["per_session"]["s2"]["excluded"]
    assert rec["overall"]["n"] == 20 and rec["overall"]["rate"] == pytest.approx(70.0)
    # s1's two baseline votes dropped; only s2's two remain (1 win 1 loss), the c-vs-d vote is kept elsewhere
    assert s["pref_raw"]["c"]["n"] == 2 and s["pref_raw"]["c"]["value"] == pytest.approx(50.0)
    assert s["dropped_votes_from_excluded_sessions"] == 2
    # a session with fewer than 5 guesses is never judged
    few = [_vote(f"p{i}", "s9", None, "probe", probe_guess="left", probe_correct=True) for i in range(3)]
    assert V.summarize(pairs, few, baseline_id=WISP, base_id=BASE)["excluded_sessions"] == []


def test_style_features_and_paths(tmp_path, monkeypatch):
    f = V.style_features("# Title\n- a\n- b\n1. c\nSome **bold** and __bold2__ \U0001F600")
    assert f["headers"] == 1 and f["list_items"] == 3 and f["bold"] == 2 and f["emoji"] == 1
    assert f["tokens"] == len("# Title\n- a\n- b\n1. c\nSome **bold** and __bold2__ \U0001F600".split())
    assert V.style_features("x y z", visible_tokens=99)["tokens"] == 99
    monkeypatch.setenv("EDAT_PRIVATE_DIR", str(tmp_path))
    assert V.votes_dir() == tmp_path / "votes"
    monkeypatch.delenv("EDAT_PRIVATE_DIR")
    monkeypatch.chdir(tmp_path)
    assert V.votes_dir() == tmp_path / "votes"


def test_cli_build_vote_summary_end_to_end(tmp_path, monkeypatch, capsys):
    answers = tmp_path / "answers.jsonl"
    V.write_jsonl(answers, _answers(n_heldout=30, n_dev=4))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"standing": 6, "validation_vs_baseline": 8, "validation_cand_cand": 2,
                                "cloud_per_model": 2, "repeats": 2, "gold": 2, "probe_sessions": 1,
                                "calibration": 2}))
    pairs_p, votes_p = tmp_path / "out" / "pairs.jsonl", tmp_path / "out" / "votes.jsonl"
    assert V.main(["build", "--answers", str(answers), "--out", str(pairs_p), "--candidates",
                   ",".join(CANDS), "--cloud", "cloud-1", "--plan", str(plan), "--seed", "2"]) == 0
    pairs = V.read_jsonl(pairs_p)
    assert V.plan_report(pairs)["standing"] == 6
    inputs = iter(["a", ""] * 40)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert V.main(["vote", "--pairs", str(pairs_p), "--votes", str(votes_p), "--seed", "3",
                   "--max-pairs", "12"]) == 0
    assert len(V.read_jsonl(votes_p)) > 0
    capsys.readouterr()
    assert V.main(["summary", "--pairs", str(pairs_p), "--votes", str(votes_p)]) == 0
    assert "pref_raw" in json.loads(capsys.readouterr().out)
    with pytest.raises(SystemExit):
        V.main(["build", "--help"])
