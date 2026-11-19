# tinyagent-bench Writing family: the contract

Spec: `docs/benchmark/03-output-quality-spec.md` section 9. English only, version 1.
The case shape is in the docstring of `writing.py`; this file fixes the rules the
generators (`writing_gen_*.py`) and the checks (`writing_checks.py`) share.

## Draft layout the checks parse

- **Subject.** When `constraints.subject` is set, the first non-empty line is
  `Subject: <text>`, at most 10 words. It is not counted in the body.
- **Invitation** (`surface == "invite"`): line 1 `Title: <text>` (at most
  `title_max_words`), then `Description: <text>` (at most `description_max_words`,
  counted from the description alone).
- **W7 title cases**: `Title: <text>` then `Subject: <text>`; the title has at most
  `title_max_words` words and the subject at most 10.
- **W4 output**: the corrected text, then, when `questions_list` is set, a line
  `Questions:` followed by one item per line (a leading `-` or `*` is allowed).
  Everything from the `Questions:` line on is not part of the edited text.
- **Body** = the draft without the subject/title/description labels and without
  the questions block. Greeting and sign-off are part of the body.
- **Word count** = whitespace-separated tokens of the body (for invitations, of the
  description). **Sentence count** = `textutil.split_sentences` over the body with
  the greeting line and sign-off lines removed. **Character count** (`max_chars`) =
  `len(body)`.

## The checks

Every check returns `pass`, `fail` or `n/a`, with a short reason.

| ID | Applies to | Passes when |
| --- | --- | --- |
| WD1 | cases with anchored points | every anchor of every anchored point appears in the body (`normalize.anchor_present`, dates and times by value in any format). `point_recall` = anchored points made ÷ anchored points |
| WD2 | all | no invented atom: an atom (`normalize.find_atoms`) of the draft whose key is absent from the request(s), the context, `allowed_text` and the sender/recipient names. Weekday words alone, the sign-off and the greeting do not count. A date not in the brief is invented even inside "I'll send it by …". `invented_rate` = invented ÷ all atoms |
| WD3 | cases with `must_not` | no `must_not` anchor appears, except on a line that starts with `>` (a quoted line of the thread) |
| WD4 | all | every name of the brief (sender, recipient, text anchors that are names) is spelled byte-exactly wherever a case- or diacritic-folded form of it appears; with an active preference, P6, P7 and P8 (`checkers.check`) pass |
| WD5 | all | all limits and conventions above hold: words (`max_words`, `min_words`, `exact_words` exactly), sentences, characters, subject present and ≤ 10 words, greeting is the exact start of the body, sign-off passes P9 (`checkers.check("P9", …)`) and ends the body, no Markdown (`hygiene.has_markdown`) on email, message and invite, no placeholder (`[Name]`, `<date>`, `XX`, `{{…}}`, `TBD`) and every other active preference passes |
| WD6 | W3 | every anchor of every fact appears (only `core` facts for `shorten`), no atom outside the source text and request, and for `to_bullets` / `to_prose` the shape (`hygiene.classify_shape`) is `bullets`/`numbered` or `paragraphs`/`sentence` |
| WD7 | all | `hygiene` D6 (no leak), D7 (no filler opener or closer) and D9 (no repetition) pass; with `output_only`, no preamble line ("Here's a draft:", "Sure", "Of course", "Below is …") and no closing offer ("Let me know if …") outside the text |
| WD8 | W4 | `fix_rate` = planted errors whose `wrong` form is gone and whose `right` form (or an alternative) is present ÷ errors; `overedit` = character edit distance from the edited text to `edit.gold`, ÷ `len(gold)`, at most 0.02; every `protected` span byte-identical; each ambiguous span unchanged and its `key` word in the questions block. Passes when all four hold |
| WD9 | cases with an `audience` | professor, customer: first body line starts with "Dear " or "Hello ", no slang-list word, no emoji, contractions ≤ 1 per 50 words. friend: no "Dear" and no "Sincerely". manager: the anchors of the `lead` point appear in the first two sentences. `register_ok` = all rules for that audience pass |
| WD10 | W5 voice | diagnostic only, never pass/fail (`n/a` status, `voice_delta` metric): stylometric distance to the samples minus the distance of the case's neutral reference to the samples |
| WD11 | W7 | every entity in `entities` is spelled the same each time (no folded or one-letter variant, no figure differing by one digit or a moved separator); for `outline` cases the point anchors appear in the piece in the order the outline gave them |

## Scores

`writing_checks.score_writing(draft, case, *, outline=None, grammar=None)` returns
`{"checks": {...}, "metrics": {...}, "gate_pass": bool, "pass": bool, "score": float, "version": str}`.

- `gate_pass` = WD1 (every anchored point), WD3, WD5 and WD7 pass. Only gated drafts
  are shown to the rater or the judge (spec 9.4); a draft that fails loses its pair.
- `pass` = every applicable check passes (WD10 never blocks).
- `score` (0–1) = **content × form**. Content is the case's own core measure:
  `point_recall` (W1, W2, W5, W6, W7), the share of required facts kept (W3), or
  `fix_rate` with the ambiguous-flag share averaged in (W4). Form is the share of the
  other applicable checks that pass. An empty draft or a draft written for another
  brief therefore scores 0: it makes none of the points.
- Every case has at least one anchored point, fact or planted error, so content is
  always defined.

## Reference drafts

Every case carries `reference_draft` (and `reference_turns` for two-turn cases): a
template draft that passes every deterministic check. The validator runs
`score_writing` on each and fails the split if one does not pass, and checks that the
empty draft and a draft from another case both score 0.

## Grammar signal

`grammar.py` wraps LanguageTool (a local server) and Harper (a local binary); neither
is installed here, so the signal is recorded as unavailable (`None`) and the
constraint component is computed without it.
