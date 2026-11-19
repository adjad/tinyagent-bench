"""iMessage/SMS threads for the "tech" profile (all invented)."""
from __future__ import annotations

from scripts.bench_profiles.lib import ProfileBuilder, Thread, tok

W, D = tok.dow, tok.date
U = {"unread": True}


def _id(x: str) -> dict:
    return {"id": x}


def add(b: ProfileBuilder) -> None:
    _hana(b)
    _bach(b)
    _mom(b)
    _samuel(b)
    _priya(b)
    _rachel(b)
    _little_ferns(b)
    _striders(b)
    _kofi(b)
    _sam(b)
    _gwen(b)
    _puget(b)
    _extras(b)


def _hana(b: ProfileBuilder) -> None:
    h, me = "ct-hana", "me"
    t = b.thread("hana", [h])
    t.script(-28, [("07:10", h, "zoe asked where daddy's plane is 😂 told her chicago"),
                   ("07:42", me, "Ha. Tell her it's parked. Landing 7:50 tonight, home by 9"),
                   ("07:44", h, "ok ❤️ safe flight"),
                   ("20:12", me, "Landed, on the train"),
                   ("20:13", h, "she's fighting bedtime, maybe u can do the last book")])
    t.script(-25, [("10:15", h, "post-night zombie. can u do the farmers market w her"),
                   ("10:17", me, "Just back from the run, already putting her shoes on. Go sleep"),
                   ("10:17", h, "love u")])
    t.script(-22, [("15:50", h, "running late off shift, can u grab her today?? sorry sorry"),
                   ("15:52", me, "Got it, leaving at 5"),
                   ("18:05", h, "thank u. trauma bay was nuts")])
    t.script(-20, [("12:30", h, "oct schedule is up. putting it on the shared cal"),
                   ("12:31", h, "days {{date:-1:short}} + {{date:+0:short}}, nights {{date:-5:short}} {{date:-4:short}} {{date:+2:short}}"),
                   ("12:40", me, "Ok so I've got pickup {{date:-1:short}} and {{date:+0:short}}. Noted")])
    t.script(-17, [("18:20", h, "kitchen sink is dripping again. under the cabinet too"),
                   ("18:24", me, "Ugh. I'll call a plumber this week"),
                   ("18:25", h, "the towel is doing its best")])
    t.script(-15, [("21:00", h, "did u call the plumber"),
                   ("21:14", me, "Tomorrow, promise")])
    t.script(-13, [("13:02", me, "Plumber booked: Puget Pipe, Fri {{date:+2:short}} 8–10am. Earliest they had"),
                   ("13:20", h, "two more weeks of towel ok"),
                   ("13:21", h, "thank u for doing it tho")])
    t.script(-11, [("10:30", h, "can we do a date night soon. like a real one"),
                   ("10:41", me, "Yes. Sat {{date:+3:short}}? I'll book somewhere and ask Maddie"),
                   ("10:42", h, "yesss. not sushi tho we always do sushi")])
    t.script(-9, [("08:15", h, "zoe's daycare shoes have a hole. ordering new ones"),
                  ("08:20", me, "Size 9 now right?"),
                  ("08:21", h, "9.5 😩 she grows overnight")])
    t.script(-8, [("17:45", me, "Booked Osteria Fiamma, Sat {{date:+3:short}} at 7"),
                  ("17:50", h, "ooh fancy"),
                  ("17:51", me, "Asking Maddie for 6:30")])
    t.script(-7, [("12:10", h, "remind me when ur denver trip is again"),
                  ("12:20", me, "{{dow:+6}} {{date:+6:short}} to {{dow:+8}} {{date:+8:short}}. Back Thu night ~8:30"),
                  ("12:21", h, "ugh i'm on days tue wed thu that week"),
                  ("12:22", h, "will ask aiko for pickups"),
                  ("12:30", me, "Sorry. Can I ask Gwen for Thursday?"),
                  ("12:31", h, "yes pls")])
    t.script(-6, [("19:40", me, "Maddie confirmed Saturday 6:30"),
                  ("19:45", h, "🙌")])
    t.script(-5, [("18:30", h, "heading in. zoe had mac n cheese, bath done, she wants u to do the dragon voice"),
                  ("18:31", me, "On it. Have a quiet night 🤞"),
                  ("18:31", h, "never say quiet")])
    t.script(-4, [("09:40", h, "home finally, handoff ran long. was not quiet."),
                  ("09:44", me, "Just finished the run. Coffee's on when we're back, then Zoe and I are going to the park so you can sleep"),
                  ("09:45", h, "ur the best"),
                  ("15:40", h, "awake. did she nap"),
                  ("15:42", me, "45 min in the stroller, so sort of")])
    t.script(-3, [("11:00", h, "aiko says yes to tue + wed pickups next week 🙏 thurs gwen?"),
                  ("11:15", me, "Texting Gwen now"),
                  ("19:30", h, "how was ur mom"),
                  ("19:35", me, "Good. Talked for an hour. She wants pictures of Zoe in the pumpkin costume"),
                  ("19:36", h, "costume still in the shipping box lol")])
    t.script(-2, [("06:30", h, "on call week starts today right? ur phone better be loud"),
                  ("06:40", me, "Yep, Mon to Mon. Sorry in advance for 3am buzzing"),
                  ("06:41", h, "i'm used to it from work 🙃"),
                  ("17:20", h, "can u get milk + bananas on the way home"),
                  ("17:21", me, "Yep"),
                  ("21:05", h, "first night on call. godspeed")])
    t.script(-1, [("06:25", h, "left. zoe still asleep. ur doing drop off right"),
                  ("06:40", me, "Yes"),
                  ("12:35", h, "lunch break. how's launch stuff"),
                  ("12:40", me, "Busy. GA moved to 1pm Thursday so at least it's not at dawn"),
                  ("12:41", h, "lol dawn for u is 7"),
                  ("19:50", h, "plumber texted me too?? says moved to fri 1-3"),
                  ("19:51", h, "i can do it, i'm home till my night shift fri", _id("msg-hana-plumber")),
                  ("19:58", me, "Yeah they moved us. Thank you — I've got an interview at 10 and the retro, plus GA follow-ups"),
                  ("19:59", h, "np"),
                  ("20:30", h, "zoe asleep?"),
                  ("20:31", me, "Out by 8:10. Two books"),
                  ("22:15", h, "going to bed, good luck w the pager")])
    t.script(0, [("00:41", me, "Paged. Laptop's in the living room, don't worry if you hear me"),
                 ("06:20", h, "saw ur text. bad night? 😟"),
                 ("06:21", h, "left, zoe still asleep, lunch is in the fridge", _id("msg-hana-left-d0")),
                 ("06:22", h, "u have pickup tonight, 5:30 latest pls. i'm on till 7:30",
                  _id("msg-hana-pickup-tonight")),
                 ("06:23", h, "she gets sad when she's the last one"),
                 ("07:02", me, "Rough night, it's under control. Pickup 5:30, got it"),
                 ("07:03", h, "❤️ coffee is in the thermos"),
                 ("07:31", h, "also picture day is tmrw, green sweater pls 💚 it's on the dresser", U)])


def _bach(b: ProfileBuilder) -> None:
    j, tm, l, bn, me = "ct-jamal", "ct-tom", "ct-luis", "ct-ben", "me"
    t = b.thread("bach", [j, tm, l, bn], title="Kofi's Last Ride 🥃🪓")
    t.script(-26, [("20:01", j, "OK GENTLEMEN 🚨🚨 kofi bach planning starts NOW"),
                   ("20:02", tm, "🍻🍻🍻"),
                   ("20:02", l, "finally lmaooo"),
                   ("20:04", j, "dates: nov 6-8. kofi's fiancée approved 🙏 cabin near leavenworth",
                    _id("msg-bach-dates")),
                   ("20:05", bn, "i'm in. need to clear w the wife but in"),
                   ("20:07", me, "In. Hana's on board"),
                   ("20:08", tm, "LEAVENWORTH 🥨🥨 german town baby"),
                   ("20:09", l, "lederhosen mandatory"),
                   ("20:09", j, "NO"),
                   ("20:10", l, "Laughed at \"NO\"")])
    t.script(-22, [("12:40", j, "activities brainstorm go 🧠"),
                   ("12:41", tm, "axe throwing!!!"),
                   ("12:41", bn, "brewery tour"),
                   ("12:43", l, "river float? or is it too cold in nov"),
                   ("12:44", j, "too cold bro 🥶"),
                   ("12:50", me, "Bowling Saturday night? There's an alley in town"),
                   ("12:51", j, "YES"),
                   ("12:52", tm, "bowling is fine but axe throwing is better 🪓"),
                   ("21:30", bn, "what does kofi actually want tho"),
                   ("21:31", j, "he said 'nothing embarrassing' so 👀")])
    t.script(-9, [("19:15", j, "ok marcus can u own bowling? book a lane sat 8pm 🎳", _id("msg-bach-bowling-ask")),
                  ("19:20", me, "Yep, I'll book it this week", _id("msg-bach-bowling-yes")),
                  ("19:21", j, "🐐"),
                  ("19:22", l, "marcus the reliable one as always"),
                  ("19:23", tm, "marcus has a spreadsheet for this i guarantee it"),
                  ("19:24", me, "It's a note, not a spreadsheet"),
                  ])
    t.script(-7, [("09:10", j, "💸 cabin deposit: everyone send me $180 by FRIDAY 💸 pocketpay @jamal-reyes",
                   _id("msg-bach-deposit")),
                  ("09:12", bn, "sent"),
                  ("09:30", l, "payday is thurs, sending then"),
                  ("11:02", tm, "sent 🫡")])
    t.script(-6, [("22:10", tm, "UPDATE: i booked axe throwing sat 4pm for 6 ppl 🪓🪓🪓 it was 20% off",
                   _id("msg-bach-axe")),
                  ("22:11", j, "LEGEND"),
                  ("22:12", l, "so do we still do bowling"),
                  ("22:15", j, "scrap bowling, too much in one day. marcus ur off the hook 🙏",
                   _id("msg-bach-scrap-bowling")),
                  ("22:30", me, "Fine by me, hadn't booked yet"),
                  ("22:31", bn, "🪓🪓")])
    t.script(-4, [("11:05", j, "🚨 BIG UPDATE 🚨 kofi's uncle is letting us use his place in leavenworth for FREE",
                   _id("msg-bach-free-cabin")),
                  ("11:05", j, "no deposit needed!! ben + tom i'm refunding u today", _id("msg-bach-no-deposit")),
                  ("11:06", tm, "W UNCLE"),
                  ("11:06", l, "good bc i never sent mine 😅"),
                  ("11:07", bn, "free cabin 🥹"),
                  ("11:20", me, "Amazing. Tell him thanks from all of us"),
                  ("11:22", j, "he wants us to not burn it down. low bar")])
    t.script(-2, [("20:40", l, "who's driving"),
                  ("20:42", bn, "i have the minivan 🚐 fits 6"),
                  ("20:43", tm, "minivan bach lets gooo"),
                  ("20:45", j, "ben drives there, someone else back"),
                  ("20:50", l, "not me i'll be dead"),
                  ("21:10", bn, "marcus u drive back? ur the responsible one")])
    t.script(-1, [("13:15", l, "BOYS"),
                  ("13:15", l, "I GOT THE JOB 🎉🎉🎉🎉 senior designer at the studio!!!", _id("msg-bach-luis-job")),
                  ("13:16", j, "LUISSSSS 🔥🔥🔥"),
                  ("13:16", tm, "LETS GOOOO"),
                  ("13:17", bn, "so proud of u man 🥹"),
                  ("13:30", j, "first round at the cabin is on luis now 😂"),
                  ("13:31", l, "fair"),
                  ("14:05", me, "Congrats Luis!! Well deserved"),
                  ("16:40", tm, "ok merch idea: shirts that say KOFI'S LAST RIDE with his face from 8th grade"),
                  ("16:41", j, "I HAVE THAT PHOTO"),
                  ("16:42", bn, "😭😭😭"),
                  ("16:45", l, "he will kill us"),
                  ("16:46", tm, "worth it"),
                  ("22:50", j, "taking shirt sizes tmrw. marcus u still a M?")])
    t.script(0, [("06:55", bn, "morning. still need a driver back sunday 👀", U),
                 ("07:30", tm, "also kofi asked me what we're planning and i panicked and said 'chess tournament'", U),
                 ("07:31", l, "😂😂😂😂", U),
                 ("07:33", j, "CHESS TOURNAMENT 💀", U)])


def _mom(b: ProfileBuilder) -> None:
    m, me = "ct-mom", "me"
    t = b.thread("mom", [m])
    t.script(-27, [("18:00", m, "Hello Marcus. Did you get back from Chicago safely? Love, Mom"),
                   ("19:15", me, "Yes, got back last night. All good!"),
                   ("19:16", m, "Thank God. Kiss Zoe for me.")])
    t.script(-20, [("10:30", m, "Marcus, your Auntie Funmi is asking for your address to send Zoe something. Is it still the same?"),
                   ("12:05", me, "Same address Mom"),
                   ("12:07", m, "Okay I will tell her.")])
    t.script(-18, [("17:00", m, "Did Hana like the recipe I sent? The jollof one."),
                   ("18:10", me, "She loved it. Zoe ate the plantains and nothing else"),
                   ("18:11", m, "That is my granddaughter.")])
    t.script(-14, [("16:45", m, "I watched a program about computers being hacked. Please make sure your company is careful."),
                   ("17:30", me, "We are, don't worry 😄"),
                   ("17:31", m, "I am your mother, I worry.")])
    t.script(-8, [("09:20", m, "Good morning Marcus. Can we talk on the phone this weekend? It has been too long.",
                   _id("msg-mom-weekend-ask")),
                  ("12:44", me, "Yes! I'll call you Sunday around 4 your time", _id("msg-mom-call-plan")),
                  ("12:45", m, "Wonderful. I will cook and then sit by the phone. 😊")])
    t.script(-5, [("20:10", m, "Your brother says you are going to Denver for work. Wear a coat, it is cold there."),
                  ("21:00", me, "It's October Mom, it'll be fine"),
                  ("21:01", m, "Take the coat.")])
    t.script(-3, [("12:58", me, "Calling in 2 min"),
                  ("12:59", m, "I am ready."),
                  ("14:20", m, "It was so nice to talk with you today. Zoe is getting so big. Thank you for calling. Love, Mom",
                   _id("msg-mom-call-thanks")),
                  ("14:25", me, "Love you Mom. Will send pumpkin pictures")])
    t.script(-1, [("08:00", m, "Good morning. Samuel says I should not make a fuss about my birthday. Do not spend money on me.", _id("msg-mom-no-fuss")),
                  ("08:30", me, "Too late 😉"),
                  ("08:31", m, "Marcus!"),
                  ("08:32", m, "Okay. But nothing big.")])
    t.script(0, [("07:10", m, "Good morning my son. I am praying for your work launch tomorrow. Do not forget to eat breakfast. Love, Mom", U)])


def _samuel(b: ProfileBuilder) -> None:
    s, me = "ct-samuel", "me"
    t = b.thread("samuel", [s])
    t.script(-24, [("21:10", s, "yo u see the game 😤"),
                   ("21:30", me, "Missed it, Zoe bedtime marathon"),
                   ("21:31", s, "dad life lol")])
    t.script(-16, [("14:00", s, "mom's printer broke again. i'm handling it. just fyi so she doesn't call u about it 💀"),
                   ("14:10", me, "Appreciate you")])
    t.script(-9, [("19:00", s, "mom bday is {{date:+5:short}}, a {{dow:+5}}. what we doing"),
                  ("19:25", me, "I'll send flowers. You do the dinner?"),
                  ("19:26", s, "bet. taking her to that fish place she likes 🐟"),
                  ("19:27", s, "u call in during dinner? like 7 her time"),
                  ("19:40", me, "Yes, 4pm my time works")])
    t.script(-5, [("17:30", s, "told mom ur going to denver. she's already worried abt ur coat lmao"),
                  ("17:35", me, "She texted me. Thanks a lot 🙄"),
                  ("17:36", s, "😂😂")])
    t.script(-2, [("12:00", s, "did u order the flowers yet. she said 'no fuss' which means fuss", _id("msg-samuel-flowers")),
                  ("12:45", me, "Ordering this week. Saturday latest"),
                  ("12:46", s, "saturday latest lol ok mr calendar")])
    t.script(-1, [("21:15", s, "also cousin tunde getting married in april. invite coming. heads up", _id("msg-samuel-tunde")),
                  ("21:16", s, "mom will want us all there 🙃"),
                  ("22:00", me, "Noted. April is far away"),
                  ("22:01", s, "it's not, she's already planning outfits")])
    t.script(0, [("07:15", s, "u up? saw u liked a meme at 1am lmao on call?", U)])


def _priya(b: ProfileBuilder) -> None:
    p, me = "ct-priya", "me"
    t = b.thread("priya", [p])
    t.script(-13, [("10:05", p, "hey are you in office today? want to whiteboard the idempotency stuff"),
                   ("10:10", me, "Yes, after 2"),
                   ("10:11", p, "👍")])
    t.script(-6, [("17:50", p, "fyi i'm out mon morning, dentist. back by 11"),
                  ("18:02", me, "No problem")])
    t.script(-1, [("16:50", p, "can you look at #4468 before ga? the idempotency key backfill", _id("msg-priya-4468-ask")),
                  ("16:51", p, "omar approved but wants you on it since it touches ledger-writer"),
                  ("17:08", me, "Yes, tonight or first thing tomorrow", _id("msg-priya-4468-yes")),
                  ("17:09", p, "ty 🙏")])
    t.script(0, [("01:05", p, "saw the page in the channel. need anything? i'm up w the baby anyway"),
                 ("01:09", me, "All good, Rachel's on. Go sleep"),
                 ("01:10", p, "ok. ping if needed"),
                 ("07:25", p, "morning. 4468 still needs your review btw, no rush before signoff",
                  {"unread": True, "id": "msg-priya-4468-nudge"})])


def _rachel(b: ProfileBuilder) -> None:
    r, me = "ct-rachel", "me"
    t = b.thread("rachel", [r])
    t.script(-20, [("15:00", r, "u primary week of oct 12 right? i'm your secondary"),
                   ("15:05", me, "Yep. Thanks for being my backup"),
                   ("15:05", r, "🫡")])
    t.script(-2, [("09:05", r, "on call starts. the disk alert on metrics-scraper-03 flaps, ignore it, ticket's open w infra",
                   _id("msg-rachel-flap")),
                  ("09:07", me, "Good to know, thanks")])
    t.script(0, [("00:29", r, "saw the p2 roll in, you on it?"),
                 ("00:30", me, "Yes. Export lag, and ledger-writer is throwing 5xx now too"),
                 ("00:32", r, "jumping on"),
                 ("00:48", r, "brightwater's midnight batch looks huge"),
                 ("00:51", me, "Retry storm. Pausing the consumer"),
                 ("01:12", r, "lag dropping"),
                 ("01:20", me, "Error rate back under 1%"),
                 ("01:42", r, "both auto-resolved. dlq is big tho"),
                 ("01:45", me, "Not replaying at 2am. I'll approve the replay first thing in the morning after I check idempotency on those",
                  _id("msg-rachel-replay-promise")),
                 ("01:46", r, "agreed. i'll write the handoff email"),
                 ("01:47", r, "root cause still tbd, could be the batch could be the retry config", _id("msg-rachel-rootcause")),
                 ("01:50", me, "Yep. Going to bed. Thanks Rachel"),
                 ("01:51", r, "night"),
                 ("07:05", r, "sent the handoff. replay is blocked on your approval in klaxon. also put the incident review on cal fri 2pm",
                  {"unread": True, "id": "msg-rachel-handoff"})])


def _little_ferns(b: ProfileBuilder) -> None:
    lf, me = "ct-little-ferns-text", "me"
    t = b.thread("little-ferns", [lf], service="sms")
    t.msg(-23, "07:30", lf, "Little Ferns: Welcome to our family text line! Replies go to the front desk. Reply STOP to opt out.")
    t.msg(-20, "14:10", lf, f"Little Ferns: Reminder: Fall family potluck {D(-20, 'short')} at 5:30pm in the Maple room. Bring a dish to share!")
    t.msg(-12, "09:00", lf, "Little Ferns: Sunflower room is low on pull-ups. Please check your child's cubby.")
    t.msg(-12, "09:30", me, "Will bring a pack Monday, thanks")
    t.msg(-8, "16:30", lf, "Little Ferns: Head lice notice for the Acorn room. Sunflower room is not affected.")
    t.msg(-5, "11:00", lf, f"Little Ferns: Picture day is {W(1)} {D(1, 'short')}! Sunflower room photos at 9:15.")
    t.msg(-2, "15:00", lf, f"Little Ferns: Early close {W(2)} {D(2, 'short')} at 3:00pm for staff training. "
                           "Pumpkin patch permission slips are due the same day.", id="msg-little-ferns-early-close")
    t.msg(-1, "17:40", lf, "Little Ferns: Zoe had a great day! She painted a pumpkin and ate all her lunch. – Ms. Rosa")
    t.msg(-1, "17:52", me, "Thank you Ms. Rosa!")
    t.msg(0, "07:05", lf, "Little Ferns: Reminder: early close this Friday at 3pm. A late pickup fee applies after close.",
          unread=True)


def _striders(b: ProfileBuilder) -> None:
    m, a, j, me = "ct-mel", "ct-aaron", "ct-jess", "me"
    t = b.thread("striders", [m, a, j], title="Greenlake Striders 🏃")
    t.script(-25, [("06:40", m, "Sat long run: 10 mi, meet at the boathouse 8:00. Coffee after!"),
                   ("06:45", j, "in!"),
                   ("07:10", me, "In, bringing the jogging stroller for the first 3"),
                   ("10:15", a, "my watch says 8:42 pace, i say lies")])
    t.script(-18, [("06:30", m, "Today: 12 mi. Boathouse 8:00. Rain gear 🌧"),
                   ("06:35", a, "rain run club 💪"),
                   ("06:50", me, "Skipping, Zoe has a fever. Next week"),
                   ("06:52", m, "Feel better Zoe!!")])
    t.script(-15, [("18:00", m, "Tue tempo tonight 6:30, track by the community center"),
                   ("18:20", j, "legs are dead from sat but coming")])
    t.script(-11, [("06:20", m, "Sat: 8 easy + strides. 8:00 boathouse"),
                   ("06:40", me, "In"),
                   ("06:41", a, "in"),
                   ("11:00", j, "who left a blue water bottle at the boathouse"),
                   ("11:05", a, "not me"),
                   ("11:20", me, "Mine! Can you hold it till next week?"),
                   ("11:21", j, "it's in my car, it lives with me now")])
    t.script(-8, [("18:10", m, "Tempo tonight! 6:30"),
                  ("18:30", me, "Can't, bedtime duty")])
    t.script(-5, [("20:00", a, "big news: signed up for the november marathon 😬"),
                  ("20:02", m, "AARON!!! 🎉"),
                  ("20:03", j, "training buddy 🙋‍♀️")])
    t.script(-4, [("06:15", m, "Sat: 14 mi for marathon folks, 8 for everyone else. 8:00"),
                  ("06:30", me, "8 for me"),
                  ("10:50", m, "Great run all. Next Sat after the run: Halloween costume fun run planning, aka coffee")])
    t.script(-2, [("19:00", m, "Tue tempo tomorrow 6:30! Also club shirt orders close Fri"),
                  ("19:05", me, "I'm on call this week so Tuesdays are out. Saturday I'll be there with my phone"),
                  ("19:06", m, "Pager-pace run 😂")])
    t.script(-1, [("19:40", j, "tempo was brutal. 6x800"),
                  ("19:45", a, "my lungs are on the track somewhere"),
                  ("21:00", m, "Shirt order: tell me your size + $22 to me. Due Fri!")])
    t.script(0, [("06:45", j, "anyone want to carpool sat? i still have your water bottle marcus 😂", U)])


def _kofi(b: ProfileBuilder) -> None:
    k, me = "ct-kofi", "me"
    t = b.thread("kofi", [k])
    t.script(-21, [("20:00", k, "bro thank you for saying yes to groomsman. means a lot"),
                   ("20:10", me, "Of course. Honored"),
                   ("20:11", k, "suits are navy. amara picked them")])
    t.script(-12, [("19:30", k, "what is jamal planning. be honest"),
                   ("19:35", me, "My lips are sealed"),
                   ("19:36", k, "that's what scares me")])
    t.script(-3, [("16:00", k, "tailor for groomsmen is anytime before the wedding, they'll text u"),
                  ("16:10", me, "Ok. Send me their info?"),
                  ("16:12", k, "will do")])
    t.script(-1, [("20:15", k, "everyone is being weird. what's going on"),
                  ("20:20", me, "Nothing is going on 😇"),
                  ("20:21", k, "the halo makes it worse")])


def _sam(b: ProfileBuilder) -> None:
    s, me = "ct-sam-kessler", "me"
    t = b.thread("sam", [s])
    t.script(-13, [("11:00", s, "got a sec for the ga comms draft? in your inbox"),
                   ("11:20", me, "Will look after lunch")])
    t.script(-6, [("15:35", s, "sent the ga plan email. thursday 10am. shout if that's bad for eng"),
                  ("15:50", me, "10 is fine for us")])
    t.script(-2, [("10:16", s, "moved ga to 1pm. brightwater asked + support coverage is better then. email out",
                   _id("msg-sam-ga-moved")),
                  ("10:20", me, "👍 better for us anyway, gives us the morning for final checks"),
                  ("10:21", s, "🙏")])
    t.script(-1, [("18:00", s, "go/no-go is wed 1pm, invite sent. sign-off by noon still"),
                  ("18:05", me, "Yep")])
    t.script(0, [("07:20", s, "morning. heard about last night 😬 does the incident change anything for ga?",
                  {"unread": True, "id": "msg-sam-incident"}),
                 ("07:21", s, "also dana asked me to remind you: sign-off by noon, in the form",
                  {"unread": True, "id": "msg-sam-signoff-reminder"})])


def _gwen(b: ProfileBuilder) -> None:
    g, me = "ct-gwen", "me"
    t = b.thread("gwen", [g])
    t.script(-3, [("11:20", me, "Hi Gwen! Any chance you could do daycare pickup {{dow:+8}} {{date:+8:short}}? I'm in Denver and Hana's on shift"),
                  ("12:40", g, "Of course! Same place, by 5:30?"),
                  ("12:45", me, "Yes, Little Ferns. I'll add you to the pickup list"),
                  ("12:46", g, "Perfect. Zoe and Juniper can have a playdate after")])
    t.script(-1, [("17:00", g, "Also you have a package on my porch, delivered to the wrong house again 🙃"),
                  ("17:30", me, "Ha, thanks! Grabbing it tonight"),
                  ("19:15", g, "Left it by your door")])


def _puget(b: ProfileBuilder) -> None:
    p, me = "ct-puget-pipe", "me"
    t = b.thread("puget", [p], service="sms")
    t.msg(-13, "12:55", p, f"Puget Pipe & Drain: Your appointment is booked for {W(2)} {D(2, 'short')}, 8:00–10:00 AM. "
                           "Your tech will call 30 min ahead. Reply C to cancel.", id="msg-puget-booked")
    t.msg(-6, "09:00", p, f"Reminder: Puget Pipe & Drain visit {W(2)} {D(2, 'short')}, 8–10 AM. Please clear the area under the sink.")
    t.msg(-1, "15:40", p, "Hi Marcus, this is Rick at Puget Pipe & Drain. Our morning tech is out Friday. "
                          "Can we move you to 1:00–3:00 PM the same day? Reply YES to confirm.", id="msg-puget-move")
    t.msg(-1, "16:05", me, "YES", id="msg-puget-yes")
    t.msg(-1, "16:06", p, f"Confirmed: {W(2)} {D(2, 'short')}, 1:00–3:00 PM. Thanks for your flexibility!",
          id="msg-puget-confirmed")


def _extras(b: ProfileBuilder) -> None:
    """Later additions to existing threads (ids continue each thread's numbering)."""
    th = {row["id"]: Thread(b, row) for row in b.threads}
    h, me = "ct-hana", "me"
    t = th["th-hana"]
    t.script(-10, [("09:30", h, "zoe wants pancakes shaped like dinosaurs. ur department"),
                   ("09:31", me, "On it 🦕")])
    t.script(-6, [("07:15", h, "reminder i'm on nights fri + sat this week"),
                  ("07:20", me, "Ok, I've got Zoe both evenings")])
    t.script(-2, [("15:10", h, "did u see the daycare email? early close fri"),
                  ("15:14", me, "Saw it. You're home Friday right?"),
                  ("15:15", h, "yep till 7, i'll grab her at 3")])
    t.script(-1, [("07:50", me, "Drop-off done. She cried for 4 seconds then ran to the sand table"),
                  ("09:40", h, "😭 she's so dramatic. like u")])
    t = th["th-bach"]
    t.script(-2, [("21:20", "ct-tom", "marcus is typing... (he's not)"),
                  ("22:40", me, "Sorry, on-call brain. Maybe? I'm secondary on-call that week, let me see if I can swap")])
    t = th["th-striders"]
    t.msg(-11, "06:10", "ct-jess", "is it raining or is it just my window")
    t.msg(-4, "07:50", "ct-aaron", "running 5 late, start without me")
    t.msg(-1, "21:05", me, "M please. Will pay Saturday")
    t.msg(-1, "21:06", "ct-mel", "👍")
    th["th-gwen"].msg(-1, "19:20", me, "Got it, thank you!")
    th["th-kofi"].msg(-12, "19:40", "ct-kofi", "fine. just nothing that ends up online pls")
    th["th-samuel"].msg(-16, "14:11", "ct-samuel", "ofc. u owe me a printer tho 😂")
    th["th-sam"].msg(-1, "18:06", "ct-sam-kessler", "also status page draft is in the launch doc if u want to look")
    th["th-priya"].msg(-6, "18:03", "ct-priya", "ty!")
    th["th-priya"].msg(-6, "18:10", "ct-priya", "random but omar told me he's still L5 after the last cycle and he's "
                       "pretty down about it. pls keep that between us", id="msg-priya-omar-level")
    th["th-priya"].msg(-6, "18:14", me, "Of course. Thanks for telling me")
    th["th-samuel"].msg(-2, "12:50", "ct-samuel", "ok real talk. DON'T tell mom: auntie funmi is flying in from houston "
                        "and all the cousins are coming to the fish place monday. it's a surprise 🤫",
                        id="msg-samuel-surprise")
    th["th-samuel"].msg(-2, "12:52", me, "Love it. Not a word")
