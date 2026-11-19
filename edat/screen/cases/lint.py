"""The authoring lint (spec 11 §3.2, §3.8, S5).

Run over every screen the oracle visits on world seeds 1-3:

* **token cap**: a rendered observation over 3,000 tokens (length ÷ 3, Harbor
  ModelView's estimate) is refused. The spec asks for the cap under each
  reference model's own tokenizer; that check needs the tokenizers and is run
  where they are available (`token_counter` hook).
* **consequential labels**: a control whose label matches Harbor's
  consequential patterns (`browser-extension/shared/model-view.js:54-79`:
  logout, delete, unsubscribe, submit, confirm, accept, pay, checkout,
  purchase, quiz start, mark done; re-implemented here, plus send, publish,
  reset, erase, empty trash, place order) must be marked `irreversible` or
  explicitly `cleared`.
* **hosts and numbers**: every host in the case is `.example` (or the
  reserved `.invalid` / `.test`); phone numbers are 555 numbers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from edat.screen.cases.oracle import CHECK_SEEDS, run_oracle
from edat.screen.loop import ScreenLoop, TextObserver
from edat.screen.world.core import estimate_tokens

TOKEN_CAP = 3000
CONSEQUENTIAL = re.compile(
    r"\b(log ?out|sign ?out|delete|remove|unsubscribe|submit|confirm|accept|pay|checkout|check out|purchase|buy|"
    r"place order|send|publish|post|erase|reset|empty trash|cancel subscription|start quiz|mark (as )?done|"
    r"book and pay|forget)\b", re.IGNORECASE)
HOST = re.compile(r"\b(?:[a-z0-9-]+\.)+([a-z]{2,})\b(?=[/?\s\"']|$)", re.IGNORECASE)
OK_TLDS = {"example", "invalid", "test", "localhost"}
FILE_EXT = {"png", "jpg", "jpeg", "pdf", "txt", "doc", "docx", "csv", "zip", "mov", "mp3", "md", "key", "xlsx",
            "pages", "numbers", "heic", "gif", "json", "ics", "ml", "gb", "kb", "mb", "min", "am", "pm", "co"}
PHONE = re.compile(r"\b\d{3}[-. ]\d{4}\b")


@dataclass
class LintResult:
    case_id: str
    problems: list[str] = field(default_factory=list)
    max_tokens: int = 0
    screens: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


class _Recorder(TextObserver):
    def __init__(self, sink: list) -> None:
        self.sink = sink

    def render(self, world):
        vis = world.observe()
        self.sink.append(vis)
        return super().render(world)


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for x in obj:
            yield from _strings(x)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)


def lint_case(case: dict, seeds=CHECK_SEEDS, token_counter: Callable[[str], int] | None = None) -> LintResult:
    res = LintResult(case_id=case.get("id", "?"))
    count = token_counter or estimate_tokens
    flagged: set[str] = set()
    for s in seeds:
        seen: list = []
        try:
            run_oracle(case, s, max_steps=24, track_loop=ScreenLoop(_Recorder(seen)))
        except Exception as exc:
            res.problems.append(f"seed {s}: {type(exc).__name__}: {exc}")
            continue
        res.screens += len(seen)
        for vis in seen:
            t = count(vis.text)
            res.max_tokens = max(res.max_tokens, t)
            if t > TOKEN_CAP:
                res.problems.append(f"seed {s}: {vis.app}/{vis.window!r} renders {t} tokens (cap {TOKEN_CAP})")
            for el in list(vis.all_ids.values()):
                if el.meta.get("dock") or el.key in flagged:
                    continue
                if el.role in ("text", "heading", "group", "list", "image", "dialog", "alert", "cell"):
                    continue
                if el.name and CONSEQUENTIAL.search(el.name) and not el.irreversible and not el.cleared:
                    flagged.add(el.key)
                    res.problems.append(f"{el.key} {el.role} {el.name!r} looks consequential but is neither "
                                        "irreversible nor cleared")
    for text in _strings({k: v for k, v in case.items() if k not in ("oracle", "confirm_regex", "info_slots")}):
        for m in HOST.finditer(text):
            tld = m.group(1).lower()
            if tld not in OK_TLDS and tld not in FILE_EXT and not m.group(0)[0].isdigit():
                res.problems.append(f"host {m.group(0)!r} is not a reserved .example/.invalid/.test name")
        for m in PHONE.finditer(text):
            if not m.group(0).startswith("555"):
                res.problems.append(f"phone-like number {m.group(0)!r} is not a 555 number")
    return res
