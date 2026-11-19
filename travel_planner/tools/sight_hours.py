"""Opening hours and entry fees of sights from OpenStreetMap tags.

OSM fills `opening_hours` for roughly 63-82% of museums in Lisbon, Tallinn and Helsinki
(much less for viewpoints and monuments); `fee` says whether entry costs money and
`charge` sometimes gives the amount.

Backends: QLever's OSM SPARQL endpoint first (about 20 s, a planet copy that can lag a few
weeks), then Overpass (live data, but often 15-60 s or down).
"""
import re

from ..models import Place
from ._http import get_json
from ._overpass import ATTRIBUTION, MAX_AGE_S, element_point, osm_url, overpass
from ._result import ToolError, ok, safe_tool
from .currency import eur_rates, is_foreign, parse_money, to_eur
from .geo import haversine_km

TOURISM = ("museum", "gallery", "attraction", "viewpoint", "zoo", "aquarium", "theme_park")
HISTORIC = ("castle", "monument", "monastery", "palace", "fort", "tower", "city_gate", "ruins", "church", "manor")
INDOOR = {"museum", "gallery", "aquarium", "palace", "church", "monastery"}
OUTDOOR = {"viewpoint", "ruins", "fort", "city_gate", "monument", "zoo", "theme_park"}


def _query(lat: float, lon: float, radius_m: int) -> str:
    has_info = '[~"^(opening_hours|fee|charge)$"~"."]'
    around = f"(around:{radius_m},{lat},{lon})"
    return (
        "[out:json][timeout:25];("
        f'nwr["tourism"~"^({"|".join(TOURISM)})$"]["name"]{has_info}{around};'
        f'nwr["historic"~"^({"|".join(HISTORIC)})$"]["name"]{has_info}{around};'
        ");out center tags 400;"
    )


QLEVER_OSM = "https://qlever.dev/api/osm-planet"
QLEVER_TIMEOUT_S = 60.0


def _sparql(lat: float, lon: float, radius_km: float) -> str:
    def values(items):
        return ",".join(f'"{i}"' for i in items)
    return f"""PREFIX osmkey: <https://www.openstreetmap.org/wiki/Key:>
PREFIX osm2rdfkey: <https://osm2rdf.cs.uni-freiburg.de/rdf/key#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#> PREFIX geof: <http://www.opengis.net/def/function/geosparql/>
SELECT ?osm ?name ?name_en ?tourism ?historic ?hours ?fee ?charge ?website ?wikipedia ?wikidata (geof:centroid(?wkt) AS ?c)
WHERE {{
  {{ ?osm osmkey:tourism ?tourism . FILTER(?tourism IN ({values(TOURISM)})) }}
  UNION {{ ?osm osmkey:historic ?historic . FILTER(?historic IN ({values(HISTORIC)})) }}
  ?osm osmkey:name ?name ; geo:hasGeometry/geo:asWKT ?wkt .
  OPTIONAL {{ ?osm osmkey:opening_hours ?hours }} OPTIONAL {{ ?osm osmkey:fee ?fee }}
  OPTIONAL {{ ?osm osmkey:charge ?charge }} OPTIONAL {{ ?osm osmkey:website ?website }}
  OPTIONAL {{ ?osm <https://www.openstreetmap.org/wiki/Key:name:en> ?name_en }}
  OPTIONAL {{ ?osm osm2rdfkey:wikipedia ?wikipedia }} OPTIONAL {{ ?osm osm2rdfkey:wikidata ?wikidata }}
  FILTER(BOUND(?hours) || BOUND(?fee) || BOUND(?charge))
  FILTER(geof:distance(?wkt, "POINT({lon} {lat})"^^geo:wktLiteral) < {radius_km})
}} LIMIT 400"""


def sparql_rows_to_elements(data: dict) -> list[dict]:
    """QLever rows -> Overpass-like elements, so both backends share element_to_place."""
    elements = []
    for row in (data.get("results") or {}).get("bindings") or []:
        v = {k: b.get("value") for k, b in row.items()}
        m = re.match(r"https://www\.openstreetmap\.org/(node|way|relation)/(\d+)", v.get("osm") or "")
        point = re.match(r"POINT\(([-\d.]+) ([-\d.]+)\)", v.get("c") or "")
        if not (m and point):
            continue
        tags = {k: v[k] for k in ("name", "tourism", "historic", "fee", "charge", "website") if v.get(k)}
        if v.get("name_en"):
            tags["name:en"] = v["name_en"]
        if v.get("hours"):
            tags["opening_hours"] = v["hours"]
        if v.get("wikidata"):
            tags["wikidata"] = v["wikidata"].rsplit("/", 1)[-1]
        if v.get("wikipedia", "").startswith("https://en.wikipedia.org/wiki/"):
            tags["wikipedia"] = "en:" + v["wikipedia"].rsplit("/", 1)[-1].replace("%20", " ")
        elements.append({"type": m.group(1), "id": int(m.group(2)), "lat": float(point.group(2)),
                         "lon": float(point.group(1)), "tags": tags})
    return elements


async def _elements(lat: float, lon: float, radius_km: float) -> list[dict]:
    try:
        data = await get_json(QLEVER_OSM, namespace="qlever_osm", params={"query": _sparql(lat, lon, radius_km)},
                              headers={"Accept": "application/sparql-results+json"},
                              max_age_s=MAX_AGE_S, timeout_s=QLEVER_TIMEOUT_S, retries=0)
        return sparql_rows_to_elements(data)
    except ToolError as first:
        try:
            data = await overpass(_query(lat, lon, int(radius_km * 1000)),
                                  unavailable_hint="Use Wikivoyage listings (they carry hours and prices) instead.")
        except ToolError as second:
            raise ToolError(f"QLever OSM: {first}; {second}") from second
        return data.get("elements", [])


def charge_eur(charge: str, rates: dict[str, float] | None = None) -> float | None:
    """'10 EUR', '€ 12.50', 'adult 8 EUR; child 4 EUR' -> the first amount in euro ('20 PLN' needs exchange rates)."""
    money = parse_money(charge or "")
    return to_eur(*money, rates) if money else None


def element_to_place(el: dict, rates: dict[str, float] | None = None) -> Place | None:
    tags = el.get("tags", {})
    lat, lon = element_point(el)
    name = tags.get("name:en") or tags.get("name")
    if not name or lat is None:
        return None
    kind = tags.get("tourism") or tags.get("historic") or "sight"
    fee, charge = tags.get("fee", ""), tags.get("charge", "")
    ticket = charge_eur(charge, rates)
    if ticket is None and fee == "no":
        ticket = 0.0
    bits = [kind.replace("_", " ")]
    if charge and (ticket is None or not re.fullmatch(r"\s*[\d.,]+\s*(EUR|€)\s*", charge)):
        bits.append(f"charge: {charge}")
    elif fee and not charge:
        bits.append("entry fee" if fee == "yes" else ("free entry" if fee == "no" else f"fee: {fee}"))
    urls = [osm_url(el)]
    if tags.get("website") or tags.get("contact:website"):
        urls.append(tags.get("website") or tags.get("contact:website"))
    wiki = tags.get("wikipedia", "")
    if wiki.startswith("en:"):
        urls.append("https://en.wikipedia.org/wiki/" + wiki[3:].replace(" ", "_"))
    return Place(
        kind="sight",
        name=name,
        lat=round(lat, 6),
        lon=round(lon, 6),
        summary="; ".join(bits),
        hours=tags.get("opening_hours"),
        ticket_eur=ticket,
        indoor=True if kind in INDOOR else (False if kind in OUTDOOR else None),
        source_urls=urls,
    )


@safe_tool
async def find_sight_hours(lat: float, lon: float, radius_km: float = 3.0, limit: int = 40) -> dict:
    """Opening hours and entry fees of museums, attractions and historic sights near a
    point, from OpenStreetMap. Use it to fill in hours / ticket prices for sights found
    with other tools (match them by name).

    hours is in OSM format, e.g. "Tu-Su 10:00-18:00; Mo off" ("PH" = public holidays).
    ticket_eur: the euro amount when OSM has it, 0 when entry is free, null when unknown.
    Results with opening hours and well-known sights (with a Wikipedia link) come first.

    Args:
        lat: Latitude of the area centre.
        lon: Longitude of the area centre.
        radius_km: Search radius in km (max 8; e.g. 6 to include outer districts).
        limit: Max results (max 60).
    """
    radius_km = round(max(0.3, min(float(radius_km), 8.0)), 1)
    rows = []
    elements = await _elements(round(lat, 4), round(lon, 4), radius_km)
    foreign = any(is_foreign(el.get("tags", {}).get("charge", "")) for el in elements)
    rates = await eur_rates() if foreign else None  # fetched only for prices in another currency
    for el in elements:
        place = element_to_place(el, rates)
        if place:
            notable = any("wikipedia.org" in u for u in place.source_urls) or "wikidata" in el.get("tags", {})
            rows.append((place.hours is None, not notable, haversine_km(lat, lon, place.lat, place.lon), place))
    if not rows:
        raise ToolError("OpenStreetMap has no sights with opening hours or fees in this area; try a larger radius")
    rows.sort(key=lambda r: r[:3])
    unique, seen = [], set()
    for *_, place in rows:
        if place.name.lower() not in seen:
            seen.add(place.name.lower())
            unique.append(place)
    return ok(unique[: max(1, min(int(limit), 60))], attribution=ATTRIBUTION)
