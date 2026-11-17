"""The full agent tree (Phase 7: without the review loop).

concierge (LlmAgent, root)  --transfer_to_agent-->
planning_pipeline (SequentialAgent)
├─ research_team (ParallelAgent)   see teams.py
├─ itinerary_planner               -> itinerary
└─ guide_writer                    -> final_guide (via save_guide), guide_reply
"""
from google.adk.agents import LlmAgent, SequentialAgent

from .concierge import build_concierge
from .guide_writer import build_guide_writer
from .planner import build_itinerary_planner
from .teams import build_research_team


def build_planning_pipeline() -> SequentialAgent:
    return SequentialAgent(
        name="planning_pipeline",
        description=("Plans the whole trip from the saved trip brief: researches transport, stays, sights and "
                     "food in parallel, builds the day-by-day itinerary and writes the trip guide."),
        sub_agents=[build_research_team(), build_itinerary_planner(), build_guide_writer()],
    )


def build_root_agent() -> LlmAgent:
    return build_concierge(build_planning_pipeline())
