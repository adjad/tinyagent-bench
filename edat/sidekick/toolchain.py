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
    return Tool("python", exe, f"{version} (pytest {pytest_v})", tuple(sorted(roots)), tuple(sorted(roots)))


def _node() -> Tool | None:
    exe = os.environ.get("EDAT_SIDEKICK_NODE") or shutil.which("node", path="/usr/local/bin:/opt/homebrew/bin:/usr/bin")
    if not exe:
        return None
    real = _real(exe)
    version = _run([real, "--version"])
    root = str(Path(real).parent.parent)
    # Homebrew-style installs keep the binary under <prefix>/bin and libraries next to it.
    return Tool("node", real, version, (root,) if root not in ("/usr", "/") else (real,), (real,))


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
    roots = (usr,) if usr not in ("/usr", "/") else (str(Path(exec_path)),)
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
