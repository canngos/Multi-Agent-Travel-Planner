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
| 1 | Show the empty map | Each station is an agent: 13 LLM agents. The lines are the orchestration. The concierge delegates to a sequential pipeline. Research runs in four parallel branches, two of them with a nested parallel search. Then come the planner, a review loop of at most three rounds, and the guide writer. The map is generated from the Python agent tree. |
| 2 | Click **Send demo message 1 of 4** ("Plan 5 days in Lisbon…") | The concierge asks only for what is missing: dates and departure city. *Coordinator pattern.* |
| 3 | Send message 2 ("We fly from Helsinki, 10-14 May…") | Two `geocode_place` calls, then `save_trip_brief` answers **needs_confirmation**. Saving is gated in code: the model cannot start planning before you confirm. |
| 4 | Send message 3 ("Yes, that's right, go ahead.") | The hand-over (dotted track). All six research agents start **at the same moment**, which is the parallel pattern. Point at the chips: `search-flight` is the **Kiwi MCP server**, `airbnb_search` the **Airbnb MCP server**, plus Transitous, OpenTripMap, Wikivoyage and Overpass. |
| 5 | While research runs, click **Flight searcher** | The inspector shows the agent's tools, its role prompt, every tool call (click one for the arguments and the JSON result) and the model's reasoning. |
| 6 | Wait for the comparers | Each comparer starts only when both of its searchers are done (sequential inside each branch). Click **Shared state** to show `transport_summary`: the cheapest, fastest and ranked options, plus a recommendation. |
| 7 | The planner, then the review loop | The planner uses weather, distance clustering and the cost tool. Then the **loop**: the budget critic runs `check_itinerary`. That is code checking opening hours, the budget, grounding and pacing. The verdict is REVISE, the reviser fixes the plan, round 2 approves, and the loop ends. The loop label counts the rounds. |
| 8 | Open the **Review** tab | Round 1 lists what the reviser changed: a flight arriving a day late swapped for a direct one on the travel day, lunch and dinner added on day 1, an empty afternoon filled. Round 2 shows the code check deciding APPROVED. *Loop pattern with a stopping condition.* |
| 9 | The **Guide** tab opens by itself | Show the transport tables (*live* vs *web estimate* prices), the stay comparison by neighbourhood, a day with history snippets and food stops, the cost table against the €2,500 budget, and the attribution footer. Point at the **Important** box at the top: code warns that the 06:50 return flight costs most of the last day and names the Finnair flight at 16:35 that keeps it (€104 more). Every link comes from a tool result (grounded in code). The files are in `output/`. |
| 10 | Send message 4 ("Which day do we visit Belém…") | The concierge answers **from the guide** only: Belém is on days 2 and 3. The Belém Tower itself is not in this plan, so for "the tower" it gives the ticket of the Monument to the Discoveries (€10) and names it, instead of inventing a price. A date written next to "Day N" is checked against the trip in code. |
| 11 | Open the **Activity** tab | The Gantt chart: the parallel research, the comparers waiting, two critic/reviser rounds. Ticks are tool calls (38 in this run). |

## If there is time

- **Watch a recorded run** at 4×, with seeking to any moment.
- Run `.venv/Scripts/python scripts/run_trip.py --demo` in a terminal. The same run takes about 3 s with no
  pacing and prints the quality checks: grounded links, valid itinerary, no must-fix problems.
- `pytest`: 193 offline tests in about a second.
- A live run on another trip (needs the VPN and keys, 5 to 7 minutes):
  `.venv/Scripts/python scripts/run_trip.py --trip tallinn`. It shows the ferry path, with Tallink and Viking
  Line from Transitous and web-estimate fares.

## Questions to expect

- **"Is the demo real?"** Yes. It is a recording of a live run (Lehmus GPT OSS 120B plus the real APIs). Demo
  mode replays the model's answers and the tool results, so the run is identical every time. Without `DEMO=1`
  the same code calls everything live.
- **"What stops the model from inventing places or prices?"** Code checks. Links must come from tool results.
  Tickets are reset to the researched values. Places not in the research are must-fix problems for the critic.
  Code decides the verdict, so the model cannot approve a plan that still has such a problem.
- **"Why so many agents?"** Each has one job and a small prompt, and independent research runs in parallel. GPT
  OSS follows short, focused instructions much better than one large prompt.
