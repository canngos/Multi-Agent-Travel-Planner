"""culture_agent: sights with a short history, opening hours and tickets -> culture_findings."""
from ..tools import find_sight_hours, find_sights, geocode_place, web_search, wiki_summary, wikivoyage_section
from ._common import research_agent

ROLE = """You are the CULTURE RESEARCHER of a trip-planning team. Find the sights worth visiting at the
destination, matched to the travellers' interests, each with a short history, opening hours and ticket price."""

STEPS = """STEPS
1. find_sights(lat, lon, interests) with destination_lat/destination_lon and interests from the brief
   (radius_km 3, limit 15). If the brief has no coordinates, call geocode_place(destination) first.
2. wikivoyage_section(page=<destination city>, section="See"). Its listings have coordinates, hours and prices.
   If the city page has few listings but returns district_pages, read the "See" section of the 1-2 most
   central districts (e.g. "Lisbon/Baixa").
3. Choose 8-12 sights: fit the interests, spread over the city, merge duplicates found by several tools, and
   include at least 3 indoor sights (museums, churches, palaces) for rainy days. No restaurants, cafés or
   food markets: the food researcher covers those.
4. find_sight_hours(destination_lat, destination_lon, radius_km 6) once: OpenStreetMap opening hours and
   entry fees (ticket_eur 0 = free). Match its results to your sights by name (names may be in the local
   language, e.g. "Mosteiro dos Jerónimos" = "Jerónimos Monastery") and fill in hours / ticket_eur, adding its
   source URLs. It also lists museums you may have missed.
5. For the 4-6 most important sights whose summary lacks history, call wiki_summary("<sight name>, <city>")
   and use 2-3 sentences of its extract.
6. Only if hours or tickets are STILL missing for the top sights, call web_search AT MOST TWICE in total, each
   time for several sights at once, e.g. "<sight A> <sight B> <sight C> opening hours ticket price".
FALLBACK: if find_sights fails, build the list from Wikivoyage "See" (and its district pages) and
find_sight_hours.
If Wikivoyage fails too, use web_search "top sights in <city> history opening hours"."""

SHAPE = """{"results": [Place, ...], "notes": "..."}
Place = {"kind": "sight", "name": str, "lat": number|null, "lon": number|null,
         "summary": str (2-3 sentences: what it is + short history), "budget_tier": null,
         "hours": str|null, "ticket_eur": number|null (0 if free, null if unknown),
         "indoor": true|false|null, "source_urls": [str, ...] (every page the facts came from)}"""

EXAMPLE = """{"results": [{"kind": "sight", "name": "Belém Tower", "lat": 38.6916, "lon": -9.216,
  "summary": "16th-century fortified tower at the mouth of the Tagus, built to defend Lisbon. A UNESCO World Heritage Site and a symbol of the Age of Discoveries.",
  "budget_tier": null, "hours": "Tu-Su 10:00-17:30", "ticket_eur": 10.0, "indoor": false,
  "source_urls": ["https://en.wikivoyage.org/wiki/Lisbon/Bel%C3%A9m", "https://en.wikipedia.org/wiki/Bel%C3%A9m_Tower"]}],
 "notes": "OpenTripMap and Wikivoyage worked; hours for 2 sights unknown."}"""


def build_culture_agent():
    return research_agent(
        name="culture_agent",
        description="Finds sights with short histories, opening hours and ticket prices.",
        role=ROLE, steps=STEPS, shape=SHAPE, example=EXAMPLE,
        tools=[find_sights, wikivoyage_section, find_sight_hours, wiki_summary, web_search, geocode_place],
        output_key="culture_findings",
        max_calls=13,
    )
