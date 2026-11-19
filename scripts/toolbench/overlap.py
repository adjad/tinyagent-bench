#!/usr/bin/env python3
"""Held-out overlap: do a model's training rows contain Toolbench's held-out prompts?

Private, offline, read-only. Compares the hashed n-gram export of every
held-out (gate, voice) prompt (`run_toolbench.py --export-shingles`: the
prompts bound in the canonical world and 50 seeded ones, stored as hashes,
never as text) with the user turns of training JSONL files, tokenised and
hashed exactly the same way (cases._norm, cases._h).

    .venv/bin/python scripts/toolbench/overlap.py --shingles S.json --train A.jsonl [--train B.jsonl] --out R.json

For each held-out prompt it finds the training user turn sharing the most of
its n-grams (n = 6 words, or the prompt's whole length when shorter) and
reports that share as `containment` (1.0 = every 6-word run of the prompt
appears verbatim in one training turn). A full-prompt hash match is counted
apart. The report holds counts, item indices, training row ids (or line
numbers) and file hashes; never a prompt or a training string, so it can be
shared.

Training rows: a `messages` list (role/content, content a string or a list of
{type: text, text} parts) or a top-level `prompt` / `question` / `text` /
`user` string. Rows with no user text are counted, so a parsing miss shows.

What it cannot see: paraphrases (n-grams miss reworded questions; the r5
build's own 0.7 character-trigram check covers those), reuse of a case's
scenario in other words, and anything outside the files given.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.toolbench.cases import _h, _norm  # noqa: E402  (stdlib + yaml only)

N = 6
BANDS = (0.5, 0.8, 1.0)


def _user_texts(row) -> list[str]:
    if not isinstance(row, dict):
        return []
    out = []
    for m in row.get("messages") or ():
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, list):
                out += [p.get("text") for p in c if isinstance(p, dict) and isinstance(p.get("text"), str)]
    if not out:
        out = [row[k] for k in ("prompt", "question", "text", "user") if isinstance(row.get(k), str)]
    return out


def _grams(tokens: list[str], n: int) -> set[str]:
    return {_h(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def compare(shingles: dict, train_files: list[Path]) -> dict:
    items = shingles.get("items") or []
    full = defaultdict(list)                       # full hash -> item indices
    index: dict[tuple[int, str], set[int]] = defaultdict(set)   # (n, gram) -> items
    size, nof = [], []
    for i, it in enumerate(items):
        n = min(N, int(it["ntok"]))
        grams = set((it.get("sh") or {}).get(str(n)) or ())
        size.append(len(grams))
        nof.append(n)
        full[it["full"]].append(i)
        for g in grams:
            index[(n, g)].add(i)
    best = [0.0] * len(items)
    where: list[str | None] = [None] * len(items)
    exact: set[int] = set()
    rows = turns = empty = 0
    files = []
    sizes_needed = sorted(set(nof))
    for path in train_files:
        files.append({"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "bytes": path.stat().st_size})
        with path.open() as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    empty += 1
                    continue
                rows += 1
                texts = _user_texts(row)
                if not texts:
                    empty += 1
                    continue
                rid = str(row.get("id") or f"{path.name}:{line_no}") if isinstance(row, dict) else f"{path.name}:{line_no}"
                for text in texts:
                    tokens = _norm(text)
                    if not tokens:
                        continue
                    turns += 1
                    for i in full.get(_h(tokens), ()):
                        exact.add(i)
                        best[i], where[i] = 1.0, rid
                    hits: Counter = Counter()
                    for n in sizes_needed:
                        for g in _grams(tokens, n):
                            for i in index.get((n, g), ()):
                                hits[i] += 1
                    for i, k in hits.items():
                        share = k / size[i] if size[i] else 0.0
                        if share > best[i]:
                            best[i], where[i] = share, rid
    short = [i for i, n in enumerate(nof) if n < N]
    long_ = [i for i, n in enumerate(nof) if n >= N]

    def band(ids):
        return {f">={b}": sum(best[i] >= b for i in ids) for b in BANDS}
    top = sorted(range(len(items)), key=lambda i: -best[i])[:25]
    return {
        "method": f"word {N}-gram containment of each held-out prompt in its best-matching training user turn "
                  "(prompts under 6 words: their whole-length n-gram), plus full-prompt hash matches; "
                  "tokenisation and hashing as scripts/toolbench/cases.py",
        "held_out_prompts": len(items), "short_prompts_under_6_words": len(short),
        "training_files": files, "training_rows": rows, "training_user_turns": turns,
        "rows_without_user_text": empty,
        "exact_prompt_matches": len(exact),
        "containment_6gram_prompts": band(long_), "containment_short_prompts": band(short),
        "top": [{"item": i, "ntok": int(items[i]["ntok"]), "containment": round(best[i], 3),
                 "training_row": where[i]} for i in top if best[i] > 0],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shingles", required=True, help="the file run_toolbench.py --export-shingles wrote")
    ap.add_argument("--train", action="append", required=True, help="a training JSONL file (repeatable)")
    ap.add_argument("--out", required=True, help="where to write the report (counts and ids only)")
    args = ap.parse_args(argv)
    shingles = json.loads(Path(args.shingles).expanduser().read_text())
    report = compare(shingles, [Path(p).expanduser() for p in args.train])
    Path(args.out).expanduser().write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("held_out_prompts", "training_rows", "training_user_turns",
                                             "rows_without_user_text", "exact_prompt_matches",
                                             "containment_6gram_prompts", "containment_short_prompts")}, indent=2))
    # 0: no exact match and no 6-gram prompt at >= 0.8; 1: otherwise; 2: nothing was read.
    if not report["training_user_turns"]:
        return 2
    return 1 if (report["exact_prompt_matches"] or report["containment_6gram_prompts"][">=0.8"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
