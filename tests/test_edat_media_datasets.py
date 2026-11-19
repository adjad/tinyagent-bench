"""Dataset manifests and the fetch script's refusals. No network: downloads use a fake opener."""
from __future__ import annotations

import hashlib
import io
import json

import pytest

from edat.media import datasets as ds


def test_every_manifest_is_well_formed():
    ms = ds.load_manifests()
    assert len(ms) >= 20
    for name, m in ms.items():
        assert ds.check_manifest(m) == [], name
        assert m["licence_checked"] == "2026-11-18"


def test_every_manifest_has_a_known_group_and_the_new_groups_are_present():
    ms = ds.load_manifests()
    assert {m["group"] for m in ms.values()} == set(ds.GROUPS)
    for name in ("kokoro-82m", "kokoro-voices-planned", "kokoro-pypi", "espeakng-loader", "espeak-ng-source",
                 "piper-en-gb-cori-medium", "piper-tts-pypi", "fsd50k-eval", "esc10", "fonts-noto-sans",
                 "fonts-handwriting", "whisper-english-spelling", "whisper-normaliser-86098128"):
        assert name in ms, name
    assert len(ms) == 44


def test_ready_items_have_bytes_and_a_checksum_or_an_explicit_note():
    for name, m in ds.load_manifests().items():
        if m["status"] != "ready":
            continue
        for f in m["files"]:
            assert isinstance(f["bytes"], int) and f["bytes"] > 0, (name, f["url"])
            assert f.get("md5") or f.get("sha256") or f.get("checksum") == "none published", (name, f["url"])
        assert m["bytes_total"] == sum(f["bytes"] for f in m["files"]), name


def test_no_url_is_a_local_path_or_plain_http():
    for name, m in ds.load_manifests().items():
        for f in m["files"]:
            u = f["url"]
            assert u.startswith("https://"), (name, u)
            for bad in ("/Users/", "/private/", "/tmp/", "file:", "localhost", "127.0.0.1"):
                assert bad not in u, (name, u)


def test_run_time_only_sets_follow_the_project_rule():
    ms = ds.load_manifests()
    for name in ("edacc-test", "ocrbench-v1", "chartqapro", "textvqa-val", "docvqa-val", "visualwebbench", "screenspot-v2"):
        assert ms[name]["status"] == "fetch_at_run_time", name
        assert ms[name]["never_commit"] is True, name
    assert ms["earnings22"]["redistributable"] == "share-alike" and ms["earnings22"]["licence"] == "CC-BY-SA-4.0"
    assert all("media.githubusercontent.com/media/revdotcom/speech-datasets/" in f["url"]
               for f in ms["earnings22"]["files"] if f["url"].endswith(".mp3"))
    ns = ms["notsofar1-eval-small"]
    assert ns["status"] == "ready" and ns["hf"]["gated"] is False and len(ns["files"]) == 320
    assert all("/sc_" in f["url"] or "/MTG_" in f["url"] for f in ns["files"])
    assert ms["whisper-english-spelling"]["pinned_commit"].startswith("ea1c2667")
    assert any(c["commit"].startswith("86098128") for c in ms["whisper-english-spelling"]["known_changes"])
    assert ms["fsd50k-eval"]["licence_filter"]["keep"] == ["CC0-1.0", "CC-BY-4.0"]


def test_voice_files_match_the_voice_plan_and_no_banned_engine_is_approved():
    plan = json.loads((ds.MANIFEST_DIR.parent / "tts" / "voices.json").read_text())
    kokoro = {v["id"] for v in plan["voices"] if v["engine"] == "kokoro-82m"}
    ms = ds.load_manifests()
    assert {f["voice"] for f in ms["kokoro-voices-planned"]["files"]} == kokoro
    assert not kokoro & {f["voice"] for f in ms["kokoro-voices-spare"]["files"]}
    assert any(f["url"].endswith("en_GB-cori-medium.onnx") for f in ms["piper-en-gb-cori-medium"]["files"])
    assert {v["engine"] for v in plan["voices"]} == {"kokoro-82m", "piper"}
    for name, m in ms.items():
        blob = (name + " " + " ".join(f["url"] for f in m["files"]) + " " + m["licence"]).lower()
        for banned in ("xtts", "f5-tts", "f5tts", "macos voice", "macos-voice", "avspeech", "say-voice", "com.apple.voice"):
            assert banned not in blob, (name, banned)


# The research (05-media-downloads, 2026-11-18) totals 20,620,527,015 bytes for v1. This repository's total is
# 523,415 bytes more: the research planned Kokoro voices af_nicole and bm_fable, while
# test_fixtures/media/tts/voices.json (the decision record) lists af_sarah, am_adam and bm_lewis instead
# (five 523,4xx-byte voice files differ). Everything else is identical, file for file.
V1_TOTAL_BYTES = 20_621_050_430
RESEARCH_V1_TOTAL_BYTES = 20_620_527_015
VOICE_PLAN_DELTA = 523_415


def test_summary_totals_are_stable():
    s = ds.summary()
    v1 = s["v1"]
    assert v1["bytes"] == V1_TOTAL_BYTES
    assert v1["bytes"] - RESEARCH_V1_TOTAL_BYTES == VOICE_PLAN_DELTA
    assert (v1["manifests"], v1["files"], v1["unsized_files"]) == (29, 10904, 0)
    assert v1["by_group"] == {"asr_eval": 8_933_027_433, "fonts": 5_311_063, "normaliser": 78_885,
                              "sounds": 6_442_739_627, "speech_engine": 617_748_436, "vision_eval": 4_622_144_986}
    assert sum(v1["by_group"].values()) == v1["bytes"]
    assert s["by_group"]["noise_room"]["ready"]["bytes"] == 11_086_114_085  # MUSAN, needed_v1 no
    assert s["total"]["manifests"] == 44
    # Only needed_v1 yes with a fetchable status counts.
    ms = ds.load_manifests()
    expect = sum(sum(f["bytes"] or 0 for f in m["files"]) for m in ms.values()
                 if m["needed_v1"] == "yes" and m["status"] in ("ready", "fetch_at_run_time"))
    assert expect == v1["bytes"]


def test_cli_summary_prints_groups_statuses_and_the_grand_total(capsys):
    assert ds.main(["summary"]) == 0
    out = capsys.readouterr().out
    assert "speech_engine" in out and "fetch_at_run_time" in out
    assert "v1 GRAND TOTAL" in out and f"{V1_TOTAL_BYTES:,}" in out
    assert ds.main(["summary", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["v1"]["bytes"] == V1_TOTAL_BYTES


def test_destination_names_keep_the_folder_tree_and_never_collide():
    ms = ds.load_manifests()
    rels = {ds.dest_relpath(f) for f in ms["notsofar1-eval-small"]["files"]}
    assert len(rels) == 320 and any(r.endswith("sc_meetup_0/ch0.wav") for r in rels)
    assert "FSD50K.eval_audio.z01" in {ds.dest_relpath(f) for f in ms["fsd50k-eval"]["files"]}
    assert "ofl/notosans/NotoSans[wdth,wght].ttf" in {ds.dest_relpath(f) for f in ms["fonts-noto-sans"]["files"]}
    assert ds.dest_relpath({"url": "https://x.test/a/tiny.bin"}) == "tiny.bin"


def test_spec_sets_are_covered():
    ms = ds.load_manifests()
    for name in ("librispeech-test-clean", "librispeech-test-other", "voxpopuli-en-test",
                 "ami-test-ihm-sdm", "notsofar1-eval-small", "earnings22", "edacc-test",
                 "crema-d", "esc10", "big-bench-audio", "cord-v2", "plotqa-test-sample",
                 "olmocr-bench", "ocrbench-v1", "docvqa-val", "visualwebbench"):
        assert name in ms, name
    # Published checksums are carried where they exist.
    assert ms["librispeech-test-clean"]["files"][0]["md5"] == "32fa31d27d2e1cad72775fee3f4849a9"


def test_unverified_licences_are_blocked_or_run_time_only():
    for name, m in ds.load_manifests().items():
        if not m["licence_verified"] or m["redistributable"] in ("unverified", "no"):
            assert m["status"] in ("blocked_licence", "fetch_at_run_time", "needs_loader"), name
            if m["status"] == "fetch_at_run_time":
                assert m["never_commit"] is True


def test_fetch_refuses_without_yes_inside_repo_and_when_blocked(tmp_path):
    with pytest.raises(ds.FetchRefused, match="--yes"):
        ds.fetch("librispeech-test-clean", tmp_path, yes=False)
    with pytest.raises(ds.FetchRefused, match="inside the repository"):
        ds.fetch("librispeech-test-clean", ds.REPO_ROOT / "data", yes=True)
    with pytest.raises(ds.FetchRefused, match="status is blocked_licence"):
        ds.fetch("earnings21", tmp_path, yes=True)
    with pytest.raises(ds.FetchRefused, match="needs_loader"):
        ds.fetch("espeak-ng-source", tmp_path, yes=True)
    # fetch_at_run_time is allowed, but still never inside the repository and never without --yes.
    assert ds.plan("edacc-test", tmp_path)["allowed"] is True
    with pytest.raises(ds.FetchRefused, match="inside the repository"):
        ds.fetch("edacc-test", ds.REPO_ROOT / "data", yes=True)
    with pytest.raises(ds.FetchRefused, match="--yes"):
        ds.fetch("edacc-test", tmp_path, yes=False)
    with pytest.raises(ds.FetchRefused, match="inside the repository"):
        ds.fetch("visualwebbench", ds.REPO_ROOT, yes=True)


def _fake_manifest(tmp_path, payload: bytes, md5: str):
    d = tmp_path / "manifests"
    d.mkdir()
    (d / "tiny.json").write_text(json.dumps({
        "schema": ds.SCHEMA, "name": "tiny", "suites": ["A1"], "homepage": "https://x.test",
        "licence": "CC0-1.0", "licence_url": "https://x.test", "licence_verified": True,
        "redistributable": "yes", "access": "http", "status": "ready",
        "files": [{"url": "https://x.test/tiny.bin", "md5": md5}]}))
    return d


def test_fetch_verifies_published_checksums(tmp_path, monkeypatch):
    payload = b"edat test payload"
    good = hashlib.md5(payload).hexdigest()
    d = _fake_manifest(tmp_path, payload, good)
    monkeypatch.setattr(ds, "MANIFEST_DIR", d)
    monkeypatch.setattr(ds, "load_manifests", lambda directory=d: {
        "tiny": json.loads((d / "tiny.json").read_text())})
    rec = ds.fetch("tiny", tmp_path / "out", yes=True, opener=lambda url: io.BytesIO(payload))
    assert rec["files"][0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / "out" / "tiny" / "tiny.bin").read_bytes() == payload
    assert json.loads((tmp_path / "out" / "tiny" / "FETCHED.json").read_text())["name"] == "tiny"
    with pytest.raises(ds.FetchRefused, match="failed its published md5"):
        ds.fetch("tiny", tmp_path / "out2", yes=True, opener=lambda url: io.BytesIO(b"tampered"))
    assert not list((tmp_path / "out2" / "tiny").glob("*.bin"))


def test_cli_list_and_plan(capsys):
    assert ds.main(["list"]) == 0
    assert "librispeech-test-clean" in capsys.readouterr().out
    assert ds.main(["plan", "musan"]) == 0
    assert json.loads(capsys.readouterr().out)["allowed"] is True
    assert ds.main(["fetch", "earnings21", "--dest", "/nonexistent-edat"]) == 2
    assert ds.main(["fetch", "edacc-test", "--dest", "/nonexistent-edat"]) == 2  # no --yes
