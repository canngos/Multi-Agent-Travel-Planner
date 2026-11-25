# Project review and work plan

A review of the Multi-Agent Travel Planner against the course brief, written from the lecturer's point of view.
It is meant as a **work plan for an agent** that implements the fixes and features below. Read §1–§3 before
starting any task.

Written on 2026-11-19 (`pytest` 385 passed then). Updated on 2026-11-25 to the submitted system: names in
this file are the current ones, and the status of every task is below.

## Status (2026-11-25)

Every task is done except A8 (multi-city), which stays a stretch goal. `pytest`: 498 passed, 18 live
deselected. Decisions are logged in `PLAN.md`.

| Task | Status |
|---|---|
| A1 Change router | Done, as a concierge tool `request_replan` (no separate router agent) |
| A2 Research on demand | Done: `extra_sights_researcher` (AgentTool) of the `itinerary_writer`, at most 2 calls per round |
| A3 Evaluation and ablation | Done: `scripts/evaluate.py`, `CRITIC_MODE=code_only`; live results in the README |
| A4 Critic vs code | Done: day-shape findings are a "decide" list the critic fixes or declines with a reason |
| A5 Skip agents that cannot help | Done: flight search skipped for an avoided mode or own car, Airbnb without `npx` |
| A6 Human in the loop | Done: `approve_plan`; the guide writer runs only after approval |
| A7 Small extras | Done: `.ics` and `.geojson` exports, tokens/seconds panel in the UI |
| A8 Multi-city | Not done (README Limitations: one city, up to 21 days) |
| F1 Demo shows an older system | Done: demo re-recorded (8 messages), every `config.demo()` behaviour branch removed |
| F2 README out of date | Done |
| F3 Default day start | Done: 09:30 in the check and the prompts |
| F4 Pace numbers | Done: the check stays one wider on purpose (comment at `review_checks.PACES`) |
| F5 Repository tidiness | Done |
| F6 Naming | Done: `budget_critic` is now `plan_critic` |

Later (2026-11-25) the system got simpler: code builds the transport and stay summaries
(`agents/summaries.py`), and one `itinerary_writer` drafts and revises in a loop of at most four rounds that
ends when the plan critic approves. That leaves **10 LLM agents**.

## 1. The brief, and where the project stands

| Requirement | Status (2026-11-25) |
|---|---|
| ≥ 3 different agents | Met: 10 LLM agents, a culture researcher used as a tool, 5 workflow agents |
| Multi-agent orchestration pattern (Lecture 4) | Met: sequential, parallel, nested parallel, loop, coordinator handoff, agent as tool, human in the loop |
| ≥ 1 tool call | Met: 24 function tools, an agent used as a tool, 2 MCP servers |
| No code from assignments 2 and 3 | Met (new repository) |
| Executable, runs without errors | Met offline; a live run needs the Oulu VPN (Lehmus) or another LLM key |
| **Fully agentic** | Was the weakest point (§2); A1–A4 addressed it, see the README's "Who decides what" |

## 2. The "fully agentic" problem

On 2026-11-19 the LLMs were autonomous *inside* each step, but **code owned every decision between steps**
(the table shows the current file names; the README's "Who decides what" shows the state after the fixes):

| Decision | Who made it (2026-11-19) | Where |
|---|---|---|
| Order of all agents | fixed workflow (Sequential/Parallel/Loop) | `agents/pipeline.py`, `agents/teams.py` |
| Handoff concierge → pipeline | code sets `transfer_to_agent` | `concierge.py::note_saved_brief` |
| Skip research on re-plan | code diffs two briefs | `pipeline.py::research_reusable` (removed by A1) |
| Review verdict | code overrides the critic both ways | `plan_critic.py::enforce_verdict` |
| End of the loop / undoing revisions | code | `itinerary_writer.py::guard_revision`, `review_loop.py` |
| Last chat message (rhythm offer) | code | `guide_writer.py::finish_guide` |

The guard code (~1,450 lines, `review_checks.py` alone ~760) is much larger than the agent definitions (~45
lines each). The README claims "code only checks and repairs, never plans". A lecturer will ask: *"If code
decides the verdict, what does the critic agent add?"*

### Principle for every task below: **agents decide, code guards facts**
- An **LLM agent** makes every *choice*: what to re-run, what to research more, whether a plan is good enough
  on matters of taste (pacing, interests, order, rhythm).
- **Code** only validates **objective facts**: opening hours, grounded links/prices, budget arithmetic, day
  count, hard requirements (`avoid_modes`). Code may *refuse* an unsafe action and say why (as
  `request_replan` and `approve_plan` do). It should not silently take the decision itself.
- Every code override is **logged** (as `review_log` does with `critic_said` / `overridden`), so we can
  measure how much the agents decide.
- Prefer giving the agent a **tool that code validates** over a callback that acts for it. Example: a
  `request_replan(scope)` tool rather than a brief diff.

## 3. Constraints (read before changing anything)

1. **Never commit, stage or push.** The project owner does all git work.
2. **The offline demo must keep replaying.** `cache/demo/` holds LLM answers keyed on each agent's *exact
   rendered prompt and tool results*. Any change to a prompt, a tool's output or a check's output **in demo
   mode** causes `LLM_CACHE_MISS`. Re-recording needs the Lehmus LLM (university VPN), which only the owner
   has. So:
   - gate new prompt text and behaviour with `config.demo()`, or append a `{state_key?}` placeholder that
     renders as `""` when the key is unset (see `culture.py`, `{trip_pace_research?}`);
   - leave empty fields out of the saved brief (`tools/trip.py::OMIT_WHEN_EMPTY`);
   - after every change, run `.venv/Scripts/python scripts/run_trip.py --demo` and check it reports
     **0 cache misses**. Back up `output/` first: the demo overwrites the Lisbon guide there.
3. Tests pin today's date via `TODAY` (`tests/conftest.py`). Use scripted models for agent tests, as
   `tests/test_agents.py` and `tests/test_review.py` do. No live LLM in the default suite.
4. Log every design decision in `PLAN.md`'s decision table (date | decision | reason), as the existing rows do.
5. Keep the tool contract: tools return `{"status": "ok", "results": [...]}` or
   `{"status": "error", "reason": ...}` and never raise.
6. Agent and state-key names appear in the UI graph (`travel-planner-ui/src/generated/agent-graph.json`, made
   by `scripts/export_agent_graph.py`) and in the demo keys. Re-export the graph when the tree changes. Don't
   rename existing agents before a re-record.

## 4. Tasks: features (agentic first)

### A1. LLM change router (highest value)
**Why:** turns the code-made "reuse research" branch into an agent decision and makes every follow-up change
fast, not only rhythm changes. It answers §2 directly.
**Now:** `pipeline.py::research_reusable` reuses research only when `day_start`/`day_end`/`pace` alone
changed. Every other change (cheaper hotel, more museums, other flight) re-runs all research (5–7 min).
**Do:**
- Give the concierge (or a new `change_router` LlmAgent placed before the pipeline) a tool such as
  `request_replan(rerun: list["transport"|"stay"|"culture"|"food"], reason: str)`. The agent picks the scope
  from the user's request.
- Code **validates** the scope and refuses with a reason when unsafe: a changed destination, dates, origin or
  travellers needs a full re-run; there must be a finished previous plan; a new `avoid_modes` needs transport.
  Store the accepted scope in state.
- Generalise `skip_research_if_reused` into a before-agent callback on each research branch
  (`transport_team`, `stay_team`, `culture_agent`, `food_agent`) that skips the branch when it is not in scope.
  Keep its state keys (`RESEARCH_KEYS`) in `reset_plan_state`.
- Keep today's rhythm-only behaviour as one case of the router (scope = nothing).
- Demo mode: the router is off (as `research_reusable` is now).
**Done when:** scripted-model tests cover a stay-only change, a culture-only change, a rhythm-only change, an
unsafe scope that is refused, and the demo path being unchanged. The README's architecture section names the
router as an agent decision. `run_trip.py --demo` has 0 misses.

### A2. Research on demand: agent as tool
**Why:** adds a new orchestration pattern (agent-as-tool) and fixes the "thin days" limitation by letting
the itinerary writer *decide* to research more, instead of code over-researching up front (`pace_research_block`).
**Do:**
- Build a *new instance* of the culture researcher (ADK agents cannot have two parents), e.g.
  `extra_sights_researcher`, wrapped in `google.adk.tools.agent_tool.AgentTool`. Its input is an area with
  lat/lon and the number of sights wanted.
- Give it to `itinerary_writer` (when revising) with a small budget (`limits.limit_tool_calls`, 1–2 calls per round).
- **Grounding:** merge its results into `culture_findings` (or add the key to what `grounding.known_urls`
  reads), so the new places' links and prices count as researched and `review_checks` finds them.
- Demo mode: do not offer the tool.
**Done when:** a test with a scripted writer calls the tool on a thin day, and the new sight passes
`check_plan` as grounded. The tool budget is enforced.

### A3. Measure who decides: evaluation and ablation
**Why:** evidence for the "fully agentic" claim, and the report needs numbers, not only tests.
**Do:** `scripts/evaluate.py` that, for each finished session or run (the four test trips and the demo):
- reads `review_log`, `revision_log`, the `*_link_fixes` keys and the guide;
- reports: rounds to approval, critic/code agreement and override rate, must/should counts per round,
  grounded-item ratio, links removed, budget vs computed total, tool calls and (if available) tokens and
  seconds per agent, and research-reuse time saved;
- optional `--judge` mode: an LLM-as-judge rubric (interest match, pacing, realism) scored 1–5;
- an ablation flag (e.g. `CRITIC_MODE=code_only`) that replaces the critic's text with the code verdict, so
  both modes can be compared on the same trips.
It must run offline on recorded runs. Live runs are for the owner (VPN).
**Done when:** it prints a Markdown table for the demo run offline. The README gets an "Evaluation" section
with the table format (the owner fills in the live numbers).

### A4. Rebalance critic vs code (needs the owner's decision first; ask before changing)
**Why:** today `enforce_verdict` overrides the critic on every must-fix item, and the 2026-11-19 work moved
taste issues into must-fix (`empty_morning`, `double_meal`, `placeholder_meal`, `before_day_start`,
`after_day_end`, `empty_afternoon`).
**Proposal:** split checks into **facts** (closed hours, ungrounded place or price, budget with a cheaper
option, wrong day count, hard requirements), which stay code-enforced must-fix, and **taste** (the day-shape
issues above), where code reports and the **critic decides**. Log every case where the critic declines a taste
issue, with its reason.
**Trade-off:** live runs showed GPT OSS approving lunch-first days. That is why these became must-fix. Show
the owner the A3 numbers before changing anything.

### A5. Skip agents that cannot help (small)
Using the A1 mechanism: skip `flight_searcher` when `"flight"` is in `avoid_modes`, and `airbnb_searcher`
when `npx` is missing. Prefer letting the router or concierge decide where it is a choice. Use code only
where it is a hard fact.

### A6. Human in the loop before the guide (optional)
Show the reviewed itinerary in the UI and let the user approve or request changes before `guide_writer` runs
(ADK long-running function tool or pause/resume). A change request goes through the A1 router.

### A7. Small extras (optional)
- `.ics` calendar export of the days (the brief now has day hours) and a GeoJSON list of the stops, as tools
  of `guide_writer`.
- A tokens/seconds-per-agent panel in the UI (the data is in the event stream). Mark reused-research runs.

### A8. Multi-city trips (stretch goal; large)
**Why:** removes the biggest product limitation ("one city, up to 21 days"). It also adds a real agent
decision: choosing the route and splitting the nights.
**Now:** one city per trip, enforced in three places:
- `TripBrief.destination` is a single city;
- `tools/trip.py::check_brief` refuses a destination that "names several places";
- `tools/geo.py` refuses regions ("Tuscany").

The concierge prompt (`concierge.py`, "destination: one city") asks the user to pick one city. The return is
always destination → origin.
**Do:**
- **Brief:** add an optional `stops: [{city, lat, lon, country, nights}]` (left out when empty, so one-city
  briefs and the demo stay byte-identical, see §3). A single-city trip keeps today's `destination` fields.
- **Route agent:** a new `route_planner` LlmAgent decides the order of the cities and the nights per city
  from the user's wishes. It can also turn a region into cities: "Tuscany" → Florence, Siena, Lucca. Use
  `geocode_place` plus a distance tool. Code only validates facts: the nights add up to the trip length,
  every stop is geocoded, each leg is reachable (no hard `avoid_modes` broken). The user confirms the route
  in the existing confirmation flow (`concierge.py::require_confirmation`).
- **Research per stop:** run the stay, culture and food research once per city, and transport for every leg
  (origin → A, A → B, …, last → origin). ADK workflow agents are built statically, so either:
  - (a) build the per-stop teams when the brief is saved, with a fixed upper limit (e.g. 3 stops), skipping
    unused slots with the A1/A5 skip callbacks; or
  - (b) loop over the stops with a `LoopAgent` whose agents read `{current_stop}`.

  Each stop writes stop-scoped state keys (e.g. `stay_summary_1`), and `grounding.known_urls` and
  `review_checks` must read all of them.
- **Planner, checks, guide:** add travel days between stops (the inter-city leg replaces the arrival and
  departure items). `check_plan` checks each stop's days against that stop's research. The guide gets one
  section per city, and the cost table sums all legs and stays.
- Demo mode: off (one-city demo unchanged).
**Done when:**
- scripted-model tests cover a 2-city and a 3-city trip, a region turned into cities, nights that don't add
  up (refused), and a leg that breaks `avoid_modes` (refused);
- a one-city brief produces the same prompts as before;
- `run_trip.py --demo` has 0 misses;
- the README "Limitations" section is updated.

**Order:** only after A1 and A3. It touches nearly every module, so plan it separately before coding.

## 5. Tasks: fixes

### F1. The demo shows an older system (urgent before submission; owner + agent)
There were about nine `config.demo()` branches (all removed since): `concierge.py` (LIMITS, DAY_RHYTHM), the
planner's `plan_rules`, the reviser's `how_to_fix`, `review_checks.py::live_checks`, `guide_writer.py` (rhythm line,
`finish_guide`) and `pipeline.py::research_reusable`. The demo the grader can run shows **none** of the new
features, and still shows faults that are already fixed (full days starting with lunch).
- **Agent:** add an end-to-end test that replays the recorded demo (`DEMO=1`, the four recorded messages)
  and asserts 0 LLM cache misses and a saved guide. It must not overwrite `output/` (use a temp output dir).
- **Owner (VPN):** freeze the prompts, re-record (see the auto-memory notes: reuse cached tool results, keep
  Nominatim failing as recorded), then `scripts/prune_demo_cache.py --delete`. Regenerate the UI replay
  (`scripts/record_ui_replay.py`) and update the facts pinned in `travel-planner-ui/src/lib/run.test.ts`.
  Consider a 5th recorded message that changes the pace, to show research reuse.
- **Agent, after the re-record:** remove the demo branches, so the demo and the live system run the same code.

### F2. README is out of date
- The test count says 357 (`README.md` requirements table and Tests section). Use the current count from
  `pytest`.
- Document the day-rhythm/pace fields, research reuse and (after A1) the router.
- Rewrite the "Fully agentic" row and add a short "Who decides what" table: the §2 table, updated after
  A1–A4.

### F3. Default day start differs between check and prompt (wait for F1)
With no `day_start`, `review_checks.DAY_START` is 09:00 for the gap check, while the prompts and
`config.DEFAULT_DAY_START` say 09:30. Aligning them changes check output in demo mode, so do it only behind
`live_checks()` or after the re-record.

### F4. Pace numbers: prompt vs check
The planner prompt says packed = 4–6 sights; `review_checks.PACES["packed"]` accepts 3–7 (relaxed 1–3,
balanced 2–5). Either align them or add a comment that the check is deliberately more tolerant.

### F5. Repository tidiness (ask the owner)
- `lisbon-2027-05-10-to-2027-05-14.md` in the repo root is a generated guide (belongs in `output/`).
- `Travel data APIs beyond web search.md` exists both in the root and in `reports/`.
- Untracked: `docs/Final project report.docx`, `research_notes/`, `travel-planner-ui/src/lib/theme.ts`.
  The owner decides what is submitted.

### F6. Naming (only after a re-record)
The critic (then `budget_critic`) also checks hours, pacing and grounding, so `plan_critic` describes it
better. Renaming changes the demo keys and the UI graph, so do it only together with F1. (Done.)

## 6. Questions the lecturer is likely to ask
- Which decisions does the LLM make, and which does code make? Why is that still *fully agentic*? (§2,
  A1–A4)
- Show a case where the critic found something the code check didn't. (A3)
- Your demo starts every day with lunch. Didn't you fix that? (F1)
- What happens if I type a different message in the demo? (`LLM_CACHE_MISS`: say so up front)
- Why 10 agents? Why is `airbnb_searcher` an agent and not a tool, and why is comparing the options code and
  not an agent? (Have a one-line reason per agent.)
- How do you know the plans are *good*, not just valid? (A3)

## 7. Suggested order
1. F2 (README counts) and the F1 demo-replay test: safe, no prompt changes.
2. A3 (evaluation): the evidence for everything else.
3. A1 (change router), then A2 (research on demand).
4. Owner: F1 re-record → remove demo branches, F3, F6.
5. A4 only after the owner decides, using the A3 numbers. A5–A7 if time allows.
6. A8 (multi-city) last, as a stretch goal with its own plan.

Verification for every task: `.venv/Scripts/python -m pytest`, `scripts/run_trip.py --demo` with 0 cache
misses, and `cd travel-planner-ui && npm test` for UI or graph changes.
