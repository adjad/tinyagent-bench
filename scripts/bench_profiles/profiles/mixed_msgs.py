"""mixed profile: iMessage/SMS threads (all invented, all in English).

Lima is one hour behind Toronto in October; stored times are Toronto wall-clock, so the
family's "7pm our time" is 20:00 for Camila.
"""
from __future__ import annotations

from scripts.bench_profiles.lib import ProfileBuilder, tok as T

ME = "me"


def add(b: ProfileBuilder) -> None:
    _family(b)
    _mom(b)
    _dad(b)
    _house(b)
    _cohort(b)
    _imani(b)
    _jonah(b)
    _ale(b)
    _dani_cousin(b)
    _sofia(b)
    _lab(b)
    _dani_o(b)
    _furnace_sms(b)
    _noor(b)


def _family(b: ProfileBuilder) -> None:
    mom, dad, ab, rosa, beto, dani = ("ct-mama", "ct-papa", "ct-abuela", "ct-tia-rosa", "ct-tio-beto", "ct-dani-prima")
    t = b.thread("familia", [mom, dad, ab, rosa, beto, dani], title="Rivas family 💛")
    t.script(-26, [
        ("08:10", mom, "Good morning family ☀️"),
        ("08:25", ab, "Good morning my loves. God bless you all"),
        ("09:02", beto, "Morning! Who's coming to the parish chicken lunch on Sunday? I already bought 20 tickets, help me sell them 😂"),
        ("09:10", dani, "me!!! save me a plate 🍗"),
        ("09:31", rosa, "We'll go with Mateo. Beto, put us down for 4"),
        ("10:45", ME, "so jealous 😭 it's already cold here, 8 degrees this morning"),
        ("10:52", mom, "Wear a scarf hija. And the hat. Your ears get cold"),
        ("10:53", ME, "yes mom 🙄❤️"),
    ])
    t.script(-19, [
        ("13:20", dani, "FAMILYYY I PASSED STATISTICS!!! 🎉"),
        ("13:24", ab, "Congratulations my Dani, I always knew it"),
        ("13:30", dad, "That's how it's done."),
        ("13:41", ME, "yesss primaaa 👏👏 told you the practice exams would work"),
        ("13:45", beto, "My daughter the genius. Gets it from her dad obviously"),
        ("13:46", dani, "papá I studied for 3 weeks, you watched football jajaja"),
        ("13:47", rosa, "Jajaja"),
    ])
    t.script(-14, [
        ("19:05", mom, "Family, on October 15 Abuela turns 80!! I'm making a video with old photos. Send me your photos here or by email"),
        ("19:12", rosa, "I have the ones from Tía Chela's wedding, Abuela is dancing in all of them"),
        ("19:30", beto, "I have one of her with the giant cake from the 70th, I'll look for it"),
        ("20:02", ME, "I'll send the ones from the summer in Paracas! the one where she won't get in the water"),
        ("20:05", mom, "Yes!! Send them by Wednesday the 14th please, I have to edit everything", {"id": "fam-photos-ask"}),
        ("20:06", ME, "ok, by wednesday 👍"),
        ("20:30", ab, "What are you all whispering about? 🤔"),
        ("20:31", dani, "nothing abuelita!! 🤐"),
    ])
    t.script(-10, [
        ("12:15", beto, "Where do we do the birthday lunch? I vote Rosa's house, she has the big table"),
        ("12:40", rosa, "Fine, at my house on Saturday the 17th at noon. Everyone brings something"),
        ("12:44", mom, "And on Thursday the 15th, the actual day, we'll do a video call so Camila can be there too 💻"),
        ("13:10", ME, "yes!! what time?"),
        ("13:15", mom, "7pm our time, after dinner", {"id": "fam-time-first"}),
        ("13:18", ME, "ok let me check my schedule, thursdays are long"),
        ("15:20", dad, "I'll bring the pisco."),
        ("15:22", beto, "And I'll bring my karaoke voice"),
        ("15:23", dani, "nooo please tío"),
    ])
    t.script(-5, [
        ("09:40", ab, "Camilita did you eat? It's cold there, make yourself a soup"),
        ("11:05", ME, "yes abuelita! I made chicken soup yesterday, with the potatoes you like 🍲"),
        ("11:07", ab, "That's my girl"),
    ])
    t.script(-4, [
        ("21:30", ME, "we just had Thanksgiving dinner with my housemates (the holiday is tomorrow) 🦃 I made causa and they loved it"),
        ("21:41", beto, "Turkey in October?? These gringos 😂"),
        ("21:43", dani, "tío they're Canadian not gringos jajaja"),
        ("21:44", beto, "Same thing"),
        ("21:52", mom, "So proud you made causa! Send a photo"),
        ("21:58", ME, "(photo of the causa, a bit lopsided)"),
    ])
    t.script(-3, [
        ("08:01", rosa, "It looks beautiful hija"),
        ("08:20", ab, "Next time put more lime"),
    ])
    t.script(-1, [
        ("13:10", mom, "Family, reminder: tomorrow is Abuela's birthday 🎂 video call at 7pm Lima time"),
        ("13:40", ME, "mom, can we do 8pm your time instead? my seminar goes until 7:50 your time 🙈"),
        ("14:05", beto, "8 is better for me too, I get out of work late"),
        ("14:20", rosa, "8 is fine for us"),
        ("14:31", mom, "Ok, 8pm Lima time then. Camila that's 9pm for you, right?"),
        ("14:45", ME, "yes, 9 for me. perfect ❤️", {"id": "fam-time-final"}),
        ("15:02", dani, "I'll join from the university if I get out early!!"),
        ("20:15", ME, "mom, here are the Paracas photos for the video 👇 there are 12", {"id": "fam-photos-sent"}),
        ("20:16", ME, "(12 photos)"),
        ("20:30", mom, "Thank you hija!! They're beautiful 😍 the one with the hat 😂"),
        ("21:05", ab, "Look at all my grandchildren. Thank you for remembering this old lady"),
        ("21:07", beto, "Not old, a young lady of 80 😂"),
    ])
    t.script(0, [
        ("07:00", mom, "Happy birthday to the best mom in the world! 🎂❤️ 80 years today", {"unread": True}),
        ("07:05", dad, "Happy birthday, suegrita!", {"unread": True}),
        ("07:11", ab, "Thank you my loves, God bless you all", {"unread": True}),
        ("07:20", rosa, "Happy birthday mamita!! We'll see you Saturday with everyone", {"unread": True}),
        ("07:33", mom, "Don't forget: video call tonight 8pm Lima time ⏰ I'll post the link here at 7:30 our time",
         {"unread": True, "id": "fam-link-plan"}),
        ("07:38", dani, "happyyy birthday abuelitaaa!!! 🎂", {"unread": True}),
        ("07:44", beto, "Happy birthday mamá. 8 sharp, nobody be late, Camila included 😜", {"unread": True}),
    ])


def _mom(b: ProfileBuilder) -> None:
    mom = "ct-mama"
    t = b.thread("mama", [mom])
    t.script(-27, [
        ("19:30", mom, "Hija"),
        ("19:30", mom, "How was the first full week at the job"),
        ("19:31", mom, "Are they treating you well"),
        ("20:40", ME, "it's good! my manager is really nice and I'm running a study with real participants"),
        ("20:42", mom, "My daughter the researcher 🙏"),
    ])
    t.script(-20, [
        ("08:05", mom, "Did you see the thing about the study permit"),
        ("08:05", mom, "Don't forget that, it's important"),
        ("12:40", ME, "yes mom, the school sends emails about it. I have time"),
        ("12:41", mom, "Check the date. Don't leave it to the end like the passport"),
        ("12:41", ME, "that was ONE time"),
    ])
    t.script(-16, [
        ("20:20", mom, "Your dad wants to send you money for the ticket in December"),
        ("20:22", ME, "mom no, I'm saving, I have a budget and everything"),
        ("20:22", mom, "Let him. It makes him happy"),
        ("20:25", ME, "ok but only a little 😅"),
    ])
    t.script(-11, [
        ("10:15", mom, "Can you talk on Sunday?"),
        ("10:48", ME, "yes! after 12 my time, I have a call with Ale first"),
        ("10:49", mom, "Ok 1pm yours"),
    ])
    t.script(-8, [
        ("18:45", mom, "Hija what do you want to give Abuela"),
        ("18:46", mom, "I can buy something here and say it's from you"),
        ("19:30", ME, "flowers? the yellow ones she likes. I'll send you money"),
        ("19:31", mom, "Ok yellow roses. Don't send money"),
        ("19:31", ME, "I'm sending it 😤"),
        ("19:45", mom, "Stubborn like your father"),
    ])
    t.script(-6, [
        ("07:50", mom, "It says 6 degrees in Toronto"),
        ("07:50", mom, "Wear the big coat"),
        ("08:30", ME, "I don't have a big coat yet, I'm waiting for a sale"),
        ("08:31", mom, "Hija!!!"),
    ])
    t.script(-4, [
        ("11:20", mom, "Happy Thanksgiving to your roommates"),
        ("11:21", mom, "Are you cooking tonight"),
        ("11:50", ME, "causa! wish me luck"),
        ("11:51", mom, "Lots of lime. And don't put too much oil in the potato"),
    ])
    t.script(-2, [
        ("20:20", mom, "Your tía sent you a package"),
        ("20:20", mom, "With something special inside 🤫"),
        ("20:21", mom, "She says it arrives in a week more or less"),
        ("20:40", ME, "omg what is it"),
        ("20:41", mom, "I can't say. Read her email"),
        ("20:52", ME, "she wrote me a whole letter 😂 ok I'll read it tomorrow"),
    ])
    t.script(-1, [
        ("08:10", mom, "Did you send the photos"),
        ("08:40", ME, "tonight, promise"),
        ("20:40", mom, "Got them hija thank you ❤️"),
        ("20:41", mom, "Are you going to be home tomorrow for the call or at the university"),
        ("20:50", ME, "home! the seminar is online this week"),
        ("20:52", mom, "Good, the internet is better at home"),
        ("21:44", mom, "Sleep early hija, tomorrow is a big day", {"id": "mama-late"}),
    ])
    t.script(0, [
        ("07:15", mom, "Good morning hija", {"unread": True}),
        ("07:15", mom, "Send Abuela a voice message this morning if you can, she's up since 6", {"unread": True}),
        ("07:16", mom, "And it says 4 degrees there and rain later. Umbrella", {"unread": True}),
    ])


def _dad(b: ProfileBuilder) -> None:
    dad = "ct-papa"
    t = b.thread("papa", [dad])
    t.script(-17, [
        ("19:00", dad, "Hi daughter. How is everything?"),
        ("21:30", ME, "hi papi! busy but good. how's your knee?"),
        ("21:35", dad, "Better. The doctor says less football. I say no."),
    ])
    t.script(-10, [
        ("08:20", dad, "I sent you something for the ticket. Don't tell your mother how much."),
        ("09:05", ME, "papi!! thank you ❤️ you didn't have to"),
        ("09:06", dad, "I know. Buy the ticket early, prices go up."),
    ])
    t.script(-1, [
        ("19:10", dad, "Tomorrow your abuela turns 80. Call early, she sleeps at 10."),
        ("21:50", ME, "it's at 8 your time now, she'll still be awake right?"),
        ("21:52", dad, "For you she stays awake."),
        ("21:52", dad, "Good night daughter."),
    ])


def _house(b: ProfileBuilder) -> None:
    noor, theo, lukas = "ct-noor", "ct-theo", "ct-lukas"
    t = b.thread("casa", [noor, theo, lukas], title="Brunswick crew 🏠")
    t.script(-25, [
        ("20:10", theo, "Reminder that garbage + green bin go out Tuesday night. Recycling is NOT this week. The calendar is on the fridge."),
        ("20:14", noor, "the calendar on the fridge has been wrong twice"),
        ("20:15", theo, "The city's calendar has been wrong twice. Mine is perfect."),
        ("20:20", lukas, "k"),
        ("20:31", ME, "who ate my yogurt with my name on it"),
        ("20:32", noor, "not me"),
        ("20:40", lukas, "sorry. will replace"),
    ])
    t.script(-18, [
        ("08:02", noor, "there's a mouse in the pantry. i saw it. it saw me. we both screamed"),
        ("08:10", theo, "I'll tell Gail. Nobody leave food out please."),
        ("08:15", ME, "noooo 😭 my rice"),
        ("19:30", theo, "Gail is sending someone Thursday to seal the gap behind the stove."),
        ("19:33", lukas, "👍"),
    ])
    t.script(-12, [
        ("21:00", theo, "Okay house, Gail confirmed the rent goes to $3,230 total from November (guideline increase). I think we should split by room size this time instead of evenly-ish. I'll do the maths."),
        ("21:05", noor, "fair. my room is a closet"),
        ("21:06", lukas, "attic has a slanted ceiling, counts as half a room"),
        ("21:10", ME, "ok but my room has the bay window so I know where this is going 😅"),
    ])
    t.script(-9, [
        ("21:30", theo, "First proposal (room size, square metres, rounded):\n- Camila 805\n- Noor 790\n- Theo 805\n- Lukas 830\nTotal 3,230. Thoughts?", {"id": "casa-rent-proposal"}),
        ("21:34", lukas, "830 for the attic is wild"),
        ("21:35", lukas, "half the room i can't stand up in"),
        ("21:40", noor, "he's not wrong"),
        ("21:44", theo, "Fair point. I'll redo it with usable floor space."),
        ("21:48", ME, "agree with lukas tbh"),
    ])
    t.script(-6, [
        ("17:10", theo, "Thanksgiving plan: Sunday 6pm here. I'm doing a turkey breast (we learned last year). Everyone bring one thing."),
        ("17:12", noor, "stuffing + my mom's fattoush"),
        ("17:20", ME, "papa a la huancaína and causa!!"),
        ("17:25", lukas, "pie"),
        ("17:26", theo, "Lukas, what kind of pie"),
        ("17:40", lukas, "bakery pie"),
    ])
    t.script(-5, [
        ("11:15", theo, "Final numbers with usable floor space, from Nov 1:\n- Camila 815\n- Noor 795\n- Theo 820\n- Lukas 800\nTotal 3,230. I'll send these to Gail if everyone agrees.", {"id": "casa-rent-final"}),
        ("11:20", lukas, "👍 works"),
        ("11:31", noor, "yep"),
        ("12:02", ME, "ok fine, 815 for the bay window room is fair 😅 agreed"),
        ("12:05", theo, "Sending to Gail. Thanks all, that was painless(ish)."),
    ])
    t.script(-4, [
        ("21:15", noor, "best thanksgiving ever. cami the causa 😭😭"),
        ("21:17", ME, "the fattoush!!! noor your mom is a genius"),
        ("21:20", theo, "Dishes are a team sport tonight, not a chore rotation."),
        ("21:21", lukas, "on it"),
        ("23:10", noor, "who left the oven on"),
        ("23:11", lukas, "me. off now"),
    ])
    t.script(-3, [
        ("10:05", theo, "I sent everyone a breakdown of the Thanksgiving groceries. $52.75 each, whenever."),
        ("22:06", ME, "sent! 🦃"),
    ])
    t.script(-2, [
        ("18:30", theo, "Two house things:\n1. Hydro bill for Aug 14 - Oct 13 is $164.80, so $41.20 each. Please e-transfer me by Friday.\n2. Gail says the furnace inspection is Thursday. Northgate will text us the time window (probably evening). Someone has to be home to let the tech into the basement + the panel behind the laundry.", {"id": "casa-hydro"}),
        ("18:31", theo, "I'm at my sister's in Hamilton Wed-Fri, so it can't be me.", {"id": "casa-furnace-ask"}),
        ("18:40", noor, "i'm closing thursday. if it's late i could maybe get back by 8:30? not great"),
        ("19:06", theo, "Camila? 🙏"),
        ("20:40", ME, "thursday is my seminar night 😩 let me see what the window is"),
    ])
    t.script(-1, [
        ("09:12", lukas, "working thurs, sorry. saw this late"),
        ("20:45", ME, "ok good news: northgate says 6-8 and my seminar is online tomorrow, so I'll be home for the furnace guy 👍", {"id": "casa-furnace-me"}),
        ("20:47", theo, "Legend. Key to the panel is on the hook by the basement door."),
        ("20:58", noor, "cami ur a hero"),
        ("22:15", noor, "also we're out of oat milk if anyone's passing a store"),
    ])
    t.script(0, [
        ("06:55", theo, "Reminder for tonight: furnace tech 6-8pm. He needs the basement and the panel behind the laundry. Thank you Camila!", {"unread": True}),
        ("07:31", noor, "thank you cami!! i owe you one", {"unread": True}),
    ])


def _cohort(b: ProfileBuilder) -> None:
    dani, marcus, hana, wren = "ct-dani-o", "ct-marcus", "ct-hana", "ct-wren"
    t = b.thread("cohort", [dani, marcus, hana, wren], title="HCI '27 ☕📚")
    t.script(-27, [
        ("22:10", wren, "who else is already drowning in the week 3 readings"),
        ("22:11", dani, "me 😭😭📚📚"),
        ("22:14", marcus, "skimmed"),
        ("22:20", hana, "I read them all. I'm not proud. I even started the Ortega ethnography."),
        ("22:25", ME, "ortega is week 6 hana 😂"),
        ("22:26", hana, "Oh no."),
    ])
    t.script(-21, [
        ("17:30", dani, "anyone want to grab food before seminar?? 🍜🍜"),
        ("17:31", ME, "can't, coming straight from parcelwise 🏃‍♀️"),
        ("17:33", wren, "i'm in"),
        ("20:55", marcus, "that discussion was 40 min about one footnote"),
        ("20:56", wren, "a great footnote though"),
    ])
    t.script(-17, [
        ("13:00", hana, "Methods pairs for A2 - anyone want to pair? I'm thinking library or transit observation."),
        ("13:20", ME, "me!! I was thinking the library self-checkout"),
        ("13:22", hana, "Perfect. Let's talk after A1 is in."),
    ])
    t.script(-13, [
        ("21:00", wren, "ok marcus and I signed up to lead week 6 (care + maintenance). we're going to make everyone argue about who fixes things"),
        ("21:02", ME, "my thesis is literally about this, I'm going to be insufferable"),
        ("21:03", marcus, "good. bring data"),
    ])
    t.script(-10, [
        ("09:40", dani, "i'm sick 🤒🤧 not coming to methods today, someone save me notes pls 🙏🙏"),
        ("09:58", ME, "feel better!! I'll take good notes"),
        ("11:15", hana, "Get well soon Dani. Marsh is doing field notes today, it's important for A2."),
    ])
    t.script(-7, [
        ("18:10", marcus, "okafor is 10 min late, room's open"),
        ("18:14", wren, "he brought a projector from 1987"),
        ("20:58", hana, "Good session. Camila your comment about the courier rating screen was the best part."),
        ("21:00", ME, "🥹 thank you"),
    ])
    t.script(-4, [
        ("16:00", wren, "happy thanksgiving nerds 🦃"),
        ("16:05", dani, "happy thanksgiving!!! 🥧🍂🧡🧡"),
        ("16:30", hana, "Happy Thanksgiving! Halloween party at mine on the 31st, costumes mandatory."),
        ("16:31", marcus, "define mandatory"),
        ("16:31", hana, "Mandatory."),
        ("16:40", ME, "I'm coming as a consent form"),
        ("16:41", wren, "iconic"),
    ])
    t.script(-2, [
        ("20:10", dani, "who's leading tomorrow's... wait no it's thursday 🤦‍♀️"),
        ("20:11", wren, "it's me and marcus for week 6. we have a quiz. kidding"),
        ("22:00", hana, "Response 6 is due at noon Thursday, reminder for future-us."),
    ])
    t.script(-1, [
        ("20:30", wren, "ok who else just got the lectern notification - okafor's in montréal so tomorrow is ONLINE 🎉 pajama seminar", {"id": "cohort-online"}),
        ("20:33", hana, "Oh nice, that saves me the commute. Same link as on the course page?"),
        ("20:34", wren, "yep, under live sessions"),
        ("20:40", ME, "pajama seminar, I'm in"),
        ("20:45", dani, "pajama seminar 🥳🥳🛌🛌"),
        ("22:43", ME, "submitted response 6. it's 2 pages of me being mad about care work"),
        ("22:45", hana, "Good. Be mad."),
    ])
    t.script(0, [
        ("07:05", marcus, "news: got the summer offer from fernhill robotics. signing friday", {"id": "cohort-marcus-news", "unread": True}),
        ("07:10", dani, "MARCUS 🎉🎉🔥🔥🥳🥳", {"unread": True}),
        ("07:22", hana, "Congratulations, Marcus! That's huge.", {"unread": True}),
        ("07:24", wren, "marcus!!! does this mean you're finally buying a round", {"unread": True}),
    ])


def _imani(b: ProfileBuilder) -> None:
    im = "ct-imani"
    t = b.thread("imani", [im])
    t.script(-23, [
        ("10:15", im, "hiii welcome to the POD chaos 🙃 do u want the note-taking template or do u have one"),
        ("10:20", ME, "send yours! I'll adapt it"),
        ("10:21", im, "sent. columns are task / time / quote / severity"),
    ])
    t.script(-16, [
        ("14:02", im, "ok v2 screens are in the board. the tag button is now 48pt"),
        ("14:30", ME, "nice. P1 and P2 are next tuesday, they're both couriers"),
        ("14:31", im, "perfect, couriers are the real test"),
    ])
    t.script(-9, [
        ("11:05", im, "P1 was SO good. 'the photo is for you guys' 😭"),
        ("11:06", ME, "writing that on a sticky note forever"),
        ("15:10", im, "P2 in the hi-vis jacket demoing with gloves on... iconic"),
        ("15:12", ME, "bigger tap targets confirmed lol"),
        ("15:13", im, "already redrawing"),
    ])
    t.script(-6, [
        ("14:31", im, "shared the tap target file w u"),
        ("14:40", ME, "these are so much better"),
        ("14:41", im, "🧤🧤"),
    ])
    t.script(-2, [
        ("12:05", im, "P3 confirmed everything lol. tag before photo, 3 for 3"),
        ("12:06", ME, "we should flip the order in v3"),
        ("12:10", im, "agree. can u send me ur P3 notes tmrw? want to update the flow before thurs", {"id": "imani-p3-ask"}),
        ("12:12", ME, "yes! tomorrow afternoon"),
        ("16:40", im, "also team lunch friday!! priya's buying. i'll pick a place lol", {"id": "imani-lunch"}),
        ("16:41", ME, "somewhere with soup pls, it's cold"),
        ("16:45", im, "noted. elliot wants 'somewhere with veg options' so we'll see"),
    ])
    t.script(-1, [
        ("15:21", ME, "sent you the P3 notes! email + research drive", {"id": "imani-p3-done"}),
        ("15:38", im, "got em, ty!! 🙏"),
        ("15:39", im, "flipping the order tonight so P4 sees v3"),
        ("15:45", ME, "P4 is a recipient though, she won't see the courier flow"),
        ("15:46", im, "omg right. P5 then"),
        ("19:55", ME, "P5 just emailed me asking for his incentive by e-transfer and my home address?? very weird"),
        ("20:01", im, "wtf. forward it to priya"),
        ("20:05", ME, "doing it first thing tomorrow"),
        ("20:06", im, "good call"),
        ("20:20", im, "unrelated but fyi i'm out next wed morning for a physio appt, my back is a mess lol. pls don't mention it to priya, i just put 'ooo' on my calendar", {"id": "imani-physio"}),
        ("20:24", ME, "of course. hope it helps!"),
    ])
    t.script(0, [
        ("07:20", im, "morning!! can u print the consent forms for P4 + P5 before 11? the printer on 5 is jammed again so use the one by reception", {"id": "imani-print", "unread": True}),
        ("07:21", im, "also we're in kestrel today not heron, heron's screen recorder is busted", {"id": "imani-room", "unread": True}),
        ("07:21", im, "i booked kestrel for both sessions", {"unread": True}),
    ])


def _jonah(b: ProfileBuilder) -> None:
    j = "ct-jonah"
    t = b.thread("jonah", [j])
    t.script(-28, [
        ("23:10", j, "Home safe? The subway was a mess tonight."),
        ("23:25", ME, "home! took forever. thank you for dinner 🥟"),
        ("23:26", j, "Anytime. Sleep, researcher."),
    ])
    t.script(-24, [
        ("12:30", j, "Found the bakery you liked on Harbord. They have the custard tarts again."),
        ("12:45", ME, "save me one, I'm stuck at the office till 5:30"),
        ("12:46", j, "Already eaten. Kidding. Mostly."),
    ])
    t.script(-20, [
        ("19:00", j, "We got the Cellar for October 10! Low Tide Choir + us opening."),
        ("19:02", ME, "JONAH!!! I'm buying a ticket right now"),
        ("19:03", j, "You don't have to buy one, I can put you on the list"),
        ("19:05", ME, "I'm buying one. supporting the arts"),
    ])
    t.script(-15, [
        ("08:40", j, "Good luck with the lunch with Dani today 😄"),
        ("13:40", ME, "it was great, we planned all of week 9 facilitation already"),
        ("21:30", j, "Want to come over Friday? I'll make the mushroom thing."),
        ("21:40", ME, "yes please. after my dentist lol"),
    ])
    t.script(-12, [
        ("16:15", j, "How are you doing with everything? You sounded tired yesterday."),
        ("17:50", ME, "a little. ethics is waiting, parcelwise is busy, and abuela's birthday is coming and I can't be there"),
        ("17:52", j, "I'm sorry. Video call will help. And December isn't that far."),
        ("17:53", ME, "❤️"),
    ])
    t.script(-9, [
        ("22:35", j, "Ticket bought, I saw. You're the best."),
        ("22:36", ME, "front row, embarrassing sign, the works"),
    ])
    t.script(-5, [
        ("20:30", j, "Soundcheck done. Nervous."),
        ("20:31", ME, "you're going to be amazing. I'm on my way"),
        ("23:50", j, "Thank you for coming. And for the sign. And for screaming."),
        ("23:52", ME, "best set of the night, no bias"),
    ])
    t.script(-4, [
        ("13:00", j, "Happy Thanksgiving! My mom says hi and wants to know when you're coming to Guelph"),
        ("13:30", ME, "tell her soon!! after the ethics thing"),
    ])
    t.script(-3, [
        ("20:10", j, "Climbing Sunday? They're resetting the overhang Saturday."),
        ("20:12", ME, "YES. 11?"),
        ("20:13", j, "11. I'll bring the good chalk."),
    ])
    t.script(-2, [
        ("12:10", j, "Dumplings tonight? I owe you after Saturday."),
        ("12:15", ME, "yes!! 7?"),
        ("21:40", j, "Sent you my half, ignore the message."),
        ("21:45", ME, "'cheapskate tax' 😂"),
    ])
    t.script(-1, [
        ("18:20", j, "How was your day?"),
        ("21:20", ME, "long. harrow moved our meeting and I'm on furnace duty tomorrow lol"),
        ("21:22", j, "So you're home tomorrow night?"),
        ("21:23", ME, "home all evening. seminar till 8:50 then abuela's call at 9"),
        ("22:40", j, "Good luck with the furnace guy tomorrow 😂 Text me when the call's done? I'll call after."),
        ("22:42", ME, "it's not a party it's a video call lol. 9 to like 10, then I'm yours"),
        ("22:43", j, "Deal."),
    ])
    t.script(0, [
        ("07:30", j, "Morning! Don't forget your umbrella, it's supposed to pour after 4.", {"unread": True}),
        ("07:31", j, "And happy birthday to your abuela 🎂", {"unread": True}),
    ])


def _ale(b: ProfileBuilder) -> None:
    a = "ct-ale"
    t = b.thread("ale", [a])
    t.script(-25, [
        ("21:00", a, "friend!! how's the cold, have you turned into a popsicle yet"),
        ("21:30", ME, "not yet, it's only 10 degrees. ask me in january"),
        ("21:31", a, "I'm sweating in Miraflores and you're in a scarf, life is unfair"),
    ])
    t.script(-18, [
        ("22:15", a, "BIG NEWS. I got the job at the design studio in Barranco"),
        ("22:16", ME, "ALEEE!!! I knew it 🎉🎉"),
        ("22:17", a, "start in two weeks. I'm scared"),
        ("22:18", ME, "you'll be amazing. they're lucky"),
    ])
    t.script(-11, [
        ("11:50", a, "still on for our call at 12 your time?"),
        ("11:51", ME, "yes! give me 10 min"),
        ("13:05", a, "that was the best hour of my week. love you friend"),
        ("13:06", ME, "same ❤️"),
    ])
    t.script(-6, [
        ("20:40", a, "when do you land in December? I need to plan Barranco nights"),
        ("21:00", ME, "hoping the 18th. haven't bought the ticket yet 🙈"),
        ("21:01", a, "BUY IT"),
        ("21:01", a, "prices only go up, ask anyone"),
        ("21:05", ME, "my dad said the same thing"),
    ])
    t.script(-1, [
        ("13:12", a, "posted my first-week photos, look how professional I am"),
        ("13:40", ME, "the blazer!!! very serious designer"),
        ("13:41", a, "it's borrowed. don't tell anyone"),
        ("19:30", a, "tell your abuela happy birthday from me tomorrow. 80!!"),
        ("21:55", ME, "I will! she still asks about you"),
        ("21:56", a, "she taught me to make picarones, I owe her"),
    ])


def _dani_cousin(b: ProfileBuilder) -> None:
    d = "ct-dani-prima"
    t = b.thread("dprima", [d])
    t.script(-22, [
        ("19:20", d, "cousiiin how's canada!!"),
        ("19:40", ME, "busy! how's uni?"),
        ("19:41", d, "statistics is killing me jajaja"),
        ("19:43", ME, "send me your practice exams, I'll look at them"),
        ("19:44", d, "you're the besttt 💕"),
    ])
    t.script(-12, [
        ("21:30", d, "so is it true jonah is coming in december???"),
        ("21:35", ME, "who told you that"),
        ("21:35", d, "your mom jajaja"),
        ("21:36", ME, "it's NOT decided. maybe for new year's"),
        ("21:37", d, "the family is going to interrogate him jajaja"),
    ])
    t.script(-3, [
        ("17:10", d, "cousin can you bring me a hoodie from your university in december?? size M, pleaseee", {"id": "dprima-hoodie"}),
        ("17:25", ME, "yes! which colour"),
        ("17:26", d, "the dark green one!!! 💚"),
        ("17:27", ME, "deal"),
    ])
    t.script(-1, [
        ("15:05", d, "I'll try to join the call tomorrow but I have a lab until 7:30!!"),
        ("15:30", ME, "it's at 8 your time now, you'll make it!"),
        ("15:31", d, "yayyy")
    ])


def _sofia(b: ProfileBuilder) -> None:
    s = "ct-sofia"
    t = b.thread("sofia", [s])
    t.script(-30, [
        ("17:40", s, "hiii it's sofía from kickoff! the other latina intern 😄"),
        ("17:45", ME, "hiii!! so happy there's another one"),
        ("17:46", s, "we're starting a club. two members. very exclusive"),
    ])
    t.script(-23, [
        ("12:05", s, "lunch? the dumpling place on spadina"),
        ("12:06", ME, "omw"),
    ])
    t.script(-14, [
        ("16:20", s, "did u submit ur timesheet? paydeck is being weird"),
        ("16:35", ME, "yes, you have to click save AND submit, it's two buttons 🙃"),
        ("16:36", s, "omg. that explains why mine was still draft"),
    ])
    t.script(-7, [
        ("11:55", s, "saving u a seat at the lunch and learn"),
        ("11:57", ME, "coming! priya is presenting so I can't be late"),
        ("17:05", s, "all hands was so long. the depot slides 😴"),
        ("17:06", ME, "the POD demo was cool though!"),
        ("17:07", s, "ok yes, that part was cool. ur famous now"),
    ])
    t.script(-3, [
        ("21:41", s, "sent u my demo slides! the left turn thing"),
        ("22:00", ME, "couriers hating left turns is the most relatable data ever"),
    ])
    t.script(-1, [
        ("12:40", s, "reminder the timesheet is due THURSDAY this time not friday!!"),
        ("12:42", ME, "ugh thank you, I almost forgot"),
        ("12:43", s, "and enter friday's hours like u'll work them"),
        ("22:10", s, "u're coming tmrw right? dinner at sundial 🥟 I need my club member", {"id": "sofia-dinner", "unread": True}),
    ])


def _lab(b: ProfileBuilder) -> None:
    bea, kofi = "ct-bea", "ct-kofi"
    t = b.thread("lab", [bea, kofi], title="HIL lab 🧪")
    t.script(-20, [
        ("15:55", bea, "lab meeting snacks: I brought the good cookies, they're on the windowsill"),
        ("15:56", kofi, "the windowsill is now empty"),
        ("15:57", ME, "😂"),
    ])
    t.script(-9, [
        ("16:10", kofi, "camila thanks for signing up for the pilot! link coming tonight, it starts tomorrow"),
        ("16:20", ME, "ready!"),
        ("16:21", kofi, "5 min a day, I promise"),
    ])
    t.script(-7, [
        ("10:30", ME, "REB came back: minor revisions 😮‍💨"),
        ("10:32", bea, "That's a pass in REB language! Congrats"),
        ("10:33", kofi, "mine had 7 items last year, you're fine"),
        ("10:40", bea, "For the retention section just use the template paragraph from the REB site. They love their own words."),
        ("10:41", ME, "lifesaver"),
    ])
    t.script(-5, [
        ("15:00", kofi, "day 4 of the pilot. your entries are very detailed lol"),
        ("15:05", ME, "occupational hazard"),
    ])
    t.script(-2, [
        ("11:20", bea, "Imogen just emailed - no lab meeting this Friday, she's at the retreat. Writing time!", {"id": "lab-nolab"}),
        ("11:25", kofi, "so my pilot results move to the 23rd?"),
        ("11:26", bea, "Yep."),
        ("11:30", ME, "perfect, I'll use friday afternoon for the REB stuff"),
        ("11:31", bea, "Good plan. Ping me if you want a read of the consent form"),
    ])


def _dani_o(b: ProfileBuilder) -> None:
    d = "ct-dani-o"
    t = b.thread("dani-o", [d])
    t.script(-16, [
        ("09:15", d, "lunch tomorrow?? 🥗🥗 we need to plan week 9!!!"),
        ("09:30", ME, "yes! 12:30 at the calder café"),
        ("09:31", d, "🙌🙌🙌"),
    ])
    t.script(-15, [
        ("14:10", d, "that was so productive 🤓📋 i'll make the slides template"),
        ("14:12", ME, "I'll do the discussion questions"),
    ])
    t.script(-4, [
        ("19:05", d, "cami can i get ur notes from the oct 5 methods class?? 🥺🙏🙏 the one i missed", {"id": "dani-o-notes-ask"}),
        ("19:30", ME, "yes!! I'll send them before next monday's class", {"id": "dani-o-notes-me"}),
        ("19:31", d, "ur an angel 😇😇💕"),
    ])
    t.script(-1, [
        ("20:50", d, "pajama seminar tmrw!!! 🛌🛌"),
        ("20:51", ME, "best news of the week honestly"),
        ("20:52", d, "also no rush on the notes, monday is fine 🫶"),
    ])


def _furnace_sms(b: ProfileBuilder) -> None:
    num = "+1 (416) 555-0190"
    t = b.thread("sms-northgate", [], kind="dm", service="sms")
    t.msg(-1, "15:02", num, f"Northgate Heating: your furnace safety inspection is booked for Thu {T.date(0, 'short')}, "
          "arrival window 18:00-20:00. An adult must be present for basement access. Reply C to confirm, R to reschedule.")
    t.msg(-1, "21:35", ME, "C", id="sms-confirm")
    t.msg(-1, "21:35", num, "Thank you, your appointment is confirmed. The technician will text 30 min before arrival.")
    t.msg(0, "07:00", num, "Reminder: Northgate Heating visit today 18:00-20:00. Your technician will text 30 min before arrival.",
          unread=True, id="sms-reminder-d0")


def _noor(b: ProfileBuilder) -> None:
    n = "ct-noor"
    t = b.thread("noor", [n])
    t.script(-20, [
        ("10:40", n, "do u want the rest of the soup in the blue container. it's lentil"),
        ("10:52", ME, "yes please!! you're the best"),
    ])
    t.script(-3, [
        ("14:20", n, "can i tell u something but u can't tell the house yet"),
        ("14:21", ME, "of course"),
        ("14:23", n, "i have a final interview monday for the assistant manager job at the roastery on ossington. if i get it i'd probably move closer to it in january", {"id": "noor-interview"}),
        ("14:24", n, "pls don't say anything to theo or lukas. theo will start a spreadsheet about replacing me"),
        ("14:30", ME, "my lips are sealed. good luck, you're going to crush it"),
        ("14:31", n, "ty 🫶"),
    ])
    t.script(-1, [
        ("22:30", n, "ok my brain is fried. thanks for the pep talk earlier"),
        ("22:41", ME, "anytime. sleep!!"),
    ])
