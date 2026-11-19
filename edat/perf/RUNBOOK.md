# Runbook: one model's performance pass on the Mac

How to measure one candidate model's speed, memory, 16 GB fit and energy on the
reference Mac (MacBook Pro, M5 Pro, 24 GB) with EDAT's performance runner. The
method is spec 04 (`docs/benchmark/04-perf-energy-memory-spec.md`).

**This runbook has not been run.** Every command was tested only against a fake
engine. The first real pass will find things to fix; note them in the run's
`operator_note` events or beside the results.

Placeholders: `<repo>` is this checkout, `<run-root>` a folder for results (for
example `<repo>/results/perf`, which git ignores), `<cand>` the candidate's entry id
in the manifest, `<port>` the benchmark engine's port (any free port except 8000,
8765 and 8791; the examples use 8779).

## What one pass is

Four invocations, each its own run (`<UTC start>-<4 hex>`), in this order:

| Pass | Plan | Power | Needs the admin helper | Rough duration, one candidate |
|---|---|---|---|---|
| 1. Speed and memory | `speed` | on AC, display on | no | about 1.5 h |
| 2. Load times | `load` | on AC | yes (`purge`) | about 15 min |
| 3. 16 GB fit | `fit16` | on AC | yes (balloon, wired limit) | about 20 min |
| 4. Energy | `energy` | **on battery, display off** | no | about 2–3 h |

Durations are estimates at assumed speeds, without cooldown waits. Print a better
one for your model list with `plan` (step 6). Long levels (32,768 and 65,536)
dominate; a model whose window is shorter skips them (`exceeds_native_window`).

Every session is bracketed by the two reference models: Gemma-Wisp, base Gemma 4
E4B, the candidate, base, Gemma-Wisp (§1.5). That is why the references appear in
every run.

The reference models' own full sweep (`--plan full`) runs once per new device, OS
and oMLX version, not every pass.

## Before the first pass (once)

1. **Python.** From `<repo>`:

   ```bash
   python3.13 -m venv .venv
   ```

   ```bash
   .venv/bin/pip install -r requirements-dev.txt jinja2
   ```

   `jinja2` renders the model's chat template for the prompt pack; it is not in the
   requirements files yet.

2. **The corpus.** The agent-shaped corpus and the 6-step trajectory come from the
   agentic build (`edat-agent-corpus-v1.jsonl`, `edat-agent-trajectory-v1.json`,
   spec 02 §11). The files in `test_fixtures/perf/` are tiny synthetic stand-ins for
   tests only and cannot fill the real levels.

3. **A prompt pack per model entry** (thinking-on and thinking-off entries get one
   each). This reads only the model's tokenizer and template, not its weights:

   ```bash
   .venv/bin/python -m edat.perf pack --corpus <corpus.jsonl> --trajectory <trajectory.json> --model-dir <model folder> --model-id <id> --entry-id <entry id> --thinking on --variants 64 --out <run-root>/packs/<entry id>.pack.json
   ```

   Build one for Gemma-Wisp, one for `gemma-4-E4B-it-oQ4e-mtp` and one for `<cand>`.
   `--variants 64` gives the energy pairs enough fresh prompts (the iPhone uses the
   first 16). A level the corpus cannot fill is listed as missing.

4. **The manifest.** Copy `test_fixtures/perf/models-manifest.example.json` to
   `<run-root>/models.json`, keep the two reference entries, replace the example
   candidate with `<cand>`, and set for each entry: `engine_ids`, `pack`,
   `native_window_tokens` (from the model's `config.json`), `profile_window_tokens`,
   `model_dir` (so `--hash-files` can hash and check the weights), `quant`,
   `architecture` and `speculation`. Gemma-Wisp's file hashes are already pinned.

5. **The benchmark oMLX instance** (D12). The user's everyday engine is never
   changed; the benchmark uses its own instance with its own settings and model ids:
   - its own base path and a private `HOME` (Harbor's engine manager found that
     `--base-path` alone does not isolate oMLX; `service/engine/manager.py`);
   - listening on `127.0.0.1:<port>`;
   - an API key in its settings; export the same value as `EDAT_OMLX_API_KEY` in the
     terminal that runs EDAT (it is sent as a bearer token and never written down);
   - **paged SSD KV cache off** and **expert offload off** (§5.2); note the memory
     guard ceiling, and pass it as `--memory-guard-ceiling-gb`;
   - one model id per mode: `<id>` with MTP as shipped, and `<id>-plain` with MTP
     off (a second folder of symlinks to the same files with its own settings entry).
     Whether oMLX accepts that is unverified (§1.4, §16): if it does not, the plain
     mode cannot run and the board shows as-shipped ratios only;
   - a `max_context_window` large enough for 65,536 + 256 tokens on every model
     whose native window allows it.

   The runner never starts, stops or reconfigures oMLX. Start the instance yourself,
   for example `HOME=<private home> /Applications/oMLX.app/Contents/MacOS/omlx-cli serve --host 127.0.0.1 --port <port> --base-path <bench base>` (flags as Harbor's
   engine manager uses them; check against your oMLX version).

## Before every pass

6. **Print the plan and its duration** (contacts nothing):

   ```bash
   .venv/bin/python -m edat.perf plan --manifest <run-root>/models.json --models <cand> --plan speed
   ```

7. **Quit, and leave quit for the whole pass:**
   - the Harbor app and its backend (ports 8765 and 8791 must stay closed);
   - the everyday oMLX app (port 8000), unless it has no model loaded and nothing
     will use it; another client loading a model mid-window voids the window;
   - browsers, mail, chat apps, Xcode, simulators, IDEs, music and video;
   - anything that builds, downloads, syncs or backs up (Time Machine, iCloud Drive,
     Photos, Spotlight indexing after a large copy, software updates);
   - other benchmark or test processes.

   **The Mac must not be doing** any other GPU work, heavy disk I/O, or memory-hungry
   work during a pass, must not sleep, and must not change power source mid-pass.
   Low Power Mode off. Lid open, on a hard surface, room temperature steady; note it
   with `--ambient-c`.

8. **Rest.** At least 10 minutes after boot or after heavy use, until the thermal
   state is nominal. The runner also waits for nominal before every request, up to
   5 minutes, and flags requests that ran hot.

9. **Pre-flight** (read-only):

   ```bash
   .venv/bin/python -m edat.perf check --engine http://127.0.0.1:<port>
   ```

   Note the `gpu_wired_limit_mb` it prints the first time; pass it as
   `--recorded-wired-limit-mb` on later checks. A different value means a helper was
   killed without restoring (reboot to clear it).

## Pass 1: speed and memory (on AC)

10. Plug in. Display on is fine.

    ```bash
    .venv/bin/python -m edat.perf run --manifest <run-root>/models.json --models <cand> --plan speed --engine http://127.0.0.1:<port> --engine-pid <pid of the benchmark omlx-server> --out <run-root> --display on --attest other_apps_closed hard_surface --ambient-c <°C> --hash-files
    ```

    `--engine-pid` is the benchmark instance's server process (`ps -axo pid,comm | grep omlx-server`);
    pass it whenever another engine might be running.

## Passes 2 and 3: load times and the 16 GB fit (on AC, with the admin helper)

The helper is the only part of EDAT that runs as root. Read
`tools/edat_admin_helper/README.md` and the script itself first. It serves one
client and exits when that client disconnects, so start it fresh for each pass.

11. Make a folder for the helper (owned by you) and rehearse it without root:

    ```bash
    mkdir -p <run-root>/helper-load
    ```

    ```bash
    /usr/bin/python3 tools/edat_admin_helper/edat_admin_helper.py --run-dir <absolute run-root>/helper-load --print-plan
    ```

12. Start it in its own terminal:

    ```bash
    sudo /usr/bin/python3 tools/edat_admin_helper/edat_admin_helper.py --run-dir <absolute run-root>/helper-load
    ```

13. Run the load pass in another terminal:

    ```bash
    .venv/bin/python -m edat.perf run --manifest <run-root>/models.json --models <cand> --plan load --engine http://127.0.0.1:<port> --engine-pid <pid> --out <run-root> --admin-socket <absolute run-root>/helper-load/admin.sock
    ```

    If `purge` does not empty the file cache for the weights, cold reps say
    `cold_verified: false`; then fall back to a reboot and 5 minutes' rest per cold
    sample (§7).

14. For the fit pass, make `<run-root>/helper-fit16`, start a fresh helper on it
    (step 12), and run `--plan fit16` with `--admin-socket <absolute run-root>/helper-fit16/admin.sock`.
    The helper wires 10.5 GiB and sets the 16 GB GPU wired limit, then restores both.
    Set the benchmark instance's memory-guard ceiling to 13 GiB for this pass, and
    pass `--memory-guard-ceiling-gb 13`.

15. After each helper pass, check that the wired limit is back:

    ```bash
    sysctl -n iogpu.wired_limit_mb
    ```

## Pass 4: energy (on battery, display off)

16. Charge to at least 90 %, then unplug. Keep the Mac awake with the display off:
    start `caffeinate -i` in a terminal for the length of the pass, then turn the
    display off (for example `pmset displaysleepnow`). Nothing else may run.

    ```bash
    .venv/bin/python -m edat.perf run --manifest <run-root>/models.json --models <cand> --plan energy --power --engine http://127.0.0.1:<port> --engine-pid <pid> --out <run-root> --display off
    ```

    `--power` samples SMC `PSTR` at 20 Hz without administrator rights. The SMC
    reader is unverified on macOS 27: if the first seconds of
    `power-<run_id>-smc_pstr.jsonl` show no readings or zeros, stop the pass and report it.
    Stop below 20 % battery.

## After the pass

17. **Aggregate** each run (raw samples to cells, ratios against both references in
    both modes, and verdicts):

    ```bash
    .venv/bin/python -m edat.perf aggregate <run-root>/<run_id>/run-<run_id>.json
    ```

18. **Keep it private until reviewed.** Run folders contain your device's model id
    and OS build; nothing personal, but published results go through review into
    `data/results/` (docs/STRUCTURE.md), never straight from `<run-root>`.

## What each file in a run folder is

| File | What |
|---|---|
| `run-<run_id>.json` | the `edat.perf/1.0` document (spec 04 §3) |
| `samples.jsonl`, `events.jsonl`, `windows.jsonl` | raw samples, events and energy windows, written as they happen |
| `mem-<run_id>.jsonl` | 20 Hz `phys_footprint` of the engine process |
| `power-<run_id>-smc_pstr.jsonl` | 20 Hz whole-device power (energy pass) |
| `admin-audit.jsonl`, `powermetrics-*.plist` | the helper's audit and any powermetrics output (helper passes) |

## If something goes wrong

- **The engine dies mid-pass.** The request is recorded as `killed_unknown` or
  `oom_killed`, higher levels of that model as `not_run_above_oom`. Restart the
  benchmark instance yourself; with `--wait-engine-s 300` the runner waits for it
  and retries the request once.
- **Ctrl-C.** The run is finalized with what it has; the helper restores on
  disconnect.
- **The helper was killed with SIGKILL.** The wired limit stays changed until a
  reboot; the balloon is freed with the process. `check` warns about it.
