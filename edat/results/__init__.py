"""EDAT results store: one record family for every test tier, its statistics and the website export.

See docs/benchmark/results-record.md. Modules:
- schema: validation of records, manifests and export files;
- keys: entry and comparison keys, and the refusals;
- writer: atomic, validated JSONL appends into run directories;
- reader: reading, integrity checks and merging;
- stats: Wilson, case and paired bootstrap, pass^k, rank ranges;
- aggregate: per-entry scores, reference differences, the 16 GB fit gate;
- convert: Toolbench, edat.perf/1.0 and in-assistant output into records;
- export: published snapshots and website files; snapshot diff;
- sample: the synthetic sample snapshot;
- cli: `python -m edat.results`.
"""
SCHEMA = "edat.result/1.0"
