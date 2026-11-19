# tinyagent-bench for iPhone

The iPhone runner of tinyagent-bench (formerly EDAT, Edge Device Agentic Testing). It runs the benchmark's on-device
measurements and exports raw samples in the Mac runs' format: one `edat.perf/1.0` record per run
(spec 04 §2–§3; no cells or verdicts, the Mac aggregator computes those), plus
`edat-phone-result/v1` lines for the phone parity items (spec 02 §6). Design, numbers to expect
and the device-run plan: the benchmark notes, `10-iphone-runner-design.md` (outside this repository).

It is separate from Harbor's iPhone app (`ios/`) and changes nothing there. It reuses the same
MLX Swift pins (mlx-swift-lm 3.32.3, mlx-swift 0.32.3, swift-transformers 1.3.5). Every schema id
and runner constant is in the `EDAT` enum in `Sources/EdgeBenchCore/PerfRecord.swift`; the ids keep
their `edat` prefix until the benchmark repository renames them.

## Layout

| Path | What |
| --- | --- |
| `Sources/EdgeBenchCore/` | Measurement core, no MLX: record types, runner, run store and kill recovery, schedule and plans, prompt packs, phone items, export, probes, stub generator |
| `Tests/EdgeBenchCoreTests/` | Unit tests against the stub generator on a manual clock |
| `Packages/EdgeBenchMLX/` | The MLX engine (text-only Gemma 4 load, token loop, `gemma4` tool parser) |
| `App/` | SwiftUI app: Models, Run, Results |
| `Resources/` | Stand-in corpus and phone items, the stand-in's source cases, known model hashes (Gemma-Wisp and base Gemma 4 E4B) |
| `tools/` | Stand-in phone-item exporter, source-case exporter, corpus generator (see `tools/README.md`) |
| `scripts/` | Xcode project generator, host-lock wrapper |

## Build and test

Swift builds run through a small lock wrapper, so only one Swift or Xcode build runs at a time (set `BENCH_BUILD_LOCK` to share a lock with other projects):

```bash
ios-bench/scripts/with-host-lock.sh swift test --package-path ios-bench --jobs 2
```

```bash
python3 ios-bench/scripts/generate_project.py
```

```bash
ios-bench/scripts/with-host-lock.sh xcodebuild -project ios-bench/EdgeBench.xcodeproj -scheme EdgeBench -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' -jobs 2 CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM= build
```

```bash
ios-bench/scripts/with-host-lock.sh xcodebuild -project ios-bench/EdgeBench.xcodeproj -scheme EdgeBench -sdk iphoneos -destination 'generic/platform=iOS' -jobs 2 CODE_SIGNING_ALLOWED=NO build
```

The Simulator has no MLX GPU support, so there the app runs the stub generator (simulated
numbers, labelled `runtime.name: "stub"`, synthetic prompt packs) and disables the balloon test.
On a phone it uses MLX.

## Automation (Simulator)

Launch arguments, for scripted Simulator checks with the stub:

- `-EDATAutoRun <plan id>`: start that built-in plan (for example `smoke`) with the default models;
- `-EDATAutoResume 1`: resume the newest unfinished run (recovering a leftover `inflight.json` first);
- `-EDATAutoKillAtCall <n>`: make the stub exit the process at generate call n (0-based), to test kill recovery.

When a run ends the app exports it and prints `EDAT_EXPORT <zip path>` and `EDAT_STATE <state>`.

## Signing for your iPhone

Create `ios-bench/Config/Local.xcconfig` (git-ignored):

```
DEVELOPMENT_TEAM = ABCDE12345
EDGEBENCH_BUNDLE_ID = com.yourname.edat
```

A free Personal Team works (7-day re-sign, three apps per device). The app asks for the
Increased Memory Limit entitlement, which the baseline model needs. If Xcode refuses it for
your team, add `EDGEBENCH_ENTITLEMENTS = App/EdgeBench-no-entitlements.entitlements`; the app
then installs with the default (~3 GB) limit and records that.

## Models, packs and items

Over USB: Finder → the iPhone → Files → drag onto **tinyagent-bench**. Model folders go to `Models`, prompt
packs (`edat.prompt-pack/0.1` JSON) to `Packs`, phone items (`edat-phone-item/v1` JSONL plus its
`.manifest.json`) to `PhoneItems`. The Models screen lists each folder with its role (baseline
Gemma-Wisp, reference base Gemma 4 E4B, or a `-iphone` fallback variant) and can verify every
file's SHA-256 against `Resources/known-models.json`.

## Export

Results → run → Export writes `Exports/tinyagent-bench-<run_id>/` (`run-<id>.json`, `mem-<id>.jsonl`,
`phone-<id>.jsonl`, `manifest-<id>.json` with each file's sha256) and `tinyagent-bench-<run_id>.zip`, which the
share sheet offers.
