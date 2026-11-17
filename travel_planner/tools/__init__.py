"""Function tools for the agents. Every tool returns {"status": "ok"|"error", ...} and never raises."""
from .dishes import local_dishes
from .flights import search_google_flights
from .food import find_restaurants
from .geo import cluster_by_distance, geocode_place
from .hotels import search_hotel_rates, stay_deep_links
from .search import web_search
from .serpapi import search_google_hotels, search_serpapi_flights
from .sight_hours import find_sight_hours
from .sights import find_sights
from .transit import plan_public_transport
from .trip import calculate_trip_cost, save_guide, save_trip_brief
from .weather import get_forecast
from .wiki import wiki_summary, wikivoyage_section

ALL_TOOLS = [
    geocode_place, cluster_by_distance, get_forecast,
    find_sights, find_sight_hours, wiki_summary, wikivoyage_section, web_search,
    local_dishes, find_restaurants,
    plan_public_transport, search_google_flights, search_serpapi_flights,
    search_hotel_rates, search_google_hotels, stay_deep_links,
    save_trip_brief, calculate_trip_cost, save_guide,
]
