"""guide_writer: writes the final Markdown guide and saves it with save_guide -> final_guide.

save_guide stores the full guide (with the attribution footer) in state["final_guide"] and the file paths in
state["guide_paths"]. The agent's own short reply goes to "guide_reply". If the model writes the guide as its
reply instead of calling save_guide, the after-agent callback saves that reply, so a guide file always exists.
Either way, every link is grounded first (grounding.py).
"""
import re
from datetime import date
from pathlib import Path

from google.adk.agents import LlmAgent
from google.genai import types

from .. import config
from ..config import MODEL
from ..tools import save_guide
from ..tools.airports import far_airport
from ..tools.sun import daylight_note
from ..tools.currency import parse_number
from ..tools._result import ToolError
from ..tools.exports import EXPORTS_KEY, export_calendar, export_map_stops, write_calendar, write_map
from .output import extract_json
from .grounding import RESEARCH_KEYS, ground_guide_before_save, ground_markdown, known_urls
from .prompts import BRIEF, REQUIREMENTS
from .review_checks import (_dicts, _key, _parse, _price, check_plan, find_place, stay_nights, transport_summary,
                            usable_options)
from .retry import offer_retry_tool, retry_malformed_call

ROLE = """You are the GUIDE WRITER of a trip-planning team. Write the final trip guide for the travellers in
Markdown, from the plan and research below, then save it with save_guide."""

INPUTS = """THE PLAN (itinerary, JSON): {itinerary?}

TRANSPORT SUMMARY: {transport_summary?}

STAY SUMMARY: {stay_summary?}

SIGHTS with history (culture_findings): {culture_findings?}

FOOD (food_findings): {food_findings?}"""

STRUCTURE = """GUIDE STRUCTURE (Markdown, English, friendly and compact):
# <Destination> trip guide: <start date> – <end date>
One short paragraph: who travels (travellers, origin), budget tier and total budget, the interests, and
whether the weather is a forecast or only typical for these dates.

## Getting there and back
The chosen outbound and return (from the plan) first, then the cheapest and fastest alternatives from the
transport summary, as one table per direction:
| | Mode | Operator | Depart → Arrive | Duration | Stops | Price (all travellers) | Link |
Then any warning from the options' details (airport change, self-transfer, bags, price per person).

## Where to stay
The chosen stay with price per night, total, rating (say the scale) and why. Then a table of the stay
summary's by_area: | Area | Airbnb | Hotel | From €/night |, each name a link. Then any caveats from the
stays' details or the summary's notes (e.g. liteAPI rates may be below public prices, city tax not included).

## Day by day
For each day: "### Day N – <weekday> <date>: <area>" and a line with the weather and walking distance.
Then a bullet per item in time order: **HH:MM Name** – what to see or eat. For sights add 1-2 sentences of
history from the SIGHTS summary, the hours, the item's "ticket" text copied word for word (e.g. "ticket €15",
"ticket price: see the website") and a [source] link. For restaurants add what to order and the budget tier.

## Food to try
One bullet for EVERY entry in FOOD's "dishes" list: **name** – its description – [source](its source_url).

## Cost estimate
A table from the plan's "cost" result: | Category | Total (€) | Share |, then total, per person, per person per
day, and the budget comparison (within budget or by how much over). Take every figure from "cost" only, never
from "notes" (code recomputed the cost; notes may be out of date). List the cost assumptions (typical food
and local transport costs) right under the table.

## Good to know
Short bullets: failed sources or fallbacks from the agents' notes, prices to double-check, closed days, and any
big event on the trip dates that the SIGHTS notes mention (with its link)."""

RULES = """RULES:
- Use ONLY facts, prices, hours and links from the inputs above. Never add places, prices or links from memory.
- Mark every transport and stay price with its source: "€297 (live)" when price_source is "live", "€34 (web
  estimate)" when it is "web_estimate". Sight tickets come from OpenStreetMap or the sights' own pages: give
  them without a marker.
- Use each item's own url / source_url for links, copied character for character (keep "http://" as it is);
  never shorten, "fix" or invent URLs. Write links as [text](url): no quotes or spaces inside the brackets.
- Never write that a place, transport or stay is wheelchair-accessible, child-friendly, pet-friendly or suits a
  diet, and never add facts such as "no alcohol is served", unless an input above says so for that place. Call a
  stay a hostel when its name or details say hostel.
- Keep it scannable: tables for comparisons, bullets for days, no long paragraphs."""

SAVE = """WHEN THE GUIDE IS READY:
1. Call save_guide(markdown_text=<the whole guide>, filename_slug="<destination>-<start_date>-to-<end_date>"),
   e.g. "lisbon-2027-05-10-to-2027-05-14". Call it exactly once; do not write the guide anywhere else.
2. Then call export_calendar() and export_map_stops() once each (no arguments): they save the days as a
   calendar file (.ics) and the planned places as a map file (GeoJSON) next to the guide.
3. Then reply in 3-5 short lines: where the guide, the calendar and the map were saved (paths from the tool
   results), the recommended transport and stay with the total cost, and whether it fits the budget.
If save_guide returns an error, fix what the reason says and call it again (at most 2 tries)."""


COST_NAMES = {"transport": "Transport", "stay": "Stay", "food": "Food", "activities": "Activities",
              "local_transport": "Local transport"}
_BOLD_ITEM = re.compile(r"^\s*[-*]\s*\*\*(\d{1,2}:\d{2})\s+(.+?)\*\*", re.MULTILINE)


def _section(text: str, heading: str) -> re.Match | None:
    """The body of "## <heading>" (up to the next "## " heading)."""
    return re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)


def render_cost(cost: dict) -> str:
    """The cost estimate as Markdown, every figure from the plan's code-computed cost."""
    rows = ["| Category | Total (€) | Share |", "|---|---|---|"]
    rows += [f"| {COST_NAMES.get(k, k)} | {v:.2f} | {(cost.get('share_pct') or {}).get(k, 0):.1f} % |"
             for k, v in cost["categories_eur"].items()]
    rows.append(f"| **Total** | **{cost['total_eur']:.2f}** | |")
    lines = ["\n".join(rows), "", f"- Per person: €{cost['per_person_eur']:.2f}",
             f"- Per person per day: €{cost['per_person_per_day_eur']:.2f}"]
    if cost.get("incomplete"):
        lines.append(f"- Not included (price unknown): {', '.join(cost['incomplete'])}. The real total is higher.")
    if cost.get("budget_eur"):
        verdict = ("within the budget" if cost.get("within_budget")
                   else f"€{-cost['difference_eur']:.2f} over the budget")
        if cost.get("incomplete") and cost.get("within_budget"):
            verdict += " without the unknown fares"
        lines.append(f"- Budget €{cost['budget_eur']:.2f}: {verdict} ({cost.get('budget_used_pct')} % used)")
    if cost.get("assumptions"):
        lines += ["", "**Assumptions**"] + [f"- {a}" for a in cost["assumptions"]]
    return "\n".join(lines) + "\n\n"


def fix_cost_section(text: str, cost: dict | None) -> str:
    """Rebuilds "## Cost estimate" from the plan's cost when the guide's figures differ from it.

    A live Tallinn guide showed the stay of another hostel (€56) and a €338 total next to the plan's
    percentages; the plan, checked by code, cost €332."""
    match = _section(text, "Cost estimate")
    if not (match and isinstance(cost, dict) and cost.get("categories_eur")):
        return text
    found = []
    for n in re.findall(r"\d[\d.,]*\d|\d", match.group(1)):
        try:
            found.append(parse_number(n))
        except ValueError:
            continue
    wanted = list(cost["categories_eur"].values()) + [cost["total_eur"]]
    if all(any(abs(w - f) <= 0.51 for f in found) for w in wanted)             and (not cost.get("incomplete") or "unknown" in match.group(1).lower()):
        return text
    return text[:match.start(1)] + render_cost(cost) + text[match.end(1):]


def guide_drift(text: str, plan: dict) -> list[str]:
    """Where the guide differs from the checked plan: another stay, or day items added or left out.

    Travel items are not compared (the guide names them its own way and covers them under "Getting there")."""
    notes = []
    stay = plan.get("stay") if isinstance(plan.get("stay"), dict) else {}
    where = _section(text, "Where to stay")
    if where and stay.get("name") and _key(stay["name"]) not in _key(where.group(1).split("\n|", 1)[0]):
        notes.append(f"The stay described in the guide is not the plan's stay: the plan and the cost use "
                     f"'{stay['name']}' (€{stay.get('total_price_eur')} total).")
    for day in plan.get("days") or []:
        if not isinstance(day, dict):
            continue
        body = re.search(rf"^### Day {day.get('day')}\b.*?(?=^### |^## |\Z)", text, re.MULTILINE | re.DOTALL)
        if not body:
            continue
        written = [name.strip() for _, name in _BOLD_ITEM.findall(body.group(0))]
        planned = [i.get("name") for i in day.get("items") or [] if isinstance(i, dict) and i.get("name")]
        travel = [i.get("name") for i in day.get("items") or [] if isinstance(i, dict) and i.get("kind") == "transport"]
        added = [w for w in written if not find_place(w, [{"name": p} for p in planned])
                 and not re.match(r"(arriv|depart|leave|fly|flight|ferry|train|bus)", w, re.I)]
        missing = [p for p in planned if p not in travel and not find_place(p, [{"name": w} for w in written])]
        notes += [f"Day {day.get('day')}: the guide adds '{w}', which is not in the checked plan." for w in added]
        notes += [f"Day {day.get('day')}: the plan's '{p}' is missing from the guide." for p in missing]
    return notes


SHOWN_SHOULD_FIX = ("interest_not_found",)  # weak points the travellers should still see in "Still to check"


def open_issues(state, guide_text: str = "") -> str:
    """A "Still to check" section: must-fix problems the code check still finds in the final plan, and where the
    guide drifted from that plan; "" when there is nothing.

    The last review round's revision is never reviewed again, and a loop can end at its round limit with
    problems left; the travellers must see those instead of a guide that looks approved."""
    plan = _parse(state.get("itinerary"))
    if not plan.get("days"):
        return ""
    report = check_plan(plan, state)
    declined = {(d.get("day"), d.get("issue")) for d in state.get("taste_declined") or []}  # the critic's choice
    open_ = report["must_fix"] + [x for x in report.get("decide") or [] if (x.get("day"), x["issue"]) not in declined]
    open_ += [x for x in report["should_fix"] if x["issue"] in SHOWN_SHOULD_FIX]
    lines = [f"- {'Day ' + str(x['day']) + ': ' if x.get('day') else ''}{x['detail']}" for x in open_]
    lines += [f"- {note}" for note in guide_drift(guide_text, plan)]
    if not lines:
        return ""
    return ("\n\n## Still to check\nThe final code check found these points in the plan and the guide. "
            "Check them before booking:\n" + "\n".join(lines) + "\n")


LATE_ARRIVAL_MIN = 17 * 60   # arriving after 17:00 loses most of the first day
EARLY_RETURN_MIN = 12 * 60   # leaving before noon loses most of the last day
KEEPS_FIRST_DAY = 12 * 60    # an alternative arriving by noon keeps most of the first day ...
KEEPS_LAST_DAY = 15 * 60     # ... and one leaving from 15:00 most of the last


def _when(iso) -> tuple[str, int] | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})", str(iso or ""))
    return (m.group(1), int(m.group(2)) * 60 + int(m.group(3))) if m else None


def travel_warnings(plan: dict, state) -> list[str]:
    """Warnings for travel times that cost most of the first or last day, with the cheapest option in the
    transport summary that keeps the day. Not an error: a cheap early flight is a fair choice, but the
    travellers must know what it costs them (a live Kraków plan lost its last day to an 08:50 flight)."""
    brief = state.get("trip_brief") or {}
    avoid = set(brief.get("avoid_modes") or [])
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    summary = transport_summary(state)
    notes = []
    for leg, key, day in (("outbound", "arrive", brief.get("start_date")), ("return", "depart", brief.get("end_date"))):
        chosen = transport.get(leg) if isinstance(transport.get(leg), dict) else None
        when = _when((chosen or {}).get(key))
        if not (chosen and day and when and when[0] == day):
            continue
        if (when[1] < LATE_ARRIVAL_MIN) if leg == "outbound" else (when[1] >= EARLY_RETURN_MIN):
            continue
        better = {}
        for option in _dicts(summary.get(leg) if isinstance(summary.get(leg), (dict, list)) else []):
            w, price = _when(option.get(key)), _price(option.get("price_eur"))
            if not (option.get("operator") and w and w[0] == day and price is not None) or option.get("mode") in avoid:
                continue
            if far_airport(option, leg, brief.get("destination_lat"), brief.get("destination_lon")):
                continue  # a live Kyiv guide offered a "day-keeping" flight from Chișinău
            if (w[1] <= KEEPS_FIRST_DAY) if leg == "outbound" else (w[1] >= KEEPS_LAST_DAY):
                better[(option["operator"], option.get(key))] = option
        stamp = f"{when[1] // 60:02d}:{when[1] % 60:02d} on {date.fromisoformat(day):%a} {date.fromisoformat(day).day} " \
                f"{date.fromisoformat(day):%b}"
        mode = chosen.get("mode") or "trip"
        text = (f"Your outbound {mode} arrives at {stamp}, so most of the first day is lost." if leg == "outbound"
                else f"Your return {mode} leaves at {stamp}, so most of the last day is lost.")
        if better:
            best = min(better.values(), key=lambda o: _price(o.get("price_eur")))
            diff = _price(best.get("price_eur")) - (_price(chosen.get("price_eur")) or 0)
            text += (f" {best['operator']} at {str(best.get(key))[11:16]} costs €{abs(diff):g} "
                     f"{'more' if diff >= 0 else 'less'} for the group (€{_price(best.get('price_eur')):g} in total) "
                     "and keeps most of the day")
            home = _when(best.get("arrive")) if leg == "return" else None
            if home and home[0] > day:  # an overnight connection: say when it gets home
                text += (f", but it gets home only on {date.fromisoformat(home[0]):%a} {date.fromisoformat(home[0]).day} "
                         f"{date.fromisoformat(home[0]):%b} at {home[1] // 60:02d}:{home[1] % 60:02d}")
            text += "."
        notes.append(text)
    searched = {leg: list(_dicts(summary.get(leg) if isinstance(summary.get(leg), (dict, list)) else []))
                for leg in ("outbound", "return")}
    return notes + feasibility_warnings(plan, brief) + unpriced_cheap_options(plan, brief, searched)


def unpriced_cheap_options(plan: dict, brief: dict, summary: dict) -> list[str]:
    """When the plan is over budget, the ferry, bus or train options whose fare nobody found: often the cheap way
    there (a live Åland run on €500 planned flights at €652; the ferries had no price and were left out)."""
    cost = plan.get("cost") if isinstance(plan.get("cost"), dict) else {}
    if cost.get("within_budget") is not False:
        return []
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    notes = []
    for leg in ("outbound", "return"):
        chosen = transport.get(leg) if isinstance(transport.get(leg), dict) else {}
        cheap = [o for o in usable_options(summary, leg, brief)
                 if o.get("mode") in ("ferry", "bus", "train") and _price(o.get("price_eur")) is None
                 and o.get("operator") != chosen.get("operator")]
        if cheap:
            o = cheap[0]
            notes.append(f"The plan is over budget, and the {leg} {o['mode']} ({o['operator']}, {str(o.get('depart'))[11:16]} "
                         f"on {_day_text(str(o.get('depart')))}) has no known fare: it may be cheaper than the chosen "
                         "option, check it with the operator.")
    return notes


def _day_text(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d:%a} {d.day} {d:%b}"


def feasibility_warnings(plan: dict, brief: dict) -> list[str]:
    """Facts about the chosen transport that the travellers must know even when no better option exists: a flight
    to or from an airport far from the destination (live Kyiv run: Chișinău), a return after the trip's last day
    (no bed that night), and a stay that starts later because the outbound travels overnight."""
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    destination = brief.get("destination") or "the destination"
    notes = []
    for leg in ("outbound", "return"):
        far = far_airport(transport.get(leg), leg, brief.get("destination_lat"), brief.get("destination_lon"))
        if far:
            notes.append(f"Your {leg} flight {'lands at' if leg == 'outbound' else 'leaves from'} {far['iata']} "
                         f"({far['city']}), about {far['km']} km from {destination} in a straight line: you must "
                         f"travel {'on to' if leg == 'outbound' else 'there from'} {destination} yourself, and that "
                         "journey is not planned or priced here.")
    back = transport.get("return") if isinstance(transport.get("return"), dict) else None
    end = brief.get("end_date")
    if back and end and str(back.get("depart", ""))[:10] > end and not brief.get("own_accommodation"):
        notes.append(f"Your return {back.get('mode') or 'trip'} leaves at {str(back['depart'])[11:16]} on "
                     f"{_day_text(back['depart'])}, after the trip's last day: no bed is booked for the night before.")
    nights = stay_nights(plan, brief) if isinstance(plan.get("stay"), dict) else None
    if nights:
        notes.append(f"You travel overnight, so book the stay from {_day_text(nights[2])} for {nights[0]} night(s), "
                     f"not {nights[1]}: the cost estimate counts {nights[0]}.")
    return notes


def add_important(text: str, notes: list[str]) -> str:
    """Puts the warnings right before the first section, where nobody misses them."""
    if not notes:
        return text
    block = "> **Important**\n" + "\n".join(f"> - {n}" for n in notes) + "\n\n"
    first = re.search(r"^## ", text, re.MULTILINE)
    return text[:first.start()] + block + text[first.start():] if first else text.rstrip() + "\n\n" + block


RHYTHM_EXAMPLES = '"we wake up early, start at 07:00", "we stay out late" or "a more relaxed pace, please"'


def rhythm_sentence(brief: dict) -> str:
    """How the plan's days run, from the brief (its defaults when the user set nothing)."""
    start = f"start at {brief['day_start']}" if brief.get("day_start") else f"start around {config.DEFAULT_DAY_START}"
    end, pace = brief.get("day_end") or config.DEFAULT_DAY_END, brief.get("pace") or config.DEFAULT_PACE
    chosen = any(brief.get(k) for k in ("day_start", "day_end", "pace"))
    return f"Days in this plan {start} and end by {end}, at a {pace} pace{'' if chosen else ' (the defaults)'}."


def add_day_rhythm(text: str, brief: dict) -> str:
    """A line under "## Day by day" saying how the days run and that the concierge can change it."""
    note = f"*{rhythm_sentence(brief)} For another rhythm, tell the concierge, e.g. {RHYTHM_EXAMPLES}.*\n\n"
    heading = re.search(r"^## Day by day[^\n]*\n+", text, re.MULTILINE)
    return text[:heading.end()] + note + text[heading.end():] if heading else text


def good_to_know(plan: dict, state) -> list[str]:
    """Lines code adds under "## Good to know": facts that need no source (or come from code)."""
    brief = state.get("trip_brief") or {}
    lines = []
    if brief.get("destination_country"):  # no source says how safe a country is: point to the official advice
        lines.append(f"Before booking, check your government's official travel advice for "
                     f"{brief['destination_country']} (safety, entry rules, insurance).")
    try:
        light = daylight_note(brief.get("destination_lat"), date.fromisoformat(brief["start_date"]),
                              date.fromisoformat(brief["end_date"]))
    except (KeyError, TypeError, ValueError):
        light = None
    if light:
        lines.append(light)
    needs = " ".join(str(r) for r in brief.get("requirements") or [])
    for pattern, line in NOT_VERIFIED:  # the tools do not report these: say so instead of a guide claiming them
        if pattern.search(needs):
            lines.append(line)
    return lines


# Requirements the planner's sources cannot confirm (a live guide called every bus and hotel "wheelchair-accessible")
NOT_VERIFIED = [
    (re.compile(r"wheelchair|mobility|accessib|disab", re.I),
     "Accessibility was not verified by the planner: check step-free access and assistance with each airline, "
     "train or bus operator, the stay and every sight before booking."),
    (re.compile(r"child|kid|infant|baby|toddler|stroller|pram", re.I),
     "Child needs (cots, child fares, stroller access) were not verified: check with each provider."),
    (re.compile(r"vegan|vegetarian|halal|kosher|gluten|allerg|lactose|diet", re.I),
     "Menus were not checked for your diet: confirm with each restaurant before you go."),
    (re.compile(r"\b(?:pet|pets|dog|dogs|cat|cats)\b", re.I),
     "Pet rules were not verified: check with the transport operators and the stay."),
]


def add_good_to_know(text: str, lines: list[str]) -> str:
    """Appends the lines to "## Good to know" (made at the end if the guide has none)."""
    if not lines:
        return text
    bullets = "\n".join(f"- {line}" for line in lines) + "\n"
    match = _section(text, "Good to know")
    if match:
        body = match.group(1).rstrip("\n")
        return text[:match.start(1)] + (body + "\n" if body else "") + bullets + "\n" + text[match.end(1):]
    return text.rstrip() + "\n\n## Good to know\n" + bullets


def finalise_guide(text: str, state) -> str:
    """The guide as it is saved: travel-time warnings, the day-rhythm line, code lines under "Good to know", cost
    figures from the plan and "Still to check"."""
    plan = _parse(state.get("itinerary"))
    text = add_important(text, travel_warnings(plan, state))
    text = add_good_to_know(text, good_to_know(plan, state))
    text = add_day_rhythm(text, state.get("trip_brief") or {})
    text = fix_cost_section(text, plan.get("cost"))
    section = open_issues(state, text)
    return text.rstrip() + section if section else text


def add_open_issues_before_save(tool, args, tool_context):
    """Before-tool callback for save_guide (after link grounding): see finalise_guide."""
    if getattr(tool, "name", "") == "save_guide" and isinstance(args.get("markdown_text"), str):
        args["markdown_text"] = finalise_guide(args["markdown_text"], tool_context.state)
    return None


def _slug(brief: dict) -> str:
    return f"{brief.get('destination', 'trip')}-{brief.get('start_date', '')}-to-{brief.get('end_date', '')}"


def _guide_from_reply(reply: str) -> str:
    """The guide in the reply: the reply itself, or the markdown_text of a save_guide call written as JSON text
    (GPT OSS sometimes prints the tool call instead of making it; seen in a live Tallinn run)."""
    if '"markdown_text"' in reply:
        try:
            text = extract_json(reply).get("markdown_text")
        except ValueError:
            text = None
        if isinstance(text, str):
            return text
    return reply


def save_reply_if_not_saved(callback_context):
    """Safety net: if the model wrote the guide as its reply instead of calling save_guide, save the reply."""
    state = callback_context.state
    reply = _guide_from_reply(state.get(RAW_REPLY_KEY) or state.get("guide_reply") or "")
    if state.get("final_guide") or reply.count("\n## ") < 2:
        return None
    reply, _, _ = ground_markdown(reply, known_urls(state, RESEARCH_KEYS + ("itinerary",)))
    reply = finalise_guide(reply, state)
    result = save_guide(reply, _slug(state.get("trip_brief") or {}), callback_context)
    if result.get("status") != "ok":
        return None
    paths = result["results"][0]
    return types.Content(role="model", parts=[types.Part(
        text=f"Saved the guide to {paths['markdown_path']} and {paths['html_path']}.")])


def rhythm_offer(brief: dict) -> str:
    return (f"{rhythm_sentence(brief)} If you would like a different rhythm (e.g. "
            f"{RHYTHM_EXAMPLES}), tell me: I will keep the research and only plan the days again, which is quicker.")


def export_if_missing(state) -> None:
    """Safety net: the calendar and map files the guide writer did not make (the demo's second guide writer skipped
    the map). They are plain conversions of the plan, so code writes them; logged in state["exports_by_code"]."""
    made = []
    for kind, write in (("calendar", write_calendar), ("map", write_map)):
        if (state.get(EXPORTS_KEY) or {}).get(kind):
            continue
        try:
            write(state)
            made.append(kind)
        except ToolError:  # e.g. no planned place has coordinates: no map
            continue
    if made:
        state["exports_by_code"] = made


RAW_REPLY_KEY = "guide_reply_raw"  # the model's own final text, kept out of the chat (see hold_guide_reply)


def hold_guide_reply(callback_context, llm_response):
    """After-model callback: the guide writer's own final text is kept in state, not shown. Live runs showed absolute
    local paths, mangled ones with wrong dates, "the temporary folder", or the raw {"markdown_text": ...} JSON, in two
    messages; finish_guide sends one message built in code instead. A turn with a tool call passes unchanged."""
    content = llm_response.content
    parts = (content.parts if content else None) or []
    if not parts or any(p.function_call for p in parts):
        return None
    text = "".join(p.text or "" for p in parts if not p.thought)
    if not text.strip():
        return None
    state = callback_context.state
    state[RAW_REPLY_KEY] = ((state.get(RAW_REPLY_KEY) or "") + "\n" + text).strip()
    if state.get("final_guide"):  # the usual case: the guide is saved, so the code-built message replaces the reply
        export_if_missing(state)
        shown = ready_text(state)
        state[MESSAGE_SENT_KEY] = getattr(callback_context, "invocation_id", True)
    else:  # the reply may be the guide itself: finish_guide saves it and sends the message
        shown = "Saving the trip guide."
    # never an empty reply: ADK reports MODEL_RETURNED_NO_CONTENT for one (seen in the second stress run)
    llm_response.content = types.Content(role="model", parts=[types.Part(text=shown)])
    return llm_response


MESSAGE_SENT_KEY = "guide_message_sent"  # the invocation whose guide-ready message hold_guide_reply already showed


def ready_text(state) -> str:
    return guide_ready_message(state) + "\n\n" + rhythm_offer(state.get("trip_brief") or {})


def _clock_day(iso) -> str:
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso or "")
    return f"{d.day} {d:%b} {str(iso)[11:16]}".strip()


def guide_ready_message(state) -> str:
    """The chat message when the guide is saved: file names (no folders), the chosen transport and stay, the total
    and how it compares with the budget. Every figure comes from the checked plan."""
    names = {k: Path(v).name for k, v in (state.get("guide_paths") or {}).items() if v}
    files = [f"{names['markdown']} (and {names['html']}, best opened in a browser)" if "markdown" in names and "html" in names
             else names.get("markdown") or names.get("html")]
    files += [f"the calendar {names['calendar']}" if "calendar" in names else None,
              f"the map {names['map']}" if "map" in names else None]
    lines = ["Your trip guide is ready: " + ", ".join(f for f in files if f) + " in the output folder."]
    plan = _parse(state.get("itinerary"))
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    legs = [f"{leg} {o.get('operator')} {o.get('mode') or ''} {_clock_day(o.get('depart'))}".replace("  ", " ")
            for leg, o in (("out", transport.get("outbound")), ("back", transport.get("return"))) if isinstance(o, dict)]
    if legs:
        lines.append("Transport: " + "; ".join(legs) + ".")
    stay = plan.get("stay")
    if isinstance(stay, dict) and stay.get("name"):
        total = _price(stay.get("total_price_eur"))
        lines.append(f"Stay: {stay['name']}" + (f" (€{total:.0f} in total)." if total is not None else "."))
    cost = plan.get("cost") if isinstance(plan.get("cost"), dict) else {}
    if isinstance(cost.get("total_eur"), (int, float)):
        line = f"Estimated total: €{cost['total_eur']:.0f}"
        if cost.get("budget_eur"):
            line += (f", within your €{cost['budget_eur']:.0f} budget" if cost.get("within_budget")
                     else f", €{-cost['difference_eur']:.0f} over your €{cost['budget_eur']:.0f} budget")
        if cost.get("incomplete"):
            line += " (without the unknown fares)"
        lines.append(line + ".")
    if "## Still to check" in (state.get("final_guide") or ""):
        lines.append("The guide ends with a few points to check before booking.")
    return "\n".join(lines)


def finish_guide(callback_context):
    """After-agent callback: the safety nets above (a guide written as text, missing exports), then one chat message
    built in code (guide_ready_message) with the offer to change the day rhythm."""
    content = save_reply_if_not_saved(callback_context)
    state = callback_context.state
    if not state.get("final_guide"):
        return content
    export_if_missing(state)
    if state.get(MESSAGE_SENT_KEY) == getattr(callback_context, "invocation_id", True):
        return None  # already shown as the guide writer's reply
    return types.Content(role="model", parts=[types.Part(text=ready_text(state))])


def build_guide_writer(standalone: bool = False) -> LlmAgent:
    """standalone: a sub-agent of the concierge, started by approve_plan (approval.py). Its turn then ends the
    invocation and the next user message goes back to the concierge, never to the guide writer."""
    instruction = "\n\n".join([ROLE, BRIEF, INPUTS, STRUCTURE, RULES, SAVE]) + REQUIREMENTS
    return LlmAgent(
        name="guide_writer",
        model=MODEL,
        description="Writes the final trip guide and saves it as Markdown and HTML.",
        instruction=instruction,
        tools=[save_guide, export_calendar, export_map_stops],
        output_key="guide_reply",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0.3),
        # links in the guide must come from the research; problems the review left are listed for the travellers
        before_tool_callback=[ground_guide_before_save, add_open_issues_before_save],
        after_agent_callback=finish_guide,
        before_model_callback=offer_retry_tool,
        after_model_callback=[retry_malformed_call, hold_guide_reply],
        disallow_transfer_to_parent=standalone,
        disallow_transfer_to_peers=standalone,
    )
