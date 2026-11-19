# EDAT: Edge Device Agentic Testing

EDAT is a benchmark for **small language models (about 0.2B to 12B parameters)
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
| Long sessions | Whether behaviour holds up as a session grows long | Planned |
| Vision and audio input | Image, document and speech inputs, for models that accept them | [05-vision-audio-spec](docs/benchmark/05-vision-audio-spec.md) |
| Controlling apps on screen | Operating apps through what is on screen | Planned |
| Sidekick tasks | Light coding and terminal tasks | Planned |
| Behaviour inside an assistant | The same model placed inside a full assistant backend, used by three synthetic people (a student, a tech worker, a mixed household) over a simulated day | [08-product-eval-design](docs/benchmark/08-product-eval-design.md), [09-profile-schema](docs/benchmark/09-profile-schema.md) |

Not tested: video input, speech output, and embedding or reranker models.

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
  testing of the assistant EDAT's harness comes from. It was not trained on this
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
| Output-quality, vision and audio, on-screen control and sidekick tests | Designed or planned, not built |
| Results store and public website | Designed; [`site/`](site/) is a clickable mock-up with sample data |

[docs/STRUCTURE.md](docs/STRUCTURE.md) says where each piece lives and where the
upcoming ones will go.

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

Run the harness's unit tests. Name the test files: a bare `pytest` also tries
paths that are not part of this repository.

```bash
.venv/bin/python -m pytest -q tests/test_toolbench.py tests/test_toolbench_world.py tests/test_rc3_toolbench_scorer.py tests/test_eval_planner_dry_run.py tests/test_sandbox_world.py tests/test_memory_eval_scorer.py
```

The full list of imported test files, and the one test expected to fail here,
are in [IMPORT.md](IMPORT.md).

A live run (`scripts/run_toolbench.py` without `--dry-run`) needs oMLX serving
the model on `127.0.0.1`. It refuses to start while anything listens on ports
8765 or 8791, and its network guard allows only the oMLX port. No API key,
token or account detail belongs in this repository: the hosted reference reads
its credential from the macOS Keychain at run time.

## Licence

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). Most of the code comes
from the Harbor assistant.
