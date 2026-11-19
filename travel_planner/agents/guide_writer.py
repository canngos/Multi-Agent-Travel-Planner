"""guide_writer: writes the final Markdown guide and saves it with save_guide -> final_guide.

save_guide stores the full guide (with the attribution footer) in state["final_guide"] and the file paths in
state["guide_paths"]. The agent's own short reply goes to "guide_reply". If the model writes the guide as its
reply instead of calling save_guide, the after-agent callback saves that reply, so a guide file always exists.
Either way, every link is grounded first (grounding.py).
"""
import re
from datetime import date

from google.adk.agents import LlmAgent
from google.genai import types

from .. import config
from ..config import MODEL
from ..tools import save_guide
from ..tools.currency import parse_number
from ..tools.exports import export_calendar, export_map_stops
from .output import extract_json
from .grounding import RESEARCH_KEYS, ground_guide_before_save, ground_markdown, known_urls
from .prompts import BRIEF, REQUIREMENTS
from .review_checks import _dicts, _key, _parse, _price, check_plan, find_place
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
summary's by_area: | Area | Airbnb | Hotel | From €/night |, each name a link. Then the recommendation and
any caveats (e.g. liteAPI rates may be below public prices, city tax not included).

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
Short bullets: failed sources or fallbacks from the agents' notes, prices to double-check, closed days."""

RULES = """RULES:
- Use ONLY facts, prices, hours and links from the inputs above. Never add places, prices or links from memory.
- Mark every transport and stay price with its source: "€297 (live)" when price_source is "live", "€34 (web
  estimate)" when it is "web_estimate". Sight tickets come from OpenStreetMap or the sights' own pages: give
  them without a marker.
- Use each item's own url / source_url for links, copied character for character (keep "http://" as it is);
  never shorten, "fix" or invent URLs. Write links as [text](url): no quotes or spaces inside the brackets.
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
    if cost.get("budget_eur"):
        verdict = ("within the budget" if cost.get("within_budget")
                   else f"€{-cost['difference_eur']:.2f} over the budget")
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
    if all(any(abs(w - f) <= 0.51 for f in found) for w in wanted):
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
    summary = _parse(state.get("transport_summary"))
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
                     "and keeps most of the day.")
        notes.append(text)
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


def finalise_guide(text: str, state) -> str:
    """The guide as it is saved: travel-time warnings, the day-rhythm line, cost figures from the plan and
    "Still to check"."""
    plan = _parse(state.get("itinerary"))
    text = add_important(text, travel_warnings(plan, state))
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
    reply = _guide_from_reply(state.get("guide_reply") or "")
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
    return (f"Your trip guide is ready. {rhythm_sentence(brief)} If you would like a different rhythm (e.g. "
            f"{RHYTHM_EXAMPLES}), tell me: I will keep the research and only plan the days again, which is quicker.")


def finish_guide(callback_context):
    """After-agent callback: the safety net above, then (outside demo mode) the offer to change the day rhythm,
    as the last chat message of the plan."""
    content = save_reply_if_not_saved(callback_context)
    state = callback_context.state
    if not state.get("final_guide"):
        return content
    offer = rhythm_offer(state.get("trip_brief") or {})
    if content and content.parts and content.parts[0].text:
        content.parts[0].text += "\n\n" + offer
        return content
    return types.Content(role="model", parts=[types.Part(text=offer)])


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
        after_model_callback=retry_malformed_call,
        disallow_transfer_to_parent=standalone,
        disallow_transfer_to_peers=standalone,
    )
