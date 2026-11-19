"""transport_comparer -> transport_summary and stay_comparer -> stay_summary.

Comparers call no tools: they read the two searchers' results from session state
(`{flight_options?}` etc.; the `?` renders an empty string if a searcher wrote nothing)
and merge, rank and pick. They only reorganise options that are already grounded.
"""
from google.adk.agents import LlmAgent
from google.genai import types

from ..config import MODEL
from .grounding import ground_research_output
from .prompts import BRIEF, REQUIREMENTS, output_format

COMPARE_RULES = """RULES:
- Use ONLY options that appear in the inputs above. Copy each chosen option object unchanged (same fields,
  same url, same price_source). Never invent, merge or recalculate an option's fields.
- If an input is empty or says a source failed, work with what is there and say so in "notes".
- If nothing at all is available for a part, use null or [] for it and explain in "notes"."""

TRANSPORT_ROLE = """You are the TRANSPORT COMPARER of a trip-planning team. Merge the flight and ground options
for each direction, rank them, and pick the cheapest and the fastest."""

TRANSPORT_INPUTS = """FLIGHT OPTIONS (from the flight searcher):
{flight_options?}

GROUND OPTIONS (from the ground transport searcher):
{ground_options?}"""

TRANSPORT_STEPS = """HOW TO COMPARE (do it separately for "outbound" and "return"):
1. Put all flight and ground options of that direction in one list.
2. "cheapest" = the option with the lowest price_eur (ignore options without a price).
   "fastest" = the option with the lowest duration_min.
3. "ranked" = up to 5 options, best value first: sort by price_eur, but move an option down when it is much
   slower than the others (e.g. > 2x the fastest) or its details warn about an AIRPORT CHANGE or self-transfer.
   Options without a price go last.
4. "recommendation": 2-3 sentences: which outbound + return combination you recommend for this brief
   (budget_tier, travellers) and why, the combined price for all travellers, and any warning to show the user.
5. "total_cheapest_eur" = price of the cheapest outbound + cheapest return (null if one is missing)."""

TRANSPORT_SHAPE = """{"outbound": {"cheapest": TransportOption|null, "fastest": TransportOption|null, "ranked": [TransportOption, ...]},
 "return": {"cheapest": TransportOption|null, "fastest": TransportOption|null, "ranked": [TransportOption, ...]},
 "recommendation": str, "total_cheapest_eur": number|null, "notes": "..."}
TransportOption = an option object copied unchanged from the inputs."""

TRANSPORT_EXAMPLE = """{"outbound": {"cheapest": {"mode": "flight", "operator": "SAS", "depart": "2027-05-10T11:15", "arrive": "2027-05-10T16:10",
   "duration_min": 415, "stops": 1, "price_eur": 297.0, "price_source": "live", "source": "kiwi", "url": "https://kiwi.com/u/abc",
   "details": "HEL → CPH → LIS; ..."}, "fastest": {"...": "copied option"}, "ranked": [{"...": "copied option"}]},
 "return": {"cheapest": null, "fastest": null, "ranked": []},
 "recommendation": "Fly SAS via Copenhagen out (€297 for both) ... Ground travel is impractical (76 h by bus).",
 "total_cheapest_eur": null, "notes": "No return options: the flight search for the return failed."}"""

STAY_ROLE = """You are the STAY COMPARER of a trip-planning team. Compare Airbnb stays with hotels per neighbourhood
and pick the best options for the brief's budget."""

STAY_INPUTS = """AIRBNB OPTIONS (from the Airbnb searcher):
{airbnb_options?}

HOTEL OPTIONS (from the hotel searcher):
{hotel_options?}"""

STAY_STEPS = """HOW TO COMPARE:
1. Ratings use different scales: Airbnb and Google ratings are out of 5; liteAPI ratings ("guest rating x/10")
   are out of 10, so halve them when comparing. Prices: compare total_price_eur for the whole stay.
2. "by_area": group all stays by area (neighbourhood). For each area with at least one stay give the best
   Airbnb and the best hotel (best = good rating for a low price; null if that kind has none there) and the
   lowest price_per_night_eur in the area. At most 6 areas, most central / best-covered first.
3. "cheapest" = lowest total_price_eur overall. "best_value" = the stay with the best balance of price,
   rating and location for the brief's budget_tier. "top_rated" = highest rating (on the 0-5 scale).
4. "recommendation": 2-3 sentences: Airbnb or hotel for this trip and where, with the total price, and any
   caveat from details (e.g. liteAPI rates may be below public prices, excluded city tax).
5. Keep any fallback search links from the inputs in "fallback_links"."""

STAY_SHAPE = """{"by_area": [{"area": str, "airbnb": StayOption|null, "hotel": StayOption|null, "min_price_per_night_eur": number}, ...],
 "cheapest": StayOption|null, "best_value": StayOption|null, "top_rated": StayOption|null,
 "recommendation": str, "fallback_links": [str, ...], "notes": "..."}
StayOption = a stay object copied unchanged from the inputs."""

STAY_EXAMPLE = """{"by_area": [{"area": "Baixa", "airbnb": {"kind": "airbnb", "name": "...", "...": "copied stay"},
   "hotel": {"kind": "hotel", "name": "...", "...": "copied stay"}, "min_price_per_night_eur": 48.5}],
 "cheapest": {"...": "copied stay"}, "best_value": {"...": "copied stay"}, "top_rated": {"...": "copied stay"},
 "recommendation": "A hotel in Baixa is the best value (€194 for 4 nights, 4.3/5) ...",
 "fallback_links": [], "notes": "Both sources worked."}"""


def _comparer(name: str, description: str, role: str, inputs: str, steps: str, shape: str, example: str,
              output_key: str) -> LlmAgent:
    instruction = "\n\n".join([role, BRIEF, inputs, steps, COMPARE_RULES, output_format(shape, example)]) + REQUIREMENTS
    return LlmAgent(
        name=name,
        model=MODEL,
        description=description,
        instruction=instruction,
        output_key=output_key,
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0.1),
        after_agent_callback=ground_research_output(output_key),  # a copied option keeps the tool's link
    )


def build_transport_comparer() -> LlmAgent:
    return _comparer("transport_comparer", "Merges flight and ground options; picks cheapest and fastest.",
                     TRANSPORT_ROLE, TRANSPORT_INPUTS, TRANSPORT_STEPS, TRANSPORT_SHAPE, TRANSPORT_EXAMPLE,
                     "transport_summary")


def build_stay_comparer() -> LlmAgent:
    return _comparer("stay_comparer", "Compares Airbnb and hotels per neighbourhood.",
                     STAY_ROLE, STAY_INPUTS, STAY_STEPS, STAY_SHAPE, STAY_EXAMPLE, "stay_summary")
