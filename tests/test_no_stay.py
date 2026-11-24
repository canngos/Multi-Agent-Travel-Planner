"""A trip without accommodation ("we stay in our own apartment"): no stay is searched, planned or costed.

Regression: a tester gave "staying in own apartment (no accommodation needed)" as a requirement; the hotel and
Airbnb searchers still ran and the guide still offered a hotel.
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
from travel_planner.tools.trip import check_brief, requirements_block, save_trip_brief

OWN = {**BRIEF, "own_accommodation": True}


def test_the_brief_keeps_the_field_only_when_set():
    state = {}
    save_trip_brief(**TOOL_ARGS, tool_context=SimpleNamespace(state=state))
    assert "own_accommodation" not in state["trip_brief"]  # ordinary briefs (and the demo's prompts) are unchanged
    save_trip_brief(**TOOL_ARGS, own_accommodation=True, tool_context=SimpleNamespace(state=state))
    assert state["trip_brief"]["own_accommodation"] is True
    assert "No accommodation is needed" in state["trip_requirements"]


def test_every_agent_is_told_there_is_no_stay_and_no_rooms_line():
    text = requirements_block(check_brief({**TOOL_ARGS, "travellers": 4, "own_accommodation": True}))
    assert '"stay": null' in text and "Where to stay" in text and "rooms" not in text
    assert requirements_block(check_brief(TOOL_ARGS)) == ""


async def test_the_stay_team_does_not_run():
    llm = ResearchLlm()
    state = await _run_research({"trip_brief": dict(OWN)}, llm)
    assert not {"AIRBNB SEARCHER", "HOTEL SEARCHER", "STAY COMPARER"} & set(llm.asked)
    assert "CULTURE RESEARCHER" in llm.asked and len(llm.asked) == 5  # 2 transport searchers, comparer, culture, food
    summary = extract_json(state["stay_summary"])
    assert summary["cheapest"] is None and "own place" in summary["notes"]
    assert extract_json(state["hotel_options"])["results"] == []


def test_a_planned_stay_is_removed_and_not_costed():
    plan = fixed_plan()  # has "Cheap Guesthouse", €150
    state = {**_state(plan), "trip_brief": {**_state(plan)["trip_brief"], "own_accommodation": True}}
    tidy_itinerary(SimpleNamespace(state=state))
    plan = extract_json(state["itinerary"])
    assert plan["stay"] is None and "own place" in plan["stay_why"]
    assert plan["cost"]["categories_eur"]["stay"] == 0
    assert any("removed" in c for c in state["itinerary_tidy_changes"])
    report = check_plan(plan, state)
    assert not {x["issue"] for x in report["must_fix"] + report["should_fix"]} & {"ungrounded_stay", "far_stay"}
    assert "**Stay:** your own place" in plan_summary(plan, state["trip_brief"])


def test_turning_accommodation_on_or_off_needs_the_stay_research():
    state = _finished()
    assert "stay" in replan.scope_problem(state, BRIEF, OWN, [])
    assert replan.scope_problem(state, BRIEF, OWN, ["stay"]) is None


def test_the_concierge_is_pointed_to_the_field_when_it_writes_a_requirement_instead():
    tool = SimpleNamespace(name="save_trip_brief")
    args = {**TOOL_ARGS, "requirements": ["staying in own apartment (no accommodation needed)"]}
    reply = require_confirmation(tool, args, _ctx({}))
    assert reply["status"] == "needs_confirmation" and "set own_accommodation true" in reply["reason"]
    reply = require_confirmation(tool, {**TOOL_ARGS, "own_accommodation": True}, _ctx({}, "inv-9"))
    assert "own_accommodation" not in reply["reason"]
    assert "own_accommodation" not in require_confirmation(tool, {**TOOL_ARGS, "requirements": ["vegetarian"]},
                                                           _ctx({}, "inv-8"))["reason"]


def test_the_field_is_offered_to_the_concierge():
    from travel_planner.agents.concierge import build_concierge
    from google.adk.agents import LlmAgent
    concierge = build_concierge(LlmAgent(name="planning_pipeline", model="x", instruction="Plan."))
    save = next(t for t in concierge.tools if getattr(t, "__name__", "") == "save_trip_brief")
    assert "own_accommodation: true only if the travellers need no place to stay" in " ".join(save.__doc__.split())
