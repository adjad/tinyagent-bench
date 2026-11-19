"""Writing family generators for W3 (rewriting) and W4 (proofreading and editing).

Spec 03 section 9; the case contract and the shared helpers are in `writing.py`.
Everything is invented: neutral senders from `World`, 2027 dates in the P7 form.

W3 builds a source text from "beats" (one sentence or two each). A beat that carries a
planted fact has an anchor list; every rendering of it (source, short, simple, tone
variants) is checked to still contain those anchors, so the reference draft always
keeps the facts. Rewrites are assembled from the matching rendering of each beat.

W4 builds a gold text from paragraph modules with inline markers, then derives the
source (the text with planted errors) from the same markers:

    <<right|wrong|type>>          a planted error (optional 4th field: alternatives, "~"-separated)
    [[span]]                      a protected span (identical in gold and source)
    [[?span|key]]                 an ambiguous span (identical in both) and its key word
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Callable

from edat.quality import writing as W
from edat.quality.normalize import anchor_present, format_p7, parse_anchor
from edat.quality.world import World, fmt_amount

GENERATOR = "writing_gen_edit/1"
STREETS = ["Quay Lane", "Mill Row", "Alder Court", "Harbour Walk", "Birch Terrace", "Kiln Street", "Orchard Way"]
PROJECTS = ["Kestrel", "Marigold", "Thistle", "Lantern", "Copperleaf", "Saltmarsh"]


# ==========================================================================
# W3 beats
# ==========================================================================

def B(fid, src, *, short=None, simple=None, exp=None, core=False, anchors=(), p=1, **tones):
    return {"fid": fid, "src": src, "short": short, "simple": simple, "exp": exp, "core": core,
            "anchors": tuple(anchors), "p": p, **tones}


def _person(w: World, exclude: list[str]) -> dict:
    return w.person(exclude=exclude)


# ---- family A: neutral notices (shorten, expand, simplify, bullets, prose, restructure) ----

def a_move(w: World, sender: dict):
    d1 = w.date_after(12, 40)
    contact = _person(w, [sender["name"]])["name"]
    code = w.code()
    addr = f"{w.rng.randrange(11, 79)} {w.rng.choice(STREETS)}"
    t1 = w.time(8, 10)
    v = {"d1": format_p7(d1, w.year), "addr": addr, "code": code, "contact": contact, "t1": t1}
    anch = {"d1": W.date_anchor(d1), "t1": W.time_anchor(t1)}
    beats = [
        B(None, "Hello everyone, this is the formal notice about the relocation of our office, which has been under "
                "discussion for several months and is now confirmed.",
          simple="Hello everyone, our office is moving. This is the final notice.",
          exp="We know that any move disrupts routines, so this note sets everything out in one place and in the order "
              "you are likely to need it.", p=0),
        B("f1", "The relocation will take place on {d1}, and all staff are asked to have their personal belongings packed "
                "and labelled before the end of the working day preceding the move.",
          short="The office moves on {d1}, and staff should pack and label their belongings the day before.",
          simple="We move on {d1}. Please pack and label your things the day before.",
          exp="Spare boxes and labels will be kept at the front desk for anyone who still needs them, and nobody will be "
              "asked to carry heavy items.", core=True, anchors=("d1",)),
        B("f2", "Our new premises are located at {addr}, a short walk from the station, and the entrance faces the "
                "courtyard instead of the main road.",
          short="The new address is {addr}, a short walk from the station.",
          simple="The new address is {addr}. It is a short walk from the station.",
          exp="The courtyard entrance is quieter than the old street door, and visitors should find it easy to spot.",
          core=True, anchors=("addr",)),
        B("f3", "Staff arriving by car may use the shared car park behind the building, which is secured by a keypad, and "
                "the entry code for the first few weeks is {code}.",
          short="Cars can use the car park behind the building, and the keypad code is {code}.",
          simple="You can park behind the building. The keypad code is {code}.",
          exp="The code will be changed after the first few weeks, and everyone will be told before that happens.",
          core=True, anchors=("code",), p=2),
        B("f4", "Any questions about desks, equipment or access passes should be directed to {contact}, who is "
                "coordinating the move and will respond as quickly as possible.",
          short="Send questions about desks, equipment or passes to {contact}.",
          simple="If you have a question, ask {contact}. {contact} is organising the move.",
          exp="Please check the shared checklist before writing, because many common questions are already answered "
              "there.", core=True, anchors=("contact",), p=2),
        B("f5", "On the first morning the reception desk will reopen at {t1}, so please do not arrive earlier, as the "
                "building manager will not yet be on site.",
          short="Reception reopens at {t1} on the first morning.",
          simple="On the first morning, reception opens at {t1}. Please do not come earlier.",
          exp="Arriving a little after that time will give the cleaners a chance to finish and keep the entrance clear.",
          anchors=("t1",), p=2),
        B(None, "We are grateful for your patience during this period and are confident that the new space will give "
                "every team more room to work comfortably.",
          simple="Thank you for your patience. The new space has more room for every team.",
          exp="A floor plan showing where each team will sit will be shared separately, so that this message stays "
              "easy to scan.", p=3),
    ]
    return v, anch, beats, "an office relocation notice to staff"


def a_status(w: World, sender: dict):
    d1 = w.date_after(14, 45)
    proj = w.rng.choice(PROJECTS)
    amt = w.amount(8000, 90000)
    code = w.code("TKT")
    owner = _person(w, [sender["name"]])["name"]
    num = w.rng.choice([14, 17, 23, 26, 31, 38, 42])
    v = {"d1": format_p7(d1, w.year), "proj": proj, "amt": fmt_amount(amt), "code": code, "owner": owner, "num": num}
    anch = {"d1": W.date_anchor(d1), "amt": W.amount_anchor(amt), "num": W.number_anchor(str(num))}
    beats = [
        B(None, "This is the monthly status update on the {proj} rollout, written for the steering group and anyone else "
                "who needs a clear picture of where we stand.",
          simple="This is the monthly update on the {proj} rollout. It shows where we stand.",
          exp="It is meant to be read in a few minutes and replaces the longer meeting notes that used to follow each "
              "review.", p=0),
        B("f1", "The customer demonstration is now scheduled for {d1}, and every team has agreed to have its part ready "
                "for a full run-through the day before.",
          short="The customer demo is on {d1}, and each team's part should be ready the day before.",
          simple="The demo for the customer is on {d1}. Each team should be ready the day before.",
          exp="A dry run will help us catch small problems early and leave time to fix them without rushing.",
          core=True, anchors=("d1",)),
        B("f2", "The approved budget stands at {amt}, and spending to date remains within that figure, with no request "
                "for additional funds expected at this stage.",
          short="The approved budget is {amt} and spending is within it.",
          simple="The budget is {amt}. So far we have spent less than that.",
          exp="Any change to that position will be reported at the next review so that nobody is surprised by it.",
          core=True, anchors=("amt",)),
        B("f3", "The one blocking issue is tracked under ticket {code}, which concerns the login service and has been "
                "escalated to the platform team for a decision.",
          short="The blocking issue is ticket {code}, about the login service.",
          simple="One problem is stopping us. It is ticket {code}, and it is about the login service.",
          exp="The platform team knows the issue is blocking and has promised to look at it before anything else.",
          core=True, anchors=("code",), p=2),
        B("f4", "{owner} is the named owner of the rollout and will chair the weekly check-in, collecting questions from "
                "the other teams beforehand.",
          short="{owner} owns the rollout and chairs the weekly check-in.",
          simple="{owner} is in charge of the rollout and leads the weekly check-in.",
          exp="Questions sent in advance are easier to answer well, so please send them early instead of raising them on "
              "the day.", core=True, anchors=("owner",), p=2),
        B("f5", "Testing has found {num} open defects, most of them minor and cosmetic, and none of them affects the core "
                "workflow of the product.",
          short="Testing has {num} open defects, none of them serious.",
          simple="Testing found {num} open defects. Most are small, and none stops the main workflow.",
          exp="The small ones will be cleared in the normal course of work and do not need separate attention.",
          anchors=("num",), p=2),
        B(None, "Overall the team is confident that the plan is sound, and we will report again next month or sooner if "
                "anything changes materially.",
          simple="The team is confident in the plan. We will report again next month, or sooner if something changes.",
          exp="Thank you to everyone who has kept their updates current, because it makes this summary far quicker to "
              "write.", p=3),
    ]
    return v, anch, beats, "a monthly project status update"


def a_policy(w: World, sender: dict):
    d1 = w.date_after(10, 35)
    a1 = w.amount(40, 120)
    a2 = w.amount(18, 60)
    code = w.code("EXP")
    approver = _person(w, [sender["name"]])["name"]
    t1 = w.time(15, 19)
    v = {"d1": format_p7(d1, w.year), "amt1": fmt_amount(a1), "amt2": fmt_amount(a2), "code": code,
         "approver": approver, "t1": t1}
    anch = {"d1": W.date_anchor(d1), "amt1": W.amount_anchor(a1), "amt2": W.amount_anchor(a2), "t1": W.time_anchor(t1)}
    beats = [
        B(None, "This note restates the expense policy for travel and client meals, which applies to every member of "
                "staff regardless of seniority or team.",
          simple="This note restates the expense rules for travel and client meals. They apply to everyone.",
          exp="The rules have not changed in spirit, but several people asked for a single clear version, so here it is.",
          p=0),
        B("f1", "Meals with clients are reimbursed up to a limit of {amt1} per person, and any amount above that limit must "
                "be approved in advance by a manager.",
          short="Client meals are reimbursed up to {amt1} per person, and more needs advance approval.",
          simple="We pay for client meals up to {amt1} per person. If it costs more, a manager must say yes first.",
          exp="Asking before the meal is far easier than explaining afterwards, and managers are happy to answer quickly.",
          core=True, anchors=("amt1",)),
        B("f2", "Every claim must be submitted on form {code}, and claims received after {d1} cannot be processed until the "
                "following month's cycle.",
          short="Submit claims on form {code} by {d1}, or they wait for the next cycle.",
          simple="Use form {code} for every claim. Send it by {d1}, or it waits until next month.",
          exp="Filing a little early also gives finance time to spot anything missing before the cutoff.",
          core=True, anchors=("code", "d1")),
        B("f3", "Receipts are required for any single purchase above {amt2}, and photographs of paper receipts are "
                "acceptable provided that every line is legible.",
          short="Receipts are needed for any purchase above {amt2}, and clear photos are fine.",
          simple="Keep the receipt for anything above {amt2}. A clear photo of it is fine.",
          exp="A blurred or cropped photo is the most common reason a claim is sent back, so check it before sending.",
          core=True, anchors=("amt2",), p=2),
        B("f4", "Approval of claims rests with {approver}, who may ask for further explanation before authorising payment "
                "and whose decision is final.",
          short="{approver} approves claims and may ask for more explanation first.",
          simple="{approver} approves claims. {approver} may ask you to explain first.",
          exp="Short, factual answers usually settle any question straight away and keep the process moving.",
          core=True, anchors=("approver",), p=2),
        B("f5", "The online claims portal closes at {t1} on the deadline day, and no extensions are granted for late "
                "technical problems.",
          short="The claims portal closes at {t1} on the deadline day.",
          simple="The claims portal closes at {t1} on the last day. We cannot give more time for tech problems.",
          exp="Submitting well before that hour avoids any last-minute trouble with connections or passwords.",
          anchors=("t1",), p=2),
        B(None, "We appreciate that paperwork is nobody's favourite task, and we have tried to keep the requirements as "
                "light as the auditors will allow.",
          simple="We know paperwork is boring, so we keep the rules as light as we can.",
          exp="If any step seems unclear, tell us, and we will improve the wording for the next version.", p=3),
    ]
    return v, anch, beats, "an expense policy reminder"


def a_water(w: World, sender: dict):
    d1 = w.date_after(10, 40)
    t1 = w.time(8, 10)
    t2 = w.time(13, 17)
    place = w.place()
    contact = _person(w, [sender["name"]])["name"]
    amt = w.amount(15, 80)
    v = {"d1": format_p7(d1, w.year), "t1": t1, "t2": t2, "place": place, "contact": contact, "amt": fmt_amount(amt)}
    anch = {"d1": W.date_anchor(d1), "t1": W.time_anchor(t1), "t2": W.time_anchor(t2), "amt": W.amount_anchor(amt)}
    beats = [
        B(None, "We are writing to let residents know about essential maintenance on the water system, which is needed to "
                "replace a worn valve before the winter weather arrives.",
          simple="We are writing about repair work on the water system. A worn valve must be replaced before winter.",
          exp="The valve has been monitored for some time, and the engineers advise that it is safest to replace it now.",
          p=0),
        B("f1", "The water supply to the building will be switched off on {d1}, between {t1} and {t2}, and will return "
                "gradually as the system is refilled.",
          short="Water will be off on {d1} from {t1} to {t2}.",
          simple="The water will be off on {d1} from {t1} to {t2}. It will come back slowly after that.",
          exp="Pressure may be low for a short while after the supply returns, which is normal while the pipes refill.",
          core=True, anchors=("d1", "t1", "t2")),
        B("f2", "Residents who need water during the shutdown can fill containers beforehand or use the tap at {place}, "
                "which is open throughout the day.",
          short="A tap at {place} is open for residents during the shutdown.",
          simple="If you need water, fill containers first or use the tap at {place}.",
          exp="Carrying a few full bottles upstairs the evening before is usually all that most households need.",
          core=True, anchors=("place",)),
        B("f3", "Please ask {contact}, our building manager, about any health or mobility concerns, so that suitable "
                "arrangements can be made in good time.",
          short="Ask {contact}, the building manager, about health or mobility needs.",
          simple="If you have health or mobility needs, tell {contact}, the building manager.",
          exp="The sooner we hear about any special requirements, the easier it is to arrange something suitable.",
          core=True, anchors=("contact",), p=2),
        B(None, "Hot water and heating may take some hours to settle afterwards, and a small amount of air in the pipes "
                "is normal and harmless.",
          simple="Hot water and heating may take some hours to settle. A little air in the pipes is normal.",
          exp="If the radiators sound unusual, bleeding them with the key from the caretaker's office will quieten them.",
          p=2),
        B("f5", "As a goodwill gesture, a credit of {amt} will be applied to each household's next service charge "
                "statement.",
          short="Each household will get a credit of {amt}.",
          simple="Each home will get a credit of {amt} on the next bill.",
          exp="The credit will appear as a separate line, so it should be simple to find on the statement.",
          anchors=("amt",), p=2),
        B(None, "We apologise for the inconvenience and thank you for your understanding while this necessary work is "
                "carried out.",
          simple="Sorry for the trouble, and thank you for understanding.",
          exp="Updates will be posted at the entrance if the schedule changes for any reason.", p=3),
    ]
    return v, anch, beats, "a water maintenance notice to residents"


A_SCENARIOS = [a_move, a_status, a_policy, a_water]


# ---- family B: a hesitant note to rewrite in a tone ----

def _greet_sign(src_g, formal_g, casual_g, warm_g, firm_g, src_s, formal_s, casual_s, warm_s, firm_s):
    return (B(None, src_g, formal=formal_g, casual=casual_g, warm=warm_g, firm=firm_g, p=0),
            B(None, src_s, formal=formal_s, casual=casual_s, warm=warm_s, firm=firm_s, p=9))


def b_invoice(w: World, sender: dict, rcp: dict):
    d1 = w.date_after(-30, -8)
    d2 = w.date_after(15, 30)
    amt = w.amount(900, 4800)
    inv = w.code("INV")
    ref = w.code("REF")
    acct = _person(w, [sender["name"], rcp["name"]])["name"]
    v = {"d1": format_p7(d1, w.year), "d2": format_p7(d2, w.year), "amt": fmt_amount(amt), "inv": inv, "ref": ref,
         "acct": acct, "rf": rcp["first"], "sf": sender["first"]}
    anch = {"d1": W.date_anchor(d1), "d2": W.date_anchor(d2), "amt": W.amount_anchor(amt)}
    g, s = _greet_sign("Hi {rf},", "Dear {rf},", "Hey {rf},", "Hi {rf}, thank you again for all the work on the site.",
                       "{rf},",
                       "Thanks so much,\n{sf}", "Yours sincerely,\n{sf}", "Cheers,\n{sf}",
                       "With thanks and best wishes,\n{sf}", "Regards,\n{sf}")
    beats = [
        g,
        B("f1", "I wanted to mention, if it isn't a bother, that invoice {inv} for {amt} doesn't seem to have been settled "
                "yet, and as far as I can tell it fell due on {d1}, though I may have missed something on my side.",
          formal="I am writing to note that invoice {inv} for {amt}, which fell due on {d1}, remains unpaid on our records.",
          casual="Quick one: invoice {inv} for {amt} was due on {d1} and I can't see that it's been paid yet.",
          warm="I noticed that invoice {inv} for {amt}, which was due on {d1}, hasn't come through yet, and I wanted to "
               "check in with you about it.",
          firm="Invoice {inv} for {amt} was due on {d1} and has not been paid. Please settle it now.",
          core=True, anchors=("inv", "amt", "d1")),
        B("f2", "We would really prefer not to stop the work, but if the payment still hasn't arrived by {d2}, I'm afraid "
                "we might have to pause the project until it does, and I'd hate for it to come to that.",
          formal="Should payment not be received by {d2}, work on the project will be paused until it is.",
          casual="If it's still not in by {d2}, we'll have to pause the work until it lands, and I'd rather not.",
          warm="I would hate to interrupt the project, but if payment hasn't reached us by {d2}, we would need to pause "
               "the work until it arrives.",
          firm="If payment has not arrived by {d2}, work on the project will pause until it does.",
          core=True, anchors=("d2",)),
        B("f3", "The bank details are on the invoice itself, and the transfer reference to use is {ref}, which lets us "
                "match the payment to the right job without any confusion.",
          formal="The bank details appear on the invoice. Please quote the transfer reference {ref} so that the payment "
                 "can be matched to the correct job.",
          casual="Bank details are on the invoice, and the reference to use is {ref} so we can match it to the right job.",
          warm="The bank details are on the invoice, and if you use the reference {ref} we can match your payment to the "
               "right job straight away.",
          firm="Pay using the bank details on the invoice, and quote the reference {ref} so the payment is matched to "
               "the right job.",
          core=True, anchors=("ref",), p=2),
        B("f4", "If something is holding it up, please call or message {acct}, who looks after our accounts and can sort "
                "out any questions about the paperwork.",
          formal="Should anything be delaying payment, please contact {acct}, who manages our accounts, regarding any "
                 "query about the paperwork.",
          casual="If something's holding it up, message {acct}, who handles our accounts and can sort out any paperwork "
                 "questions.",
          warm="If something is holding it up, please do get in touch with {acct}, who looks after our accounts and will "
               "be glad to help with any paperwork questions.",
          firm="If something is delaying payment, contact {acct}, who handles our accounts, straight away.",
          core=True, anchors=("acct",), p=2),
        B(None, "I realise that things get busy and I'm sure this is simply an oversight, so there's no need to worry "
                "about it.",
          formal="I trust that this is simply an oversight.", casual="I'm sure it just slipped through the cracks.",
          warm="I know how busy things get, and I'm sure it simply slipped through in a hectic week.",
          firm="I expect this to be settled promptly.", p=2),
        s,
    ]
    return v, anch, beats, "a note to a contractor about an unpaid invoice", "contractor"


def b_fence(w: World, sender: dict, rcp: dict):
    d1 = w.date_after(7, 25)
    t1 = w.time(8, 11)
    amt = w.amount(300, 1500)
    org = w.org()[0]
    code = w.code()
    v = {"d1": format_p7(d1, w.year), "t1": t1, "amt": fmt_amount(amt), "org": org, "code": code,
         "rf": rcp["first"], "sf": sender["first"]}
    anch = {"d1": W.date_anchor(d1), "t1": W.time_anchor(t1), "amt": W.amount_anchor(amt)}
    g, s = _greet_sign("Hi {rf},", "Dear {rf},", "Hey {rf},",
                       "Hi {rf}, thank you for being so easy to deal with about the fence.", "{rf},",
                       "Thanks,\n{sf}", "Yours sincerely,\n{sf}", "Cheers,\n{sf}", "Warmest wishes,\n{sf}",
                       "Regards,\n{sf}")
    beats = [
        g,
        B("f1", "I just wanted to let you know, in case it's inconvenient, that the repair crew from {org} is planning to "
                "come on {d1}, arriving at about {t1}, to replace the damaged fence panels.",
          formal="I am writing to inform you that the repair crew from {org} will arrive on {d1} at {t1} to replace the "
                 "damaged fence panels.",
          casual="Heads up: the crew from {org} is coming on {d1} around {t1} to swap out the broken fence panels.",
          warm="I wanted to let you know that the repair crew from {org} will be coming on {d1} at around {t1} to replace "
               "the damaged panels, and I'm so glad we're getting it fixed.",
          firm="The crew from {org} will arrive on {d1} at {t1} to replace the damaged fence panels. Please make sure they "
               "can reach the panels.",
          core=True, anchors=("org", "d1", "t1")),
        B("f2", "The total cost comes to {amt}, and if you're happy with it I thought we could split it evenly, though I'm "
                "open to other ideas if that doesn't suit you.",
          formal="The total cost is {amt}. I propose that we divide it equally, and I am willing to consider alternatives.",
          casual="Total's {amt}. I figured we'd split it down the middle, but tell me if you'd rather do it differently.",
          warm="The total comes to {amt}, and I thought we might split it evenly, but I'm very happy to talk it through if "
               "another way works better for you.",
          firm="The total cost is {amt}. I am proposing that we split it evenly; please confirm that this works.",
          core=True, anchors=("amt",)),
        B("f3", "The side gate will be left unlocked for them, but if it gets locked the code is {code}, and I'd be "
                "grateful if you could keep that between us.",
          formal="The side gate will be left unlocked for the crew. Should it be locked, the code is {code}, which I would "
                 "ask you to keep confidential.",
          casual="I'll leave the side gate open for them, but if it's locked the code's {code}. Keep it to yourself, "
                 "please.",
          warm="I'll leave the side gate unlocked for them, and if it ends up locked the code is {code}, which I know I "
               "can trust you to keep to yourself.",
          firm="The side gate will be unlocked. If it is locked, the code is {code}; do not share it.",
          core=True, anchors=("code",), p=2),
        B(None, "I'm sorry for any noise or mess, and I'll make sure the crew tidy up properly before they leave.",
          formal="I apologise for any noise or disruption, and I will ensure the crew tidies the area before leaving.",
          casual="Sorry in advance for the noise, and I'll make sure they clean up.",
          warm="I'm sorry for any noise or mess, and I promise to make sure the crew tidies up before they go.",
          firm="There may be noise. The crew will tidy up before they leave.", p=2),
        B(None, "Please let me know either way when you have a moment, because I'd like to confirm everything with the "
                "crew by the end of the week.",
          formal="I would be grateful for your reply at your earliest convenience so that I may confirm arrangements with "
                 "the crew.",
          casual="Let me know either way when you get a sec, so I can confirm it with the crew.",
          warm="Please let me know either way when you have a moment, so I can confirm everything with the crew.",
          firm="Please let me know either way, so I can confirm everything with the crew.", p=2),
        s,
    ]
    return v, anch, beats, "a note to a neighbour about a shared fence repair", "neighbour"


def b_delivery(w: World, sender: dict, rcp: dict):
    d1 = w.date_after(5, 12)
    d2 = w.date_after(13, 24)
    amt = w.amount(400, 3000)
    order = w.code("ORD")
    wh = _person(w, [sender["name"], rcp["name"]])["name"]
    v = {"d1": format_p7(d1, w.year), "d2": format_p7(d2, w.year), "amt": fmt_amount(amt), "order": order, "wh": wh,
         "rf": rcp["first"], "sf": sender["first"]}
    anch = {"d1": W.date_anchor(d1), "d2": W.date_anchor(d2), "amt": W.amount_anchor(amt)}
    g, s = _greet_sign("Hi {rf},", "Dear {rf},", "Hey {rf},", "Hi {rf}, thanks for your help so far.", "{rf},",
                       "Many thanks,\n{sf}", "Yours sincerely,\n{sf}", "Cheers,\n{sf}", "With best wishes,\n{sf}",
                       "Regards,\n{sf}")
    beats = [
        g,
        B("f1", "I'm sorry to chase, but order {order} was promised for {d1} and, as far as we can tell, it hasn't "
                "arrived at the warehouse, so I was wondering if there has been a hold-up somewhere.",
          formal="I am writing about order {order}, which was promised for {d1} and has not yet arrived at our warehouse. "
                 "I would appreciate an explanation of the delay.",
          casual="Chasing order {order}: it was meant to land on {d1} and it still hasn't shown up at the warehouse. "
                 "What's going on?",
          warm="Order {order} was promised for {d1} and hasn't reached our warehouse yet, so I wanted to check whether "
               "there's been a hold-up.",
          firm="Order {order} was promised for {d1} and has not arrived at our warehouse. Please explain the delay.",
          core=True, anchors=("order", "d1")),
        B("f2", "If at all possible, could it be delivered by {d2} instead? That would still let us meet our own "
                "commitments, though only just, and we'd really appreciate your help.",
          formal="Could you please arrange delivery by {d2}? That would only just allow us to meet our own commitments.",
          casual="Can you get it here by {d2}? That'd just about let us hit our own deadlines.",
          warm="Would it be possible to deliver by {d2} instead? That would just about let us meet our own commitments, "
               "and we'd be so grateful for your help.",
          firm="Deliver by {d2}. That is the latest date that lets us meet our own commitments.",
          core=True, anchors=("d2",)),
        B("f3", "We paid a deposit of {amt} when we placed the order, and if delivery by then isn't possible I would have "
                "to ask about getting that deposit back.",
          formal="A deposit of {amt} was paid at the time of the order. If delivery by that date is not possible, I will "
                 "need to request a refund of the deposit.",
          casual="We put down a {amt} deposit, so if you can't make that date I'll have to ask for it back.",
          warm="We paid a deposit of {amt} with the order, and if that date isn't possible I'm afraid I'd need to ask "
               "about getting it back, though I'd much rather not.",
          firm="We paid a deposit of {amt}. If you cannot deliver by that date, I will request a refund of the deposit.",
          core=True, anchors=("amt",), p=2),
        B("f4", "For anything about the delivery slot, please speak to {wh}, who runs our warehouse and can take the lorry "
                "at short notice.",
          formal="For matters concerning the delivery slot, please contact {wh}, who manages our warehouse and can receive "
                 "the delivery at short notice.",
          casual="For the delivery slot, talk to {wh}, who runs our warehouse and can take it at short notice.",
          warm="For anything about the delivery slot, {wh} runs our warehouse and will happily take the delivery at short "
               "notice.",
          firm="For the delivery slot, contact {wh}, who runs our warehouse and can take the delivery at short notice.",
          core=True, anchors=("wh",), p=2),
        B(None, "I know these things are rarely anyone's fault, and I'd like to sort this out together without any fuss.",
          formal="I appreciate that such delays are rarely deliberate, and I would like us to resolve this cooperatively.",
          casual="I know these things happen and I'd like to sort it out without any fuss.",
          warm="I know these things are rarely anyone's fault, and I'd really like us to sort this out together.",
          firm="I want this resolved without further delay.", p=2),
        s,
    ]
    return v, anch, beats, "a note to a supplier about a late delivery", "supplier"


def b_volunteers(w: World, sender: dict, rcp: dict):
    d1 = w.date_after(6, 20)
    t1 = w.time(7, 10)
    place = w.place()
    num = w.rng.choice([14, 16, 18, 22, 24, 27])
    coord = _person(w, [sender["name"], rcp["name"]])["name"]
    v = {"d1": format_p7(d1, w.year), "t1": t1, "place": place, "num": num, "coord": coord,
         "rf": rcp["first"], "sf": sender["first"]}
    anch = {"d1": W.date_anchor(d1), "t1": W.time_anchor(t1), "num": W.number_anchor(str(num))}
    g, s = _greet_sign("Hi {rf},", "Dear {rf},", "Hey {rf},", "Hi {rf}, thank you so much for rallying everyone.",
                       "{rf},",
                       "Thanks again,\n{sf}", "Yours sincerely,\n{sf}", "Cheers,\n{sf}", "With gratitude,\n{sf}",
                       "Regards,\n{sf}")
    beats = [
        g,
        B("f1", "I'm hoping, if it's not too much to ask, that you can confirm your volunteers for the weekend fair by "
                "{d1}, since the organisers need the list before they finalise the stalls.",
          formal="I would be grateful if you could confirm your volunteers for the weekend fair by {d1}, as the "
                 "organisers require the list before finalising the stalls.",
          casual="Can you confirm your fair volunteers by {d1}? The organisers need the list before they sort the stalls.",
          warm="Could you confirm your volunteers for the weekend fair by {d1}? The organisers need the list before "
               "they finalise the stalls, and I'm so grateful for your help.",
          firm="Confirm your volunteers for the weekend fair by {d1}. The organisers need the list before they finalise "
               "the stalls.",
          core=True, anchors=("d1",)),
        B("f2", "We'll need {num} people in total, and the first shift starts at {t1} at {place}, with a short briefing "
                "just before so everybody knows where to stand.",
          formal="We require {num} people in total. The first shift begins at {t1} at {place}, preceded by a short "
                 "briefing so that everyone knows their position.",
          casual="We need {num} people in all, starting at {t1} at {place}, with a quick briefing first so everyone knows "
                 "where to stand.",
          warm="We'll need {num} lovely people in total. The first shift starts at {t1} at {place}, with a short briefing "
               "just before so everybody feels settled.",
          firm="We need {num} people in total. The first shift starts at {t1} at {place}. Be there for the short briefing "
               "just before it.",
          core=True, anchors=("num", "t1", "place")),
        B("f3", "Anyone who can't make it should tell {coord} as soon as possible, because it's much easier to find a "
                "replacement early than on the morning.",
          formal="Anyone who is unable to attend should inform {coord} as soon as possible, since a replacement is easier "
                 "to find early than on the morning itself.",
          casual="If anyone can't make it, tell {coord} early. Finding a swap is way easier before the morning.",
          warm="If anyone can't make it, that's completely fine, just tell {coord} as soon as you can so we can find a "
               "replacement in good time.",
          firm="Anyone who cannot attend must tell {coord} as soon as possible. A replacement is easier to find early.",
          core=True, anchors=("coord",), p=2),
        B(None, "Please remind everyone to wear comfortable shoes and bring a water bottle, as the stalls are outdoors and "
                "it can get quite warm.",
          formal="Please remind everyone to wear comfortable shoes and to bring water, as the stalls are outdoors and the "
                 "weather may be warm.",
          casual="Tell everyone to wear comfy shoes and bring water; the stalls are outdoors and it gets warm.",
          warm="A gentle reminder to wear comfy shoes and bring a water bottle, since the stalls are outdoors and it can "
               "get warm.",
          firm="Everyone must wear comfortable shoes and bring water. The stalls are outdoors and it can get warm.", p=2),
        B(None, "Thanks for taking care of this; it really does make the day run smoothly for everyone involved.",
          formal="Thank you for attending to this, which greatly assists the smooth running of the day.",
          casual="Thanks for sorting this, it really helps the day run smoothly.",
          warm="Thank you for taking care of this; it really does make the day run smoothly for everyone.",
          firm="This is necessary for the day to run smoothly.", p=2),
        s,
    ]
    return v, anch, beats, "a note to a volunteer group lead", "volunteer lead"


B_SCENARIOS = [b_invoice, b_fence, b_delivery, b_volunteers]


# ==========================================================================
# W3 assembly
# ==========================================================================

def _fmt(s, v):
    return s.format(**v) if s else None


def _chunk(texts: list[str], size: int = 3) -> str:
    paras = [" ".join(texts[i:i + size]) for i in range(0, len(texts), size)]
    return "\n\n".join(paras)


def _by_para(beats, key, v) -> str:
    groups: dict[int, list[str]] = {}
    for b in beats:
        t = _fmt(b.get(key), v)
        if t:
            groups.setdefault(b["p"], []).append(t)
    return "\n\n".join(" ".join(groups[k]) for k in sorted(groups))


def _anchors_of(beat, v, anch):
    return [anch.get(k, str(v[k])) for k in beat["anchors"]]


def _check_anchors(text: str, beat, v, anch, what: str, year: int):
    for a in _anchors_of(beat, v, anch):
        t, val, al = parse_anchor(a)
        if not anchor_present(text, t, val, aliases=al, case_year=year):
            raise AssertionError(f"{what}: anchor {a!r} missing from {text!r}")


def _facts(beats, v, anch, year):
    facts = []
    for b in beats:
        if not b["fid"]:
            continue
        txt = _fmt(b["short"] or b["src"], v)
        facts.append({"id": b["fid"], "text": txt, "anchors": _anchors_of(b, v, anch), "core": bool(b["core"])})
    return facts


def _wc(s: str) -> int:
    """Whitespace words, not counting list markers."""
    return len(re.sub(r"^- ", "", s, flags=re.M).split())


def _ceil5(n: float) -> int:
    n = int(n)
    return n if n % 5 == 0 else n + (5 - n % 5)


TASKS = {
    "shorten": "Shorten the text below. Keep the details that matter most.",
    "expand": "Expand the text below with fuller explanation and a smoother flow. Do not add any new facts, names, dates, "
              "numbers or promises.",
    "tone_formal": "Rewrite the text below in a formal tone, as for a business letter.",
    "tone_casual": "Rewrite the text below in a casual, friendly tone, as to someone I know well.",
    "tone_warm": "Rewrite the text below in a warm, appreciative tone.",
    "tone_firm": "Rewrite the text below in a firm, direct tone. Stay polite, and add no new threats and no new dates.",
    "simplify": "Rewrite the text below so a general reader can follow it easily: plain words and short sentences.",
    "to_bullets": "Turn the text below into a bulleted list, one bullet per point, each line starting with \"- \".",
    "to_prose": "Turn the bullet list below into ordinary prose paragraphs, with no list.",
    "restructure": "Reorganise the text below for clarity: put the main point first and keep related details together. "
                   "Do not make it longer.",
}


def _w3(kind: str, seed: int, index: int, profile) -> dict:
    w = World.make(seed)
    sender_c = w.person()
    sender = {"name": sender_c["name"], "preferred_name": sender_c["first"]}
    year = w.year
    tone = kind.startswith("tone_")
    cons: dict = {}
    target_shape = None
    recipient = None
    if tone:
        rcp = w.person(exclude=[sender_c["name"]])
        fn = w.rng.choice(B_SCENARIOS)
        v, anch, beats, what, rel = fn(w, sender_c, rcp)
        recipient = {"name": rcp["name"], "relation": rel}
        key = kind.split("_", 1)[1]
        src = _by_para(beats, "src", v)
        ref = _by_para(beats, key, v)
        for b in beats:
            if b["fid"]:
                for k in ("src", key):
                    _check_anchors(_fmt(b[k], v), b, v, anch, f"{kind}/{k}", year)
    else:
        fn = w.rng.choice(A_SCENARIOS)
        v, anch, beats, what = fn(w, sender_c)
        order = list(range(len(beats)))
        srcs = [_fmt(b["src"], v) for b in beats]
        if kind == "to_prose":
            src = "\n".join("- " + t for t in srcs)
            ref = _chunk(srcs)
        elif kind == "restructure":
            canon = srcs
            for _ in range(50):
                w.rng.shuffle(order)
                if order != sorted(order) and order[0] != 0:
                    break
            src = _chunk([srcs[i] for i in order])
            ref = _chunk(canon)
        else:
            src = _chunk(srcs)
            if kind == "shorten":
                ref = " ".join(_fmt(b["short"], v) for b in beats if b["fid"] and b["core"])
            elif kind == "expand":
                ref = _chunk([_fmt(b["src"], v) + " " + _fmt(b["exp"], v) for b in beats])
            elif kind == "simplify":
                ref = _chunk([_fmt(b["simple"], v) for b in beats if b["simple"]])
            elif kind == "to_bullets":
                ref = "\n".join("- " + _fmt(b["short"], v) for b in beats if b["fid"])
            else:
                raise KeyError(kind)
        for b in beats:
            if b["fid"]:
                for k in ("src", "short", "simple"):
                    if b[k]:
                        _check_anchors(_fmt(b[k], v), b, v, anch, f"{kind}/{k}", year)
    n_src, n_ref = _wc(src), _wc(ref)
    assert 120 <= n_src <= 260, (kind, n_src)

    if kind == "shorten":
        cap = _ceil5(n_ref * 1.2)
        assert cap <= 0.5 * n_src, (cap, n_src)
        cons = {"max_words": cap}
    elif kind == "expand":
        lo, hi = int(n_ref * 0.8) // 5 * 5, _ceil5(n_ref * 1.2)
        assert lo >= 1.25 * n_src, (lo, n_src)
        cons = {"min_words": lo, "max_words": hi}
    elif kind in ("simplify", "restructure"):
        cons = {"max_words": n_src}
    elif kind == "to_bullets":
        target_shape = "bullets"
    elif kind == "to_prose":
        target_shape = "paragraphs"

    facts = _facts(beats, v, anch, year)
    if kind == "shorten":
        assert 3 <= sum(f["core"] for f in facts) <= 5
    case = W.new_case(
        sub="W3", kind=kind, seed=seed, generator=GENERATOR, today=w.today, profile=None, sender=sender,
        recipient=recipient, purpose=f"rewrite {what}: {kind.replace('_', ' ')}",
        constraints=cons, context={"source_text": src}, facts=facts, target_shape=target_shape,
        reference_draft=ref)
    lines = [TASKS[kind] + " Do not change any facts: every name, date, time, amount, number and code must stay exactly "
             "as written.", "", "The text:", '"""', src, '"""']
    lines += W.render_constraints(case["constraints"], case["surface"])
    case["turns"] = ["\n".join(lines)]
    return W.finish(case)


# ==========================================================================
# W4 modules
# ==========================================================================
# General modules: errors only.
GENERAL = [
    "Thanks for coming to the planning session on {d1}. <<It's been a|Its been a|apostrophe>> long few weeks for the {pl} "
    "team, and I want to say how much I appreciate the effort. Please <<receive the|recieve the|spelling>> notes by "
    "email before the end of the week.",

    "I checked the shared drive this morning. <<The notes were|The notes was|agreement>> still there, so nothing is lost. "
    "We should <<definitely move|definately move|spelling>> them to the archive folder before the audit on {d2}.",

    "<<The client's request|The clients request|apostrophe>> arrived late on {d1}, so we pushed the delivery back. Please "
    "bring <<your laptop to the|your laptop to to the|doubled_word>> training room, and remember that badges are "
    "collected at the front desk.",

    "Both teams left <<their coats|there coats|homophone>> in the hallway after the fire drill, and the caretaker had to "
    "carry them back one by one. It was <<a lot of|alot of|spelling>> extra work for a Tuesday afternoon.",

    "The meeting <<ran long; we missed|ran long, we missed|comma_splice|ran long. We missed>> the last bus home, so {n1} "
    "gave three of us a lift. I <<should have called|should of called|grammar>> ahead to say we would be late.",

    "<<Everyone has|Everyone have|agreement>> signed the safety form, and <<you're welcome to collect|your welcome to "
    "collect|apostrophe>> a copy from {n2} at any time. The cabinet in room {num} stays locked outside office hours.",

    "The second option is <<cheaper than the|cheaper then the|spelling>> first, but it takes longer to set up. I think "
    "the delay <<would affect the|would effect the|word_choice>> launch less than the extra cost would.",

    "We can <<accommodate up to|accomodate up to|spelling>> {num} guests in the main room. The error with the booking form "
    "<<occurred only|occured only|spelling>> once, and the vendor has already fixed it.",

    "The printer's toner <<is running|are running|agreement>> low again. Please check <<whether the|wether the|spelling>> "
    "spare cartridges in the cupboard are still sealed, and charge any new order to the {pl} account.",

    "<<Everyone's input|Everyones input|apostrophe>> has been added to the draft, and the final version will go out after "
    "{n3} reviews the numbers. Please <<keep your comments|keep you're comments|word_choice>> short and specific, since "
    "there is little time left.",

    "<<If we lose the venue|If we loose the venue|spelling>> we will have to start again, so I booked a second room at "
    "{pl} just in case. The deposit <<was paid yesterday|was payed yesterday|spelling>>, and the receipt is in my bag.",

    "The two managers <<are meeting|is meeting|agreement>> on {d1} to settle the budget. <<It's a short|Its a short|"
    "apostrophe>> agenda, but a lot depends on the outcome, so please keep that afternoon free.",

    "<<I received your|I recieved your|spelling>> message about the delay, and <<I'm sorry|Im sorry|apostrophe>> for the "
    "confusion. The parcel left the depot on {d1}, and the courier says it should arrive soon.",

    "<<Rehearsals begin|Rehearsals begins|agreement>> at the {pl} on {d2}, and the doors open early. Parents <<who can't "
    "attend|who cant attend|apostrophe>> should tell {n2} beforehand, so that a place is not wasted.",
]

# Protected modules: names, numbers and voice markers that must survive untouched.
PROTECTED = [
    "Quick update from the {pl} site: we're [[gonna]] ship the [[{code}]] batch on [[{d1}]], [[come hell or high water]]. "
    "[[Not ideal. Not even close.]] But <<the crew's overtime|the crews overtime|apostrophe>> has been amazing.",

    "I spoke with [[{fn1}]] about the [[{amt} EUR]] quote, and honestly it's a [[no-brainer]]. [[Kinda steep though.]] We "
    "<<should have asked|should of asked|grammar>> for a second quote, but <<it's too late|its too late|apostrophe>> now.",

    "Heads up, [[{n1}]] is out until [[{d1}]], so ping [[{n2}]] if the {num} printers act up. [[Fingers crossed.]] "
    "<<There's no|Theres no|apostrophe>> backup unit, and the vendor takes <<a week to|a weak to|homophone>> ship one.",

    "We need to [[hit the ground running]] when [[{n1}]] joins on [[{d1}]]. [[Break a leg]], everyone. <<The onboarding "
    "pack is|The onboarding pack are|agreement>> ready, and the [[{num} laptops]] are boxed in the store room.",

    "Look, I'm not [[gonna]] sugarcoat it: [[the {code} launch slipped again]]. [[Ugh.]] The vendor <<definitely "
    "promised|definately promised|spelling>> [[{d2}]] and then went quiet, so now <<we're stuck|were stuck|apostrophe>>.",

    "Dinner at [[{pl}]] was [[way too loud]], but [[{n1}]] loved it, and the bill came to [[{amt} EUR]]. <<The desserts "
    "were|The desserts was|agreement>> the best part, [[no joke]].",
]

# Ambiguous modules: one sentence that only the author can resolve.
AMBIGUOUS = [
    "Quick reminder about the handover. <<The files are|The files is|agreement>> ready on the shared drive. [[?Send the "
    "files to {n1} and {n2}'s team by Friday.|{n1}]] I will be away from {d1}, so please do not wait for me.",

    "After the review, [[?{n1} told {n2} that she would present the numbers on Tuesday.|she]] The <<slides still "
    "need|slides still needs|agreement>> a final check, and the {pl} room is booked from {d1}.",

    "We are planning the autumn workshop. [[?Please invite everyone from the design and sales teams who attended the "
    "workshop.|attended]] <<Registration closes on|Registration close on|agreement>> {d1}, and places are limited.",

    "The new equipment arrives soon. [[?Ask {n1} to collect the printer and the cables she ordered.|ordered]] <<Please "
    "remember to|Please rember to|spelling>> sign the delivery note, because {pl} will not release anything without it.",

    "Notes from the {pl} visit. [[?{n1} met {n2} after her talk and gave her the notes.|her]] <<Everyone was|Everyone "
    "were|agreement>> impressed, <<so we'll go|so well go|apostrophe>> again in the spring.",

    "Supplier update. [[?Call the supplier about the invoice and the delivery that went missing.|missing]] <<The total "
    "is|The total are|agreement>> {amt} EUR, and the payment <<is due on|is dew on|spelling>> {d2}.",
]

_MARK = re.compile(r"<<(?P<e>.+?)>>|\[\[(?P<p>.+?)\]\]")


def _day(w: World, used: set) -> str:
    """A date 3-60 days out, never reused inside one text (so protected dates stay unique)."""
    while True:
        d = w.today + dt.timedelta(days=w.rng.randrange(3, 61))
        if d not in used and d.day not in (1, 10, 15, 20, 30):
            used.add(d)
            return format_p7(d, w.year)


def _slots(w: World, people: list[dict], used_nums: set) -> dict:
    used = used_nums
    n1, n2, n3, f = people.pop(), people.pop(), people.pop(), people.pop()
    while True:
        num = w.rng.randrange(12, 96)
        if num % 10 and num not in used_nums:
            used_nums.add(num)
            break
    return {"n1": n1["first"], "n2": n2["first"], "n3": n3["first"], "fn1": f["name"], "pl": w.place(),
            "d1": _day(w, used), "d2": _day(w, used),
            "amt": fmt_amount(w.amount(120, 4000)).replace(" EUR", ""), "code": w.code(), "num": num}


def _compose(w: World, sender_first: str, picks: list[str], people: list[dict], n_errors: int) -> tuple[str, str, dict]:
    used: set = set()
    mods = [m.format(**_slots(w, people, used)) for m in picks]
    half = max(1, len(mods) // 2)
    marked = "Hi all,\n\n" + " ".join(mods[:half]) + "\n\n" + " ".join(mods[half:]) + f"\n\nThanks,\n{sender_first}"
    # keep n_errors of the planted errors; the others are left correct in both texts
    total = len(re.findall(r"<<", marked))
    keep = set(w.rng.sample(range(total), min(n_errors, total)))
    seen = {"i": -1}

    def heal(m):
        seen["i"] += 1
        return m.group(0) if seen["i"] in keep else m.group("e").split("|")[0]

    marked = re.sub(r"<<(?P<e>.+?)>>", heal, marked)
    errors, protected, ambiguous = [], [], []
    state = {"n": 0}

    def gold_cb(m):
        if m.group("e"):
            parts = m.group("e").split("|")
            state["n"] += 1
            errors.append({"id": f"e{state['n']}", "type": parts[2], "wrong": parts[1], "right": parts[0],
                           "alternatives": parts[3].split("~") if len(parts) > 3 else []})
            return parts[0]
        span = m.group("p")
        if span.startswith("?"):
            sp, key = span[1:].rsplit("|", 1)
            ambiguous.append({"span": sp, "key": key})
            return sp
        protected.append(span)
        return span

    gold = _MARK.sub(gold_cb, marked)
    source = _MARK.sub(lambda m: m.group("e").split("|")[1] if m.group("e") else
                       (m.group("p")[1:].rsplit("|", 1)[0] if m.group("p").startswith("?") else m.group("p")), marked)
    # the planting must be exact and reversible
    for e in errors:
        assert source.count(e["wrong"]) == 1, ("wrong not unique", e)
        assert gold.count(e["right"]) == 1, ("right not unique", e)
        assert e["wrong"] not in gold, ("wrong in gold", e)
    fixed = source
    for e in errors:
        fixed = fixed.replace(e["wrong"], e["right"])
    assert fixed == gold
    for s in protected + [a["span"] for a in ambiguous]:
        assert source.count(s) == 1 and gold.count(s) == 1, ("span not unique", s)
    return gold, source, {"errors": errors, "protected": protected, "ambiguous": ambiguous}


def _w4(kind: str, seed: int, index: int, profile) -> dict:
    w = World.make(seed, n_contacts=26)
    sender_c = w.person()
    people = [c for c in w.contacts if c["name"] != sender_c["name"]]
    w.rng.shuffle(people)
    sender = {"name": sender_c["name"], "preferred_name": sender_c["first"]}
    rng = w.rng
    for _ in range(200):
        if kind == "minimal":
            picks = rng.sample(GENERAL, rng.choice([3, 4]))
            n_err = rng.randrange(4, 8)
        elif kind == "protected":
            picks = rng.sample(GENERAL, 2) + rng.sample(PROTECTED, 2)
            rng.shuffle(picks)
            n_err = rng.randrange(3, 6)
        else:
            k = rng.choice([1, 2])
            picks = rng.sample(GENERAL, 3 - k) + rng.sample(AMBIGUOUS, k)
            rng.shuffle(picks)
            n_err = rng.randrange(3, 6)
        try:
            gold, source, meta = _compose(w, sender_c["first"], picks, list(people), n_err)
        except AssertionError:          # a repeated place or voice marker: draw again
            continue
        if 100 <= _wc(gold) <= 180:
            break
    else:
        raise RuntimeError(f"no W4 composition for seed {seed}")
    edit = {"gold": gold, "errors": meta["errors"], "protected": meta["protected"], "ambiguous": meta["ambiguous"]}
    asks_q = kind == "ambiguous"
    cons = {"questions_list": asks_q}
    ref = gold
    if asks_q:
        ref = gold + "\n\nQuestions:\n" + "\n".join(f"- {a['key']}" for a in meta["ambiguous"])
    case = W.new_case(
        sub="W4", kind=kind, seed=seed, generator=GENERATOR, today=w.today, profile=None, sender=sender,
        purpose="proofread the text with minimal edits", constraints=cons, context={"source_text": source},
        edit=edit, reference_draft=ref)
    lines = ["Proofread the text below. Fix only grammar, spelling and punctuation, with the smallest edit that works. "
             "Keep every name, number and my own voice exactly as written, and do not reword anything."]
    if asks_q:
        lines.append("If a sentence could be read in more than one way and only I can say which is meant, leave it "
                     "exactly as written.")
    lines += ["", "The text:", '"""', source, '"""']
    cl = W.render_constraints(case["constraints"], case["surface"])
    if asks_q:
        cl = [c.replace("Reply with the text only.", "Reply with the corrected text and the questions list only.")
              for c in cl]
    case["turns"] = ["\n".join(lines + cl)]
    return W.finish(case)


# ==========================================================================
# Registry
# ==========================================================================

def _make(sub: str, kind: str) -> Callable[..., dict]:
    impl = _w3 if sub == "W3" else _w4

    def gen(seed: int, *, index: int = 0, profile: str | None = None) -> dict:
        return impl(kind, seed, index, profile)

    gen.__name__ = f"gen_{sub.lower()}_{kind}"
    return gen


GENERATORS: dict[tuple[str, str], Callable[..., dict]] = {
    (sub, kind): _make(sub, kind) for sub, kind, _, _ in W.PLAN if sub in ("W3", "W4")
}
