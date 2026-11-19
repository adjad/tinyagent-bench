"""Audio understanding beyond transcription (spec 05 §5 A7): generators and scorers.

Seven families, all seeded and parameterised so the full sets are produced by
``n`` and ``seed``; nothing downloads and nothing needs a real speech engine:

1. Conversations (1-4 voices, 30-120 s) with speaker-count, who-said and
   what-did-the-Nth-speaker-ask-for questions.
2. A diarisation error rate (DER) scorer with a collar and the optimal
   one-to-one speaker mapping (Hungarian algorithm, no scipy).
3. CREMA-D filename parsing, tone and intensity prompts and a free-text scorer.
4. 600 urgency messages, content crossed with delivery.
5. Everyday sounds: an ESC-10 loader stub and 1,000 procedural stand-in clips
   (labelled ``synthetic_stand_in``) with a hook for CC0 / CC BY recordings.
6. 300 recordings with planted facts and 1,500 questions (10 % "not stated").
7. A Big Bench Audio row loader and a rule-based scorer.

Plans are plain dicts built from ``rng_for(seed, ...)`` streams, so item ``i``
never depends on item ``j``. Durations are planned from words at
:data:`WORDS_PER_S` words per second. ``python -m edat.media.audio.understanding
counts`` prints the per-skill case counts.
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from edat.media.audio.mix import generate_noise
from edat.media.audio.tts import SpeechEngine
from edat.media.audio.wav import SAMPLE_RATE, Clip, read_wav, resample
from edat.media.common import (GIVEN_NAMES, MONTHS, WEEKDAYS, Inventor, canon_text, canonical,
                               rng_for, time_12h)
from edat.media.textdist import levenshtein

WORDS_PER_S = 2.5
TURN_GAP_S = 0.35                    # planned and rendered silence between turns
WINDOW_S = 30.0                      # A8 windowing policy
WINDOW_OVERLAP_S = 1.0
DEFAULT_VOICES = tuple(f"stub-{i:02d}" for i in range(12))
ORDINALS = ("first", "second", "third", "fourth")

N_CONVERSATIONS = 1000
N_URGENCY = 600
N_SOUNDS = 1000
N_RECORDINGS = 300
N_MEMOS, N_MEETINGS, N_TALKS = 100, 150, 50
QUESTIONS_PER_RECORDING = 5


# ============================================================ shared helpers

def windows(duration_s: float, window_s: float = WINDOW_S,
            overlap_s: float = WINDOW_OVERLAP_S) -> list[tuple[float, float]]:
    """The published A8 windows: ``window_s`` long, each starting ``window_s - overlap_s`` on."""
    if duration_s <= window_s:
        return [(0.0, round(duration_s, 3))]
    stride = window_s - overlap_s
    out, start = [], 0.0
    while True:
        end = min(start + window_s, duration_s)
        out.append((round(start, 3), round(end, 3)))
        if end >= duration_s:
            return out
        start += stride


def planned_duration_s(words: int, turns: int, gap_s: float = TURN_GAP_S) -> float:
    return words / WORDS_PER_S + gap_s * max(0, turns - 1)


def _words(text: str) -> int:
    return len(text.split())


def _fill(template: str, rng, vocab: dict) -> str:
    return template.format(**{k: rng.choice(v) for k, v in vocab.items()})


_FOODS = ("lentil soup", "flatbread", "berry crumble", "mushroom risotto", "ginger noodles",
          "roasted pumpkin", "plum jam", "cheese scones")
_THINGS = ("printer", "heating", "kettle", "shelf", "projector", "meeting room", "bike rack",
           "filing cabinet", "window blinds", "coffee machine", "supply cupboard", "noticeboard")
_HOBBIES = ("pottery", "birdwatching", "rock climbing", "chess", "gardening", "sketching",
            "long distance walking", "bread baking")
_TOPICS = ("planning", "tooling", "training", "scheduling", "onboarding", "reporting",
           "maintenance", "procurement", "documentation", "testing")
_VOCAB = {"food": _FOODS, "thing": _THINGS, "thing2": _THINGS, "hobby": _HOBBIES, "topic": _TOPICS}

_CONV_FILLER = (
    "I had a quiet weekend and mostly stayed home and cooked.",
    "The weather has been grey all week, which makes the mornings feel slow.",
    "I tried a new {food} recipe and it turned out better than I expected.",
    "The {thing} in the office has been making a strange noise again lately.",
    "Have you tried the new place that sells {food} near the station?",
    "I keep meaning to tidy up the {thing} but something always comes up.",
    "My neighbour is repainting the fence and the whole street smells of fresh paint.",
    "The train was a little late this morning but I still got here on time.",
    "I read a long article about {hobby} and now I want to give it a go.",
    "We should really plan something nice for the end of the month.",
    "I think the {thing} needs a better label so nobody mixes it up.",
    "It was surprisingly warm yesterday, so I walked home the long way round.",
    "Honestly the {thing} has been more trouble than it is worth this season.",
    "I have been getting up early to practise {hobby} before breakfast.",
    "The queue at lunch was long but the {food} was worth the wait.",
    "Everything feels a bit calmer now that the busy stretch is behind us.",
)

_REC_FILLER = (
    "Another point worth making is how the {thing} connects with the {thing2} over time.",
    "In my experience the {topic} side of things always takes longer than people expect.",
    "We spent a good while last week going over the {topic} process and where it slows down.",
    "It is easy to forget how much the {thing} affects the daily routine of the team.",
    "I would like everyone to keep the {topic} notes tidy so they are easy to find later.",
    "There was a long discussion about the {thing} and whether it still earns its place.",
    "People keep telling me the {topic} work feels rushed, so I want to look at that carefully.",
    "On reflection the {thing} was not the real problem, the handover around it was.",
    "Let me step back and describe how the {topic} work has been going so far.",
    "A few people raised concerns about the {thing} and I think those are fair.",
    "The general feeling is that the {topic} effort has improved, though not evenly everywhere.",
    "I keep coming back to the idea that the {thing2} should sit closer to the {thing}.",
    "Nothing dramatic happened this week, which in this line of work is good news.",
    "We should review the {topic} routine again once things have settled down.",
    "That is roughly where the {topic} discussion ended up, with a few loose threads left.",
    "I will keep an eye on the {thing} and mention it again if anything changes.",
)

_INTROS = ("Hi, it's {n} here.", "This is {n}.", "Hello everyone, {n} here.",
           "{n} speaking, good to be here.", "Hey, {n} here, hope you can hear me.",
           "Morning all, it's {n}.")


def _turn(speaker: str, voice: str, text: str, **extra) -> dict:
    return {"speaker": speaker, "voice": voice, "text": text, **extra}


def _clip_slice(c: Clip) -> tuple[np.ndarray, float, int]:
    """The speech part of a synthesised clip: samples, offset of its first sample, word shift."""
    a = int(round((c.speech_start_s or 0.0) * c.sample_rate))
    b = int(round((c.eos_s if c.eos_s is not None else c.duration_s) * c.sample_rate))
    return c.samples[a:max(a, b)], a / c.sample_rate, a


def render_turns(turns: Sequence[dict], engine: SpeechEngine, *, gap_s: float = TURN_GAP_S,
                 max_turns: int | None = None, meta: dict | None = None) -> Clip:
    """Concatenate turns into one :class:`Clip`; each turn keeps its own voice and timing.

    ``meta["turns"]`` lists speaker, voice, text, start_s, end_s and word timings in clip time,
    and ``meta["segments"]`` is the same as (speaker, start_s, end_s) tuples for DER.
    """
    parts: list[np.ndarray] = []
    out_turns, t = [], 0.0
    gap = np.zeros(int(round(gap_s * SAMPLE_RATE)), dtype=np.float32)
    placeholder = False
    for k, tr in enumerate(turns if max_turns is None else turns[:max_turns]):
        c = engine.synthesize(tr["text"], tr["voice"])
        x, off, _ = _clip_slice(c)
        if k:
            parts.append(gap)
            t += len(gap) / SAMPLE_RATE
        shift = t - off
        wt = [{"word": w["word"], "start_s": round(w["start_s"] + shift, 4),
               "end_s": round(w["end_s"] + shift, 4)} for w in c.meta.get("word_timings", [])]
        parts.append(x.astype(np.float32))
        out_turns.append({**{k2: v for k2, v in tr.items() if k2 != "text"}, "text": tr["text"],
                          "start_s": round(t, 4), "end_s": round(t + len(x) / SAMPLE_RATE, 4),
                          "word_timings": wt})
        t += len(x) / SAMPLE_RATE
        placeholder = placeholder or bool(c.meta.get("placeholder"))
    samples = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    segments = [(o["speaker"], o["start_s"], o["end_s"]) for o in out_turns]
    return Clip(samples, SAMPLE_RATE, speech_start_s=out_turns[0]["start_s"] if out_turns else None,
                eos_s=out_turns[-1]["end_s"] if out_turns else None,
                meta={**(meta or {}), "engine": getattr(engine, "id", ""), "turns": out_turns,
                      "segments": segments, "placeholder": placeholder})


# ============================================================ answer parsing

_NUM_WORDS = {"zero": 0, "no": 0, "one": 1, "a single": 1, "single": 1, "a lone": 1, "two": 2,
              "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10, "eleven": 11, "twelve": 12}
_NUM_RE = re.compile(r"\d+(?:\.\d+)?|\b(?:" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True))
                     + r")\b")


def parse_number(text: str) -> int | None:
    """The first number in an answer, written as digits or as a word up to twelve."""
    t = str(text).replace(",", "").casefold()
    m = _NUM_RE.search(t)
    if not m:
        return None
    tok = m.group(0)
    if tok[0].isdigit():
        f = float(tok)
        return int(f) if f == int(f) else None
    return _NUM_WORDS[tok]


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word.casefold())}(?![a-z0-9])", text.casefold()) is not None


def anls(ref: str, hyp: str, threshold: float = 0.5) -> float:
    """Normalised Levenshtein similarity of two canonical strings, 0 below ``threshold``."""
    a, b = canon_text(ref), canon_text(hyp)
    if not a and not b:
        return 1.0
    sim = 1.0 - levenshtein(list(a), list(b)) / max(len(a), len(b))
    return sim if sim >= threshold else 0.0


_ARTICLE = re.compile(r"^(?:the|a|an)\s+")


def score_phrase(expected: str, answer: str) -> float:
    """1.0 when the planted phrase appears whole in the answer, else its ANLS (field or anls)."""
    exp = _ARTICLE.sub("", canon_text(expected))
    ans = canon_text(answer)
    if exp and re.search(rf"(?<![a-z0-9]){re.escape(exp)}(?![a-z0-9])", ans):
        return 1.0
    return anls(exp, _ARTICLE.sub("", ans))


# ============================================================ 1. conversations

_MOVE_LINES = ("Heads up, the meeting moved to {weekday}.",
               "Just so you know, the meeting moved to {weekday} afternoon.",
               "Quick note, the meeting moved to {weekday}, same room.")
_ASK_LINES = ("Could you send me {phrase} before {weekday}?",
              "When you get a moment, I need {phrase} sent over by {weekday}.",
              "Please send me {phrase}, I would like it before {weekday}.")
_ASK_NOUNS = ("floor plan", "invoice", "contract", "schedule", "price list", "site report",
              "route map", "checklist", "parts list", "booking form")


def conversation_plan(index: int, seed: int = 0, voices: Sequence[str] = DEFAULT_VOICES) -> dict:
    """One conversation script: 1-4 speakers introduced by name, planted lines, planned duration.

    Speaker order is introduction order, so "the second speaker" is ``speakers[1]``. The planted
    "meeting moved" line is said by exactly one speaker and the planted request names a phrase
    unique across the whole set (its batch number is derived from ``index``).
    """
    r = rng_for(seed, "conv", index)
    inv = Inventor(r)
    n = 1 + index % 4
    names = r.sample(GIVEN_NAMES, n)
    vs = r.sample(list(voices), n)
    speakers = [{"name": nm, "voice": v, "ordinal": ORDINALS[i]} for i, (nm, v) in enumerate(zip(names, vs))]
    mover, asker = r.randrange(n), r.randrange(n)
    weekday = r.choice(WEEKDAYS[:5])
    phrase = f"the {inv.brand()} {r.choice(_ASK_NOUNS)} number {1000 + index}"
    planted = {mover: [r.choice(_MOVE_LINES).format(weekday=weekday)]}
    planted.setdefault(asker, []).append(r.choice(_ASK_LINES).format(phrase=phrase, weekday=r.choice(WEEKDAYS[:5])))
    turns = [_turn(speakers[i]["name"], speakers[i]["voice"], r.choice(_INTROS).format(n=names[i]), role="intro")
             for i in range(n)]
    body: list[tuple[int, str, str]] = []                 # speaker index, text, role
    for k, lines in planted.items():
        for ln in lines:
            body.append((k, ln, "move" if "moved" in ln else "ask"))
    target = r.uniform(40.0, 108.0)
    words = sum(_words(t["text"]) for t in turns) + sum(_words(b[1]) for b in body)
    while planned_duration_s(words, len(turns) + len(body)) < target:
        k = r.randrange(n)
        text = " ".join(_fill(r.choice(_CONV_FILLER), r, _VOCAB) for _ in range(r.randint(1, 2)))
        body.append((k, text, "filler"))
        words += _words(text)
    r.shuffle(body)
    prev = -1
    for k, text, role in body:                            # no immediate repeat of the same speaker
        if n > 1 and k == prev and role == "filler":
            k = (k + 1 + r.randrange(n - 1)) % n
        turns.append(_turn(speakers[k]["name"], speakers[k]["voice"], text, role=role))
        prev = k
    dur = planned_duration_s(sum(_words(t["text"]) for t in turns), len(turns))
    assert 30.0 <= dur <= 120.0, (index, dur)
    return {"id": f"conv-{index:04d}", "index": index, "n_speakers": n, "speakers": speakers,
            "turns": turns, "planned_duration_s": round(dur, 2),
            "needs_windowing": dur > WINDOW_S, "n_windows": len(windows(dur)),
            "mover": speakers[mover]["name"], "asker": speakers[asker]["name"],
            "asker_ordinal": ORDINALS[asker], "asked_phrase": phrase}


def conversation_questions(plan: dict) -> list[dict]:
    """The count question plus the two attribution questions for one conversation."""
    cid = plan["id"]
    return [
        {"id": f"{cid}-count", "conversation": cid, "type": "speaker_count",
         "question": "How many people speak in this recording?", "answer": plan["n_speakers"],
         "answer_kind": "number", "scoring": "exact"},
        {"id": f"{cid}-who", "conversation": cid, "type": "who_said",
         "question": "Who said the meeting moved?", "answer": plan["mover"],
         "answer_kind": "name", "scoring": "field"},
        {"id": f"{cid}-ask", "conversation": cid, "type": "asked_for",
         "question": f"What did the {plan['asker_ordinal']} speaker ask for?",
         "answer": plan["asked_phrase"], "answer_kind": "phrase", "scoring": "field_or_anls"},
    ]


def conversation_plans(n: int = N_CONVERSATIONS, seed: int = 0) -> list[dict]:
    return [conversation_plan(i, seed) for i in range(n)]


def all_conversation_questions(n: int = N_CONVERSATIONS, seed: int = 0) -> list[dict]:
    return [q for p in conversation_plans(n, seed) for q in conversation_questions(p)]


def render_conversation(plan: dict, engine: SpeechEngine, *, gap_s: float = TURN_GAP_S,
                        max_turns: int | None = None) -> Clip:
    """Render a conversation plan; the clip's ``meta["segments"]`` doubles as diarisation truth."""
    return render_turns(plan["turns"], engine, gap_s=gap_s, max_turns=max_turns,
                        meta={"plan_id": plan["id"], "n_speakers": plan["n_speakers"]})


def score_speaker_count(answer: str, expected: int) -> bool:
    return parse_number(answer) == int(expected)


def score_who_said(answer: str, expected: str, speaker_names: Iterable[str]) -> bool:
    """Right when the expected name is the only speaker name the answer mentions."""
    found = {nm for nm in speaker_names if _has_word(str(answer), nm)}
    return found == {expected}


def score_conversation_answer(question: dict, answer: str, plan: dict | None = None) -> float:
    kind = question["type"]
    if kind == "speaker_count":
        return float(score_speaker_count(answer, question["answer"]))
    if kind == "who_said":
        names = [s["name"] for s in plan["speakers"]] if plan else GIVEN_NAMES
        return float(score_who_said(answer, question["answer"], names))
    return score_phrase(question["answer"], answer)


# ============================================================ 2. diarisation error rate

def _seg(x) -> tuple[str, float, float]:
    if isinstance(x, dict):
        s = x.get("speaker", x.get("label"))
        a = x.get("start_s", x.get("start"))
        b = x.get("end_s", x.get("end"))
        if b is None and "duration" in x:
            b = a + x["duration"]
        return str(s), float(a), float(b)
    s, a, b = x
    return str(s), float(a), float(b)


def parse_rttm(text: str) -> list[tuple[str, float, float]]:
    """(speaker, start_s, end_s) from RTTM ``SPEAKER file chan start dur <NA> <NA> spk ...`` lines."""
    out = []
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 8 and f[0] == "SPEAKER":
            out.append((f[7], float(f[3]), float(f[3]) + float(f[4])))
    return out


def hungarian(cost) -> list[tuple[int, int]]:
    """Minimum-cost one-to-one assignment of rows to columns (rectangular allowed)."""
    a = np.asarray(cost, dtype=np.float64)
    if a.size == 0:
        return []
    n, m = a.shape
    if n > m:
        return [(i, j) for j, i in hungarian(a.T)]
    inf = float("inf")
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    p, way = [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [inf] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = a[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    return sorted((p[j] - 1, j - 1) for j in range(1, m + 1) if p[j])


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + 1e-9:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def der(reference: Iterable, hypothesis: Iterable, *, collar_s: float = 0.25,
        frame_s: float = 0.01, uem: tuple[float, float] | None = None) -> dict:
    """Diarisation error rate with overlap scored and a no-score collar around reference edges.

    ``(missed + false alarm + confusion) / total reference speech`` over 10 ms frames, with the
    one-to-one speaker mapping that maximises correctly attributed time (Hungarian). The collar
    skips frames within ``collar_s`` of any boundary of a reference speaker's merged segments.
    Segments are (speaker, start_s, end_s) tuples, dicts, or ``parse_rttm`` output. Times are
    seconds in the result.
    """
    ref = [_seg(x) for x in reference]
    hyp = [_seg(x) for x in hypothesis]
    t0, t1 = uem if uem else (0.0, max([b for _, _, b in ref + hyp] + [0.0]))
    nf = max(0, int(math.ceil((t1 - t0) / frame_s - 1e-9)))
    centers = t0 + (np.arange(nf) + 0.5) * frame_s
    mask = np.ones(nf, dtype=bool)

    def activity(segs):
        names = sorted({s for s, _, _ in segs})
        act = np.zeros((len(names), nf), dtype=bool)
        for s, a, b in segs:
            act[names.index(s)] |= (centers >= a) & (centers < b)
        return names, act

    rn, ra = activity(ref)
    hn, ha = activity(hyp)
    if collar_s > 0:
        for name in rn:
            for a, b in _merge([(x, y) for s, x, y in ref if s == name]):
                for edge in (a, b):
                    mask &= np.abs(centers - edge) >= collar_s - 1e-9
    ra, ha = ra[:, mask], ha[:, mask]
    nr, nh = ra.sum(axis=0), ha.sum(axis=0)
    mapping: dict[str, str] = {}
    ncorrect = np.zeros(ra.shape[1], dtype=np.int64)
    if len(rn) and len(hn):
        co = ra.astype(np.int64) @ ha.astype(np.int64).T
        for i, j in hungarian(-co):
            mapping[rn[i]] = hn[j]
            ncorrect += ra[i] & ha[j]
    missed = float(np.maximum(0, nr - nh).sum()) * frame_s
    fa = float(np.maximum(0, nh - nr).sum()) * frame_s
    conf = float((np.minimum(nr, nh) - ncorrect).sum()) * frame_s
    total = float(nr.sum()) * frame_s
    rate = (missed + fa + conf) / total if total > 0 else (0.0 if fa == 0 else float("inf"))
    return {"der": rate, "missed_s": missed, "false_alarm_s": fa, "confusion_s": conf,
            "total_s": total, "mapping": mapping, "collar_s": collar_s}


def der_corpus(pairs: Iterable[tuple[Iterable, Iterable]], **kw) -> dict:
    """Corpus DER: component seconds summed over files, then divided by total reference speech."""
    m = fa = c = tot = 0.0
    for ref, hyp in pairs:
        d = der(ref, hyp, **kw)
        m, fa, c, tot = m + d["missed_s"], fa + d["false_alarm_s"], c + d["confusion_s"], tot + d["total_s"]
    return {"der": (m + fa + c) / tot if tot else 0.0, "missed_s": m, "false_alarm_s": fa,
            "confusion_s": c, "total_s": tot}


# ============================================================ 3. tone and intensity (CREMA-D)

CREMAD_EMOTIONS = {"ANG": "anger", "DIS": "disgust", "FEA": "fear", "HAP": "happy",
                   "NEU": "neutral", "SAD": "sad"}
CREMAD_LEVELS = {"LO": "low", "MD": "medium", "HI": "high"}      # XX means unspecified
CREMAD_SENTENCES = ("IEO", "TIE", "IOM", "IWW", "TAI", "MTI", "IWL", "ITH", "DFA", "ITS", "TSI", "WSI")

TONE_PROMPT = ("Listen to the speaker. Which emotion do they express: anger, disgust, fear, "
               "happy, neutral or sad? Answer with one word.")
INTENSITY_PROMPT = ("Listen to the speaker. How intense is the emotion: low, medium or high? "
                    "Answer with one word.")

_EMOTION_SYN = {
    "ANG": ("angry", "anger", "mad", "furious", "irritated", "annoyed", "enraged", "outraged", "rage"),
    "DIS": ("disgust", "disgusted", "disgusting", "revolted", "repulsed", "revulsion", "contempt"),
    "FEA": ("fear", "fearful", "afraid", "scared", "frightened", "terrified", "anxious", "worried",
            "nervous", "panicked"),
    "HAP": ("happy", "happiness", "joy", "joyful", "cheerful", "glad", "pleased", "delighted",
            "excited", "elated"),
    "NEU": ("neutral", "calm", "flat", "emotionless", "no emotion", "unemotional", "matter of fact"),
    "SAD": ("sad", "sadness", "unhappy", "sorrow", "sorrowful", "depressed", "gloomy", "grief",
            "grieving", "miserable", "melancholy"),
}
_LEVEL_SYN = {
    "LO": ("low", "mild", "slight", "slightly", "subtle", "weak", "faint", "gentle", "light"),
    "MD": ("medium", "moderate", "mid", "middle", "average", "medium-level"),
    "HI": ("high", "strong", "intense", "extreme", "powerful", "heavy"),
}
_NEGATION = re.compile(r"(?:\bnot|\bnever|\bno|n't|\bisn't|\bwithout)\s+(?:\w+\s+){0,1}$")


def match_label(answer: str, synonyms: dict[str, Sequence[str]], *, negation: bool = True) -> str | None:
    """The single label whose synonyms the answer mentions; None when none or several match.

    Longer synonyms win over shorter ones that sit inside them, and a mention directly after
    "not" / "n't" does not count.
    """
    text = canon_text(answer)
    spans: list[tuple[int, int, str]] = []
    for label, syns in synonyms.items():
        for s in syns:
            for m in re.finditer(rf"(?<![a-z0-9]){re.escape(canon_text(s))}(?![a-z0-9])", text):
                if negation and _NEGATION.search(text[:m.start()]):
                    continue
                spans.append((m.start(), m.end(), label))
    spans.sort(key=lambda s: -(s[1] - s[0]))
    taken: list[tuple[int, int, str]] = []
    for a, b, lab in spans:
        if all(b <= c or a >= d for c, d, _ in taken):
            taken.append((a, b, lab))
    labels = {lab for _, _, lab in taken}
    return next(iter(labels)) if len(labels) == 1 else None


def parse_cremad_filename(name: str) -> dict:
    """``1001_DFA_ANG_XX.wav`` -> actor, sentence, emotion code, level code (intensity None for XX)."""
    stem = Path(str(name)).name
    stem = stem[:-4] if stem.lower().endswith(".wav") else stem
    parts = stem.split("_")
    if len(parts) != 4 or not parts[0].isdigit() or parts[2] not in CREMAD_EMOTIONS \
            or parts[3] not in (*CREMAD_LEVELS, "XX"):
        raise ValueError(f"not a CREMA-D filename: {name!r}")
    actor, sentence, emo, lvl = parts
    return {"file": Path(str(name)).name, "actor": int(actor), "sentence": sentence, "emotion": emo,
            "emotion_name": CREMAD_EMOTIONS[emo], "level": lvl,
            "intensity": CREMAD_LEVELS.get(lvl)}


def load_cremad(source: str | Path | Iterable[str]) -> list[dict]:
    """Items from a folder of ``*.wav`` or a list of filenames. No download; unreadable names raise."""
    if isinstance(source, (str, Path)) and Path(source).is_dir():
        root = Path(source)
        names = sorted(p.name for p in root.glob("*.wav"))
        paths = {n: str(root / n) for n in names}
    else:
        names = list(source)
        paths = {Path(n).name: str(n) for n in names}
    out = []
    for n in names:
        item = parse_cremad_filename(n)
        item["id"] = f"cremad-{Path(n).stem}"
        item["path"] = paths[Path(n).name]
        out.append(item)
    return out


def score_tone(answer: str, expected_code: str) -> bool:
    return match_label(answer, _EMOTION_SYN) == expected_code


def score_intensity(answer: str, expected_level: str) -> bool:
    """``expected_level`` is LO, MD or HI (or low/medium/high)."""
    exp = {v: k for k, v in CREMAD_LEVELS.items()}.get(expected_level, expected_level)
    return match_label(answer, _LEVEL_SYN) == exp


def score_cremad(rows: Iterable[tuple[dict, str, str | None]]) -> dict:
    """``(item, tone_answer, intensity_answer)`` rows -> tone accuracy and intensity accuracy.

    Intensity is scored only on clips whose level is not XX (the graded-intensity sentence).
    """
    tone = inten = 0
    nt = ni = 0
    for item, ta, ia in rows:
        nt += 1
        tone += score_tone(ta, item["emotion"])
        if item["level"] != "XX" and ia is not None:
            ni += 1
            inten += score_intensity(ia, item["level"])
    return {"tone_accuracy": tone / nt if nt else 0.0, "intensity_accuracy": inten / ni if ni else 0.0,
            "n_tone": nt, "n_intensity": ni}


# ============================================================ 4. urgency

URGENT_TEMPLATES = (
    "{name}, the {room} is flooding and water is reaching the sockets, please come right now.",
    "{name}, call me back immediately, the {device} caught fire and we need help.",
    "This is urgent {name}, the {device} has failed and customers cannot check out, I need you now.",
    "{name}, please drop everything, the delivery to {place} must leave within the hour.",
    "{name}, my flight to {place} was cancelled and I need you to rebook me before the desk closes.",
    "Emergency, {name}, the door to the {room} is jammed and someone is stuck inside.",
    "{name}, the {device} is leaking gas, evacuate the {room} and ring me straight away.",
    "{name}, the deadline for the {client} filing is in twenty minutes and nothing has been signed.",
    "I need you right now {name}, the {client} server is down and everything is stopped.",
    "{name}, there has been an accident outside the {room}, please come as fast as you can.",
)
ROUTINE_TEMPLATES = (
    "Hi {name}, whenever you get a chance, could you pick up some {product} this week.",
    "{name}, no rush at all, I was thinking we could look at the {client} notes next month.",
    "Just a reminder {name}, the {room} plants need watering sometime before the weekend.",
    "{name}, when you are next passing {place}, a postcard would be nice, nothing special.",
    "Hello {name}, I wanted to let you know the {device} is due its yearly service eventually.",
    "{name}, whenever suits you, could we find a time to chat about the {client} newsletter.",
    "Hi {name}, it is not pressing, but the {room} could use a tidy some time soon.",
    "{name}, I am just checking if you fancy a coffee at {place} later in the month.",
    "Morning {name}, a small note, the {product} order can wait until next week's run.",
    "{name}, this can wait, but I would love your thoughts on the {client} poster when you can.",
)
_U_VOCAB = {"room": ("kitchen", "basement", "workshop", "server room", "lobby", "garage", "studio"),
            "device": ("furnace", "payment terminal", "boiler", "generator", "oven", "heater"),
            "place": ("Marrowdale", "Quillton", "Ostervale", "Brindlemoor", "Lunhaven", "Rosmere"),
            "client": ("Tavora", "Quilix", "Brenora", "Corvent", "Lumura", "Pellora"),
            "product": ("oat milk", "green tea", "lentils", "rye bread", "apples", "honey")}
DELIVERY = {"calm": {"rate": 0.9, "pitch_semitones": 0.0},
            "rushed": {"rate": 1.25, "pitch_semitones": 2.0}}
URGENCY_PROMPT = "Is this message urgent or routine? Answer with one word: urgent or routine."


def urgency_plan(n: int = N_URGENCY, seed: int = 0, voices: Sequence[str] = DEFAULT_VOICES) -> list[dict]:
    """Content (urgent/routine) crossed with delivery (calm/rushed), balanced in every block of four."""
    out = []
    for i in range(n):
        r = rng_for(seed, "urgency", i)
        content = ("urgent", "routine")[i % 2]
        delivery = ("calm", "rushed")[(i // 2) % 2]
        inv = Inventor(r)
        tpl = r.choice(URGENT_TEMPLATES if content == "urgent" else ROUTINE_TEMPLATES)
        text = tpl.format(name=inv.given(), **{k: r.choice(v) for k, v in _U_VOCAB.items()})
        out.append({"id": f"urg-{i:04d}", "text": text, "content": content, "delivery": delivery,
                    "delivery_label": "urgent" if delivery == "rushed" else "routine",
                    "conflict": (content == "urgent") != (delivery == "rushed"),
                    "voice": voices[i % len(voices)], **DELIVERY[delivery]})
    return out


def render_urgency(item: dict, engine: SpeechEngine) -> Clip:
    c = engine.synthesize(item["text"], item["voice"], rate=item["rate"],
                          pitch_semitones=item["pitch_semitones"])
    return c.copy(urgency_id=item["id"], content=item["content"], delivery=item["delivery"])


def parse_urgency(answer: str) -> str | None:
    """urgent / routine from a free answer; "not urgent" and "non-urgent" are routine."""
    t = canon_text(answer)
    t = re.sub(r"\b(?:not|non|isn't|un)[\s-]*(?:urgent|time[\s-]sensitive|pressing)", " routine ", t)
    routine = re.search(r"\b(?:routine|casual|ordinary|normal|low priority|can wait)\b", t)
    urgent = re.search(r"\b(?:urgent|urgently|emergency|time[\s-]sensitive|critical|immediately|"
                       r"high priority)\b", t)
    if bool(routine) == bool(urgent):
        return None
    return "urgent" if urgent else "routine"


def score_urgency(rows: Iterable[tuple[dict, str]]) -> dict:
    """``(plan item, answer)`` rows.

    ``accuracy`` is against the content label over every item. The conflict cells (urgent words
    delivered calmly, routine words delivered in a rush) separate the two drivers:
    ``content_driven`` is the share of conflict answers matching the content label and
    ``delivery_driven`` the share matching the delivery label (they sum to 1 minus unparsed).
    ``agree_accuracy`` covers the cells where content and delivery point the same way.
    """
    n = right = 0
    conf = conf_content = conf_delivery = agree = agree_right = 0
    cells: dict[str, list[int]] = {}
    for item, ans in rows:
        p = parse_urgency(ans)
        n += 1
        ok = p == item["content"]
        right += ok
        cell = cells.setdefault(f"{item['content']}/{item['delivery']}", [0, 0])
        cell[0] += 1
        cell[1] += ok
        if item["conflict"]:
            conf += 1
            conf_content += ok
            conf_delivery += p == item["delivery_label"]
        else:
            agree += 1
            agree_right += ok
    div = lambda a, b: a / b if b else 0.0
    return {"n": n, "accuracy": div(right, n), "content_driven": div(conf_content, conf),
            "delivery_driven": div(conf_delivery, conf), "agree_accuracy": div(agree_right, agree),
            "n_conflict": conf, "by_cell": {k: div(v[1], v[0]) for k, v in sorted(cells.items())}}


# ============================================================ 5. everyday sounds

SOUND_CLASSES = ("doorbell", "smoke_alarm", "baby_crying", "glass_breaking", "kettle",
                 "phone_ringing", "knocking", "dog_barking", "running_water", "car_horn")
SOUND_TIERS = {"0db": 0.0, "-10db": -10.0, "-20db": -20.0, "low": -30.0}
AUDIBLE_TIERS = ("0db", "-10db", "-20db")
ROOM_TONES = ("fan", "pink", "brown")
ROOM_TONE_RMS = 0.02                 # about -34 dBFS
SOUND_CLIP_S = 8.0
SOUND_PROMPT = ("What household sound is in this recording: doorbell, smoke alarm, baby crying, "
                "glass breaking, kettle, phone ringing, knocking, dog barking, running water or car "
                "horn? If you hear nothing but room tone, say none.")
SYNTHETIC_STAND_IN = "synthetic_stand_in"

SOUND_SYNONYMS: dict[str, tuple[str, ...]] = {
    "doorbell": ("doorbell", "door bell", "ding dong", "door chime", "chime"),
    "smoke_alarm": ("smoke alarm", "smoke detector", "fire alarm", "alarm"),
    "baby_crying": ("baby crying", "crying baby", "baby cry", "baby", "infant", "child crying",
                    "crying", "wailing"),
    "glass_breaking": ("glass breaking", "breaking glass", "broken glass", "glass smash", "shattering",
                       "shattered glass", "glass shatter", "glass"),
    "kettle": ("kettle", "whistling kettle", "boiling water", "whistle"),
    "phone_ringing": ("phone ringing", "telephone", "ringtone", "phone ring", "phone", "ringing"),
    "knocking": ("knocking", "knock", "door knock", "tapping", "rapping"),
    "dog_barking": ("dog barking", "dog", "barking", "bark", "woof"),
    "running_water": ("running water", "water running", "tap", "faucet", "stream", "flowing water",
                      "water"),
    "car_horn": ("car horn", "horn", "honk", "honking"),
}

ESC10_SYNONYMS: dict[str, tuple[str, ...]] = {
    "dog": ("dog", "barking", "bark"),
    "rain": ("rain", "raining", "rainfall", "drizzle"),
    "sea_waves": ("sea waves", "waves", "ocean", "sea", "surf"),
    "crying_baby": ("crying baby", "baby crying", "baby", "infant", "crying"),
    "clock_tick": ("clock tick", "ticking", "clock", "tick tock", "tick"),
    "person_sneeze": ("sneeze", "sneezing", "achoo"),
    "helicopter": ("helicopter", "chopper", "rotor"),
    "chainsaw": ("chainsaw", "chain saw", "saw"),
    "rooster": ("rooster", "cockerel", "cock", "crowing", "crow"),
    "fire_crackling": ("fire crackling", "crackling fire", "crackling", "fire", "campfire", "bonfire"),
}
_NONE_RE = re.compile(r"\b(?:none|nothing|no sound|no event|only room tone|only noise|just noise|"
                      r"just room tone|silence|silent|cannot hear|can't hear|do not hear|don't hear)\b")


def score_sound(answer: str, expected: str, vocab: dict[str, Sequence[str]] | None = None) -> bool:
    return match_label(answer, vocab or SOUND_SYNONYMS) == expected


def asserts_sound(answer: str) -> bool:
    """True when the answer claims some sound is there (does not say none / nothing)."""
    return not _NONE_RE.search(canon_text(answer))


def load_esc10(root: str | Path) -> list[dict]:
    """ESC-10 rows from ``<root>/meta/esc50.csv`` (rows whose ``esc10`` is True). No download."""
    root = Path(root)
    out = []
    with open(root / "meta" / "esc50.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if str(row.get("esc10", "")).strip().lower() != "true":
                continue
            out.append({"id": f"esc10-{Path(row['filename']).stem}", "path": str(root / "audio" / row["filename"]),
                        "class": row["category"], "fold": int(row["fold"]), "target": int(row["target"]),
                        "source": "ESC-10", "licence": "CC BY (per clip, see ESC-50 repository)"})
    classes = {r["class"] for r in out}
    if out and len(classes) != 10:
        raise ValueError(f"ESC-10 should have 10 classes, found {len(classes)}")
    return out


# ---- procedural stand-ins -------------------------------------------------

def _tvec(sec: float) -> np.ndarray:
    return np.arange(int(round(sec * SAMPLE_RATE))) / SAMPLE_RATE


def _bandpass(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SAMPLE_RATE)
    spec[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(spec, len(x))


def _place(buf: np.ndarray, sig: np.ndarray, at_s: float) -> None:
    a = int(round(at_s * SAMPLE_RATE))
    if a < len(buf):
        seg = sig[: len(buf) - a]
        buf[a:a + len(seg)] += seg


def _edge(n: int, ms: float = 8.0) -> np.ndarray:
    k = max(1, min(n // 2, int(ms / 1000 * SAMPLE_RATE)))
    e = np.ones(n)
    e[:k] = np.linspace(0, 1, k)
    e[n - k:] = np.linspace(1, 0, k)
    return e


def _event(cls: str, g: np.random.Generator) -> np.ndarray:
    """A procedural stand-in for ``cls`` (float64, arbitrary level, 1 to 4.5 s)."""
    sr = SAMPLE_RATE
    if cls == "doorbell":                                   # two-tone chime, decaying partials
        f1 = float(g.choice((587.0, 659.0, 698.0)))
        f2, buf = f1 * 0.794, np.zeros(int(2.4 * sr))
        for f, at in ((f1, 0.0), (f2, 0.65 + g.uniform(0, 0.1))):
            t = _tvec(1.3)
            tone = sum(a * np.sin(2 * np.pi * f * k * t) for k, a in ((1, 1.0), (2, 0.35), (3, 0.12)))
            _place(buf, tone * np.exp(-t / 0.35), at)
        return buf
    if cls == "smoke_alarm":                                # 3 kHz beeps, temporal-3: 3 beeps then a pause
        f = g.uniform(2900, 3100)
        beep = np.sin(2 * np.pi * f * _tvec(0.5)) * _edge(int(0.5 * sr))
        buf = np.zeros(int(2.5 * sr))
        for k in range(3):
            _place(buf, beep, k * 1.0)
        return buf
    if cls == "baby_crying":                                # pitched harmonic cry contour with vibrato
        buf = np.zeros(int(3.6 * sr))
        at = 0.1
        for _ in range(int(g.integers(2, 4))):
            d = g.uniform(0.7, 1.0)
            t = _tvec(d)
            u = t / d
            f0 = g.uniform(330, 420) + 160 * np.sin(np.pi * u) + 8 * np.sin(2 * np.pi * 6 * t)
            ph = 2 * np.pi * np.cumsum(f0) / sr
            sig = sum(np.sin(k * ph) / k ** 0.8 for k in range(1, 9))
            _place(buf, sig * np.sin(np.pi * u) ** 0.5, at)
            at += d + g.uniform(0.3, 0.5)
        return buf[: int(min(len(buf), (at + 0.1) * sr))]
    if cls == "glass_breaking":                             # noisy transient, ringing partials, tinkles
        buf = np.zeros(int(2.2 * sr))
        t = _tvec(0.15)
        _place(buf, _bandpass(g.standard_normal(len(t)), 1500, 7500) * np.exp(-t / 0.03) * 3.0, 0.0)
        for at, scale in [(0.0, 1.0)] + [(g.uniform(0.2, 1.7), g.uniform(0.15, 0.5)) for _ in range(5)]:
            tt = _tvec(0.7)
            ring = sum(np.sin(2 * np.pi * g.uniform(2000, 9000) * tt) * np.exp(-tt / g.uniform(0.08, 0.3))
                       for _ in range(7))
            _place(buf, ring * scale, at)
        return buf
    if cls == "kettle":                                     # rising whistle over steam noise
        t = _tvec(3.5)
        f = 1500 + 1500 * (1 - np.exp(-t / 0.9)) + 15 * np.sin(2 * np.pi * 5 * t)
        whistle = np.sin(2 * np.pi * np.cumsum(f) / sr) + 0.3 * np.sin(4 * np.pi * np.cumsum(f) / sr)
        steam = _bandpass(g.standard_normal(len(t)), 2000, 6000) * 0.5
        return (whistle + steam) * np.clip(t / 0.6, 0, 1)
    if cls == "phone_ringing":                              # dual-tone ring, warble cadence
        buf = np.zeros(int(3.0 * sr))
        t = _tvec(0.4)
        burst = (np.sin(2 * np.pi * 440 * t) + np.sin(2 * np.pi * 480 * t)) * (0.6 + 0.4 * np.sin(2 * np.pi * 20 * t))
        burst = burst * _edge(len(t))
        for ring in range(2):
            for k in range(2):
                _place(buf, burst, ring * 1.9 + k * 0.6)
        return buf
    if cls == "knocking":                                   # impulse train of damped thumps
        buf = np.zeros(int(2.0 * sr))
        t = _tvec(0.12)
        f = g.uniform(150, 260)
        at = 0.1
        for _ in range(int(g.integers(3, 6))):
            knock = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.025)
            knock += _bandpass(g.standard_normal(len(t)), 100, 1200) * np.exp(-t / 0.01) * 0.8
            _place(buf, knock * g.uniform(0.7, 1.0), at)
            at += g.uniform(0.18, 0.32)
        return buf[: int(min(len(buf), (at + 0.2) * sr))]
    if cls == "dog_barking":                                # harmonic bursts shaped by two formants
        buf = np.zeros(int(3.2 * sr))
        at = 0.1
        for _ in range(int(g.integers(2, 5))):
            d = g.uniform(0.18, 0.26)
            t = _tvec(d)
            f0 = g.uniform(380, 560) * (1.0 - 0.25 * t / d)
            ph = 2 * np.pi * np.cumsum(f0) / sr
            sig = np.zeros(len(t))
            for k in range(1, 22):
                fk = k * float(f0.mean())
                amp = np.exp(-((fk - 700) / 250) ** 2) + 0.6 * np.exp(-((fk - 1800) / 400) ** 2)
                sig += amp * np.sin(k * ph)
            _place(buf, sig * np.minimum(t / 0.01, 1) * np.exp(-t / 0.09), at)
            at += d + g.uniform(0.25, 0.5)
        return buf[: int(min(len(buf), (at + 0.1) * sr))]
    if cls == "running_water":                              # band-limited noise with slow modulation
        n = int(3.5 * sr)
        x = _bandpass(g.standard_normal(n), 800, 7000)
        slow = _bandpass(g.standard_normal(n), 0.5, 20)
        slow = slow / (np.abs(slow).max() + 1e-12)
        return x * (1 + 0.35 * slow)
    if cls == "car_horn":                                   # two-tone horn with clipped harmonics
        buf = np.zeros(int(3.0 * sr))
        at = 0.1
        for _ in range(int(g.integers(1, 3))):
            d = g.uniform(0.5, 1.1)
            t = _tvec(d)
            f = g.uniform(390, 420)
            sig = np.tanh(2.2 * (np.sin(2 * np.pi * f * t) + np.sin(2 * np.pi * f * 1.25 * t)))
            _place(buf, sig * _edge(len(t), 15), at)
            at += d + 0.3
        return buf[: int(min(len(buf), (at - 0.2) * sr))]
    raise ValueError(f"unknown sound class {cls!r}")


def sound_plan(n: int = N_SOUNDS, seed: int = 0) -> list[dict]:
    """Class, level tier and room tone for each generated clip: 100 per class, 25 per class and tier."""
    out = []
    for i in range(n):
        r = rng_for(seed, "sound", i)
        cls = SOUND_CLASSES[i % 10]
        tier = tuple(SOUND_TIERS)[(i // 10) % 4]
        out.append({"id": f"snd-{i:04d}", "index": i, "class": cls, "tier": tier,
                    "level_db": SOUND_TIERS[tier], "room_tone": ROOM_TONES[(i // 40) % 3],
                    "seed": r.getrandbits(32), "onset_frac": r.random(), "source": SYNTHETIC_STAND_IN})
    return out


_LICENCE_OK = re.compile(r"^(?:CC0(?:-1\.0)?|CC-BY(?:-\d\.\d)?)$")


@dataclass
class SoundLibrary:
    """Recorded CC0 / CC BY events from a folder: ``name.wav`` plus ``name.json`` with the licence."""

    by_class: dict[str, list[dict]] = field(default_factory=dict)
    rejected: list[tuple[str, str]] = field(default_factory=list)

    def pick(self, cls: str, index: int) -> dict | None:
        files = self.by_class.get(cls)
        return files[index % len(files)] if files else None


def load_sound_library(folder: str | Path) -> SoundLibrary:
    """Index a folder of recorded events. Files without a sidecar JSON naming ``class`` and a CC0 /
    CC BY ``licence`` are rejected (listed in ``rejected``), never used."""
    lib = SoundLibrary()
    for wav in sorted(Path(folder).glob("*.wav")):
        side = wav.with_suffix(".json")
        if not side.exists():
            lib.rejected.append((wav.name, "no licence JSON"))
            continue
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
        except ValueError:
            lib.rejected.append((wav.name, "unreadable licence JSON"))
            continue
        lic = str(meta.get("licence", "")).strip().upper().replace(" ", "-").replace("_", "-")
        if meta.get("class") not in SOUND_CLASSES or not _LICENCE_OK.match(lic):
            lib.rejected.append((wav.name, f"class or licence not accepted: {meta.get('class')!r}, {meta.get('licence')!r}"))
            continue
        lib.by_class.setdefault(meta["class"], []).append(
            {"path": str(wav), "licence": meta["licence"], "attribution": meta.get("attribution", ""),
             "source_url": meta.get("source_url", "")})
    return lib


def render_sound(item: dict, *, library: SoundLibrary | None = None, clip_s: float = SOUND_CLIP_S) -> Clip:
    """One generated clip: room tone plus the event ``level_db`` relative to the room tone's RMS.

    The event RMS is measured over the event's own duration. When ``library`` holds a recording of
    the class, it replaces the procedural stand-in (``source`` becomes ``recorded`` with its
    licence); otherwise ``source`` is ``synthetic_stand_in``.
    """
    g = np.random.default_rng(item["seed"])
    room = generate_noise(item["room_tone"], clip_s, seed=item["seed"] & 0x7FFFFFFF).astype(np.float64)
    room *= ROOM_TONE_RMS / (np.sqrt(np.mean(room ** 2)) + 1e-12)
    rec = library.pick(item["class"], item["index"]) if library else None
    source, licence = SYNTHETIC_STAND_IN, "generated (no third-party material)"
    if rec:
        x, sr = read_wav(Path(rec["path"]).read_bytes())
        ev = resample(x, sr)[: int(4.5 * SAMPLE_RATE)].astype(np.float64)
        source, licence = "recorded", rec["licence"]
    else:
        ev = _event(item["class"], g)
    ev = ev / (np.sqrt(np.mean(ev ** 2)) + 1e-12)
    ev *= ROOM_TONE_RMS * 10 ** (item["level_db"] / 20)
    onset = 0.5 + item["onset_frac"] * max(0.0, clip_s - len(ev) / SAMPLE_RATE - 1.0)
    mix = room.copy()
    _place(mix, ev, onset)
    peak = float(np.max(np.abs(mix)))
    if peak > 0.95:
        mix *= 0.95 / peak
    return Clip(mix.astype(np.float32), SAMPLE_RATE,
                meta={"id": item["id"], "label": item["class"], "tier": item["tier"],
                      "level_db": item["level_db"], "room_tone": item["room_tone"],
                      "onset_s": round(onset, 3), "event_s": round(len(ev) / SAMPLE_RATE, 3),
                      "source": source, "licence": licence,
                      "synthetic_stand_in": source == SYNTHETIC_STAND_IN})


def score_sounds(rows: Iterable[tuple[dict, str]]) -> dict:
    """``(plan item, answer)`` rows.

    ``class_accuracy`` covers the audible tiers (0, -10, -20 dB); ``low_level_detection_rate`` is
    the share of ``low`` tier clips whose answer names the right class; ``low_level_asserted_rate``
    is the share where the answer claims some sound (a diagnostic: a model that always says yes
    scores 1 there but not on detection).
    """
    aud = [0, 0]
    low = [0, 0, 0]
    tiers: dict[str, list[int]] = {}
    for item, ans in rows:
        ok = score_sound(ans, item["class"])
        t = tiers.setdefault(item["tier"], [0, 0])
        t[0] += 1
        t[1] += ok
        if item["tier"] in AUDIBLE_TIERS:
            aud[0] += 1
            aud[1] += ok
        elif item["tier"] == "low":
            low[0] += 1
            low[1] += ok
            low[2] += asserts_sound(ans)
    div = lambda a, b: a / b if b else 0.0
    return {"class_accuracy": div(aud[1], aud[0]), "n_audible": aud[0],
            "low_level_detection_rate": div(low[1], low[0]), "low_level_asserted_rate": div(low[2], low[0]),
            "n_low": low[0], "by_tier": {k: div(v[1], v[0]) for k, v in tiers.items()}}


# ============================================================ 6. questions about a recording

ABSTAIN = "not stated"
_ABSTAIN_RE = re.compile(r"\b(?:not|never)\s+(?:been\s+|explicitly\s+|actually\s+|clearly\s+)*"
                         r"(?:stated|mentioned|said)\b|\b(?:isn't|wasn't|hasn't\s+been|weren't|aren't)\s+"
                         r"(?:explicitly\s+|actually\s+)?(?:stated|mentioned|said)\b|\bnot\s+stated\b")
_THINGS_NEEDED = ("chairs", "laptops", "banners", "tables", "badges", "boxes", "tents", "lamps")


def _unique_brands(inv: Inventor, k: int) -> list[str]:
    out: list[str] = []
    while len(out) < k:
        b = inv.brand()
        if b not in out:
            out.append(b)
    return out


def _fact(kind_id: str, p: str, r, inv: Inventor, taken: set[str]) -> dict:
    """One planted fact for project ``p``: its sentence, its question and its canonical answer."""
    def fresh_person() -> str:
        while True:
            who = inv.person()
            if who not in taken:
                taken.add(who)
                return who

    if kind_id == "deadline":
        d = inv.day(date(2027, 1, 4), 300)
        txt = f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
        return {"type": kind_id, "kind": "date", "project": p, "sentence": f"The {p} launch is set for {txt}.",
                "question": f"When is the {p} launch set for?", "answer": d.isoformat(), "display": txt}
    if kind_id == "review_time":
        hhmm = inv.clock(8, 17, 15)
        return {"type": kind_id, "kind": "time", "project": p,
                "sentence": f"The {p} review starts at {time_12h(hhmm)}.",
                "question": f"What time does the {p} review start?", "answer": hhmm, "display": time_12h(hhmm)}
    if kind_id == "budget":
        amt = r.randrange(500, 90000, 50)
        return {"type": kind_id, "kind": "money", "project": p,
                "sentence": f"The budget for {p} is {amt:,} dollars.",
                "question": f"What is the budget for {p}?", "answer": f"{amt:.2f} USD", "display": f"${amt:,}"}
    if kind_id == "owner":
        who = fresh_person()
        return {"type": kind_id, "kind": "text", "project": p, "sentence": f"{who} owns the {p} rollout.",
                "question": f"Who owns the {p} rollout?", "answer": who, "display": who}
    if kind_id == "decision":
        vendor = inv.merchant()
        return {"type": kind_id, "kind": "text", "project": p,
                "sentence": f"We decided to go with {vendor} for the {p} packaging.",
                "question": f"Which vendor was chosen for the {p} packaging?", "answer": vendor, "display": vendor}
    if kind_id == "count":
        n, th = r.randint(3, 40), r.choice(_THINGS_NEEDED)
        return {"type": kind_id, "kind": "number", "project": p, "sentence": f"We need {n} {th} for {p}.",
                "question": f"How many {th} are needed for {p}?", "answer": str(n), "display": str(n)}
    if kind_id == "action":
        who = fresh_person()
        return {"type": kind_id, "kind": "text", "project": p,
                "sentence": f"{who} will send the signed contract to {p} Grocers.",
                "question": f"Who will send the signed contract to {p} Grocers?", "answer": who, "display": who}
    raise ValueError(kind_id)


FACT_TYPES = ("deadline", "review_time", "budget", "owner", "decision", "count", "action")


def recording_kind(index: int) -> str:
    return "memo" if index < N_MEMOS else "meeting" if index < N_MEMOS + N_MEETINGS else "talk"


def recording_plan(index: int, seed: int = 0, voices: Sequence[str] = DEFAULT_VOICES) -> dict:
    """Facts, questions, speakers and planned duration for one recording (no script text yet).

    Memos 36-165 s, one speaker; meetings 310-880 s with 2-4 speakers; talks 1,210-1,780 s with one
    (planned inside 30-180 s, 300-900 s and 1,200-1,800 s with room for the script's rounding).
    Every other recording (150 of 300) carries one "not stated" question, which makes 150 of the
    1,500 questions (10 %). ``needs_windowing`` is true when the recording is longer than 30 s.
    """
    kind = recording_kind(index)
    r = rng_for(seed, "rec", index)
    inv = Inventor(r)
    lo, hi = {"memo": (36, 165), "meeting": (310, 880), "talk": (1210, 1780)}[kind]
    target = r.uniform(lo, hi)
    n_sp = {"memo": 1, "talk": 1}.get(kind) or 2 + index % 3
    names = r.sample(GIVEN_NAMES, n_sp)
    speakers = [{"name": nm, "voice": v} for nm, v in zip(names, r.sample(list(voices), n_sp))]
    n_facts = 5 if kind == "memo" else 7
    types = r.sample(FACT_TYPES, n_facts)
    projects = _unique_brands(inv, n_facts + 1)
    taken: set[str] = set(names)
    facts = [_fact(t, projects[i], r, inv, taken) for i, t in enumerate(types)]
    not_stated = index % 2 == 0
    asked = r.sample(range(n_facts), QUESTIONS_PER_RECORDING - (1 if not_stated else 0))
    qs = [{"id": f"rec-{index:03d}-q{k}", "recording": f"rec-{index:03d}", "question": facts[i]["question"],
           "answer": facts[i]["answer"], "answer_kind": facts[i]["kind"], "stated": True, "fact": i}
          for k, i in enumerate(asked)]
    if not_stated:
        ghost = _fact(r.choice(types), projects[n_facts], r, inv, set(taken))     # project never spoken
        qs.append({"id": f"rec-{index:03d}-q{len(qs)}", "recording": f"rec-{index:03d}",
                   "question": ghost["question"], "answer": ABSTAIN, "answer_kind": ghost["kind"],
                   "stated": False, "fact": None})
    r.shuffle(qs)
    return {"id": f"rec-{index:03d}", "index": index, "recording_kind": kind, "speakers": speakers,
            "n_speakers": n_sp, "facts": facts, "questions": qs, "target_words": int(target * WORDS_PER_S),
            "planned_duration_s": round(target, 1), "needs_windowing": round(target, 1) > WINDOW_S,
            "n_windows": len(windows(round(target, 1)))}


def recording_plans(n: int = N_RECORDINGS, seed: int = 0) -> list[dict]:
    return [recording_plan(i, seed) for i in range(n)]


def recording_script(plan: dict, seed: int = 0) -> dict:
    """Turn the plan into spoken turns: intros, filler talk and the planted fact sentences.

    Returns ``{"turns": [...], "words": int, "duration_s": float}`` where duration is estimated at
    :data:`WORDS_PER_S`. Facts sit at random positions across the whole recording.
    """
    r = rng_for(seed, "rec-script", plan["index"])
    sp = plan["speakers"]
    kind = plan["recording_kind"]
    if kind == "memo":
        intros = [f"Voice memo, {sp[0]['name']} here."]
    elif kind == "talk":
        intros = [f"Good morning everyone, I'm {sp[0]['name']}, and today I want to talk about "
                  f"{r.choice(_TOPICS)} and what we have learned."]
    else:
        intros = [r.choice(_INTROS).format(n=s["name"]) for s in sp]
    turns = [_turn(sp[min(i, len(sp) - 1)]["name"], sp[min(i, len(sp) - 1)]["voice"], t, role="intro")
             for i, t in enumerate(intros)]
    fact_sents = [f["sentence"] for f in plan["facts"]]
    words = sum(_words(t["text"]) for t in turns) + sum(_words(s) for s in fact_sents)
    sentences: list[tuple[str, int | None]] = []
    avg_turn = {"memo": 3, "meeting": 2, "talk": 4}[kind]
    target = plan["planned_duration_s"]
    while planned_duration_s(words, len(turns) + len(sentences) // avg_turn + 1) < target:
        s = _fill(r.choice(_REC_FILLER), r, _VOCAB)
        sentences.append((s, None))
        words += _words(s)
    for i, s in enumerate(fact_sents):
        sentences.insert(r.randint(0, len(sentences)), (s, i))
    k = 0
    while k < len(sentences):
        chunk = sentences[k:k + r.randint(max(1, avg_turn - 1), avg_turn + 1)]
        k += len(chunk)
        who = sp[r.randrange(len(sp))] if len(sp) > 1 else sp[0]
        turns.append(_turn(who["name"], who["voice"], " ".join(c[0] for c in chunk), role="body",
                           facts=[c[1] for c in chunk if c[1] is not None]))
    total = sum(_words(t["text"]) for t in turns)
    while planned_duration_s(total, len(turns)) < target:        # chunking came out short: add talk
        who = sp[r.randrange(len(sp))]
        text = _fill(r.choice(_REC_FILLER), r, _VOCAB)
        turns.append(_turn(who["name"], who["voice"], text, role="body", facts=[]))
        total += _words(text)
    return {"turns": turns, "words": total, "duration_s": round(planned_duration_s(total, len(turns)), 1)}


def render_recording(plan: dict, engine: SpeechEngine, *, script: dict | None = None,
                     max_turns: int | None = None) -> Clip:
    script = script or recording_script(plan)
    return render_turns(script["turns"], engine, max_turns=max_turns,
                        meta={"plan_id": plan["id"], "needs_windowing": plan["needs_windowing"]})


def is_abstention(answer: str) -> bool:
    """True for "not stated", "not mentioned", "not said" and their plain variants."""
    return _ABSTAIN_RE.search(canon_text(answer).replace("’", "'")) is not None


def score_recording_answer(question: dict, answer: str) -> dict:
    """Exact match after canonicalisation for stated facts; abstention for "not stated" items."""
    abstained = is_abstention(answer)
    if not question["stated"]:
        return {"correct": abstained, "abstained": abstained, "stated": False}
    if abstained:
        return {"correct": False, "abstained": True, "stated": True}
    kind, exp = question["answer_kind"], question["answer"]
    if kind == "number":
        got = parse_number(answer)
        ok = got is not None and str(got) == canonical("number", exp)
    elif kind == "text":
        got, want = canon_text(answer), canon_text(exp)
        ok = got == want or (re.search(rf"(?<![a-z0-9]){re.escape(want)}(?![a-z0-9])", got) is not None
                             and len(got.split()) <= len(want.split()) + 6)
    else:
        ok = canonical(kind, answer, default_year=int(exp[:4]) if kind == "date" else None) == canonical(kind, exp)
    return {"correct": bool(ok), "abstained": False, "stated": True}


def score_recordings(rows: Iterable[tuple[dict, str]], plans: dict[str, dict] | None = None) -> dict:
    """``(question, answer)`` rows -> accuracy on stated facts and abstention behaviour.

    ``qa_accuracy`` is stated-fact accuracy; ``abstention_rate`` the share of not-stated items
    answered with an abstention; ``false_abstention_rate`` the share of stated items refused.
    Windowed and whole-recording paths are reported separately by the caller.
    """
    st = [0, 0, 0]
    ns = [0, 0]
    for q, a in rows:
        s = score_recording_answer(q, a)
        if q["stated"]:
            st[0] += 1
            st[1] += s["correct"]
            st[2] += s["abstained"]
        else:
            ns[0] += 1
            ns[1] += s["correct"]
    div = lambda a, b: a / b if b else 0.0
    return {"qa_accuracy": div(st[1], st[0]), "false_abstention_rate": div(st[2], st[0]),
            "abstention_rate": div(ns[1], ns[0]), "hallucination_rate": 1 - div(ns[1], ns[0]) if ns[0] else 0.0,
            "n_stated": st[0], "n_not_stated": ns[0]}


# ============================================================ 7. Big Bench Audio

@dataclass(frozen=True)
class BBAItem:
    id: str
    category: str
    audio: object                  # HF audio value (dict with path/array/sampling_rate) or bytes
    transcript: str
    answer: str


def load_bba_rows(rows: Iterable[dict]) -> list[BBAItem]:
    """Rows in the HF layout: ``id, category, audio, transcript, official_answer`` (``answer`` accepted)."""
    out = []
    for i, row in enumerate(rows):
        ans = row.get("official_answer", row.get("answer"))
        if ans is None:
            raise ValueError(f"row {i} has no official answer")
        out.append(BBAItem(str(row.get("id", i)), str(row.get("category", "")), row.get("audio"),
                           str(row.get("transcript", "")), str(ans)))
    return out


_ANSWER_CUE = re.compile(r"(?:final answer|the answer is|answer is|answer:|therefore|thus|so the answer)"
                         r"\s*[:\-]?\s*", re.I)
_YES_NO = {"yes": "yes", "no": "no", "true": "yes", "false": "no", "valid": "yes", "invalid": "no",
           "correct": "yes", "incorrect": "no"}


def _tail(answer: str) -> str:
    """The part of a free answer after the last "the answer is" style cue, else all of it."""
    last = None
    for m in _ANSWER_CUE.finditer(answer):
        last = m
    return answer[last.end():] if last else answer


def _letter(answer: str) -> str | None:
    seg = _tail(answer)
    m = re.search(r"\(([A-Za-z])\)", seg) or re.search(r"\b(?:option|choice|answer)\s*(?:is\s*)?\(?([A-Za-z])\b", seg, re.I) \
        or re.match(r"\s*\(?([A-Za-z])[\).:,]?(?:\s|$)", seg)
    return m.group(1).upper() if m else None


def score_bba(answer: str, official: str) -> bool:
    """Rule-based scoring: option letter, yes/no-like, number, else short text containment.

    The official answer decides the rule. Free-form replies are read from the text after a
    "the answer is" cue when there is one; yes/no-like replies take the first such token there.
    """
    off = str(official).strip()
    if re.fullmatch(r"\(?[A-Za-z]\)?[\).:]?", off):
        got = _letter(answer)
        return got is not None and got == re.sub(r"\W", "", off).upper()
    off_c = canon_text(off)
    if off_c in _YES_NO:
        seg = canon_text(_tail(answer))
        toks = re.findall(r"\b(invalid|valid|incorrect|correct|yes|no|true|false)\b", seg)
        seg_neg = re.sub(r"\bnot\s+(valid|correct|true)\b", lambda m: {"valid": "invalid", "correct": "incorrect",
                                                                       "true": "false"}[m.group(1)], seg)
        toks = re.findall(r"\b(invalid|valid|incorrect|correct|yes|no|true|false)\b", seg_neg) or toks
        return bool(toks) and _YES_NO[toks[0]] == _YES_NO[off_c]
    if re.fullmatch(r"-?\d+(?:\.\d+)?|" + "|".join(_NUM_WORDS), off_c):
        seg = _tail(answer)
        nums = [m.group(0) for m in _NUM_RE.finditer(seg.replace(",", "").casefold())]
        if not nums:
            return False
        got = nums[-1]
        v = float(got) if got[0].isdigit() else _NUM_WORDS[got]
        want = float(off_c) if off_c[0] in "-0123456789" else _NUM_WORDS[off_c]
        return v == want
    seg = canon_text(_tail(answer))
    return seg == off_c or (re.search(rf"(?<![a-z0-9]){re.escape(off_c)}(?![a-z0-9])", seg) is not None
                            and len(seg.split()) <= len(off_c.split()) + 8)


def score_bba_rows(rows: Iterable[tuple[BBAItem, str]]) -> dict:
    n = right = 0
    cats: dict[str, list[int]] = {}
    for item, ans in rows:
        ok = score_bba(ans, item.answer)
        n += 1
        right += ok
        c = cats.setdefault(item.category, [0, 0])
        c[0] += 1
        c[1] += ok
    return {"accuracy": right / n if n else 0.0, "n": n,
            "by_category": {k: v[1] / v[0] for k, v in sorted(cats.items())}}


# ============================================================ counts and CLI

def counts() -> dict[str, int]:
    """Per-skill case counts (fixed by the spec; the generators take ``n`` to make fewer)."""
    return {"conversations": N_CONVERSATIONS, "speaker_count_questions": N_CONVERSATIONS,
            "who_said_questions": 2 * N_CONVERSATIONS, "urgency": N_URGENCY,
            "generated_sounds": N_SOUNDS, "recordings": N_RECORDINGS,
            "recording_questions": N_RECORDINGS * QUESTIONS_PER_RECORDING}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["counts"]:
        print(json.dumps(counts(), indent=2))
        return 0
    print("usage: python -m edat.media.audio.understanding counts", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
