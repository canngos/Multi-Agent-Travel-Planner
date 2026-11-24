"""Airport coordinates by IATA code (airportsdata, MIT licence, offline), and the airports a flight option uses.

A live Kyiv run planned "11:40 Arrive in Kyiv" for flights that land in Chișinău (RMO), 470 km away: nothing compared
the arrival airport with the destination. The flight tools give the route only as text in `details`
(Kiwi "HEL → CPH → RMO; ...", SerpApi "AY951 HEL 06:20→CPH 06:55 | ..."), so the codes are read from there.
"""
import re
from functools import lru_cache

from .geo import haversine_km

FAR_AIRPORT_KM = 100  # an airport farther than this from the destination is not "arriving there"
_ROUTE = re.compile(r"^\s*([A-Z]{3})((?:\s*→\s*[A-Z]{3})+)")  # Kiwi: "HEL → CPH → RMO" at the start
_SEGMENT = re.compile(r"\b([A-Z]{3}) \d{1,2}:\d{2}\s*→\s*([A-Z]{3})\b")  # SerpApi: "HEL 06:20→CPH 06:55"


@lru_cache(maxsize=1)
def _airports() -> dict:
    import airportsdata  # loaded on first use: ~28,000 airports
    return airportsdata.load("IATA")


def airport(code: str) -> dict | None:
    """{"iata", "name", "city", "country", "lat", "lon"} of an airport, or None if the code is unknown."""
    a = _airports().get(str(code or "").upper())
    if not a:
        return None
    return {"iata": a["iata"], "name": a["name"], "city": a["city"], "country": a["country"],
            "lat": float(a["lat"]), "lon": float(a["lon"])}


def route_codes(details: str) -> list[str]:
    """The airports of a flight option, in order, from its details text ([] if none can be read)."""
    text = str(details or "")
    m = _ROUTE.match(text)
    if m:
        return [m.group(1)] + re.findall(r"[A-Z]{3}", m.group(2))
    segments = _SEGMENT.findall(text)
    if segments:
        return [segments[0][0]] + [to for _, to in segments]
    return []


def far_airport(option: dict, leg: str, lat, lon) -> dict | None:
    """The destination-side airport of a flight (the last one outbound, the first one on the return) when it is
    more than FAR_AIRPORT_KM from (lat, lon): {"iata", "city", "km", ...}. None when it is near or unknown."""
    if not isinstance(option, dict) or option.get("mode") != "flight" or lat is None or lon is None:
        return None
    codes = route_codes(option.get("details"))
    if not codes:
        return None
    a = airport(codes[-1] if leg == "outbound" else codes[0])
    if not a:
        return None
    km = haversine_km(float(lat), float(lon), a["lat"], a["lon"])
    return {**a, "km": round(km)} if km > FAR_AIRPORT_KM else None


def far_airport_note(found: dict, leg: str, destination: str) -> str:
    verb = "lands at" if leg == "outbound" else "leaves from"
    return f"FAR AIRPORT: {verb} {found['iata']} ({found['city']}), {found['km']} km from {destination}"
