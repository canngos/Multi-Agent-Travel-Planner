"""Workflow agents (no LLM) that run the research agents together.

research_team (ParallelAgent)
├─ transport_team (SequentialAgent): ParallelAgent[flight_searcher, ground_searcher] → transport_comparer
├─ stay_team (SequentialAgent):      ParallelAgent[airbnb_searcher, hotel_searcher] → stay_comparer
├─ culture_agent
└─ food_agent

Every agent writes its own state key, and nothing in a parallel branch reads another branch's key
(the comparers only read their own team's searchers, which have finished by then).
"""
from google.adk.agents import ParallelAgent, SequentialAgent

from .comparers import build_stay_comparer, build_transport_comparer
from .culture import build_culture_agent
from .food import build_food_agent
from .stay import build_airbnb_searcher, build_hotel_searcher
from .transport import build_flight_searcher, build_ground_searcher

RESEARCH_KEYS = ("flight_options", "ground_options", "transport_summary", "airbnb_options", "hotel_options",
                 "stay_summary", "culture_findings", "food_findings")


def build_transport_team() -> SequentialAgent:
    return SequentialAgent(
        name="transport_team",
        description="Searches flights and ground transport in parallel, then compares them.",
        sub_agents=[
            ParallelAgent(name="transport_search", sub_agents=[build_flight_searcher(), build_ground_searcher()]),
            build_transport_comparer(),
        ],
    )


def build_stay_team() -> SequentialAgent:
    return SequentialAgent(
        name="stay_team",
        description="Searches Airbnb and hotels in parallel, then compares them per neighbourhood.",
        sub_agents=[
            ParallelAgent(name="stay_search", sub_agents=[build_airbnb_searcher(), build_hotel_searcher()]),
            build_stay_comparer(),
        ],
    )


def build_research_team() -> ParallelAgent:
    return ParallelAgent(
        name="research_team",
        description="Runs transport, stay, culture and food research at the same time.",
        sub_agents=[build_transport_team(), build_stay_team(), build_culture_agent(), build_food_agent()],
    )
