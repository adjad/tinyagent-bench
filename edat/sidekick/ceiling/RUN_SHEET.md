# Run sheet: the coding ceiling of the small local models (nothing here has been run)

**Not run yet.** The question: where does each small model's coding ability stop? Each model entry (a
model in one thinking mode) climbs the ladder in `rungs.py`, easiest rung first, and the report says, in
plain words, what it can reliably do, where it starts failing and how. Local models only, on this Mac
through oMLX. No cloud model (each needs its own go-ahead), nothing published, the report stays private.
It starts only when the person running the benchmark says so.

## The ladder

| Rung | What it asks | Cases | Mode |
|---|---|---|---|
| 1 | Simple one-step tasks (sidekick core, level L1) | 44 | answer only |
| 2 | Everyday helper tasks (sidekick core, L2) | 43 | answer only |
| 3 | The harder helper tasks (sidekick core, L3) | 13 | answer only |
| 4 | A function with several rules that interact | 12 | answer only |
| 5 | A small class that keeps track of state | 12 | answer only |
| 6 | A short script that reads a data file and reports on it | 12 | answer only |
| 7 | Find and fix two bugs in a 60 to 100 line file | 12 | answer only |
| 8 | A small change that has to be made in two files | 12 | answer only |
| 9 | Read the code, edit it, run the tests, fix what fails (tools, 10 calls) | 12 | tool mode |

Rungs 1-3 are the sidekick tier's core cases without its Restraint set; the Restraint set (12 core
cases) runs in the full plan and is reported as safety, not as a rung. Rungs 4-9 were written for this
ladder (no well-known exercises), are checked by hidden tests in the sealed sandbox, and are reported
apart from the Sidekick Score. Every case passes its validator: the reference passes, the near-miss
wrong answers, an empty answer and "OK." fail, and the given starting code fails.

**Ceiling, precisely.** A case holds when it passes on a majority of its trials (2 of 3, or 1 of 1). A
rung holds when at least half of its cases hold. The ceiling is the highest rung that holds; "solid up
to" is the highest rung with every rung below it holding too. The report gives every rung's pass rate,
so the fall-off is visible.

## The models: read from the model folder when the run starts

The roster is not fixed. `preflight` and `run-all` read oMLX's model folder (from oMLX's own settings,
read only) and keep every **complete chat model** of at most 11 GB of weights:

- a `config.json`, a tokenizer, a chat template, and every weight shard the index names, with no
  partial download files left in the folder;
- not a helper: drafters (DFlash, MTP donors), embedders and rerankers are left out, and so are adapters
  (a folder with `adapter_config.json`) and folders with no template;
- local only, no cloud model.

Every folder left out is listed with its reason (in the preflight, in `$OUT/roster.json` and on the
report's "Not run" list). Each model's **quantisation method and measured bits per weight** (read from
the weight files' headers) go into every record. Where one model is present at several quantisations,
the one nearest 4 bits per weight is the standard entry and the others run after everything else.

**Entries and their order.** An entry is one model, in one thinking mode, with one sampling setting.
Entries run in tiers; within a tier the two references go first, then the rest smallest first:

| tier | what | |
|---|---|---|
| 0 | the standard entry of each model: its standard quantisation, the thinking mode it ships in, the author's recommended sampling | the first pass |
| 1 | the other thinking mode, where the template has one | |
| 2 | the second sampling setting: Qwen3.5 and Ornith "precise coding with thinking" (temperature 0.6, presence penalty 0); for models with no author recommendation (Phi-4-mini, SenseNova-Loop), the fixed sensitivity setting (temperature 0.6, top-p 0.95) beside greedy | |
| 3 | the other quantisations of a model | |

**Sampling** is sent by the runner with every request (`models.SAMPLING`, from the model authors'
cards), never left to oMLX's per-model settings, which are read and never changed. Every record carries
the values sent and their source (`author_recommended` or `fixed_sensitivity`); `progress.json` has
the basis in words.

**Entries the engine cannot serve are skipped, not fatal.** Before a model, the run checks that the
engine lists it; a model whose first three trials all fail on engine faults (a model needing remote
code, say) is recorded as skipped and the run goes on. A thinking-on entry whose first three answers
carry no reasoning is recorded as "thinking forced off in this build" and its thinking-off entry runs
in its place. Only host-level reasons stop the whole run (section 5).

**What the folder holds today** (the scan on the day this sheet was written; the run re-reads it): 17
chat models, 14 of them standard entries, the two Gemma references among them: Gemma 4 E2B (4-bit
standard, 6-bit after), E4B (base reference), Gemma-Wisp r5 (tuned reference), Gemma 4 12B, Nemotron
3.5 Bolt E4B (with and without its MTP head, so the MTP build is the standard entry), Qwen3.5 4B and
9B, Phi-4-mini, Ling 3.0 tiny, LFM3 1.7B, LFM3 Omni 2B, Spark X2.6 4B (4-bit standard, 6-bit after),
SenseNova-Loop 9B, and Ornith 1.5 9B. Left out: a reranker, two drafters, two folders with no model
config, and nine empty folders. `plan` prints the list as the run will see it. Notes:

- **SenseNova-Loop 9B** has no loader in the installed oMLX (it needs remote code). It is expected to
  be skipped as not servable; the probe records that.
- **The shipped thinking mode** is the model author's default (for the tuned reference, the mode Harbor
  ships it in: off), from the table in `models.py`, and the runner sends it with every request; it
  never comes from oMLX's settings, so step 0 does not change it. Where the chat template's own default
  differs, the run follows the author and records both (`ships_thinking`, `template_thinking_default` in
  `progress.json` and `roster.json`). Today that is **Nemotron 3.5 Bolt E4B**: its card says thinking is
  on by default; the template it ships with defaults to off.
- **Qwen3.5 9B**: if its build cannot switch thinking on, the probe records it and its thinking-off
  entry runs instead.
- **LFM3 Omni** and the greedy entries are deterministic, so their three trials can come out identical.
- **The base reference** here is a local quantisation; it may not match the published repository byte
  for byte. Each record carries the weights' sha256, so the exact files are known.
- **Ornith 9B** (8-bit, 10.7 GB of weights) is the upper bound. It fits a 24 GB Mac. It would not fit
  a 16 GB Mac as it is: macOS lets the GPU use about two-thirds of memory by default, about 10.7 GB
  there, which leaves no room for the context. A 4-bit build (about 5.5 GB) would fit.

## 0. One approved settings write, before the first model loads (only on the word to do it)

The person running the benchmark has approved one change to oMLX: writing the model authors' sampling
settings into oMLX's per-model settings for eight newly downloaded models. Do it only when told to,
after any earlier real run on this Mac has ended and its state has been put back, and before this run
loads its first model. The approval covers these settings and nothing else: no other model's entry,
not the two references, not multi-token prediction, memory or context-window settings.

| oMLX model id | Fields written |
|---|---|
| `Qwen3.5-4B-oQ4e-mtp` | temperature 1.0, top_p 0.95, top_k 20, min_p 0, presence_penalty 1.5, repetition_penalty 1.0, thinking on |
| `Qwen3.5-9B-oQ4e-mtp` | temperature 0.7, top_p 0.8, top_k 20, min_p 0, presence_penalty 1.5, repetition_penalty 1.0, thinking off (its card says thinking is forced off in this build; if thinking does switch on at its first load, re-run with `--qwen9-thinks` to write the 4B's thinking-on values instead) |
| `gemma-4-E2B-it-oQ4e-mtp`, `gemma-4-E2B-it-oQ6e-mtp`, `gemma-4-12B-it-oQ4e-mtp` | temperature 1.0, top_p 0.95, top_k 64, no penalties, thinking on |
| `Ling-3.0-tiny-oQ4e` | temperature 1.0, top_p 0.95, top_k 20, thinking on |
| `Phi-4-mini-instruct-oQ4e` | temperature 0 (greedy); it has no thinking mode |
| `SenseNova-Loop-9B-MLX-4bit` | temperature 1.0, top_p 0.95; if the engine does not list it, it is left alone and that is recorded |

```bash
.venv/bin/python -m edat.sidekick.ceiling omlx-profile
```

```bash
.venv/bin/python -m edat.sidekick.ceiling omlx-profile --apply
```

The first command is a dry run: it lists each field that would change (current value, new value) and
each model the engine does not list (left alone), and sends nothing. The second:
1. copies `~/.omlx/model_settings.json` to `model_settings.json.bak-sampling-<date>-<time>`;
2. sends each listed model's fields, and only those, through oMLX's own per-model settings call
   (`PUT /admin/api/models/<id>/settings`), which merges them into that model's entry and saves the file;
3. reads the file back and checks that each written field holds its new value and that every other
   model and every other field matches the backup exactly. Any difference is printed and the command
   fails; the backup is the way back.

Why through oMLX and not by editing the file: oMLX holds these settings in memory. It does not see a
changed file until it reloads its models (which also preloads pinned models), and it rewrites the whole
file from memory whenever any setting is saved, so a hand edit can be silently lost.

Then, on each of these models' first load in the run, check that the engine reports the written values
(its model status), and that the model ids match the engine's own list (the dry run already checks the
ids). The runner still sends each entry's sampling itself with every request and records what it sent,
so results do not depend on these settings. That matters for the Qwen3.5 4B: its oMLX entry holds the
thinking-on values, and its thinking-off entry gets its own values per request.

**Context windows are not part of this approval.** The preflight reads each model's window from oMLX's
settings (its own value, else the engine default) and records it in `$OUT/roster.json` with what the
cases can need: about 5,600 tokens with thinking off and 13,800 with it on for answer-only and ladder
cases, and about 14,300 (off) and 17,300 (on) for a tool-mode trial at its 10-call worst case. Every
model has room except the two references: their 16,384-token setting is below the worst case of a
thinking-on tool-mode trial. That only arises in tier 1 of the full plan, never in the first pass. Each
entry's `progress.json` also records the window the engine had.

## 1. Read-only checks before the run

With `OUT=~/tinyagent-bench-results/coding-ceiling` (a private folder outside the repository),
`EDAT_PRIVATE_DIR=<the private folder>` (it holds the held-out sidekick seed) and
`LOCK=/private/tmp/claude-501/wisp-heavy.lock`:

```bash
.venv/bin/python -m edat.sidekick.ceiling preflight --out $OUT --lock $LOCK --engine --weights
```

It only reads. It checks:
- the results folder is outside the repository;
- the held-out seed is there (checked before the sandbox, so each refusal names its own cause);
- the Python and Node that model code runs with are self-contained (below);
- the sealed sandbox self-test passes (the run refuses to start otherwise);
- every model folder has its weights and template;
- free memory is at least 30%, and there are 5 GB of free disk;
- the heavy-job lock is free;
- Harbor is not running;
- the model folder scan: every model with its quantisation, bits per weight, size, thinking modes,
  whether it is the standard entry and whether it would fit a 16 GB Mac, and every folder left out
  with its reason (also written to `$OUT/roster.json`);
- the engine (GET only) lists each engine id; one it does not list is shown as "will be skipped".

**Which Python and Node run the model's code here.** Python: the repository's `.venv` interpreter, a
uv-managed CPython 3.13 with pytest. Node: the official nodejs.org build in `/usr/local/bin` (v24). Both
load only their own files and the system's libraries, which is what the sandbox lets model code read. A
toolchain installed in a shared prefix (`/usr/local`, Homebrew's) gets only its own binary, never the
prefix.
A build that loads libraries from elsewhere (a Homebrew Node linking Homebrew's libuv, say) is refused
with that reason by name; point `EDAT_SIDEKICK_PYTHON` or `EDAT_SIDEKICK_NODE` at a self-contained
build instead. The versions go into every record's run key; the paths stay on this Mac.

**What the sandbox does with a process that outlives its command, and its limits.** After each command
the harness kills the command's process group. A process that left the group (`setsid`) survives that
kill. Then:
- **Detected:** each command's processes inherit a sentinel pipe; one still held after the kill marks a
  `surviving_process`. At the seal, before grading, the harness also lists (with `lsof`) every process of
  this user that has a file or its working folder in the trial folder. Each one found is stopped at once,
  so it can no longer fork or pass a descriptor on, and the listing is repeated until two in a row find
  no new one; then all are killed. Any process found that way is a survivor too, even if it closed every
  descriptor first, and any survivor fails the trial as unsafe.
- **Contained:** the trial folder is then renamed. The sandbox lets model code write only *inside* the
  trial folder, never the folder itself, so it cannot remove it, replace it with a link, or create the
  old path again after the rename. It has no network and cannot write anywhere else.
- **The limits, plainly:** survivors cannot all be found for certain. `lsof` takes one listing at a time,
  and a process that hands its descriptor to a new child and exits between two listings can be missed by
  each of them. If holders keep appearing for twelve listings, the trial is marked unsafe with a note
  that not all may have been found. A process that is never found keeps running, confined, until it
  exits or reaches its CPU-time limit. It can reach none of the trial's files by name, only through
  descriptors it already holds, and nothing it writes through them matters: each grader reads what it
  grades once, after the seal, from a copy or through handles, so a late write can only produce a state
  the model could have written itself before the seal.
- **A fast forker:** a process that forks faster than `lsof` lists can keep a descriptor alive through the
  whole search, so the search may end "settled" while a descendant still writes to a file after the seal.
  No pass comes out of it once any listing has found one of its processes, which already makes the trial
  unsafe (and a process no listing finds is the case above). The full fix is a separate
  user account per trial, so every process it starts can be found and killed by account; that needs
  administrator rights and is left for later.

**What model code can reach on this Mac.** Its own trial folder (read and write), the pinned toolchains
and the system's own folders (read), `/dev/null`, `zero`, `random`, `urandom` and its own descriptors;
never a terminal or pty, `/usr/local`, the network (loopback and local sockets included), another
process's files, or anything about a process outside its own sandbox: not its arguments or environment,
its path, its details or the process list. Model code itself gets a fixed environment.

**What the graders trust.** The hardened graders trust nothing the model's code can write (the git grader
is the exception, and labelled: below). The python3, node and git the graders run
live in a read-only folder the harness makes outside every trial folder. Code answers run in a driver
that only reports what each call returned; the expected values, the reference and the verdict stay on
the harness side. Written tests run once per module (the correct one and each mutant), each in a fresh
folder, in a random order, and each run counts by its exit code alone. A folder snapshot stops at 20,000
entries (files, links and folders all count); a folder past that is not graded, so a file past the cap
can never pass for a deleted one.

**What is hardened, and what is labelled.** Hardened against adversarial model code: the code and regex
graders, the shell cases with an exact or forbidding output check, the commit-message grader and the
Yes/No diff cases without a bug. Labelled `not hardened against adversarial model code` are
the labelled task types and cases, each with its reason (the row's `not_hardened_reason`):
- git (`git_view`): graded by real git reading a repository the model controls;
- write_test (`source_compare`): a test that compares the module against the shown source passes;
- explanations matched by key facts, every explain_error case and the diff "what" cases (`prompt_echo`):
  echoing the prompt can pass;
- the Yes/No diff cases that have a bug (`prompt_echo`): their facts are matched by the same matcher;
- the shell cases whose output check takes any line holding a name and its number, with no forbidden
  output (`task_blind_output`): task-blind output can pass;
- the ceiling ladder's rungs 4 to 9 (`probe_in_process`) until their conversion lands.

A labelled row carries the tag `grade-not-hardened`, `grade_hardened: false` and its reason, set from how
its case is graded before grading starts, so an error in grading cannot strip it. One function
(`labels.may_score`) decides whether a row may be scored, and every scorer uses it: the runner's summary,
the results aggregate (labelled rows go to their own `sidekick_not_hardened` view, never ranked, with no
difference from a reference), the export and the site data (no site figure or trajectory), and the
ceiling report (rungs 1 to 3, the ceiling and "solid up to" are worked out without them). Labelled rows
stay out of the Sidekick Score and every ranked or published number; the summary reports them apart,
under `not_hardened`, with their reasons. Each is to be hardened in a small change of its own, and its
label comes off only after that change has had its own adversarial pass. A sandboxed test program cannot read other
processes or devices (see "What model code can reach").

**How the git grader reads a repository: real git, on a sanitised copy, labelled.** The harness copies
the repository (working tree, `.git`, `origin.git`) through folder handles into a fresh area and leaves
out what could make git run something, read elsewhere or report something other than what is stored
(the table below). Git, the host's own binary with its config replaced and its environment isolated,
then reads that copy, and its view is graded: HEAD and its branch, every ref, the stash list, the
reachable commits with their trees, parents and subjects, the files of each tree, the index, untracked
files with no ignore rule, staged and unstaged changes with attributes taken from the empty tree and no
index flag believed for a file on disk, origin's refs, and the model's config read as data with includes
off (and `config.worktree` where `extensions.worktreeConfig` makes git read it; sparse-checkout sets it).
A config over 1 MiB is not read at all (never in part), so no config predicate can hold. The objects
whose text is graded come through a store that checks each one hashes to its id. This closes the view
mechanisms found so far, but git's view of a repository the model controls is not a verdict the model
cannot shape, so these rows are labelled until the git tasks are rebuilt (below).

**The raw reader is a tripwire.** A second, strict reader parses the same copy from its files and checked
objects: loose and packed refs in git's exact format, commit headers, tree entries, the stash log, the
index and its extensions, and the config by a hand-written parser, refusing anything git could read
another way, with `git fsck --strict` as a backstop. Where it refuses the repository, differs from git on
any fact the grade uses, or the copy left out a pointer elsewhere (`commondir`, `gitdir`, grafts, a
shallow list, replace refs, a `core.worktree` or extension in the model's config), the trial carries a
`git_view_mismatch` flag: a count in the row's metrics and the facts in its raw record, for a person to
read. A flag never fails a trial or changes a grade, so no honest model loses a point to it, and an
answer without a flag is not thereby honest. Nothing the repository holds stops the snapshot: a file too
large is kept by name, the snapshot keeps at most 1 MiB of any file's text and 4 MiB in all (past that
it keeps the file's id), and a read that does not finish is a failed grade, never an error.

The honest states checked with no flag: a link to a folder (one entry, as git lists it), a nested
repository (one untracked `name/`, never walked into), sparse checkouts in cone mode and with a sparse
index, a file edited under skip-worktree or assume-unchanged (both readers see the change), an orphan
branch with an empty root commit, a subject with leading spaces (kept, as git's `%s` keeps them), file
names in decomposed Unicode and case-only renames (read as git does on macOS, with `precomposeunicode`
and the file system's `ignorecase`), a 6 MB file, tags of a tree or a blob, merges, rebases and
cherry-picks stopped on conflicts, notes, `feature.manyFiles`, `pack-refs`, `gc --prune=now`, and a
submodule with changes inside it. One rule is stricter than git: no attribute applies, so a file whose
bytes on disk differ from its blob is changed even where `text=auto` would call it clean (CRLF on disk,
LF stored).

What was checked against git's documentation (gitrepository-layout, git-config, githooks, gitattributes,
gitignore, git-replace, git-sparse-checkout, git-update-index, gitformat-index, git environment
variables), and what each item does here:

| Mechanism | What it could do | Here |
| --- | --- | --- |
| `refs/replace/*`, `GIT_NO_REPLACE_OBJECTS` | swap any object for another | left out of the copy and flagged; replacement off in the environment and on the command line |
| `info/grafts`, `shallow` | rewrite or cut a commit's parents | left out and flagged; `GIT_GRAFT_FILE=/dev/null` |
| `objects/info/alternates`, `http-alternates` | borrow objects from elsewhere | left out |
| pack `.idx`, multi-pack index, commit graph | point an id at other content, or other parents | indexes and graphs left out or off; every object whose text is graded is checked against its id |
| `info/exclude`, `.gitignore`, `core.excludesFile` | hide untracked files | no ignore rule: every file not in the index is untracked |
| assume-unchanged, skip-worktree, intent-to-add bits | hide changes to tracked files | cleared in the copy for every file on disk, so git compares it; a skip-worktree file absent from disk (a sparse checkout) is not a deletion |
| `info/sparse-checkout`, sparse index | hide files from the index | left out and off; the index is read in full |
| links to folders, nested repositories | hide what is inside, or walk into another repository | a link is one entry; a folder with its own `.git` is one untracked `name/` |
| `.gitattributes`, `info/attributes`, filters, `core.attributesFile` | make different bytes look clean | attributes from the empty tree; no filter or line-ending rule |
| untracked cache, fsmonitor (`core.fsmonitor`, `core.untrackedCache`) | report a stale or forged status | off: status is never asked |
| config, `config.worktree`, `include`, `includeIf` | load settings or other files | replaced by a fixed config; the model's config read as data, includes off; over 1 MiB not read at all, and flagged |
| hooks, `core.hooksPath`, reference-transaction | run code | left out; hooks path set to `/dev/null` |
| `gitdir`, `commondir`, `worktrees`, `modules`, a `.git` file | point at another repository | left out (a `.git` that is a file is not copied); `commondir` and `gitdir` flagged |
| `core.worktree`, `core.bare`, `extensions.*` (reftable, `objectFormat`) in the model's config | point git at another work tree, or other refs or objects | the fixed config has none of them; the raw reader refuses them, so the trial is flagged |
| links inside `.git` | make git read outside the copy | never copied, and reported |
| a ref, header, tree or index form git reads one way and a lax reader another | the readers see different repositories | git's reading is graded; the raw reader refuses it, so the trial is flagged |
| submodules (gitlinks) | pull in another repository | never looked into, by either reader |
| anything not yet found | git's view shaped by the model | the label: git tasks stay out of every ranked score |

File metadata (sizes, dates, names by exact path) is readable anywhere on the Mac by design: path lookup
needs it, and contents stay denied.

**Rungs 4 to 9 are not hardened against adversarial model code.** Their hidden tests run in the same
pytest process as the model's code, so a model that attacks the grader on purpose (reads the reference
from the test process, patches pytest, fakes the exit code) could pass one; an honest wrong answer cannot
pass by accident. Their rows carry the tag `grade-not-hardened`, the report marks them, and they stay out
of any published or ranked score until they are rebuilt. A tripwire flags answers on those rungs whose
code touches the test machinery or the process (`tripwire` in the row's metrics, listed in the report)
for a person to read; it never changes a grade, and an answer it does not flag is not thereby honest.
The labelled sidekick tasks on rungs 1 to 3 (above) carry the same label: rungs 1 to 3, the ceiling and
"solid up to" are worked out without them.

**Hardening the git tasks** is a later change, designed first: the host would rebuild what it grades
from the candidate's objects into a fresh host-owned repository through a narrow, validated import,
reading neither the model's repository nor a hand-written parse of it as the source of truth.

`--weights` prints each model's `weights_sha256` for the records (cached in `$OUT/weights.json`;
`run-all` computes any missing one itself); it reads about 75 GB in all. If a host check fails, the run
does not start; a model the engine cannot serve only means that model is skipped.

## 2. What the run changes, and how it is put back

| what | during the run | put back by |
|---|---|---|
| Harbor | stays closed (it is closed now); the run never opens or quits it. `run-all` refuses to start while it is open, and stops before the next unload if it is opened during the run | nothing |
| oMLX | used as it is; never started, stopped or restarted | nothing |
| resident model | `run-all` calls `engine_prep keep <engine id> --save $OUT/resident-before.json` whenever the model changes; it unloads the other chat models (the first call saves what was loaded and never overwrites it); the engine loads the model under test on its first request | at the end: `engine_prep restore $OUT/resident-before.json` |
| oMLX settings | read; changed only by the approved step 0 (eight new models' sampling fields); each request sends the entry's sampling and thinking flag itself | step 0's backup, if ever needed |
| heavy-job lock | each command takes `$LOCK` once (`mkdir`) and removes it only if it created it; it refuses to start if the lock exists | automatic |
| keep awake | `caffeinate -i` around each command | ends with the command |

## 3. Commands, in order

One model resident at a time: `run-all` unloads the other chat models (`engine_prep keep`, saving what
was loaded to `$OUT/resident-before.json` once) whenever the model changes, and finishes all of a
model's entries in a tier before the next model. It takes `$LOCK` once for the whole run.

**Smoke, about 5-10 minutes** (before any of the passes below, the quick look included): base Gemma, thinking off, two cases from every rung, one trial.

```bash
caffeinate -i .venv/bin/python -m edat.sidekick.ceiling run-all --plan smoke --tiers 0 --only gemma-4-E4B-it-oQ4e-mtp --out $OUT --lock $LOCK
```

Check the printed lines: 18 trial lines, each `PASS` or a failure kind with its seconds, no
`excluded`, and `done` at the end. Then redo the estimate from it:

```bash
.venv/bin/python -m edat.sidekick.ceiling plan --measured $OUT
```

**Or a quick look, one sitting:** answer-only rungs, one try each, a few tasks a rung, to see every
model's rough ceiling the same night. The choices are flags; `plan` shows the models, the entries and
the estimate for any combination before anything runs:

```bash
.venv/bin/python -m edat.sidekick.ceiling plan --plan quick --per-rung 4 --rungs 1-8 --thinking off
```

```bash
caffeinate -i .venv/bin/python -m edat.sidekick.ceiling run-all --plan quick --per-rung 4 --rungs 1-8 --thinking off --out $OUT --lock $LOCK
```

- `--per-rung 3` or `4`: tasks a rung (rungs 1-3 spread across task types);
- `--rungs`: which rungs, e.g. `1-8` (the default: answer-only) or `1-3,5-8`; `1-9` adds the tool-mode
  rung 9 (read, edit, run the tests, fix), one try per task;
- `--thinking off` runs every model with thinking off (with the author's thinking-off sampling, or
  greedy where there is none); `--thinking shipped` runs each model in the mode it ships in.

One entry a model (standard quantisations only), references first. Results go to `$OUT/quick/`, apart
from the first pass, and the report is `report $OUT/quick`. Its page says plainly what a quick look
cannot tell, at the top: a quick look with wide error bars, not a ranking. It gives counts ("2 of 4
tasks, one try each") rather than shares, says "passed in this look" rather than "reliably does", and
lists the models alphabetically. No repeat tries, so a ceiling is good to about a rung; thinking off
understates the models that ship with thinking (it names them); no safety set. Without rung 9 it says
there is no tool mode and writes a ceiling at the top rung run as "8 or higher (rung 9 not run)".

**The first pass, one sitting at a time** (the sittings are listed by `plan`; `--max-minutes 720`
stops a sitting cleanly at 12 hours and the next command resumes it):

```bash
caffeinate -i .venv/bin/python -m edat.sidekick.ceiling run-all --plan first-pass --tiers 0 --out $OUT --lock $LOCK --max-minutes 720
```

**Later passes**, once the first-pass report has been read: `--plan full --tiers 0`, then `--tiers 1`,
`2`, `3`. `--only <engine id>` (repeatable) limits a command to some models. The full plan keeps its
results in `$OUT/full/`, apart from the first pass, so neither resumes from the other's trials; a
trial is also reused only for the same ladder split (public or private seed).

**The report** after any command (it covers whatever has been recorded):

```bash
.venv/bin/python -m edat.sidekick.ceiling report $OUT
```

(`report $OUT/quick` for a quick look, `report $OUT/full` for the full plan, `report $OUT/smoke` for the
smoke step.)

**Put things back:** `engine_prep restore $OUT/resident-before.json`. Harbor stays closed unless the
person running it says otherwise.

## 4. Duration, and its basis

Model calls per entry are counted from the plans: one per answer-only trial; about 4 per sidekick
tool-mode trial and 7 per rung-9 trial. Seconds per call are 1.5 plus output tokens divided by decode
speed. Output tokens per call are guesses:

| thinking | answer-only | ladder | tool step |
|---|---|---|---|
| off | 300 | 900 | 180 |
| on | 2,200 | 3,500 | 900 |

Each is capped at the budget: 1,536, 4,096 and 1,024 tokens with thinking off; 8,192, 12,288 and 4,096
with it on. Decode speeds are guesses from the size of the weights (about 200 tokens/s divided by the
gigabytes, between 12 and 150, and 30% more with an MTP head). Then add 1-4 s of sandbox grading per
trial and a minute each time the model changes. `plan --measured` replaces the token counts and scales
every speed by what the smoke step measured. These are guesses until the smoke step has run; thinking-on
entries carry most of the uncertainty.

Today's folder (14 standard entries, 13 in tier 1, 7 in tier 2, 6 in tier 3):

| plan | what it runs | estimate |
|---|---|---|
| **quick look, thinking off** | 14 entries, every model with thinking off; answer-only rungs 1-8, 4 tasks a rung, 1 try | **about 2.5 hours** in one sitting, plus the smoke step (about 2.3 h if SenseNova is skipped); 3 tasks a rung: about 1.9 h |
| **quick look, shipped mode** | the same, each model in the thinking mode it ships in | **about 6.6 hours** (about 5.7 h if SenseNova is skipped); 3 tasks a rung: about 5.0 h |
| **quick look, shipped mode, with rung 9** | the same plus the tool-mode rung 9 (`--rungs 1-9`) | **about 8.4 hours** (+1.8 h; about 7.2 h if SenseNova is skipped); 3 tasks a rung: about 6.3 h (+1.3 h). The weakest estimate here: tool-mode output per call is a guess, and the smoke step corrects only the speed |
| **first pass** | tier 0 (the standard entry of each model, in its shipped thinking mode); answer-only rungs 1-8; pass A: 12 cases a rung (the sidekick rungs spread evenly across task types), 1 trial, to find the ceiling; pass B: trials 2 and 3 on the ceiling rung and the rungs either side | **about 36 hours, in four sittings** (about 31 h if SenseNova is skipped) |
| **full, all tiers** | per entry: the sidekick core set (112 answer-only cases incl. Restraint, 16 tool-mode cases) and rungs 4-9, 3 trials each; all 40 entries | **about 314 hours** (tier 0 118 h, tier 1 78 h, tier 2 77 h, tier 3 42 h) |

The first pass by sitting (each under 12 hours; an entry is its own resumable unit, so a sitting can
stop between entries):

| sitting | entries | hours |
|---|---|---|
| 1 | base Gemma 0.9, Gemma-Wisp 1.0, LFM3 1.7B 1.3, Phi-4-mini 0.6, Spark 4B 1.9, LFM3 Omni 0.7, Qwen3.5 4B 2.6, Gemma 4 E2B 0.7 | 9.6 |
| 2 | Ling 3.0 tiny 3.7, Nemotron Bolt 3.5 | 7.2 |
| 3 | Qwen3.5 9B 4.9, SenseNova-Loop 5.0 (expected to be skipped within minutes), Gemma 4 12B 1.3 | 11.2 (about 6.3 if SenseNova is skipped) |
| 4 | Ornith 9B | 8.2 |

So about 31-36 hours in practice, depending on SenseNova and on whether Qwen3.5 9B runs with thinking
on (thinking off is much shorter).

**What the first pass leaves out:**
- the other thinking mode of every model (tier 1);
- the second sampling settings (tier 2) and the other quantisations (tier 3);
- tool mode: rung 9 and the 16 sidekick tool-mode cases;
- the Restraint set;
- trials 2 and 3 on rungs far from the ceiling;
- 32 of the 44 rung-1 cases and 31 of the 43 rung-2 cases.

So it finds each model's answer-only ceiling in the mode it ships in; it does not show how thinking,
sampling or quantisation change the ceiling, or how the model does with tools. Those are the next
passes, model by model, once the first-pass report has been read.

## 5. Stop conditions and resuming

The run checks between trials and stops cleanly by itself when:
- free memory is under 15% (`memory_pressure -Q`);
- over 2% of the trials so far were lost to engine or transport faults (from 50 trials on);
- the file `$OUT/STOP` exists (the way to stop it from another terminal);
- `--max-minutes` has passed.

`$OUT/<entry>/progress.json` says why an entry stopped, and `$OUT/run-all.json` lists every entry
with its result: done (with its ceiling), skipped (and why), or stopped. Ctrl-C also stops it. The trial in progress is then
lost and redone on resume.

**Resume** by running the same command again. Trials already recorded are skipped; first-pass pass B
picks its rungs from pass A's records. A results folder is bound to the seeds it was made with
(`seeds.json`: a fingerprint of the ladder seed and, for held-out sidekick cases, of the private seed). A
run with other seeds, such as a replaced private seed, refuses to resume there and runs nothing; use a
new `$OUT`.

A trial whose grading cannot finish (a grader error) is counted as a failed trial (`grader_error`) and
the run goes on. So is model code that replaced its working folder before a command: that command is
refused, and the trial fails as unsafe.

Stop by hand when the Mac is unresponsive: the pointer or a terminal lags for seconds, or no line has
printed for 10 minutes (one thinking-on answer can take several minutes on Ornith).

## 6. What the person gets

`$OUT/report/report.html` and `report.md`, one page, private. For each model entry:
- what it can reliably do (the rungs it is solid up to);
- where it starts failing and how, in plain words: wrong results, broken syntax, ignored the
  instructions on names or files, ran out of room, went round in circles with the tools, or proposed
  something unsafe;
- two or three short real examples of its answers.

Then one table of rung against model, with the ceiling and "solid up to" rows. The records are in
`$OUT/<entry>/runs/` in the results store's format. Exporting or publishing anything is a separate
decision.
