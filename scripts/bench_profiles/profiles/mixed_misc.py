"""mixed profile: documents, notes, reminders, browser history and memory (all invented)."""
from __future__ import annotations

from scripts.bench_profiles.lib import ProfileBuilder, tok as T


def add_documents(b: ProfileBuilder) -> None:
    b.document("~/Documents/Thesis/REB_26-117_application_v3.docx", "REB 26-117 application v3 (revisions)",
               kind="docx", day=-1, time="23:10", id="ethics", content=f"""\
RIDGELINE INSTITUTE OF TECHNOLOGY - RESEARCH ETHICS BOARD
Application for ethics review: REB 26-117 (RESUBMISSION, draft v3)

Title: "Did I take it already?" Older adults, voice assistants and daily medication routines
Principal investigator: Dr. Imogen Harrow (supervisor)
Student investigator: Camila Duarte, MSc HCI

1. Summary
Semi-structured interviews (60 min) and a two-week diary study with 12-16 adults aged 70+ who
live independently and take three or more daily medications. Participants may already use a
smart speaker; no device is provided.

2. Recruitment
Posters at two community centres and a seniors' newsletter. [REVISED - item 1] The recruitment
script has been rewritten at a grade 6 reading level and now states that participation will not
affect any services participants receive. Large-print version attached (Appendix B).

3. Consent
Written consent before the interview; verbal re-consent before each diary call.
[REVISED - item 2] Data retention: de-identified transcripts kept for 5 years on Ridgeline research
storage, then deleted. Audio deleted after transcription is checked (max 90 days).

4. Audio and transcription   [TODO - item 3]
REB asked: which transcription service? Must be Ridgeline-approved or done on-premise.
-> ask Harrow if the lab's local transcription setup counts. Draft text:
"Audio will be transcribed on an encrypted lab workstation using locally installed software;
no audio leaves Ridgeline storage."

5. Compensation   [TODO - item 4]
REB asked us to name the form and timing. Current text: "$25 gift card" -> change to "$25 grocery
gift card per completed interview, given at the end of the session, even if the participant
withdraws partway through."

6. Risks
Minimal. Possible discomfort discussing health or memory; participants may skip any question.

Notes to self
- Harrow wants this by end of day Friday {T.date(1, 'short')} so she can sign the PI attestation.
- Official resubmission deadline: Wed {T.date(13, 'short')}, 16:00 (for the November board).
- Bring printed recruitment script + consent form to the check-in.
""")

    b.document("~/Documents/Parcelwise/POD_redesign_study_plan.docx", "POD redesign usability study plan (v2)",
               kind="docx", day=-15, time="17:40", id="study-plan", content=f"""\
Parcelwise | Courier app: proof-of-delivery (POD) redesign
Usability study plan v2 - owner: Priya Venkatesan; moderator: Camila Duarte; notes: Imani Harlow

Goal
Find out whether couriers can complete the new POD flow (photo + safe-place tag + recipient
note) in under 20 seconds without errors, and whether recipients understand the delivery card.

Participants (8, recruited through Bookwell)
- 5 couriers (at least 2 doing 40+ drops a day), 3 recipients who get 2+ parcels a week.
- Incentive: $75 digital gift card, sent automatically by Bookwell 48 h after the session.
  Never pay participants directly, never share recordings outside Parcelwise.

Schedule (as of {T.date(-15, 'short')})
P1 Tue {T.date(-9, 'short')} 10:00 | P2 Tue {T.date(-9, 'short')} 14:00 | P3 Tue {T.date(-2, 'short')} 11:00
P4 Thu {T.date(0, 'short')} 11:00 | P5 Thu {T.date(0, 'short')} 14:00 | P6 Fri {T.date(1, 'short')} 10:00
P7 Tue {T.date(5, 'short')} 10:00 | P8 Thu {T.date(7, 'short')} 11:00
Room: Heron (screen recorder + one-way mirror). Backup: Kestrel.

Tasks
1. Deliver to a front door with no answer (safe-place tag).
2. Deliver to a building concierge (recipient note).
3. Recover from a blurry photo.
4. Recipient: read the delivery card and find the parcel.

Measures: time on task, errors, SEQ after each task, SUS at the end.
Consent: paper consent form, printed per session. Recording stored in the research drive only.
Synthesis: after P5 (draft) - see Priya.
""")

    b.document("~/Documents/Parcelwise/sessions/P3_notes.md", "P3 session notes", kind="markdown",
               day=-1, time="15:05", id="p3-notes", content=f"""\
# P3 - Owen B. (courier, ~55 drops/day) - {T.date(-2, 'short')}, 11:00, Heron

**Task 1 (no answer, safe place)**: 14 s. Tapped "safe place" before taking the photo, then had to
go back. Said: "I always do the tag first, the photo is for you guys."
**Task 2 (concierge)**: 31 s. Missed the recipient-note field, it's below the fold on his phone size.
**Task 3 (blurry photo)**: recovered fine, liked the retake prompt. "This one's good."
**Task 4**: n/a (courier).

SEQ: 6, 4, 6. SUS 72.5.

Themes
- Order of photo vs. tag matters: couriers think tag-first.
- Note field is invisible on small screens (same as P1).
- Wants offline mode for basements and parking garages.

Quote for the deck: "If it takes more than three taps, I'm not doing it in the rain."
""")

    b.document("~/Documents/Money/budget_fall_2026.xlsx", "Fall 2026 budget (CAD)", kind="xlsx",
               day=-12, time="21:30", id="budget", rows=[
        ["Item", "Category", "Monthly (CAD)", "Notes"],
        ["Parcelwise pay (est. net, ~20 h/wk at $24/h)", "Income", 1790.00, "estimate, varies with hours"],
        ["Graduate entrance scholarship (term / 4)", "Income", 625.00, "paid per term"],
        ["Rent (Brunswick house)", "Housing", 780.00, "e-transfer to Gail by the 1st"],
        ["Hydro + internet share", "Housing", 62.00, "Theo sends the split"],
        ["Phone (Brightwave Mobile $40 + 13% HST)", "Bills", 45.20, ""],
        ["FareLoop monthly pass", "Transit", 156.00, "auto-renews on the 1st"],
        ["Groceries", "Food", 340.00, "Bloorside Market mostly"],
        ["Eating out / Dishdrop", "Food", 90.00, "try to keep under 90"],
        ["Reelbox ($9.99 + HST)", "Fun", 11.29, ""],
        ["Climbing 10-punch ($140 + HST) / 4", "Fun", 39.55, "one pass lasts ~4 months"],
        ["Tuition top-up savings", "School", 200.00, "winter instalment"],
        ["Lima trip fund (December)", "Savings", 250.00, "flight ~$650-700"],
        ["Total out", "", 1974.04, ""],
        ["Left over", "", 440.96, "income - total out"],
    ])

    b.document("~/Documents/Ridgeline/HCI6010/week6_reading_summary.md", "HCI 6010 week 6 reading summary",
               kind="markdown", day=-1, time="22:30", id="reading", content="""\
# HCI 6010 - Week 6: Care, maintenance and the "invisible work" of technology

## Ortega & Brandt (2019), "Situated Care: Repair Work in Assisted Living"
- Ethnography in two assisted-living homes, 14 months.
- Argues that devices "work" only because staff and family do constant unacknowledged repair:
  re-pairing speakers, rewording commands, resetting routines.
- Key term: *care infrastructure* - the people around a device are part of the system.
- My take: strong for my thesis - who fixes the voice assistant when the reminder breaks?

## Nakamura (2021), "Maintenance as Method"
- Short provocation: HCI rewards novelty; proposes studying maintenance as a design method.
- Three moves: follow the breakdown, count the workarounds, credit the maintainers.
- Weaker evidence, mostly examples. Good discussion fuel.

## Optional: Ferreira-Liu (2016), "The Help Desk at Home"
- Survey of 300 households: one person (usually a daughter or granddaughter) does most tech support.

## Discussion questions I'll bring
1. Is "care infrastructure" just a new word for social support?
2. What would a study plan look like that budgets time for the maintainers?
3. Connection to my REB protocol: should family helpers be participants too?

Response 6 submitted Wed night (counts for this week).
""")

    b.document("~/Downloads/HCI6100_A2_brief.pdf", "HCI 6100 Assignment 2 brief", kind="pdf",
               day=-10, time="14:12", id="a2-brief", content=f"""\
HCI 6100 Research Methods for HCI - Assignment 2: Observation study
Instructor: Prof. Lena Marsh   TA: Ravi Iyer
Due: Tuesday {T.date(5, 'short')}, 23:59, on Lectern. Weight: 20%.

Task
Observe people using a public or shared technology (a self-checkout, a library kiosk, a transit
ticket machine, a shared kitchen appliance) for at least 2 hours across 2 sessions. Write up:
1. Setting and ethics (no recording of faces; field notes only).
2. Observation protocol and coding scheme.
3. Findings: at least 3 patterns, each with field-note evidence.
4. Reflection: what you could not observe and how you would study it next.

Format: max 6 pages, 11 pt, references not counted. Individual or pairs (pairs: one submission,
both names, and a one-paragraph contribution statement).
Late policy: 10% per day, up to 3 days.
""")


def add_rest(b: ProfileBuilder) -> None:
    _notes(b)
    _reminders(b)
    _browser(b)
    _memory(b)


def _notes(b: ProfileBuilder) -> None:
    N = b.note
    N("Abuela's ají de gallina", """\
From Abuela on the video call (she measures by eye, so amounts are my guesses)

- 1 whole chicken breast + 1 thigh, poached with carrot, celery, garlic. KEEP THE BROTH
- 4-5 slices of white bread soaked in evaporated milk (1 can)
- 3 tbsp ají amarillo paste (the good jarred kind, not the cheap one, Abuela insists)
- 1 big red onion, finely chopped, 4 cloves garlic
- handful of walnuts or pecans, ground
- parmesan, a little
- to serve: rice, boiled yellow potatoes, hard-boiled egg, black olives

1. Sweat onion + garlic slowly, "until it cries" (~15 min). Add ají, cook 5 more.
2. Blend the soaked bread with milk, add to the pot, stir stir stir.
3. Shred the chicken, add with broth until creamy. Nuts + cheese at the end.
Abuela: never let it boil hard, and taste for salt twice.
""", day=-17, time="20:40", folder="Recipes", created_day=-32, id="recipe-aji")
    N("Papa a la huancaína (Mom's way)", """\
4 ají amarillo (or 3 tbsp paste), 1/2 onion sauteed, 200 g queso fresco, 4 soda crackers,
evaporated milk until it's like thick yogurt, a bit of oil, salt.
Blend. Over boiled yellow potatoes + lettuce + egg + olive.
Mom says the crackers are the secret. Dad says it's the cheese. They have argued about this for 30 years.
""", day=-30, time="19:05", folder="Recipes", created_day=-30, id="recipe-huancaina")
    N("Thesis timeline", """\
THESIS TIMELINE (plan from August)
- REB submit: Oct 9
- REB approval: by Oct 30
- Interviews: start Nov 2 -> mid Dec
- Diary study: Jan
- Analysis: Feb-Mar
- Write: Mar-Jun
- Defence: ??? (summer? ask Harrow)
""", day=-30, time="11:20", folder="Thesis", created_day=-48, id="thesis-timeline")
    N("Questions for Harrow (REB)", """\
for the check-in
- does the lab transcription workstation count as "on-premise"?? (item 3)
- gift card: grocery vs. general? REB wants the form named (item 4)
- should family helpers be participants or just mentioned? (from week 6 reading)
- can I start recruiting posters before approval? (probably no)
- signature: she signs PI attestation after my Fri version
""", day=-1, time="22:55", folder="Thesis", created_day=-6, id="harrow-qs")
    N("Synthesis plan", f"""\
POD synthesis
- affinity map in the board after P5
- draft to Priya by Mon {T.date(4, 'short')}
- themes so far: tag-before-photo, note field hidden, offline mode, gloves/rain
- ask Imani for the clip timestamps
""", day=-6, time="12:15", folder="Parcelwise", created_day=-7, id="synth-plan")
    N("Study permit renewal", """\
permit stuff
- passport: valid to 2029, fine
- permit expiry: CHECK THE PAPER (blue folder, desk drawer). I think spring?? don't guess
- need: enrolment letter from the registrar, proof of funds (bank letter + scholarship letter),
  photo, fee
- ISO says apply at least 90 days before it expires
- ask Hana how long hers took
""", day=-10, time="23:02", folder="Admin", created_day=-11, id="permit-docs")
    N("Groceries", """\
- [ ] oat milk (Noor says we're out)
- [ ] eggs
- [ ] rice 2 kg
- [ ] yellow potatoes
- [x] onions
- [ ] limes
- [ ] queso fresco (the Latin shop on Bloor)
- [ ] coffee
- [ ] cough drops
""", day=-1, time="22:50", folder="Notes", created_day=-6, id="groceries")
    N("Session day checklist", """\
SESSION DAY
- [ ] print consent forms x2 per participant (one for them)
- [ ] test screen recorder (Heron has been flaky)
- [ ] test phone mirroring
- [ ] water + snacks
- [ ] task cards in order 1-4
- [ ] Bookwell check-in link open
- [ ] after: upload recording to research drive ONLY, rename P#_date
""", day=-9, time="09:05", folder="Parcelwise", created_day=-17, id="session-checklist")
    N("seminar wk6 messy", """\
okafor wk6
care infrastructure = ppl around device are part of system
-> abuela's speaker: who fixes it? mom. on the phone. from lima. lol
nakamura: "follow the breakdown"
q: is this just social support w/ new name? (hana will say yes)
response 6 done wed
""", day=-1, time="22:35", folder="Ridgeline", created_day=-2, id="sem-wk6")
    N("A2 ideas", """\
observation study options
1. library self-checkout (Bloor branch) - lots of people, easy
2. FareLoop reload machines at Spadina station - interesting errors, but cold
3. the shared kitchen microwave at Parcelwise lol (ethics? coworkers = no)
-> going with 1, pairing with Hana (she said yes)
sessions: Sunday afternoon + Monday after class
""", day=-8, time="21:14", folder="Ridgeline", created_day=-10, id="a2-ideas")
    N("Lima in December", """\
LIMA DEC
- flight: watching Condor Andino, ~$650-700, want Dec 18 -> Jan 6
- gifts: maple syrup (lots), the toque for Dad, chocolates for Abuela, a book for Mateo
- Dani wants an Ridgeline hoodie (size M)
- ask Jonah if he's coming for New Year's or not
- dentist in Lima is cheaper, book with Dr. Paredes?
""", day=-3, time="22:40", folder="Notes", created_day=-28, id="lima-dec")
    N("Abuela 80", """\
ABUELA 80 - Oct 15
- [x] photos to Mom for the video (sent Wed)
- [x] Mom buys flowers from me (I sent her money)
- [ ] write something to read on the call
- [ ] record a short video for the party Saturday (Lima), they'll play it at Tía Rosa's
idea: tell the story of her teaching me to make picarones in her kitchen in Surco
""", day=-1, time="19:40", folder="Notes", created_day=-14, id="abuela-80")
    N("House chores", """\
rotation (Theo's system, starts Sundays)
wk A: Cami bathroom, Noor kitchen, Theo floors, Lukas recycling/garbage
wk B: Cami kitchen, Noor floors, Theo recycling, Lukas bathroom
garbage + green bin: Tuesday night out
recycling: every other week (the calendar's on the fridge)
""", day=-20, time="10:30", folder="Notes", created_day=-35, id="chores")
    N("Links", """\
Confera rooms
- Harrow's office room: confera.example/r/harrow-office
- HCI 6010 (on Lectern course page)
- Parcelwise standup: confera.example/r/pw-research
Bookwell workspace: bookwell.test/w/parcelwise-research
""", day=-9, time="10:02", folder="Notes", created_day=-30, id="links")
    N("Rent math", """\
new split (Theo's first proposal)
total 3230
me 805 / noor 790 / theo 805 / lukas 830
lukas not happy (attic ceiling)
-> wait for final numbers before changing the budget
""", day=-9, time="21:50", folder="Notes", created_day=-9, id="rent-math")
    N("HCI 6100 notes Oct 5", f"""\
methods {T.date(-10, 'short')} - observation & field notes
- descriptive vs reflective notes, keep them in two columns
- "jottings" in the field -> full notes within 24 h
- Marsh: count things! frequency tables are underrated
- ethics: public settings, no faces, no names, no recording without consent
- coding: start open, then focused codes, then a codebook w/ examples
- reading for next time: chapter 7 + the Osterman article
A2 posted (observation study)
""", day=-10, time="13:05", folder="Ridgeline", created_day=-10, id="methods-oct5")
    N("To read (not for school)", """\
- the Lima novel Ale recommended (the one with the bakery)
- that essay collection about walking in cities
- Jonah's sci-fi one he won't stop talking about
""", day=-25, time="23:30", folder="Notes", created_day=-40, id="books")
    N("Cragline projects", """\
- blue V3 in the cave, the heel hook at the top
- pink V2 slab (done!!)
- try the new set on the overhang Sunday
""", day=-11, time="14:20", folder="Notes", created_day=-26, id="climbing")
    N("For Tía Rosa / Mateo", """\
Mateo - Canadian unis, rough info
- international undergrad tuition: very roughly $35k-50k/yr depending on school + program (check)
- most ask for an English test unless school was in English
- application windows: Oct-Jan for September
- living costs Toronto: rent alone ~$900-1200 for a room
- send her the links when I have an hour
""", day=-1, time="12:20", folder="Notes", created_day=-1, id="mateo")
    N("House Wi-Fi", """\
network: brunswick-house
password: on the fridge, under the chore chart (Theo changes it every semester)
""", day=-36, time="18:00", folder="Notes", created_day=-36, id="wifi")
    N("3am thoughts", """\
is it weird that my thesis is basically about Abuela
also is it weird that I like the Parcelwise job more than my thesis some weeks
(no. it pays. it has snacks.)
""", day=-19, time="02:51", folder="Notes", created_day=-19, id="journal")
    N("Interview guide v2 (snippets)", """\
warm-up: "Tell me about a normal morning."
routine: "Walk me through the last time you took your morning pills."
device: "Have you ever asked the speaker about your medication? What happened?"
breakdown: "Tell me about a time it didn't work."
helpers: "Who do you call when it stops working?"
close: "Is there anything I should have asked?"
""", day=-12, time="16:45", folder="Thesis", created_day=-25, id="interview-guide")
    N("Parcelwise first week", """\
- badge: front desk, 5th floor
- Priya 1:1 Tuesdays 16:00
- standup Thursdays 09:45
- timesheets in Paydeck, two-week periods (Sun-Sat), due the last Friday of the period at 17:00
- Heron has the one-way mirror, Kestrel is the small one
- wifi PW-Guest for participants
""", day=-34, time="17:50", folder="Parcelwise", created_day=-36, id="pw-first-week")
    N("Thanksgiving menu", """\
house thanksgiving (Sunday 6pm)
- Theo: turkey breast (not a whole one, we learned)
- Noor: stuffing + her mom's fattoush
- Lukas: pumpkin pie from the bakery
- me: papa a la huancaína + causa
- drinks: everyone
""", day=-7, time="20:15", folder="Notes", created_day=-9, id="thanksgiving-menu")
    N("POD insights so far", """\
P1-P3
- tag before photo (3/3)
- note field hidden on small screens (2/3)
- offline mode asked for (2/3)
- gloves: big tap targets (P2 in a hi-vis jacket demo-ing with gloves!)
P4/P5 tomorrow -> check recipient side (P4 is a recipient)
""", day=-1, time="16:30", folder="Parcelwise", created_day=-8, id="pod-insights")
    N("Quotes", """\
"If it takes more than three taps, I'm not doing it in the rain." - P3
"The speaker is polite but it doesn't know me." - pilot interview, Mrs. L.
"Ay, hija, the phone is smarter than me." - Abuela
""", day=-2, time="12:30", folder="Notes", created_day=-20, id="quotes")
    N("Gift ideas Jonah", """\
bday July 22
- the pedal thing he keeps looking at
- concert tickets
(this was for this summer - did the concert, done)
""", day=-45, time="22:00", folder="Notes", created_day=-50, id="gift-jonah")


def _reminders(b: ProfileBuilder) -> None:
    R = b.reminder
    R("Send Mom the photos for Abuela's video", day=-1, time="20:00", created_day=-14, id="photos")
    R("Study permit renewal: check expiry date + gather documents", day=None, created_day=-11, id="permit",
      notes="ISO says 90 days before expiry")
    R("Abuela's 80th birthday!", day=0, time="08:00", created_day=-20, id="abuela-bday")
    R("e-transfer Theo hydro", day=1, time="12:00", created_day=-2, id="hydro")
    R("REB revisions to Dr. Harrow", day=1, time="17:00", created_day=-6, id="reb")
    R("A2: draft to Hana", day=4, time="20:00", created_day=-3, id="a2-draft")
    R("Pick up library hold (Designing for Older Adults)", day=1, time="16:00", created_day=-5, id="library")
    R("Send Dani O the Oct 5 methods notes", day=None, created_day=-4, id="dani-notes")
    R("Reply to Tía Rosa about Mateo", day=None, created_day=-2, id="tia")
    R("Book Lima flight", day=-14, time="21:00", created_day=-28, id="flight")
    R("Buy furnace filter 16x25x1", day=-20, time="18:00", created_day=-30, id="filter")
    R("Laundry", day=-2, time="20:00", created_day=-3, id="laundry")
    R("Card for Noor's birthday (Oct 30)", day=13, time="09:00", created_day=-6, id="noor-card")
    R("Ask ISO about the free tax clinic", day=None, created_day=-35, id="tax-clinic")
    R("Return the jacket (Northbound)", day=-8, time="18:00", created_day=-15, id="return-jacket")
    R("Groceries", day=None, created_day=-6, id="groceries")
    R("Dentist 14:30", day=-13, time="13:30", created_day=-20, completed=True, completed_day=-13,
      completed_time="13:35", id="dentist")
    R("Rent to Gail ($780)", day=-14, time="09:00", created_day=-45, completed=True, completed_day=-15,
      completed_time="21:10", id="rent-oct")
    R("Update FareLoop payment card", day=-4, time="12:00", created_day=-6, completed=True,
      completed_day=-4, completed_time="11:48", id="fareloop")
    R("Fill in Harrow's lab poll", day=-10, time="17:00", created_day=-12, completed=True,
      completed_day=-11, completed_time="10:22", id="poll")
    R("Send P3 notes to Imani", day=-1, time="12:00", created_day=-2, completed=True, completed_day=-1,
      completed_time="15:21", id="p3-notes")
    R("Reading response 6", day=0, time="12:00", created_day=-8, completed=True, completed_day=-1,
      completed_time="22:42", id="resp6")
    R("Call Abuela", day=None, created_day=-40, id="call-abuela-old",
      notes="old; made before the weekly Sunday calls started")


def _browser(b: ProfileBuilder) -> None:
    rows = [
        (-28, "21:10", "condorandino.example", "/flights/yyz-lim", "Toronto to Lima flights | Condor Andino"),
        (-26, "10:05", "lectern.ritech.example", "/courses/hci6010/syllabus", "HCI 6010 Syllabus - Lectern"),
        (-24, "09:40", "transitplanner.example", "/trip?from=annex&to=spadina-king", "Trip planner: Annex to Spadina & King"),
        (-22, "15:45", "reb.ritech.example", "/forms/application-template", "REB application template - Ridgeline Research Ethics"),
        (-20, "13:15", "wiki.parcelwise.test", "/research/consent-templates", "Consent templates - Parcelwise Wiki"),
        (-18, "22:30", "scholarhub.example", "/search?q=older+adults+voice+assistant+medication", "Search results - ScholarHub"),
        (-17, "19:20", "cocinaperuana.example", "/recetas/aji-de-gallina", "Ají de gallina, the classic recipe - Cocina Peruana"),
        (-16, "08:55", "bookwell.test", "/w/parcelwise-research/sessions", "Sessions - Bookwell"),
        (-15, "12:10", "library.ritech.example", "/catalog/designing-for-older-adults", "Designing for Older Adults (3rd ed.) - Ridgeline Libraries"),
        (-14, "21:05", "condorandino.example", "/flights/yyz-lim?dep=12-18", "YYZ-LIM Dec 18 - Condor Andino"),
        (-13, "16:20", "annexdental.example", "/after-your-cleaning", "After your cleaning - Annex Dental"),
        (-12, "21:25", "splitcalc.example", "/rent-by-room-size", "Split rent by room size - SplitCalc"),
        (-11, "10:20", "pollpal.example", "/p/hil-winter-meetings", "HIL winter lab meeting times - PollPal"),
        (-11, "19:45", "iso.ritech.example", "/study-permit/renewing", "Renewing your study permit - Ridgeline International Student Office"),
        (-10, "14:10", "lectern.ritech.example", "/courses/hci6100/assignments/a2", "A2: Observation study - Lectern"),
        (-9, "09:58", "confera.example", "/r/pw-research", "Confera - pw-research"),
        (-8, "20:40", "scholarhub.example", "/paper/situated-care-repair-work", "Situated Care: Repair Work in Assisted Living - ScholarHub"),
        (-7, "09:20", "reb.ritech.example", "/my/applications/26-117", "REB 26-117 - Decision letter"),
        (-6, "11:30", "rooms.parcelwise.test", "/book/heron?d=fri", "Book Heron - Parcelwise Rooms"),
        (-6, "18:50", "dishdrop.example", "/orders/new", "Dishdrop - Your cart"),
        (-5, "13:00", "northboundoutfitters.example", "/outerwear/parkas", "Parkas - Northbound Outfitters"),
        (-4, "11:45", "fareloop.test", "/account/auto-renew", "Monthly pass auto-renew - FareLoop"),
        (-4, "19:40", "weatherly.example", "/toronto/10-day", "Toronto 10-day forecast - Weatherly"),
        (-3, "20:10", "andesurexpress.test", "/track/AXP-PE-48213", "Track shipment AXP-PE-48213 - Andesur Express"),
        (-2, "14:05", "bookwell.test", "/w/parcelwise-research/sessions/p6", "P6 - rescheduled - Bookwell"),
        (-2, "21:30", "unidirectory.example", "/canada/international-undergrad-costs", "Studying in Canada as an international undergrad - UniDirectory"),
        (-1, "12:40", "paydeck.test", "/timesheets/current", "Timesheet (period ending Oct 17) - Paydeck"),
        (-1, "17:15", "confera.example", "/r/harrow-office", "Confera - harrow-office"),
        (-1, "20:20", "lectern.ritech.example", "/courses/hci6010/announcements/118", "Week 6 is online - HCI 6010 - Lectern"),
        (-1, "21:00", "lectern.ritech.example", "/courses/hci6010/assignments/response-6", "Reading response 6 - Lectern"),
        (-1, "22:20", "scholarhub.example", "/paper/maintenance-as-method", "Maintenance as Method - ScholarHub"),
        (-1, "23:05", "reb.ritech.example", "/guidance/transcription", "Transcription and audio data - Ridgeline Research Ethics"),
        (-1, "23:20", "timezones.example", "/toronto-vs-lima", "Toronto vs Lima time difference - TimeZones"),
        (0, "07:05", "weatherly.example", "/toronto/today", "Toronto today: rain after 16:00, 9°C - Weatherly"),
        (0, "07:12", "transitplanner.example", "/trip?from=annex&to=spadina-king&dep=0900", "Trip planner: leave 09:00"),
        (0, "07:25", "andesurexpress.test", "/track/AXP-PE-48213", "Track shipment AXP-PE-48213 - Andesur Express"),
        (0, "07:40", "paydeck.test", "/timesheets/current", "Timesheet (period ending Oct 17) - Paydeck"),
    ]
    for i, (day, time, host, path, title) in enumerate(rows):
        b.web(host, path, title, day=day, time=time, browser="chrome" if host.endswith(".test") else "safari")


def _memory(b: ProfileBuilder) -> None:
    M = b.remember
    M("Works at Parcelwise as a UX research intern: Tue and Thu 09:30-17:30, Fri 09:30-13:00.",
      category="routine", day=-36, id="work-hours")
    M("Thesis supervisor is Dr. Imogen Harrow (Humane Interfaces Lab).", category="person", day=-40, id="supervisor")
    M("Rent is $780 a month, sent to the landlord by e-transfer on the 1st.", category="fact", day=-44,
      superseded_by="mem-rent-new", id="rent-old")
    M("From November 1 rent is $815 a month (house re-split by room size).", category="fact", day=-5,
      id="rent-new")
    M("Family lives in Lima, Peru. Lima has no daylight saving: it is one hour behind Toronto until "
      "early November, then the same time.", category="fact", day=-30, id="lima-time")
    M("I do errands on Saturday mornings and keep weekday afternoons for thesis writing.",
      category="preference", day=-40, superseded_by="mem-focus-errands", id="errands-old")
    M("Mornings are for focused work (thesis writing, synthesis); errands go after 16:00.",
      category="routine", day=-9, id="focus-errands")
    M("Add 15 minutes of travel buffer before any in-person event, on top of the travel time.",
      category="routine", day=-20, id="travel-buffer")
    M("When I email the lab group or the study team, include every member, not just the person who asked.",
      category="preference", day=-12, id="group-emails")
    M("This week the REB ethics revisions are my top priority: put them first in the Brief and the Day plan.",
      category="preference", day=-6, id="reb-priority")
    M("Takes transit to the Parcelwise office (about 30 min) and walks to campus (about 12 min).",
      category="routine", day=-36, id="commute")
