"""The full agent tree.

concierge (LlmAgent, root; tools: geocode_place, save_trip_brief, request_replan, approve_plan)
├─ planning_pipeline (SequentialAgent)       <- save_trip_brief "ok", or request_replan
│  ├─ research_team (ParallelAgent)          see teams.py; a re-plan skips the branches out of scope
│  ├─ itinerary_planner                      -> itinerary
│  └─ review_loop (LoopAgent, max 3 rounds)
│     ├─ plan_critic                       -> critique (ends with VERDICT: APPROVED | REVISE)
│     └─ itinerary_reviser                   -> itinerary (revised), or exit_loop when approved;
│                                               may call extra_sights_researcher (agent as tool)
│  then present_plan shows the reviewed plan and waits for the user
└─ guide_writer                              <- approve_plan; -> final_guide (via save_guide), guide_reply
"""
from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent

from .approval import STATUS_KEY, SUMMARY_KEY, present_plan
from .plan_critic import MAX_ROUNDS, build_plan_critic
from .concierge import build_concierge
from .guide_writer import build_guide_writer
from .itinerary_reviser import build_itinerary_reviser
from .planner import build_itinerary_planner
from .replan import BRANCHES, NOTE_KEY, PLANNED_KEY, REUSED_KEY, accepted_scope, note_block
from .teams import RESEARCH_KEYS, build_research_team

PLAN_KEYS = RESEARCH_KEYS + ("itinerary", "itinerary_before_revision", "itinerary_link_fixes", "itinerary_tidy_changes",
                             "research_link_fixes", "final_guide", "guide_paths", "guide_reply", "guide_link_fixes",
                             STATUS_KEY, SUMMARY_KEY)


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


def reset_review(callback_context):
    """Before-agent callback of the loop: a new plan starts a new review (the session may plan several trips)."""
    state = callback_context.state
    for key in ("review_round", "review_verdict", "review_log", "revision_log", "critique", "itinerary_check",
                "best_itinerary", "critique_overridden", "extra_sights_log", "taste_declined"):
        state[key] = None
    return None


def build_review_loop(model=None) -> LoopAgent:
    return LoopAgent(
        name="review_loop",
        description="Critic and reviser improve the itinerary until the critic approves it (at most 3 rounds).",
        sub_agents=[build_plan_critic(model), build_itinerary_reviser(model)],
        max_iterations=MAX_ROUNDS,
        before_agent_callback=reset_review,
    )


def build_planning_pipeline() -> SequentialAgent:
    return SequentialAgent(
        name="planning_pipeline",
        description=("Plans the whole trip from the saved trip brief: researches transport, stays, sights and "
                     "food in parallel, builds the day-by-day itinerary, reviews and revises it, and shows the "
                     "reviewed plan to the user for approval."),
        sub_agents=[build_research_team(), build_itinerary_planner(), build_review_loop()],
        before_agent_callback=reset_plan_state,
        after_agent_callback=present_plan,
    )


def build_root_agent() -> LlmAgent:
    return build_concierge(build_planning_pipeline(), build_guide_writer(standalone=True))
