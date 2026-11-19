"""Research agents: construction, instruction templating, and the output checker (no model calls)."""
from types import SimpleNamespace

import pytest
from google.adk.utils.instructions_utils import inject_session_state

from travel_planner.agents import RESEARCH_AGENTS
from travel_planner.agents.output import check_output, extract_json, item_urls
from travel_planner.tools.mcp import CachedMcpToolset, mcp_cache_after, mcp_cache_before

EXPECTED = {
    "culture_agent": ("culture_findings", {"find_sights", "wikivoyage_section", "find_sight_hours", "wiki_summary",
                                           "web_search", "geocode_place"}),
    "food_agent": ("food_findings", {"local_dishes", "web_search", "wikivoyage_section", "find_restaurants", "geocode_place"}),
    "flight_searcher": ("flight_options", {"kiwi", "search_google_flights", "search_serpapi_flights"}),
    "ground_searcher": ("ground_options", {"plan_public_transport", "web_search", "geocode_place"}),
    "airbnb_searcher": ("airbnb_options", {"airbnb", "stay_deep_links"}),
    "hotel_searcher": ("hotel_options", {"search_google_hotels", "search_hotel_rates", "stay_deep_links"}),
}


def _tool_names(agent) -> set[str]:
    return {t.server if isinstance(t, CachedMcpToolset) else t.__name__ for t in agent.tools}


@pytest.mark.parametrize("name", RESEARCH_AGENTS)
def test_agent_construction(name):
    agent = RESEARCH_AGENTS[name]()
    key, tools = EXPECTED[name]
    assert agent.name == name and agent.output_key == key and agent.include_contents == "none"
    assert _tool_names(agent) == tools
    assert agent.instruction.count("{trip_brief}") == 1
    uses_mcp = any(isinstance(t, CachedMcpToolset) for t in agent.tools)
    if uses_mcp:  # MCP agents must cache/normalise through the callbacks
        assert mcp_cache_before in agent.canonical_before_tool_callbacks and agent.after_tool_callback is mcp_cache_after


def test_factories_return_fresh_agents():
    """An ADK agent can have only one parent, so the harness and the pipeline need separate instances."""
    assert RESEARCH_AGENTS["culture_agent"]() is not RESEARCH_AGENTS["culture_agent"]()


@pytest.mark.parametrize("name", RESEARCH_AGENTS)
async def test_instruction_renders_brief_and_keeps_json_examples(name):
    agent = RESEARCH_AGENTS[name]()
    state = {"trip_brief": {"destination": "Lisbon", "start_date": "2027-05-10"}}
    ctx = SimpleNamespace(state=state, _invocation_context=SimpleNamespace(
        artifact_service=None, session=SimpleNamespace(state=state)))
    rendered = await inject_session_state(agent.instruction, ctx)
    assert "'destination': 'Lisbon'" in rendered and "{trip_brief}" not in rendered
    assert '"notes"' in rendered and '{"' in rendered  # JSON shape and example survive templating


def test_output_keys_are_unique():
    keys = [RESEARCH_AGENTS[n]().output_key for n in RESEARCH_AGENTS]
    assert len(keys) == len(set(keys))


# ---------------------------------------------------------------- output checker

STAY = {"kind": "hotel", "name": "H", "area": "Baixa", "lat": 38.71, "lon": -9.14, "price_per_night_eur": 50,
        "total_price_eur": 200, "rating": 4.3, "price_source": "live", "source": "x", "url": "https://h.example", "details": ""}


def test_extract_json_prefers_last_fence_and_falls_back_to_bare_object():
    text = 'Draft:\n```json\n{"a": 1}\n```\nFinal:\n```json\n{"a": 2, "s": "x}y"}\n```'
    assert extract_json(text) == {"a": 2, "s": "x}y"}
    assert extract_json('Here: {"b": [1, 2]} done') == {"b": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_check_output_valid_and_invalid():
    data, problems = check_output("hotel_options", '```json\n{"results": [%s], "notes": "ok"}\n```' % __import__("json").dumps(STAY))
    assert problems == [] and data["results"][0]["name"] == "H"
    bad = {**STAY, "price_source": "guess"}
    _, problems = check_output("hotel_options", __import__("json").dumps({"results": [bad], "notes": "x"}))
    assert problems and problems[0].startswith("results[0]: price_source")
    _, problems = check_output("flight_options", '{"outbound": [], "notes": "none"}')
    assert problems == ["'return' is missing or not a list"]
    _, problems = check_output("food_findings", "Sorry, I could not do it.")
    assert problems == ["no JSON object found in the agent output"]


def test_item_urls_collects_every_link_field():
    data = {"results": [{"url": "u1"}, {"source_urls": ["u2", "u3"]}], "dishes": [{"source_url": "u4"}],
            "fallback_links": ["u0"], "notes": "n"}
    assert item_urls(data) == ["u0", "u1", "u2", "u3", "u4"]
    _, problems = check_output("airbnb_options", '{"results": [], "fallback_links": "u", "notes": ""}')
    assert problems == ["'fallback_links' must be a list of strings"]


# ---------------------------------------------------------------- comparers and teams

from google.adk.agents import LlmAgent, LoopAgent, ParallelAgent, SequentialAgent  # noqa: E402

from travel_planner.agents.comparers import build_stay_comparer, build_transport_comparer  # noqa: E402
from travel_planner.agents.teams import RESEARCH_KEYS, build_research_team  # noqa: E402

FLIGHT = {"mode": "flight", "operator": "SAS", "depart": "2027-05-10T11:15", "arrive": "2027-05-10T16:10",
          "duration_min": 415, "stops": 1, "price_eur": 297.0, "price_source": "live", "source": "kiwi",
          "url": "https://kiwi.com/u/a", "details": ""}


@pytest.mark.parametrize("factory, key, inputs", [
    (build_transport_comparer, "transport_summary", ("flight_options", "ground_options")),
    (build_stay_comparer, "stay_summary", ("airbnb_options", "hotel_options")),
])
async def test_comparers_read_only_their_team_and_render_without_inputs(factory, key, inputs):
    agent = factory()
    assert agent.output_key == key and not agent.tools and agent.include_contents == "none"
    for name in inputs:
        assert "{" + name + "?}" in agent.instruction  # optional: renders empty if a searcher wrote nothing
    state = {"trip_brief": {"destination": "Lisbon"}}
    ctx = SimpleNamespace(state=state, _invocation_context=SimpleNamespace(artifact_service=None,
                                                                          session=SimpleNamespace(state=state)))
    rendered = await inject_session_state(agent.instruction, ctx)
    assert "?}" not in rendered


def _walk(agent):
    yield agent
    for sub in agent.sub_agents:
        yield from _walk(sub)


def test_research_team_structure():
    team = build_research_team()
    assert isinstance(team, ParallelAgent)
    assert [a.name for a in team.sub_agents] == ["transport_team", "stay_team", "culture_agent", "food_agent"]
    transport = team.sub_agents[0]
    assert isinstance(transport, SequentialAgent)
    assert isinstance(transport.sub_agents[0], ParallelAgent)
    assert [a.name for a in transport.sub_agents[0].sub_agents] == ["flight_searcher", "ground_searcher"]
    assert transport.sub_agents[1].name == "transport_comparer"
    keys = [a.output_key for a in _walk(team) if isinstance(a, LlmAgent)]
    assert sorted(keys) == sorted(RESEARCH_KEYS) and len(set(keys)) == len(keys) == 8


def test_check_output_nested_comparer_shapes():
    good = {"outbound": {"cheapest": FLIGHT, "fastest": FLIGHT, "ranked": [FLIGHT]},
            "return": {"cheapest": None, "fastest": None, "ranked": []},
            "recommendation": "x", "total_cheapest_eur": None, "notes": "n"}
    data, problems = check_output("transport_summary", __import__("json").dumps(good))
    assert problems == [] and item_urls(data) == ["https://kiwi.com/u/a"]
    bad = {**good, "return": {"cheapest": {**FLIGHT, "duration_min": -1}, "ranked": "x"}}
    _, problems = check_output("transport_summary", __import__("json").dumps(bad))
    assert "return.cheapest: duration_min: Input should be greater than or equal to 0" in problems
    assert "'return.fastest' is missing" in problems and "'return.ranked' is missing or not a list" in problems
    stay = {"by_area": [{"area": "Baixa", "airbnb": None, "hotel": {**STAY, "lat": 200}}],
            "cheapest": STAY, "best_value": STAY, "top_rated": None, "recommendation": "", "fallback_links": [], "notes": ""}
    _, problems = check_output("stay_summary", __import__("json").dumps(stay))
    assert problems == ["by_area[0].hotel: lat: Input should be less than or equal to 90"]


# ---------------------------------------------------------------- Phase 7: pipeline, planner, writer, concierge

from google.genai import types  # noqa: E402

from travel_planner import agent as entry  # noqa: E402
from travel_planner.agents import concierge as concierge_mod  # noqa: E402
from travel_planner.agents.guide_writer import build_guide_writer, save_reply_if_not_saved  # noqa: E402
from travel_planner.agents.pipeline import build_root_agent  # noqa: E402
from travel_planner.agents.planner import build_itinerary_planner  # noqa: E402


def _ctx(state):
    return SimpleNamespace(state=state, _invocation_context=SimpleNamespace(
        artifact_service=None, session=SimpleNamespace(state=state)))


def test_root_agent_tree():
    root = build_root_agent()
    assert root.name == "concierge" and entry.root_agent.name == "concierge"
    assert {t.__name__ for t in root.tools} == {"geocode_place", "save_trip_brief", "request_replan", "approve_plan"}
    pipeline, writer = root.sub_agents
    assert isinstance(pipeline, SequentialAgent) and pipeline.name == "planning_pipeline"
    assert [a.name for a in pipeline.sub_agents] == ["research_team", "itinerary_planner", "review_loop"]
    assert writer.name == "guide_writer" and writer.disallow_transfer_to_parent and writer.disallow_transfer_to_peers
    loop = pipeline.sub_agents[2]
    assert isinstance(loop, LoopAgent) and loop.max_iterations == 3
    assert [a.name for a in loop.sub_agents] == ["plan_critic", "itinerary_reviser"]
    llm = [a for a in _walk(root) if isinstance(a, LlmAgent)]
    assert len(llm) == 13
    keys = [a.output_key for a in llm if a.output_key]
    # the reviser rewrites the planner's "itinerary" in place (that is the loop's job); every other key is unique
    assert keys.count("itinerary") == 2 and len(keys) - 1 == len(set(keys))
    assert {"itinerary", "critique", "guide_reply"} <= set(keys)


def test_demo_runs_the_same_tree_as_a_live_run(monkeypatch):
    names = lambda root: [a.name for a in _walk(root)]  # noqa: E731
    live = build_root_agent()
    monkeypatch.setenv("DEMO", "1")
    demo = build_root_agent()
    assert names(demo) == names(live) and {t.__name__ for t in demo.tools} == {t.__name__ for t in live.tools}
    def prompts(root):
        return [a.instruction for a in _walk(root) if isinstance(a, LlmAgent) and isinstance(a.instruction, str)]
    assert prompts(demo) == prompts(live)  # no demo-only prompt text: the demo shows the system as it is


@pytest.mark.parametrize("factory, tools, reads", [
    (build_itinerary_planner, {"get_forecast", "cluster_by_distance", "calculate_trip_cost"},
     ("transport_summary", "stay_summary", "culture_findings", "food_findings")),
    (build_guide_writer, {"save_guide", "export_calendar", "export_map_stops"},
     ("itinerary", "transport_summary", "stay_summary", "culture_findings", "food_findings")),
])
async def test_planner_and_writer_read_state(factory, tools, reads):
    agent = factory()
    assert {t.__name__ for t in agent.tools} == tools and agent.include_contents == "none"
    for key in reads:
        assert "{" + key + "?}" in agent.instruction
    state = {"trip_brief": {"destination": "Lisbon"}, "culture_findings": '{"results": ["Belém Tower"]}'}
    rendered = await inject_session_state(agent.instruction, _ctx(state))
    assert "Belém Tower" in rendered and "?}" not in rendered and "{trip_brief}" not in rendered


def test_check_output_itinerary():
    plan = {"transport": {"outbound": FLIGHT, "return": None, "why": ""}, "stay": STAY, "stay_why": "",
            "weather_kind": "forecast", "days": [], "cost": None, "notes": ""}
    assert check_output("itinerary", __import__("json").dumps(plan))[1] == []
    _, problems = check_output("itinerary", __import__("json").dumps({**plan, "days": "x", "stay": {"kind": "tent"}}))
    assert "'days' is missing or not a list" in problems and any(p.startswith("stay: ") for p in problems)


def test_concierge_instruction_has_today_and_guide():
    empty = concierge_mod.concierge_instruction(SimpleNamespace(state={}))
    assert "Today is 20" in empty and "No guide has been made yet" in empty
    done = concierge_mod.concierge_instruction(SimpleNamespace(state={
        "trip_brief": {"destination": "Lisbon"}, "final_guide": "# Lisbon guide", "guide_paths": {"md": "x.md"}}))
    assert "# Lisbon guide" in done and "x.md" in done and "'destination': 'Lisbon'" in done


def test_concierge_hides_pipeline_events():
    user = types.Content(role="user", parts=[types.Part(text="5 days in Lisbon")])
    own = types.Content(role="model", parts=[types.Part(text="Which dates?")])
    other = types.Content(role="user", parts=[types.Part(text="For context: below is a transcript..."),
                                              types.Part(text="[food_agent] said: {...}")])
    req = SimpleNamespace(contents=[user, own, other])
    assert concierge_mod.hide_pipeline_events(None, req) is None
    assert req.contents == [user, own]


def test_guide_writer_safety_net_saves_reply(tmp_path, monkeypatch):
    from travel_planner import config
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    brief = {"destination": "Lisbon", "start_date": "2027-05-10", "end_date": "2027-05-14"}
    guide = "# Lisbon\n\n## Getting there\nx\n\n## Day by day\ny\n"
    ctx = SimpleNamespace(state={"trip_brief": brief, "guide_reply": guide})
    reply = save_reply_if_not_saved(ctx)
    assert "lisbon-2027-05-10-to-2027-05-14.md" in reply.parts[0].text
    assert ctx.state["final_guide"].startswith("# Lisbon")
    # already saved by the tool, or the reply is not a guide: do nothing
    assert save_reply_if_not_saved(ctx) is None
    assert save_reply_if_not_saved(SimpleNamespace(state={"trip_brief": brief, "guide_reply": "Saved."})) is None


# ---------------------------------------------------------------- link grounding

from travel_planner.agents import grounding  # noqa: E402


def _research_state():
    culture = {"results": [{"name": "Belém Tower", "source_urls": ["https://torrebelem.com/en/tour/tickets",
                                                                     "http://www.carris.pt/en/elevators/"]}]}
    return {"culture_findings": "```json\n" + __import__("json").dumps(culture) + "\n```",
            "transport_summary": {"outbound": {"cheapest": {"url": "https://kiwi.com/u/a"}}}}


def test_ground_url_restores_spelling_and_rejects_unknown():
    known = grounding.known_urls(_research_state())
    assert grounding.ground_url("https://www.torrebelem.com/en/tour/tickets/", known) == "https://torrebelem.com/en/tour/tickets"
    assert grounding.ground_url("https://carris.pt/en/elevators", known) == "http://www.carris.pt/en/elevators/"
    assert grounding.ground_url("https://kiwi.com/u/a", known) == "https://kiwi.com/u/a"
    assert grounding.ground_url("https://made-up.example/lisbon", known) is None


def test_ground_markdown_fixes_and_unlinks():
    known = grounding.known_urls(_research_state())
    text = ("[Belém](https://www.torrebelem.com/en/tour/tickets) and [fake](https://made-up.example/x) "
            "see https://carris.pt/en/elevators/. Footer [Transitous](https://transitous.org/sources/)")
    out, fixed, removed = grounding.ground_markdown(text, known)
    assert "[Belém](https://torrebelem.com/en/tour/tickets)" in out
    assert "fake" in out and "made-up.example" not in out and removed == ["https://made-up.example/x"]
    assert "see http://www.carris.pt/en/elevators/." in out and len(fixed) == 2
    assert "(https://transitous.org/sources/)" in out


def test_ground_itinerary_callback_rewrites_state():
    plan = {"days": [{"items": [{"name": "Belém Tower", "source_url": "https://www.torrebelem.com/en/tour/tickets"},
                                {"name": "Invented", "source_url": "https://made-up.example/y"}]}], "notes": "ok."}
    state = _research_state() | {"itinerary": "```json\n" + __import__("json").dumps(plan) + "\n```"}
    assert grounding.ground_itinerary(SimpleNamespace(state=state)) is None
    items = extract_json(state["itinerary"])["days"][0]["items"]
    assert items[0]["source_url"] == "https://torrebelem.com/en/tour/tickets" and items[1]["source_url"] is None
    assert "Removed 1 link" in extract_json(state["itinerary"])["notes"]
    assert state["itinerary_link_fixes"]["removed"] == ["https://made-up.example/y"]


def test_ground_guide_before_save_edits_tool_args():
    state = _research_state()
    args = {"markdown_text": "[x](https://made-up.example/z) [k](https://www.kiwi.com/u/a)", "filename_slug": "s"}
    tool = SimpleNamespace(name="save_guide")
    assert grounding.ground_guide_before_save(tool, args, SimpleNamespace(state=state)) is None
    assert args["markdown_text"] == "x [k](https://kiwi.com/u/a)"


def test_concierge_needs_confirmation_in_a_later_turn():
    tool = SimpleNamespace(name="save_trip_brief")
    state = {}
    args = {"destination": "Lisbon", "start_date": "2027-05-10", "end_date": "2027-05-14", "travellers": 2}
    turn1 = SimpleNamespace(state=state, invocation_id="inv-1")
    first = concierge_mod.require_confirmation(tool, args, turn1)
    assert first["status"] == "needs_confirmation"
    assert concierge_mod.require_confirmation(tool, args, turn1)["status"] == "needs_confirmation"  # same turn: still no
    turn2 = SimpleNamespace(state=state, invocation_id="inv-2")
    changed = {**args, "travellers": 3}
    assert concierge_mod.require_confirmation(tool, changed, turn2)["status"] == "needs_confirmation"  # changed: ask again
    turn3 = SimpleNamespace(state=state, invocation_id="inv-3")
    assert concierge_mod.require_confirmation(tool, changed, turn3) is None  # confirmed
    assert state[concierge_mod.PENDING_KEY] is None
    assert concierge_mod.require_confirmation(SimpleNamespace(name="geocode_place"), {}, turn3) is None


def test_tidy_plan_orders_items_and_adds_ticket_text():
    from travel_planner.agents.itinerary_tidy import tidy_plan
    plan = {"transport": {"outbound": None, "return": None}, "days": [
        {"day": 1, "date": "2027-05-10", "items": [{"time": "19:00", "kind": "restaurant", "name": "Faca & Garfo"},
                                                   {"time": "17:30", "kind": "sight", "name": "Castle", "ticket_eur": None}]},
        {"day": 2, "date": "2027-05-11", "items": [{"time": "12:00", "kind": "sight", "name": "Park", "ticket_eur": 0},
                                                   {"time": "10:00", "kind": "sight", "name": "Tower", "ticket_eur": 15}]}]}
    assert tidy_plan(plan) == []  # no transport to anchor to: only sorting and ticket texts
    day1, day2 = plan["days"]
    assert [i["name"] for i in day1["items"]] == ["Castle", "Faca & Garfo"]
    assert day1["items"][0]["ticket"] == "ticket price: see the website"
    assert [i.get("ticket") for i in day2["items"]] == ["ticket €15", "free entry"]


def test_tidy_plan_rebuilds_travel_items_from_the_chosen_transport():
    """Regression: the plan used the cheapest flight's times (arrive day 2 09:55) while SAS (arrive day 1 16:10) was chosen."""
    from travel_planner.agents.itinerary_tidy import tidy_plan
    sas = {"mode": "flight", "operator": "SAS", "depart": "2027-05-10T11:15", "arrive": "2027-05-10T16:10",
           "url": "https://kiwi.com/u/a"}
    fin = {"mode": "flight", "operator": "Finnair", "depart": "2027-05-12T16:35", "arrive": "2027-05-12T23:10",
           "url": "https://kiwi.com/u/b"}
    plan = {"transport": {"outbound": sas, "return": fin}, "days": [
        {"day": 1, "date": "2027-05-10", "items": [{"time": "23:15", "kind": "transport", "name": "Depart from Helsinki Airport"},
                                                   {"time": "13:00", "kind": "free", "name": "Lunch near Baixa"}]},
        {"day": 2, "date": "2027-05-11", "items": [{"time": "09:55", "kind": "transport", "name": "Arrive at Lisbon Airport"},
                                                   {"time": "14:30", "kind": "sight", "name": "Jerónimos", "ticket_eur": None}]},
        {"day": 3, "date": "2027-05-12", "items": [{"time": "10:00", "kind": "sight", "name": "Castle", "ticket_eur": 17},
                                                   {"time": "19:30", "kind": "restaurant", "name": "Dinner place"}]}]}
    changes = tidy_plan(plan, {"origin": "Helsinki", "destination": "Lisbon"})
    day1, day2, day3 = (d["items"] for d in plan["days"])
    assert [(i["time"], i["name"]) for i in day1] == [("16:10", "Arrive in Lisbon")]
    assert day1[0]["source_url"] == "https://kiwi.com/u/a"
    assert [i["name"] for i in day2] == ["Jerónimos"]
    assert [(i["time"], i["name"]) for i in day3] == [("10:00", "Castle"), ("16:35", "Depart from Lisbon")]
    assert any("Lunch near Baixa" in c for c in changes) and any("Dinner place" in c for c in changes)


def test_ground_markdown_repairs_quoted_link_targets():
    known = grounding.known_urls(_research_state())
    out, _, removed = grounding.ground_markdown("[details]('https://kiwi.com/u/a') [y](<https://kiwi.com/u/a>)", known)
    assert out == "[details](https://kiwi.com/u/a) [y](https://kiwi.com/u/a)" and removed == []
