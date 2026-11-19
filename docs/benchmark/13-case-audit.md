> **Draft, 2026-11-18.** Design document; it will change. No benchmark results are published yet.

# 13 · Case audit: a strong model works the cases blind

Date: 2026-11-18. Source: the Harbor files imported into this repository (no diff from Harbor commit `46da8eb5` in any audited path). Every agent that worked or judged a case ran on Claude Opus 5.5 at medium effort. Nothing was run: no harness, scorer, build, server or local model, and no case file was changed.

This report names cases by id only. It contains no prompt, expected answer or check text of any held-out case, so it is safe to publish. Case details are in a findings file (`13-case-audit-findings.jsonl`) that is kept privately and not published. A verdicts file (`13-case-audit-verdicts.csv`) with every Toolbench case's verdict, by id only, is also kept privately.

## Counts

### Toolbench: 383 of 383 cases have a verdict

| | Cases |
|---|---|
| Audited | 383 (gate 326, dev 34, voice 23) |
| Blind answer agrees with the expectation, as the scorer would judge it | **360** |
| Blind answer disagrees | **23** |

Disagreements by class:

| Class | Count | Meaning |
|---|---|---|
| (a) expectation looks wrong | 7 | 4 of these expect a tool the host always refuses to run |
| (b) prompt ambiguous | 5 | |
| (c) expectation too narrow | 6 | |
| (d) world data contradicts the case | 0 | (3 more were flagged without a disagreement; see below) |
| (e) Harbor-specific habit | 3 | |
| (f) the auditor was wrong; the case is fine | 2 | Both are safety cases that production's system prompt settles |

- **By split:** gate 22, dev 1, voice 0.
- **By category:** reminders 4, web 4, device 3, safety 3, contacts 2, mail 2, calendar 1, messages 1, files 1, harbor 1, followup 1. Chat, compose, day, memory, notes and voice had none.
- **Corrected comparator errors:** three cases first showed as disagreements because of bugs in my own comparison: it ignored the scorer's argument aliases and filled placeholders from the wrong fact table. After the fix they agree. One case first showed as agreeing because the comparator counted a refused write as done. It is counted above as a disagreement (class e).

**Flags without a disagreement** (step 4 of the method): 77 flags on 76 cases.

| Flag | Count |
|---|---|
| (w) weak check: a wrong answer can pass | 53 |
| (u) unpassable for every model | 7 (4 overlap the class-a cases above) |
| (dup) duplicate or near-duplicate | 7 |
| (m) trap note describes one model's observed failure | 7 |
| (d) world data contradicts the check | 3 |

By severity, 7 are high, 32 medium and 38 low.

**Distinct cases with at least one finding that needs a fix:** 88 (gate 79, dev 9, voice 0). Of these, 52 are high or medium severity, and all 52 are gate cases.

### Lighter passes

| Set | Audited | Agreed | Findings | High | Main classes |
|---|---|---|---|---|---|
| Memory eval (`test_fixtures/memory_eval`) | 42 of 42 | 37 | 15 | 0 | 5 w, 4 b, 3 d |
| Task corpus (`assistant-task-corpus-v1`) | 48 of 48 | 22 | 33 | 4 | 11 c, 6 w, 3 u |
| Synthetic emails (commitment and profile-fact benches) | 20 of 20 | 6 | 24 | 2 | 9 w, 7 c, 2 a |
| Routing corpus, sample | 100 of 432 (stratified by domain, seed 13) | 73 | 40 | 3 | 17 c, 8 w, 5 a, 5 dup |

On the memory eval, none of the known-bad answers in `bad_answers.jsonl` scored as correct, and every gold fact is supported by its source.

## How far earlier gate results can be trusted

The held-out sets are gate (326 cases) and voice (23), 349 in all.

- **20 gate cases (6.1% of gate) are failed by a careful, correct model**, excluding the 2 class-f cases. Of these, 4 can never pass for any model, because they expect a tool the host refuses to run (route fault). That caps every model's gate pass rate at 322 of 326. The other 16 punish a defensible answer. They lower every model's absolute score, and they lower the strongest, most cautious models most: a model that asks a clarifying question or uses the purpose-built tool loses the point.
- **31 gate cases have a weak check of high or medium severity** that a wrong or refusing answer can pass. On these, earlier gate scores may be inflated, more so for weak models.
- **In all, 52 gate cases (16%) carry a high- or medium-severity finding**, and 79 (24%) carry any finding that needs a fix. The voice split is clean: its 23 cases had no disagreement and no flag.
- **Overall:** earlier absolute gate rates are uncertain by roughly ±5 percentage points. Model rankings with gaps larger than that are probably sound. Close comparisons, and pass-rate thresholds such as a fine-tune gate, should be re-scored after the fixes. The four unpassable cases should be dropped from the gate denominator straight away.

## Patterns found

1. **Answer checks that a wrong answer satisfies (w, 53 flags).** These are the most common problem, and they concentrate in calendar, day, device and safety. They take three shapes:
   - a `contains_any` term so common that a contradicting answer contains it too;
   - a missing `not_contains` for the obvious false claim of success;
   - a `forbid` list that misses a current delete tool.
2. **Gate cases that no model can pass (a/u, 4 gate + 1 seeded-world + 2 dev).**
   - Three mac-controls cases (`dev_mac_*`) and `safe_mcp_drive_create` expect routes that the model-led loop refuses for every model (no completion contract). The scorer turns these into `route_fault` every time.
   - `compose_literal_text` binds only in the canonical world.
   - Two dev followup cases cannot be satisfied as written.
3. **Under-specified prompts graded on one reading (a/b, 12).** In these, the prompt omits a detail that the tool's own description says to ask about, or a sibling case accepts the opposite answer. The fix is nearly always to make the prompt carry the missing detail, not to widen the grading.
4. **Expectations too narrow (c, 6).** A correct answer fails for one of these reasons:
   - it uses a sensible phrasing that isn't on the case's list;
   - it quotes the source text, which trips a `not_contains`;
   - it uses an equally valid tool;
   - it makes one reasonable read on a `no_tool` case.
5. **Harbor-specific habits (e, 3).**
   - Two cases (`contact_phone_only_not_email`, `mail_draft_mom`) expect an action that the tool's own refusal text and description tell the model to replace with a question.
   - One web case relies on which tools the route offers on that turn.
6. **Model-specific trap notes (m, 7).** These record one failure seen in one model's earlier run, and one names a tool that no longer exists. They should describe the skill under test instead.
7. **World inconsistencies** that blind solvers ran into. Not all of them break a case.
   - A mailed schedule change conflicts with the calendar in the canonical world.
   - A day-prediction line disagrees with the calendar on one event's day.
   - Some fakes ignore a parameter or understand only one input value.
   - One tool has no fake.
   - Three web "latch" cases grade on a fact that the world does not return on that path (d).
8. **Lighter sets.**
   - **Task corpus:** the expected tool lists are often narrow (11 c). Three cases depend on setup content that never reaches the model's turn (u).
   - **Synthetic emails:** two scoring problems affect every email. A bad-phrase check runs against the evidence text rather than the fact, and one date check allows only one of two valid dates.
   - **Routing sample:** `need` groups leave out `get_my_day_plan` on day-plan prompts. One delete paraphrase routes to the wrong store. The gold also names tools that no longer exist in the registry.

## Prioritised fix list (ids only)

Proposed fixes for each id are in the privately kept findings file.

**P0: remove from the gate denominator now (unpassable for every model)**
- `dev_mac_volume_down`, `dev_mac_lock_screen`, `dev_mac_pause_spotify`, `safe_mcp_drive_create`: move to dev or mark report-only until those tools get a completion contract.

**P1: a correct model fails (gate, high)**
- Make the prompt carry the missing detail: `rem_alarm_no_time`, `rem_trash_tonight`, `rem_dana_report`, `rem_keep_doordash`, `web_possessive_close_tonight`.
- Accept the clarifying question as correct: `contact_phone_only_not_email`, `mail_draft_mom` (decide the two together; they are near-duplicates).
- Widen a tool alternative: `harbor_email_how_far_back` (accept the alternative tool and add its fake), `web_who_is_president`.
- Fix answer phrasing that is too narrow: `contact_not_in_my_contacts`, `mail_long_email_door_code`, `msg_post_likes_not_mine`.

**P2: a wrong answer passes (gate, high)**
- `safe_rid_of_notes` and `safe_delete_everything`: add the missing delete tool to `forbid`.
- `web_latch_email_first` and `web_latch_bitcoin_first_lc`: fix the answer check against the world (d).

**P3: medium severity, gate**
- Too narrow or ambiguous: `files_reorganize_downloads`, `fu_question_mark_restates`, `web_pub_world_series`.
- Weak answer checks:
  - calendar and day: `cal_cancel_readonly_holiday`, `cal_move_refused_readonly`, `cal_move_refused_unknown_invitees`, `cal_move_rename_location`, `cal_when_is_midterm`, `day_class_tomorrow_pls`, `day_free_friday_afternoon`, `day_free_thirty_friday_2`, `day_free_trip_blocks`;
  - device and safety: `dev_battery`, `dev_status_wifi_name`, `dev_time_london_vs_here`, `dev_time_tokyo`, `dev_timer_time_left`, `safe_wifi_check`;
  - compose, contacts, mail and messages: `compose_cal_friend_lower_noask`, `contact_in_my_contacts`, `mail_unread_count`, `msg_hike_photos`;
  - files, notes, followup and harbor: `files_tidy_downloads_lc`, `notes_missing_topic`, `fu_compare_stocks_last_week`, `harbor_can_you_send_texts`, `harbor_email_how_far_back`;
  - memory, reminders and web: `mem_forget_everything_mail`, `mem_not_fact_do_i_live`, `rem_bank_bill`, `web_bitcoin_news`, `web_moms_open_stays_private`.
- Unpassable in seeded worlds: `compose_literal_text`.

**P4: low severity.** These are 38 flags: 22 weak checks, 7 trap notes describing one model, 6 near-duplicates, 2 unpassable dev cases and 1 world contradiction (`fu_tell_him_yes`). Also `cal_book_vet` (dev, b) and the optional wording additions for `safe_post_json_webhook`, `safe_upload_transfer_sh` and `web_latch_email_first`. The ids are in the privately kept verdicts file.

**Lighter sets, high first:**
- task corpus: `ATC-029` (d), `ATC-043`, `ATC-044`, `ATC-048` (u);
- synthetic emails: `commit-tax` (c), `fact-sister_news` (a), and the two scoring issues that affect every email;
- routing: `calendar-024`, `calendar-030` (c), `delete_paraphrase-008` (a).

Medium and low findings for these sets are in the privately kept findings file.

## Method and its limits

1. **Blind view.** For each case, a blind view held the prompt with its placeholders bound from the canonical world, any history, the access mode and any missing permission. The tool list was the registry's names, descriptions and parameter schemas, extracted statically from `service/tools/*.py`. Solvers simulated tool results by reading `world.py`, the data and the fakes. The case files, scorer, matchers, runner and tests were off limits to them.
2. **Solving.** 18 Opus 5.5 solvers at medium effort each took 13 to 28 cases, one category per batch where possible.
3. **Comparison.** The comparison reused the scorer's own `_match_expect` and `_answer_problems` from `scripts/toolbench/scorer.py`, which are pure functions. It added forbid, no_tool, the call budget and host-refused route faults.
4. **Adjudication.** Five Opus 5.5 adjudicators at medium effort judged every disagreement against the case, the scorer and the world. They also read every case in their categories for weak checks, duplicates, model-specific trap notes and unpassable cases.
5. **Lighter passes.** One Opus 5.5 agent at medium effort covered each of the four lighter sets.

Limits that affect the counts:
- **Harbor's system prompt was withheld.** The blind solvers never saw production's system prompt (`service/agent/loop.py`), which a real run includes. That cost two false disagreements in safety (class f). Some class-e calls might change with it, though the adjudicators judged the three that remain as genuine.
- **Some schemas were incomplete.** For 25 tools the static extraction found no literal parameter schema, so solvers inferred arguments from the fakes' signatures. `recall`, `remember`, `forget` and `search_conversations` had no description at all.
- **Writes were assumed to take effect.** The comparator treats every write call as done unless access is view-only. A write that a fake refuses can therefore show as a false agreement. One such case was caught (`mail_draft_mom`); others may remain.
- **One run per case.** Agreement means one strong model, run once, got it right. It does not show that the check is tight; step 4 covers that.
- **Lighter passes were looser.** The task-corpus pass could not tell whether setup text reaches the model. The routing pass found gold tool names that no longer exist.
- **One solver tried to run repo code.** In one case, the device_b solver tried to import a repo module to run the calculator. The import failed on a read-only database and changed nothing. All later solvers were told explicitly not to execute repo code.

## Open questions

1. When a request is under-specified, should the gate reward a clarifying question? Toolbench mostly expects action, while the tool descriptions often say to ask. The answer settles about 8 cases at once.
2. Should host-refused tools stay in the gate as a measure of the host, or leave the model benchmark? This report assumes they leave.
3. Should future blind audits include Harbor's system prompt? It is what a model really sees, but tinyagent-bench is meant to test general capability.
4. Should the dev and voice splits be audited again with the system prompt and with repeated runs? That would tighten the agreement figure for those splits.
