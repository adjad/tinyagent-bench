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


def test_spec_sets_are_covered():
    ms = ds.load_manifests()
    for name in ("librispeech-test-clean", "librispeech-test-other", "voxpopuli-en-test",
                 "ami-test-ihm-sdm", "notsofar1-eval-small", "earnings22", "edacc-test",
                 "crema-d", "esc10", "big-bench-audio", "cord-v2", "plotqa-test-sample",
                 "olmocr-bench", "ocrbench-v1", "docvqa-val", "visualwebbench"):
        assert name in ms, name
    # Published checksums are carried where they exist.
    assert ms["librispeech-test-clean"]["files"][0]["md5"] == "32fa31d27d2e1cad72775fee3f4849a9"


def test_unverified_licences_are_blocked():
    for name, m in ds.load_manifests().items():
        if not m["licence_verified"] or m["redistributable"] in ("unverified", "no"):
            assert m["status"] == "blocked_licence", name
            assert not ds.plan(name)["allowed"]


def test_fetch_refuses_without_yes_inside_repo_and_when_blocked(tmp_path):
    with pytest.raises(ds.FetchRefused, match="--yes"):
        ds.fetch("librispeech-test-clean", tmp_path, yes=False)
    with pytest.raises(ds.FetchRefused, match="inside the repository"):
        ds.fetch("librispeech-test-clean", ds.REPO_ROOT / "data", yes=True)
    with pytest.raises(ds.FetchRefused, match="status is blocked_licence"):
        ds.fetch("edacc-test", tmp_path, yes=True)
    with pytest.raises(ds.FetchRefused, match="needs_loader"):
        ds.fetch("ami-test-ihm-sdm", tmp_path, yes=True)


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
    assert ds.main(["fetch", "edacc-test", "--dest", "/nonexistent-edat"]) == 2
