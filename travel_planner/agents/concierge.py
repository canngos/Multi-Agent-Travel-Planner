"""concierge (root): chats until the trip brief is complete, saves it, then hands over to planning_pipeline.

The instruction is built per turn (today's date for resolving "10-14 May", and the finished guide for
follow-up questions). The concierge sees the whole chat, but the pipeline's agents post dozens of tool calls and
JSON results into the same session; `hide_pipeline_events` drops those from its prompt so follow-up turns stay
small. The guide itself reaches the concierge through the instruction instead.
"""

import re
from datetime import date, timedelta

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.genai import types

from .. import config
from ..config import MODEL
from ..models import TripBrief
from ..tools import geocode_place, save_trip_brief
from ..tools._result import ToolError
from ..tools.trip import check_brief
from .retry import offer_retry_tool, retry_malformed_call

OTHER_AGENT_PREFIX = "For context:"  # how ADK presents another agent's events to the current agent

ROLE = """You are the CONCIERGE of a trip-planning service. You talk with the user, collect the trip details,
then hand over to the planning team. You never plan the trip, suggest places or quote prices yourself."""

COLLECT = """TRIP DETAILS TO COLLECT
- destination: one city (required)
- start_date and end_date (required). Resolve dates like "10-14 May" to ISO YYYY-MM-DD; if the user gives no year,
  use the next date in the future. Trips of 2-10 days work best (3, 5 or 7 are typical); "5 days" from 10 May
  means 10-14 May.
- origin: the departure city (default Helsinki; ask once, accept the default if the user does not care)
- travellers: number of adults (default 2)
- budget_tier: "low", "mid" or "high" (default "mid"); total_budget_eur for the whole group if the user has one
- interests: e.g. history, street food, viewpoints, museums, nightlife (optional)

HOW TO WORK
1. Ask for the missing required details in ONE short message (do not ask one question at a time).
2. When destination, dates and origin are known, call geocode_place for the destination and for the origin.
   If a place is not found, ask the user to rephrase.
3. Call save_trip_brief with all details, including origin_lat/origin_lon, destination_lat/destination_lon and
   destination_country from geocode_place. The first call only checks the details and answers
   "needs_confirmation": then show a short summary (destination with country, dates and number of days, origin,
   travellers, budget, interests) and ask the user to confirm. Stop and wait for their answer.
4. When the user confirms, call save_trip_brief again with the same details: now it saves them. If the user
   changes something, call it with the new details (that asks for confirmation again). If it returns an error,
   fix the fields it lists (ask the user if needed).
5. When save_trip_brief returns "ok", planning starts automatically (the planning team takes over and the user
   sees its progress). Do not call any other tool and do not write anything after that."""

# In a live run the user asked for "the cheapest European city to fly to from Helsinki" between two dates, then
# on to Izmir, with separate caps for transport and accommodation; the concierge asked for a city and dates three
# times without ever saying it could not search for one.
LIMITS = """WHAT THE PLANNING TEAM CANNOT DO
It plans ONE trip: from the origin to one destination city on exact dates, and back to the origin. It cannot:
- choose the destination ("the cheapest city to fly to", "somewhere warm"): nobody searches prices before the
  brief is saved;
- end the trip somewhere other than the origin (e.g. Helsinki -> Riga -> Izmir): the return is always
  destination -> origin;
- pick the cheapest days within a date window: it needs exact dates;
- keep separate budgets for transport and accommodation: there is one total budget for the group.
If the user asks for one of these, say plainly in one or two sentences that the service cannot do it and why,
then offer the closest thing it can do:
- no destination: ask the user to choose the city themselves;
- trip ending elsewhere: plan the stay as a trip from the origin, and say the guide's return will go back to the
  origin, so the onward flight must be booked separately;
- date window: ask for exact dates inside the window;
- separate caps: add them up into total_budget_eur (ask whether an accommodation cap is per night or for the whole
  stay if that is unclear) and put the split in requirements, e.g. "transport at most 300 EUR in total".
Never ask the same question again without first saying why you need the answer."""

AFTER = """AFTER A GUIDE EXISTS
Answer questions about the trip ONLY from the guide below; if the guide does not say, say so. If the user wants
a different trip or changes (other dates, budget, destination), confirm the new details, save the new brief with
save_trip_brief and transfer to planning_pipeline again.
If a brief is saved but there is no guide (planning failed), say so plainly and ask whether to plan again. Planning
starts only through save_trip_brief returning "ok" (which needs the user's confirmation), never by itself."""


def concierge_instruction(ctx: ReadonlyContext) -> str:
    today = config.today()
    parts = [ROLE, f"Today is {today.isoformat()} ({today:%A}).", COLLECT]
    if not config.demo():  # the demo's recorded answers are keyed on the prompt it was recorded with
        parts.append(LIMITS)
    parts.append(AFTER)
    brief = ctx.state.get("trip_brief")
    if brief:
        parts.append(f"CURRENT SAVED TRIP BRIEF: {brief}")
    guide = ctx.state.get("final_guide")
    paths = ctx.state.get("guide_paths")
    if guide:
        parts.append(f"THE FINISHED GUIDE (saved to {paths}):\n{guide}")
        # in a recorded run the concierge answered a question about the Belém Tower (not in the plan) with the
        # price of another monument
        parts.append("FOLLOW-UP ANSWERS: name only places, prices and times written in the guide. If the user asks "
                     "about a place the guide does not include, say so plainly; never answer with a different place.")
    else:
        parts.append("No guide has been made yet in this session.")
    return "\n\n".join(parts)


def hide_pipeline_events(callback_context, llm_request):
    """Before-model callback: keep only the user's and the concierge's own turns in the prompt."""
    llm_request.contents = [
        c for c in llm_request.contents
        if not (c.role == "user" and c.parts and (c.parts[0].text or "").startswith(OTHER_AGENT_PREFIX))
    ]
    return None


PENDING_KEY = "concierge_pending_brief"
COORD_TOLERANCE_DEG = 0.05  # ~5 km: the same geocoding result copied twice, not another place with the same name


def _shown(brief: TripBrief) -> dict:
    """The brief as the user saw it in the summary: names and interests ignore case (interests also order)."""
    shown = brief.model_dump(mode="json")
    for key in ("destination", "origin", "destination_country"):
        shown[key] = (shown[key] or "").casefold() or None
    shown["interests"] = sorted({i.strip().casefold() for i in shown["interests"]})
    return shown


def _same_brief(a: dict | None, b: dict) -> bool:
    if not isinstance(a, dict) or a.keys() != b.keys():
        return False
    for key in b:
        x, y = a[key], b[key]
        if key.endswith(("_lat", "_lon")) and x is not None and y is not None:
            if abs(x - y) > COORD_TOLERANCE_DEG:
                return False
        elif x != y:
            return False
    return True


SAVED_KEY = "concierge_brief_saved_in"  # invocation in which save_trip_brief last returned "ok"


def require_confirmation(tool, args, tool_context):
    """Before-tool callback: save_trip_brief only saves details the user saw and confirmed in a later turn,
    and the planning pipeline only starts in the turn in which the brief was saved.

    The first save_trip_brief call of a turn validates the details (so the user never confirms an impossible
    brief), stores them as pending and answers "needs_confirmation". A call in a later invocation (the user has
    replied) with the same details goes through: every field counts, including coordinates (Paris, France is not
    Paris, Texas), country and interests; omitted fields count as their defaults.
    transfer_to_agent is refused unless save_trip_brief returned "ok" in this invocation: in a live run the
    concierge restarted the pipeline on a follow-up question, and the research agents, given only that
    question, overwrote the research with empty results.
    """
    name = getattr(tool, "name", "")
    if name == "transfer_to_agent":
        if tool_context.state.get(SAVED_KEY) == tool_context.invocation_id:
            return None
        return {"status": "error",
                "reason": "Planning starts only right after save_trip_brief returned ok in this turn. Answer the "
                          "user from the guide, or if there is no guide, say so and ask whether to plan again."}
    if name != "save_trip_brief":
        return None
    try:
        shown = _shown(check_brief(args))
    except ToolError as exc:
        return {"status": "error", "reason": str(exc)}
    pending = tool_context.state.get(PENDING_KEY) or {}
    if pending and pending.get("invocation_id") != tool_context.invocation_id and _same_brief(pending.get("brief"), shown):
        tool_context.state[PENDING_KEY] = None
        return None
    tool_context.state[PENDING_KEY] = {"invocation_id": tool_context.invocation_id, "brief": shown}
    reason = ("Not saved yet. Show the user a short summary of these details and ask them to confirm. "
              "When they confirm in their next message, call save_trip_brief again with the same details.")
    if shown["avoid_modes"] or shown["requirements"]:
        reason += " Include the hard requirements (avoid_modes, requirements) in the summary."
    return {"status": "needs_confirmation", "reason": reason}


_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_DATE_TEXT = (r"\d{4}-\d{2}-\d{2}"                                       # 2026-12-02
              r"|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{4}"   # 2 Dec 2026
              r"|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}")  # December 2, 2026
_DAY_DATE = re.compile(rf"(\bDay\s+(\d{{1,2}})\b[^\n\d]{{0,25}}?)({_DATE_TEXT})", re.IGNORECASE)
# "Outbound (12 Dec 2026)" / "Return (14 Dec 2026)" for ferries on 1 and 3 December (second live Tallinn run)
_LEG_DATE = re.compile(rf"(\b(outbound|return)\b[^\n\d]{{0,25}}?)({_DATE_TEXT})", re.IGNORECASE)


def _parse_date(text: str) -> tuple[date, str] | None:
    """(date, style) of a date as the concierge writes it; style is "iso", "%d %b", "%d %B", "%b %d" or "%B %d"."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text), "iso"
    words = re.findall(r"[A-Za-z]+", re.sub(r"(st|nd|rd|th)\b", "", text))
    nums = [int(n) for n in re.findall(r"\d+", text)]
    if not words or words[0][:3].lower() not in _MONTHS or len(nums) != 2:
        return None
    long_name = len(words[0]) > 3
    try:
        d = date(nums[1], _MONTHS.index(words[0][:3].lower()) + 1, nums[0])
    except ValueError:
        return None
    first_is_day = text.strip()[0].isdigit()
    return d, ("%d %B" if long_name else "%d %b") if first_is_day else ("%B %d" if long_name else "%b %d")


def _date_like(d: date, style: str) -> str:
    if style == "iso":
        return d.isoformat()
    if style.startswith("%d"):
        return f"{d.day} {d.strftime(style[3:])} {d.year}"
    return f"{d.strftime(style[:2])} {d.day}, {d.year}"


def fix_day_dates(callback_context, llm_response):
    """After-model callback: a date written next to "Day N" must be that trip day's date, and an "Outbound" or
    "Return" date outside the trip becomes the trip's first or last day.

    In a live run the concierge answered about "Day 2 (12 Dec 2026)" for a trip on 1-3 December; the guide said
    2026-12-02. Only such day-and-date pairs are corrected; the rest of the answer is left as it is."""
    brief = callback_context.state.get("trip_brief") or {}
    try:
        start, end = date.fromisoformat(brief["start_date"]), date.fromisoformat(brief["end_date"])
    except (KeyError, TypeError, ValueError):
        return None
    changed = False

    def fix(m: re.Match) -> str:
        nonlocal changed
        parsed, n = _parse_date(m.group(3)), int(m.group(2))
        want = start + timedelta(days=n - 1)
        if parsed is None or not 1 <= n <= (end - start).days + 1 or parsed[0] == want:
            return m.group(0)
        changed = True
        return m.group(1) + _date_like(want, parsed[1])

    def fix_leg(m: re.Match) -> str:  # only a date outside the trip is wrong (an overnight trip may arrive later)
        nonlocal changed
        parsed = _parse_date(m.group(3))
        if parsed is None or start <= parsed[0] <= end:
            return m.group(0)
        changed = True
        return m.group(1) + _date_like(start if m.group(2).lower() == "outbound" else end, parsed[1])

    for part in (llm_response.content.parts if llm_response.content else None) or []:
        if part.text and not part.thought:
            part.text = _LEG_DATE.sub(fix_leg, _DAY_DATE.sub(fix, part.text))
    return llm_response if changed else None


PIPELINE_NAME = "planning_pipeline"


def note_saved_brief(tool, args, tool_context, tool_response):
    """After-tool callback: a saved brief starts the planning pipeline (the same action transfer_to_agent sets).

    The transfer is done in code: in a live run the model said "planning will start now" and ended its turn
    without calling transfer_to_agent, so nothing was planned.
    """
    if getattr(tool, "name", "") == "save_trip_brief" and isinstance(tool_response, dict) \
            and tool_response.get("status") == "ok":
        tool_context.state[SAVED_KEY] = tool_context.invocation_id
        tool_context.actions.transfer_to_agent = PIPELINE_NAME
    return None


def build_concierge(planning_pipeline) -> LlmAgent:
    return LlmAgent(
        name="concierge",
        model=MODEL,
        description="Talks with the user, collects and saves the trip brief, then starts the planning pipeline.",
        instruction=concierge_instruction,
        tools=[geocode_place, save_trip_brief],
        sub_agents=[planning_pipeline],
        before_model_callback=[hide_pipeline_events, offer_retry_tool],
        # a tool call with invalid JSON gets another try; "Day N" dates in answers must match the trip
        after_model_callback=[retry_malformed_call, fix_day_dates],
        before_tool_callback=require_confirmation,
        after_tool_callback=note_saved_brief,
        generate_content_config=types.GenerateContentConfig(temperature=0.3),
    )
