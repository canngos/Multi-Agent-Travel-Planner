"""food_agent: traditional dishes and restaurants per budget tier -> food_findings."""
from ..tools import find_restaurants, geocode_place, local_dishes, web_search, wikivoyage_section
from ._common import research_agent
from .enrich import fill_from_tool_results

ROLE = """You are the FOOD RESEARCHER of a trip-planning team. Find the destination's traditional dishes and
restaurants for every budget tier, favouring local cuisine and the brief's budget_tier."""

STEPS = """STEPS
1. local_dishes(destination_country) and pick 5-8 typical dishes. Prefer the dishes that Wikivoyage
   highlights (wikivoyage.dish_terms and text); use the Wikidata results for descriptions. source_url = the
   Wikivoyage URL or the dish's Wikidata URL. Skip generic foods (cheese types, "Mediterranean diet").
   Write each description from the Wikivoyage text or the Wikidata description; do NOT web_search dishes.
2. wikivoyage_section(page=<destination city>, section="Eat"). Its listings carry a budget_tier from the
   Budget / Mid-range / Splurge headings. If the city page has fewer than 5 listings and returns
   district_pages, you MUST read "Eat" of 1-2 central districts (e.g. "Lisbon/Alfama", "Lisbon/Bairro Alto")
   next: they have coordinates, hours and budget tiers.
3. find_restaurants(destination_lat, destination_lon, radius_m=800) for more local places near the centre
   (if the brief has no coordinates, call geocode_place first). This server is slow and may fail.
4. Choose 9-12 places: at least 2 per budget tier where the data allows, most of them in the brief's tier,
   local cuisine first, a few cafés or bars are welcome.
FALLBACK: if local_dishes fails, use web_search("traditional dishes to try in <city>"). If find_restaurants
fails, use the Wikivoyage listings only. Use web_search for restaurants ONLY if Wikivoyage (city and district
pages) gives fewer than 5 places: "best traditional restaurants in <city> cheap mid-range"."""

SHAPE = """{"dishes": [{"name": str, "description": str (1 sentence), "source_url": str}, ...],
 "results": [Place, ...], "notes": "..."}
Place = {"kind": "restaurant"|"cafe"|"bar", "name": str, "lat": number|null, "lon": number|null,
         "summary": str (cuisine and what to order, 1-2 sentences), "budget_tier": "low"|"mid"|"high"|null,
         "hours": str|null, "ticket_eur": null, "indoor": null, "source_urls": [str, ...]}
budget_tier: from the Wikivoyage heading/price, or from the tool's budget_tier; null if unknown."""

EXAMPLE = """{"dishes": [{"name": "Pastel de nata", "description": "Custard tart with crisp puff pastry, best warm with cinnamon.",
  "source_url": "https://en.wikivoyage.org/wiki/Portugal#Eat"}],
 "results": [{"kind": "restaurant", "name": "Example Tasca", "lat": 38.7101, "lon": -9.1357,
  "summary": "Family-run tasca serving grilled sardines and bacalhau à brás.", "budget_tier": "low",
  "hours": "Mo-Sa 12:00-22:00", "ticket_eur": null, "indoor": null,
  "source_urls": ["https://en.wikivoyage.org/wiki/Lisbon/Baixa"]}],
 "notes": "Overpass timed out; restaurants come from Wikivoyage district pages."}"""


def build_food_agent():
    return research_agent(
        name="food_agent",
        description="Finds traditional dishes and restaurants per budget tier.",
        role=ROLE, steps=STEPS, shape=SHAPE, example=EXAMPLE,
        tools=[local_dishes, wikivoyage_section, find_restaurants, web_search, geocode_place],
        output_key="food_findings",
        max_calls=8,
        before_grounding=[fill_from_tool_results("food_findings")],  # opening hours the agent left out
    )
