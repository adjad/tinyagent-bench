# Import from Harbor

Most of this repository's code was imported from
[Harbor](https://github.com/adjad/Harbor), a local-first macOS assistant
released under Apache-2.0. Every imported file keeps its Harbor path, so work
written against Harbor (patches, branches) applies here without path changes.

| | |
| --- | --- |
| Source repository | https://github.com/adjad/Harbor |
| Branch | `main` (public) |
| Commit | `46da8eb5c9ed42219a946422789e9b3f1d3bd3a6` |
| Imported files | 547 |
| Import date | 2026-11-18 |

Every imported file is byte-identical to Harbor's public `main` at that commit.
The benchmark's in-progress branches were cut from a later, not yet public
Harbor commit (`stage-0.1.7`, `01f8651`). That commit is not imported because it
holds unreleased Harbor changes. None of those branches touches a file that
differs between the two commits, so their patches apply here unchanged. When
Harbor publishes it, refresh with `--ref` (below).

## Machine-readable files

- [`import/harbor-import.json`](import/harbor-import.json): the import
  definition. Source commit, included paths (a trailing `/` means the whole
  folder), explicit exclusions inside included folders, and the folders and files
  deliberately not imported, each with its reason.
- [`import/harbor-files.json`](import/harbor-files.json): the resolved list of
  every imported file with its git mode and blob id at the source commit.

## Re-running the import

[`tools/import_harbor.py`](tools/import_harbor.py) reads files straight from a
Harbor commit with git (never from a working tree, so uncommitted changes in a
Harbor checkout cannot leak in).

```bash
git clone https://github.com/adjad/Harbor.git ../Harbor
```

Check that this tree still matches the recorded import (exit code 1 lists every
file that differs, is missing, or is no longer selected):

```bash
python3 tools/import_harbor.py --harbor ../Harbor --check
```

Re-create the files and refresh `import/harbor-files.json`:

```bash
python3 tools/import_harbor.py --harbor ../Harbor
```

To move to a newer Harbor commit, pass `--ref <commit>`, review `git diff`, then
record the new commit in `import/harbor-import.json`. Files that drop out of the
selection are listed and kept unless `--prune` is given. Once local changes are
made to imported files (for example the harness fairness fixes), `--check`
reports those files as differing; that is expected.

## What was imported

| Path | Why |
| --- | --- |
| `service/` (whole, one file excluded) | Harbor's backend: agent loop, config, router, tool registry, oMLX client, model qualification (`service/inference/qualify.py`). Toolbench and the other harnesses import it, so it is imported whole rather than cut down. |
| `sandbox/` | A simulated world (mail, messages, calendar, notes, clock) for running an isolated Harbor backend. |
| `scripts/run_toolbench.py`, `scripts/toolbench/` | Toolbench: 383 deterministic tool-calling cases, faked tools over a synthetic world, a pure scorer, reports. |
| `scripts/bench_ondevice_perf.py` | Speed and memory sweep against oMLX. |
| `scripts/eval_planner.py`, `scripts/eval_planner_fakes.py`, `test_fixtures/routing/quality_corpus.json` | Planner evaluation over a synthetic routing-quality corpus. |
| `scripts/run_routing_stress_suite.py`, `build_`, `report_`, `grade_routing_stress_strict.py`, `check_routing_stress_permissions.py`, `test_fixtures/routing_stress/`, `docs/routing_stress/`, `docs/HARBOR_TOOL_ACTIVATION_INVENTORY.json` | The 1,000-case compound-request stress suite, its builder, graders and write-ups (all public in Harbor). |
| `scripts/eval_arms.py`, `scripts/eval_core.py`, `scripts/eval_ling_tasks.py` | Arm-by-arm evaluation against the sandboxed backend, and its task set. |
| `scripts/email_commitment_extraction_bench.py`, `scripts/email_profile_fact_bench.py`, `scripts/long_email_digest_recall.py` | Email extraction and recall benches over synthetic mail. |
| `scripts/demo_sweep.py`, `scripts/demo_prompts.json` | Mechanical checks over a fixed prompt sweep. |
| `test_fixtures/memory_eval/` | Memory evaluation cases and their scorer. |
| `tests/` (17 test files, `tests/__init__.py`, `tests/conftest.py`, `tests/fixtures/`) | The tests that cover the imported harnesses, plus the test modules they import (listed below). |
| `conftest.py`, `tests/conftest.py`, `pytest.ini`, `requirements-runtime.txt`, `requirements-dev.txt`, `TESTING.md` | Test bootstrap, dependencies, testing notes. The two `conftest.py` files keep every test away from real Harbor state and the real login Keychain. Never run the tests without them. |
| `build-support/engine_guard/` | The patches Harbor applies to oMLX, the engine the baseline runs on. The perf work needs to know them, and `tests/test_power_tier.py` reads them. The rest of `build-support/` is not imported. |
| `docs/ASSISTANT_ARCHITECTURE.md`, `docs/technical-overview.md`, `docs/inference-providers.md`, `docs/REMOTE_INFERENCE.md` | How the imported backend works, including the remote-inference client the hosted reference uses (`service/inference/providers.py`, `service/inference/remote_policy.py`, `service/config/endpoints.py`). |
| `ios/Packages/HarborLing/` | Harbor's MLX Swift package (on-device model runtime and parity checks), for the iPhone runner to reuse. |
| `LICENSE` | Harbor's Apache-2.0 licence. |

Imported test files: `test_toolbench.py`, `test_toolbench_world.py`,
`test_rc3_toolbench_scorer.py`, `test_effect_refusal_no_lock.py`,
`test_harbor_self_reference.py`, `test_eval_planner_dry_run.py`,
`test_sandbox_world.py`, `test_reply_bridge_simulation.py`,
`test_backend_identity_and_sandbox_isolation.py`, `test_memory_eval_scorer.py`,
`test_local_provider_tools.py`, `test_power_tier.py`, `test_long_email_digest.py`,
`test_model_only_quality.py`, `test_model_led_integration.py`,
`test_model_led_entrypoint.py`, `test_send_recipient_preflight.py`.

## What was left out, and why

Excluded inside an imported folder:

- `service/corpus_cases.json`: its own provenance note says the request patterns
  were adapted from a review of saved Harbor chats, that is, from real history.
  Only `service/corpus.py:live_cases()` reads it, lazily, and no benchmark code
  calls that.

Not imported:

| Path | Reason |
| --- | --- |
| `app/`, `browser-extension/`, `integrations/` | The Mac app, browser extension and third-party integrations. The benchmark drives the Python backend. |
| `build-support/` (apart from `engine_guard/`), `infra/`, `.github/`, `scripts/harbor-build`, `scripts/package_app.sh`, `scripts/release_performance.py`, `scripts/resign_local_install.py`, `scripts/run_simulation_qa.py`, Swift contract scripts | Release, packaging, signing, CI and Simulation QA tooling. |
| `air/`, `mini/` | Side-device nodes. (`pytest.ini` still lists `air/tests`; pytest ignores a missing path.) |
| `training/`, LoRA and harvesting scripts | Training data and its tooling. |
| `ios/` apart from `ios/Packages/HarborLing/` | The Harbor iPhone app. `ios/App/OnDeviceModel.swift` and `ios/Sources/HarborCore/LocalInference.swift` are the next candidates if the iPhone runner needs them. |
| `CHANGELOG.md`, `docs/releases/`, Harbor's `README.md`, `PRODUCT.md`, `CONTRIBUTING.md`, `SECURITY.md` | Harbor's release notes and own top-level documents. |
| `scripts/replay_prompts_regression.json` and the replay scripts, `test_fixtures/routing_regressions/historical_prompts.json` | Derived from real history. |
| `scripts/seed_real_apps.sh`, `scripts/clear_real_apps.sh`, `scripts/attention_snapshot.py` | Act on, or read, the user's real apps and state. |
| `scripts/harvest_training_data.py`, `scripts/build_lora_dataset.py` | Training-data tooling that reads real history. |
| `scripts/test_model.py`, `scripts/test_all_tools.py` | Live smoke tests against real data and the live backend. |
| `test_fixtures/task_engine/` | Golden cases whose `source` fields cite Harbor debug exports and quote real prompts. |
| `test_fixtures/performance/` | Fixtures for Harbor's removed release-performance gate. |
| Other `test_fixtures/` folders and other `tests/` | Fixtures and tests for app features the benchmark does not cover (browser, research, attention, Study tab and more). Bring them in when a harness needs them. |

## Verification at import time

Checked on 2026-11-18 in a fresh Python 3.13 virtual environment inside this
repository. No model was loaded and no engine was started.

- `scripts/run_toolbench.py --list`: 383 cases.
- `scripts/run_toolbench.py --dry-run`: 383 cases bound, 1 reported
  (`mail_water_shutoff`, `host-forbids`); the same happens in Harbor.
- The 17 imported test files, each run on its own: 1,327 passed, 1 failed. The
  failure is `test_send_recipient_preflight.py::test_the_draft_cards_show_the_quiet_line_neutral_with_no_tick`.
  It reads the Mac app's Swift source (`app/`), which is not imported, and it
  passes in a full Harbor checkout. Deselect it with
  `--deselect tests/test_send_recipient_preflight.py::test_the_draft_cards_show_the_quiet_line_neutral_with_no_tick`.
- Every `service.`, `sandbox.`, `scripts.` and `tests.` import in the 449
  imported Python files resolves to a file in this repository.

## Notes on imported content

- Most Toolbench case files (`scripts/toolbench/cases/*.yaml`) carry `trap`
  comments citing Harbor's internal real-usage and feature reports by item
  number, saying the case was modelled on a problem seen in real use; one
  (`compose.yaml`, `compose_draft_events_message`) says its prompt is verbatim
  from such a report. The prompts are generic, use the synthetic world's
  placeholders, and carry no personal details. The files are unchanged from
  Harbor's public `main`.
- `scripts/eval_core.py` scores run records that hold real prompts when run
  against a live assistant. No such records are imported; only the scorer is.
- `scripts/run_routing_stress_suite.py` copies the running user's
  `~/.moe/config.yaml` and skills into its isolated home. That is noted in
  [docs/benchmark/07-harness-review.md](docs/benchmark/07-harness-review.md)
  for the harness fixes.
