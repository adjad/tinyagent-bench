"""Tests for the EDAT admin helper and its client. Fakes only: nothing here runs a command or needs root."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import signal
import socket
import stat
import tempfile
import threading
import time
from pathlib import Path

import pytest

from edat.perf import admin_client as ac
from edat.perf.constants import EDAT_16GB_WIRED_MB

HELPER_PATH = Path(__file__).resolve().parent.parent / "tools" / "edat_admin_helper" / "edat_admin_helper.py"
spec = importlib.util.spec_from_file_location("edat_admin_helper", HELPER_PATH)
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


class FakePopen:
    instances = []

    def __init__(self, argv, stdout=None):
        self.stdout_fd = stdout
        self.stdout_mode = os.fstat(stdout).st_mode if isinstance(stdout, int) else None
        self.argv, self.pid, self.signals, self.returncode = argv, 4242 + len(FakePopen.instances), [], None
        self.terminated = False
        FakePopen.instances.append(self)

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.returncode = -9


class FakeBalloon:
    log = []

    def __init__(self, nbytes):
        self.nbytes = nbytes
        FakeBalloon.log.append(("wire", nbytes))

    def release(self):
        FakeBalloon.log.append(("release", self.nbytes))


def _unix_bind_allowed() -> bool:
    base = tempfile.gettempdir()
    d = tempfile.mkdtemp(dir=base if len(base) < 50 else "/tmp", prefix="edatp")
    s = socket.socket(socket.AF_UNIX)
    try:
        s.bind(os.path.join(d, "t.sock"))
        return True
    except PermissionError:
        return False
    finally:
        s.close()
        shutil.rmtree(d, ignore_errors=True)


# Some sandboxes forbid binding any socket; the socket tests then skip instead of failing.
needs_unix_socket = pytest.mark.skipif(not _unix_bind_allowed(),
                                       reason="this environment forbids binding a Unix socket")


@pytest.fixture
def parent_dir():
    base = tempfile.gettempdir()  # AF_UNIX paths are limited to ~104 bytes: fall back to /tmp when $TMPDIR is long
    # realpath: the helper refuses links anywhere in --run-dir, and on macOS /tmp is a link.
    d = os.path.realpath(tempfile.mkdtemp(dir=base if len(base) < 50 else "/tmp", prefix="edat"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def rundir(parent_dir):
    """A run folder that does not exist yet: the helper creates it."""
    d = os.path.join(parent_dir, "run")
    assert len(os.path.join(d, "admin.sock")) < 100
    return d


@pytest.fixture(autouse=True)
def _reset():
    FakePopen.instances, FakeBalloon.log = [], []


def make(rundir, dry_run=False, original=5000, lifetime_s=None, run_rc=None, **kw):
    ran = []

    def run(argv):
        ran.append(list(argv))
        return (run_rc or {}).get(argv[0], 0)

    h = H.Helper(rundir, 6, dry_run, lifetime_s=lifetime_s, run=run, read_sysctl=lambda: original,
                 balloon_factory=FakeBalloon, popen=FakePopen, out=io.StringIO(), **kw)
    h.start()
    return h, ran


def req(h, verb, rid=1, **args):
    return h.handle_line(json.dumps({"id": rid, "verb": verb, "args": args}).encode())


def audit_lines(rundir):
    return [json.loads(x) for x in Path(rundir, "admin-audit.jsonl").read_text().splitlines()]


# ------------------------------------------------------------------ verbs run exactly the expected argv
def test_purge(rundir):
    h, ran = make(rundir)
    assert req(h, "purge")["ok"]
    assert ran == [["/bin/sync"], ["/usr/sbin/purge"]]


def test_purge_stops_when_sync_fails(rundir):
    h, ran = make(rundir, run_rc={"/bin/sync": 1})
    assert not req(h, "purge")["ok"]
    assert ran == [["/bin/sync"]]


@pytest.mark.parametrize("interval", [100, 250, 1000])
def test_powermetrics_start_stop(rundir, interval):
    h, _ = make(rundir)
    r = req(h, "powermetrics_start", interval_ms=interval)
    assert r["ok"]
    assert FakePopen.instances[0].argv == [
        "/usr/bin/powermetrics", "--samplers", "cpu_power,gpu_power,ane_power,thermal", "-i", str(interval),
        "-f", "plist"]                               # no output path: it writes to the descriptor we opened
    assert r["result"]["path"] == os.path.join(rundir, "powermetrics-1.plist")
    assert isinstance(FakePopen.instances[0].stdout_fd, int)
    assert stat.S_ISREG(FakePopen.instances[0].stdout_mode)
    st = os.lstat(os.path.join(rundir, "powermetrics-1.plist"))
    assert stat.S_ISREG(st.st_mode) and (st.st_mode & 0o777) == 0o600 and st.st_uid == os.getuid()
    assert isinstance(r["result"]["t_ns"], int) and r["result"]["t_wall_utc"].endswith("+00:00")
    assert req(h, "status")["result"]["powermetrics_running"]
    assert req(h, "powermetrics_stop")["ok"]
    assert FakePopen.instances[0].signals == [signal.SIGINT]
    assert not req(h, "status")["result"]["powermetrics_running"]


def test_powermetrics_one_instance_and_counter(rundir):
    h, _ = make(rundir)
    assert req(h, "powermetrics_start", interval_ms=250)["ok"]
    second = req(h, "powermetrics_start", interval_ms=250)
    assert not second["ok"] and len(FakePopen.instances) == 1
    req(h, "powermetrics_stop")
    again = req(h, "powermetrics_start", interval_ms=100)
    assert again["result"]["path"].endswith("powermetrics-2.plist")


def test_powermetrics_stop_without_start_signals_nothing(rundir):
    h, _ = make(rundir)
    assert not req(h, "powermetrics_stop")["ok"]
    assert FakePopen.instances == []


def test_wired_limit_set_and_reset(rundir):
    h, ran = make(rundir, original=12345)
    assert req(h, "wired_limit_set", mb=EDAT_16GB_WIRED_MB)["ok"]
    assert ran == [["/usr/sbin/sysctl", "iogpu.wired_limit_mb=10900"]]
    st = req(h, "status")["result"]
    assert (st["original_wired_limit_mb"], st["current_wired_limit_mb"]) == (12345, 10900)
    assert req(h, "wired_limit_reset")["ok"]
    assert ran[-1] == ["/usr/sbin/sysctl", "iogpu.wired_limit_mb=12345"]


def test_wired_limit_refused_when_original_unknown(rundir):
    h, ran = make(rundir, original=None)
    assert not req(h, "wired_limit_set", mb=10900)["ok"]
    assert ran == []


def test_balloon(rundir):
    h, ran = make(rundir)
    r = req(h, "balloon_wire", gib=10.5)
    assert r["ok"] and r["result"]["bytes"] == int(10.5 * 2**30)
    assert not req(h, "balloon_wire", gib=8)["ok"]  # one at a time
    assert req(h, "status")["result"]["balloon_bytes"] == int(10.5 * 2**30)
    assert req(h, "balloon_release")["ok"]
    assert not req(h, "balloon_release")["ok"]
    assert FakeBalloon.log == [("wire", int(10.5 * 2**30)), ("release", int(10.5 * 2**30))]
    assert ran == []


def test_status_fields(rundir):
    h, _ = make(rundir)
    st = req(h, "status")["result"]
    assert {"original_wired_limit_mb", "current_wired_limit_mb", "balloon_bytes", "powermetrics_running",
            "powermetrics_path", "uptime_s", "max_hours", "dry_run"} <= set(st)
    assert st["max_hours"] == 6 and st["dry_run"] is False


# ------------------------------------------------------------------ refusals
@pytest.mark.parametrize("verb,args", [
    ("rm", {}), ("purge", {"x": 1}), ("powermetrics_start", {"interval_ms": 50}),
    ("powermetrics_start", {}), ("powermetrics_start", {"interval_ms": "250"}),
    ("powermetrics_start", {"interval_ms": True}), ("powermetrics_start", {"interval_ms": 250, "o": "/etc/x"}),
    ("wired_limit_set", {"mb": 20000}), ("wired_limit_set", {"mb": "10900; reboot"}),
    ("balloon_wire", {"gib": 9}), ("balloon_wire", {"gib": 64}), ("status", {"a": 1}),
])
def test_refused_and_audited(rundir, verb, args):
    h, ran = make(rundir)
    r = req(h, verb, **args)
    assert r["ok"] is False and r["error"]
    assert ran == [] and FakePopen.instances == [] and FakeBalloon.log == []
    last = audit_lines(rundir)[-1]
    assert last["event"] == "request" and last["ok"] is False and last["verb"] == verb


@pytest.mark.parametrize("line", [b"not json", b"[1,2]", b'{"id": "a", "verb": "status"}',
                                  b'{"id": 1, "verb": 5}', b'{"id": 1, "verb": "status", "args": []}',
                                  b'{"id": 1, "verb": "status", "extra": 1}', b"\xff\xfe"])
def test_malformed_refused(rundir, line):
    h, ran = make(rundir)
    r = h.handle_line(line)
    assert r["ok"] is False and ran == []
    assert audit_lines(rundir)[-1]["ok"] is False


def test_audit_records_commands_and_clock(rundir):
    h, _ = make(rundir)
    req(h, "purge")
    rec = audit_lines(rundir)[-1]
    assert [c["argv"] for c in rec["commands"]] == [["/bin/sync"], ["/usr/sbin/purge"]]
    assert all(c["exit_code"] == 0 for c in rec["commands"])
    assert isinstance(rec["t_ns"], int) and rec["t_wall_utc"] and rec["clock"] in (
        "mach_continuous_time", "monotonic_ns_fallback")
    assert audit_lines(rundir)[0]["event"] == "start"


# ------------------------------------------------------------------ dry run
def test_dry_run_executes_nothing(rundir):
    h, ran = make(rundir, dry_run=True)
    for verb, args in [("purge", {}), ("powermetrics_start", {"interval_ms": 250}), ("wired_limit_set", {"mb": 10900}),
                       ("balloon_wire", {"gib": 8}), ("balloon_release", {}), ("powermetrics_stop", {}),
                       ("wired_limit_reset", {})]:
        assert req(h, verb, **args)["ok"], verb
    assert ran == [] and FakePopen.instances == [] and FakeBalloon.log == []
    out = h.out.getvalue()
    assert "would run: /bin/sync" in out and "would run: /usr/sbin/purge" in out
    assert "would run: /usr/bin/powermetrics --samplers cpu_power,gpu_power,ane_power,thermal -i 250" in out
    assert "would run: /usr/sbin/sysctl iogpu.wired_limit_mb=10900" in out
    assert audit_lines(rundir)[0]["dry_run"] is True


def test_dry_run_reports_balloon_size(rundir):
    h = H.Helper(rundir, 6, True, out=io.StringIO())
    h.start()
    assert req(h, "balloon_wire", gib=8)["result"] == {"bytes": 8 * 2**30, "wired": False}


# ------------------------------------------------------------------ startup checks
def test_run_dir_is_created_private(rundir):
    h, _ = make(rundir)
    st = os.lstat(rundir)
    assert stat.S_ISDIR(st.st_mode) and (st.st_mode & 0o777) == 0o700 and st.st_uid == os.getuid()
    a = os.lstat(os.path.join(rundir, "admin-audit.jsonl"))
    assert stat.S_ISREG(a.st_mode) and (a.st_mode & 0o777) == 0o600


@pytest.mark.parametrize("bad", ["relative/dir", "/a/../b", "/a/./b", "//double"])
def test_run_dir_must_be_plain_absolute(bad):
    with pytest.raises(H.Refusal):
        H.create_run_dir(bad, os.getuid(), os.getgid())


def test_existing_run_dir_refused(rundir):
    os.mkdir(rundir)
    with pytest.raises(H.Refusal, match="already exists"):
        make(rundir)


def test_missing_parent_refused(parent_dir):
    with pytest.raises(H.Refusal, match="does not exist"):
        make(os.path.join(parent_dir, "missing", "run"))


# ------------------------------------------------------------------ links are refused, never followed
def test_link_in_parent_path_refused(parent_dir):
    real = os.path.join(parent_dir, "real")
    os.mkdir(real)
    os.symlink(real, os.path.join(parent_dir, "linked"))
    with pytest.raises(H.Refusal, match="symbolic link"):
        make(os.path.join(parent_dir, "linked", "run"))
    assert os.listdir(real) == []                     # nothing was created through the link


def test_link_at_run_dir_refused(parent_dir):
    target = os.path.join(parent_dir, "elsewhere")
    os.mkdir(target)
    os.symlink(target, os.path.join(parent_dir, "run"))
    with pytest.raises(H.Refusal):
        make(os.path.join(parent_dir, "run"))
    assert os.listdir(target) == []


def test_planted_link_at_audit_log_refused(parent_dir, rundir):
    victim = os.path.join(parent_dir, "victim.txt")
    with open(victim, "w") as f:
        f.write("untouched")
    h = H.Helper(rundir, 6, False, run=lambda a: 0, read_sysctl=lambda: 5000, balloon_factory=FakeBalloon,
                 popen=FakePopen, out=io.StringIO())
    h.prepare()                                       # the folder exists; a link is planted before the first write
    os.symlink(victim, os.path.join(rundir, "admin-audit.jsonl"))
    with pytest.raises(H.Refusal, match="admin-audit.jsonl"):
        h.start()
    with open(victim) as f:
        assert f.read() == "untouched"
    assert os.lstat(victim).st_uid == os.getuid()


def test_planted_link_at_power_sample_file_refused(parent_dir, rundir):
    victim = os.path.join(parent_dir, "victim.plist")
    with open(victim, "w") as f:
        f.write("untouched")
    h, _ = make(rundir)
    os.symlink(victim, os.path.join(rundir, "powermetrics-1.plist"))
    r = req(h, "powermetrics_start", interval_ms=1000)
    assert not r["ok"] and "powermetrics-1.plist" in r["error"]
    assert FakePopen.instances == []                  # powermetrics was never started
    with open(victim) as f:
        assert f.read() == "untouched"
    assert any(x.get("event") == "refused" or x.get("ok") is False for x in audit_lines(rundir))


def test_run_dir_swapped_for_a_link_before_the_socket_is_made(parent_dir, rundir):
    h, _ = make(rundir)
    moved = os.path.join(parent_dir, "moved")
    os.rename(rundir, moved)
    os.symlink(moved, rundir)                         # the path now leads elsewhere through a link
    h.serve()                                         # refuses before bind: no socket anywhere
    assert not os.path.exists(os.path.join(moved, "admin.sock"))
    lines = [json.loads(x) for x in open(os.path.join(moved, "admin-audit.jsonl"))]
    assert any(x.get("event") == "refused" for x in lines)
    assert lines[-1]["event"] == "exit" and lines[-1]["reason"] == "refused"


def test_same_dir_and_safe_name():
    with pytest.raises(H.Refusal):
        H.safe_name("../x")
    with pytest.raises(H.Refusal):
        H.safe_name("a/b")
    assert H.safe_name("admin.sock") == "admin.sock"


def test_source_never_writes_or_chowns_by_following_a_path():
    src = Path(H.__file__).read_text()
    assert " open(" not in src.replace("os.open(", "")          # no builtin open of a path
    assert "os.chmod(" not in src
    for line in src.splitlines():
        if "os.chown(" in line:
            assert "follow_symlinks=False" in line and "dir_fd=" in line
        if "O_CREAT" in line:
            assert "O_EXCL" in line and "O_NOFOLLOW" in line


def test_main_refuses_non_root_and_missing_sudo_uid(rundir, capsys):
    assert H.main(["--run-dir", rundir], is_root=lambda: False, env={}) == 2
    assert H.main(["--run-dir", rundir], is_root=lambda: True, env={}) == 2
    assert H.main(["--run-dir", rundir], is_root=lambda: True,
                  env={"SUDO_UID": str(os.getuid() + 1), "SUDO_GID": "20"}) == 2  # dir owned by someone else
    assert "refused" in capsys.readouterr().err


@pytest.mark.parametrize("hours", [0, 1, 7, 12, 24])
def test_max_hours_only_6_or_18(rundir, hours):
    assert H.main(["--run-dir", rundir, "--max-hours", str(hours), "--dry-run"]) == 2
    with pytest.raises(H.Refusal):
        H.Helper(rundir, hours)


def test_max_hours_18_accepted(rundir):
    assert H.Helper(rundir, 18).lifetime_s == 18 * 3600.0


def test_print_plan_lists_every_verb(capsys):
    assert H.main(["--print-plan"]) == 0
    out = capsys.readouterr().out
    for verb in H.VERBS:
        assert verb in out
    assert "/usr/sbin/sysctl iogpu.wired_limit_mb=10900" in out and "/usr/bin/powermetrics" in out


def test_constant_matches_edat_constants():
    assert H.EDAT_16GB_WIRED_MB == EDAT_16GB_WIRED_MB


def test_source_has_no_shell_or_network():
    src = HELPER_PATH.read_text()
    for banned in ("shell=True", "AF_INET", "os.system", "os.popen", "import edat", "from edat"):
        assert banned not in src
    assert src.count("shell=False") >= 2


# ------------------------------------------------------------------ restore
def dirty(h):
    req(h, "powermetrics_start", interval_ms=250)
    req(h, "balloon_wire", gib=8)
    req(h, "wired_limit_set", mb=10900)


def steps_ran(rundir):
    return [r["step"] for r in audit_lines(rundir) if r["event"] == "restore_step" and r["did_something"]]


def test_restore_order_and_idempotence(rundir):
    h, ran = make(rundir, original=7777)
    dirty(h)
    first = h.restore("test")
    assert [s["step"] for s in first] == ["powermetrics_stop", "balloon_release", "wired_limit_reset"]
    assert all(s["ok"] and s["did_something"] for s in first)
    assert ran[-1] == ["/usr/sbin/sysctl", "iogpu.wired_limit_mb=7777"]
    n_ran = len(ran)
    second = h.restore("again")
    assert not any(s["did_something"] for s in second) and len(ran) == n_ran
    assert FakePopen.instances[0].signals == [signal.SIGINT]


def test_restore_continues_after_a_failed_step(rundir):
    h, ran = make(rundir, original=7777)
    dirty(h)
    FakePopen.instances[0].send_signal = lambda sig: (_ for _ in ()).throw(OSError("boom"))
    steps = h.restore("test")
    assert steps[0]["ok"] is False
    assert steps[1]["did_something"] and steps[2]["did_something"]
    assert ran[-1] == ["/usr/sbin/sysctl", "iogpu.wired_limit_mb=7777"]


@needs_unix_socket
def test_failed_set_still_restores(rundir):
    h, ran = make(rundir, original=7777, run_rc={"/usr/sbin/sysctl": 1})
    assert not req(h, "wired_limit_set", mb=10900)["ok"]
    h.restore("test")
    assert ran.count(["/usr/sbin/sysctl", "iogpu.wired_limit_mb=7777"]) == 1


def start_server(h):
    t = threading.Thread(target=h.serve, daemon=True)
    t.start()
    for _ in range(100):
        if os.path.exists(h.sock_path) and os.path.exists(h.audit_path) and "listening" in h.out.getvalue():
            break
        time.sleep(0.02)
    return t


class Conn:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(5)
        self.s.connect(path)
        self.f = self.s.makefile("rwb")

    def call(self, rid, verb, **args):
        self.f.write((json.dumps({"id": rid, "verb": verb, "args": args}) + "\n").encode())
        self.f.flush()
        return json.loads(self.f.readline())

    def raw(self, data):
        self.f.write(data)
        self.f.flush()
        return json.loads(self.f.readline())


@needs_unix_socket
def test_socket_mode_and_second_client_refused(rundir):
    h, _ = make(rundir)
    t = start_server(h)
    assert (os.stat(h.sock_path).st_mode & 0o777) == 0o600
    c = Conn(h.sock_path)
    assert c.call(1, "status")["ok"]
    with pytest.raises(OSError):  # listener closed after the first accept
        socket.socket(socket.AF_UNIX).connect(h.sock_path)
    assert c.raw(b"garbage\n")["ok"] is False
    c.f.close()
    c.s.close()
    t.join(5)
    assert not t.is_alive()


@needs_unix_socket
def test_restore_on_exit_verb(rundir):
    h, ran = make(rundir, original=7777)
    t = start_server(h)
    c = Conn(h.sock_path)
    c.call(1, "balloon_wire", gib=8)
    c.call(2, "wired_limit_set", mb=10900)
    r = c.call(3, "exit")
    assert r["ok"] and [s["step"] for s in r["result"]["restored"]] == [
        "powermetrics_stop", "balloon_release", "wired_limit_reset"]
    t.join(5)
    assert not t.is_alive() and not os.path.exists(h.sock_path)
    assert ran[-1] == ["/usr/sbin/sysctl", "iogpu.wired_limit_mb=7777"]
    assert audit_lines(rundir)[-1] == {**audit_lines(rundir)[-1], "event": "exit", "reason": "exit"}


@needs_unix_socket
def test_restore_on_client_disconnect(rundir):
    h, ran = make(rundir, original=7777)
    t = start_server(h)
    c = Conn(h.sock_path)
    c.call(1, "balloon_wire", gib=10.5)
    c.call(2, "wired_limit_set", mb=10900)
    c.f.close()
    c.s.close()
    t.join(5)
    assert not t.is_alive()
    assert FakeBalloon.log[-1][0] == "release" and ran[-1][-1] == "iogpu.wired_limit_mb=7777"
    assert audit_lines(rundir)[-1]["reason"] == "disconnect"


@needs_unix_socket
def test_restore_on_signal_handler(rundir):
    h, ran = make(rundir, original=7777)
    t = start_server(h)
    c = Conn(h.sock_path)
    c.call(1, "wired_limit_set", mb=10900)
    h.request_stop("SIGTERM")  # what the SIGTERM handler calls
    t.join(5)
    assert not t.is_alive() and ran[-1][-1] == "iogpu.wired_limit_mb=7777"
    assert audit_lines(rundir)[-1]["reason"] == "SIGTERM"


@needs_unix_socket
def test_restore_on_timeout(rundir):
    h, ran = make(rundir, original=7777, lifetime_s=0.6)
    t = start_server(h)
    c = Conn(h.sock_path)
    c.call(1, "wired_limit_set", mb=10900)
    t.join(5)
    assert not t.is_alive() and ran[-1][-1] == "iogpu.wired_limit_mb=7777"
    assert audit_lines(rundir)[-1]["reason"] == "timeout"


@needs_unix_socket
def test_timeout_before_any_client(rundir):
    h, _ = make(rundir, lifetime_s=0.3)
    t = start_server(h)
    t.join(5)
    assert not t.is_alive() and audit_lines(rundir)[-1]["reason"] == "timeout"


# ------------------------------------------------------------------ client
@needs_unix_socket
def test_client_round_trip_against_helper(rundir):
    h, ran = make(rundir, original=7777)
    t = start_server(h)
    client = ac.AdminClient(h.sock_path).connect()
    assert client.status()["original_wired_limit_mb"] == 7777
    client.purge()
    pm = client.powermetrics_start(250)
    assert pm["path"].endswith("powermetrics-1.plist")
    client.powermetrics_stop()
    client.wired_limit_set(10900)
    client.balloon_wire(8)
    assert client.status()["balloon_bytes"] == 8 * 2**30
    client.balloon_release()
    client.wired_limit_reset()
    with pytest.raises(ac.AdminError):
        client.balloon_wire(9)
    with pytest.raises(ac.AdminError):
        client.call("format_disk")
    client.exit()
    t.join(5)
    assert not t.is_alive()
    assert ["/bin/sync"] in ran


def test_client_unavailable_when_not_connected(rundir):
    with pytest.raises(ac.AdminUnavailable):
        ac.AdminClient(os.path.join(rundir, "nope.sock")).connect()
    with pytest.raises(ac.AdminUnavailable):
        ac.AdminClient(os.path.join(rundir, "nope.sock")).status()


def test_null_client_raises_for_every_verb():
    n = ac.NullAdminClient()
    calls = [n.purge, lambda: n.powermetrics_start(250), n.powermetrics_stop, lambda: n.wired_limit_set(10900),
             n.wired_limit_reset, lambda: n.balloon_wire(8), n.balloon_release, n.status, n.exit]
    for call in calls:
        with pytest.raises(ac.AdminUnavailable):
            call()
    assert issubclass(ac.AdminUnavailable, ac.AdminError)


def test_fake_client_records_calls():
    f = ac.FakeAdminClient(results={"status": {"balloon_bytes": 0}}, fail={"purge": "no"})
    f.balloon_wire(8)
    assert f.status() == {"balloon_bytes": 0}
    with pytest.raises(ac.AdminError):
        f.purge()
    assert f.calls == [("balloon_wire", {"gib": 8}), ("status", {}), ("purge", {})]


def test_check_wired_limit():
    assert ac.check_wired_limit(5000, read=lambda: 5000) is None
    warn = ac.check_wired_limit(5000, read=lambda: 10900)
    assert "10900" in warn and "5000" in warn
    assert "could not read" in ac.check_wired_limit(5000, read=lambda: None)
    assert ac.check_wired_limit(None, read=lambda: 5000) is None
