"""Sealed execution of model output (spec 12 §3.4, build item S1).

Layers handled here (the static pre-screen, layer 1, is `edat.sidekick.prescreen`):

2. **Throwaway area** — `TrialArea`: a new folder per trial with `work/` (the
   fixture), `home/` (a fake HOME with a pinned `.gitconfig` and a canary
   file), `tmp/` and `bin/` (wrappers for the pinned toolchains). Deleted after
   grading.
3. **Seatbelt profile** — `edat-sidekick.sb`, deny by default, rendered with
   the trial folder and toolchain paths.
4. **Clean environment** — a fixed environment; nothing is inherited.
5. **Limits** — wall clock (watchdog kills the process group), CPU time and
   file size (rlimits), process count (rlimit headroom plus a watchdog count),
   resident memory (watchdog), output caps.

Isolation sits behind the `Isolation` interface so another method (a macOS
virtual machine, spec §8 question 1) can replace Seatbelt without touching the
graders. There is **no unsandboxed fallback**: if the sandbox cannot be applied,
`check()` raises `SandboxUnavailable` and nothing runs.
"""
from __future__ import annotations

import hashlib
import os
import resource
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from edat.sidekick.toolchain import Toolchain, ToolchainError, find_toolchain, wrapper_scripts

PROFILE_TEMPLATE = Path(__file__).with_name("edat-sidekick.sb")
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
ZSH = "/bin/zsh"

GIT_IDENTITY = {"name": "EDAT Test", "email": "test@edat.invalid"}
FIXED_GIT_DATE = "2026-01-05T09:00:00+00:00"


class SandboxUnavailable(RuntimeError):
    """The sandbox cannot be applied. The harness refuses to run."""


@dataclass(frozen=True)
class Limits:
    wall_s: float = 10.0
    cpu_s: int = 10
    file_bytes: int = 16 * 1024 * 1024
    max_procs: int = 64
    output_bytes: int = 64 * 1024
    rss_bytes: int = 1024 * 1024 * 1024


@dataclass
class ExecResult:
    exit_code: int | None
    stdout: str
    stderr: str
    wall_s: float
    timed_out: bool = False
    killed: str | None = None          # "wall_clock" | "memory" | "processes" | "cpu_time" | None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    pids: set[int] = field(default_factory=set)
    pid: int | None = None
    started: float = 0.0
    ended: float = 0.0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.killed

    def summary(self, cap: int = 4096) -> str:
        """What tool mode returns to the model: exit code and the first 4 KB of output."""
        head = f"exit code: {self.exit_code if self.exit_code is not None else 'killed'}"
        if self.killed:
            head += f" (stopped: {self.killed})"
        out = self.stdout[:cap]
        err = self.stderr[:cap]
        parts = [head]
        if out:
            parts.append("stdout:\n" + out + ("\n[stdout cut]" if len(self.stdout) > cap or self.stdout_truncated else ""))
        if err:
            parts.append("stderr:\n" + err + ("\n[stderr cut]" if len(self.stderr) > cap or self.stderr_truncated else ""))
        return "\n".join(parts)


CANARY_NAME = "edat-home-canary.txt"


class TrialArea:
    """One trial's throwaway folder. Use as a context manager."""

    def __init__(self, root_parent: str | os.PathLike | None = None, *, keep: bool = False,
                 toolchain: Toolchain | None = None):
        base = Path(root_parent or os.environ.get("EDAT_SIDEKICK_TMP") or tempfile.gettempdir())
        base.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="edat-sk-", dir=base)).resolve()
        self.work = self.root / "work"
        self.home = self.root / "home"
        self.tmp = self.root / "tmp"
        self.bin = self.root / "bin"
        self.keep = keep
        self.canary = "EDATCANARY" + uuid.uuid4().hex[:12]
        for d in (self.work, self.home, self.tmp, self.bin):
            d.mkdir()
        (self.home / ".gitconfig").write_text(
            "[user]\n"
            f"\tname = {GIT_IDENTITY['name']}\n\temail = {GIT_IDENTITY['email']}\n"
            "[init]\n\tdefaultBranch = main\n"
            "[core]\n\tpager = cat\n\teditor = true\n"
            "[advice]\n\tdetachedHead = false\n"
            "[commit]\n\tgpgsign = false\n")
        (self.home / CANARY_NAME).write_text(self.canary + "\n")
        tc = toolchain or find_toolchain()
        for name, text in wrapper_scripts(tc).items():
            p = self.bin / name
            p.write_text(text)
            p.chmod(0o755)

    def path(self, rel: str) -> Path:
        p = (self.work / rel).resolve()
        if p != self.work and self.work not in p.parents:
            raise ValueError(f"path escapes the working folder: {rel}")
        return p

    def write_files(self, files: dict[str, str | dict], base: Path | None = None) -> None:
        """files: rel path -> text, or {"content": text, "mode": 0o755}."""
        base = base or self.work
        for rel, spec in files.items():
            p = (base / rel)
            if base.resolve() not in p.resolve().parents:
                raise ValueError(f"fixture path escapes: {rel}")
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(spec, dict):
                p.write_text(spec.get("content", ""))
                if "mode" in spec:
                    p.chmod(int(spec["mode"]))
            else:
                p.write_text(spec)

    def cleanup(self) -> None:
        if not self.keep:
            shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "TrialArea":
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()


def clean_env(area: TrialArea) -> dict[str, str]:
    """Layer 4: the only environment model output ever sees."""
    return {
        "HOME": str(area.home),
        "TMPDIR": str(area.tmp) + "/",
        "PATH": f"{area.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "SHELL": ZSH,
        "USER": "edat",
        "LOGNAME": "edat",
        "TERM": "dumb",
        "TZ": "UTC",
        "GIT_CONFIG_GLOBAL": str(area.home / ".gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": GIT_IDENTITY["name"],
        "GIT_AUTHOR_EMAIL": GIT_IDENTITY["email"],
        "GIT_COMMITTER_NAME": GIT_IDENTITY["name"],
        "GIT_COMMITTER_EMAIL": GIT_IDENTITY["email"],
        "GIT_EDITOR": "true",
        "GIT_SEQUENCE_EDITOR": "true",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "PAGER": "cat",
        "EDITOR": "true",
        "VISUAL": "true",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "NODE_OPTIONS": "",
        "NO_COLOR": "1",
    }


class Isolation(Protocol):
    """One way of running untrusted code. Seatbelt today; replaceable."""

    name: str

    def check(self) -> None:
        """Raise SandboxUnavailable unless the isolation can be applied here, now."""
        ...

    def run(self, area: TrialArea, script: str, *, cwd: Path | None = None,
            limits: Limits = Limits(), stdin: str | None = None,
            env: dict[str, str] | None = None) -> ExecResult:
        """Run `script` with `zsh -f -c` inside the isolation, in `cwd` (default area.work)."""
        ...

    def describe(self) -> dict:
        """Run-key facts: method, profile hash. No local paths."""
        ...


def profile_template_sha256() -> str:
    return hashlib.sha256(PROFILE_TEMPLATE.read_bytes()).hexdigest()


def _sb_quote(path: str) -> str:
    if '"' in path or "\\" in path or "\n" in path:
        raise SandboxUnavailable(f"path not usable in a sandbox profile: {path!r}")
    return f'"{path}"'


def render_profile(area_root: Path, tc: Toolchain) -> str:
    text = PROFILE_TEMPLATE.read_text()
    read = "\n  ".join(f"(subpath {_sb_quote(p)})" for p in tc.read_roots())
    execs = []
    for p in tc.exec_roots():
        execs.append(f"(subpath {_sb_quote(p)})" if os.path.isdir(p) else f"(literal {_sb_quote(p)})")
    return (text.replace("@TRIAL@", str(area_root).replace('"', ""))
                .replace("@TOOLCHAIN_READ@", read)
                .replace("@TOOLCHAIN_EXEC@", "\n  ".join(execs)))


def _user_proc_count() -> int:
    try:
        out = subprocess.run(["/bin/ps", "-x", "-o", "pid="], capture_output=True, text=True, timeout=5)
        return len(out.stdout.split())
    except Exception:
        return 512


def _group_procs(pgid: int) -> list[tuple[int, int]]:
    """[(pid, rss_bytes)] for processes in the group."""
    try:
        out = subprocess.run(["/bin/ps", "-A", "-o", "pid=,pgid=,rss="], capture_output=True,
                             text=True, timeout=2)
    except Exception:
        return []
    res = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == str(pgid):
            try:
                res.append((int(parts[0]), int(parts[2]) * 1024))
            except ValueError:
                pass
    return res


def _reader(stream, cap: int, sink: list, flag: list) -> None:
    got = 0
    while True:
        chunk = stream.read(8192)
        if not chunk:
            break
        if got < cap:
            keep = chunk[: cap - got]
            sink.append(keep)
            got += len(keep)
            if len(keep) < len(chunk):
                flag.append(True)
        else:
            flag.append(True)
    stream.close()


class SeatbeltIsolation:
    """`sandbox-exec` with the deny-by-default tinyagent-bench profile.

    `sandbox-exec` is marked deprecated in its manual page (spec §7, open
    question 1). It is kept behind `Isolation` so it can be swapped out.
    """

    name = "seatbelt"

    def __init__(self, toolchain: Toolchain | None = None, sandbox_exec: str = SANDBOX_EXEC):
        self._tc = toolchain
        self.sandbox_exec = sandbox_exec
        self._checked = False

    @property
    def toolchain(self) -> Toolchain:
        if self._tc is None:
            try:
                self._tc = find_toolchain()
            except ToolchainError as exc:
                raise SandboxUnavailable(f"toolchain not usable: {exc}") from exc
        return self._tc

    def check(self) -> None:
        if os.uname().sysname != "Darwin":
            raise SandboxUnavailable("Seatbelt needs macOS")
        if not os.access(self.sandbox_exec, os.X_OK):
            raise SandboxUnavailable(f"{self.sandbox_exec} is missing")
        tc = self.toolchain
        with TrialArea(toolchain=tc) as area:
            try:
                res = self._spawn(area, "print -r -- edat-sandbox-ok", area.work, Limits(wall_s=10), None, None)
            except OSError as exc:
                raise SandboxUnavailable(f"sandbox-exec could not start: {exc}") from exc
            if res.exit_code != 0 or "edat-sandbox-ok" not in res.stdout:
                raise SandboxUnavailable("the sandbox profile could not be applied: "
                                         + (res.stderr.strip()[:300] or f"exit {res.exit_code}"))
            # The profile must actually deny: a write to the trial's parent must fail.
            probe = area.root.parent / f"edat-sk-probe-{uuid.uuid4().hex[:8]}"
            res = self._spawn(area, f"print -r -- x > {probe}", area.work, Limits(wall_s=10), None, None)
            escaped = probe.exists()
            if escaped:
                probe.unlink()
            if escaped or res.exit_code == 0:
                raise SandboxUnavailable("the sandbox did not deny a write outside the trial folder")
        self._checked = True

    def describe(self) -> dict:
        return {"isolation": self.name, "profile": "edat-sidekick-sb/1",
                "profile_sha256": profile_template_sha256(), "network": "denied"}

    def run(self, area: TrialArea, script: str, *, cwd: Path | None = None,
            limits: Limits = Limits(), stdin: str | None = None,
            env: dict[str, str] | None = None) -> ExecResult:
        if not self._checked:
            self.check()
        cwd = Path(cwd) if cwd else area.work
        if cwd.resolve() != area.root and area.root not in cwd.resolve().parents:
            raise ValueError("cwd must be inside the trial folder")
        return self._spawn(area, script, cwd, limits, stdin, env)

    def _spawn(self, area: TrialArea, script: str, cwd: Path, limits: Limits,
               stdin: str | None, env: dict[str, str] | None) -> ExecResult:
        profile = area.root / ".edat-profile.sb"
        if not profile.exists():
            profile.write_text(render_profile(area.root, self.toolchain))
            profile.chmod(0o444)
        full_env = clean_env(area)
        if env:
            full_env.update(env)
        argv = [self.sandbox_exec, "-f", str(profile), ZSH, "-f", "-c", script]
        nproc_soft = _user_proc_count() + limits.max_procs

        def preexec() -> None:
            os.setsid()
            resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_s, limits.cpu_s + 1))
            resource.setrlimit(resource.RLIMIT_FSIZE, (limits.file_bytes, limits.file_bytes))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            try:
                hard = resource.getrlimit(resource.RLIMIT_NPROC)[1]
                soft = nproc_soft if hard == resource.RLIM_INFINITY else min(nproc_soft, hard)
                resource.setrlimit(resource.RLIMIT_NPROC, (soft, hard))
            except (ValueError, OSError):
                pass

        started = time.time()
        t0 = time.monotonic()
        proc = subprocess.Popen(argv, cwd=str(cwd), env=full_env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                preexec_fn=preexec, close_fds=True)
        pgid = proc.pid
        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        out_flag: list = []
        err_flag: list = []
        readers = [threading.Thread(target=_reader, args=(proc.stdout, limits.output_bytes, out_chunks, out_flag), daemon=True),
                   threading.Thread(target=_reader, args=(proc.stderr, limits.output_bytes, err_chunks, err_flag), daemon=True)]
        for r in readers:
            r.start()
        try:
            if stdin:
                proc.stdin.write(stdin.encode())
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        killed = None
        pids = {proc.pid}
        last_sample = 0.0
        while proc.poll() is None:
            elapsed = time.monotonic() - t0
            if elapsed > limits.wall_s:
                killed = "wall_clock"
                break
            if elapsed - last_sample >= 0.2:
                last_sample = elapsed
                group = _group_procs(pgid)
                pids.update(p for p, _ in group)
                if sum(r for _, r in group) > limits.rss_bytes:
                    killed = "memory"
                    break
                if len(group) > limits.max_procs:
                    killed = "processes"
                    break
            time.sleep(0.02)
        if killed:
            _kill_group(pgid)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if not killed:
            killed = cpu_limit_kill(proc.returncode)
        # Background children left behind are stopped too.
        _kill_group(pgid)
        for r in readers:
            r.join(timeout=2)
        wall = time.monotonic() - t0
        return ExecResult(
            exit_code=None if killed else proc.returncode,
            stdout=b"".join(out_chunks).decode("utf-8", "replace"),
            stderr=b"".join(err_chunks).decode("utf-8", "replace"),
            wall_s=wall, timed_out=killed == "wall_clock", killed=killed,
            stdout_truncated=bool(out_flag), stderr_truncated=bool(err_flag),
            pids=pids, pid=proc.pid, started=started, ended=time.time())


def cpu_limit_kill(returncode: int | None) -> str | None:
    """"cpu_time" when the process (or the shell's last command) was ended by the CPU-time limit: SIGXCPU at
    the soft limit, SIGKILL at the hard one. The shell reports a child's signal as 128 + its number."""
    if returncode is None:
        return None
    sig = -returncode if returncode < 0 else returncode - 128 if 128 < returncode < 160 else None
    return "cpu_time" if sig in (signal.SIGXCPU, signal.SIGKILL) else None


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


_DEFAULT: Isolation | None = None


def get_isolation() -> Isolation:
    """The isolation every grader uses. Raises SandboxUnavailable; never falls back."""
    global _DEFAULT
    if _DEFAULT is None:
        iso = SeatbeltIsolation()
        iso.check()
        _DEFAULT = iso
    return _DEFAULT


def set_isolation(iso: Isolation | None) -> None:
    """Replace the isolation method (e.g. a VM). `None` resets to Seatbelt."""
    global _DEFAULT
    _DEFAULT = iso
