"""Single-rater blind-vote tool for EDAT (spec 03 s4.3, s5, s13.1).

A local command-line tool plus importable functions. No server, no network.

    python -m edat.quality.votes build   --answers answers.jsonl --candidates a,b ...
    python -m edat.quality.votes vote    [--pairs pairs.jsonl --votes votes.jsonl]
    python -m edat.quality.votes summary [--pairs pairs.jsonl --votes votes.jsonl]

Files are JSONL, one object per line, stored in ``$EDAT_PRIVATE_DIR/votes/`` when
that variable is set, else ``./votes/`` (never inside the repository's tracked
folders: see ``check_output_path``).

Pair file line::

    {"pair_id", "case_id", "split", "block", "request", "profile",
     "a": {"answer_id", "model_id", "text", "visible_tokens"},
     "b": {...}, "repeat_of": null | pair_id,
     "qtype", "length_matched", "auto_outcome", "auto_reason"}

``block`` is one of standing, validation, cloud, repeat, gold, probe, calibration,
spot. The last five keys are additions: ``auto_outcome`` is set ("A", "B" or
"tie_bad") when an answer leaked, so the pair is never shown and loses
automatically; ``visible_tokens`` inside a side is used for style control.

Vote file line::

    {"vote_id", "pair_id", "session_id", "ts", "case_id", "block",
     "shown_left": "A"|"B", "side_seed", "choice": "left"|"right"|"tie_good"|"tie_bad",
     "outcome": "A"|"B"|"tie_good"|"tie_bad", "flag", "flag_side": "A"|"B"|"both"|null,
     "probe_guess": null|"left"|"right", "probe_correct": null|bool}

Probe votes carry only the baseline guess (choice and outcome are null).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import textwrap
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from . import stats as S

VOTES_VERSION = "edat-votes/1"

BASELINE_ID = "Gemma-Wisp"
BASE_ID = "gemma-4-E4B-it-oQ4e-mtp"

BLOCKS = ("standing", "validation", "cloud", "repeat", "gold", "probe", "calibration", "spot")
FLAGS = ("wrong_invented", "ignored_format", "too_long", "too_short",
         "hard_to_read", "odd_tone", "leak")

# Version 1 vote set (spec 4.3). Plain data: change the counts here or pass a copy.
V1_PLAN = {
    "standing": 150,
    "validation_vs_baseline": 240,
    "validation_cand_cand": 60,
    "cloud_per_model": 20,
    "repeats": 45,
    "gold": 25,
    "spot_per_candidate": 0,       # optional extra candidate-vs-baseline pairs
    "probe_per_session": 10,
    "probe_sessions": 7,           # probe pairs built = per_session * sessions
    "calibration": 6,
    "length_matched_share": 0.30,  # at least this share of drawn pairs length-matched
    "length_band": 0.20,           # within +-20 % visible tokens
}

PROFILE_WIDTH = {"panel": 45, "window": 90}
RECOGNITION_THRESHOLD = 65.0       # percent correct above which blindness failed
MIN_PROBE_GUESSES = 5              # fewer guesses in a session: not judged
SHOWN_BLOCKS_FOR_PREF = ("validation", "cloud", "spot")

GOLD_OPENERS = (
    "Great question! I would be absolutely delighted to help you with this today.",
    "Thanks so much for asking, this is a really interesting request and I am happy to dive in.",
    "Of course! Let me take a moment to think carefully about this before answering.",
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_split(split: str | None) -> str:
    return re.sub(r"[^a-z]", "", (split or "").lower())


def is_heldout(split: str | None) -> bool:
    return _norm_split(split) == "heldout"


def votes_dir() -> Path:
    base = os.environ.get("EDAT_PRIVATE_DIR")
    return (Path(base) / "votes") if base else (Path.cwd() / "votes")


def _repo_root(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def check_output_path(path: str | Path, *, allow_in_repo: bool = False) -> Path:
    """Refuse paths inside a git checkout, except a top-level ``votes/`` folder."""
    p = Path(path).expanduser().resolve()
    if allow_in_repo:
        return p
    root = _repo_root(p.parent)
    if root is not None:
        rel = p.relative_to(root)
        if rel.parts and rel.parts[0] != "votes":
            raise ValueError(
                f"{p} is inside a git repository ({root}); keep vote files outside tracked "
                "folders (set EDAT_PRIVATE_DIR) or use a top-level votes/ folder")
    return p


def read_jsonl(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_jsonl(path: str | Path, rows: Iterable[Mapping]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def append_jsonl(path: str | Path, row: Mapping) -> None:
    """Append one line and flush it to disk."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass


def _tokens(answer: Mapping) -> int:
    vt = answer.get("visible_tokens")
    if vt is None:
        vt = len(str(answer.get("text", "")).split())
    return max(1, int(vt))


def length_matched(tok_a: int, tok_b: int, band: float = 0.20) -> bool:
    """True when the two lengths are within +-band of each other (ratio 0.8-1.25 at 20 %)."""
    if tok_a <= 0 or tok_b <= 0:
        return False
    r = tok_a / tok_b
    return (1.0 - band) <= r <= 1.0 / (1.0 - band)


# ---------------------------------------------------------------------------
# Style features (computed from answer text)
# ---------------------------------------------------------------------------

_LIST_RE = re.compile(r"^[ \t]*(?:[-*+•]|\d{1,3}[.)])[ \t]+\S", re.M)
_BOLD_RE = re.compile(r"\*\*[^*\n]+?\*\*|__[^_\n]+?__")
_HEADER_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+\S", re.M)
_EMOJI_RANGES = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF), (0x2300, 0x23FF))


def style_features(text: str, visible_tokens: int | None = None) -> dict:
    """Counts used for style control. Token proxy: word count when no token count is given."""
    text = text or ""
    tokens = visible_tokens if visible_tokens is not None else len(text.split())
    emoji = sum(1 for ch in text if any(lo <= ord(ch) <= hi for lo, hi in _EMOJI_RANGES))
    return {
        "tokens": max(1, int(tokens)),
        "list_items": len(_LIST_RE.findall(text)),
        "bold": len(_BOLD_RE.findall(text)),
        "headers": len(_HEADER_RE.findall(text)),
        "emoji": emoji,
    }


def feature_diff(side_a: Mapping, side_b: Mapping) -> dict:
    fa = style_features(side_a.get("text", ""), side_a.get("visible_tokens"))
    fb = style_features(side_b.get("text", ""), side_b.get("visible_tokens"))
    return {
        "log_len": math.log(fa["tokens"] / fb["tokens"]),
        "list_items": fa["list_items"] - fb["list_items"],
        "bold": fa["bold"] - fb["bold"],
        "headers": fa["headers"] - fb["headers"],
        "emoji": fa["emoji"] - fb["emoji"],
    }


# ---------------------------------------------------------------------------
# Building the vote set
# ---------------------------------------------------------------------------

def _side(ans: Mapping) -> dict:
    return {"answer_id": ans["answer_id"], "model_id": ans["model_id"],
            "text": ans.get("text", ""), "visible_tokens": _tokens(ans)}


def _make_pair(pair_id, block, case, a_ans, b_ans, *, matched=None, repeat_of=None) -> dict:
    ta, tb = _tokens(a_ans), _tokens(b_ans)
    return {
        "pair_id": pair_id, "case_id": case["case_id"], "split": case["split"], "block": block,
        "request": case["request"], "profile": case["profile"],
        "a": _side(a_ans), "b": _side(b_ans), "repeat_of": repeat_of,
        "qtype": case.get("qtype"),
        "length_matched": length_matched(ta, tb) if matched is None else matched,
    }


def make_gold_pair(answer: Mapping, *, pair_id: str = "gold-000", seed: int = 0) -> dict:
    """Attention-check pair: the clean answer (A) against the same answer with a planted
    filler opener and doubled length (B). The clean answer is the obvious winner."""
    rng = random.Random(f"{seed}:{answer['answer_id']}")
    clean = answer.get("text", "")
    opener = rng.choice(GOLD_OPENERS)
    padded = f"{opener}\n\n{clean}\n\n{clean}"
    case = {"case_id": answer["case_id"], "split": answer.get("split", ""),
            "request": answer.get("request", ""), "profile": answer.get("profile", "panel"),
            "qtype": answer.get("qtype")}
    a = {**answer}
    b = {**answer, "answer_id": f"{answer['answer_id']}+padded", "model_id": "gold-padded",
         "text": padded, "visible_tokens": len(padded.split())}
    if answer.get("visible_tokens") is not None:
        b["visible_tokens"] = 2 * int(answer["visible_tokens"]) + len(opener.split())
    return _make_pair(pair_id, "gold", case, a, b, matched=False)


class _Pool:
    """Answers (never leaked ones) indexed by model and case, plus case metadata."""

    def __init__(self, answers: Sequence[Mapping]):
        self.by_model: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        self.cases: dict[str, dict] = {}
        self.leaked: list[dict] = []
        for a in sorted(answers, key=lambda x: x["answer_id"]):
            cid = a["case_id"]
            self.cases.setdefault(cid, {
                "case_id": cid, "split": a.get("split", ""), "qtype": a.get("qtype"),
                "profile": a.get("profile", "panel"), "request": a.get("request", "")})
            if a.get("leak"):
                self.leaked.append(a)
            else:
                self.by_model[a["model_id"]][cid].append(a)

    def answers(self, model: str, case_id: str) -> list[dict]:
        return self.by_model.get(model, {}).get(case_id, [])


def _draw(rng, pool: _Pool, usage: Counter, used: set, *, n: int, a_models: Sequence[str],
          b_models: Sequence[str] | None, case_ok: Callable[[dict], bool], share: float,
          band: float) -> list[tuple[dict, dict, dict]]:
    """Draw up to n (case, answer_a, answer_b) triples, stratified by (qtype, profile)."""
    if n <= 0 or not a_models:
        return []
    strata: dict[tuple, list[str]] = defaultdict(list)
    for cid, case in pool.cases.items():
        if case_ok(case):
            strata[(str(case.get("qtype")), case.get("profile"))].append(cid)
    if not strata:
        return []
    order = sorted(strata)
    rng.shuffle(order)
    for k in order:
        strata[k].sort()
        rng.shuffle(strata[k])
    cursor = {k: 0 for k in order}
    out: list[tuple[dict, dict, dict]] = []
    matched_count = 0
    s_ptr = a_ptr = 0
    fails = 0
    limit = len(order) * max(1, len(a_models)) * 3
    while len(out) < n and fails <= limit:
        key = order[s_ptr % len(order)]
        s_ptr += 1
        a_model = a_models[(a_ptr + a_ptr // len(order)) % len(a_models)]
        if b_models is None:
            others = [m for m in a_models if m != a_model]
            if not others:
                return out
            b_model = rng.choice(others)
        else:
            b_model = b_models[0]
        cases = strata[key]
        picked = None
        for step in range(len(cases)):
            cid = cases[(cursor[key] + step) % len(cases)]
            xs, ys = pool.answers(a_model, cid), pool.answers(b_model, cid)
            cands = [(x, y) for x in xs for y in ys
                     if frozenset((x["answer_id"], y["answer_id"])) not in used]
            if not cands:
                continue
            need = matched_count < math.ceil(share * (len(out) + 1))
            if need:
                m = [p for p in cands if length_matched(_tokens(p[0]), _tokens(p[1]), band)]
                if m:
                    cands = m
            low = min(usage[y["answer_id"]] + usage[x["answer_id"]] for x, y in cands)
            best = [p for p in cands if usage[p[0]["answer_id"]] + usage[p[1]["answer_id"]] == low]
            picked = (rng.choice(best), pool.cases[cid])
            cursor[key] = (cursor[key] + step + 1) % len(cases)
            break
        if picked is None:
            fails += 1
            continue
        fails = 0
        a_ptr += 1
        (x, y), case = picked
        used.add(frozenset((x["answer_id"], y["answer_id"])))
        usage[x["answer_id"]] += 1
        usage[y["answer_id"]] += 1
        if length_matched(_tokens(x), _tokens(y), band):
            matched_count += 1
        if rng.random() < 0.5 and b_models is None:
            x, y = y, x  # candidate-vs-candidate: side A is arbitrary
        out.append((case, x, y))
    return out


def build_pairs(answers: Sequence[Mapping], *, candidate_ids: Sequence[str],
                baseline_id: str = BASELINE_ID, base_id: str = BASE_ID,
                cloud_ids: Sequence[str] = (), plan: Mapping = V1_PLAN, seed: int = 0,
                heldout_split: str = "heldout") -> list[dict]:
    """Draw the version 1 vote set from answer records.

    answers: {answer_id, model_id, case_id, split, seed, text, visible_tokens, qtype,
    profile, request, leak}. Side A is the candidate (cloud model, or base Gemma in the
    standing pair), side B the baseline. Leaked answers are never shown: each one that
    would meet the baseline gets a pair with ``auto_outcome`` set (it loses). Order of
    the returned list: calibration, probe, standing, validation, spot, cloud, repeats,
    gold, auto-leak pairs. Pools too small for a quota give fewer pairs, never repeats
    of the same answer pair.
    """
    plan = {**V1_PLAN, **dict(plan)}
    rng = random.Random(seed)
    pool = _Pool(answers)
    band = float(plan["length_band"])
    share = float(plan["length_matched_share"])
    held = (lambda c: _norm_split(c["split"]) == _norm_split(heldout_split))
    counters: Counter = Counter()
    pairs: list[dict] = []

    def emit(block, triples, **kw):
        out = []
        for case, x, y in triples:
            counters[block] += 1
            out.append(_make_pair(f"{block}-{counters[block]:03d}", block, case, x, y, **kw))
        pairs.extend(out)
        return out

    # Calibration and probe use the development split when there is one.
    non_held = [c for c in pool.cases.values() if not held(c)]
    warm_ok = (lambda c: not held(c)) if non_held else (lambda c: True)
    warm_usage: Counter = Counter()
    warm_used: set = set()
    emit("calibration", _draw(rng, pool, warm_usage, warm_used, n=int(plan["calibration"]),
                              a_models=list(candidate_ids), b_models=[baseline_id],
                              case_ok=warm_ok, share=0.0, band=band))
    emit("probe", _draw(rng, pool, warm_usage, warm_used,
                        n=int(plan["probe_per_session"]) * int(plan["probe_sessions"]),
                        a_models=list(candidate_ids), b_models=[baseline_id],
                        case_ok=warm_ok, share=0.0, band=band))

    usage: Counter = Counter()
    used: set = set()
    shown = []
    shown += emit("standing", _draw(rng, pool, usage, used, n=int(plan["standing"]),
                                    a_models=[base_id], b_models=[baseline_id],
                                    case_ok=held, share=share, band=band))
    shown += emit("validation", _draw(rng, pool, usage, used,
                                      n=int(plan["validation_vs_baseline"]),
                                      a_models=list(candidate_ids), b_models=[baseline_id],
                                      case_ok=held, share=share, band=band))
    shown += emit("validation", _draw(rng, pool, usage, used,
                                      n=int(plan["validation_cand_cand"]),
                                      a_models=list(candidate_ids), b_models=None,
                                      case_ok=held, share=share, band=band))
    if int(plan["spot_per_candidate"]) > 0:
        for cid in candidate_ids:
            shown += emit("spot", _draw(rng, pool, usage, used,
                                        n=int(plan["spot_per_candidate"]),
                                        a_models=[cid], b_models=[baseline_id],
                                        case_ok=held, share=share, band=band))
    for cloud in cloud_ids:
        shown += emit("cloud", _draw(rng, pool, usage, used, n=int(plan["cloud_per_model"]),
                                     a_models=[cloud], b_models=[baseline_id],
                                     case_ok=held, share=share, band=band))

    # Repeats: same answers, sides swapped, new pair id.
    n_rep = min(int(plan["repeats"]), len(shown))
    for orig in rng.sample(sorted(shown, key=lambda p: p["pair_id"]), n_rep):
        counters["repeat"] += 1
        pairs.append({**orig, "pair_id": f"repeat-{counters['repeat']:03d}", "block": "repeat",
                      "a": dict(orig["b"]), "b": dict(orig["a"]), "repeat_of": orig["pair_id"]})

    # Gold pairs: one answer per case where possible.
    clean = [a for m in sorted(pool.by_model) if m in set(candidate_ids) | {baseline_id}
             for cid in sorted(pool.by_model[m]) for a in pool.by_model[m][cid][:1]]
    rng.shuffle(clean)
    seen_cases: set = set()
    chosen = []
    for a in clean:
        if a["case_id"] not in seen_cases:
            seen_cases.add(a["case_id"])
            chosen.append(a)
    for a in clean:
        if len(chosen) >= int(plan["gold"]):
            break
        if a not in chosen:
            chosen.append(a)
    for a in chosen[: int(plan["gold"])]:
        counters["gold"] += 1
        pairs.append(make_gold_pair(a, pair_id=f"gold-{counters['gold']:03d}", seed=seed))

    # Leaked answers lose automatically (never shown).
    for leaked in pool.leaked:
        if _norm_split(leaked.get("split")) != _norm_split(heldout_split):
            continue
        case = pool.cases[leaked["case_id"]]
        m = leaked["model_id"]
        if m == baseline_id:
            opp = [x for c in candidate_ids for x in pool.answers(c, leaked["case_id"])]
            block, leaked_is_a = "validation", False
        else:
            opp = [x for x in pool.answers(baseline_id, leaked["case_id"])]
            block = "cloud" if m in set(cloud_ids) else ("standing" if m == base_id else "validation")
            leaked_is_a = True
        if not opp:
            continue
        other = opp[0]
        counters["auto"] += 1
        withheld = {**leaked, "text": "[withheld: leak]"}
        if leaked_is_a:
            pr = _make_pair(f"auto-{counters['auto']:03d}", block, case, withheld, other, matched=False)
            pr["auto_outcome"] = "B"
        else:
            pr = _make_pair(f"auto-{counters['auto']:03d}", block, case, other, withheld, matched=False)
            pr["auto_outcome"] = "A"
        pr["auto_reason"] = "leak"
        pairs.append(pr)
    return pairs


def plan_report(pairs: Sequence[Mapping]) -> dict:
    """Counts per block (shown pairs) and automatic leak losses."""
    c = Counter(p["block"] for p in pairs if not p.get("auto_outcome"))
    out = {b: c.get(b, 0) for b in BLOCKS}
    out["auto_leak"] = sum(1 for p in pairs if p.get("auto_outcome"))
    out["length_matched"] = sum(1 for p in pairs if p.get("length_matched") and not p.get("auto_outcome")
                                and p["block"] in ("validation", "spot", "cloud", "standing"))
    return out


# ---------------------------------------------------------------------------
# Terminal session
# ---------------------------------------------------------------------------

def shown_left_for(side_seed: int) -> str:
    """Which canonical side ("A" or "B") is shown on the left for a logged side seed."""
    return "A" if random.Random(side_seed).random() < 0.5 else "B"


def render_text(text: str, profile: str = "panel") -> str:
    """Plain-text rendering wrapped to ~45 characters (panel) or 90 (window)."""
    width = PROFILE_WIDTH.get(profile, 90)
    lines = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        if not raw.strip():
            lines.append("")
            continue
        indent = re.match(r"[ \t]*(?:(?:[-*+•]|\d{1,3}[.)])[ \t]+)?", raw).group(0)
        sub = " " * len(indent.replace("\t", "    "))
        lines.extend(textwrap.wrap(raw.rstrip(), width=width, subsequent_indent=sub,
                                   replace_whitespace=False) or [""])
    return "\n".join(lines)


_CHOICE_MAP = {"a": "left", "b": "right", "g": "tie_good", "x": "tie_bad"}


def _outcome_from_choice(choice: str, shown_left: str) -> str:
    if choice == "tie_good" or choice == "tie_bad":
        return choice
    left_side = shown_left
    right_side = "B" if shown_left == "A" else "A"
    return left_side if choice == "left" else right_side


def _ask(input_fn, prompt: str) -> str | None:
    try:
        return input_fn(prompt).strip().lower()
    except EOFError:
        return None


def _parse_flag(raw: str) -> str | None:
    raw = raw.strip().lower()
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(FLAGS):
        return FLAGS[int(raw) - 1]
    for f in FLAGS:
        if raw == f or raw == f.replace("_", " "):
            return f
    return "?"


def start_session(pairs: Sequence[Mapping] | str | Path, votes_path: str | Path, *,
                  input_fn: Callable[[str], str] | None = None,
                  output_fn: Callable[[str], None] = print,
                  seed: int | None = None, session_id: str | None = None,
                  clock: Callable[[], float] = time.monotonic,
                  now_fn: Callable[[], str] = utc_now,
                  max_pairs: int = 90, max_minutes: float = 30.0,
                  baseline_id: str = BASELINE_ID, probe_n: int = 10,
                  calibration_n: int = 6) -> dict:
    """Run one blind-vote session. Returns {"session_id","seed","shown","votes","stopped"}.

    Order: calibration set, recognition probe, then unvoted pairs in random order
    (a repeat only after its original was voted in an earlier session). The cap
    counts every pair shown. Keys: a / b (left / right answer better), g (both good),
    x (both bad), q (save and quit). Votes are appended and flushed one by one.
    """
    input_fn = input_fn or input
    if isinstance(pairs, (str, Path)):
        pairs = read_jsonl(pairs)
    pairs = list(pairs)
    if seed is None:
        seed = int(time.time()) % (2 ** 31)
    sid = session_id or f"s{now_fn().replace('-', '').replace(':', '')}-{seed}"
    rng = random.Random(seed)
    prior = read_jsonl(votes_path)
    voted = {v["pair_id"] for v in prior}
    voted_other_sessions = {v["pair_id"] for v in prior if v.get("session_id") != sid}

    shown_ok = [p for p in pairs if not p.get("auto_outcome")]
    calibration = [p for p in shown_ok if p["block"] == "calibration"][:calibration_n]
    probes = [p for p in shown_ok if p["block"] == "probe" and p["pair_id"] not in voted][:probe_n]
    rest = [p for p in shown_ok if p["block"] not in ("calibration", "probe")
            and p["pair_id"] not in voted
            and (p["block"] != "repeat" or p.get("repeat_of") in voted_other_sessions)]
    rest.sort(key=lambda p: p["pair_id"])
    rng.shuffle(rest)
    queue = calibration + probes + rest

    start = clock()
    shown = votes = 0
    stopped = "finished"
    output_fn(f"EDAT blind vote, session {sid}. Keys: a = left better, b = right better, "
              "g = both good, x = both bad, q = save and quit.")
    output_fn("Which answer would you rather get on your phone or in a small side panel? "
              "Judge how easy it is to read and how well it is written. "
              "Assume both are accurate unless one is clearly wrong.")
    for pair in queue:
        if shown >= max_pairs:
            stopped = "pair_cap"
            break
        if clock() - start >= max_minutes * 60.0:
            stopped = "time_cap"
            break
        side_seed = rng.randrange(2 ** 31)
        left = shown_left_for(side_seed)
        right = "B" if left == "A" else "A"
        sides = {"A": pair["a"], "B": pair["b"]}
        profile = pair.get("profile", "panel")
        width = PROFILE_WIDTH.get(profile, 90)
        is_probe = pair["block"] == "probe"
        shown += 1
        output_fn("")
        output_fn(f"--- [{shown}/{max_pairs}] {pair['block']} ({profile}) ---")
        output_fn("Request:")
        output_fn(render_text(pair.get("request", ""), profile))
        output_fn("")
        output_fn("[A] " + "-" * max(4, width - 4))
        output_fn(render_text(sides[left].get("text", ""), profile))
        output_fn("")
        output_fn("[B] " + "-" * max(4, width - 4))
        output_fn(render_text(sides[right].get("text", ""), profile))
        output_fn("")
        record = {
            "vote_id": f"{sid}-{shown:03d}", "pair_id": pair["pair_id"], "session_id": sid,
            "ts": now_fn(), "case_id": pair["case_id"], "block": pair["block"],
            "shown_left": left, "side_seed": side_seed, "choice": None, "outcome": None,
            "flag": None, "flag_side": None, "probe_guess": None, "probe_correct": None,
        }
        if is_probe:
            while True:
                ans = _ask(input_fn, "Which side is the baseline? [a = left / b = right / q] ")
                if ans in (None, "q"):
                    stopped = "quit"
                    break
                if ans in ("a", "b", "l", "r", "left", "right"):
                    guess = "left" if ans in ("a", "l", "left") else "right"
                    base_side = next((s for s in ("A", "B")
                                      if sides[s].get("model_id") == baseline_id), None)
                    record["probe_guess"] = guess
                    if base_side is not None:
                        record["probe_correct"] = (guess == ("left" if base_side == left else "right"))
                    break
                output_fn("Please answer a, b or q.")
        else:
            while True:
                ans = _ask(input_fn, "Better? [a / b / g = both good / x = both bad / q] ")
                if ans in (None, "q"):
                    stopped = "quit"
                    break
                if ans in _CHOICE_MAP:
                    record["choice"] = _CHOICE_MAP[ans]
                    record["outcome"] = _outcome_from_choice(record["choice"], left)
                    break
                output_fn("Please answer a, b, g, x or q.")
            if stopped != "quit":
                menu = ", ".join(f"{i + 1}={f}" for i, f in enumerate(FLAGS))
                while True:
                    raw = _ask(input_fn, f"Flag (optional: {menu}; Enter = none): ")
                    flag = _parse_flag(raw or "")
                    if flag != "?":
                        break
                    output_fn("Unknown flag.")
                record["flag"] = flag
                if flag:
                    while True:
                        raw = _ask(input_fn, "Which answer? [a = left / b = right / Enter = both]: ") or ""
                        if raw in ("", "both"):
                            record["flag_side"] = "both"
                            break
                        if raw in ("a", "b"):
                            record["flag_side"] = left if raw == "a" else right
                            break
                        output_fn("Please answer a, b or Enter.")
        if stopped == "quit":
            shown -= 1
            break
        append_jsonl(votes_path, record)
        votes += 1
    else:
        if shown >= max_pairs:
            stopped = "pair_cap"
    output_fn(f"Saved {votes} vote(s). Session ended ({stopped}).")
    return {"session_id": sid, "seed": seed, "shown": shown, "votes": votes, "stopped": stopped}


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _vote_records(pairs: Sequence[Mapping], votes: Sequence[Mapping]) -> list[dict]:
    """Join votes with their pairs; add automatic leak losses as session "auto"."""
    by_id = {p["pair_id"]: p for p in pairs}
    recs = []
    for v in votes:
        p = by_id.get(v["pair_id"])
        if p is None:
            continue
        recs.append({"pair": p, "vote": v, "session": v.get("session_id"),
                     "outcome": v.get("outcome"), "auto": False})
    for p in pairs:
        if p.get("auto_outcome"):
            recs.append({"pair": p, "vote": None, "session": "auto",
                         "outcome": p["auto_outcome"], "auto": True})
    return recs


def _involves(pair: Mapping, model_id: str) -> bool:
    return pair["a"].get("model_id") == model_id or pair["b"].get("model_id") == model_id


def _perspective(rec: Mapping, model: str) -> str | None:
    """"win"/"loss"/"tie" for ``model`` in this record."""
    o = rec["outcome"]
    if o in ("tie_good", "tie_bad"):
        return "tie"
    side = "A" if rec["pair"]["a"].get("model_id") == model else "B"
    return "win" if o == side else "loss"


def _rate_block(outs: Sequence[str]) -> dict:
    n = len(outs)
    if n == 0:
        return {"value": None, "lo": None, "hi": None, "n": 0, "n_ties": 0}
    wins = sum(1 for o in outs if o == "win")
    ties = sum(1 for o in outs if o == "tie")
    lo, hi = S.wilson_interval(wins + 0.5 * ties, n)
    return {"value": S.win_rate(outs), "lo": 100.0 * lo, "hi": 100.0 * hi, "n": n, "n_ties": ties}


def _bt_outcome(o: str) -> str:
    return "tie" if o in ("tie_good", "tie_bad") else o


def summarize(pairs: Sequence[Mapping], votes: Sequence[Mapping], *,
              baseline_id: str = BASELINE_ID, base_id: str = BASE_ID) -> dict:
    """Numbers for the results page (spec 13.1). All rates 0-100.

    Keys: pref_raw {model: {value, lo, hi, n, n_ties, n_auto_leak}}, pref_votes {model: non-tie
    count}, pref_style_ctrl {model: win rate vs baseline, style terms at zero}, style_coef,
    pref_raw_length_matched, rater_self_consistency, gold_accuracy, baseline_recognition,
    excluded_sessions, standing {tuning_pref, tuning_pref_style_ctrl, style_share, flags...},
    both_bad_rate, block_counts. Votes from a session whose probe accuracy is above 65 %
    (at least 5 guesses) drop out of every number that involves the baseline.
    """
    pairs = list(pairs)
    votes = list(votes)
    recs = _vote_records(pairs, votes)

    # --- recognition probe -------------------------------------------------
    per_session: dict[str, dict] = {}
    for r in recs:
        v = r["vote"]
        if v is None or v.get("probe_guess") is None or v.get("probe_correct") is None:
            continue
        s = per_session.setdefault(r["session"], {"n": 0, "correct": 0})
        s["n"] += 1
        s["correct"] += 1 if v["probe_correct"] else 0
    excluded = []
    for sid, s in sorted(per_session.items()):
        s["rate"] = 100.0 * s["correct"] / s["n"]
        s["excluded"] = s["n"] >= MIN_PROBE_GUESSES and s["rate"] > RECOGNITION_THRESHOLD
        if s["excluded"]:
            excluded.append(sid)
    tot_n = sum(s["n"] for s in per_session.values())
    tot_c = sum(s["correct"] for s in per_session.values())
    recognition = {"overall": {"n": tot_n, "correct": tot_c,
                               "rate": (100.0 * tot_c / tot_n) if tot_n else None},
                   "per_session": per_session, "threshold": RECOGNITION_THRESHOLD,
                   "min_guesses": MIN_PROBE_GUESSES}

    def keep(r) -> bool:
        return not (r["session"] in excluded and _involves(r["pair"], baseline_id))

    graded = [r for r in recs if r["outcome"] is not None and keep(r)]
    dropped = sum(1 for r in recs if r["outcome"] is not None and not keep(r))
    block_counts = Counter(r["pair"]["block"] for r in recs if r["outcome"] is not None)

    # --- pref_raw / pref_votes, per candidate vs the baseline ----------------
    pref_recs = [r for r in graded if r["pair"]["block"] in SHOWN_BLOCKS_FOR_PREF
                 and _involves(r["pair"], baseline_id)]
    models = sorted({m for r in pref_recs for m in (r["pair"]["a"]["model_id"], r["pair"]["b"]["model_id"])
                     if m != baseline_id})
    pref_raw, pref_votes, pref_lm = {}, {}, {}
    for m in models:
        mine = [r for r in pref_recs if _involves(r["pair"], m)]
        outs = [_perspective(r, m) for r in mine]
        blk = _rate_block(outs)
        blk["n_auto_leak"] = sum(1 for r in mine if r["auto"])
        pref_raw[m] = blk
        pref_votes[m] = sum(1 for o in outs if o != "tie")
        lm = [_perspective(r, m) for r in mine if r["pair"].get("length_matched")]
        pref_lm[m] = _rate_block(lm)

    # --- style-controlled rating over validation / cloud / spot --------------
    comps = []
    for r in graded:
        p = r["pair"]
        if p["block"] not in SHOWN_BLOCKS_FOR_PREF:
            continue
        feats = ({k: 0.0 for k in S.STYLE_FEATURES} if r["auto"] else feature_diff(p["a"], p["b"]))
        comps.append((p["a"]["model_id"], p["b"]["model_id"], _bt_outcome(r["outcome"]), feats))
    pref_style_ctrl, style_coef = {}, {}
    if comps:
        fit = S.style_controlled_bt(comps, anchor=baseline_id)
        pref_style_ctrl = {m: wr for m, wr in fit["win_rate"].items() if m != baseline_id}
        style_coef = fit["coef"]

    # --- self-consistency on repeats ---------------------------------------
    by_pair: dict[str, dict] = {}
    for r in graded:
        if not r["auto"]:
            by_pair.setdefault(r["pair"]["pair_id"], r)

    def verdict(r) -> str:
        o = r["outcome"]
        if o in ("tie_good", "tie_bad"):
            return o
        return "win:" + r["pair"]["a" if o == "A" else "b"]["answer_id"]

    agree = total = 0
    for r in graded:
        p = r["pair"]
        if p["block"] != "repeat" or r["auto"]:
            continue
        orig = by_pair.get(p.get("repeat_of"))
        if orig is None:
            continue
        total += 1
        agree += 1 if verdict(orig) == verdict(r) else 0
    if total:
        lo, hi = S.wilson_interval(agree, total)
        consistency = {"rate": 100.0 * agree / total, "lo": 100.0 * lo, "hi": 100.0 * hi,
                       "n": total, "agree": agree}
    else:
        consistency = {"rate": None, "lo": None, "hi": None, "n": 0, "agree": 0}

    # --- gold pairs ---------------------------------------------------------
    gold = [r for r in graded if r["pair"]["block"] == "gold"]
    gold_ok = sum(1 for r in gold if r["outcome"] == "A")
    gold_accuracy = {"rate": (100.0 * gold_ok / len(gold)) if gold else None,
                     "n": len(gold), "correct": gold_ok}

    # --- standing pair --------------------------------------------------------
    standing_recs = [r for r in graded if r["pair"]["block"] == "standing" and not r["auto"]]
    outs = [{"B": "win", "A": "loss"}.get(r["outcome"], "tie") for r in standing_recs]
    raw = S.tuning_pref(outs)
    ctrl_val = None
    if standing_recs:
        sc = [(r["pair"]["a"]["model_id"], r["pair"]["b"]["model_id"], _bt_outcome(r["outcome"]),
               feature_diff(r["pair"]["a"], r["pair"]["b"])) for r in standing_recs]
        wr = S.style_controlled_win_rate(sc, anchor=base_id)
        if baseline_id in wr:
            ctrl_val = wr[baseline_id] - 50.0
    flags_side: dict[str, Counter] = defaultdict(Counter)
    for r in standing_recs:
        v = r["vote"]
        if v and v.get("flag"):
            sides = ("A", "B") if v.get("flag_side") in (None, "both") else (v["flag_side"],)
            for s in sides:
                flags_side[r["pair"]["a" if s == "A" else "b"]["model_id"]][v["flag"]] += 1
    standing = {
        "tuning_pref": raw["tuning_pref"] if raw["n"] else None,
        "win_rate": raw["win_rate"] if raw["n"] else None,
        "lo": raw["lo"] if raw["n"] else None, "hi": raw["hi"] if raw["n"] else None,
        "n": raw["n"], "n_non_tie": raw["n_non_tie"], "excludes_zero": raw["excludes_zero"],
        "tuning_pref_style_ctrl": ctrl_val,
        "style_share": S.style_share(raw, ctrl_val) if ctrl_val is not None else None,
        "flags_by_side": {m: dict(c) for m, c in flags_side.items()},
    }

    # --- both-bad rate and flags ---------------------------------------------
    voted = [r for r in graded if not r["auto"] and r["pair"]["block"]
             not in ("gold", "probe", "calibration")]
    bad = sum(1 for r in voted if r["outcome"] == "tie_bad")
    flags_all = Counter(r["vote"]["flag"] for r in voted if r["vote"] and r["vote"].get("flag"))

    return {
        "pref_raw": pref_raw, "pref_votes": pref_votes, "pref_style_ctrl": pref_style_ctrl,
        "style_coef": style_coef, "pref_raw_length_matched": pref_lm,
        "rater_self_consistency": consistency, "gold_accuracy": gold_accuracy,
        "baseline_recognition": recognition, "excluded_sessions": excluded,
        "dropped_votes_from_excluded_sessions": dropped,
        "standing": standing,
        "both_bad_rate": (100.0 * bad / len(voted)) if voted else None,
        "flags": dict(flags_all), "block_counts": dict(block_counts),
    }


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def _csv(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def _default_path(name: str, given: str | None) -> Path:
    return Path(given) if given else votes_dir() / name


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m edat.quality.votes",
        description="Single-rater blind vote tool for EDAT (local files only, no network).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="draw the vote set from an answers JSONL file")
    b.add_argument("--answers", required=True, help="answer records, one JSON object per line")
    b.add_argument("--out", help="pair file to write (default: <votes dir>/pairs.jsonl)")
    b.add_argument("--candidates", required=True, help="comma-separated candidate model ids")
    b.add_argument("--baseline", default=BASELINE_ID)
    b.add_argument("--base", default=BASE_ID, help="untuned base model id (standing pair)")
    b.add_argument("--cloud", default="", help="comma-separated cloud model ids")
    b.add_argument("--plan", help="JSON file overriding V1_PLAN counts")
    b.add_argument("--seed", type=int, default=0)
    b.add_argument("--heldout-split", default="heldout")
    b.add_argument("--allow-in-repo", action="store_true")

    v = sub.add_parser("vote", help="run one blind voting session in the terminal")
    v.add_argument("--pairs", help="pair file (default: <votes dir>/pairs.jsonl)")
    v.add_argument("--votes", help="vote file, appended (default: <votes dir>/votes.jsonl)")
    v.add_argument("--baseline", default=BASELINE_ID)
    v.add_argument("--seed", type=int)
    v.add_argument("--max-pairs", type=int, default=90)
    v.add_argument("--max-minutes", type=float, default=30.0)
    v.add_argument("--allow-in-repo", action="store_true")

    s = sub.add_parser("summary", help="print pref_raw, pref_style_ctrl and the other statistics")
    s.add_argument("--pairs")
    s.add_argument("--votes")
    s.add_argument("--baseline", default=BASELINE_ID)
    s.add_argument("--base", default=BASE_ID)

    args = ap.parse_args(argv)
    try:
        if args.cmd == "build":
            out = check_output_path(_default_path("pairs.jsonl", args.out),
                                    allow_in_repo=args.allow_in_repo)
            plan = dict(V1_PLAN)
            if args.plan:
                plan.update(json.loads(Path(args.plan).read_text(encoding="utf-8")))
            pairs = build_pairs(read_jsonl(args.answers), candidate_ids=_csv(args.candidates),
                                baseline_id=args.baseline, base_id=args.base,
                                cloud_ids=_csv(args.cloud), plan=plan, seed=args.seed,
                                heldout_split=args.heldout_split)
            write_jsonl(out, pairs)
            print(f"wrote {len(pairs)} pairs to {out}")
            print(json.dumps(plan_report(pairs), indent=2))
            return 0
        if args.cmd == "vote":
            pairs_p = check_output_path(_default_path("pairs.jsonl", args.pairs),
                                        allow_in_repo=args.allow_in_repo)
            votes_p = check_output_path(_default_path("votes.jsonl", args.votes),
                                        allow_in_repo=args.allow_in_repo)
            res = start_session(pairs_p, votes_p, seed=args.seed, baseline_id=args.baseline,
                                max_pairs=args.max_pairs, max_minutes=args.max_minutes)
            return 0 if res["stopped"] != "error" else 1
        pairs = read_jsonl(_default_path("pairs.jsonl", args.pairs))
        votes = read_jsonl(_default_path("votes.jsonl", args.votes))
        print(json.dumps(summarize(pairs, votes, baseline_id=args.baseline, base_id=args.base),
                         indent=2, default=str))
        return 0
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
