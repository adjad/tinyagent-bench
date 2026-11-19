# tinyagent-bench

tinyagent-bench is a benchmark for **small language models (about 0.2B to 12B parameters)
used as on-device agents**: on laptops and phones, offline, with the model as the
only thing deciding what to do.

> **No results are published yet.** This repository holds the harness, its
> offline tests, the design documents and a website mock-up. Every number in
> the mock-up is sample data.

## Who it is for

- People choosing a small model, quantisation and runtime for an on-device
  assistant, who need to know whether it can actually act (call the right tool,
  with the right arguments, and stop when it should), not only chat.
- Model and runtime builders who want a fixed, reproducible agentic workload with
  speed, memory and energy measured on real devices at fixed context sizes.

## What it measures

| Area | What is measured | Design |
| --- | --- | --- |
| Agentic and tool use | Tool choice, arguments, multi-step chains, asking before risky actions, refusing what is forbidden, over a fully faked synthetic world | [02-agentic-spec](docs/benchmark/02-agentic-spec.md) |
| Instruction following | Standing preferences kept across a session: persistence, one-off overrides, supersession, scope, and never waiving a confirmation | [14-standing-preferences](docs/benchmark/14-standing-preferences.md) |
| Privacy | Leaks of private details in what the agent sends: messages, emails, tool arguments | [02-agentic-spec](docs/benchmark/02-agentic-spec.md), [03-output-quality-spec](docs/benchmark/03-output-quality-spec.md) |
| Output quality | Whether answers are correct, grounded in the sources the tools returned, and readable | [03-output-quality-spec](docs/benchmark/03-output-quality-spec.md) |
| Speed, memory, energy | Prefill and decode speed, time to first token, peak memory and energy per task at fixed context sizes; battery and energy over a simulated day | [04-perf-energy-memory-spec](docs/benchmark/04-perf-energy-memory-spec.md) |
| Long sessions | Whether behaviour holds up as a session grows long | [02-agentic-spec](docs/benchmark/02-agentic-spec.md) §3.15, [08-product-eval-design](docs/benchmark/08-product-eval-design.md) |
| Vision and audio input | Image, document and speech inputs, for models that accept them | [05-vision-audio-spec](docs/benchmark/05-vision-audio-spec.md) |
| Controlling apps on screen | Operating apps through what is on screen | [11-screen-control-spec](docs/benchmark/11-screen-control-spec.md) |
| Sidekick tasks | Light coding and terminal tasks | [12-sidekick-coding-spec](docs/benchmark/12-sidekick-coding-spec.md) |
| Behaviour inside an assistant | The same model placed inside a full assistant backend, used by three synthetic people (a student, a tech worker, a mixed household) over a simulated day | [08-product-eval-design](docs/benchmark/08-product-eval-design.md), [09-profile-schema](docs/benchmark/09-profile-schema.md) |

Not tested: video input, speech output, and embedding or reranker models.

The benchmark's own code uses the short internal prefix `edat` (the `edat/`
package, `EDAT_*` environment variables, `edat.*` schema ids).

The harness was built from parts of the [Harbor](https://github.com/adjad/Harbor)
assistant (Apache-2.0), a local-first macOS assistant. [IMPORT.md](IMPORT.md)
says exactly what was taken, from which commit, and what was left out. Imported
code keeps its original names and paths.

## Current scope

- **Ranked models** are local candidates. Each must fit on a 16 GB Mac and runs
  on [oMLX](https://github.com/jundot/omlx) on Apple silicon (MLX Swift on the
  phone).
- **Language:** English only, for now.
- **Reference devices:** a MacBook Pro (M5 Pro, 24 GB) and an iPhone Air.
  Smaller memory classes are simulated by capping the memory the model may use.
- **Reference models:** two.
  - **Primary:** a tuned Gemma 4 E4B, release 5 (oMLX engine id `Gemma-Wisp`).
  - **Second:** the base Gemma 4 E4B (engine id `gemma-4-E4B-it-oQ4e-mtp`).

  Every model is shown as a difference from both references. The gap between
  the two references shows the effect of the tuning.

  *Disclosure:* the primary reference was fine-tuned on data from real-life
  testing of the assistant this benchmark's harness comes from. It was not trained on this
  benchmark's questions. The method includes checking its training data for
  overlap with the held-out questions. That check has not been run yet, so no
  result is claimed.
- **Hosted reference:** a small set of hosted models is run through a cloud API
  as an unranked reference, on the quality tests only, with synthetic data. They
  are never ranked against the local candidates.

## What exists today, and what is planned

| Piece | State |
| --- | --- |
| Toolbench: 383 deterministic tool-calling cases, every tool faked over a synthetic world, a pure scorer (`scripts/run_toolbench.py`, `scripts/toolbench/`) | **Imported; offline checks pass.** Fairness fixes for model comparison are in progress. |
| Routing stress suite: 1,000 compound requests (`test_fixtures/routing_stress/`, `scripts/run_routing_stress_suite.py`) | Imported |
| Planner eval, memory eval scorer, email extraction benches, demo sweep, model-qualification checks | Imported |
| Speed and memory sweep (`scripts/bench_ondevice_perf.py`) | Imported; the fixed-context performance runner is planned |
| Simulated world for an isolated assistant backend (`sandbox/`) | Imported |
| Remote-inference client for the hosted reference (`service/inference/providers.py`, `remote_policy.py`, `service/config/endpoints.py`) | Imported; see [docs/REMOTE_INFERENCE.md](docs/REMOTE_INFERENCE.md) |
| Three synthetic people and their standing preferences | In progress |
| In-assistant evaluation | In progress |
| iPhone runner (MLX Swift test app) | In progress; the imported MLX Swift package (`ios/Packages/HarborLing/`) is there for it |
| Model adapters (`edat/models/`) | In progress |
| Output-quality, vision and audio tests | Designed, not built |
| On-screen app control (`edat/screen/`, cases in `test_fixtures/screen/`) and sidekick coding and terminal tasks (`edat/sidekick/`, cases in `test_fixtures/sidekick/`) | Planned |
| Results store (`edat/results/`), published results (`data/results/`), website data (`site/data/`) | In progress; no results are published |
| Public website | Designed; [`site/`](site/) is a clickable mock-up with sample data |

An audit of the imported Toolbench cases is summarised in
[13-case-audit](docs/benchmark/13-case-audit.md).

[docs/STRUCTURE.md](docs/STRUCTURE.md) says where each piece lives and where the
upcoming ones will go.

## Known limits

- **Only part of the sidekick set is scored.** The host isolation (pull request 9)
  and the grade-integrity repair (pull request 17) are merged, each after its own
  adversarial review. Every scorer asks one default-deny rule
  (`edat/sidekick/labels.py`). A row scores only when its grading is on the
  allow-list (hidden tests, regex write and read, exact shell checks) and its type is
  scored (write_fn, fix_bug, shell, regex). That is 118 of the 228 cases; see
  [edat/sidekick/README.md](edat/sidekick/README.md).
- **The other types are labelled "not hardened against adversarial model code"**
  and reported apart, unranked, because a model could pass them without doing the
  task:
  - git tasks: graded by real git reading a repository the model controls;
  - ladder rungs 4 to 9: the hidden tests run in the same process as the model's code;
  - write_test: a test that compares the module against the shown source passes;
  - explain_error, diff "what", Yes/No with a bug and commit_msg: echoing the prompt can pass;
  - Yes/No without a bug: one constant "No." passes;
  - shell_wc_csv and shell_grep_count_errors: task-blind output can pass.

  Each label comes off only in its own pull request, after its own adversarial
  review. No coding run has been made yet, so no coding results exist.

- **A process that outlives its command cannot always be found and killed on
  macOS.** After each command the harness kills the command's process group, but a
  process that left the group survives that kill. Survivors are detected where
  possible, and any survivor fails the trial as unsafe. Survivors cannot all be found
  for certain. A process that is never found keeps running, confined, until it exits
  or reaches its CPU-time limit. Once the trial folder is sealed and renamed, it can
  reach none of the trial's files by name, only through descriptors it already
  holds. It stays inside the sandbox, with no network and no access to other
  processes or to devices beyond `/dev/null`, `zero`, `random` and `urandom`. The full fix is a separate user account per trial, which
  needs administrator rights and is left for later. See
  [edat/sidekick/ceiling/RUN_SHEET.md](edat/sidekick/ceiling/RUN_SHEET.md).

## Running the offline checks

Nothing below loads a model or needs network access after installing packages.
Python 3.13 on macOS (Apple silicon).

```bash
python3.13 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements-dev.txt
```

List the Toolbench cases, then bind every case to the synthetic world without
running a model:

```bash
.venv/bin/python scripts/run_toolbench.py --list
```

```bash
.venv/bin/python scripts/run_toolbench.py --dry-run
```

Run the unit tests the way the gate does. The gate is the single macOS job in
`.github/workflows/tests.yml`: it runs every file that `tools/ci_test_files.py`
lists, each in its own pytest process (a bare `pytest` also tries paths that are
not part of this repository). The script names each excluded file and why.

```bash
for f in $(.venv/bin/python tools/ci_test_files.py); do .venv/bin/python -m pytest -q "$f" || echo "FAILED: $f"; done
```

The imported test files, and the one test expected to fail here, are listed in
[IMPORT.md](IMPORT.md).

A live run (`scripts/run_toolbench.py` without `--dry-run`) needs oMLX serving
the model on `127.0.0.1`. It refuses to start while anything listens on ports
8765 or 8791, and its network guard allows only the oMLX port. No API key,
token or account detail belongs in this repository: the hosted reference reads
its credential from the macOS Keychain at run time.

## Licence

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). Most of the code comes
from the Harbor assistant.
