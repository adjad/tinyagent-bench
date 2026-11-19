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

import atexit
import hashlib
import os
import resource
import select
import shutil
import signal
import stat
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
    survivor: bool = False             # a process outlived the command and its process-group kill

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
        self.keep = keep
        self.canary = "EDATCANARY" + uuid.uuid4().hex[:12]
        for d in (self.work, self.home, self.tmp):
            d.mkdir()
        # The host side reaches model-written files only through this handle on the working folder, opened
        # once now, before any model code runs, and never through a path the model could re-point.
        self._work_fd = os.open(self.work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        st = os.fstat(self._work_fd)
        self._work_id = (st.st_dev, st.st_ino)
        self.refused: list[dict] = []   # symbolic links met on the host side: never followed, reported
        self.survivors: list[str] = []  # commands that left a process running after their group was killed
        self.killed = 0                 # such processes found holding this folder and killed
        self.sealed = False
        (self.home / ".gitconfig").write_text(
            "[user]\n"
            f"\tname = {GIT_IDENTITY['name']}\n\temail = {GIT_IDENTITY['email']}\n"
            "[init]\n\tdefaultBranch = main\n"
            "[core]\n\tpager = cat\n\teditor = true\n"
            "[advice]\n\tdetachedHead = false\n"
            "[commit]\n\tgpgsign = false\n"
            # no detached background gc or maintenance: they would outlive the command (setsid)
            "[gc]\n\tauto = 0\n\tautoDetach = false\n"
            "[maintenance]\n\tauto = false\n")
        (self.home / CANARY_NAME).write_text(self.canary + "\n")
        tc = toolchain or find_toolchain()
        # The python3/node/git wrappers live in a host-owned folder outside every trial folder, which the
        # sandbox may run from but never write: model code cannot replace what the graders run.
        self.bin = tools_bin(tc, base)

    def path(self, rel: str) -> Path:
        p = (self.work / rel).resolve()
        if p != self.work and self.work not in p.parents:
            raise ValueError(f"path escapes the working folder: {rel}")
        return p

    # ---- host-side file access, never through a link (model code may re-point any path at any time)

    def _refuse(self, rel: str, why: str) -> None:
        self.refused.append({"path": rel, "why": why})

    @staticmethod
    def _parts(rel: str) -> list[str] | None:
        if not rel or os.path.isabs(rel):
            return None
        parts = [x for x in Path(rel).parts if x not in ("", ".")]
        return None if not parts or ".." in parts else parts

    def _is_link(self, name: str, dir_fd: int) -> bool:
        try:
            return stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
        except OSError:
            return False

    def _open_parent(self, parts: list[str], rel: str, *, create: bool = False, start: int | None = None) -> int | None:
        """A handle on the folder holding the last part, reached from the working-folder handle (or `start`)
        one part at a time, each opened without following links. None if a part is missing (unless
        `create`) or a link."""
        fd = os.dup(self._work_fd if start is None else start)
        try:
            for part in parts[:-1]:
                try:
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        return None
                    try:
                        os.mkdir(part, 0o755, dir_fd=fd)
                    except FileExistsError:
                        pass
                    try:
                        nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    except OSError:
                        if self._is_link(part, fd):
                            self._refuse(rel, "a folder on its path is a symbolic link")
                        return None
                except OSError:
                    if self._is_link(part, fd):
                        self._refuse(rel, "a folder on its path is a symbolic link")
                    return None
                os.close(fd)
                fd = nxt
            out, fd = fd, -1
            return out
        finally:
            if fd >= 0:
                os.close(fd)

    def read_bytes_inside(self, rel: str, limit: int | None = None, *, start: int | None = None) -> bytes | None:
        """A regular file's bytes from inside the working folder, or None (missing, a folder, a link, a pipe,
        outside). Nothing on the way is followed if it is a link, however the folder changes meanwhile."""
        parts = self._parts(rel)
        if parts is None:
            return None
        dfd = self._open_parent(parts, rel, start=start)
        if dfd is None:
            return None
        try:
            try:
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
            except OSError:
                if self._is_link(parts[-1], dfd):
                    self._refuse(rel, "the file is a symbolic link")
                return None
        finally:
            os.close(dfd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            chunks, left = [], limit
            while left is None or left > 0:
                b = os.read(fd, 1 << 16 if left is None else min(1 << 16, left))
                if not b:
                    break
                chunks.append(b)
                if left is not None:
                    left -= len(b)
            return b"".join(chunks)
        finally:
            os.close(fd)

    def read_result(self, path: Path, limit: int = 1 << 22) -> bytes | None:
        """A file a grader script wrote somewhere under the trial folder (`root/grade/...`), read on the host
        the same way: from a handle on the trial folder, never following a link the code under test made."""
        try:
            rel = Path(path).relative_to(self.root)
        except ValueError:
            return None
        rfd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            return self.read_bytes_inside(str(rel), limit, start=rfd)
        finally:
            os.close(rfd)

    def grade_dir(self, files: dict[str, str]) -> Path:
        """A new folder for a grader's own scripts, `root/grade-<random>`, made and filled by the host
        through a folder handle. Code under test may have run in this area before (a git task, written
        tests run against several mutants), so a fixed name like `grade` could already be a link to a host
        folder; a fresh random name cannot be, and every file in it is created new, never through a link."""
        name = f"grade-{uuid.uuid4().hex}"
        rfd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.mkdir(name, 0o755, dir_fd=rfd)
            gfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rfd)
        finally:
            os.close(rfd)
        try:
            for fname, text in files.items():
                if "/" in fname or fname in ("", ".", ".."):
                    raise ValueError(f"grader file names are plain names: {fname!r}")
                fd = os.open(fname, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=gfd)
                with os.fdopen(fd, "w") as fh:
                    fh.write(text)
        finally:
            os.close(gfd)
        return self.root / name

    def copy_out(self, src: str, dest: "TrialArea", dst: str, *, skip=lambda rel: False,
                 budget: list[int] | None = None) -> bool:
        """Copy the folder `src` of this trial folder ("work", or "origin.git" beside it) to `dst` in another
        area, on the host side and through handles only: nothing on the way is followed, links are copied as
        links (their targets never read), anything that is not a file, folder or link is left out, and so is
        every path `skip(rel)` names. `budget` is [bytes, entries] left, shared across calls; False when it
        runs out (the copy is then incomplete and must not be graded). A missing `src` copies nothing."""
        budget = budget if budget is not None else [64 << 20, 50000]
        rfd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if src == "work":
                sfd = os.dup(self._work_fd)
            else:
                try:
                    sfd = os.open(src, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rfd)
                except OSError:
                    if self._is_link(src, rfd):
                        self._refuse(src, "a folder the grader copies is a symbolic link")
                    return True
        finally:
            os.close(rfd)
        dfd = dest.dir_handle(dest.root / dst) if dst == "work" else None
        if dfd is None:
            drfd = os.open(dest.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.mkdir(dst, 0o755, dir_fd=drfd)
                dfd = os.open(dst, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=drfd)
            finally:
                os.close(drfd)
        out_fds = {".": dfd}
        try:
            for dirpath, dirnames, filenames, sfd_cur in os.fwalk(".", dir_fd=sfd, follow_symlinks=False):
                rel_dir = os.path.normpath(dirpath)
                ofd = out_fds.get(rel_dir)
                if ofd is None:
                    dirnames[:] = []
                    continue
                keep = []
                for name in list(dirnames) + list(filenames):
                    rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                    if skip(rel):
                        continue
                    budget[1] -= 1
                    if budget[1] < 0:
                        return False
                    try:
                        st = os.stat(name, dir_fd=sfd_cur, follow_symlinks=False)
                    except OSError:
                        continue
                    in_git = ".git" in rel.split("/")[:-1] or src.endswith(".git")
                    if name == ".git" and not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode) and in_git:
                        # A `.git` file (a pointer to a repository elsewhere) or a link inside a repository's
                        # own folder: git would follow either. Never copied; a link is reported.
                        if stat.S_ISLNK(st.st_mode):
                            self._refuse(f"{src}/{rel}", "a link inside the repository's .git folder")
                        continue
                    if stat.S_ISLNK(st.st_mode):
                        os.symlink(os.readlink(name, dir_fd=sfd_cur), name, dir_fd=ofd)
                    elif stat.S_ISDIR(st.st_mode):
                        os.mkdir(name, 0o755, dir_fd=ofd)
                        out_fds[rel] = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ofd)
                        keep.append(name)
                    elif stat.S_ISREG(st.st_mode):
                        if not self._copy_file(name, sfd_cur, ofd, 0o755 if st.st_mode & 0o100 else 0o644, budget):
                            return False
                dirnames[:] = [d for d in dirnames if d in keep]
            return True
        finally:
            for fd in out_fds.values():
                os.close(fd)
            os.close(sfd)

    @staticmethod
    def _copy_file(name: str, sfd: int, ofd: int, mode: int, budget: list[int]) -> bool:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=sfd)
        except OSError:
            return True
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return True
            out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=ofd)
            try:
                while True:
                    b = os.read(fd, 1 << 16)
                    if not b:
                        return True
                    budget[0] -= len(b)
                    if budget[0] < 0:
                        return False
                    while b:
                        b = b[os.write(out, b):]
            finally:
                os.close(out)
        finally:
            os.close(fd)

    def clear_work_files(self) -> None:
        """Remove the files (and links) at the top of the working folder, through its handle."""
        for name in os.listdir(self._work_fd):
            try:
                st = os.stat(name, dir_fd=self._work_fd, follow_symlinks=False)
                if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                    os.unlink(name, dir_fd=self._work_fd)
            except FileNotFoundError:
                pass

    def read_inside(self, rel: str) -> str | None:
        """The text of a model-written file, read on the host as `read_bytes_inside` reads it, or None."""
        data = self.read_bytes_inside(rel)
        return None if data is None else data.decode("utf-8", errors="replace")

    def write_inside(self, rel: str, text: str) -> bool:
        """Write a file inside the working folder on the host side, creating its folders, never through a
        link. False if a part of the path is a link or not a folder, or the file is not a regular file."""
        parts = self._parts(rel)
        if parts is None:
            return False
        dfd = self._open_parent(parts, rel, create=True)
        if dfd is None:
            return False
        try:
            try:
                fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644, dir_fd=dfd)
            except OSError:
                if self._is_link(parts[-1], dfd):
                    self._refuse(rel, "the file is a symbolic link")
                return False
        finally:
            os.close(dfd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False
            os.ftruncate(fd, 0)
            data = text.encode("utf-8")
            while data:
                data = data[os.write(fd, data):]
            return True
        finally:
            os.close(fd)

    def work_replaced(self) -> bool:
        """True when the path `work` no longer names the folder the area was created with."""
        try:
            st = os.stat(self.work, follow_symlinks=False)
        except OSError:
            return True
        return (st.st_dev, st.st_ino) != self._work_id

    def seal(self) -> None:
        """End of the model's part. Processes that outlived their command and still hold anything in this
        folder (an open file, their working folder) are found, stopped and killed, and counted as survivors
        (see `stop_and_kill_holders`). Then the trial folder is renamed:
        the sandbox profile lets sandboxed code write only inside the old path, never create the path
        itself, so whatever is left can no longer reach the files by name. (A process that was not found
        keeps only the descriptors it already had. What it writes through them is something the model could
        have written before the seal anyway: each grader reads its input once, after the seal, from a copy
        or through handles, and never compares two reads.) If the working folder was swapped for something
        else, the original is put back and reported. Never raises: a folder that cannot be sealed is
        reported, and the trial fails as unsafe."""
        if self.sealed:
            return
        self.sealed = True
        try:
            # Always looked for (two or more lsof listings, a tenth of a second each): a process can close
            # the sentinel pipe it was started with and so not be counted as a survivor when its command
            # ended. Any holder found makes the trial unsafe, whatever it did.
            n, settled = stop_and_kill_holders(self.root)
            self.killed += n
            if n:
                self.survivors.append("(a process still holding the trial folder at the seal)")
            if not settled:
                self.survivors.append("(processes kept taking over the trial folder; not all may have been found)")
            new = self.root.with_name(self.root.name + "-sealed")
            for _ in range(100):
                try:
                    os.rename(self.root, new)
                    break
                except OSError:
                    time.sleep(0.01)
            else:
                self._refuse("trial", "the trial folder could not be sealed")
                return
            self.root = new
            self.work, self.home, self.tmp = new / "work", new / "home", new / "tmp"
            if self.work_replaced():
                self._refuse("work", "the working folder was replaced")
                self._put_work_back()
        except OSError:
            self._refuse("trial", "the trial folder could not be sealed")

    def _put_work_back(self) -> None:
        rfd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            found = None
            for name in os.listdir(rfd):
                st = os.stat(name, dir_fd=rfd, follow_symlinks=False)
                if (st.st_dev, st.st_ino) == self._work_id:
                    found = name
            if found == "work" or found is None:
                return
            try:
                st = os.stat("work", dir_fd=rfd, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    os.rename("work", "work.replaced", src_dir_fd=rfd, dst_dir_fd=rfd)
                else:
                    os.unlink("work", dir_fd=rfd)
            except FileNotFoundError:
                pass
            os.rename(found, "work", src_dir_fd=rfd, dst_dir_fd=rfd)
        finally:
            os.close(rfd)

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

    def dir_handle(self, cwd: Path) -> int | None:
        """A handle on a folder inside the trial folder, reached without following links (the working
        folder: its own handle). None if a part is a link or missing."""
        cwd = Path(cwd)
        if cwd == self.work:
            return os.dup(self._work_fd)
        try:
            rel = cwd.relative_to(self.root)
        except ValueError:
            return None
        try:
            rfd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            return None
        if not rel.parts:
            return rfd
        try:
            parts = list(rel.parts) + ["."]
            return self._open_parent(parts, str(rel), start=rfd)
        except OSError:
            return None
        finally:
            os.close(rfd)

    def cleanup(self) -> None:
        if getattr(self, "root", None) is not None:
            try:
                self.killed = getattr(self, "killed", 0) + kill_holders(self.root)
            except OSError:
                pass
        if getattr(self, "_work_fd", -1) >= 0:
            os.close(self._work_fd)
            self._work_fd = -1
        if not self.keep:
            shutil.rmtree(self.root, ignore_errors=True)
            if os.path.lexists(self.root):           # a folder the model locked (mode 000, uchg): unlock, remove
                _unlock_tree(str(self.root))
                shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "TrialArea":
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()


def _unlock_tree(root: str) -> None:
    """Clear the user flags (uchg and the like) and give the owner full access on everything under `root`,
    never through a link: a symbolic link has its own flags cleared and is never followed, and a file with
    more than one hard link is left alone (its other name may be outside the trial folder). Runs after every
    process of the trial is gone; anything it cannot change stays."""
    def unlock(path: str, mode: int | None) -> None:
        for fn, arg in ((getattr(os, "chflags", None), 0), (os.chmod, mode)):
            if fn is None or arg is None:
                continue
            try:
                fn(path, arg, follow_symlinks=False)
            except (OSError, NotImplementedError, ValueError):
                pass
    stack = [root]
    while stack:
        d = stack.pop()
        unlock(d, 0o700)
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_symlink():
                        unlock(e.path, None)             # flags only: the link itself, never its target
                    elif e.stat(follow_symlinks=False).st_nlink == 1:
                        unlock(e.path, 0o600)
        except OSError:
            pass


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


_TOOLS: dict[tuple, Path] = {}


def tools_bin(tc: Toolchain, base: Path | None = None) -> Path:
    """A host-owned folder of wrapper scripts (python3, node, git) for this toolchain, made once per process
    and left read-only. It is outside every trial folder, so the sandbox can run from it but not write it."""
    base = Path(base or os.environ.get("EDAT_SIDEKICK_TMP") or tempfile.gettempdir())
    key = (str(base.resolve()), tuple(sorted(wrapper_scripts(tc).items())))
    d = _TOOLS.get(key)
    if d is not None and d.is_dir():
        return d
    d = Path(tempfile.mkdtemp(prefix="edat-sk-tools-", dir=base)).resolve()
    for name, text in wrapper_scripts(tc).items():
        fd = os.open(d / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o555)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
    d.chmod(0o555)
    _TOOLS[key] = d

    def _remove(path=d):
        try:
            path.chmod(0o755)
            shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass
    atexit.register(_remove)
    return d


def _sb_regex_prefix(path: str) -> str:
    """The path as a literal prefix in a Seatbelt regex: letters, digits, '/', '_' and '-' as they are, any
    other character in a bracket (so '.' is a dot)."""
    if any(c in path for c in '[]\\"\n^$'):
        raise SandboxUnavailable(f"path not usable in a sandbox profile: {path!r}")
    return "".join(c if (c.isalnum() or c in "/_-") else f"[{c}]" for c in path)


def render_profile(area_root: Path, tc: Toolchain, tools: Path | None = None) -> str:
    text = PROFILE_TEMPLATE.read_text()
    tools = tools or tools_bin(tc)
    read = "\n  ".join(f"(subpath {_sb_quote(p)})" for p in tc.read_roots())
    execs = []
    for p in tc.exec_roots():
        execs.append(f"(subpath {_sb_quote(p)})" if os.path.isdir(p) else f"(literal {_sb_quote(p)})")
    return (text.replace("@TRIAL_RE@", _sb_regex_prefix(str(area_root)))
                .replace("@TRIAL@", str(area_root).replace('"', ""))
                .replace("@TOOLS@", str(tools).replace('"', ""))
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
        return {"isolation": self.name, "profile": "edat-sidekick-sb/2",
                "profile_sha256": profile_template_sha256(), "network": "denied"}

    def run(self, area: TrialArea, script: str, *, cwd: Path | None = None,
            limits: Limits = Limits(), stdin: str | None = None,
            env: dict[str, str] | None = None) -> ExecResult:
        if not self._checked:
            self.check()
        cwd = Path(cwd) if cwd else area.work
        if (cwd == area.work or area.work in cwd.parents) and area.work_replaced():
            # Model code replaced the working folder (with a link, say). That is the model's doing, not a
            # harness fault: the command is refused, the area records it (a planted_link event, so the trial
            # fails as unsafe) and the run goes on.
            area._refuse("work", "the working folder was replaced before a command")
            return ExecResult(exit_code=126, stdout="", stderr="refused: the project folder was replaced",
                              wall_s=0.0)
        # A lexical check only (a harness mistake if it fails); links on the way are the model's doing and are
        # refused below, when the command's folder is opened through handles.
        if ".." in cwd.parts or (cwd != area.root and area.root not in cwd.parents):
            raise ValueError("cwd must be inside the trial folder")
        return self._spawn(area, script, cwd, limits, stdin, env)

    def _spawn(self, area: TrialArea, script: str, cwd: Path, limits: Limits,
               stdin: str | None, env: dict[str, str] | None) -> ExecResult:
        # The profile is passed as text and kept on the host side only. Written into the trial folder (as it
        # once was) the model could replace it before its next command, since that folder is writable to it.
        if getattr(area, "_profile_root", None) != area.root:      # first command, or the area was sealed
            area._profile_text = render_profile(area.root, self.toolchain, area.bin)
            area._profile_root = area.root
        full_env = clean_env(area)
        if env:
            full_env.update(env)
        argv = [self.sandbox_exec, "-p", area._profile_text, ZSH, "-f", "-c", script]
        nproc_soft = _user_proc_count() + limits.max_procs

        # The command starts in its folder through a handle (fchdir in the child), never by a path the model
        # could re-point between a check and the start.
        cwd_fd = area.dir_handle(cwd)
        if cwd_fd is None:
            area._refuse("cwd", "the command's folder was replaced")
            return ExecResult(exit_code=126, stdout="", stderr="refused: the project folder was replaced", wall_s=0.0)

        def preexec() -> None:
            os.fchdir(cwd_fd)
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
        # A sentinel pipe every process of the command inherits: while any of them is alive, its write end
        # stays open. After the process-group kill, an open write end means a process escaped the group
        # (`setsid`). macOS gives user space no reliable way to find such a process to kill it (no cgroups;
        # `ps` hides the environment of system binaries), so it is detected here, reported by the graders,
        # and locked out by `TrialArea.seal`. A process that closes every descriptor escapes the detection,
        # not the seal or the handle-only host access.
        sent_r, sent_w = os.pipe()
        try:
            proc = subprocess.Popen(argv, env=full_env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    preexec_fn=preexec, close_fds=True, pass_fds=(sent_w,))
        finally:
            os.close(sent_w)
            os.close(cwd_fd)
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
        survivor = _still_held(sent_r, 1.0)
        os.close(sent_r)
        for r in readers:
            r.join(timeout=2)
        survivor = survivor or any(r.is_alive() for r in readers)
        if survivor:
            area.survivors.append(script[:300])
        wall = time.monotonic() - t0
        return ExecResult(
            exit_code=None if killed else proc.returncode,
            stdout=b"".join(out_chunks).decode("utf-8", "replace"),
            stderr=b"".join(err_chunks).decode("utf-8", "replace"),
            wall_s=wall, timed_out=killed == "wall_clock", killed=killed,
            stdout_truncated=bool(out_flag), stderr_truncated=bool(err_flag),
            pids=pids, pid=proc.pid, started=started, ended=time.time(), survivor=survivor)


def _holders(prefix: str) -> set[int]:
    """Processes of this user, other than this one, holding a file under `prefix` open or working there."""
    try:
        out = subprocess.run(["/usr/sbin/lsof", "-n", "-P", "-w", "-F", "pn", "-u", str(os.getuid())],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    pids, pid = set(), None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith("n") and pid and pid != os.getpid():
            name = line[1:]
            if name == prefix or name.startswith(prefix + "/"):
                pids.add(pid)
    return pids


HOLDER_ROUNDS = 12        # listings at most
HOLDER_QUIET = 2          # listings in a row with no new holder before the search counts as settled


def stop_and_kill_holders(root: Path) -> tuple[int, bool]:
    """Find and kill the processes that outlived a command (`setsid`) and still hold something under `root`.
    One listing is not enough: a process can pass its descriptor to a child and exit between the listing
    and the kill. So every holder found is stopped at once (it can no longer fork or pass anything on), and
    the listing is repeated until HOLDER_QUIET listings in a row find no new one; then all are killed.
    Returns (number found, settled). Not settled means holders kept appearing for HOLDER_ROUNDS listings and
    some may not have been found: the caller reports that plainly. A process that holds nothing under
    `root` when the folder is sealed can no longer reach it by name."""
    prefix = str(root)
    seen: set[int] = set()
    quiet = 0
    for _ in range(HOLDER_ROUNDS):
        new = _holders(prefix) - seen
        if not new:
            quiet += 1
            if quiet >= HOLDER_QUIET:
                break
            continue
        quiet = 0
        for p in new:
            try:
                os.kill(p, signal.SIGSTOP)
            except (ProcessLookupError, PermissionError):
                pass
        seen |= new
    n = 0
    for p in seen:
        try:
            os.kill(p, signal.SIGKILL)
            n += 1
        except (ProcessLookupError, PermissionError):
            pass
    return len(seen), quiet >= HOLDER_QUIET


def kill_holders(root: Path) -> int:
    return stop_and_kill_holders(root)[0]


def _still_held(fd: int, wait_s: float) -> bool:
    """True if some process still holds the write end of the sentinel pipe after `wait_s` seconds."""
    end = time.monotonic() + wait_s
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return True
        ready, _, _ = select.select([fd], [], [], left)
        if ready:
            if os.read(fd, 4096) == b"":
                return False                     # end of file: every holder has exited


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
