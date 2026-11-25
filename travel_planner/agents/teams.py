"""Workflow agents (no LLM) that run the research agents together.

research_team (ParallelAgent)
├─ transport_team (ParallelAgent): flight_searcher, ground_searcher  -> code writes transport_summary
├─ stay_team (ParallelAgent):      airbnb_searcher, hotel_searcher   -> code writes stay_summary
├─ culture_agent
└─ food_agent

Every agent writes its own state key, and nothing in a parallel branch reads another branch's key. When a team's
searchers are done, its after-agent callback compares their options in code (summaries.py).

On a re-plan, a branch the concierge's scope does not name is skipped and keeps its results (replan.skip_branch).
"""
from google.adk.agents import ParallelAgent

from .culture import build_culture_agent
from .food import build_food_agent
from .replan import skip_branch
from .stay import build_airbnb_searcher, build_hotel_searcher, skip_without_stay
from .summaries import summarize_stay, summarize_transport
from .transport import build_flight_searcher, build_ground_searcher

RESEARCH_KEYS = ("flight_options", "ground_options", "transport_summary", "airbnb_options", "hotel_options",
                 "stay_summary", "culture_findings", "food_findings")


def build_transport_team() -> ParallelAgent:
    return ParallelAgent(
        name="transport_team",
        description="Searches flights and ground transport in parallel; code then compares the options.",
        sub_agents=[build_flight_searcher(), build_ground_searcher()],
        before_agent_callback=skip_branch("transport"),
        after_agent_callback=summarize_transport,
    )


def build_stay_team() -> ParallelAgent:
    return ParallelAgent(
        name="stay_team",
        description="Searches Airbnb and hotels in parallel; code then compares the stays per neighbourhood.",
        sub_agents=[build_airbnb_searcher(), build_hotel_searcher()],
        # no stay needed (a hard fact the user stated), or a re-plan that keeps the stay research
        before_agent_callback=[skip_without_stay, skip_branch("stay")],
        after_agent_callback=summarize_stay,
    )


def _skippable(agent, branch: str):
    """A research LlmAgent that a re-plan can skip (it then re-emits its kept output, see replan.skip_branch)."""
    agent.before_agent_callback = skip_branch(branch, agent.output_key)
    return agent


def build_research_team(**kwargs) -> ParallelAgent:
    return ParallelAgent(
        name="research_team",
        description="Runs transport, stay, culture and food research at the same time.",
        sub_agents=[build_transport_team(), build_stay_team(), _skippable(build_culture_agent(), "culture"),
                    _skippable(build_food_agent(), "food")],
        **kwargs,
    )
