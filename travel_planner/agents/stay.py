"""airbnb_searcher -> airbnb_options and hotel_searcher -> hotel_options."""
import shutil

from google.genai import types

from .. import config
from ..tools import search_google_hotels, search_hotel_rates, stay_deep_links
from ..tools.mcp import airbnb_toolset, mcp_cache_after, mcp_cache_before
from ..tools.trip import PEOPLE_PER_ROOM
from ._common import research_agent
from .output import fenced_json
from .summaries import build_stay_summary

STAY_SHAPE = """{"results": [StayOption, ...], "fallback_links": [str, ...], "notes": "..."}
fallback_links: the stay_deep_links URLs when you had to use that fallback, otherwise [].
StayOption = {"kind": "airbnb"|"hotel", "name": str, "area": str (neighbourhood), "lat": number, "lon": number,
  "price_per_night_eur": number, "total_price_eur": number (whole stay), "rating": number|null,
  "price_source": "live"|"web_estimate", "source": str, "url": str, "details": str}
Copy each stay from the tool result; keep its details text (rating scale, taxes, distance)."""

AIRBNB_ROLE = """You are the AIRBNB SEARCHER of a trip-planning team. Find Airbnb stays for the whole trip
(checkin = start_date, checkout = end_date) for all travellers, in good areas for the budget."""

AIRBNB_STEPS = """STEPS
1. airbnb_search(location = "<destination>, <destination_country>", checkin, checkout, adults = travellers).
   For budget_tier "low" any property type is fine; for "mid" and "high" pass propertyType "entire_home".
2. Then search 1-2 central, well-located neighbourhoods by name (e.g. "Alfama, Lisbon"); you may choose the
   neighbourhood names from general knowledge, but every stay you report must come from a tool result.
3. Keep up to 8 stays: cheapest first, from at least 2 different areas, preferring rating >= 4.7 and
   listings close to the centre. If total_budget_eur is set, prefer stays under 35% of it.
FALLBACK: if airbnb_search fails, call stay_deep_links(city, checkin, checkout, adults), return an empty
"results" list and copy the Airbnb URL into "fallback_links"."""

AIRBNB_EXAMPLE = """{"results": [{"kind": "airbnb", "name": "Renovated Studio in Historic Center", "area": "São Miguel",
  "lat": 38.7163, "lon": -9.1353, "price_per_night_eur": 97.0, "total_price_eur": 388.0, "rating": 4.93,
  "price_source": "live", "source": "airbnb", "url": "https://www.airbnb.com/rooms/123?check_in=2027-05-10&check_out=2027-05-14&adults=2",
  "details": "1 bedroom, 1 queen bed; Superhost; 1.0 km from the centre; Airbnb rating 4.93/5 (455 reviews)"}],
 "fallback_links": [], "notes": "Searched Lisbon, Alfama and Baixa."}"""

HOTEL_ROLE = """You are the HOTEL SEARCHER of a trip-planning team. Find hotels for the whole trip
(checkin = start_date, checkout = end_date) for all travellers in one room, matching the budget tier."""

HOTEL_STEPS = """STEPS
1. search_google_hotels(area = destination, checkin, checkout, adults = travellers, sort_by, min_rating,
   hotel_class, exclude_hostels): budget_tier "low" -> sort_by "lowest_price", min_rating 3.5 (hostels allowed);
   "mid" -> "lowest_price", 4.0, hotel_class "3,4,5", exclude_hostels true; "high" -> "highest_rating", 4.5,
   hotel_class "4,5", exclude_hostels true. These are public prices with taxes (they include Booking.com).
2. search_hotel_rates(lat = destination_lat, lon = destination_lon, checkin, checkout, adults, radius_km 3)
   for more hotels (liteAPI wholesale rates).
3. Keep up to 8 hotels: mostly from Google Hotels, plus the 2-3 best liteAPI ones. For every liteAPI hotel,
   add to details: "liteAPI rate: may be 15-30% below public booking sites; excludes city tax".
FALLBACK: if search_google_hotels fails, use liteAPI alone and say in "notes" that prices may be 15-30% below
booking sites. If both fail, call stay_deep_links(city, checkin, checkout, adults), return an empty
"results" list and copy the Booking.com URL into "fallback_links"."""

HOTEL_EXAMPLE = """{"results": [{"kind": "hotel", "name": "The Central House Lisbon Baixa", "area": "Baixa",
  "lat": 38.7105, "lon": -9.1372, "price_per_night_eur": 48.5, "total_price_eur": 194.0, "rating": 4.3,
  "price_source": "live", "source": "google_hotels (serpapi)",
  "url": "https://www.booking.com/searchresults.html?ss=The+Central+House+Lisbon+Baixa%2C+Lisbon&checkin=2027-05-10&checkout=2027-05-14&group_adults=2&no_rooms=1",
  "details": "2★; Google rating 4.3/5 (771 reviews); location 4.8/5"}],
 "fallback_links": [], "notes": "Google Hotels and liteAPI both worked."}"""


def skip_without_npx(callback_context):
    """Before-agent callback (REVIEW.md A5): the Airbnb MCP server runs through npx; without Node.js the agent can
    only fail, so it does not run. A hard fact, not a choice. Never offline: the recorded MCP answers need no npx."""
    if config.offline() or shutil.which("npx"):
        return None
    text = fenced_json({"results": [], "fallback_links": [], "notes": (
        "Not searched: the Airbnb source needs Node.js (npx), which is not installed on this computer.")})
    callback_context.state["airbnb_options"] = text
    return types.Content(role="model", parts=[types.Part(text=text)])


# What each budget tier searches, set in code (the prompt says the same, but a model may drift): live runs gave mid
# budgets the cheapest hostel, and a "money is no object" trip a €367 village Airbnb.
HOTEL_TIER_ARGS = {
    "low": {"sort_by": "lowest_price", "min_rating": 3.5, "hotel_class": "", "exclude_hostels": False},
    "mid": {"sort_by": "lowest_price", "min_rating": 4.0, "hotel_class": "3,4,5", "exclude_hostels": True},
    "high": {"sort_by": "highest_rating", "min_rating": 4.5, "hotel_class": "4,5", "exclude_hostels": True},
}


def rooms_needed(brief: dict) -> int:
    return max(1, -(-int(brief.get("travellers") or 1) // PEOPLE_PER_ROOM))


def tier_hotel_args(tool, args, tool_context):
    """Before-tool callback of the hotel searcher: the brief's budget tier sets the Google Hotels filters, and a
    group gets one room per two travellers in both hotel sources."""
    name = getattr(tool, "name", "")
    brief = tool_context.state.get("trip_brief") or {}
    if name == "search_google_hotels":
        args.update(HOTEL_TIER_ARGS.get(brief.get("budget_tier"), HOTEL_TIER_ARGS["mid"]))
    if name in ("search_google_hotels", "search_hotel_rates", "stay_deep_links") and rooms_needed(brief) > 1:
        args["rooms"] = max(int(args.get("rooms") or 1), rooms_needed(brief))
    return None


NO_STAY_NOTE = "Not searched: the travellers stay in their own place (no accommodation needed)."
DAY_TRIP_NOTE = "Not searched: a day trip, out and back the same day (no night to stay)."


def skip_without_stay(callback_context):
    """Before-agent callback of the stay team: a brief with own_accommodation (or a day trip, with no night) needs no
    stay, so the searchers do not run. A hard fact the user stated, like an excluded transport mode. Their outputs
    and the summary say why, so the itinerary writer knows the empty stay is intended (and plans "stay": null)."""
    state = callback_context.state
    brief = state.get("trip_brief") or {}
    if brief.get("own_accommodation"):
        note = NO_STAY_NOTE
    elif brief.get("nights") == 0:  # a day trip: out and back the same day
        note = DAY_TRIP_NOTE
    else:
        return None
    nothing = {"results": [], "fallback_links": [], "notes": note}
    for key in ("airbnb_options", "hotel_options"):
        state[key] = fenced_json(nothing)
    state["stay_summary"] = fenced_json(build_stay_summary(nothing, nothing, brief) | {"notes": note})
    return types.Content(role="model", parts=[types.Part(text=note)])


def build_airbnb_searcher():
    return research_agent(
        name="airbnb_searcher",
        description="Finds Airbnb stays with live prices in good areas.",
        role=AIRBNB_ROLE, steps=AIRBNB_STEPS, shape=STAY_SHAPE, example=AIRBNB_EXAMPLE,
        tools=[airbnb_toolset(), stay_deep_links],
        output_key="airbnb_options",
        max_calls=4,
        before_agent_callback=skip_without_npx,
        before_tool_callback=mcp_cache_before,
        after_tool_callback=mcp_cache_after,
    )


def build_hotel_searcher():
    return research_agent(
        name="hotel_searcher",
        description="Finds hotels with public (Google Hotels) and wholesale (liteAPI) prices.",
        role=HOTEL_ROLE, steps=HOTEL_STEPS, shape=STAY_SHAPE, example=HOTEL_EXAMPLE,
        tools=[search_google_hotels, search_hotel_rates, stay_deep_links],
        output_key="hotel_options",
        max_calls=4,
        before_tool_callback=tier_hotel_args,
    )
