"""Word and character error rate for transcripts (spec 05 §3.4, A1).

* **WER** = (S + D + I) / reference words, corpus level (errors summed over all
  utterances, then divided), after :mod:`edat.media.audio.normalize`.
* **CER** the same over characters of the normalised text (spaces kept as characters).
* **Insertion rate** = I / reference words, reported on its own because native
  audio LMs that invent words show there first.
* A 95 % bootstrap interval over utterances (fixed seed) for WER and CER.

Every utterance result keeps the raw and the normalised text, so results can be
re-scored when the normaliser changes.

Also here: the A5 measures that read a transcript on its own:
:func:`has_content` (any letter or digit left after removing a model's declared
"no speech" markers) and :func:`is_answer_leak`.
"""
from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass
from typing import Callable, Iterable

from edat.media.audio.normalize import EnglishNormalizer
from edat.media.textdist import ZERO, EditCounts, align

BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 20271

# Harbor's "no speech" replies (service/voice.py), plus the same in other brackets.
NO_SPEECH_MARKERS = ("[silence]", "(silence)", "[no speech]", "(no speech)", "[inaudible]",
                     "(inaudible)", "no speech", "silence", "[blank_audio]", "<no speech>")


@dataclass
class UtteranceScore:
    utt_id: str
    ref_raw: str
    hyp_raw: str
    ref_norm: str
    hyp_norm: str
    words: EditCounts
    chars: EditCounts

    @property
    def wer(self) -> float:
        return self.words.rate()

    @property
    def cer(self) -> float:
        return self.chars.rate()

    def to_json(self) -> dict:
        d = asdict(self)
        d["wer"], d["cer"] = self.wer, self.cer
        return d


@dataclass
class CorpusScore:
    n: int
    words: EditCounts
    chars: EditCounts
    wer: float
    cer: float
    insertion_rate: float
    wer_ci95: tuple[float, float] | None
    cer_ci95: tuple[float, float] | None
    normalizer: str

    def to_json(self) -> dict:
        return asdict(self)


def score_utterance(ref: str, hyp: str, *, utt_id: str = "",
                    normalizer: Callable[[str], str] | None = None) -> UtteranceScore:
    norm = normalizer or EnglishNormalizer()
    r, h = norm(ref), norm(hyp or "")
    return UtteranceScore(utt_id, ref, hyp or "", r, h, align(r.split(), h.split()),
                          align(list(r), list(h)))


def _rate(c: EditCounts) -> float:
    return c.rate()


def corpus_score(utts: list[UtteranceScore], *, normalizer_id: str = "",
                 bootstrap: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED) -> CorpusScore:
    words = sum((u.words for u in utts), ZERO)
    chars = sum((u.chars for u in utts), ZERO)
    ref_words = words.ref_len
    ins = words.insertions / ref_words if ref_words else (1.0 if words.insertions else 0.0)
    wci = cci = None
    if bootstrap and len(utts) > 1:
        rng = random.Random(seed)
        ws, cs = [], []
        for _ in range(bootstrap):
            sample = [utts[rng.randrange(len(utts))] for _ in utts]
            ws.append(_rate(sum((u.words for u in sample), ZERO)))
            cs.append(_rate(sum((u.chars for u in sample), ZERO)))
        ws.sort()
        cs.sort()
        lo, hi = int(0.025 * bootstrap), int(0.975 * bootstrap) - 1
        wci, cci = (ws[lo], ws[hi]), (cs[lo], cs[hi])
    return CorpusScore(len(utts), words, chars, words.rate(), chars.rate(), ins, wci, cci,
                       normalizer_id)


def score_corpus(pairs: Iterable[tuple[str, str, str]], *,
                 normalizer: EnglishNormalizer | None = None, bootstrap: int = BOOTSTRAP_RESAMPLES
                 ) -> tuple[CorpusScore, list[UtteranceScore]]:
    """``pairs`` of (utt_id, reference, hypothesis) -> corpus score and per-utterance scores."""
    norm = normalizer or EnglishNormalizer()
    utts = [score_utterance(r, h, utt_id=u, normalizer=norm) for u, r, h in pairs]
    return corpus_score(utts, normalizer_id=norm.id, bootstrap=bootstrap), utts


# ------------------------------------------------------------------ A5 helpers

def strip_no_speech(text: str) -> str:
    t = (text or "").strip()
    low = t.lower()
    for m in sorted(NO_SPEECH_MARKERS, key=len, reverse=True):
        low = low.replace(m, " ")
    return low.strip()


def has_content(text: str) -> bool:
    """True when any letter or digit is left after removing declared "no speech" markers (A5)."""
    return bool(re.search(r"[^\W_]", strip_no_speech(text)))


def invented_words(text: str) -> int:
    return len(strip_no_speech(text).split())


def is_answer_leak(ref: str, hyp: str, *, normalizer: EnglishNormalizer | None = None,
                   wer_threshold: float = 0.6) -> bool:
    """A5 answer detector: transcript WER above 60 % and no reference word bigram in it.

    Spec 05 §11.1 asks for "WER > 60 % and no reference overlap"; overlap is read as
    sharing no word bigram with the reference, so an answer that repeats one word of
    the question still counts as a leak. Items flagged here are spot-checked by hand.
    """
    u = score_utterance(ref, hyp, normalizer=normalizer)
    if not u.hyp_norm:
        return False
    rw, hw = u.ref_norm.split(), u.hyp_norm.split()
    rb = set(zip(rw, rw[1:]))
    hb = set(zip(hw, hw[1:]))
    return u.wer > wer_threshold and not (rb & hb)
