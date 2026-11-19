# Edge_device_agentic_testing

A benchmark for **small language models (about 0.2B to 12B parameters) used as
on-device agents** on edge hardware with less than 16 GB of memory available to
the model: laptops and phones, offline, with the model as the only thing
deciding what to do.

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
| Output quality | Whether answers are correct, grounded in the sources the tools returned, and readable | [03-output-quality-spec](docs/benchmark/03-output-quality-spec.md) |
| Speed, memory, energy | Prefill and decode speed, time to first token, peak memory and energy per task, at fixed context sizes | [04-perf-energy-memory-spec](docs/benchmark/04-perf-energy-memory-spec.md) |
| Vision and audio | Image, document and speech inputs for models that accept them | [05-vision-audio-spec](docs/benchmark/05-vision-audio-spec.md) |
| Behaviour inside an assistant | The same model placed inside a real assistant backend, used by three synthetic user profiles (student, tech worker, mixed household) over a simulated day | [09-profile-schema](docs/benchmark/09-profile-schema.md) |

The harness starts from code in [Harbor](https://github.com/adjad/Harbor), a
local-first macOS assistant (Apache-2.0). See [IMPORT.md](IMPORT.md) for exactly
what was imported, from which commit, and what was left out.

## Current scope

- **Runtime:** [oMLX](https://github.com/jundot/omlx) on the Mac; MLX Swift on the phone.
- **Hardware:** Apple silicon only for now. Two reference devices: a MacBook Pro
  (M5 Pro, 24 GB) and an iPhone Air. Smaller memory classes are simulated by
  capping the memory the model may use.
- **Baseline model:** Harbor's tuned Gemma 4 E4B ("r5", oMLX engine id `Gemma-Wisp`).

## What exists today, and what is planned

| Piece | State |
| --- | --- |
| Toolbench: 383 deterministic tool-calling cases, every tool faked over a synthetic world, a pure scorer (`scripts/run_toolbench.py`, `scripts/toolbench/`) | **Imported from Harbor; offline checks pass.** Fairness fixes for model comparison are in progress. |
| Routing stress suite: 1,000 compound requests (`test_fixtures/routing_stress/`, `scripts/run_routing_stress_suite.py`) | Imported |
| Planner eval, memory eval scorer, email extraction benches, demo sweep, model-qualification checks | Imported |
| Speed and memory sweep (`scripts/bench_ondevice_perf.py`) | Imported; the fixed-context performance runner is planned |
| Simulated world for an isolated assistant backend (`sandbox/`) | Imported |
| Three synthetic user profiles | In progress |
| In-assistant ("product") evaluation | In progress |
| iPhone runner (MLX Swift test app) | In progress; Harbor's MLX Swift package (`ios/Packages/HarborLing/`) is imported for it |
| Output-quality tests, vision and audio tests | Designed, not built |
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

The full list of imported test files is in [IMPORT.md](IMPORT.md).

A live run (`scripts/run_toolbench.py` without `--dry-run`) needs oMLX serving
the model on `127.0.0.1` and refuses to start while a live Harbor is running.
Its network guard allows only the oMLX port.

## Licence

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). Most of the code comes
from Harbor.
