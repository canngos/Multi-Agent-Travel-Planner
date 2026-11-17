"""Hotel rates from liteAPI (Nuitée), plus search deep links for Booking.com and Airbnb."""
import asyncio
from datetime import date
from urllib.parse import urlencode

from ..models import StayOption
from ._http import api_key, post_json
from ._result import ToolError, ok, safe_tool
from .geo import neighbourhood

LITEAPI_RATES = "https://api.liteapi.travel/v3.0/hotels/rates"


def booking_url(query: str, checkin: str, checkout: str, adults: int) -> str:
    return "https://www.booking.com/searchresults.html?" + urlencode(
        {"ss": query, "checkin": checkin, "checkout": checkout, "group_adults": adults, "no_rooms": 1}
    )


def airbnb_url(city: str, checkin: str, checkout: str, adults: int) -> str:
    return f"https://www.airbnb.com/s/{city.replace(' ', '-')}/homes?" + urlencode(
        {"checkin": checkin, "checkout": checkout, "adults": adults}
    )


def cheapest_offer(hotel_rates: dict) -> tuple[float, dict] | None:
    """Lowest total offer for a hotel and its first rate (room name, board, taxes)."""
    best = None
    for room in hotel_rates.get("roomTypes", []):
        total = (room.get("offerRetailRate") or {}).get("amount")
        if total is None and room.get("rates"):
            total = room["rates"][0]["retailRate"]["total"][0]["amount"]
        if total is not None and (best is None or total < best[0]):
            best = (float(total), (room.get("rates") or [{}])[0])
    return best


HOTEL_FIELDS = ("id", "name", "latitude", "longitude", "rating", "stars", "review_count", "city_name", "address")


def slim_rates(data: dict) -> dict:
    """Keeps only each hotel's cheapest room and the fields we show (raw replies can be ~20 MB)."""
    if not isinstance(data, dict):
        return data
    slim_data = []
    for hr in data.get("data", []):
        best = cheapest_offer(hr)
        if best is None:
            continue
        total, rate = best
        slim_rate = {
            "name": rate.get("name"),
            "boardName": rate.get("boardName"),
            "retailRate": {"taxesAndFees": (rate.get("retailRate") or {}).get("taxesAndFees")},
            "cancellationPolicies": {"refundableTag": (rate.get("cancellationPolicies") or {}).get("refundableTag")},
        }
        slim_data.append({"hotelId": hr.get("hotelId"),
                          "roomTypes": [{"offerRetailRate": {"amount": total}, "rates": [slim_rate]}]})
    return {
        "sandbox": data.get("sandbox"),
        "error": data.get("error"),
        "hotels": [{k: h.get(k) for k in HOTEL_FIELDS} for h in data.get("hotels", [])],
        "data": slim_data,
    }


def describe(hotel: dict, rate: dict) -> str:
    bits = []
    if hotel.get("stars"):
        bits.append(f"{hotel['stars']}★")
    if hotel.get("rating"):
        bits.append(f"guest rating {hotel['rating']}/10 ({hotel.get('review_count', '?')} reviews)")
    if rate.get("name"):
        bits.append(rate["name"])
    if rate.get("boardName"):
        bits.append(rate["boardName"])
    excluded = [t for t in (rate.get("retailRate") or {}).get("taxesAndFees") or [] if not t.get("included")]
    for t in excluded:
        bits.append(f"excl. {t.get('description', 'tax')} €{t.get('amount')}")
    if (rate.get("cancellationPolicies") or {}).get("refundableTag") == "NRFN":
        bits.append("non-refundable")
    return ", ".join(bits)


@safe_tool
async def search_hotel_rates(
    lat: float, lon: float, checkin: str, checkout: str, adults: int = 2, radius_km: float = 3.0, max_results: int = 10,
) -> dict:
    """Live hotel prices near a point for the given dates, cheapest first.

    Each result has the total price for the stay, price per night, neighbourhood,
    rating (0-10) and a Booking.com search link for that hotel.

    Args:
        lat: Latitude of the area to stay in (e.g. city centre).
        lon: Longitude of the area.
        checkin: Check-in date, ISO "YYYY-MM-DD".
        checkout: Check-out date, ISO "YYYY-MM-DD".
        adults: Number of adults in one room.
        radius_km: Search radius in km (max 10).
        max_results: Max hotels to return (max 15).
    """
    key = api_key("LITEAPI_KEY")
    nights = (date.fromisoformat(checkout) - date.fromisoformat(checkin)).days
    if nights < 1:
        raise ToolError("checkout must be after checkin")
    data = await post_json(
        LITEAPI_RATES,
        namespace="liteapi",
        max_age_s=24 * 3600,
        json={
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "radius": int(min(radius_km, 10) * 1000),
            "checkin": checkin,
            "checkout": checkout,
            "currency": "EUR",
            "guestNationality": "FI",
            "occupancies": [{"adults": int(adults)}],
            "limit": 40,
            "timeout": 10,
        },
        headers={"X-API-Key": key},
        slim=slim_rates,
    )
    if data.get("error"):
        raise ToolError(f"liteAPI: {data['error']}")
    hotels = {h["id"]: h for h in data.get("hotels", [])}
    offers = []
    for hr in data.get("data", []):
        best = cheapest_offer(hr)
        hotel = hotels.get(hr.get("hotelId"))
        if best and hotel and hotel.get("latitude") is not None:
            offers.append((best[0], best[1], hotel))
    if not offers:
        raise ToolError("No hotel rates found for these dates and area")
    offers.sort(key=lambda o: o[0])
    offers = offers[: max(1, min(int(max_results), 15))]
    areas = await asyncio.gather(*(neighbourhood(h["latitude"], h["longitude"]) for _, _, h in offers))
    source = "liteapi (sandbox)" if data.get("sandbox") else "liteapi"
    stays = [
        StayOption(
            kind="hotel",
            name=h["name"],
            area=area or h.get("address", ""),
            lat=h["latitude"],
            lon=h["longitude"],
            price_per_night_eur=round(total / nights, 2),
            total_price_eur=round(total, 2),
            rating=h.get("rating"),
            price_source="live",
            source=source,
            url=booking_url(f"{h['name']}, {h.get('city_name', '')}".strip(", "), checkin, checkout, int(adults)),
            details=describe(h, rate),
        )
        for (total, rate, h), area in zip(offers, areas)
    ]
    return ok(stays, nights=nights, sandbox=bool(data.get("sandbox")))


@safe_tool
def stay_deep_links(city: str, checkin: str, checkout: str, adults: int = 2) -> dict:
    """Search links for Airbnb and Booking.com for a city and dates (last-resort
    fallback when the live searches fail: lets the user check prices themselves).

    Args:
        city: City name, e.g. "Lisbon".
        checkin: Check-in date, ISO "YYYY-MM-DD".
        checkout: Check-out date, ISO "YYYY-MM-DD".
        adults: Number of adults.
    """
    return ok([
        {"site": "airbnb", "url": airbnb_url(city, checkin, checkout, adults)},
        {"site": "booking.com", "url": booking_url(city, checkin, checkout, adults)},
    ])
