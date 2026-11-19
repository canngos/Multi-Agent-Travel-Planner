"""MCP toolsets: Kiwi.com flights (remote, streamable HTTP) and Airbnb (local, npx over stdio).

Both go through the same disk cache as the REST tools, so a recorded run replays
with OFFLINE=1 without starting or reaching any MCP server:
  * the tool list of each server is cached (CachedMcpToolset.get_tools);
  * tool results are cached by the agent callbacks mcp_cache_before / mcp_cache_after,
    which every agent that uses these toolsets must register.
"""
import asyncio
import json
import logging
import os
import re
from datetime import date
import shutil
import sys
from typing import Any

from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams, StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_tool import McpTool
from mcp import StdioServerParameters
from mcp.types import Tool as McpToolSpec

from .. import config
from ..models import StayOption, TransportOption
from . import _cache
from ._result import error, ok
from .currency import parse_number
from .geo import haversine_km, neighbourhood

log = logging.getLogger(__name__)

KIWI_URL = "https://mcp.kiwi.com"
AIRBNB_PACKAGE = "@openbnb/mcp-server-airbnb@0.3.0"  # pinned: the server scrapes pages and changes behaviour between versions
TOOL_LIST_MAX_AGE_S = 7 * 24 * 3600

# The callbacks below turn raw results into our shared models, so the servers' own descriptions of
# their result format (and Kiwi's display instructions, e.g. "end with a fun fact") no longer apply.
DESCRIPTIONS = {
    "search-flight": (
        "Searches Kiwi.com for flights between two places (IATA codes or city names) on a date. "
        "Dates are dd/mm/yyyy. Always pass currency 'EUR'. Omit returnDate for one-way. "
        "Returns {status, results}: each result is a flight option with operator, local depart/arrive times, "
        "duration_min, stops, price_eur (total for all passengers), url (Kiwi booking link) and details. "
        "details may contain warnings (AIRPORT CHANGE, self-transfer, no cabin bag) that must be shown to the user."
    ),
    "airbnb_search": (
        "Searches Airbnb listings for a location and dates (YYYY-MM-DD). Always pass checkin, checkout and adults. "
        "Returns {status, results}: each result is a stay with name, area (neighbourhood), lat/lon, "
        "price_per_night_eur, total_price_eur (incl. fees), rating (0-5), url (listing link with dates) and details "
        "(distance from the centre, beds). Listings far from the destination centre are dropped; for a specific "
        "neighbourhood, search e.g. 'Alfama, Lisbon'."
    ),
}


def _npx() -> str:
    """On Windows npx is a .cmd script, which subprocess only finds by its full name."""
    if sys.platform == "win32":
        return shutil.which("npx.cmd") or "npx.cmd"
    return shutil.which("npx") or "npx"


class CachedMcpToolset(McpToolset):
    """McpToolset whose tool list is stored on disk (and served from it when OFFLINE=1).

    It fills ADK's in-memory tool-list cache from our disk cache before delegating to
    McpToolset.get_tools, so ADK still builds and filters the McpTool objects itself.
    """

    def __init__(self, *, server: str, **kwargs: Any) -> None:
        kwargs.setdefault("tool_list_cache_ttl_seconds", 3600)
        super().__init__(**kwargs)
        self.server = server

    async def get_tools(self, readonly_context=None):
        headers = await self._build_headers(readonly_context)
        key = self._tool_list_cache_key(headers)
        if self._read_tool_list_cache(key) is None:
            async def produce():
                result = await self._execute_with_session(
                    lambda session: session.list_tools(), "Failed to get tools from MCP server",
                    readonly_context, headers=headers,
                )
                return [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in result.tools]

            specs = await _cache.cached("mcp_tools", {"server": self.server}, produce, TOOL_LIST_MAX_AGE_S)
            specs = [{**s, "description": DESCRIPTIONS.get(s["name"], s.get("description"))} for s in specs]
            self._write_tool_list_cache(key, [McpToolSpec.model_validate(s) for s in specs])
        return await super().get_tools(readonly_context)


def kiwi_toolset() -> CachedMcpToolset:
    return CachedMcpToolset(
        server="kiwi",
        connection_params=StreamableHTTPConnectionParams(url=KIWI_URL, timeout=30, sse_read_timeout=120),
        tool_filter=["search-flight"],
    )


def airbnb_args() -> list[str]:
    """Airbnb's robots.txt disallows search pages; the project owner chose to ignore it for this
    low-volume, cached course demo (Decisions log). Set AIRBNB_IGNORE_ROBOTS_TXT=0 to respect it."""
    args = ["-y", AIRBNB_PACKAGE]
    if os.environ.get("AIRBNB_IGNORE_ROBOTS_TXT", "1") != "0":
        args.append("--ignore-robots-txt")
    return args


def airbnb_toolset() -> CachedMcpToolset:
    return CachedMcpToolset(
        server="airbnb",
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(command=_npx(), args=airbnb_args()),
            timeout=60,  # the first run downloads the package
        ),
    )


# ---------------------------------------------------------------- result caching + normalising (agent callbacks)

RESULT_MAX_AGE_S = {"search-flight": 12 * 3600}
DEFAULT_RESULT_MAX_AGE_S = 24 * 3600
MAX_TEXT_CHARS = 6000  # cap for results we do not normalise
MAX_STAY_DISTANCE_KM = 15  # Airbnb searches whole regions; drop listings far from the destination centre


def _cache_request(tool_name: str, args: dict) -> dict:
    return {"tool": tool_name, "args": {k: args[k] for k in sorted(args)}}


def _payload(result: dict) -> tuple[Any, str]:
    """(structured content or parsed JSON text or None, raw text) of an MCP CallToolResult dump."""
    text = "".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")
    data = result.get("structuredContent")
    if data is None:
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    return data, text


def _hhmm(seconds: int) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60}h{minutes % 60:02d}"


def kiwi_itinerary_to_option(it: dict, adults: int) -> TransportOption:
    out = it["outbound"]
    segs = out.get("segments") or []
    airlines = list(dict.fromkeys(s.get("carrierName") or s.get("carrier") or "?" for s in segs))
    flights = " | ".join(f"{s.get('flightNumber', '?')} {s.get('from')} {s.get('departureTime', '')[11:16]}"
                         f"→{s.get('to')} {s.get('arrivalTime', '')[11:16]}" for s in segs)
    notes = [" → ".join(out.get("route") or []), flights]
    changes = [f"{a.get('to')}→{b.get('from')}" for a, b in zip(segs, segs[1:]) if a.get("to") != b.get("from")]
    if changes:
        notes.append(f"AIRPORT CHANGE during layover ({', '.join(changes)}): you must travel between airports yourself")
    if len(airlines) > 1 and len(segs) > 1:
        notes.append("different airlines: possibly a self-transfer on separate tickets, check on Kiwi")
    bags = it.get("baggage") or {}
    notes.append(f"included bags for all travellers: {bags.get('personalItem', 0)} personal item, "
                 f"{bags.get('cabinBag', 0)} cabin, {bags.get('checkedBag', 0)} checked")
    if it.get("inbound"):
        back = it["inbound"]
        notes.append(f"return {' → '.join(back.get('route') or [])} {back.get('departureTime', '')[:16]}"
                     f" → {back.get('arrivalTime', '')[:16]} ({_hhmm(back.get('durationSeconds') or 0)})")
    notes.append(f"price is the total for {adults} adult(s){', both directions' if it.get('inbound') else ''}")
    return TransportOption(
        mode="flight",
        operator=", ".join(airlines),
        depart=out["departureTime"][:16],
        arrive=out["arrivalTime"][:16],
        duration_min=round((out.get("durationSeconds") or it.get("totalDurationSeconds") or 0) / 60),
        stops=out.get("stops", max(len(segs) - 1, 0)),
        price_eur=float(it["price"]) if it.get("price") is not None else None,
        price_source="live" if it.get("price") is not None else None,
        source="kiwi",
        url=it.get("bookingUrl") or "https://www.kiwi.com/",
        details="; ".join(n for n in notes if n),
    )


async def normalise_kiwi(result: dict, args: dict, max_results: int = 10) -> dict:
    data, text = _payload(result)
    if result.get("isError") or not isinstance(data, dict):
        return error(f"Kiwi search failed: {text[:300] or 'no data'}")
    if data.get("error"):
        return error(f"Kiwi search failed: {data['error']}")
    adults = int((data.get("passengers") or {}).get("adults") or args.get("adults") or 1)
    if data.get("currency", "EUR") != "EUR":
        return error(f"Kiwi returned prices in {data.get('currency')}; search again with currency 'EUR'")
    options = [kiwi_itinerary_to_option(it, adults) for it in data.get("itineraries") or [] if it.get("outbound")]
    if not options:
        return error("Kiwi found no flights for this route and date")
    return ok(options[:max_results], query=data.get("query"))


def _euros(text: str) -> float | None:
    """The first euro amount in Airbnb's text, in English ('€1,234.56') or European ('€1.234,56') notation."""
    m = re.search(r"€\s?(\d[\d.,]*)", text or "")
    return parse_number(m.group(1).rstrip(".,")) if m else None


def airbnb_listing_to_option(r: dict, nights: int, area: str, query: str, km: float | None = None) -> StayOption | None:
    stay = r.get("demandStayListing") or {}
    coord = (stay.get("location") or {}).get("coordinate") or {}
    price = r.get("structuredDisplayPrice") or {}
    breakdown = (price.get("explanationData") or {}).get("priceDetails") or ""
    total_m = re.search(r"Total:\s*(€\s?\d[\d.,]*)", breakdown)
    total = _euros(total_m.group(1)) if total_m else _euros((price.get("primaryLine") or {}).get("accessibilityLabel", ""))
    if total is None or coord.get("latitude") is None:
        return None
    name = ((stay.get("description") or {}).get("name") or {}).get("localizedStringWithTranslationPreference") or "Airbnb listing"
    rating_m = re.match(r"([\d.]+) out of 5 average rating,\s*([\d,]+) reviews", r.get("avgRatingA11yLabel") or "")
    info = r.get("structuredContent") or {}
    bits = [info.get("primaryLine"), r.get("badges"), f"{km:.1f} km from the centre" if km is not None else None]
    if rating_m:
        bits.append(f"Airbnb rating {rating_m.group(1)}/5 ({rating_m.group(2)} reviews)")
    bits.append(breakdown or "total incl. fees as shown by Airbnb")
    return StayOption(
        kind="airbnb",
        name=name,
        area=area,
        lat=coord["latitude"],
        lon=coord["longitude"],
        price_per_night_eur=round(total / nights, 2),
        total_price_eur=total,
        rating=float(rating_m.group(1)) if rating_m else None,
        price_source="live",
        source="airbnb",
        url=f"{r.get('url') or 'https://www.airbnb.com/rooms/' + str(r.get('id'))}?{query}",
        details="; ".join(b for b in bits if b),
    )


async def normalise_airbnb_search(result: dict, args: dict, centre: tuple[float, float] | None = None,
                                  max_results: int = 10) -> dict:
    data, text = _payload(result)
    if result.get("isError") or not isinstance(data, dict) or data.get("error"):
        reason = data.get("error") if isinstance(data, dict) else text[:300]
        return error(f"Airbnb search failed: {reason or 'no data'}")
    if not (args.get("checkin") and args.get("checkout")):
        return error("Airbnb search needs checkin and checkout dates to show prices")
    nights = (date.fromisoformat(args["checkout"]) - date.fromisoformat(args["checkin"])).days
    query = f"check_in={args['checkin']}&check_out={args['checkout']}&adults={int(args.get('adults') or 1)}"
    def coord(r: dict) -> dict | None:
        return ((r.get("demandStayListing") or {}).get("location") or {}).get("coordinate")

    listings = [(r, coord(r)) for r in data.get("searchResults") or [] if coord(r)]
    found = len(listings)
    if centre:
        listings = [(r, c, haversine_km(centre[0], centre[1], c["latitude"], c["longitude"])) for r, c in listings]
        listings = [x for x in listings if x[2] <= MAX_STAY_DISTANCE_KM]
    else:
        listings = [(r, c, None) for r, c in listings]
    listings = listings[:max_results]
    if not listings:
        return error(f"Airbnb found {found} listings, none within {MAX_STAY_DISTANCE_KM} km of the centre; "
                     "search again with a neighbourhood, e.g. 'Baixa, Lisbon'")
    areas = await asyncio.gather(*(neighbourhood(c["latitude"], c["longitude"]) for _, c, _ in listings))
    stays = [o for o in (airbnb_listing_to_option(r, nights, a, query, km) for (r, _, km), a in zip(listings, areas)) if o]
    if not stays:
        return error("Airbnb returned no priced listings for these dates")
    stays.sort(key=lambda o: o.total_price_eur)
    extra = {"dropped_far_away": found - len(listings)} if centre else {
        "note": "no destination coordinates in the trip brief: check `area`, the search can cover a whole region"}
    return ok(stays, nights=nights, search_url=data.get("searchUrl"), **extra)


async def normalise_generic(result: dict) -> dict:
    data, text = _payload(result)
    if result.get("isError"):
        return error(text[:500] or "MCP tool reported an error")
    if data is not None:
        return ok([data] if not isinstance(data, list) else data)
    return ok([], text=text[:MAX_TEXT_CHARS] + ("…" if len(text) > MAX_TEXT_CHARS else ""))


NORMALISERS = {"search-flight": normalise_kiwi}


async def normalise(tool_name: str, args: dict, result: Any, centre: tuple[float, float] | None = None) -> dict:
    """Turns a raw MCP CallToolResult dump into our {"status": ...} shape (and shared models)."""
    if not isinstance(result, dict):
        return error(f"Unexpected MCP result type {type(result).__name__}")
    if "error" in result and "content" not in result:  # ADK's own error dict (connection failure etc.)
        return error(str(result["error"])[:500])
    try:
        if tool_name == "airbnb_search":
            return await normalise_airbnb_search(result, args, centre)
        if tool_name in NORMALISERS:
            return await NORMALISERS[tool_name](result, args)
        return await normalise_generic(result)
    except Exception as exc:  # a changed result format must not crash the agent
        log.warning("could not normalise %s result: %s", tool_name, exc)
        return error(f"Could not read the {tool_name} result ({type(exc).__name__})")


def _destination(tool_context) -> tuple[float, float] | None:
    """Destination centre from the trip brief in session state (set by save_trip_brief)."""
    try:
        brief = tool_context.state.get("trip_brief") or {}
    except AttributeError:
        return None
    lat, lon = brief.get("destination_lat"), brief.get("destination_lon")
    return (lat, lon) if lat is not None and lon is not None else None


def _is_normalised(response: Any) -> bool:
    return isinstance(response, dict) and response.get("status") in ("ok", "error") and "content" not in response


def _round_trip_arg(name: str) -> bool:
    return name in ("returnDate", "returnDateTo", "returnDateFlexDays") or name.startswith(("ret_", "nights_in_dst"))


async def mcp_cache_before(tool, args: dict, tool_context) -> dict | None:
    """before_tool_callback: serves MCP results from the disk cache (or fails cleanly when OFFLINE).

    Kiwi searches are made one-way: a round-trip search prices both directions together, and that total would
    be read as the outbound leg's price, with the return flight added on top."""
    if not isinstance(tool, McpTool):
        return None
    if tool.name == "search-flight":
        for name in [k for k in args if _round_trip_arg(k)]:
            del args[name]  # ADK calls the tool with this same dict
    request = _cache_request(tool.name, args)
    if not config.refresh_cache():
        hit, response = _cache.read("mcp", request, RESULT_MAX_AGE_S.get(tool.name, DEFAULT_RESULT_MAX_AGE_S))
        if hit:
            return response
    if config.offline():
        return error(f"OFFLINE mode and no recorded {tool.name} result for these arguments")
    return None


async def mcp_cache_after(tool, args: dict, tool_context, tool_response) -> dict | None:
    """after_tool_callback: normalises MCP results to our shape and caches successful ones.

    ADK runs after-callbacks even when a before-callback answered the call, so a result that is
    already in our shape (served from the cache, or the OFFLINE miss error) is left unchanged.
    """
    if not isinstance(tool, McpTool) or _is_normalised(tool_response):
        return None
    response = await normalise(tool.name, args, tool_response, _destination(tool_context))
    if response["status"] == "ok":
        _cache.write("mcp", _cache_request(tool.name, args), response)
    return response
