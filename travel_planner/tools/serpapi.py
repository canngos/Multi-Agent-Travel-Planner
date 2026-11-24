"""Google Flights and Google Hotels through SerpApi (optional key, free plan ~250 searches/month).

SerpApi fetches Google's results from its own servers, so the EU consent page that
blocks fast-flights does not apply. Every search costs quota, so replies are cached
(flights 12 h, hotels 24 h) and trimmed to the fields we use before caching.
"""
import asyncio
import re
from datetime import date

from ..models import StayOption, TransportOption
from ._http import api_key, get_json
from ._result import ToolError, ok, safe_tool
from .geo import neighbourhood
from .hotels import booking_url

SERPAPI_SEARCH = "https://serpapi.com/search.json"
COMMON = {"currency": "EUR", "hl": "en", "gl": "fi"}
HOTEL_SORT = {"relevance": None, "lowest_price": 3, "highest_rating": 8, "most_reviewed": 13}
HOTEL_MIN_RATING = {3.5: 7, 4.0: 8, 4.5: 9}  # SerpApi "rating" filter codes


async def _search(params: dict, *, slim, max_age_s: float) -> dict:
    key = api_key("SERPAPI_KEY")
    data = await get_json(
        SERPAPI_SEARCH, namespace="serpapi", params={**params, **COMMON, "api_key": key},
        max_age_s=max_age_s, timeout_s=60, slim=slim,
    )
    if data.get("error"):  # e.g. "Google hasn't returned any results for this query."
        raise ToolError(f"SerpApi: {data['error']}")
    return data


# ---------------------------------------------------------------- flights

SEGMENT_FIELDS = ("departure_airport", "arrival_airport", "duration", "airline", "flight_number")


def slim_flights(data: dict) -> dict:
    """Drops logos, booking tokens, carbon data and legroom notes (keeps ~10% of the reply)."""
    if not isinstance(data, dict):
        return data

    def flight(f: dict) -> dict:
        return {
            "price": f.get("price"),
            "total_duration": f.get("total_duration"),
            "layovers": [{k: lay.get(k) for k in ("id", "duration", "overnight")} for lay in f.get("layovers") or []],
            "flights": [{k: s.get(k) for k in SEGMENT_FIELDS} for s in f.get("flights") or []],
        }

    return {
        "error": data.get("error"),
        "google_flights_url": (data.get("search_metadata") or {}).get("google_flights_url"),
        "flights": [flight(f) for f in (data.get("best_flights") or []) + (data.get("other_flights") or [])],
    }


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60}h{minutes % 60:02d}"


def serp_flight_to_option(f: dict, url: str, adults: int) -> TransportOption:
    segs = f["flights"]
    first, last = segs[0], segs[-1]
    legs = [f"{s.get('flight_number', '?')} {s['departure_airport']['id']} {s['departure_airport']['time'][-5:]}"
            f"→{s['arrival_airport']['id']} {s['arrival_airport']['time'][-5:]}" for s in segs]
    layovers = [f"layover {lay['id']} {_hhmm(lay.get('duration') or 0)}{' (overnight)' if lay.get('overnight') else ''}"
                for lay in f.get("layovers") or []]
    price = f.get("price")
    per_person = f", ~€{price / adults:.0f} per person" if price and adults > 1 else ""
    return TransportOption(
        mode="flight",
        operator=", ".join(dict.fromkeys(s.get("airline") or "?" for s in segs)),
        depart=first["departure_airport"]["time"].replace(" ", "T"),
        arrive=last["arrival_airport"]["time"].replace(" ", "T"),
        duration_min=int(f.get("total_duration") or sum(s.get("duration") or 0 for s in segs)),
        stops=len(segs) - 1,
        price_eur=float(price) if price else None,
        price_source="live" if price else None,
        source="google_flights (serpapi)",
        url=url,
        details=" | ".join(legs + layovers) + (f"; price is the total for {adults} adults{per_person}" if price else ""),
    )


@safe_tool
async def search_serpapi_flights(
    origin_iata: str, destination_iata: str, date: str, adults: int = 2, max_results: int = 6,
) -> dict:
    """Flight search on Google Flights via SerpApi: the second fallback, use it only
    when Kiwi and search_google_flights both fail (each call uses the small monthly quota).

    Prices are the TOTAL for all adults; duration includes layovers.

    Args:
        origin_iata: Origin airport IATA code, e.g. "HEL".
        destination_iata: Destination airport IATA code, e.g. "LIS".
        date: Departure date, ISO "YYYY-MM-DD".
        adults: Number of adult passengers.
        max_results: Number of flights to return (max 10).
    """
    adults = max(1, int(adults))
    data = await _search(
        {"engine": "google_flights", "type": 2,  # one way
         "departure_id": origin_iata.strip().upper(), "arrival_id": destination_iata.strip().upper(),
         "outbound_date": date, "adults": adults},
        slim=slim_flights, max_age_s=12 * 3600,
    )
    url = data.get("google_flights_url") or "https://www.google.com/travel/flights"
    options = [serp_flight_to_option(f, url, adults) for f in data.get("flights", []) if f.get("flights")]
    if not options:
        raise ToolError("SerpApi Google Flights found no flights for this route and date")
    options.sort(key=lambda o: (o.price_eur is None, o.price_eur or 0, o.duration_min))
    return ok(options[: max(1, min(int(max_results), 10))], search_url=url)


# ---------------------------------------------------------------- hotels

PROPERTY_FIELDS = ("type", "name", "gps_coordinates", "hotel_class", "extracted_hotel_class",
                   "overall_rating", "reviews", "location_rating", "amenities")


def slim_hotels(data: dict) -> dict:
    """Keeps priced properties and the fields we show; drops images, ads and nearby places (~200 KB -> ~10 KB)."""
    if not isinstance(data, dict):
        return data
    props = []
    for p in data.get("properties") or []:
        total = (p.get("total_rate") or {}).get("extracted_lowest")
        if total is None:
            continue
        props.append({
            **{k: p.get(k) for k in PROPERTY_FIELDS},
            "total": total,
            "before_taxes": (p.get("total_rate") or {}).get("extracted_before_taxes_fees"),
            "amenities": (p.get("amenities") or [])[:6],
        })
    return {
        "error": data.get("error"),
        "google_hotels_url": (data.get("search_metadata") or {}).get("google_hotels_url"),
        "properties": props,
    }


def describe_property(p: dict) -> str:
    bits = []
    if p.get("extracted_hotel_class"):
        bits.append(f"{p['extracted_hotel_class']}★")
    if p.get("overall_rating"):
        bits.append(f"Google rating {p['overall_rating']}/5 ({p.get('reviews', '?')} reviews)")
    if p.get("location_rating"):
        bits.append(f"location {p['location_rating']}/5")
    if p.get("before_taxes") and p["before_taxes"] != p.get("total"):
        bits.append(f"€{p['before_taxes']} before taxes and fees")
    if p.get("amenities"):
        bits.append(", ".join(p["amenities"][:4]))
    return "; ".join(bits)


HOSTEL = re.compile(r"host[ae]l|\bdorm|backpack|capsule", re.IGNORECASE)  # "hostal": a Spanish budget inn
HOSTEL_NOTE = "hostel: the price may be for dorm beds, not a private room; check the room type"
MAX_ADULTS_PER_SEARCH = 6  # Google Hotels (SerpApi) fails above this; a bigger group is searched per room


def is_hostel(name, details="", kind="") -> bool:
    return bool(HOSTEL.search(" ".join(str(x or "") for x in (name, details, kind))))


@safe_tool
async def search_google_hotels(
    area: str, checkin: str, checkout: str, adults: int = 2, sort_by: str = "relevance", min_rating: float = 0.0,
    max_results: int = 10, hotel_class: str = "", exclude_hostels: bool = False, rooms: int = 1,
) -> dict:
    """Hotel prices from Google Hotels via SerpApi (second hotel source next to
    search_hotel_rates; each call uses quota, so call it once per area).

    Each result has the total price for the stay (taxes included), price per night,
    neighbourhood, Google rating (0-5) and a Booking.com search link.

    Args:
        area: City or neighbourhood, e.g. "Lisbon" or "Alfama, Lisbon".
        checkin: Check-in date, ISO "YYYY-MM-DD".
        checkout: Check-out date, ISO "YYYY-MM-DD".
        adults: Number of adults in total.
        sort_by: "relevance", "lowest_price", "highest_rating" or "most_reviewed".
            "lowest_price" alone mostly finds hostels: combine it with min_rating.
        min_rating: Minimum Google rating: 0 (any), 3.5, 4.0 or 4.5.
        max_results: Max hotels to return (max 15).
        hotel_class: Star classes to keep, e.g. "3,4,5" ("" = any).
        exclude_hostels: Leave hostels out.
        rooms: Number of rooms; the adults are spread over them and the price is the total for all rooms.
    """
    nights = (date.fromisoformat(checkout) - date.fromisoformat(checkin)).days
    if nights < 1:
        raise ToolError("checkout must be after checkin")
    if sort_by not in HOTEL_SORT:
        raise ToolError(f"sort_by must be one of {list(HOTEL_SORT)}")
    if min_rating and float(min_rating) not in HOTEL_MIN_RATING:
        raise ToolError(f"min_rating must be 0 or one of {list(HOTEL_MIN_RATING)}")
    classes = ",".join(c for c in re.findall(r"[1-5]", str(hotel_class or "")))
    adults, rooms = max(1, int(adults)), max(1, int(rooms))
    if adults > MAX_ADULTS_PER_SEARCH:  # a big group: price one room and multiply
        rooms = max(rooms, -(-adults // 2))
    rooms = min(rooms, adults)
    per_room = -(-adults // rooms)  # the fullest room sets the price searched
    params = {"engine": "google_hotels", "q": area.strip(), "check_in_date": checkin, "check_out_date": checkout,
              "adults": per_room}
    if HOTEL_SORT[sort_by]:
        params["sort_by"] = HOTEL_SORT[sort_by]
    if min_rating:
        params["rating"] = HOTEL_MIN_RATING[float(min_rating)]
    if classes:
        params["hotel_class"] = classes
    data = await _search(params, slim=slim_hotels, max_age_s=24 * 3600)
    props = [p for p in data.get("properties", []) if (p.get("gps_coordinates") or {}).get("latitude") is not None]
    if exclude_hostels:
        props = [p for p in props if not is_hostel(p.get("name"), kind=p.get("type"))]
    if not props:
        raise ToolError(f"Google Hotels returned no priced hotels for '{area}' on these dates")
    props = props[: max(1, min(int(max_results), 15))]
    if rooms > 1:
        props = [{**p, "total": round(p["total"] * rooms, 2)} for p in props]
    areas = await asyncio.gather(
        *(neighbourhood(p["gps_coordinates"]["latitude"], p["gps_coordinates"]["longitude"]) for p in props)
    )
    city = area.split(",")[-1].strip()
    stays = [
        StayOption(
            kind="hotel",
            name=p["name"],
            area=hood or "",
            lat=p["gps_coordinates"]["latitude"],
            lon=p["gps_coordinates"]["longitude"],
            price_per_night_eur=round(p["total"] / nights, 2),
            total_price_eur=float(p["total"]),
            rating=p.get("overall_rating"),
            price_source="live",
            source="google_hotels (serpapi)",
            url=booking_url(f"{p['name']}, {city}", checkin, checkout, adults, rooms),
            details="; ".join(x for x in (
                f"total for {rooms} rooms ({per_room} adults each)" if rooms > 1 else "",
                describe_property(p),
                HOSTEL_NOTE if is_hostel(p["name"], kind=p.get("type")) else "") if x),
        )
        for p, hood in zip(props, areas)
    ]
    return ok(stays, nights=nights, google_hotels_url=data.get("google_hotels_url"))
