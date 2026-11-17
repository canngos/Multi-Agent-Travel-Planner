"""Agents of the travel planner. Factories, because an ADK agent instance can have only one parent
(the same agent is used alone in the dev harness and inside the pipeline)."""
from .culture import build_culture_agent
from .food import build_food_agent
from .stay import build_airbnb_searcher, build_hotel_searcher
from .transport import build_flight_searcher, build_ground_searcher

RESEARCH_AGENTS = {
    "culture_agent": build_culture_agent,
    "food_agent": build_food_agent,
    "flight_searcher": build_flight_searcher,
    "ground_searcher": build_ground_searcher,
    "airbnb_searcher": build_airbnb_searcher,
    "hotel_searcher": build_hotel_searcher,
}
