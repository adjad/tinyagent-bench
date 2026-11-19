"""The pinned toolchains model output runs with (spec 12 §3.1).

Python (with pytest), Node and git are located once, on the harness side,
outside the sandbox. Inside a trial they are reached through small wrapper
scripts in the trial's own `bin/` folder, so `python3`, `node` and `git` in a
model's command always mean the pinned builds, and the Xcode `git` shim (which
would start `xcrun`) is never used.

Override with environment variables:
  EDAT_SIDEKICK_PYTHON  a Python 3.13 interpreter that can import pytest
  EDAT_SIDEKICK_NODE    a Node LTS binary
  EDAT_SIDEKICK_GIT     a git binary (default: the real binary behind /usr/bin/git)

A tool must be self-contained: every library its binary loads has to live in its
own folder or in the system's (/usr/lib, /System). The sandbox lets model code
read only those, so a build that loads libraries from elsewhere (a Homebrew Node
linking /opt/homebrew/opt/libuv, say) is refused here with the reason, instead
of failing the sandbox self-test later. Use the official nodejs.org Node and a
python.org or uv-managed CPython.

Versions go in the run key; local paths never leave the machine.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


class ToolchainError(RuntimeError):
    pass


def _run(argv: list[str], timeout: float = 20.0) -> str:
    out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                         env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/")})
    if out.returncode != 0:
        raise ToolchainError(f"{argv[0]} failed: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


@dataclass(frozen=True)
class Tool:
    name: str
    path: str                     # what the wrapper execs
    version: str
    read_roots: tuple[str, ...]   # folders the sandbox must let it read
    exec_roots: tuple[str, ...]   # folders (or files) it may exec from


@dataclass(frozen=True)
class Toolchain:
    python: Tool
    node: Tool | None
    git: Tool
    zsh_version: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def read_roots(self) -> list[str]:
        out: list[str] = []
        for t in self.tools():
            out.extend(t.read_roots)
        return sorted(set(out))

    def exec_roots(self) -> list[str]:
        out: list[str] = []
        for t in self.tools():
            out.extend(t.exec_roots)
        return sorted(set(out))

    def tools(self) -> list[Tool]:
        return [t for t in (self.python, self.node, self.git) if t is not None]

    def run_key(self) -> dict:
        """Versions only: no local paths."""
        return {"python": self.python.version, "node": self.node.version if self.node else None,
                "git": self.git.version, "zsh": self.zsh_version, "notes": list(self.notes)}


def _real(p: str) -> str:
    return os.path.realpath(p)


SYSTEM_LIB_ROOTS = ("/usr/lib/", "/System/")
SHARED_PREFIXES = ("/", "/usr", "/usr/local", "/opt/homebrew", "/opt/local")
OTOOL = "/usr/bin/otool"


def _linked(binary: str) -> list[str]:
    """Libraries a Mach-O binary links (absolute paths; @rpath-style entries are inside the tool)."""
    out = subprocess.run([OTOOL, "-L", binary], capture_output=True, text=True, timeout=20,
                         env={"PATH": "/usr/bin:/bin"})
    libs = []
    for line in out.stdout.splitlines():
        if not line.startswith(("\t", " ")):
            continue                                   # "<binary> (architecture ...):" headers
        lib = line.strip().split(" (compatibility", 1)[0].strip()
        if lib and not lib.startswith("@"):
            libs.append(lib)
    return sorted(set(libs))


def foreign_libraries(binary: str, own_roots: tuple[str, ...]) -> list[str] | None:
    """Libraries the binary (or a library of its own) loads from outside its own folders and the system's.
    None when this cannot be checked (no otool)."""
    if not os.access(OTOOL, os.X_OK):
        return None
    roots = tuple(r.rstrip("/") + "/" for r in own_roots)
    seen, todo, foreign = set(), [_real(binary)], []
    while todo and len(seen) < 64:
        b = todo.pop()
        if b in seen:
            continue
        seen.add(b)
        try:
            libs = _linked(b)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for lib in libs:
            if lib.startswith(SYSTEM_LIB_ROOTS):
                continue
            real = _real(lib)
            if real.startswith(roots) or lib.startswith(roots):
                todo.append(real)                      # its own library: check what that one loads
            else:
                foreign.append(lib)
    return sorted(set(foreign))


def _require_self_contained(name: str, binary: str, own_roots: tuple[str, ...], env_var: str) -> None:
    foreign = foreign_libraries(binary, own_roots)
    if foreign:
        raise ToolchainError(
            f"{name} at {binary} loads {foreign[0]}"
            + (f" (and {len(foreign) - 1} more)" if len(foreign) > 1 else "")
            + " from outside its own folder; the sealed sandbox does not let model code read there, so this "
            f"{name} cannot run inside it. Use a self-contained build "
            + ("(the official nodejs.org Node)" if name == "node" else "(a python.org or uv-managed CPython)")
            + f" and point {env_var} at it.")


def _python() -> Tool:
    exe = os.environ.get("EDAT_SIDEKICK_PYTHON") or sys.executable
    if not exe or not os.path.exists(exe):
        raise ToolchainError("no Python interpreter for model code (set EDAT_SIDEKICK_PYTHON)")
    probe = _run([exe, "-I", "-c",
                  "import sys, pytest; print(sys.version.split()[0]); print(sys.prefix); "
                  "print(sys.base_prefix); print(pytest.__version__); print(pytest.__file__)"])
    lines = probe.splitlines()
    if len(lines) < 5:
        raise ToolchainError("Python probe gave no answer")
    version, prefix, base, pytest_v, pytest_file = lines[:5]
    roots = {_real(prefix), _real(base), str(Path(_real(pytest_file)).parent.parent)}
    roots.add(str(Path(_real(exe)).parent.parent))
    # A venv's interpreter is a symlink into the base install: allow both.
    roots.add(str(Path(exe).parent.parent))
    _require_self_contained("python", _real(exe), tuple(sorted(roots)), "EDAT_SIDEKICK_PYTHON")
    return Tool("python", exe, f"{version} (pytest {pytest_v})", tuple(sorted(roots)), tuple(sorted(roots)))


def _node() -> Tool | None:
    exe = os.environ.get("EDAT_SIDEKICK_NODE") or shutil.which("node", path="/usr/local/bin:/opt/homebrew/bin:/usr/bin")
    if not exe:
        return None
    real = _real(exe)
    version = _run([real, "--version"])
    root = str(Path(real).parent.parent)
    # Self-contained installs keep the binary under <prefix>/bin and anything it loads next to it. A shared
    # prefix (/usr/local, Homebrew's) holds other software's files too: only the binary itself is granted.
    roots = (root,) if root not in SHARED_PREFIXES else (real,)
    _require_self_contained("node", real, roots, "EDAT_SIDEKICK_NODE")
    return Tool("node", real, version, roots, (real,))


def _git() -> Tool:
    exe = os.environ.get("EDAT_SIDEKICK_GIT")
    if not exe:
        try:
            exe = _run(["/usr/bin/xcrun", "--find", "git"])
        except (ToolchainError, FileNotFoundError, subprocess.TimeoutExpired):
            exe = shutil.which("git") or "/usr/bin/git"
    real = _real(exe)
    version = _run([real, "--version"])
    exec_path = _run([real, "--exec-path"])
    # Developer/usr holds git, its libexec helpers and its share/ templates.
    usr = str(Path(real).parent.parent)
    roots = (usr,) if usr not in SHARED_PREFIXES else (str(Path(exec_path)),)
    return Tool("git", real, version, roots, (real, _real(exec_path)))


@lru_cache(maxsize=1)
def find_toolchain() -> Toolchain:
    notes = []
    py = _python()
    if not py.version.startswith("3.13"):
        notes.append(f"python is {py.version.split()[0]}, spec pins 3.13")
    node = _node()
    if node is None:
        notes.append("node not found: JavaScript cases cannot run")
    git = _git()
    zsh = _run(["/bin/zsh", "--version"])
    return Toolchain(py, node, git, zsh, tuple(notes))


def wrapper_scripts(tc: Toolchain) -> dict[str, str]:
    """name -> script text for the trial's bin/ folder."""
    def w(path: str, extra: str = "") -> str:
        return f"#!/bin/zsh -f\n{extra}exec {_sh_quote(path)} \"$@\"\n"
    out = {"python3": w(tc.python.path), "python": w(tc.python.path),
           "python3.13": w(tc.python.path), "git": w(tc.git.path)}
    if tc.node is not None:
        out["node"] = w(tc.node.path)
    return out


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
