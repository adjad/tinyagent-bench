# Repository structure

The layout of tinyagent-bench.

Where each kind of thing lives, and where the pieces still being built will go.
The rule that shapes the layout: **files imported from Harbor keep their Harbor
paths** (see [IMPORT.md](../IMPORT.md)). Work that changes an imported harness
edits it in place, so patches written against Harbor apply here unchanged. Code
written only for this benchmark goes in the benchmark's own folders.

## Today

| Path | What it is | Origin |
| --- | --- | --- |
| `README.md`, `IMPORT.md`, `NOTICE`, `LICENSE` | Overview, import provenance, attribution, licence | Benchmark (LICENSE from Harbor) |
| `import/` | Machine-readable import definition and resolved file list | Benchmark |
| `tools/` | The benchmark's own maintenance tools (`import_harbor.py`) | Benchmark |
| `docs/STRUCTURE.md` | This file | Benchmark |
| `docs/benchmark/` | Design documents, all drafts (numbered `NN-name.md`) | Benchmark |
| `site/` | Website mock-up with sample data | Benchmark |
| `service/` | Harbor's backend: agent loop, tools, router, config, oMLX client | Harbor |
| `sandbox/` | Simulated world for an isolated Harbor backend | Harbor |
| `scripts/run_toolbench.py`, `scripts/toolbench/` | Toolbench: cases (`cases/*.yaml`), synthetic world, runner, scorer, report | Harbor |
| `scripts/*.py` (other) | Planner eval, routing stress suite, perf sweep, email benches, demo sweep | Harbor |
| `test_fixtures/` | Case and corpus files the harnesses read | Harbor |
| `tests/` | Unit tests for the harnesses (`tests/test_*.py`), shared test helpers (`tests/fixtures/`) | Harbor |
| `docs/*.md`, `docs/routing_stress/` | Harbor's architecture notes and stress-suite write-ups | Harbor |
| `ios/Packages/HarborLing/` | Harbor's MLX Swift package, for the iPhone runner | Harbor |
| `build-support/engine_guard/` | The patches Harbor applies to oMLX (the baseline's engine) | Harbor |

## Where upcoming pieces go

| Piece | Location | Notes |
| --- | --- | --- |
| Harness fairness fixes (Toolbench) | `scripts/run_toolbench.py`, `scripts/toolbench/`, `tests/test_toolbench*.py` | Edits in place. Branch `claude/bench-harness-fairness`. Review: [07-harness-review](benchmark/07-harness-review.md). |
| Three synthetic user profiles | `test_fixtures/bench_profiles/{student,tech,mixed}/`, loader and adapters in `scripts/bench_profiles/` | Branch `claude/bench-profiles`. Schema: [09-profile-schema](benchmark/09-profile-schema.md). |
| In-assistant ("product") evaluation | `scripts/productbench/`, `test_fixtures/productbench/` | Branch `claude/bench-product-eval`. Runs Harbor's backend against `sandbox/` with a profile loaded. Design: `docs/benchmark/08-product-eval-design.md` (coming). |
| iPhone runner | `ios-bench/` (new top-level Swift package and test app) | Branch `claude/bench-iphone-runner`. Reuses `ios/Packages/HarborLing/`. Design: `docs/benchmark/10-iphone-runner-design.md` (coming). |
| Performance runner (fixed context sizes, memory, energy) | `edat/perf/`, with fixtures in `test_fixtures/perf/` | Builds on `scripts/bench_ondevice_perf.py` rather than replacing it. Design: [04-perf-energy-memory-spec](benchmark/04-perf-energy-memory-spec.md). |
| Model-neutral agentic harness pieces | `edat/` (for example `edat/loop.py`, `edat/cases/`, `edat/phone/`) | Names as proposed in [02-agentic-spec](benchmark/02-agentic-spec.md). Harbor-specific behaviour stays in `scripts/toolbench/`. |
| Output-quality tests | `edat/quality/`, cases in `test_fixtures/quality/` | Design: [03-output-quality-spec](benchmark/03-output-quality-spec.md). |
| Vision and audio tests | `edat/media/`, synthetic items in `test_fixtures/media/` | Design: [05-vision-audio-spec](benchmark/05-vision-audio-spec.md). Rendered items only; no personal media. |
| Results store: code | `edat/results/` | Writes one record per run for every tier, computes the statistics and exports what the website reads. |
| Results store: published results | `data/results/` | Reviewed, versioned result files, one folder per benchmark version and device. Nothing real is published yet. Local run output stays in the git-ignored `/results/` and `test_results/` at the repository root. |
| Website data | `site/data/` | Exported from `data/results/` by `edat/results/`. Today it holds only labelled sample data. |
| Website | `site/` | Today a mock-up. The real site renders from `site/data/`. Design: [06-ui-design](benchmark/06-ui-design.md). |
| Hosted reference (unranked, quality tests only) | `edat/hosted/`, reusing `service/inference/providers.py`, `remote_policy.py` and `service/config/endpoints.py` | Synthetic data only. Credentials come from the Keychain at run time and never enter the repository ([REMOTE_INFERENCE](REMOTE_INFERENCE.md)). |
| Model adapters | `edat/models/` | One adapter per runtime or provider, so the harness talks to every model the same way. |
| Sidekick tasks (light coding and terminal) | `edat/sidekick/`, cases in `test_fixtures/sidekick/` | Planned. |
| On-screen app control | `edat/screen/`, cases in `test_fixtures/screen/` | Planned. |
| Standing preferences, long sessions | Cases in `test_fixtures/` next to the harness that runs them; new harness code in `edat/` | [14-standing-preferences](benchmark/14-standing-preferences.md); long sessions are designed in a later document. |
| Tests for new benchmark code | `tests/test_edat_*.py` for `edat/`, next to the imported tests | Keeps one `pytest.ini`. |

Rules for new work:

- Do not move or rename an imported file. If an imported file must change, edit
  it in place and say so in the commit message. `tools/import_harbor.py --check`
  will then list it as differing from Harbor, which is expected.
- New fixtures are synthetic. Names, addresses and numbers use placeholder
  domains (`example.com`, `.test`, `.invalid`) and 555 phone numbers.
- Nothing in this repository may come from real mail, messages, chats, debug
  exports or other real history.
- Local run output stays in git-ignored folders (`results/`, `test_results/`, `runs/`).

## Open questions

Recorded during the import on 2026-11-18. Each has the choice taken for now.

1. **Which Harbor commit to import.** The in-progress branches were cut from
   Harbor `stage-0.1.7` (`01f8651`). That commit is six commits ahead of Harbor's
   public `main` and holds an unreleased security change. Publishing it here
   would disclose that change before Harbor ships it. *Taken:* import from
   public `main` (`46da8eb5`). None of the branches touches any of the 17
   imported files that differ between the two commits. Once Harbor publishes
   0.1.7, refresh with `tools/import_harbor.py --ref <commit> --prune`.
2. **Toolbench cases modelled on real use.** Most case files carry `trap`
   comments saying a case was modelled on a problem seen in real use (Harbor's
   real-usage and feature reports, by item number); one prompt is marked
   verbatim. The prompts are generic and the files are already public in
   Harbor. *Taken:* imported unchanged; the comments could be reworded later.
3. **`service/` imported whole.** It includes Harbor features the benchmark does
   not exercise (Study tab code, phone gateway, Google connectors). *Taken:*
   import whole, because cutting it down would mean editing files.
4. **iOS scope.** Only `ios/Packages/HarborLing/` was imported. *Taken:* bring
   in `ios/App/OnDeviceModel.swift` or `ios/Sources/HarborCore/` only if the
   iPhone runner needs them.
5. **Default Toolbench model.** `scripts/run_toolbench.py` defaults to
   `gemma-4-E4B-it-oQ4e-mtp`, the untuned second reference, not the primary
   baseline `Gemma-Wisp`. *Taken:* left as is. Runs must pass both references
   explicitly (`--models Gemma-Wisp,gemma-4-E4B-it-oQ4e-mtp`), since every
   model is reported as a difference from both.
6. **One dry-run warning.** `--dry-run` reports `mail_water_shutoff` as
   `host-forbids` (the host withholds the mail tools that case expects). It is
   the same at the source commit. *Taken:* left for the harness fairness work.
7. **Package name for the benchmark's own code.** `edat/` follows the agentic
   spec's proposal. *Taken:* use `edat/` until the project has a final name.
8. **Simulation QA manifest.** The harness-fairness and product-eval branches
   both add their new tests to Harbor's `scripts/run_simulation_qa.py`. That is
   Harbor release tooling and is not imported. *Taken:* leave that file out when
   applying those branches (see below). If this repository gets CI, list the
   test files in its own workflow instead.
9. **One test needs the Mac app's source.**
   `tests/test_send_recipient_preflight.py::test_the_draft_cards_show_the_quiet_line_neutral_with_no_tick`
   reads `app/Sources/HarborApp/HarborClient.swift`. *Taken:* `app/` stays out;
   deselect that test (see IMPORT.md).
10. **Remote-inference module path.** The cloud-reference lane was described as
   reusing `service/inference/endpoints.py`. No such file exists in Harbor; the
   endpoint code is `service/config/endpoints.py`, which is imported. *Taken:*
   that file.
11. **Naming.** The benchmark is tinyagent-bench, matching the repository. Its
   own code still uses the internal prefix `edat` (the `edat/` package,
   `tests/test_edat_*.py`, schema ids such as `edat.result/1.0`, `EDAT_*`
   environment variables, the schema `$id` URLs). *Taken:* one mechanical rename
   pass to `tinyagent_bench` once the open build branches have merged, so it
   doesn't conflict with them. Imported Harbor code keeps Harbor's identifiers
   (`HARBOR_*`, `Toolbench`, `ios/Packages/HarborLing/`) for good.

## Bringing in the in-progress branches

Four branches in a Harbor checkout hold benchmark work that belongs here. They
were cut from Harbor `01f8651` and keep Harbor's paths. Bring each one in as a
patch, once it is finished and has passed its own review, and only when the
benchmark's coordinator says so:

```bash
git -C <harbor> diff 01f8651 <branch> --binary -- . ':(exclude)scripts/run_simulation_qa.py' > branch.patch
```

```bash
git apply --3way --check branch.patch
```

```bash
git apply --3way branch.patch
```

Then rerun the offline checks and commit with the branch named in the message.
Apply them one at a time, in this order:

| Branch | Touches | State on 2026-11-18 |
| --- | --- | --- |
| `claude/bench-profiles` | New: `scripts/bench_profiles/`, `test_fixtures/bench_profiles/`, one new test | 1 commit, more uncommitted |
| `claude/bench-harness-fairness` | Imported: `scripts/run_toolbench.py`, `scripts/toolbench/` (including case files), two `tests/test_toolbench*.py`. New: four `scripts/toolbench/` modules, four tests. Not imported: `scripts/run_simulation_qa.py` (leave out) | 5 commits |
| `claude/bench-product-eval` | Imported: one `sandbox/` file. New: `scripts/productbench/`, `test_fixtures/productbench/`, one script, one test. Not imported: `scripts/run_simulation_qa.py` (leave out) | 1 commit, more uncommitted |
| `claude/bench-iphone-runner` | New: `ios-bench/` only | Uncommitted |

Profiles go first because product-eval reads them. The two branches that touch
imported files share no files with each other. Before applying a branch's new
fixtures, scan them for real names, addresses and numbers as for any other
fixture.
