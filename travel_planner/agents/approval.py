"""Human in the loop before the guide (REVIEW.md A6).

The planning pipeline stops after the review loop: `present_plan` shows the reviewed days in the chat and marks the
plan as waiting for approval. The user then answers the concierge, which decides what the answer means:
- approval ("looks good, write the guide") -> `approve_plan`, which starts the guide writer;
- a change -> `request_replan` (replan.py), so a change at this stage reuses the research it does not affect;
- a question -> an answer from the plan.
Code only checks the fact that a reviewed plan is waiting (`approve_plan` refuses otherwise) and does the handover.
"""
from datetime import date

from google.adk.tools import ToolContext
from google.genai import types

from ..tools._result import error, ok, safe_tool
from .review_checks import _parse

STATUS_KEY = "plan_status"        # "awaiting_approval" -> "approved"
SUMMARY_KEY = "plan_summary"      # the reviewed plan as shown to the user (also in the concierge's instruction)
GUIDE_WRITER = "guide_writer"
APPROVE_HINT = ("Reply \"looks good\" and I will write the full trip guide, or tell me what to change (e.g. a "
                "cheaper hotel, more museums, a slower pace, start the days earlier).")


def _clock(iso) -> str:
    text = str(iso or "")
    return text[11:16] if len(text) >= 16 else text


def _leg(name: str, leg) -> str | None:
    """One transport line. A leg arriving on a later day says so ("+1 day") with its duration: a live summary showed
    "FlixBus 14:38-19:09" for a 28-hour bus."""
    if not isinstance(leg, dict):
        return None
    what = " ".join(str(x) for x in (leg.get("operator"), leg.get("mode")) if x)
    when = f"{str(leg.get('depart') or '')[:10]} {_clock(leg.get('depart'))}-{_clock(leg.get('arrive'))}".strip()
    try:
        later = (date.fromisoformat(str(leg.get("arrive"))[:10]) - date.fromisoformat(str(leg.get("depart"))[:10])).days
    except ValueError:
        later = 0
    if later > 0:
        minutes = int(leg.get("duration_min") or 0)
        when += f" (+{later} day{'s' if later > 1 else ''}" + (f", {minutes // 60} h {minutes % 60:02d} min" if minutes else "") + ")"
    price = f", {leg['price_eur']:.0f} EUR" if isinstance(leg.get("price_eur"), (int, float)) else ""
    return f"- **{name}:** {what} {when}{price}".rstrip()


def plan_summary(plan: dict, brief: dict) -> str:
    """The reviewed plan in a few lines of Markdown: transport, stay, one line per day, the cost."""
    lines = [f"**Your reviewed plan for {brief.get('destination', 'the trip')} "
             f"({brief.get('start_date', '')} to {brief.get('end_date', '')})**", ""]
    transport = plan.get("transport") or {}
    if brief.get("own_car"):
        lines.append("- **Getting there:** by your own car" + (" (and the car ferry below)" if any(
            isinstance(transport.get(leg), dict) for leg in ("outbound", "return")) else ""))
    lines += [x for x in (_leg("Outbound", transport.get("outbound")), _leg("Return", transport.get("return"))) if x]
    stay = plan.get("stay")
    if brief.get("own_accommodation"):
        lines.append("- **Stay:** your own place (no accommodation needed)")
    elif brief.get("nights") == 0:
        lines.append("- **Stay:** none, a day trip (back the same evening)")
    elif isinstance(stay, dict) and stay.get("name"):
        total = stay.get("total_price_eur")
        lines.append(f"- **Stay:** {stay['name']}" + (f" ({stay['area']})" if stay.get("area") else "")
                     + (f", {total:.0f} EUR in total" if isinstance(total, (int, float)) else ""))
    lines.append("")
    for day in plan.get("days") or []:
        items = [f"{i.get('time', '')} {i.get('name', '')}".strip() for i in day.get("items") or [] if isinstance(i, dict)]
        lines.append(f"- **Day {day.get('day', '?')} ({day.get('weekday', '')} {day.get('date', '')}):** "
                     + " · ".join(items))
    cost = plan.get("cost") or {}
    if isinstance(cost.get("total_eur"), (int, float)):
        budget = brief.get("total_budget_eur")
        lines += ["", f"Estimated total: {cost['total_eur']:.0f} EUR"
                  + (f" (budget {budget:.0f} EUR)" if isinstance(budget, (int, float)) else "") + "."]
    return "\n".join(lines)


def unchanged_stay_note(plan: dict, state) -> str | None:
    """When the user asked to change the stay but the new plan has the same one, say why (a live "cheaper hotel"
    re-plan returned the same hostel without a word: it was already the cheapest the research found)."""
    from .replan import NOTE_KEY, STAY_WORDS
    from .review_checks import _price, asked_cheaper_stay

    before = (state.get("previous_plan_choices") or {}).get("stay")
    stay = plan.get("stay")
    if not (isinstance(before, dict) and isinstance(stay, dict) and STAY_WORDS.search(str(state.get(NOTE_KEY) or ""))):
        return None
    if (before.get("name"), before.get("total_price_eur")) != (stay.get("name"), stay.get("total_price_eur")):
        old, new = _price(before.get("total_price_eur")), _price(stay.get("total_price_eur"))
        if asked_cheaper_stay(state) and old is not None and new is not None and new >= old:
            return (f"Note: the search found no suitable stay cheaper than the previous {before.get('name')} "
                    f"(€{old:.0f}); the new plan has {stay['name']} (€{new:.0f}). Say so if you prefer to keep the "
                    "previous one.")
        return None
    cheapest = (_parse(state.get("stay_summary")).get("cheapest") or {})
    if stay.get("name") == cheapest.get("name") and _price(stay.get("total_price_eur")) == _price(cheapest.get("total_price_eur")):
        return (f"Note: the stay is still {stay['name']}: it is already the cheapest stay the search found for these "
                "dates.")
    return f"Note: the stay is still {stay['name']}: no other stay in the search suited this trip better."


def present_plan(callback_context):
    """After-agent callback of the planning pipeline: show the reviewed plan and wait for the user's answer."""
    state = callback_context.state
    plan = _parse(state.get("itinerary"))
    if not plan.get("days"):
        state[STATUS_KEY] = None
        state[SUMMARY_KEY] = None
        return types.Content(role="model", parts=[types.Part(
            text="Sorry, planning did not produce a usable day-by-day plan. Shall I try again?")])
    summary = plan_summary(plan, state.get("trip_brief") or {})
    unchanged = unchanged_stay_note(plan, state)
    if unchanged:
        summary += "\n\n" + unchanged
    state[STATUS_KEY] = "awaiting_approval"
    state[SUMMARY_KEY] = summary
    return types.Content(role="model", parts=[types.Part(text=f"{summary}\n\n{APPROVE_HINT}")])


@safe_tool
def approve_plan(tool_context: ToolContext) -> dict:
    """Starts writing the trip guide for the reviewed plan. Call it only when the user has approved the plan
    shown to them (e.g. "looks good", "yes, write the guide"), not when their "yes" answers your offer of a change."""
    state = tool_context.state
    if state.get(STATUS_KEY) != "awaiting_approval":
        return error("No reviewed plan is waiting for approval: answer the user (a guide may already exist, or "
                     "planning has not run yet).")
    state[STATUS_KEY] = "approved"
    tool_context.actions.transfer_to_agent = GUIDE_WRITER
    return ok([{"approved": True}], next_step="The guide writer takes over now. Do not write anything else.")


# In a recording the concierge answered "looks good" with "the revised itinerary will be ready shortly": it took the
# finished plan for one still being made (the pipeline's messages are hidden from its prompt).
AWAITING = """A REVIEWED PLAN IS WAITING FOR THE USER'S APPROVAL
The planning team has FINISHED. Nothing is running now, and the user has already seen this plan in the chat.
There is no guide yet; that is not a failure.
{summary}
- If the user approves it ("looks good", "go ahead", "write the guide"), call approve_plan right away, before
  writing anything. But if YOUR last message offered or proposed a change ("shall I look for a cheaper hotel?"),
  a "yes" accepts that change: handle it with request_replan, not approve_plan.
- If they want a change, handle it as described under CHANGES TO A FINISHED PLAN (request_replan).
- If they ask about the plan, answer only from it."""


def awaiting_block(state) -> str | None:
    if state.get(STATUS_KEY) != "awaiting_approval" or not state.get(SUMMARY_KEY):
        return None
    return AWAITING.replace("{summary}", state[SUMMARY_KEY])
