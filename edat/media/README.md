# tinyagent-bench vision and audio tests (`edat/media/`)

Design: [05-vision-audio-spec](../../docs/benchmark/05-vision-audio-spec.md), full-coverage
revision. Scope: English only, no video, no speech output, redistributable data only, and no
judge model. Nothing here runs a model, a speech engine or a download on its own. Every
generator scales by parameter, so the full sets are produced by command. A small development
sample is committed. Held-out splits are generated only into `$EDAT_PRIVATE_DIR`, and every
generator refuses to write them inside the repository.

## Vision

| Suite | What | Module | Full count |
| --- | --- | --- | --- |
| V1 extraction | 11 synthetic families from invented data, with ground truth and pixel proof | `vision/families/*.py`, `canvas.py`, `items.py`, `generate.py`, `validate.py`, `score.py` | 5,500 images, 22,000 questions + 5,500 full readings |
| V2 public sets | manifests only; loaders not built | `datasets.py` | about 15,000 |
| V3 image to action | flyers, tickets, invitations, bookings, appointments, due notices, chats; scored with Harbor's own `parse_proposal` / `proposal_call` | `vision/actions.py` | 1,000 (15 % no event, 15 % several) |
| V4 cost and limits | image tokens, TTFT, decode, preprocess; 1–16 images-per-request ladder; resolution and token-budget accuracy ladders | `workloads.py` | about 3,000 requests |
| V5 robustness | 25 conditions over 600 base images; 500 unreadable images | `vision/robustness.py` | 30,000 + 500 requests |
| V7 reasoning | counting, spatial, multi-image (2–4 per request), chart trend, control location (point or box) | `vision/reasoning.py` | 5,500 requests (+ about 1,270 ScreenSpot-v2, fetched at run time) |

V1 families (500 images each, 4 scored questions and 1 full reading per image, 10 %
abstention items):

| Family | Templates |
| --- | --- |
| `app_screens` | chat, inbox, settings, error dialog, notifications, maps card, contact, music, files, alert, alarms |
| `web_pages` | product, flight, hotel, train, article, search results, sign-in error, order, checkout, booking, pricing |
| `documents` | letters, reports, policies, multi-column pages, slides, dense pages |
| `receipts_forms` | 12 receipt layouts, invoices, tax-style forms, application forms, ID-like cards with clearly fake data |
| `tables` | plain, zebra, grouped headers, totals, wide, status, long (3–40 rows) |
| `charts` | bar, horizontal bar, grouped, stacked, line, area, pie, scatter, histogram, box plot, dual axis |
| `handwriting` | notes, shopping lists, to-do lists, sticky notes (open handwriting fonts through `EDAT_HANDWRITING_FONT_DIR`; a proxy until they are fetched) |
| `photo_text` | flyer, poster, sign, menu, product label, whiteboard, parcel, screen, ticket; perspective, lighting and damage levels 0–3 |
| `calendar` | day, week, month, agenda, timeline, clash views |
| `spreadsheet` | grids with column letters, frozen headers, highlighted cells, toolbar |
| `diagrams` | flowcharts, org charts, timelines, floor plans, network and sequence diagrams, labelled maps |

```bash
.venv/bin/python -m edat.media.vision.generate generate --split dev --count 500 --out OUT/v1
```

```bash
.venv/bin/python -m edat.media.vision.robustness generate --split dev --out OUT/v5
```

```bash
.venv/bin/python -m edat.media.vision.reasoning generate --split dev --out OUT/v7
```

```bash
.venv/bin/python -m edat.media.vision.actions generate --split dev --out OUT/v3
```

For a held-out split, run `python -m edat.media.vision.generate init-heldout-seed` once with
`EDAT_PRIVATE_DIR` set, then pass `--split heldout` and an output folder outside the
repository.

Each V1 item is `<id>.png` plus `<id>.json`. The JSON holds:
- the source data;
- every planted field with its pixel box and a hash of its pixels taken right after drawing;
- named element boxes for control location;
- the text in reading order (the full-reading reference);
- the questions with their gold answers.

The validator proves that every asked field is inside the image, was not drawn over, has ink
at 3:1 contrast, uses glyphs the font has and reads as its planted value. It also checks that
the gold answer scores 1 and a blank answer scores 0. The generators write only items that
pass.

Scoring has no judge:
- normalised exact match (NFKC, case-fold, ISO dates, 24-hour times, money, phone digits);
- ANLS at 0.5;
- within 5 % for values read off a plot;
- CER and line recall for full readings;
- false-answer rate for abstention;
- exact counts with error by count size;
- point inside the element's box, or IoU ≥ 0.5 when a box is asked for.

Font: Pillow's embedded Aileron Regular (CC0), ASCII only. Noto and the handwriting fonts are
in the font manifests and plug in through `EDAT_FONT_DIR` and `EDAT_HANDWRITING_FONT_DIR`.
Every name, brand, address, phone number (555-01xx) and email (`example.com`, `example.org`,
`mail.test`) is invented.

## Audio

| Suite | What | Module | Full count |
| --- | --- | --- | --- |
| A1 accuracy | normaliser (`edat-en-norm/1`, Whisper's rules), WER/CER at corpus level with a bootstrap interval | `audio/normalize.py`, `audio/wer.py`, `textdist.py` | the public sets in the manifests |
| A2 latency | TTFT-audio, time to final, RTF, user-perceived latency, reference endpointer | `audio/latency.py`, `workloads.py` | ladders |
| A3 spoken commands | single (agentic, Toolbench, 500 entity-dense), voice variance, follow-up dialogues, corrections, disfluency, noisy; two-pass, native and pipeline | `audio/commands.py`, `audio/spoken_cases.py`, `audio/dialogue.py` | 10,866 clips |
| A4 robustness | noise types and SNRs, three image-source rooms, level, clipping, truncation, narrowband, overlap | `audio/mix.py` | 2,620 utterances × 19 conditions |
| A5 non-speech | silence and non-speech, speech then noise, commands in noise, answer-leak | `audio/nonspeech.py` | 3,000 clips |
| A7 understanding | conversations (who said, speaker count, DER), urgency, sound events, recordings with planted facts, CREMA-D, ESC-10, Big Bench Audio | `audio/understanding.py` | about 15,000 questions |
| A8 long audio | length ladder, 30 s windows with 1 s overlap, seam errors, behaviour past the limit | `audio/longform.py` | 200 ladder clips |
| entities | names, numbers, dates, money and term lists for entity accuracy | `audio/entities.py` | — |

Speech engines (`audio/tts.py`):
- **Kokoro-82M** (Apache-2.0) is the main engine.
- **Piper `en_GB-cori-medium`** (CC BY 4.0) is the second.
- **The 12-voice plan** is in `test_fixtures/media/tts/voices.json`.

Both real engines load only from a local folder (`EDAT_KOKORO_DIR`, `EDAT_PIPER_DIR`) with the
hub forced offline, and refuse rather than download. The stub engine writes **placeholder
tones, not speech**, so the whole pipeline can be tested; its clips are marked
`placeholder: true`. No rendered speech is committed.

## Shared

- `runner.py`:
  - writes `edat.result/1.0` records (`tier` `vision` or `audio`, suite in `facets.family`);
  - runs 3 trials;
  - records `not_applicable` ("no image input" / "no audio input");
  - retries once, then marks the record `excluded`;
  - sends 1–4 images per V7 request, V5 items in their own format, and V3 as Harbor's own request.
- `adapter.py`: the media capabilities and the gaps the shared model adapter still has
  (`ADAPTER_GAPS`).
- `checkpoint.py`: safetensors header inspection (tower sizes, T / T+V / T+V+A, strip plan).
- `datasets.py`: 44 manifests in `test_fixtures/media/datasets/` with every file's size,
  checksum and licence. It provides:
  - `python -m edat.media.datasets summary`, which totals what version 1 needs;
  - `fetch`, which refuses anything not approved, any destination inside the repository and any
    call without `--yes`.

## Tests

```bash
.venv/bin/python -m pytest -q tests/test_edat_media_*.py
```

They need Pillow, NumPy and PyYAML in the environment. Pillow and NumPy are not yet in
`requirements-dev.txt`; without them the image and audio tests skip.
