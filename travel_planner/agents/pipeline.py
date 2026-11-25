"""The full agent tree.

concierge (LlmAgent, root; tools: geocode_place, save_trip_brief, request_replan, approve_plan)
├─ planning_pipeline (SequentialAgent)       <- save_trip_brief "ok", or request_replan
│  ├─ research_team (ParallelAgent)          see teams.py; a re-plan skips the branches out of scope
│  └─ review_loop (LoopAgent, max 4 rounds)  see review_loop.py; ends when the critic approves
│     ├─ itinerary_writer                    -> itinerary (drafted in round 1, revised later);
│     │                                         may call extra_sights_researcher (agent as tool)
│     └─ plan_critic                         -> critique (ends with VERDICT: APPROVED | REVISE)
│  then present_plan shows the reviewed plan and waits for the user
└─ guide_writer                              <- approve_plan; -> final_guide (via save_guide), guide_reply
"""
from google.adk.agents import LlmAgent, SequentialAgent

from .approval import STATUS_KEY, SUMMARY_KEY, present_plan
from .concierge import build_concierge
from .guide_writer import build_guide_writer
from .replan import (BRANCHES, NOTE_KEY, PLANNED_KEY, REUSED_KEY, STAY_WORDS, TRANSPORT_WORDS, accepted_scope,
                     changed_fields, note_block)
from .review_checks import _parse
from .review_loop import build_review_loop
from .teams import RESEARCH_KEYS, build_research_team

PLAN_KEYS = RESEARCH_KEYS + ("itinerary", "itinerary_before_revision", "itinerary_link_fixes", "itinerary_tidy_changes",
                             "research_link_fixes", "final_guide", "guide_paths", "guide_reply", "guide_reply_raw", "guide_message_sent", "guide_link_fixes",
                             STATUS_KEY, SUMMARY_KEY, "guide_exports", "exports_by_code", "ungrounded_dropped", "json_repaired",
                             "infeasible_dropped")


PINNED_KEY = "pinned_choices"          # transport/stay of the previous plan that this re-plan keeps (tidy restores them)
PREVIOUS_KEY = "previous_plan_choices"  # transport and stay of the previous plan, to tell the user what did not change


def pinned_choices(scope: dict, previous: dict, brief: dict) -> dict | None:
    """The previous plan's transport (and stay) when the re-plan keeps that research and nothing that could change
    the choice changed: a live "swap two days" re-plan (rerun []) switched the outbound flight from Finnair 07:00
    to airBaltic 09:15 although the user asked for nothing about transport."""
    changed = changed_fields(scope.get("base"), brief)
    note = str(scope.get("change_note") or "")
    pins = {}
    if "transport" not in scope["rerun"] and isinstance(previous.get("transport"), dict) \
            and not changed & {"total_budget_eur", "budget_tier", "avoid_modes", "own_car", "requirements"} \
            and not TRANSPORT_WORDS.search(note):
        pins["transport"] = previous["transport"]
    if "stay" not in scope["rerun"] and isinstance(previous.get("stay"), dict) \
            and not changed & {"total_budget_eur", "budget_tier", "own_accommodation", "requirements"} \
            and not STAY_WORDS.search(note):
        pins["stay"] = previous["stay"]
    return pins or None


def reset_plan_state(callback_context):
    """Before-agent callback of the pipeline: a new plan starts from nothing, except the research branches that the
    concierge's accepted re-plan scope keeps (see replan.py: the agent decides, code only validates).

    A session can plan several trips. Without this, an agent that writes nothing on the second plan (an error, an
    empty reply) left the first trip's research in place for the later agents, and the old guide stopped the guide
    writer's safety net and kept answering the concierge's follow-up questions."""
    state = callback_context.state
    scope = accepted_scope(state, getattr(callback_context, "invocation_id", None))
    kept = [b for b in BRANCHES if b not in scope["rerun"]] if scope else []
    keep = {key for branch in kept for key in BRANCHES[branch]}
    previous = _parse(state.get("itinerary")) if scope else {}
    state[PINNED_KEY] = pinned_choices(scope, previous, state.get("trip_brief") or {}) if previous else None
    state[PREVIOUS_KEY] = ({"transport": previous.get("transport"), "stay": previous.get("stay")}
                           if previous else None)
    link_fixes = state.get("research_link_fixes") or {}
    for key in PLAN_KEYS:
        if key not in keep:
            state[key] = None
    if keep:  # link fixes of the kept research stay with it
        fixes = {k: v for k, v in link_fixes.items() if k in keep}
        state["research_link_fixes"] = fixes or None
    state[REUSED_KEY] = kept or None
    state[NOTE_KEY] = note_block(scope)
    state[PLANNED_KEY] = state.get("trip_brief")  # what the research in state (kept or new) is for
    return None


def build_planning_pipeline() -> SequentialAgent:
    return SequentialAgent(
        name="planning_pipeline",
        description=("Plans the whole trip from the saved trip brief: researches transport, stays, sights and "
                     "food in parallel, builds the day-by-day itinerary, reviews and revises it, and shows the "
                     "reviewed plan to the user for approval."),
        sub_agents=[build_research_team(), build_review_loop()],
        before_agent_callback=reset_plan_state,
        after_agent_callback=present_plan,
    )


def build_root_agent() -> LlmAgent:
    return build_concierge(build_planning_pipeline(), build_guide_writer(standalone=True))
