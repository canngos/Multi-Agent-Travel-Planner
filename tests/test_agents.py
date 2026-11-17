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
        assert agent.before_tool_callback is mcp_cache_before and agent.after_tool_callback is mcp_cache_after


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

from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent  # noqa: E402

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
