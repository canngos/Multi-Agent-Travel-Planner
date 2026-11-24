"""Web fares of the ground searcher: the model copies the per-person fare, the code prices the group.

A live Tallinn-Riga run for two stored the €24 per-person bus fare as the price, so the plan and its cost counted
half the tickets while the guide said €48.
"""
import json
from types import SimpleNamespace

from travel_planner.agents.output import check_output
from travel_planner.agents.transport import price_web_fares

BUS = {"mode": "bus", "operator": "FlixBus", "depart": "2027-05-21T10:07", "arrive": "2027-05-21T15:10",
       "duration_min": 303, "stops": 0, "price_eur": None, "price_source": "web_estimate",
       "source": "transitous+tavily", "url": "https://global.flixbus.com",
       "details": "fare from https://www.omio.com/buses/tallinn/riga"}


def _run(options: dict, travellers: int) -> tuple[dict, list[str]]:
    state = {"trip_brief": {"travellers": travellers},
             "ground_options": "```json\n" + json.dumps(options) + "\n```"}
    price_web_fares(SimpleNamespace(state=state))
    return check_output("ground_options", state["ground_options"])


def test_the_per_person_fare_is_priced_for_the_group_and_the_field_removed():
    data, problems = _run({"outbound": [{**BUS, "fare_per_person_eur": 24}], "return": []}, travellers=2)
    bus = data["outbound"][0]
    assert not problems  # the extra field is gone, so the shared schema still holds
    assert "fare_per_person_eur" not in bus
    assert bus["price_eur"] == 48.0 and bus["price_source"] == "web_estimate"
    assert "€24 per person x 2 travellers" in bus["details"]


def test_a_price_the_model_computed_itself_is_replaced_by_the_code():
    data, _ = _run({"outbound": [{**BUS, "fare_per_person_eur": 24, "price_eur": 24}], "return": []}, travellers=3)
    assert data["outbound"][0]["price_eur"] == 72.0


def test_no_fare_found_keeps_the_price_unknown_and_other_options_are_untouched():
    flight_like = {**BUS, "price_eur": 99.0, "details": "live"}
    data, problems = _run({"outbound": [{**BUS, "fare_per_person_eur": None}], "return": [flight_like]},
                          travellers=2)
    assert not problems
    assert data["outbound"][0]["price_eur"] is None and "fare_per_person_eur" not in data["outbound"][0]
    assert data["return"][0] == flight_like


def test_the_ground_searcher_is_asked_for_the_per_person_fare_and_flights_keep_totals():
    # the first try added the field to the steps only: the shared shape still said "price_eur is the TOTAL", and
    # the model followed the shape and multiplied by itself
    from travel_planner.agents.transport import FLIGHT_EXAMPLE, GROUND_SHAPE, TRANSPORT_SHAPE, build_ground_searcher
    assert "fare_per_person_eur" in GROUND_SHAPE and "TOTAL" not in GROUND_SHAPE
    assert "fare_per_person_eur" not in TRANSPORT_SHAPE + FLIGHT_EXAMPLE
    assert "fare_per_person_eur" in str(build_ground_searcher().instruction)


def test_one_traveller_gets_no_per_person_note():
    data, _ = _run({"outbound": [{**BUS, "fare_per_person_eur": 30}], "return": []}, travellers=1)
    assert data["outbound"][0]["price_eur"] == 30.0
    assert "per person" not in data["outbound"][0]["details"]
