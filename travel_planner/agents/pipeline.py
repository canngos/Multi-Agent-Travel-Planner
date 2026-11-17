"""The full agent tree.

concierge (LlmAgent, root)  --transfer_to_agent-->
planning_pipeline (SequentialAgent)
├─ research_team (ParallelAgent)   see teams.py
├─ itinerary_planner               -> itinerary
├─ review_loop (LoopAgent, max 3 rounds)
│  ├─ budget_critic                -> critique (ends with VERDICT: APPROVED | REVISE)
│  └─ itinerary_reviser            -> itinerary (revised), or exit_loop when approved
└─ guide_writer                    -> final_guide (via save_guide), guide_reply
"""
from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent

from .budget_critic import MAX_ROUNDS, build_budget_critic
from .concierge import build_concierge
from .guide_writer import build_guide_writer
from .itinerary_reviser import build_itinerary_reviser
from .planner import build_itinerary_planner
from .teams import build_research_team


def reset_review(callback_context):
    """Before-agent callback of the loop: a new plan starts a new review (the session may plan several trips)."""
    state = callback_context.state
    for key in ("review_round", "review_verdict", "review_log", "revision_log", "critique", "itinerary_check",
                "best_itinerary", "critique_overridden"):
        state[key] = None
    return None


def build_review_loop(model=None) -> LoopAgent:
    return LoopAgent(
        name="review_loop",
        description="Critic and reviser improve the itinerary until the critic approves it (at most 3 rounds).",
        sub_agents=[build_budget_critic(model), build_itinerary_reviser(model)],
        max_iterations=MAX_ROUNDS,
        before_agent_callback=reset_review,
    )


def build_planning_pipeline() -> SequentialAgent:
    return SequentialAgent(
        name="planning_pipeline",
        description=("Plans the whole trip from the saved trip brief: researches transport, stays, sights and "
                     "food in parallel, builds the day-by-day itinerary, reviews and revises it, and writes the "
                     "trip guide."),
        sub_agents=[build_research_team(), build_itinerary_planner(), build_review_loop(), build_guide_writer()],
    )


def build_root_agent() -> LlmAgent:
    return build_concierge(build_planning_pipeline())
