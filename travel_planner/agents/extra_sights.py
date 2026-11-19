"""Research on demand (REVIEW.md A2): the itinerary reviser may call a culture researcher as a tool.

extra_sights_researcher is a second culture researcher (an ADK agent has one parent, so it is a new instance,
not culture_agent), wrapped in an AgentTool and given to the itinerary reviser. When a day is thin and the code
check lists no unused place nearby, the REVISER decides to research that area instead of the team researching
everything up front. Code guards the facts:
- the researcher runs in its own session with its own tool results, and its links are grounded strictly against
  them (a link it did not get from a tool is removed);
- `merge_extra_sights` adds its new sights to culture_findings, so later grounding and the review checks treat
  them as researched, and logs them (extra_sights_log); places already researched are not added twice;
- at most MAX_CALLS_PER_ROUND calls per review round (limits.limit_tool_calls, no slack).
"""
import json

from google.adk.tools.agent_tool import AgentTool
from pydantic import BaseModel, Field

from ..tools import find_sight_hours, find_sights, wiki_summary
from ._common import research_agent
from .culture import SHAPE
from .enrich import fill_from_tool_results
from .limits import limit_tool_calls
from .review_checks import _parse, find_place

TOOL_NAME = "extra_sights_researcher"
OUTPUT_KEY = "extra_sights_findings"
LOG_KEY = "extra_sights_log"
MAX_CALLS_PER_ROUND = 2


class ExtraSightsRequest(BaseModel):
    """Where the reviser needs more sights."""
    area: str = Field(description="Name of the area or neighbourhood, e.g. \"Belém\".")
    lat: float = Field(ge=-90, le=90, description="Latitude of the area's centre (e.g. a planned item there).")
    lon: float = Field(ge=-180, le=180, description="Longitude of the area's centre.")
    count: int = Field(default=3, ge=1, le=4, description="How many new sights are wanted (1-4).")


ROLE = """You are the EXTRA SIGHTS RESEARCHER of a trip-planning team. The itinerary reviser found a thin day and
asks for a few more sights in one area. The user message is JSON: {"area", "lat", "lon", "count"}."""

STEPS = """STEPS
1. find_sights(lat, lon, interests) with the lat/lon of the request and the brief's interests (radius_km 1.5,
   limit 12).
2. find_sight_hours(lat, lon, radius_km 1.5) once: opening hours and entry fees (ticket_eur 0 = free); match its
   results to the sights by name and add its source URLs.
3. Choose at most "count" sights that are NOT already researched (list below; names may be in another language)
   and fit the interests. No restaurants, cafés or food markets.
4. For a chosen sight without a summary, call wiki_summary("<sight name>, <city>") at most twice in total.

ALREADY RESEARCHED (do not return these): {culture_findings?}"""

EXAMPLE = """{"results": [{"kind": "sight", "name": "Museu de Marinha", "lat": 38.6967, "lon": -9.2097,
  "summary": "Maritime museum in the west wing of the Jerónimos Monastery, on Portugal's naval history.",
  "budget_tier": null, "hours": "Tu-Su 10:00-17:00", "ticket_eur": 8.0, "indoor": true,
  "source_urls": ["https://www.openstreetmap.org/node/1"]}], "notes": "find_sights and find_sight_hours worked."}"""


def build_extra_sights_researcher():
    agent = research_agent(
        name=TOOL_NAME,
        description=("Researches a few more sights around one area (lat/lon) for a thin day: names, short history, "
                     "opening hours, tickets and source links. Input: area, lat, lon, count."),
        role=ROLE, steps=STEPS, shape=SHAPE, example=EXAMPLE,
        tools=[find_sights, find_sight_hours, wiki_summary],
        output_key=OUTPUT_KEY,
        max_calls=5,
        before_grounding=[fill_from_tool_results(OUTPUT_KEY)],
        strict_grounding=True,
    )
    agent.input_schema = ExtraSightsRequest
    return agent


def extra_sights_tool() -> AgentTool:
    return AgentTool(agent=build_extra_sights_researcher())


budget = limit_tool_calls(MAX_CALLS_PER_ROUND, only=(TOOL_NAME,), slack=0)


def merge_extra_sights(tool, args, tool_context, tool_response):
    """After-tool callback of the reviser: the grounded new sights join culture_findings, and the reviser gets them
    back (in place of the researcher's raw reply, whose links were not yet grounded)."""
    if getattr(tool, "name", "") != TOOL_NAME:
        return None
    state = tool_context.state
    found = _parse(state.get(OUTPUT_KEY)).get("results") or []
    culture = _parse(state.get("culture_findings")) or {"results": [], "notes": ""}
    known = [p for p in culture.get("results") or [] if isinstance(p, dict)]
    added = []
    for place in found:
        if isinstance(place, dict) and place.get("name") and not find_place(place["name"], known + added):
            added.append({**place, "kind": "sight"})
    if added:
        culture["results"] = known + added
        state["culture_findings"] = "```json\n" + json.dumps(culture, ensure_ascii=False, indent=1) + "\n```"
    state[LOG_KEY] = list(state.get(LOG_KEY) or []) + [{
        "round": state.get("review_round"), "area": (args or {}).get("area"),
        "added": [p["name"] for p in added], "already_known": len(found) - len(added)}]
    state[OUTPUT_KEY] = None
    return {"status": "ok", "count": len(added), "results": added,
            "notes": ("These sights are now part of the research: add them to the plan with these names, "
                      "ticket_eur, hours and coordinates." if added else
                      "No new sights found in this area: leave the gap and say so in \"notes\".")}
