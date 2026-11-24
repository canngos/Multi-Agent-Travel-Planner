"""Wikipedia summaries and Wikivoyage sections (with structured listings)."""
import re
from urllib.parse import quote, unquote

from ..models import Place
from ._http import get_json
from ._result import ToolError, ok, safe_tool
from .currency import eur_rates, is_foreign, leading_price, parse_money, parse_number, to_eur

WIKIVOYAGE_API = "https://en.wikivoyage.org/w/api.php"
LISTING_KINDS = {"see": "sight", "do": "sight", "eat": "restaurant", "drink": "bar", "listing": "sight"}
MAX_TEXT_CHARS = 1500
MAX_EXTRACT_CHARS = 1200
MAX_LISTINGS = 30


# ---------------------------------------------------------------- Wikipedia

@safe_tool
async def wiki_summary(title: str, lang: str = "en") -> dict:
    """Short Wikipedia summary of a sight or topic (use it for a sight's history).

    If there is no page with exactly that title, the best search match is used.

    Args:
        title: Article title or search phrase, e.g. "Belém Tower".
        lang: Wikipedia language code, default "en".
    """
    if not re.fullmatch(r"[a-z]{2,12}(?:-[a-z]{2,12})?", lang or ""):  # it becomes part of the host name
        raise ToolError(f"lang must be a Wikipedia language code such as 'en' or 'pt', not {lang!r}")
    api = f"https://{lang}.wikipedia.org/w/api.php"
    # The query API answers 200 with "missing" instead of 404, so every step is cacheable
    # (errors are never cached, which would break offline replay of the search fallback).
    page = await _wiki_page(api, title)
    if page.get("missing"):
        found = await get_json(
            api, namespace="wikipedia",
            params={"action": "query", "list": "search", "srsearch": title, "srlimit": 1,
                    "format": "json", "formatversion": 2},
        )
        hits = found.get("query", {}).get("search", [])
        if not hits:
            raise ToolError(f"No Wikipedia article found for '{title}'")
        page = await _wiki_page(api, hits[0]["title"])
    if "disambiguation" in (page.get("pageprops") or {}):
        raise ToolError(f"'{title}' is ambiguous on Wikipedia; add the destination city, e.g. '{title}, <city>'")
    coords = (page.get("coordinates") or [{}])[0]
    extract = page.get("extract") or ""
    if len(extract) > MAX_EXTRACT_CHARS:  # cut at a sentence end
        cut = extract.rfind(". ", 0, MAX_EXTRACT_CHARS)
        extract = extract[: cut + 1 if cut > 0 else MAX_EXTRACT_CHARS]
    return ok(
        [{
            "title": page.get("title"),
            "description": page.get("description"),
            "extract": extract,
            "lat": coords.get("lat"),
            "lon": coords.get("lon"),
            "url": page.get("fullurl"),
        }],
        attribution="Text from Wikipedia (CC BY-SA 4.0)",
    )


async def _wiki_page(api: str, title: str) -> dict:
    data = await get_json(
        api, namespace="wikipedia",
        params={
            "action": "query", "titles": title, "redirects": 1, "format": "json", "formatversion": 2,
            "prop": "extracts|coordinates|description|info|pageprops",
            "exintro": 1, "explaintext": 1, "inprop": "url", "ppprop": "disambiguation",
        },
    )
    pages = data.get("query", {}).get("pages") or [{"missing": True}]
    return pages[0]


# ---------------------------------------------------------------- Wikivoyage parsing helpers

def _template_spans(text: str, names: set[str]) -> list[tuple[str, str, int, int]]:
    """Finds top-level {{name|...}} templates; returns (name, inner text, start, end), nested braces kept."""
    out, i = [], 0
    pattern = re.compile(r"\{\{\s*(" + "|".join(names) + r")\s*\|", re.IGNORECASE)
    while (m := pattern.search(text, i)) is not None:
        depth, j = 0, m.start()
        while j < len(text):
            if text.startswith("{{", j):
                depth, j = depth + 1, j + 2
            elif text.startswith("}}", j):
                depth, j = depth - 1, j + 2
                if depth == 0:
                    break
            else:
                j += 1
        out.append((m.group(1).lower(), text[m.end():j - 2], m.start(), j))
        i = j
    return out


def _split_params(inner: str) -> dict[str, str]:
    """Splits template params on top-level '|' (ignores pipes inside nested {{ }} and [[ ]])."""
    parts, depth, cur = [], 0, []
    k = 0
    while k < len(inner):
        two = inner[k:k + 2]
        if two in ("{{", "[["):
            depth += 1
            cur.append(two)
            k += 2
            continue
        if two in ("}}", "]]"):
            depth -= 1
            cur.append(two)
            k += 2
            continue
        if inner[k] == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(inner[k])
        k += 1
    parts.append("".join(cur))
    params = {}
    for part in parts:
        if "=" in part:
            key, _, value = part.partition("=")
            params[key.strip().lower()] = value.strip()
    return params


def clean_wikitext(text: str) -> str:
    """Turns wikitext into readable plain text (good enough for summaries)."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", "", text, flags=re.DOTALL)
    text = re.sub(r"\[\[(?:File|Image):[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{\s*m2\s*\|\s*([\d.,]+)[^}]*\}\}", r"\1 m²", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{\s*(?:m|km)\s*\|\s*([\d.,]+)[^}]*\}\}", r"\1 m", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{\s*(?:station|w|ill)\s*\|\s*([^|}]+)[^}]*\}\}", r"\1", text, flags=re.IGNORECASE)
    for _ in range(3):  # drop remaining (possibly nested) templates
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[[^|\]]*\|([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]#]*)(?:#[^\]]*)?\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+ ([^\]]+)\]", r"\1", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"^=+\s*(.*?)\s*=+\s*$", r"\1:", text, flags=re.MULTILINE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _budget_from_heading(heading: str) -> str | None:
    h = heading.lower()
    if any(w in h for w in ("budget", "cheap", "inexpensive")):
        return "low"
    if "mid" in h or "moderate" in h:
        return "mid"
    if any(w in h for w in ("splurge", "expensive", "upscale", "luxury", "fine dining")):
        return "high"
    return None


def _budget_from_price(price: str, rates: dict[str, float] | None = None) -> str | None:
    """Meal price text like '€15-30' (or 'zł 30-50' with exchange rates) -> tier by the euro midpoint (per person)."""
    money = parse_money(price)
    nums = [parse_number(n) for n in re.findall(r"\d+(?:[.,]\d+)?", price)]
    if money is None or not nums:
        return None
    mid = to_eur(sum(nums[:2]) / len(nums[:2]), money[1], rates)
    if mid is None:
        return None
    return "low" if mid < 15 else "mid" if mid <= 35 else "high"


def _price_eur(price: str, rates: dict[str, float] | None = None) -> float | None:
    """'€2' -> 2.0, 'Free' -> 0.0, '20 zł' -> its euro value when exchange rates are given;
    ranges, price lists and unconvertible currencies -> None (keep the text instead)."""
    p = price.strip()
    if p.lower().startswith("free"):
        return 0.0
    money = parse_money(p, whole=True)
    return to_eur(*money, rates) if money else None


def _ticket_eur(price: str, rates: dict[str, float] | None = None) -> float | None:
    """A sight's adult ticket: a single price, or the first price of a list ("€6 (adults), €3 (seniors)"); in a live
    Tallinn run every museum's list price was dropped and the trip cost counted all tickets as €0."""
    exact = _price_eur(price, rates)
    if exact is not None:
        return exact
    first = leading_price(price.strip())
    return to_eur(*first, rates) if first else None


async def wikivoyage_rates(wikitext: str) -> dict[str, float] | None:
    """Exchange rates, fetched only when a listing's price is in a currency other than the euro."""
    prices = re.findall(r"\|\s*price\s*=\s*([^|}]*)", wikitext)
    return await eur_rates() if any(is_foreign(clean_wikitext(p)) for p in prices) else None


def _float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_listings(wikitext: str, page_url: str, limit: int = MAX_LISTINGS,
                   rates: dict[str, float] | None = None) -> list[Place]:
    """Extracts {{see|...}}, {{eat|...}} etc. listings, tracking budget-tier subheadings.
    With exchange rates (see wikivoyage_rates), prices in other currencies are converted to euro."""
    places = []
    # Split by headings so each listing knows the subheading it sits under.
    chunks = re.split(r"^(=+[^=\n]+=+)\s*$", wikitext, flags=re.MULTILINE)
    heading = ""
    for chunk in chunks:
        if re.fullmatch(r"=+[^=\n]+=+", chunk.strip()):
            heading = chunk.strip("= \n")
            continue
        tier = _budget_from_heading(heading)
        for name, inner, _, _ in _template_spans(chunk, set(LISTING_KINDS)):
            p = _split_params(inner)
            if not p.get("name"):
                continue
            price_text = clean_wikitext(p.get("price", ""))
            summary = clean_wikitext(p.get("content", ""))
            converted = _price_eur(price_text, rates) if price_text else None
            if price_text and _price_eur(price_text) is None:  # not a plain euro price: keep the original text
                approx = f" (≈ €{converted:g} at the ECB rate)" if converted is not None else ""
                summary = f"{summary} Price: {price_text}{approx}".strip()
            places.append(Place(
                kind=LISTING_KINDS[name],
                name=clean_wikitext(p["name"]),
                lat=_float(p.get("lat")),
                lon=_float(p.get("long")),
                summary=summary[:600],
                budget_tier=(tier or _budget_from_price(price_text, rates)) if name in ("eat", "drink") else None,
                hours=clean_wikitext(p.get("hours", "")) or None,
                ticket_eur=_ticket_eur(price_text, rates) if name in ("see", "do") and price_text else None,
                source_urls=[u for u in (page_url, p.get("url", "").strip()) if u],
            ))
            if len(places) >= limit:
                return places
    return places


def district_pages(wikitext: str, page: str) -> list[str]:
    """Sub-pages like 'Lisbon/Alfama' linked from the text (big cities keep listings there)."""
    found = re.findall(r"\[\[\s*(" + re.escape(page) + r"/[^|\]#]+)", wikitext)
    return list(dict.fromkeys(f.strip() for f in found))


# ---------------------------------------------------------------- Wikivoyage tool

async def wikivoyage_wikitext(page: str, section: str) -> tuple[str, str, str, str]:
    """(page title, section heading, raw wikitext incl. subsections, page URL) of one Wikivoyage section."""
    page = unquote(page).replace("_", " ").strip()  # the model sometimes passes the URL form, "Krak%C3%B3w"
    meta = await get_json(
        WIKIVOYAGE_API, namespace="wikivoyage",
        params={"action": "parse", "page": page, "prop": "sections", "format": "json", "redirects": 1},
    )
    if "error" in meta:
        raise ToolError(f"Wikivoyage has no page '{page}': {meta['error'].get('info', '')}")
    title = meta["parse"]["title"]
    sections = meta["parse"]["sections"]
    match = next((s for s in sections if s["line"].strip().lower() == section.strip().lower()), None)
    if match is None:
        top = [s["line"] for s in sections if s.get("toclevel") == 1]
        raise ToolError(f"No section '{section}' on Wikivoyage page '{title}'. Sections: {top}")
    data = await get_json(
        WIKIVOYAGE_API, namespace="wikivoyage",
        params={"action": "parse", "page": title, "prop": "wikitext", "section": match["index"], "format": "json"},
    )
    page_url = f"https://en.wikivoyage.org/wiki/{quote(title.replace(' ', '_'))}"
    return title, match["line"], data["parse"]["wikitext"]["*"], page_url


@safe_tool
async def wikivoyage_section(page: str, section: str) -> dict:
    """Reads one section of a Wikivoyage travel guide page: "See", "Do", "Eat", "Drink",
    "Get in", "Get around", "Understand", "Stay safe" and so on.

    Returns structured listings (name, coordinates, hours, price, description; for
    "Eat", a budget tier from the Budget/Mid-range/Splurge subheadings), a plain-text
    excerpt, and "district_pages": big cities keep their listings on district pages
    such as "Lisbon/Belém", which you can read with this same tool.

    Args:
        page: Wikivoyage page title, e.g. "Lisbon" or "Lisbon/Belém".
        section: Section heading, e.g. "See" or "Eat".
    """
    title, line, wikitext, page_url = await wikivoyage_wikitext(page, section)
    listings = parse_listings(wikitext, page_url, rates=await wikivoyage_rates(wikitext))
    # The excerpt is the prose outside listings (listings are returned separately).
    prose, cursor = [], 0
    for _, _, start, end in _template_spans(wikitext, set(LISTING_KINDS)):
        prose.append(wikitext[cursor:start])
        cursor = end
    prose.append(wikitext[cursor:])
    text = clean_wikitext(re.sub(r"^\*\s*$", "", "".join(prose), flags=re.MULTILINE))
    districts = district_pages(wikitext, title)
    if not listings and not districts and "/" not in title:
        # Big cities keep listings on district pages, linked from their "Districts" section.
        try:
            _, _, district_text, _ = await wikivoyage_wikitext(title, "Districts")
            districts = district_pages(district_text, title)
        except ToolError:
            pass
    return ok(
        listings,
        page=title,
        section=line,
        url=page_url,
        text=text[:MAX_TEXT_CHARS] + ("…" if len(text) > MAX_TEXT_CHARS else ""),
        district_pages=districts,
        attribution="Wikivoyage (CC BY-SA 4.0)",
    )
