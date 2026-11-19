# Published tinyagent-bench results

This folder holds **published snapshots** of the tinyagent-bench board, one folder per board
version and device. It holds **no real results yet**. The only snapshot here,
`sample-0/`, is synthetic sample data: every model name, hash and number in it is
invented (see [`edat/results/sample.py`](../../edat/results/sample.py)).

The record format and the rules are in
[docs/benchmark/results-record.md](../../docs/benchmark/results-record.md).

## Layout

```
data/results/
  <board_version>/
    <device_id>/            e.g. mac-ref, iphone-ref
      snapshot.json         edat.snapshot/1.0: the runs it was built from, with checksums, and every aggregate
      records.jsonl         edat.result/1.0 records of the dev and public_dataset splits
    cloud/                  cloud references (never ranked), same two files
```

- **Held-out records never appear here.** A snapshot publishes the held-out split's
  aggregates (`records_withheld_heldout` counts them); its case ids, records and
  trajectories stay in the private store.
- **Raw outputs are not copied here.** The website gets short trajectories through
  `site/data/<board_version>/<device_id>.trajectories.json`.
- The website reads `site/data/`, not this folder.
- `sample-0/` keeps only `snapshot.json`; its `records.jsonl` is left out to keep the
  repository small. Regenerate everything with the command below.

## Where runs live

Runs are not kept in the repository. The writer puts them under
`$EDAT_RESULTS_DIR/runs/<run_id>/` (default `~/.edat/results`); held-out runs go to
the private store under `$EDAT_PRIVATE_DIR`.

## Commands

Check runs, then publish a board version:

```bash
python -m edat.results validate ~/.edat/results/runs/<run_id>
```

```bash
python -m edat.results export ~/.edat/results/runs/<run_a> ~/.edat/results/runs/<run_b> --data-dir data/results --site-dir site/data
```

What changed between two snapshots of one device (refuses different board versions
unless `--allow-board-change`, which lists changes without comparing scores):

```bash
python -m edat.results diff data/results/<board_a>/mac-ref data/results/<board_b>/mac-ref
```

Regenerate the synthetic sample (deterministic):

```bash
python -m edat.results sample --data-dir data/results --site-dir site/data --no-records
```
