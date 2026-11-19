# Run sheet: the routine agentic run on the two references

The first real run of the agentic tier (spec 02). It runs on this Mac only with
the go-ahead, after the in-assistant runs have left the engine. Tool:
`edat/cases/agentic_run.py` (`python -m edat.cases.agentic_run`).

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
2. Harbor and Wisp are both closed: they share oMLX, and the run refuses to start while either is open.
3. Work in the shared clone, on a commit that contains this sheet. Use the repository's virtualenv.
4. Run the commands outside Claude's sandbox. The lock directory and oMLX's loopback port are outside it.

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
   - the lock is free;
   - Harbor and Wisp are closed;
   - free memory is at least 30% and disk has at least 5 GB;
   - both model folders exist, with a context window of at least 16k.

   The second form also lists the engine's models and what is loaded (GET only), and hashes both references' weights (read only, cached in `OUT/weights.json`).
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
6. **Stop early:** `touch $OUT/routine/STOP` (or `$OUT/smoke/STOP`, `$OUT/smoke-long/STOP`). The run stops before its next trial, restores the engine and releases the lock. Remove the file before resuming.
7. **Progress at any time** (no engine): `plan --out $OUT --plan routine --measured $OUT` shows the hours left at each size, with finished trials subtracted.

## What is written where

All of it goes under `OUT` (private; held-out records refuse to be written anywhere else):

| Path | What |
|---|---|
| `OUT/<plan>/store/runs/<run id>/` | `edat.result/1.0` records (`records.jsonl`), raw trajectories (`raw/<sha256>.json`), the store's `manifest.json`. One run per tier and suite. |
| `OUT/<plan>/selection.json` | The selection manifest: sorted case ids, `manifest_sha256`, items per group. It names held-out cases, so it is private. |
| `OUT/<plan>/timing.jsonl` | Seconds, user turns and family per trial: the measured speed. |
| `OUT/<plan>/errors.jsonl` | Trials that raised an error. They are not recorded, so a resume runs them again. |
| `OUT/<plan>/run-report.json` | Start, finish, counts, why it stopped, the engine restore result. |
| `OUT/resident-before.json` | The engine's loaded models before the first unload, restored at the end. |
| `OUT/weights.json` | The weights hash cache. |

Every record carries:
- `model` {id, role, weights_sha256, headline_entry};
- `runtime` (`omlx`, or `harbor-omlx` with the harness commit);
- `device`;
- `settings` (thinking, the sampling sent, `sampling_source`, seed, world seed);
- `selection`: {tier `core` (or `smoke`, `smoke-long`), manifest_sha256, n_items, ci95_half_width_pts} per split/lane/family group.

The store never pools records of different selections or manifests.

## Safety

| Rule | How |
|---|---|
| Shared heavy-job lock | `mkdir /private/tmp/claude-501/wisp-heavy.lock`. If it exists, nothing starts. It is removed only by the run that made it, also after a failure. |
| One model at a time | Before each model, `scripts/productbench/engine_prep.py keep` unloads every other chat model; it stops if any stays loaded. |
| Engine restored | The loaded set is saved once (`resident-before.json`) and restored in a `finally`. This covers a normal finish, a stop, an error, Ctrl-C and SIGTERM. The restore result is printed and written to `run-report.json` before any failure is reported. |
| Harbor closed | The run refuses to start while Harbor or Wisp is open. It stops if either opens during the run (checked every 10 trials). |
| Stops between trials | The STOP file; `--max-minutes`; free memory under 15% (`memory_pressure -Q`); more than 2% engine faults after 50 model trials; 5 trials raising errors. |
| Resume | Trials already in the store (model, suite, split, case, trial) are not run again. Engine-fault trials are recorded as `excluded` and also count as done. |
| Results private | OUT must be outside the repository; a held-out run must be under `$EDAT_PRIVATE_DIR`. |

**Harbor's ports.** The Harbor-loop column needs none of them (not 8765 or
8791). It imports Harbor's code (`service/`) into this process, through
Toolbench's runner. Its only traffic is to oMLX on 127.0.0.1 at the engine
port. Harbor must still be closed: it shares the engine, and the one-model
rule would unload its models.

## What a model's tool call can touch

Nothing in this tier runs model-written code or commands on the Mac. Every
tool is simulated:
- the assistant lane uses Toolbench's fakes over an in-memory world (`scripts/toolbench/world.py`, which opens no file, socket or process);
- the neutral lane uses an in-memory state store.

A tool call changes only that world. The world is checked and thrown away
after the trial.

Before any case loads, the process runs Toolbench's `isolate()`:
- Harbor's home becomes a throwaway temp directory;
- IP sockets are refused except loopback to the engine port (Unix-domain sockets are not guarded; no fake opens one);
- subprocesses are refused except read-only probes (`id`, `sysctl`, `sw_vers`, `uname`, `memory_pressure`, `git`, `vm_stat`, `ps`, `lsof`, `pgrep`), and a shell is never allowed.

Harbor's real tool functions are swapped for the fakes, and the swap refuses
to go on if a real one survives.

What the host sees:
- requests to oMLX;
- writes to the throwaway Harbor home and to OUT;
- reads of the case files, the model folders (weights hash) and oMLX's settings.

oMLX's key is read from its settings and sent only to the loopback address. It
is never printed or written.

## After the run

- The run restores the engine itself. Check that the printed line says `engine restored`, and that `engine_prep.py status` shows the models from before.
- Nothing is published. Records stay in OUT until the results chat imports them.
