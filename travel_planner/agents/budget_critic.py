"""budget_critic: reviews the itinerary inside the review loop -> critique (ends with VERDICT: APPROVED|REVISE).

The critic calls `check_itinerary` (code checks of budget, hours, grounding and pacing) and may call
`calculate_trip_cost` to test a cheaper option before recommending it. It adds what code cannot judge
(interests, rainy days, order) and writes concrete fix instructions for the reviser.

`enforce_verdict` (after-agent callback) re-runs the code checks: if any must-fix problem remains, the verdict
becomes REVISE whatever the model wrote, and missed problems are appended to the critique. Without must-fix
problems, another round is allowed only when it can still help (see `worth_revising`).
"""
import re

from google.adk.agents import LlmAgent
from google.genai import types

from ..config import MODEL
from ..tools import calculate_trip_cost
from .limits import limit_tool_calls
from .prompts import BRIEF, REQUIREMENTS, tool_budget
from .review_checks import check_itinerary, check_state
from .retry import offer_retry_tool, retry_malformed_call

MAX_ROUNDS = 3
VERDICT_RE = re.compile(r"VERDICT\W*(APPROVED|REVISE)", re.I)
BARE_VERDICT_RE = re.compile(r"\b(APPROVED|REVISE)\b")

ROLE = """You are the BUDGET CRITIC of a trip-planning team. You review the day-by-day plan before it becomes the
trip guide: budget, opening hours, pacing, walking distances, and whether every place and price comes from the
research. You do not rewrite the plan: you tell the reviser exactly what to change."""

INPUTS = """REVIEW ROUND: {review_round?} of 3

PLAN TO REVIEW (JSON; empty if the planner failed): {itinerary?}

TRANSPORT SUMMARY: {transport_summary?}

STAY SUMMARY: {stay_summary?}"""

STEPS = """STEPS
1. Call check_itinerary() once (no arguments). It checks the plan in code: the budget (cost recomputed from the
   plan), visit times against opening hours, places and prices missing from the research, transport and stay
   against the summaries, the day count, thin or packed days, long walks, meals far from the day's sights and
   repeated places. Problems come as "must_fix" and "should_fix"; thin days and far meals list "alternatives"
   (unused researched places nearby).
2. If the plan is over budget, call calculate_trip_cost to test the fix you suggest (e.g. the cheapest stay
   or transport option from the summaries, keeping the other figures) and give the new total. Never pass
   food_per_person_per_day_eur or local_transport_per_person_per_day_eur: food and local transport are fixed
   typical costs, not something the plan can save on. Only the options check_itinerary lists are real savings.
3. Read the plan yourself for what code cannot judge: do the sights match the brief's interests, are rainy
   days mostly indoor, is the order within each day sensible, are the notes still true."""

RULES = """RULES
- Every problem in check_itinerary's "must_fix" goes under MUST FIX. Never drop or soften one.
- Base every fix on the inputs and the check results (listed alternatives, options in the summaries). Never
  suggest a place, price or opening time from memory.
- Be specific: day, time, item and the exact change ("move to 10:30", "replace with <alternative>, 0.4 km").
- A should-fix problem with no alternative and no simple fix (e.g. a thin day with nothing unused nearby) can be
  left: say so in one line instead of asking for a change.
- VERDICT: REVISE if MUST FIX is not empty. In rounds 1 and 2, also REVISE for SHOULD FIX problems the reviser can
  fix with the listed alternatives or by moving items. Otherwise APPROVED. In round 3, APPROVED unless MUST FIX."""

OUTPUT = """OUTPUT: plain text in exactly this layout (no JSON, nothing after the verdict line):
MUST FIX:
1. Day 4 09:30 MUDE - Museu do Design: opens at 10:00 on Thursdays -> move it to 10:30.
SHOULD FIX:
1. Day 3: nothing planned 14:30-19:30 -> add Museu do Dinheiro (0.6 km, We-Sa 10:00-18:00, free) at 15:00.
KEEP: one sentence on what works and must not change.
VERDICT: REVISE
(Write "none" under a heading with no problems.)"""


def start_round(callback_context):
    """Before-agent callback: count review rounds (shown to the critic)."""
    state = callback_context.state
    state["review_round"] = int(state.get("review_round") or 0) + 1
    return None


def verdict_of(text: str) -> str | None:
    found = VERDICT_RE.findall(text or "") or BARE_VERDICT_RE.findall((text or "")[-200:])
    return found[-1].upper() if found else None


FIXABLE = ("repeated_place", "packed_day", "long_walk")  # fixable by moving or dropping items, no new place needed


def worth_revising(should: list[dict], previous: dict | None, round_no: int) -> str | None:
    """Why another round for should-fix problems only is worth it, or None.

    Another round needs a fix grounded in the research (listed alternatives, or moving/dropping items) and,
    after round 1, progress since the last round; never in the last round. Without this the live loop went back
    and forth (adding, removing and re-adding the same museum) until the round limit.
    """
    if round_no >= MAX_ROUNDS:
        return None
    if not any(x.get("alternatives") or x["issue"] in FIXABLE for x in should):
        return None
    if previous and len(should) >= previous.get("should_fix", 0):
        return None
    return "fixable"


def enforce_verdict(callback_context):
    """After-agent callback: code has the last word on the verdict.

    - any must-fix problem -> REVISE (problems the critique missed are appended);
    - only should-fix problems -> the critic's REVISE stands only if `worth_revising`, otherwise APPROVED;
    - the verdict line always ends the critique.
    """
    state = callback_context.state
    text = str(state.get("critique") or "").strip()
    said = verdict_of(text)
    report = check_state(state)
    must, should = report["must_fix"], report["should_fix"]
    log = list(state.get("review_log") or [])
    round_no = int(state.get("review_round") or len(log) + 1)
    body = "\n".join(ln for ln in text.splitlines() if not VERDICT_RE.search(ln)).rstrip()
    if must:
        verdict = "REVISE"
        if said != "REVISE":
            body += "\nMUST FIX (found by the code check and missing above):\n" + "\n".join(
                f"- {'Day ' + str(x['day']) + ': ' if x.get('day') else ''}{x['detail']}" for x in must)
    elif said == "REVISE" and worth_revising(should, log[-1] if log else None, round_no):
        verdict = "REVISE"
    else:
        verdict = "APPROVED"
        if said == "REVISE":  # keep the critic's text in the log; the reviser only needs to see "approved"
            state["critique_overridden"] = body
            body = ("Approved by the code check: no must-fix problems, and the remaining points have no "
                    "researched fix, made no progress since the last round, or this is the last round. "
                    "Nothing to change: call exit_loop.")
    state["critique"] = f"{body}\nVERDICT: {verdict}".strip()
    state["review_verdict"] = verdict
    log.append({"round": round_no, "verdict": verdict, "critic_said": said, "overridden": said != verdict,
                "must_fix": len(must), "should_fix": len(should), "total_eur": report["facts"].get("total_eur")})
    state["review_log"] = log
    return None


def build_budget_critic(model=None) -> LlmAgent:
    instruction = "\n\n".join([ROLE, BRIEF, INPUTS, STEPS, tool_budget(3), RULES, OUTPUT]) + REQUIREMENTS
    return LlmAgent(
        name="budget_critic",
        model=model or MODEL,
        description="Reviews the itinerary (budget, hours, grounding, pacing) and says APPROVED or REVISE.",
        instruction=instruction,
        tools=[check_itinerary, calculate_trip_cost],
        output_key="critique",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        before_agent_callback=start_round,
        after_agent_callback=enforce_verdict,
        before_tool_callback=limit_tool_calls(3),
        before_model_callback=offer_retry_tool,
        after_model_callback=retry_malformed_call,
    )
