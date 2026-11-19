# Run sheet: the routine agentic run on the two references

The first real run of the agentic tier (spec 02). It runs on this Mac only with
the go-ahead, after the in-assistant runs have left the engine. Tool:
`edat/cases/agentic_run.py` (`python -m edat.cases.agentic_run`). Every run goes
through the shared run guard, `edat/runtime/run_guard.py` (lock, engine state,
signals, Harbor, stops).

## What runs

| | |
|---|---|
| Models, in order | `gemma-4-E4B-it-r5-step-01800-oQ4e-mtp` (Gemma-Wisp r5, tuned reference, role `baseline`), then `gemma-4-E4B-it-oQ4e-mtp` (Gemma 4 E4B, base reference, role `reference`) |
| Thinking | the mode each ships in: **off** for both (`edat/sidekick/ceiling/models.py` `KNOWN`) |
| Sampling | the author's, sent on every request and recorded as `author_recommended`: temperature 1.0, top_p 0.95, top_k 64 (Google: the same for every size and both modes) |
| Trials | 3; trial t uses world seed t and sampling seed 1000 + t |
| Loops | the minimal reference loop (`reference-loop`, the headline) on every case; Harbor's own Ask turn (`harbor-loop`) on the column's cases (§5.6.1) |
| Splits | the development split and the private held-out split (`$EDAT_PRIVATE_DIR/agentic/heldout`); English only |
| Selection (`routine`) | every case tagged `core` in each split (the 7 headline families, privacy, preferences, memory, date-time, oversized results, long sessions) plus the noisy variants (typo, transcript, paraphrase) of each split's 30 core seeds. The scaling probe is not in the routine run. |
| Selection (`smoke`) | 9 development cases on the tuned reference, 1 trial: one single-turn case per headline family (both loops), one neutral-lane case and one two-turn preferences case (reference loop only). 16 trials. |
| Selection (`smoke-long`) | one assistant-lane long session (60 user turns, history up to 9.3k tokens) on the tuned reference, 1 trial, reference loop only. It times long sessions apart from the other cases. |

The Harbor-loop column has no trial for the neutral lane, preferences, memory,
long sessions or noisy variants (§5.6.1). A column case Harbor's host refuses
outright is recorded `not_applicable` without a model call. In the column,
thinking off is Toolbench's `off` variant (`enable_thinking=false` on every
request). Harbor itself would think on its selection steps, so this column
measures Harbor's loop at the model's shipped mode, not Harbor's production
thinking setting.

## Time

Estimate = user turns × trials × seconds per turn. The spec's speed assumption
is 10 s per single-turn case-trial on the M5 Pro, which is not yet measured.
Long sessions are timed and estimated at their own rate. `plan` prints the
routine run at three sizes; after the two smoke steps, `--measured $OUT`
replaces the assumption with measured speed.

| Per model (both splits) | Case-trials | User turns | Hours at 10 s/turn |
|---|---|---|---|
| Reference loop | 2,871 | 4,362 | 12.1 |
| Harbor loop | 1,110 | 1,110 | 3.1 |
| **Per model** | | | **15.2** (development 7.7, held-out 7.5) |
| **Both references** | | | **30.4** |

| Size (per model, at 10 s/turn) | Flags | Hours |
|---|---|---|
| (a) both splits, 3 trials | (default) | 15.2 |
| (b) development 3 trials, held-out 1 | `--heldout-trials 1` | 10.2 |
| (c) development only, 3 trials | `--splits dev` | 7.7 |

The size is chosen after the smoke steps, from the measured re-estimate.

The spec's 11.5 h per model (8.8 h + 2.7 h) is for one split. Running both
splits is what doubles it. The long sessions (2 per split, 60 user turns each)
are 720 of each model's reference-loop turn-trials. Their turns carry up to
9.3k tokens of history, so they will likely run slower than 10 s per turn.
The `smoke-long` step measures them.

## Before the run

1. The go-ahead has been given, and the in-assistant runs are finished.
2. Harbor is closed in every form: the app (also under its old name, Wisp), a `swift run` build, and a standalone backend. They share oMLX, and the run refuses to start while any of them is found.
3. No unrestored engine save is left from an earlier run. If one is, the start is refused with the restore command.
4. Work in the shared clone, on a commit that contains this sheet. Use the repository's virtualenv.
5. Run the commands outside Claude's sandbox. The lock directory and oMLX's loopback port are outside it.

```bash
export EDAT_PRIVATE_DIR=<the private folder, outside the repository>
OUT=$EDAT_PRIVATE_DIR/results/agentic-routine-1
BOARD=2026.11-draft1   # private pre-release run; a draft board version is never published as is
PY=.venv/bin/python
```

## Steps

1. **Preflight (no model).** Read-only. It checks:
   - OUT is outside the repository and under `$EDAT_PRIVATE_DIR`;
   - the held-out cases are present;
   - the lock is free, and no earlier run left an unrestored engine save;
   - Harbor is closed (process names and ports 8765/8791);
   - free memory is at least 30% (unknown fails) and disk has at least 5 GB;
   - both model folders exist, with a context window of at least 16k.

   The second form also reads the engine's model status: what is loaded, pinned and the default (GET only). It also hashes both references' weights (read only, cached in `OUT/weights.json`).
   ```bash
   $PY -m edat.cases.agentic_run preflight --out $OUT
   ```
   ```bash
   $PY -m edat.cases.agentic_run preflight --out $OUT --engine --weights
   ```
2. **Smoke (a few minutes)** on the tuned reference:
   ```bash
   caffeinate -i $PY -m edat.cases.agentic_run run --out $OUT --plan smoke --board-version $BOARD --max-minutes 20
   ```
3. **Long-session smoke (about 10 minutes)**, one long session on the tuned reference:
   ```bash
   caffeinate -i $PY -m edat.cases.agentic_run run --out $OUT --plan smoke-long --board-version $BOARD --max-minutes 30
   ```
4. **Re-estimate from measured speed** (every `timing.jsonl` under OUT), at the three sizes:
   ```bash
   $PY -m edat.cases.agentic_run plan --out $OUT --plan routine --measured $OUT
   ```
5. **Routine run** at the chosen size. Tuned reference, then base. It can run in several sittings: each sitting stops at `--max-minutes`, and the same command resumes it. For size (b), add `--heldout-trials 1`; for size (c), add `--splits dev`.
   ```bash
   caffeinate -i $PY -m edat.cases.agentic_run run --out $OUT --plan routine --board-version $BOARD --max-minutes 600
   ```
6. **Stop early:** `touch $OUT/routine/STOP` (or `$OUT/smoke/STOP`, `$OUT/smoke-long/STOP`). The run stops before its next trial, restores the engine and releases the lock. Remove the file before resuming. A STOP file already there when a model is due stops the run before anything is unloaded or loaded. Ctrl-C, closing the terminal (SIGHUP), SIGTERM and SIGQUIT also stop the run and restore the engine. A repeat of a signal within 10 s, or any signal once the restore has begun, is recorded in the report (`signals_ignored`) and does not cut the restore short.
   Exit codes: `0` finished; `3` stopped by the STOP file or the time limit (resumable); `1` failed (an error, a memory, fault or Harbor stop, every model trial an engine fault or raised, or a restore that failed); `128+n` interrupted by signal n.
7. **Progress at any time** (no engine): `plan --out $OUT --plan routine --measured $OUT` shows the hours left at each size, with finished trials subtracted.
8. **If a restore failed** (the report says `restore FAILED`, for example because Harbor was opened, a load never finished, or the default model changed), close Harbor and put back what the report names, then run the restore below. It takes the heavy-job lock while it works, and refuses if the lock is held. If no heavy job is running (the run was killed), remove the lock folder first. A bad `--url` is refused before the lock is taken.
   **Known limit: a hung restore.** Once the restore has begun, signals are ignored so that it finishes. It can wait up to 2 min for each load to settle and up to 10 min for each load call. If it hangs beyond that, only `kill -9` stops it. That leaves the lock folder and the save behind: safe, since the next start is refused, but remove the lock folder and run the restore below by hand.
   ```bash
   $PY -m edat.runtime.run_guard restore --save /private/tmp/claude-501/wisp-heavy.lock.engine-save.json
   ```

## What is written where

All of it goes under `OUT` (private; held-out records refuse to be written anywhere else):

| Path | What |
|---|---|
| `OUT/<plan>/store/runs/<run id>/` | `edat.result/1.0` records (`records.jsonl`), raw trajectories (`raw/<sha256>.json`), the store's `manifest.json`. One run per tier and suite. |
| `OUT/<plan>/selection.json` | The selection manifest: sorted case ids, `manifest_sha256`, items per group. It names held-out cases, so it is private. |
| `OUT/<plan>/timing.jsonl` | Seconds, user turns and family per trial: the measured speed. |
| `OUT/<plan>/errors.jsonl` | Trials that raised an error. They are not recorded, so a resume runs them again. |
| `OUT/<plan>/run-report.json` | Start, finish, outcome and exit code, counts, why it stopped, the engine state before, the restore result. Written on every exit, also when the restore fails. |
| `/private/tmp/claude-501/wisp-heavy.lock.engine-save.json` | The engine state saved at this start: loaded, pinned and default model, with a token and a timestamp. It is removed once a restore matches it (the report keeps the state as `engine_before`). It sits beside the lock, so any harness using the guard sees one left unrestored. |
| `OUT/weights.json` | The weights hash cache. |

Every record carries:
- `model` {id, role, weights_sha256, headline_entry};
- `runtime` (`omlx`, or `harbor-omlx` with the harness commit);
- `device`;
- `settings` (thinking, the sampling sent, `sampling_source`, seed, world seed);
- `selection`: {tier `core` (or `smoke`, `smoke-long`), manifest_sha256, n_items, ci95_half_width_pts} per split/lane/family group.

The store never pools records of different selections or manifests.

## Safety

The run guard (`edat/runtime/run_guard.py`) does all of this. Two test files
cover it:
- `tests/test_edat_runtime_run_guard.py` checks every exit path against a fake engine. One of its tests sends SIGHUP to a real child process.
- `tests/test_edat_runtime_run_guard_probes.py` keeps the safety review's probes. They run this CLI against a fake oMLX over real loopback HTTP with a fake key, and cover the signals, a stale state, a failed restore, all-fault smoke, the STOP file, the lock, the key, proxies, redirects, URL tricks and the single URL. They skip where a loopback port cannot be bound. The second review's probes (a second signal during the exit, a load cut off mid-request, an evicted helper, a changed default model, a folder that cannot be made, unreadable memory, trials that raise) are regression tests in `tests/test_edat_runtime_run_guard.py` and `tests/test_edat_agentic_run.py`.

| Rule | How |
|---|---|
| Output folder | A folder inside the repository is refused before anything is written. A held-out run must be under `$EDAT_PRIVATE_DIR`. |
| Shared heavy-job lock | `mkdir /private/tmp/claude-501/wisp-heavy.lock`. An empty lock path is refused. If the lock exists, nothing starts. It is removed only by the run that made it, on every exit. |
| Engine state | Saved fresh at each start, once no model is still loading: loaded chat models, loaded helpers, pinned and default model, with a token and a timestamp. The save is removed after a restore that matches it, so one still present refuses the next start and prints the restore command. |
| One model at a time | Before each model the run waits for the engine to settle (a model still loading counts as loaded), then unloads every other chat model (helper models such as embedders stay), waiting out a queued unload. It stops if any stays loaded, then loads the model. |
| Restore | Waits up to 2 min for any load to finish (oMLX goes on loading after a load request is cut off), unloads what was not loaded before and loads what was, chat models and helpers, pinned models included, waits again, then compares chat models, helpers, the pinned set and the default model. Any difference fails the restore and keeps the save. It runs on every exit: normal end, stop, exception, Ctrl-C, SIGTERM, SIGHUP, SIGQUIT. Signals that arrive while it runs are recorded and ignored. A restore error is recorded in the report and re-raised; the report is written either way, and the lock and signal handlers are released even if one of those steps fails. |
| Harbor | Checked at start, before every unload, load and restore, and every 10 trials. The checks cover the app (`/MacOS/Harbor` with any suffix, such as `Harbor-preview`; `/MacOS/Wisp` alone or with a hyphen suffix, so an unrelated `Wispr Flow` does not match), a `swift run` build (`.build/.../Harbor`), a standalone backend (`-m service` or `uvicorn service`), and anything listening on 8765 or 8791. A check that errors counts as Harbor running. While Harbor runs, nothing is unloaded, loaded or restored. |
| Engine URL and key | One `--url` (default `http://127.0.0.1:8000`) serves every engine call: unloads, loads, restore and both loops' chats. It must be `127.0.0.1` with a real port (toolbench pins the Harbor loop's endpoint there, so any other address would be a second one; the guard itself accepts only the literal addresses `127.0.0.1` and `::1`, never a name). It is checked before any header is built, and proxy settings are ignored. The key is read from oMLX's settings and passed to each client directly, never put in the environment, printed or written. |
| Stops between trials | The STOP file or `--max-minutes`: outcome `stopped`. Free memory under 15% (`memory_pressure -Q`) or unreadable twice running, more than 2% engine faults or raised trials after 50 model trials, Harbor appearing, or 5 trials raising errors: outcome `failed`. A run in which every model trial was an engine fault or raised is `failed` at any trial count. |
| Resume | Trials already in the store (model, suite, split, case, trial) are not run again. Engine-fault trials are recorded as `excluded` and count as done. A record line cut off mid-write is skipped and counted, and its trial runs again. |

**Harbor's ports.** The Harbor-loop column needs none of them (not 8765 or
8791). It imports Harbor's code (`service/`) into this process, through
Toolbench's runner. Its only traffic is to oMLX at `--url`. Harbor must still
be closed: it shares the engine, and the one-model rule would unload its
models.

## What a model's tool call can touch

Nothing in this tier runs model-written code or commands on the Mac. Every
tool is simulated:
- the assistant lane uses Toolbench's fakes over an in-memory world (`make_fakes` in `scripts/toolbench/world.py`; that file makes no file, socket or subprocess call);
- the neutral lane uses an in-memory state store.

A tool call changes only that world. The world is checked and thrown away
after the trial. `install_fakes` swaps Harbor's real tool functions for the
fakes and raises if any real one survives.

Before any case loads, the process runs Toolbench's `isolate()`. It does three
things.
- Harbor's home becomes a throwaway temp directory.
- **Network:** `socket.connect` and `connect_ex` are refused for IP addresses other than loopback at the engine port. Unix-domain sockets and connectionless `sendto` are not guarded; nothing in the tier uses them.
- **Subprocesses:** `subprocess.Popen` and asyncio's subprocess calls are refused except for a short list (`id`, `sysctl`, `sw_vers`, `uname`, `memory_pressure`, `git`, `vm_stat`, `ps`, `lsof`, `pgrep`). A shell is never allowed. `os.system` and the `os.exec*`/`os.spawn*` family are not guarded; nothing in the tier calls them.

These guards hold back the harness's own code paths. They are not a sandbox
for untrusted code, which this tier never runs.

What the host sees:
- requests to oMLX;
- writes to the throwaway Harbor home, to OUT and to the lock and its save file;
- reads of the case files, the model folders (weights hash) and oMLX's settings.

## After the run

- The run restores the engine itself. Check that the printed line says `engine restored`, and that the report's `outcome` is the one expected. If the save file is still there, the restore did not finish: `python -m edat.runtime.run_guard show` prints it.
- Nothing is published. Records stay in OUT until the results chat imports them.
