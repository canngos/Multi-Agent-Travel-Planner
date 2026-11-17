"""Sights near a point from OpenTripMap, enriched with each place's detail record."""
import asyncio
import os

from ..models import Place
from ._http import get_json
from ._result import ToolError, ok, safe_tool

OTM = "https://api.opentripmap.com/0.1/en/places"

# Interest words -> OpenTripMap "kinds" categories.
INTEREST_KINDS = {
    "history": "historic",
    "historic": "historic",
    "museum": "museums",
    "museums": "museums",
    "art": "museums,theatres_and_entertainments",
    "architecture": "architecture",
    "religion": "religion",
    "churches": "churches",
    "nature": "natural,gardens_and_parks",
    "parks": "gardens_and_parks",
    "viewpoints": "view_points",
    "culture": "cultural",
}
DEFAULT_KINDS = "historic,cultural,architecture"
INDOOR_KINDS = {"museums", "churches", "cathedrals", "theatres_and_entertainments", "monasteries", "palaces", "castles"}
OUTDOOR_KINDS = {"monuments", "squares", "view_points", "gardens_and_parks", "natural", "bridges", "fountains", "sculptures"}
MAX_DETAILS = 15


def kinds_for(interests: list[str]) -> str:
    kinds = []
    for word in interests:
        for key, value in INTEREST_KINDS.items():
            if key in word.lower():
                kinds.extend(value.split(","))
    return ",".join(dict.fromkeys(kinds)) or DEFAULT_KINDS


def guess_indoor(kinds: str) -> bool | None:
    ks = set(kinds.split(","))
    if ks & INDOOR_KINDS:
        return True
    if ks & OUTDOOR_KINDS:
        return False
    return None


@safe_tool
async def find_sights(lat: float, lon: float, interests: list[str], radius_km: float = 3.0, limit: int = 15) -> dict:
    """Finds notable sights near a point, best rated first, with links and a short
    description (often from Wikipedia, sometimes in the local language).

    Follow up with wiki_summary for an English history of the sights you pick.

    Args:
        lat: Latitude of the area centre (e.g. the city centre).
        lon: Longitude of the area centre.
        interests: Traveller interests, e.g. ["history", "architecture"].
        radius_km: Search radius in km (max 10).
        limit: Max number of sights (max 15).
    """
    key = os.environ.get("OPENTRIPMAP_API_KEY")
    if not key:
        raise ToolError("OPENTRIPMAP_API_KEY is not set in .env")
    limit = max(1, min(int(limit), MAX_DETAILS))
    found = await get_json(
        f"{OTM}/radius",
        namespace="opentripmap",
        params={
            "radius": int(min(radius_km, 10) * 1000),
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "kinds": kinds_for(interests),
            "rate": "3",  # well-known places only
            "format": "json",
            "limit": 60,
            "apikey": key,
        },
    )
    named = [p for p in found if p.get("name")]
    named.sort(key=lambda p: (-int(str(p.get("rate", 0)).rstrip("h") or 0), p.get("dist", 0)))
    # Drop duplicates (same name at several OSM objects).
    seen, picked = set(), []
    for p in named:
        if p["name"].lower() not in seen:
            seen.add(p["name"].lower())
            picked.append(p)
        if len(picked) >= limit:
            break
    if not picked:
        raise ToolError("No well-known sights found; try a larger radius or other interests")

    details = await asyncio.gather(
        *(get_json(f"{OTM}/xid/{p['xid']}", namespace="opentripmap", params={"apikey": key}) for p in picked),
        return_exceptions=True,
    )
    places = []
    for p, d in zip(picked, details):
        d = d if isinstance(d, dict) else {}
        extract = (d.get("wikipedia_extracts") or {}).get("text") or (d.get("info") or {}).get("descr") or ""
        urls = [d.get("otm") or f"https://opentripmap.com/en/card/{p['xid']}", d.get("wikipedia"), d.get("url")]
        places.append(Place(
            kind="sight",
            name=p["name"],
            lat=round(p["point"]["lat"], 6),
            lon=round(p["point"]["lon"], 6),
            summary=extract[:500],
            indoor=guess_indoor(p.get("kinds", "")),
            source_urls=[u for u in urls if u],
        ))
    return ok(places, attribution="Data © OpenTripMap, © OpenStreetMap contributors")
