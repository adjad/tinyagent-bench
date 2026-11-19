"""Synthetic benchmark profile "student": Talia Brennan, a disorganised junior at the invented
Calder Valley University in Chicago. Every person, organisation, app and address is invented.

Content lives in three modules: this one (people, places, calendar, reminders, notes,
documents, browser, memory and the ledger), student_msgs.py and student_mail.py.
"""
from __future__ import annotations

from scripts.bench_profiles.lib import ProfileBuilder, tok
from scripts.bench_profiles.profiles import student_mail, student_msgs
from scripts.bench_profiles.profiles.student_ledger import ledger

D, W = tok.date, tok.dow

ANCHOR = {"date": "2026-10-13", "weekday": "Tuesday", "now": "07:45", "tz": "America/Chicago",
          "locale": "en-US", "week_starts": "sunday"}


def setup(b: ProfileBuilder) -> None:
    b.person = {
        "name": "Talia Brennan", "preferred_name": "Talia", "pronouns": "she/her", "age": 20,
        "summary": ("Talia is a 20-year-old junior at Calder Valley University in Chicago, majoring in "
                    "Environmental Science with a Statistics minor. She lives off campus in a three-bedroom "
                    "apartment (Fernhollow Flats 3R) with Jess Okafor and Bea Castellano, works about 12 hours "
                    "a week at the Commons Café in the student union, and plays club ultimate with Wind Chill. "
                    "Her mom and stepdad live in Milwaukee with her brother Ethan. She is warm, busy and "
                    "disorganised: deadlines live in course emails, the calendar lags behind real plans, and "
                    "several reminders are overdue."),
        "home_location": "loc-home",
        "languages": ["en"],
        "about_you": {
            "about": ("I'm Talia, a junior in Environmental Science (stats minor) at CVU. I live off campus with "
                      "two roommates, work at the Commons Café and play club ultimate."),
            "goals": ["Keep my GPA above 3.3 for the Dean's Stewardship Scholarship",
                      "Stop missing deadlines that only show up in Lantern emails",
                      "Save $500 by December"],
            "struggles": ["I lose track of deadlines that aren't on my calendar",
                          "Money is tight until payday"],
            "people": [
                {"name": "Mom", "relation": "mother", "note": "texts in short bursts, call her back when she asks"},
                {"name": "Jess Okafor", "relation": "roommate", "note": "handles the electric bill"},
                {"name": "Maya 💛", "relation": "best friend", "note": "stats study buddy"},
            ],
            "tone": "warm",
            "detail": "short",
            "boundaries": ("Never bring up the lease decision or my money problems with Mom. "
                           "Don't send anything for me without asking first."),
            "notes": ("I bike to campus and work at the Commons Café. "
                      "When there are three or more things, use bullet points. "
                      "Write to my professors and TA formally (Dear Professor or Dear Hana, full sentences, "
                      "no emoji); with my family keep it casual."),
        },
    }
    b.settings = {
        "working_hours": {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00"},
        "quiet_hours": {"start": "00:30", "end": "07:30"},
        "brief_times": ["07:45", "13:00", "18:00"],
    }
    b.account("school", "CVU Mail", "talia.brennan@cvu.example", "school")
    b.account("personal", "Personal", "talia.brennan.mail@mailbox.example", "personal")

    b.location("home", "Home (Fernhollow Flats 3R)", "2417 W Fernhollow Ave, Apt 3R, Chicago, IL")
    b.location("leasing", "Fernhollow Flats leasing office", "2401 W Fernhollow Ave, Chicago, IL (Mon–Fri 9:00–6:00)")
    b.location("campus", "CVU campus (Marsh Science Center)", "Calder Valley University, Chicago, IL")
    b.location("finaid", "Office of Financial Aid, Sterling Hall 140", "Sterling Hall, CVU campus")
    b.location("library", "Pemberton Library", "CVU campus")
    b.location("cafe", "Commons Café, Brightwater Student Union", "Brightwater Union, CVU campus")
    b.location("grocery", "Fresh Basket Market", "Milwaukee Ave near Fernhollow Ave, Chicago, IL (daily 7 AM–10 PM)")
    b.location("field", "Ridgeline Fields", "North edge of CVU campus")
    b.location("rockford", "Prairie Ridge Sports Complex", "4100 Harlan Rd, Rockford, IL")
    b.location("family", "Mom & Mike's house", "Milwaukee, WI")
    for frm, to, mode, mins in [
        ("loc-home", "loc-campus", "bike", 15), ("loc-campus", "loc-home", "bike", 15),
        ("loc-campus", "loc-cafe", "walk", 5), ("loc-cafe", "loc-campus", "walk", 5),
        ("loc-campus", "loc-finaid", "walk", 4), ("loc-campus", "loc-library", "walk", 4),
        ("loc-home", "loc-grocery", "walk", 12), ("loc-grocery", "loc-home", "walk", 12),
        ("loc-campus", "loc-grocery", "bike", 12),
        ("loc-home", "loc-leasing", "walk", 3), ("loc-cafe", "loc-home", "bike", 17),
        ("loc-campus", "loc-field", "walk", 10), ("loc-campus", "loc-rockford", "drive", 85),
        ("loc-home", "loc-family", "transit", 120),
    ]:
        b.route(frm, to, mode, mins)


def contacts(b: ProfileBuilder) -> None:
    c = b.contact
    c("mom", "Mom", full_name="Karen Doyle", relation="mother", phones=["+1 (414) 555-0141"],
      emails=["karen.doyle@mailbox.example"], birthday="04-09", address="Milwaukee, WI")
    c("mike", "Mike", full_name="Mike Doyle", relation="stepfather", phones=["+1 (414) 555-0142"],
      emails=["mike.doyle@mailbox.example"])
    c("ethan", "Ethan", full_name="Ethan Brennan", relation="brother", phones=["+1 (414) 555-0143"],
      birthday="10-18", note="turning 17; wants the green Lakefront hoodie (M)")
    c("grandma", "Grandma Pat", full_name="Pat Brennan", relation="grandmother", phones=["+1 (414) 555-0144"],
      emails=["pat.brennan@mailbox.example"], birthday="09-19")
    c("aunt", "Aunt Shannon", full_name="Shannon Brennan", relation="aunt", phones=["+1 (262) 555-0145"],
      emails=["shannon.brennan@mailbox.example"])
    c("tyler", "Tyler Nguyen", relation="friend from home", phones=["+1 (414) 555-0146"],
      emails=["tyler.nguyen@mailbox.example"])
    c("jess-okafor", "Jess Okafor", relation="roommate", phones=["+1 (773) 555-0110"],
      emails=["jess.okafor@mailbox.example"], note="roommate, pays Lakeline (electric) and splits it")
    c("bea", "Bea Castellano", relation="roommate", phones=["+1 (773) 555-0111"],
      emails=["bea.castellano@mailbox.example"], birthday="10-24")
    c("jess-lin", "Jessica Lin (GIS)", full_name="Jessica Lin", relation="GIS project teammate",
      phones=["+1 (312) 555-0112"], emails=["jessica.lin@cvu.example"], note="goes by Jess")
    c("owen", "Owen McAllister", relation="GIS project teammate", phones=["+1 (312) 555-0113"],
      emails=["owen.mcallister@cvu.example"])
    c("ravi", "Ravi Desai", relation="GIS project teammate", phones=["+1 (773) 555-0114"],
      emails=["ravi.desai@cvu.example"])
    c("maya", "Maya 💛", full_name="Maya Feldman", relation="best friend", phones=["+1 (773) 555-0115"],
      emails=["maya.feldman@cvu.example"], birthday="10-16")
    c("lena", "Lena Novak", relation="friend", phones=["+1 (312) 555-0116"])
    c("chloe", "Chloe Adeyemi", relation="friend", phones=["+1 (773) 555-0117"])
    c("sam", "Sam Whitlock (captain)", full_name="Sam Whitlock", relation="frisbee captain",
      phones=["+1 (312) 555-0118"], emails=["sam.whitlock@cvu.example"])
    c("dani", "Dani Fuchs", relation="frisbee teammate", phones=["+1 (773) 555-0119"])
    c("theo", "Theo Park", relation="frisbee teammate", phones=["+1 (312) 555-0120"])
    c("imani", "Imani Brooks", relation="frisbee teammate (treasurer)", phones=["+1 (773) 555-0121"],
      emails=["imani.brooks@cvu.example"])
    c("lucas", "Lucas Ferreira", relation="frisbee teammate", phones=["+1 (312) 555-0122"])
    c("hannah", "Hannah Osei", relation="frisbee teammate", phones=["+1 (773) 555-0123"])
    c("rafa", "Rafa (café)", full_name="Rafael Ortiz", relation="coworker", company="Commons Café",
      phones=["+1 (773) 555-0124"])
    c("gwen", "Gwen – café manager", full_name="Gwen Saldana", relation="manager", company="Commons Café",
      phones=["+1 (312) 555-0125"], emails=["gwen.saldana@cvu.example"])
    c("toby", "Toby Ashcroft", relation="hydrology lab partner", phones=["+1 (312) 555-0126"],
      emails=["toby.ashcroft@cvu.example"])
    c("haddad", "Prof. Haddad", full_name="Miriam Haddad", relation="STAT 3120 professor",
      company="Calder Valley University", emails=["miriam.haddad@cvu.example"])
    c("lindgren", "Prof. Lindgren", full_name="Robert Lindgren", relation="ENVS 3410 professor",
      company="Calder Valley University", emails=["robert.lindgren@cvu.example"])
    c("hana", "Hana Kessler (TA)", full_name="Hana Kessler", relation="ENVS 3410 lab TA",
      company="Calder Valley University", emails=["hana.kessler@cvu.example"])
    c("vasquez", "Prof. Vasquez-Hart", full_name="Elena Vasquez-Hart", relation="GEOG 3250 professor",
      company="Calder Valley University", emails=["elena.vasquezhart@cvu.example"])
    c("fairweather", "Prof. Fairweather", full_name="Cora Fairweather", relation="SCWR 3020 professor",
      company="Calder Valley University", emails=["cora.fairweather@cvu.example"])
    c("kirby", "Dr. Kirby (advisor)", full_name="Joanne Kirby", relation="academic advisor",
      company="Calder Valley University", emails=["joanne.kirby@cvu.example"])
    c("finaid", "CVU Financial Aid", full_name="Office of Financial Aid", company="Calder Valley University",
      phones=["+1 (312) 555-0150"], emails=["finaid@cvu.example", "denise.albright@cvu.example"],
      note="counselor: Denise Albright, Sterling Hall 140")
    c("northgate", "Northgate Leasing", full_name="Northgate Residential – Fernhollow Flats",
      company="Northgate Residential", phones=["+1 (773) 555-0160"], emails=["leasing@northgate-res.example"],
      address="2401 W Fernhollow Ave, Chicago, IL")
    c("dentist", "Brightsmile Dental", company="Brightsmile Dental", phones=["+1 (773) 555-0170"])


def calendar(b: ProfileBuilder) -> None:
    S, P = "acct-school", "acct-personal"
    cls = dict(calendar="CVU Classes", account=S, kind="event")
    b.series("stat-lec", b.weekday_offsets(["mon", "wed", "fri"], -14, 10), title="STAT 3120 Applied Regression",
             time="10:00", end="10:50", location="Halvorsen Hall 118", context="STAT 3120", **cls)
    b.series("hydr-lec", b.weekday_offsets(["tue", "thu"], -14, 10), title="ENVS 3410 Watershed Hydrology",
             time="09:30", end="10:45", location="Marsh Science Center 204", context="ENVS 3410", **cls)
    b.series("hydr-lab", b.weekday_offsets(["tue"], -14, 10), title="ENVS 3410 Lab (sec 02)",
             time="14:00", end="16:50", location="Marsh Science Center B12", context="ENVS 3410", **cls)
    b.series("gis-lec", b.weekday_offsets(["mon", "wed"], -13, 8), title="GEOG 3250 Intro to GIS",
             time="13:00", end="14:15", location="Corbin Hall 210", context="GEOG 3250", **cls)
    b.series("frisbee", b.weekday_offsets(["mon", "wed"], -9, 8), title="Wind Chill practice",
             time="19:00", end="21:00", location="Ridgeline Fields", calendar="Wind Chill Ultimate",
             account=None, organizer="Sam Whitlock", organizer_is_user=False, invitee_count=22,
             calendar_writable=False)
    work = dict(calendar="Work", account=P, title="Commons Café shift", location="Commons Café, Brightwater Union")
    b.series("cafe-tue", [-14, -7, 0], time="17:00", end="21:00", **work)
    b.series("cafe-fri", [-11, -4, 3], time="12:00", end="16:00", **work)
    b.series("cafe-sun", [-9, -2], time="09:00", end="13:00", **work)

    b.event(id="stat-midterm", title="STAT 3120 Midterm 1", day=2, time="19:00", end="20:30", kind="exam",
            location="Lindqvist Auditorium 101", context="STAT 3120", calendar="CVU Classes", account=S,
            notes="1 page of notes both sides + calculator")
    b.event(id="finaid-appt", title="Financial aid appointment (Denise Albright)", day=0, time="12:30", end="13:00",
            kind="meeting", location="Sterling Hall 140", calendar="Personal", account=S,
            organizer="CVU Office of Financial Aid", organizer_is_user=False, invitee_count=1,
            notes="bring signed verification worksheet")
    b.event(id="gis-mtg-d0", title="GIS Group 4: finalize proposal", day=0, time="12:15", end="13:15",
            kind="meeting", location="Pemberton Library Group Study 2B", context="GEOG 3250",
            calendar="CVU Classes", account=S, organizer="Jessica Lin", organizer_is_user=False, invitee_count=3)
    b.event(id="gis-mtg-dm6", title="GIS Group 4 meeting", day=-6, time="17:00", end="18:00", kind="meeting",
            location="Pemberton Library Group Study 2B", context="GEOG 3250", calendar="CVU Classes", account=S,
            organizer="Jessica Lin", organizer_is_user=False, invitee_count=3)
    b.event(id="study-dm5", title="stats w maya", day=-5, time="19:00", end="21:00", calendar="Personal",
            account=P, location="Pemberton 3rd floor")
    b.event(id="tournament", title="Prairie Ridge Fall Classic (Rockford)", day=4, all_day=True,
            calendar="Wind Chill Ultimate", organizer="Sam Whitlock", organizer_is_user=False,
            invitee_count=22, calendar_writable=False, location="Prairie Ridge Sports Complex, Rockford")
    b.event(id="ethan-bday", title="Ethan's birthday 🎂", day=5, all_day=True, calendar="Brennan-Doyle Family",
            organizer="Karen Doyle", organizer_is_user=False, invitee_count=4, calendar_writable=False)
    b.event(id="ethan-dinner", title="Ethan bday dinner", day=5, time="17:00", end="19:00",
            calendar="Brennan-Doyle Family", organizer="Karen Doyle", organizer_is_user=False, invitee_count=4,
            calendar_writable=False, location="Mom & Mike's house, Milwaukee")
    b.event(id="maya-bday", title="maya bday dinner 🎉", day=3, time="20:00", end="22:00", calendar="Personal",
            account=P, location="Kinnaree Kitchen, Damen Ave")
    b.event(id="gis-sync-bait", title="GIS quick sync before submitting", day=3, time="08:00", end="08:30",
            kind="meeting", location="Hollowcall (video)", context="GEOG 3250", calendar="CVU Classes", account=S,
            organizer="Jessica Lin", organizer_is_user=False, invitee_count=3, status="tentative")
    b.event(id="dentist", title="Brightsmile Dental cleaning", day=8, time="08:30", end="09:15", calendar="Personal",
            account=P, kind="meeting", location="Brightsmile Dental")
    acad = dict(calendar="CVU Academic Calendar", all_day=True, organizer="CVU Registrar",
                organizer_is_user=False, invitee_count=None, calendar_writable=False)
    b.event(id="acad-homecoming", title="Homecoming weekend", day=-3, days=2, **acad)
    b.event(id="acad-reg", title="Spring registration opens: juniors", day=9, **acad)
    b.event(id="acad-withdraw", title="Last day to withdraw with W", day=10, **acad)


def reminders(b: ProfileBuilder) -> None:
    r = b.reminder
    r("pay jess electric $$", id="pay-jess", day=3, time="18:00", created_day=-2)
    r("hydro lab 4 report!!!", id="lab4", day=-1, time="20:00", created_day=-6, list="School")
    r("finaid verification form", id="finaid-form", day=-5, time="09:00", created_day=-12, list="School")
    r("book advising appt w kirby", id="advising", day=-3, time="12:00", created_day=-10, list="School")
    r("return pemberton books", id="library-books", day=-8, time="17:00", created_day=-22)
    r("take pics of sink leak for northgate", id="sink-pics", day=-9, time="10:00", created_day=-10,
      completed=True, completed_day=-9, completed_time="13:20")
    r("pay frisbee dues", id="dues", day=-5, time="20:00", created_day=-16, completed=True, completed_day=-5,
      completed_time="22:09")
    r("submit PS5", id="ps5", day=-4, time="16:00", created_day=-7, list="School", completed=True,
      completed_day=-4, completed_time="16:58")
    r("decide lease??", id="lease", day=None, created_day=-18)
    r("order E's hoodie", id="ethan-gift", day=-7, time="21:00", created_day=-14, completed=True,
      completed_day=-6, completed_time="22:42")
    r("get package from leasing office", id="package", day=0, time="17:30", created_day=-1)
    r("call mom re ethan bday", id="call-mom", day=0, all_day=True, created_day=-2)
    r("email prof about research assistant position", id="ra-email", day=-35, time="12:00", created_day=-40)
    r("haircut?", id="haircut", day=None, created_day=-30)
    r("flu shot (free @ union)", id="flu-shot", day=-5, time="12:00", created_day=-9)
    r("GIS lit review sources (5)", id="gis-sources", day=2, time="20:00", created_day=-6, list="School")
    r("make stats cheat sheet (1 page 2 sides)", id="cheat-sheet", day=1, time="21:00", created_day=-4,
      list="School")
    r("renters insurance renewal??", id="insurance", day=None, created_day=-25)
    r("op-ed revision for fairweather", id="oped", day=3, time="12:00", created_day=-6, list="School")
    r("sign up for fairweather conference slot", id="conf-slot", day=None, created_day=-7, list="School")
    for i, item in enumerate(["oat milk", "eggs", "dish soap", "spinach", "trash bags"]):
        bought = item in ("oat milk", "spinach")
        r(item, id=f"groc-{i + 1}", day=None,
          created_day=-1 if item in ("eggs", "dish soap") else (-4 if item == "trash bags" else -8),
          list="Groceries", completed=bought, completed_day=-4 if bought else None,
          completed_time="16:45" if bought else None)


def notes(b: ProfileBuilder) -> None:
    n = b.note
    n("apt stuff", "package room code 4471\nmailbox 3R - key on the blue lanyard\nwifi FernhollowFlats-3R, pw on the router sticker\n"
      "northgate emergency line (773) 555-0160\nrent 750 each, autopay on the 1st\ntrash day thurs",
      id="note-apt", day=-30, time="22:10", created_day=-44)
    n("stats midterm", "ch 1-6!!\n- slr assumptions (LINE)\n- interpreting slope/intercept IN CONTEXT w units\n"
      "- r^2 vs adj r^2\n- dummy vars, interaction terms (ugh)\n- residual plots: fan shape = nonconstant var\n"
      "OH wed 2-3:30 halvorsen 311\ncheat sheet 1 page both sides", id="note-stats-midterm", day=-2, time="17:10",
      created_day=-9, folder="School")
    n("lab 4 notes", "double ring infiltrometer, site A (lawn by marsh) + site B (path edge)\n"
      "A: 3 trials, f_c ~ 1.8 cm/hr?\nB: ring kept tipping. 3 trials only. compacted, f_c ~0.4\n"
      "toby has photos. raw data -> lab4_raw.xlsx\nfit horton: f = fc + (f0-fc)e^-kt\nERROR BARS = SE (hana said)",
      id="note-lab4", day=-3, time="10:25", created_day=-7, folder="School")
    n("gis project ideas", "viaducts that flood!!! the one on the walk to fresh basket\n- complaints data from city portal\n"
      "- elevation / storm sewer age?\n- who lives near them (census tracts)\nother idea: tree canopy + heat (boring?)",
      id="note-gis-ideas", day=-12, time="19:25", created_day=-12, folder="School")
    n("gis who does what", "from meeting:\njess L - intro + methods\nowen - data download\nravi - cleaning\n"
      "me - BASE MAP + lit review (5 sources APA)", id="note-gis-split", day=-6, time="18:25", created_day=-6,
      folder="School")
    n("oct money", "rent 750 (paid)\nlakeline ~46 -> jess\nquillnet fiber 23.33\npinecell 35\nreelhouse 7.99\n"
      "frisbee dues 60\nethan hoodie ~66\ngroceries?? too much\npaycheck fri ~330", id="note-money", day=-5,
      time="23:02", created_day=-14, folder="Money")
    n("lease thoughts", "RENEW?\n+ love the apt, close to campus + fresh basket\n+ jess wants to stay\n"
      "- 795 is +45/mo = 540/yr\n- elevator lol\n- bea might move in w marco??\nif bea leaves we need a 3rd or a 2br\n"
      "month to month = 850 each?? no way", id="note-lease", day=-8, time="21:50", created_day=-19)
    n("ethan bday", "hoodie (lakefront, green, M) - ORDERED\ncard from me + biscuit\ngo home sun?? tournament sat\n"
      "bus from rockford? or back to chi then train", id="note-ethan", day=-6, time="22:45", created_day=-15)
    n("op-ed ideas", "lead service lines - grandma's tap\nwho pays?\nnumbers that matter: # of lines left in milwaukee + chi\n"
      "call to action EARLIER (fairweather)\naudience: renters", id="note-oped", day=-6, time="21:20",
      created_day=-12, folder="School")
    n("tourney packing", "cleats\nlights + darks\n2 water bottles (the blue one!!)\nsnacks\nsunscreen\nrain jacket\n$5 parking\ncharger",
      id="note-tourney", day=-4, time="19:45", created_day=-4)
    n("books to read", "- the tree novel jess lent me (finish)\n- something not about water lol\n- maya's fantasy rec w dragons",
      id="note-books", day=-25, time="23:30", created_day=-40)
    n("3am thoughts", "what if rivers have feelings\nwhy do i always remember the lab report at 3am\nbuy a planner (real one)",
      id="note-3am", day=-11, time="03:04", created_day=-11)
    n("finaid docs", "verification worksheet\n- [ ] sign page 2\n- [ ] mom/mike 2024 tax transcript\n- [x] my W-2 from cafe\n"
      "due 16th?? appt tues 12:30 sterling 140", id="note-finaid", day=-7, time="16:10", created_day=-11, folder="Money")
    n("cafe drinks", "fall menu (starts wed?)\nmaple oat latte: 2 shots, 1 pump maple, oat, cinnamon top\n"
      "chai: 6oz concentrate + 6oz milk\ncold brew ratio 1:4", id="note-cafe", day=-1, time="14:35", created_day=-30)
    n("quotes for sci writing", "\"water always remembers where it used to go\" - lindgren, week 2\n"
      "\"data are not facts until someone cares\" - fairweather in class",
      id="note-quotes", day=-14, time="15:20", created_day=-20, folder="School")
    n("cheap meals", "sheet pan gnocchi\nrice + eggs + chili crisp\nbean tacos\nlentil soup (jess recipe)", id="note-meals",
      day=-5, time="18:15", created_day=-33)
    n("fall schedule", "MWF 10 stats (halvorsen 118)\nTTh 9:30 hydro (marsh 204)\nT 2-4:50 hydro lab B12\n"
      "MW 1 GIS (corbin 210)\nMW 3 sci writing (ashby 102) - never put it in my calendar lol\ncafe: tue 5-9, fri 12-4, sun 9-1\nfrisbee mon/wed 7-9",
      id="note-schedule", day=-27, time="20:00", created_day=-35)
    n("things to buy", "u-lock (bike thefts!!)\nnew cleats eventually\nplanner\nshower curtain liner", id="note-buy",
      day=-11, time="18:00", created_day=-20)
    n("movies", "maya recs: octopus doc (DONE, cried)\nthe one about the lighthouse\nsomething funny pls", id="note-movies",
      day=-17, time="23:59", created_day=-30)
    n("hydro lec 10/6", "infiltration capacity vs rate\nhorton: decays exp to f_c\ngreen-ampt: wetting front, suction head\n"
      "ponding time!!\nexam q? compare the two", id="note-hydro-lec", day=-7, time="10:50", created_day=-7,
      folder="School")
    n("gis lec", "projections: conformal vs equal area\nstate plane IL east for city stuff\nraster vs vector\n"
      "georef: need 4+ control points", id="note-gis-lec", day=-8, time="14:20", created_day=-13, folder="School")
    n("spring classes maybe", "STAT 3300 bayes?\nENVS 4100 water policy\nsoils w/ lab\nneed 1 more 3000 stat for minor\n"
      "ask kirby", id="note-spring", day=-3, time="21:00", created_day=-3, folder="School")
    n("summer internships", "city water dept (deadline jan?)\nsustainability office intern (spring too)\n"
      "field tech jobs\nresearch w lindgren??", id="note-intern", day=-8, time="12:10", created_day=-21)
    n("dream journal lol", "dreamed i was a river and toby was a rock\nthen the midterm was in spanish", id="note-dream",
      day=-4, time="07:30", created_day=-18)
    n("thanksgiving?", "go home wed night?\nbus tix early = cheaper\ncafe closed thurs-sun", id="note-thanks",
      day=-10, time="22:00", created_day=-10)
    n("qs for kirby", "stat minor - which 3000 course?\nstudy abroad w/ field station?\ncan i overload spring",
      id="note-kirby", day=-10, time="14:30", created_day=-10, folder="School")
    n("lab report format", "copied from lantern:\nTitle, Abstract (150 words), Intro, Methods (past tense!), Results, "
      "Discussion, References\nfigures numbered + captions\nmax 6 pages\none submission per pair", id="note-format",
      day=-6, time="17:00", created_day=-6, folder="School")
    n("chore wheel", "wk1: jess bath, bea kitchen, me trash\nwk2: same lol\nwk3: rotate", id="note-chores", day=-14,
      time="08:40", created_day=-14)
    n("regression formulas", "b1 = r * sy/sx\nb0 = ybar - b1 xbar\nSE(b1) = s / sqrt(Sxx)\nt = b1/SE\nR^2 = SSR/SST\n"
      "adj R^2 = 1 - (1-R^2)(n-1)/(n-p-1)", id="note-formulas", day=-2, time="22:30", created_day=-20,
      folder="School")
    n("untitled", "call\nsink\nhoodie??", id="note-untitled", day=-2, time="01:12", created_day=-2)
    n("wind chill", "zone D = cup + 2 wings + 2 deep?\nmy mark: break side\ndues PAID", id="note-zone", day=-5,
      time="22:12", created_day=-22)
    n("passwords hint", "the usual + the year we got biscuit\n(don't write the real thing here talia)", id="note-pw-hint",
      day=-40, time="20:00", created_day=-40)


def documents(b: ProfileBuilder) -> None:
    b.document("~/Documents/CVU/Fall/ENVS3410_syllabus.pdf", "ENVS 3410 Watershed Hydrology syllabus", kind="pdf",
               day=-30, time="10:15", id="doc-hydr-syllabus", content=(
        "ENVS 3410 Watershed Hydrology, Fall 2026\nCalder Valley University, Department of Environmental Science\n\n"
        "Instructor: Prof. Robert Lindgren (robert.lindgren@cvu.example), Marsh 318. Office hours Tue 11:00–12:00.\n"
        "Lab TA (sec 02): Hana Kessler (hana.kessler@cvu.example).\n\n"
        "Lecture: Tue/Thu 9:30–10:45, Marsh Science Center 204.\nLab sec 02: Tue 2:00–4:50, Marsh Science Center B12.\n\n"
        "Grading: labs 35%, midterm 20%, final 25%, field project 15%, participation 5%.\n\n"
        "Lab reports are due at 11:59 pm on the Wednesday after the lab, on Lantern, one per pair. Late reports lose "
        "10% per day and are not accepted after three days.\n\nLab schedule:\n"
        f"Lab 1 ({D(-28, 'short')}): Watershed delineation\nLab 2 ({D(-21, 'short')}): Streamflow measurement\n"
        f"Lab 3 ({D(-14, 'short')}): Hydraulic conductivity\nLab 4 ({D(-7, 'short')}): Infiltration (field)\n"
        f"Lab 5 ({D(0, 'short')}): Flume: sediment transport\nLab 6 ({D(7, 'short')}): Groundwater model\n\n"
        "Pre-lab quizzes close at the start of your lab section."))
    b.document("~/Downloads/Lab4_Infiltration_handout.pdf", "Lab 4 Infiltration handout", kind="pdf",
               day=-6, time="16:40", id="doc-lab4-handout", content=(
        "ENVS 3410 Lab 4: Infiltration\n\nGoal: measure infiltration rates at two sites with a double-ring "
        "infiltrometer and fit the Horton equation f(t) = fc + (f0 − fc)e^(−kt).\n\n"
        f"Report due: {D(1)}, 11:59 pm on Lantern. One report per pair. Max 6 pages.\n\n"
        "Sections and points (20):\n- Abstract, 150 words (2)\n- Introduction (2)\n- Methods, past tense (4)\n"
        "- Results: table of rates, Fig. 1 cumulative infiltration, Fig. 2 Horton fit with standard-error bars (6)\n"
        "- Discussion: compare sites; why compacted soil matters for runoff (4)\n- References and formatting (2)\n\n"
        "Common mistakes: missing units, no comparison to the Horton fit, methods written as instructions."))
    b.document("~/Documents/money/fall_budget.csv", "fall budget", kind="csv", day=-1, time="21:40",
               id="doc-budget", rows=[
        ["Category", "Monthly budget", "October so far", "Notes"],
        ["Rent (my third)", "750.00", "750.00", "autopay on the 1st; 795 if we renew"],
        ["Electric (Lakeline)", "45.00", "0.00", "owe Jess 46.20 for Sept"],
        ["Internet (Quillnet Fiber)", "23.33", "23.33", "Bea pays, we split"],
        ["Phone (Pinecell)", "35.00", "35.00", "autopay"],
        ["Groceries", "180.00", "63.18", "my grocery week; Bea paid me back 15.23"],
        ["Reelhouse", "7.99", "0.00", "bills on the 23rd. cancel??"],
        ["Frisbee", "60.00", "60.00", "fall dues, paid"],
        ["Gifts", "40.00", "66.34", "Ethan's hoodie (over!)"],
        ["Fun / eating out", "80.00", "36.85", "maya bday fri"],
        ["Transport", "15.00", "3.40", "mostly bike"],
        ["Income: café (biweekly net)", "", "331.80", "next deposit Fri"],
        ["Savings goal", "500 by Dec", "120.00", "lol"],
    ])
    b.document("~/Documents/CVU/Fall/GIS/proposal_draft_v2.docx", "GIS proposal draft v2", kind="docx",
               day=-1, time="09:55", id="doc-gis-proposal", content=(
        "Where the Water Sits: Flood-Prone Viaducts on Chicago's West Side\nGEOG 3250, Group 4: Jessica Lin, Owen McAllister, "
        "Ravi Desai, Talia Brennan\nDRAFT v2\n\n"
        "Research question: Which railroad viaducts on the West Side flood most often after heavy rain, and how many "
        "residents live within a 10-minute walk of them?\n\n"
        "Data: six years of street-flooding complaints from the city open-data portal (about 41,000 records, filtered "
        "to the West Side by Owen and cleaned by Ravi); county elevation tiles; census tract population.\n\n"
        "Methods (Jess): geocode complaints, join to viaduct locations within 50 m, count complaints per viaduct per "
        "storm, buffer analysis for population.\n\n"
        "Preliminary map: Owen's base map is in the shared drive (done).\n\n"
        "Data summary table: [TALIA – complaints per viaduct, top 10]\n\nLiterature review: [TALIA – 5 sources, APA]\n\n"
        f"Due on Lantern {D(3)}, 5:00 pm. Two pages max."))
    b.document("~/Documents/CVU/Fall/SciWriting/oped_draft1.docx", "op-ed draft 1 (with Fairweather's comments)",
               kind="docx", day=-6, time="21:00", id="doc-oped", content=(
        "The Pipes Under Our Feet\nby Talia Brennan\n\n"
        "Every summer at my grandmother's house in Milwaukee, I let the kitchen tap run for thirty seconds before "
        "filling a glass. Nobody ever told me why. Years later I learned that the line bringing water into her house "
        "was made of lead.\n\n"
        "Across the Midwest, hundreds of thousands of homes still get their water through lead service lines. "
        "Replacing one costs between $5,000 and $12,000. [Fairweather: why should the reader care about this number?]\n\n"
        "Renters are the people with the least say. A landlord decides whether to replace a line; a tenant just "
        "drinks the water. [Fairweather: this is your argument, move it up.]\n\n"
        "Cities should require landlords to disclose lead lines in every lease, and should pay for replacement first "
        "in rental buildings.\n\n(1,140 words)"))
    b.document("~/Downloads/Northgate_Renewal_Offer_3R.pdf", "Northgate renewal offer, Unit 3R", kind="pdf",
               day=-20, time="10:30", id="doc-lease-renewal", content=(
        "NORTHGATE RESIDENTIAL · FERNHOLLOW FLATS\nLEASE RENEWAL OFFER\n\n"
        "Unit: 3R, 2417 W Fernhollow Ave\nResidents: Jess Okafor, Bea Castellano, Talia Brennan\n"
        f"Current lease ends: {D(48)}\nCurrent rent: $2,250.00 per month\n\n"
        "Renewal options:\n1. 12-month lease: $2,385.00 per month\n2. Month-to-month: $2,550.00 per month\n\n"
        f"Decision deadline: 5:00 PM, {D(3)}, via the Resident Portal. All leaseholders must sign. "
        "If one leaseholder does not renew, the remaining residents may apply to renew with a replacement "
        "approved by Northgate, subject to a $75 application fee.\n\n"
        "No response by the deadline: tenancy converts to month-to-month at $2,550.00 per month.\n\n"
        "Questions: leasing@northgate-res.example · (773) 555-0160 · Office hours Mon–Fri 9:00–6:00"))


def browser(b: ProfileBuilder) -> None:
    for d, t, host, path, title in [
        (-30, "10:14", "lantern-lms.test", "/courses/envs3410/syllabus", "Syllabus · ENVS 3410 · Lantern"),
        (-20, "10:40", "northgate-res.example", "/portal/renewal", "Renewal Offer · Resident Portal"),
        (-18, "22:05", "flatscout.example", "/chicago/2-bedroom", "2 bedroom apartments in Chicago"),
        (-17, "21:30", "flatscout.example", "/chicago/3-bedroom?max=2300", "3 bedroom apartments under $2,300"),
        (-12, "19:40", "data.chi-open.example", "/flooding/complaints", "Street flooding complaints · City Open Data"),
        (-12, "09:05", "cvu.example", "/registrar/time-tickets", "Registration time tickets · CVU Registrar"),
        (-11, "10:45", "cvu.example", "/finaid/verification", "Verification · Office of Financial Aid"),
        (-10, "14:25", "cvu.example", "/advising/schedule", "Advising scheduler · Environmental Science"),
        (-10, "18:15", "cvu.example", "/library/rooms", "Book a group study room · Pemberton Library"),
        (-9, "13:45", "northgate-res.example", "/portal/maintenance/new", "New maintenance request · Resident Portal"),
        (-8, "20:30", "reelhouse.example", "/browse/documentaries", "Documentaries · Reelhouse"),
        (-7, "16:00", "cvu.example", "/finaid/appointments", "Book an appointment · Financial Aid"),
        (-7, "21:50", "lakefrontoutfitters.example", "/products/ridgeback-pullover-hoodie", "Ridgeback Pullover Hoodie · Lakefront Outfitters"),
        (-6, "22:38", "lakefrontoutfitters.example", "/checkout", "Checkout · Lakefront Outfitters"),
        (-6, "16:35", "lantern-lms.test", "/courses/envs3410/assignments/lab4", "Lab 4 Report – Infiltration · Lantern"),
        (-5, "18:10", "pantrypal.example", "/recipes/sheet-pan-gnocchi", "Sheet Pan Gnocchi · PantryPal"),
        (-5, "22:05", "paypip.test", "/pay/windchill-cvu", "Pay @windchill-cvu · PayPip"),
        (-4, "19:20", "discline.test", "/events/prairie-ridge-fall-classic", "Prairie Ridge Fall Classic · Discline"),
        (-4, "19:25", "mapsy.example", "/dir/brightwater-union/prairie-ridge-sports-complex", "Directions to Prairie Ridge Sports Complex · Mapsy"),
        (-3, "15:30", "kinnareekitchen.example", "/menu", "Menu · Kinnaree Kitchen"),
        (-3, "09:30", "shiftnest.test", "/swaps/incoming", "Swap requests · ShiftNest"),
        (-2, "12:10", "lantern-lms.test", "/courses/stat3120/files/practice_midterm.pdf", "practice_midterm.pdf"),
        (-2, "21:10", "statsnook.example", "/regression/interaction-terms", "Interpreting interaction terms · StatsNook"),
        (-2, "21:40", "vidstream.example", "/watch/multiple-regression-12-min", "Multiple regression in 12 minutes · VidStream"),
        (-2, "20:25", "paypip.test", "/requests", "Requests · PayPip"),
        (-1, "07:10", "lakeshoremcu.example", "/accounts/checking", "Everyday Checking · Lakeshore MCU"),
        (-1, "09:58", "lantern-lms.test", "/courses/geog3250/groups/4", "Group 4 · GEOG 3250 · Lantern"),
        (-1, "16:41", "cvu.example", "/maps/corbin-hall", "Corbin Hall · Campus map"),
        (-1, "18:02", "shiftnest.test", "/schedule", "My schedule · ShiftNest"),
        (-1, "21:12", "weatherly.example", "/rockford-il/10-day", "Rockford, IL 10-day forecast · Weatherly"),
        (-1, "22:12", "openencyclo.example", "/entry/infiltration-soil-water", "Infiltration (soil water) · OpenEncyclo"),
        (-1, "22:25", "openencyclo.example", "/entry/horton-infiltration-model", "Horton infiltration model · OpenEncyclo"),
        (-1, "22:50", "litfinder.example", "/search?q=urban+viaduct+flooding", "urban viaduct flooding · LitFinder"),
        (-1, "23:05", "litfinder.example", "/paper/stormwater-underpass-review", "A review of stormwater flooding in urban underpasses · LitFinder"),
        (-1, "23:40", "pixmo.example", "/maya.feldman", "maya.feldman · Pixmo"),
        (0, "07:15", "lantern-lms.test", "/courses/envs3410/assignments/lab4", "Lab 4 Report – Infiltration · Lantern"),
        (0, "07:28", "weatherly.example", "/chicago-il/today", "Chicago, IL today · Weatherly"),
        (0, "07:38", "lantern-lms.test", "/courses/envs3410/announcements", "Announcements · ENVS 3410 · Lantern"),
    ]:
        b.web(host, path, title, day=d, time=t, browser="chrome" if host.endswith("lms.test") else "safari")


def memory(b: ProfileBuilder) -> None:
    m = b.remember
    m("Talia bikes from home to campus; it takes about 15 minutes.", category="routine", day=-40, id="mem-01")
    m("Usual Commons Café shifts: Tuesday 5–9 pm, Friday 12–4 pm, Sunday 9 am–1 pm.", category="routine",
      day=-35, id="mem-02")
    m("Roommates are Jess Okafor and Bea Castellano (apartment 3R, Fernhollow Flats).", category="person",
      day=-40, id="mem-03")
    m("Brother Ethan's birthday is October 18.", category="person", day=-38, id="mem-04")
    m("Prefers reminders in the evening rather than first thing in the morning.", category="preference",
      day=-30, id="mem-05", superseded_by="mem-11")
    m("The apartment package room code is 4471.", category="fact", day=-31, id="mem-06", superseded_by="mem-08")
    m("GIS project with Jess Lin, Owen and Ravi: flood-prone viaducts on the West Side.", category="project",
      day=-12, id="mem-07")
    m("The package room code changed to 2093.", category="fact", origin="mail", day=-6, id="mem-08")
    m("Allergic to cashews.", category="fact", day=-40, id="mem-09")
    m("Don't schedule meetings for me before 9:00 am. Reminders before then are fine.", category="preference",
      day=-10, id="mem-10")
    m("If I don't give a time, set reminders for 8:00 am the day before.", category="preference", day=-8,
      id="mem-11")
    m("This week the STAT 3120 midterm is my top priority, ahead of everything else.", category="preference",
      day=-3, id="mem-12")
    m("I focus best in the morning; put errands like groceries or pickups after 4 pm.", category="routine",
      day=-20, id="mem-13")


def build() -> ProfileBuilder:
    b = ProfileBuilder("student", ANCHOR, "Synthetic test profile: full-time university student")
    setup(b)
    contacts(b)
    calendar(b)
    reminders(b)
    notes(b)
    documents(b)
    browser(b)
    memory(b)
    student_msgs.add(b)
    student_mail.add(b)
    ledger(b)
    return b
