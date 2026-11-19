# EDAT vision and audio tests (`edat/media/`)

Design: [05-vision-audio-spec](../../docs/benchmark/05-vision-audio-spec.md). Version 1 scope
only: English, no video, no speech output, synthetic speech first, redistributable data only,
no judge model. Nothing here runs a model, a speech engine or a download by itself.

| Piece | Module | Spec |
| --- | --- | --- |
| Synthetic image families (7) and their ground truth | `vision/families/*.py`, `vision/canvas.py`, `vision/items.py`, `vision/generate.py` | §7.1 V1, §7.4 V5 |
| Extraction scorers: normalised exact match, ANLS, relaxed (5 %), reading CER, abstention | `vision/score.py`, `common.py` | §7.1 |
| Validator: every planted field visibly present, gold scores 1, blank scores 0 | `vision/validate.py` | §7.1 |
| Image and audio cost workloads for the performance runner | `workloads.py` | §8 V4, §4 A2, §5 A9 |
| Transcript normaliser, WER, CER, insertion rate, bootstrap interval, A5 detectors | `audio/normalize.py`, `audio/wer.py`, `textdist.py` | §3 A1, §5 A5 |
| Speech-engine seam, stub engine, candidate engines | `audio/tts.py` | §5 A3, §13.2 |
| Noise ladder, simulated rooms, overlap | `audio/mix.py` | §5 A4 |
| Spoken commands: 300 entity-dense cases, two-pass / native / pipeline, matcher seam, entity accuracy | `audio/commands.py` | §5 A3 |
| Latency definitions | `audio/latency.py` | §4.1 A2 |
| Runner writing `edat.result/1.0` records, 3 trials, "no image/audio input" | `runner.py`, `adapter.py` | §2, §10.5 |
| Checkpoint header inspection (tower sizes, T / T+V / T+V+A, strip plan) | `checkpoint.py` | §9.4 M1 |
| Dataset manifests and fetch script | `datasets.py`, `test_fixtures/media/datasets/` | §3.3, §5, §7.2 |

## Vision items

```bash
.venv/bin/python -m edat.media.vision.generate families
```

```bash
.venv/bin/python -m edat.media.vision.generate generate --family documents --count 250 --out /tmp/edat-v1
```

Each item is `<id>.png` plus `<id>.json`: the source data, every planted field with its pixel
box and a hash of its pixels taken right after drawing, the text in reading order (the
full-reading reference), and the questions with gold answers. Every item the generator writes
passes the validator first.

| Family | Templates | Questions per image |
| --- | --- | --- |
| `app_screens` | chat, inbox, settings, error dialog, notifications, maps card, contact | 4 + reading |
| `web_pages` | product, flight, hotel, article, search results, sign-in error, order | 4 + reading |
| `documents` | receipt (10 layouts), invoice | 5 + reading |
| `charts_tables` | table, bar, line, pie, stacked bar, scatter | 4 + reading |
| `calendar` | day, week, month | 4 + reading |
| `spreadsheet` | expenses, inventory, roster, orders, grades, timesheet, sales | 4 + reading |
| `photo_text` | flyer, poster, sign, menu, on a generated photo with perspective and damage levels 0–3 | 5 + reading |

Splits and counts:

| Split | Seed | Images | Questions | Where |
| --- | --- | --- | --- | --- |
| dev sample | public (`DEV_SEED`) | 21 (3 per family) | 111 | committed, `test_fixtures/media/vision/dev/` |
| dev full | public | 250 per family, 1,750 | 9,250 (1,750 of them full readings) | generated on demand |
| held-out | private (`$EDAT_PRIVATE_DIR/media/heldout_seed`) | 250 per family, 1,750 | 9,250 | `$EDAT_PRIVATE_DIR` only; the generator refuses to write it in the repository |

Within each template, theme (light, dark) and device pixel ratio (1, 2) rotate, and 2 items
in 20 are abstention items (a field absent or blurred; the right answer is "not shown").
`--long-side` re-renders at the V5 sizes (512 to 3,000 px) instead of resizing.

Font: Pillow's embedded Aileron Regular (CC0), so no font is downloaded and the same Pillow and
FreeType versions give the same pixels (both are recorded with every item). It covers ASCII
only, and the validator refuses any other character. Every name, brand, address, phone number
(555-01xx) and email (`example.com`, `example.org`, `mail.test`) is invented.

## Audio

Speech engines: only the stub exists. It writes **placeholder tones, not speech**, so the whole
pipeline can be tested; its clips are marked `placeholder: true`. A real engine needs the
coordinator's approval of the engine and each voice (`audio/tts.py`, `CANDIDATES`). No
rendered speech is committed.

## Model adapter

The runner calls `client.chat(...)` exactly as `docs/benchmark/model-adapter.md` defines and
duck-types the result, so it runs today against `StubMediaClient` and later against
`edat.models` clients. What the adapter must add for media is in `adapter.ADAPTER_GAPS`.

## Tests

```bash
.venv/bin/python -m pytest -q tests/test_edat_media_audio_wer.py tests/test_edat_media_audio_pipeline.py tests/test_edat_media_checkpoint.py tests/test_edat_media_datasets.py tests/test_edat_media_runner.py tests/test_edat_media_vision.py
```

They need Pillow and NumPy in the environment (not yet in `requirements-dev.txt`); without them
the image and audio tests skip.
