# ios-bench tools

## Phone parity items (stand-in)

The app reads `edat-phone-item/v1` JSONL (spec 02 §6.3) and writes `edat-phone-result/v1` lines;
see `Sources/EdgeBenchCore/PhoneItems.swift`. The published 120-item parity subset comes from the
agentic harness's exporter on the Mac (02 §6, B11). Until it exists, the app bundles a stand-in:

```
python3 ios-bench/tools/export_phone_items.py --today 2026-11-18
python3 -m pytest ios-bench/tools/test_export_phone_items.py -q
```

This writes `Resources/phone-items-standin.jsonl` and `.manifest.json` from `Resources/portable-cases.json`:
35 items, `thinking: true`, author-recommended sampling with seed `1000 + index`, `rendered_prompt: null`.
The manifest says it is NOT the published subset.

- `check.kind` is `calls`, `no_call`, `text` or `either`. `either` takes `options`, a list of full checks,
  and passes if any option passes. The item's top-level `text` block applies on top of every kind.
- `json` cases become `no_call` with the weak text regex `\{`; `max_words` is dropped.
- The exporter refuses regexes with lookbehind or possessive quantifiers (not portable to ICU).

## Source cases

`Resources/portable-cases.json` (`edgebench.portable-cases/0.1`) is the stand-in's source only; the app no
longer scores it. Regenerate it with

```
python ios-bench/tools/export_portable_cases.py --exported-at 2026-11-18T00:00:00Z
```

It needs PyYAML and Harbor's `service.*` imports (repo venv), points `HOME` at an empty temp directory
first so nothing reads user data, and takes tool schemas from `service.tools.registry.tool_schemas` with
`HARBOR_TERSE_TOOL_SCHEMAS=1`. Authored text uses the persona "Test User" at `test.user@example.com` and
`.example` / `.invalid` domains only.

The earlier Mac-side runner (`run_portable_cases_mac.py`) is removed: the Mac re-grades phone results with
the agentic harness, and runs no model from this folder.

## Corpus

`make_corpus.py` writes `Resources/corpus-agent-v0.txt`, the stand-in prose for on-device prompts when no
prompt pack is present (recorded as `corpus: "provisional-ios-agent-v0"`, not comparable with the Mac).
