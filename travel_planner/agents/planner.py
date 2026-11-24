"""itinerary_planner: day-by-day plan from the research results and the weather -> itinerary."""
from google.adk.agents import LlmAgent
from google.genai import types

from ..config import MODEL
from ..tools import calculate_trip_cost, cluster_by_distance, get_forecast
from .grounding import ground_itinerary
from .itinerary_tidy import tidy_itinerary
from .limits import limit_tool_calls
from .prompts import BRIEF, REQUIREMENTS, output_format, tool_budget
from .retry import offer_retry_tool, retry_malformed_call

ROLE = """You are the ITINERARY PLANNER of a trip-planning team. Turn the research below into a realistic
day-by-day plan: nearby places on the same day, indoor sights on rainy days, and sights only on days they are open."""

INPUTS = """RESEARCH RESULTS (JSON written by the research agents; an empty value means that agent failed):
TRANSPORT SUMMARY: {transport_summary?}

STAY SUMMARY: {stay_summary?}

SIGHTS (culture_findings): {culture_findings?}

FOOD (food_findings): {food_findings?}"""

STEPS = """STEPS
1. get_forecast(destination_lat, destination_lon, start_date, end_date). Each day has a weekday and "rainy".
   If it fails, plan without weather and say so in "notes".
2. Choose the transport: the transport summary's "recommended" outbound and return (picked by code: price plus
   travel time and lost trip time for this budget tier, and only options that suit the travellers); if it has
   none, the options its recommendation names (otherwise: low budget -> cheapest, mid -> first of "ranked",
   high -> fastest). Note the outbound arrival time and the
   return departure time: day 1 starts about 1.5 h after arrival, and the last day ends about 2.5 h before
   departure (for a flight; 1 h for a ferry, bus or train).
3. Choose the stay: low budget -> "cheapest", mid -> "best_value" (never a hostel), high -> the best-rated 4-5★
   hotel ("★" in its details) that fits total_budget_eur, else "top_rated" (fall back to any stay in the summary).
4. Pick the sights: 2-4 per full day and 0-2 on arrival/departure days, matching the brief's interests,
   at most 14 in total. Call cluster_by_distance once with places = [{"name", "lat", "lon"}] of those sights
   (skip sights without coordinates and plan them where they fit), n_days = the number of trip days, and
   food_places = [{"name", "lat", "lon"}] of the FOOD results that have coordinates.
5. Give each cluster a date: the smallest clusters go to the arrival and departure days; rainy days get the
   cluster with the most indoor sights. Check every sight's hours against that day's weekday AND the visit
   time: a sight closed that day ("Mo off", "closed Mon") or closing before your visit moves to another
   time or day, or is dropped (say so in "notes").
   Lunch and dinner: take them from that day's food_nearby list, mostly in the brief's budget tier. Use each
   restaurant, cafe or bar at most ONCE in the whole trip; if a day has no unused food place nearby, add an
   item {"kind": "free", "name": "Lunch near <area>"} instead. Use the dishes list for "note" where it fits.
6. calculate_trip_cost(transport_total_eur = chosen outbound price + return price (0 if unknown),
   stay_total_eur = the stay's total_price_eur, activities_total_eur = sum of ticket_eur of the planned
   sights x travellers, days, travellers, budget_tier, total_budget_eur). Leave food and local transport
   out: the tool uses typical costs and lists them as assumptions.
FALLBACK: if cluster_by_distance fails, group the sights yourself by area. If calculate_trip_cost fails,
set "cost" to null and explain in "notes"."""

RULES = """RULES:
- Use ONLY places, prices, hours and links from the inputs above and your tool results. Never add a place
  from memory. Copy each item's source_url character for character from the input's url / source_urls
  (keep "http://" as it is); null if the input had none.
- ticket_eur: copy it from the input; null means unknown (never turn it into 0).
- Day 1 starts with a "transport" item for the arrival ("Arrive at ..."); the last day ends with a "transport"
  item for the departure ("Depart from ..."). Nothing is planned before the arrival or after the departure.
- Copy the chosen transport options and the chosen stay unchanged from the summaries.
- Times are local, "HH:MM". Keep days realistic: a museum takes 1.5-2.5 h, a viewpoint 30 min; leave time
  to walk between places."""

# Live runs (and the first recorded demo) started every full day with lunch at 12:30-13:00 and left the mornings
# empty, and put two restaurants 2.5 h apart on one day.
DAY_SHAPE_RULES = """
- Day hours: the brief's day_start and day_end (09:30 and 22:00 if the brief has none). Full days (every day
  except the arrival and departure days) start at day_start with a sight that is open then; lunch comes after
  the morning's sights. Never start a full day with lunch. Nothing is planned before day_start or after
  day_end; on the arrival day, start at the later of arrival + 1.5 h and day_start.
- Pace: the brief's pace ("balanced" if it has none) replaces the numbers in step 4. relaxed = 1-3 sights per
  full day with long breaks; balanced = 2-4 sights; packed = 4-6 sights with short breaks (at most 20 in total).
- One restaurant per meal: lunch and dinner at least 3 hours apart, and no second restaurant right after a meal."""


PLAN_SIZE = "{trip_plan_size?}"  # a long trip may plan more than 14 sights; "" for an ordinary trip


def plan_rules() -> str:
    """RULES, plus the day-shape rules (the reviser uses the same rules)."""
    return RULES + DAY_SHAPE_RULES


SHAPE = """{"transport": {"outbound": TransportOption|null, "return": TransportOption|null, "why": str},
 "stay": StayOption|null, "stay_why": str,
 "weather_kind": str, "days": [Day, ...], "cost": <the calculate_trip_cost result object>|null, "notes": "..."}
Day = {"day": int, "date": "YYYY-MM-DD", "weekday": str, "weather": str (e.g. "light rain, 14-19 °C"), "rainy": bool,
       "area": str, "walk_km": number|null, "items": [Item, ...]}
Item = {"time": "HH:MM", "kind": "sight"|"restaurant"|"cafe"|"bar"|"transport"|"free", "name": str,
        "note": str (why / what to see or order, 1 sentence), "ticket_eur": number|null, "hours": str|null,
        "lat": number|null, "lon": number|null, "source_url": str|null}
TransportOption / StayOption = copied unchanged from the summaries."""

EXAMPLE = """{"transport": {"outbound": {"mode": "flight", "operator": "SAS", "...": "copied option"},
   "return": {"mode": "flight", "operator": "Finnair", "...": "copied option"}, "why": "Recommended by the comparer: cheapest pair."},
 "stay": {"kind": "hotel", "name": "The Central House", "...": "copied stay"}, "stay_why": "Best value in Baixa, near most sights.",
 "weather_kind": "typical (observed on the same dates in an earlier year, not a forecast)",
 "days": [{"day": 1, "date": "2027-05-10", "weekday": "Mon", "weather": "mainly clear, 15-22 °C", "rainy": false,
   "area": "Baixa and Chiado", "walk_km": 2.1, "items": [
   {"time": "16:10", "kind": "transport", "name": "Arrive at Lisbon airport (SAS)", "note": "Metro red line to the centre.",
    "ticket_eur": null, "hours": null, "lat": null, "lon": null, "source_url": null},
   {"time": "18:00", "kind": "sight", "name": "Santa Justa Lift", "note": "Iron lift with views over Baixa.",
    "ticket_eur": 6.0, "hours": "Mo-Su 07:30-23:00", "lat": 38.71194, "lon": -9.13917, "source_url": "https://en.wikipedia.org/wiki/Santa_Justa_Lift"}]}],
 "cost": {"categories_eur": {"transport": 684.0, "...": 0}, "total_eur": 1650.0, "...": "tool result"},
 "notes": "Weather is typical, not a forecast. Belém Tower is closed on Mondays, so it is on day 2."}"""


def build_itinerary_planner() -> LlmAgent:
    instruction = "\n\n".join([ROLE, BRIEF, INPUTS, STEPS, tool_budget(5), plan_rules(), output_format(SHAPE, EXAMPLE)]) \
        + REQUIREMENTS + PLAN_SIZE
    return LlmAgent(
        name="itinerary_planner",
        model=MODEL,
        description="Builds the day-by-day plan from the research results and the weather.",
        instruction=instruction,
        tools=[get_forecast, cluster_by_distance, calculate_trip_cost],
        output_key="itinerary",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        # restore mangled links / drop links no tool returned, then sort items and add ticket texts
        after_agent_callback=[ground_itinerary, tidy_itinerary],
        before_tool_callback=limit_tool_calls(5),
        before_model_callback=offer_retry_tool,  # retry when a tool call's arguments are not valid JSON
        after_model_callback=retry_malformed_call,
    )
