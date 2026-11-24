"""Regression tests on the final states of the 2026-11-19 live stress test (reports/stress-test-2026-11-19).

Each fixture is a run's session state (guide text and local paths removed). The code checks are re-run on the plan
the run ended with: every check added after the test must find the problem the report describes."""
import json
from pathlib import Path

import pytest

from travel_planner.agents.output import extract_json, was_repaired
from travel_planner.agents.review_checks import check_plan

FIXTURES = Path(__file__).parent / "fixtures" / "stress"


def state_of(run: str) -> dict:
    return json.loads((FIXTURES / f"{run}.json").read_text(encoding="utf-8"))


def issues(run: str, level: str = "must_fix") -> dict[str, list[dict]]:
    state = state_of(run)
    report = check_plan(extract_json(state["itinerary"]), state)
    found = {}
    for x in report[level] + (report.get("decide") or [] if level == "must_fix" else []):
        found.setdefault(x["issue"], []).append(x)
    return found


def test_vienna_broken_summary_is_repaired_and_the_lost_day_found():
    state = state_of("07")
    assert was_repaired(state["transport_summary"])
    assert extract_json(state["transport_summary"])["outbound"]["ranked"]
    assert "travel_day_lost" in issues("07")


def test_kyiv_flights_to_chisinau_are_found_and_warned():
    from travel_planner.agents.guide_writer import travel_warnings
    found = issues("27", "should_fix")["far_airport"]  # no option lands nearer: a warning, not a must-fix
    assert {"RMO" in x["detail"] for x in found} == {True}
    state = state_of("27")
    assert any("Chișinău" in n for n in travel_warnings(extract_json(state["itinerary"]), state))


def test_no_time_to_get_to_the_airport_on_the_last_day():
    assert "Casa Batll" in issues("24")["no_time_to_leave"][0]["detail"]
    assert any("Mix kiosk" in x["detail"] for x in issues("12")["no_time_to_leave"])


def test_return_after_the_trip_and_overnight_outbound():
    from travel_planner.agents.review_checks import plan_cost, stay_nights
    assert "night_without_stay" in issues("06", "should_fix")
    for run, nights in (("09", (1, 2)), ("12", (2, 3))):
        state = state_of(run)
        plan = extract_json(state["itinerary"])
        assert stay_nights(plan, state["trip_brief"])[:2] == nights
        cost = plan_cost(plan, state["trip_brief"])
        assert cost["categories_eur"]["stay"] < plan["stay"]["total_price_eur"]
        assert any("check in on" in a for a in cost["assumptions"])


def test_leisure_mornings_are_removed_and_the_empty_days_found():
    from travel_planner.agents.itinerary_tidy import tidy_plan
    state = state_of("03")
    plan = extract_json(state["itinerary"])
    changes = tidy_plan(plan, state["trip_brief"])
    assert sum("Leisure morning" in c for c in changes) >= 10
    report = check_plan(plan, state)
    assert len([x for x in report["must_fix"] if x["issue"] == "empty_day"]) >= 10


def test_typo_of_a_researched_name_matches_it():
    assert "ungrounded_place" not in issues("25b")


@pytest.mark.parametrize("run", ["07", "09", "24", "29", "14"])
def test_stay_below_the_budget_tier(run):
    assert issues(run)["stay_tier"][0]["alternatives"]


def test_low_budget_may_keep_a_hostel():
    assert "stay_tier" not in issues("01")


def test_transport_value_and_needs():
    found = issues("07")
    assert len(found["unsuitable_transport"]) == 2  # 31-38 h buses for a wheelchair user
    detail = issues("29")["poor_transport_choice"][0]["detail"]  # the 08:50 return while 18:55 costs €4 more
    assert "18:55" in detail and "€4 more" in detail


def test_recommended_option_prefers_the_day_keeping_flight():
    from travel_planner.agents.review_checks import recommended_option, transport_summary
    state = state_of("29")
    best = recommended_option(transport_summary(state), "return", state["trip_brief"])
    assert best["depart"].endswith("18:55")


def test_unknown_fares_are_not_counted_silently():
    from travel_planner.agents.guide_writer import render_cost, travel_warnings
    from travel_planner.agents.review_checks import plan_cost
    state = state_of("09")
    plan = extract_json(state["itinerary"])
    cost = plan_cost(plan, state["trip_brief"])
    assert cost["incomplete"] and "Not included (price unknown)" in render_cost(cost)
    aland = state_of("15")
    assert any("no known fare" in n for n in travel_warnings(extract_json(aland["itinerary"]), aland))


async def test_group_over_nine_is_searched_per_person_and_scaled():
    from types import SimpleNamespace
    from travel_planner.agents.transport import flight_tool_after, flight_tool_before
    ctx = SimpleNamespace(state={"trip_brief": {"travellers": 14}})
    tool = SimpleNamespace(name="search_serpapi_flights")
    args = {"origin_iata": "HEL", "destination_iata": "VIE", "date": "2027-04-20", "adults": 14}
    assert await flight_tool_before(tool, args, ctx) is None and args["adults"] == 1
    out = await flight_tool_after(tool, args, ctx, {"status": "ok", "results": [{"price_eur": 120.0, "details": "x"}]})
    assert out["results"][0]["price_eur"] == 1680.0 and "group desk" in out["results"][0]["details"]


def test_interests_and_food_tier():
    found = issues("15")
    assert found["interest_missing"][0]["alternatives"]  # "nature": a researched place is left unused
    assert "cycling" in issues("15", "should_fix")["interest_not_found"][0]["detail"]
    assert "Hotel Holt" in issues("02")["food_tier"][0]["detail"]  # a splurge on a €200 low-budget trip


def test_splurge_meals_are_priced():
    from travel_planner.agents.itinerary_tidy import tag_food_tiers
    from travel_planner.agents.review_checks import plan_cost, research_places
    state = state_of("02")
    plan = extract_json(state["itinerary"])
    before = plan_cost(plan, state["trip_brief"])["categories_eur"]["food"]
    tag_food_tiers(plan, research_places(state))
    after = plan_cost(plan, state["trip_brief"])
    assert after["categories_eur"]["food"] > before and any("extra for planned" in a for a in after["assumptions"])


def test_polar_night_and_trip_size_blocks():
    from datetime import date
    from travel_planner.models import TripBrief
    from travel_planner.tools.sun import daylight_note
    from travel_planner.tools.trip import food_research_block, pace_research_block, plan_size_block
    assert daylight_note(78.2, date(2027, 1, 15), date(2027, 1, 18)).startswith("Polar night")
    assert daylight_note(38.7, date(2027, 5, 10), date(2027, 5, 14)) is None
    long_trip = TripBrief(destination="Porto", start_date="2027-04-01", end_date="2027-04-21")
    assert "up to 30 sights" in pace_research_block(long_trip) and "Go next" in pace_research_block(long_trip)
    assert "up to 30 places" in food_research_block(long_trip) and "up to 30 sights" in plan_size_block(long_trip)
    short = TripBrief(destination="Porto", start_date="2027-04-01", end_date="2027-04-05")
    assert pace_research_block(short) == food_research_block(short) == plan_size_block(short) == ""


def test_long_trips_get_more_research_calls():
    from travel_planner.agents.limits import limit_tool_calls
    from travel_planner.tools.trip import extra_research_calls
    from types import SimpleNamespace
    for days, allowed in ((5, 3), (21, 7)):
        cb = limit_tool_calls(1, bonus=extra_research_calls)
        ctx = SimpleNamespace(state={"trip_brief": {"days": days}}, invocation_id=f"r{days}", agent_name="x")
        answers = [cb(SimpleNamespace(name="web_search"), {}, ctx) for _ in range(10)]
        assert answers.index(next(a for a in answers if a)) == allowed


def test_items_too_late_to_leave_are_dropped_after_the_last_round():
    from travel_planner.agents.itinerary_reviser import drop_too_late_to_leave
    state = state_of("24")
    state["review_round"] = 3
    drop_too_late_to_leave(state)
    assert any("Casa Batll" in x for x in state["infeasible_dropped"])
    assert "no_time_to_leave" not in {x["issue"] for x in check_plan(extract_json(state["itinerary"]), state)["must_fix"]}


def test_the_time_to_leave_message_names_the_latest_start():
    assert "start it by 07:15" in issues("24")["no_time_to_leave"][0]["detail"]  # 10:45 flight - 2.5 h - 1 h visit


def test_fixes_found_by_the_second_stress_run():
    from datetime import date
    from travel_planner.agents.review_checks import parse_hours, travel_free
    hours = "Mon–Fri 07:00–16:00, Sat 08:00–16:00, Sun 09:00–15:00"
    assert parse_hours(hours, date(2027, 6, 4)) == [(420, 960)]  # a Friday
    assert parse_hours(hours, date(2027, 6, 6)) == [(540, 900)]  # a Sunday
    plan = {"transport": {"outbound": {"arrive": "2027-04-06T20:55"}, "return": {"depart": "2027-04-11T10:00"}}}
    assert not travel_free(plan, "2027-04-06") and travel_free(plan, "2027-04-07")  # landed at 20:55 on day 2
    assert not travel_free(plan, "2027-04-11")


def test_the_recommendation_stays_on_the_trips_travel_days():
    from travel_planner.agents.review_checks import recommended_option
    brief = {"start_date": "2027-06-04", "end_date": "2027-06-06", "travellers": 2, "budget_tier": "low"}
    ferry = {"mode": "ferry", "operator": "Viking Line", "depart": "2027-06-07T13:45", "arrive": "2027-06-07T22:42",
             "duration_min": 537, "price_eur": 28.0}
    flight = {"mode": "flight", "operator": "Finnair", "depart": "2027-06-06T09:50", "arrive": "2027-06-06T10:40",
              "duration_min": 50, "price_eur": 155.0}
    assert recommended_option({"return": {"ranked": [ferry, flight]}}, "return", brief)["operator"] == "Finnair"
    assert recommended_option({"return": {"ranked": [ferry]}}, "return", brief)["operator"] == "Viking Line"
