# Run sheet: screen control, core run on the two references (nothing here has been run)

**Not run yet.** It starts only when the person running the benchmark says so. Local models only, on
this Mac through oMLX; no cloud model, nothing published, results stay in a private folder.

## What runs

| Track | Cases | Trials | Model calls |
|---|---|---|---|
| `ax_text` (text track) | 200 core cases: 102 dev + 98 held-out | 3 | one per step; budget max(6, 2 × optimal), at most 24 |
| `screenshot_som` (screenshot track) | 100 core cases: 51 dev + 49 held-out | 3 | the same, each step with one PNG |

The core is the build's stratified core (`core: true`), the same on every track. The held-out split
comes from `$EDAT_PRIVATE_DIR/screen/heldout/`; it runs only locally (a cloud entry is refused it).
Records carry `selection: {tier: core, manifest_sha256}`, so a core run is never pooled with a full or
smoke run.

**Entries, in run order** (one model resident at a time; text track first, then screenshots):

| # | Model (oMLX id) | Role | Thinking | Sampling sent with every request |
|---|---|---|---|---|
| 1 | `gemma-4-E4B-it-r5-step-01800-oQ4e-mtp` (Gemma-Wisp r5, tuned) | baseline | off (the mode Harbor ships it in) | temperature 1.0, top_p 0.95, top_k 64, max 256 output tokens |
| 2 | `gemma-4-E4B-it-oQ4e-mtp` (base Gemma 4 E4B) | reference | off (its template's default) | the same |

- **Sampling and thinking** come from the shared roster (`edat/sidekick/ceiling/models.py`: Google's
  card, the same values for both modes). The runner sends them, plus the trial's seed, with every
  request; oMLX's own per-model settings are read and never changed.
- **What is recorded:** every record stores what was sent: `settings.sampling`, `sampling_source:
  author_recommended`, `thinking`, `seed`, `world_seed`, the weights' sha256, the oMLX version, and the
  renderer and Pillow versions for screenshot trials.
- **Both builds have a vision tower** in their weights and load with `mlx-vlm`. Whether oMLX accepts an
  image for them on chat requests is checked by the smoke step.
- **Engine refuses images:** if the first 3 screenshot trials of a model all fail on the engine, that
  model's screenshot track is recorded as skipped ("images are probably not accepted") and the run
  goes on.

## What a model can touch

- **The simulated world only.** A model's actions (`click`, `type_text`, `select_option`, `scroll`,
  `press_key`, `go_back`, `finish`, `ask_user`) change Screen World and nothing else. Screen World is a
  pure-Python state machine with invented apps and `.example` sites, held in memory.
- **No way out of the simulator.** It has no network, no OS automation, no clock and no file access
  outside the run folder. `tests/test_edat_screen_safety.py` checks that no screen module imports a
  network, process or automation library, or anything that can capture the real screen (`ImageGrab`,
  `screencapture`, Quartz, AppKit, `osascript`). It also checks that a trial runs with sockets disabled.
- **Screenshots are drawn, never captured.** `edat/screen/render/raster.py` draws the simulated screen
  with Pillow, from the same layout as the text observation (1,280 × 800, numbered marks, Pillow's
  embedded font). Nothing in this tier reads the Mac's display.
- **What the harness does outside Python** (`edat/screen/live.py`):
  - HTTP to oMLX on a loopback address only, refused before anything is sent otherwise: chat requests, and
    listing, unloading and reloading resident models. The engine's API key is never read or sent. oMLX here
    listens on 127.0.0.1 with key checks skipped for loopback, and the preflight and the run refuse to
    start if that changes;
  - read-only host checks (`pgrep` for Harbor or its old name Wisp, failing closed if `pgrep` errors;
    `memory_pressure`; free disk);
  - results written under `$OUT`.
- **Run it outside Claude Code's sandbox.** A sandboxed `pgrep` cannot see other processes, so the
  Harbor check would always pass, and the sandbox may block the loopback port.

## What the run changes, and how it is put back

| What | During the run | Put back by |
|---|---|---|
| Harbor | must be closed. The run refuses to start while it is open, and stops between trials if it opens. The run never opens or quits it | nothing |
| oMLX | used as it is: never started, stopped, restarted or reconfigured | nothing |
| Resident model | at the start of each sitting, what is loaded and pinned is saved to `$OUT/engine-state.json` (fresh for the sitting; a sitting refuses to start if the last one's state was never restored). Before each model every other chat model is unloaded; helper models are left alone; the engine loads the model under test on its first request | restored automatically when the command ends: done, stopped, error, Ctrl-C, SIGTERM, SIGHUP (closing the terminal) or SIGQUIT (a second signal cannot cut the restore short). Not while Harbor is open: then the engine is left alone, the state stays unrestored and the next sitting refuses to start until `restore --out` has run. Every model loaded before is loaded again and checked; the state file and the sitting log record `restored`. Only SIGKILL or power loss skips it: see "Put things back by hand" |
| Heavy-job lock | `mkdir $LOCK` once per command; the command refuses to start if it exists | removed at the end, only if this command created it |
| Keep awake | `caffeinate -i` around the command | ends with the command |

## Commands, in order

From the repository root (current `origin/main`), with:

```bash
OUT=~/tinyagent-bench-results/screen-control
```

```bash
LOCK=/private/tmp/claude-501/wisp-heavy.lock
```

```bash
export EDAT_PRIVATE_DIR=<the private folder>
```

**1. Preflight** (only reads; loads nothing, sends nothing to a model):

```bash
.venv/bin/python -m edat.screen.live preflight --out $OUT --lock $LOCK --engine --weights
```

- **It checks:**
  - `$OUT` is outside the repository, and the engine address is loopback (if either fails, it stops
    there and writes or sends nothing);
  - oMLX answers loopback requests without its key, and the last sitting's engine state was restored;
  - the held-out split is present, and the core has 200 text and 100 screenshot cases;
  - the lock is free and Harbor is closed;
  - at least 30% memory and 5 GB of disk are free;
  - both reference folders are complete, with their quantisation, loader, shipped thinking mode and
    sampling;
  - a harness dry run passes: the oracle stub on 2 smoke cases per track, with no model.
- **`--engine`** asks the engine (GET only) whether it lists both ids. Other loaded chat models are a
  note, because the run unloads them.
- **`--weights`** hashes both weight folders (about 11 GB read), cached in `$OUT/weights.json`.
- Any FAIL line means do not start.

**2. Smoke step** on the tuned reference, about 7 minutes at the assumed pace. It runs 12 text trials
and 4 screenshot trials (dev core cases spread over families and levels, 1 trial each):

```bash
caffeinate -i .venv/bin/python -m edat.screen.live run --plan smoke --out $OUT --lock $LOCK --max-minutes 30
```

**Check the output:**
- 16 trial lines, each `PASS` or `FAIL <why>`, with its steps and seconds;
- no `excluded`;
- both units `done`. `screenshot_som: skipped` means oMLX did not take the images;
- `done` at the end;
- the sitting log `$OUT/smoke/sitting-*.json` says `"restored": true`.

Then re-estimate from the measured pace (seconds per step, and steps taken per optimal step):

```bash
.venv/bin/python -m edat.screen.live plan --plan core --measured $OUT
```

**3. The core run**, one sitting per reference. `--max-minutes` stops a sitting cleanly between
trials, and the same command resumes it:

```bash
caffeinate -i .venv/bin/python -m edat.screen.live run --plan core --only gemma-4-E4B-it-r5-step-01800-oQ4e-mtp --out $OUT --lock $LOCK --max-minutes 480
```

```bash
caffeinate -i .venv/bin/python -m edat.screen.live run --plan core --only gemma-4-E4B-it-oQ4e-mtp --out $OUT --lock $LOCK --max-minutes 480
```

`--track ax_text` or `--track screenshot_som` limits a sitting to one track.

**4. Report**, after any sitting (it covers what has been recorded):

```bash
.venv/bin/python -m edat.screen.live report $OUT
```

**Put things back by hand** only if a sitting was killed hard (SIGKILL, power loss), so that its
automatic restore never ran. The next sitting refuses to start until this has been done:

```bash
.venv/bin/python -m edat.screen.live restore --out $OUT
```

Then remove `$LOCK`, if that sitting created it.

## Duration and its basis

| Plan | Per model | Both references |
|---|---|---|
| smoke (tuned only) | about 0.11 h | — |
| core, text track | about 4.0 h | about 7.9 h |
| core, screenshot track | about 3.1 h | about 6.2 h |
| **core, both tracks** | **about 7.1 h** | **about 14.2 h** |

**How the hours are computed:**
- Spec 11 §3.11 assumes 2.5 s per step. A screenshot step carries an image, so it is assumed at 3.9 s,
  the figure that reproduces the spec's 2.9 h.
- A trial is assumed to take 1.7 × its optimal steps, capped at its budget. Real models wander, and
  failing trials run to the budget.
- Each trial adds 0.3 s, and each model change a minute.
- `plan --measured` replaces seconds per step and the step factor with the smoke step's figures, per
  track.
- **Not measured before the smoke step:** the image-token cost of a 1,280 × 800 screenshot for these
  models, and how much prompt caching helps across a trial's steps.

## Stop conditions and resuming

The run checks between trials and stops cleanly, restoring the engine, when:
- `$OUT/STOP` exists (to stop it from another terminal);
- `--max-minutes` has passed;
- free memory is under 15%;
- Harbor has been opened;
- over 2% of a unit's trials were lost to engine faults (checked from 50 trials on).

**Resume** by running the same command again. Recorded trials are never redone, and an engine-fault
trial stays recorded as `excluded`. A results folder is bound to its case list: if the core manifest
changes, the run refuses to resume there and needs a new `$OUT`.

**Stop by hand** if the Mac is unresponsive (the pointer or a terminal lags for seconds), or if no trial
line has printed for 10 minutes.

## What the person gets

- **Records:** `$OUT/core/runs/screen-core-<model>-<track>/`, in the results store's format. Each has
  `records.jsonl`, `raw/` (every trial's full transcript, final state, effects and typed text) and
  `manifest.json`.
- **Attachments:**
  - `restraint-events.jsonl`, the `edat-restraint-event/v1` guard results with the track named;
  - `restraint-trials.json`, the denominators;
  - `perf-samples.jsonl`, with `test: screen_task` and the level budgets.
- **Logs:** `$OUT/core/sitting-*.json`, one per sitting: what ran, what was sent, why it stopped, and
  whether the engine was restored.
- **Report:** `report $OUT` prints the Screen Score, reliability, grounding, violations and the other
  SC metrics per model and track.

Exporting or publishing anything is a separate decision.
