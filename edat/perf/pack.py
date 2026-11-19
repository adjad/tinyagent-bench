"""Prompt-pack builder (spec 04 §2.3, §6.1, §6.3; inputs from spec 02 §11.1-§11.3).

Builds ``pack.json`` (``edat.prompt-pack/0.1``) once per model entry on the Mac: the chat
template is applied with the generation prompt, the text is tokenized, and every context
level holds 16 nonce variants that are exact to the token. The iPhone runner reads the
layout through ``PromptPack.swift`` (variant ``i`` = ``prefix_ids + nonce_ids + body_ids[:n]
+ suffix_ids``, or the variant's own ``ids``); the Mac uses each variant's ``text``.

Level layout, per level (token ids, ``n = level_tokens - len(prefix) - len(nonce) - len(suffix)``)::

    prefix_ids   tokens before the nonce (BOS, system-turn header, anything the template puts first)
    nonce_ids    the variant's nonce (same token count for all variants)
    body_ids     tokens after the nonce up to and including the content of the last kept history
                 message (the trimmable tail); a variant uses body_ids[:n]
    suffix_ids   closing tokens of that message + the fixed messages after the history range
                 (the final user turn) + the generation prompt

``pack_sha256`` is the sha256 (hex) of the canonical JSON of the pack with the ``pack_sha256``
key ABSENT. Canonical JSON = ``json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)`` encoded as UTF-8. ``write_pack`` writes exactly that canonical form with
the key added, so a reader can recompute the hash from the parsed document.

DECISIONS THAT ARE CHOICES (the specs leave them open):

* ``trim.message_range`` is ``[start, end]``, END INCLUSIVE, negatives counted from the end
  (so ``[2, -2]`` of a 5-message record is messages 2 and 3). Index 0 must be the system message.
* Trim unit ``whole_records_then_tokens``: a "record" is one history message. Messages are dropped
  from the END of the range until the smallest kept prefix whose intact length reaches the level;
  then only the tail of the LAST kept message's content is token-trimmed. A message with no string
  content (an assistant message with only tool calls) is never the last kept message, because it has
  no tail to trim. The cut must leave at least one content token of the last kept message.
* Gap rule: when the target length falls between two content regions (inside a tool-call message or a
  turn header, where no content token can be cut), the last kept message that falls short is EXTENDED
  with the text of the history messages after it and trimmed as usual; the level (or replay prefix)
  records ``tail_padded: true``. This is the only case where text of a dropped message is used.
* A level that the corpus cannot fill (fixed parts too long, or all history too short) raises
  ``PackError``. A level that is simply absent from the corpus is listed in ``missing_levels``.
* The nonce is ``"Session ref: <words/digits>"`` with NO trailing period, followed by a newline
  before the original system text, so it cannot merge with what follows. Its token count is fixed
  (``nonce_tokens``, default 8) in the model's own tokenizer; no nonce repeats anywhere in the pack
  (levels and replay variants). Nonces come from a seeded RNG, so a fixed seed gives the same pack.
* Variants whose re-tokenised text differs from their ids get the nearest cut ``n`` (searched
  outward, bounded by ``window``) at which ``len(encode(decode(ids))) == level_tokens``; their
  full ``ids`` are stored and they are marked ``"roundtrip": "adjusted"``. If no cut works the build fails.
* ``content_tokens`` is {head, menu, history, final, other}: head = system content, menu = what the
  tool declarations add to the template (measured in isolation), history = kept history content with
  the trimmed tail, final = messages after the range, other = the remainder (template tokens,
  nonce, rounding). The five sum to ``level_tokens``; the split is approximate, the sum is exact.
* ``tokens_per_1k_chars`` = 1000 x tokens / characters over the DISTINCT string contents of every
  history-range message of every corpus record (each encoded on its own).
* ``first_token``: a short user message from a fixed sentence, cut by tokens, whose TOTAL templated
  length is 8 if reachable, else the closest (the shortest when the template overhead alone is
  above 8), with ``exact`` saying which.
* Agent replay (trajectory format, the spec gives only the key names): ``steps[0].messages`` is the
  whole starting conversation (system message, one or more earlier-exchange messages, and the user
  request LAST); ``steps[k].messages`` is what step k adds before its model call (usually empty);
  ``canned_assistant`` is the assistant message and ``canned_tool_results`` the tool messages that
  follow it; ``tools`` comes from step 0. Step k's input = prefix + request + the canned messages of
  steps 1..k-1. The shared prefix is everything up to the end of the last kept earlier-exchange
  message (nonce included, no generation prompt); it is exactly ``prefix_tokens`` tokens (the
  trajectory's value unless ``replay_prefix_tokens`` overrides it). The chat template must render
  the prefix as a text prefix of every step (checked).
* The pack's ``generator_seed`` is the BUILDER's seed (nonces); the corpus records carry their own.
* ``chat template`` date functions are fixed (see ``edat.perf.tokenizer``).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from edat.perf.constants import (AGENT_CORPUS_SCHEMA, AGENT_TRAJECTORY_SCHEMA, FIRST_TOKEN_PROMPT_TOKENS,
                                 LEVELS, NONCE_VARIANTS, PROMPT_PACK_SCHEMA, REPLAY_PREFIX_TOKENS,
                                 REPLAY_STEPS)
from edat.perf.tokenizer import HFTokenizer, ModelTokenizer, StubTokenizer

PACK_VERSION = "0.1"
BUILDER = {"name": "edat.perf.pack", "version": "0.1.0"}
DEFAULT_SEED = 41
DEFAULT_NONCE_TOKENS = 8
DEFAULT_REPLAY_VARIANTS = 32
DEFAULT_ADJUST_WINDOW = 32
TRIM_UNIT = "whole_records_then_tokens"
NONCE_SENTINEL = "@@EDAT_NONCE@@"
TAIL_SENTINEL = "@@EDAT_TAIL@@"
FIRST_TOKEN_SENTENCE = "Please say hello to me in one short friendly sentence."


class PackError(ValueError):
    """The corpus, trajectory or pack is unusable; the message says why."""


# --------------------------------------------------------------------------- canonical hash

def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_pack_sha256(pack: dict[str, Any]) -> str:
    """sha256 of the canonical JSON of ``pack`` with ``pack_sha256`` absent."""
    return sha256_hex(canonical_bytes({k: v for k, v in pack.items() if k != "pack_sha256"}))


def write_pack(pack: dict[str, Any], path: str | Path) -> str:
    """Sets ``pack["pack_sha256"]``, writes the canonical JSON atomically, returns the hash."""
    digest = compute_pack_sha256(pack)
    pack["pack_sha256"] = digest
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(canonical_bytes(pack))
    os.replace(tmp, target)
    return digest


# --------------------------------------------------------------------------- nonces

_ADJ = ("amber", "brisk", "calm", "dusty", "eager", "faint", "gentle", "hollow", "ivory", "jolly", "keen",
        "lucid", "mellow", "nimble", "opal", "plain", "quiet", "rusty", "silver", "tidy", "umber", "vivid",
        "woolly", "young", "zesty", "bold", "crisp", "dim", "early", "fresh", "grand", "humble", "icy",
        "jade", "kind", "light", "mild", "noble", "olive", "proud", "quick", "round", "sandy", "tame",
        "upper", "vast", "warm", "xenial")
_NOUN = ("heron", "willow", "harbor", "lantern", "meadow", "pebble", "ribbon", "thistle", "violet", "walnut",
         "anchor", "beacon", "cedar", "dune", "ember", "fjord", "glacier", "hazel", "island", "juniper",
         "kettle", "lagoon", "maple", "nettle", "orchard", "pine", "quarry", "reef", "spruce", "tundra",
         "umbra", "valley", "wren", "yarrow", "zephyr", "acorn", "brook", "clover", "delta", "falcon",
         "garnet", "heath", "iris", "jasper", "kestrel", "linden", "marsh", "newt")


class _Nonces:
    """Unique nonce spans of one fixed token count, from a seeded RNG."""

    def __init__(self, tok: ModelTokenizer, seed: int, nonce_tokens: int) -> None:
        self.tok = tok
        self.nonce_tokens = nonce_tokens
        self.rng = random.Random(f"edat-perf-pack-nonce:{seed}")
        self.used: set[str] = set()

    def take(self) -> tuple[str, list[int]]:
        for _ in range(200_000):
            sep = self.rng.choice(("-", " "))
            parts = []
            for _p in range(self.rng.randint(1, 8)):
                r = self.rng.random()
                parts.append(self.rng.choice(_ADJ) if r < 0.4 else self.rng.choice(_NOUN) if r < 0.8
                             else str(self.rng.randint(100, 9999)))
            text = "Session ref: " + sep.join(parts)
            if text in self.used:
                continue
            ids = self.tok.encode(text)
            if len(ids) == self.nonce_tokens:
                self.used.add(text)
                return text, ids
        raise PackError(f"could not find a nonce of exactly {self.nonce_tokens} tokens in this tokenizer")


# --------------------------------------------------------------------------- corpus checks

def _normalize_messages(messages: Sequence[dict]) -> list[dict]:
    out = copy.deepcopy(list(messages))
    for m in out:
        calls = m.get("tool_calls")
        if isinstance(calls, list):
            fixed = []
            for c in calls:
                if isinstance(c, str):
                    try:
                        c = json.loads(c)
                    except ValueError:
                        pass
                fixed.append(c)
            m["tool_calls"] = fixed
    return out


def _resolve_range(rng: Sequence[int], n: int) -> tuple[int, int]:
    """Inclusive ``[lo, hi]`` of the trimmable range; ``lo > hi`` means empty."""
    if len(rng) != 2 or not all(isinstance(x, int) for x in rng):
        raise PackError(f"trim.message_range must be two integers, got {rng!r}")
    lo, hi = rng
    lo = lo + n if lo < 0 else lo
    hi = hi + n if hi < 0 else hi
    hi = min(hi, n - 1)
    if lo < 1:
        raise PackError("trim.message_range may not include the system message (index 0)")
    return lo, hi


def validate_record(rec: dict[str, Any]) -> None:
    if rec.get("schema") != AGENT_CORPUS_SCHEMA:
        raise PackError(f"corpus record schema is {rec.get('schema')!r}, expected {AGENT_CORPUS_SCHEMA!r}")
    if rec.get("lang") != "en":
        raise PackError(f"corpus record for level {rec.get('level')} has lang {rec.get('lang')!r}; "
                        "EDAT packs are English only")
    if not isinstance(rec.get("level"), int):
        raise PackError("corpus record has no integer 'level'")
    msgs = rec.get("messages")
    if not isinstance(msgs, list) or len(msgs) < 2 or msgs[0].get("role") != "system":
        raise PackError(f"level {rec['level']}: messages must start with a system message")
    trim = rec.get("trim")
    if not isinstance(trim, dict) or trim.get("unit") != TRIM_UNIT:
        raise PackError(f"level {rec['level']}: trim.unit must be {TRIM_UNIT!r}")
    _resolve_range(trim.get("message_range", []), len(msgs))
    blob = json.dumps(msgs)
    if NONCE_SENTINEL in blob or TAIL_SENTINEL in blob:
        raise PackError(f"level {rec['level']}: corpus text contains a reserved marker")


# --------------------------------------------------------------------------- exact fitting

@dataclass
class _Fit:
    level: int
    nonce_tokens: int
    prefix_ids: list[int]
    body_ids: list[int]
    suffix_ids: list[int]
    n: int                      # body tokens used when the nonce has nonce_tokens tokens
    floor: int                  # body tokens before the last kept message's content starts
    last_kept: int              # absolute index of the last kept history message (lo - 1: none)
    lo: int
    hi: int
    padded: bool = False        # the tail message was extended with text of dropped messages


def _render_segments(tok: ModelTokenizer, messages: list[dict], tools: Sequence[dict] | None, thinking: bool,
                     lo: int, hi: int, last_kept: int, gen_prompt: bool, *,
                     empty_tail: bool = False) -> tuple[str, str, str]:
    kept_history = last_kept >= lo
    msgs = [dict(m) for m in messages[:last_kept + 1]] + [dict(m) for m in messages[hi + 1:]]
    msgs[0]["content"] = NONCE_SENTINEL + "\n" + (msgs[0].get("content") or "")
    if kept_history:
        base = "" if empty_tail else msgs[last_kept]["content"]
        msgs[last_kept]["content"] = base + TAIL_SENTINEL
    text = tok.apply_chat_template(msgs, list(tools) if tools else None, add_generation_prompt=gen_prompt,
                                   thinking=thinking)
    if text.count(NONCE_SENTINEL) != 1 or (kept_history and text.count(TAIL_SENTINEL) != 1):
        raise PackError("the chat template dropped or repeated a message's content; cannot place the nonce")
    pre, rest = text.split(NONCE_SENTINEL)
    if kept_history:
        body, suffix = rest.split(TAIL_SENTINEL)
    else:
        body, suffix = "", rest
    return pre, body, suffix


def _fit(tok: ModelTokenizer, messages: list[dict], tools: Sequence[dict] | None, thinking: bool,
         rng: tuple[int, int], level: int, nonce_tokens: int, gen_prompt: bool, what: str) -> _Fit:
    lo, hi = rng
    cache: dict[int, tuple[list[int], list[int], list[int]]] = {}

    def build(last_kept: int) -> tuple[list[int], list[int], list[int]]:
        if last_kept not in cache:
            pre, body, suffix = _render_segments(tok, messages, tools, thinking, lo, hi, last_kept, gen_prompt)
            cache[last_kept] = (tok.encode(pre), tok.encode(body) if body else [], tok.encode(suffix))
        return cache[last_kept]

    def total(last_kept: int) -> int:
        p, b, s = build(last_kept)
        return len(p) + nonce_tokens + len(b) + len(s)

    base = total(lo - 1)
    if base == level:
        p, b, s = build(lo - 1)
        return _Fit(level, nonce_tokens, p, b, s, 0, 0, lo - 1, lo, hi)
    if base > level:
        raise PackError(f"{what}: the fixed parts alone are {base} tokens, above the {level} target")
    eligible = [i for i in range(lo, hi + 1)
                if isinstance(messages[i].get("content"), str) and messages[i]["content"].strip()]
    if not eligible or total(eligible[-1]) < level:
        have = total(eligible[-1]) if eligible else base
        raise PackError(f"{what}: the corpus is too short; all history gives {have} tokens, {level} needed")
    a, b_ = 0, len(eligible) - 1                  # smallest eligible index whose intact total reaches level
    while a < b_:
        mid = (a + b_) // 2
        if total(eligible[mid]) >= level:
            b_ = mid
        else:
            a = mid + 1
    def attempt(msgs: list[dict], i: int, padded: bool) -> _Fit | None:
        pre, body, suffix = _render_segments(tok, msgs, tools, thinking, lo, hi, i, gen_prompt)
        p, b, s = tok.encode(pre), tok.encode(body), tok.encode(suffix)
        n = level - len(p) - nonce_tokens - len(s)
        _, empty_body, _ = _render_segments(tok, msgs, tools, thinking, lo, hi, i, gen_prompt, empty_tail=True)
        floor = len(tok.encode(empty_body)) if empty_body else 0
        if floor < n <= len(b):
            return _Fit(level, nonce_tokens, p, b, s, n, floor, i, lo, hi, padded)
        return None

    for j in range(a, len(eligible)):
        fit = attempt(messages, eligible[j], False)
        if fit:
            return fit
    # The target fell in a gap between two content regions (a tool call, a turn header): no cut of
    # any one message's content reaches it. Extend the last message that falls short with the text
    # of the history messages that follow it (they are dropped otherwise), then trim as usual.
    for j in range(a - 1, -1, -1):
        i = eligible[j]
        later = [messages[k]["content"] for k in eligible if k > i]
        if not later:
            continue
        msgs2 = [dict(m) for m in messages]
        msgs2[i]["content"] = messages[i]["content"] + " " + " ".join(later)
        fit = attempt(msgs2, i, True)
        if fit:
            return fit
    raise PackError(f"{what}: no history prefix can be trimmed to exactly {level} tokens")


def _realize(tok: ModelTokenizer, fit: _Fit, nonce_ids: list[int],
             window: int) -> tuple[list[int], str, str, int]:
    """(ids, text, roundtrip, n) for one nonce: the Swift formula, else the nearest cut that round-trips."""
    if len(nonce_ids) != fit.nonce_tokens:
        raise PackError("nonce length differs from the length the level was fitted with")

    def ids_for(n: int) -> list[int]:
        return fit.prefix_ids + nonce_ids + fit.body_ids[:n] + fit.suffix_ids

    ids = ids_for(fit.n)
    text = tok.decode(ids)
    if tok.encode(text) == ids:
        return ids, text, "exact", fit.n
    for d in range(window + 1):
        for n2 in ((fit.n,) if d == 0 else (fit.n + d, fit.n - d)):
            if not fit.floor < n2 <= len(fit.body_ids):
                continue
            cand_text = tok.decode(ids_for(n2))
            again = tok.encode(cand_text)
            if len(again) == fit.level:
                return again, cand_text, "adjusted", n2
    raise PackError(f"no cut within {window} tokens of {fit.n} round-trips to exactly {fit.level} tokens")


# --------------------------------------------------------------------------- levels

def _calls_text(m: dict) -> str:
    return json.dumps(m["tool_calls"], sort_keys=True) if m.get("tool_calls") else ""


def _tools_overhead(tok: ModelTokenizer, tools: Sequence[dict], thinking: bool) -> int:
    if not tools:
        return 0
    probe = [{"role": "system", "content": "x"}]
    with_tools = tok.apply_chat_template(probe, list(tools), add_generation_prompt=False, thinking=thinking)
    without = tok.apply_chat_template(probe, None, add_generation_prompt=False, thinking=thinking)
    return max(0, len(tok.encode(with_tools)) - len(tok.encode(without)))


def _level_breakdown(tok: ModelTokenizer, fit: _Fit, messages: list[dict], tools: Sequence[dict],
                     thinking: bool) -> dict[str, int]:
    def length(text: str) -> int:
        return len(tok.encode(text)) if text else 0

    head = length(messages[0].get("content") or "")
    menu = _tools_overhead(tok, tools, thinking)
    final = sum(length((m.get("content") or "") + _calls_text(m)) for m in messages[fit.hi + 1:])
    history = 0
    for i in range(fit.lo, fit.last_kept + 1):
        m = messages[i]
        history += length(_calls_text(m))
        history += (fit.n - fit.floor) if i == fit.last_kept else length(m.get("content") or "")
    return {"head": head, "menu": menu, "history": history, "final": final,
            "other": fit.level - head - menu - history - final}


def _build_level(tok: ModelTokenizer, rec: dict[str, Any], thinking: bool, nonces: _Nonces, n_variants: int,
                 window: int) -> dict[str, Any]:
    level = rec["level"]
    messages = _normalize_messages(rec["messages"])
    tools = rec.get("tools") or []
    lo, hi = _resolve_range(rec["trim"]["message_range"], len(messages))
    fit = _fit(tok, messages, tools, thinking, (lo, hi), level, nonces.nonce_tokens, True, f"level {level}")
    variants = []
    for _ in range(n_variants):
        ntext, nids = nonces.take()
        ids, text, rt, n = _realize(tok, fit, nids, window)
        v: dict[str, Any] = {"nonce_ids": nids, "nonce_text": ntext, "text": text, "roundtrip": rt, "body_n": n}
        if rt == "adjusted":
            v["ids"] = ids
        variants.append(v)
    return {
        "level_tokens": level,
        "prefix_ids": fit.prefix_ids, "body_ids": fit.body_ids, "suffix_ids": fit.suffix_ids,
        "variants": variants,
        "tools_count": len(tools),
        "history_messages_kept": fit.last_kept - lo + 1,
        "history_messages_total": max(0, hi - lo + 1),
        "tail_padded": fit.padded,
        "content_tokens": _level_breakdown(tok, fit, messages, tools, thinking),
        "text_chars": len(variants[0]["text"]) if variants else 0,
    }


def tokens_per_1k_chars(records: Sequence[dict[str, Any]], tok: ModelTokenizer) -> dict[str, Any]:
    """Tokenizer efficiency over the corpus history text (spec 04 §6.1)."""
    seen: set[str] = set()
    tokens = chars = 0
    for rec in records:
        msgs = rec["messages"]
        lo, hi = _resolve_range(rec["trim"]["message_range"], len(msgs))
        for m in msgs[lo:hi + 1]:
            c = m.get("content")
            if isinstance(c, str) and c and c not in seen:
                seen.add(c)
                tokens += len(tok.encode(c))
                chars += len(c)
    if not chars:
        raise PackError("the corpus has no history text to measure tokens per 1,000 characters")
    return {"value": round(1000.0 * tokens / chars, 4), "tokens": tokens, "chars": chars, "messages": len(seen)}


# --------------------------------------------------------------------------- first token

def _build_first_token(tok: ModelTokenizer, thinking: bool, target: int = FIRST_TOKEN_PROMPT_TOKENS) -> dict:
    base = tok.encode(FIRST_TOKEN_SENTENCE)
    best: tuple[int, int, str, str, list[int]] | None = None
    for j in range(1, len(base) + 1):
        content = tok.decode(base[:j]).strip()
        text = tok.apply_chat_template([{"role": "user", "content": content}], None,
                                       add_generation_prompt=True, thinking=thinking)
        ids = tok.encode(text)
        key = (abs(len(ids) - target), len(ids))
        if best is None or key < best[:2]:
            best = (key[0], key[1], content, text, ids)
        if len(ids) >= target:
            break
    assert best is not None
    return {"ids": best[4], "text": best[3], "user_text": best[2], "prompt_tokens": best[1],
            "target_tokens": target, "exact": best[1] == target}


# --------------------------------------------------------------------------- agent replay

def _build_replay(tok: ModelTokenizer, traj: dict[str, Any], thinking: bool, nonces: _Nonces,
                  n_variants: int, prefix_target: int, window: int) -> dict[str, Any]:
    if traj.get("schema") != AGENT_TRAJECTORY_SCHEMA:
        raise PackError(f"trajectory schema is {traj.get('schema')!r}, expected {AGENT_TRAJECTORY_SCHEMA!r}")
    if traj.get("lang") != "en":
        raise PackError(f"trajectory lang is {traj.get('lang')!r}; EDAT packs are English only")
    steps = traj.get("steps")
    if not isinstance(steps, list) or len(steps) != REPLAY_STEPS:
        raise PackError(f"the trajectory must have exactly {REPLAY_STEPS} steps")
    start = _normalize_messages(steps[0]["messages"])
    tools = steps[0].get("tools") or []
    if len(start) < 3 or start[0].get("role") != "system" or start[-1].get("role") != "user":
        raise PackError("steps[0].messages must be [system, earlier exchange..., user request]")
    for k, st in enumerate(steps[1:], start=2):
        if st.get("tools") and st["tools"] != tools:
            raise PackError(f"step {k} changes the tool list; the shared prefix would not be shared")
    prefix_msgs = start[:-1]
    fit = _fit(tok, prefix_msgs, tools, thinking, (1, len(prefix_msgs) - 1), prefix_target,
               nonces.nonce_tokens, False, "agent-replay prefix")
    kept = prefix_msgs[:fit.last_kept + 1]
    pre_text = tok.apply_chat_template(kept, list(tools) or None, add_generation_prompt=False, thinking=thinking)
    conv = kept + [start[-1]]
    rests: list[list[int]] = []
    for k, st in enumerate(steps):
        if k > 0:
            prev = steps[k - 1]
            conv = conv + _normalize_messages([prev["canned_assistant"]]) \
                + _normalize_messages(prev.get("canned_tool_results") or []) \
                + _normalize_messages(st.get("messages") or [])
        full = tok.apply_chat_template(conv, list(tools) or None, add_generation_prompt=True, thinking=thinking)
        if not full.startswith(pre_text):
            raise PackError(f"step {k + 1}: the chat template does not render the shared prefix as a text "
                            "prefix of the step's prompt")
        rests.append(tok.encode(full[len(pre_text):]))
    variants = []
    for v in range(n_variants):
        ntext, nids = nonces.take()
        a_ids, _a_text, a_rt, _n = _realize(tok, fit, nids, window)
        out_steps = []
        for k, rest in enumerate(rests, start=1):
            ids = a_ids + rest
            text = tok.decode(ids)
            again = tok.encode(text)
            out_steps.append({"step": k, "ids": ids, "text": text, "prompt_tokens": len(ids),
                              "roundtrip": "exact" if again == ids else "drift",
                              "retokenized_tokens": len(again)})
        variants.append({"variant": v, "nonce_text": ntext, "nonce_ids": nids, "prefix_roundtrip": a_rt,
                         "steps": out_steps})
    return {
        "prefix_tokens": prefix_target,
        "prefix_messages_kept": len(kept),             # includes the system message
        "tools_count": len(tools),
        "steps": [{"step": s["step"], "ids": s["ids"]} for s in variants[0]["steps"]] if variants else [],
        "variants": variants,
    }


# --------------------------------------------------------------------------- build

def build_pack(corpus_records: Sequence[dict[str, Any]], trajectory: dict[str, Any] | None,
               tokenizer: ModelTokenizer, *, model_id: str, entry_id: str, thinking: bool | str,
               levels: Sequence[int] = LEVELS, n_variants: int = NONCE_VARIANTS,
               replay_variants: int = DEFAULT_REPLAY_VARIANTS, seed: int = DEFAULT_SEED,
               nonce_tokens: int = DEFAULT_NONCE_TOKENS, corpus_name: str | None = None,
               corpus_sha256: str | None = None, trajectory_sha256: str | None = None,
               replay_prefix_tokens: int | None = None, built_utc: str | None = None,
               window: int = DEFAULT_ADJUST_WINDOW, log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Builds the pack document (without ``pack_sha256``; ``write_pack`` adds it).

    ``trajectory=None`` skips the agent replay (the key is then absent)."""
    think = thinking in (True, "on")
    if thinking not in (True, False, "on", "off"):
        raise PackError(f"thinking must be 'on' or 'off', got {thinking!r}")
    say = log or (lambda _m: None)
    by_level: dict[int, dict[str, Any]] = {}
    for rec in corpus_records:
        validate_record(rec)
        if rec["level"] in by_level:
            raise PackError(f"the corpus has two records for level {rec['level']}")
        by_level[rec["level"]] = rec
    wanted = sorted(set(levels))
    missing = [lv for lv in wanted if lv not in by_level]
    nonces = _Nonces(tokenizer, seed, nonce_tokens)
    built = []
    for lv in wanted:
        if lv in by_level:
            say(f"level {lv}")
            built.append(_build_level(tokenizer, by_level[lv], think, nonces, n_variants, window))
    name = corpus_name or next((r["corpus"] for r in corpus_records if isinstance(r.get("corpus"), str)),
                               AGENT_CORPUS_SCHEMA)
    pack: dict[str, Any] = {
        "schema": PROMPT_PACK_SCHEMA,
        "model_id": model_id,
        "entry_id": entry_id,
        "thinking": "on" if think else "off",
        "corpus": name,
        "version": PACK_VERSION,
        "language": "en",
        "generator_seed": seed,
        "tokenizer_sha256": tokenizer.tokenizer_sha256,
        "template_sha256": tokenizer.template_sha256,
        "corpus_sha256": corpus_sha256 or sha256_hex(canonical_bytes(list(corpus_records))),
        "trajectory_sha256": (trajectory_sha256 or sha256_hex(canonical_bytes(trajectory))) if trajectory else None,
        "nonce_tokens": nonce_tokens,
        "levels": built,
        "missing_levels": missing,
        "tokens_per_1k_chars": tokens_per_1k_chars(list(by_level.values()), tokenizer),
        "first_token": _build_first_token(tokenizer, think),
        "built_utc": built_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "builder": dict(BUILDER),
    }
    if trajectory is not None:
        say("agent replay")
        target = replay_prefix_tokens or trajectory.get("prefix_tokens") or REPLAY_PREFIX_TOKENS
        pack["agent_replay"] = _build_replay(tokenizer, trajectory, think, nonces, replay_variants, target, window)
    return pack


# --------------------------------------------------------------------------- reading a pack

def _level_entry(pack: dict[str, Any], level: int) -> dict[str, Any]:
    for lvl in pack["levels"]:
        if lvl["level_tokens"] == level:
            return lvl
    raise PackError(f"the pack has no {level}-token level")


def _variant_ids(lvl: dict[str, Any], v: dict[str, Any]) -> list[int]:
    """The Swift ``ids(level:variant:)`` formula."""
    level = lvl["level_tokens"]
    if v.get("ids") is not None:
        out = v["ids"]
    else:
        nonce = v.get("nonce_ids") or []
        count = level - len(lvl["prefix_ids"]) - len(nonce) - len(lvl["suffix_ids"])
        if count < 0 or count > len(lvl["body_ids"]):
            raise PackError(f"the {level}-token level's body is too short for a variant")
        out = lvl["prefix_ids"] + nonce + lvl["body_ids"][:count] + lvl["suffix_ids"]
    if len(out) != level:
        raise PackError(f"a variant of the {level}-token level has {len(out)} tokens")
    return list(out)


def prompt(pack: dict[str, Any], level: int, variant: int) -> tuple[list[int], str]:
    """(token ids, text) of repetition ``variant`` of ``level`` (wraps at the variant count)."""
    lvl = _level_entry(pack, level)
    if not lvl["variants"]:
        raise PackError(f"the {level}-token level has no variants")
    v = lvl["variants"][variant % len(lvl["variants"])]
    return _variant_ids(lvl, v), v["text"]


def replay_step(pack: dict[str, Any], variant: int, step: int) -> tuple[list[int], str]:
    """(token ids, text) of agent-replay step ``step`` (1-based) of nonce variant ``variant``."""
    replay = pack.get("agent_replay")
    if not replay or not replay.get("variants"):
        raise PackError("the pack has no agent-replay variants")
    v = replay["variants"][variant % len(replay["variants"])]
    if not 1 <= step <= len(v["steps"]):
        raise PackError(f"agent replay has no step {step}")
    s = v["steps"][step - 1]
    return list(s["ids"]), s["text"]


def load_pack(path: str | Path, tokenizer: ModelTokenizer | None = None) -> dict[str, Any]:
    """Reads and validates a pack: schema, language, pack_sha256, every variant's exact length,
    and the replay prefix. With ``tokenizer`` it also checks the hashes match and that each
    variant's text re-encodes to its ids."""
    pack = json.loads(Path(path).read_text(encoding="utf-8"))
    if pack.get("schema") != PROMPT_PACK_SCHEMA:
        raise PackError(f"not a prompt pack (schema {pack.get('schema')!r})")
    if pack.get("language") != "en":
        raise PackError(f"the pack's language is {pack.get('language')!r}; EDAT packs are English only")
    if pack.get("pack_sha256") != compute_pack_sha256(pack):
        raise PackError("pack_sha256 does not match the pack's content")
    if tokenizer is not None:
        if pack.get("tokenizer_sha256") != tokenizer.tokenizer_sha256:
            raise PackError("the pack was built with a different tokenizer")
        if pack.get("template_sha256") != tokenizer.template_sha256:
            raise PackError("the pack was built with a different chat template")
    for lvl in pack["levels"]:
        for i, v in enumerate(lvl["variants"]):
            ids = _variant_ids(lvl, v)
            if tokenizer is not None and tokenizer.encode(v["text"]) != ids:
                raise PackError(f"variant {i} of the {lvl['level_tokens']}-token level: text does not re-encode to its ids")
    replay = pack.get("agent_replay")
    if replay:
        target = replay["prefix_tokens"]
        for v in replay["variants"]:
            first = v["steps"][0]["ids"]
            for s in v["steps"]:
                if s["ids"][:target] != first[:target] or len(s["ids"]) != s["prompt_tokens"]:
                    raise PackError(f"replay variant {v['variant']} step {s['step']}: prefix or length mismatch")
                if tokenizer is not None and s["roundtrip"] == "exact" and tokenizer.encode(s["text"]) != s["ids"]:
                    raise PackError(f"replay variant {v['variant']} step {s['step']}: text does not re-encode")
    return pack


# --------------------------------------------------------------------------- CLI

def _read_json(path: str) -> tuple[Any, str]:
    raw = Path(path).read_bytes()
    return json.loads(raw.decode("utf-8")), sha256_hex(raw)


def _read_jsonl(path: str) -> tuple[list[dict], str]:
    raw = Path(path).read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    return rows, sha256_hex(raw)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m edat.perf.pack", description="Build an EDAT prompt pack.")
    ap.add_argument("--corpus", required=True, help="edat-agent-corpus-v1.jsonl")
    ap.add_argument("--trajectory", required=True, help="edat-agent-trajectory-v1.json")
    ap.add_argument("--model-dir", help="model directory with tokenizer.json and a chat template")
    ap.add_argument("--stub-tokenizer", action="store_true", help="use the file-free test tokenizer")
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--entry-id", required=True)
    ap.add_argument("--thinking", required=True, choices=("on", "off"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--levels", help="comma-separated levels (default: the seven spec levels)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--nonce-tokens", type=int, default=DEFAULT_NONCE_TOKENS)
    ap.add_argument("--replay-variants", type=int, default=DEFAULT_REPLAY_VARIANTS)
    ap.add_argument("--replay-prefix-tokens", type=int)
    ap.add_argument("--corpus-name")
    ap.add_argument("--variants", type=int, default=NONCE_VARIANTS,
                    help="nonce variants per level (16 for the iPhone; the Mac's energy pairs need "
                         "2K+11 fresh ones per level, so build Mac packs with 64)")
    args = ap.parse_args(argv)
    if args.stub_tokenizer:
        tok: ModelTokenizer = StubTokenizer()
    elif args.model_dir:
        tok = HFTokenizer.from_model_dir(args.model_dir)
    else:
        ap.error("give --model-dir or --stub-tokenizer")
    records, corpus_sha = _read_jsonl(args.corpus)
    traj, traj_sha = _read_json(args.trajectory)
    levels = tuple(int(x) for x in args.levels.split(",")) if args.levels else LEVELS
    try:
        pack = build_pack(records, traj, tok, model_id=args.model_id, entry_id=args.entry_id,
                          thinking=args.thinking, levels=levels, seed=args.seed, nonce_tokens=args.nonce_tokens,
                          n_variants=args.variants,
                          replay_variants=args.replay_variants, corpus_name=args.corpus_name,
                          corpus_sha256=corpus_sha, trajectory_sha256=traj_sha,
                          replay_prefix_tokens=args.replay_prefix_tokens,
                          log=lambda m: print(m, file=sys.stderr))
    except PackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    digest = write_pack(pack, args.out)
    adjusted = sum(1 for lvl in pack["levels"] for v in lvl["variants"] if v["roundtrip"] == "adjusted")
    print(f"wrote {args.out}: {len(pack['levels'])} levels, {adjusted} adjusted variants, pack_sha256 {digest}")
    if pack["missing_levels"]:
        print(f"missing levels (not in the corpus): {pack['missing_levels']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
