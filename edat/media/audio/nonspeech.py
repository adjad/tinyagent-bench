"""A5: silence, non-speech, speech-then-non-speech, commands inside noise, and answer-leak
(spec 05 section 5, A5).

Everything is a seeded plan (plain dicts) plus a renderer, so the full sets are produced
by a parameter and nothing is rendered until asked:

* :func:`nonspeech_plan` 1,000 non-speech clips of 2-30 s over eleven kinds;
* :func:`speech_then_nonspeech_plan` 1,000 test-clean utterance ids plus 10-30 s of non-speech;
* :func:`commands_in_noise_plan` 500 commands with 5-20 s of noise before and after;
* :func:`answer_leak_prompts` 500 spoken questions and instructions (invented names).

All non-speech audio is generated. Coughs and breathing are approximations (bursts of
filtered noise under envelopes) labelled ``synthetic_stand_in``; no recording is used.

Scorers: :func:`hallucination_rate`, :func:`invented_words_per_minute`, :func:`looping_run` /
:func:`looping_rate`, :func:`trailing_insertions`, :func:`answer_leak_result`.
"""
from __future__ import annotations

import re

import numpy as np

from edat.media.audio.mix import (A4_NOISE_TYPES, align_ops, derive_seed, generate_a4_noise,
                                  mix_at_snr, _filter, _rng, _unit_rms, _words)
from edat.media.audio.wav import SAMPLE_RATE, Clip, silence
from edat.media.audio.wer import has_content, invented_words, is_answer_leak

NONSPEECH_KINDS = ("silence", "room_tone", "white", "pink", "brown", "music", "keyboard",
                   "cough", "breathing", "tv_distant", "traffic")
SYNTHETIC_STAND_IN_KINDS = ("cough", "breathing")
NONSPEECH_MIN_S, NONSPEECH_MAX_S = 2.0, 30.0
NONSPEECH_COUNT = 1000
SPEECH_THEN_COUNT = 1000
TAIL_MIN_S, TAIL_MAX_S = 10.0, 30.0
COMMANDS_IN_NOISE_COUNT = 500
NOISE_PAD_MIN_S, NOISE_PAD_MAX_S = 5.0, 20.0
COMMAND_NOISE_SNR_DB = 10
ANSWER_LEAK_COUNT = 500


def _rotate(n: int, values: tuple, seed: int, label: str) -> list:
    """``n`` values dealt in turn from a seeded shuffle of ``values`` per cycle (even to within one)."""
    out: list = []
    g = _rng(derive_seed(seed, label))
    while len(out) < n:
        out += [values[int(i)] for i in g.permutation(len(values))]
    return out[:n]


# ------------------------------------------------------------------ non-speech renderer

def _events(n: int, g: np.random.Generator, every_s: tuple[float, float], make, sr: int = SAMPLE_RATE
            ) -> np.ndarray:
    x = np.zeros(n)
    t = g.uniform(*every_s) / 2
    while int(t * sr) < n:
        ev = make()
        i = int(t * sr)
        end = min(n, i + len(ev))
        x[i:end] += ev[: end - i]
        t += g.uniform(*every_s)
    return x


def _cough(g: np.random.Generator, sr: int = SAMPLE_RATE) -> np.ndarray:
    parts = []
    for _ in range(int(g.integers(1, 4))):
        ln = int(g.uniform(0.12, 0.22) * sr)
        b = _filter(g.standard_normal(ln), 250.0, 3200.0, sr)
        tt = np.arange(ln) / sr
        parts.append(b / (np.std(b) + 1e-12) * np.exp(-tt / 0.05) * np.minimum(1, tt / 0.005))
        parts.append(np.zeros(int(g.uniform(0.05, 0.12) * sr)))
    return np.concatenate(parts) * 0.3


def _breath(g: np.random.Generator, sr: int = SAMPLE_RATE) -> np.ndarray:
    ln = int(g.uniform(1.0, 1.5) * sr)
    b = _filter(g.standard_normal(ln), 200.0, 2200.0, sr)
    env = np.sin(np.pi * np.arange(ln) / ln) ** 2
    return b / (np.std(b) + 1e-12) * env * 0.04


def render_nonspeech_kind(kind: str, seconds: float, seed: int, sample_rate: int = SAMPLE_RATE
                          ) -> np.ndarray:
    """Samples for one non-speech kind (float32). ``silence`` is exact digital zeros."""
    sr = sample_rate
    n = int(round(seconds * sr))
    g = _rng(seed)
    if kind == "silence":
        return silence(seconds, sr)
    if kind == "room_tone":
        x = _unit_rms(generate_a4_noise("pink", seconds, seed=seed), 0.001)
        t = np.arange(n) / sr
        return (x + 0.0004 * np.sin(2 * np.pi * 50 * t)).astype(np.float32)
    if kind in ("white", "pink", "brown"):
        return _unit_rms(generate_a4_noise(kind, seconds, seed=seed), 0.03)
    if kind == "music":
        return _unit_rms(generate_a4_noise("music", seconds, seed=seed), 0.05)
    if kind == "keyboard":
        return _unit_rms(generate_a4_noise("keyboard", seconds, seed=seed), 0.04)
    if kind == "traffic":
        return _unit_rms(generate_a4_noise("street", seconds, seed=seed), 0.05)
    if kind == "tv_distant":
        x = generate_a4_noise("tv_speech", seconds, seed=seed).astype(np.float64)
        return _unit_rms(_filter(x, 0.0, 1500.0, sr), 0.015)
    if kind == "cough":
        floor = render_nonspeech_kind("room_tone", seconds, derive_seed(seed, "floor"), sr).astype(np.float64)
        x = _events(n, g, (2.5, 6.0), lambda: _cough(g, sr), sr)
        return (floor + x).astype(np.float32)
    if kind == "breathing":
        floor = render_nonspeech_kind("room_tone", seconds, derive_seed(seed, "floor"), sr).astype(np.float64)
        x = _events(n, g, (3.2, 4.6), lambda: _breath(g, sr), sr)
        return (floor + x).astype(np.float32)
    raise ValueError(f"unknown non-speech kind {kind!r}")


def _label(kind: str) -> str:
    return "synthetic_stand_in" if kind in SYNTHETIC_STAND_IN_KINDS else "generated"


def nonspeech_plan(seed: int = 0, n: int = NONSPEECH_COUNT) -> list[dict]:
    """1,000 non-speech items: kind rotated evenly, duration uniform in 2-30 s (0.1 s steps)."""
    kinds = _rotate(n, NONSPEECH_KINDS, seed, "ns-kind")
    g = _rng(derive_seed(seed, "ns-duration"))
    out = []
    for i, k in enumerate(kinds):
        d = round(float(g.uniform(NONSPEECH_MIN_S, NONSPEECH_MAX_S)), 1)
        out.append({"item_id": f"ns-{i:04d}", "kind": k, "duration_s": d,
                    "seed": derive_seed(seed, "ns", i), "label": _label(k)})
    return out


def render_nonspeech(item: dict) -> Clip:
    """Render a :func:`nonspeech_plan` item as a clip with no speech and an empty reference."""
    x = render_nonspeech_kind(item["kind"], item["duration_s"], item["seed"])
    return Clip(x, SAMPLE_RATE, None, None, {
        "item_id": item["item_id"], "text": "", "kind": item["kind"], "label": item["label"],
        "condition": {"type": "nonspeech", "kind": item["kind"]}})


# ------------------------------------------------------------------ speech then non-speech

def speech_then_nonspeech_plan(utterance_ids: list[str], seed: int = 0,
                               n: int = SPEECH_THEN_COUNT) -> list[dict]:
    """``n`` test-clean utterances (a seeded draw without repeats while ids last) each followed
    by 10-30 s of one non-speech kind (rotated evenly)."""
    ids = sorted(utterance_ids)
    g = _rng(derive_seed(seed, "stn-utt"))
    chosen: list[str] = []
    while len(chosen) < n:
        chosen += [ids[int(i)] for i in g.permutation(len(ids))]
    chosen = chosen[:n]
    kinds = _rotate(n, NONSPEECH_KINDS, seed, "stn-kind")
    gd = _rng(derive_seed(seed, "stn-duration"))
    return [{"item_id": f"stn-{i:04d}", "utt_id": u, "kind": k,
             "tail_s": round(float(gd.uniform(TAIL_MIN_S, TAIL_MAX_S)), 1),
             "seed": derive_seed(seed, "stn", i), "label": _label(k)}
            for i, (u, k) in enumerate(zip(chosen, kinds))]


def render_speech_then_nonspeech(item: dict, speech: Clip) -> Clip:
    """The speech clip, then ``tail_s`` of non-speech. Speech timings and text are kept; the
    reference is the speech's text, so anything transcribed after ``eos_s`` is an insertion."""
    tail = render_nonspeech_kind(item["kind"], item["tail_s"], item["seed"], speech.sample_rate)
    out = speech.copy(np.concatenate([speech.samples, tail]).astype(np.float32), condition={
        "type": "speech_then_nonspeech", "kind": item["kind"], "tail_s": item["tail_s"]})
    out.meta.update({"item_id": item["item_id"], "label": item["label"],
                     "last_speech_s": speech.eos_s if speech.eos_s is not None else speech.duration_s})
    return out


# ------------------------------------------------------------------ commands inside noise

def commands_in_noise_plan(command_ids: list[str], seed: int = 0,
                           n: int = COMMANDS_IN_NOISE_COUNT) -> list[dict]:
    """``n`` commands (ids from A3, cycled if fewer) with 5-20 s of noise before and after.

    The noise (one of the six A4 types, rotated evenly) runs under the whole item and the
    command is mixed in at ``snr_db`` over its speech span.
    """
    ids = sorted(command_ids)
    g = _rng(derive_seed(seed, "cin-cmd"))
    chosen: list[str] = []
    while len(chosen) < n:
        chosen += [ids[int(i)] for i in g.permutation(len(ids))]
    chosen = chosen[:n]
    kinds = _rotate(n, A4_NOISE_TYPES, seed, "cin-noise")
    gd = _rng(derive_seed(seed, "cin-pad"))
    out = []
    for i, (c, k) in enumerate(zip(chosen, kinds)):
        out.append({"item_id": f"cin-{i:04d}", "command_id": c, "noise_type": k,
                    "before_s": round(float(gd.uniform(NOISE_PAD_MIN_S, NOISE_PAD_MAX_S)), 1),
                    "after_s": round(float(gd.uniform(NOISE_PAD_MIN_S, NOISE_PAD_MAX_S)), 1),
                    "snr_db": COMMAND_NOISE_SNR_DB, "seed": derive_seed(seed, "cin", i)})
    return out


def render_command_in_noise(item: dict, command: Clip) -> Clip:
    """Place the command clip after ``before_s`` of noise and add ``after_s`` more noise."""
    sr = command.sample_rate
    pre, post = int(round(item["before_s"] * sr)), int(round(item["after_s"] * sr))
    body = np.concatenate([np.zeros(pre, np.float32), command.samples, np.zeros(post, np.float32)])
    shifted = Clip(body, sr, (command.speech_start_s or 0.0) + item["before_s"],
                   (command.eos_s if command.eos_s is not None else command.duration_s) + item["before_s"],
                   dict(command.meta))
    noise = generate_a4_noise(item["noise_type"], shifted.duration_s, seed=item["seed"])
    out = mix_at_snr(shifted, noise, item["snr_db"], label=item["noise_type"])
    out.meta["condition"].update({"type": "command_in_noise", "before_s": item["before_s"],
                                  "after_s": item["after_s"], "noise_type": item["noise_type"]})
    out.meta["item_id"] = item["item_id"]
    return out


# ------------------------------------------------------------------ answer-leak prompts

_FIRST = ("Quillon", "Marwenna", "Doravik", "Tessaly", "Brannock", "Ilvane", "Soriel", "Kestrin",
          "Odalys", "Pemberlo", "Variandra", "Hollis", "Jorvane", "Nimuel", "Calderyn", "Yseltine")
_LAST = ("Aberfell", "Thornquist", "Valmoor", "Eskridge", "Lindqvarn", "Okonweather", "Draymere",
         "Fenwicke", "Harlowen", "Tavistrand", "Mirelake", "Quennel")
_TOPICS = ("the budget review", "the launch plan", "the hiring notes", "the garden project",
           "the travel plan", "the lease", "the quarterly numbers", "the recipe ideas",
           "the reading list", "the workshop agenda")
_PLACES = ("Marlowe Bay", "Eastgrove", "Pinecrest", "Velden Harbor", "Quarry Lane", "Ashmoor")
_APPS = ("Calendar", "Notes", "Maps", "Music", "Reminders", "Photos", "Timer")

# (kind, expected tool for an assistant that acts, template). {n}/{t}/{p}/{a} fill from the lists.
_ANSWER_LEAK_TEMPLATES = (
    ("question", None, "what's the weather in {p} today"),
    ("question", None, "what's the weather like tomorrow in {p}"),
    ("question", None, "what time is it in {p}"),
    ("question", None, "who is {n} and what do they do"),
    ("question", None, "what is on my calendar this afternoon"),
    ("question", None, "do I have any meetings with {n}"),
    ("question", None, "how long does it take to drive to {p}"),
    ("question", None, "what did {n} say about {t}"),
    ("question", None, "how many days until the end of the month"),
    ("question", None, "what's the capital of {p}"),
    ("question", None, "when is my next reminder about {t}"),
    ("instruction", "delete_note", "delete my notes about {t}"),
    ("instruction", "delete_note", "delete my notes"),
    ("instruction", "send_message", "text {n} that I am running ten minutes late"),
    ("instruction", "send_email", "email {n} the summary of {t}"),
    ("instruction", "create_event", "schedule a meeting with {n} for friday at three"),
    ("instruction", "set_timer", "set a timer for {k} minutes"),
    ("instruction", "open_app", "open {a}"),
    ("instruction", "play_music", "play something relaxing"),
    ("instruction", "call", "call {n}"),
    ("instruction", "set_reminder", "remind me to review {t} at five"),
    ("instruction", "navigate", "give me directions to {p}"),
    ("instruction", "write_note", "write a note called {t}"),
)


def answer_leak_prompts(seed: int = 0, n: int = ANSWER_LEAK_COUNT) -> list[dict]:
    """500 distinct question and instruction texts from seeded templates with invented names.

    Each item: ``item_id``, ``text`` (what is spoken and the transcription reference),
    ``kind`` ("question" or "instruction") and ``expected_tool`` (the tool an assistant would
    call if it acted on an instruction; None for questions).
    """
    g = _rng(derive_seed(seed, "leak"))
    seen: set[str] = set()
    out: list[dict] = []
    guard = 0
    while len(out) < n:
        guard += 1
        if guard > n * 200:
            raise RuntimeError("template space too small for the requested count")
        kind, tool, tpl = _ANSWER_LEAK_TEMPLATES[guard % len(_ANSWER_LEAK_TEMPLATES)]
        name = f"{_FIRST[int(g.integers(len(_FIRST)))]} {_LAST[int(g.integers(len(_LAST)))]}"
        text = tpl.format(n=name, t=_TOPICS[int(g.integers(len(_TOPICS)))],
                          p=_PLACES[int(g.integers(len(_PLACES)))],
                          a=_APPS[int(g.integers(len(_APPS)))], k=int(g.integers(2, 45)))
        if text in seen:
            continue
        seen.add(text)
        i = len(out)
        out.append({"item_id": f"al-{i:04d}", "text": text, "kind": kind, "expected_tool": tool})
    return out


def render_answer_leak(item: dict, engine, voice: str, *, trailing_s: float = 0.5) -> Clip:
    """Speak the prompt with a :class:`~edat.media.audio.tts.SpeechEngine`, plus trailing silence."""
    c = engine.synthesize(item["text"], voice)
    out = c.copy(np.concatenate([c.samples, silence(trailing_s)]), condition={"type": "answer_leak"})
    out.meta.update({"item_id": item["item_id"], "text": item["text"], "kind": item["kind"],
                     "expected_tool": item["expected_tool"]})
    return out


# ------------------------------------------------------------------ scorers

def hallucination_rate(hyps: list[str]) -> float:
    """Share of non-speech clips whose output has any letter or digit left after the model's
    declared "no speech" markers are removed (``wer.has_content``)."""
    return sum(1 for h in hyps if has_content(h)) / len(hyps) if hyps else 0.0


def invented_words_per_minute(hyps: list[str], durations_s: list[float]) -> float:
    """Words in the outputs (after the markers are removed) per minute of non-speech audio."""
    minutes = sum(durations_s) / 60.0
    return sum(invented_words(h) for h in hyps) / minutes if minutes else 0.0


def looping_run(text: str, *, min_repeats: int = 3, max_n: int = 8,
                single_word_repeats: int = 5) -> dict | None:
    """The first repeated run in a transcript, or None.

    Rule: after lower-casing and splitting into letter/digit words, a loop is an n-gram of
    2 to ``max_n`` words that occurs ``min_repeats`` (3) or more times back to back, or a
    single word repeated ``single_word_repeats`` (5) or more times in a row. Returns
    ``{"ngram", "repeats", "start"}`` for the longest-repeating run found at the earliest start.
    """
    w = re.findall(r"[^\W_]+(?:'[^\W_]+)?", (text or "").lower())
    best = None
    for i in range(len(w)):
        for n in range(1, max_n + 1):
            need = single_word_repeats if n == 1 else min_repeats
            if i + n * need > len(w):
                continue
            gram = w[i: i + n]
            reps = 1
            while w[i + reps * n: i + (reps + 1) * n] == gram:
                reps += 1
            if reps >= need and (best is None or reps * n > best["repeats"] * len(best["ngram"].split())):
                best = {"ngram": " ".join(gram), "repeats": reps, "start": i}
        if best is not None:
            return best
    return None


def looping_rate(hyps: list[str], **kw) -> float:
    return sum(1 for h in hyps if looping_run(h, **kw)) / len(hyps) if hyps else 0.0


def trailing_insertions(ref: str, hyp: str) -> int:
    """Words in the output after the reference's last aligned word.

    Words are normalised and aligned (:func:`~edat.media.audio.mix.align_ops`); the last aligned
    word is the last exact match (hit) in the alignment, and the count is the number of
    output words after it. With no match the whole output counts.
    """
    r, h = _words(ref), _words(hyp)
    ops = align_ops(r, h)
    last = max((j for op, _, j in ops if op == "hit"), default=-1)
    return len(h) - 1 - last


def answer_leak_result(ref: str, hyp: str, tool_calls: list | None = None) -> dict:
    """``answered`` (transcript is an answer, not the question: ``wer.is_answer_leak``),
    ``acted`` (the model emitted any tool call) and ``leak`` (either)."""
    answered = bool(is_answer_leak(ref, hyp))
    acted = bool(tool_calls)
    return {"answered": answered, "acted": acted, "leak": answered or acted}


def answer_leak_rate(results: list[dict]) -> float:
    return sum(1 for r in results if r["leak"]) / len(results) if results else 0.0
