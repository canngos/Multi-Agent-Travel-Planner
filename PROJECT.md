# Multi-Agent Travel Planner — AI Engineering Final Project

A multi-agent system that turns one trip request into a complete travel plan. The plan covers transport, stays, sights with short histories, food by budget, and a day-by-day itinerary. Built solo with Google ADK (Python) for the AI Engineering course, and meant to be used for real trips afterwards.

> **For Claude Code:** this file is the project spec. Follow the [Build plan](#build-plan) in order; each step should end with something runnable. Never hard-code API keys: read them from `.env`. Every place and price shown to the user must come from a tool result, never from model memory.

---

## 1. Overview

**Problem.** Planning one trip by hand takes 4–5 days: researching sights and their history, finding traditional food and restaurants for the budget, comparing Airbnb with hotels, comparing flights, buses and ferries, then fitting it all into a 3- or 7-day schedule.

**Goal.** Cut that to roughly an hour of reviewing a draft. The system plans and compares; it never books. Every option comes with a link so the final booking stays with the user.

**Example request:** *"5 days in Lisbon from Helsinki, 10–14 May, 2 people, mid budget, we love history and street food."*

### Scope

| | MVP (graded) | Stretch (after the course) |
| --- | --- | --- |
| Destinations | One city per trip | Multi-city routes |
| Origin | Tampere/Helsinki by default, changeable | Any origin |
| Trip length | 3, 5 or 7 days | Any length |
| Interface | `adk web` chat UI | Simple web front end, map in the guide |
| Output | Markdown/HTML trip guide saved to file | Saved traveller profiles |

### Course requirements

- At least three different agents
- A multi-agent orchestration pattern (Lecture 4)
- At least one tool call
- Fully agentic; no code reused from assignments 2 and 3
- Executable code that runs without errors

---

## 2. Tech stack and LLM setup

Python 3.11+ with Google ADK, connected to the university's OpenAI-compatible endpoint through LiteLLM. Tool calling on that endpoint is expected to work and gets confirmed in build step 1.

| Layer | Choice |
| --- | --- |
| Agent framework | Google ADK, installed with `pip install "google-adk[mcp]"` |
| LLM | University API, GPT OSS 120B (API model id `azsydsttjnlbfjbgqnwd`) |
| LLM adapter | ADK `LiteLlm` wrapper, model string `openai/azsydsttjnlbfjbgqnwd` |
| LLM fallback | Qwen3.8-27B on the same endpoint, if GPT OSS tool calls misbehave |
| MCP clients | ADK `McpToolset`: `StreamableHTTPConnectionParams` for remote servers, `StdioConnectionParams` for `npx` servers |
| HTTP calls | `httpx` (async) inside custom function tools |
| Data models | Pydantic |
| Config | `.env` file for keys and the API base URL; never committed |
| Runtime extras | Node.js 18+ (for the Airbnb MCP server via `npx`) |
| UI | `adk web` (chat UI plus event trace for the demo) |

Model setup, shared by every agent:

```python
from google.adk.models.lite_llm import LiteLlm
import os

MODEL = LiteLlm(
    model=f"openai/{os.environ['UNI_MODEL_ID']}",
    api_base=os.environ["UNI_API_BASE"],   # ends with /v1
    api_key=os.environ["UNI_API_KEY"],
)
```

If the endpoint returns *Not Found*, check that `api_base` ends with `/v1`.

---

## 3. Agent architecture

Thirteen LLM agents in one pipeline, using four orchestration patterns: **coordinator/delegation**, **sequential**, **parallel** (nested) and a **generator–critic loop**. Agents pass results through ADK session state: each writes to its own `output_key`, and later agents read those keys in their instructions.

### Flow

1. **Concierge** (root) chats with the user until the trip brief is complete, saves it with a tool, then hands over to the planning pipeline.
2. **Research team** runs four branches in parallel: Transport, Stay, Culture & History, Food & Drink. Transport and Stay are small teams of their own: two searchers in parallel, then a comparer that merges their results.
3. **Itinerary Planner** builds the day-by-day plan from all research results and the weather forecast.
4. **Review loop** (max 3 rounds): the Budget Critic checks cost, pace and distances; the Reviser fixes the plan, or ends the loop when the critic approves.
5. **Guide Writer** turns the approved plan into a trip guide and saves it to a file.

```
concierge (LlmAgent, root)
   │  transfer_to_agent
   ▼
planning_pipeline (SequentialAgent)
   ├─ research_team (ParallelAgent)
   │     ├─ transport_team (SequentialAgent)
   │     │     ├─ ParallelAgent: flight_searcher ∥ ground_searcher
   │     │     └─ transport_comparer
   │     ├─ stay_team (SequentialAgent)
   │     │     ├─ ParallelAgent: airbnb_searcher ∥ hotel_searcher
   │     │     └─ stay_comparer
   │     ├─ culture_agent
   │     └─ food_agent
   ├─ itinerary_planner
   ├─ review_loop (LoopAgent, max_iterations=3)
   │     ├─ budget_critic
   │     └─ itinerary_reviser   (calls exit_loop when approved)
   └─ guide_writer
```

### Agents

| # | Agent | ADK type | Job | Writes to state |
| --- | --- | --- | --- | --- |
| 1 | `concierge` | LlmAgent (root) | Asks for missing details (destination, dates, travellers, budget, interests), confirms, saves the brief, transfers to the pipeline | `trip_brief` |
| 2 | `flight_searcher` | LlmAgent | Flights from origin to destination and back | `flight_options` |
| 3 | `ground_searcher` | LlmAgent | Trains, buses, ferries; schedules plus prices | `ground_options` |
| 4 | `transport_comparer` | LlmAgent | Merges both lists, ranks by price and time, picks a cheapest and a fastest option | `transport_summary` |
| 5 | `airbnb_searcher` | LlmAgent | Airbnb listings in the best areas for the budget | `airbnb_options` |
| 6 | `hotel_searcher` | LlmAgent | Hotel rates for the same areas and dates | `hotel_options` |
| 7 | `stay_comparer` | LlmAgent | Airbnb vs hotels per neighbourhood: price per night, total, rating | `stay_summary` |
| 8 | `culture_agent` | LlmAgent | Sights, a short history of each, opening hours and tickets | `culture_findings` |
| 9 | `food_agent` | LlmAgent | Traditional dishes, restaurants per budget tier near the sights | `food_findings` |
| 10 | `itinerary_planner` | LlmAgent | Day-by-day plan; nearby places on the same day, indoor sights on rainy days | `itinerary` |
| 11 | `budget_critic` | LlmAgent | Totals per category vs budget; flags packed days and long walks | `critique` |
| 12 | `itinerary_reviser` | LlmAgent | Applies the critique, or calls `exit_loop` when the critique says approved | `itinerary` |
| 13 | `guide_writer` | LlmAgent | Writes the final guide and saves it | `final_guide` |

Workflow agents that hold them together (no LLM): `planning_pipeline` (SequentialAgent), `research_team` (ParallelAgent), `transport_team` and `stay_team` (SequentialAgent, each wrapping a ParallelAgent of two searchers plus a comparer), `review_loop` (LoopAgent, `max_iterations=3`).

### Design notes

- Parallel agents share one session, so every agent writes to a distinct key. Nothing in a parallel branch reads another branch's output.
- The concierge → pipeline handover uses ADK's `transfer_to_agent` with `planning_pipeline` as a sub-agent. Verify this early; the fallback is wrapping the pipeline in an `AgentTool` the concierge calls.
- Thirteen LLM agents means about 15–20 model calls per trip plus tool calls. If runs get slow, the first merge candidates are the two comparers into their searchers.

---

## 4. Tools and data sources

Every data need has a free source, and most agents **combine** several tools because each covers something the others miss. A separate **fallback** layer only runs when a source errors or returns nothing. Two sources are MCP servers (Kiwi, Airbnb); the rest are Python function tools wrapping REST APIs.

| Agent | Tools it combines | What the merge gives | Fallback |
| --- | --- | --- | --- |
| `concierge` | `save_trip_brief` (own code) + `geocode_place` (Nominatim) | Validated destination and origin with coordinates | Ask the user to rephrase |
| `flight_searcher` | Kiwi MCP `search-flight` | Live flight prices, times, booking links | `search_google_flights` (fast-flights library), then SerpApi Google Flights |
| `ground_searcher` | `plan_public_transport` (Transitous) + `web_search` (Tavily) | Train/bus/ferry schedules from Transitous, ticket prices from operator pages | Tavily only |
| `airbnb_searcher` | Airbnb MCP `airbnb_search`, `airbnb_listing_details` | Listings with nightly price, rating, link | Airbnb search deep link |
| `hotel_searcher` | `search_hotel_rates` (liteAPI) + optional `search_google_hotels` (SerpApi) | Hotel rates for the same dates and areas | Booking.com search deep link |
| `culture_agent` | `find_sights` (OpenTripMap) + `wiki_summary` (Wikipedia) + `wikivoyage_section` (Wikivoyage) + `web_search` | Sights with location, short history, traveller tips, hours and tickets | Wikivoyage "See" section only |
| `food_agent` | `find_restaurants` (OSM Overpass) + `wikivoyage_section` ("Eat") + `web_search` + optional `foursquare_search` | Traditional dishes, restaurants per budget tier, near the sights | Overpass plus model knowledge |
| `itinerary_planner` | `get_forecast` (Open-Meteo) + `cluster_by_distance` (own code) | Nearby places grouped per day, indoor sights on rainy days | Plan without the forecast |
| `budget_critic` | `calculate_trip_cost` (own code) | Totals per category vs the budget | n/a |
| `itinerary_reviser` | `exit_loop` (ADK loop-exit pattern) | Ends the review loop on approval | Loop stops at 3 rounds |
| `guide_writer` | `save_guide` (own code) | Markdown and HTML guide in `output/` | n/a |

### Rules for every tool wrapper

- Returns the shared schema for its category (see [Data model](#6-data-model)), never raw API JSON.
- Tags every price with `price_source`: `live` (Kiwi, liteAPI, Airbnb) or `web_estimate` (Tavily extractions).
- Catches its own errors and returns `{"status": "error", "reason": ...}` so the agent can switch to the fallback instead of crashing.
- Caches responses on disk by request hash. This saves free quota and makes the demo trip replayable offline.
- Respects each API's rules: one request per second to Nominatim, a descriptive `User-Agent` on Nominatim, Wikipedia and Transitous, attribution for OSM, Open-Meteo and Transitous in the guide footer.

### MCP connections (sketch)

```python
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams, StdioConnectionParams
from mcp import StdioServerParameters

kiwi_tools = McpToolset(
    connection_params=StreamableHTTPConnectionParams(url="https://mcp.kiwi.com"),
    tool_filter=["search-flight"],
)

airbnb_tools = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="npx", args=["-y", "@openbnb/mcp-server-airbnb"]
        )
    ),
)
```

Check the exact import paths and parameter names against the installed ADK version.

### Source notes

- [Kiwi.com MCP](https://www.kiwi.com/ag/pages/mcp/): official, remote at `https://mcp.kiwi.com`, no sign-in, read-only search with a booking URL per itinerary.
- [Airbnb MCP](https://github.com/openbnb-org/mcp-server-airbnb): open source, runs with `npx -y @openbnb/mcp-server-airbnb`, reads public listing pages. Best-effort: can break if Airbnb changes its pages.
- [Transitous](https://transitous.org/api/): community-run public transport routing (MOTIS 2 API). Free for open-source, non-commercial projects; no prices.
- [fast-flights](https://github.com/AWeirdDev/flights): Google Flights scraper for Python, no key. Fallback only, since Google can change the page.
- Amadeus Self-Service closed on 17 July 2026 and is not an option.

---

## 5. Accounts and registration

Three free sign-ups are required (Tavily, OpenTripMap, liteAPI), two are optional (SerpApi, Foursquare), and everything else needs no account. No credit card is needed for any required service. Put every key in `.env` and add `.env` to `.gitignore`.

### Action needed: sign up for a key

| Service | Required? | What to do | Free allowance | `.env` variable |
| --- | --- | --- | --- | --- |
| University LLM API | Required | Get the API key and base URL from the course or university portal; confirm the model id | Per university terms | `UNI_API_KEY`, `UNI_API_BASE`, `UNI_MODEL_ID` |
| [Tavily](https://app.tavily.com) | Required | Create an account, copy the API key from the dashboard | 1,000 credits/month, no card (1 basic search = 1 credit) | `TAVILY_API_KEY` |
| [OpenTripMap](https://dev.opentripmap.com) | Required | Register on the developer site, request the free API key | Free tier; check the daily limit shown after sign-up | `OPENTRIPMAP_API_KEY` |
| [liteAPI (Nuitée)](https://docs.liteapi.travel/docs/faq) | Required | Create a free account, open Profile in the dashboard, copy the **sandbox** key. Do not add a card; production keys are not needed | Free sandbox | `LITEAPI_KEY` |
| [SerpApi](https://serpapi.com) | Optional | Create an account, copy the key. Used as flight fallback and extra hotel source | 250 searches/month, shared by Flights and Hotels | `SERPAPI_KEY` |
| [Foursquare Places](https://foursquare.com/developers) | Optional | Create a developer account and a project key. Only for restaurant ratings | Free Pro calls; sources disagree on 500 vs 10,000 a month, and one says a card is required. Skip unless needed | `FOURSQUARE_API_KEY` |

### Action needed: no account, but set something up

| Service | What to do |
| --- | --- |
| [Transitous](https://transitous.org/api/) | Send a `User-Agent` on every request with app name, version and a contact email, e.g. `TripPlannerCourseProject/0.1 (your-email)`. The policy is for open-source, non-commercial projects: publish the repo under an open-source licence (light prototyping before publishing is allowed). Link the Transitous sources page in the guide footer. Stay well under 720 `/plan` requests a day. |
| Airbnb MCP | Install Node.js 18+ so `npx` can run the server. No key. |
| Nominatim (OSM geocoding) | Descriptive `User-Agent`, at most 1 request per second, cache results. |

### No action needed

- **Kiwi.com MCP**: remote server, no sign-in.
- **Open-Meteo**: no key; free for non-commercial use; credit it in the guide footer.
- **OSM Overpass**: no key; keep queries small and cache them.
- **Wikipedia and Wikivoyage**: no key; send a descriptive `User-Agent`.
- **fast-flights**: `pip install fast-flights`, no key.

**Deliberately not used:** Google Maps/Places (needs a billing account with a card), Amadeus (closed), Skyscanner and Rome2rio APIs (partner-only or paid).

**Worth checking right after sign-up:** whether liteAPI sandbox rates reflect real prices, and OpenTripMap's daily request limit.

### `.env.example`

```dotenv
# University LLM
UNI_API_BASE=https://<university-endpoint>/v1
UNI_API_KEY=
UNI_MODEL_ID=azsydsttjnlbfjbgqnwd

# Required data sources
TAVILY_API_KEY=
OPENTRIPMAP_API_KEY=
LITEAPI_KEY=

# Optional
SERPAPI_KEY=
FOURSQUARE_API_KEY=

# Used in User-Agent headers (Nominatim, Wikipedia, Transitous)
CONTACT_EMAIL=
APP_USER_AGENT=TripPlannerCourseProject/0.1
```

---

## 6. Data model

Shared Pydantic models let agents compare results from different sources like for like. Tool wrappers return these models; comparer and planner agents read them from session state.

```python
from pydantic import BaseModel
from typing import Literal

class TripBrief(BaseModel):
    origin: str = "Helsinki"
    destination: str
    start_date: str            # ISO, e.g. "2027-05-10"
    end_date: str
    travellers: int = 2
    budget_tier: Literal["low", "mid", "high"] = "mid"
    total_budget_eur: float | None = None
    interests: list[str] = []  # "history", "street food", ...

class TransportOption(BaseModel):
    mode: Literal["flight", "train", "bus", "ferry"]
    operator: str
    depart: str                # ISO datetime, local time
    arrive: str
    duration_min: int
    stops: int
    price_eur: float | None
    price_source: Literal["live", "web_estimate"]
    source: str                # "kiwi", "transitous+tavily", ...
    url: str

class StayOption(BaseModel):
    kind: Literal["airbnb", "hotel"]
    name: str
    area: str                  # neighbourhood
    lat: float
    lon: float
    price_per_night_eur: float
    total_price_eur: float
    rating: float | None
    price_source: Literal["live", "web_estimate"]
    source: str
    url: str

class Place(BaseModel):     # sights and restaurants
    kind: Literal["sight", "restaurant", "cafe", "bar"]
    name: str
    lat: float
    lon: float
    summary: str               # short history, or cuisine and dishes
    budget_tier: Literal["low", "mid", "high"] | None
    hours: str | None
    ticket_eur: float | None
    indoor: bool | None
    source_urls: list[str]
```

### Session state keys

| Key | Written by | Read by |
| --- | --- | --- |
| `trip_brief` | `concierge` (via `save_trip_brief` tool) | every agent |
| `flight_options`, `ground_options` | the two transport searchers | `transport_comparer` |
| `transport_summary` | `transport_comparer` | planner, critic, writer |
| `airbnb_options`, `hotel_options` | the two stay searchers | `stay_comparer` |
| `stay_summary` | `stay_comparer` | planner, critic, writer |
| `culture_findings`, `food_findings` | culture and food agents | planner, writer |
| `itinerary` | planner, then reviser | critic, writer |
| `critique` | `budget_critic` | `itinerary_reviser` |
| `final_guide` | `guide_writer` | user (also saved to `output/`) |

Instructions read state with ADK's `{key}` templating, e.g. `"Compare these options: {flight_options} {ground_options}"`.

---

## 7. Requirements checklist

| Requirement | How the design meets it |
| --- | --- |
| At least 3 different agents | 13 LLM agents with distinct roles, plus 6 workflow agents |
| Multi-agent orchestration pattern | Coordinator/delegation (concierge), sequential (pipeline), parallel (research team, nested in transport and stay), loop (review) |
| At least one tool call | 15+ function tools and 2 MCP servers; every research agent calls tools |
| Fully agentic | Agents choose search parameters, which tools to call, how to rank and when the plan is good enough; no hard-coded trip logic |
| No code from assignments 2 and 3 | New repository written from scratch |
| Executable, no errors | Error-returning tool wrappers, fallbacks per source, response cache, a recorded demo trip, smoke tests |

---

## 8. Risks and mitigations

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| GPT OSS tool calls fail or return malformed JSON through the university endpoint | Low–medium | Test in build step 1; switch to Qwen3.8-27B; keep tool schemas small and flat |
| Airbnb MCP or fast-flights breaks after a site change | Medium | Hotels still compared via liteAPI; Kiwi is the main flight source; deep links as last resort |
| liteAPI sandbox prices are not real | Medium | Check after sign-up; if fake, use SerpApi Google Hotels as the hotel price source |
| Free quota runs out during testing (SerpApi 250, Tavily 1,000) | Medium | Disk cache by request hash; small test trips; demo replays cached results |
| Transitous has no data for a route (common outside Europe) | Medium | Tavily search for operators and schedules |
| Web-extracted prices are wrong | Medium | `price_source: web_estimate` shown in the guide so the user double-checks |
| Parallel agents overwrite each other's state | Low | Distinct `output_key` per agent |
| Runs are slow (15–20 model calls) | Medium | Parallel research; merge comparers into searchers if needed; stream progress in `adk web` |
| Model invents places or prices | Medium | Instructions require every place and price to come from a tool result with a source URL; critic checks this |

---

## Build plan

Build bottom-up: prove the model and each tool alone, then wire agents in, then add the loop and the guide. Each step ends with something runnable.

1. **Model test.** One ADK agent with one dummy tool against the university endpoint. Confirm tool calls work; otherwise switch model.
2. **Accounts.** Sign up for Tavily, OpenTripMap, liteAPI; fill `.env`; check the liteAPI sandbox prices and OpenTripMap limits.
3. **Tool wrappers.** Write each wrapper with its schema, error handling and cache, and a pytest smoke test per tool.
4. **MCP connections.** Connect Kiwi (remote) and Airbnb (`npx`) through `McpToolset`; list their tools.
5. **Research agents.** Build the four branches one at a time, running each alone in `adk web`.
6. **Comparers and teams.** Add the transport and stay teams (parallel searchers plus comparer).
7. **Pipeline.** Concierge, research team, planner and guide writer, end to end without the loop.
8. **Review loop.** Add critic, reviser and `exit_loop`; check it stops by round 3.
9. **Hardening.** Run 3–4 different trips; fix failures; record the demo trip's cache.
10. **Report and demo.** README with setup steps, architecture diagram, demo video or live run.

### Project layout

```
travel_planner/
├── agent.py            # root_agent = concierge (ADK entry point)
├── agents/             # one file per agent and team
├── tools/              # API wrappers, MCP toolsets, cost and distance tools
├── models.py           # TripBrief, TransportOption, StayOption, Place
├── cache/              # cached API responses (gitignored except demo)
├── output/             # generated trip guides
├── tests/              # tool smoke tests
├── .env.example
└── README.md
```

---

## Demo scenario

One scripted run in `adk web` that shows every pattern and both MCP servers in about five minutes.

1. User: *"Plan 5 days in Lisbon for two, mid budget, we love history and street food."*
2. Concierge asks for the dates and departure city, then saves the brief. Shows the delegation pattern.
3. The event trace shows the four research branches running in parallel, with Kiwi and Airbnb MCP calls visible.
4. Transport comparer shows cheapest vs fastest; stay comparer shows Airbnb vs hotels per neighbourhood.
5. Planner drafts 5 days; the critic flags an overpacked day or an over-budget total; the reviser fixes it; a later round approves. Shows the loop.
6. Guide writer saves `output/lisbon-<dates>.md` and `.html`: transport, stay comparison, daily plan with history snippets and food stops, cost table, links, and price sources marked.

Keep the cached responses from a rehearsal so the live demo cannot fail on a rate limit or a broken scraper.

---

## Sources

- [Kiwi.com MCP server](https://www.kiwi.com/ag/pages/mcp/)
- [Airbnb MCP server (OpenBnB)](https://github.com/openbnb-org/mcp-server-airbnb)
- [Transitous API and usage policy](https://transitous.org/api/)
- [liteAPI getting started / FAQ](https://docs.liteapi.travel/docs/faq)
- [Tavily API credits](https://docs.tavily.com/documentation/api-credits)
- [Open-Meteo](https://open-meteo.com/)
- [OpenTripMap API](https://dev.opentripmap.com)
- [LiteLLM: OpenAI-compatible endpoints](https://docs.litellm.ai/docs/providers/openai_compatible)
- [ADK: MCP tools](https://adk.dev/tools-custom/mcp-tools/deployment/)
- [vLLM recipe for gpt-oss-120b (tool calling flags)](https://recipes.vllm.ai/openai/gpt-oss-120b)
