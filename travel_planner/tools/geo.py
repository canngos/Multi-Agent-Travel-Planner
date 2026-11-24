"""Geocoding (Nominatim / OpenStreetMap) and distance-based clustering."""
import math

from ._http import get_json
from ._result import ToolError, ok, safe_tool

NOMINATIM = "https://nominatim.openstreetmap.org"
OPEN_METEO_GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
REGION_TYPES = {"country", "state", "region", "province", "county", "state_district", "archipelago", "island",
                "continent"}  # Nominatim addresstypes that are not a city
SMALL_PLACE_TYPES = {"village", "hamlet", "isolated_dwelling", "locality", "farm", "neighbourhood"}
SMALL_PLACE_IMPORTANCE = 0.5  # a small place below this is an unexpected match for a trip destination


@safe_tool
async def geocode_place(name: str) -> dict:
    """Finds the coordinates and country of a city or place.

    Args:
        name: Place name, e.g. "Lisbon" or "Tampere, Finland".
    """
    try:
        data = await get_json(
            f"{NOMINATIM}/search",
            namespace="nominatim",
            params={"q": name, "format": "jsonv2", "limit": 1, "addressdetails": 1, "accept-language": "en",
                    "extratags": 1},
        )
    except ToolError as exc:  # e.g. HTTP 429 when Nominatim's 1 request/s limit is hit
        fallback = await _open_meteo_geocode(name)
        if fallback is None:
            raise ToolError(f"{exc} (the Open-Meteo geocoder found nothing either)") from exc
        return fallback
    if not data:
        raise ToolError(f"No place found for '{name}'. Ask the user to rephrase or add the country.")
    top = data[0]
    kind = top.get("addresstype") or top.get("type")
    if kind in REGION_TYPES:
        top = await _city_for_region(name, top)
        kind = top.get("addresstype") or top.get("type")
    address = top.get("address", {})
    extra = {}
    if kind in REGION_TYPES and (top.get("extratags") or {}).get("linked_place") == "city":
        kind = "city"  # a capital region that is the city (Tokyo): nothing to ask the user
    if kind in REGION_TYPES:
        # its centre point is no place to search for sights, stays or connections
        extra["warning"] = (f"'{top.get('name') or name}' is a {kind}, not a city: ask the user which city to plan "
                            "(the planner plans one city per trip).")
    elif kind in SMALL_PLACE_TYPES and float(top.get("importance") or 0) < SMALL_PLACE_IMPORTANCE:
        # a live run mapped "Atlantis" silently to a small town: the user must confirm such a match
        extra["warning"] = (f"'{top.get('name') or name}' was found only as a small {kind} in "
                            f"{address.get('country') or 'an unexpected place'} ({top.get('display_name')}): confirm "
                            "with the user that this is the place they mean.")
    return ok(
        [{
            "name": top.get("name") or name,
            "display_name": top.get("display_name"),
            "lat": round(float(top["lat"]), 5),
            "lon": round(float(top["lon"]), 5),
            "country": address.get("country"),
            "country_code": (address.get("country_code") or "").upper(),
            "type": kind,
        }],
        attribution="© OpenStreetMap contributors (ODbL)",
        **extra,
    )


async def _city_for_region(name: str, region: dict) -> dict:
    """A city-state or a capital region is a city to plan: "Singapore" and "Monaco" are countries in OpenStreetMap
    and "Tokyo" a province (a live run asked the user "the 23 wards or another city?"). Tokyo's region carries
    linked_place=city; for the others, a city search for the same name near the region's point gives the city.
    Real regions ("Portugal", "Bavaria") stay regions."""
    if (region.get("extratags") or {}).get("linked_place") == "city":
        return region
    try:
        found = await get_json(
            f"{NOMINATIM}/search", namespace="nominatim",
            params={"q": name, "format": "jsonv2", "limit": 1, "addressdetails": 1, "accept-language": "en",
                    "featureType": "city"},
        )
    except ToolError:
        return region
    if not found:
        return region
    city = found[0]
    near = haversine_km(float(region["lat"]), float(region["lon"]), float(city["lat"]), float(city["lon"])) <= 50
    same = (city.get("name") or "").casefold() == (region.get("name") or name).casefold()
    if near and same:
        return {**city, "addresstype": "city"}
    return region


async def _open_meteo_geocode(name: str) -> dict | None:
    """Fallback geocoder for cities (Open-Meteo, from GeoNames; free, no key). "Tampere, Finland" filters by country."""
    city, _, country = (part.strip() for part in name.partition(","))
    data = await get_json(OPEN_METEO_GEOCODING, namespace="open-meteo-geocoding",
                          params={"name": city, "count": 5, "language": "en", "format": "json"})
    results = (data or {}).get("results") or []
    if country:
        results = [r for r in results if country.lower() in (r.get("country") or "").lower()] or results
    if not results:
        return None
    top = results[0]
    return ok(
        [{"name": top.get("name") or city, "display_name": ", ".join(x for x in (top.get("name"), top.get("admin1"),
                                                                                top.get("country")) if x),
          "lat": round(float(top["latitude"]), 5), "lon": round(float(top["longitude"]), 5),
          "country": top.get("country"), "country_code": (top.get("country_code") or "").upper(),
          "type": "city"}],
        attribution="Geocoding by Open-Meteo (GeoNames, CC BY 4.0)",
    )


async def neighbourhood(lat: float, lon: float) -> str:
    """Neighbourhood name for a coordinate. Helper for hotel/Airbnb results, not an agent tool."""
    try:
        data = await get_json(
            f"{NOMINATIM}/reverse",
            namespace="nominatim",
            params={"lat": round(lat, 4), "lon": round(lon, 4), "format": "jsonv2", "zoom": 15, "accept-language": "en"},
        )
    except ToolError:
        return ""
    address = data.get("address", {}) if isinstance(data, dict) else {}
    for key in ("neighbourhood", "quarter", "suburb", "city_district", "borough", "town", "city"):
        if address.get(key):
            return address[key]
    return ""


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _dist(p: dict, c: tuple[float, float]) -> float:
    return haversine_km(p["lat"], p["lon"], c[0], c[1])


def _route_km(points: list[dict]) -> tuple[list[dict], float]:
    """Orders points by greedy nearest neighbour; returns (ordered points, straight-line km)."""
    if not points:
        return [], 0.0
    remaining = points[1:]
    route, total = [points[0]], 0.0
    while remaining:
        last = route[-1]
        nxt = min(remaining, key=lambda p: haversine_km(last["lat"], last["lon"], p["lat"], p["lon"]))
        total += haversine_km(last["lat"], last["lon"], nxt["lat"], nxt["lon"])
        route.append(nxt)
        remaining.remove(nxt)
    return route, total


def _centre(group: list[dict]) -> tuple[float, float]:
    return sum(p["lat"] for p in group) / len(group), sum(p["lon"] for p in group) / len(group)


def _agglomerate(points: list[dict], k: int, cap: int) -> list[list[dict]]:
    """Merges the two closest groups (centroid distance) until k groups remain.

    A merge may not exceed `cap` places, unless no allowed merge is left. Unlike k-means with
    far-apart seeds, two neighbouring outliers (e.g. Belém Tower and Jerónimos) end up together and
    a dense centre gets split instead. Deterministic: ties go to the earliest pair.
    """
    groups = [[p] for p in points]
    while len(groups) > k:
        pairs = [(haversine_km(*_centre(a), *_centre(b)), i, j)
                 for i, a in enumerate(groups) for j, b in enumerate(groups) if i < j]
        allowed = [t for t in pairs if len(groups[t[1]]) + len(groups[t[2]]) <= cap]
        _, i, j = min(allowed or pairs)
        groups[i] = groups[i] + groups.pop(j)
    return groups


@safe_tool
def cluster_by_distance(places: list[dict], n_days: int, food_places: list[dict] | None = None) -> dict:
    """Groups places into n_days clusters of nearby places, one cluster per day.

    Each cluster is ordered as a walking route with its straight-line walking
    distance in km. Places without lat/lon are listed under "unplaced".
    If food_places are given, each day also lists "food_nearby": the closest ones
    (up to 5 within 2 km of the day's centre, nearest first) for lunch and dinner.

    Args:
        places: Sights: list of objects with at least "name", "lat", "lon" (other keys are kept).
        n_days: Number of days (clusters) to make.
        food_places: Optional restaurants/cafes/bars: objects with "name", "lat", "lon".
    """
    if n_days < 1:
        raise ToolError("n_days must be at least 1")
    located = [p for p in places if p.get("lat") is not None and p.get("lon") is not None]
    unplaced = [p.get("name", "?") for p in places if p.get("lat") is None or p.get("lon") is None]
    if not located:
        raise ToolError("No places with coordinates to cluster")
    k = min(n_days, len(located))
    groups = _agglomerate(located, k, cap=math.ceil(len(located) / k) + 1)  # cap keeps days roughly balanced

    days = []
    for i, g in enumerate(sorted((g for g in groups if g), key=lambda g: -len(g))):
        route, km = _route_km(g)
        days.append({
            "day": i + 1,
            "places": route,
            "walk_km": round(km, 2),
            "centre": {
                "lat": round(sum(p["lat"] for p in g) / len(g), 5),
                "lon": round(sum(p["lon"] for p in g) / len(g), 5),
            },
        })
        if food_places:
            c = _centre(g)
            near = sorted((round(_dist(f, c), 2), f["name"]) for f in food_places
                          if f.get("lat") is not None and f.get("lon") is not None and f.get("name"))
            days[-1]["food_nearby"] = [{"name": n, "km_from_centre": km} for km, n in near if km <= 2.0][:5]
    return ok(days, unplaced=unplaced)
