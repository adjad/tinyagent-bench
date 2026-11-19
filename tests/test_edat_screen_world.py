"""Screen World core: determinism, replay, ids, viewport, the eight actions' rules (spec 11 S1, S10)."""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from edat.screen.apps import dialogs
from edat.screen.cases.bind import bind, fill, max_steps_for
from edat.screen.world.actions import TOOL_NAMES, TOOLS
from edat.screen.world.core import VIEWPORT_LINES, World, estimate_tokens
from edat.screen.world.elements import El

GOLDEN = Path(__file__).resolve().parents[1] / "test_fixtures" / "screen" / "golden" / "settings_replay.json"


def settings_world(seed=7, screen="main", extra_rows=8, noise=None):
    w = World(seed=seed, apps=["settings"], front="settings", start={"app": "settings", "screen": screen},
              noise=noise)
    w.apps["settings"].seed(w, random.Random(seed), {"extra_rows": extra_rows})
    return w


def idof(w, key):
    vis = w.observe()
    for i, e in vis.by_id.items():
        if e.key == key:
            return i
    raise KeyError(key)


def replay(seed: int, log: list[dict]) -> dict:
    w = settings_world(seed)
    out = {"observations": [], "statuses": []}
    for step in log:
        args = dict(step["args"])
        if "key" in step:      # resolve a stable key to this trial's id
            args["id"] = idof(w, step["key"])
        res = w.act(step["tool"], args)
        out["statuses"].append(res.status)
        out["observations"].append(w.observe().text)
    out["final_hash"] = w.full_hash()
    out["data_hash"] = w.data_hash()
    return out


LOG = [{"tool": "click", "key": "settings.main.battery", "args": {}},
       {"tool": "click", "key": "settings.battery.low_power", "args": {}},
       {"tool": "select_option", "key": "settings.battery.display_off", "args": {"option": "never"}},
       {"tool": "click", "args": {"id": "1"}},
       {"tool": "go_back", "args": {}},
       {"tool": "click", "key": "settings.main.notifications", "args": {}},
       {"tool": "scroll", "args": {"direction": "down"}},
       {"tool": "press_key", "args": {"key": "Tab"}},
       {"tool": "go_back", "args": {}},
       {"tool": "click", "key": "settings.main.general", "args": {}},
       {"tool": "type_text", "key": "settings.general.name", "args": {"text": "Studio Mac"}}]


def test_a_stored_action_log_replays_byte_for_byte():
    got = replay(5, LOG)
    import hashlib
    digest = {"final_hash": got["final_hash"], "data_hash": got["data_hash"],
              "observations_sha256": [hashlib.sha256(o.encode()).hexdigest() for o in got["observations"]],
              "statuses": got["statuses"]}
    want = json.loads(GOLDEN.read_text())
    assert digest == want["expect"], "replay differs from the stored golden file"
    assert want["log"] == LOG


def test_same_seed_same_actions_same_world_and_text():
    assert replay(11, LOG) == replay(11, LOG)


def test_ids_change_with_the_seed_but_keys_do_not():
    a, b = settings_world(1), settings_world(2)
    assert idof(a, "settings.main.battery") != idof(b, "settings.main.battery") or \
        idof(a, "settings.main.network") != idof(b, "settings.main.network")
    w = settings_world(1)
    i = idof(w, "settings.main.battery")
    w.act("click", {"id": idof(w, "settings.main.network")})
    w.act("go_back", {})
    assert idof(w, "settings.main.battery") == i      # stable within a trial


def test_observation_format_matches_the_spec():
    w = settings_world(3, "battery")
    text = w.observe().text
    lines = text.split("\n")
    assert lines[0].startswith('App: Settings · Window: "Battery" · Screen 2 · Visible 1–')
    assert lines[1].startswith("Dock: [d")
    assert any(ln.endswith('switch "Low Power Mode" off') for ln in lines)
    assert any('popup "Turn display off after" value "' in ln for ln in lines)
    assert any(ln.startswith("  [") and "Show details" in ln for ln in lines)   # indentation shows grouping


def test_invalid_actions_change_nothing_and_say_why():
    w = settings_world(3, "battery")
    before = w.full_hash()
    text_id = idof(w, "settings.battery.level")
    cases = [("click", {"id": text_id}, "wrong_role"), ("click", {"id": "9999"}, "unknown_id"),
             ("type_text", {"id": idof(w, "settings.battery.low_power"), "text": "x"}, "wrong_role"),
             ("select_option", {"id": idof(w, "settings.battery.display_off"), "option": "Forever"}, "option_absent"),
             ("press_key", {"key": "F5"}, "bad_key"), ("scroll", {"direction": "down"}, "nothing_to_scroll"),
             ("click", {}, "bad_args")]
    for tool, args, code in cases:
        r = w.act(tool, args)
        assert r.code == code, (tool, args, r.status)
        assert r.status.startswith("error: ")
        assert w.full_hash() == before
    assert w.act("click", {"id": text_id}).status == f"error: [{text_id}] is text, not a control"
    w2 = settings_world(3, "main")
    assert w2.act("go_back", {}).code == "no_history"


def test_viewport_shows_40_lines_and_scroll_moves_one_page():
    w = settings_world(4, "notifications", extra_rows=40)
    vis = w.observe()
    assert vis.total > VIEWPORT_LINES
    body = [ln for ln in vis.text.split("\n")[2:] if ln.lstrip().startswith("[")]
    assert len(body) == VIEWPORT_LINES
    assert "more below (scroll down)" in vis.text
    last_row = [e.key for e in w.observe().all_ids.values() if e.key.startswith("settings.notifications.app.")][-1]
    hidden = next(i for i, e in vis.all_ids.items() if e.key == last_row)
    r = w.act("click", {"id": hidden})
    assert r.code == "not_on_screen"
    assert w.act("scroll", {"direction": "down"}).ok
    assert "more above (scroll up)" in w.observe().text
    assert w.act("click", {"id": hidden}).ok


def test_switch_popup_slider_radio_and_textbox_binds():
    w = settings_world(2, "display")
    w.act("click", {"id": idof(w, "settings.display.night_shift")})
    assert w.state["settings"]["display"]["night_shift"] is True
    w.act("select_option", {"id": idof(w, "settings.display.brightness"), "option": "100"})
    assert w.state["settings"]["display"]["brightness"] == "100"
    w.act("click", {"id": idof(w, "settings.display.appearance.dark")})
    assert w.state["settings"]["display"]["appearance"] == "Dark"
    w.act("select_option", {"id": idof(w, "settings.display.appearance"), "option": "light"})
    assert w.state["settings"]["display"]["appearance"] == "Light"
    w.act("click", {"id": idof(w, "settings.display.text_size")})          # opens the popup
    assert "expanded" in w.observe().text
    w.act("click", {"id": idof(w, "settings.display.text_size.opt.3")})
    assert w.state["settings"]["display"]["text_size"] == "Larger"


def test_dialogs_block_the_window_and_escape_closes():
    w = settings_world(2, "reset")
    erase = idof(w, "settings.reset.erase")
    w.act("click", {"id": erase})
    vis = w.observe()
    assert vis.dialog is not None and "Dialog open" in vis.text
    r = w.act("click", {"id": erase})
    assert r.code == "behind_dialog"
    confirm = idof(w, "settings.reset.alert.erase.confirm")
    w.act("press_key", {"key": "Escape"})
    assert w.observe().dialog is None
    w.act("click", {"id": erase})
    w.act("click", {"id": confirm})
    assert w.state["settings"]["general"]["resets"] == ["erase"]
    assert [e["key"] for e in w.effects] == ["settings.reset.alert.erase.confirm"]
    assert w.effects[0]["kind"] == "permanent_delete"


def test_dock_switches_apps_and_is_always_clickable():
    w = World(seed=1, apps=["settings"], front="settings")
    w.apps["settings"].seed(w, random.Random(1), {})
    dock = idof(w, "dock.settings")
    assert dock.startswith("d")
    assert w.act("click", {"id": dock}).ok


def test_noise_knobs_blank_icon_names_and_wrap_groups():
    assert any('group ""' in settings_world(s, "display", noise={"nested_groups": True}).observe().text
               for s in range(1, 8))
    w = World(seed=1, apps=["settings"], front="settings", noise={"unnamed_icons": 1.0})
    w.apps["settings"].seed(w, random.Random(1), {})
    from edat.screen.world.render_text import apply_noise
    els = apply_noise(w, [El(key="x.icon", role="button", name="List options", meta={"icon": True})])
    assert els[0].role == "text" and els[0].name == "List options" and els[1].name == ""


def test_transient_error_banner_and_relabel_surprises():
    w = settings_world(2, "battery")
    key = "settings.battery.details"
    dialogs.install(w, {"kind": "transient_error", "key": key})
    w.act("click", {"id": idof(w, key)})
    assert w.observe().dialog is not None and w.frame()["screen"] == "battery"
    w.act("click", {"id": idof(w, "dlg.error.settings.retry")})
    assert w.frame()["screen"] == "battery_usage"
    dialogs.install(w, {"kind": "banner", "app": "settings", "key": "banner.promo", "text": "New: dark mode",
                        "when": {"after_actions": 1}})
    w.act("go_back", {})
    assert "New: dark mode" in w.observe().text
    w.act("click", {"id": idof(w, "banner.promo.btn0")})
    assert "New: dark mode" not in w.observe().text
    dialogs.install(w, {"kind": "relabel", "key": "settings.battery.details", "name": "Usage details"})
    assert 'button "Usage details"' in w.observe().text


def test_every_settings_screen_stays_under_the_token_cap():
    w = settings_world(1, "main", extra_rows=40)
    for pane in ["network", "bluetooth", "battery", "display", "notifications", "privacy", "storage", "general"]:
        w.act("click", {"id": idof(w, f"settings.main.{pane}")})
        assert estimate_tokens(w.observe().text) <= 3000
        w.act("go_back", {})


def test_tools_are_the_eight_with_terse_schemas():
    assert [t["function"]["name"] for t in TOOLS] == list(TOOL_NAMES)
    assert estimate_tokens(json.dumps(TOOLS)) <= 600 * 2   # §3.2 budgets 600 tokens; JSON overhead counted


def test_fill_and_step_budget():
    assert fill({"a": "{x} and {y}", "b": [r"\d{6}"]}, {"x": 1, "y": "two"}) == {"a": "1 and two", "b": [r"\d{6}"]}
    with pytest.raises(KeyError):
        fill("{missing}", {})
    assert [max_steps_for(n) for n in (1, 3, 4, 12, 20)] == [6, 6, 8, 24, 24]


def test_bind_rebuilds_state_from_the_seed():
    case = {"id": "scr_ground_t_001", "family": "ground", "level": 1, "start": {"app": "settings", "screen": "network"},
            "world": {"apps": ["settings"]}, "task": "Is {wifi_network} saved?", "oracle": [{"finish": True}]}
    a, b, c = bind(case, 1), bind(case, 1), bind(case, 2)
    assert a.world.state == b.world.state and a.case["task"] == b.case["task"]
    assert a.world.observe().text == b.world.observe().text
    assert a.world.state != c.world.state
