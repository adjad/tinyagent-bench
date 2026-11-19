"""The ceiling run's only way to the engine: one checked loopback address for reading, unloading, loading
and chatting, and the engine state saved before the run and put back on every way out.

- **Loopback only.** A base URL is accepted only as `http://127.0.0.1:<port>` or `http://[::1]:<port>`,
  with nothing else in it (no user or password, path, query or fragment). A host name is refused, even
  `localhost`, since it is resolved at call time. The check runs before any header is built, so oMLX's
  key never goes anywhere else. Redirects are not followed and proxy settings in the environment are not
  used, so a request cannot be passed on.
- **One engine.** Unloads, loads, status reads and chats all use the same checked address.
- **The engine state.** Before the first unload, the run saves which models are loaded and which are
  pinned, with this run's token. `restore` acts only on a state saved by the same run: one left in the
  folder by an earlier run is set aside under a dated name and never restored. Restore unloads what the
  run left loaded, loads back every chat model that was loaded (a pinned model among them), and checks
  that the pinned set is as it was (the run never changes it; a difference is reported). Helper models
  (embedders, rerankers) are never touched.
"""
from __future__ import annotations

import ipaddress
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
AUX = ("embed", "rerank", "bge", "e5-", "minilm", "colbert")
UNLOAD_WAIT_S = 30


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
    return {"loaded": sorted(m["id"] for m in rows if m.get("loaded") and m.get("id")),
            "pinned": sorted(m["id"] for m in rows if m.get("pinned") and m.get("id"))}


class EngineState:
    """The engine state saved by this run, in `<out>/resident-before.json`."""

    def __init__(self, out: Path, base_url: str):
        self.path = Path(out) / "resident-before.json"
        self.url = loopback_url(base_url)
        self.token = f"{time.time_ns()}"
        self.saved: dict | None = None

    def save_once(self, c) -> dict:
        """Save the state before this run's first change; an earlier run's file is set aside, never used."""
        if self.saved is not None:
            return self.saved
        if self.path.exists():
            self.path.rename(self.path.with_name(f"resident-before.stale-{time.strftime('%Y%m%d-%H%M%S')}.json"))
        self.saved = {**state_of(status(c)), "run_token": self.token, "base_url": self.url}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.saved))
        tmp.replace(self.path)
        return self.saved

    def keep(self, engine_id: str) -> list[str]:
        """Save (once), then unload every other loaded chat model. Returns what is still loaded besides it."""
        with client(self.url) as c:
            self.save_once(c)
            for m in state_of(status(c))["loaded"]:
                if m != engine_id and not is_aux(m):
                    _ok(c.post(f"/v1/models/{m}/unload"))     # 200, or 202 while its requests are aborted
            for _ in range(UNLOAD_WAIT_S):
                left = [m for m in state_of(status(c))["loaded"] if m != engine_id and not is_aux(m)]
                if not left:
                    break
                time.sleep(1)
            return left

    def restore(self) -> str:
        """Put back this run's saved state. Does nothing if this run changed nothing."""
        if self.saved is None:
            return "nothing to restore: this run changed nothing in the engine"
        on_disk = json.loads(self.path.read_text())
        if on_disk.get("run_token") != self.token:
            raise RuntimeError(f"{self.path} is not this run's saved state; not restoring it")
        want = [m for m in self.saved["loaded"] if not is_aux(m)]
        with client(self.url) as c:
            now = state_of(status(c))
            for m in now["loaded"]:
                if m not in want and not is_aux(m):
                    _ok(c.post(f"/v1/models/{m}/unload"))
            for m in want:
                if m not in now["loaded"]:
                    _ok(c.post(f"/v1/models/{m}/load"))
            after = state_of(status(c))
        if after["pinned"] != self.saved["pinned"]:
            return f"restored loaded models; the pinned set changed: {self.saved['pinned']} -> {after['pinned']}"
        missing = [m for m in want if m not in after["loaded"]]
        return f"restored: {want}" + (f" (not loaded again: {missing})" if missing else "")
