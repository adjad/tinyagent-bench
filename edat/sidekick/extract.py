"""Answer extraction and placeholder detection (spec 12 §3.2, build item S4).

The visible answer is the final message with the reasoning block removed (03
§1). From it the harness takes the first fenced code block whose language tag
matches what was asked; with no matching fence and a one-line answer, that
line is used; otherwise extraction fails (SK-14).
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

LANG_TAGS = {
    "python": {"python", "py", "python3"},
    "javascript": {"js", "javascript", "node", "mjs", "cjs"},
    "shell": {"sh", "zsh", "bash", "shell", "console", "terminal", "shell-session"},
    "git": {"sh", "zsh", "bash", "shell", "console", "terminal", "git"},
    "regex": {"regex", "regexp", "re", "python", "py", "text", "txt"},
    "text": {"text", "txt", "markdown", "md", "git", "gitcommit", "commit", "diff"},
}

THINK_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>\s*", re.S | re.I)
UNCLOSED_THINK_RE = re.compile(r"^\s*<(think|thinking|reasoning)>.*", re.S | re.I)
FENCE_RE = re.compile(r"^[ \t]*(```+|~~~+)[ \t]*([\w+.#-]*)[^\n]*\n(.*?)^[ \t]*\1[ \t]*$", re.S | re.M)


def visible_answer(text: str | None) -> str:
    """Final message with documented reasoning blocks removed."""
    t = text or ""
    t = THINK_RE.sub("", t)
    if UNCLOSED_THINK_RE.match(t):
        return ""          # a reasoning block that never closed leaves no visible answer
    # Gemma-style channel markers
    t = re.sub(r"<\|channel\|>thought.*?<\|message\|>", "", t, flags=re.S)
    return t.strip()


@dataclass
class Extraction:
    ok: bool
    snippet: str = ""
    how: str = ""          # fence | fence_untagged | single_line | none
    reason: str = ""


def fences(text: str) -> list[tuple[str, str]]:
    return [(m.group(2).lower(), m.group(3)) for m in FENCE_RE.finditer(text)]


def extract(answer: str, kind: str) -> Extraction:
    """kind: python | javascript | shell | git | regex | text."""
    vis = visible_answer(answer)
    if not vis:
        return Extraction(False, reason="empty answer")
    tags = LANG_TAGS.get(kind, set())
    blocks = fences(vis)
    for tag, body in blocks:
        if tag in tags:
            return Extraction(True, body.rstrip("\n"), "fence")
    for tag, body in blocks:
        if tag == "":
            return Extraction(True, body.rstrip("\n"), "fence_untagged")
    if blocks:
        return Extraction(False, reason=f"no fence tagged for {kind} (saw {', '.join(t or '-' for t, _ in blocks)})")
    lines = [l for l in vis.splitlines() if l.strip()]
    if len(lines) == 1:
        line = lines[0].strip()
        # Inline code spans on a single line: `cmd`
        m = re.fullmatch(r"`([^`]+)`\.?", line)
        if m:
            line = m.group(1)
        return Extraction(True, line, "single_line")
    if kind in ("shell", "git", "regex"):
        # A single inline code span in prose is the answer a user would copy.
        spans = re.findall(r"`([^`\n]+)`", vis)
        if len(spans) == 1:
            return Extraction(True, spans[0].strip(), "inline_span")
    return Extraction(False, reason="no fenced block and more than one line")


def regex_from_snippet(snippet: str) -> str:
    """Take a bare pattern out of the forms models write: r'...', /.../, re.compile(r"...")."""
    s = snippet.strip()
    lines = [l for l in s.splitlines() if l.strip()]
    if len(lines) > 1:
        # Several lines: Python code (or a pattern with a comment line above it).
        code_lines = [l for l in lines if not l.strip().startswith("#")]
        if len(code_lines) == 1:
            lines = code_lines
    if len(lines) > 1:
        # Python code: find the first string literal passed to re.* or assigned.
        try:
            tree = ast.parse(s)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    return node.value
        except SyntaxError:
            pass
        s = lines[0].strip()
    else:
        s = lines[0].strip() if lines else ""
    m = re.fullmatch(r"(?:\w+\s*=\s*)?(?:re\.(?:compile|fullmatch|match|search)\()?\s*([rRbBuU]{0,2})(['\"])(.*)\2\s*(?:,.*)?\)?", s)
    if m:
        try:
            return ast.literal_eval(m.group(1) + m.group(2) + m.group(3) + m.group(2))
        except (ValueError, SyntaxError):
            return m.group(3)
    m = re.fullmatch(r"/(.+)/[gimsuy]*", s)
    if m:
        return m.group(1)
    return s


# ------------------------------------------------------------- placeholders

PLACEHOLDER_RES = [
    re.compile(r"<(?!<)(?:[A-Za-z][\w .-]{0,30})>(?![>&])"),     # <branch>, <file name>
    re.compile(r"\byour[-_ ]?(?:file|branch|repo|folder|dir|directory|name|commit|path|hash|sha|remote|message|email|pattern|string)\w*", re.I),
    re.compile(r"\bpath/to/\S*", re.I),
    re.compile(r"\b(?:YOUR|MY)_[A-Z_]+\b"),
    re.compile(r"\{(?:branch|file|filename|name|commit|sha|hash|path|folder|dir)\}", re.I),
    re.compile(r"\b(?:commit[-_]?hash|commit[-_]?sha|branch[-_]name|file[-_]name|filename\.ext|example\.txt)\b", re.I),
]


def find_placeholders(command: str, given_names: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Placeholder tokens left in a command. Names the prompt gave are never placeholders."""
    hits = []
    spans: list[tuple[int, int]] = []
    stripped = re.sub(r"<<-?\s*'?(\w+)'?", "", command)      # heredoc markers
    stripped = re.sub(r"\s<\s", " ", stripped)                 # input redirects
    for rx in PLACEHOLDER_RES:
        for m in rx.finditer(stripped):
            tok = m.group(0)
            if any(n and n in tok for n in given_names):
                continue
            if tok.startswith("<") and re.fullmatch(r"<\(.*", tok):
                continue
            if any(a <= m.start() and m.end() <= b for a, b in spans):
                continue           # already inside a placeholder found earlier
            spans.append((m.start(), m.end()))
            hits.append(tok)
    return hits
