"""Own-code tools: save the trip brief, total the trip cost, save the final guide."""
import html
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


MAX_TRIP_DAYS = 21  # a longer plan no longer fits in one model answer
_SEVERAL_PLACES = re.compile(r"\s(?:and|&|\+)\s|/", re.IGNORECASE)
BRIEF_FIELDS = tuple(TripBrief.model_fields)


def check_brief(args: dict) -> TripBrief:
    """The validated brief from save_trip_brief's arguments (omitted or null ones take their defaults).

    Raises ToolError naming every field to fix: the model's own rules (dates in order, travellers, budget) plus
    what the planner cannot plan: a start date in the past, more than MAX_TRIP_DAYS days, several destinations.
    """
    values = {k: v for k, v in args.items() if k in BRIEF_FIELDS and v is not None}
    try:
        brief = TripBrief(**values)
    except ValidationError as exc:
        raise ToolError(_validation_reason(exc)) from exc
    problems = []
    if brief.start_date < config.today():
        problems.append(f"start_date: {brief.start_date} is in the past (today is {config.today()})")
    if brief.days > MAX_TRIP_DAYS:
        problems.append(f"end_date: the trip has {brief.days} days; the planner plans at most {MAX_TRIP_DAYS}")
    if _SEVERAL_PLACES.search(brief.destination):
        problems.append(f"destination: '{brief.destination}' names several places; the planner plans one city per "
                        "trip (plan the other one afterwards)")
    if problems:
        raise ToolError("Invalid trip brief - " + "; ".join(problems))
    return brief


PEOPLE_PER_ROOM = 2


def requirements_block(brief: TripBrief) -> str:
    """Instruction text for every planning agent (state["trip_requirements"]); "" when there is nothing to add,
    so the instructions of an ordinary trip stay exactly as they were."""
    lines = []
    if brief.avoid_modes:
        lines.append(f"- Never use these transport modes: {', '.join(brief.avoid_modes)}.")
    lines += [f"- {r}" for r in brief.requirements]
    if brief.travellers > PEOPLE_PER_ROOM:
        rooms = -(-brief.travellers // PEOPLE_PER_ROOM)
        lines.append(f"- Group of {brief.travellers}: hotels need {rooms} rooms (pass rooms={rooms} to "
                     f"search_hotel_rates and stay_deep_links; search_google_hotels prices one room only). Airbnb "
                     f"stays must sleep {brief.travellers}.")
    if not lines:
        return ""
    # agents commented on requirements outside their job ("vegetarian does not affect transport"): ask them not to
    return ("\n\nHARD REQUIREMENTS OF THE TRAVELLERS (they override the steps and defaults above). Follow the ones "
            "that concern your task, say in \"notes\" which of those you could not meet, and do not mention the "
            "others:\n" + "\n".join(lines) + "\nIf a requirement mentions children, infants or pets, pass them to "
            "every search tool that has such a parameter.")


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
    avoid_modes: list[str] | None = None,
    requirements: list[str] | None = None,
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
        avoid_modes: Transport modes the travellers refuse, from "flight", "train", "bus", "ferry"
            (e.g. ["flight"] for "we don't fly"). Only what the user said.
        requirements: Other hard needs the user stated, one short phrase each, e.g. "travelling with
            children aged 4 and 7", "bringing a dog", "wheelchair access", "vegetarian", "two separate beds".
    """
    brief = check_brief(dict(
        origin=origin, destination=destination, start_date=start_date, end_date=end_date,
        travellers=travellers, budget_tier=budget_tier, total_budget_eur=total_budget_eur,
        interests=interests or [], origin_lat=origin_lat, origin_lon=origin_lon,
        destination_lat=destination_lat, destination_lon=destination_lon,
        destination_country=destination_country, avoid_modes=avoid_modes, requirements=requirements,
    ))
    # empty requirement lists are left out, so an ordinary brief (and every instruction showing it) is unchanged
    saved = {k: v for k, v in brief.model_dump(mode="json").items() if v or k not in ("avoid_modes", "requirements")}
    saved.update(nights=brief.nights, days=brief.days)
    tool_context.state["trip_brief"] = saved
    tool_context.state["trip_requirements"] = requirements_block(brief)
    return ok([saved])


# Typical daily costs per person, used only when no sourced figure is given. The result labels them as assumptions.
ASSUMED_FOOD_PER_PERSON_DAY = {"low": 30.0, "mid": 50.0, "high": 90.0}
ASSUMED_LOCAL_TRANSPORT_PER_PERSON_DAY = {"low": 5.0, "mid": 8.0, "high": 15.0}


@safe_tool
def calculate_trip_cost(
    transport_total_eur: float,
    stay_total_eur: float,
    activities_total_eur: float,
    days: int,
    travellers: int,
    budget_tier: str = "mid",
    food_per_person_per_day_eur: float | None = None,
    local_transport_per_person_per_day_eur: float | None = None,
    total_budget_eur: float | None = None,
) -> dict:
    """Totals the trip cost per category and compares it with the budget.

    Food and local transport default to typical daily costs for the budget tier
    (low/mid/high); the result lists them under "assumptions" so the guide can say so.

    Args:
        transport_total_eur: Getting there and back, all travellers together.
        stay_total_eur: Accommodation for the whole stay, all travellers.
        activities_total_eur: All tickets and activities, all travellers together.
        days: Number of trip days.
        travellers: Number of travellers.
        budget_tier: "low", "mid" or "high"; picks the typical food / local transport cost.
        food_per_person_per_day_eur: Food and drink per person per day, only if you have a sourced figure.
        local_transport_per_person_per_day_eur: Metro/tram/bus per person per day, only if sourced.
        total_budget_eur: The group's total budget, if known.
    """
    if days < 1 or travellers < 1:
        raise ToolError("days and travellers must be at least 1")
    tier = budget_tier if budget_tier in ASSUMED_FOOD_PER_PERSON_DAY else "mid"
    assumptions = []
    if food_per_person_per_day_eur is None:
        food_per_person_per_day_eur = ASSUMED_FOOD_PER_PERSON_DAY[tier]
        assumptions.append(f"food: typical {tier}-budget cost of €{food_per_person_per_day_eur:.0f} per person per day")
    if local_transport_per_person_per_day_eur is None:
        local_transport_per_person_per_day_eur = ASSUMED_LOCAL_TRANSPORT_PER_PERSON_DAY[tier]
        assumptions.append(
            f"local transport: typical €{local_transport_per_person_per_day_eur:.0f} per person per day")
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
        "assumptions": assumptions,
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


_UNSAFE_LINK = re.compile(r"""\b(href|src)\s*=\s*(["'])(?!https?:|mailto:|#)[^"']*\2""", re.IGNORECASE)


def guide_html(text: str, raw_html: bool = False) -> str:
    """Markdown -> HTML for the saved guide. The model's text gets no raw HTML (a tag copied from a web page would
    run in the browser) and only http(s)/mailto links; raw_html=True is for our own footer."""
    converter = md.Markdown(extensions=["tables", "sane_lists"])
    if not raw_html:
        converter.preprocessors.deregister("html_block")
        converter.inlinePatterns.deregister("html")
    return _UNSAFE_LINK.sub(r'\1="#"', converter.convert(text))


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:80] or datetime.now().strftime("trip-%Y%m%d-%H%M%S")


@safe_tool
def save_guide(markdown_text: str, filename_slug: str, tool_context: ToolContext = None) -> dict:
    """Saves the finished trip guide as Markdown and HTML in the output/ folder.

    An attribution footer (data sources and price notes) is added automatically.
    The guide is also kept in the session as "final_guide".

    Args:
        markdown_text: The full guide in Markdown.
        filename_slug: File name without extension, e.g. "lisbon-2027-05-10".
    """
    if not markdown_text.strip():
        raise ToolError("The guide is empty")
    slug = slugify(filename_slug)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    markdown_text = markdown_text.replace("web_estimate", "web estimate")  # the raw field value reads badly
    full = markdown_text.rstrip() + ATTRIBUTION_FOOTER
    md_path = config.OUTPUT_DIR / f"{slug}.md"
    html_path = config.OUTPUT_DIR / f"{slug}.html"
    md_path.write_text(full, encoding="utf-8")
    title_match = re.search(r"^#\s+(.+)$", markdown_text, flags=re.MULTILINE)
    body = guide_html(markdown_text.rstrip()) + "\n" + guide_html(ATTRIBUTION_FOOTER, raw_html=True)
    title = html.escape(title_match.group(1) if title_match else slug)
    html_path.write_text(HTML_TEMPLATE.format(title=title, body=body), encoding="utf-8")
    if tool_context is not None:
        tool_context.state["final_guide"] = full
        tool_context.state["guide_paths"] = {"markdown": str(md_path), "html": str(html_path)}
    return ok([{"markdown_path": str(md_path), "html_path": str(html_path)}])
