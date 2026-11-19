"""Start one isolated server process for a productbench run: the backend or the sandbox.

    python -m scripts.productbench.serve backend --port 8775 --offset <s> \
        --user-name "Robin Vale" [--stub-script stub.json]
    python -m scripts.productbench.serve sandbox --port 8766 --offset <s>

What it adds around uvicorn, all inside this process and nothing in service/:

* the shifted wall clock (clock.py), so "now" is the profile's anchor;
* a socket guard: loopback only, and never Harbor's real ports (8765, the
  bundled engine's 8791); with --stub-script the engine port 8000 is refused too;
* a subprocess guard: nothing outside a short read-only allowlist runs, so the
  backend can never run `omlx-cli start/restart` against the real oMLX, open
  apps or run AppleScript; and service.main's oMLX start helpers report "CLI
  unavailable" as if it were not installed;
* the persona's name in place of the macOS account's (`id -F`), so the real
  owner's name never reaches a prompt or a captured answer;
* with --stub-script, the scripted engine (stub_engine.py).

Every refused connection or command is appended to <HARBOR_HOME>/../escapes.jsonl
so the runner can report it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.productbench import clock  # noqa: E402

REAL_PORTS = frozenset({8765, 8791})
ENGINE_PORT = 8000
ALLOWED_EXEC = frozenset({"id", "sysctl", "sw_vers", "uname", "memory_pressure", "vm_stat",
                          "ps", "pgrep", "lsof", "git"})
_escape_log: Path | None = None


def _escape(kind: str, detail: str) -> None:
    if _escape_log is None:
        return
    try:
        with _escape_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(), "kind": kind, "detail": detail}) + "\n")
    except OSError:
        pass


_cloud_ips: set[str] = set()


def denied_port(host: str, port: int, *, block_engine: bool,
                cloud_ips: set[str] | frozenset[str] = frozenset()) -> str | None:
    """Why a connection to (host, port) is refused, or None when it may go ahead.

    `cloud_ips` are the addresses the one pinned cloud host resolved to (a
    cloud-reference run): HTTPS to them is the only non-loopback traffic allowed."""
    if host in cloud_ips and port == 443:
        return None
    if host not in ("127.0.0.1", "::1", "localhost"):
        return "not loopback"
    if port in REAL_PORTS:
        return "a real Harbor port"
    if block_engine and port == ENGINE_PORT:
        return "the real engine port (stub run)"
    return None


def guard_network(*, block_engine: bool, cloud_host: str | None = None) -> None:
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else str(host)
        if name not in ("127.0.0.1", "::1", "localhost") and name != cloud_host:
            _escape("dns", name)
            raise socket.gaierror(f"productbench: lookup of {name!r} refused")
        rows = real_getaddrinfo(host, *args, **kwargs)
        if cloud_host and name == cloud_host:
            _cloud_ips.update(str(r[4][0]) for r in rows)
        return rows

    def check(sock, address):
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return
        why = denied_port(str(address[0]), int(address[1]), block_engine=block_engine,
                          cloud_ips=_cloud_ips)
        if why:
            _escape("network", f"{address[0]}:{address[1]} ({why})")
            raise PermissionError(f"productbench: connection to {address!r} refused: {why}")

    def connect(sock, address):
        check(sock, address)
        return real_connect(sock, address)

    def connect_ex(sock, address):
        check(sock, address)
        return real_connect_ex(sock, address)

    socket.socket.connect = connect          # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex    # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo         # type: ignore[assignment]


def _exe(args) -> str:
    first = args if isinstance(args, (str, bytes)) else (list(args)[0] if args else "")
    first = os.fsdecode(first) if isinstance(first, bytes) else str(first)
    return os.path.basename(first.split()[0]) if first.strip() else ""


def guard_subprocess(extra: frozenset[str] = frozenset()) -> None:
    """Refuse every subprocess outside ALLOWED_EXEC (plus `extra`)."""
    allowed = ALLOWED_EXEC | extra
    real_init = subprocess.Popen.__init__

    def popen_init(self, args, *a, **kw):
        exe = _exe(args)
        if kw.get("shell") or exe not in allowed:
            _escape("exec", exe)
            raise PermissionError(f"productbench: subprocess {exe!r} is blocked")
        return real_init(self, args, *a, **kw)

    subprocess.Popen.__init__ = popen_init   # type: ignore[method-assign]
    real_exec = asyncio.create_subprocess_exec

    async def create_exec(program, *args, **kw):
        exe = _exe([program])
        if exe not in allowed:
            _escape("exec", exe)
            raise PermissionError(f"productbench: subprocess {exe!r} is blocked")
        return await real_exec(program, *args, **kw)

    async def create_shell(cmd, *a, **kw):
        _escape("shell", str(cmd)[:60])
        raise PermissionError("productbench: shell subprocesses are blocked")

    asyncio.create_subprocess_exec = create_exec    # type: ignore[assignment]
    asyncio.create_subprocess_shell = create_shell  # type: ignore[assignment]


def _usage_fields(body: dict) -> dict:
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    out = {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")
           if usage.get(k) is not None}
    details = usage.get("completion_tokens_details") or {}
    if isinstance(details, dict) and details.get("reasoning_tokens") is not None:
        out["reasoning_tokens"] = details["reasoning_tokens"]
    for k in ("model", "provider"):
        if isinstance(body.get(k), str):
            out[k] = body[k]
    return out


def install_usage_tee(path: Path) -> None:
    """Record every chat-completion call the backend makes: when, how long, the
    sampling it sent (never the messages, never a header, so never a key) and the
    usage, resolved model and upstream provider the reply reported. Read-only: the
    bytes pass through unchanged."""
    import httpx
    from service.inference.attributed_transport import CredentialTransport
    real = CredentialTransport.handle_async_request

    async def handle(self, request):
        if not request.url.path.endswith("/chat/completions"):
            return await real(self, request)
        sent: dict = {}
        try:
            body = json.loads(request.content or b"{}")
            sent = {k: body.get(k) for k in ("model", "temperature", "top_p", "seed", "max_tokens",
                                             "stream", "reasoning", "reasoning_effort",
                                             "chat_template_kwargs") if k in body}
            sent["tools"] = len(body.get("tools") or [])
        except (ValueError, httpx.RequestNotRead, TypeError):
            pass
        started, wall = time.monotonic(), time.time()
        response = await real(self, request)
        upstream = response.stream
        record = {"at": wall, "host": request.url.host, "status": response.status_code, "sent": sent}

        class Tee(httpx.AsyncByteStream):
            async def __aiter__(self):
                first = None
                buf = b""
                got: dict = {}
                try:
                    async for chunk in upstream:
                        if first is None:
                            first = time.monotonic() - started
                        buf += chunk
                        yield chunk
                finally:
                    text = buf.decode("utf-8", "replace")
                    lines = [ln[6:] for ln in text.splitlines() if ln.startswith("data: ")] or [text]
                    for ln in lines:
                        try:
                            obj = json.loads(ln)
                        except ValueError:
                            continue
                        if isinstance(obj, dict):
                            got.update(_usage_fields(obj))
                    record.update({"first_byte_s": None if first is None else round(first, 3),
                                   "elapsed_s": round(time.monotonic() - started, 3), **got})
                    try:
                        with path.open("a", encoding="utf-8") as f:
                            f.write(json.dumps(record) + "\n")
                    except OSError:
                        pass

            async def aclose(self):
                await upstream.aclose()

        return httpx.Response(response.status_code, headers=response.headers, stream=Tee(),
                              request=request, extensions=response.extensions)

    CredentialTransport.handle_async_request = handle


def prepare_backend(*, user_name: str, stub_script: str | None, usage_log: Path | None = None) -> None:
    """Patches that need service.* imported. Runs after the clock and guards."""
    from service.memory import identity
    identity._full_name = user_name
    import service.main as main

    async def no_cli(*args, **kwargs):
        _escape("omlx-cli", " ".join(map(str, args)))
        return False

    main._omlx_cli = no_cli
    main._omlx_cli_wait = no_cli
    if stub_script:
        from scripts.productbench import stub_engine
        stub_engine.install(stub_engine.Script.load(stub_script), usage_log)
        # A stub model has no oMLX settings entry, so Harbor would budget it the
        # small default window and refuse ordinary prompts as too long.
        import service.config as config
        real_window = config.model_context_window
        config.model_context_window = lambda model: 32768
    if usage_log is not None:
        install_usage_tee(usage_log)


def main(argv: list[str] | None = None) -> None:
    global _escape_log
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("which", choices=("backend", "sandbox"))
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--offset", type=float, default=0.0, help="seconds added to the wall clock")
    ap.add_argument("--user-name", default="Test User")
    ap.add_argument("--stub-script", default=None)
    ap.add_argument("--cloud-host", default=None,
                    help="the one HTTPS host a cloud-reference backend may reach (openrouter.ai)")
    args = ap.parse_args(argv)
    if args.cloud_host and args.cloud_host != "openrouter.ai":
        sys.exit("cloud-reference runs go to openrouter.ai only")

    if args.port in REAL_PORTS or args.port == ENGINE_PORT:
        sys.exit(f"refusing port {args.port}: it belongs to the real Harbor or its engine")
    home = os.environ.get("HARBOR_HOME", "")
    real = (Path.home() / ".moe").resolve()
    if not home or Path(home).resolve() == real or real in Path(home).resolve().parents:
        sys.exit("refusing to start: HARBOR_HOME must be a scratch directory, never ~/.moe")
    if not os.environ.get("HARBOR_SANDBOX_HOME"):
        sys.exit("refusing to start: HARBOR_SANDBOX_HOME is not set")
    _escape_log = Path(home).resolve().parent / "escapes.jsonl"

    # Third-party libraries load first, on the real classes: pydantic.v1 subclasses
    # datetime.date with its own metaclass, which cannot combine with the shifted
    # clock's. Harbor's modules, imported after install(), get the shifted names.
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    import pydantic  # noqa: F401
    import uvicorn  # noqa: F401
    try:
        import pydantic.v1  # noqa: F401
    except ImportError:
        pass
    clock.install(args.offset)
    cloud_host = args.cloud_host if args.which == "backend" and not args.stub_script else None
    guard_network(block_engine=bool(args.stub_script) or bool(args.cloud_host), cloud_host=cloud_host)
    # Harbor reads the cloud token with /usr/bin/security when a request is made.
    guard_subprocess(frozenset({"security"}) if cloud_host else frozenset())
    if args.which == "backend":
        prepare_backend(user_name=args.user_name, stub_script=args.stub_script,
                        usage_log=Path(home).resolve().parent / "usage.jsonl")
        app = "service.main:app"
    else:
        app = "sandbox.server:app"
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=False,
                log_level="warning", lifespan="on")


if __name__ == "__main__":
    main()
