"""REVIEW.md A2: the reviser researches a thin day on demand (agent as tool); its sights count as researched."""
import json
from types import SimpleNamespace

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import Field

from tests.test_review import _item, _state, fixed_plan
from travel_planner.agents.extra_sights import TOOL_NAME, budget
from travel_planner.agents.itinerary_reviser import build_itinerary_reviser
from travel_planner.agents.output import extract_json
from travel_planner.agents.review_checks import check_plan

FADO = {"kind": "sight", "name": "Museu do Fado", "lat": 38.7110, "lon": -9.1290, "summary": "Museum of fado music.",
        "budget_tier": None, "hours": "Tu-Su 10:00-18:00", "ticket_eur": 5.0, "indoor": True,
        "source_urls": ["https://example.org/fado", "https://invented.example/fado"]}


def find_sights(lat: float, lon: float) -> dict:
    """Stands in for the real find_sights (no network)."""
    return {"status": "ok", "results": [{"name": "Museu do Fado", "url": "https://example.org/fado"}]}


def thin_plan() -> dict:
    plan = fixed_plan()
    plan.pop("changes")
    return plan


def plan_with_fado() -> dict:
    plan = thin_plan()
    plan["days"][2]["items"].insert(1, _item("11:30", "sight", "Museu do Fado", 5.0, FADO["lat"], FADO["lon"]))
    plan["changes"] = ["Day 3: added Museu do Fado (researched on demand)"]
    return plan


class OnDemandLlm(BaseLlm):
    """The reviser asks for sights near Alfama, the researcher finds one, the reviser adds it."""
    model: str = "on-demand"
    calls: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        system = str(llm_request.config.system_instruction or "")
        results = [p.function_response for c in llm_request.contents for p in c.parts or [] if p.function_response]
        if "EXTRA SIGHTS RESEARCHER" in system:
            self.calls.append("researcher")
            if not results:
                part = types.Part(function_call=types.FunctionCall(name="find_sights", args={"lat": 38.71, "lon": -9.13}))
            else:
                part = types.Part(text="```json\n" + json.dumps({"results": [FADO], "notes": "ok"}) + "\n```")
        else:
            self.calls.append("reviser")
            if not results:
                part = types.Part(function_call=types.FunctionCall(name=TOOL_NAME, args={
                    "area": "Alfama", "lat": 38.7102, "lon": -9.1326, "count": 1}))
            else:
                part = types.Part(text="```json\n" + json.dumps(plan_with_fado(), ensure_ascii=False) + "\n```")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


async def test_reviser_researches_a_thin_day_and_the_new_sight_is_grounded():
    llm = OnDemandLlm()
    reviser = build_itinerary_reviser(llm)
    (tool,) = [t for t in reviser.tools if getattr(t, "name", "") == TOOL_NAME]
    tool.agent.model = llm
    tool.agent.tools = [find_sights]
    state = {**_state(thin_plan()), "review_verdict": "REVISE", "review_round": 1,
             "critique": "SHOULD FIX:\n1. Day 3: thin day, no alternatives.\nVERDICT: REVISE"}
    runner = InMemoryRunner(agent=reviser, app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u", state=state)
    async for _ in runner.run_async(user_id="u", session_id=session.id,
                                    new_message=types.Content(role="user", parts=[types.Part(text="Revise.")])):
        pass
    state = (await runner.session_service.get_session(app_name="t", user_id="u", session_id=session.id)).state
    await runner.close()

    assert llm.calls == ["reviser", "researcher", "researcher", "reviser"]
    assert state["extra_sights_log"] == [{"round": 1, "area": "Alfama", "added": ["Museu do Fado"], "already_known": 0}]
    culture = extract_json(state["culture_findings"])["results"]
    fado = [p for p in culture if p["name"] == "Museu do Fado"][0]
    assert fado["source_urls"] == ["https://example.org/fado"]  # the link no tool returned is gone
    plan = extract_json(state["itinerary"])
    assert any(i["name"] == "Museu do Fado" for i in plan["days"][2]["items"])
    report = check_plan(plan, state)
    assert not [x for x in report["must_fix"] + report["should_fix"]
                if x["issue"] in ("ungrounded_place", "ungrounded_price") and "Fado" in x["detail"]]


def test_on_demand_research_is_limited_per_round():
    tool = SimpleNamespace(name=TOOL_NAME)
    ctx = SimpleNamespace(state={"review_round": 1}, invocation_id="inv-budget", agent_name="itinerary_reviser")
    assert budget(tool, {}, ctx) is None and budget(tool, {}, ctx) is None
    refused = budget(tool, {}, ctx)
    assert refused["status"] == "error" and "at most 2" in refused["reason"]
    assert budget(SimpleNamespace(name="exit_loop"), {}, ctx) is None  # other tools are not counted
    ctx.state["review_round"] = 2  # a new round, a new budget
    assert budget(tool, {}, ctx) is None


def test_reviser_is_told_when_to_research_on_demand():
    reviser = build_itinerary_reviser()
    assert [getattr(t, "name", getattr(t, "__name__", "")) for t in reviser.tools] == ["exit_loop", TOOL_NAME]
    assert "NO listed alternative: you may call extra_sights_researcher" in reviser.instruction
