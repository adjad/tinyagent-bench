"""tinyagent-bench cloud_reference lane: OpenRouter models run Toolbench's hermetic turn.

Off unless `--engine openrouter` is given. A cloud run is its own kind of
result (kind "cloud_reference"): its own run directory, its own table, never ranked
with local models and never pooled into their statistics (policy kind is a
hard key; --compare and --baseline refuse to mix kinds even with
--allow-policy-mismatch).

HOW A CLOUD TURN RUNS. Through Harbor's own cloud mode, not a bench copy of
it: the isolated home gets exactly the profile Settings ▸ Cloud inference ▸
Everything writes (config.set_cloud_everything: a remote-only profile plus
remote_parity), so the Ask turn is model-led with Harbor's prompts, tools and
world fakes, and every production rule for OpenRouter applies unchanged:
payload validation (remote_policy.RemoteOnlyProfile.validate_payload), the
reasoning option (REASONING_EFFORT_MODELS), whole-reply fetching and
reasoning_details replay for REPLAY_REASONING_MODELS, latency-sorted provider
routing, Anthropic prompt-cache marks, the 429 retry.

WHAT THE BENCH ADDS, recorded on every row and in meta.json, each one a
narrow widening of the production validator for this model only:
  * sampling: the model's author settings, filtered to what CLOUD_FILE says
    the model accepts (production sends no sampling at all to the cloud);
  * seed, where accepted (per request, like the local lane);
  * the "off" thinking variant: CLOUD_FILE's `reasoning_off` option instead
    of production's reasoning option (a model with none has no off variant);
  * a provider pin (`provider.order`, no fallbacks, require_parameters) when
    CLOUD_FILE names one; otherwise production's latency routing, and the
    upstream provider that answered is recorded per request;
  * reasoning replay for a model CLOUD_FILE marks `replay_reasoning` that
    Harbor's list lacks (a reply's reasoning_details then go back as they do
    for the listed models).

LIMITS. Only the synthetic world is sent (the fakes; the isolated home).
Only the development split by default (CLOUD_SPLITS): held-out prompts never
leave the Mac unless --allow-heldout-to-cloud. The network guard opens exactly
one origin, https://openrouter.ai:443 (runner.allow_cloud_origin). The key
comes from the environment (KEY_ENV) or Harbor's Keychain item, is read once
before isolation, never printed, logged or stored, and scrubbed from
everything written (scrub). Every request is metered (UsageTap): resolved
model, upstream provider, tokens in / out / reasoning / cached, cost, time to
headers, first byte and end. --cost-budget is required for a live run and
refuses the next request once spent; --dry-run prints an estimate.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

ENGINE = "openrouter"
KIND = "cloud_reference"          # the results store's spelling (results-record.md)
HOST = "openrouter.ai"
ORIGIN = "https://openrouter.ai"          # == service.inference.providers.OPENROUTER_ORIGIN (tested)
API_PREFIX = "/api/v1"
PORT = 443
KEY_ENV = "TOOLBENCH_OPENROUTER_API_KEY"
# Harbor's Keychain service for inference keys (provider_credentials.SERVICE, tested).
KEYCHAIN_SERVICE = "com.harbor.inference"
CLOUD_FILE = Path(__file__).resolve().parent / "cloud_models.json"
VARIANTS = ("default", "off")
# The only split a cloud reference sends by default: development cases are
# public; gate and voice are held out of training and stay on the Mac.
CLOUD_SPLITS = ("dev",)
REDACTED = "[REDACTED]"
ACCEPTABLE = ("temperature", "top_p", "top_k", "seed")
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.I)
_SECRET_FIELD = re.compile(r"authorization|api[_-]?key|token|secret", re.I)

# Rough sizes for a cost estimate with no measured run (--cost-from): Harbor's
# system prompt and tool schemas are about 10k tokens a step.
EST_IN_PER_STEP = 10_000
EST_STEPS_PER_CASE = 2.5
EST_OUT_PER_STEP = 400
EST_REASONING_PER_STEP = 1_500


class KeyUnavailable(RuntimeError):
    """No OpenRouter key where the user said it would be. The message never holds one."""


class BudgetExceeded(RuntimeError):
    """The run's --cost-budget is spent: the next request is refused before it is sent."""


# ---- configuration -------------------------------------------------------

def load_models(path: str | Path = CLOUD_FILE) -> dict[str, dict]:
    """{model id: entry} from CLOUD_FILE, validated. Ids are exact OpenRouter
    ids: never a `:batch` variant or a `-latest` alias."""
    data = json.loads(Path(path).read_text())
    out: dict[str, dict] = {}
    for model, entry in data.items():
        if model.startswith("_"):
            continue
        if ":" in model or model.endswith("-latest") or "/" not in model:
            raise ValueError(f"{CLOUD_FILE.name}: {model!r} is not an exact, dated OpenRouter id")
        if not isinstance(entry, dict):
            raise ValueError(f"{CLOUD_FILE.name}: {model!r} must map to an object")
        if bad := sorted(set(entry.get("accepts") or ()) - set(ACCEPTABLE)):
            raise ValueError(f"{CLOUD_FILE.name}: {model!r} accepts unknown keys {bad}")
        price = entry.get("price_per_mtok") or {}
        if not all(isinstance(price.get(k), (int, float)) and price[k] >= 0 for k in ("in", "out")):
            raise ValueError(f"{CLOUD_FILE.name}: {model!r} needs price_per_mtok in/out")
        pin = entry.get("provider")
        if pin is not None and not (isinstance(pin, str) and pin):
            raise ValueError(f"{CLOUD_FILE.name}: {model!r} provider must be a slug or null")
        off = entry.get("reasoning_off")
        if off is not None and not (isinstance(off, dict) and set(off) <= {"effort", "enabled"}):
            raise ValueError(f"{CLOUD_FILE.name}: {model!r} reasoning_off must be {{effort}} / {{enabled}} or null")
        out[model] = entry
    return out


def entry_for(model: str, table: dict[str, dict]) -> dict:
    if model not in table:
        raise ValueError(f"{CLOUD_FILE.name} has no entry for {model!r}: add what it accepts, its price "
                         "and its reasoning-off option before running it")
    return table[model]


def sent_sampling(params: dict, entry: dict) -> tuple[dict, dict]:
    """(sent, dropped): the author sampling the model accepts, and what it would not."""
    accepts = set(entry.get("accepts") or ())
    sent = {k: v for k, v in (params or {}).items() if k in accepts}
    return sent, {k: v for k, v in (params or {}).items() if k not in accepts}


def provider_pin(entry: dict) -> dict | None:
    """The `provider` object pinning one upstream with no fallback, or None."""
    slug = entry.get("provider")
    if not slug:
        return None
    return {"order": [slug], "allow_fallbacks": False, "require_parameters": True}


def variant_blocked(entry: dict, variant: str, *, mandatory: bool | None = None) -> str | None:
    if variant not in VARIANTS:
        return f"cloud models run only the thinking variants {VARIANTS}"
    if variant == "off" and (entry.get("reasoning_off") is None or mandatory):
        return "this model's reasoning cannot be turned off (no reasoning_off option)"
    return None


def key_fields(plan_: dict) -> dict:
    """The hosted run key's fields for every row of a cloud model (spec §9 P1b.16)."""
    return {"requested_model": plan_["model"], "params_sent": plan_["sampling_sent"],
            "params_unsupported": sorted([*plan_["sampling_not_accepted"], *([] if plan_["seed"] else ["seed"])]),
            "reasoning_setting": plan_["reasoning"], "provider_routing": plan_["provider"]}


def plan(model: str, entry: dict, *, sampling: dict, harbor_reasoning: dict | None,
         replayed_by_harbor: bool) -> dict:
    """What a run sends this model, for meta.json and the report: production's
    part and the bench's additions, side by side."""
    sent, dropped = sent_sampling(sampling, entry)
    return {
        "model": model, "sampling_sent": sent, "sampling_not_accepted": dropped,
        "seed": "seed" in (entry.get("accepts") or ()),
        "reasoning": {"default": harbor_reasoning or "provider default (Harbor sends none)",
                      "off": entry.get("reasoning_off")},
        "provider": provider_pin(entry) or "OpenRouter routing {'sort': 'latency'} (Harbor's), not pinned",
        "replay_reasoning": {"harbor": replayed_by_harbor,
                             "bench_added": bool(entry.get("replay_reasoning")) and not replayed_by_harbor},
        "price_per_mtok": entry.get("price_per_mtok"), "price_source": entry.get("price_source"),
        "context_window": entry.get("context_window"),
    }


# ---- the key -------------------------------------------------------------

def keychain_account(name: str, base_url: str = ORIGIN) -> str:
    """Harbor's Keychain account for a provider key (provider_credentials.keychain_account)."""
    return f"{name}:{hashlib.sha256(base_url.encode('utf-8')).hexdigest()}"


def resolve_key(*, keychain: str = "", env: str = KEY_ENV, runner=subprocess.run) -> tuple[str, str]:
    """(key, where): from Harbor's Keychain item `keychain` (service
    com.harbor.inference, account <name>:<sha256 of https://openrouter.ai>) or
    from the environment variable the user set, which is then removed from this
    process's environment. Called before isolation; never prints the value."""
    if keychain:
        if not _NAME.fullmatch(keychain):
            raise KeyUnavailable("--cloud-keychain must be the Keychain item's name (letters, digits, - _)")
        try:
            r = runner(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                        "-a", keychain_account(keychain), "-w"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
                       env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise KeyUnavailable("Keychain did not answer") from None
        key = (r.stdout or b"").decode("ascii", "replace").rstrip("\n") if r.returncode == 0 else ""
        where = f"keychain:{keychain}"
    else:
        key, where = os.environ.pop(env, ""), f"env:{env}"
    if not key:
        raise KeyUnavailable(f"no OpenRouter key: set {env}, or pass --cloud-keychain <name> for the "
                             f"Keychain item Harbor saved (service {KEYCHAIN_SERVICE})")
    if len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise KeyUnavailable(f"the key from {where} is not a printable token")
    return key, where


def scrub(value, secrets=()):
    """`value` with every secret, bearer token and secret-named field replaced."""
    secrets = [s for s in secrets if s]
    if isinstance(value, str):
        for s in secrets:
            value = value.replace(s, REDACTED)
        return _BEARER.sub(r"\1" + REDACTED, value)
    if isinstance(value, dict):
        return {k: (REDACTED if isinstance(k, str) and _SECRET_FIELD.search(k) and isinstance(v, str)
                    else scrub(v, secrets)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(scrub(v, secrets) for v in value)
    return value


# ---- metering ------------------------------------------------------------

class Ledger:
    """Spend across the run. A request is refused before it is sent once
    spent >= budget; cost is OpenRouter's own `usage.cost`, or the price list's
    estimate when a response has none (marked)."""

    def __init__(self, budget: float | None, prices: dict[str, dict]):
        self.budget = budget
        self.prices = prices
        self.spent = 0.0
        self.by_model: dict[str, float] = {}
        self.requests = 0
        self.estimated = 0
        self.refused = 0

    def over(self) -> bool:
        return self.budget is not None and self.spent >= self.budget

    def check(self) -> None:
        if self.over():
            self.refused += 1
            raise BudgetExceeded(f"cost budget ${self.budget:.2f} spent (${self.spent:.4f}); request not sent")

    def charge(self, model: str, rec: dict) -> float:
        cost = rec.get("cost")
        if not isinstance(cost, (int, float)) or not math.isfinite(cost):
            p = (self.prices.get(model) or {}).get("price_per_mtok") or {}
            cost = ((rec.get("prompt_tokens") or 0) * p.get("in", 0)
                    + (rec.get("completion_tokens") or 0) * p.get("out", 0)) / 1e6
            rec["cost_estimated"] = True
            self.estimated += 1
        self.requests += 1
        self.spent += float(cost)
        self.by_model[model] = self.by_model.get(model, 0.0) + float(cost)
        return float(cost)

    def to_dict(self) -> dict:
        return {"budget_usd": self.budget, "spent_usd": round(self.spent, 6),
                "by_model_usd": {m: round(v, 6) for m, v in self.by_model.items()},
                "requests": self.requests, "estimated_costs": self.estimated, "refused": self.refused}


def usage_record(body: dict) -> dict:
    """The metered fields of one chat-completion body (or the last SSE chunk carrying them)."""
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    out_d = usage.get("completion_tokens_details") or {}
    in_d = usage.get("prompt_tokens_details") or {}
    return {"id": body.get("id"), "model": body.get("model"), "provider": body.get("provider"),
            "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": out_d.get("reasoning_tokens"), "cached_tokens": in_d.get("cached_tokens"),
            "cost": usage.get("cost")}


def parse_body(raw: bytes, *, streamed: bool) -> dict:
    """Merge the metered fields of a JSON body or an SSE stream (later chunks win)."""
    rec: dict = {}
    if not streamed:
        try:
            body = json.loads(raw)
        except ValueError:
            return {"parse_error": True}
        return usage_record(body) if isinstance(body, dict) else {"parse_error": True}
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.startswith("data:") or line[5:].strip() in ("", "[DONE]"):
            continue
        try:
            chunk = json.loads(line[5:])
        except ValueError:
            continue
        if isinstance(chunk, dict):
            for k, v in usage_record(chunk).items():
                if v is not None:
                    rec[k] = v
    return rec


def usage_tap(inner, *, sink, ledger: Ledger | None, cap: int = 16 * 1024 * 1024):
    """An httpx transport around `inner` that meters every chat-completions
    request: refuses one past the budget, then records resolved model, upstream
    provider, tokens, cost and timings via `sink(record)`. Bodies pass through
    unchanged; at most `cap` bytes of each are kept for parsing."""
    import httpx

    class TapStream(httpx.AsyncByteStream):
        def __init__(self, stream, done):
            self._stream, self._done, self._buf, self._first = stream, done, bytearray(), None

        async def __aiter__(self):
            async for chunk in self._stream:
                if self._first is None:
                    self._first = time.monotonic()
                if len(self._buf) < cap:
                    self._buf.extend(chunk[: cap - len(self._buf)])
                yield chunk

        async def aclose(self):
            try:
                await self._stream.aclose()
            finally:
                self._done(bytes(self._buf), self._first)

    class UsageTap(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            metered = request.url.path.endswith("/chat/completions")
            if not metered:
                return await inner.handle_async_request(request)
            if ledger is not None:
                ledger.check()
            try:
                sent = json.loads(request.content or b"{}")
            except ValueError:
                sent = {}
            streamed = bool(sent.get("stream"))
            t0 = time.monotonic()
            response = await inner.handle_async_request(request)
            t_headers = time.monotonic()
            closed = []

            def done(raw: bytes, first: float | None):
                if closed:
                    return
                closed.append(1)
                rec = {"requested_model": sent.get("model"), "status": response.status_code,
                       "streamed": streamed, "sent_reasoning": sent.get("reasoning"),
                       "sent_provider": sent.get("provider"),
                       "sent_sampling": {k: sent[k] for k in ACCEPTABLE if k in sent},
                       **parse_body(raw, streamed=streamed),
                       "t_headers_s": round(t_headers - t0, 3),
                       "t_first_byte_s": round(first - t0, 3) if first else None,
                       "t_total_s": round(time.monotonic() - t0, 3)}
                # Flagged, never refused: OpenRouter may answer with a dated slug.
                rec["resolved_differs"] = bool(rec.get("model")) and rec.get("model") != sent.get("model")
                if ledger is not None and response.status_code == 200:
                    rec["charged_usd"] = ledger.charge(str(sent.get("model") or ""), rec)
                sink(rec)

            return httpx.Response(response.status_code, headers=response.headers,
                                  stream=TapStream(response.stream, done), extensions=response.extensions)

        async def aclose(self):
            await inner.aclose()

    return UsageTap()


# ---- estimate --------------------------------------------------------------

def measured_sizes(rows: list[dict]) -> dict[str, tuple[float, float]]:
    """{case: (input tokens, output tokens)} per trial, from a finished local
    run's rows (Harbor's own prompt-size estimate on each step), averaged."""
    acc: dict[str, list[tuple[int, int]]] = {}
    for r in rows:
        steps = r.get("steps") or ()
        i = sum(int(s.get("prompt_tokens_est") or 0) for s in steps)
        o = sum(int(s.get("output_chars") or 0) for s in steps) // 4
        acc.setdefault(r["case"], []).append((i, o))
    return {c: (sum(x for x, _ in v) / len(v), sum(y for _, y in v) / len(v)) for c, v in acc.items()}


def estimate(models: list[str], cases: list[str], *, trials: int, variants: list[str],
             table: dict[str, dict], sizes: dict[str, tuple[float, float]] | None = None) -> dict:
    """Dry-run cost: per model and in total, from the price list and either
    measured prompt sizes (`sizes`) or the rough defaults above. Reasoning-on
    variants add EST_REASONING_PER_STEP output tokens a step. No caching."""
    out: dict = {"models": {}, "total_usd": 0.0, "source": "measured" if sizes else "rough defaults"}
    for m in models:
        e = entry_for(m, table)
        p = e["price_per_mtok"]
        usd, tin, tout = 0.0, 0.0, 0.0
        for v in variants:
            if variant_blocked(e, v):
                continue
            for c in cases:
                if sizes and c in sizes:
                    i, o = sizes[c]
                    steps = max(1.0, i / EST_IN_PER_STEP)
                else:
                    i, o, steps = (EST_IN_PER_STEP * EST_STEPS_PER_CASE,
                                   EST_OUT_PER_STEP * EST_STEPS_PER_CASE, EST_STEPS_PER_CASE)
                if v == "default":
                    o += EST_REASONING_PER_STEP * steps
                tin, tout = tin + i * trials, tout + o * trials
        usd = (tin * p["in"] + tout * p["out"]) / 1e6
        out["models"][m] = {"input_tokens": int(tin), "output_tokens": int(tout), "usd": round(usd, 2)}
        out["total_usd"] += usd
    out["total_usd"] = round(out["total_usd"], 2)
    return out
