"""Token accounting (spec 03 section 6.1, the EDAT-wide definitions).

- `visible_tokens`, `reasoning_tokens`, `toolcall_tokens`: re-tokenized with the
  model's own tokenizer, never taken from the server's usage field. The runner
  passes a `count_model_tokens(text) -> int` callable for the model; without
  one, these fields are null and `token_source` says why.
- `ref_tokens`: one fixed reference tokenizer for every model, `o200k_base` from
  tiktoken (pinned version recorded). tiktoken is not installed in this
  environment, so `ref_tokens()` falls back to a documented approximation and
  says so in `ref_tokenizer_id()`. Records carry that id so approximate counts
  are never mixed with exact ones.
- Cloud references: visible and reasoning tokens come from the provider's usage
  report (`token_source = provider_usage`); a missing reasoning count is None,
  never 0.

`approx_tokens()` is also what the case generator uses to size sources, so
generated cases do not depend on whether tiktoken happens to be installed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

try:  # pragma: no cover - depends on the environment
    import tiktoken as _tiktoken
    _ENC = _tiktoken.get_encoding("o200k_base")
    _TIKTOKEN_VERSION = getattr(_tiktoken, "__version__", "unknown")
except Exception:  # noqa: BLE001
    _tiktoken = None
    _ENC = None
    _TIKTOKEN_VERSION = None

APPROX_ID = "approx-o200k/1"
_PIECE = re.compile(r"\s?[A-Za-z]+|\s?\d{1,3}|\s?[^\sA-Za-z\d]|\s+")


def approx_tokens(text: str) -> int:
    """A deterministic stand-in for o200k_base on English text.

    Words of up to 8 letters count as one token, longer words as one token per
    6 further letters; digits are grouped in threes; every other non-space
    character is one token. Within a few percent of o200k on plain English.
    """
    n = 0
    for p in _PIECE.findall(text or ""):
        s = p.strip()
        if not s:
            continue
        if s.isalpha():
            n += 1 + max(0, (len(s) - 3)) // 6
        else:
            n += 1
    return n


def ref_tokenizer_id() -> str:
    return f"o200k_base@tiktoken-{_TIKTOKEN_VERSION}" if _ENC is not None else APPROX_ID


def ref_tokens(text: str) -> int:
    if _ENC is not None:
        return len(_ENC.encode(text or "", disallowed_special=()))
    return approx_tokens(text)


@dataclass
class TokenCounts:
    visible_tokens: int | None
    reasoning_tokens: int | None
    toolcall_tokens: int | None
    ref_tokens: int
    ref_tokenizer: str
    token_source: str               # model_tokenizer | provider_usage | unavailable
    completion_tokens: int | None
    consistency_gap: float | None   # |sum - completion| / completion
    needs_inspection: bool

    def to_metrics(self) -> dict:
        return {"visible_tokens": self.visible_tokens, "reasoning_tokens": self.reasoning_tokens,
                "toolcall_tokens": self.toolcall_tokens, "ref_tokens": self.ref_tokens,
                "token_source": self.token_source, "token_consistency_gap": self.consistency_gap,
                "token_needs_inspection": self.needs_inspection}


def count_tokens(visible: str, reasoning: str, *, toolcall_text: str = "",
                 count_model_tokens: Callable[[str], int] | None = None,
                 usage: dict | None = None, provider_usage: bool = False) -> TokenCounts:
    """Apply 03 s6.1. `usage` is the engine's usage dict (completion/reasoning tokens)."""
    usage = usage or {}
    completion = usage.get("completion_tokens")
    if provider_usage:
        rt = usage.get("reasoning_tokens")
        vt = (completion - rt) if (completion is not None and rt is not None) else completion
        return TokenCounts(vt, rt, None, ref_tokens(visible), ref_tokenizer_id(), "provider_usage",
                           completion, None, False)
    if count_model_tokens is None:
        return TokenCounts(None, None, None, ref_tokens(visible), ref_tokenizer_id(), "unavailable",
                           completion, None, False)
    vt = count_model_tokens(visible) if visible else 0
    rt = count_model_tokens(reasoning) if reasoning else 0
    tt = count_model_tokens(toolcall_text) if toolcall_text else 0
    gap = None
    flag = False
    if completion:
        gap = abs((vt + rt + tt) - completion) / completion
        flag = gap > 0.02
    return TokenCounts(vt, rt, tt, ref_tokens(visible), ref_tokenizer_id(), "model_tokenizer",
                       completion, gap, flag)


def tokens_to_answer(visible: str, anchor_char_offset: int | None) -> int | None:
    """`ref_tokens` before the answer key's anchor first appears."""
    if anchor_char_offset is None:
        return None
    return ref_tokens(visible[:anchor_char_offset])


def thinking_overhead(reasoning_tokens: int | None, visible_tokens: int | None) -> float | None:
    if reasoning_tokens is None or not visible_tokens:
        return None
    return reasoning_tokens / visible_tokens
