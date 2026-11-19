"""The ceiling's own engine checks: the strict loopback address and the read-only calls of `engine_check` and
the preflight. Everything that changes the engine in a run (the lock, the saved state, unloads and loads,
signals, Harbor, the restore) goes through the shared run guard, `edat/runtime/run_guard.py`.

- **Loopback only.** A base URL is accepted only as `http://127.0.0.1:<port>` or `http://[::1]:<port>`,
  with nothing else in it (no user or password, path, query or fragment). A host name is refused, even
  `localhost`, since it is resolved at call time. The check runs before any header is built, so oMLX's
  key never goes anywhere else. Redirects are not followed and proxy settings in the environment are not
  used, so a request cannot be passed on.
- **One address.** The run guard's `Engine` is built on the address this check returns, and the chats
  (`OmlxClient`) use the same one.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
AUX = ("embed", "rerank", "bge", "e5-", "minilm", "colbert")


class EngineAddressError(ValueError):
    """A base URL that is not plain http on a loopback address and port."""


def loopback_url(base_url: str) -> str:
    """The base URL, normalised, if it is plain http to 127.0.0.1 or ::1 with a port and nothing else."""
    try:
        p = urlsplit(base_url)
        port = p.port
    except ValueError as exc:
        raise EngineAddressError(f"not a valid engine address: {base_url!r}") from exc
    if p.scheme != "http":
        raise EngineAddressError(f"the engine address must be plain http, got {base_url!r}")
    if p.username is not None or p.password is not None or "@" in p.netloc:
        raise EngineAddressError("the engine address may not carry a user or password")
    if p.path not in ("", "/") or p.query or p.fragment:
        raise EngineAddressError("the engine address may hold only a host and port")
    host = p.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise EngineAddressError(f"the engine address must be 127.0.0.1 or [::1], not the name {host!r} "
                                 "(a name is resolved when the request is made)") from None
    if ip not in (ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")):
        raise EngineAddressError(f"the engine address must be 127.0.0.1 or [::1], not {host}")
    if port is None:
        raise EngineAddressError(f"the engine address must name a port, got {base_url!r}")
    return f"http://{'[::1]' if ip.version == 6 else '127.0.0.1'}:{port}"


def client(base_url: str, timeout: float = 120):
    """An httpx client to the checked address, with oMLX's key; no redirects, no proxy from the environment."""
    import httpx
    url = loopback_url(base_url)                       # before the key is read
    from scripts.productbench.engine_prep import _headers
    return httpx.Client(base_url=url, headers=_headers(), timeout=timeout, follow_redirects=False, trust_env=False)


def _ok(r):
    if r.is_redirect or r.status_code >= 400:
        raise RuntimeError(f"the engine answered {r.status_code} to {r.request.method} {r.request.url.path}")
    return r


def is_aux(model_id: str) -> bool:
    return any(a in model_id.lower() for a in AUX)


def status(c) -> list[dict]:
    return _ok(c.get("/v1/models/status")).json().get("models", [])


def listed(c) -> list[str]:
    return [m.get("id") for m in _ok(c.get("/v1/models")).json().get("data", [])]


def state_of(rows: list[dict]) -> dict:
    return {"loaded": sorted(m["id"] for m in rows if (m.get("loaded") or m.get("is_loading")) and m.get("id")),
            "pinned": sorted(m["id"] for m in rows if m.get("pinned") and m.get("id"))}
