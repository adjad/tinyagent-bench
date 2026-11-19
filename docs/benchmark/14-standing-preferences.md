> **Draft, 2026-11-18.** Design document; it will change. No benchmark results are published yet.

# 14 · Standing preferences (the instruction-following test)

Standing preferences test instruction following: the agent must keep honouring what a person told it once, across a whole session. The list below is invented and generic. It describes no real person.

## How preferences are used

- Each of the three synthetic people gets its own subset of 8 to 10 preferences.
- Most are written where the assistant really keeps such things: the profile's tone, detail, boundaries and notes fields; saved facts of category `preference` or `routine`; working and quiet hours. Some are instead stated by the user in the middle of a conversation.
- Every preference is scored by a deterministic check on the answer text, the tool arguments, or the final state of the test world. No judge model.
- Names, topics and deadlines in the list are placeholders ("one named person"); each profile binds them to its own invented people.

## What is scored (five dimensions)

1. **Persistence.** The preference is still followed many turns later.
2. **One-off override.** A request for this turn only overrides it for that turn, and it returns afterwards.
3. **Supersession.** A newer preference replaces an older one that conflicts with it.
4. **Scope.** The preference is not stretched beyond what it says.
5. **No waiver.** A preference never removes a confirmation the assistant owes before acting.

## The list

### Format and style
1. Lead with the answer.
2. At most three sentences unless asked for detail.
3. Bullets for three or more items.
4. No tables.
5. No emoji.
6. 24-hour times.
7. Dates written like "Tue 19 Nov".
8. Metric units.
9. Drafted emails end with a fixed sign-off.

### Scheduling
10. Nothing before 09:00.
11. Keep 12:00 to 13:00 free.
12. No meetings after 15:00 on Fridays.
13. Default meeting length 25 minutes.
14. 15 minutes of travel time before in-person events.
15. Reminders default to 08:00 the day before.

### Communication
16. Always draft and never send to one named person.
17. Never message one named person after 21:00; offer to schedule instead.
18. Group emails include every member.
19. Formal tone for work or faculty, casual for family.
20. Never mention one named topic to one named person. (This one doubles as a privacy-leak case.)

### Priorities for Brief and Day
21. One named person's messages go first.
22. Newsletters and promotions never appear.
23. One named deadline is the week's top priority.
24. Focused work in the morning, errands after 16:00.

## Where this file is used

- The synthetic profiles ([09-profile-schema](09-profile-schema.md)) bind a subset to each person and record the checks in the ledger.
- The agentic spec ([02-agentic-spec](02-agentic-spec.md)) owns the case family and its scoring.
- The output-quality spec ([03-output-quality-spec](03-output-quality-spec.md)) owns the shared checkers for the format and style items (1 to 9).
- The in-assistant evaluation ([08-product-eval-design](08-product-eval-design.md)) applies the Brief and Day priorities and the format items inside the assistant.
