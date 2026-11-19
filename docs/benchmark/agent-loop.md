# Agent loop: the reference loop and its extension points

`edat/loop.py` holds EDAT's reference loop, `edat-reference-v1` (spec 02 §2.1).
Other build chats (screen control, spec 11; sidekick coding, spec 12) reuse it
**without editing it**: everything they change is a setting or an object they
pass in. With no settings changed, the loop behaves exactly as spec 02 describes.

## The reference loop, per user turn

- At most `max_steps` model steps (8 by default).
- Every call in a step runs in the order emitted; each result goes back as a `role: tool` message.
- No nudges, re-prompts, dedupe, repairs or claim rewriting.
- Virtual per-tool timeout of 10 s; nothing waits.
- Results over 4,000 tokens (4 characters per token) are cut, with a note saying so.
- Malformed calls, unknown tools and tools not on the menu are answered with an error and never run.
- Tools that need confirmation go to the `Approver` first.
- An engine fault is retried once; a second fault excludes the trial.

```python
from edat.loop import LoopSettings, get_loop
trace = get_loop("reference").run_turn(client, env, messages, menu=tool_names,
                                       settings=LoopSettings(), approver=Approver())
```

`trace.stop` is one of `answer`, `empty`, `max_steps`, `engine_error`,
`max_calls` or `stop_tool`. `trace.steps` keeps every step's raw output, and
`trace.events` lists blocked calls.

## Extension points

| Need | How | Default |
|---|---|---|
| A different tool set | Pass your own `ToolEnvironment` (`edat/state/env.py`): `schemas(menu)`, `info(name) -> ToolInfo`, `call(name, args) -> ToolResult`, `system_prompt(...)`. Optional `format_error(name, message)` controls error text. | The assistant lane or the neutral lane `edat-generic-v1` |
| What the model sees of earlier results | `LoopSettings(observation=policy)`, where `policy(messages, step) -> list[dict]` returns the list sent this step. It must not mutate `messages`, because the stored transcript keeps every result in full. `keep_latest_results(stub, keep=1)` keeps the last result in full and stubs the earlier ones. | Everything in full |
| Per-case step budget | `LoopSettings(max_steps=per_case_step_limit(optimal_steps))`, which gives max(6, 2 × optimal) with a ceiling of 24. All three numbers are parameters. | 8 |
| Cap on executed calls per turn | `LoopSettings(max_calls=6)`. Calls past the cap get an error result (`outcome: call_limit`) and do not run, and the turn ends after that step with `stop: max_calls`. | No cap |
| Calls that end the turn | `LoopSettings(stop_tools=("finish",))`. The turn ends right after such a call runs, with `stop: stop_tool`. | None |
| Pre-run blocking (static pre-screen) | `LoopSettings(guard=fn)`, where `fn(name, args, step)` returns `None` to allow, or `(result_text, detail)` to block. A blocked call never runs and never reaches the approver; it is logged in `trace.events` with `outcome: blocked`. | None |
| A wholly different loop | Implement `AgentLoop` (`name`, `run_turn(...)`) and call `register_loop(name, factory)`. | — |

### Spec 11 (screen control)

```python
def screen_stub(msg, n):
    status = msg["content"].splitlines()[0]
    return f"{status}\n(screen after step {n + 1}: {title_of(msg)})"

settings = LoopSettings(observation=keep_latest_results(screen_stub),
                        max_steps=per_case_step_limit(case["optimal"]["steps"]),
                        stop_tools=("finish",))
```

The screenshot track puts its image in the tool or user message as an image part
(`edat.models.media.image_part`; see model-adapter.md). The observation
policy can stub old images the same way it stubs old text.

### Spec 12 (sidekick coding)

```python
settings = LoopSettings(max_calls=6, guard=prescreen)   # prescreen: rm -rf, curl | sh, ...
trace = get_loop().run_turn(client, DevToolEnv(sandbox), messages,
                            menu=["run_command", "read_file", "write_file", "run_tests"],
                            settings=settings)
```

## Restraint events

Specs 11 and 12 report violations to spec 02's Restraint panel as
`edat-restraint-event/v1` records:

```python
from edat.grading.aggregate import restraint_event, external_restraint_rows
ev = restraint_event("12", case_id, trial, model_key, board_version, "destructive",
                     {"rule": "rm-rf", "command": cmd})
rows = external_restraint_rows(events, trials={"11": n11, "12": n12})   # rows #69 and #70
```

The allowed kinds are `unsafe_action`, `unconfirmed_effect`, `destructive`,
`leak` and `injection_obeyed`. A guard block in `trace.events` is not itself an
event: the caller decides which blocks count and writes them with
`restraint_event`. Row #69b (screenshot track) is listed in the PR description as an open request.
