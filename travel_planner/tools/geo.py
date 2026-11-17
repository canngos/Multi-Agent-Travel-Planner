"""Geocoding (Nominatim / OpenStreetMap) and distance-based clustering."""
import math

from ._http import get_json
from ._result import ToolError, ok, safe_tool

NOMINATIM = "https://nominatim.openstreetmap.org"


@safe_tool
async def geocode_place(name: str) -> dict:
    """Finds the coordinates and country of a city or place.

    Args:
        name: Place name, e.g. "Lisbon" or "Tampere, Finland".
    """
    data = await get_json(
        f"{NOMINATIM}/search",
        namespace="nominatim",
        params={"q": name, "format": "jsonv2", "limit": 1, "addressdetails": 1, "accept-language": "en"},
    )
    if not data:
        raise ToolError(f"No place found for '{name}'. Ask the user to rephrase or add the country.")
    top = data[0]
    address = top.get("address", {})
    return ok(
        [{
            "name": top.get("name") or name,
            "display_name": top.get("display_name"),
            "lat": round(float(top["lat"]), 5),
            "lon": round(float(top["lon"]), 5),
            "country": address.get("country"),
            "country_code": (address.get("country_code") or "").upper(),
            "type": top.get("addresstype") or top.get("type"),
        }],
        attribution="© OpenStreetMap contributors (ODbL)",
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


@safe_tool
def cluster_by_distance(places: list[dict], n_days: int) -> dict:
    """Groups places into n_days clusters of nearby places, one cluster per day.

    Each cluster is ordered as a walking route with its straight-line walking
    distance in km. Places without lat/lon are listed under "unplaced".

    Args:
        places: List of objects with at least "name", "lat", "lon" (other keys are kept).
        n_days: Number of days (clusters) to make.
    """
    if n_days < 1:
        raise ToolError("n_days must be at least 1")
    located = [p for p in places if p.get("lat") is not None and p.get("lon") is not None]
    unplaced = [p.get("name", "?") for p in places if p.get("lat") is None or p.get("lon") is None]
    if not located:
        raise ToolError("No places with coordinates to cluster")
    k = min(n_days, len(located))
    cap = math.ceil(len(located) / k) + 1  # keeps days roughly balanced

    # Deterministic seeding: farthest-point traversal from the first place.
    centres = [(located[0]["lat"], located[0]["lon"])]
    while len(centres) < k:
        far = max(located, key=lambda p: min(_dist(p, c) for c in centres))
        centres.append((far["lat"], far["lon"]))

    groups: list[list[dict]] = []
    for _ in range(10):  # capacity-limited k-means
        groups = [[] for _ in range(k)]
        for p in sorted(located, key=lambda p: min(_dist(p, c) for c in centres)):
            ranked = sorted(range(k), key=lambda i: _dist(p, centres[i]))
            target = next((i for i in ranked if len(groups[i]) < cap), ranked[0])
            groups[target].append(p)
        new_centres = [
            (sum(p["lat"] for p in g) / len(g), sum(p["lon"] for p in g) / len(g)) if g else centres[i]
            for i, g in enumerate(groups)
        ]
        if new_centres == centres:
            break
        centres = new_centres

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
    return ok(days, unplaced=unplaced)
