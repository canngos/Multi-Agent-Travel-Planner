"""Phase 9 regressions found by the four test trips (no network, no model)."""
import json
from datetime import date
from types import SimpleNamespace

from travel_planner.agents import guide_writer
from travel_planner.agents.grounding import ground_url, known_urls
from travel_planner.tools import transit


def _option(mode, operator, depart, arrive):
    return {"mode": mode, "operator": operator, "depart": depart, "arrive": arrive, "duration_min": 160, "stops": 2,
            "price_eur": None, "price_source": None, "source": "transitous", "url": "https://transitous.org/",
            "details": f"{mode} ({operator}): Helsinki → Tallinn"}


def test_typical_day_is_the_same_weekday_inside_the_timetable():
    trip = date(2027, 5, 12)  # a Wednesday
    near = transit.typical_day(trip, date(2026, 11, 17))
    assert near.weekday() == trip.weekday() and 14 <= (near - date(2026, 11, 17)).days < 21


async def test_far_trip_adds_typical_ferries_with_moved_dates(monkeypatch):
    """Regression: for May 2027 Transitous knew only FlixBus, not the ferries (timetables not published yet)."""
    async def fake_query(from_lat, from_lon, to_lat, to_lon, depart_date, depart_time, n):
        if depart_date == "2027-05-12":
            return [_option("bus", "FlixBus-eu", "2027-05-12T14:38", "2027-05-12T18:43")]
        return [_option("ferry", "Tallink Grupp AS", f"{depart_date}T10:30", f"{depart_date}T12:30"),
                _option("bus", "FlixBus-eu", f"{depart_date}T14:38", f"{depart_date}T18:43")]

    monkeypatch.setattr(transit, "_query", fake_query)
    monkeypatch.setattr(transit.config, "today", lambda: date(2026, 11, 17))
    r = await transit.plan_public_transport(60.17, 24.94, 59.44, 24.75, "2027-05-12", "07:00", 3)
    assert r["status"] == "ok" and [o["operator"] for o in r["results"]] == ["FlixBus-eu", "Tallink Grupp AS"]
    ferry = r["results"][1]
    assert ferry["depart"] == "2027-05-12T10:30" and ferry["arrive"] == "2027-05-12T12:30"
    assert "TYPICAL TIMETABLE: times from 2026-12-02" in ferry["details"]

    monkeypatch.setattr(transit.config, "today", lambda: date(2027, 5, 1))  # near trip: no typical query
    r = await transit.plan_public_transport(60.17, 24.94, 59.44, 24.75, "2027-05-12", "07:00", 3)
    assert [o["operator"] for o in r["results"]] == ["FlixBus-eu"]


def test_ground_url_repairs_a_doubled_scheme():
    known = known_urls({"flight_options": json.dumps({"url": "https://kiwi.com/u/57kzszz"})})
    assert ground_url("https://https://kiwi.com/u/57kzszz", known) == "https://kiwi.com/u/57kzszz"


def test_safety_net_saves_a_save_guide_call_written_as_text(tmp_path, monkeypatch):
    """Regression: the writer printed {"filename_slug": ..., "markdown_text": ...} instead of calling save_guide."""
    from travel_planner import config
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    guide = "# Tallinn trip guide\n\nIntro.\n\n## Getting there and back\nFerry.\n\n## Day by day\nDay 1."
    reply = json.dumps({"filename_slug": "tallinn", "markdown_text": guide})
    state = {"guide_reply": reply, "trip_brief": {"destination": "Tallinn", "start_date": "2027-05-12",
                                                  "end_date": "2027-05-14"}}
    ctx = SimpleNamespace(state=state, _invocation_context=SimpleNamespace(artifact_service=None,
                                                                         session=SimpleNamespace(state=state)))
    content = guide_writer.save_reply_if_not_saved(ctx)
    assert content is not None and state["final_guide"].startswith("# Tallinn trip guide")
    assert (tmp_path / "tallinn-2027-05-12-to-2027-05-14.md").exists()


async def test_geocode_falls_back_to_open_meteo(monkeypatch):
    from travel_planner.tools import geo
    from travel_planner.tools._result import ToolError

    async def fake_get_json(url, *, namespace, params=None, **kw):
        if namespace == "nominatim":
            raise ToolError("nominatim.openstreetmap.org: HTTP 429: Too many requests")
        assert params["name"] == "Tampere"
        return {"results": [{"name": "Tampere", "latitude": 61.49911, "longitude": 23.78712, "country": "Finland",
                             "country_code": "FI", "admin1": "Pirkanmaa"}]}

    monkeypatch.setattr(geo, "get_json", fake_get_json)
    r = await geo.geocode_place("Tampere, Finland")
    assert r["status"] == "ok" and r["results"][0]["country"] == "Finland" and r["results"][0]["lat"] == 61.49911


async def test_concierge_cannot_restart_planning_without_a_saved_brief():
    """Regression: a follow-up question made the concierge transfer to the pipeline again."""
    from google.adk.agents import LlmAgent
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types
    from pydantic import Field

    from travel_planner.agents.concierge import build_concierge

    class Scripted(BaseLlm):
        model: str = "scripted"
        seen: list = Field(default_factory=list)

        async def generate_content_async(self, llm_request, stream=False):
            results = [p.function_response for c in llm_request.contents for p in c.parts or [] if p.function_response]
            self.seen.append(results[-1].response if results else None)
            if not results:
                part = types.Part(function_call=types.FunctionCall(name="transfer_to_agent",
                                                                   args={"agent_name": "planning_pipeline"}))
            else:
                part = types.Part(text="There is no guide yet; shall I plan again?")
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    pipeline_llm = Scripted()
    concierge = build_concierge(LlmAgent(name="planning_pipeline", model=pipeline_llm, instruction="Plan."))
    concierge.model = Scripted()
    runner = InMemoryRunner(agent=concierge, app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u",
                                                          state={"trip_brief": {"destination": "Rome"}})
    async for _ in runner.run_async(user_id="u", session_id=session.id, new_message=types.Content(
            role="user", parts=[types.Part(text="On which day do we see the Colosseum?")])):
        pass
    await runner.close()
    assert pipeline_llm.seen == []  # the pipeline never ran
    assert concierge.model.seen[-1]["status"] == "error" and "save_trip_brief" in concierge.model.seen[-1]["reason"]


def test_tidy_restores_the_chosen_options_from_the_summaries():
    """Regression: the model mistyped a Kiwi link, grounding removed it, and the return flight had url null."""
    from travel_planner.agents.itinerary_tidy import restore_chosen_options
    ret = {"mode": "flight", "operator": "Finnair", "depart": "2027-06-09T18:45", "arrive": "2027-06-09T23:10",
           "duration_min": 205, "stops": 0, "price_eur": 231.0, "price_source": "live", "source": "kiwi",
           "url": "https://kiwi.com/u/wh8w2w", "details": "FCO → HEL"}
    stay = {"kind": "airbnb", "name": "Casa Ludovico", "total_price_eur": 744.0, "url": "https://www.airbnb.com/rooms/1"}
    state = {"transport_summary": json.dumps({"return": {"cheapest": ret, "ranked": [ret]}}),
             "stay_summary": json.dumps({"best_value": stay})}
    plan = {"transport": {"outbound": None, "return": {**ret, "url": None, "details": "shortened"}},
            "stay": {**stay, "url": "https://airbnb.com/rooms/1/"}}
    changes = restore_chosen_options(plan, state)
    assert plan["transport"]["return"] == ret and plan["stay"] == stay and len(changes) == 2


async def test_saved_brief_starts_the_pipeline_without_a_transfer_call():
    """Regression: the model said "planning starts" but never called transfer_to_agent."""
    from google.adk.agents import LlmAgent
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types
    from pydantic import Field

    from types import SimpleNamespace

    from travel_planner.agents.concierge import build_concierge, require_confirmation

    brief = {"destination": "Lisbon", "start_date": "2027-05-10", "end_date": "2027-05-14", "origin": "Helsinki",
             "travellers": 2, "budget_tier": "mid", "total_budget_eur": 2500.0}

    class Scripted(BaseLlm):
        model: str = "scripted"
        seen: list = Field(default_factory=list)

        async def generate_content_async(self, llm_request, stream=False):
            self.seen.append(str(llm_request.config.system_instruction or "")[:20])
            if "Plan." in str(llm_request.config.system_instruction):
                part = types.Part(text="planning done")
            else:
                part = types.Part(function_call=types.FunctionCall(name="save_trip_brief", args=brief))
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    pipeline_llm = Scripted()
    concierge = build_concierge(LlmAgent(name="planning_pipeline", model=pipeline_llm, instruction="Plan."))
    concierge.model = Scripted()
    runner = InMemoryRunner(agent=concierge, app_name="t")
    earlier = {}  # the user saw the summary last turn
    require_confirmation(SimpleNamespace(name="save_trip_brief"), dict(brief),
                         SimpleNamespace(state=earlier, invocation_id="an-earlier-turn"))
    session = await runner.session_service.create_session(app_name="t", user_id="u", state=earlier)
    async for _ in runner.run_async(user_id="u", session_id=session.id, new_message=types.Content(
            role="user", parts=[types.Part(text="Yes, go ahead.")])):
        pass
    state = (await runner.session_service.get_session(app_name="t", user_id="u", session_id=session.id)).state
    await runner.close()
    assert state["trip_brief"]["destination"] == "Lisbon"
    assert len(pipeline_llm.seen) == 1  # the pipeline ran right after the save, with no transfer_to_agent call
