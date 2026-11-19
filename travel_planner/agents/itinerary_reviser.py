"""itinerary_reviser: second agent of the review loop -> itinerary (rewritten), or exit_loop when approved.

Guards in code, because the loop must not lose a good plan or stop with a bad one:
- `snapshot_plan` keeps the plan as it was before this agent ran;
- `gate_exit_loop` refuses exit_loop while the verdict is REVISE;
- `guard_revision` restores the snapshot when the verdict was APPROVED (the plan must not change) or when the
  reply is not a valid plan with the right number of days. If the model "approves" in text but forgets
  exit_loop, it ends the loop itself (escalate);
- `merge_extra_sights` adds sights the reviser researched on demand to culture_findings (extra_sights.py);
- then the planner's callbacks run again: link grounding and tidy (travel times, order, tickets, cost);
- `keep_best_plan` scores every revision with the code checks. The last round's revision is never reviewed
  again, so if it scores worse than an earlier plan, the best earlier plan is kept (a live run's last
  revision added three places that were not in the research).
"""
import json

from google.adk.agents import LlmAgent
from google.adk.tools import exit_loop
from google.genai import types

from ..config import MODEL
from .plan_critic import MAX_ROUNDS
from .extra_sights import budget as extra_sights_budget, extra_sights_tool, merge_extra_sights
from .grounding import ground_itinerary
from .itinerary_tidy import tidy_itinerary
from .output import extract_json
from .review_checks import _parse, check_plan
from .planner import EXAMPLE, INPUTS as RESEARCH_INPUTS, SHAPE, plan_rules
from .prompts import BRIEF, REQUIREMENTS, output_format
from .retry import offer_retry_tool, retry_malformed_call

ROLE = """You are the ITINERARY REVISER of a trip-planning team. The plan critic has reviewed the plan; you
apply the review."""

VERDICT = """VERDICT OF THE REVIEW: {review_verdict?}
- If the verdict is APPROVED: call exit_loop and do nothing else. Write no text and no plan.
- If the verdict is REVISE: fix every MUST FIX problem and as many SHOULD FIX problems as you can, change
  nothing else, and reply with the full revised plan. Do not call exit_loop."""

INPUTS = """CURRENT PLAN (JSON; empty if the planner failed): {itinerary?}

CRITIQUE: {critique?}

CODE CHECK RESULTS (problems with grounded alternatives): {itinerary_check?}"""

HOW = """HOW TO FIX
- No current plan (the planner failed): build the whole plan from the research, following the rules below.
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
Keep each day's date, weekday and weather. Keep "cost" as it is (code recomputes it from your plan).
Add "changes": a list of short strings, one per change (e.g. "Day 4: MUDE moved 09:30 -> 10:30, opens 10:00").
Update "notes" so that everything in it is still true. Never write cost totals or budget figures in "notes" or
"changes": code recomputes the cost after your reply, so any figure you write could be wrong."""

# How to fix the day-shape checks (review_checks._day_shape_issues)
HOW_DAY_SHAPE = """
- Empty morning: move the day's sights that open early into the morning (the first around 10:00) and put lunch
  after them, or add one of the listed alternatives.
- Two meals close together: keep one, and use the other slot for one of the listed alternatives or drop it.
- Before the travellers' day start or after their day end: move it inside their day hours or drop it."""


# Research on demand (extra_sights.py): the reviser decides when a thin day needs more research
HOW_EXTRA = """
- Thin day or empty gap with NO listed alternative: you may call extra_sights_researcher once for that day's area
  (area name, lat/lon of a planned item there, count 2-3). Add the sights it returns, with their names, ticket_eur
  and hours unchanged. It is limited to 2 calls per round; never call it when alternatives are listed."""


def how_to_fix() -> str:
    """HOW, plus how to fix the day-shape checks and when to research on demand (inserted after the list of
    fixes)."""
    return HOW.replace("\nKeep each day's date", HOW_DAY_SHAPE + HOW_EXTRA + "\nKeep each day's date", 1)


REVISED_SHAPE = SHAPE.replace('"notes": "..."}', '"notes": "...", "changes": [str, ...]}')


def snapshot_plan(callback_context):
    state = callback_context.state
    state["itinerary_before_revision"] = state.get("itinerary")
    state["review_exit_called"] = False
    return None


def gate_exit_loop(tool, args, tool_context):
    """Before-tool callback: exit_loop only ends the loop when the critic approved."""
    if getattr(tool, "name", "") != "exit_loop":
        return None
    if tool_context.state.get("review_verdict") != "APPROVED":
        return {"status": "error",
                "reason": "The verdict is REVISE: do not exit. Fix the problems and reply with the revised plan JSON."}
    tool_context.state["review_exit_called"] = True
    return None


def _valid_plan(text, days: int) -> bool:
    try:
        plan = extract_json(text or "")
    except ValueError:
        return False
    return isinstance(plan.get("days"), list) and (not days or len(plan["days"]) == days)


def guard_revision(callback_context):
    """After-agent callback (runs before grounding and tidy)."""
    state = callback_context.state
    before = state.get("itinerary_before_revision")
    log = list(state.get("revision_log") or [])
    entry = {"round": state.get("review_round"), "verdict": state.get("review_verdict")}
    if state.get("review_verdict") == "APPROVED":
        if state.get("itinerary") != before:
            state["itinerary"] = before
            entry["restored"] = "plan changed although approved"
        if not state.get("review_exit_called"):
            callback_context.actions.escalate = True  # the model forgot exit_loop: end the loop in code
            entry["exit"] = "forced by code"
        else:
            entry["exit"] = "exit_loop"
    elif not _valid_plan(state.get("itinerary"), int((state.get("trip_brief") or {}).get("days") or 0)):
        state["itinerary"] = before
        entry["restored"] = "reply was not a valid plan with the right number of days"
    else:
        plan = extract_json(state["itinerary"])
        entry["changes"] = plan.pop("changes", None)  # logged here; the guide writer only needs the plan
        state["itinerary"] = "```json\n" + json.dumps(plan, ensure_ascii=False, indent=1) + "\n```"
    log.append(entry)
    state["revision_log"] = log
    return None


def _score(itinerary, state) -> list[int]:
    """[must-fix, other open problems]; day-shape findings the critic declined do not count."""
    report = check_plan(_parse(itinerary), state)
    declined = {(d.get("day"), d.get("issue")) for d in state.get("taste_declined") or []}
    taste = [x for x in report.get("decide") or [] if (x.get("day"), x["issue"]) not in declined]
    return [len(report["must_fix"]), len(taste) + len(report["should_fix"])]


def keep_best_plan(callback_context):
    """After-agent callback (runs last): remember the best-scoring plan; after the final round, use it."""
    state = callback_context.state
    if state.get("review_verdict") == "APPROVED":
        return None
    best = state.get("best_itinerary") or {"itinerary": state.get("itinerary_before_revision"), "round": 0,
                                            "score": _score(state.get("itinerary_before_revision"), state)}
    score = _score(state.get("itinerary"), state)
    round_no = int(state.get("review_round") or 0)
    if score <= best["score"]:
        best = {"itinerary": state.get("itinerary"), "round": round_no, "score": score}
    state["best_itinerary"] = best
    if round_no >= MAX_ROUNDS and best["score"] < score:
        state["itinerary"] = best["itinerary"]
        log = list(state.get("revision_log") or [])
        if log:
            log[-1]["restored"] = (f"last revision scored {score} (must, should); kept the plan from round "
                                   f"{best['round']} with {best['score']}")
        state["revision_log"] = log
    return None


def build_itinerary_reviser(model=None) -> LlmAgent:
    instruction = "\n\n".join([ROLE, VERDICT, BRIEF, INPUTS, RESEARCH_INPUTS, how_to_fix(), plan_rules(),
                               output_format(REVISED_SHAPE, EXAMPLE)]) + REQUIREMENTS
    return LlmAgent(
        name="itinerary_reviser",
        model=model or MODEL,
        description="Applies the critic's review to the itinerary, or ends the review loop when it is approved.",
        instruction=instruction,
        tools=[exit_loop, extra_sights_tool()],
        output_key="itinerary",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        before_agent_callback=snapshot_plan,
        before_tool_callback=[gate_exit_loop, extra_sights_budget],
        after_tool_callback=merge_extra_sights,
        after_agent_callback=[guard_revision, ground_itinerary, tidy_itinerary, keep_best_plan],
        before_model_callback=offer_retry_tool,
        after_model_callback=retry_malformed_call,
    )
