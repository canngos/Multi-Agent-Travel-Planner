"""REVIEW.md A1/A5: the concierge's change router (request_replan), validated in code, and branch skipping."""
import json
from types import SimpleNamespace

import pytest
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import Field

from travel_planner.agents import replan
from travel_planner.agents.concierge import (PENDING_KEY, _shown, build_concierge, concierge_instruction,
                                             require_confirmation)
from travel_planner.agents.pipeline import reset_plan_state
from travel_planner.agents.teams import RESEARCH_KEYS, build_research_team
from travel_planner.tools.trip import check_brief

BRIEF = {"origin": "Helsinki", "destination": "Lisbon", "start_date": "2027-05-10", "end_date": "2027-05-14",
         "travellers": 2, "budget_tier": "mid", "total_budget_eur": 2500.0, "interests": ["history"],
         "destination_lat": 38.72, "destination_lon": -9.14, "destination_country": "Portugal",
         "origin_lat": 60.17, "origin_lon": 24.94, "nights": 4, "days": 5}


def _old(key):
    return "```json\n" + json.dumps({"results": [{"name": f"old {key}"}], "notes": "old"}) + "\n```"


def _finished(**brief_changes) -> dict:
    """State after a finished plan; trip_brief may already hold a newer saved brief."""
    state = {k: _old(k) for k in RESEARCH_KEYS}
    state.update(trip_brief={**BRIEF, **brief_changes}, planned_brief=dict(BRIEF), itinerary="old plan",
                 final_guide="# Lisbon", review_log=[{"round": 1, "verdict": "APPROVED"}],
                 research_link_fixes={"culture_findings": {"removed": ["https://x"]}, "stay_summary": {"removed": []}})
    return state


def _ctx(state, invocation="inv-2"):
    return SimpleNamespace(state=state, invocation_id=invocation, actions=SimpleNamespace(transfer_to_agent=None))


def _pending(**changes):
    return {"invocation_id": "inv-1", "brief": _shown(check_brief({**BRIEF, **changes}))}


# ---------------------------------------------------------------- code validates the agent's scope

@pytest.mark.parametrize("changes, rerun, refused", [
    ({}, ["stay"], None),
    ({"pace": "relaxed", "day_start": "08:00"}, [], None),
    ({"interests": ["history", "museums"]}, ["culture"], None),
    ({"destination": "Porto"}, ["stay", "culture", "food"], "new trip"),
    ({"end_date": "2027-05-15"}, ["stay"], "new trip"),
    ({"travellers": 3}, ["stay"], "new trip"),
    ({"origin": "Tampere"}, ["transport"], "new trip"),
    ({"avoid_modes": ["flight"]}, [], "transport"),
    ({"pace": "packed"}, ["food"], "packed"),
    ({}, ["hotels"], "Unknown"),
])
def test_scope_problem_refuses_only_unsafe_scopes(changes, rerun, refused):
    state = _finished()
    problem = replan.scope_problem(state, BRIEF, {**BRIEF, **changes}, rerun)
    assert (problem is None) if refused is None else (refused in problem)


def test_scope_is_refused_without_a_finished_plan():
    state = {**_finished(), "food_findings": None}
    assert "no finished plan" in replan.scope_problem(state, BRIEF, BRIEF, [])
    assert "no finished plan" in replan.scope_problem({**_finished(), "review_log": None}, BRIEF, BRIEF, [])
    assert "no finished plan" in replan.scope_problem(_finished(), None, BRIEF, [])


def test_same_brief_ignores_case_order_and_empty_fields():
    shown = _shown(check_brief({**BRIEF, "interests": ["History"], "destination": "LISBON"}))
    assert replan.same_brief(shown, BRIEF)
    assert not replan.same_brief(shown, {**BRIEF, "pace": "relaxed"})
    assert replan.same_brief({**BRIEF, "destination_lat": 38.74}, BRIEF)  # the same place geocoded twice


# ---------------------------------------------------------------- the tool

TOOL_ARGS = {k: v for k, v in BRIEF.items() if k not in ("nights", "days")}  # what save_trip_brief is called with


def test_an_accepted_scope_never_starts_planning_itself():
    state = _finished()
    ctx = _ctx(state)
    result = replan.request_replan(["stay"], ctx, change_note="a cheaper hotel", reason="only the stay changes")
    assert result["status"] == "ok" and result["results"][0]["reused"] == ["transport", "culture", "food"]
    assert "save_trip_brief" in result["next_step"] and ctx.actions.transfer_to_agent is None
    assert state["replan_scope"]["rerun"] == ["stay"] and state["replan_scope"]["invocation_id"] == "inv-2"
    assert state["replan_log"][-1]["accepted"] is True


def test_save_after_an_accepted_scope_needs_no_second_confirmation_for_a_kept_trip():
    """Regression: a recording confirmed "a relaxed pace" in words; the brief must still be saved with it."""
    state = _finished()
    replan.request_replan(["stay"], _ctx(state), change_note="cheaper stay")
    tool = SimpleNamespace(name="save_trip_brief")
    assert require_confirmation(tool, {**TOOL_ARGS, "pace": "relaxed"}, _ctx(state)) is None  # saved now
    other_turn = require_confirmation(tool, {**TOOL_ARGS, "pace": "relaxed"}, _ctx(state, "inv-3"))
    assert other_turn["status"] == "needs_confirmation"  # the scope was accepted in an earlier turn
    new_trip = require_confirmation(tool, {**TOOL_ARGS, "destination": "Porto"}, _ctx(state))
    assert new_trip["status"] == "needs_confirmation"  # a new city is always confirmed first


def test_unsafe_scope_is_refused_with_a_reason_and_logged():
    state = {**_finished(), PENDING_KEY: _pending(avoid_modes=["flight"])}
    ctx = _ctx(state)
    result = replan.request_replan(["stay"], ctx, reason="guess")
    assert result["status"] == "error" and "transport" in result["reason"]
    assert ctx.actions.transfer_to_agent is None and not state.get("replan_scope")
    assert state["replan_log"] == [{"rerun": ["stay"], "change_note": "", "reason": "guess", "accepted": False,
                                    "refused": result["reason"]}]


# ---------------------------------------------------------------- the pipeline uses the accepted scope

def _start_plan(state, invocation="inv-2"):
    reset_plan_state(SimpleNamespace(state=state, invocation_id=invocation))
    return state


def test_stay_only_change_keeps_the_other_research():
    state = _finished()
    replan.request_replan(["stay"], _ctx(state), change_note="a cheaper hotel")
    _start_plan(state)
    assert state["research_reused"] == ["transport", "culture", "food"]
    assert all(state[k] is None for k in ("airbnb_options", "hotel_options", "stay_summary"))
    assert state["culture_findings"] == _old("culture_findings") and state["transport_summary"]
    assert state["research_link_fixes"] == {"culture_findings": {"removed": ["https://x"]}}
    assert state["itinerary"] is None and state["final_guide"] is None  # the days are always planned again
    assert "a cheaper hotel" in state["plan_change_note"] and state["replan_scope"] is None


def test_rhythm_only_change_keeps_all_research():
    state = {**_finished(), PENDING_KEY: _pending(day_start="07:00")}
    replan.request_replan([], _ctx(state))
    state["trip_brief"] = {**BRIEF, "day_start": "07:00"}  # save_trip_brief, after the confirmation
    _start_plan(state)
    assert state["research_reused"] == ["transport", "stay", "culture", "food"]
    assert all(state[k] for k in RESEARCH_KEYS) and state["plan_change_note"] is None
    assert state["planned_brief"]["day_start"] == "07:00"


def test_without_a_scope_or_with_a_stale_one_everything_is_researched_again():
    state = _start_plan(_finished(pace="relaxed"))  # the concierge saved a new brief but asked for no scope
    assert state["research_reused"] is None and all(state[k] is None for k in RESEARCH_KEYS)

    state = {**_finished(), PENDING_KEY: _pending(day_start="07:00")}
    replan.request_replan([], _ctx(state, "inv-1"))
    state["trip_brief"] = {**BRIEF, "day_start": "08:00"}  # not the brief the scope was checked against ...
    _start_plan(state, "inv-5")  # ... and a later turn
    assert state["research_reused"] is None and state["culture_findings"] is None
    assert "not the one the scope was checked against" in state["replan_log"][-1]["refused"]


class ResearchLlm(BaseLlm):
    """Every research agent answers at once with new (empty) results; records who was asked."""
    model: str = "research"
    asked: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        self.asked.append(str(llm_request.config.system_instruction).split(" of a trip")[0].split("the ")[-1])
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(
            text='```json\n{"results": [], "notes": "new"}\n```')]))


def _with_model(agent, llm):
    if isinstance(agent, LlmAgent):
        agent.model = llm
    for sub in agent.sub_agents:
        _with_model(sub, llm)
    return agent


async def _run_research(state, llm) -> dict:
    pipeline = SequentialAgent(name="planning_pipeline", sub_agents=[_with_model(build_research_team(), llm)],
                               before_agent_callback=reset_plan_state)
    runner = InMemoryRunner(agent=pipeline, app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u", state=state)
    async for _ in runner.run_async(user_id="u", session_id=session.id,
                                    new_message=types.Content(role="user", parts=[types.Part(text="Plan.")])):
        pass
    state = (await runner.session_service.get_session(app_name="t", user_id="u", session_id=session.id)).state
    await runner.close()
    return state


async def test_culture_only_change_runs_only_the_culture_researcher():
    state = _finished(interests=["history", "museums"])
    state[PENDING_KEY] = None
    state["replan_scope"] = {"rerun": ["culture"], "change_note": "more museums", "reason": "x",
                             "base": dict(BRIEF), "target": state["trip_brief"]}
    llm = ResearchLlm()
    state = await _run_research(state, llm)
    assert llm.asked == ["CULTURE RESEARCHER"]
    assert json.loads(state["culture_findings"].strip("`json\n"))["notes"] == "new"
    for key in ("food_findings", "transport_summary", "flight_options", "stay_summary", "airbnb_options"):
        assert state[key] == _old(key), key  # skipped branches keep their results unchanged


async def test_full_plan_runs_every_researcher():
    llm = ResearchLlm()
    await _run_research({"trip_brief": dict(BRIEF)}, llm)
    assert len(llm.asked) == 6  # 4 searchers, culture, food (code compares the options)


# ---------------------------------------------------------------- the concierge decides

class ConciergeLlm(BaseLlm):
    """The user confirmed "a relaxed pace and a cheaper hotel": scope, then save with the new pace."""
    model: str = "concierge"
    seen: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        system = str(llm_request.config.system_instruction or "")
        done = [p.function_response.name for c in llm_request.contents for p in c.parts or [] if p.function_response]
        if system.startswith("Plan."):
            self.seen.append("pipeline")
            part = types.Part(text="planning")
        elif not done:
            part = types.Part(function_call=types.FunctionCall(name="request_replan", args={
                "rerun": ["stay"], "change_note": "a cheaper hotel", "reason": "only the stay changes"}))
        elif done[-1] == "request_replan":
            part = types.Part(function_call=types.FunctionCall(name="save_trip_brief",
                                                               args={**TOOL_ARGS, "pace": "relaxed"}))
        else:
            part = types.Part(text="Re-planning with a cheaper stay.")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


async def test_concierge_request_starts_the_pipeline_with_its_scope():
    llm = ConciergeLlm()
    concierge = build_concierge(LlmAgent(name="planning_pipeline", model=llm, instruction="Plan."))
    concierge.model = llm
    runner = InMemoryRunner(agent=concierge, app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u", state=_finished())
    async for _ in runner.run_async(user_id="u", session_id=session.id, new_message=types.Content(
            role="user", parts=[types.Part(text="Yes, a relaxed pace and a cheaper hotel, please.")])):
        pass
    state = (await runner.session_service.get_session(app_name="t", user_id="u", session_id=session.id)).state
    await runner.close()
    assert llm.seen == ["pipeline"] and state["trip_brief"]["pace"] == "relaxed"
    assert state["replan_scope"]["rerun"] == ["stay"]  # waiting for the real pipeline's reset_plan_state


def test_concierge_is_told_how_to_scope_a_change():
    concierge = build_concierge(LlmAgent(name="planning_pipeline", model="x", instruction="Plan."))
    assert "request_replan" in {t.__name__ for t in concierge.tools}
    text = concierge_instruction(SimpleNamespace(state={}))
    assert "CHANGES TO A FINISHED PLAN" in text and '"a cheaper hotel" -> ["stay"]' in text


# ---------------------------------------------------------------- A5: agents that cannot help are skipped

def test_airbnb_searcher_is_skipped_without_npx_but_never_offline(monkeypatch):
    from travel_planner.agents import stay
    monkeypatch.setattr(stay.shutil, "which", lambda name: None)
    state = {}
    monkeypatch.setenv("OFFLINE", "1")
    assert stay.skip_without_npx(SimpleNamespace(state=state)) is None  # the recorded answers need no npx
    monkeypatch.setenv("OFFLINE", "0")
    content = stay.skip_without_npx(SimpleNamespace(state=state))
    assert "Node.js" in content.parts[0].text and json.loads(state["airbnb_options"].strip("`json\n"))["results"] == []
    monkeypatch.setattr(stay.shutil, "which", lambda name: "C:/node/npx.cmd")
    assert stay.skip_without_npx(SimpleNamespace(state={})) is None
