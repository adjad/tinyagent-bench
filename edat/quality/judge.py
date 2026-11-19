"""tinyagent-bench judge harness (spec 03, sections 4.4, 5, 7.6, 10).

A judge model compares two answers pairwise. It is trusted only after
`validate_judge` passes against one human rater's blind votes. Standard
library only. The harness never runs a real model by itself: the caller passes
a client with the adapter method

    client.chat(messages, *, tools=None, sampling=None, seed=None,
                max_tokens, thinking)   ->  result with `.text`

Conventions
- Pair sides are the pair's own `a` and `b`. Verdict outcomes and author votes
  are both stated in pair sides ("A" is `pair["a"]`), not in display order.
- Rates are fractions in 0..1.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import votes as _V

OUTCOMES = ("A", "B", "tie_good", "tie_bad")
TIES = ("tie_good", "tie_bad")

# --------------------------------------------------------------------------
# Rubrics (spec 03 section 4.4, verbatim)
# --------------------------------------------------------------------------

PAIRWISE_RUBRIC = r'''You are comparing two answers from an on-device assistant. The answers appear in
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

POINTWISE_RUBRIC = r'''Score the answer from 1 to 5 on each line. 5 = a careful human writer would not
change it; 3 = usable but a reader would notice a flaw; 1 = hard to use.
answer_first, shape_fit, concision, clarity, tone
Do not reward length. An answer that is twice as long as needed scores at most 3
on concision even if every sentence is true.
Reply with JSON: {"answer_first": n, "shape_fit": n, "concision": n,
"clarity": n, "tone": n}'''

POINTWISE_KEYS = ("answer_first", "shape_fit", "concision", "clarity", "tone")

PAIRWISE_MAX_TOKENS = 256
POINTWISE_MAX_TOKENS = 128
CLAIMS_MAX_TOKENS = 1024


def rubric_hash(rubric: str = PAIRWISE_RUBRIC) -> str:
    """sha256 (hex) of the rubric text. Defaults to the pairwise rubric."""
    return hashlib.sha256(rubric.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Judge identity and the family rule
# --------------------------------------------------------------------------


class FamilyRuleError(Exception):
    """A judge was used against the family rule (spec section 10)."""


@dataclass(frozen=True)
class JudgeSpec:
    model_id: str
    family: str
    weights_sha256: str
    quant: str
    sampling: Dict[str, Any] = field(default_factory=lambda: {"temperature": 0})
    seed: Optional[int] = 0
    rubric_hash: str = field(default_factory=rubric_hash)


def judge_id(spec: JudgeSpec) -> str:
    """Stable id: any change to model, weights, quant, sampling, seed or rubric gives a new id."""
    payload = json.dumps(asdict(spec), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "judge-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _is_gemma(spec: JudgeSpec, model_family: Optional[dict]) -> bool:
    fam = (model_family or {}).get(spec.model_id, spec.family)
    return any("gemma" in str(x).lower() for x in (spec.family, spec.model_id, fam))


def check_family(spec: JudgeSpec, pair_model_ids: Iterable[str], model_family: Optional[dict] = None) -> dict:
    """Apply the family rule.

    Raises FamilyRuleError for a Gemma-family judge (never allowed).
    Otherwise returns {"ok": bool, "reason": str, "route": None | "second_judge"}.
    A pair that includes the judge's own model or family is not ok and is
    routed to a second judge.
    """
    model_family = model_family or {}
    if _is_gemma(spec, model_family):
        raise FamilyRuleError(f"judge {spec.model_id!r} is Gemma-family; never allowed as a judge")
    own = model_family.get(spec.model_id, spec.family)
    for mid in pair_model_ids:
        fam = model_family.get(mid, mid)
        if mid == spec.model_id or fam == own or fam == spec.family:
            return {
                "ok": False,
                "reason": f"pair includes {mid!r}, the judge's own model or family ({own!r})",
                "route": "second_judge",
            }
    return {"ok": True, "reason": "", "route": None}


# --------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------


def _profile_name(profile: Any) -> str:
    if isinstance(profile, dict):
        return str(profile.get("name") or profile.get("profile") or json.dumps(profile, sort_keys=True))
    return str(profile)


def build_pairwise_messages(request: str, profile: Any, answer_a: str, answer_b: str, active_prefs: Sequence[str] = ()) -> List[dict]:
    """One user message: the rubric verbatim, then request, display profile, Answer A, Answer B."""
    parts = [PAIRWISE_RUBRIC, "", "Request:", request, "", f"Display: {_profile_name(profile)}"]
    prefs = [str(p) for p in (active_prefs or ())]
    if prefs:
        parts += [
            "",
            "The user has these standing preferences active:",
            *[f"- {p}" for p in prefs],
            "Answers that follow them are not marked down for brevity.",
        ]
    parts += ["", "Answer A:", answer_a, "", "Answer B:", answer_b]
    return [{"role": "user", "content": "\n".join(parts)}]


def build_pointwise_messages(request: str, profile: Any, answer: str) -> List[dict]:
    parts = [POINTWISE_RUBRIC, "", "Request:", request, "", f"Display: {_profile_name(profile)}", "", "Answer:", answer]
    return [{"role": "user", "content": "\n".join(parts)}]


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

_FENCE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*\n?(.*?)\n?\s*```\s*$", re.S)


def _unfence(text: str) -> str:
    text = (text or "").strip()
    m = _FENCE.match(text)
    return m.group(1).strip() if m else text


def _bad(raw_reason: str = "") -> dict:
    return {"winner": "tie_good", "deciding_point": None, "reason": raw_reason, "parse_ok": False}


def parse_verdict(text: str) -> dict:
    """Parse the judge's reply: exactly one line of JSON (a code fence is tolerated).

    Anything unparseable returns parse_ok False with winner "tie_good" (it
    counts as a tie; judge_pair combines ties conservatively).
    """
    body = _unfence(text)
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if len(lines) != 1:
        return _bad()
    try:
        obj = json.loads(lines[0])
    except (ValueError, TypeError):
        return _bad()
    if not isinstance(obj, dict):
        return _bad()
    winner = obj.get("winner")
    point = obj.get("deciding_point")
    reason = obj.get("reason", "")
    if winner not in OUTCOMES:
        return _bad()
    if isinstance(point, bool) or not isinstance(point, int) or not 1 <= point <= 5:
        return _bad()
    if not isinstance(reason, str):
        return _bad()
    return {"winner": winner, "deciding_point": point, "reason": reason, "parse_ok": True}


def parse_pointwise(text: str) -> dict:
    """Parse five 1-5 scores. Returns {"scores": {...}|None, "parse_ok": bool}. Diagnostic only."""
    body = _unfence(text)
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return {"scores": None, "parse_ok": False}
    if not isinstance(obj, dict):
        return {"scores": None, "parse_ok": False}
    scores = {}
    for k in POINTWISE_KEYS:
        v = obj.get(k)
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 5:
            return {"scores": None, "parse_ok": False}
        scores[k] = v
    return {"scores": scores, "parse_ok": True}


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


class JsonlCache:
    """Dict-like cache backed by an append-only JSONL file ({"key", "value"} per line)."""

    def __init__(self, path: str):
        self.path = str(path)
        self._data: Dict[str, Any] = {}
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue  # a torn last line is ignored
                    self._data[rec["key"]] = rec["value"]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __contains__(self, key):
        return key in self._data

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": key, "value": value}, ensure_ascii=False, sort_keys=True) + "\n")

    def __len__(self):
        return len(self._data)


def pair_content_hash(pair: dict, active_prefs: Sequence[str] = ()) -> str:
    payload = {
        "request": pair.get("request"),
        "profile": _profile_name(pair.get("profile")),
        "a": pair["a"].get("text"),
        "b": pair["b"].get("text"),
        "prefs": [str(p) for p in active_prefs],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def cache_key(jid: str, content_hash: str, order: str) -> str:
    return f"{jid}|{content_hash}|{order}"


# --------------------------------------------------------------------------
# Running the judge
# --------------------------------------------------------------------------


def _sampling_for(spec: JudgeSpec) -> Any:
    """The adapter's Sampling object when edat.models exists, else the plain dict."""
    try:  # optional: this module does not depend on the adapter
        from edat.models import Sampling  # type: ignore

        src = "greedy_diagnostic" if spec.sampling.get("temperature") == 0 else "unspecified"
        return Sampling(**spec.sampling, source=src)
    except Exception:
        return dict(spec.sampling)


def _call(client, spec: JudgeSpec, messages: List[dict], max_tokens: int) -> str:
    res = client.chat(messages, tools=None, sampling=_sampling_for(spec), seed=spec.seed, max_tokens=max_tokens, thinking=False)
    return res.text


def _flip(w: str) -> str:
    return {"A": "B", "B": "A"}.get(w, w)


def _combine(orders: List[dict]) -> tuple:
    """Return (outcome, position_consistent) from the two mapped order records."""
    w1, w2 = orders[0]["winner"], orders[1]["winner"]
    if w1 in TIES and w2 in TIES:
        return ("tie_bad" if "tie_bad" in (w1, w2) else "tie_good"), True
    if w1 == w2:
        return w1, True
    # sides disagree, or one order says a side and the other says tie
    label = "tie_bad" if "tie_bad" in (w1, w2) else "tie_good"
    return label, False


def judge_pair(client, spec: JudgeSpec, pair: dict, *, cache=None, model_family: Optional[dict] = None, active_prefs: Optional[Sequence[str]] = None) -> dict:
    """Judge one pair in both side orders; a verdict counts only if both orders agree.

    Order "AB" shows pair a as Answer A; "BA" shows pair b as Answer A. Each
    order's winner is mapped back to pair sides. Disagreement is a tie
    (position_consistent False); the tie label is tie_bad if any order said
    tie_bad, else tie_good. A reply that does not parse counts as a tie.
    Answers marked `leak` are never sent: a leaking answer loses automatically
    (both leaking: tie_bad, no call).
    """
    cache = {} if cache is None else cache
    jid = judge_id(spec)
    check = check_family(spec, [pair["a"].get("model_id"), pair["b"].get("model_id")], model_family) if model_family is not None else None
    if check is None:
        if _is_gemma(spec, None):
            raise FamilyRuleError(f"judge {spec.model_id!r} is Gemma-family; never allowed as a judge")
    elif not check["ok"]:
        raise FamilyRuleError(check["reason"])

    leak_a, leak_b = bool(pair["a"].get("leak")), bool(pair["b"].get("leak"))
    base = {"pair_id": pair.get("pair_id"), "judge_id": jid}
    if leak_a or leak_b:
        outcome = "tie_bad" if (leak_a and leak_b) else ("B" if leak_a else "A")
        return {**base, "outcome": outcome, "position_consistent": True, "orders": [], "leak_decided": True}

    prefs = list(active_prefs if active_prefs is not None else pair.get("active_prefs") or ())
    chash = pair_content_hash(pair, prefs)
    orders = []
    for order in ("AB", "BA"):
        first, second = (pair["a"], pair["b"]) if order == "AB" else (pair["b"], pair["a"])
        key = cache_key(jid, chash, order)
        hit = cache.get(key) if hasattr(cache, "get") else (cache[key] if key in cache else None)
        if hit is None:
            msgs = build_pairwise_messages(pair["request"], pair.get("profile"), first["text"], second["text"], prefs)
            raw = _call(client, spec, msgs, PAIRWISE_MAX_TOKENS)
            cache[key] = {"raw": raw}
        else:
            raw = hit["raw"]
        v = parse_verdict(raw)
        mapped = _flip(v["winner"]) if order == "BA" else v["winner"]
        orders.append(
            {
                "order": order,
                "raw": raw,
                "winner": mapped,
                "shown_winner": v["winner"],
                "deciding_point": v["deciding_point"],
                "reason": v["reason"],
                "parse_ok": v["parse_ok"],
            }
        )
    outcome, consistent = _combine(orders)
    return {**base, "outcome": outcome, "position_consistent": consistent, "orders": orders}


def judge_pointwise(client, spec: JudgeSpec, request: str, profile: Any, answer: str) -> dict:
    """Five 1-5 scores for one answer. Diagnostic only, never used for ranking."""
    raw = _call(client, spec, build_pointwise_messages(request, profile, answer), POINTWISE_MAX_TOKENS)
    out = parse_pointwise(raw)
    out["raw"] = raw
    out["judge_id"] = judge_id(spec)
    return out


# --------------------------------------------------------------------------
# Probes (spec section 5 and 4.4)
# --------------------------------------------------------------------------

FILLER_OPENERS = (
    "Great question! I'd be happy to help you with that.",
    "Sure thing! Let me take a look at this for you.",
    "Thanks for asking! Here is what I can tell you.",
    "Absolutely! I'm glad you brought this up.",
)
FILLER_CLOSERS = (
    "I hope this helps! Let me know if you would like anything else.",
    "Feel free to ask if you have any more questions!",
    "Please do not hesitate to reach out if you need further help.",
    "Happy to go into more detail whenever you like!",
)
DECORATION_EMOJI = ("✨", "\U0001F44D", "\U0001F4A1", "\U0001F680")


def _first_sentences(text: str, n: int = 2) -> str:
    sents = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    return " ".join(sents[:n]).strip()


def make_padding_probe(answer: str, request: str, variant: int = 0) -> str:
    """The answer plus a filler opener, a filler closer and a restated paragraph."""
    opener = FILLER_OPENERS[variant % len(FILLER_OPENERS)]
    closer = FILLER_CLOSERS[variant % len(FILLER_CLOSERS)]
    gist = _first_sentences(re.sub(r"[*_`#>-]", "", answer))
    restated = f"To restate what you asked: {' '.join(request.split())} In short, {gist}"
    return f"{opener}\n\n{answer.strip()}\n\n{restated}\n\n{closer}"


def make_decoration_probe(answer: str, variant: int = 0) -> str:
    """The answer with needless bold spans and emoji added."""
    emoji = DECORATION_EMOJI
    out = []
    k = 0
    for line in answer.strip().splitlines():
        m = re.match(r"^(\s*(?:[-*+]|\d+[.)])\s+)?(.*)$", line)
        prefix, body = (m.group(1) or ""), m.group(2)
        words = body.split(" ")
        if body.strip() and "**" not in body and len(words) >= 3:
            body = "**" + " ".join(words[:2]) + "** " + " ".join(words[2:])
        if body.strip():
            body = f"{body} {emoji[(variant + k) % len(emoji)]}"
            k += 1
        out.append(prefix + body)
    text = "\n".join(out)
    if text == answer:  # empty answer: still decorate
        text = f"**{answer}** {emoji[variant % len(emoji)]}"
    return text


def probe_set(answers: Sequence[dict], n: int = 50, seed: int = 0) -> dict:
    """Build n padding pairs and n decoration pairs from real answers.

    `answers`: dicts with `text`, `request`, `profile`, and optionally
    `case_id`, `answer_id`, `model_id`, `leak`. Answers with a leak are skipped.
    Returns {"padding": [pair, ...], "decoration": [pair, ...]}. Each pair has
    the normal pair fields plus `probe` and `expected_winner` ("A" or "B": the
    original for padding, the plainer one for decoration). Sides are shuffled
    with the seeded generator.
    """
    pool = [a for a in answers if not a.get("leak")]
    if not pool:
        raise ValueError("no usable answers for probes")
    rng = random.Random(seed)
    out: Dict[str, list] = {"padding": [], "decoration": []}
    for kind in ("padding", "decoration"):
        order = list(range(len(pool)))
        rng.shuffle(order)
        for i in range(n):
            src = pool[order[i % len(pool)]]
            variant = i // len(pool) + (i % 4)
            text = src["text"]
            if kind == "padding":
                altered = make_padding_probe(text, src["request"], variant)
            else:
                altered = make_decoration_probe(text, variant)
            base_id = src.get("answer_id", f"ans{order[i % len(pool)]}")
            model_id = src.get("model_id")
            original = {"answer_id": base_id, "model_id": model_id, "text": text}
            probe = {"answer_id": f"{base_id}+{kind}", "model_id": model_id, "text": altered}
            original_first = rng.random() < 0.5
            a, b = (original, probe) if original_first else (probe, original)
            out[kind].append(
                {
                    "pair_id": f"probe-{kind}-{i:03d}",
                    "case_id": src.get("case_id"),
                    "split": "probe",
                    "block": "probe",
                    "request": src["request"],
                    "profile": src.get("profile"),
                    "a": a,
                    "b": b,
                    "probe": kind,
                    "expected_winner": "A" if original_first else "B",
                }
            )
    return out


def probe_results(probe_pairs: Sequence[dict], verdicts: Sequence[dict]) -> List[dict]:
    """Join probe pairs with their verdicts: [{"pair_id", "expected_winner", "outcome"}]."""
    by_id = {v["pair_id"]: v for v in verdicts}
    return [
        {"pair_id": p["pair_id"], "expected_winner": p["expected_winner"], "outcome": by_id[p["pair_id"]]["outcome"]}
        for p in probe_pairs
        if p["pair_id"] in by_id
    ]


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


def wilson_interval(successes: float, n: int, z: float = 1.959964) -> tuple:
    """95 % Wilson score interval for successes/n (successes may be fractional, as with half-credit ties)."""
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def cohens_kappa(pairs_xy: Sequence[tuple], labels: Sequence[str] = OUTCOMES) -> Optional[float]:
    """Cohen's kappa over (rater1, rater2) label pairs. None when empty."""
    n = len(pairs_xy)
    if n == 0:
        return None
    po = sum(1 for x, y in pairs_xy if x == y) / n
    pe = 0.0
    for lab in labels:
        p1 = sum(1 for x, _ in pairs_xy if x == lab) / n
        p2 = sum(1 for _, y in pairs_xy if y == lab) / n
        pe += p1 * p2
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1 - pe)


# --------------------------------------------------------------------------
# Headline input
# --------------------------------------------------------------------------


def _score_for(outcome: str, side: str) -> float:
    if outcome in TIES:
        return 0.5
    return 1.0 if outcome == side else 0.0


def pref_judge(verdicts: Sequence[dict], pairs: Sequence[dict], baseline_id: str = "Gemma-Wisp") -> dict:
    """Per model: win rate against the baseline, ties half.

    Returns {model_id: {"win_rate", "n", "non_tie"}} (win_rate is a fraction
    0..1; the headline score is 100 * win_rate with the baseline at 50).
    """
    by_id = {v["pair_id"]: v for v in verdicts}
    acc: Dict[str, dict] = {}
    for p in pairs:
        v = by_id.get(p["pair_id"])
        if v is None:
            continue
        ma, mb = p["a"].get("model_id"), p["b"].get("model_id")
        if ma == baseline_id and mb != baseline_id:
            model, side = mb, "B"
        elif mb == baseline_id and ma != baseline_id:
            model, side = ma, "A"
        else:
            continue
        d = acc.setdefault(model, {"score": 0.0, "n": 0, "non_tie": 0})
        d["score"] += _score_for(v["outcome"], side)
        d["n"] += 1
        d["non_tie"] += 0 if v["outcome"] in TIES else 1
    return {m: {"win_rate": d["score"] / d["n"], "n": d["n"], "non_tie": d["non_tie"]} for m, d in acc.items()}


# --------------------------------------------------------------------------
# Validation against the author's votes (spec section 4.4 table)
# --------------------------------------------------------------------------

THRESHOLDS = {
    "agreement": 0.80,
    "self_consistency_margin": 0.05,
    "kappa": 0.6,
    "cvc_agreement": 0.65,
    "position_consistency": 0.80,
    "padding": 0.90,
    "decoration": 0.85,
}


def _test(name: str, value, threshold, passed: bool, **detail) -> dict:
    return {"name": name, "value": value, "threshold": threshold, "passed": bool(passed), **detail}


def _frac(x) -> Optional[float]:
    if x is None:
        return None
    return x / 100.0 if x > 1 else float(x)


def validate_judge(
    judge_verdicts: Sequence[dict],
    author_votes: Sequence[dict],
    pairs: Sequence[dict],
    *,
    self_consistency: Optional[float],
    probes: Optional[dict],
    baseline_id: str = "Gemma-Wisp",
    include_blocks: Optional[Iterable[str]] = None,
) -> dict:
    """Run every test of the spec's validation table.

    `probes`: {"padding": [...], "decoration": [...]}; each item has
    `expected_winner` ("A"/"B", the original or plainer answer) and `outcome`
    (see `probe_results`). `self_consistency` is the author's repeat agreement
    on non-tie pairs, as a fraction (a value above 1 is read as a percent).
    Cloud pairs (block "cloud") are excluded; `include_blocks`, when given,
    restricts validation to those blocks. Missing data fails its test.
    """
    pair_by_id = {p["pair_id"]: p for p in pairs}
    jv = {v["pair_id"]: v for v in judge_verdicts}
    allowed = set(include_blocks) if include_blocks is not None else None

    rows = []  # (pair, author_outcome, judge_verdict)
    for vote in author_votes:
        p = pair_by_id.get(vote["pair_id"])
        block = vote.get("block", p.get("block") if p else None)
        if p is None or block == "cloud" or p.get("block") == "cloud":
            continue
        if allowed is not None and block not in allowed:
            continue
        v = jv.get(vote["pair_id"])
        if v is None or vote.get("outcome") not in OUTCOMES or v.get("outcome") not in OUTCOMES:
            continue
        rows.append((p, vote["outcome"], v))

    tests: List[dict] = []
    n_all = len(rows)

    # 1. agreement on non-tie pairs, and against self-consistency
    nt = [(p, a, v) for p, a, v in rows if a in ("A", "B")]
    agree = sum(1 for _, a, v in nt if v["outcome"] == a) / len(nt) if nt else None
    tests.append(_test("agreement_non_tie", agree, THRESHOLDS["agreement"], agree is not None and agree >= THRESHOLDS["agreement"], n=len(nt)))
    sc = _frac(self_consistency)
    sc_thr = None if sc is None else sc - THRESHOLDS["self_consistency_margin"]
    tests.append(
        _test(
            "agreement_vs_self_consistency",
            agree,
            sc_thr,
            agree is not None and sc_thr is not None and agree >= sc_thr - 1e-12,
            self_consistency=sc,
        )
    )

    # 2. Cohen's kappa, four outcomes
    kappa = cohens_kappa([(a, v["outcome"]) for _, a, v in rows])
    tests.append(_test("cohens_kappa", kappa, THRESHOLDS["kappa"], kappa is not None and kappa >= THRESHOLDS["kappa"], n=n_all))

    # 3. candidate-against-candidate agreement
    cvc = [(p, a, v) for p, a, v in nt if baseline_id not in (p["a"].get("model_id"), p["b"].get("model_id"))]
    cvc_agree = sum(1 for _, a, v in cvc if v["outcome"] == a) / len(cvc) if cvc else None
    tests.append(
        _test(
            "candidate_vs_candidate_agreement",
            cvc_agree,
            THRESHOLDS["cvc_agreement"],
            cvc_agree is not None and cvc_agree >= THRESHOLDS["cvc_agreement"],
            n=len(cvc),
            reported=True,
        )
    )

    # 4. per-model check against the baseline
    per_model: Dict[str, dict] = {}
    for p, a, v in rows:
        ma, mb = p["a"].get("model_id"), p["b"].get("model_id")
        if ma == baseline_id and mb != baseline_id:
            model, side = mb, "B"
        elif mb == baseline_id and ma != baseline_id:
            model, side = ma, "A"
        else:
            continue
        d = per_model.setdefault(model, {"author": 0.0, "judge": 0.0, "n": 0})
        d["author"] += _score_for(a, side)
        d["judge"] += _score_for(v["outcome"], side)
        d["n"] += 1
    models = {}
    for m, d in sorted(per_model.items()):
        lo, hi = wilson_interval(d["author"], d["n"])
        jr = d["judge"] / d["n"]
        models[m] = {"author_win_rate": d["author"] / d["n"], "judge_win_rate": jr, "interval": [lo, hi], "n": d["n"], "inside": lo <= jr <= hi}
    pm_ok = bool(models) and all(x["inside"] for x in models.values())
    tests.append(
        _test(
            "per_model_win_rate",
            sum(1 for x in models.values() if x["inside"]),
            len(models),
            pm_ok,
            models=models,
            failing=[m for m, x in models.items() if not x["inside"]],
        )
    )

    # 5. position consistency
    pc_flags = [bool(jv[p["pair_id"]].get("position_consistent")) for p, _, _ in rows]
    pc = sum(pc_flags) / len(pc_flags) if pc_flags else None
    tests.append(_test("position_consistency", pc, THRESHOLDS["position_consistency"], pc is not None and pc >= THRESHOLDS["position_consistency"], n=len(pc_flags)))

    # 6 and 7. probes
    probes = probes or {}
    for kind, thr_key in (("padding", "padding"), ("decoration", "decoration")):
        items = list(probes.get(kind) or [])
        rate = sum(1 for it in items if it.get("outcome") == it.get("expected_winner")) / len(items) if items else None
        tests.append(_test(f"{kind}_probe", rate, THRESHOLDS[thr_key], rate is not None and rate >= THRESHOLDS[thr_key], n=len(items)))

    passed = all(t["passed"] for t in tests)
    return {
        "judge_id": next(iter(jv.values()), {}).get("judge_id") if jv else None,
        "n_pairs": n_all,
        "tests": tests,
        "passed": passed,
        "fallback": None if passed else "structure only",
    }


# --------------------------------------------------------------------------
# claim_faithfulness side column (spec 7.6). NOT validated.
# --------------------------------------------------------------------------

CLAIM_LABELS = ("supported", "unsupported", "contradicted")

CLAIMS_INSTRUCTIONS = (
    "Split the answer into separate factual claims. For each claim, label it against the source:\n"
    '"supported" (the source says it), "unsupported" (the source does not say it), or\n'
    '"contradicted" (the source says the opposite).\n'
    "Reply with exactly one line of JSON:\n"
    '{"claims": [{"claim": "<short claim>", "label": "supported" | "unsupported" | "contradicted"}]}'
)


def claim_faithfulness_prompt(source: str, answer: str) -> List[dict]:
    text = f"{CLAIMS_INSTRUCTIONS}\n\nSource:\n{source}\n\nAnswer:\n{answer}"
    return [{"role": "user", "content": text}]


def parse_claims(text: str) -> dict:
    """Parse the claim labels. The result is marked not validated (spec 7.6)."""
    out = {"claims": [], "counts": {k: 0 for k in CLAIM_LABELS}, "parse_ok": False, "validation": "not validated"}
    try:
        obj = json.loads(_unfence(text))
    except (ValueError, TypeError):
        return out
    claims = obj.get("claims") if isinstance(obj, dict) else None
    if not isinstance(claims, list):
        return out
    parsed = []
    for c in claims:
        if not isinstance(c, dict) or c.get("label") not in CLAIM_LABELS or not isinstance(c.get("claim"), str):
            return out
        parsed.append({"claim": c["claim"], "label": c["label"]})
    out["claims"] = parsed
    for c in parsed:
        out["counts"][c["label"]] += 1
    out["parse_ok"] = True
    return out


# ==========================================================================
# Writing family (spec 03 section 9.4)
# ==========================================================================

WRITING_PAIRWISE_RUBRIC = r'''You are comparing two drafts written for the same brief. Decide which one the
sender would rather send. Both drafts already contain every required point and
meet the length and format limits.

Look at these, in this order:
1. Does it do the job? The purpose of the brief is clear from the first lines
   (the decline is clearly a decline, the ask is clearly an ask).
2. Every point is made plainly, once, without burying it.
3. It fits the recipient and the relationship. Hard messages are kind and clear:
   no grovelling, no blame, no passive aggression, no false promises.
4. It sounds like a person. If samples of the sender's writing are given, it
   sounds like them.
5. Nothing extra. A longer draft is NOT better. Extra apologies, repeated thanks,
   filler pleasantries, or details the brief did not give all count against it.

Reply with one line of JSON:
{"winner": "A" | "B" | "tie_good" | "tie_bad", "deciding_point": 1-5,
 "reason": "<one sentence, under 25 words>"}'''

WRITING_SUBS_VALIDATED = _V.WRITING_SUBS  # W1, W2, W3, W5, W6; W4 is scored by WD8, never voted
WRITING_PADDING_N = 50

# Invented polite paragraphs for the padding probe (no facts, no names).
POLITE_PARAGRAPHS = (
    "I really appreciate your time and patience with this, and I hope the timing is not an "
    "inconvenience. Please accept my warmest thanks in advance for your understanding.",
    "I wanted to say how grateful I am for everything, and that I hope you are keeping well. "
    "It means a great deal to me, and I truly value the way you always handle things.",
    "Thank you again for taking the time to read this. I know how busy things can get, and I "
    "am sorry if this adds to your plate. Your kindness is always appreciated.",
    "I hope this message finds you well and that your week has been going smoothly. I am "
    "thankful for your help, and I would be glad to return the favour whenever I can.",
    "Please forgive the length of this note, and thank you for bearing with me. I sincerely "
    "hope that none of this causes any trouble, and I appreciate your generosity.",
    "It is always a pleasure to be in touch, and I am deeply thankful for your support. I hope "
    "you know how much it is valued, and I send my best wishes for the days ahead.",
)


def build_writing_pairwise_messages(case: dict, draft_a: str, draft_b: str, *, active_prefs: Optional[Sequence[str]] = None) -> List[dict]:
    """One user message: the rubric verbatim, then the brief (the rendered request), the
    recipient and relation, any active preferences, the word limit, Draft A and Draft B."""
    prefs = [str(p) for p in (active_prefs if active_prefs is not None else _V.writing_prefs(case))]
    parts = [
        WRITING_PAIRWISE_RUBRIC, "", "Brief:", _V.writing_request_text(case), "",
        f"Recipient and relation: {_V.writing_recipient_text(case)}",
    ]
    if prefs:
        parts += ["", "The sender has these standing preferences active:", *[f"- {p}" for p in prefs]]
    parts += ["", f"Limit: {_V.writing_limit_text(case)}", "", "Draft A:", draft_a, "", "Draft B:", draft_b]
    return [{"role": "user", "content": "\n".join(parts)}]


def parse_writing_verdict(text: str) -> dict:
    """Same one-line JSON verdict as `parse_verdict` (winner A, B, tie_good or tie_bad)."""
    return parse_verdict(text)


def writing_pair_content_hash(pair: dict, active_prefs: Sequence[str] = ()) -> str:
    case = pair.get("case") or {}
    payload = {
        "kind": "writing", "rubric": rubric_hash(WRITING_PAIRWISE_RUBRIC),
        "case_id": case.get("case_id"), "turns": case.get("turns"),
        "a": pair["a"].get("text"), "b": pair["b"].get("text"), "prefs": [str(p) for p in active_prefs],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def judge_writing_pair(client, spec: JudgeSpec, pair: dict, *, cache=None, model_family: Optional[dict] = None, active_prefs: Optional[Sequence[str]] = None) -> dict:
    """Judge one writing pair in both side orders, as `judge_pair` does (same cache, same
    combination rule: a verdict counts only if both orders agree).

    A pair with `auto_outcome` (a draft failed the gate) is never sent: the gated draft
    wins. The judge sees the brief, recipient, preferences and word limit.
    """
    cache = {} if cache is None else cache
    jid = judge_id(spec)
    check = check_family(spec, [pair["a"].get("model_id"), pair["b"].get("model_id")], model_family) if model_family is not None else None
    if check is None:
        if _is_gemma(spec, None):
            raise FamilyRuleError(f"judge {spec.model_id!r} is Gemma-family; never allowed as a judge")
    elif not check["ok"]:
        raise FamilyRuleError(check["reason"])
    base = {"pair_id": pair.get("pair_id"), "judge_id": jid}
    if pair.get("auto_outcome"):
        return {**base, "outcome": pair["auto_outcome"], "position_consistent": True, "orders": [], "auto": True}
    case = pair["case"]
    prefs = list(active_prefs if active_prefs is not None else _V.writing_prefs(case))
    chash = writing_pair_content_hash(pair, prefs)
    orders = []
    for order in ("AB", "BA"):
        first, second = (pair["a"], pair["b"]) if order == "AB" else (pair["b"], pair["a"])
        key = cache_key(jid, chash, order)
        hit = cache.get(key) if hasattr(cache, "get") else (cache[key] if key in cache else None)
        if hit is None:
            msgs = build_writing_pairwise_messages(case, first["text"], second["text"], active_prefs=prefs)
            raw = _call(client, spec, msgs, PAIRWISE_MAX_TOKENS)
            cache[key] = {"raw": raw}
        else:
            raw = hit["raw"]
        v = parse_writing_verdict(raw)
        mapped = _flip(v["winner"]) if order == "BA" else v["winner"]
        orders.append({"order": order, "raw": raw, "winner": mapped, "shown_winner": v["winner"],
                       "deciding_point": v["deciding_point"], "reason": v["reason"], "parse_ok": v["parse_ok"]})
    outcome, consistent = _combine(orders)
    return {**base, "outcome": outcome, "position_consistent": consistent, "orders": orders}


# --------------------------------------------------------------------------
# Writing padding probe (spec 9.4, measure 3)
# --------------------------------------------------------------------------


def _within_limits(case: dict, text: str) -> bool:
    """The padded draft still stays inside the brief's word, sentence and character limits."""
    c = case.get("constraints") or {}
    if c.get("exact_words") or c.get("exact_sentences") or c.get("max_sentences"):
        return False  # one more paragraph would break an exact or sentence limit
    if c.get("max_words") and len(text.split()) > int(c["max_words"]):
        return False
    if c.get("max_chars") and len(text) > int(c["max_chars"]):
        return False
    return True


def _pad_draft(draft: str, paragraph: str) -> str:
    """Insert the extra paragraph before the closing paragraph (the sign-off) when the
    draft has three or more paragraphs, else at the end."""
    paras = [p for p in re.split(r"\n\s*\n", draft.strip()) if p.strip()]
    if len(paras) >= 3:
        paras.insert(len(paras) - 1, paragraph)
    else:
        paras.append(paragraph)
    return "\n\n".join(paras)


def build_writing_padding_pairs(cases_with_drafts: Sequence[dict], n: int = WRITING_PADDING_N, seed: int = 0) -> List[dict]:
    """Up to n pairs: a gated draft against the same draft plus one extra polite paragraph
    that stays within the brief's limits. Drafts where no paragraph fits are skipped.
    Items: {"case", "draft", optional "model_id", "gate_pass"}. Each pair has
    `probe` "writing_padding" and `expected_winner` (the unpadded draft's side)."""
    rng = random.Random(seed)
    combos = []
    for i, it in enumerate(cases_with_drafts):
        if it.get("gate_pass") is False or not str(it.get("draft", "")).strip():
            continue
        for j, para in enumerate(POLITE_PARAGRAPHS):
            padded = _pad_draft(it["draft"], para)
            if _within_limits(it["case"], padded):
                combos.append((i, j, padded))
    rng.shuffle(combos)
    out = []
    for k, (i, j, padded) in enumerate(combos[:n]):
        it = cases_with_drafts[i]
        case = it["case"]
        mid = it.get("model_id")
        orig = {"answer_id": f"{case['case_id']}:orig", "model_id": mid, "text": it["draft"]}
        pad = {"answer_id": f"{case['case_id']}:pad{j}", "model_id": mid, "text": padded}
        orig_first = rng.random() < 0.5
        a, b = (orig, pad) if orig_first else (pad, orig)
        out.append({"pair_id": f"wprobe-padding-{k:03d}", "family": "writing", "case_id": case["case_id"],
                    "sub": case.get("sub"), "block": "probe", "case": case,
                    "request": _V.writing_request_text(case), "a": a, "b": b, "probe": "writing_padding",
                    "expected_winner": "A" if orig_first else "B"})
    return out


def _outcome_of(res: Any) -> Optional[str]:
    if isinstance(res, dict):
        res = res.get("outcome")
    return res if res in OUTCOMES else None


def writing_padding_probe(cases_with_drafts: Sequence[dict], n: int = WRITING_PADDING_N, seed: int = 0, *, judge_fn=None) -> dict:
    """Build the padding pairs and, given `judge_fn(pair) -> outcome` (or a dict with
    "outcome"), run them. The judge must prefer the shorter draft at least 90 % of the time.

    Returns {"passed", "rate", "n", "n_requested", "threshold", "pairs"}. Without
    `judge_fn`, `passed` and `rate` are None. Fewer than n buildable pairs fails.
    """
    pairs = build_writing_padding_pairs(cases_with_drafts, n, seed)
    res = {"n": len(pairs), "n_requested": n, "threshold": THRESHOLDS["padding"], "pairs": pairs,
           "passed": None, "rate": None}
    if judge_fn is None:
        return res
    right = sum(1 for p in pairs if _outcome_of(judge_fn(p)) == p["expected_winner"])
    res["rate"] = right / len(pairs) if pairs else None
    res["passed"] = bool(pairs) and len(pairs) >= n and res["rate"] >= THRESHOLDS["padding"]
    return res


# --------------------------------------------------------------------------
# Writing validation and the headline input
# --------------------------------------------------------------------------


def validate_writing_judge(judge_fn, author_votes: Sequence[dict], *, pairs: Optional[Sequence[dict]] = None,
                           padding: Optional[dict] = None, self_consistency: Optional[float] = None) -> dict:
    """Validate the writing judge on the author's votes over the writing validation pairs.

    `judge_fn(pair) -> outcome` ("A", "B", "tie_good", "tie_bad") or a dict with
    "outcome" (and optionally "position_consistent"). `author_votes`: vote records with
    `pair_id` and `outcome` (pairs looked up in `pairs`), or records with an embedded
    `pair`. Only validation pairs of W1, W2, W3, W5 and W6 count; W4 and automatic
    (gate) pairs are ignored. Gating tests: agreement on non-tie pairs >= 80 % and the
    padding probe (a `writing_padding_probe` result) >= 90 %; position consistency (>= 80 %)
    gates only when the judge reports it; agreement against the author's own
    self-consistency (within 5 points) gates when given. Agreement per sub-family is
    reported and does not gate. Missing data fails its test.
    """
    by_id = {p["pair_id"]: p for p in (pairs or [])}
    rows, flags, excluded = [], [], 0
    for v in author_votes:
        p = v.get("pair") or by_id.get(v.get("pair_id"))
        if p is None or v.get("outcome") not in OUTCOMES or p.get("auto_outcome"):
            continue
        if p.get("block", "validation") != "validation":
            continue
        if p.get("sub") not in WRITING_SUBS_VALIDATED:
            excluded += 1
            continue
        res = judge_fn(p)
        out = _outcome_of(res)
        if out is None:
            out = "tie_good"
        if isinstance(res, dict) and "position_consistent" in res:
            flags.append(bool(res["position_consistent"]))
        rows.append((p, v["outcome"], out))
    nt = [(p, a, o) for p, a, o in rows if a in ("A", "B")]
    agree = sum(1 for _, a, o in nt if o == a) / len(nt) if nt else None
    tests = [_test("agreement_non_tie", agree, THRESHOLDS["agreement"],
                   agree is not None and agree >= THRESHOLDS["agreement"], n=len(nt))]
    sc = _frac(self_consistency)
    if sc is not None:
        thr = sc - THRESHOLDS["self_consistency_margin"]
        tests.append(_test("agreement_vs_self_consistency", agree, thr,
                           agree is not None and agree >= thr - 1e-12, self_consistency=sc))
    if flags:
        pc = sum(flags) / len(flags)
        tests.append(_test("position_consistency", pc, THRESHOLDS["position_consistency"],
                           pc >= THRESHOLDS["position_consistency"], n=len(flags)))
    prate = (padding or {}).get("rate")
    tests.append(_test("writing_padding_probe", prate, THRESHOLDS["padding"],
                       bool(padding) and bool(padding.get("passed")) and prate is not None
                       and prate >= THRESHOLDS["padding"], n=(padding or {}).get("n", 0)))
    per_sub = {}
    for sub in WRITING_SUBS_VALIDATED:
        s_nt = [(a, o) for p, a, o in nt if p.get("sub") == sub]
        per_sub[sub] = {"agreement": (sum(1 for a, o in s_nt if o == a) / len(s_nt)) if s_nt else None,
                        "n_non_tie": len(s_nt), "n": sum(1 for p, _, _ in rows if p.get("sub") == sub),
                        "gating": False}
    return {"n_pairs": len(rows), "n_non_tie": len(nt), "n_expected": 120, "n_excluded_w4": excluded,
            "tests": tests, "per_sub": per_sub, "padding": {k: v for k, v in (padding or {}).items() if k != "pairs"},
            "passed": all(t["passed"] for t in tests)}


def writing_pref_judge(verdicts: Sequence[dict], pairs: Sequence[dict], baseline_id: str = "Gemma-Wisp") -> dict:
    """Per model: win rate against the baseline on writing pairs, 0-100, ties half.

    Pairs with `auto_outcome` (a draft failed the gate) count with that outcome whether or
    not a verdict exists. Returns {model: {"win_rate_raw", "style_controlled", "n",
    "n_auto", "non_tie"}} and, under "_ranking", the full and length-matched rankings.
    `style_controlled` fits the length, list and bold covariates over all pairs given
    (candidate-against-candidate pairs help the fit) and reads off the rate with those
    terms at zero.
    """
    by_id = {v["pair_id"]: v for v in verdicts}
    items = []
    for p in pairs:
        if p.get("auto_outcome"):
            items.append((p, p["auto_outcome"]))
        elif p["pair_id"] in by_id:
            items.append((p, by_id[p["pair_id"]]["outcome"]))
    ranking = _V.writing_rankings(items, anchor=baseline_id)
    acc: Dict[str, dict] = {}
    for p, outcome in items:
        ma, mb = p["a"].get("model_id"), p["b"].get("model_id")
        if ma == baseline_id and mb != baseline_id:
            model, side = mb, "B"
        elif mb == baseline_id and ma != baseline_id:
            model, side = ma, "A"
        else:
            continue
        d = acc.setdefault(model, {"score": 0.0, "n": 0, "n_auto": 0, "non_tie": 0})
        d["score"] += _score_for(outcome, side)
        d["n"] += 1
        d["n_auto"] += 1 if p.get("auto_outcome") else 0
        d["non_tie"] += 0 if outcome in TIES else 1
    out: Dict[str, Any] = {}
    for m, d in acc.items():
        out[m] = {"win_rate_raw": 100.0 * d["score"] / d["n"],
                  "style_controlled": ranking["full"]["models"].get(m, {}).get("style_ctrl_win_rate"),
                  "n": d["n"], "n_auto": d["n_auto"], "non_tie": d["non_tie"]}
    out["_ranking"] = ranking
    return out
