"""Restaurants, cafés and bars near a point from OpenStreetMap (Overpass API)."""
from ..models import Place
from ._overpass import ATTRIBUTION, element_point, osm_url, overpass
from ._result import ToolError, ok, safe_tool
from .geo import haversine_km

KIND = {"restaurant": "restaurant", "fast_food": "restaurant", "food_court": "restaurant",
        "cafe": "cafe", "bar": "bar", "pub": "bar"}
CHAINS_TAG = "brand"  # chains carry a brand tag; travellers want local places first


def _query(lat: float, lon: float, radius_m: int) -> str:
    amenities = "|".join(KIND)
    return (
        f'[out:json][timeout:25];'
        f'(node["amenity"~"^({amenities})$"]["name"](around:{radius_m},{lat},{lon});'
        f'way["amenity"~"^({amenities})$"]["name"](around:{radius_m},{lat},{lon}););'
        f'out center tags 300;'
    )


def to_place(el: dict, lat: float, lon: float) -> tuple[Place, float, bool]:
    tags = el.get("tags", {})
    plat, plon = element_point(el)
    cuisine = tags.get("cuisine", "").replace(";", ", ").replace("_", " ")
    bits = [f"cuisine: {cuisine}" if cuisine else "", tags.get("description", "")]
    if tags.get("outdoor_seating") == "yes":
        bits.append("outdoor seating")
    urls = [osm_url(el)]
    if tags.get("website") or tags.get("contact:website"):
        urls.append(tags.get("website") or tags.get("contact:website"))
    place = Place(
        kind=KIND.get(tags.get("amenity", ""), "restaurant"),
        name=tags["name"],
        lat=round(plat, 6),
        lon=round(plon, 6),
        summary="; ".join(b for b in bits if b),
        budget_tier="low" if tags.get("amenity") in ("fast_food", "food_court") else None,
        hours=tags.get("opening_hours"),
        source_urls=urls,
    )
    return place, haversine_km(lat, lon, plat, plon), CHAINS_TAG in tags


@safe_tool
async def find_restaurants(lat: float, lon: float, radius_m: int = 800, cuisine: str = "", limit: int = 20) -> dict:
    """Finds named restaurants, cafés and bars near a point (e.g. near a sight),
    local places before chains, nearest first. Includes cuisine and opening hours
    when OpenStreetMap has them; prices are not available from this source.

    Args:
        lat: Latitude of the point (e.g. a sight).
        lon: Longitude of the point.
        radius_m: Search radius in metres (max 2000).
        cuisine: Optional cuisine filter, e.g. "portuguese" or "seafood".
        limit: Max results (max 40).
    """
    radius_m = max(100, min(int(radius_m), 2000))
    query = _query(round(lat, 5), round(lon, 5), radius_m)
    data = await overpass(query, unavailable_hint="Use Wikivoyage 'Eat' listings instead.")

    rows = []
    for el in data.get("elements", []):
        if not el.get("tags", {}).get("name"):
            continue
        if not (el.get("lat") or el.get("center")):
            continue
        rows.append(to_place(el, lat, lon))
    if cuisine:
        rows = [r for r in rows if cuisine.lower() in r[0].summary.lower() or cuisine.lower() in r[0].name.lower()]
    if not rows:
        raise ToolError("No matching places found; try a larger radius or drop the cuisine filter")
    rows.sort(key=lambda r: (r[2], r[1]))  # local first, then nearest
    places = [r[0] for r in rows[: max(1, min(int(limit), 40))]]
    return ok(places, attribution=ATTRIBUTION)
