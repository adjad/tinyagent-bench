# The model adapter

Every part of EDAT that asks a model something (the agentic reference loop,
the output-quality tests, the vision and audio tests, the performance runner)
goes through one method, `chat()`, in `edat/models/`. A result means the same
thing whichever runtime produced it.

Status: version `edat-model-adapter/v1`. Three clients: a scripted stub, oMLX
(local, loopback only) and OpenRouter (the hosted cloud references). None of
them has been run against a real model or service; the oMLX and OpenRouter
clients are tested against fake servers only.

## The call

```python
from edat.models import OmlxClient, Sampling

client = OmlxClient("Gemma-Wisp", base_url="http://127.0.0.1:<omlx port>")
result = client.chat(
    messages,                  # OpenAI chat messages (system, user, assistant, tool)
    tools=schemas,             # OpenAI function tools, or None
    sampling=Sampling(temperature=1.0, top_p=0.95, top_k=64, source="author_recommended"),
    seed=1003,                 # or None
    max_tokens=512,            # required
    thinking=False,            # required: thinking-on and thinking-off are separate entries
)
```

`max_tokens` and `thinking` are keyword-only and required, so no caller can
forget the thinking mode that defines its result key.

### `Sampling`

| Field | Wire name | Notes |
| --- | --- | --- |
| `temperature`, `top_p`, `top_k`, `min_p`, `repetition_penalty`, `presence_penalty` | same | `None` means "not sent" |
| `source` | not sent | `author_recommended`, `fixed_sensitivity`, `provider_default`, `greedy_diagnostic` or `unspecified`; recorded on every row |

`FIXED_SENSITIVITY` is the fixed check every model also runs at:
temperature 0.6, top-p 0.95.

### `ChatResult`

| Field | What it holds |
| --- | --- |
| `text` | The assistant content exactly as returned. Leftover tool-call markup or thinking tags stay in it, so graders can label `text_tool_call`, `markup_leak` and `think_leak`. |
| `tool_calls` | A list of `ToolCall` (below), in emitted order. Several calls in one result are one model step (parallel calls). |
| `reasoning` | Separated reasoning text, when the runtime splits it (`reasoning_content` on oMLX, `reasoning` on OpenRouter). |
| `finish_reason` | As returned (`stop`, `tool_calls`, `length`, ...). `provider_rejected_tool_call` is EDAT's own (below). |
| `usage` | `prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `cached_prompt_tokens`, and `cost_usd` (hosted only). |
| `timing` | `ttft_s` (request sent to first generated token: content, reasoning or tool-call delta; `None` when not streaming) and `total_s`, measured by the client in seconds. |
| `raw` | The raw response body, or the list of stream chunks. Always kept: hosted output is not reproducible, so raw output is the record. |
| `request` | What was sent, without messages or secrets: model, message count, tool names, sampling with its source, seed (or `unsupported`), max tokens, thinking. |
| `meta` | Client facts: resolved model, upstream provider, parameters sent, accepted and omitted, request time (hosted). |

`result.assistant_message()` gives the message to append to the history,
with each call's raw argument text, so the model sees what it wrote.
`result.to_record()` gives a JSON-ready dict.

### `ToolCall`

| Field | What it holds |
| --- | --- |
| `id`, `name` | As returned |
| `arguments_raw` | The exact argument text the runtime returned. If a runtime returns an object instead of text, its JSON dump, with `raw_was_object` set. |
| `arguments` | The parsed object, or `None` |
| `parse_error` | Why parsing failed, or `None` |

Parsing never repairs anything. An empty string is an empty object. Text that
is not exactly one JSON object (truncated JSON, a list, a double-encoded
string, unquoted keys) is a parse error.

## Two rules

1. **A malformed tool call is the model's failure, never an engine fault.**
   The client returns it as a `ToolCall` with `parse_error` set and never
   raises for it. Graders label the trial `malformed` and count it. (Harbor's
   own client raised for it and the old scorer dropped the trial, which hid
   broken calls; spec 02 §1.6.) Schema-invalid arguments are also a format
   failure, judged by the grader against the tool's schema, not by the client.
2. **Only transport and engine faults raise**, as `EngineError(kind, detail)`
   with `kind` one of `transport`, `timeout`, `server`, `rate_limit`, `request`,
   `auth`. The harness retries once; a trial that still fails is excluded from
   every rate and counted. A run with more than 2% excluded trials is invalid
   (spec 02 §5.1). Set-up mistakes (a non-loopback oMLX address, a missing
   key, an unpinned provider) raise `ConfigurationError` before any request.

## The clients

### `ScriptedClient` and `NullClient` (`edat/models/stub.py`)

For tests and case validation. `ScriptedClient(turns)` replays a fixed list.
Each turn is `{"text", "calls": [{"name", "arguments"} or {"name",
"arguments_raw"}], "reasoning", "finish_reason"}`, or `{"engine_error":
"<kind>"}`, or a callable `(messages, tools, index) -> turn`. It records every
request in `.requests`. `NullClient` always answers "OK." with no calls: the
"do nothing" model that every EDAT case must fail under. Timings are zero.

### `OmlxClient` (`edat/models/omlx.py`)

- **Loopback only.** The base URL must be `http://127.0.0.1:<port>`,
  `http://localhost:<port>` or `http://[::1]:<port>`; anything else is refused.
- **Endpoint:** `POST /v1/chat/completions`, streaming by default (for time to
  first token) with `stream_options.include_usage`.
- **Thinking:** `chat_template_kwargs.enable_thinking`, the flag Toolbench uses.
- **Sampling and seed:** sent as given.
- **Tool calls:** parsed server-side by oMLX; the client keeps the argument
  text and parses it without repair.
- **Never manages models.** It does not load, unload or switch models. The run
  harness keeps one model resident at a time.
- `describe()` returns the run-key facts the caller passes in: runtime version,
  weights hash, quantisation, template hash, `tool_parser: omlx-server`.

### `OpenRouterClient` (`edat/models/openrouter.py`)

For the six unranked cloud references (spec 02 §4.4; the list and what each
accepts is in `edat/hosted/catalog.py`). Every request carries:

- `provider: {order: [<pinned provider>], allow_fallbacks: false,
  data_collection: "deny", require_parameters: true}`. The provider must be
  passed explicitly (`provider=`); one provider serves a whole run.
- `tool_choice: "auto"` and `parallel_tool_calls: true` with tools; never
  structured outputs.
- `usage: {include: true}`, so cost and tokens come back.
- `reasoning: {enabled: true}` (thinking on) or `{enabled: false}` (off); for a
  model that cannot switch reasoning off, `{effort: "minimal"}`, labelled
  "minimal reasoning".

Sampling and seed are omitted for models that do not accept them (Haiku 5.5
takes neither; GPT-6 Luna takes a seed but no sampling). The omission is
recorded in `meta.params_omitted`, the request record says `seed:
"unsupported"` and `sampling: {"source": "provider_default"}`.

The key is read from `OPENROUTER_API_KEY` (or the variable named by
`key_env=`) at request time. It is never stored on the client and never
appears in `request`, `meta`, `raw`, `describe()` or `repr()`. The client talks
only to `https://openrouter.ai/api/v1` (a test may point it at a loopback
fake).

`meta` carries the hosted run key: `requested_model`, `resolved_model` (the
`model` field returned), `resolved_model_differs` (flagged, never hidden),
`upstream_provider`, `params_sent`, `params_accepted` (with
`require_parameters`, everything sent was accepted; OpenRouter does not echo
applied values), `params_omitted`, `reasoning_entry`, `request_utc`, and
`efficiency_label: "not comparable with on-device numbers"`.

A provider error that rejects the model's own malformed call (an HTTP 400 or
422, or an in-band error, whose message is about tool-call or argument
parsing) comes back as a result with one `ToolCall` whose `parse_error`
holds the provider's message and `finish_reason: provider_rejected_tool_call`.
It is scored as `malformed`. Rate limits, outages and transport errors raise
`EngineError`.

Hosted runs send only the public development split (spec 02 §4.4). The guard
that refuses anything else lives with the runner (`edat/hosted/`), not in the
client, because the client never sees case ids.

## Image and audio parts

A message's `content` may be a list of OpenAI-style parts instead of a string:

```python
from edat.models import user_message, image_part, audio_part
msg = user_message("What does this say?", image_part("shot.png"), audio_part(wav_bytes, "wav"))
# {"role": "user", "content": [{"type": "text", ...},
#   {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
#   {"type": "input_audio", "input_audio": {"data": "<base64>", "format": "wav"}}]}
```

- `image_part(bytes | path, mime=None)` builds a data URL. `image_url_part(url)` is for hosted models only.
- `audio_part(bytes | path, fmt=None)` accepts wav, mp3, flac, ogg or m4a.
- `message_text(msg)` gives the text parts only.
- The stub (`ScriptedClient`, `NullClient`) records each media part in `requests[i]["media"]` as `{type, mime | format, bytes, sha256}`, and it stores messages with the base64 replaced by that summary. Scripted turns that are callables still receive the full messages.
- The oMLX and OpenRouter clients pass the list through unchanged. Whether a given server or model accepts images or audio is **untested**: no real server was called while building this.
- Tool messages stay strings in the reference loop. A loop that returns screenshots as observations (spec 11) puts the image in a user message after the tool result, because servers differ on media inside `role: tool`.

## Adding a runtime

Implement `describe()` and `chat()` with the same signature and the two rules
above. An OpenAI-compatible server can reuse `edat/models/openai_compat.py`
(`build_payload`, `run_request`, a `Transport`). Other runtimes are out of
scope for version 1 (spec 02 §4.3).

## The loop

The reference loop and its extension points (observation policy, per-case step
limit, call cap, stop tools, pre-run guard) are documented in `agent-loop.md`.

## Tests

`tests/test_edat_models_adapter.py` drives every client through an in-process
fake transport, plus one test that runs the real httpx transport against a
loopback HTTP server on a free port (it skips where binding a port is not
allowed).

```bash
.venv/bin/python -m pytest -q tests/test_edat_models_adapter.py
```
