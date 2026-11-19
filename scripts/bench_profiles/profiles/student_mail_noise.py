"""Bulk and low-stakes email for the synthetic "student" profile: university notices, newsletters,
social notifications, promotions, plus a few minor person-to-person threads. Everything is invented."""
from __future__ import annotations

from scripts.bench_profiles.lib import ProfileBuilder, tok
from scripts.bench_profiles.profiles.student_mail import (FA, FW, LANTERN, LINDGREN, ME_PER, ME_SCH, PER, SCH,
                                                          TODAY, CU)

D = tok.date


def university(b: ProfileBuilder) -> None:
    e = b.email
    e(account=SCH, day=-15, time="06:30", frm=TODAY, category="news", bulk=True,
      subject="CVU Today: free flu shots next week, and Lot C closes for resurfacing",
      body=(f"Student Health is giving free flu shots {D(-6, 'short')}–{D(-4, 'short')} in Brightwater Union 120, "
            "walk-in, 10 to 4. Bring your ID.\n\nParking Lot C closes Monday for two weeks; permit holders can use "
            "the Halsted structure at no charge.\n\nAlso today: Homecoming court nominations close Friday at noon."))
    e(account=SCH, day=-6, time="06:30", frm=TODAY, category="news", bulk=True,
      subject="CVU Today: Midterm wellness week",
      body=("It's midterm wellness week. Pemberton's lobby has free tea from 8 PM to midnight through Thursday, "
            "and the counseling center has added drop-in hours (Mon–Thu, 1–4 PM, Sterling 210).\n\n"
            "Thursday at 6 PM in Marsh 101: \"Who pays for clean water?\", a panel with two city utility engineers "
            "and a tenants' rights organizer."))
    e(account=SCH, day=-1, time="06:30", frm=TODAY, category="news", bulk=True,
      subject="CVU Today: Pemberton group rooms closed Tuesday",
      body=(f"Pemberton Library's group study rooms 2A–2D are closed all day {D(0, 'short')} for carpet cleaning. "
            "Quiet study floors stay open.\n\n"
            f"Faculty submit midterm grades by {D(12, 'short')}. The Calder Connector shuttle detours around Halsted "
            "Walk this week while the water main is replaced."))
    e(account=SCH, day=0, time="06:30", frm=TODAY, category="news", bulk=True, unread=True,
      subject="CVU Today: Halsted Walk crossing closed through Friday",
      body=("Water main work closes the Halsted Walk crossing through Friday. Cyclists coming from the west should "
            "use the Calder Way gate; the bike racks by Corbin Hall are still reachable from that side.\n\n"
            f"Environmental & Sustainability Careers Night is {D(8, 'short')}, 5–7 PM, Brightwater Ballroom."))
    e(account=SCH, day=-11, time="17:45", frm=("CVU Police", "alerts@cvu.example"), category="attention",
      subject="Timely Warning: bike thefts near Corbin Hall",
      body=("Four bicycles secured with cable locks were stolen from racks near Corbin Hall between "
            f"{D(-14, 'short')} and {D(-11, 'short')}. Use a U-lock through the frame and register your bike "
            "with CVU Police. Report suspicious activity at (312) 555-0199."))
    e(account=SCH, day=-12, time="09:00", frm=("CVU Registrar", "registrar@cvu.example"), category="attention",
      subject="Your Spring 2027 registration time ticket",
      body=(f"Talia Brennan, your registration time ticket opens {D(9)} at 8:00 AM. You must have an advising "
            "release from your department before you can register."))
    e(account=SCH, day=-18, time="08:00", frm=("CVU Student Accounts", "bursar@cvu.example"), category="accounts",
      subject="Your October eBill is available",
      body=("Tuition and fees $6,840.00, Dean's Stewardship Scholarship −$3,500.00, state grant −$2,100.00, "
            "Federal loan −$1,240.00. Balance: $0.00. No payment is due."))
    e(id="em-it-mfa", account=SCH, day=-10, time="10:00", frm=("CVU IT Services", "it-help@cvu.example"),
      category="attention", subject="Action required: re-enroll in CVU multi-factor sign-in",
      body=(f"All students must re-enroll in the new multi-factor sign-in by {D(18)}. It takes about 2 minutes "
            "at the IT Help portal. You will see a reminder banner when you sign in until you finish."))
    e(account=SCH, day=-13, time="11:30", frm=("CVU Career Services", "careers@cvu.example"), bulk=True,
      category="news", subject=f"Careers Night {D(8, 'short')}: who's coming",
      body=("Confirmed so far: the city water department, two environmental consulting firms, the county forest "
            "preserve district and a stormwater start-up. Drop-in resume reviews 4–5 PM in Brightwater 210."))
    e(account=SCH, day=-5, time="10:00", frm=("Pemberton Library", "library@cvu.example"), category="attention",
      subject="Your hold is ready: Urban Stormwater Systems",
      body=("Urban Stormwater Systems (2nd ed.) is waiting at the Pemberton circulation desk until "
            f"{D(2, 'short')}. You also have one overdue item."))
    e(account=SCH, day=-2, time="07:30", frm=("Pemberton Library", "library@cvu.example"), category="attention",
      subject="Overdue item: Rivers, Rain and Runoff (4th ed.)",
      body=(f"This item was due {D(-8, 'short')}. Overdue fine: $0.25 per day, maximum $10. Renew online "
            "unless another patron has placed a hold."))
    e(account=SCH, day=-7, time="11:00", frm=FA, category="news", bulk=True,
      subject="Aid News: scholarship essay workshops start Thursday",
      body=("Writing a renewal reflection? Workshops run Thursdays at 4 PM in Sterling 120 through November. "
            "Last year's winning reflections are on the Aid Portal under Scholarships > Examples."))


def newsletters(b: ProfileBuilder) -> None:
    ww = ("The Watershed Weekly", "hello@watershedweekly.example")
    for d, s, body in [
        (-26, "The Watershed Weekly: Why your gutter matters more than you think",
         "A single downspout can send 600 gallons into the street in an inch of rain. This week: three cities paying "
         "residents to disconnect them, and a stormwater engineer who keeps a canoe in her office."),
        (-19, "The Watershed Weekly: The sturgeon are back",
         "Lake sturgeon counts on two Wisconsin tributaries hit a 40-year high. Also: what a '100-year storm' "
         "actually means, in one chart."),
        (-12, "The Watershed Weekly: Lead lines, explained",
         "How many lead service lines are left in the Midwest, what replacing one costs, and why renters rarely get "
         "a say. Reader question of the week: is a water filter enough?"),
        (-5, "The Watershed Weekly: Fall turnover",
         "Why lakes flip in autumn and what it does to the fish. Plus a citizen-science app for measuring stream "
         "turbidity with your phone camera, and three entry-level jobs we'd apply for."),
    ]:
        b.email(account=PER, day=d, time="07:00", frm=ww, subject=s, body=body, category="news", bulk=True)
    cc = ("The Calder Courier", "newsletter@caldercourier.example")
    b.email(account=SCH, day=-13, time="08:00", frm=cc, category="news", bulk=True,
            subject="Courier: Rents near campus up 7%",
            body=("We looked at 400 listings within a mile of campus. Average three-bedroom rent rose 7% from last "
                  "fall, to $2,410. Landlords cite insurance and property taxes; tenant groups say renewals are "
                  "rising faster than new leases."))
    b.email(account=SCH, day=-2, time="08:00", frm=cc, category="news", bulk=True,
            subject="Courier: Club sports could lose 10% of funding",
            body=("Student government's draft budget trims club sports by 10% next year. Ultimate, rowing and "
                  "climbing would be hit hardest because of travel costs. Public comment is open until Friday."))
    b.email(account=PER, day=-7, time="12:00", frm=("StatSnack", "letters@statsnack.example"), category="news",
            bulk=True, subject="StatSnack #114: Interaction terms without tears",
            body=("When a slope depends on another variable. We fit ice cream sales on temperature, add a weekend "
                  "dummy and the interaction, and read every coefficient out loud in plain English."))
    b.email(account=PER, day=-10, time="18:00", frm=("Midwest Field Notes", "notes@midwestfieldnotes.example"),
            category="news", bulk=True, subject="Field Notes: The cranes are moving",
            body="Peak sandhill crane migration is about two weeks out. Our map of six viewing marshes within two hours of Chicago.")
    gc = ("Green Calder", "greencalder@cvu.example")
    b.email(account=SCH, day=-17, time="20:00", frm=gc, category="social", bulk=True,
            subject="Green Calder: river cleanup Saturday",
            body="Meet at the boathouse at 9 AM. Gloves, grabbers and bags provided. Last year we pulled 600 lbs of trash out of the river.")
    b.email(account=SCH, day=-4, time="20:00", frm=gc, category="social", bulk=True,
            subject="Green Calder: composting vote Thursday",
            body="We vote on the spring dining-hall composting campaign Thursday at 7 PM in Marsh 105. Vegan pizza from the place on Damen.")
    ng = ("Northgate Residential", "community@northgate-res.example")
    b.email(account=PER, day=-16, time="12:00", frm=ng, category="news", bulk=True,
            subject="Fernhollow Flats Community News – October",
            body=("Elevator modernization: parts are on order and work should start in November. Reminder: quiet "
                  "hours are 10 PM–7 AM, and bikes may not be stored in hallways."))
    b.email(account=PER, day=-2, time="12:00", frm=ng, category="news", bulk=True,
            subject="Coffee morning in the lobby this Saturday",
            body="Saturday 9–11 AM in the lobby: coffee, cider donuts and a table where you can sign up for the winter parking lottery.")


def social(b: ProfileBuilder) -> None:
    px = ("Pixmo", "notify@pixmo.example")
    for d, t, s, body in [
        (-18, "19:30", "You have 4 new followers", "chloe.ade, greencalder, windchill.ultimate and dani.fux followed you."),
        (-9, "21:15", "lena.nvk mentioned you in a comment", "lena.nvk: \"@taliab this is literally us at the thrift haul\""),
        (-5, "21:30", "chloe.ade and 3 others liked your photo", "Your photo \"first practice of the season 🥏\" has 24 likes."),
        (-1, "21:02", "maya.feldman tagged you in a post", "maya.feldman: \"study grind w my fav 📚✨\""),
    ]:
        b.email(account=PER, day=d, time=t, frm=px, subject=s, body=body, category="social", bulk=True)
    b.email(account=PER, day=-6, time="09:00", frm=("Trailmark", "kudos@trailmark.example"), category="social",
            bulk=True, subject="Your week: 9 rides, 35 mi",
            body="9 rides · 35.1 mi · 2 h 21 min. Longest: 6.2 mi to Ridgeline Fields. Dani F. and Theo P. gave kudos.")
    b.email(id="em-discline-change", account=SCH, day=-1, time="21:20", frm=("Discline", "noreply@discline.test"),
            category="social", bulk=True, subject="Schedule updated: Prairie Ridge Fall Classic",
            body=("Pool B, Round 1: Wind Chill (CVU) vs Rock River Riptide · Field 6 · 8:30 AM (was 9:00 AM).\n"
                  "Round 2: vs Beloit Blizzard · Field 6 · 10:15 AM."))


def promos(b: ProfileBuilder) -> None:
    tp = ("Thread & Pine", "hello@threadandpine.example")
    b.email(account=PER, day=-18, time="10:00", frm=tp, category="promotions", bulk=True,
            subject="Still thinking about the Juniper cord jacket?",
            body="The Juniper cord jacket in rust, size S, is still in your cart at $88. Free shipping over $75 until Sunday.")
    b.email(account=PER, day=-7, time="10:00", frm=tp, category="promotions", bulk=True,
            subject="Members shop the Fall Friends Sale 24 hours early",
            body="Your early-access code is EARLY24: 25% off flannels, knits and the cord jacket. Starts tomorrow at 9 AM.")
    b.email(id="em-promo-final-hours", account=PER, day=0, time="07:10", frm=tp, category="promotions", bulk=True,
            unread=True, subject="⏰ FINAL HOURS: 60% off ends at midnight",
            body=("This is it: 60% off select outerwear ends TONIGHT at midnight. The Juniper cord jacket is now $35. "
                  "Once it's gone, it's gone."))
    fk = ("Forkful", "deals@forkful.example")
    b.email(account=PER, day=-9, time="17:30", frm=fk, category="promotions", bulk=True,
            subject="Midterm fuel: 20% off after 10 PM",
            body="Code NIGHTOWL takes 20% off orders after 10 PM at Saigon Corner, Kinnaree Kitchen and 14 more spots near you.")
    b.email(id="em-forkful-tonight", account=PER, day=0, time="07:02", frm=fk, category="promotions", bulk=True,
            unread=True, subject="⚠️ Your $15 credit expires TONIGHT",
            body="Use your $15 credit on any order over $30 before 11:59 PM tonight. After that, it's gone for good!")
    b.email(account=PER, day=-20, time="19:00", frm=("Reelhouse", "hello@reelhouse.example"), category="promotions",
            bulk=True, subject="New on Reelhouse: The Quiet Delta",
            body="A four-part docuseries about the towns where the river meets the gulf, from the team behind Eight Arms, One Heart.")
    b.email(account=SCH, day=-3, time="11:00", frm=("Calder Co-op Bookstore", "news@calder-coop.example"),
            category="promotions", bulk=True, subject="Homecoming leftovers: hoodies 2 for $50",
            body="Crewnecks and hoodies from the homecoming run, all sizes, 2 for $50 through Sunday at the Brightwater store.")
    b.email(account=PER, day=-6, time="12:00", frm=("Pinecell Mobile", "offers@pinecellmobile.example"),
            category="promotions", bulk=True, subject="Give a friend a free month, get $50",
            body="Your referral link gives a friend one free month of Unlimited Lite and takes $50 off your next bill.")
    b.email(account=PER, day=-24, time="09:00", frm=("ShelfSwap", "hello@shelfswap.example"), category="promotions",
            bulk=True, subject="Your buyback quotes are in",
            body="We'll pay $31 for Watershed Hydrology Basics (2nd ed.) and $18 for Regression in Practice (3rd ed.) in good condition.")
    fb = ("Fresh Basket Market", "deals@freshbasket.example")
    b.email(account=PER, day=-15, time="06:00", frm=fb, category="promotions", bulk=True,
            subject="Chicken thighs $1.99/lb this week",
            body="Plus 20% off oat milk and the first squash of the season. Digital coupons load to your phone number at checkout.")
    b.email(id="em-fb-deals", account=PER, day=-1, time="06:00", frm=fb, category="promotions", bulk=True,
            subject="This week: eggs $2.49/dozen, dish soap buy one get one",
            body=("Large eggs $2.49 a dozen and buy-one-get-one dish soap through Sunday. "
                  "Milwaukee Ave store: open daily 7 AM–10 PM."))
    b.email(account=PER, day=-14, time="10:00", frm=("Lakefront Outfitters", "hello@lakefrontoutfitters.example"),
            category="promotions", bulk=True, subject="Welcome! Here's 10% off your first order",
            body="Use WELCOME10 on anything, including the Ridgeback pullover hoodie in eight colors (forest green is back).")


def minor_people(b: ProfileBuilder) -> None:
    e = b.email
    for d, t, s, body in [
        (-12, "10:40", "Grade posted: SCWR 3020 Peer review",
         "Your grade: Complete ✓\nFeedback from Cora Fairweather: \"Specific, kind and useful. Thank you.\""),
        (-3, "14:05", "GEOG 3250: Grade posted for GIS Lab 3 – Joins",
         "Your grade: 22 / 25\nFeedback: \"Join worked; symbology legend is missing units.\""),
        (-2, "09:00", "New reply in ENVS 3410 discussion: \"Horton vs Green-Ampt?\"",
         "Toby Ashcroft replied: \"Horton is empirical, Green-Ampt is physically based. That's the one-line version.\""),
    ]:
        e(account=SCH, day=d, time=t, frm=LANTERN, subject=s, body=body, category="attention", bulk=True)
    e(account=SCH, day=-1, time="12:30", frm=("Sam Whitlock", "sam.whitlock@cvu.example"),
      to=["windchill-ultimate@lists.cvu.example"], subject="Rockford roster + rides",
      body=("Hi all,\n\nRoster for Saturday is 18 players. Rides: Lucas's van (8), Hannah's car (4), Theo's car (4), "
            "two people with Imani. Ride list goes out Wednesday with the final schedule.\n\nSam"))
    e(account=SCH, day=-4, time="09:30", frm=LINDGREN, to=["envs3410-f26@lists.cvu.example"],
      subject="ENVS 3410 – field project groups",
      body=("Dear all,\n\nPlease form groups of three for the field project by the end of next week and send me "
            "one email per group with a site you'd like to study. The project counts for 15% of your grade.\n\n"
            "Regards,\nR. Lindgren"))
    e(account=SCH, day=-2, time="10:15", frm=FW, to=["scwr3020-f26@lists.cvu.example"],
      subject="SCWR 3020: conference sign-up sheet",
      body=("Dear all,\n\nA reminder that conference slots for next week are on the sheet outside Ashby 214. "
            "About half of you have signed up. Please choose a slot by Wednesday so I can plan my reading.\n\n"
            "Kind regards,\nCora Fairweather"))
    e(account=SCH, day=-2, time="16:00", frm=("Gwen Saldana", "gwen.saldana@cvu.example"),
      to=["commons-cafe-staff@lists.cvu.example"], subject="Fall menu starts Wednesday",
      body=("Hi team! The fall menu starts Wednesday. New drinks: maple oat latte, cider chai, brown-butter cold "
            "brew. Recipe cards are taped by the register; please read them before your first shift. Thanks!\n\nGwen"))
    e(id="em-mike-transcript", account=PER, day=-1, time="21:40", frm=("Mike Doyle", "mike.doyle@mailbox.example"),
      subject="tax transcript", unread=True,
      body=("T,\n\nTried to get the transcript online. It wants to mail an activation code to the house first, "
            "5 to 10 days. I'll try the other way at lunch tomorrow.\n\nMike"))
    e(account=SCH, day=-12, time="20:15", frm=("Forkful", "orders@forkful.example"), category="accounts",
      subject="Your Forkful order from Kinnaree Kitchen",
      body="Pad see ew  13.95\nThai iced tea  4.50\nDelivery + service  5.10\nTip  3.00\nTotal  $26.55")
    e(account=PER, day=-24, time="10:00", frm=CU, category="accounts", subject="Your Lakeshore MCU sign-in code",
      body="Your one-time sign-in code is 402 117. It expires in 10 minutes. We will never call or text to ask for this code.")
    e(id="em-gis-sync-invite", account=SCH, day=-1, time="23:35", frm=("Jessica Lin", "jessica.lin@cvu.example"),
      to=[ME_SCH, "owen.mcallister@cvu.example", "ravi.desai@cvu.example"], category="attention",
      subject=f"Invitation: GIS quick sync, Fri {D(3, 'short')} 8:00–8:30 AM (Hollowcall)",
      body=("hi all! 30-min video check before we submit at 5. sorry it's early, it's my only gap before lab\n\n"
            "Accept · Maybe · Decline\n\njess"))
    e(id="em-ravi-table", account=SCH, day=-1, time="22:10", frm=("Ravi Desai", "ravi.desai@cvu.example"),
      to=[ME_SCH, "jessica.lin@cvu.example"], subject="numbers for the summary table",
      body=("yo here are the top viaducts by complaints (2020-2025, within 50 m):\n"
            "1. Rail yard underpass: 412\n2. Fernhollow & Milwaukee: 287\n3. 16th St viaduct: 240\n"
            "4. Lake St east: 198\n5. Grand Ave rail bridge: 171\n\nfull list in the drive. -ravi"))
    e(account=SCH, day=-8, time="15:10", frm=("Hana Kessler", "hana.kessler@cvu.example"),
      to=["envs3410-lab02@lists.cvu.example"], subject="Lab 3 grades + regrades",
      body=("Lab 02,\n\nLab 3 grades are on Lantern. Regrade requests by email within 7 days, with the rubric line "
            "you think is wrong. Most common deduction: methods too long.\n\nHana"))
    e(account=SCH, day=-3, time="11:20", frm=("Denise Albright", "denise.albright@cvu.example"),
      subject="Before Tuesday's appointment",
      body=("Hi Talia,\n\nIf it's easier, you can upload documents to the Aid Portal before Tuesday. A clear phone "
            "photo of the signed page 2 is fine. If your parents can't get the tax transcript in time, bring "
            "whatever they have and we'll talk through options.\n\nDenise"))
    e(account=SCH, day=-4, time="16:30", frm=("Miriam Haddad", "miriam.haddad@cvu.example"),
      to=["stat3120-f26@lists.cvu.example"], subject="STAT 3120: no problem set over midterm week",
      body=("Dear all,\n\nThere will be no problem set due during midterm week. Problem Set 6 will be posted the "
            "Friday after the exam.\n\nBest regards,\nProfessor Haddad"))
    e(account=PER, day=-2, time="10:00", frm=("Brightsmile Dental", "reminders@brightsmiledental.example"),
      subject=f"Reminder: cleaning on {D(8, 'short')} at 8:30 AM",
      body="Reply C to confirm or call (773) 555-0170 to reschedule. Please arrive 10 minutes early.")
    _ = ME_PER


def add(b: ProfileBuilder) -> None:
    for fn in (university, newsletters, social, promos, minor_people):
        fn(b)
