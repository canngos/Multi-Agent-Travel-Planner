"""flight_searcher -> flight_options and ground_searcher -> ground_options."""
import json

from google.genai import types

from ..tools import geocode_place, plan_public_transport, search_google_flights, search_serpapi_flights, web_search
from ..tools.geo import haversine_km
from ..tools.mcp import kiwi_toolset, mcp_cache_after, mcp_cache_before
from ._common import research_agent


SNAP_KM = 50  # an endpoint this close to the brief's origin or destination is taken to mean that place


def snap_transit_points(tool, args, tool_context):
    """Before-tool callback for plan_public_transport: each endpoint must be the trip's origin or destination.

    In a live run the model searched from (-9.13659, -9.13659), Lisbon's longitude typed into both fields, and
    found no connection. An endpoint far from both places is set to the place the other endpoint is not; with
    both endpoints wrong, the travel date decides (start date: origin -> destination)."""
    if getattr(tool, "name", "") != "plan_public_transport":
        return None
    brief = tool_context.state.get("trip_brief") or {}
    places = {"origin": (brief.get("origin_lat"), brief.get("origin_lon")),
              "destination": (brief.get("destination_lat"), brief.get("destination_lon"))}
    if any(None in p for p in places.values()):
        return None

    def place_at(end: str) -> str | None:
        try:
            point = float(args.get(f"{end}_lat")), float(args.get(f"{end}_lon"))
        except (TypeError, ValueError):
            return None
        near = [name for name, p in places.items() if haversine_km(*point, *p) <= SNAP_KM]
        return near[0] if near else None

    ends = {"from": place_at("from"), "to": place_at("to")}
    if ends["from"] is None and ends["to"] is None:
        outbound = args.get("depart_date") != brief.get("end_date")
        ends = {"from": "origin" if outbound else "destination", "to": "destination" if outbound else "origin"}
        for end in ("from", "to"):
            args[f"{end}_lat"], args[f"{end}_lon"] = places[ends[end]]
    for end, other in (("from", "to"), ("to", "from")):
        if ends[end] is None:
            target = "destination" if ends[other] == "origin" else "origin"
            args[f"{end}_lat"], args[f"{end}_lon"] = places[target]
    return None


def skip_when_excluded(modes: set[str], output_key: str, what: str):
    """Before-agent callback factory: a searcher whose transport modes the travellers all refuse does not run.
    Its output says why, so the comparer knows the empty list is intended, not a failed search."""
    def callback(callback_context):
        avoid = set((callback_context.state.get("trip_brief") or {}).get("avoid_modes") or [])
        if not modes <= avoid:
            return None
        text = "```json\n" + json.dumps({"outbound": [], "return": [], "fallback_links": [],
                                         "notes": f"Not searched: the travellers do not want to travel by {what}."}) \
            + "\n```"
        callback_context.state[output_key] = text
        return types.Content(role="model", parts=[types.Part(text=text)])

    callback.__name__ = f"skip_{output_key}"
    return callback

TRANSPORT_SHAPE = """{"outbound": [TransportOption, ...], "return": [TransportOption, ...], "fallback_links": [str, ...], "notes": "..."}
fallback_links: search links returned by a failed tool (e.g. search_url) for the user to check themselves, otherwise [].
TransportOption = {"mode": "flight"|"train"|"bus"|"ferry", "operator": str, "depart": "YYYY-MM-DDTHH:MM" (local),
  "arrive": "YYYY-MM-DDTHH:MM", "duration_min": int, "stops": int (number of changes; 0 if unknown, never null), "price_eur": number|null,
  "price_source": "live"|"web_estimate"|null (required when price_eur is set), "source": str, "url": str, "details": str}
price_eur is the TOTAL for all travellers. Copy each option from the tool result; keep its details text."""

FLIGHT_ROLE = """You are the FLIGHT SEARCHER of a trip-planning team. Find flights from the origin to the destination
on start_date (outbound) and back on end_date (return) for all travellers."""

FLIGHT_STEPS = """STEPS
1. Call the Kiwi tool, named exactly `search-flight` (with a hyphen), for the outbound: flyFrom = origin, flyTo = destination (city names or IATA codes),
   departureDate in dd/mm/yyyy (2027-05-10 becomes 10/05/2027), adults = travellers, currency "EUR".
   Then the same for the return (flyFrom = destination, flyTo = origin, date = end_date).
2. FALLBACK for a direction where Kiwi fails or finds nothing: search_google_flights(origin_iata,
   destination_iata, date as YYYY-MM-DD, adults). From the EU it often fails with a consent page; then call
   search_serpapi_flights with the same arguments. Use the main airport IATA codes (e.g. HEL, LIS).
   If every source fails, return an empty list for that direction and copy the search_url from the
   search_google_flights error into "fallback_links".
3. Keep up to 5 options per direction: the cheapest, the fastest and good-value ones in between, different
   airlines where possible.
IMPORTANT: details may contain warnings (AIRPORT CHANGE, self-transfer, included bags). Keep them in details."""

FLIGHT_EXAMPLE = """{"outbound": [{"mode": "flight", "operator": "Finnair", "depart": "2027-05-10T13:00", "arrive": "2027-05-10T15:50",
  "duration_min": 290, "stops": 0, "price_eur": 411.0, "price_source": "live", "source": "kiwi",
  "url": "https://kiwi.com/u/abc123", "details": "HEL → LIS; AY1739 HEL 13:00→LIS 15:50; included bags for all travellers: 2 personal item, 2 cabin, 0 checked; price is the total for 2 adult(s)"}],
 "return": [], "fallback_links": ["https://www.google.com/travel/flights?..."],
 "notes": "Kiwi worked for the outbound; the return search failed, Google Flights showed its consent page."}"""

GROUND_ROLE = """You are the GROUND TRANSPORT SEARCHER of a trip-planning team. Find train, bus and ferry connections
from the origin to the destination on start_date and back on end_date, with ticket prices."""

GROUND_STEPS = """STEPS
1. plan_public_transport(from = origin_lat/origin_lon, to = destination_lat/destination_lon,
   depart_date = start_date, depart_time "07:00", max_results 3), then the return (swap from/to, end_date).
   If the brief lacks coordinates, call geocode_place for origin and destination first.
2. Prices: Transitous gives schedules only. For the main operator of the best connection, call web_search once
   per direction at most, e.g. "Tallink Helsinki Tallinn ferry ticket price". From a result, take a typical
   one-way fare per person, multiply by travellers, set price_eur to it, price_source "web_estimate",
   source "transitous+tavily", and add "fare from <page url>" to details. If no fare is found, keep price_eur null.
   Keep "TYPICAL TIMETABLE" notes in details unchanged: those times come from a nearer date and must be checked.
FALLBACK: if plan_public_transport fails or finds nothing, call web_search
"<origin> to <destination> train bus ferry timetable price" and report what the pages say
(price_source "web_estimate", source "tavily", url = the page).
If the only ground routes take more than 24 hours (e.g. Helsinki to Lisbon), report at most one per direction
and say in "notes" that ground travel is impractical for this trip.
Otherwise keep up to 3 connections per direction (different departure times or operators)."""

GROUND_EXAMPLE = """{"outbound": [{"mode": "ferry", "operator": "Tallink", "depart": "2026-12-01T07:30", "arrive": "2026-12-01T10:30",
  "duration_min": 180, "stops": 2, "price_eur": 70.0, "price_source": "web_estimate", "source": "transitous+tavily",
  "url": "https://www.tallink.com/", "details": "tram 7 (HSL): Rautatientori → Länsiterminaali | ferry (Tallink): Helsinki → Tallinn; fare from https://www.example.com/fares"}],
 "return": [], "fallback_links": [], "notes": "Return connection not found by Transitous; fares are web estimates."}"""


def build_flight_searcher():
    return research_agent(
        name="flight_searcher",
        description="Finds outbound and return flights with live prices.",
        role=FLIGHT_ROLE, steps=FLIGHT_STEPS, shape=TRANSPORT_SHAPE, example=FLIGHT_EXAMPLE,
        tools=[kiwi_toolset(), search_google_flights, search_serpapi_flights],
        output_key="flight_options",
        max_calls=6,
        before_agent_callback=skip_when_excluded({"flight"}, "flight_options", "plane"),
        before_tool_callback=mcp_cache_before,
        after_tool_callback=mcp_cache_after,
    )


def build_ground_searcher():
    return research_agent(
        name="ground_searcher",
        description="Finds train, bus and ferry connections with estimated fares.",
        role=GROUND_ROLE, steps=GROUND_STEPS, shape=TRANSPORT_SHAPE, example=GROUND_EXAMPLE,
        tools=[plan_public_transport, web_search, geocode_place],
        output_key="ground_options",
        max_calls=6,
        before_agent_callback=skip_when_excluded({"train", "bus", "ferry"}, "ground_options", "train, bus or ferry"),
        before_tool_callback=snap_transit_points,
    )
