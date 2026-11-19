"""Deterministic ranking, snippets and chunking (spec 15 §3.3, §3.4).

The same query always gives the same list, whoever asks: two-field BM25
(k1 1.2, b 0.75): a page's title and description as one field weighted 2,
its body as another weighted 1. Tokens are
lower-cased, accent-folded, split on anything that is not a letter or digit,
with a fixed stop list and no stemming. Scores are rounded before sorting and
ties break on a caller-given prior (the captured rank), then on the id.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

RANKER = "edat-bm25f/v1"
CHUNKER = "edat-chunk-800/v1"
K1, B = 1.2, 0.75
HEAD_WEIGHT = 2.0
SNIPPET_CHARS = 240
CHUNK_CHARS = 800

STOPWORDS = frozenset("""
a an and are as at be been but by can could did do does for from had has have how i if in into is it
its me my of on or our so than that the their then there these they this to was we were what when where
which who why will with would you your about
""".split())

_SPLIT = re.compile(r"[^0-9a-z]+")


def fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def tokenize(text: str) -> list[str]:
    return [t for t in _SPLIT.split(fold(text)) if t and t not in STOPWORDS]


@dataclass(frozen=True)
class Doc:
    id: str
    title: str = ""
    description: str = ""
    text: str = ""

    def head_tokens(self) -> list[str]:
        return tokenize(self.title) + tokenize(self.description)

    def body_tokens(self) -> list[str]:
        return tokenize(self.text)


class _Field:
    def __init__(self, token_lists: list[list[str]]) -> None:
        self.tf: list[dict[str, int]] = []
        self.len: list[int] = []
        df: dict[str, int] = {}
        for toks in token_lists:
            counts: dict[str, int] = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            for t in counts:
                df[t] = df.get(t, 0) + 1
            self.tf.append(counts)
            self.len.append(len(toks))
        n = len(token_lists)
        self.avg = (sum(self.len) / n) if n else 0.0
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, i: int, q: list[str], k1: float, b: float) -> float:
        tf, ln, s = self.tf[i], self.len[i], 0.0
        for t in q:
            f = tf.get(t)
            if f:
                s += self.idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * ln / (self.avg or 1)))
        return s


class BM25Index:
    """Two-field BM25: title plus description (weight `head_weight`) and body
    (weight 1), each field with its own length normalisation, so a long page
    is not buried under short ones that merely mention the query words."""

    def __init__(self, docs: Iterable[Doc], *, k1: float = K1, b: float = B, head_weight: float = HEAD_WEIGHT) -> None:
        self.docs = list(docs)
        self.k1, self.b, self.head_weight = k1, b, head_weight
        self._head = _Field([d.head_tokens() for d in self.docs])
        self._body = _Field([d.body_tokens() for d in self.docs])

    def score(self, query: str) -> list[float]:
        q = list(dict.fromkeys(tokenize(query)))
        return [round(self.head_weight * self._head.score(i, q, self.k1, self.b)
                      + self._body.score(i, q, self.k1, self.b), 9) for i in range(len(self.docs))]

    def search(self, query: str, k: int, *, prior: dict[str, int] | None = None) -> list[tuple[str, float]]:
        prior = prior or {}
        scored = [(s, d.id) for s, d in zip(self.score(query), self.docs) if s > 0]
        scored.sort(key=lambda x: (-x[0], prior.get(x[1], 10**6), x[1]))
        return [(i, s) for s, i in scored[:max(0, k)]]


def cut_at_word(text: str, limit: int) -> str:
    t = " ".join(str(text or "").split())
    if len(t) <= limit:
        return t
    cut = t[:limit]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit // 2 else cut).rstrip(" ,;:") + "…"


def snippet(description: str, text: str, limit: int = SNIPPET_CHARS) -> str:
    """The page's description, else the start of its body (headings and link
    syntax kept as text), cut at a word boundary."""
    if str(description or "").strip():
        return cut_at_word(description, limit)
    body = " ".join(line.lstrip("#>*- ").strip() for line in str(text or "").splitlines() if line.strip())
    return cut_at_word(body, limit)


def window(text: str, offset: int, limit: int) -> tuple[str, int | None]:
    """Characters [offset, offset+limit) cut at the last line break in the
    second half of the window; returns the text and the next offset (or None)."""
    offset = max(0, int(offset or 0))
    if offset >= len(text):
        return "", None
    end = offset + max(1, int(limit))
    if end >= len(text):
        return text[offset:], None
    nl = text.rfind("\n", offset + limit // 2, end)
    if nl != -1:
        end = nl + 1
    return text[offset:end], end


def chunk_text(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Split on blank-line paragraphs, packing whole paragraphs up to
    `max_chars`; a paragraph longer than that is split at sentence ends, then
    at spaces. No overlap. Deterministic."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", str(text or "")) if p.strip()]
    pieces: list[str] = []
    for p in paras:
        if len(p) <= max_chars:
            pieces.append(p)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", p)
        buf = ""
        for s in sentences:
            while len(s) > max_chars:
                sp = s.rfind(" ", 0, max_chars)
                sp = sp if sp > 0 else max_chars
                if buf:
                    pieces.append(buf)
                    buf = ""
                pieces.append(s[:sp].strip())
                s = s[sp:].strip()
            if buf and len(buf) + 1 + len(s) > max_chars:
                pieces.append(buf)
                buf = s
            else:
                buf = f"{buf} {s}".strip()
        if buf:
            pieces.append(buf)
    chunks: list[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + 2 + len(piece) <= max_chars:
            chunks[-1] = chunks[-1] + "\n\n" + piece
        else:
            chunks.append(piece)
    return chunks
