"""Long sessions: a conversation that fills the window and keeps going (spec 04 §9.2, D17).

Input: ``edat-long-session-v1`` (**PROVISIONAL**; the agentic spec will script the real one)::

    {"schema": "edat-long-session-v1", "lang": "en", "window_tokens": 16384,
     "system": "<system prompt>", "tools": [<tool schemas>],
     "turns": [{"user": "<user message>",
                "canned_assistant": "<text>" | {"content": "<text>", "tool_calls": [...]},
                "canned_tool_results": ["<tool result text>", ...]}]}

Each turn sends system + history + the new user message; the model's own answer is generated
and timed, then discarded, and the history grows by the **canned** assistant message and its
canned tool results, so every model sees the same conversation (as 02 §11.2's forced mode).

Trimming (:class:`TrimmingConversation`) is a **stand-in** policy modelled on Harbor's
``_fit_window`` (``service/agent/loop.py:1295``, ``:1470-1505``), labelled
``policy: "stand-in:fit_window-like-v1"`` until the harness exports its own policy. When the
templated prompt plus 256 reserved output tokens exceeds the window:

1. shorten older tool results first (oldest first, every turn but the last completed one) to
   their first 16 tokens plus ``" [shortened]"``;
2. then condense the oldest turns, one at a time (the last completed turn is always kept):
   they are dropped and a fixed digest sentence is appended to the system message once (it
   is the same text however many turns it stands for). Appending to the system message,
   rather than adding a message, keeps role alternation valid for strict chat templates and
   changes the prompt prefix as Harbor's digest does, which resets the prefix cache.

It trims until the prompt plus reserve is at most 75 % of the window (hysteresis, so a trim
is an occasional event and the 3 turns before it are untrimmed), and reports
``trim_event {tokens_removed, digest_tokens_added, prefix_changed}``: ``tokens_removed`` is the
gross count taken out (prompt before − after + digest added).

``prefix_changed`` (on every turn): the previous turn's messages, templated without the
generation prompt, are not a token prefix of this turn's prompt. ``cache_reset``:
``cached_tokens`` is at or below the fixed prefix: the current system message (with the digest
once it is there) and tools templated alone, plus the next message's role header, which any
two prompts share (``system_prefix_tokens`` on each sample). Shortening an old tool result
keeps the cache up to that result, so it is a trim without a reset.

Energy (:func:`run_long_session`): every turn is its own 20 Hz window over
``[t_submit, t_end]``; idle is measured once per 5 turns (§9.2 allows it): 60 s idle before
turns 0, 5, 10, … and 60 s after turns 4, 9, 14, … (and the last). Each turn's window records
``idle_ref = {"before": [t0, t1], "after": [t0, t1]}``, its group's two idle spans, and
:func:`turn_energies` subtracts their mean median power. The runner's usual 5 s gap follows
every turn, outside the windows. The prefix cache stays on. Content is not forced (§9.2:
"long sessions are task-like"); ``runner.request`` has no sampling argument, so the runner's
greedy settings are used rather than the author's (reported to the integrator).
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from typing import Any, Sequence

from .constants import LEAK_FLAG_FRACTION, LONG_SESSION_MIN_TRIMS
from .sustained import begin_window, end_window, rewrite, ttft_s, _sleep

LONG_SESSION_SCHEMA = "edat-long-session-v1"
POLICY = "stand-in:fit_window-like-v1"
DIGEST_TEXT = ("[Earlier conversation condensed] Earlier turns covered the user's previous requests and "
               "the tool results that answered them; their details were removed to fit the window.")
SHORTENED_MARK = " [shortened]"
_NS = 1_000_000_000


@dataclass
class TurnPrompt:
    index: int
    messages: list[dict]
    text: str
    tokens: int
    trim_event: dict | None
    prefix_changed: bool | None
    fits: bool
    prefix_tokens: int = 0         # the current fixed prefix (system message + tools + next role header)


@dataclass
class _Turn:
    messages: list[dict]
    shortened: set = field(default_factory=set)


def load_conversation(path_or_doc) -> dict:
    if isinstance(path_or_doc, dict):
        doc = path_or_doc
    else:
        with open(path_or_doc, encoding="utf-8") as f:
            doc = json.load(f)
    if doc.get("schema") != LONG_SESSION_SCHEMA:
        raise ValueError(f"not an {LONG_SESSION_SCHEMA} file: schema {doc.get('schema')!r}")
    if doc.get("lang") != "en":
        raise ValueError(f"long-session lang must be 'en', got {doc.get('lang')!r}")
    if not doc.get("turns"):
        raise ValueError("a long session needs turns")
    return doc


class TrimmingConversation:
    """Builds each turn's messages and applies the stand-in trimming policy."""

    def __init__(self, conv: dict, tokenizer, *, window_tokens: int | None = None, reserve_tokens: int = 256,
                 thinking: bool = False, tool_keep_tokens: int = 16, target_fraction: float = 0.75,
                 digest_text: str = DIGEST_TEXT):
        conv = load_conversation(conv)
        self.tok = tokenizer
        self.system = conv["system"]
        self.tools = list(conv.get("tools") or ())
        self.turns = list(conv["turns"])
        self.window = int(window_tokens or conv["window_tokens"])
        self.reserve = reserve_tokens
        self.thinking = thinking
        self.keep = tool_keep_tokens
        self.target = int(self.window * target_fraction)
        self.digest_text = digest_text
        self.history: list[_Turn] = []
        self.digest = False
        self.condensed_turns = 0
        self.policy = POLICY
        self._last_conv_ids: list[int] | None = None
        self.system_prefix_tokens = self._sys_tokens()
        a = self.tok.encode(self.render([self._system_msg(), {"role": "user", "content": "alpha"}], gen=False))
        b = self.tok.encode(self.render([self._system_msg(), {"role": "user", "content": "omega"}], gen=False))
        common = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        self.header_tokens = max(0, common - self.system_prefix_tokens)

    def _sys_tokens(self) -> int:
        return len(self.tok.encode(self.render([self._system_msg()], gen=False)))

    # -- building
    def _system_msg(self) -> dict:
        body = self.system + ("\n\n" + self.digest_text if self.digest else "")
        return {"role": "system", "content": body}

    def messages(self, user: str) -> list[dict]:
        out = [self._system_msg()]
        for t in self.history:
            out.extend(t.messages)
        out.append({"role": "user", "content": user})
        return out

    def render(self, msgs: list[dict], gen: bool = True) -> str:
        return self.tok.apply_chat_template(msgs, self.tools or None, add_generation_prompt=gen, thinking=self.thinking)

    def count(self, msgs: list[dict]) -> int:
        return len(self.tok.encode(self.render(msgs)))

    # -- trimming
    def _shorten_one(self) -> bool:
        for t in self.history[:-1]:
            for j, m in enumerate(t.messages):
                if m.get("role") != "tool" or j in t.shortened:
                    continue
                ids = self.tok.encode(m.get("content") or "")
                t.shortened.add(j)
                if len(ids) > self.keep:
                    t.messages[j] = {**m, "content": self.tok.decode(ids[:self.keep]) + SHORTENED_MARK}
                    return True
        return False

    def _condense_one(self) -> int | None:
        """Drop the oldest turn into the digest. Returns digest tokens added (0 after the first)."""
        if len(self.history) <= 1:
            return None
        self.history.pop(0)
        self.condensed_turns += 1
        if self.digest:
            return 0
        before = len(self.tok.encode(self.render([self._system_msg()], gen=False)))
        self.digest = True
        return len(self.tok.encode(self.render([self._system_msg()], gen=False))) - before

    def prepare(self, i: int) -> TurnPrompt:
        user = self.turns[i]["user"]
        msgs = self.messages(user)
        n = self.count(msgs)
        trim = None
        if n + self.reserve > self.window:
            before, added, ops = n, 0, []
            while n + self.reserve > self.target:
                if self._shorten_one():
                    ops.append("shorten_tool_result")
                else:
                    d = self._condense_one()
                    if d is None:
                        break
                    added += d
                    ops.append("condense_turn")
                msgs = self.messages(user)
                n = self.count(msgs)
            trim = {"tokens_removed": before - n + added, "digest_tokens_added": added, "prefix_changed": None,
                    "policy": self.policy, "ops": {o: ops.count(o) for o in sorted(set(ops))},
                    "tokens_before": before, "tokens_after": n}
        text = self.render(msgs)
        ids = self.tok.encode(text)
        changed = None
        if self._last_conv_ids is not None:
            changed = ids[:len(self._last_conv_ids)] != self._last_conv_ids
        if trim is not None:
            trim["prefix_changed"] = changed
        self._pending = msgs
        return TurnPrompt(index=i, messages=msgs, text=text, tokens=len(ids), trim_event=trim, prefix_changed=changed,
                          fits=len(ids) + self.reserve <= self.window,
                          prefix_tokens=self._sys_tokens() + self.header_tokens)

    def commit(self, i: int) -> None:
        """Add turn ``i``'s user message, canned assistant message and canned tool results."""
        t = self.turns[i]
        a = t.get("canned_assistant")
        am = {"role": "assistant", "content": a} if isinstance(a, str) or a is None else \
            {"role": "assistant", "content": a.get("content") or "", **({"tool_calls": a["tool_calls"]}
                                                                        if a.get("tool_calls") else {})}
        msgs = [{"role": "user", "content": t["user"]}, am]
        msgs += [{"role": "tool", "content": r if isinstance(r, str) else json.dumps(r, sort_keys=True)}
                 for r in t.get("canned_tool_results") or ()]
        self.history.append(_Turn(msgs))
        self._last_conv_ids = self.tok.encode(self.render(self._pending, gen=False))


# ----------------------------------------------------------------------------- driver

def run_long_session(runner, entry, mode: str, conv, tokenizer, *, min_trims: int = LONG_SESSION_MIN_TRIMS,
                     per_turn_window: bool = True, idle_every: int = 5, rep: int = 1,
                     max_turns: int | None = None, window_tokens: int | None = None) -> dict:
    """Run one session (test ``long_session_turn``), every turn sampled, prefix cache on.
    Returns ``{samples, windows, trims, short_of_min_trims}``."""
    thinking = entry.thinking == "on"
    if not isinstance(conv, TrimmingConversation):
        conv = TrimmingConversation(conv, tokenizer, thinking=thinking, window_tokens=window_tokens)
    runner.select(entry, mode)
    use_win = runner.i.power is not None
    idle_s = runner.cfg.idle_window_s
    n = len(conv.turns) if max_turns is None else min(max_turns, len(conv.turns))
    samples, windows, group = [], [], []
    before_span = None
    trims = 0
    if use_win and not per_turn_window:
        runner.open_window("long_session")
    for i in range(n):
        tp = conv.prepare(i)
        if tp.trim_event is not None:
            trims += 1
            runner.w.event(runner.now(), "trim", {"turn_index": i, **tp.trim_event})
        if use_win and per_turn_window:
            if i % idle_every == 0:
                runner.i.power.set_rate(20.0)
                a = runner.now()
                _sleep(runner, idle_s)
                before_span = [a, runner.now()]
            begin_window(runner, "long_session")
        s = runner.request(entry, mode, test="long_session_turn", phase="measured",
                           prompt={"text": tp.text, "messages": tp.messages, "tokens": tp.tokens}, level=None,
                           variant=None, rep=rep, gate=(i == 0), force_length=False,
                           extra={"turn_index": i, "trim_event": tp.trim_event, "replay_cache": "on",
                                  "prefix_changed": tp.prefix_changed, "trim_policy": conv.policy,
                                  "window_tokens": conv.window})
        s["context_tokens"] = s.get("prompt_tokens")
        if s.get("prompt_tokens") is not None:
            s["new_prefill_tokens"] = s["prompt_tokens"] - (s.get("cached_tokens") or 0)
        c = s.get("cached_tokens")
        s["system_prefix_tokens"] = tp.prefix_tokens
        s["cache_reset"] = (c is not None and c <= tp.prefix_tokens) if i > 0 else None
        rewrite(runner, s)
        samples.append(s)
        if use_win and per_turn_window:
            w = end_window(runner, idle_window_s=idle_s, write=False,
                           t_start_ns=s.get("t_submit_ns"), t_end_ns=s.get("t_end_ns"),
                           extra={"purpose": "long_session", "turn_index": i, "session_rep": rep,
                                  "entry_id": entry.entry_id, "mode": mode, "idle_group": i // idle_every})
            if w["t_start_ns"] is None:
                w["t_start_ns"] = runner._win_t0
            if w["t_end_ns"] is None:
                w["t_end_ns"] = runner.now()
            group.append(w)
            if i % idle_every == idle_every - 1 or i == n - 1 or s["status"] in ("oom_killed", "killed_unknown"):
                b = runner.now()
                _sleep(runner, idle_s)
                after_span = [b, runner.now()]
                for g in group:
                    g["idle_ref"] = {"before": before_span, "after": after_span}
                    runner.w.window(g)
                windows += group
                group = []
        conv.commit(i)
        if s["status"] in ("oom_killed", "killed_unknown", "not_run_above_oom"):
            break
    if use_win and not per_turn_window:
        windows.append(runner.close_window())
    short = trims < min_trims
    if short:
        runner.w.event(runner.now(), "operator_note", f"long session {entry.entry_id} rep {rep}: {trims} trims, "
                                                      f"fewer than {min_trims}; the script is too short")
    runner.conditions_extra.setdefault("long_sessions", []).append(
        {"entry_id": entry.entry_id, "mode": mode, "rep": rep, "turns": len(samples), "trims": trims,
         "window_tokens": conv.window, "policy": conv.policy, "short_of_min_trims": short,
         "system_prefix_tokens": conv.system_prefix_tokens, "idle_every": idle_every})
    return {"samples": samples, "windows": windows, "trims": trims, "short_of_min_trims": short}


# ----------------------------------------------------------------------------- derivation

def turn_energies(trace, windows: Sequence[dict]) -> dict[str, float | None]:
    """Active energy per long-session turn: window total minus the group's idle power (mean of
    the medians over its ``idle_ref`` spans) × duration. Keyed by the turn's sample id."""
    from .energy import integrate_J, median_power_W
    out: dict[str, float | None] = {}
    for w in windows:
        if w.get("purpose") != "long_session" or not w.get("sample_ids"):
            continue
        ref = w.get("idle_ref") or {}
        sides = []
        for k in ("before", "after"):
            span = ref.get(k)
            if span and span[1] > span[0]:
                try:
                    sides.append(median_power_W(trace, span[0], span[1]))
                except ValueError:
                    pass
        try:
            total = integrate_J(trace, w["t_start_ns"], w["t_end_ns"])
        except ValueError:
            total = None
        out[w["sample_ids"][0]] = (total - statistics.fmean(sides) * (w["t_end_ns"] - w["t_start_ns"]) / _NS
                                   if (total is not None and sides) else None)
    return out


def _decode_tps(s: dict) -> float | None:
    a, b, n = s.get("t_first_token_ns"), s.get("t_last_token_ns"), s.get("output_tokens")
    if not (isinstance(a, int) and isinstance(b, int) and isinstance(n, int)) or b <= a or n < 2:
        return None
    return (n - 1) / ((b - a) / _NS)


def _fp(s: dict) -> int | None:
    return s.get("footprint_steady_bytes") if s.get("footprint_steady_bytes") is not None else s.get(
        "footprint_end_bytes")


def derive_long_session(samples: Sequence[dict], energies: dict[str, float | None] | Sequence | None = None, *,
                        window_tokens: int | None = None) -> dict:
    """The §3.12 ``long_session`` verdict for one session.

    * ``trim_ttft_cost_s``: for each trim turn k with 3 turns before it, TTFT(k) − median TTFT of
      turns k−3..k−1; the median over trims. ``trim_energy_cost_J`` the same with energies.
      (The trim turn is the first request on the trimmed context: "the turn after a trim".)
    * ``plateau_footprint_bytes``: median steady footprint of the turns from the first trim on.
    * ``leak_flag``: footprint at the last trim turn > 1.05 × at the first; null with fewer than 2 trims.
    """
    ss = sorted((s for s in samples if s.get("test", "long_session_turn") == "long_session_turn"),
                key=lambda s: s.get("turn_index") or 0)
    if isinstance(energies, (list, tuple)):
        energies = {s["sample_id"]: e for s, e in zip(ss, energies)}
    energies = energies or {}
    trim_idx = [k for k, s in enumerate(ss) if s.get("trim_event")]
    ct, ce = [], []
    for k in trim_idx:
        if k < 3:
            continue
        prev = ss[k - 3:k]
        t_now, t_prev = ttft_s(ss[k]), [ttft_s(p) for p in prev]
        if t_now is not None and all(x is not None for x in t_prev):
            ct.append(t_now - statistics.median(t_prev))
        e_now, e_prev = energies.get(ss[k]["sample_id"]), [energies.get(p["sample_id"]) for p in prev]
        if e_now is not None and all(x is not None for x in e_prev):
            ce.append(e_now - statistics.median(e_prev))
    out: dict[str, Any] = {
        "window_tokens": window_tokens if window_tokens is not None else next(
            (s.get("window_tokens") for s in ss if s.get("window_tokens")), None),
        "turns": len(ss), "trims": len(trim_idx),
        "trim_ttft_cost_s": statistics.median(ct) if ct else None,
        "trim_energy_cost_J": statistics.median(ce) if ce else None,
        "n_trim_costs": len(ct), "plateau_footprint_bytes": None, "leak_flag": None,
        "ttft_by_context": [[s.get("context_tokens"), ttft_s(s)] for s in ss],
        "decode_by_context": [[s.get("context_tokens"), _decode_tps(s)] for s in ss],
        "cache_resets": sum(1 for s in ss if s.get("cache_reset")),
    }
    if not ce:
        out["trim_energy_cost_reason"] = "no_turn_energies" if not energies else "too_few_turns_before_trims"
    if trim_idx:
        fps = [_fp(s) for s in ss[trim_idx[0]:] if _fp(s) is not None]
        out["plateau_footprint_bytes"] = statistics.median(fps) if fps else None
        if len(trim_idx) >= 2:
            a, b = _fp(ss[trim_idx[0]]), _fp(ss[trim_idx[-1]])
            if a is not None and b is not None:
                out["leak_flag"] = b > a * (1 + LEAK_FLAG_FRACTION)
                out["leak_growth_frac"] = (b - a) / a if a else None
        else:
            out["leak_reason"] = "fewer_than_two_trims"
    return out
