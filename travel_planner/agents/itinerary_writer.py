"""itinerary_writer: drafts the day-by-day plan, then revises it after each review -> itinerary.

The review loop runs it before the plan critic in every round, and its task follows from state:
- DRAFT (no plan has been reviewed yet): plan from the research and the weather
  (tools: get_forecast, cluster_by_distance, calculate_trip_cost);
- REVISE (the critic asked for changes): apply the critique, and research a thin area on demand if needed
  (extra_sights_researcher, an agent used as a tool; extra_sights.py).
Both tasks share the role, the inputs, the rules and the output shape, so drafting and revising cannot drift apart.

Guards in code, after every reply:
- `guard_revision`: a revision that is not a valid plan with the right number of days is undone (the last plan is
  kept), and the revision's "changes" list moves to revision_log;
- link grounding (links only from the research) and tidy (travel times, order, tickets, cost).
"""
from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools import BaseTool, FunctionTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.utils.instructions_utils import inject_session_state
from google.genai import types

from ..config import MODEL
from ..tools import calculate_trip_cost, cluster_by_distance, get_forecast
from .extra_sights import budget as extra_sights_budget, extra_sights_tool, merge_extra_sights
from .grounding import ground_itinerary
from .itinerary_tidy import tidy_itinerary
from .limits import limit_tool_calls
from .output import extract_json, fenced_json
from .prompts import BRIEF, REQUIREMENTS, output_format, tool_budget
from .retry import offer_retry_tool, retry_malformed_call
from .review_checks import _parse

MAX_TOOL_CALLS = 5

ROLE = """You are the ITINERARY WRITER of a trip-planning team. You turn the research below into a realistic
day-by-day plan: nearby places on the same day, indoor sights on rainy days, and sights only on days they are open.
After each review by the plan critic, you revise the plan as the critique asks."""

RESEARCH = """RESEARCH RESULTS (JSON written by the research team; an empty value means that part failed):
TRANSPORT SUMMARY: {transport_summary?}

STAY SUMMARY: {stay_summary?}

SIGHTS (culture_findings): {culture_findings?}

FOOD (food_findings): {food_findings?}"""

# ---------------------------------------------------------------- the draft

DRAFT_STEPS = """TASK: write the first plan.
1. get_forecast(destination_lat, destination_lon, start_date, end_date). Each day has a weekday and "rainy".
   If it fails, plan without weather and say so in "notes".
2. Choose the transport: the transport summary's "recommended" outbound and return (picked by code: price plus
   travel time and lost trip time for this budget tier, and only options that suit the travellers). For a
   direction without one: low budget -> "cheapest", mid -> first of "ranked", high -> "fastest". Note the outbound
   arrival time and the return departure time: day 1 starts about 1.5 h after arrival, and the last day ends about
   2.5 h before departure (for a flight; 1 h for a ferry, bus or train).
3. Choose the stay: low budget -> "cheapest", mid -> "best_value" (never a hostel), high -> the best-rated 4-5★
   hotel ("★" in its details) that fits total_budget_eur, else "top_rated" (fall back to any stay in the summary).
4. Pick the sights: 2-4 per full day and 0-2 on arrival/departure days, matching the brief's interests,
   at most 14 in total. Call cluster_by_distance once with places = [{"name", "lat", "lon"}] of those sights
   (skip sights without coordinates and plan them where they fit), n_days = the number of trip days, and
   food_places = [{"name", "lat", "lon"}] of the FOOD results that have coordinates.
5. Give each cluster a date: the smallest clusters go to the arrival and departure days; rainy days get the
   cluster with the most indoor sights. Check every sight's hours against that day's weekday AND the visit
   time: a sight closed that day ("Mo off", "closed Mon") or closing before your visit moves to another
   time or day, or is dropped (say so in "notes").
   Lunch and dinner: take them from that day's food_nearby list, mostly in the brief's budget tier. Use each
   restaurant, cafe or bar at most ONCE in the whole trip; if a day has no unused food place nearby, add an
   item {"kind": "free", "name": "Lunch near <area>"} instead. Use the dishes list for "note" where it fits.
6. calculate_trip_cost(transport_total_eur = chosen outbound price + return price (0 if unknown),
   stay_total_eur = the stay's total_price_eur, activities_total_eur = sum of ticket_eur of the planned
   sights x travellers, days, travellers, budget_tier, total_budget_eur). Leave food and local transport
   out: the tool uses typical costs and lists them as assumptions.
FALLBACK: if cluster_by_distance fails, group the sights yourself by area. If calculate_trip_cost fails,
set "cost" to null and explain in "notes"."""

# ---------------------------------------------------------------- the revision

REVIEW = """CURRENT PLAN (JSON): {itinerary?}

CRITIQUE: {critique?}

CODE CHECK RESULTS (problems with grounded alternatives): {itinerary_check?}"""

REVISE_STEPS = """TASK: the plan critic asked for changes. Fix every MUST FIX problem and as many SHOULD FIX problems as
you can, change nothing else, and reply with the full revised plan.
HOW TO FIX
- Over budget: switch to the cheaper stay or transport option the critique names, copied unchanged from the
  summaries, or remove paid sights from the plan. Never change a ticket price (code resets every ticket to the
  research value). Food and local transport costs are fixed; never change or mention them.
- Closed day or outside the opening hours: move the visit to a time or day it is open (check that day's
  weekday and the hours), or drop it and say so in "notes".
- Place or price not in the research: use the research version (same name, same ticket_eur) or drop it.
- Thin day, empty gap or missing meal: add one of the listed alternatives at a sensible time.
- Packed day or long walk: move the farthest item to another day or drop it.
- Meal far from the day's sights: swap it for a listed nearby alternative (or a "free" item "Lunch near <area>").
- Place used twice: replace the repeat with an unused alternative.
- A day-shape point the critique lists under DECLINED: leave it as it is (the critic decided it suits this trip).
  Fix the "decide" items of the code check that the critique lists under MUST FIX.
- Empty morning: move the day's sights that open early into the morning (the first around 10:00) and put lunch
  after them, or add one of the listed alternatives.
- Two meals close together: keep one, and use the other slot for one of the listed alternatives or drop it.
- Before the travellers' day start or after their day end: move it inside their day hours or drop it.
- Thin day or empty gap with NO listed alternative: you may call extra_sights_researcher once for that day's area
  (area name, lat/lon of a planned item there, count 2-3). Add the sights it returns, with their names, ticket_eur
  and hours unchanged. It is limited to 2 calls per round; never call it when alternatives are listed.
Keep each day's date, weekday and weather. Keep "cost" as it is (code recomputes it from your plan).
Add "changes": a list of short strings, one per change (e.g. "Day 4: MUDE moved 09:30 -> 10:30, opens 10:00").
Update "notes" so that everything in it is still true. Never write cost totals or budget figures in "notes" or
"changes": code recomputes the cost after your reply, so any figure you write could be wrong."""

# ---------------------------------------------------------------- shared by both tasks

RULES = """RULES:
- Use ONLY places, prices, hours and links from the inputs above and your tool results. Never add a place
  from memory. Copy each item's source_url character for character from the input's url / source_urls
  (keep "http://" as it is); null if the input had none.
- ticket_eur: copy it from the input; null means unknown (never turn it into 0).
- Day 1 starts with a "transport" item for the arrival ("Arrive at ..."); the last day ends with a "transport"
  item for the departure ("Depart from ..."). Nothing is planned before the arrival or after the departure.
- Copy the chosen transport options and the chosen stay unchanged from the summaries.
- Times are local, "HH:MM". Keep days realistic: a museum takes 1.5-2.5 h, a viewpoint 30 min; leave time
  to walk between places.
- Day hours: the brief's day_start and day_end (09:30 and 22:00 if the brief has none). Full days (every day
  except the arrival and departure days) start at day_start with a sight that is open then; lunch comes after
  the morning's sights. Never start a full day with lunch. Nothing is planned before day_start or after
  day_end; on the arrival day, start at the later of arrival + 1.5 h and day_start.
- Pace: the brief's pace ("balanced" if it has none). relaxed = 1-3 sights per full day with long breaks;
  balanced = 2-4 sights; packed = 4-6 sights with short breaks (at most 20 in total).
- One restaurant per meal: lunch and dinner at least 3 hours apart, and no second restaurant right after a meal."""

PLAN_SIZE = "{trip_plan_size?}"  # a long trip may plan more than 14 sights; "" for an ordinary trip

SHAPE = """{"transport": {"outbound": TransportOption|null, "return": TransportOption|null, "why": str},
 "stay": StayOption|null, "stay_why": str,
 "weather_kind": str, "days": [Day, ...], "cost": <the calculate_trip_cost result object>|null, "notes": "..."}
Day = {"day": int, "date": "YYYY-MM-DD", "weekday": str, "weather": str (e.g. "light rain, 14-19 °C"), "rainy": bool,
       "area": str, "walk_km": number|null, "items": [Item, ...]}
Item = {"time": "HH:MM", "kind": "sight"|"restaurant"|"cafe"|"bar"|"transport"|"free", "name": str,
        "note": str (why / what to see or order, 1 sentence), "ticket_eur": number|null, "hours": str|null,
        "lat": number|null, "lon": number|null, "source_url": str|null}
TransportOption / StayOption = copied unchanged from the summaries."""

REVISED_SHAPE = SHAPE.replace('"notes": "..."}', '"notes": "...", "changes": [str, ...]}')

EXAMPLE = """{"transport": {"outbound": {"mode": "flight", "operator": "SAS", "...": "copied option"},
   "return": {"mode": "flight", "operator": "Finnair", "...": "copied option"}, "why": "The recommended pair: cheap and keeps both trip days."},
 "stay": {"kind": "hotel", "name": "The Central House", "...": "copied stay"}, "stay_why": "Best value in Baixa, near most sights.",
 "weather_kind": "typical (observed on the same dates in an earlier year, not a forecast)",
 "days": [{"day": 1, "date": "2027-05-10", "weekday": "Mon", "weather": "mainly clear, 15-22 °C", "rainy": false,
   "area": "Baixa and Chiado", "walk_km": 2.1, "items": [
   {"time": "16:10", "kind": "transport", "name": "Arrive at Lisbon airport (SAS)", "note": "Metro red line to the centre.",
    "ticket_eur": null, "hours": null, "lat": null, "lon": null, "source_url": null},
   {"time": "18:00", "kind": "sight", "name": "Santa Justa Lift", "note": "Iron lift with views over Baixa.",
    "ticket_eur": 6.0, "hours": "Mo-Su 07:30-23:00", "lat": 38.71194, "lon": -9.13917, "source_url": "https://en.wikipedia.org/wiki/Santa_Justa_Lift"}]}],
 "cost": {"categories_eur": {"transport": 684.0, "...": 0}, "total_eur": 1650.0, "...": "tool result"},
 "notes": "Weather is typical, not a forecast. Belém Tower is closed on Mondays, so it is on day 2."}"""

DRAFT = "\n\n".join([ROLE, BRIEF, RESEARCH, DRAFT_STEPS, tool_budget(MAX_TOOL_CALLS), RULES,
                     output_format(SHAPE, EXAMPLE)]) + REQUIREMENTS + PLAN_SIZE
REVISION = "\n\n".join([ROLE, BRIEF, REVIEW, RESEARCH, REVISE_STEPS, RULES,
                        output_format(REVISED_SHAPE, EXAMPLE)]) + REQUIREMENTS + PLAN_SIZE


def revising(state) -> bool:
    """True once the critic has reviewed a plan; before that, or when the draft was no plan at all, it drafts."""
    return bool(state.get("critique")) and bool(_parse(state.get("itinerary")).get("days"))


async def writer_instruction(ctx: ReadonlyContext) -> str:
    return await inject_session_state(REVISION if revising(ctx.state) else DRAFT, ctx)


class WriterTools(BaseToolset):
    """The tools of the writer's current task, so each task sees only the tools its prompt names (with all of them, a
    draft researched more sights before the critic had even seen the plan)."""

    def __init__(self):
        super().__init__()
        self.by_task = {"draft": [FunctionTool(get_forecast), FunctionTool(cluster_by_distance),
                                  FunctionTool(calculate_trip_cost)],
                        "revise": [extra_sights_tool()]}

    async def get_tools(self, readonly_context: ReadonlyContext | None = None) -> list[BaseTool]:
        return self.by_task["revise" if readonly_context and revising(readonly_context.state) else "draft"]


def snapshot_plan(callback_context):
    """Before-agent callback: the plan a revision starts from, for guard_revision (None for a draft)."""
    state = callback_context.state
    state["itinerary_before_revision"] = state.get("itinerary") if revising(state) else None
    return None


def _valid_plan(text, days: int) -> bool:
    try:
        plan = extract_json(text or "")
    except ValueError:
        return False
    return isinstance(plan.get("days"), list) and (not days or len(plan["days"]) == days)


def guard_revision(callback_context):
    """After-agent callback (before grounding and tidy): a revision must be a valid plan with the right number of
    days, or the last plan is kept. A draft has nothing to fall back to: the critic reports a broken draft."""
    state = callback_context.state
    before = state.get("itinerary_before_revision")
    if not before:
        return None
    entry = {"round": state.get("review_round")}
    if not _valid_plan(state.get("itinerary"), int((state.get("trip_brief") or {}).get("days") or 0)):
        state["itinerary"] = before
        entry["restored"] = "reply was not a valid plan with the right number of days"
    else:
        plan = extract_json(state["itinerary"])
        entry["changes"] = plan.pop("changes", None)  # logged here; the guide writer only needs the plan
        state["itinerary"] = fenced_json(plan)
    state["revision_log"] = [*(state.get("revision_log") or []), entry]
    return None


def build_itinerary_writer(model=None) -> LlmAgent:
    return LlmAgent(
        name="itinerary_writer",
        model=model or MODEL,
        description="Drafts the day-by-day plan from the research and the weather, then revises it after each review.",
        instruction=writer_instruction,
        tools=[WriterTools()],
        output_key="itinerary",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        before_agent_callback=snapshot_plan,
        before_tool_callback=[limit_tool_calls(MAX_TOOL_CALLS), extra_sights_budget],
        after_tool_callback=merge_extra_sights,
        # restore mangled links / drop links no tool returned, then sort items and add ticket texts
        after_agent_callback=[guard_revision, ground_itinerary, tidy_itinerary],
        before_model_callback=offer_retry_tool,  # retry when a tool call's arguments are not valid JSON
        after_model_callback=retry_malformed_call,
    )
