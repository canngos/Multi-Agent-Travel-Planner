"""flight_searcher -> flight_options and ground_searcher -> ground_options."""
import copy
import json
import re

from google.genai import types

from ..tools import geocode_place, plan_public_transport, search_google_flights, search_serpapi_flights, web_search
from ..tools.airports import far_airport, far_airport_note
from ..tools.geo import haversine_km
from ..tools.mcp import kiwi_toolset, mcp_cache_after, mcp_cache_before
from ._common import research_agent
from .output import extract_json


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
    Its output says why, so the itinerary writer knows the empty list is intended, not a failed search."""
    def callback(callback_context):
        brief = callback_context.state.get("trip_brief") or {}
        avoid = set(brief.get("avoid_modes") or [])
        # a car trip needs no flights; the ground searcher still runs, for a car ferry over the sea
        own_car = bool(brief.get("own_car")) and modes == {"flight"}
        if not (modes <= avoid or own_car):
            return None
        why = "the travellers drive their own car" if own_car else f"the travellers do not want to travel by {what}"
        text = "```json\n" + json.dumps({"outbound": [], "return": [], "fallback_links": [],
                                         "notes": f"Not searched: {why}."}) + "\n```"
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

# The ground searcher's fares come from web pages, per person: it copies that number and code prices the group
GROUND_SHAPE = TRANSPORT_SHAPE.replace(
    '"price_eur": number|null,', '"fare_per_person_eur": number|null, "price_eur": null,').replace(
    "price_eur is the TOTAL for all travellers.",
    "fare_per_person_eur is the one-way fare for ONE person as the page gives it; leave price_eur null, the code "
    "sets it to the total for all travellers.")

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
   one-way fare PER PERSON and copy it, as the page gives it, into fare_per_person_eur; set price_source
   "web_estimate", source "transitous+tavily", and add "fare from <page url>" to details. Leave price_eur null:
   the code multiplies the fare by the travellers. If no fare is found, leave both null.
   Keep "TYPICAL TIMETABLE" notes in details unchanged: those times come from a nearer date and must be checked.
FALLBACK: if plan_public_transport fails or finds nothing, call web_search
"<origin> to <destination> train bus ferry timetable price" and report what the pages say
(fare_per_person_eur as above, price_source "web_estimate", source "tavily", url = the page).
If the only ground routes take more than 24 hours (e.g. Helsinki to Lisbon), report at most one per direction
and say in "notes" that ground travel is impractical for this trip.
Otherwise keep up to 3 connections per direction (different departure times or operators)."""

GROUND_EXAMPLE = """{"outbound": [{"mode": "ferry", "operator": "Tallink", "depart": "2026-12-01T07:30", "arrive": "2026-12-01T10:30",
  "duration_min": 180, "stops": 2, "fare_per_person_eur": 35.0, "price_eur": null, "price_source": "web_estimate", "source": "transitous+tavily",
  "url": "https://www.tallink.com/", "details": "tram 7 (HSL): Rautatientori → Länsiterminaali | ferry (Tallink): Helsinki → Tallinn; fare from https://www.example.com/fares"}],
 "return": [], "fallback_links": [], "notes": "Return connection not found by Transitous; fares are web estimates."}"""


def _rewrite(state, key: str, data: dict) -> None:
    state[key] = "```json\n" + json.dumps(data, ensure_ascii=False, indent=1) + "\n```"


def mark_far_airports(callback_context):
    """After-agent callback of the flight searcher (before grounding): a flight whose destination-side airport is
    far from the destination gets "FAR AIRPORT: ..." in its details, so the transport summary ranks it last and the guide
    shows it. A live Kyiv run planned "Arrive in Kyiv" for flights landing in Chișinău, 409 km away."""
    state = callback_context.state
    brief = state.get("trip_brief") or {}
    try:
        data = extract_json(state.get("flight_options") or "")
    except ValueError:
        return None
    marked = 0
    for leg in ("outbound", "return"):
        for option in data.get(leg) or []:
            found = far_airport(option, leg, brief.get("destination_lat"), brief.get("destination_lon"))
            if found and "FAR AIRPORT" not in str(option.get("details")):
                option["details"] = (str(option.get("details") or "") + "; "
                                     + far_airport_note(found, leg, brief.get("destination", "the destination"))).strip("; ")
                marked += 1
    if marked:
        _rewrite(state, "flight_options", data)
    return None


GROUP_MAX = 9  # Kiwi, Google Flights and SerpApi all refuse more passengers in one search
FLIGHT_TOOLS = {"search-flight", "search_google_flights", "search_serpapi_flights"}
GROUP_NOTE = ("price is the per-person fare x {n} travellers (an estimate): groups over 9 book through the airline's "
              "group desk, and group fares may differ")


def _group_size(tool_context) -> int:
    return int((tool_context.state.get("trip_brief") or {}).get("travellers") or 1)


async def flight_tool_before(tool, args, tool_context):
    """Before-tool callback of the flight searcher: a group over GROUP_MAX is searched for one passenger (a live school
    group of 14 broke every flight source and silently got a 28-hour bus), then the MCP cache."""
    if getattr(tool, "name", "") in FLIGHT_TOOLS and _group_size(tool_context) > GROUP_MAX and "adults" in args:
        args["adults"] = 1
    return await mcp_cache_before(tool, args, tool_context)


def scale_group_fares(response, n: int):
    """The one-passenger results of a group search, priced for the whole group with a note."""
    if not (isinstance(response, dict) and response.get("status") == "ok"):
        return None
    scaled = copy.deepcopy(response)
    for option in scaled.get("results") or []:
        if not isinstance(option, dict):
            continue
        if option.get("price_eur") is not None:
            option["price_eur"] = round(float(option["price_eur"]) * n, 2)
        details = re.sub(r";?\s*price is the total for 1 adults?(?:\(s\))?[^;]*", "", str(option.get("details") or ""))
        option["details"] = (details + "; " + GROUP_NOTE.format(n=n)).strip("; ")
    return scaled


async def flight_tool_after(tool, args, tool_context, tool_response):
    """After-tool callback of the flight searcher: the MCP normalising and cache, then group fares (see above)."""
    response = await mcp_cache_after(tool, args, tool_context, tool_response)
    n = _group_size(tool_context)
    if getattr(tool, "name", "") not in FLIGHT_TOOLS or n <= GROUP_MAX or int(args.get("adults") or 0) != 1:
        return response
    scaled = scale_group_fares(response if response is not None else tool_response, n)
    return scaled if scaled is not None else response


def build_flight_searcher():
    return research_agent(
        name="flight_searcher",
        description="Finds outbound and return flights with live prices.",
        role=FLIGHT_ROLE, steps=FLIGHT_STEPS, shape=TRANSPORT_SHAPE, example=FLIGHT_EXAMPLE,
        tools=[kiwi_toolset(), search_google_flights, search_serpapi_flights],
        output_key="flight_options",
        max_calls=6,
        before_agent_callback=skip_when_excluded({"flight"}, "flight_options", "plane"),
        before_tool_callback=flight_tool_before,  # group passengers, then the MCP cache
        after_tool_callback=flight_tool_after,
        before_grounding=[mark_far_airports],
    )


def routed_ferries(session) -> list[dict]:
    """Ferry connections in this session's plan_public_transport results (the routing tool's own data)."""
    found = []
    for event in getattr(session, "events", None) or []:
        for part in getattr(getattr(event, "content", None), "parts", None) or []:
            fr = getattr(part, "function_response", None)
            if fr is None or fr.name != "plan_public_transport" or not isinstance(fr.response, dict):
                continue
            found += [r for r in fr.response.get("results") or [] if isinstance(r, dict) and r.get("mode") == "ferry"]
    return found


def keep_car_ferry(callback_context):
    """After-agent callback of the ground searcher (before grounding), for a trip in the travellers' own car: a road
    route over the sea needs the car ferry. If the routing tool found a ferry but the agent reported none (a live
    Helsinki-Tallinn run "drove" 100 km across the Gulf of Finland), the tool's ferry options are added, per
    direction, with their source; the car fare stays unknown. Nothing changes for other trips."""
    state = callback_context.state
    brief = state.get("trip_brief") or {}
    if not brief.get("own_car"):
        return None
    try:
        data = extract_json(state.get("ground_options") or "")
    except ValueError:
        data = {"outbound": [], "return": [], "fallback_links": [], "notes": ""}
    ferries = routed_ferries(callback_context.session)
    added = 0
    for leg, day in (("outbound", brief.get("start_date")), ("return", brief.get("end_date"))):
        options = [o for o in data.get(leg) or [] if isinstance(o, dict)]
        if any(o.get("mode") == "ferry" for o in options):
            continue
        same_day = [dict(f) for f in ferries if str(f.get("depart", ""))[:10] == day][:3]
        for f in same_day:
            f["details"] = (str(f.get("details") or "") + " | car ferry: the road route crosses the sea; car fare "
                            "unknown, check with the operator").strip(" |")
        data[leg] = same_day + options
        added += len(same_day)
    if added:
        data["notes"] = (str(data.get("notes") or "") + f" Added {added} car ferry option(s) from the routing "
                         "tool: the road route crosses the sea.").strip()
        state["ground_options"] = "```json\n" + json.dumps(data, ensure_ascii=False, indent=1) + "\n```"
    return None


def price_web_fares(callback_context):
    """After-agent callback of the ground searcher (before grounding): a web fare is copied per person
    (fare_per_person_eur) and the group total is computed here. A live Tallinn-Riga run for two set price_eur to the
    €24 per-person bus fare, so the plan and its cost counted half the tickets while the guide said €48."""
    state = callback_context.state
    n = int((state.get("trip_brief") or {}).get("travellers") or 1)
    try:
        data = extract_json(state.get("ground_options") or "")
    except ValueError:
        return None
    changed = False
    for leg in ("outbound", "return"):
        for option in data.get(leg) or []:
            if not isinstance(option, dict) or "fare_per_person_eur" not in option:
                continue
            fare = option.pop("fare_per_person_eur")
            changed = True
            if isinstance(fare, (int, float)) and not isinstance(fare, bool) and fare >= 0:
                option["price_eur"] = round(float(fare) * n, 2)
                option["price_source"] = "web_estimate"
                if n > 1:
                    option["details"] = (str(option.get("details") or "")
                                         + f"; price is €{fare:g} per person x {n} travellers").strip("; ")
    if changed:
        _rewrite(state, "ground_options", data)
    return None


def build_ground_searcher():
    return research_agent(
        name="ground_searcher",
        description="Finds train, bus and ferry connections with estimated fares.",
        role=GROUND_ROLE, steps=GROUND_STEPS, shape=GROUND_SHAPE, example=GROUND_EXAMPLE,
        tools=[plan_public_transport, web_search, geocode_place],
        output_key="ground_options",
        max_calls=6,
        before_agent_callback=skip_when_excluded({"train", "bus", "ferry"}, "ground_options", "train, bus or ferry"),
        before_tool_callback=snap_transit_points,
        # web fares per person -> group totals; an own-car trip over the sea keeps the routing tool's car ferry
        before_grounding=[price_web_fares, keep_car_ferry],
    )
