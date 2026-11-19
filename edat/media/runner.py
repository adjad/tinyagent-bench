"""Run a model over vision and audio items and write ``edat.result/1.0`` records.

One runner for the media suites (spec 05; record contract
``docs/benchmark/results-record.md``, owned by the results store):

* ``run_vision``          V1 (and V5 re-renders): one record per question per trial
* ``run_transcription``   A1 speech (WER, CER, latency) and A5 non-speech (hallucination)
* ``run_spoken_commands`` A3 in ``two_pass`` (headline), ``native`` or ``pipeline``,
                          plus the typed twin for the typed-to-spoken gap

Rules applied to every record:

* **3 trials** in routine runs (``trials``), seeds ``base_seed + trial``.
* A model **without image or audio input** gets ``status: not_applicable`` with
  ``status_detail`` "no image input" / "no audio input" — never a zero. A model
  without audio input still runs two-pass spoken commands when a shared
  recogniser is given, and the record names that recogniser.
* Native one-call where the runtime does not accept audio with tools:
  ``unsupported_by_runtime``, "not supported".
* An engine or transport fault is retried once; if it fails again the trial is
  ``excluded`` (counted, never scored).
* A reply cut off by the token budget with no text is a failed item, never silence.
* Each request carries a unique prefix so a prompt cache cannot make repeats look fast.
* Raw output goes beside the record, content-addressed under ``raw/``.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from edat.media.adapter import capabilities_for, image_part, is_engine_error
from edat.media.audio import commands as cmd
from edat.media.audio.latency import (reference_endpoint_delay, spoken_command_latency,
                                      transcript_latency, user_perceived_latency)
from edat.media.audio.wav import Clip, to_wav_bytes
from edat.media.audio.wer import has_content, invented_words, score_utterance
from edat.media.vision.score import score_answer

RESULT_SCHEMA = "edat.result/1.0"
MAX_TOKENS = {"vision": 128, "reading": 700, "transcribe": 240, "agent": 512}


@dataclass
class RunConfig:
    model: dict                               # edat.result model block: id, role, weights, quant…
    runtime: dict                             # {"name": "omlx", "version": "0.7.3"}
    thinking: bool
    board_version: str
    harness_commit: str
    kind: str = "local"                       # local | cloud_reference
    device: dict | None = None
    sampling: Any = None                      # edat.models.Sampling (or None)
    sampling_source: str = "author_recommended"
    trials: int = 3
    base_seed: int = 1000
    split: str = "dev"
    capabilities: dict | None = None          # {"image", "audio", "audio_with_tools"}
    recogniser: dict | None = None            # {"id": ...} of the shared recogniser, if used
    max_tokens: dict = field(default_factory=lambda: dict(MAX_TOKENS))
    run_id: str = ""
    # The selection tier (spec 05 §17): core, extended or full. Every record carries it in
    # the results store's `selection` field (tier, manifest sha256, item n, 95 % half-width).
    tier_run: Any = None                      # edat.media.tiers.TierRun

    def __post_init__(self) -> None:
        if self.trials < 1:
            raise ValueError("trials must be at least 1")
        if not self.run_id:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
            self.run_id = f"{stamp}-{os.urandom(2).hex()}"


# ------------------------------------------------------------------ record sink

class JsonlSink:
    """A minimal run directory in the results store's layout.

    Writes ``records.jsonl``, ``raw/<sha256>.json`` and a small ``manifest.json``.
    When ``edat.results.writer.RunWriter`` is available, :func:`open_sink` uses it
    instead, which also validates every record against the schema.
    """

    def __init__(self, root: Path, run_id: str, *, tier: str, suite: str) -> None:
        self.dir = Path(root) / "runs" / run_id
        (self.dir / "raw").mkdir(parents=True, exist_ok=True)
        self.tier, self.suite, self.run_id = tier, suite, run_id
        self.records: list[dict] = []

    def put_raw(self, obj: Any) -> dict:
        data = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        sha = hashlib.sha256(data).hexdigest()
        path = self.dir / "raw" / f"{sha}.json"
        if not path.exists():
            path.write_bytes(data)
        return {"path": f"raw/{sha}.json", "sha256": sha, "bytes": len(data)}

    def append(self, record: dict) -> None:
        self.records.append(record)
        with (self.dir / "records.jsonl").open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        (self.dir / "manifest.json").write_text(json.dumps(
            {"schema": "edat.run-manifest/1.0", "run_id": self.run_id, "tier": self.tier,
             "suite": self.suite, "records": len(self.records)}, indent=1))

    def close(self) -> None:
        pass


@contextlib.contextmanager
def open_sink(root: Path, run_id: str, *, tier: str, suite: str):
    """The record sink for a run: the shared results store's ``RunWriter`` when it is
    importable (it validates every record and holds the run's lock), else a plain
    :class:`JsonlSink`. Use it as ``with open_sink(...) as sink:``."""
    try:
        from edat.results.writer import RunWriter  # type: ignore[import-not-found]
    except ImportError:
        sink = JsonlSink(root, run_id, tier=tier, suite=suite)
        try:
            yield sink
        finally:
            sink.close()
        return
    with RunWriter.open(root, run_id, tier=tier, suite=suite) as writer:
        yield writer


# ------------------------------------------------------------------ helpers

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nonce(run_id: str, case_id: str, trial: int) -> str:
    return hashlib.sha256(f"{run_id}|{case_id}|{trial}".encode()).hexdigest()[:12]


def _settings(cfg: RunConfig, seed: int | None) -> dict:
    s = cfg.sampling
    sampling = s.params() if hasattr(s, "params") else (dict(s) if isinstance(s, dict) else {})
    return {"thinking": "on" if cfg.thinking else "off", "sampling": sampling,
            "sampling_source": cfg.sampling_source, "seed": seed}


_TAG_BAD = re.compile(r"[^a-z0-9_-]+")


def _tag(value: object) -> str | None:
    """A tag in the results store's form (``^[a-z][a-z0-9_-]{0,63}$``), or None."""
    t = _TAG_BAD.sub("-", str(value).lower()).strip("-_")
    if t and not t[0].isalpha():
        t = "t" + t
    return t[:64] or None


def _clean_facets(facets: dict) -> dict:
    out = dict(facets)
    if "tags" in out:
        tags = [t for t in (_tag(x) for x in out["tags"]) if t]
        out["tags"] = list(dict.fromkeys(tags))[:32]
    return out


def base_record(cfg: RunConfig, *, tier: str, suite: str, case_id: str, trial: int,
                status: str, facets: dict, metrics: dict | None = None,
                status_detail: str | None = None, raw: dict | None = None,
                started_at: str | None = None) -> dict:
    rec = {
        "schema": RESULT_SCHEMA, "kind": cfg.kind, "tier": tier, "suite": suite,
        "run_id": cfg.run_id, "board_version": cfg.board_version, "lang": "en",
        "model": cfg.model, "runtime": cfg.runtime, "device": cfg.device,
        "settings": _settings(cfg, cfg.base_seed + trial),
        "harness_commit": cfg.harness_commit, "started_at": started_at or _now(),
        "case_id": case_id, "split": cfg.split, "trial": trial, "status": status,
        "facets": _clean_facets(facets), "metrics": dict(metrics or {}),
        "safety": {"unsafe_actions": None, "privacy_leaks": None, "labels": []},
        "raw": raw,
    }
    if cfg.tier_run is not None:
        rec["selection"] = cfg.tier_run.selection(facets.get("family", suite), facets.get("category"))
        rec["facets"]["tags"] = list(dict.fromkeys(
            rec["facets"].get("tags", []) + [f"tier-{cfg.tier_run.tier}"]))[:32]
    if cfg.device is None:
        rec["device_reason"] = "cloud" if cfg.kind == "cloud_reference" else "not recorded"
    if raw is None:
        rec["raw_reason"] = status_detail or status
    if status_detail:
        rec["status_detail"] = status_detail
    return rec


def call_with_retry(fn: Callable[[], Any]) -> tuple[Any, str | None]:
    """(result, None) or (None, error text) after one retry of an engine fault."""
    last = None
    for _ in range(2):
        try:
            return fn(), None
        except Exception as exc:  # only engine faults are retried and excluded
            if not is_engine_error(exc):
                raise
            last = f"{getattr(exc, 'kind', 'engine')}: {exc}"
    return None, last


def _truncated_empty(result) -> bool:
    return (getattr(result, "finish_reason", None) == "length"
            and not (getattr(result, "text", "") or "").strip()
            and not getattr(result, "tool_calls", None))


def _usage(result) -> tuple[int | None, int | None]:
    u = getattr(result, "usage", None)
    return getattr(u, "prompt_tokens", None), getattr(u, "completion_tokens", None)


# ------------------------------------------------------------------ vision

def run_vision(client, items: Iterable[tuple[dict, bytes]], cfg: RunConfig, sink, *,
               suite: str = "v1", no_image_prompt_tokens: int | None = None) -> list[dict]:
    """V1: ask every question of every item, ``cfg.trials`` times."""
    caps = capabilities_for(client, declared=cfg.capabilities)
    out = []
    for meta, png in items:
        for q in meta["questions"]:
            case_id = f"{meta['item_id']}/{q['qid']}"
            facets = {"family": suite, "category": meta["family"],
                      "tags": [meta["template"], q["qtype"], q["kind"],
                               meta["variant"].get("theme", ""), f"scale{meta['image']['scale']:g}"]}
            for trial in range(1, cfg.trials + 1):
                if not caps.image:
                    rec = base_record(cfg, tier="vision", suite=suite, case_id=case_id,
                                      trial=trial, status="not_applicable", facets=facets,
                                      status_detail="no image input")
                    sink.append(rec)
                    out.append(rec)
                    continue
                reading = q["kind"] == "reading"
                text = q["question"] if not reading else None
                prompt = score_prompt(q)
                messages = [{"role": "user", "content": [
                    image_part(png), {"type": "text",
                                      "text": f"[{_nonce(cfg.run_id, case_id, trial)}] {prompt}"}]}]
                started = _now()
                result, err = call_with_retry(lambda: client.chat(
                    messages, tools=None, sampling=cfg.sampling, seed=cfg.base_seed + trial,
                    max_tokens=cfg.max_tokens["reading" if reading else "vision"],
                    thinking=cfg.thinking))
                if result is None:
                    rec = base_record(cfg, tier="vision", suite=suite, case_id=case_id,
                                      trial=trial, status="excluded", facets=facets,
                                      status_detail=err, started_at=started)
                    sink.append(rec)
                    out.append(rec)
                    continue
                s = score_answer(q, result.text, default_year=meta.get("default_year"),
                                 reading_text=meta.get("reading_text", ""))
                pt, ct = _usage(result)
                metrics = {"pass": s.passed, "score": s.score, "prompt_tokens": pt,
                           "completion_tokens": ct, "ttft_s": result.timing.ttft_s,
                           "total_s": result.timing.total_s,
                           "image_tokens": (pt - no_image_prompt_tokens)
                           if (pt is not None and no_image_prompt_tokens is not None) else None}
                if q["kind"] == "field":
                    metrics["field_exact"] = s.passed
                elif q["kind"] == "anls":
                    metrics["anls"] = s.score
                elif q["kind"] == "relaxed":
                    metrics["relaxed_ok"] = s.passed
                elif q["kind"] == "reading":
                    metrics["cer"] = s.cer
                else:
                    metrics["false_answer"] = s.false_answer
                    metrics["abstained"] = s.abstained
                detail = None
                if _truncated_empty(result):
                    metrics["pass"], metrics["score"] = False, 0.0
                    detail = "truncated_empty"
                raw = sink.put_raw({"case_id": case_id, "output": result.to_record(),
                                    "score": s.to_json(), "text": text})
                rec = base_record(cfg, tier="vision", suite=suite, case_id=case_id, trial=trial,
                                  status="ok", facets=facets, metrics=metrics,
                                  status_detail=detail, raw=raw, started_at=started)
                sink.append(rec)
                out.append(rec)
    return out


def _image_case(client, cfg: RunConfig, sink, *, suite: str, case_id: str, facets: dict,
                messages_for: Callable[[str], list[dict]], max_tokens: int,
                score: Callable[[str], dict], out: list[dict]) -> None:
    """One image request ``cfg.trials`` times: not_applicable, excluded or scored."""
    caps = capabilities_for(client, declared=cfg.capabilities)
    for trial in range(1, cfg.trials + 1):
        if not caps.image:
            rec = base_record(cfg, tier="vision", suite=suite, case_id=case_id, trial=trial,
                              status="not_applicable", facets=facets,
                              status_detail="no image input")
            sink.append(rec)
            out.append(rec)
            continue
        messages = messages_for(f"[{_nonce(cfg.run_id, case_id, trial)}] ")
        started = _now()
        result, err = call_with_retry(lambda: client.chat(
            messages, tools=None, sampling=cfg.sampling, seed=cfg.base_seed + trial,
            max_tokens=max_tokens, thinking=cfg.thinking))
        if result is None:
            rec = base_record(cfg, tier="vision", suite=suite, case_id=case_id, trial=trial,
                              status="excluded", facets=facets, status_detail=err,
                              started_at=started)
            sink.append(rec)
            out.append(rec)
            continue
        s = score(result.text)
        pt, ct = _usage(result)
        metrics = {"pass": bool(s["passed"]), "score": 1.0 if s["passed"] else 0.0,
                   "prompt_tokens": pt, "completion_tokens": ct,
                   "ttft_s": result.timing.ttft_s, "total_s": result.timing.total_s}
        for k in ("abs_error", "iou", "false_answer", "abstained", "proposed", "no_proposal_ok"):
            if s.get(k) is not None:
                metrics[k] = s[k]
        detail = None
        if _truncated_empty(result):
            metrics["pass"], metrics["score"] = False, 0.0
            detail = "truncated_empty"
        raw = sink.put_raw({"case_id": case_id, "output": result.to_record(), "score": s})
        rec = base_record(cfg, tier="vision", suite=suite, case_id=case_id, trial=trial,
                          status="ok", facets=facets, metrics=metrics, status_detail=detail,
                          raw=raw, started_at=started)
        sink.append(rec)
        out.append(rec)


def _mime(fmt: str) -> str:
    return {"jpeg": "image/jpeg", "jpg": "image/jpeg"}.get(fmt, "image/png")


def run_reasoning(client, items: Iterable[tuple[dict, list[bytes]]], cfg: RunConfig, sink
                  ) -> list[dict]:
    """V7: one question per item, with 1–4 images in one request."""
    from edat.media.vision.reasoning import score_question
    out: list[dict] = []
    for meta, blobs in items:
        q = meta["question"]
        facets = {"family": "v7", "category": meta["skill"],
                  "tags": [meta["template"], q["kind"], f"images{len(blobs)}"]}

        def messages_for(prefix, blobs=blobs, meta=meta):
            return [{"role": "user", "content": [*(image_part(b) for b in blobs),
                                                 {"type": "text", "text": prefix + meta["prompt"]}]}]
        _image_case(client, cfg, sink, suite="v7", case_id=meta["item_id"], facets=facets,
                    messages_for=messages_for, max_tokens=cfg.max_tokens["vision"],
                    score=lambda text, q=q, meta=meta: score_question(
                        q, text, default_year=meta.get("default_year")), out=out)
    return out


def run_robustness(client, items: Iterable[tuple[dict, bytes]], cfg: RunConfig, sink
                   ) -> list[dict]:
    """V5: the base item's questions under one condition (``facets.category`` = condition)."""
    out: list[dict] = []
    for meta, data in items:
        for q in meta["questions"]:
            facets = {"family": "v5", "category": meta["condition"],
                      "tags": [meta["family"], meta["group"], q["kind"]]}

            def messages_for(prefix, data=data, meta=meta, q=q):
                return [{"role": "user", "content": [
                    image_part(data, _mime(meta["image"]["format"])),
                    {"type": "text", "text": prefix + score_prompt(q)}]}]

            def score(text, q=q, meta=meta):
                s = score_answer(q, text, default_year=meta.get("default_year"))
                return s.to_json()
            _image_case(client, cfg, sink, suite="v5", case_id=f"{meta['item_id']}/{q['qid']}",
                        facets=facets, messages_for=messages_for,
                        max_tokens=cfg.max_tokens["vision"], score=score, out=out)
    return out


def run_actions(client, items: Iterable[tuple[dict, bytes]], cfg: RunConfig, sink
                ) -> list[dict]:
    """V3: Harbor's own vision request; the reply's proposal is scored by Harbor's parser."""
    from edat.media.vision import actions
    out: list[dict] = []
    for meta, data in items:
        facets = {"family": "v3", "category": meta["template"], "tags": [meta["case"]]}

        def messages_for(prefix, meta=meta, data=data):
            msgs = actions.harbor_messages(meta, data)
            parts = msgs[1]["content"]
            parts[-1] = {"type": "text", "text": prefix + parts[-1]["text"]}
            return msgs
        _image_case(client, cfg, sink, suite="v3", case_id=meta["item_id"], facets=facets,
                    messages_for=messages_for, max_tokens=cfg.max_tokens["reading"],
                    score=lambda text, meta=meta: actions.score_reply(meta, text), out=out)
    return out


def score_prompt(q: dict) -> str:
    from edat.media.vision.items import ANSWER_INSTRUCTION, READING_INSTRUCTION
    if q["kind"] == "reading":
        return READING_INSTRUCTION
    return f"{ANSWER_INSTRUCTION}\nQuestion: {q['question']}"


# ------------------------------------------------------------------ transcription (A1, A5)

@dataclass
class Utterance:
    utt_id: str
    clip: Clip
    reference: str | None             # None for a non-speech clip (A5)
    tags: list[str] = field(default_factory=list)


def run_transcription(client, utts: Iterable[Utterance], cfg: RunConfig, sink, *,
                      suite: str = "a1") -> list[dict]:
    caps = capabilities_for(client, declared=cfg.capabilities)
    out = []
    for u in utts:
        speech = u.reference is not None
        fam = suite if speech else "a5"
        facets = {"family": fam, "tags": list(u.tags) + [u.clip.meta.get("condition", {}).get("type", "clean")]}
        for trial in range(1, cfg.trials + 1):
            if not caps.audio:
                rec = base_record(cfg, tier="audio", suite=fam, case_id=u.utt_id, trial=trial,
                                  status="not_applicable", facets=facets,
                                  status_detail="no audio input")
                sink.append(rec)
                out.append(rec)
                continue
            msgs = cmd.transcribe_messages(u.clip, nonce=_nonce(cfg.run_id, u.utt_id, trial))
            started = _now()
            result, err = call_with_retry(lambda: client.chat(
                msgs, tools=None, sampling=cfg.sampling, seed=cfg.base_seed + trial,
                max_tokens=cfg.max_tokens["transcribe"], thinking=cfg.thinking))
            if result is None:
                rec = base_record(cfg, tier="audio", suite=fam, case_id=u.utt_id, trial=trial,
                                  status="excluded", facets=facets, status_detail=err,
                                  started_at=started)
                sink.append(rec)
                out.append(rec)
                continue
            lat = transcript_latency(result, u.clip.duration_s)
            pt, ct = _usage(result)
            metrics: dict[str, Any] = {"ttft_s": lat.ttft_audio_s,
                                       "time_to_final_s": lat.time_to_final_s, "rtf": lat.rtf,
                                       "rtfx": lat.rtfx, "audio_s": round(u.clip.duration_s, 4),
                                       "prompt_tokens": pt, "completion_tokens": ct}
            detail = None
            truncated = _truncated_empty(result)
            if speech:
                us = score_utterance(u.reference, result.text or "")
                metrics.update({"wer": us.wer, "cer": us.cer, "score": max(0.0, 1.0 - us.wer),
                                "ref_words": us.words.ref_len, "word_errors": us.words.errors,
                                "substitutions": us.words.substitutions,
                                "deletions": us.words.deletions, "insertions": us.words.insertions,
                                "ref_chars": us.chars.ref_len, "char_errors": us.chars.errors})
                norm = {"ref_norm": us.ref_norm, "hyp_norm": us.hyp_norm}
            else:
                hallucinated = has_content(result.text or "")
                metrics.update({"pass": not hallucinated, "hallucinated": hallucinated,
                                "invented_words": invented_words(result.text or "")})
                norm = {}
            if truncated:
                metrics["pass"] = False
                metrics["score"] = 0.0
                detail = "truncated_empty"
            raw = sink.put_raw({"case_id": u.utt_id, "output": result.to_record(),
                                "reference": u.reference, **norm, "clip": u.clip.meta})
            rec = base_record(cfg, tier="audio", suite=fam, case_id=u.utt_id, trial=trial,
                              status="ok", facets=facets, metrics=metrics, status_detail=detail,
                              raw=raw, started_at=started)
            sink.append(rec)
            out.append(rec)
    return out


# ------------------------------------------------------------------ spoken commands (A3)

def run_spoken_commands(client, cases: Iterable[tuple[cmd.CommandCase, Clip]], cfg: RunConfig,
                        sink, *, matcher: cmd.CommandMatcher, configuration: str = "two_pass",
                        recogniser=None, typed_twin: bool | None = None) -> list[dict]:
    """A3 for every (case, clip). The typed twin runs with the two-pass headline by default."""
    if typed_twin is None:
        typed_twin = configuration == "two_pass"
    caps = capabilities_for(client, declared=cfg.capabilities)
    out = []
    for case, clip in cases:
        facets = {"family": "a3", "test": configuration,
                  "tags": list(case.tags) + [clip.meta.get("condition", {}).get("type", "clean"),
                                             clip.meta.get("voice", "")]}
        endpoint = reference_endpoint_delay(clip)
        for trial in range(1, cfg.trials + 1):
            seed = cfg.base_seed + trial
            nonce = _nonce(cfg.run_id, case.case_id + configuration, trial)
            status, detail = "ok", None
            rec_client = None
            if configuration == "native":
                if not caps.audio:
                    status, detail = "not_applicable", "no audio input"
                elif caps.audio_with_tools is False:
                    status, detail = "unsupported_by_runtime", "not supported"
            elif configuration == "two_pass":
                if not caps.audio:
                    if recogniser is None:
                        status, detail = "not_applicable", "no audio input"
                    else:
                        rec_client = recogniser
            elif recogniser is None:
                raise ValueError("the pipeline configuration needs a recogniser")
            else:
                rec_client = recogniser
            case_id = f"{case.case_id}/{configuration}"
            if status != "ok":
                rec = base_record(cfg, tier="audio", suite="a3", case_id=case_id, trial=trial,
                                  status=status, facets=facets, status_detail=detail)
                sink.append(rec)
                out.append(rec)
                continue
            conf = configuration if rec_client is None else "pipeline"
            started = _now()
            outcome, err = call_with_retry(lambda: cmd.run_spoken_command(
                case, clip, configuration=conf, client=client, matcher=matcher,
                recogniser=rec_client, sampling=cfg.sampling, seed=seed, thinking=cfg.thinking,
                transcribe_max_tokens=cfg.max_tokens["transcribe"],
                agent_max_tokens=cfg.max_tokens["agent"], nonce=nonce))
            if outcome is None:
                rec = base_record(cfg, tier="audio", suite="a3", case_id=case_id, trial=trial,
                                  status="excluded", facets=facets, status_detail=err,
                                  started_at=started)
                sink.append(rec)
                out.append(rec)
                continue
            latency, src = spoken_command_latency(outcome.results, conf)
            if not outcome.match.passed:
                latency = None          # only a correct call has a command latency
            metrics = {"pass": outcome.match.passed,
                       "score": outcome.entities_right / outcome.entities_total
                       if outcome.entities_total else float(outcome.match.passed),
                       "entities_right": outcome.entities_right,
                       "entities_total": outcome.entities_total,
                       "command_latency_s": latency, "tool_call_time_source": src,
                       "endpoint_delay_s": endpoint,
                       "user_perceived_latency_s": user_perceived_latency(latency, endpoint),
                       "ttft_s": outcome.results[0].timing.ttft_s,
                       "malformed_calls": sum(1 for c in outcome.calls if c["arguments"] is None)}
            if outcome.transcript is not None:
                us = score_utterance(case.spoken, outcome.transcript)
                metrics["transcript_wer"] = us.wer
            raw = sink.put_raw({"case_id": case_id, "transcript": outcome.transcript,
                                "calls": outcome.calls, "mismatches": outcome.match.mismatches,
                                "entities": outcome.entity_detail,
                                "outputs": [r.to_record() for r in outcome.results],
                                "recogniser": cfg.recogniser if rec_client is not None else None,
                                "clip": {k: v for k, v in clip.meta.items() if k != "word_timings"}})
            if rec_client is not None and configuration == "two_pass":
                detail = f"shared recogniser: {(cfg.recogniser or {}).get('id', 'unnamed')}"
            rec = base_record(cfg, tier="audio", suite="a3", case_id=case_id, trial=trial,
                              status="ok", facets=facets, metrics=metrics, status_detail=detail,
                              raw=raw, started_at=started)
            sink.append(rec)
            out.append(rec)
            if typed_twin:
                typed, terr = call_with_retry(lambda: cmd.run_typed_command(
                    case, client=client, matcher=matcher, sampling=cfg.sampling, seed=seed,
                    thinking=cfg.thinking, agent_max_tokens=cfg.max_tokens["agent"],
                    nonce=nonce + "t"))
                tid = f"{case.case_id}/typed"
                tf = {"family": "a3", "test": "typed", "tags": list(case.tags)}
                if typed is None:
                    trec = base_record(cfg, tier="audio", suite="a3", case_id=tid, trial=trial,
                                       status="excluded", facets=tf, status_detail=terr)
                else:
                    traw = sink.put_raw({"case_id": tid, "calls": typed.calls,
                                         "mismatches": typed.match.mismatches})
                    trec = base_record(cfg, tier="audio", suite="a3", case_id=tid, trial=trial,
                                       status="ok", facets=tf, raw=traw, metrics={
                                           "pass": typed.match.passed,
                                           "score": typed.entities_right / typed.entities_total
                                           if typed.entities_total else float(typed.match.passed),
                                           "entities_right": typed.entities_right,
                                           "entities_total": typed.entities_total})
                sink.append(trec)
                out.append(trec)
    return out


def main(argv: list[str] | None = None) -> int:
    """``python -m edat.media.runner --tier core plan``: what a tier runs and what it costs.

    The runner itself is driven by the harness; this entry point plans and selects. The
    default tier is ``core``."""
    import argparse
    from edat.media.tiers.__main__ import main as tiers_main
    from edat.media.tiers import DEFAULT_TIER, TIERS
    ap = argparse.ArgumentParser(prog="python -m edat.media.runner")
    ap.add_argument("--tier", default=DEFAULT_TIER, choices=TIERS)
    ap.add_argument("cmd", nargs="?", default="plan", choices=("plan", "select"))
    a = ap.parse_args(argv)
    return tiers_main([a.cmd, "--tier", a.tier])


if __name__ == "__main__":
    raise SystemExit(main())
