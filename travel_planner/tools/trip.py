"""Own-code tools: save the trip brief, total the trip cost, save the final guide."""
import re
from datetime import datetime

import markdown as md
from google.adk.tools import ToolContext
from pydantic import ValidationError

from .. import config
from ..models import TripBrief
from ._result import ToolError, ok, safe_tool

ATTRIBUTION_FOOTER = """

---

<small>
Map and place data © OpenStreetMap contributors (ODbL) · Weather data by Open-Meteo.com (CC BY 4.0) ·
Public-transport routing by [Transitous](https://transitous.org/sources/) · Sights data © OpenTripMap ·
Texts from Wikipedia and Wikivoyage (CC BY-SA 4.0) · Prices marked *web estimate* come from web pages and
may be out of date; prices marked *live* were quoted by the booking source when this guide was made.
This guide plans and compares only; nothing has been booked.
</small>
"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; max-width: 52rem; margin: 2rem auto;
         padding: 0 1rem; line-height: 1.55; color: #1f2328; }}
  h1, h2, h3 {{ line-height: 1.25; }}
  h2 {{ border-bottom: 1px solid #d0d7de; padding-bottom: .3rem; margin-top: 2rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: .95rem; }}
  th, td {{ border: 1px solid #d0d7de; padding: .4rem .6rem; text-align: left; vertical-align: top; }}
  th {{ background: #f6f8fa; }}
  a {{ color: #0969da; }}
  small {{ color: #59636e; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _validation_reason(exc: ValidationError) -> str:
    parts = []
    for e in exc.errors():
        field = ".".join(str(x) for x in e["loc"]) or "brief"
        parts.append(f"{field}: {e['msg']}")
    return "Invalid trip brief - " + "; ".join(parts)


@safe_tool
def save_trip_brief(
    destination: str,
    start_date: str,
    end_date: str,
    tool_context: ToolContext,
    origin: str = "Helsinki",
    travellers: int = 2,
    budget_tier: str = "mid",
    total_budget_eur: float | None = None,
    interests: list[str] | None = None,
    origin_lat: float | None = None,
    origin_lon: float | None = None,
    destination_lat: float | None = None,
    destination_lon: float | None = None,
    destination_country: str | None = None,
) -> dict:
    """Validates the complete trip brief and saves it for the planning team.

    Call it only after the user has confirmed the details. If it returns an
    error, ask the user to fix the listed fields.

    Args:
        destination: Destination city, e.g. "Lisbon".
        start_date: First day of the trip, ISO "YYYY-MM-DD".
        end_date: Last day of the trip, ISO "YYYY-MM-DD".
        origin: City the travellers leave from.
        travellers: Number of travellers.
        budget_tier: "low", "mid" or "high".
        total_budget_eur: Total budget for the whole group in EUR, if the user gave one.
        interests: Interests, e.g. ["history", "street food"].
        origin_lat: Origin latitude from geocode_place.
        origin_lon: Origin longitude from geocode_place.
        destination_lat: Destination latitude from geocode_place.
        destination_lon: Destination longitude from geocode_place.
        destination_country: Destination country from geocode_place.
    """
    try:
        brief = TripBrief(
            origin=origin, destination=destination, start_date=start_date, end_date=end_date,
            travellers=travellers, budget_tier=budget_tier, total_budget_eur=total_budget_eur,
            interests=interests or [], origin_lat=origin_lat, origin_lon=origin_lon,
            destination_lat=destination_lat, destination_lon=destination_lon,
            destination_country=destination_country,
        )
    except ValidationError as exc:
        raise ToolError(_validation_reason(exc)) from exc
    saved = {**brief.model_dump(mode="json"), "nights": brief.nights, "days": brief.days}
    tool_context.state["trip_brief"] = saved
    return ok([saved])


@safe_tool
def calculate_trip_cost(
    transport_total_eur: float,
    stay_total_eur: float,
    food_per_person_per_day_eur: float,
    activities_total_eur: float,
    days: int,
    travellers: int,
    local_transport_per_person_per_day_eur: float = 0.0,
    total_budget_eur: float | None = None,
) -> dict:
    """Totals the trip cost per category and compares it with the budget.

    Args:
        transport_total_eur: Getting there and back, all travellers together.
        stay_total_eur: Accommodation for the whole stay, all travellers.
        food_per_person_per_day_eur: Food and drink per person per day.
        activities_total_eur: All tickets and activities, all travellers together.
        days: Number of trip days.
        travellers: Number of travellers.
        local_transport_per_person_per_day_eur: Metro/tram/bus per person per day.
        total_budget_eur: The group's total budget, if known.
    """
    if days < 1 or travellers < 1:
        raise ToolError("days and travellers must be at least 1")
    values = [transport_total_eur, stay_total_eur, food_per_person_per_day_eur, activities_total_eur,
              local_transport_per_person_per_day_eur]
    if any(v is None or v < 0 for v in values):
        raise ToolError("costs must be zero or positive numbers")
    categories = {
        "transport": transport_total_eur,
        "stay": stay_total_eur,
        "food": food_per_person_per_day_eur * travellers * days,
        "activities": activities_total_eur,
        "local_transport": local_transport_per_person_per_day_eur * travellers * days,
    }
    total = sum(categories.values())
    result = {
        "categories_eur": {k: round(v, 2) for k, v in categories.items()},
        "share_pct": {k: round(100 * v / total, 1) if total else 0.0 for k, v in categories.items()},
        "total_eur": round(total, 2),
        "per_person_eur": round(total / travellers, 2),
        "per_person_per_day_eur": round(total / travellers / days, 2),
    }
    if total_budget_eur:
        diff = total_budget_eur - total
        result.update({
            "budget_eur": total_budget_eur,
            "difference_eur": round(diff, 2),
            "within_budget": diff >= 0,
            "budget_used_pct": round(100 * total / total_budget_eur, 1),
        })
    else:
        result["within_budget"] = None
        result["note"] = "No total budget given; judge against the budget tier instead."
    return ok([result])


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:80] or datetime.now().strftime("trip-%Y%m%d-%H%M%S")


@safe_tool
def save_guide(markdown_text: str, filename_slug: str) -> dict:
    """Saves the finished trip guide as Markdown and HTML in the output/ folder.

    An attribution footer (data sources and price notes) is added automatically.

    Args:
        markdown_text: The full guide in Markdown.
        filename_slug: File name without extension, e.g. "lisbon-2027-05-10".
    """
    if not markdown_text.strip():
        raise ToolError("The guide is empty")
    slug = slugify(filename_slug)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    full = markdown_text.rstrip() + ATTRIBUTION_FOOTER
    md_path = config.OUTPUT_DIR / f"{slug}.md"
    html_path = config.OUTPUT_DIR / f"{slug}.html"
    md_path.write_text(full, encoding="utf-8")
    title_match = re.search(r"^#\s+(.+)$", markdown_text, flags=re.MULTILINE)
    body = md.markdown(full, extensions=["tables", "sane_lists"])
    html_path.write_text(
        HTML_TEMPLATE.format(title=(title_match.group(1) if title_match else slug), body=body), encoding="utf-8"
    )
    return ok([{"markdown_path": str(md_path), "html_path": str(html_path)}])
