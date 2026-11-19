# Preference checkers P1–P9

`edat/quality/checkers.py`, version `edat-prefs/1.0`. The shared deterministic
checkers for the nine format and style preferences (design spec 03 §4.5,
standing preferences 1–9). Every tier that grades text calls these; none of
them calls a model.

```python
from edat.quality.checkers import check, check_all, Meta

check("P6", "The train leaves at 15:40.", "answer", {"source_times": ["15:40"]})
# CheckResult(pref_id="P6", status="pass", reason="1 times", spans=[], notes=[])
```

`check(pref_id, text, surface, meta) -> CheckResult` with `status` one of
`pass`, `fail`, `n/a`, a `reason`, the failing character `spans` and
`notes` (reported but never failing, e.g. text emoticons).

## Inputs

`surface` is one of `answer`, `draft_email`, `draft_message`, `json`,
`tool_args`. A checker returns `n/a` on a surface outside its scope (that is
how the scope dimension is tested).

`meta` is a `Meta` or a plain dict; unknown keys are ignored. Every field is
optional and is set by the case author, never guessed from wording.

| Field | Used by | Meaning |
| --- | --- | --- |
| `answer_key` | P1 | Anchors of the answer: strings (`"15:40"`, `"QX-4471"`) or `{"type", "value", "aliases"}` |
| `request` | P1 | The user's request (restatement test) |
| `detail_requested` | P2 | `true` makes P2 `n/a` |
| `expected_items` | P3 | Number of parallel items the answer should list |
| `qtype` | P3 | Numbered lists count only for `steps` |
| `source_times` | P6 | `HH:MM` times in the source |
| `source_dates` | P7 | ISO dates in the source (reference only) |
| `source_quantities` | P8 | Imperial source quantities, `[{"value": 5, "unit": "mi"}]` |
| `case_year` | P7 | The case's year |
| `active` | all | `false` on a one-off override turn: every checker returns `n/a` |
| `signoff` | P9 | The person's sign-off, may span lines |
| `source_text` | P5–P8 | Quoted spans and block quotes that reproduce it verbatim are exempt |
| `needs_time`, `needs_date` | P6, P7 | Fail when no time/date is written but one was needed (default: `n/a`) |

## Rules

Scope: P1–P3 `answer`; P4–P8 `answer`, `draft_email`, `draft_message`; P9
`draft_email`. `json` and `tool_args` are always `n/a` (schema formats govern
structured output, spec 03 §8).

**P1 Lead with the answer** (the same function as hygiene check D4,
`answer_first`). The *first unit* is the first sentence, or the first list
item when the answer opens with a list; a lead-in line ending in `:` directly
above a list is joined with the first item. Fail if the first unit is empty or
is a filler opener (list in `textutil.FILLER_OPENERS`). Pass if an answer-key
anchor (normalized: `15:00` = `3 pm`, `Tue 19 Nov` = `2024-11-19`, `1,250.40`
= `1250.40`) appears in the first unit and starts before word 30 of the
answer. Otherwise fail; the reason says "restates the request" when the first
unit's content tokens overlap the request's by ≥ 0.6 both ways. `n/a` without
an answer key.

**P2 At most three sentences unless asked for detail.** `n/a` if
`detail_requested`. Units = prose sentences outside lists (shared splitter)
+ 1 per list block whose items all have ≤ 20 words. In a list with longer
items, each long item counts as its own sentences and the short items count
as one unit together. Code blocks are not counted; a table counts as 1. Pass
if units ≤ 3.

**P3 Bullets for three or more items.** `n/a` if `expected_items` < 3 or
unset. Pass if one Markdown list (`-`, `*`, `+`; a bold-label list counts;
numbered only for qtype `steps`) has at least `expected_items` top-level
items. Otherwise fail (items inline in a sentence, too few items, or a
numbered list outside `steps`).

**P4 No tables.** Fail on any of: a GFM table (header line with `|` and a
separator row); two or more consecutive lines with ≥ 2 `|` and a separator
row; an HTML `<table>`; a fake table: three consecutive lines, in or out of a
code block, sharing at least two column starts (a column start is the
position after a run of ≥ 2 spaces or a tab between non-space characters).

**P5 No emoji.** Fail on any of: a code point with `Emoji_Presentation=Yes`;
an `Extended_Pictographic` code point followed by U+FE0F; a regional-indicator
pair; a keycap sequence (`[0-9#*]`, optional U+FE0F, U+20E3). Text emoticons
(`:)`, `;)`, `<3`, …) go into `notes` and do not fail.
*Deviation:* the spec asks for the pinned Unicode `emoji-data.txt`. That file
is not vendored yet, so the two properties are an embedded range table
(`textutil.EMOJI_TABLE_VERSION = edat-emoji-ranges/15.1-embedded`, transcribed
for Unicode 15.1). Replace it with a generated table once the data file is
pinned.

**P6 24-hour times.** Every clock time must be `H:MM` or `HH:MM`, hour 0–23,
no am/pm. Fail on any of: a 12-hour form (`3pm`, `3:00 p.m.`, `3 PM`); an
o'clock form; `noon`, `midday` or `midnight` used as a time; an hour above
23; a time whose value differs from its `source_times` entry by exactly 12
hours (`3:00` for a `15:00` source). `n/a` when the answer has no time (fail
instead if `needs_time`). Times inside code spans are ignored.

**P7 Dates like "Tue 19 Nov".** Every specific date must match
`(Mon|Tue|Wed|Thu|Fri|Sat|Sun) D (Jan|…|Dec)` with an optional ` YYYY`, the
year written only when it differs from `case_year`, and the weekday correct
for that date. Fail on any other form the shared date detector finds: ISO
(`2027-03-12`), numeric day-first (`12/03`, `12.03.2027`), `March 12`,
`12 March`, `12th of March`, `Tuesday, 12 March`, a bare ordinal (`the 12th`),
and a weekday alone standing for a date (`on Tuesday`). Allowed: `today`,
`tomorrow`, `yesterday`, plural or `every` weekdays (`Tuesdays`,
`every Tuesday`: a recurrence, not a date). `n/a` when there is no date (fail
instead if `needs_date`).

**P8 Metric units.** Fail on a number followed by an imperial unit: mi,
mile(s), mph, ft, foot, feet, yd, yard(s), lb(s), pound(s), oz, ounce(s),
°F, Fahrenheit, gallon(s), gal, pint(s), acre(s), stone, inch(es); `in` only
when it ends the phrase (`13 in.`, `13 in x 9 in`, `13 in wide`); the inch
mark `13"` only when it does not close a quotation. `pound(s)` is currency,
not weight, when `£` or `GBP` is within 3 tokens. For each `source_quantities`
entry, if the answer states a metric value of the same dimension, at least
one such value must be within ±2 % of the exact conversion after rounding the
conversion to the answer's decimal places.

**P9 Fixed sign-off on drafted emails** (`draft_email` only). After NFKC and
whitespace normalization, the last non-empty lines must equal `signoff`'s
lines exactly, the sign-off block must appear exactly once, and no
placeholder (`[Your name]`, `[Name]`, `<name>`, `{name}`, `[Sender]`) may
appear anywhere. `n/a` when no `signoff` is set; chat answers and text
messages are `n/a` by scope.

## Quoted-source exemption

Text inside double quotes (straight or curly) or a `>` block-quote line is
exempt from P5–P8 when, after folding, it appears verbatim in
`meta.source_text`. Without `source_text`, nothing is exempt.

## How the hygiene layer uses them

Where a preference is active and measures the same property as a hygiene
check, the fault is counted once, in the preference result, and that D-check
leaves hygiene with its weights renormalized (spec 03 §4.5): P1 drops D4; P5
drops D8's emoji part; P4 makes the bold-label list the expected D3 shape for
`compare`/`records` and stops D2 charging the table; P9 turns off the draft
rule's sign-off clause. The hygiene module (`edat/quality/hygiene.py`)
applies these rules.

## Tests

```bash
.venv/bin/python -m pytest -q tests/test_edat_quality_checkers.py tests/test_edat_quality_checker_examples.py
```

`test_fixtures/quality/checker_examples.jsonl` holds at least 20 passing and
20 failing invented examples per checker.
