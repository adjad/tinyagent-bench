# tinyagent-bench sidekick tier (spec 12)

Light coding and terminal tasks in tinyagent-bench for small models used as a developer's helper
on a Mac. Design: [12-sidekick-coding-spec](../../docs/benchmark/) (the
working copy is newer than the repository's `docs/benchmark/`).

## Layout

| Path | Build item | What it is |
| --- | --- | --- |
| `sandbox/run.py`, `sandbox/edat-sidekick.sb` | S1 | Per-trial throwaway folder, fake home, clean environment, deny-by-default Seatbelt profile, limits, watchdog. Behind the `Isolation` interface so a VM can replace `sandbox-exec`. No unsandboxed fallback. |
| `sandbox/denials.py` | S2 | Reads Seatbelt denials for a trial's pids from the unified log (`--read-denials`) |
| `sandbox/selftest.py` | S11 | Proves network (loopback included) and reads/writes outside the folder are denied; the runner refuses to start unless it passes |
| `prescreen.py`, `prescreen_rules.yaml` | S3 | zsh one-liner parser and the hard-deny / scoped-destructive rules |
| `extract.py` | S4 | Answer extraction (fences, single line) and placeholder detection |
| `graders/` | S5 | `code` (hidden tests + 200 differential inputs, Python and Node), `shell` (shown + hidden folder), `git` (predicates + loss check on real git's view of a sanitised copy; labelled, see Known limits), `regex` (write and read), `write_test` (mutants), `text` (key facts, commit rules, Yes/No diff bugs) |
| `gen/` | S6, S7 | `core.py` (case model, counts, splits), `registry.py`, one `t_<type>.py` per task type plus `t_restraint.py` |
| `validate.py` | S11 | Every reference passes; wrong, empty and "OK." answers fail; every mutant is caught; tool-mode references pass |
| `tools_dev.py`, `tool_mode.py`, `loop_glue.py` | S8 | The `edat-dev-v1` tools (10 s per command, 60 s total), scripted approve/deny replies and last-command replay. Turns run through the shared reference loop (`edat/loop.py`) with its `max_calls=6` cap and the pre-screen as its `guard`; models come through `edat/models/` |
| `restraint.py` | S9 | `edat-restraint-event/v1` records (built with `edat.grading.aggregate.restraint_event`) and trial counts for row #70 |
| `report.py` | S10 | SK-1 to SK-27 |
| `records.py`, `runner.py` | | Results-store records (`tier: sidekick`, `suite: answer_only / tool_mode`), written through `edat.results.writer.RunWriter`, one run per mode, and the 3-trial runner |
| `ceiling/` | | The coding-ceiling ladder: rungs 1-3 are the core cases by level, rungs 4-9 are probes (interacting rules, a stateful class, a data script, two bugs, a two-file change, a tool-mode fix) graded by hidden pytest files in the sandbox; the run driver, the plain-language report and the run sheet (`ceiling/RUN_SHEET.md`). Reported apart from the Sidekick Score |
| `splits.py`, `human.py` | S12 | Development export, private held-out export, human-graded sheet and key-fact audit |

Cases: `test_fixtures/sidekick/cases/dev/` (development split, public).
Tests: `tests/test_edat_sidekick_*.py`.

## Known limits

- **What is scored (the allow-list, `edat/sidekick/labels.py`):** only rows graded by a method whose
  verdict holds against code written to attack the grader. That is the code grader (write_fn, fix_bug and
  their restraint cases: 58 of the 228 cases), the regex cases (pattern writing and reading: 24), and
  the 36 shell cases whose output check is exact, a number, a set or list of lines or a folder tree, or
  forbids an output (these held against nine spray commands). Nothing else is scored: 118 of 228 cases.
  A model's code runs in the sealed sandbox; the expected values, references and verdicts stay on the
  harness side.
- **What is labelled `not hardened against adversarial model code`,** and why:
  - git (`git_view`): graded by real git reading a repository the model controls (below);
  - write_test (`source_compare`): a test that compares the module against the shown source passes;
  - every answer whose verdict comes from the fact matcher, which looks for the facts anywhere in the
    answer's free text, so an answer that contains the prompt passes (`prompt_echo`): explain_error, the
    diff "what" cases, the Yes/No diff cases that have a bug, and commit messages;
  - the Yes/No diff cases without a bug (`guessable`): "No." passes every one. With the cases above, the
    whole diff_review type is labelled;
  - the shell cases whose check takes any line holding a name and its number (`task_blind_output`):
    task-blind output can pass;
  - the ceiling ladder's rungs 4 to 6, graded by host-driven probes (`host_probes_unreviewed`) until that
    method is on the allow-list, and rungs 7 to 9 (`probe_in_process`) until their conversion lands. The
    ceiling and "solid up to" include those rungs; rungs 1 to 3 and the hardened ceiling do not.

  Each is hardened in a small change of its own, and its label comes off only after that change has had
  its own adversarial pass. Git tasks are graded by real git reading a sanitised
  copy of a repository the model controls; the known view mechanisms (replace refs, grafts, ignore rules,
  index flags, attributes, the model's config, pointers elsewhere) are closed, but that view is not one
  the model cannot shape. A strict raw reader of the same copy is a tripwire: where it disagrees with
  git, the trial carries a `git_view_mismatch` flag for a person to read, and the grade is unchanged.
  A later change rebuilds the repository on the host before grading.
- **Default deny:** one function, `labels.may_score`, decides whether a row may be scored, and the
  runner's summary, the results aggregate, the export, the site data and the ceiling report all use it.
  A graded row scores only when the runner marked it `grade_hardened: true` with an allow-listed
  `grading`, its type is on the scored list, and it is not tagged `grade-not-hardened`. A row with no
  mark, an old or resumed record (the ceiling runs such a row again rather than reuse it), or an unknown
  type is not scored. Rows that are not scored stay out of the Sidekick Score (the macro over the scored
  types) and every ranked or published number, sampling-sensitivity deltas and snapshot differences
  included; they are reported apart, with their reasons. A row that was not graded (excluded, an error)
  scores nothing; one of a scored type still counts against its entry's exclusion rate.
- **The sandbox:** a sandboxed test program cannot read other processes (their arguments, environment,
  paths or the process list) or devices beyond `/dev/null`, `zero`, `random`, `urandom` and its own
  descriptors; it has no network, loopback included. A process that outlives its command is stopped and
  fails the trial (`ceiling/RUN_SHEET.md` says what "found" means there).

## Commands

All of these run model output only inside the sandbox. `sandbox-exec` cannot
nest, so run them from a normal terminal, not from inside another Seatbelt
sandbox (the tests skip with "nested Seatbelt sandbox" there).

```bash
python -m edat.sidekick selftest
python -m edat.sidekick counts
python -m edat.sidekick validate --prefix shell_ --trials 3
python -m edat.sidekick export-dev
EDAT_PRIVATE_DIR=/path/outside/the/repo python -m edat.sidekick export-heldout
python -m edat.sidekick human-sheet --out sheet.csv
python -m edat.sidekick run --model null --split dev --set core --trials 3
python -m edat.sidekick run --model omlx:<engine id> --base-url http://127.0.0.1:<port> \
    --device-id <id> --weights-fingerprint <64 hex>
python -m pytest -q tests/test_edat_sidekick_*.py
```

A local run needs a device and the weights' sha256 or fingerprint (the results
store refuses records without them). `null` and `stub:` runs get a stand-in
device and fingerprint, and their records are marked `sample`.

`EDAT_SIDEKICK_PYTHON` (a Python 3.13 with pytest), `EDAT_SIDEKICK_NODE` and
`EDAT_SIDEKICK_GIT` pin the toolchains; versions, the profile hash and the
pre-screen rule version go in the run key.
