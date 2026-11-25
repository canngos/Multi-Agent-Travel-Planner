# Demo script (about 8 minutes)

This follows the demo scenario in PROJECT.md. It uses the recorded Lisbon run, so it cannot fail on a rate limit,
a slow API or a network problem, and it needs no university VPN. Everything shown is the real system: the real
agents and code guards, replaying recorded tool results and model answers.

## Before you start

Ten minutes before the demo, in the project root:

```bash
.venv/Scripts/python scripts/serve.py --demo        # opens http://localhost:8000
```

`--demo` replays the recorded run at a watchable speed (`DEMO_PACE=0.2`, about 2 minutes for the pipeline;
set `DEMO_PACE=0.3` for more time to talk). Check that the header says "Planner server connected".

Fallbacks:
- If the API server misbehaves, switch the UI to **Watch a recorded run**. It replays the same run in the
  browser without any server, at 1× to 8× speed.
- `adk web` with `DEMO=1` shows ADK's own trace view.

## Script

| # | Do | Point out |
|---|---|---|
| 1 | Show the empty map | Each station is an agent: 10 LLM agents. The lines are the orchestration. The concierge delegates to a sequential pipeline. Research runs in four parallel branches, two of them with a nested parallel search whose options code then compares (no LLM needed for sorting). Then the review **loop** of at most four rounds: the itinerary writer drafts, the plan critic reviews, the writer revises; the guide writer is the concierge's second hand-over, after your approval. The map is generated from the Python agent tree. |
| 2 | Click **Send demo message 1 of 8** ("Plan 5 days in Lisbon…") | The concierge asks only for what is missing: dates and departure city. *Coordinator pattern.* |
| 3 | Send message 2 ("We fly from Helsinki, 10-14 May…") | Two `geocode_place` calls, then `save_trip_brief` answers **needs_confirmation**. Saving is gated in code: the model cannot start planning before you confirm. |
| 4 | Send message 3 ("Yes, that's right, go ahead.") | The hand-over (dotted track). All six research agents start **at the same moment**: the parallel pattern. Point at the chips: `search-flight` is the **Kiwi MCP server**, `airbnb_search` the **Airbnb MCP server**, plus Transitous, OpenTripMap, Wikivoyage and Overpass. |
| 5 | While research runs, click **Flight searcher** | The inspector shows the agent's tools, its role prompt, every tool call (click one for the arguments and the JSON result) and the model's reasoning. |
| 6 | The review loop | The itinerary writer drafts the plan with weather, distance clustering and the cost tool. The plan critic runs `check_itinerary`: code reports facts as must-fix (opening hours, budget, grounding) and day-shape points under **decide**, which are the critic's call (fix, or decline with a reason about this trip). In round 1 code found 1 must-fix point (Lisbon Cathedral planned after closing) and 1 placeholder lunch; the writer revised: the cathedral moved to 18:00 and the lunch became Restaurante Tascardoso. When revising, the writer may call `extra_sights_researcher` (a culture researcher used as a tool) for a thin day; it did not need to here (the live Rome run shows it, see `scripts/evaluate.py`). |
| 7 | The plan appears in the chat | The pipeline stops and shows the reviewed plan with **Approve** / **Request changes** (*human in the loop*). The guide is not written yet. Open the **Review** tab: two rounds. In round 2 the critic still said REVISE, but no must-fix problem was left and another round could not help, so code approved, logged the override, and the approval ended the loop. |
| 8 | Click **Approve** (message 4) | The concierge reads "looks good" and calls `approve_plan`; code checks that a reviewed plan is waiting, then hands over to the guide writer. The **Guide** tab opens: transport tables (*live* vs *web estimate*), stays by neighbourhood, days with history snippets, the cost table against €2,500, the day-rhythm line, the attribution footer. Every link comes from a tool result. |
| 9 | Send message 5 ("Which day do we visit Belém…") | The concierge answers **from the guide**: Belém is on day 2, and the Belém Tower is not in this plan, so it says so instead of quoting a price from memory. A date next to "Day N" is checked against the trip in code. |
| 10 | Send message 6 ("Could we take it a bit slower… somewhere cheaper?") | The concierge shows the changed details (pace: relaxed, a cheaper stay) and asks you to confirm. |
| 11 | Send message 7 ("Yes, please do that.") | **The change router:** the concierge calls `request_replan(rerun=["stay"])`, then saves the brief with the relaxed pace. Code validated the scope (same city, dates, origin and group). On the map only the **stay team** works; transport, culture and food are skipped and keep their results. Open **Cost and time**: "Research reused: transport, culture, food. Researched again: stay (the concierge's choice)." |
| 12 | The second plan appears; click **Approve** (message 8) | A cheaper stay (Green View Home, €176 instead of Marta's House at €356), relaxed days. Round 1: REVISE with 2 must-fix points (an opening-hours clash, and no street food although the travellers asked for it); the writer added Mercado Oriental. In round 2 code again approved over the critic's REVISE and logged it. The guide writer saves the guide plus a **calendar** (`.ics`) and a **map** (`.geojson`); its chat message is written by code (file names, transport, stay, total). |
| 13 | Open the **Activity** and **Cost and time** tabs | The Gantt chart: two plans, the second without most of the research. Tokens and tool calls per agent (51 tool calls in this run). |

## If there is time

- **Watch a recorded run** at 4×, with seeking to any moment.
- Run `.venv/Scripts/python scripts/run_trip.py --demo` in a terminal. The same run takes about 3 s with no
  pacing and prints the quality checks: grounded links, valid itinerary, no must-fix problems.
- `pytest`: the offline test suite (a few seconds), including a replay of this demo that must have no cache miss.
- `.venv/Scripts/python scripts/evaluate.py --agents`: who decided what in the recorded runs (critic/code
  agreement, overrides, grounding, tokens per agent).
- A live run on another trip (needs the VPN and keys, 5 to 7 minutes):
  `.venv/Scripts/python scripts/run_trip.py --trip tallinn`. It shows the ferry path, with Tallink and Viking
  Line from Transitous and web-estimate fares.

## Questions to expect

- **"Is the demo real?"** Yes. It is a recording of a live run (Lehmus GPT OSS 120B plus the real APIs). Demo
  mode replays the model's answers and the tool results, so the run is identical every time. Without `DEMO=1`
  the same code calls everything live.
- **"What stops the model from inventing places or prices?"** Code checks. Links must come from tool results.
  Tickets are reset to the researched values. Places not in the research are must-fix problems for the critic.
  Code overrules the critic on such facts, and logs it, so the model cannot approve a plan that still has one.
- **"What does the LLM decide, and what code?"** The agents choose: research, schedule, fixes, what a change needs
  (`request_replan`), when to research more (`extra_sights_researcher`), and the user approves (`approve_plan`).
  Code checks facts and refuses unsafe actions with a reason (README, "Who decides what").
- **"What if I type a different message in the demo?"** `LLM_CACHE_MISS`: the replay only knows the recorded
  conversation. Without `DEMO=1` the same code runs live.
- **"Why so many agents?"** Each has one job and a small prompt, and independent research runs in parallel. GPT
  OSS follows short, focused instructions much better than one large prompt.
