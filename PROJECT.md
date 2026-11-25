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
    model=f"openai/{os.environ['LEHMUS_MODEL_ID']}",
    api_base=os.environ["LEHMUS_API_BASE"],   # ends with /v1
    api_key=os.environ["LEHMUS_API_KEY"],
)
```

If the endpoint returns *Not Found*, check that `api_base` ends with `/v1`.

---

## 3. Agent architecture

Ten LLM agents, using five orchestration patterns: **coordinator/delegation**, **sequential**, **parallel** (nested), a **generator–critic loop** and an **agent used as a tool**, plus a **human-in-the-loop** approval before the guide. Agents pass results through ADK session state: each writes to its own `output_key`, and later agents read those keys in their instructions. Comparing options is sorting and filtering, so code does it (`agents/summaries.py`), not an LLM agent.

### Flow

1. **Concierge** (root) chats with the user until the trip brief is complete, saves it with a tool, then hands over to the planning pipeline.
2. **Research team** runs four branches in parallel: Transport, Stay, Culture & History, Food & Drink. Transport and Stay are small teams of their own: two searchers in parallel, then code ranks and compares their results.
3. **Review loop** (at most 4 rounds): the Itinerary Writer drafts the day-by-day plan from the research and the weather forecast; the Plan Critic checks cost, opening hours, pace and grounding; the writer revises. The loop ends when the critic approves. When revising, the writer may call `extra_sights_researcher` for a thin day.
4. The reviewed plan is shown to the user, who approves it or asks for a change (a change goes back to the concierge, which decides which research to re-run).
5. **Guide Writer** turns the approved plan into a trip guide, a calendar and a map file.

```
concierge (LlmAgent, root)
   │  transfer_to_agent
   ├─▶ planning_pipeline (SequentialAgent)
   │     ├─ research_team (ParallelAgent)
   │     │     ├─ transport_team (ParallelAgent: flight_searcher ∥ ground_searcher; code then writes transport_summary)
   │     │     ├─ stay_team (ParallelAgent: airbnb_searcher ∥ hotel_searcher; code then writes stay_summary)
   │     │     ├─ culture_agent
   │     │     └─ food_agent
   │     └─ review_loop (LoopAgent, max_iterations=4)
   │           ├─ itinerary_writer   (tool: extra_sights_researcher, an AgentTool)
   │           └─ plan_critic        (approval ends the loop)
   └─▶ guide_writer   (after the user approved the plan)
```

### Agents

| # | Agent | ADK type | Job | Writes to state |
| --- | --- | --- | --- | --- |
| 1 | `concierge` | LlmAgent (root) | Asks for missing details (destination, dates, travellers, budget, interests), confirms, saves the brief, transfers to the pipeline; later decides what a change request needs re-run and records the user's approval | `trip_brief`, `replan_scope` |
| 2 | `flight_searcher` | LlmAgent | Flights from origin to destination and back | `flight_options` |
| 3 | `ground_searcher` | LlmAgent | Trains, buses, ferries; schedules plus prices | `ground_options` |
| 4 | `airbnb_searcher` | LlmAgent | Airbnb listings in the best areas for the budget | `airbnb_options` |
| 5 | `hotel_searcher` | LlmAgent | Hotel rates for the same areas and dates | `hotel_options` |
| 6 | `culture_agent` | LlmAgent | Sights, a short history of each, opening hours and tickets | `culture_findings` |
| 7 | `food_agent` | LlmAgent | Traditional dishes, restaurants per budget tier near the sights | `food_findings` |
| 8 | `itinerary_writer` | LlmAgent | Drafts the day-by-day plan (nearby places on the same day, indoor sights on rainy days), then revises it from the critique; may research more sights for a thin day | `itinerary` |
| 9 | `plan_critic` | LlmAgent | Reviews the plan with the code check (must-fix facts, day-shape points to decide); `VERDICT: APPROVED` ends the loop | `critique` |
| 10 | `guide_writer` | LlmAgent | Writes the final guide and saves it with a calendar and a map file | `final_guide`, `guide_reply` |

`extra_sights_researcher` is a second culture researcher wrapped in an ADK `AgentTool`; the itinerary writer calls it, so it is a tool, not a step in the tree. Workflow agents that hold them together (no LLM): `planning_pipeline` (SequentialAgent), `research_team`, `transport_team` and `stay_team` (ParallelAgent), `review_loop` (LoopAgent, `max_iterations=4`).

### Design notes

- Parallel agents share one session, so every agent writes to a distinct key. Nothing in a parallel branch reads another branch's output.
- The concierge → pipeline and concierge → guide writer handovers use ADK's `transfer_to_agent` with both as sub-agents.
- Code checks facts (opening hours, grounding, budget arithmetic, day count) and refuses unsafe actions with a reason; agents make the choices. Every code override is logged in `review_log`.
- One itinerary writer both drafts and revises, so every plan, the last one included, is reviewed by the critic.

---

## 4. Tools and data sources

Every data need has a free source, and most agents **combine** several tools because each covers something the others miss. A separate **fallback** layer only runs when a source errors or returns nothing. Two sources are MCP servers (Kiwi, Airbnb); the rest are Python function tools wrapping REST APIs.

| Agent | Tools it combines | What the merge gives | Fallback |
| --- | --- | --- | --- |
| `concierge` | `save_trip_brief`, `request_replan`, `approve_plan` (own code) + `geocode_place` (Nominatim) | Validated destination and origin with coordinates; the re-plan scope; the user's approval | Ask the user to rephrase |
| `flight_searcher` | Kiwi MCP `search-flight` | Live flight prices, times, booking links | `search_google_flights` (fast-flights library), then SerpApi Google Flights |
| `ground_searcher` | `plan_public_transport` (Transitous) + `web_search` (Tavily) | Train/bus/ferry schedules from Transitous, ticket prices from operator pages | Tavily only |
| `airbnb_searcher` | Airbnb MCP `airbnb_search`, `airbnb_listing_details` | Listings with nightly price, rating, link | Airbnb search deep link |
| `hotel_searcher` | `search_hotel_rates` (liteAPI) + optional `search_google_hotels` (SerpApi) | Hotel rates for the same dates and areas | Booking.com search deep link |
| `culture_agent` | `find_sights` (OpenTripMap) + `wiki_summary` (Wikipedia) + `wikivoyage_section` (Wikivoyage) + `web_search` | Sights with location, short history, traveller tips, hours and tickets | Wikivoyage "See" section only |
| `food_agent` | `find_restaurants` (OSM Overpass) + `wikivoyage_section` ("Eat") + `web_search` | Traditional dishes, restaurants per budget tier, near the sights | Overpass plus model knowledge |
| `itinerary_writer` | `get_forecast` (Open-Meteo) + `cluster_by_distance` + `calculate_trip_cost` (own code); when revising, `extra_sights_researcher` (agent as tool) | Nearby places grouped per day, indoor sights on rainy days, the cost total; more sights for a thin day | Plan without the forecast; group sights by area itself |
| `plan_critic` | `check_itinerary` + `calculate_trip_cost` (own code) | Must-fix facts and day-shape points, totals per category vs the budget | Loop stops at 4 rounds and keeps the best reviewed plan |
| `guide_writer` | `save_guide`, `export_calendar`, `export_map_stops` (own code) | Markdown and HTML guide, `.ics` calendar and `.geojson` stops in `output/` | n/a |

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

Only the LLM key is required. Four free data-source sign-ups are optional (Tavily and OpenTripMap recommended; liteAPI and SerpApi), and everything else needs no account. Without a key the planner still works with less detail (`scripts/check_setup.py` says what each missing key costs). No credit card is needed for any of them. Put every key in `.env` and add `.env` to `.gitignore`.

### Action needed: sign up for a key

| Service | Required? | What to do | Free allowance | `.env` variable |
| --- | --- | --- | --- | --- |
| University LLM API (Lehmus) | Required for live runs (not for the offline demo) | Get the API key and base URL from the course or university portal; confirm the model id | Per university terms | `LEHMUS_API_KEY`, `LEHMUS_API_BASE`, `LEHMUS_MODEL_ID` |
| [Tavily](https://app.tavily.com) | Optional (recommended) | Create an account, copy the API key from the dashboard | 1,000 credits/month, no card (1 basic search = 1 credit) | `TAVILY_API_KEY` |
| [OpenTripMap](https://dev.opentripmap.com) | Optional (recommended) | Register on the developer site, request the free API key | Free tier; check the daily limit shown after sign-up | `OPENTRIPMAP_API_KEY` |
| [liteAPI (Nuitée)](https://docs.liteapi.travel/docs/faq) | Optional | Create a free account, open Profile in the dashboard, copy the **sandbox** key. Do not add a card; production keys are not needed | Free sandbox | `LITEAPI_KEY` |
| [SerpApi](https://serpapi.com) | Optional | Create an account, copy the key. Used as flight fallback and extra hotel source | 250 searches/month, shared by Flights and Hotels | `SERPAPI_KEY` |

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

The real file is `.env.example` in the repository root; in short:

```dotenv
# University LLM (Lehmus; not needed for the offline demo, DEMO=1)
LEHMUS_API_BASE=https://api.lehmus-ai.oulu.fi/v1
LEHMUS_API_KEY=
LEHMUS_MODEL_ID=azsydsttjnlbfjbgqnwd

# Optional, instead of Lehmus: any LiteLLM model string with its key
LLM_MODEL=
LLM_API_BASE=
LLM_API_KEY=

# Data sources: free keys, all optional (Tavily and OpenTripMap recommended)
TAVILY_API_KEY=
OPENTRIPMAP_API_KEY=
LITEAPI_KEY=
SERPAPI_KEY=

# Used in User-Agent headers (Nominatim, Wikipedia, Transitous)
CONTACT_EMAIL=
APP_USER_AGENT=TripPlannerCourseProject/0.1
```

---

## 6. Data model

Shared Pydantic models let agents compare results from different sources like for like. Tool wrappers return these models; the comparison code and the itinerary writer read them from session state.

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
| `replan_scope` | `concierge` (via `request_replan` tool) | the research branches (skip when out of scope) |
| `flight_options`, `ground_options` | the two transport searchers | the transport team's code |
| `transport_summary` | `transport_team` (code, no LLM) | itinerary writer, critic, guide writer |
| `airbnb_options`, `hotel_options` | the two stay searchers | the stay team's code |
| `stay_summary` | `stay_team` (code, no LLM) | itinerary writer, critic, guide writer |
| `culture_findings`, `food_findings` | culture and food agents (new sights also from `extra_sights_researcher`) | itinerary writer, guide writer |
| `itinerary` | `itinerary_writer` (drafted in round 1, revised later) | critic, guide writer |
| `critique` | `plan_critic` | `itinerary_writer` |
| `final_guide`, `guide_reply` | `guide_writer` | user (also saved to `output/`) |

Instructions read state with ADK's `{key}` templating, e.g. `"Compare these options: {flight_options} {ground_options}"`.

---

## 7. Requirements checklist

| Requirement | How the design meets it |
| --- | --- |
| At least 3 different agents | 10 LLM agents with distinct roles, plus a culture researcher used as a tool and 5 workflow agents |
| Multi-agent orchestration pattern | Coordinator/delegation (concierge), sequential (pipeline), parallel (research team, nested in transport and stay), loop (review), agent as tool (`extra_sights_researcher`), human in the loop (approval) |
| At least one tool call | 24 function tools, an agent used as a tool and 2 MCP servers; every research agent calls tools |
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
| Runs are slow (many model and tool calls) | Medium | Parallel research; comparisons in code instead of LLM agents; a re-plan re-runs only the research the change needs; progress streamed in the web UI |
| Model invents places or prices | Medium | Instructions require every place and price to come from a tool result with a source URL; critic checks this |

---

## Build plan

Build bottom-up: prove the model and each tool alone, then wire agents in, then add the loop and the guide. Each step ends with something runnable.

1. **Model test.** One ADK agent with one dummy tool against the university endpoint. Confirm tool calls work; otherwise switch model.
2. **Accounts.** Sign up for Tavily, OpenTripMap, liteAPI; fill `.env`; check the liteAPI sandbox prices and OpenTripMap limits.
3. **Tool wrappers.** Write each wrapper with its schema, error handling and cache, and a pytest smoke test per tool.
4. **MCP connections.** Connect Kiwi (remote) and Airbnb (`npx`) through `McpToolset`; list their tools.
5. **Research agents.** Build the four branches one at a time, running each alone in `adk web`.
6. **Teams.** Add the transport and stay teams (parallel searchers, then code that ranks and compares their options).
7. **Pipeline.** Concierge, research team, itinerary writer and guide writer, end to end without the loop.
8. **Review loop.** Add the plan critic and the writer's revision step; check the loop ends on approval or after round 4.
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
4. Code ranks the transport options (best value, cheapest, fastest) and compares Airbnb with hotels per neighbourhood.
5. The itinerary writer drafts 5 days; the critic flags a problem such as a sight planned after closing; the writer fixes it; a later round approves. Shows the loop.
6. The reviewed plan appears in the chat; the user approves it (human in the loop).
7. Guide writer saves `output/lisbon-<dates>.md` and `.html`: transport, stay comparison, daily plan with history snippets and food stops, cost table, links, and price sources marked.

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
