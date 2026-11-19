# EDAT output quality and text understanding

Code for design spec 03 (`docs/benchmark/03-output-quality-spec.md`), English
only, version 1. Everything here is deterministic and stdlib-only; nothing
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
| `tokens.py` | Token definitions shared by every EDAT part | §6.1 |
| `runner.py`, `records.py` | Runs an entry over a split through the model adapter; writes `edat.result/1.0` records | §1, §1.5 |
| `headline.py` | Text understanding and Readability, Δ against both references, noise side columns | §13.2 |
| `votes.py`, `stats.py` | The single-rater blind vote tool; Bradley–Terry with ties, style control, Wilson and bootstrap intervals, κ | §1.3, §4.3 |
| `judge.py` | Judge rubric, prompts, swap-order judging, cache, probes, validation against the author's votes | §4.4, §5, §10 |
| `splits.py` | Builds and checks the dev and held-out splits; overlap check; case validator | §1.4 |

## Commands

```bash
.venv/bin/python -m edat.quality.splits build-dev
```

```bash
.venv/bin/python -m edat.quality.splits validate
```

```bash
EDAT_PRIVATE_DIR=<private folder> .venv/bin/python -m edat.quality.splits build-heldout
```

```bash
EDAT_PRIVATE_DIR=<private folder> .venv/bin/python -m edat.quality.splits overlap
```

```bash
.venv/bin/python -m edat.quality.votes --help
```

```bash
.venv/bin/python -m edat.quality headline <run dir> ...
```

```bash
.venv/bin/python -m pytest -q tests/test_edat_quality_*.py
```

## Case counts (benchmark version 1)

Dev is 25 % and held-out 75 % of each family (spec §1.4). Dev is in
`test_fixtures/quality/dev/` (public seeds from 20270000). Held-out is
generated from private random seeds into `$EDAT_PRIVATE_DIR/quality/` and is
never committed.

| Family | Dev | Held-out | Total |
| --- | --- | --- | --- |
| Source cases: email, thread, sheet, calendar, document × S, M, L, XL | 60 (3 per cell) | 180 (9 per cell) | 240 |
| Numeric cases: sum, comparison, count, date arithmetic, free slots | 15 (3 per kind) | 45 (9 per kind) | 60 |
| Schema cases: 7 shapes | 15 | 45 | 60 |
| Noisy request variants: typos, casual, voice, self-correction | 20 (5 per kind) | 60 (15 per kind) | 80 |
| Tasks in source, numeric and schema cases | 293 | 877 | 1,170 |

Each source case carries 3–6 tasks: an open summary, one or two point
questions, an extraction (JSON), and for emails a focused summary; sheets and
calendars add numeric questions. 20 % of cases also run a robustness
rendering, and cases with a planted instruction run fenced and unfenced.

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
  used is in `render.system_prompt` (`edat-quality-system/1`).
- Reference answers are generated from the fact table before any model runs
  (tilt T9), marked `reference_source: generated-template`; the author still
  has to re-read them.
- The readability qtype cases (12 qtypes × 20, spec §11 F1/F2) are not
  generated yet; hygiene runs on the summary, point and numeric answers.
- Facts with no anchor (meeting decisions) are marked `judge_only` and kept
  out of deterministic recall; the judge's claim column is not built beyond
  its prompt and parser.
