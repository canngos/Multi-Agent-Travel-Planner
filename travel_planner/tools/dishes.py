"""Traditional dishes of a country: Wikivoyage's country "Eat" section + Wikidata (SPARQL).

Wikivoyage names the dishes travellers should try (as italic terms in curated text);
Wikidata adds canonical names, descriptions, images and a popularity rank (sitelinks).
Cities have no dish data of their own, so this works per country.
"""
import re

from ._http import get_json
from ._result import ToolError, ok, safe_tool
from .wiki import clean_wikitext, wikivoyage_wikitext

# QLever (University of Freiburg) answers in seconds; the official WDQS often takes 25-40 s and throttles.
SPARQL_ENDPOINTS = ["https://qlever.dev/api/wikidata", "https://query.wikidata.org/sparql"]
SPARQL_TIMEOUT_S = 45.0
MAX_TEXT_CHARS = 2500

DISHES_QUERY = """PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> PREFIX schema: <http://schema.org/>
PREFIX wikibase: <http://wikiba.se/ontology#>
SELECT ?dish (SAMPLE(?label) AS ?name) (SAMPLE(?desc) AS ?description) (SAMPLE(?img) AS ?image) (MAX(?sl) AS ?links)
WHERE {
  ?country rdfs:label "COUNTRY"@en ; wdt:P31 wd:Q6256 .
  { ?dish wdt:P495 ?country }
  UNION { ?dish wdt:P2012|wdt:P361 ?cuisine . ?cuisine wdt:P17 ?country ; wdt:P31/wdt:P279* wd:Q1778821 . }
  ?dish (wdt:P31|wdt:P279)/wdt:P279* wd:Q2095 .
  MINUS { ?dish wdt:P31/wdt:P279* wd:Q11707 }
  MINUS { ?dish (wdt:P31|wdt:P279)/wdt:P279* wd:Q40050 }
  MINUS { ?dish wdt:P31/wdt:P279* wd:Q431289 }
  ?dish rdfs:label ?label . FILTER(lang(?label) = "en")
  OPTIONAL { ?dish schema:description ?desc . FILTER(lang(?desc) = "en") }
  OPTIONAL { ?dish wdt:P18 ?img }
  OPTIONAL { ?dish wikibase:sitelinks ?sl }
} GROUP BY ?dish ORDER BY DESC(?links) LIMIT 40"""
# Q6256 country, Q1778821 cuisine, Q2095 food; removed: Q11707 restaurant, Q40050 drink, Q431289 brand.


def dishes_query(country: str) -> str:
    return DISHES_QUERY.replace("COUNTRY", country.replace('"', "").strip())


def italic_terms(wikitext: str, limit: int = 40) -> list[str]:
    """Italic ''terms'' in wikitext (Wikivoyage writes local dish names in italics); bold is skipped."""
    terms = []
    for m in re.finditer(r"(?<!')''(?!')(.+?)(?<!')''(?!')", wikitext):
        term = clean_wikitext(m.group(1)).strip(" ,.;:")
        if 2 <= len(term) <= 40 and term.lower() not in (t.lower() for t in terms):
            terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def rows_to_dishes(data: dict) -> list[dict]:
    dishes = []
    for row in (data.get("results") or {}).get("bindings") or []:
        value = {k: v.get("value") for k, v in row.items()}
        qid = (value.get("dish") or "").rsplit("/", 1)[-1]
        if not value.get("name") or not qid.startswith("Q"):
            continue
        dishes.append({
            "name": value["name"],
            "description": value.get("description") or "",
            "image": value.get("image"),
            "url": f"https://www.wikidata.org/wiki/{qid}",
            "popularity": int(float(value.get("links") or 0)),
        })
    return dishes


async def _wikidata_dishes(country: str) -> list[dict]:
    errors = []
    for endpoint in SPARQL_ENDPOINTS:
        try:
            data = await get_json(endpoint, namespace="wikidata", params={"query": dishes_query(country)},
                                  headers={"Accept": "application/sparql-results+json"},
                                  max_age_s=30 * 24 * 3600, timeout_s=SPARQL_TIMEOUT_S, retries=0)
            return rows_to_dishes(data)
        except ToolError as exc:
            errors.append(str(exc))
    raise ToolError("Wikidata unavailable (" + " | ".join(errors) + ")")


@safe_tool
async def local_dishes(country: str, limit: int = 15) -> dict:
    """Traditional dishes of a COUNTRY (dish data exists per country, not per city).

    Returns "wikivoyage": the country's Wikivoyage "Eat" text and the dish names it
    highlights (the dishes travellers are told to try, best for choosing), and
    "results": matching Wikidata dishes with an English description, image and a
    popularity score. Wikidata can include generic foods (e.g. cheese types or
    dishes shared with neighbours): prefer dishes that Wikivoyage also mentions.

    Args:
        country: Country name in English, e.g. "Portugal" or "Estonia".
        limit: Max Wikidata dishes to return (max 40).
    """
    country = country.strip()
    wikivoyage, problems = None, []
    try:
        title, _, wikitext, url = await wikivoyage_wikitext(country, "Eat")
        text = clean_wikitext(wikitext)
        wikivoyage = {"page": title, "url": f"{url}#Eat", "dish_terms": italic_terms(wikitext),
                      "text": text[:MAX_TEXT_CHARS] + ("…" if len(text) > MAX_TEXT_CHARS else "")}
    except ToolError as exc:
        problems.append(f"Wikivoyage: {exc}")
    try:
        dishes = (await _wikidata_dishes(country))[: max(1, min(int(limit), 40))]
    except ToolError as exc:
        dishes = []
        problems.append(str(exc))
    if wikivoyage is None and not dishes:
        raise ToolError("; ".join(problems) or f"No dish data found for '{country}'")
    return ok(dishes, wikivoyage=wikivoyage, problems=problems,
              attribution="Wikivoyage (CC BY-SA 4.0); Wikidata (CC0)")
