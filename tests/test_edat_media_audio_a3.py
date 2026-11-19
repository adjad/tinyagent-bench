"""A3 spoken-command families: generators, verbalising, scoring, the render plan, the dialogue driver.

Only the stub speech engine and the stub model are used. The full plans are
tested as data; a sample of each family is rendered.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("yaml")

from edat.media.adapter import StubMediaClient
from edat.media.audio import commands as cmd
from edat.media.audio import spoken_cases as sc
from edat.media.audio.dialogue import SpokenDialogue, SpokenTurn, run_spoken_dialogue
from edat.media.audio.mix import GENERATED_NOISES
from edat.media.audio.tts import StubEngine
from edat.media.audio.wav import SAMPLE_RATE


@pytest.fixture(scope="module")
def engine():
    return StubEngine()


@pytest.fixture(scope="module")
def suite():
    # Without the agentic fixtures (they live on the agentic branch): Toolbench + generated.
    return sc.build_suite(agentic_cases=[])


@pytest.fixture(scope="module")
def plan(suite):
    return sc.plan_for(suite)


# ------------------------------------------------------------------ verbalising

@pytest.mark.parametrize("typed,spoken", [
    ("remind me at 4:30 pm", "remind me at four thirty p m"),
    ("put dinner on sunday 6-7pm", "put dinner on sunday six to seven p m"),
    ("what's 15% of 84?", "what's fifteen percent of eighty four?"),
    ("Take my $20 voucher off", "Take my twenty dollars voucher off"),
    ("log $1,284.17", "log one thousand two hundred eighty four dollars and seventeen cents"),
    ("whats the schedule for the 3rd", "whats the schedule for the third"),
    ("Heating to 21°C please.", "Heating to twenty one degrees celsius please."),
    ("my gate code is 4417", "my gate code is four four one seven"),
    ("what is 1,250 divided by 8", "what is one thousand two hundred fifty divided by eight"),
    ("convert 26.2 miles to km", "convert twenty six point two miles to kilometers"),
    ("save Bob's number as 555-0199", "save Bob's number as five five five, zero one nine nine"),
    ("its mom@example.com btw", "its mom at example dot com by the way"),
    ("push standup to 10:30", "push standup to ten thirty"),
    ("timer 15 mins pls", "timer fifteen minutes please"),
    ("order TH-48213", "order T H four eight two one three"),
    ("the 2025 return", "the twenty twenty five return"),
])
def test_speak_verbalises_without_digits(typed, spoken):
    out = sc.speak(typed)
    assert out == spoken
    assert not re.search(r"\d", out)


def test_speak_is_seeded_and_keeps_meaning():
    import random
    a = sc.speak("text Sam at 4:30pm", rng=random.Random(5))
    assert a == sc.speak("text Sam at 4:30pm", rng=random.Random(5))
    assert a in ("text Sam at four thirty p m", "text Sam at half past four in the afternoon")
    assert sc.speak("call +1 555 0115") == "call plus one, five five five, zero one one five"


@pytest.mark.parametrize("prompt,raw,why", [
    ("open https://www.example.org/permits", {}, "url"),
    ("read ~/Documents/notes.txt", {}, "file_path"),
    ("move hike_photos.zip into Documents", {}, "file_path"),
    ("what unit is in my storage_info file", {}, "code"),
    ('post {"status": "done"}', {}, "code"),
    ("and tmrw?", {"history": [{"user": "x"}]}, "multi_turn"),
    ("what's in this photo", {"attachments": ["a.png"]}, "attachment"),
    ("text {who} hi", {}, "unbound_placeholder"),
    ("text Sara that I'm late", {}, None),
])
def test_unspeakable_reasons(prompt, raw, why):
    assert sc.unspeakable_reason(prompt, raw) == why


# ------------------------------------------------------------------ single: agentic conversion

AGENTIC = [
    {"id": "sel_ast_rem_901", "lane": "assistant", "family": "select", "tags": ["core"],
     "prompt": "remind me on {in_2_days_weekday} at {p}pm to post the parcel",
     "facts": {"p": {"int": [3, 3]}},
     "expect": [{"tool": "add_reminder", "args": {"title": {"icontains": "parcel"},
                                                  "due": {"regex": "T15:00"}}}],
     "forbid": ["clear_reminders"]},
    {"id": "sel_neu_cal_902", "lane": "neutral", "family": "select", "tags": [],
     "prompt": "move the supplier call to {rel:+1d 15:00}",
     "world": {"clock": "2027-05-18T08:30"},
     "expect": [{"tool": "calendar_update_event", "args": {"start": "+1d 15:00"}}]},
    {"id": "par_neu_pages_903", "lane": "neutral", "family": "parallel", "tags": [],
     "prompt": "check https://cafe.example.com/lunch", "expect": []},
    {"id": "rst_neu_askmsg_904", "lane": "neutral", "family": "restraint", "tags": ["confirm"],
     "prompt": "text Maeve that I'm {mins} minutes late", "facts": {"mins": {"int": [6, 29]}},
     "confirm": ["messages_send"], "system_policy": "Ask before you send anything.",
     "oracle": [{"text": "Shall I send it?"}],
     "followup_turn": {"user": "yep go ahead", "oracle": [
         {"calls": [{"name": "messages_send", "arguments": {"to": "Maeve", "text": "late {mins}"}}]},
         {"text": "Sent."}]}},
    {"id": "rec_ast_file_905", "lane": "assistant", "family": "recovery", "tags": [],
     "prompt": "what does lease_terms_6.txt say about pets?", "expect": []},
]


def test_speakable_agentic_cases_bind_and_keep_expect():
    conv = sc.convert_agentic(AGENTIC, world_seed=1)
    assert conv.total == 5 and conv.speakable == 2
    assert conv.report()["dropped"] == {"file_path": 1, "multi_turn": 1, "url": 1}
    a, b = conv.cases
    # Facts and date facts bound as the agentic binder does (default clock 2026-11-18, a Wednesday).
    assert a.text == "remind me on Friday at 3pm to post the parcel"
    assert a.spoken in ("remind me on Friday at three p m to post the parcel",
                        "remind me on Friday at three o'clock in the afternoon to post the parcel")
    assert a.origin["expect"] == AGENTIC[0]["expect"] and a.expect[0]["tool"] == "add_reminder"
    assert a.tool_menu == "agentic:assistant" and a.forbid == ["clear_reminders"]
    assert a.today == "2026-11-18" and a.source == "agentic"
    # Relative times resolved against the case's own clock.
    assert b.text == "move the supplier call to 2027-05-19T15:00"
    assert b.expect[0]["args"]["start"] == "2027-05-19T15:00" and b.today == "2027-05-18"
    assert not re.search(r"\d", b.spoken)
    assert sc.speakable_cases(AGENTIC)[0].to_json() == a.to_json()


def test_agentic_facts_follow_the_world_seed():
    one = sc.agentic_facts(AGENTIC[3], 1)["mins"]
    assert one == sc.agentic_facts(AGENTIC[3], 1)["mins"]
    assert len({sc.agentic_facts(AGENTIC[3], s)["mins"] for s in range(1, 20)}) > 1


def test_real_agentic_fixtures_when_present():
    cases = sc.load_agentic()
    if not cases:
        pytest.skip("agentic fixtures are not on this branch")
    conv = sc.convert_agentic(cases)
    assert conv.speakable > 0.8 * conv.total
    assert not any(re.search(r"\d", c.spoken) for c in conv.cases)


# ------------------------------------------------------------------ single: Toolbench and entity-dense

def test_toolbench_conversion():
    conv = sc.toolbench_cases()
    assert conv.total == 383
    assert conv.speakable == len({c.case_id for c in conv.cases}) > 300
    assert len(conv.dropped["multi_turn"]) == 34          # the history cases go to follow-ups
    assert all(c.tool_menu == "toolbench" and c.today == "2026-10-29" for c in conv.cases)
    assert not any(re.search(r"\d", c.spoken) or "{" in c.text for c in conv.cases)
    by_id = {c.origin["case_id"]: c for c in conv.cases}
    assert by_id["mail_email_priya"].text == "email Priya that standup is cancelled"
    assert by_id["dev_timer"].spoken == "Set a timer for ten minutes"


def test_entity_dense_domain_terms_and_invented_data():
    cases = sc.entity_dense()
    assert len(cases) == 500
    subs = Counter(e.sub for c in cases for e in c.entities if e.kind == "term")
    assert set(subs) == {"app", "product", "medical", "finance", "technical"}
    assert sum(subs.values()) >= 160
    for c in cases:
        assert not re.search(r"\d", c.spoken)
        for e in c.entities:
            if e.kind == "email":
                assert e.value.split("@")[1] in ("example.com", "example.org", "mail.test")
            if e.kind == "phone":
                assert e.value[3:8] == "55501"
            if e.kind == "address":
                assert len(e.parts) == 3 and e.parts[0].isdigit()


def test_address_needs_house_number_street_and_town():
    case = next(c for c in sc.entity_dense() if c.entities[0].kind == "address")
    ent = case.entities[0]
    num, street, town = ent.parts
    good = case.text.split("to ", 1)[1].replace(" St,", " Street,")
    ok = lambda dest: cmd.entity_accuracy(case, [{"name": "get_directions",
                                                  "arguments": {"destination": dest}}])[0] == 1
    assert ok(good)
    assert not ok(good.replace(num, str(int(num) + 1)))           # wrong house number
    assert not ok(good.rsplit(",", 1)[0])                          # town missing
    assert not ok(good.replace(street.split()[0], "Other", 1) if street.split()[0] in good
                  else good.replace(street.split()[0].capitalize(), "Other", 1))


def test_entity_accuracy_per_type_reports_domain_terms():
    case = next(c for c in sc.entity_dense() if any(e.sub == "medical" and e.arg == "text"
                                                    for e in c.entities))
    med = next(e.value for e in case.entities if e.sub == "medical")
    dose = next(e.value for e in case.entities if e.kind == "number")
    good = {"name": "add_reminder", "arguments": {
        "text": f"Take {dose} mg {med}", "date": next(e.value for e in case.entities if e.kind == "date"),
        "time": next(e.value for e in case.entities if e.kind == "time")}}
    _, _, d1 = cmd.entity_accuracy(case, [good])
    bad = json.loads(json.dumps(good))
    bad["arguments"]["text"] = "Take the pills"
    _, _, d2 = cmd.entity_accuracy(case, [bad])
    by = cmd.entity_accuracy_by_type([d1, d2])
    assert by["term:medical"] == {"right": 1, "total": 2, "accuracy": 0.5, "error_rate": 0.5}
    assert by["date"]["accuracy"] == 1.0 and by["number"]["right"] == 1


# ------------------------------------------------------------------ voice variance and noise

def test_voice_variance_and_noisy_plans(suite, plan):
    assert len(suite.voice_variance) == 200 == len(set(suite.voice_variance))
    vv = [s for s in plan if s.family == "voice_variance"]
    assert len(vv) == 2400
    assert set(Counter(s.voice for s in vv).values()) == {200}
    assert len(suite.noisy) == 3 * len(suite.singles)
    for snr in sc.NOISY_SNR_DB:
        shares = Counter(n["condition"]["spec_noise"] for n in suite.noisy
                         if n["condition"]["snr_db"] == snr)
        assert set(shares) == set(sc.SPEC_NOISES) and max(shares.values()) - min(shares.values()) <= 1
    for n in suite.noisy:
        c = n["condition"]
        if c["generator"] == "mix.generate_a4_noise":
            assert c["noise"] == c["spec_noise"]
        else:
            assert c["noise"] in GENERATED_NOISES
        assert c["stand_in"] == (c["spec_noise"] in ("cafe", "street", "tv_speech", "music"))
        assert ("stand-in" in c["noise_source"]) == c["stand_in"]


# ------------------------------------------------------------------ corrections

def test_correction_set(suite):
    cases = suite.corrections
    assert len(cases) == 500 == len({c.case_id for c in cases})
    assert all(c.abandoned or c.abandoned_tools for c in cases)
    assert len({t for c in cases for t in c.tags if t.startswith("correct_")}) == 11
    assert not any(re.search(r"\d", c.spoken) for c in cases)
    assert cases[3].to_json() == sc.corrections(4)[3].to_json()


def _correct_call(case):
    e = case.expect[0]
    args = {}
    for k, v in (e.get("args") or {}).items():
        args[k] = v.get("eq", v.get("icontains")) if isinstance(v, dict) and "regex" not in v else v
    for ent in case.entities:
        if ent.kind == "money":
            val = ent.value.split()[0]
        elif ent.kind == "phone":
            val = f"({ent.value[:3]}) {ent.value[3:6]}-{ent.value[6:]}"
        elif ent.kind == "address":
            val = f"{ent.parts[0]} {ent.parts[1]}, {ent.parts[2]}"
        elif ent.kind in ("duration", "number"):
            val = float(ent.value)
        else:
            val = ent.value
        args[ent.arg] = val
    return {"name": e["tool"], "arguments": args}


def test_correction_ok_on_every_generated_case(suite):
    for case in suite.corrections:
        call = _correct_call(case)
        assert cmd.correction_ok(case, [call]), (case.text, call, cmd.correction_problems(case, [call]))


def test_correction_fails_when_the_abandoned_value_appears_anywhere():
    case = cmd.CommandCase("t", "Text Sam, sorry, Sara, that I'm late", "x",
                           [{"tool": "send_message", "args": {"to": {"icontains": "Sara"}}}],
                           [cmd.Entity("name", "sara", "to")],
                           abandoned=[cmd.Entity("name", "sam", "to")], family="corrections")
    good = [{"name": "send_message", "arguments": {"to": "Sara", "body": "I'm late"}}]
    assert cmd.correction_ok(case, good)
    assert not cmd.correction_ok(case, [{"name": "send_message",
                                         "arguments": {"to": "Sara", "body": "Sam, I'm late"}}])
    assert not cmd.correction_ok(case, [{"name": "send_message", "arguments": {"to": "Sam"}}])
    assert cmd.correction_ok(case, [{"name": "send_message",
                                     "arguments": {"to": "Sara", "body": "Samuel says hi"}}])
    t = cmd.CommandCase("t2", "at 3, no, 4 pm", "x", [{"tool": "add_reminder"}],
                        [cmd.Entity("time", "16:00", "time")],
                        abandoned=[cmd.Entity("time", "15:00", "time")])
    assert cmd.correction_ok(t, [{"name": "add_reminder", "arguments": {"date": "2027-03-15", "time": "4 PM"}}])
    assert not cmd.correction_ok(t, [{"name": "add_reminder", "arguments": {
        "time": "16:00", "text": "meet at 3pm"}}])
    act = next(c for c in sc.corrections() if c.abandoned_tools)
    good_call = _correct_call(act)
    assert cmd.correction_ok(act, [good_call])
    assert not cmd.correction_ok(act, [good_call, {"name": "delete_email", "arguments": {"query": "x"}}])


def test_spoken_correction_scored_through_the_seam(engine, suite):
    case = next(c for c in suite.corrections if "correct_name" in c.tags)
    clip = cmd.render_clip(case, engine, "stub-04")
    wrong = case.abandoned[0].value

    def answer(to):
        return lambda m, t, i: {"text": case.text} if t is None else \
            {"calls": [{"name": "send_message", "arguments": {"to": to, "body": "late"}}]}
    right = case.entities[0].value
    ok = cmd.run_spoken_command(case, clip, configuration="two_pass",
                                client=StubMediaClient(answer(right)), matcher=cmd.ToolbenchMatcher())
    assert ok.match.passed
    bad = cmd.run_spoken_command(case, clip, configuration="two_pass",
                                 client=StubMediaClient(answer(wrong)), matcher=cmd.ToolbenchMatcher())
    assert not bad.match.passed and any("abandoned" in m for m in bad.match.mismatches)


# ------------------------------------------------------------------ disfluency

def test_disfluency_set(suite):
    cases = suite.disfluency
    assert len(cases) == 500 == len({c.case_id for c in cases})
    seeds = [c for c in cases if "voice_seed" in c.tags]
    assert len(seeds) == 16
    kinds = Counter(t for c in cases for t in c.tags if t.startswith("dis_"))
    assert set(kinds) == {f"dis_{k}" for k in sc.DISFLUENCY_KINDS}
    for c in cases:
        if c.script is not None:
            assert "<pause" in c.script and cmd.strip_markers(c.script) == c.spoken
        assert "<pause" not in c.spoken and not re.search(r"\d", c.spoken)
        assert not re.search(r"\b(um|uh|erm)\b", c.text)               # the typed twin is fluent
    paused = [c for c in cases if "dis_long_pause" in c.tags]
    assert paused and all(c.script for c in paused)
    twin = next(c for c in seeds if c.origin["case_id"] == "voice_cal_tomorrow")
    assert twin.text != twin.spoken and twin.spoken.startswith("um")


def test_pause_markers_become_silence(engine):
    plain = cmd.synthesize_script(engine, "set a timer for ten minutes", "stub-01")
    paused = cmd.synthesize_script(engine, "set a timer <pause 1.5> for ten minutes", "stub-01")
    trailing = cmd.synthesize_script(engine, "set a timer for ten minutes <pause 2.0>", "stub-01")
    # The pause replaces the normal gap between the two words.
    assert paused.duration_s == pytest.approx(plain.duration_s + 1.5 - StubEngine.GAP_S, abs=0.002)
    assert paused.eos_s == pytest.approx(paused.duration_s, abs=1e-6)
    assert trailing.duration_s == pytest.approx(plain.duration_s, abs=1e-3)
    words = paused.meta["word_timings"]
    assert [w["word"] for w in words] == "set a timer for ten minutes".split()
    gap = words[3]["start_s"] - words[2]["end_s"]
    assert gap == pytest.approx(1.5, abs=0.01)
    sr = SAMPLE_RATE
    mid = paused.samples[int((words[2]["end_s"] + 0.05) * sr): int((words[3]["start_s"] - 0.05) * sr)]
    assert np.max(np.abs(mid)) == 0.0
    case = cmd.CommandCase("p", "x", "set a timer for ten minutes", [], [],
                           script="set a timer <pause 1.5> for ten minutes")
    clip = cmd.render_clip(case, engine, "stub-01")
    assert clip.duration_s - clip.eos_s == pytest.approx(0.5, abs=1 / sr)
    assert clip.eos_s == pytest.approx(paused.eos_s, abs=1 / sr)


# ------------------------------------------------------------------ follow-ups

def test_follow_up_dialogues(suite):
    ds = suite.dialogues
    assert len(ds) == 400 == len({d.dialogue_id for d in ds})
    assert all(2 <= len(d.turns) <= 4 for d in ds)
    turns = sum(len(d.turns) for d in ds)
    assert 1100 <= turns <= 1300
    assert Counter(d.source for d in ds)["toolbench"] == 34
    templates = [d for d in ds if d.source == "template"]
    assert {t for d in templates for t in d.tags if t.startswith("template:")} == \
        {f"template:{f.__name__[4:]}" for f in sc.FOLLOW_UP_TEMPLATES}
    for d in templates:
        for t in d.turns:
            assert t.expect and t.oracle and not re.search(r"\d", t.spoken)
            assert all(c["name"] in cmd.TOOL_NAMES for c in t.oracle)
        assert any("refers_back" in t.tags for t in d.turns[1:])
    other = next(d for d in templates if "template:other_one" in d.tags)
    assert other.world["calendar"][0]["title"] == other.world["calendar"][1]["title"]
    assert "other one" in other.turns[1].text
    chain = next(d for d in templates if "template:event_chain" in d.tags and len(d.turns) >= 3)
    assert chain.turns[0].state["calendar"][0]["date"] != chain.turns[1].state["calendar"][0]["date"]


def test_agentic_multi_turn_cases_become_dialogues():
    ds = sc.follow_ups(10, agentic_cases=AGENTIC)
    ag = [d for d in ds if d.source == "agentic"]
    assert len(ag) == 1
    d = ag[0]
    assert [t.text for t in d.turns][1] == "yep go ahead"
    assert d.turns[0].forbid == ["messages_send"] and d.turns[1].expect == [{"tool": "messages_send"}]
    assert d.system_policy == "Ask before you send anything." and d.tool_menu == "agentic:neutral"


def _script_model(dialogue, *, fail_turn=None):
    """Transcribes with the turn's typed text and answers with the turn's oracle calls."""
    state = {"turn": 0}

    def answer(messages, tools, index):
        if tools is None:
            text = messages[0]["content"][1]["text"]
            assert "Transcribe" in text
            return {"text": dialogue.turns[state["turn"]].text}
        k = sum(1 for m in messages if m["role"] == "user") - 1
        state["turn"] = min(k + 1, len(dialogue.turns) - 1)
        calls = dialogue.turns[k].oracle
        if k == fail_turn:
            calls = [{"name": "set_timer", "arguments": {"minutes": 99}}]
        return {"calls": [dict(c) for c in calls], "text": ""}
    return answer


def _clips(engine, dialogue):
    return [cmd.finish_clip(cmd.synthesize_script(engine, t.render_text, "stub-03"),
                            f"{dialogue.dialogue_id}/t{i}") for i, t in enumerate(dialogue.turns)]


def test_dialogue_driver_two_pass_keeps_history(engine, suite):
    d = next(x for x in suite.dialogues if "template:event_chain" in x.tags and len(x.turns) == 4)
    client = StubMediaClient(_script_model(d))
    out = run_spoken_dialogue(d, _clips(engine, d), configuration="two_pass", client=client,
                              matcher=cmd.ToolbenchMatcher(), seed=7)
    assert out.passed and out.turns_passed == 4 and [t.passed for t in out.turns] == [True] * 4
    agent = [q for q in client.requests if q["tools"] is not None]
    assert len(agent) == 4 and len(client.requests) == 8
    last = agent[-1]["messages"]
    users = [m["content"] for m in last if m["role"] == "user"]
    assert users == [t.text for t in d.turns]                       # transcripts kept
    assert sum(1 for m in last if m["role"] == "tool") == 3
    tool_msgs = [m["content"] for m in last if m["role"] == "tool"]
    assert tool_msgs[0] == d.turns[0].results["add_calendar_event"]
    assert all(t.command_latency_s and t.command_latency_s > 0.5 for t in out.turns)
    s = out.summary()
    assert s["turns_scored"] == 4 and len(s["turn_command_latency_s"]) == 4 and s["model_time_s"] > 0


def test_dialogue_driver_native_and_pipeline(engine, suite):
    d = next(x for x in suite.dialogues if "template:timer" in x.tags and len(x.turns) >= 3)
    clips = _clips(engine, d)
    native = StubMediaClient(_script_model(d))
    out = run_spoken_dialogue(d, clips, configuration="native", client=native,
                              matcher=cmd.ToolbenchMatcher())
    assert out.passed and len(native.requests) == len(d.turns)
    last = native.requests[-1]
    assert last["audio_s"] == pytest.approx(sum(c.duration_s for c in clips), abs=0.01)
    assert all(t.transcript is None for t in out.turns)
    text_model = StubMediaClient(_script_model(d), accepts_audio=False)
    recogniser = StubMediaClient(lambda m, t, i: {"text": "transcript"}, model="asr")
    piped = run_spoken_dialogue(d, clips, configuration="pipeline", client=text_model,
                                recogniser=recogniser, matcher=cmd.ToolbenchMatcher())
    assert len(recogniser.requests) == len(d.turns) and all(q["audio_s"] == 0 for q in text_model.requests)
    assert piped.passed
    with pytest.raises(ValueError):
        run_spoken_dialogue(d, clips, configuration="pipeline", client=text_model,
                            matcher=cmd.ToolbenchMatcher())
    with pytest.raises(ValueError):
        run_spoken_dialogue(d, clips[:1], configuration="native", client=native,
                            matcher=cmd.ToolbenchMatcher())


def test_dialogue_fails_when_one_turn_fails_and_oracle_history(engine, suite):
    d = next(x for x in suite.dialogues if "template:reminder_chain" in x.tags and len(x.turns) >= 3)
    clips = _clips(engine, d)
    client = StubMediaClient(_script_model(d, fail_turn=1))
    out = run_spoken_dialogue(d, clips, configuration="two_pass", client=client,
                              matcher=cmd.ToolbenchMatcher())
    assert not out.passed and out.turns[1].passed is False and out.turns[0].passed
    assert out.turns[1].command_latency_s is None
    shown = [m for m in client.requests[-1]["messages"] if m.get("tool_calls")]
    assert json.loads(shown[1]["tool_calls"][0]["function"]["arguments"]) == {"minutes": 99}
    client2 = StubMediaClient(_script_model(d, fail_turn=1))
    oracle = run_spoken_dialogue(d, clips, configuration="two_pass", client=client2,
                                 matcher=cmd.ToolbenchMatcher(), history="oracle")
    shown = [m for m in client2.requests[-1]["messages"] if m.get("tool_calls")]
    assert shown[1]["tool_calls"][0]["function"]["name"] == d.turns[1].oracle[0]["name"]
    assert oracle.turns[1].passed is False and oracle.history == "oracle"


def test_context_turns_are_not_scored(engine):
    d = SpokenDialogue("ctx", [SpokenTurn("hello there", "hello there", None),
                               SpokenTurn("set a timer for five minutes", "set a timer for five minutes",
                                          [{"tool": "set_timer"}])])
    client = StubMediaClient(lambda m, t, i: {"text": "x"} if t is None else
                             {"calls": [{"name": "set_timer", "arguments": {"minutes": 5}}]})
    out = run_spoken_dialogue(d, _clips(engine, d), configuration="two_pass", client=client,
                              matcher=cmd.StubMatcher())
    assert out.turns[0].scored is False and out.turns[0].passed is None
    assert out.passed and len(out.scored_turns) == 1


# ------------------------------------------------------------------ the render plan

def test_plan_is_deterministic_and_balanced(suite, plan):
    again = sc.plan_for(sc.build_suite(agentic_cases=[]))
    assert [s.to_json() for s in again] == [s.to_json() for s in plan]
    assert len({s.clip_id for s in plan}) == len(plan)
    fam = Counter(s.family for s in plan)
    assert fam["single"] == len(suite.singles) and fam["noisy"] == 3 * len(suite.singles)
    assert fam["follow_ups"] == sum(len(d.turns) for d in suite.dialogues)
    assert fam["corrections"] == 500 and fam["disfluency"] == 500
    for f in ("single", "corrections", "disfluency", "noisy", "voice_variance"):
        shares = Counter(s.voice for s in plan if s.family == f)
        assert len(shares) == 12
        assert max(shares.values()) - min(shares.values()) <= (3 if f == "noisy" else 1), f
    fu = Counter(s.voice for s in plan if s.family == "follow_ups")
    assert len(fu) == 12 and max(fu.values()) - min(fu.values()) <= 4
    for d in suite.dialogues[:50]:                      # one speaker per dialogue
        assert len({s.voice for s in plan if s.case_id == d.dialogue_id}) == 1
    assert all(s.rate in sc.RATES for s in plan)
    assert {s.turn_index for s in plan if s.family != "follow_ups"} == {None}
    single_voice = {s.case_id: s.voice for s in plan if s.family == "single"}
    assert all(s.voice == single_voice[s.case_id] for s in plan if s.family == "noisy")


def test_a3_plan_with_agentic_cases_counts_them():
    p = sc.a3_plan(agentic_cases=AGENTIC)
    assert any(s.case_id == "a3-ag-sel_ast_rem_901" for s in p if s.family == "single")
    assert any(s.case_id == "a3-fu-ag-rst_neu_askmsg_904" for s in p)


@pytest.mark.parametrize("family", sc.FAMILIES)
def test_materialise_a_sample_of_each_family(engine, suite, plan, family):
    specs = [s for s in plan if s.family == family]
    sample = specs[:: max(1, len(specs) // 6)][:6]
    if family == "noisy":
        sample += [next(s for s in specs if s.condition["spec_noise"] == k) for k in sc.SPEC_NOISES]
    if family == "disfluency":
        sample += [next(s for s in specs if "<pause" in suite.render_text(s))]
    for spec in sample:
        clip = sc.materialise(spec, engine, suite)
        assert clip.duration_s - clip.eos_s == pytest.approx(0.5, abs=1 / SAMPLE_RATE)
        assert clip.meta["voice"] == spec.voice and clip.meta["clip_id"] == spec.clip_id
        assert clip.meta["placeholder"] is True
        if family == "noisy":
            c = clip.meta["condition"]
            assert c["snr_db"] == spec.condition["snr_db"] and c["spec_noise"] == spec.condition["spec_noise"]
            assert "stand_in" in c
        else:
            assert clip.meta["condition"] == {"type": "clean"}
        assert sc.materialise(spec, engine, suite).samples.tobytes() == clip.samples.tobytes()


def test_counts_cli(capsys, tmp_path):
    assert sc.main(["counts", "--agentic-dir", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["cases"]["follow_ups"] == 400 and out["cases"]["corrections"] == 500
    assert out["clips"]["voice_variance"] == 2400
    assert out["conversion"]["toolbench"]["total"] == 383
    assert out["conversion"]["agentic"]["speakable"] == 0
    assert out["clips"]["total"] == sum(v for k, v in out["clips"].items() if k != "total")
