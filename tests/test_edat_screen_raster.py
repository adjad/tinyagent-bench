"""The Pillow screenshot rasteriser: size, determinism, marks, the dialog overlay, ASCII labels."""
from __future__ import annotations

import io

from PIL import Image

from edat.screen.apps import dialogs
from edat.screen.cases.bind import bind
from edat.screen.render import raster, som

CASE = {"id": "scr_ground_lp_001", "spec": "screen", "family": "ground", "level": 1,
        "start": {"app": "settings", "screen": "battery"}, "world": {"apps": ["settings"]},
        "task": "Turn on Low Power Mode.", "first_action": {"tool": "click", "target": "settings.battery.low_power"},
        "final_state": [{"in": "settings.battery", "expect": {"low_power": True}}],
        "unchanged": ["settings.network", "settings.display"], "optimal": {"steps": 2}, "max_steps": 6,
        "oracle": [{"click": "settings.battery.low_power"}, {"finish": True}]}
RED = (215, 38, 61)


def observe(seed=1, dialog=False):
    w = bind(CASE, seed).world
    if dialog:
        w.open_dialog(dialogs.rate_app("settings", "Settings"))
    return w.observe()


def image(png):
    return Image.open(io.BytesIO(png)).convert("RGB")


def test_png_signature_size_and_mode():
    png = raster.draw(observe())
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    im = Image.open(io.BytesIO(png))
    assert im.size == (som.WIDTH, som.HEIGHT) and im.mode == "RGB"
    assert raster.renderer_versions()["renderer"] == raster.RENDERER_ID


def test_deterministic_and_seed_sensitive():
    assert raster.draw(observe(1)) == raster.draw(observe(1))
    assert raster.draw(observe(1)) != raster.draw(observe(2))


def test_marks_draw_red_outlines_only_when_asked():
    vis = observe()
    box = next(b for b in som.layout(vis) if b.marked)
    pos = (box.x - 2, box.y - 2 + box.h // 2)        # left edge of the 2 px outline
    assert image(raster.draw(vis, marks=True)).getpixel(pos) == RED
    assert RED not in {c for _, c in image(raster.draw(vis, marks=False)).getcolors(1 << 20)}


def test_dialog_dims_the_window():
    plain, dlg = image(raster.draw(observe())), image(raster.draw(observe(dialog=True)))
    assert plain.getpixel((5, 400)) == raster.BG
    assert dlg.getpixel((5, 400)) != raster.BG and sum(dlg.getpixel((5, 400))) < sum(raster.BG) * 0.7
    assert dlg.getpixel((som.WIDTH // 2, som.TOP + 52)) == raster.WHITE      # the dialog box itself


def test_ascii_label_mappings():
    f = raster.ascii_label
    assert f("☑ A ☐ B") == "[x] A [ ] B"
    assert f("Wi-Fi  ● on  ○ off") == "Wi-Fi  (on)  (off)"
    assert f("a – b — c · d") == "a - b - c - d"
    assert f("“hi” it’s…") == '"hi" it\'s...'
    assert f("2×3 café Zoë") == "2x3 cafe Zoe"
    assert f("日本") == "??"
    assert f("日本 é").isascii()


def test_every_drawn_label_is_ascii():
    vis = observe()
    assert all(raster.ascii_label(b.label).isascii() for b in som.layout(vis))


def test_a_very_long_label_draws_quickly():
    import time
    vis = observe()
    boxes = som.layout(vis)
    boxes[0].label = "x" * 20000
    t = time.perf_counter()
    raster.draw(vis, boxes)
    assert time.perf_counter() - t < 2.0
