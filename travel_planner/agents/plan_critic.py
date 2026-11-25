"""plan_critic: reviews the itinerary inside the review loop -> critique (ends with VERDICT: APPROVED|REVISE).

The critic calls `check_itinerary` (code checks of budget, hours, grounding and pacing) and may call
`calculate_trip_cost` to test a cheaper option before recommending it. It adds what code cannot judge
(interests, rainy days, order) and writes concrete fix instructions for the itinerary writer.

`enforce_verdict` (after-agent callback) re-runs the code checks: if any must-fix problem (a fact) remains, the
verdict becomes REVISE whatever the model wrote, and missed problems are appended to the critique. Day-shape
findings (a matter of taste, the "decide" list) are the critic's: it fixes or declines each one, and code only
checks that each was decided with a reason about this trip (silence or a stock phrase counts as "fix it"), keeps
the obvious cases must-fix, and logs every decline. Without open problems, another round is allowed only when it
can still help (see `worth_revising`). An APPROVED verdict ends the review loop, and every round's plan is scored
so that the loop can keep the best one (`remember_best_plan`, review_loop.finish_review).

Ablation (scripts/evaluate.py): CRITIC_MODE=code_only replaces the critic's LLM call with a critique written from
the code check alone (`code_only_critique`), so the same trips can be compared with and without the critic agent.
"""
import os
import re

from google.adk.agents import LlmAgent
from google.genai import types

from ..config import MODEL
from ..tools import calculate_trip_cost
from .limits import limit_tool_calls
from .prompts import BRIEF, REQUIREMENTS, tool_budget
from .review_checks import TASTE_ISSUES, check_itinerary, check_state
from .retry import offer_retry_tool, retry_malformed_call

MAX_ROUNDS = 4  # reviews: the draft and up to three revisions, each of them reviewed
VERDICT_RE = re.compile(r"VERDICT\W*(APPROVED|REVISE)", re.I)
BARE_VERDICT_RE = re.compile(r"\b(APPROVED|REVISE)\b")

ROLE = """You are the PLAN CRITIC of a trip-planning team. You review the day-by-day plan before it becomes the
trip guide: budget, opening hours, pacing, walking distances, and whether every place and price comes from the
research. You do not rewrite the plan: you tell the itinerary writer exactly what to change."""

INPUTS = """REVIEW ROUND: {review_round?} of MAX_ROUNDS

PLAN TO REVIEW (JSON; empty if the writer failed): {itinerary?}

TRANSPORT SUMMARY: {transport_summary?}

STAY SUMMARY: {stay_summary?}""".replace("MAX_ROUNDS", str(MAX_ROUNDS))

STEPS = """STEPS
1. Call check_itinerary() once (no arguments). It checks the plan in code: the budget (cost recomputed from the
   plan), visit times against opening hours, places and prices missing from the research, transport and stay
   against the summaries, the day count, thin or packed days, long walks, meals far from the day's sights and
   repeated places. Problems come as "must_fix", "decide" (day-shape findings you decide, see below) and
   "should_fix"; most list "alternatives" (unused researched places nearby).
2. If the plan is over budget, call calculate_trip_cost to test the fix you suggest (e.g. the cheapest stay
   or transport option from the summaries, keeping the other figures) and give the new total. Never pass
   food_per_person_per_day_eur or local_transport_per_person_per_day_eur: food and local transport are fixed
   typical costs, not something the plan can save on. Only the options check_itinerary lists are real savings.
3. Read the plan yourself for what code cannot judge: do the sights match the brief's interests, are rainy
   days mostly indoor, is the order within each day sensible, are the notes still true."""

# REVIEW.md A4: the day-shape findings are matters of taste, so the critic decides them; code only makes sure each
# one is decided (enforce_verdict) and keeps the obvious cases in must_fix.
DAY_SHAPE = """DAY-SHAPE DECISIONS (check_itinerary's "decide" list: these are yours)
Each item is a day-shape finding that has a researched fix: an empty morning on a full day, two meals close
together, a "Lunch near ..." placeholder while a researched food place is nearby, or a whole empty afternoon.
Travellers expect full days that start with a sight, lunch after the morning's sights, one restaurant per meal
and something to do in the afternoon. So normally you put the item under MUST FIX with the concrete change (one of
its alternatives, or moving a planned item). Decline it only for a reason that is specific to THIS trip and THIS
day, written under DECLINED, for example:
- the brief's pace is "relaxed" and the day already has its sights and long breaks are what the travellers want;
- the free time is needed: an arrival or departure, a long visit just before or after, a late opening time;
- it rains that day and the alternatives are outdoor;
- the alternatives do not match the travellers' interests or budget.
Not a reason: "it is fine", "minor", "acceptable", "the plan is good overall", anything true of every trip.
Write every "decide" item either under MUST FIX or under DECLINED as "Day <n> <issue>: <reason>", with the issue
name exactly as check_itinerary gives it. An item you leave out counts as MUST FIX. Items under "must_fix" with an
"obvious" field are not yours to decline."""

RULES = """RULES
- Every problem in check_itinerary's "must_fix" goes under MUST FIX. Never drop or soften one.
- Every problem in "decide" goes under MUST FIX or DECLINED (see DAY-SHAPE DECISIONS).
- Base every fix on the inputs and the check results (listed alternatives, options in the summaries). Never
  suggest a place, price or opening time from memory.
- Be specific: day, time, item and the exact change ("move to 10:30", "replace with <alternative>, 0.4 km").
- A should-fix problem with no alternative and no simple fix (e.g. a thin day with nothing unused nearby) can be
  left: say so in one line instead of asking for a change.
- VERDICT: REVISE if MUST FIX is not empty. Before the last round, also REVISE for SHOULD FIX problems the writer
  can fix with the listed alternatives or by moving items. Otherwise APPROVED. In the last round, APPROVED unless
  MUST FIX."""

OUTPUT = """OUTPUT: plain text in exactly this layout (no JSON, nothing after the verdict line):
MUST FIX:
1. Day 4 09:30 MUDE - Museu do Design: opens at 10:00 on Thursdays -> move it to 10:30.
2. Day 2 empty_morning: start at 09:30 with Carmo Convent (opens 10:00), lunch after it.
DECLINED:
1. Day 3 empty_afternoon: the pace is relaxed and the morning has two long museum visits; the free afternoon stays.
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


TASTE_NAMES = "|".join(TASTE_ISSUES)
_DECLINE_RE = re.compile(rf"Day\s*(\d+)\W+?({TASTE_NAMES})\b\s*[:\-–—]+\s*(.+)", re.I)
_HEADING_RE = re.compile(r"^\s*(MUST FIX|DECLINED|SHOULD FIX|KEEP|VERDICT)\b", re.I)
MIN_REASON_WORDS = 6
# A reason that holds for any plan is not a decision about this one
_VAGUE_RE = re.compile(r"^\W*(it'?s|this is|that is|looks)?\s*(fine|ok|okay|acceptable|minor|good( enough)?|not (a )?"
                       r"(big|major|real) (deal|issue|problem)|no need|not needed|can stay)\W*$", re.I)


def _section(text: str, heading: str) -> str:
    """The lines under a heading of the critique, up to the next heading."""
    out, inside = [], False
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            inside = m.group(1).upper() == heading
            continue
        if inside:
            out.append(line)
    return "\n".join(out)


def declines_of(text: str) -> dict[tuple[str, str], str]:
    """(day, issue) -> reason, from the critique's DECLINED section ("Day 3 empty_afternoon: <reason>")."""
    found = {}
    for line in _section(text, "DECLINED").splitlines():
        for name in TASTE_ISSUES:  # "empty morning" or "Empty_Morning" -> "empty_morning"
            line = re.sub(name.replace("_", "[ _-]"), name, line, flags=re.I)
        m = _DECLINE_RE.search(line)
        if m:
            found[(m.group(1), m.group(2).lower())] = m.group(3).strip()
    return found


def reason_is_specific(reason: str) -> bool:
    """A decline needs a reason about this trip: long enough to say why, and not a stock phrase."""
    return len(re.findall(r"\w+", reason)) >= MIN_REASON_WORDS and not _VAGUE_RE.match(reason)


def decide_taste(text: str, decide: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Splits the day-shape findings by the critic's decision: (declined with a specific reason, declines refused
    for a stock reason, still to fix). A finding the critique does not decline must be fixed."""
    declined, refused, open_ = [], [], []
    reasons = declines_of(text)
    for x in decide:
        reason = reasons.get((str(x.get("day")), x["issue"]))
        if reason and reason_is_specific(reason):
            declined.append({"day": x.get("day"), "issue": x["issue"], "reason": reason})
        else:
            if reason:
                refused.append({"day": x.get("day"), "issue": x["issue"], "reason": reason})
            open_.append(x)
    return declined, refused, open_


def enforce_verdict(callback_context):
    """After-agent callback: code has the last word on the facts, the critic on matters of taste.

    - any must-fix problem -> REVISE (problems the critique missed are appended);
    - day-shape findings ("decide"): the critic declines them (DECLINED, with a reason about this trip) or they are
      fixed; one it does not decline, or declines with a stock phrase, counts as must-fix. Every decline is logged;
    - only should-fix problems -> the critic's REVISE stands only if `worth_revising`, otherwise APPROVED;
    - the verdict line always ends the critique.
    """
    state = callback_context.state
    text = str(state.get("critique") or "").strip()
    said = verdict_of(text)
    report = check_state(state)
    must, should = report["must_fix"], report["should_fix"]
    declined, refused, open_taste = decide_taste(text, report.get("decide") or [])
    state["taste_declined"] = declined  # for the writer's leave-alone list and the guide's "Still to check"
    log = list(state.get("review_log") or [])
    round_no = int(state.get("review_round") or len(log) + 1)
    body = "\n".join(ln for ln in text.splitlines() if not VERDICT_RE.search(ln)).rstrip()
    if must or open_taste:
        verdict = "REVISE"
        listed = _section(text, "MUST FIX")
        refused_keys = {(r["day"], r["issue"]) for r in refused}
        missing = [x for x in must if said != "REVISE"]
        missing += [x for x in open_taste if (x.get("day"), x["issue"]) in refused_keys
                    or not re.search(rf"\bDay\s*{x.get('day')}\b", listed)]
        if missing:
            body += "\nMUST FIX (found by the code check, and not fixed or declined with a reason above):\n" + "\n".join(
                f"- {'Day ' + str(x['day']) + ': ' if x.get('day') else ''}{x['detail']}" for x in missing)
    elif said == "REVISE" and worth_revising(should, log[-1] if log else None, round_no):
        verdict = "REVISE"
    else:
        verdict = "APPROVED"
        if said == "REVISE":  # keep the critic's text in the log
            state["critique_overridden"] = body
            body = ("Approved by the code check: no must-fix problems, and the remaining points have no "
                    "researched fix, made no progress since the last round, or this is the last round.")
        callback_context.actions.escalate = True  # ends the review loop
    state["critique"] = f"{body}\nVERDICT: {verdict}".strip()
    state["review_verdict"] = verdict
    log.append({"round": round_no, "verdict": verdict, "critic_said": said, "overridden": said != verdict,
                "must_fix": len(must), "should_fix": len(should), "decide": len(report.get("decide") or []),
                "declined": declined, "declines_refused": refused, "total_eur": report["facts"].get("total_eur")})
    state["review_log"] = log
    remember_best_plan(state, round_no, [len(must), len(open_taste) + len(should)])
    return None


def remember_best_plan(state, round_no: int, score: list[int]) -> None:
    """Keeps the best-scoring plan of the review ([must-fix, other open problems]; a later plan wins a tie)."""
    best = state.get("best_itinerary")
    if not best or score <= best["score"]:
        state["best_itinerary"] = {"itinerary": state.get("itinerary"), "round": round_no, "score": score}


def _problem_lines(problems: list[dict]) -> str:
    if not problems:
        return "none"
    lines = []
    for n, x in enumerate(problems, 1):
        alts = ", ".join(a.get("name", "") for a in x.get("alternatives") or [] if isinstance(a, dict))
        lines.append(f"{n}. {'Day ' + str(x['day']) + ': ' if x.get('day') else ''}{x['detail']}"
                     + (f" Alternatives: {alts}." if alts else ""))
    return "\n".join(lines)


def code_only_critique(callback_context):
    """Before-agent callback (after start_round), only with CRITIC_MODE=code_only: the critique comes from the code
    check alone and the critic's LLM is not called. The returned text becomes `critique` (the output key). It
    declines no day-shape finding: every "decide" item is fixed, as before REVIEW.md A4."""
    if os.environ.get("CRITIC_MODE") != "code_only":
        return None
    state = callback_context.state
    report = check_state(state)
    must, should = report["must_fix"] + (report.get("decide") or []), report["should_fix"]
    log = state.get("review_log") or []
    round_no = int(state.get("review_round") or len(log) + 1)
    verdict = "REVISE" if must or worth_revising(should, log[-1] if log else None, round_no) else "APPROVED"
    state["critique"] = (f"MUST FIX:\n{_problem_lines(must)}\nSHOULD FIX:\n{_problem_lines(should)}\n"
                         f"KEEP: everything not listed above.\nVERDICT: {verdict}")
    state["critic_mode"] = "code_only"
    enforce_verdict(callback_context)
    return types.Content(role="model", parts=[types.Part(text=state["critique"])])


def build_plan_critic(model=None) -> LlmAgent:
    instruction = "\n\n".join([ROLE, BRIEF, INPUTS, STEPS, tool_budget(3), DAY_SHAPE, RULES, OUTPUT]) + REQUIREMENTS
    return LlmAgent(
        name="plan_critic",
        model=model or MODEL,
        description="Reviews the itinerary (budget, hours, grounding, pacing) and says APPROVED or REVISE.",
        instruction=instruction,
        tools=[check_itinerary, calculate_trip_cost],
        output_key="critique",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        before_agent_callback=[start_round, code_only_critique],
        after_agent_callback=enforce_verdict,
        before_tool_callback=limit_tool_calls(3),
        before_model_callback=offer_retry_tool,
        after_model_callback=retry_malformed_call,
    )
