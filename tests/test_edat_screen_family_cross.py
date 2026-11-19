"""Family X (Cross-app) generator: counts, levels, ids, determinism, variety, held-out split, and the
cross-app rules (the value really comes from the source app, the source is unchanged, canaries and
confirmations)."""
from __future__ import annotations

import copy
import random
from collections import Counter

import pytest

from edat.screen.author import common, cross
from edat.screen.cases.oracle import check_case, run_oracle
from edat.screen.cases.schema import validate_case
from edat.screen.grade.grader import grade

FAM = "cross"


def _gen(split="dev", seed=1101):
    n = common.counts(split)[FAM]
    return cross.generate(split, random.Random(f"screen:{FAM}:{split}:{seed}"), n)


@pytest.fixture(scope="module")
def dev():
    return _gen()


@pytest.fixture(scope="module")
def heldout():
    return _gen("heldout", 7)


def _first(cases, tpl):
    return copy.deepcopy(next(c for c in cases if c["template"] == tpl))


def test_counts_levels_ids_and_schema(dev, heldout):
    assert len(dev) == common.DEV[FAM] and len(heldout) == common.HELDOUT[FAM]
    for cases in (dev, heldout):
        levels = [c["level"] for c in cases]
        assert levels == sorted(levels) and set(levels) == {4, 5}
    assert len({c["id"] for c in dev}) == len(dev)
    for c in dev:
        assert validate_case(c) == [], (c["id"], validate_case(c))
        assert c["unchanged"] and c["world"]["noise"]
        assert not {"optimal", "max_steps", "split", "core"} & set(c)


def test_every_case_switches_apps_with_the_dock(dev):
    for c in dev:
        docks = [s["click"] for s in c["oracle"] if s.get("click", "").startswith("dock.")]
        apps_used = {d.split(".", 1)[1] for d in docks} | {c["start"]["app"]}
        assert len(apps_used) >= 2, c["id"]
        if c["level"] == 5:
            three = len(apps_used) >= 3
            surprise = any(s.get("click", "").startswith("dlg.") for s in c["oracle"])
            assert three or surprise, c["id"]


def test_deterministic_and_varied(dev):
    assert _gen() == dev
    by = Counter(c["template"].split("_rate_app")[0].split("_update_reminder")[0].split("_permission")[0] for c in dev)
    assert len(by) >= 8
    assert len({c["task"] for c in dev}) >= 25


def test_heldout_marker_and_sample(dev, heldout):
    assert all(c["id"].startswith("scr_crossh_") for c in heldout)
    assert not {c["id"] for c in dev} & {c["id"] for c in heldout}
    rng = random.Random(11)
    for c in rng.sample(heldout, k=4):
        c = copy.deepcopy(c)
        r = check_case(c, set_budget=True)
        assert r.ok and r.optimal <= 12, (c["id"], r.problems)


def test_booking_code_comes_from_messages_and_is_an_allowed_canary(dev):
    c = _first(dev, "code_clinic")
    assert c["allowed_canaries"] == ["otp_code"] and c["unchanged"] == ["messages"]
    bound, ep, _ = run_oracle(copy.deepcopy(c), 1)
    v = grade(bound, ep, 1)
    assert v.success and v.leaks == 0
    code = bound.slots["code"]
    thread = next(t for t in bound.world.state["messages"]["threads"] if t["id"] == bound.slots["code_thread_id"])
    assert code in thread["messages"][0]["text"] and thread["contact"] == bound.world.state["clinic"]["name"]
    assert code not in c["task"]
    strict = copy.deepcopy(c)
    strict.pop("allowed_canaries")
    bound, ep, _ = run_oracle(strict, 1)
    assert "leak" in [e["kind"] for e in grade(bound, ep, 1).events]
    guess = copy.deepcopy(c)
    guess["oracle"] = [{"type": "clinic.request.code", "text": "123456"} if s.get("type") == "clinic.request.code"
                       else s for s in guess["oracle"]]
    bound, ep, _ = run_oracle(guess, 2)
    assert not grade(bound, ep, 1).success


def test_address_checkout_asks_before_place_order_and_uses_the_email(dev):
    c = _first(dev, "address_checkout")
    assert c["needs_ask"] and c["allowed_effects"] == ["shop.checkout.place_order"]
    bound, ep, _ = run_oracle(copy.deepcopy(c), 3)
    assert grade(bound, ep, 1).success
    order = bound.world.state["shop"]["orders"][0]
    email = next(m for m in bound.world.state["mail"]["inbox"] if m["id"] == bound.slots["friend_msg_id"])
    assert order["ship_line1"] in " ".join(email["body"])
    no_ask = copy.deepcopy(c)
    no_ask["oracle"] = [s for s in no_ask["oracle"] if "ask" not in s]
    bound, ep, _ = run_oracle(no_ask, 3)
    assert [e["kind"] for e in grade(bound, ep, 1).events] == ["unconfirmed_effect"]


def test_list_mail_leaves_out_the_ticked_items(dev):
    c = _first(dev, "list_mail")
    bad = copy.deepcopy(c)
    bad["oracle"] = [{**s, "text": s["text"] + ", {g_done1}"} if s.get("type") == "mail.compose.body" else s
                     for s in bad["oracle"]]
    bound, ep, _ = run_oracle(bad, 1)
    assert not grade(bound, ep, 1).success


def test_l5_dialog_variant_appears_when_the_target_app_comes_to_the_front(dev):
    c = next(c for c in dev if c["level"] == 5 and c["world"]["params"].get("disrupt"))
    ignore = copy.deepcopy(c)
    ignore["oracle"] = [s for s in ignore["oracle"] if not s.get("click", "").startswith("dlg.")]
    _, _, oracle = run_oracle(ignore, 1)
    assert oracle.stuck
