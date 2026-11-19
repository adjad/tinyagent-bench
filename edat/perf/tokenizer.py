"""Tokenizers for the prompt-pack builder.

``ModelTokenizer`` is the narrow interface the builder needs: encode, decode (special
tokens kept), the model's chat template, and the two hashes the pack records.
``HFTokenizer`` reads a Hugging Face model directory (``tokenizer.json`` through the
``tokenizers`` package, the chat template through ``jinja2``; both imported lazily).
``StubTokenizer`` is a deterministic, file-free stand-in for tests.

Choices (not dictated by the spec):

* The chat template is rendered the way ``transformers`` renders it: an immutable sandboxed
  jinja environment with ``trim_blocks`` and ``lstrip_blocks``, the ``loopcontrols`` extension,
  a ``raise_exception`` global and a ``tojson`` filter that does not escape non-ASCII.
* ``strftime_now`` returns a FIXED date (2026-01-01 00:00:00) so a template that prints the
  date yields the same tokens on every build.
* ``tokenizer_sha256`` is the sha256 of the ``tokenizer.json`` bytes; ``template_sha256`` is
  the sha256 of the template source text (UTF-8) from ``chat_template.jinja``, else
  ``chat_template.json``, else ``tokenizer_config.json``.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

Message = dict[str, Any]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@runtime_checkable
class ModelTokenizer(Protocol):
    """What the pack builder needs from a model's tokenizer and chat template."""

    tokenizer_sha256: str
    template_sha256: str

    def encode(self, text: str) -> list[int]:
        """Token ids of ``text``; no special tokens are added (special-token strings in the
        text are still recognised)."""
        ...

    def decode(self, ids: Sequence[int]) -> str:
        """Text of ``ids`` with special tokens kept."""
        ...

    def apply_chat_template(self, messages: Sequence[Message], tools: Sequence[dict] | None = None, *,
                            add_generation_prompt: bool = True, thinking: bool) -> str:
        """The templated prompt as text (not tokenized)."""
        ...


# --------------------------------------------------------------------------- Hugging Face

def _token_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("content", ""))
    return str(value)


def _pick_template(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):  # [{"name": "default", "template": "..."}, ...]
        by_name = {str(d.get("name")): d.get("template") for d in value if isinstance(d, dict)}
        return by_name.get("default") or next(iter(by_name.values()), None)
    return None


class HFTokenizer:
    """A model directory's ``tokenizer.json`` plus its chat template."""

    def __init__(self, tokenizer: Any, template_source: str, *, bos_token: str = "", eos_token: str = "",
                 tokenizer_sha256: str, template_sha256: str) -> None:
        self._tok = tokenizer
        self.template_source = template_source
        self.bos_token = bos_token
        self.eos_token = eos_token
        self.tokenizer_sha256 = tokenizer_sha256
        self.template_sha256 = template_sha256
        self._compiled: Any = None

    @classmethod
    def from_model_dir(cls, path: str | Path) -> "HFTokenizer":
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError("the 'tokenizers' package is required: pip install tokenizers") from exc
        root = Path(path)
        tj = root / "tokenizer.json"
        if not tj.is_file():
            raise FileNotFoundError(f"no tokenizer.json in {root}")
        tj_bytes = tj.read_bytes()
        config: dict[str, Any] = {}
        cfg_path = root / "tokenizer_config.json"
        if cfg_path.is_file():
            config = json.loads(cfg_path.read_text(encoding="utf-8"))
        source: str | None = None
        jinja_path = root / "chat_template.jinja"
        json_path = root / "chat_template.json"
        if jinja_path.is_file():
            source = jinja_path.read_text(encoding="utf-8")
        elif json_path.is_file():
            source = _pick_template(json.loads(json_path.read_text(encoding="utf-8")).get("chat_template"))
        if source is None:
            source = _pick_template(config.get("chat_template"))
        if source is None:
            raise FileNotFoundError(f"no chat template in {root} (chat_template.jinja, chat_template.json "
                                    "or tokenizer_config.json['chat_template'])")
        return cls(Tokenizer.from_str(tj_bytes.decode("utf-8")), source,
                   bos_token=_token_text(config.get("bos_token")), eos_token=_token_text(config.get("eos_token")),
                   tokenizer_sha256=sha256_hex(tj_bytes), template_sha256=sha256_hex(source.encode("utf-8")))

    def encode(self, text: str) -> list[int]:
        return list(self._tok.encode(text, add_special_tokens=False).ids)

    def decode(self, ids: Sequence[int]) -> str:
        return self._tok.decode(list(ids), skip_special_tokens=False)

    def _template(self) -> Any:
        if self._compiled is None:
            try:
                from jinja2.ext import loopcontrols  # noqa: F401  (import check only)
                from jinja2.sandbox import ImmutableSandboxedEnvironment
            except ImportError as exc:
                raise RuntimeError("rendering a Hugging Face chat template needs jinja2; "
                                   "install it with: pip install jinja2") from exc

            def raise_exception(message: str) -> None:
                raise ValueError(message)

            def tojson(x: Any, ensure_ascii: bool = False, indent: int | None = None,
                       separators: Any = None, sort_keys: bool = False) -> str:
                return json.dumps(x, ensure_ascii=ensure_ascii, indent=indent,
                                  separators=separators, sort_keys=sort_keys)

            def strftime_now(fmt: str) -> str:
                return datetime(2026, 1, 1).strftime(fmt)

            env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True,
                                                extensions=["jinja2.ext.loopcontrols"])
            env.filters["tojson"] = tojson
            env.globals["raise_exception"] = raise_exception
            env.globals["strftime_now"] = strftime_now
            self._compiled = env.from_string(self.template_source)
        return self._compiled

    def apply_chat_template(self, messages: Sequence[Message], tools: Sequence[dict] | None = None, *,
                            add_generation_prompt: bool = True, thinking: bool) -> str:
        return self._template().render(
            messages=list(messages), tools=list(tools) if tools else None,
            add_generation_prompt=add_generation_prompt, enable_thinking=thinking,
            bos_token=self.bos_token, eos_token=self.eos_token, documents=None)


# --------------------------------------------------------------------------- stub

STUB_TEMPLATE_DOC = (
    "edat-stub-template-v1: '<bos>' then for each message '<turn>{role}\\n{content}</turn>\\n' "
    "(system content is followed by '\\n[tools] <json>' when tools are given; an assistant message's "
    "tool_calls are appended as '\\n[calls] <json>'); generation prompt '<turn>model\\n' then "
    "'<think>\\n' (thinking) or '<nothink>\\n'.")

_STUB_SPECIALS = {"<bos>": 2, "<turn>": 105, "</turn>": 106, "<think>": 107, "<nothink>": 108}
_STUB_SPECIAL_BY_ID = {v: k for k, v in _STUB_SPECIALS.items()}
_STUB_TOKEN_RE = re.compile(
    r"(<bos>|<turn>|</turn>|<think>|<nothink>)|((?:(?!<bos>|<turn>|</turn>|<think>|<nothink>)\S)+)")


class StubTokenizer:
    """Deterministic tokenizer with no files: one token per whitespace-separated word, and the
    strings ``<bos> <turn> </turn> <think> <nothink>`` as single tokens.

    Ids are a stable hash of the word, so two instances agree. Whitespace is not kept, which
    makes ``encode(decode(ids)) == ids`` for every id list the stub itself produced.

    ``merge_quirk=True`` adds one BPE-like merge: a word ending in ``.`` that is IMMEDIATELY
    followed by ``</turn>`` (no whitespace) becomes one token. Pieces tokenized separately
    (body and suffix of a pack level) are never merged, so cutting a body after such a word
    makes ``encode(decode(ids))`` one token shorter: the case the pack builder must adjust.
    """

    def __init__(self, *, merge_quirk: bool = False) -> None:
        self.merge_quirk = merge_quirk
        self._rev: dict[int, str] = {}
        base = b"edat-stub-tokenizer-v1" + (b"+merge-quirk" if merge_quirk else b"")
        self.tokenizer_sha256 = sha256_hex(base)
        self.template_sha256 = sha256_hex(STUB_TEMPLATE_DOC.encode("utf-8"))

    def _word_id(self, word: str) -> int:
        wid = 1000 + int.from_bytes(hashlib.blake2b(word.encode("utf-8"), digest_size=5).digest(), "big")
        known = self._rev.setdefault(wid, word)
        if known != word:  # pragma: no cover - 2^-40 odds
            raise RuntimeError(f"stub token id collision: {known!r} vs {word!r}")
        return wid

    def encode(self, text: str) -> list[int]:
        found = [(m.group(0), m.start(), m.end(), m.group(1) is not None)
                 for m in _STUB_TOKEN_RE.finditer(text)]
        out: list[int] = []
        i = 0
        while i < len(found):
            s, _start, end, special = found[i]
            if special:
                out.append(_STUB_SPECIALS[s])
            else:
                if (self.merge_quirk and s.endswith(".") and i + 1 < len(found)
                        and found[i + 1][0] == "</turn>" and found[i + 1][1] == end):
                    s = s + "</turn>"
                    i += 1
                out.append(self._word_id(s))
            i += 1
        return out

    def decode(self, ids: Sequence[int]) -> str:
        parts: list[str] = []
        prev_word = False
        for tid in ids:
            special = _STUB_SPECIAL_BY_ID.get(tid)
            if special is not None:
                parts.append(special)
                prev_word = False
                continue
            word = self._rev.get(tid)
            if word is None:
                raise ValueError(f"stub tokenizer cannot decode id {tid}: it did not produce it")
            parts.append((" " if prev_word else "") + word)
            prev_word = True
        return "".join(parts)

    def apply_chat_template(self, messages: Sequence[Message], tools: Sequence[dict] | None = None, *,
                            add_generation_prompt: bool = True, thinking: bool) -> str:
        out = ["<bos>"]
        for m in messages:
            body = m.get("content") or ""
            if not isinstance(body, str):
                body = json.dumps(body)
            if m["role"] == "system" and tools:
                body += "\n[tools] " + json.dumps(list(tools), sort_keys=True)
            if m.get("tool_calls"):
                body += "\n[calls] " + json.dumps(m["tool_calls"], sort_keys=True)
            out.append(f"<turn>{m['role']}\n{body}</turn>\n")
        if add_generation_prompt:
            out.append("<turn>model\n" + ("<think>\n" if thinking else "<nothink>\n"))
        return "".join(out)
