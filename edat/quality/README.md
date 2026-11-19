# tinyagent-bench: output quality, text understanding and writing

Code for design spec 03 (`docs/benchmark/03-output-quality-spec.md`), English
only, version 1. Everything here is deterministic and stdlib-only. Nothing
loads a model or calls a service except through the shared model adapter's
`chat()` interface, and the tests use scripted stubs only.

## Modules

| Module | What it does | Spec |
| --- | --- | --- |
| `checkers.py` | Shared preference checkers P1–P9; rules in [CHECKERS.md](CHECKERS.md) | §4.5 |
| `textutil.py`, `normalize.py` | Markdown blocks, sentence splitter, emoji table, filler lists; date/time/amount/code/name detection, normalization and anchor matching | §4.2, §7.1 |
| `hygiene.py` | Layer-1 hygiene checks D1–D11, the shape classifier, the preference conflict rules, prose proxies | §4.1–4.2, §4.5, §5 |
| `world.py`, `generate.py` | Seeded world for Test User; planted facts, traps, five source types at four tiers; tasks and reference answers | §7.1–7.5, §7.7 |
| `render.py` | Primary and robustness renderings, the fenced condition, the fixed system prompt | §1, §7.3 |
| `schema_cases.py` | The dedicated structured-output cases | §8 |
| `noise.py` | Noisy-request variants | §7.8 |
| `scorers.py` | Recall, fabrication, traps, numeric accuracy, verbosity, compression, noise consistency | §6.2, §7.2, §7.6–7.8 |
| `structured.py`, `jsonschema_lite.py` | JSON extraction and schema scoring; a draft 2020-12 subset validator | §8 |
| `writing.py` | The Writing family's case contract, plan (342 cases), profile access and request renderer; rules in [WRITING.md](WRITING.md) | §9.1–9.2 |
| `writing_gen_draft.py`, `writing_gen_edit.py`, `writing_gen_style.py` | Brief generators: W1, W2, W6; W3, W4; W5, W7 | §9.1, §9.6 |
| `writing_checks.py` | The Writing checks WD1–WD11 and the case score | §9.3 |
| `grammar.py` | The offline grammar-signal wrapper (LanguageTool and Harper) and its rule-precision check | §9.3 |
| `tokens.py` | Token definitions shared by every tinyagent-bench part | §6.1 |
| `runner.py`, `records.py` | Runs an entry over a split through the model adapter; writes `edat.result/1.0` records | §1, §1.5 |
| `headline.py` | Text understanding, Readability and Writing, Δ against both references, noise side columns | §13.2 |
| `votes.py`, `stats.py` | The single-rater blind vote tool (general and writing blocks); Bradley–Terry with ties, style control, Wilson and bootstrap intervals, κ | §1.3, §4.3, §9.4 |
| `judge.py` | Judge rubrics (general and writing), prompts, swap-order judging, cache, probes (incl. the writing padding probe), validation against the author's votes | §4.4, §5, §9.4, §10 |
| `splits.py` | Builds and checks the dev and held-out splits, text and writing; overlap checks; case validators | §1.4 |

## Commands

```bash
.venv/bin/python -m edat.quality.splits build-dev
```

```bash
.venv/bin/python -m edat.quality.splits build-writing-dev
```

```bash
.venv/bin/python -m edat.quality.splits validate
```

```bash
.venv/bin/python -m edat.quality.splits validate-writing
```

```bash
EDAT_PRIVATE_DIR=<private folder> .venv/bin/python -m edat.quality.splits build-heldout
```

```bash
EDAT_PRIVATE_DIR=<private folder> .venv/bin/python -m edat.quality.splits build-writing-heldout
```

```bash
EDAT_PRIVATE_DIR=<private folder> .venv/bin/python -m edat.quality.splits overlap
```

```bash
EDAT_PRIVATE_DIR=<private folder> .venv/bin/python -m edat.quality.splits overlap-writing
```

```bash
.venv/bin/python -m edat.quality.votes --help
```

```bash
.venv/bin/python -m edat.quality run --help
```

```bash
.venv/bin/python -m edat.quality headline <run dir> ...
```

```bash
.venv/bin/python -m pytest -q tests/test_edat_quality_*.py
```

## Case counts (benchmark version 1)

Dev is about 25 % and held-out about 75 % of each family (spec §1.4). Dev is in
`test_fixtures/quality/dev/` (public seeds from 20270000; writing from
20280000). Held-out is generated from private random seeds into
`$EDAT_PRIVATE_DIR/quality/` and is never committed.

| Family | Dev | Held-out | Total |
| --- | --- | --- | --- |
| Source cases: email, thread, sheet, calendar, document × S, M, L, XL | 60 (3 per cell) | 180 (9 per cell) | 240 |
| Numeric cases: sum, comparison, count, date arithmetic, free slots | 15 (3 per kind) | 45 (9 per kind) | 60 |
| Schema cases: 7 shapes | 15 | 45 | 60 |
| Noisy request variants: typos, casual, voice, self-correction | 20 (5 per kind) | 60 (15 per kind) | 80 |
| Tasks in source, numeric and schema cases | 293 | 877 | 1,170 |
| Writing W1 Drafting from a brief (7 kinds) | 18 | 52 | 70 |
| Writing W2 Hard messages (6 kinds) | 12 | 36 | 48 |
| Writing W3 Rewriting (10 kinds) | 15 | 45 | 60 |
| Writing W4 Proofreading and editing (3 kinds) | 10 | 30 | 40 |
| Writing W5 Style control (5 kinds) | 14 | 40 | 54 |
| Writing W6 Audience and register (10 briefs × 4 audiences) | 12 (3 briefs) | 28 (7 briefs) | 40 |
| Writing W7 Longer pieces (3 kinds; outline cases take two turns) | 8 | 22 | 30 |
| **Writing, all** | **89** | **253** | **342** (+ 8 second turns) |

Each source case carries 3–6 tasks: an open summary, one or two point
questions, an extraction (JSON), and for emails a focused summary; sheets and
calendars add numeric questions. 20 % of cases also run a robustness
rendering, and cases with a planted instruction run fenced and unfenced.

About half of W1, W2, W5 and W6 are written as one of the three synthetic
profiles in `test_fixtures/bench_profiles/` (their contacts, threads, sent
messages and sign-offs); the rest use invented senders. The profile ledgers
never reach the model.

## Deviations and open points

- The spec names the preference library `edat.checks.prefs`; it is
  `edat.quality.checkers` here, as the build task assigned.
- P5 uses an embedded Unicode 15.1 emoji range table, not a pinned
  `emoji-data.txt`.
- `ref_tokens` falls back to an approximation of `o200k_base` (marked
  `approx-o200k/1` in every record) because `tiktoken` is not a dependency.
  The case generator always sizes sources with the approximation, so cases do
  not change when tiktoken is installed.
- The spec names one fixed benchmark system prompt but gives no text; the one
  used is in `render.system_prompt` (`edat-quality-system/1`). Writing cases use
  `writing.system_prompt` (`edat-writing-system/1`).
- Reference answers and reference drafts are generated from templates before
  any model runs (tilt T9); the author still has to re-read them.
- The readability qtype cases (12 qtypes × 20, spec §11 F1/F2) are not
  generated yet; hygiene runs on the summary, point and numeric answers.
- Facts with no anchor (meeting decisions) and semantic writing points are
  judge-only and kept out of deterministic recall; the judge's claim column is
  not built beyond its prompt and parser.
- The grammar signal (LanguageTool, Harper) is wrapped but neither tool is
  installed, so `grammar_errors_per_100w` is recorded as unavailable and the
  Writing constraint component has no grammar penalty. The 200-sentence rule
  precision set is not authored.
- Talia's and Camila's email sign-offs are not stated in their profiles; the
  generators use `Best, Talia` and `Kind regards, Camila` and the request
  states them. Marcus's comes from his own notes.
