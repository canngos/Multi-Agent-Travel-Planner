# After the fixes: second live run, 2026-11-20

> These runs used the system as it was on 2026-11-20, before the agent tree was simplified to the current 10 LLM agents (PLAN.md, Phase 13): the planner and reviser named here are now one `itinerary_writer`, and the transport and stay comparisons are code. The results on the current system are in [FINAL.md](FINAL.md).

The findings of [REPORT.md](REPORT.md) were fixed (PLAN.md, Phase 12), then 19 of the scenarios were run again live
(same harness, 4 at a time, while the demo was being re-recorded). Results are in `runs-after/`; the first runs stay in
`runs/`. Summary tables: `STRESS_RUNS=runs-after python stats.py` and `... summarize.py`.

The second run itself found more problems, which were fixed afterwards (listed at the end). Most of these runs
started before the last of those fixes, so a few numbers below are better on today's code than they were live.

## Scoreboard

| What | Before (21 full plans) | After (17 full plans) |
|---|---|---|
| Itinerary valid, right days, all links grounded | 21 / 21 | 17 / 17 |
| All 4 files written (.md, .html, .ics, .geojson) | 21 / 21 | 16 / 17 (03: harness turn timeout of 20 min) |
| Hostel on a mid/high budget | 5 | **0** |
| Filler items ("Leisure morning", "Free snack", "Taxi to accommodation", "Metro tip") | 7 | **0** |
| `[source](null)` links | 1 | **0** |
| Final plan still has a code must-fix | 3 | 5 live; **3** on today's code (03 timeout, 14 a place twice, 15 a café after closing) |
| Critic approved (no 3× REVISE) | 13 / 21 | 6 / 17 |
| Last day without an activity | 6 | 7 (early flights: a plan that cannot reach the airport now loses the item) |
| Average time per conversation | 818 s | 820 s |

The critic approves less often because the checks are stricter (airport buffer, rushed visits, interests, transport
value). The plans that end after 3 rounds now end on smaller points, and the guide lists what is left under
"Still to check".

## The findings, one by one

| Finding | Live result after the fix |
|---|---|
| C1 Kyiv flights land in Chișinău | 27: the Important box says the flight lands at RMO, 409 km away; the return is the bus from Kyiv, not a flight from Chișinău |
| C2 21-day Porto plan full of "Leisure morning" | 03: no filler; research asked for 30 sights and day trips (the run hit the harness timeout in review round 2) |
| C3 31 h bus for a wheelchair user | 07: flights both ways and Citadines Danube (apartment-hotel); the guide says accessibility was not verified |
| C4 Groups over 9 break every flight source | 06: Finnair both ways, priced as per-person fare × 14 with a group-desk note |
| C5 No same-day trips | 04, 04b: a day trip, ferry out 10:13 and back 19:11, no stay |
| C6 Overnight transport and paid nights | 09, 12: "book the stay from Sat 8 May for 1 night, not 2"; the cost counts the nights slept |
| C7 No airport buffer | no final plan of the second run has an item too late to reach the airport or station (`no_time_to_leave`; items that still do not fit after the last round are dropped) |
| H1 Early return kept although the alternative cost €5 more | 01: the evening return (19:20) is chosen; 07 is flagged on today's code |
| H2 Hostels on mid, a €367 Airbnb on a €50,000 trip | no hostel on mid/high; 14: Hôtel Hermitage Monte-Carlo |
| H3 Unpriced options disappear, counted as €0 | 09: the unknown car-ferry fare is listed as "not included". 15: the ferries now have a fare (€28), but one arrives the next morning and the other leaves after the trip, so the flights stay and the budget check says the €500 cannot be met |
| H4 Splurge restaurants on a low budget | 02: Hotel Holt is gone; Dill is flagged on today's code (no cheaper place was left, now replaced by a placeholder) |
| H5 Impossible budget not flagged | 02: the summary says food and local transport alone cost about €350 and asks whether to plan anyway |
| H6 Interests missed | 24: the Sagrada Família is in the plan (€26); 15: "cycling" is listed as not found |
| Guide writer chat leaks paths | every run: one message built in code, file names only |
| Finnish request got English | 24: Finnish throughout; 25b (English) got Finnish, fixed afterwards |
| Credit card number ignored | 20: the number is removed before the model sees it; the warning is now added in code |
| Off-topic answer | 21: says what the service does and invites a trip |
| Ambiguous date 03/04/2027 | 25b: asked whether it is 3 April or 4 March |
| Cheaper hotel returned the same one; wishes saved as requirements | 29: the wishes stay in the change note (requirements empty) |
| Tokyo as a province, Atlantis silently in South Africa | geocoder: Tokyo, Singapore and Monaco are cities; Atlantis needs confirming (tested live) |

## Found by the second run and fixed afterwards

- A long-haul arrival day (Tokyo, landing 20:55 on day 2) was treated as a full day: `empty_day` was wrong.
- Opening hours "Mon–Fri 07:00–16:00, Sat 08:00–16:00, Sun 09:00–15:00" were all read as Friday's.
- `far_airport` suggested a bus arriving two days later.
- Getting home at 00:40 counted as a whole lost day, so a day-keeping evening flight was not preferred.
- The Important box offered a "day-keeping" flight from Chișinău, and one that got home only the next day.
- The concierge answered an English message in Finnish (the prompt's example primed it).
- The card warning was left to the model, which skipped it.
- The guide writer's blanked reply made ADK log `MODEL_RETURNED_NO_CONTENT`.
- "A cheaper hotel" produced a dearer one (€68 → €96): now must-fix when a cheaper stay exists, otherwise the plan
  summary says so.
- The code's recommended return in 15 was a ferry leaving the day after the trip: options on the travel days now
  come first.
- In the demo, the departure-day checks pushed the reviser back and forth (lunch too late for the airport, then too
  soon after a sight): the message now names the latest start time.
