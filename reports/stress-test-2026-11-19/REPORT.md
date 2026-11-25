# Live stress test, 2026-11-19

> These runs used the system as it was on 2026-11-19, before the agent tree was simplified to the current 10 LLM agents (PLAN.md, Phase 13): the planner and reviser named here are now one `itinerary_writer`, and the transport and stay comparisons are code. The results on the current system are in [FINAL.md](FINAL.md).

The real system (live Lehmus GPT OSS 120B, all keys set) was run on 30 unusual trip requests, plus 3 re-runs that
answered the concierge's own questions (04b, 05b, 25b). Each conversation ran against the real root agent with
`InMemoryRunner`, 5 at a time. 21 conversations went all the way to a written guide. The rest were tests of the
concierge alone (validation, injection, off-topic and similar).

- Harness: `driver.py` (one scenario), `runall.py` (parallel), `scenarios.py` (the 33 conversations),
  `stats.py` / `summarize.py` (tables).
- Per run in `runs/<id>/`: `log.txt` (chat, every tool call and error), `state.json`, `result.json`, and the
  guide, `.ics` and `.geojson` files.
- Quota used: about 19 SerpApi searches (208 left this month). Tavily usage did not change.

## Scoreboard (21 full plans)

| What | Result |
|---|---|
| Itinerary JSON valid, right number of days, all links grounded, all 4 export files written | 21 / 21 |
| Critic approved the plan | 13 / 21 (8 ended after 3× REVISE) |
| Final plan still has a code `must_fix` | 3 / 21 (08, 14, 25b) |
| Plan contains placeholder items ("Leisure morning", "Free snack", "Lunch near Asakusa", "Metro tip", "Taxi to accommodation") | 8 / 21 |
| Last day has no activity at all (early return flight) | 6 / 21 |
| Hostel chosen on a **mid/high** budget | 5 / 21 |
| Time per conversation (5 running in parallel) | 818 s on average, 431–1218 s |

The plumbing is solid: no crashes, no timeouts, no ungrounded links, and follow-up answers never invented places.
The problems are in what gets planned.

---

## Critical: the plan is physically wrong or unsafe

### C1. The arrival airport is never checked against the destination (27, Kyiv)
Every Kiwi result for "Kyiv" lands at **RMO, Chișinău (Moldova)**, about 470 km away (`HEL → CPH → RMO`, ...). The
plan says "11:40 Arrive in Kyiv · 13:30 Kyiv Pechersk Lavra". Nothing compares the arrival airport's coordinates
with `destination_lat/lon`. The guide also has no travel-safety note for a country at war.
**Fix:** check in code that the last segment's airport is within about 100 km of the destination; otherwise
must_fix, or show it in the Important box ("lands in Chișinău, 470 km away"). Consider a short travel-advisory line
in the guide's "Good to know" section.

### C2. Long trips collapse into placeholder days (03, Porto, 21 days)
Days 9–20 are each just "10:00 Leisure morning"; days 7–8 have one sight each. The critic **approved** it in round
3, and "Still to check" is empty. Wine, surfing and azulejo interests are mostly missing.
Causes: culture research returns about 8–12 sights whatever the length (only `pace=packed` scales it), and empty
full days (`thin_day`, `empty_gap`) are should_fix, not must_fix.
**Fix:** scale the number of sights and restaurants with `days` (for example ≥ 2 per day, capped), suggest day trips
for trips over 7 days (Porto → Douro, Braga), and make "a full day with 0 sights" a must_fix.

### C3. The transport choice ignores tier, requirements and lost days (07, 06, 08)
- **07 Vienna, mid budget €2,500, wheelchair user and 2-year-old:** FlixBus Helsinki→Vienna, **31 h each way**. Day
  1 is travel only, the arrival is 21:44 on day 2, and the return arrives the day after the trip ends. A hostel
  (Wombat's) "may need a second room". The guide says "All transport and accommodation listed are
  wheelchair-accessible" and "bus confirmed wheelchair-friendly"; no tool said so.
- **06 school group:** a 28 h bus each way, so 1 usable day out of 4.
**Fix:** score options by price **plus lost hours** (weighted by tier), never choose a leg that eats more than about
50% of a short trip when a flight within budget exists, and let requirements (accessibility, children) rule out
overnight buses. Forbid accessibility claims that no tool result supports.

### C4. Groups of more than 9 break every flight source (06)
Kiwi (validation error on `adults`), fast-flights (`Too many passengers (> 9)`) and SerpApi (`≤ 9`) all fail, and
SerpApi hotels fail above 6 travellers. The planner silently falls back to a bus.
**Fix:** split the search (for example 9 + 5 passengers) and add the prices, or tell the user that groups over 9
need a group booking. Split hotel searches by room.

### C5. Same-day trips are impossible, and the concierge misleads the user (04, 04b)
`TripBrief` rejects `start_date == end_date`. The concierge moved the end to the next day and still called it a "day
trip, return planned for the evening of 15 May". It set `own_accommodation=True` because the user said "no hotel".
The result: outbound on the 15th, return bus at 10:17 on the 16th, and **nowhere to sleep**.
**Fix:** support 1-day trips (end == start, no stay, return the same evening), or have the concierge say plainly
that it cannot, and never set `own_accommodation` because of that.

### C6. Overnight transport and paid nights don't match (04b, 06, 09, 12)
- 09 Turku–Stockholm: overnight ferry, yet the stay is booked for that night too.
- 12 Svalbard: overnight flight, and the night spent on the plane is paid.
- 06: the return bus leaves at 01:49 **after** the trip's end date, so there is a night without a bed.
**Fix:** derive check-in and check-out from the actual arrival and departure, and flag a departure after
`end_date` (or a night with no stay) as must_fix.

### C7. The last day is not feasible: there is no airport buffer
- 24 Barcelona: "09:00 Casa Batlló · 10:45 Depart" (flight).
- 12: "11:00 Mix kiosk · 12:10 Depart" (flight).
No check in `review_checks.py` covers time to the airport.
**Fix:** for flights, the last item must end about 2.5 h before departure (about 1 h for trains and buses).

---

## High: wrong prices or wrong choices

### H1. Early return flights waste the last day, even when the fix costs €5 (01)
The Important box says: "airBaltic at 20:10 costs €5 more and keeps most of the day", but the plan keeps the 08:55
flight, so a 2-day trip gets 1 day. Same pattern in 25b (07:15), 29 (08:50), 15 and 24. The box finds the better
option; nothing acts on it.
**Fix:** when the alternative is within X% or €Y (scaled by tier), the planner or reviser should switch to it.

### H2. A mid budget gets hostels, and hostel prices are per bed (01, 07, 09, 24, 29, 27, 15)
`stay.py` uses `sort_by "lowest_price"` for **mid** too, so the cheapest result (often a dorm bed) wins. The
Google Hotels lowest price for hostels looks per-bed rather than per-room for 2 people: City Loft Kyiv €12.50 a
night, Övernäsgården €25 a night, Bresson Hostel €32 a night. Stay costs for groups are probably understated. At
the other end, **14 Monaco, "money is no object, best hotel", €50,000:** the stay is an Airbnb "Duplex in a village
house" for €367, and the guide itself says the Monte-Carlo Bay is the luxurious choice. €1,725 of €50,000 was spent.
**Fix:** mid → sort by rating or price with a minimum class, and exclude hostels unless the tier is low. High →
`highest_rating` plus 4–5★, and use the budget. Treat hostel prices for 2+ people as per bed, or exclude them.

### H3. Options without a price disappear (15, 09)
- 15 Åland, low budget €500: the ferry options had no price, so they were left out of the cheapest/fastest
  comparison. The plan uses flights and ends over budget (€652). The ferry is the usual cheap way there.
- 09: the car-ferry fare was unknown and counted as **€0**.
**Fix:** keep price-unknown options with an estimate band, or with a note in the Important box. Never count €0.

### H4. Food costs ignore what is actually planned (02)
On a €200 "low" trip the plan includes **Dill** (Michelin) and lunch at Hotel Holt. The cost uses "typical" food
costs, so these are never priced. A low tier should exclude fine dining, or such places should carry their own
price.

### H5. Impossible budgets are not flagged before planning (02, Reykjavik, €200 for 2 people and 5 days)
The concierge confirmed without comment, and 14 minutes later the guide said €1,215. One sentence at confirmation
("the flights alone usually cost more than €200; plan anyway?") would save the user a run.

### H6. Interests are often missed
- 24 Barcelona with "architecture": no Sagrada Família, no Park Güell.
- 03: no surfing or wine tasting.
- 15: no cycling.
**Fix:** a check that each stated interest has at least one item, or a "Still to check" line when it doesn't.

---

## Medium: review loop and guide quality

- **The review loop often does not converge.** 8 of 21 runs ended after 3× REVISE, and 3 still have must_fix items
  (08 empty morning; 14 and 25b ungrounded "St. Nicholas Cathedral" and "Notre-Dam Treasury", a typo of a
  researched name). A fuzzy name match would solve the latter.
- **Placeholder items get through** (8/21): "Free snack", "Snack before flight", "Free morning", "Free dinner",
  "Lunch near Asakusa" (on a high budget), "Afternoon coffee break", "Metro tip", "Taxi to accommodation",
  "Check-in and settle", "Indoor activity". The placeholder_meal check doesn't catch these name patterns.
- **The same place appears twice** (07: Zum Leupold on days 3 and 4). A district is used as a meal place (09:
  "Kungsholmen"). A 30-minute hammam is followed by lunch (11).
- **Broken links:** 14's guide has `[source](null)` twice.
- **Unverified claims in guides:** "no alcohol is served" at every eatery (11), the wheelchair claims (07), "Sleep
  in Heaven" called a hotel (it is a hostel).
- **The plan summary hides multi-day legs:** "FlixBus 14:38-19:09" is shown without the arrival date and reads as
  4.5 h (it is 28 h). Show "+1 day".
- **No seasonal warnings:** polar night in Svalbard in January (12), Grand Prix weekend road closures and prices in
  Monaco (14).

## Medium: concierge and chat

- **The guide writer's chat message is broken in almost every run.** It leaks absolute local paths
  (`C:\Users\canba\AppData\...`), shows mangled ones (`Temp\wo...`, `...-to-2027-09-09.md` with a wrong date) or
  mentions "the temporary folder". In 03 it dumped the raw `{"markdown_text": ...}` JSON into the chat. It also
  sends 2 messages. Replace it with a fixed message built in code (file names only).
- **Language:** a Finnish request (24) got English answers throughout, including the Finnish follow-up question.
- **Extra confirmation rounds:** in 04b and 27 the user's "yes" made the concierge show the summary again instead
  of saving. In 25b, after "convert it to euros", it asked for the budget tier, which has a default.
- **An ambiguous "yes" after a change:** in 29, "Yes, please do that" (meant as accepting the change) was taken as
  **approval** and the guide was written.
- **The "cheaper hotel" re-plan returned the same hostel** (29) without saying it was already the cheapest. "cheaper
  hotel" and "no Auschwitz visit" were saved as permanent `requirements` and carried into the next trip's brief.
- **The day-swap re-plan (`rerun []`) changed the outbound flight** (30: Finnair 07:00 → airBaltic 09:15) although
  transport was meant to be kept. The concierge's own preview of the swap put "depart 19:40" on day 1.
- **Credit card number (20):** "book with my card 4111..." was ignored silently. There was no "I never book; please
  don't share card details" warning, and the number stays in the session sent to the LLM.
- **Off-topic (21):** "I'm sorry, but I can't help with that." It never says what the service does.
- **Geocoding edge cases:**
  - "Tokyo" comes back as a *province* and the concierge asks a confusing "23 wards or another city?" question (05).
    Treat capital-type regions such as Tokyo or Singapore as cities.
  - "Atlantis" was silently mapped to the town of Atlantis in South Africa (23).
  - "03/04/2027" was read as 3 April without asking (25b).
- **Counting slips in summaries:** "7–9 May (3 nights, 2 days)" and "13–15 May (3 nights, 3 days)".

## Low: tools and infrastructure

- **Tool error messages contain hard-coded Lisbon examples:** `wiki_summary` says "add the city, e.g. 'Blue Mosque,
  Lisbon'", and airbnb says "e.g. 'Baixa, Lisbon'". A model may copy "Lisbon" for Istanbul.
- **Google Flights (fast-flights) returns the cookie-consent page** from this network (14), so the tool is dead
  here.
- **Under parallel load:** Nominatim 429 (the Open-Meteo fallback also failed once) and Overpass 502. There are
  fallbacks, but a shared rate limiter would help in the UI (several users).
- **Speed:** 7–9 min for one plan alone, 12–18 min with 5 at once (the LLM is the bottleneck). The README says 5–7
  min.

## What worked well
- Validation: past dates (17), 45 days (16, it offers a split), multi-city (18), 0 travellers combined with "5★ for
  €300" (26), ambiguous "Springfield" (22).
- Prompt injection and system-prompt extraction were refused (20). Booking requests were refused (30).
- Follow-up answers stay grounded: "the guide does not include Schönbrunn / Sagrada Família / sushi" instead of
  inventing answers.
- Changing one's mind twice before confirming (28): the final brief was exactly right.
- Requirements: vegan-only restaurants (11), no flights (08, `avoid_modes`), own accommodation (10), own car with
  car ferry (09).
- A trip across New Year (13), a remote destination (12), a long-haul destination in another currency (05b).
- The open-jaw request (27) was explained clearly and the closest alternative was offered.

## Suggested order of work
1. A code check that the arrival airport is near the destination (C1). It is a few lines and prevents absurd plans.
2. Feasibility checks in `review_checks`: airport buffer (C7), departure after `end_date` or a night without a
   stay (C6), full day with 0 sights as must_fix (C2), a wider placeholder pattern.
3. Stay tier logic: no hostels on mid, a real high tier, per-bed prices (H2).
4. Transport scoring by price plus lost hours, by tier and requirements (C3, H1). Splitting groups over 9 (C4).
5. Research that scales with trip length (C2). Coverage of stated interests (H6).
6. Concierge: same-day trips (C5), replying in the user's language, a fixed guide-ready message, a card-data
   warning, no extra confirmations.
