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
from .geo import haversine_km

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
OMIT_WHEN_EMPTY = ("avoid_modes", "requirements", "day_start", "day_end", "pace", "own_accommodation", "own_car")
ROAD_FACTOR, DRIVE_KMH = 1.25, 80  # road distance ~ straight line x 1.25; average speed with stops


def drive_estimate(brief: TripBrief) -> tuple[int, float] | None:
    """(road km, hours of driving) one way, roughly, from the straight-line distance; None without coordinates.
    Only distance and time: fuel or charging costs depend on the car, so the plan does not guess them."""
    coords = (brief.origin_lat, brief.origin_lon, brief.destination_lat, brief.destination_lon)
    if any(c is None for c in coords):
        return None
    km = haversine_km(*coords) * ROAD_FACTOR
    return int(round(km, -1)), round(km / DRIVE_KMH * 2) / 2
SIGHTS_PER_DAY = {"relaxed": 2, "balanced": 3, "packed": 5}
MAX_RESEARCH_SIGHTS, USUAL_SIGHTS = 30, 12  # the culture researcher's usual 8-12, and the most a long trip gets
MAX_FOOD_PLACES, USUAL_FOOD = 30, 12
LONG_TRIP_DAYS = 7    # longer trips also get day trips out of town
LONG_TRIP_EXTRA_CALLS = 4


def sights_wanted(brief: TripBrief) -> int:
    """Sights for every full day at the pace, plus a couple for the travel days (a packed trip at least 15)."""
    per_day = SIGHTS_PER_DAY[brief.pace or "balanced"]
    floor = 15 if brief.pace == "packed" else USUAL_SIGHTS
    return min(MAX_RESEARCH_SIGHTS, max(floor, per_day * max(0, brief.days - 2) + 2))


def extra_research_calls(brief: dict) -> int:
    """More tool calls for the culture and food researchers on a long trip (limits.limit_tool_calls)."""
    return LONG_TRIP_EXTRA_CALLS if int((brief or {}).get("days") or 0) > LONG_TRIP_DAYS else 0


def pace_research_block(brief: TripBrief) -> str:
    """Extra instruction for the culture researcher (state["trip_pace_research"]): a packed or long trip needs more
    sights than the usual 8-12, and a trip over a week day trips out of town (a live 21-day Porto plan had about 10
    sights and filled days 9-20 with "Leisure morning"). "" otherwise, so the instruction stays as it was."""
    want, long_trip = sights_wanted(brief), brief.days > LONG_TRIP_DAYS
    if want <= USUAL_SIGHTS and brief.pace != "packed":
        return ""
    text = (f"\n\nTRIP SIZE: {brief.days} days at a {brief.pace or 'balanced'} pace, so in step 3 choose up to {want} "
            "sights instead of 8-12 (still at least 3 indoor ones); use find_sights with radius_km 6, and read the "
            "\"See\" section of 2-3 district pages as well.")
    if long_trip:
        text += (f" The trip is longer than a week: also call wikivoyage_section(page=<destination city>, section=\"Go "
                 "next\") once and add 2-4 day trips (towns or sights within about 1.5 hours) as sights, each with "
                 "coordinates (from the listing, or geocode_place) and a summary that says it is a day trip. You may "
                 f"use {LONG_TRIP_EXTRA_CALLS} more tool calls for this.")
    return text


def food_research_block(brief: TripBrief) -> str:
    """Extra instruction for the food researcher (state["trip_food_research"]): two meals a day need more places on a
    long trip. "" for an ordinary trip."""
    want = min(MAX_FOOD_PLACES, max(USUAL_FOOD, 2 * brief.days))
    if want <= USUAL_FOOD:
        return ""
    return (f"\n\nTRIP SIZE: {brief.days} days, so in step 4 choose up to {want} places instead of 9-12 (each restaurant "
            "is used once), from the city page and 2-3 more district pages"
            + (f"; you may use {LONG_TRIP_EXTRA_CALLS} more tool calls for this." if brief.days > LONG_TRIP_DAYS else "."))


def plan_size_block(brief: TripBrief) -> str:
    """Extra instruction for the itinerary writer (state["trip_plan_size"]): the cap of 14 sights in total does not
    fit a long trip. "" for an ordinary trip."""
    per_day = SIGHTS_PER_DAY[brief.pace or "balanced"]
    want = min(MAX_RESEARCH_SIGHTS, max(14, (per_day - 1) * max(1, brief.days - 2) + 2))
    if want <= 14:
        return ""
    return (f"\n\nTRIP SIZE: {brief.days} days: plan up to {want} sights in total (not at most 14), so every full day "
            "has sights; a day trip out of town from the research can fill a full day on its own.")


def requirements_block(brief: TripBrief) -> str:
    """Instruction text for every planning agent (state["trip_requirements"]); "" when there is nothing to add,
    so the instructions of an ordinary trip stay exactly as they were."""
    lines = []
    if brief.avoid_modes:
        lines.append(f"- Never use these transport modes: {', '.join(brief.avoid_modes)}.")
    lines += [f"- {r}" for r in brief.requirements]
    if brief.day_trip:
        lines.append("- A day trip: out and back on the same day, no night and no stay (\"stay\": null, stay cost 0). "
                     "The return leaves on start_date in the evening; day 1 is both the arrival and the departure day. "
                     "The guide's \"Where to stay\" section says in one line that no stay is needed.")
    if brief.own_accommodation:
        lines.append("- No accommodation is needed: the travellers stay in their own place. Nobody searches for a "
                     "stay; the plan has \"stay\": null and no stay cost (stay_total_eur 0), and the guide's \"Where "
                     "to stay\" section says in one line that they stay in their own place.")
    if brief.own_car:
        drive = drive_estimate(brief)
        how_far = (f" If the route is by road only, it is roughly {drive[0]} km and {drive[1]:g} h of driving each "
                   "way (estimated from the straight-line distance; not valid for a route over the sea).") if drive else ""
        lines.append(f"- The travellers drive their own car from {brief.origin} to {brief.destination} and back, and "
                     f"use it at the destination.{how_far} Nobody searches or plans flights, trains or buses. The "
                     "ground transport searcher still calls plan_public_transport for both directions, only to see "
                     "whether the route crosses the sea: if a connection includes a ferry, that ferry is the car "
                     "ferry (report it as mode \"ferry\" and look up the fare for a car and its passengers with "
                     "web_search); if none does, it reports no options. The planner plans the car ferry from the "
                     "transport summary when there is one; otherwise the plan's transport outbound and return are "
                     "null with \"why\": \"own car\". Day 1 "
                     "starts with a \"transport\" item \"Arrive in <destination> by car\" at a realistic time after "
                     "the drive, and the last day ends with \"Depart by car\". The cost has no fuel or charging, tolls "
                     "or parking (they depend on the car) and no local public transport; the guide's \"Getting there "
                     "and back\" says they drive (with the car ferry if there is one, otherwise the road distance and "
                     "time above) and that those costs are not included.")
    if brief.travellers > PEOPLE_PER_ROOM and not brief.own_accommodation and not brief.day_trip:
        rooms = -(-brief.travellers // PEOPLE_PER_ROOM)
        lines.append(f"- Group of {brief.travellers}: hotels need {rooms} rooms (pass rooms={rooms} to "
                     f"search_google_hotels, search_hotel_rates and stay_deep_links; their prices are then the total "
                     f"for all rooms). Airbnb stays must sleep {brief.travellers}.")
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
    day_start: str | None = None,
    day_end: str | None = None,
    pace: str | None = None,
    own_accommodation: bool = False,
    own_car: bool = False,
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
        day_start: When the travellers like to start their days, "HH:MM", only if the user said so
            (e.g. "we wake up early" -> "07:00"). Leave it out otherwise: the planners use 09:30.
        day_end: When their days should end, "HH:MM", only if the user said so (default 22:00).
        pace: "relaxed", "balanced" or "packed", only if the user said how full the days should be
            (default "balanced").
        own_accommodation: true only if the travellers need no place to stay (e.g. "we stay in our own
            apartment", "we stay with family"). Then no hotel or Airbnb is searched or planned. Do not also put
            it in requirements.
        own_car: true only if the travellers drive their own car there and back (e.g. "we'll take our car",
            "a road trip in our car"). Then no flights, trains or buses are searched or planned. Do not also put
            it in requirements or avoid_modes.
    """
    brief = check_brief(dict(
        origin=origin, destination=destination, start_date=start_date, end_date=end_date,
        travellers=travellers, budget_tier=budget_tier, total_budget_eur=total_budget_eur,
        interests=interests or [], origin_lat=origin_lat, origin_lon=origin_lon,
        destination_lat=destination_lat, destination_lon=destination_lon,
        destination_country=destination_country, avoid_modes=avoid_modes, requirements=requirements,
        day_start=day_start, day_end=day_end, pace=pace, own_accommodation=own_accommodation, own_car=own_car,
    ))
    # empty optional fields are left out, so an ordinary brief (and every instruction showing it) is unchanged
    saved = {k: v for k, v in brief.model_dump(mode="json").items() if v or k not in OMIT_WHEN_EMPTY}
    saved.update(nights=brief.nights, days=brief.days)
    # the brief of the previous plan in this session: a change of only the day rhythm reuses its research
    tool_context.state["previous_trip_brief"] = tool_context.state.get("trip_brief")
    tool_context.state["trip_brief"] = saved
    tool_context.state["trip_requirements"] = requirements_block(brief)
    tool_context.state["trip_pace_research"] = pace_research_block(brief)
    tool_context.state["trip_food_research"] = food_research_block(brief)
    tool_context.state["trip_plan_size"] = plan_size_block(brief)
    return ok([saved])


# Typical daily costs per person, used only when no sourced figure is given. The result labels them as assumptions.
ASSUMED_FOOD_PER_PERSON_DAY = {"low": 30.0, "mid": 50.0, "high": 90.0}
ASSUMED_LOCAL_TRANSPORT_PER_PERSON_DAY = {"low": 5.0, "mid": 8.0, "high": 15.0}
MEAL_PER_PERSON = {"low": 15.0, "mid": 30.0, "high": 80.0}  # one restaurant meal, by the place's budget tier


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
    extra_food_eur: float = 0.0,
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
        extra_food_eur: Extra for planned restaurants above the budget tier, all travellers (code computes it;
            leave it out).
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
    extra_food_eur = max(0.0, float(extra_food_eur or 0.0))
    if extra_food_eur:
        assumptions.append(f"food: €{extra_food_eur:.0f} extra for planned restaurants above the {tier} budget "
                           f"(typical meal: {', '.join(f'{k} €{v:.0f}' for k, v in MEAL_PER_PERSON.items())} per person)")
    categories = {
        "transport": transport_total_eur,
        "stay": stay_total_eur,
        "food": food_per_person_per_day_eur * travellers * days + extra_food_eur,
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
