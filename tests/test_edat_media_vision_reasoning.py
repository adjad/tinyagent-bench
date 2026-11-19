"""V3 image to action, V5 robustness and V7 reasoning (spec 05 §7.3, §7.4, §7.7)."""
from __future__ import annotations

import io
import json

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from edat.media.vision import actions, reasoning, robustness  # noqa: E402
from edat.media.vision import generate as gen  # noqa: E402
from edat.media.vision.score import score_answer  # noqa: E402

SEED = gen.DEV_SEED


# ---------------------------------------------------------------- V7

@pytest.mark.parametrize("skill", ["counting", "spatial", "multi_image", "chart_trend"])
def test_v7_items_gold_passes_and_blank_fails(skill):
    templates = set()
    for i in range(16):
        meta, blobs = reasoning.build(skill, i, split="dev")
        q = meta["question"]
        assert len(blobs) == len(meta["images"]) >= 1
        for spec, blob in zip(meta["images"], blobs):
            im = Image.open(io.BytesIO(blob))
            assert im.size == (spec["width"], spec["height"])
        assert reasoning.score_question(q, q["answer"])["passed"]
        for acc in q["accept"]:
            assert reasoning.score_question(q, acc)["passed"], acc
        assert not reasoning.score_question(q, "")["passed"]
        assert not reasoning.score_question(q, "not shown")["passed"]
        templates.add(meta["template"])
    assert len(templates) == 4


def test_v7_is_deterministic():
    a = reasoning.build("multi_image", 5, split="dev")[0]
    b = reasoning.build("multi_image", 5, split="dev")[0]
    assert [i["sha256"] for i in a["images"]] == [i["sha256"] for i in b["images"]]


def test_v7_counting_covers_zero_to_thirty_and_scores_words():
    counts = [int(reasoning.build("counting", i, split="dev")[0]["question"]["answer"])
              for i in range(0, 160, 4)]           # the plain-shapes template
    assert min(counts) <= 3 and max(counts) >= 25
    q = {"qid": "q", "kind": "count", "answer": "7"}
    assert reasoning.score_question(q, "seven")["passed"]
    r = reasoning.score_question(q, "There are 9.")
    assert not r["passed"] and r["abs_error"] == 2 and r["bucket"] == "6-10"


def test_v7_multi_image_counts():
    n_images = sum(len(reasoning.build("multi_image", i, split="dev")[0]["images"])
                   for i in range(40))
    assert 2 * 40 <= n_images <= 4 * 40
    meta = reasoning.build("multi_image", 0, split="dev")[0]
    assert meta["prompt"].startswith("The images are numbered 1 to")


def test_point_and_box_scoring():
    q = {"qid": "q", "kind": "point", "answer": "10,10,50,30", "target_box": [10, 10, 50, 30]}
    assert reasoning.score_question(q, "(20, 15)")["passed"]
    assert reasoning.score_question(q, "x=49 y=29")["passed"]
    assert not reasoning.score_question(q, "50,30")["passed"]        # end-exclusive
    assert not reasoning.score_question(q, "")["passed"]
    b = dict(q, kind="box")
    assert reasoning.score_question(b, "10,10,50,30")["iou"] == 1.0
    assert reasoning.score_question(b, "12,10,52,30")["passed"]
    assert not reasoning.score_question(b, "30,10,70,30")["passed"]   # IoU 1/3
    assert reasoning.iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(1 / 3)


def test_v7_locate_uses_unique_elements_when_present():
    got = reasoning.build("locate", 0, split="dev")
    if got is None:
        pytest.skip("no V1 screen family records element boxes yet")
    meta, blobs = got
    q = meta["question"]
    assert q["kind"] in ("point", "box")
    assert reasoning.score_question(q, reasoning._gold_coords(q))["passed"]
    w, h = meta["truth"]["image_size"]
    b = q["target_box"]
    assert 0 <= b[0] < b[2] <= w and 0 <= b[1] < b[3] <= h


# ---------------------------------------------------------------- V3

def test_v3_items_and_harbor_scoring():
    cases = {}
    for i in range(42):
        meta, data = actions.build(i, split="dev")
        cases.setdefault(meta["case"], 0)
        cases[meta["case"]] += 1
        assert Image.open(io.BytesIO(data)).size == (meta["image"]["width"], meta["image"]["height"])
        gold = meta["expect"]
        if gold is None:
            assert actions.score_reply(meta, "No single event here.")["passed"]
            assert actions.score_tool_call(meta, None, None)["passed"]
            continue
        good = "Here you go.\n" + json.dumps({"proposal": gold})
        assert actions.score_reply(meta, good)["passed"]
        assert not actions.score_reply(meta, "")["passed"]
        wrong = dict(gold, date="2027-01-01")
        r = actions.score_reply(meta, json.dumps({"proposal": wrong}))
        assert not r["passed"] and r["fields"]["date"] is False
        name, args = actions._vision().proposal_call(actions._vision()._validate_proposal(gold))
        assert actions.score_tool_call(meta, name, args)["passed"]
        assert not actions.score_tool_call(meta, "send_message", args)["passed"]
    assert set(cases) == {"one", "none", "several"}
    assert cases["one"] > cases["none"] + cases["several"]


def test_v3_proposal_for_a_none_image_fails():
    meta = next(m for m, _ in (actions.build(i, split="dev") for i in range(60))
                if m["case"] == "none")
    fake = {"kind": "event", "title": "X", "date": "2027-03-03", "start_time": None,
            "end_time": None, "location": None, "notes": None}
    assert not actions.score_reply(meta, json.dumps({"proposal": fake}))["passed"]


def test_v3_harbor_messages_carry_the_image():
    meta, data = actions.build(0, split="dev")
    msgs = actions.harbor_messages(meta, data)
    assert msgs[0]["role"] == "system" and "proposal" in msgs[0]["content"]
    parts = msgs[1]["content"]
    assert parts[0]["type"] == "image_url" and parts[0]["image_url"]["url"].startswith(
        "data:image/png;base64,")
    assert parts[-1]["text"] == meta["request"]


def test_v3_title_keys():
    assert actions.title_matches("Kavya's birthday party!", ["kavya's birthday party",
                                                             "birthday party"])
    assert actions.title_matches("Birthday party", ["kavya's birthday party", "birthday party"])
    assert not actions.title_matches("Party", ["kavya's birthday party", "birthday party"])


# ---------------------------------------------------------------- V5

def test_v5_has_25_conditions():
    assert len(robustness.CONDITIONS) == 25
    assert len(robustness.BASE_FAMILIES) * robustness.BASE_PER_FAMILY == 600


@pytest.fixture(scope="module")
def v5_items():
    idx, items = next(robustness.base_indices("spreadsheet", split="dev", count=1))
    return idx, items


def test_v5_every_condition_keeps_its_questions(v5_items):
    idx, items = v5_items
    assert [m["condition"] for m, _ in items] == list(robustness.CONDITIONS)
    qids = {q["qid"] for q in items[0][0]["questions"]}
    for meta, data in items:
        assert {q["qid"] for q in meta["questions"]} == qids
        im = Image.open(io.BytesIO(data))
        assert im.size == (meta["image"]["width"], meta["image"]["height"])
        assert im.format == meta["image"]["format"].upper()
        for name, box in meta["field_boxes"].items():
            assert 0 <= box[0] < box[2] <= im.size[0] and 0 <= box[1] < box[3] <= im.size[1]
        for q in meta["questions"]:
            assert score_answer(q, q["answer"], default_year=meta["default_year"]).score == 1.0
            assert score_answer(q, "", default_year=meta["default_year"]).score == 0.0


def test_v5_conditions_change_what_they_say(v5_items):
    _, items = v5_items
    by = {m["condition"]: (m, d) for m, d in items}
    cut = by["crop_cut"][0]
    assert sum(q["kind"] == "abstain" for q in cut["questions"]) == 1
    assert all(q["kind"] != "abstain" for q in by["crop_tight"][0]["questions"])
    r90 = by["rotate_90"][0]["image"]
    base = by["blur_1"][0]["image"]
    assert (r90["width"], r90["height"]) == (base["height"], base["width"])
    assert by["lowres_384"][0]["image"]["width"] <= 384 >= by["lowres_384"][0]["image"]["height"]
    assert max(by["lowres_384"][0]["image"]["width"], by["lowres_384"][0]["image"]["height"]) >= 380
    assert min(by["small_text_7"][0]["params"]["field_px"]) in (6, 7, 8)
    assert by["jpeg_20"][0]["image"]["format"] == "jpeg"
    assert len(by["jpeg_20"][1]) < len(by["jpeg_40"][1])


def test_v5_unreadable_kinds():
    kinds = set()
    for n in range(5):
        meta, data = robustness.build_unreadable(n, split="dev")
        q = meta["questions"][0]
        assert q["kind"] == "abstain"
        assert score_answer(q, "not shown").score == 1.0
        assert score_answer(q, q["unreadable_from"]).false_answer
        kinds.add(meta["params"]["kind"])
    assert kinds == set(robustness.UNREADABLE_KINDS)


def test_heldout_writers_refuse_the_repository(tmp_path):
    inside = gen.REPO_ROOT / "test_fixtures" / "media" / "nope"
    for fn in (lambda: robustness.generate(inside, split="heldout", families=(), unreadable=0),
               lambda: reasoning.generate("counting", inside, split="heldout", count=0),
               lambda: actions.generate(inside, split="heldout", count=0)):
        with pytest.raises(RuntimeError):
            fn()
    assert not inside.exists()
