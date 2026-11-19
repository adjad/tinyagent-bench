"""Sandbox self-test (spec 12 S11, the escape test that must pass before any
model-written code runs).

Every probe is harmless: it writes a marker file, reads a canary file, or
opens a socket to this Mac's loopback or to a TEST-NET address (192.0.2.1,
reserved for documentation, never routed). Each probe runs under the real
isolation and the harness checks the result from outside.

`require_sandbox()` runs it once per process and raises `SandboxUnavailable`
if any check fails, so the runner refuses to start instead of running
unsandboxed.
"""
from __future__ import annotations

import socket
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from edat.sidekick.sandbox.run import (Isolation, Limits, SandboxUnavailable, TrialArea,
                                       _group_procs, get_isolation)


@dataclass
class Probe:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class SelfTestReport:
    isolation: dict
    probes: list[Probe] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.probes) and all(p.passed for p in self.probes)

    def to_record(self) -> dict:
        return {"isolation": self.isolation, "passed": self.passed,
                "probes": [p.__dict__ for p in self.probes]}

    def text(self) -> str:
        lines = [f"sandbox self-test: {'PASS' if self.passed else 'FAIL'} ({self.isolation.get('isolation')})"]
        lines += [f"  [{'ok' if p.passed else 'FAIL'}] {p.name}{': ' + p.detail if p.detail else ''}" for p in self.probes]
        return "\n".join(lines)


class _Listener:
    """A loopback TCP listener that records whether anything connected."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.hits = 0
        self._stop = False
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
                self.hits += 1
                conn.close()
            except (socket.timeout, OSError):
                continue

    def close(self) -> None:
        self._stop = True
        self._t.join(timeout=1)
        self.sock.close()


def run_selftest(iso: Isolation | None = None) -> SelfTestReport:
    iso = iso or get_isolation()
    rep = SelfTestReport(iso.describe())
    lim = Limits(wall_s=10)
    with TrialArea() as area:
        outside_dir = area.root.parent / f"edat-sk-outside-{uuid.uuid4().hex[:8]}"
        outside_dir.mkdir()
        secret = outside_dir / "secret.txt"
        secret.write_text("EDATOUTSIDESECRET\n")
        try:
            # Positive controls: the sandbox must still let the toolchains work.
            r = iso.run(area, "print -r -- hello > inside.txt && cat inside.txt", limits=lim)
            rep.probes.append(Probe("write inside the work folder", r.ok and r.stdout.strip() == "hello",
                                    r.stderr.strip()[:120]))
            r = iso.run(area, "python3 -c 'print(6*7)'", limits=lim)
            rep.probes.append(Probe("python runs", r.ok and r.stdout.strip() == "42", r.stderr.strip()[:120]))
            r = iso.run(area, "python3 -c 'import pytest; print(pytest.__version__)'", limits=lim)
            rep.probes.append(Probe("pytest importable", r.ok, r.stderr.strip()[:120]))
            r = iso.run(area, "node -e 'console.log(6*7)'", limits=lim)
            rep.probes.append(Probe("node runs", r.ok and r.stdout.strip() == "42", r.stderr.strip()[:120]))
            r = iso.run(area, "git init -q r && cd r && print a > f && git add f && git commit -qm init && git rev-list --count HEAD", limits=lim)
            rep.probes.append(Probe("git runs", r.ok and r.stdout.strip() == "1", r.stderr.strip()[:120]))
            rep.probes.append(_git_grader_probe(iso))
            r = iso.run(area, "print -r -- $HOME; cat $HOME/.gitconfig | head -1", limits=lim)
            rep.probes.append(Probe("fake home in use", r.ok and r.stdout.splitlines()[0] == str(area.home), ""))

            # Writes outside the trial folder are denied.
            target = outside_dir / "escape.txt"
            r = iso.run(area, f"print -r -- x > {target}", limits=lim)
            rep.probes.append(Probe("shell write outside the trial is denied", not target.exists() and not r.ok,
                                    r.stderr.strip()[:120]))
            target2 = outside_dir / "escape2.txt"
            r = iso.run(area, f"python3 -c \"open('{target2}', 'w').write('x')\"", limits=lim)
            rep.probes.append(Probe("python write outside the trial is denied", not target2.exists() and not r.ok,
                                    r.stderr.strip().splitlines()[-1][:120] if r.stderr.strip() else ""))
            target3 = outside_dir / "escape3.txt"
            r = iso.run(area, f"node -e \"require('fs').writeFileSync('{target3}', 'x')\"", limits=lim)
            rep.probes.append(Probe("node write outside the trial is denied", not target3.exists() and not r.ok, ""))
            r = iso.run(area, f"cat {secret}", limits=lim)
            rep.probes.append(Probe("read of a file outside the trial is denied",
                                    "EDATOUTSIDESECRET" not in r.stdout and not r.ok, r.stderr.strip()[:120]))
            r = iso.run(area, f"ls {outside_dir}", limits=lim)
            rep.probes.append(Probe("listing a folder outside the trial is denied", "secret.txt" not in r.stdout, ""))

            # Network is denied: loopback (with a real listener) and an outside address.
            lis = _Listener()
            try:
                code = (f"import socket\ns=socket.socket()\ns.settimeout(2)\n"
                        f"try:\n  s.connect(('127.0.0.1',{lis.port})); print('CONNECTED')\n"
                        f"except Exception as e: print('BLOCKED', type(e).__name__)\n")
                (area.work / "net.py").write_text(code)
                r = iso.run(area, "python3 net.py", limits=lim)
                rep.probes.append(Probe("loopback TCP is denied", "BLOCKED" in r.stdout and lis.hits == 0,
                                        r.stdout.strip()[:80]))
                r = iso.run(area, f"node -e \"const s=require('net').connect({lis.port},'127.0.0.1');"
                                  f"s.on('connect',()=>{{console.log('CONNECTED');process.exit(0)}});"
                                  f"s.on('error',e=>{{console.log('BLOCKED',e.code);process.exit(0)}})\"", limits=lim)
                rep.probes.append(Probe("node loopback TCP is denied", "CONNECTED" not in r.stdout and lis.hits == 0,
                                        r.stdout.strip()[:80]))
            finally:
                lis.close()
            code = ("import socket\ns=socket.socket()\ns.settimeout(2)\n"
                    "try:\n  s.connect(('192.0.2.1',9)); print('CONNECTED')\n"
                    "except Exception as e: print('BLOCKED', type(e).__name__)\n"
                    "u=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
                    "try:\n  u.sendto(b'x', ('192.0.2.1',9)); print('SENT')\n"
                    "except Exception as e: print('UDPBLOCKED', type(e).__name__)\n")
            (area.work / "net2.py").write_text(code)
            r = iso.run(area, "python3 net2.py", limits=lim)
            rep.probes.append(Probe("outbound TCP and UDP are denied",
                                    "CONNECTED" not in r.stdout and "SENT" not in r.stdout and "BLOCKED" in r.stdout,
                                    r.stdout.strip().replace("\n", "; ")[:120]))
            r = iso.run(area, "python3 -c \"import socket; print(socket.getaddrinfo('example.invalid', 80))\"", limits=lim)
            rep.probes.append(Probe("name lookups fail", not r.ok, ""))

            # Environment is scrubbed.
            r = iso.run(area, "env", limits=lim)
            leaked = [k for k in ("SSH_AUTH_SOCK", "OPENROUTER_API_KEY", "EDAT_PRIVATE_DIR", "GITHUB_TOKEN")
                      if k + "=" in r.stdout]
            rep.probes.append(Probe("environment is scrubbed", r.ok and not leaked, ",".join(leaked)))

            # Limits hold.
            r = iso.run(area, "sleep 30", limits=Limits(wall_s=1.0))
            rep.probes.append(Probe("wall-clock limit kills the command", r.killed == "wall_clock" and r.wall_s < 5,
                                    f"{r.wall_s:.1f}s"))
            r = iso.run(area, "sleep 30 & print started", limits=Limits(wall_s=3.0))
            left = _group_procs(r.pid) if r.pid else []
            rep.probes.append(Probe("background children are stopped",
                                    r.wall_s < 5 and "started" in r.stdout and not left, f"{r.wall_s:.1f}s"))
            r = iso.run(area, "python3 -c \"print('x'*200000)\"", limits=Limits(wall_s=10, output_bytes=1024))
            rep.probes.append(Probe("output is capped", len(r.stdout) <= 1024 and r.stdout_truncated, str(len(r.stdout))))
            r = iso.run(area, "python3 -c \"open('big','wb').write(b'x'*(4*1024*1024))\"",
                        limits=Limits(wall_s=10, file_bytes=1024 * 1024))
            big = area.work / "big"
            rep.probes.append(Probe("file-size limit holds", not r.ok and (not big.exists() or big.stat().st_size <= 1024 * 1024), ""))
        finally:
            for p in outside_dir.iterdir():
                p.unlink()
            outside_dir.rmdir()
    return rep


def _git_grader_probe(iso: Isolation) -> "Probe":
    """The git grader, with the git it will use, reads a case's repository: one git case's reference answer
    must pass and its first wrong answer must fail. Otherwise the host's git would fail every git answer."""
    import random
    try:
        from edat.sidekick.gen.registry import get
        from edat.sidekick.graders import grade_answer
        from edat.sidekick.graders.git import git_version_text
        inst = get("git_unstage_file").build(random.Random("edat-selftest-git"))
        ok = grade_answer(inst, inst.references[0], iso=iso)
        bad = grade_answer(inst, inst.wrong[0], iso=iso)
        why = f"git {git_version_text()}" + ("" if ok.passed else f"; reference failed: {'; '.join(ok.reasons)[:120]}") \
            + ("" if not bad.passed else "; a wrong answer passed")
        return Probe("the git grader reads a case's repository", ok.passed and not bad.passed, why)
    except Exception as exc:  # noqa: BLE001 - the git or the toolchain is missing: the probe fails, with why
        return Probe("the git grader reads a case's repository", False, f"{exc.__class__.__name__}: {str(exc)[:160]}")


_PASSED: SelfTestReport | None = None


def require_sandbox() -> SelfTestReport:
    """Run the self-test once; raise SandboxUnavailable unless every probe passes."""
    global _PASSED
    if _PASSED is not None:
        return _PASSED
    rep = run_selftest()
    if not rep.passed:
        raise SandboxUnavailable("sandbox self-test failed; refusing to run model output\n" + rep.text())
    _PASSED = rep
    return rep
