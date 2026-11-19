# tinyagent-bench admin helper

Read this before you run it with `sudo`. It is one Python file, `edat_admin_helper.py`. You can read it in one sitting.

## What it is for

Some performance tests need things only an administrator can do:

- drop the file cache (`purge`) so a model load is cold;
- run `powermetrics` to sample power;
- lower the GPU wired-memory limit to the 16 GB Mac default;
- pin a block of RAM (a "balloon") so a 24 GB Mac behaves like a 16 GB one.

The helper does exactly these things and nothing else. It is the only tinyagent-bench process that runs as root. The benchmark runner talks to it over a Unix socket.

## How to start it

Pick a run folder you own. Then:

```bash
sudo /usr/bin/python3 tools/edat_admin_helper/edat_admin_helper.py --run-dir /absolute/path/to/run-folder
```

Options:

- `--max-hours 6` (default) or `--max-hours 18` (for the real-time simulated day). No other value is accepted.
- `--dry-run`: see below.
- `--print-plan`: print the verb table with exact commands, then exit. No socket, no root needed.

It refuses to start if you did not use `sudo`, if `--run-dir` is not a plain absolute path, if it already exists, if any part of the path is a symbolic link (on macOS `/tmp` is one: use `/private/tmp`), or if the folder that will hold it is not owned by you or by root. The helper creates `--run-dir` itself; use a new folder for every start.

## Files and links

The helper runs as root, so it never lets a symbolic link redirect what it writes:

- **It creates the run folder itself**, mode 0700, owned by you. Before that it opens the folder's parent one path component at a time, from `/`, with `O_NOFOLLOW`: a link anywhere in the path, including one swapped in while it starts, is refused, not followed. It then works only through that folder's open descriptor.
- **Every file it writes** (`admin-audit.jsonl`, `powermetrics-<n>.plist`) is created by a fixed name relative to the folder's descriptor with `O_CREAT | O_EXCL | O_NOFOLLOW`, mode 0600. If anything already sits at that name, a planted link included, it refuses and writes nothing. No name can contain `/` or `..`, so nothing can resolve outside the folder.
- **Ownership is handed to you through the file's descriptor** (`fchown`), never by path. The run folder gets the same treatment. The socket, which can only be named by path, has its owner changed with the link-safe call relative to the folder (`fchownat` with `AT_SYMLINK_NOFOLLOW`).
- **powermetrics never opens a path.** The helper creates its output file as above and passes the open file as powermetrics' standard output.
- **The socket** is created 0600 (umask) at `<run-dir>/admin.sock`. Binding a Unix socket takes a path, so immediately before and after binding the helper checks, without following links, that the path still names the folder it created; if not, it refuses, logs it and exits.
- It never calls `chmod` or `chown` on a path, and never opens a file with Python's ordinary `open`. A test checks the source for this.

It uses only the Python standard library. It does not import the `edat` package.

## What it listens on

A Unix socket at `<run-dir>/admin.sock`, mode 0600, owned by you. It accepts one client. After that client connects, the listener is closed. A second client cannot connect.

Requests are one JSON object per line: `{"id": 1, "verb": "purge", "args": {}}`.
Answers are one JSON line: `{"id": 1, "ok": true, "result": {...}, "error": null}`.

Malformed lines, unknown verbs and any argument not in the table are refused with `ok: false`. They are logged and never run.

## The complete verb table

Every command is a fixed list of arguments. There is no shell.

| Verb | Allowed arguments | Exactly what runs |
|---|---|---|
| `purge` | none | `/bin/sync`, then `/usr/sbin/purge` |
| `powermetrics_start` | `interval_ms` is 100, 250 or 1000 | `/usr/bin/powermetrics --samplers cpu_power,gpu_power,ane_power,thermal -i <interval_ms> -f plist`, with its standard output going to `<run-dir>/powermetrics-<n>.plist`, a new file the helper creates first (`n` counts from 1; one instance at a time) |
| `powermetrics_stop` | none | SIGINT to the powermetrics process it started, then wait (SIGTERM only to that child after a timeout) |
| `wired_limit_set` | `mb` is 10900 | `/usr/sbin/sysctl iogpu.wired_limit_mb=10900` |
| `wired_limit_reset` | none | `/usr/sbin/sysctl iogpu.wired_limit_mb=<value read at start>` |
| `balloon_wire` | `gib` is 8 or 10.5 | No command. In the helper process: allocate anonymous memory, write one byte per page, `mlock` it. One balloon at a time. |
| `balloon_release` | none | No command. `munlock` and free. |
| `status` | none | No command. Reports state. |
| `exit` | none | Restore, then exit. |

At start it reads the original limit once with `/usr/sbin/sysctl -n iogpu.wired_limit_mb`. That read changes nothing. If it cannot read the original, it refuses to change the limit.

The powermetrics sampler names and flags have not been checked against `powermetrics -h` on the reference Mac yet. If powermetrics rejects them, the exit code shows in the audit log.

## What it never does

- Run any other command, sysctl or sampler.
- Write outside the run folder.
- Open a network connection.
- Signal a process it did not start.
- Install anything.
- Touch oMLX or Harbor settings.

## How restore works

On `exit`, when the client disconnects, on SIGINT, on SIGTERM, and when its lifetime ends, the helper does these steps in order:

1. stop powermetrics;
2. release the balloon;
3. reset `iogpu.wired_limit_mb` to the value read at start (only if it changed it).

Each step runs even if an earlier one fails. Each step is logged. Running restore twice is safe.

## What a SIGKILL leaves behind

SIGKILL cannot be caught. If the helper is killed that way:

- The wired limit stays changed until you reset it or reboot. The sysctl is not persistent.
- The balloon is freed with the process.
- A powermetrics child may keep running. Check with `pgrep powermetrics`.

The runner checks `sysctl -n iogpu.wired_limit_mb` at the start of every run. It warns if the value differs from the recorded original.

## The audit log

Every request goes to `<run-dir>/admin-audit.jsonl`, one JSON object per line, flushed at once. Each line has the verb, arguments, result, error, the commands run with their exit codes, `t_wall_utc` and `t_ns`. `t_ns` is `mach_continuous_time` in nanoseconds. If that clock is unavailable the line says `monotonic_ns_fallback` in its `clock` field. Startup, each restore step and exit are logged too.

```bash
python3 -c "import json,sys; [print(r['t_wall_utc'], r.get('verb') or r['event'], r.get('ok'), r.get('error')) for r in map(json.loads, open(sys.argv[1]))]" /absolute/path/to/run-folder/admin-audit.jsonl
```

## Rehearse with `--dry-run`

```bash
python3 tools/edat_admin_helper/edat_admin_helper.py --run-dir /absolute/path/to/run-folder --dry-run
```

No root is needed. The run folder must not exist yet; the helper creates it, as for a real start. The helper still serves the socket, so the runner can be rehearsed against it. It prints each exact command it would run and runs none. It does not `mlock`: it only reports how big the balloon would be.

## Check by hand afterwards

```bash
sysctl -n iogpu.wired_limit_mb
```

The value should be what it was before the run (the audit log's first line has it). `0` means the system default. Also check that nothing is left running: `pgrep powermetrics` should print nothing.
