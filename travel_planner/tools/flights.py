"""Google Flights fallback via the fast-flights library (used only when Kiwi fails).

From EU networks Google often shows a cookie-consent page instead of results.
We do not try to bypass it: the tool reports the problem and returns a Google
Flights search link the user can open in a browser.
"""
import asyncio
import dataclasses
from urllib.parse import urlencode

from fast_flights import FlightQuery, Passengers, create_query, fetch_flights_html
from fast_flights.fetcher import URL as GOOGLE_FLIGHTS_URL
from fast_flights.parser import parse

from ..models import TransportOption
from ._cache import cached
from ._result import ToolError, error, ok, safe_tool


@dataclasses.dataclass
class _Dt:
    date: tuple
    time: tuple


def search_url(query) -> str:
    return f"{GOOGLE_FLIGHTS_URL}?{urlencode(query.params())}"


def is_consent_page(html: str) -> bool:
    head = html[:5000].lower()
    return "consent.google.com" in head or "before you continue" in head


def _fmt(dt) -> str:
    (y, m, d), (hh, mm) = dt.date, dt.time
    return f"{y:04d}-{m:02d}-{d:02d}T{hh:02d}:{mm:02d}"


def flight_to_option(f: dict, url: str) -> TransportOption:
    """f is a dataclasses.asdict(fast_flights.model.Flights)."""
    legs = f["flights"]
    first, last = legs[0], legs[-1]
    route = " → ".join([legs[0]["from_airport"]["code"]] + [leg["to_airport"]["code"] for leg in legs])
    return TransportOption(
        mode="flight",
        operator=", ".join(f["airlines"]) or "unknown",
        depart=_fmt(_Dt(**first["departure"])),
        arrive=_fmt(_Dt(**last["arrival"])),
        duration_min=sum(leg["duration"] for leg in legs),
        stops=len(legs) - 1,
        price_eur=float(f["price"]) if f.get("price") else None,
        price_source="live" if f.get("price") else None,
        source="google_flights",
        url=url,
        details=f"{route}; duration is flying time only (layovers not included); price as shown by Google Flights",
    )


@safe_tool
async def search_google_flights(origin_iata: str, destination_iata: str, date: str, adults: int = 2) -> dict:
    """Fallback flight search on Google Flights (use when the Kiwi search fails).

    Args:
        origin_iata: Origin airport IATA code, e.g. "HEL".
        destination_iata: Destination airport IATA code, e.g. "LIS".
        date: Departure date, ISO "YYYY-MM-DD".
        adults: Number of adult passengers.
    """
    query = create_query(
        flights=[FlightQuery(date=date, from_airport=origin_iata.upper(), to_airport=destination_iata.upper())],
        trip="one-way",
        passengers=Passengers(adults=int(adults)),
        currency="EUR",
        language="en-GB",
    )
    url = search_url(query)

    async def produce():
        html = await asyncio.to_thread(fetch_flights_html, query)
        if is_consent_page(html):
            raise ToolError("Google Flights returned its cookie-consent page (common from EU networks)")
        return [dataclasses.asdict(f) for f in parse(html)]

    request = {"from": origin_iata.upper(), "to": destination_iata.upper(), "date": date, "adults": int(adults)}
    try:
        flights = await cached("google_flights", request, produce, max_age_s=12 * 3600)
    except ToolError as exc:
        return error(f"{exc}. Give the user the search link instead.", search_url=url)
    except Exception as exc:  # parser breaks when Google changes the page
        return error(f"Could not read Google Flights results ({type(exc).__name__}). Give the user the search link.",
                     search_url=url)
    if not flights:
        return error("Google Flights found no flights", search_url=url)
    options = sorted((flight_to_option(f, url) for f in flights), key=lambda o: (o.price_eur or 1e9))
    return ok(options[:8], search_url=url)
