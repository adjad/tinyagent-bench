"""Checkpoint inspection from safetensors headers, on synthetic files (no model, no weights)."""
from __future__ import annotations

import json
import struct

import pytest

from edat.media.checkpoint import classify, inspect, main, read_header, strip_plan


def write_safetensors(path, tensors: dict[str, tuple[str, list[int]]]):
    """Write a header with the given (dtype, shape) tensors and zero-filled data."""
    width = {"BF16": 2, "F32": 4, "U32": 4, "F16": 2}
    header, offset = {}, 0
    for name, (dtype, shape) in tensors.items():
        n = width[dtype]
        for d in shape:
            n *= d
        header[name] = {"dtype": dtype, "shape": shape, "data_offsets": [offset, offset + n]}
        offset += n
    header["__metadata__"] = {"format": "mlx"}
    raw = json.dumps(header).encode()
    raw += b" " * (-len(raw) % 8)
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\0" * offset)
    return offset


GEMMA_LIKE = {
    "audio_tower.subsample_conv_projection.conv_0.conv.weight": ("BF16", [128, 1, 3, 3]),
    "audio_tower.conformer.0.attention.attn.q_proj.weight": ("BF16", [64, 64]),
    "embed_audio.embedding_projection.weight": ("U32", [32, 8]),
    "vision_tower.encoder.layers.0.self_attn.q_proj.weight": ("BF16", [48, 48]),
    "embed_vision.embedding_projection.weight": ("U32", [32, 6]),
    "language_model.model.embed_tokens.weight": ("U32", [100, 8]),
    "language_model.model.embed_tokens_per_layer.weight": ("U32", [100, 16]),
    "language_model.model.per_layer_model_projection.weight": ("BF16", [16, 16]),
    "language_model.model.layers.0.self_attn.q_proj.weight": ("U32", [64, 8]),
    "language_model.model.layers.0.self_attn.q_proj.scales": ("BF16", [64, 1]),
    "language_model.model.layers.1.mlp.gate_proj.weight": ("U32", [64, 8]),
    "language_model.model.norm.weight": ("BF16", [64]),
}


@pytest.fixture
def ckpt(tmp_path):
    write_safetensors(tmp_path / "model.safetensors", GEMMA_LIKE)
    write_safetensors(tmp_path / "model-mtp.safetensors",
                      {"language_model.mtp.layers.0.weight": ("U32", [16, 8])})
    (tmp_path / "config.json").write_text(json.dumps({
        "model_type": "gemma4", "audio_config": {"hidden_size": 64},
        "vision_config": {"hidden_size": 48}, "quantization": {"bits": 4, "group_size": 64}}))
    return tmp_path


def test_classification_rules():
    assert classify("model.audio_tower.x.weight") == "audio_encoder"
    assert classify("embed_audio.embedding_projection.weight") == "audio_projector"
    assert classify("vision_tower.encoder.layers.3.mlp.fc1.weight") == "vision_encoder"
    assert classify("multi_modal_projector.linear_1.weight") == "vision_projector"
    assert classify("language_model.model.embed_tokens_per_layer.weight") == "per_layer_embeddings"
    assert classify("language_model.mtp.layers.0.weight") == "mtp_head"
    assert classify("model.layers.7.self_attn.k_proj.weight") == "decoder_layers"
    assert classify("visual.blocks.0.attn.qkv.weight") == "vision_encoder"
    assert classify("something_new.weight") == "other"


def test_component_bytes_and_configurations(ckpt):
    rep = inspect(ckpt)
    comps = rep["components"]
    audio = 128 * 9 * 2 + 64 * 64 * 2
    vision = 48 * 48 * 2
    assert comps["audio_encoder"]["bytes"] == audio
    assert comps["audio_encoder"]["float_params"] == audio // 2
    assert comps["audio_projector"]["bytes"] == 32 * 8 * 4
    assert comps["vision_encoder"]["bytes"] == vision
    assert comps["mtp_head"]["bytes"] == 16 * 8 * 4
    assert comps["decoder_layers"]["quantised_bytes"] == 2 * 64 * 8 * 4
    total = rep["total_tensor_bytes"]
    configs = rep["configurations"]
    assert configs["T+V+A"] == total
    assert configs["T+V"] == total - audio - 32 * 8 * 4
    assert configs["T"] == total - audio - 32 * 8 * 4 - vision - 32 * 6 * 4 - 16 * 8 * 4
    assert rep["declares"] == {"vision_config": True, "audio_config": True,
                               "quantization": {"bits": 4, "group_size": 64}}
    assert rep["has_towers"] == {"vision": True, "audio": True}
    assert rep["other_tensors"] == []


def test_strip_plan_lists_names_only(ckpt):
    before = {p.name: p.stat().st_mtime_ns for p in ckpt.iterdir()}
    plan = strip_plan(ckpt, "T+V")
    assert plan["drop_config_sections"] == ["audio_config"]
    assert all(n.startswith(("audio_tower.", "embed_audio.")) for n in plan["drop_tensors"])
    assert len(plan["drop_tensors"]) == 3
    assert {p.name: p.stat().st_mtime_ns for p in ckpt.iterdir()} == before   # wrote nothing


def test_bad_headers_are_refused(tmp_path):
    p = tmp_path / "short.safetensors"
    p.write_bytes(b"\x01\x02")
    with pytest.raises(ValueError):
        read_header(p)
    p.write_bytes(struct.pack("<Q", 10**12))
    with pytest.raises(ValueError):
        read_header(p)
    bad = tmp_path / "bad.safetensors"
    raw = json.dumps({"x": {"dtype": "BF16", "shape": [4], "data_offsets": [0, 6]}}).encode()
    bad.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\0" * 6)
    with pytest.raises(ValueError, match="offsets"):
        inspect(bad)


def test_cli(ckpt, capsys):
    assert main([str(ckpt)]) == 0
    out = capsys.readouterr().out
    assert "audio_encoder" in out and "configuration T+V" in out
    assert main([str(ckpt), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "edat.media.checkpoint/1"
