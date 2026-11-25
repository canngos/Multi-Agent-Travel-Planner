# Final system: third live run, 2026-11-25

All 33 conversations of [REPORT.md](REPORT.md) were run again on the submitted system (10 LLM agents: the itinerary
writer and plan critic loop of at most 4 rounds, transport and stay summaries built in code). Same harness, 5 at a
time, next to two other live runs. Results are in `runs-final/`; summary tables:
`STRESS_RUNS=runs-final python stats.py` and `... summarize.py`.

Four long plans (01, 02, 03, 05) hit the harness's 20-minute turn limit in the middle of the review loop. They were
run again with `TURN_TIMEOUT=3600`, 4 at a time; the cut-off runs are kept in `runs-final-timeouts/`.

## Scoreboard

| What | Result |
|---|---|
| Conversations that should not lead to a plan, handled correctly | 9 / 10 (22: "Springfield" taken as Springfield, Illinois without asking) |
| Full plans: valid, right number of days, every place grounded | 23 / 23 |
| All 4 files written (.md, .html, .ics, .geojson) | 23 / 23 |
| Guide written only after the user's approval | 23 / 23 |
| Final plan still has a code must-fix | 2 / 23 (03: 21-day Porto, sights on closed days and five days without a sight; 02: Reykjavik on €200, one food stand on two days) |
| Approved by the critic (not stopped at the round limit) | 17 / 23 |
| Hostel on a mid/high budget | 0 |
| `[source](null)` links | 0 |
| Time per full conversation | 261 to 1131 s, median 708 s |
| Tool calls per full conversation | 28 to 61 |

## Notes

- Refused or questioned as intended: 45 days (16), past dates (17), several cities (18), no destination (19),
  prompt injection and a card number (20, the number is removed before the model sees it), off-topic (21),
  Atlantis (23, asks to confirm the village in Florida), an ambiguous date (25), 0 travellers (26).
- 05 Tokyo: the guide writer's model returned an empty answer once; the retry wrote the guide.
- "Lunch near …" items counted as placeholders by `stats.py` are the meal placeholders code puts in when no
  researched restaurant fits; they are not filler.
- The 21-day trip is the weakest case: the research does not find enough sights for three weeks, and the itinerary
  writer did not call `extra_sights_researcher` to find more.
