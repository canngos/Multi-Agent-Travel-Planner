"""review_loop (LoopAgent): itinerary_writer -> plan_critic, at most MAX_ROUNDS rounds.

Round 1: the writer drafts the plan and the critic reviews it. Each later round: the writer revises the plan as the
critique asks and the critic reviews the revision. So every plan, the last one included, is reviewed.

Code decides when the loop ends: the critic's verdict is enforced in code (plan_critic.enforce_verdict), and an
APPROVED verdict ends the loop. After the last round without approval, `finish_review` keeps the best plan the
review saw and removes what the travellers could not follow.
"""
from google.adk.agents import LoopAgent

from .itinerary_tidy import tidy_plan
from .itinerary_writer import build_itinerary_writer
from .output import extract_json, fenced_json
from .plan_critic import MAX_ROUNDS, build_plan_critic
from .review_checks import PLACE_KINDS, RESEARCH_KINDS, find_place, research_places, too_late_to_leave, unreadable_research

REVIEW_KEYS = ("review_round", "review_verdict", "review_log", "revision_log", "critique", "itinerary_check",
               "best_itinerary", "critique_overridden", "extra_sights_log", "taste_declined")


def reset_review(callback_context):
    """Before-agent callback: a new plan starts a new review (the session may plan several trips)."""
    for key in REVIEW_KEYS:
        callback_context.state[key] = None
    return None


def finish_review(callback_context):
    """After-agent callback: when the critic never approved, the best-scoring plan of the review is kept (a live run's
    last revision added three places that were not in the research), and the facts the loop failed to fix are
    removed from it."""
    state = callback_context.state
    if state.get("review_verdict") == "APPROVED":
        return None
    best = state.get("best_itinerary")
    if best and best["itinerary"] != state.get("itinerary"):
        state["itinerary"] = best["itinerary"]
        state["revision_log"] = [*(state.get("revision_log") or []), {
            "round": state.get("review_round"),
            "restored": f"the plan of round {best['round']} scored better {best['score']} (must, should)"}]
    drop_ungrounded_places(state)
    drop_too_late_to_leave(state)
    return None


def drop_too_late_to_leave(state) -> None:
    """Departure-day items that still end after the travellers must leave for the airport or station leave the plan
    (logged in state["infeasible_dropped"]): a plan the travellers cannot follow is worse than a shorter last day."""
    try:
        plan = extract_json(state.get("itinerary") or "")
    except ValueError:
        return
    late = too_late_to_leave(plan)
    if not late:
        return
    day = plan["days"][late[0][0]]
    gone = [id(item) for _, item in late]
    day["items"] = [i for i in day.get("items") or [] if id(i) not in gone]
    tidy_plan(plan, state.get("trip_brief") or {})
    state["itinerary"] = fenced_json(plan)
    state["infeasible_dropped"] = [f"day {day.get('day')}: '{item.get('name')}' at {item.get('time')}" for _, item in late]


def drop_ungrounded_places(state) -> None:
    """Places that are still not in the research leave the plan (logged). Two live runs ended with an invented
    "St. Nicholas Cathedral" and a misspelt name. Not when the research itself is unreadable."""
    try:
        plan = extract_json(state.get("itinerary") or "")
    except ValueError:
        return
    places = research_places(state)
    unchecked = set().union(*(RESEARCH_KINDS[key] for key in unreadable_research(state)))
    dropped = []
    for day in plan.get("days") or []:
        if not isinstance(day, dict):
            continue
        kept = []
        for item in day.get("items") or []:
            kind = item.get("kind") if isinstance(item, dict) else None
            if kind in PLACE_KINDS and kind not in unchecked and places and find_place(item.get("name"), places) is None:
                dropped.append(f"day {day.get('day')}: '{item.get('name')}'")
            else:
                kept.append(item)
        day["items"] = kept
    if dropped:
        tidy_plan(plan, state.get("trip_brief") or {})  # walk and cost follow the shorter plan
        state["itinerary"] = fenced_json(plan)
        state["ungrounded_dropped"] = dropped


def build_review_loop(model=None) -> LoopAgent:
    return LoopAgent(
        name="review_loop",
        description=("The writer drafts the itinerary, the critic reviews it, and the writer revises it until the "
                     f"critic approves (at most {MAX_ROUNDS} rounds)."),
        sub_agents=[build_itinerary_writer(model), build_plan_critic(model)],
        max_iterations=MAX_ROUNDS,
        before_agent_callback=reset_review,
        after_agent_callback=finish_review,
    )
