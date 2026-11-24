"""A trip in the travellers' own car ("I will use my car"): no flights, trains or buses; the drive is not costed.

Fuel is deliberately not estimated: the car may be electric (project owner's decision). The plan gives a rough
road distance and driving time and says that fuel or charging, tolls and parking are not included.
"""
from types import SimpleNamespace

from tests.test_replan import BRIEF, ResearchLlm, TOOL_ARGS, _ctx, _finished, _run_research
from tests.test_review import _state, fixed_plan
from travel_planner.agents import replan
from travel_planner.agents.approval import plan_summary
from travel_planner.agents.concierge import require_confirmation
from travel_planner.agents.itinerary_tidy import tidy_itinerary
from travel_planner.agents.output import extract_json
from travel_planner.agents.review_checks import check_plan
from travel_planner.tools.trip import check_brief, drive_estimate, requirements_block, save_trip_brief

CAR = {**BRIEF, "own_car": True}


def test_the_brief_keeps_the_field_only_when_set_and_gives_a_rough_drive():
    state = {}
    save_trip_brief(**TOOL_ARGS, tool_context=SimpleNamespace(state=state))
    assert "own_car" not in state["trip_brief"]  # ordinary briefs (and the demo's prompts) are unchanged
    save_trip_brief(**TOOL_ARGS, own_car=True, tool_context=SimpleNamespace(state=state))
    text = state["trip_requirements"]
    assert state["trip_brief"]["own_car"] is True and "drive their own car from Helsinki to Lisbon" in text
    km, hours = drive_estimate(check_brief(TOOL_ARGS))
    assert 4000 < km < 4400 and hours == round(km / 80 * 2) / 2  # Helsinki-Lisbon: ~3,370 km straight line
    assert f"roughly {km} km" in text and "car ferry" in text and "fuel or charging" in text
    assert "fuel or charging" not in requirements_block(check_brief(TOOL_ARGS))


async def test_flights_are_not_searched_but_the_ground_searcher_may_find_a_car_ferry():
    llm = ResearchLlm()
    state = await _run_research({"trip_brief": dict(CAR)}, llm)
    assert "FLIGHT SEARCHER" not in llm.asked and "GROUND TRANSPORT SEARCHER" in llm.asked
    assert "drive their own car" in extract_json(state["flight_options"])["notes"]


def test_a_planned_flight_is_removed_a_ferry_stays_and_the_drive_is_not_costed():
    plan = fixed_plan()  # Norwegian flight out, TAP flight back
    ferry = {**plan["transport"]["return"], "mode": "ferry", "operator": "Ferry Co"}
    plan["transport"]["return"] = ferry
    base = _state(plan)
    state = {**base, "trip_brief": {**base["trip_brief"], "own_car": True}}
    tidy_itinerary(SimpleNamespace(state=state))
    plan = extract_json(state["itinerary"])
    assert plan["transport"]["outbound"] is None and plan["transport"]["return"]["operator"] == "Ferry Co"
    assert any("removed: the travellers drive their own car" in c for c in state["itinerary_tidy_changes"])
    cost = plan["cost"]
    assert cost["categories_eur"]["local_transport"] == 0
    assert cost["categories_eur"]["transport"] == ferry["price_eur"]  # the ferry only: no fuel guess
    assert any("fuel or charging, tolls and parking are not included" in a for a in cost["assumptions"])
    report = check_plan(plan, state)
    assert not {x["issue"] for x in report["must_fix"]} & {"avoided_mode", "travel_day_lost"}
    assert "**Getting there:** by your own car (and the car ferry below)" in plan_summary(plan, state["trip_brief"])


def test_turning_the_car_on_or_off_needs_the_transport_research():
    state = _finished()
    assert "transport" in replan.scope_problem(state, BRIEF, CAR, ["stay"])
    assert replan.scope_problem(state, BRIEF, CAR, ["transport"]) is None


def test_the_concierge_is_pointed_to_the_field_when_it_writes_a_requirement_instead():
    tool = SimpleNamespace(name="save_trip_brief")
    reply = require_confirmation(tool, {**TOOL_ARGS, "requirements": ["I will use my car during the trip"]}, _ctx({}))
    assert "set own_car true" in reply["reason"]
    reply = require_confirmation(tool, {**TOOL_ARGS, "own_car": True}, _ctx({}, "inv-9"))
    assert "own_car" not in reply["reason"]


def _routing_session(*ferries):
    response = {"status": "ok", "results": list(ferries)}
    part = SimpleNamespace(function_response=SimpleNamespace(name="plan_public_transport", response=response))
    return SimpleNamespace(events=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])


FERRY_OUT = {"mode": "ferry", "operator": "Tallink", "depart": "2027-05-10T10:30", "arrive": "2027-05-10T12:30",
             "duration_min": 120, "stops": 0, "price_eur": None, "source": "transitous", "url": "https://transitous.org/",
             "details": "ferry (Tallink): Helsinki → Tallinn"}
FERRY_BACK = {**FERRY_OUT, "operator": "Viking Line", "depart": "2027-05-14T16:30", "arrive": "2027-05-14T18:30"}


def test_a_ferry_the_routing_tool_found_is_kept_when_the_agent_drives_over_the_sea():
    """Regression: a live Helsinki-Tallinn car trip "drove" 100 km across the Gulf of Finland."""
    from travel_planner.agents.transport import keep_car_ferry
    said = "```json\n" + '{"outbound": [], "return": [], "fallback_links": [], "notes": "They drive (100 km)."}' + "\n```"
    state = {"trip_brief": dict(CAR), "ground_options": said}
    keep_car_ferry(SimpleNamespace(state=state, session=_routing_session(FERRY_OUT, FERRY_BACK)))
    data = extract_json(state["ground_options"])
    assert [o["operator"] for o in data["outbound"]] == ["Tallink"] and data["return"][0]["operator"] == "Viking Line"
    assert "car fare unknown" in data["outbound"][0]["details"] and "crosses the sea" in data["notes"]
    plain = {"trip_brief": dict(BRIEF), "ground_options": said}  # not a car trip: nothing changes
    keep_car_ferry(SimpleNamespace(state=plain, session=_routing_session(FERRY_OUT)))
    assert plain["ground_options"] == said


def test_a_car_trip_over_the_sea_without_the_ferry_is_must_fix():
    import json as _json
    plan = fixed_plan()
    plan["transport"] = {"outbound": None, "return": None, "why": "own car"}
    base = _state(plan)
    summary = {"outbound": {"cheapest": FERRY_OUT, "fastest": FERRY_OUT, "ranked": [FERRY_OUT]},
               "return": {"cheapest": None, "fastest": None, "ranked": []}}
    state = {**base, "trip_brief": {**base["trip_brief"], "own_car": True}, "transport_summary": _json.dumps(summary)}
    issues = [x for x in check_plan(plan, state)["must_fix"] if x["issue"] == "car_ferry_missing"]
    assert len(issues) == 1 and "outbound" in issues[0]["detail"] and "Tallink" in issues[0]["detail"]
    plan["transport"]["outbound"] = dict(FERRY_OUT)
    assert not [x for x in check_plan(plan, state)["must_fix"] if x["issue"] == "car_ferry_missing"]
