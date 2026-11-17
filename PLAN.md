# Implementation Plan: Multi-Agent Travel Planner

Spec: [PROJECT.md](PROJECT.md). This file says **in what order** we build it and **how we know each phase is done**. Tick boxes as we go. Each phase ends with something runnable.

## Conventions (apply in every phase)

- **Layout:** repo root is the ADK "agents dir"; `travel_planner/` is the agent package (`__init__.py` does `from . import agent`; `agent.py` exposes `root_agent`). Run `adk web` from the repo root.
- **Config:** `travel_planner/config.py` loads `.env` (python-dotenv), builds the shared `MODEL = LiteLlm(...)` and exposes paths and the User-Agent. No keys in code.
- **Tool wrapper contract:** returns a dict matching the shared Pydantic schema (`model_dump()`) wrapped as `{"status": "ok", "results": [...]}`, or `{"status": "error", "reason": ...}`. It never raises to the agent and never returns raw API JSON. Every price carries `price_source`.
- **HTTP + cache:** all REST calls go through one helper (`tools/_http.py`). It uses an async httpx client, sets the User-Agent, keeps per-host rate limits, and caches each response on disk under a SHA-256 of (method, url, params, body). Setting `OFFLINE=1` turns a cache miss into an error instead of a network call. The same switch drives offline tests and the replayable demo.
- **State:** each agent has a unique `output_key`. Instructions read state with `{key}`, or `{key?}` when the key may be missing. Agents never read another parallel branch's key.
- **Grounding rule in every research and planning prompt:** every place and every price must come from a tool result and carry its source URL.
- **Encoding:** every file read and write uses `encoding="utf-8"`; the model's output contains characters that Windows cp1252 cannot encode.
- **Tests:** `pytest` runs offline against fixtures in `tests/fixtures/cache/`. `pytest -m live` hits the real APIs and re-records fixtures.

---

## Phase 0 — Skeleton and model smoke test  *(spec step 1)*
**Goal:** prove the university endpoint does tool calling through ADK, and prove the orchestration features we rely on before we build on them.

Files: `requirements.txt`, `.gitignore`, `.env.example`, `travel_planner/{__init__,agent,config}.py`, `scratch/` (spike agents, deleted later)

- [x] `requirements.txt`: `google-adk[mcp]`, `litellm`, `httpx`, `pydantic`, `python-dotenv`, `tzdata` (Windows needs it for zoneinfo), `markdown` (added in Phase 3), `fast-flights`, `pytest`, `pytest-asyncio`
- [x] `.gitignore`: `.env`, `.venv/`, `__pycache__/`, `cache/*` (except `cache/demo/`), `output/`, `.idea/workspace.xml`
- [x] `.env.example` with `LEHMUS_*` variables (the university API is Lehmus AI); local `.env` created
- [x] `config.py` with `MODEL`; if you get *Not Found*, check that `api_base` ends in `/v1`
- [x] Spike 1: one `LlmAgent` with a dummy `get_time(city)` tool. Confirm in `adk web` that the tool is called and its result is used.
- [x] Spike 2: root `LlmAgent` → `transfer_to_agent` → a `SequentialAgent` of two dummy agents. Confirm the handover works; otherwise switch to the `AgentTool` fallback.
- [x] Spike 3: a `ParallelAgent` of two LiteLlm agents with different `output_key`s, plus a `LoopAgent` that ends through `exit_loop` (`tool_context.actions.escalate = True`)
- [x] ~~If tool calls misbehave, retry with Qwen3.8-27B~~ Not needed: GPT OSS 120B handles tool calls natively (see Decisions log)

**Done when:** all three spikes run in `adk web` and the event trace shows tool calls, the transfer, the parallel branches and the loop exit.

**Status: done (2026-11-17).** `scratch/run_spikes.py` checks all of this headlessly: 7/7 checks passed on 3 runs with GPT OSS 120B (~10 s per toy pipeline run; Gemma4 26B was ~3 s). Spikes 2 + 3 are combined in `scratch/spike_pipeline/` (concierge → Sequential[Parallel, writer, Loop]), the same shape as the real planner.

## Phase 1 — Accounts and environment  *(spec step 2; your actions, can overlap Phase 0)*
- [x] Install Node.js 18+ and check that `node -v` and `npx -v` work in PowerShell
- [x] Sign up for Tavily, OpenTripMap and the liteAPI **sandbox** key, and fill `.env`
- [x] Optional: SerpApi (flight fallback and hotel prices; free plan, no card, ~250 searches/month; ~6 used while building). Foursquare key added but not used (see Decisions log)
- [x] Set `CONTACT_EMAIL` in `.env` (`APP_USER_AGENT` defaults to `TripPlannerCourseProject/0.1`)
- [x] Check whether liteAPI sandbox prices look real (compare 2–3 hotels to a public site). If not, make SerpApi Google Hotels the main hotel price source. *Result (2026-11-17): plausible but consistently too low, so Google Hotels becomes the price source for the budget. May 2027 (10–14 May, 2 adults), checked by hand on Booking.com: Dorma Saldanha liteAPI €383 (+€33 city tax) vs €523; Rossio Plaza €438 (+€33) vs €614; HF Fénix Urban €443 (+€32 fee) vs €550, i.e. 14–29% below Booking.com. Dec 2026 (7–11 Dec) vs Google Hotels: Dorma €279 incl. tax vs Booking.com €288; HF Fénix Urban €314 vs €362, i.e. 3–13% below. liteAPI's rates are mostly non-refundable wholesale rates, and the gap grows for dates further ahead.*
- [ ] Note OpenTripMap's daily limit here (shown in your OpenTripMap dashboard): `____`

**Done when:** `.env` holds every required key and both checks are written down above.

## Phase 2 — Shared foundation
Files: `travel_planner/models.py`, `travel_planner/tools/{_result,_cache,_http}.py`, `pytest.ini`, `tests/{conftest,test_models,test_result,test_http_cache}.py`

- [x] `models.py`: `TripBrief`, `TransportOption`, `StayOption`, `Place` as in PROJECT.md §6
- [x] `_result.py`: `ok(results)` and `error(reason)` helpers, plus a decorator that catches exceptions and returns `error(...)`
- [x] `_http.py` + `_cache.py`: async httpx (one client per request, see Decisions log), User-Agent, retries, disk cache, `OFFLINE` switch, per-host rate limiter (Nominatim at most 1 request/s)
- [x] `pytest.ini`: register the `live` marker and skip it by default (`addopts = -m "not live"`). `conftest.py` sets `OFFLINE=1` and points the cache at `tests/fixtures/cache/` for offline runs.
- [x] Unit tests: cache hit/miss, offline miss → error, the error decorator, model validation

**Done when:** `pytest` passes offline.

**Status: done (2026-11-17).** 35 tests pass offline in under 1 s; `pytest -m live` is opt-in. How to use it in Phase 3:
- HTTP tools call `await _http.get_json(url, namespace="<api>", params=...)` / `post_json(...)`. Retries, User-Agent, rate limits and caching are handled there. Put new per-host limits in `_http.MIN_INTERVAL_S`.
- Non-HTTP sources (fast-flights, MCP results) use `await _cache.cached(namespace, request_dict, produce)`.
- Decorate every tool with `@safe_tool` and return `ok([...models])`. Raise `ToolError("reason")` for expected failures.
- Switches: `OFFLINE=1` (cache only), `REFRESH_CACHE=1` (re-record), `CACHE_DIR=...`.


## Phase 3 — Function tool wrappers  *(spec step 3)*
Files: `travel_planner/tools/{geo,weather,wiki,search,sights,food,transit,flights,hotels,trip}.py`, `tests/{test_tools_logic,test_tools_recorded}.py`, `tests/fixtures/cache/`

Each tool gets its own module in `travel_planner/tools/`, an offline test plus a `@pytest.mark.live` test, and recorded fixtures. Keep the signatures **small and flat** (strings, numbers, short lists), which makes tool calling on GPT OSS more reliable.

**3a. Geo and weather**
- [x] `geocode_place` (Nominatim): name → lat/lon/country, cached, rate-limited
- [x] `get_forecast` (Open-Meteo): daily rain/temperature; beyond ~2 weeks it uses the same dates last year (archive API), labelled "typical"
- [x] `cluster_by_distance` (own code): haversine grouping of places into N day clusters

**3b. Culture**
- [x] `find_sights` (OpenTripMap): radius search filtered by interests → `Place`
- [x] `wiki_summary` (Wikipedia query API, with search fallback)
- [x] `wikivoyage_section(page, section)`: "See", "Eat" and so on; parses listing templates into `Place`s and returns `district_pages`
- [x] `web_search` (Tavily): returns the top results with URLs; used for hours, tickets and prices

**3c. Food**
- [x] `find_restaurants` (Overpass): restaurants/cafés near given coordinates, with cuisine tags → `Place`
- [x] ~~Optional: `foursquare_search` (ratings)~~ Skipped: ratings are paid Premium fields (see Decisions log)

**3d. Transport**
- [x] `plan_public_transport` (Transitous MOTIS `/plan`) → `TransportOption` with `price_eur=None`
- [x] `search_google_flights` (fast-flights), used as a fallback. From EU networks it gets Google's consent page, so it returns an error plus a Google Flights search link
- [x] Optional: `search_serpapi_flights`, the second fallback (SerpApi Google Flights; not blocked by the consent page; prices are the total for all adults)

**3e. Stay**
- [x] `search_hotel_rates` (liteAPI sandbox) → `StayOption`
- [x] Optional: `search_google_hotels` (SerpApi): Google Hotels prices, rating 0–5, `sort_by` and `min_rating` filters
- [x] `stay_deep_links`: Airbnb and Booking.com search URLs for the city and dates (the last-resort fallback)

**3f. Own code**
- [x] `save_trip_brief(...)`: validates with `TripBrief` and writes `tool_context.state["trip_brief"]`
- [x] `calculate_trip_cost(...)`: totals per category vs budget, per person and overall
- [x] `save_guide(markdown, slug)`: writes `output/<slug>.md` plus an `.html` version and appends the attribution footer (OSM, Open-Meteo, Transitous)

**Done when:** `pytest` (offline) and `pytest -m live` both pass for every tool.

**Status: done (2026-11-17), including the optional SerpApi tools (17 tools).** 89 tests pass offline in under 1 s; the 14 live tests pass against the real APIs (~1 min, mostly waiting on Overpass). Recorded fixtures: 356 KB, with no keys or email inside.
- Tools live in `travel_planner/tools/` and are exported as `ALL_TOOLS` from `tools/__init__.py`. A test checks that every tool's declaration documents all of its parameters.
- `fetch(..., slim=fn)` shrinks big replies before caching (liteAPI went from 19 MB to 40 KB). Use it for any new verbose API.
- Re-recording: `tests/test_tools_recorded.py` pins dates (`TRIP_*`, `FERRY_DAY`). Move them forward once they are in the past.
- SerpApi has ~250 free searches a month: run `pytest -m live -k serpapi` only when needed (2 searches per run); replies are cached 12 h (flights) / 24 h (hotels).
- Overpass is slow (17–37 s) and sometimes refuses requests. The food agent should fall back to Wikivoyage "Eat" listings (the tool's error message says so).


## Phase 4 — MCP connections  *(spec step 4)*
Files: `travel_planner/tools/mcp.py`, `scripts/list_mcp_tools.py`, `tests/test_mcp.py`, `scratch/mcp_spike/` + `scratch/run_mcp_spike.py` (spike, deleted in Phase 10)

- [x] Kiwi `McpToolset` (`StreamableHTTPConnectionParams("https://mcp.kiwi.com")`, `tool_filter=["search-flight"]`)
- [x] Airbnb `McpToolset` over stdio: `npx.cmd` on Windows, `npx` elsewhere; package pinned to `@openbnb/mcp-server-airbnb@0.3.0`; started with `--ignore-robots-txt` (see Decisions log; `AIRBNB_IGNORE_ROBOTS_TXT=0` turns it off)
- [x] Check import paths and parameter names against the installed ADK version (ADK 2.14.1, mcp 2.3.0: `McpToolset`, `StdioConnectionParams(server_params=..., timeout=...)`; the tool spec field is `input_schema`)
- [x] `scripts/list_mcp_tools.py` prints each server's tools (`--schema` for the input schemas); works offline from the cache
- [x] MCP caching: `CachedMcpToolset` caches each server's **tool list** on disk; the agent callbacks `mcp_cache_before` / `mcp_cache_after` cache **tool results** and replay them when `OFFLINE=1`
- [x] Extra: the after-callback turns raw MCP results into our shared models (`TransportOption` for Kiwi, `StayOption` for Airbnb), so MCP and function tools look the same to the agents

**Done when:** the script lists the tools of both servers, and a tiny test agent returns real flights and listings in `adk web`.

**Status: done (2026-11-17).** `scratch/run_mcp_spike.py` (GPT OSS 120B + both servers + callbacks) passes 4/4 live (~20 s) and 4/4 replayed with `OFFLINE=1` (no MCP server started). The same agent runs in the browser with `adk web scratch` → `mcp_spike`. 104 offline tests pass; the 2 new live tests record one real call per server. Fixtures: 440 KB.
- **Every agent that uses an MCP toolset must register** `before_tool_callback=mcp_cache_before, after_tool_callback=mcp_cache_after`. Without them, results are raw (60 KB for Kiwi) and nothing is cached.
- Kiwi results: 60 KB → 5 KB after normalising. `price_eur` is the total for all passengers. `details` flags **airport changes during layovers** (e.g. STN→LTN overnight), likely self-transfers and missing cabin bags. The flight agent's prompt must say these warnings have to be shown.
- Airbnb searches a whole region (Lisbon district reaches 50 km out). Listings more than 15 km from `trip_brief.destination_lat/lon` are dropped, and `details` gives the distance. Search a neighbourhood ("Alfama, Lisbon") to compare areas.
- Our own tool descriptions replace the servers' ones (`DESCRIPTIONS` in `mcp.py`). Kiwi's original told the model how to lay out the raw results and to "end with a fun fact", which GPT OSS obeyed.
- The model sometimes passes made-up arguments (e.g. `tripType`); Kiwi ignores them.

## Phase 5 — Research agents, one at a time  *(spec step 5)*
Files: `travel_planner/agents/{__init__,_common,prompts,output,culture,food,transport,stay}.py`, `tests/test_agents.py`, `scratch/dev_agent.py` (harness)

Each agent reads `{trip_brief}` and writes its own key (PROJECT.md §6). Its output is a JSON object in the shared schema, plus short notes. Agents that call tools do **not** use `output_schema`; the format is enforced in the instruction and checked by `agents/output.py`.

- [x] `culture_agent` → `culture_findings` (OpenTripMap + Wikivoyage "See" incl. district pages + Wikipedia + Tavily; falls back to Wikivoyage alone)
- [x] `food_agent` → `food_findings` (Tavily for dishes + Wikivoyage "Eat" + Overpass; budget tiers; falls back to Wikivoyage/web)
- [x] `flight_searcher` → `flight_options` (Kiwi, then fast-flights, then SerpApi; MCP cache callbacks registered; warnings kept in `details`)
- [x] `ground_searcher` → `ground_options` (Transitous + Tavily for prices, tagged `web_estimate`; Tavily only as fallback; says when ground travel is impractical)
- [x] `airbnb_searcher` → `airbnb_options` (Airbnb MCP city + 1–2 neighbourhoods, then deep link; MCP cache callbacks registered)
- [x] `hotel_searcher` → `hotel_options` (Google Hotels via SerpApi + liteAPI with a "may be below public prices" note; liteAPI alone if SerpApi fails; then deep link)
- [x] A dev harness (`scratch/dev_agent.py <agent|all> [--fail tool,...] [--brief lisbon|tallinn]`) runs any single agent as root with a pre-filled `trip_brief`, forces tools to fail, validates the output against the shared models and checks that every URL came from a tool result

**Done when:** each agent, run alone on the Lisbon brief, returns grounded results with URLs, and still returns something when its main tool is forced to fail.

**Status: done (2026-11-17).** All 6 agents pass the harness on the Lisbon brief (valid schema, items, every URL found in a tool result), and all 6 pass with their main source forced to fail (culture without OpenTripMap, food without Overpass, flights without Kiwi → SerpApi, ground without Transitous → Tavily, Airbnb → deep links, hotels without Google Hotels → liteAPI, and with both down → deep links). The ground searcher also passes on the Tallinn brief (tram → Tallink ferry → bus, fares as web estimates). 121 offline tests pass.
- Agents are built by **factories** (`RESEARCH_AGENTS` in `agents/__init__.py`): an ADK agent can only have one parent, and the harness and the pipeline need separate instances.
- `include_contents="none"`: each agent sees only its instruction (with the brief) and its own tool calls, not the chat or the other branches.
- Output shape: `{"results"|"outbound"/"return": [...], "fallback_links": [...], "notes": "..."}`. `fallback_links` exists because the model left search links out of free-text notes.
- Typical run times (live model, warm cache): flights 30–45 s, hotels 20–30 s, Airbnb 50 s, ground 20–30 s, food 40–70 s, culture 65–125 s. In the parallel research team the slowest branch (culture) sets the pace.
- The model sometimes exceeds the stated tool budget (culture made 10–15 calls for a budget of 12, and more web searches than allowed). Harmless now; if runs get slow, enforce the budget with a before-tool callback (Phase 9).
- GPT OSS once called the Kiwi tool `search_flight` instead of `search-flight` (ADK answered "tool not found" and it corrected itself); the instruction now names it exactly.

### Phase 5b — Fewer web searches (2026-11-17)
Research on free alternatives to Tavily: `reports/Travel data APIs beyond web search.md` (notes in `research_notes/`). Free, no-card sources replace web search for dishes and museum hours; fares, ticket prices and restaurant ratings still need web search as the fallback.
- [x] `local_dishes(country)` (`tools/dishes.py`): Wikivoyage country "Eat" text + its highlighted dish names, plus Wikidata dishes via SPARQL (QLever mirror first, official WDQS as fallback), drinks/brands/restaurants filtered out
- [x] `find_sight_hours(lat, lon, radius_km)` (`tools/sight_hours.py`): OpenStreetMap `opening_hours` / `fee` / `charge` for museums, attractions and historic sights; QLever's OSM SPARQL endpoint first (~20 s), Overpass as fallback (Overpass was down for hours during the build)
- [x] Food agent reads Wikivoyage district "Eat" pages (budget tiers, coordinates) before anything else; `wikivoyage_section` now returns the city's district pages even when the requested section does not link them
- Result: the food agent makes 0 web searches on the Lisbon brief (was 2–4); the culture agent fills hours/tickets from OSM and searches the web at most twice. 132 offline tests, 18 live tests.

## Phase 6 — Comparers and teams  *(spec step 6)*
Files: `travel_planner/agents/{comparers,teams}.py`; `agents/output.py` (nested schemas); tests in `tests/test_agents.py`; harness runs teams

- [x] `transport_comparer` → `transport_summary`: per direction `cheapest`, `fastest` and up to 5 `ranked` options (copied unchanged), plus `recommendation` and `total_cheapest_eur`
- [x] `stay_comparer` → `stay_summary`: `by_area` (best Airbnb and best hotel per neighbourhood, min price per night), `cheapest`, `best_value`, `top_rated` (ratings normalised to /5), `recommendation`
- [x] `transport_team` = Sequential[Parallel[flight, ground], transport_comparer]
- [x] `stay_team` = Sequential[Parallel[airbnb, hotel], stay_comparer]
- [x] `research_team` = Parallel[transport_team, stay_team, culture_agent, food_agent]

**Done when:** running `research_team` on a pre-filled brief fills all eight research keys, and the trace shows the branches running in parallel.

**Status: done (2026-11-17).** `scratch/dev_agent.py research_team` (Lisbon) fills all 8 keys with valid, grounded output in 136 s; the six searchers all start within 4–6 s of each other (timeline printed by the harness). On the Tallinn brief, `transport_team` picks the ferry as cheapest (Tallink €34 out, Viking Line €60 back, web estimates) and the Finnair flight as fastest (30 min, €163 live). 132 offline tests pass.
- Comparers have no tools and read only their own team's searchers (`{flight_options?}`; `?` = empty if missing), so parallel branches never read each other.
- The slowest branch is the culture agent (~130 s). The comparers take 30–70 s each because they copy whole option objects; if Phase 7/9 needs speed, let them reference options by index or merge them into the searchers (PROJECT.md design note).
- ADK 2.x deprecates `SequentialAgent`/`ParallelAgent` in favour of `Workflow`, which cannot yet be an LlmAgent sub-agent; we keep them (Decisions log).

## Phase 7 — End-to-end pipeline without the loop  *(spec step 7)*
Files: `travel_planner/agents/{concierge,itinerary_planner,guide_writer,pipeline}.py`, `travel_planner/agent.py`

- [ ] `concierge` (root): asks for missing fields, geocodes origin and destination, confirms with the user, calls `save_trip_brief`, transfers to `planning_pipeline` (or uses the Phase 0 fallback)
- [ ] `itinerary_planner` → `itinerary`: uses `get_forecast` and `cluster_by_distance`; puts nearby places on the same day and indoor sights on rainy days; supports 3, 5 or 7 days
- [ ] `guide_writer` → `final_guide`: transport, stay comparison, daily plan with history snippets and food stops, cost table, links and price-source markers; calls `save_guide`
- [ ] `planning_pipeline` = Sequential[research_team, itinerary_planner, guide_writer]

**Done when:** the demo request in `adk web` produces `output/lisbon-<dates>.md` and `.html`.

## Phase 8 — Review loop  *(spec step 8)*
Files: `travel_planner/agents/{budget_critic,itinerary_reviser}.py`; update `pipeline.py`

- [ ] `budget_critic` → `critique`: calls `calculate_trip_cost`, flags over-budget totals, packed days, long walks and **ungrounded places or prices**. Ends with the literal verdict `APPROVED` or `REVISE`.
- [ ] `itinerary_reviser` → `itinerary`: if the critique says `APPROVED`, calls `exit_loop`; otherwise rewrites the plan
- [ ] `review_loop` = Loop[budget_critic, itinerary_reviser], `max_iterations=3`, inserted between the planner and the writer
- [ ] Test that a deliberately over-budget brief triggers at least one revision, and that the loop never runs more than 3 rounds

**Done when:** the trace shows critique → revise → approve, and the guide reflects the revised plan.

## Phase 9 — Hardening  *(spec step 9)*
- [ ] Run 4 different trips:
  - Lisbon, 5 days, mid budget (the demo)
  - Tallinn from Helsinki, 3 days, low budget (ferry, which exercises the ground transport path)
  - Rome, 7 days, high budget
  - Kraków from Tampere, 3 days
- [ ] Fix failures: malformed tool calls, empty results, missing state keys, slow runs (merge comparers into searchers if needed)
- [ ] Check the guide: no place or price without a source, and `web_estimate` prices clearly marked
- [ ] Record the demo: run Lisbon live with the cache dir set to `cache/demo/`, commit it, then confirm that `OFFLINE=1 adk web` replays the whole demo

**Done when:** all 4 trips finish without errors and the offline demo replay works.

## Phase 10 — Report and demo  *(spec step 10)*
- [ ] `README.md`: what it does, setup (Python, Node, `.env`, sign-ups), how to run (`adk web`, offline demo mode), tests
- [ ] Architecture diagram (Mermaid in the README): agent tree, state keys, tools per agent
- [ ] Requirements checklist mapped to the code (PROJECT.md §7)
- [ ] Demo video or live-run script following the PROJECT.md demo scenario
- [ ] Delete `scratch/` and do a final `pytest` run

**Done when:** a fresh clone, following the README, runs the offline demo without errors.

---

## Decisions log
Record choices made during the build (model switch, transfer vs AgentTool, hotel price source, …).

| Date | Decision | Why |
| --- | --- | --- |
| 2026-11-17 | LLM is **GPT OSS 120B** (`azsydsttjnlbfjbgqnwd`) on Lehmus AI, as in the spec | Native `tool_calls` work, both raw and through LiteLLM. Fallbacks on the same key: Gemma4 26B MoE (`wnofibsjlomanbprgbdp`, ~3× faster, also passes all spikes), Qwen3.8-27B, Gemma4 31B, Qwen3.6-35B-A3B |
| 2026-11-17 | Env vars are `LEHMUS_API_BASE/KEY/MODEL_ID` instead of the spec's `UNI_*` | Matches the provider's name; `config.py` reads them |
| 2026-11-17 | Concierge → pipeline uses plain `transfer_to_agent` with the `SequentialAgent` as a sub-agent | Works in ADK 2.14.1; no `AgentTool` fallback needed |
| 2026-11-17 | Use ADK's built-in `google.adk.tools.exit_loop` | It sets `escalate` and `skip_summarization`, so the reviser's previous `itinerary` output is kept |
| 2026-11-17 | Always use UTF-8 for text I/O (`encoding="utf-8"` on every file write; `sys.stdout.reconfigure(encoding="utf-8")` in scripts) | GPT OSS emits typographic Unicode (‑,  , —). Windows defaults to cp1252 and crashes on it |
| 2026-11-17 | `TripBrief` dates are `datetime.date` (ISO strings in and out); added optional `origin_lat/lon`, `destination_lat/lon`, `destination_country`; models reject unknown fields | Validates dates early (end after start); the concierge stores the geocoded coordinates once so research tools need no extra lookups; unknown fields catch model typos |
| 2026-11-17 | The cache lives in its own module (`_cache.py`), apart from `_http.py`; secrets (`apikey`, `key`, `token`, …) are left out of cache keys and files; headers are never stored | MCP and fast-flights reuse the same cache; fixtures are safe to commit and work with anyone's key |
| 2026-11-17 | No shared `httpx.AsyncClient` or `asyncio.Lock`; one client per request, and a rate limiter built on a thread lock plus `asyncio.sleep` | Both would be tied to one event loop, and pytest creates a loop per test. At our request volume, a client per request costs nothing noticeable |
| 2026-11-17 | `config.py` warns (instead of failing) when `LEHMUS_*` is missing; the cache settings are read at call time | Offline tests run on a fresh clone with no `.env`; tests can switch `OFFLINE` and `CACHE_DIR` per test |
| 2026-11-17 | Model tweaks: `Place.lat/lon` optional; `TransportOption.price_source` required only when there is a price; `details` text field on `TransportOption` and `StayOption` | Wikivoyage and web results often lack coordinates; Transitous gives schedules without prices; leg summaries, stars, board and city-tax notes need a place to go |
| 2026-11-17 | User-Agent includes `CONTACT_EMAIL` (set in `.env`) | Wikimedia answers 403 to clients without contact info; the Transitous and Nominatim policies ask for it too |
| 2026-11-17 | **Foursquare skipped** | Ratings and prices are paid "Premium" fields (the free account gets 429 "no API credits"); basic search adds little over Overpass |
| 2026-11-17 | Google Flights (fast-flights) **reports the EU cookie-consent page** and returns a search link instead of results | From EU networks Google serves a consent wall; getting around it was blocked by the permission system and is not something to automate. Kiwi MCP stays the main flight source; SerpApi (optional, free 250/month) is the real second fallback |
| 2026-11-17 | Overpass: 60 s timeout, no retry on timeout, then the maps.mail.ru mirror; results cached 30 days | overpass-api.de takes 17–35 s per query from here and answers 406 after bursts |
| 2026-11-17 | Weather beyond ~2 weeks = the same dates in an earlier year (Open-Meteo archive), labelled "typical" | The forecast API only reaches 16 days ahead; trips are usually planned months before |
| 2026-11-17 | Wikivoyage tool parses `{{see}}`/`{{eat}}` listing templates; budget tier from Budget/Mid-range/Splurge headings, else from the price (<€15 low, ≤€35 mid) | Gives structured, coordinate-tagged places with hours and prices; big cities point to district pages (`district_pages`) |
| 2026-11-17 | liteAPI rates are searched by coordinates + radius; neighbourhood from Nominatim reverse geocoding; booking link = Booking.com search for the hotel name | liteAPI has no neighbourhood field or public booking link. Sandbox prices look realistic (e.g. 3★ Rossio Plaza €109/night in May) but are flagged `liteapi (sandbox)` |
| 2026-11-17 | SerpApi tools live in `tools/serpapi.py`; flight price = total for all adults (per-person in `details`); hotel link = Booking.com search for the hotel name; `lowest_price` should be combined with `min_rating` | Checked live: 2 adults cost €357 vs €169 for 1. Google Hotels has no booking link. Sorting by price alone returns hostels and camping rated 2.5/5 |
| 2026-11-17 | **Google Hotels (SerpApi) is the hotel price source for the budget**; liteAPI gives the wider hotel list, the fallback when SerpApi fails, and a "may be cheaper" hint. liteAPI prices are shown with their excluded city tax and marked as possibly lower than public sites | Manual check: liteAPI is 3–13% below Booking.com in December and 14–29% below in May 2027; budgeting on it would understate the trip cost. One Google Hotels search per trip fits the free 250/month |
| 2026-11-17 | **Airbnb MCP runs with `--ignore-robots-txt`** (project owner's decision) | Airbnb's robots.txt disallows all search pages (`Disallow: /s/*/*`), so the server returns nothing otherwise. Low volume and cached; configurable with `AIRBNB_IGNORE_ROBOTS_TXT`. The alternative checked was Google holiday rentals via SerpApi |
| 2026-11-17 | MCP results are cached and normalised by agent callbacks; tool lists are cached by a `McpToolset` subclass that fills ADK's in-memory tool-list cache from our disk cache | Offline replay must work without reaching Kiwi or starting npx; normalising gives the comparers one schema and cuts tokens ~10×. ADK runs after-callbacks even when a before-callback answered, so the after-callback skips results already in our shape |
| 2026-11-17 | Our own descriptions for `search-flight` and `airbnb_search`; Airbnb package pinned to 0.3.0 | The server descriptions describe the raw result format and contain display instructions; a scraping server can change behaviour between versions |
| 2026-11-17 | Research agents: factories, `include_contents="none"`, temperature 0.2, JSON object output checked by `agents/output.py` (schema + URL grounding) | One agent instance per parent; no cross-talk between parallel branches; parse-and-validate makes "grounded" testable instead of eyeballed |
| 2026-11-17 | Dishes from Wikivoyage + Wikidata (`local_dishes`); sight hours/fees from OSM via QLever (`find_sight_hours`); web search stays the fallback for fares, ticket prices and ratings | Deep research found no free, card-less, ToS-clean API for ferry/rail fares (Tallink, Viking Line, VR, CP have none; Ferryhopper MCP has no Baltic results; Virail answered 2 of 9 test searches) or restaurant ratings (Google needs billing; Tripadvisor's free API closed 31 Aug 2026; Yelp and Foursquare are paid) |
| 2026-11-17 | Keep `SequentialAgent` / `ParallelAgent` (deprecated in ADK 2.x in favour of `Workflow`) | ADK: "Workflow cannot yet be used as an LlmAgent sub-agent", and the concierge transfers to the pipeline as its sub-agent. The warning is filtered in `pytest.ini` |
| 2026-11-17 | Python 3.13 venv in `.venv/` | 3.14 is too new for some wheels; 3.13 meets the 3.11+ requirement |
