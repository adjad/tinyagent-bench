"""A8: the length ladder, the windowing policy, seam errors and behaviour past the limit
(spec 05 section 5, A8).

* :data:`LADDER_S`: 5, 15, 30, 45, 60, 120 s, then 5, 10, 30, 60 min; 20 clips per rung.
  :func:`ladder_plan` builds each clip as a concatenation of test-clean utterances
  (ordered by a seeded shuffle) with exact seam times and reference text;
  :func:`build_long_clip` renders one from utterance clips and their texts.
* The windowing policy: 30 s windows with 1 s overlap (:func:`windows`), merged by the
  longest word overlap of up to 8 words (:func:`merge_transcripts`).
* :func:`seam_errors`: reference words dropped, and output words duplicated, near a
  window boundary, per boundary.
* :func:`classify_past_limit`: refusal, error, truncated or hallucinated_tail.
* :func:`audio_tokens_per_minute`.
"""
from __future__ import annotations

import re

import numpy as np

from edat.media.audio.mix import align_ops, derive_seed, _rng
from edat.media.audio.wav import SAMPLE_RATE, Clip, silence

LADDER_S = (5, 15, 30, 45, 60, 120, 300, 600, 1800, 3600)
CLIPS_PER_RUNG = 20
WINDOW_S = 30.0
OVERLAP_S = 1.0
MAX_MERGE_OVERLAP_WORDS = 8
MIN_MERGE_OVERLAP_WORDS = 2
SEAM_ZONE_S = 1.0
UTTERANCE_GAP_S = 0.3
TRUNCATED_COVERAGE = 0.7


# ------------------------------------------------------------------ the length ladder

def _norm_utterances(utterances) -> list[tuple[str, float]]:
    out = []
    for u in utterances:
        if isinstance(u, dict):
            out.append((u["utt_id"], float(u["duration_s"])))
        else:
            out.append((u[0], float(u[1])))
    return out


def ladder_plan(utterances, seed: int = 0, *, rungs=LADDER_S, clips_per_rung: int = CLIPS_PER_RUNG,
                gap_s: float = UTTERANCE_GAP_S) -> list[dict]:
    """Plan 10 rungs x 20 clips as concatenations of utterances.

    ``utterances`` are ``(utt_id, duration_s)`` pairs (or dicts with those keys), e.g. from
    the test-clean manifest. Each clip takes its own seeded shuffle of the utterances and
    adds the first unused one that still fits (duration + gap within the remaining time)
    until none fits, so the total lands just under the rung (within the shortest
    utterance). No utterance repeats inside a clip; clips of long rungs share utterances
    (the whole set is 5.4 h, the ladder 36 h), and the plan says which.

    Each clip: ``clip_id``, ``rung_s``, ``utterances`` (``utt_id``, ``start_s``, ``end_s``),
    ``seams_s`` (the start of every utterance after the first), ``total_s``.
    The reference text is the utterances' texts joined in order.
    """
    utts = _norm_utterances(utterances)
    out = []
    for rung in rungs:
        for k in range(clips_per_rung):
            g = _rng(derive_seed(seed, "ladder", rung, k))
            order = [utts[int(i)] for i in g.permutation(len(utts))]
            used = [False] * len(order)
            t = 0.0
            items = []
            while True:
                remaining = rung - t
                pick = None
                for idx, (uid, d) in enumerate(order):
                    if not used[idx] and d + (gap_s if items else 0.0) <= remaining + 1e-9:
                        pick = idx
                        break
                if pick is None:
                    break
                used[pick] = True
                uid, d = order[pick]
                start = t + (gap_s if items else 0.0)
                items.append({"utt_id": uid, "start_s": round(start, 4), "end_s": round(start + d, 4)})
                t = start + d
            out.append({"clip_id": f"ladder-{rung}s-{k:02d}", "rung_s": rung, "index": k,
                        "gap_s": gap_s, "utterances": items,
                        "seams_s": [it["start_s"] for it in items[1:]],
                        "total_s": round(t, 4)})
    return out


def build_long_clip(parts: list[tuple[Clip, str]], *, gap_s: float = UTTERANCE_GAP_S) -> Clip:
    """Concatenate utterance clips (with ``gap_s`` of silence between) and their texts.

    The result's ``meta`` holds ``text`` (joined reference), ``seams_s`` (start of each
    utterance after the first, in the long clip's time), ``segments`` and, when every part
    has ``word_timings``, the concatenated ``word_timings`` shifted to the new timeline.
    ``eos_s`` is the end of the last utterance's speech.
    """
    sr = parts[0][0].sample_rate if parts else SAMPLE_RATE
    chunks: list[np.ndarray] = []
    t = 0.0
    seams, segments, timings, texts = [], [], [], []
    have_timings = True
    eos = 0.0
    for i, (clip, text) in enumerate(parts):
        if i > 0:
            chunks.append(silence(gap_s, sr))
            t += gap_s
            seams.append(round(t, 4))
        # Use the speech span only: leading silence is dropped, trailing audio after eos too.
        a = int(round((clip.speech_start_s or 0.0) * sr))
        b = int(round((clip.eos_s if clip.eos_s is not None else clip.duration_s) * sr))
        seg = clip.samples[a:b]
        lead = a / sr
        wt = clip.meta.get("word_timings")
        if wt:
            timings += [{"word": w["word"], "start_s": round(w["start_s"] - lead + t, 4),
                         "end_s": round(w["end_s"] - lead + t, 4)} for w in wt]
        else:
            have_timings = False
        segments.append({"start_s": round(t, 4), "end_s": round(t + len(seg) / sr, 4), "text": text})
        chunks.append(seg)
        t += len(seg) / sr
        eos = t
        texts.append(text)
    samples = np.concatenate(chunks) if chunks else silence(0)
    meta = {"text": " ".join(texts), "seams_s": seams, "segments": segments,
            "condition": {"type": "long_form", "n_utterances": len(parts)}}
    if have_timings and timings:
        meta["word_timings"] = timings
    return Clip(samples.astype(np.float32), sr, 0.0, eos, meta)


# ------------------------------------------------------------------ windowing policy

def windows(duration_s: float, *, window_s: float = WINDOW_S, overlap_s: float = OVERLAP_S
            ) -> list[tuple[float, float]]:
    """Window ``(start, end)`` pairs: 30 s long, each starting 29 s after the last (1 s overlap).

    The last window ends at the clip's end, so it may be shorter. A clip of 30 s or less is
    one window.
    """
    if duration_s <= 0:
        return []
    step = window_s - overlap_s
    out = []
    s = 0.0
    while True:
        e = min(s + window_s, duration_s)
        out.append((round(s, 4), round(e, 4)))
        if e >= duration_s - 1e-9:
            break
        s += step
    return out


def window_boundaries(duration_s: float, **kw) -> list[float]:
    """The seam time between consecutive windows: the middle of each 1 s overlap."""
    ws = windows(duration_s, **kw)
    return [round((ws[i + 1][0] + ws[i][1]) / 2, 4) for i in range(len(ws) - 1)]


def _tok(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower()).strip("'")


def merge_with_info(texts: list[str], *, max_overlap: int = MAX_MERGE_OVERLAP_WORDS,
                    min_overlap: int = MIN_MERGE_OVERLAP_WORDS) -> tuple[list[str], list[int]]:
    """Merged words and, per join, how many words were dropped as the duplicate overlap."""
    merged: list[str] = []
    dropped: list[int] = []
    for text in texts:
        words = text.split()
        if not merged:
            merged = list(words)
            continue
        best = 0
        for k in range(min(max_overlap, len(merged), len(words)), min_overlap - 1, -1):
            if k >= 1 and [_tok(w) for w in merged[-k:]] == [_tok(w) for w in words[:k]]:
                best = k
                break
        dropped.append(best)
        merged += words[best:]
    return merged, dropped


def merge_transcripts(texts: list[str], *, max_overlap: int = MAX_MERGE_OVERLAP_WORDS,
                      min_overlap: int = MIN_MERGE_OVERLAP_WORDS) -> str:
    """Join window transcripts, dropping the duplicate at each seam.

    At each join the longest run of ``max_overlap`` (8) or fewer words that ends the text so
    far and starts the next window's text (compared after lower-casing and stripping
    punctuation) is dropped from the next window. Runs shorter than ``min_overlap`` (2) are not
    treated as overlap, so a lone repeated word ("that that") survives.
    """
    return " ".join(merge_with_info(texts, max_overlap=max_overlap, min_overlap=min_overlap)[0])


# ------------------------------------------------------------------ seam errors

def seam_errors(ref_words: list[dict], hyp_text: str, boundaries_s: list[float], *,
                zone_s: float = SEAM_ZONE_S) -> dict:
    """Words dropped or duplicated within ``zone_s`` of a window boundary, per boundary.

    ``ref_words`` are ``{"word", "start_s", "end_s"}`` for the whole reference (the stub engine's
    timings, or :func:`build_long_clip`'s). Reference and output words are lower-cased with
    punctuation removed (no number rewriting, so references and outputs must use the same
    forms) and aligned. A reference word whose midpoint lies within ``zone_s`` of a boundary
    and that the alignment deletes is *dropped*. An output word inserted by the alignment
    between reference words in a zone, and equal to a reference word within three words of
    the insertion point, is *duplicated*. ``rate`` = (dropped + duplicated) / boundaries.
    """
    ref = [_tok(w["word"]) for w in ref_words]
    keep = [i for i, r in enumerate(ref) if r]
    ref = [ref[i] for i in keep]
    rw = [ref_words[i] for i in keep]
    hyp = [t for t in (_tok(w) for w in hyp_text.split()) if t]
    mid = [(w["start_s"] + w["end_s"]) / 2 for w in rw]

    def zone(i: int) -> int | None:
        for b_idx, b in enumerate(boundaries_s):
            if abs(mid[i] - b) <= zone_s:
                return b_idx
        return None

    dropped = duplicated = 0
    ops = align_ops(ref, hyp)
    last_ref = -1
    for op, i, j in ops:
        if op in ("hit", "sub", "del"):
            last_ref = i
        if op == "del" and zone(i) is not None:
            dropped += 1
        elif op == "ins":
            lo, hi = max(0, last_ref - 2), min(len(ref), last_ref + 4)
            near = [k for k in range(lo, hi) if k < len(ref)]
            if near and any(zone(k) is not None for k in near) and hyp[j] in {ref[k] for k in near}:
                duplicated += 1
    nb = len(boundaries_s)
    return {"dropped": dropped, "duplicated": duplicated, "boundaries": nb,
            "rate": (dropped + duplicated) / nb if nb else 0.0}


# ------------------------------------------------------------------ behaviour past the limit

_REFUSAL = re.compile(r"\b(i (can ?not|can't|am unable|'m unable|won't)|unable to (process|transcribe)|"
                      r"too (long|large)|exceeds? (the )?(maximum|limit|length)|not able to (process|transcribe)|"
                      r"cannot (process|transcribe)|sorry)\b", re.I)


def classify_past_limit(ref_text: str, reply: str | None, *, error: str | None = None) -> str:
    """Label what a model did with audio past its limit: ``error``, ``refusal``,
    ``hallucinated_tail``, ``truncated`` or ``ok``.

    Rules, in order:

    1. ``error``: the call failed (``error`` is set) or there is no reply at all (None).
    2. ``refusal``: the reply matches a refusal phrase (cannot / unable / too long / exceeds the
       limit / sorry) and covers less than 30 % of the reference words, or is empty text.
    3. ``hallucinated_tail``: after the last reference word the alignment matches, the reply has
       at least max(8, 5 % of the reference length) words that are not in the reference.
    4. ``truncated``: coverage (matched words / reference words) is below 70 % and the extra
       is not hallucinated (rule 3 did not fire).
    5. ``ok`` otherwise.
    """
    if error or reply is None:
        return "error"
    ref = [t for t in (_tok(w) for w in ref_text.split()) if t]
    hyp = [t for t in (_tok(w) for w in reply.split()) if t]
    if not hyp:
        return "refusal"
    ops = align_ops(ref, hyp)
    hits = sum(1 for o in ops if o[0] == "hit")
    coverage = hits / len(ref) if ref else 1.0
    if _REFUSAL.search(reply) and coverage < 0.3:
        return "refusal"
    last = max((j for o, _, j in ops if o == "hit"), default=-1)
    vocab = set(ref)
    tail_new = sum(1 for w in hyp[last + 1:] if w not in vocab)
    if tail_new >= max(8, int(0.05 * len(ref))):
        return "hallucinated_tail"
    if coverage < TRUNCATED_COVERAGE:
        return "truncated"
    return "ok"


def audio_tokens_per_minute(prompt_tokens_with: int | None, prompt_tokens_without: int | None,
                            duration_s: float) -> float | None:
    """Prompt tokens added by the clip, per minute of audio (Gemma 4: about 1,500)."""
    if prompt_tokens_with is None or prompt_tokens_without is None or duration_s <= 0:
        return None
    return (prompt_tokens_with - prompt_tokens_without) / duration_s * 60.0
