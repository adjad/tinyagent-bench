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
| `graders/` | S5 | `code` (hidden tests + 200 differential inputs, Python and Node), `shell` (shown + hidden folder), `git` (plumbing predicates + loss check), `regex` (write and read), `write_test` (mutants), `text` (key facts, commit rules, Yes/No diff bugs) |
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
