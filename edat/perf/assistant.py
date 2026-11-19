"""The interface an in-assistant runner must provide to drive the simulated day (spec 04 §9.1,
§5.7; 02 §11.4 ``live`` mode).

The stand-in executor (:class:`edat.perf.simday.ReplayExecutor`) replays a teacher-forced
trajectory through the benchmark engine. To run the day's jobs through the real assistant
instead (its own planner, tools, retrieval and helper models), the in-assistant evaluation's runner
(`scripts/productbench`) implements :class:`AssistantSession`, and :class:`InAssistantExecutor` turns what it reports
into §3.6 samples on the simulated-day timetable.

What the in-assistant runner must implement
-------------------------------------------
``start(profile_id)``
    Bring the assistant up against the synthetic profile's world (``profile_id`` from the
    script), pointed at the **benchmark** oMLX instance only (never Harbor's or the user's own
    engine, ``FORBIDDEN_PORTS``). No real accounts, no outbound actions.
``run_job(job) -> dict``
    Run one scripted job to the end and return::

        {"completed": bool | None, "wall_s": float,
         "requests": [{"t_submit_ns", "t_first_token_ns", "t_first_answer_ns", "t_end_ns",
                       "prompt_tokens", "cached_tokens", "output_tokens", "aux": bool,
                       "kind": "chat" | "embeddings" | "rerank" (optional),
                       "model": <engine id> (optional)}]}

    ``job`` is ``{task_id, kind (02 kind), event_kind, budget_kind, family, ref, ref_missing,
    world_events_before, script_time_s, mode: "forced" | "live"}``; ``world_events_before``
    are the synthetic arrivals the world must add before the job runs.

    * Every timestamp is **nanoseconds on ``mach_continuous_time``**, the run clock
      (``edat.perf.clock.now_ns``), so requests line up with the power and footprint traces
      without an alignment step (§2.5, §4.3). Wall clock or ``time.monotonic`` values are wrong.
    * One entry per **model request** the job made, in order: every chat/completions call to
      the model under test, and every ``/v1/embeddings`` or reranker call.
    * ``aux: true`` for embedding and rerank requests (§5.7 rule 4), ``false`` for calls to the
      model under test. Their active energy is excluded from J per completed task and shown as
      ``aux_share``.
    * ``completed``: whether the job's outcome passed its check, or ``None`` when the runner
      cannot tell (the executor then falls back to the completion table, 02 §11.3).
    * ``wall_s``: end to end, from the job's start to its final answer (tool time follows the
      world's mocked latencies), for the §9.4 budget.
``loaded_models() -> list[str]``
    The engine's loaded-model ids right now (for ``aux_models_resident``, §5.7 rule 3).
``stop()``
    Shut the assistant down; leave the engine running.

The run's clock is not advanced by the executor: a real session takes real time. Energy
windows are opened and closed around ``run_job`` by :func:`edat.perf.simday.run_day`.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .record import blank_sample
from .simday import TaskResult, event_fields, lookup_completion

_NS = 1_000_000_000
INTERFACE = "edat.perf.assistant.AssistantSession"


@runtime_checkable
class AssistantSession(Protocol):
    def start(self, profile_id: str | None) -> None: ...
    def run_job(self, job: dict) -> dict: ...
    def loaded_models(self) -> list[str]: ...
    def stop(self) -> None: ...


def job_for(event: dict, mode: str = "forced") -> dict:
    keys = ("task_id", "kind", "event_kind", "budget_kind", "family", "ref", "ref_missing", "world_events_before",
            "script_time_s")
    return {**{k: event.get(k) for k in keys}, "mode": mode}


class InAssistantExecutor:
    """A :class:`edat.perf.simday.TaskExecutor` over an :class:`AssistantSession`."""

    name = "in-assistant"

    def __init__(self, session: AssistantSession, *, completion: dict | None = None, job_mode: str = "forced"):
        self.session = session
        self.completion = completion
        self.job_mode = job_mode

    def run(self, event: dict, runner, entry, mode: str, *, test: str = "sim_day_event") -> TaskResult:
        res = self.session.run_job(job_for(event, self.job_mode))
        under_test = entry.engine_id(mode)
        loaded = list(self.session.loaded_models() or ())
        aux_fp = runner.conditions_extra.get("aux_footprint_bytes") or {}
        resident = [{"id": m, "aux_footprint_bytes": aux_fp.get(m)} for m in loaded if m and m != under_test]
        samples, aux_ids = [], []
        for k, rq in enumerate(res.get("requests") or (), start=1):
            s = blank_sample(sample_id=runner._sid(), model_id=entry.id, mode=mode, entry_id=entry.entry_id,
                             test=test, phase="measured", round=0, rep=event.get("index", 0) + 1,
                             order_index=runner.w.next_order(), step=k, status="ok",
                             t_submit_ns=rq.get("t_submit_ns"), t_first_token_ns=rq.get("t_first_token_ns"),
                             t_first_answer_ns=rq.get("t_first_answer_ns"), t_end_ns=rq.get("t_end_ns"),
                             prompt_tokens=rq.get("prompt_tokens"), cached_tokens=rq.get("cached_tokens"),
                             output_tokens=rq.get("output_tokens"), aux=bool(rq.get("aux")),
                             aux_models_resident=resident,
                             energy_window_id=runner._window_id if runner.window_samples is not None else None,
                             executor=self.name, request_kind=rq.get("kind"), request_model=rq.get("model"),
                             **event_fields(event))
            if s["prompt_tokens"] is not None:
                s["new_prefill_tokens"] = s["prompt_tokens"] - (s["cached_tokens"] or 0)
            runner.w.sample(s)
            if runner.window_samples is not None:
                runner.window_samples.append(s["sample_id"])
            samples.append(s)
            if s["aux"]:
                aux_ids.append(s["sample_id"])
        completed = res.get("completed")
        if completed is None:
            completed = lookup_completion(self.completion, event)
        return TaskResult(task_id=event["task_id"], kind=event["budget_kind"], completed=completed,
                          sample_ids=[s["sample_id"] for s in samples], wall_s=res.get("wall_s"),
                          aux_sample_ids=aux_ids, samples=samples, executor=self.name)


class StubAssistantSession:
    """Placeholder until the in-assistant evaluation's runner implements the interface."""

    def _no(self, what: str):
        raise NotImplementedError(
            f"{what}: the in-assistant runner is not implemented yet. Provide an object implementing "
            f"{INTERFACE} (start, run_job, loaded_models, stop; timestamps in ns on mach_continuous_time, "
            "embedding and rerank requests tagged aux) — see the edat.perf.assistant module docstring.")

    def start(self, profile_id: str | None) -> None:
        self._no("start")

    def run_job(self, job: dict) -> dict:
        self._no("run_job")
        return {}

    def loaded_models(self) -> list[str]:
        self._no("loaded_models")
        return []

    def stop(self) -> None:
        self._no("stop")


class FakeAssistantSession:
    """Test double: each job is ``steps`` chat requests plus ``aux`` embedding requests, timed on
    a ``FakeClock`` it advances. All numbers are invented and deterministic."""

    def __init__(self, clock, *, model: str = "M", aux_models: tuple[str, ...] = ("aux-embedder",),
                 steps: int = 2, aux: int = 1, ttft_s: float = 0.4, decode_s: float = 1.0, aux_s: float = 0.05,
                 completed: Any = True, prompt_tokens: int = 1200, output_tokens: int = 50):
        self.clock = clock
        self.model = model
        self.aux_models = aux_models
        self.steps, self.aux = steps, aux
        self.ttft_s, self.decode_s, self.aux_s = ttft_s, decode_s, aux_s
        self.completed = completed
        self.prompt_tokens, self.output_tokens = prompt_tokens, output_tokens
        self.jobs: list[dict] = []
        self.started: str | None = None
        self.stopped = False

    def start(self, profile_id: str | None) -> None:
        self.started = profile_id or ""

    def run_job(self, job: dict) -> dict:
        self.jobs.append(job)
        t_start = self.clock.now_ns()
        reqs = []
        for _ in range(self.aux):
            t0 = self.clock.now_ns()
            self.clock.advance(self.aux_s)
            reqs.append({"t_submit_ns": t0, "t_first_token_ns": None, "t_first_answer_ns": None,
                         "t_end_ns": self.clock.now_ns(), "prompt_tokens": 40, "cached_tokens": 0,
                         "output_tokens": 0, "aux": True, "kind": "embeddings", "model": self.aux_models[0]})
        for k in range(self.steps):
            t0 = self.clock.now_ns()
            self.clock.advance(self.ttft_s)
            t1 = self.clock.now_ns()
            self.clock.advance(self.decode_s)
            reqs.append({"t_submit_ns": t0, "t_first_token_ns": t1, "t_first_answer_ns": t1,
                         "t_end_ns": self.clock.now_ns(), "prompt_tokens": self.prompt_tokens + 100 * k,
                         "cached_tokens": self.prompt_tokens if k else 0, "output_tokens": self.output_tokens,
                         "aux": False, "kind": "chat", "model": self.model})
        done = self.completed(job) if callable(self.completed) else self.completed
        return {"completed": done, "wall_s": (self.clock.now_ns() - t_start) / _NS, "requests": reqs}

    def loaded_models(self) -> list[str]:
        return [self.model, *self.aux_models]

    def stop(self) -> None:
        self.stopped = True
