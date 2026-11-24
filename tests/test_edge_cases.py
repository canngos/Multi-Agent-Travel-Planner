"""Regression tests for the edge cases found in the November 2026 review (one section per fix)."""
from datetime import date

import pytest

from travel_planner.agents.review_checks import parse_hours

MON, TUE, WED, SAT = date(2027, 5, 10), date(2027, 5, 11), date(2027, 5, 12), date(2027, 5, 15)


# ---------------------------------------------------------------- opening hours

@pytest.mark.parametrize("text, day, want", [
    # day ranges written with words, not only with a dash
    ("Tuesday to Sunday 10:00-18:00", WED, [(600, 1080)]),
    ("Tuesday to Sunday 10:00-18:00", MON, "closed"),
    ("Mon through Fri 09:00-17:00", WED, [(540, 1020)]),
    ("Mon through Fri 09:00-17:00", SAT, "closed"),
    # the pronoun "we" is not Wednesday, but OSM's "We" still is
    ("10:00-18:00 (we recommend booking)", MON, [(600, 1080)]),
    ("We 11:30-13:00; Sa 14:00-18:00", WED, [(690, 780)]),
    ("Mo-Sa 10:00-18:00; We off", WED, "closed"),
    # closed for good or for now (both occur in the recorded Lisbon research)
    ("closed", WED, "closed"),
    ("Temporarily closed for renovations", WED, "closed"),
    ("Closed until further notice", WED, "closed"),
    ("Temporarily closed for renovations; planned reopening Oct 2021", WED, None),  # stale: may be open again
    ("Closed until June 2027", WED, "closed"),
    ("closed Mondays", WED, None),  # a closed day, not a closed place
    # a month qualifier in brackets applies to the bracket only (real string from the Lisbon demo)
    ("Wed–Mon 11:00–19:00 (Jun–Sep: Fri Sat until 22:00), closed Tue", TUE, "closed"),
    ("Wed–Mon 11:00–19:00 (Jun–Sep: Fri Sat until 22:00), closed Tue", WED, [(660, 1140)]),
    # two seasons in one comma-separated rule
    ("Mar–Oct: daily 10:00–21:00, Nov–Feb daily 09:00–18:00", WED, [(600, 1260)]),
    # "except" and a closed day after "daily"
    ("Daily except Monday 10:00-18:00", MON, "closed"),
    ("Daily except Monday 10:00-18:00", TUE, [(600, 1080)]),
    ("Daily 10:00-18:00; closed Mon", MON, "closed"),
    # OSM: a later rule overrides an earlier one, both ways
    ("Mo-Su 10:00-18:00; Mo off", MON, "closed"),
    ("Mo off; Mo-Su 10:00-18:00", MON, [(600, 1080)]),
])
def test_parse_hours_edge_formats(text, day, want):
    assert parse_hours(text, day) == want


# ---------------------------------------------------------------- budget that cannot be met

def _budget_issue(budget):
    import copy
    from tests.test_review import FAST_OUT, _state, fixed_plan
    from travel_planner.agents.review_checks import check_plan
    plan = fixed_plan()
    plan["transport"]["outbound"] = copy.deepcopy(FAST_OUT)  # costs €1108; the cheaper flight saves €200, tickets €60
    state = _state(plan)
    state["trip_brief"]["total_budget_eur"] = budget
    report = check_plan(plan, state)
    must = [x for x in report["must_fix"] if x["issue"] == "over_budget"]
    should = [x for x in report["should_fix"] if x["issue"] == "over_budget"]
    return must, should


def test_over_budget_is_must_fix_when_the_options_can_close_the_gap():
    must, should = _budget_issue(900.0)  # €208 over, €260 of savings
    assert len(must) == 1 and not should


def test_unreachable_budget_is_not_a_must_fix_to_strip_every_paid_sight():
    must, should = _budget_issue(700.0)  # €408 over, at most €260 of savings
    assert not must and len(should) == 1
    assert "cannot be met" in should[0]["detail"] and "€260" in should[0]["detail"]


# ---------------------------------------------------------------- research JSON with small mistakes

def test_extract_json_repairs_trailing_commas():
    from travel_planner.agents.output import extract_json
    text = '```json\n{"results": [{"name": "Belem Tower", "ticket_eur": 10,},], "notes": "ok",}\n```'
    assert extract_json(text) == {"results": [{"name": "Belem Tower", "ticket_eur": 10}], "notes": "ok"}


def test_unreadable_research_does_not_turn_every_sight_into_an_invented_one():
    from tests.test_review import _state, fixed_plan
    from travel_planner.agents.review_checks import check_plan
    plan = fixed_plan()
    state = _state(plan)
    state["culture_findings"] = state["culture_findings"][:200]  # cut off mid-answer: not JSON at all
    report = check_plan(plan, state)
    assert not [x for x in report["must_fix"] if x["issue"] in ("ungrounded_place", "ungrounded_price")]
    assert [x["issue"] for x in report["should_fix"] if x["issue"] == "research_unreadable"] == ["research_unreadable"]


# ---------------------------------------------------------------- link grounding

WIKI = "https://en.wikipedia.org/wiki/Carmo_Convent_(Lisbon)"


def _known(*urls):
    import json
    from travel_planner.agents import grounding
    return grounding.known_urls({"culture_findings": json.dumps({"results": [{"source_urls": list(urls)}]})})


def test_researched_link_with_parentheses_is_kept():
    from travel_planner.agents import grounding
    item = {"name": "Carmo Convent", "source_url": WIKI}
    grounding.ground_data(item, _known(WIKI), [], [])
    assert item["source_url"] == WIKI
    out, _, removed = grounding.ground_markdown(f"- Carmo Convent [source]({WIKI}).", _known(WIKI))
    assert out == f"- Carmo Convent [source]({WIKI})." and removed == []


def test_unknown_link_with_parentheses_is_unlinked_without_a_stray_bracket():
    from travel_planner.agents import grounding
    out, _, removed = grounding.ground_markdown("- Carmo [source](https://made-up.example/X_(Y)).", _known(WIKI))
    assert out == "- Carmo source." and removed == ["https://made-up.example/X_(Y)"]


def test_invented_bare_url_is_removed_from_the_guide_but_known_ones_stay():
    from travel_planner.agents import grounding
    text = "Tickets: https://invented-tickets.example/lisbon (see https://kiwi.com/u/a)."
    out, _, removed = grounding.ground_markdown(text, _known("https://kiwi.com/u/a"))
    assert "invented-tickets" not in out and "(see https://kiwi.com/u/a)." in out
    assert removed == ["https://invented-tickets.example/lisbon"]


def test_link_paths_are_case_sensitive_but_host_scheme_and_encoding_are_not():
    from travel_planner.agents import grounding
    known = _known("https://kiwi.com/u/AbC123", "https://en.wikipedia.org/wiki/Bel%C3%A9m_Tower")
    assert grounding.ground_url("https://kiwi.com/u/abc123", known) is None
    assert grounding.ground_url("HTTP://WWW.Kiwi.com/u/AbC123/", known) == "https://kiwi.com/u/AbC123"
    assert grounding.ground_url("https://en.wikipedia.org/wiki/Belém_Tower", known) == \
        "https://en.wikipedia.org/wiki/Bel%C3%A9m_Tower"


def _session_with_tool_result(response: dict):
    from types import SimpleNamespace
    from google.genai import types
    part = types.Part(function_response=types.FunctionResponse(name="find_sights", response=response))
    return SimpleNamespace(events=[SimpleNamespace(content=types.Content(role="user", parts=[part]))])


def test_research_output_links_must_come_from_tool_results():
    import json
    from types import SimpleNamespace
    from travel_planner.agents.grounding import ground_research_output
    from travel_planner.agents.output import extract_json
    real = "https://www.opentripmap.com/en/card/N123"
    output = {"results": [{"name": "Belém Tower", "source_urls": [real, "https://made-up.example/belem"],
                           "summary": "Fare from https://made-up.example/fares here."}],
              "fallback_links": ["https://made-up.example/search"], "notes": "ok."}
    state = {"culture_findings": "```json\n" + json.dumps(output) + "\n```"}
    ctx = SimpleNamespace(state=state, session=_session_with_tool_result({"status": "ok", "results": [{"url": real}]}))
    assert ground_research_output("culture_findings")(ctx) is None
    data = extract_json(state["culture_findings"])
    assert data["results"][0]["source_urls"] == [real] and data["fallback_links"] == []
    assert "made-up" not in json.dumps(data) and "Removed 3 link(s)" in data["notes"]


def test_research_output_with_only_respelled_links_is_not_rewritten():
    """http/https, www. or a trailing slash name the same page; later agents treat both spellings as one link."""
    import json
    from types import SimpleNamespace
    from travel_planner.agents.grounding import ground_research_output
    text = "```json\n" + json.dumps({"results": [{"source_urls": ["https://www.castle.example/en/"]}], "notes": ""}) + "\n```"
    state = {"culture_findings": text}
    session = _session_with_tool_result({"url": "http://castle.example/en"})
    ground_research_output("culture_findings")(SimpleNamespace(state=state, session=session))
    assert state["culture_findings"] == text


def _grounding_agents():
    from travel_planner.agents import RESEARCH_AGENTS
    from travel_planner.agents.comparers import build_stay_comparer, build_transport_comparer
    return list(RESEARCH_AGENTS.values()) + [build_transport_comparer, build_stay_comparer]


@pytest.mark.parametrize("factory", _grounding_agents())
def test_every_research_agent_and_comparer_grounds_its_own_output(factory):
    import json
    from types import SimpleNamespace
    agent = factory()
    state = {agent.output_key: "```json\n" + json.dumps(
        {"results": [{"url": "https://made-up.example/x"}], "notes": ""}) + "\n```"}
    ctx = SimpleNamespace(state=state, session=_session_with_tool_result({"url": "https://real.example/a"}))
    for callback in agent.canonical_after_agent_callbacks:
        callback(ctx)
    assert "made-up.example" not in state[agent.output_key]


# ---------------------------------------------------------------- confirming and validating the brief

FUTURE = {"destination": "Lisbon", "start_date": "2027-05-10", "end_date": "2027-05-14"}  # conftest: today 2026-11-17


def _confirm(args_by_turn):
    from types import SimpleNamespace
    from travel_planner.agents.concierge import require_confirmation
    state, results = {}, []
    for turn, args in enumerate(args_by_turn):
        results.append(require_confirmation(SimpleNamespace(name="save_trip_brief"), args,
                                            SimpleNamespace(state=state, invocation_id=f"turn-{turn}")))
    return state, results


def test_today_is_pinned_for_tests():
    from travel_planner import config
    assert config.today() == date(2026, 11, 17)


def test_confirmation_validates_the_brief_before_asking_the_user():
    from travel_planner.agents.concierge import PENDING_KEY
    state, (result,) = _confirm([{**FUTURE, "end_date": "2027-05-01"}])
    assert result["status"] == "error" and "end_date" in result["reason"] and not state.get(PENDING_KEY)


def test_confirmation_treats_omitted_defaults_as_the_same_brief():
    _, results = _confirm([FUTURE, {**FUTURE, "origin": "Helsinki", "travellers": 2, "budget_tier": "mid"}])
    assert results[0]["status"] == "needs_confirmation" and results[1] is None


BASE = {**FUTURE, "destination_lat": 38.72509, "destination_lon": -9.1498, "destination_country": "Portugal",
        "interests": ["history", "street food"]}


@pytest.mark.parametrize("change", [
    {"destination_lat": 33.66, "destination_lon": -95.55},  # Paris, France shown; Paris, Texas saved
    {"destination_country": "United States"},
    {"interests": ["nightlife"]},
])
def test_confirmation_is_asked_again_when_a_shown_detail_changes(change):
    _, results = _confirm([BASE, {**BASE, **change}])
    assert results[1]["status"] == "needs_confirmation"


def test_confirmation_ignores_coordinate_noise_and_interest_order():
    _, results = _confirm([BASE, {**BASE, "destination_lat": 38.7251, "interests": ["Street food", "history"]}])
    assert results[1] is None


@pytest.mark.parametrize("change, field", [
    ({"start_date": "2026-11-01", "end_date": "2026-11-05"}, "start_date"),  # in the past
    ({"end_date": "2027-06-30"}, "end_date"),  # 52 days
    ({"destination": "Lisbon and Porto"}, "destination"),
    ({"destination": "Lisbon/Porto"}, "destination"),
])
def test_save_trip_brief_rejects_trips_the_planner_cannot_plan(change, field):
    from types import SimpleNamespace
    from travel_planner.tools import save_trip_brief
    result = save_trip_brief(tool_context=SimpleNamespace(state={}), **{**FUTURE, **change})
    assert result["status"] == "error" and field in result["reason"]


async def test_geocode_warns_when_the_place_is_a_region_not_a_city(tmp_cache, mock_http):
    from travel_planner.tools import geocode_place
    def nominatim(kind, city_search=None):
        def handler(req):
            if "featureType" in str(req.url):  # the city search for a region's name
                return mock_http.json_response(city_search or [])
            return mock_http.json_response([{"name": "Tuscany", "lat": "43.45", "lon": "11.13", "addresstype": kind,
                                             "address": {"country": "Italy", "country_code": "it"}}])
        return handler
    mock_http.handler = nominatim("state")
    region = await geocode_place("Tuscany")
    assert "not a city" in region["warning"]
    mock_http.handler = nominatim("city")
    assert "warning" not in await geocode_place("Florence")


async def test_a_city_state_or_capital_region_is_a_city(tmp_cache, mock_http):
    from travel_planner.tools import geocode_place

    def handler(req):
        if "featureType" in str(req.url):
            return mock_http.json_response([{"name": "Singapore", "lat": "1.29", "lon": "103.85", "addresstype": "city",
                                             "address": {"country": "Singapore", "country_code": "sg"}}])
        return mock_http.json_response([{"name": "Singapore", "lat": "1.357", "lon": "103.82", "addresstype": "country",
                                         "address": {"country": "Singapore", "country_code": "sg"}}])
    mock_http.handler = handler
    result = await geocode_place("Singapore")
    assert "warning" not in result and result["results"][0]["lat"] == 1.29
    mock_http.handler = lambda req: mock_http.json_response([{
        "name": "Atlantis", "lat": "26.59", "lon": "-80.1", "addresstype": "village", "importance": 0.45,
        "display_name": "Atlantis, Florida, United States", "address": {"country": "United States"}}])
    assert "small village" in (await geocode_place("Atlantis"))["warning"]


# ---------------------------------------------------------------- hard requirements and groups

def _save(**extra):
    from types import SimpleNamespace
    from travel_planner.tools import save_trip_brief
    state = {}
    result = save_trip_brief(tool_context=SimpleNamespace(state=state), **{**FUTURE, **extra})
    return result, state


def test_brief_keeps_hard_requirements_and_tells_every_agent():
    result, state = _save(avoid_modes=["flight"], requirements=["travelling with a child aged 4", "vegetarian"])
    assert result["status"] == "ok"
    assert state["trip_brief"]["avoid_modes"] == ["flight"]
    assert state["trip_brief"]["requirements"] == ["travelling with a child aged 4", "vegetarian"]
    block = state["trip_requirements"]
    assert "HARD REQUIREMENTS" in block and "flight" in block and "child aged 4" in block and "vegetarian" in block


def test_brief_without_requirements_looks_as_before():
    result, state = _save()
    assert "avoid_modes" not in state["trip_brief"] and "requirements" not in state["trip_brief"]
    assert state["trip_requirements"] == ""


def test_unknown_transport_mode_is_rejected():
    result, _ = _save(avoid_modes=["plane"])
    assert result["status"] == "error" and "avoid_modes" in result["reason"]


def test_group_gets_several_hotel_rooms_in_the_requirements():
    _, state = _save(travellers=5)
    assert "3 rooms" in state["trip_requirements"]


def _all_planning_agents():
    from travel_planner.agents.plan_critic import build_plan_critic
    from travel_planner.agents.guide_writer import build_guide_writer
    from travel_planner.agents.itinerary_reviser import build_itinerary_reviser
    from travel_planner.agents.planner import build_itinerary_planner
    return _grounding_agents() + [build_itinerary_planner, build_plan_critic, build_itinerary_reviser,
                                  build_guide_writer]


@pytest.mark.parametrize("factory", _all_planning_agents())
async def test_every_planning_agent_reads_the_requirements_only_when_there_are_some(factory):
    from types import SimpleNamespace
    from google.adk.utils.instructions_utils import inject_session_state
    agent = factory()

    async def render(state):
        state = {"trip_brief": {"destination": "Lisbon"}, **state}
        ctx = SimpleNamespace(state=state, _invocation_context=SimpleNamespace(artifact_service=None,
                                                                               session=SimpleNamespace(state=state)))
        return await inject_session_state(agent.instruction, ctx)

    with_block = await render({"trip_requirements": "\n\nHARD REQUIREMENTS: no flights"})
    assert with_block.endswith("HARD REQUIREMENTS: no flights")
    assert await render({}) == await render({"trip_requirements": ""}) == with_block.removesuffix(
        "\n\nHARD REQUIREMENTS: no flights")


@pytest.mark.parametrize("builder, avoid, key", [
    ("build_flight_searcher", ["flight"], "flight_options"),
    ("build_ground_searcher", ["train", "bus", "ferry"], "ground_options"),
])
def test_searcher_is_skipped_when_its_transport_modes_are_excluded(builder, avoid, key):
    from types import SimpleNamespace
    from travel_planner.agents import transport
    from travel_planner.agents.output import extract_json
    agent = getattr(transport, builder)()

    def run(avoid_modes):
        state = {"trip_brief": {"destination": "Lisbon", "avoid_modes": avoid_modes}}
        results = [cb(SimpleNamespace(state=state)) for cb in agent.canonical_before_agent_callbacks]
        return [r for r in results if r is not None], state

    skipped, state = run(avoid)
    assert skipped and extract_json(state[key])["outbound"] == [] and "do not want" in extract_json(state[key])["notes"]
    assert run(avoid[:-1] if len(avoid) > 1 else [])[0] == []  # not every mode excluded: searches as usual


def test_plan_using_an_excluded_mode_is_must_fix_and_not_a_budget_option():
    from tests.test_review import _state, fixed_plan
    from travel_planner.agents.review_checks import check_plan
    plan = fixed_plan()
    state = _state(plan)
    state["trip_brief"].update(avoid_modes=["flight"], total_budget_eur=900.0)
    report = check_plan(plan, state)
    assert {x["issue"] for x in report["must_fix"]} >= {"avoided_mode"}
    budget = [x for x in report["must_fix"] + report["should_fix"] if x["issue"] == "over_budget"]
    assert budget and "Norwegian" not in budget[0]["detail"]  # a cheaper flight is no option for them


def test_changing_requirements_asks_for_confirmation_again():
    _, results = _confirm([BASE, {**BASE, "avoid_modes": ["flight"]}])
    assert results[1]["status"] == "needs_confirmation" and "requirements" in results[1]["reason"]


async def test_hotel_rates_book_several_rooms_for_a_group(tmp_cache, mock_http, monkeypatch):
    import json
    from travel_planner.tools import hotels

    async def no_reverse_geocoding(lat, lon):
        return "Baixa"
    monkeypatch.setattr(hotels, "neighbourhood", no_reverse_geocoding)
    monkeypatch.setenv("LITEAPI_KEY", "test-key")
    mock_http.handler = lambda req: mock_http.json_response({
        "data": [{"hotelId": "h1", "roomTypes": [{"offerRetailRate": {"amount": 900.0}, "rates": [{"name": "Twin"}]}]}],
        "hotels": [{"id": "h1", "name": "Hotel A", "latitude": 38.71, "longitude": -9.14, "city_name": "Lisbon"}]})
    group = await hotels.search_hotel_rates(38.71, -9.14, "2027-05-10", "2027-05-14", adults=5, rooms=3)
    sent = json.loads(mock_http.calls[-1].content)
    assert sent["occupancies"] == [{"adults": 2}, {"adults": 2}, {"adults": 1}]
    assert "no_rooms=3" in group["results"][0]["url"] and "3 rooms" in group["results"][0]["details"]
    await hotels.search_hotel_rates(38.71, -9.14, "2027-05-10", "2027-05-14", adults=2)
    assert json.loads(mock_http.calls[-1].content)["occupancies"] == [{"adults": 2}]  # a couple: one room as before
    links = hotels.stay_deep_links("Lisbon", "2027-05-10", "2027-05-14", adults=5, rooms=3)["results"]
    assert "no_rooms=3" in links[1]["url"]


# ---------------------------------------------------------------- Kiwi searches are one-way

from tests.test_mcp import offline_tool  # noqa: E402,F401  (fixture: a real Kiwi McpTool without a server)


async def test_kiwi_search_is_always_one_way(offline_tool):
    """A round-trip price would be counted as the outbound leg, and the return flight added on top."""
    from types import SimpleNamespace
    from travel_planner.tools.mcp import mcp_cache_before
    tool = await offline_tool()
    args = {"flyFrom": "HEL", "flyTo": "LIS", "departureDate": "10/05/2027", "adults": 2, "returnDate": "14/05/2027",
            "returnDateTo": "15/05/2027", "returnDateFlexDays": 1, "nights_in_dst_from": 4, "ret_dtime_from": "08:00"}
    await mcp_cache_before(tool, args, SimpleNamespace(state={}))
    assert args == {"flyFrom": "HEL", "flyTo": "LIS", "departureDate": "10/05/2027", "adults": 2}


# ---------------------------------------------------------------- prices in other currencies

@pytest.mark.parametrize("text, want", [
    ("€12", (12.0, "EUR")), ("12 EUR", (12.0, "EUR")), ("€1.234,56", (1234.56, "EUR")), ("€1,234", (1234.0, "EUR")),
    ("€12,50", (12.5, "EUR")), ("20 zł", (20.0, "PLN")), ("PLN 25", (25.0, "PLN")), ("£12", (12.0, "GBP")),
    ("350 CZK", (350.0, "CZK")), ("120 Kč", (120.0, "CZK")), ("2 500 Ft", (2500.0, "HUF")), ("$15", (15.0, "USD")),
    ("Free", None), ("about 20", None), ("100 kr", None),  # "kr" could be SEK, NOK, DKK or ISK
])
def test_parse_money(text, want):
    from travel_planner.tools.currency import parse_money
    assert parse_money(text) == want


async def test_wikivoyage_prices_in_zloty_are_converted_to_euro(tmp_cache, mock_http):
    from travel_planner.tools.currency import RATES_URL
    from travel_planner.tools.wiki import parse_listings, wikivoyage_rates
    mock_http.handler = lambda req: mock_http.json_response({"base": "EUR", "date": "2026-11-16", "rates": {"PLN": 4.0}})
    text = ("==See==\n* {{see | name=Wawel Castle | price=20 zł | content=Royal castle.}}\n"
            "==Eat==\n===Budget===\n* {{eat | name=Bar Mleczny | price=zł 30-50 | content=Pierogi.}}\n")
    rates = await wikivoyage_rates(text)
    assert str(mock_http.calls[0].url).startswith(RATES_URL)
    castle, bar = parse_listings(text, "https://en.wikivoyage.org/wiki/Krak%C3%B3w", rates=rates)
    assert castle.ticket_eur == 5.0 and "20 zł" in castle.summary
    assert bar.budget_tier == "low"
    assert await wikivoyage_rates("* {{see | name=X | price=€5}}") is None and len(mock_http.calls) == 1  # euro: no fetch


async def test_without_exchange_rates_foreign_prices_stay_unknown(tmp_cache):
    from travel_planner.tools.currency import eur_rates
    from travel_planner.tools.sight_hours import charge_eur
    assert await eur_rates() is None  # offline and nothing cached
    assert charge_eur("20 PLN") is None and charge_eur("20 PLN", {"PLN": 4.0}) == 5.0


def test_airbnb_total_reads_european_number_formats():
    from travel_planner.tools.mcp import _euros
    assert (_euros("€1.234,56"), _euros("€1,234"), _euros("€97")) == (1234.56, 1234.0, 97.0)


# ---------------------------------------------------------------- Transitous answers expire

FERRY = {"itineraries": [{"duration": 7200, "startTime": "2026-12-01T07:30:00Z", "endTime": "2026-12-01T09:30:00Z",
                          "transfers": 0, "legs": [{"mode": "FERRY", "duration": 7200, "agencyName": "Tallink",
                                                    "from": {"name": "Helsinki"}, "to": {"name": "Tallinn"}}]}]}


@pytest.fixture
def clock(monkeypatch):
    """The cache's clock, moved forward by setting .days."""
    import time as real_time
    from types import SimpleNamespace
    from travel_planner.tools import _cache, _http
    fake = SimpleNamespace(days=0)
    monkeypatch.setattr(_cache, "time", SimpleNamespace(time=lambda: real_time.time() + fake.days * 86400))
    monkeypatch.setattr(_http, "MIN_INTERVAL_S", {})  # mocked servers need no politeness delay
    return fake


async def _plan_ferry():
    from travel_planner.tools.transit import plan_public_transport
    return await plan_public_transport(60.17, 24.94, 59.44, 24.75, "2026-12-01")  # 14 days ahead: no typical day


async def test_an_empty_transitous_answer_is_asked_again_after_a_day(tmp_cache, mock_http, clock):
    replies = [{"itineraries": []}, FERRY]
    mock_http.handler = lambda req: mock_http.json_response(replies[len(mock_http.calls) - 1])
    assert (await _plan_ferry())["status"] == "error"
    assert (await _plan_ferry())["status"] == "error" and len(mock_http.calls) == 1  # same day: from the cache
    clock.days = 2
    assert (await _plan_ferry())["status"] == "ok" and len(mock_http.calls) == 2  # the timetable is out now


async def test_found_connections_are_reused_for_a_week(tmp_cache, mock_http, clock):
    mock_http.handler = lambda req: mock_http.json_response(FERRY)
    await _plan_ferry()
    clock.days = 6
    await _plan_ferry()
    assert len(mock_http.calls) == 1
    clock.days = 8
    await _plan_ferry()
    assert len(mock_http.calls) == 2


# ---------------------------------------------------------------- item times

def _leg(depart, arrive):
    return {"mode": "flight", "operator": "TAP", "depart": depart, "arrive": arrive, "url": "https://kiwi.com/u/t"}


@pytest.mark.parametrize("text, want", [
    ("13:00", 780), ("9:30", 570), ("1:00 PM", 780), ("1 pm", 780), ("12:30 AM", 30), ("12 p.m.", 720),
    ("13h00", 780), ("10:00-11:30", 600), ("10:00 park walk", 600), ("lunch", None), ("2", None), ("", None),
])
def test_clock_minutes(text, want):
    from travel_planner.agents.review_checks import clock_minutes
    assert clock_minutes(text) == want


def test_arrival_day_reads_12_hour_times_and_keeps_untimed_and_late_items():
    from travel_planner.agents.itinerary_tidy import tidy_plan
    plan = {"transport": {"outbound": _leg("2027-05-10T07:00", "2027-05-10T11:00"), "return": None},
            "days": [{"day": 1, "date": "2027-05-10", "items": [
                {"time": "1:00 PM", "kind": "sight", "name": "Belem Tower"},
                {"time": "lunch", "kind": "free", "name": "Lunch near Belem"},
                {"time": "00:30", "kind": "bar", "name": "Late bar"},
                {"time": "15:00", "kind": "sight", "name": "Jeronimos"},
                {"time": "09:00", "kind": "sight", "name": "Before landing"}]}]}
    tidy_plan(plan, {"destination": "Lisbon"})
    assert [(i["time"], i["name"]) for i in plan["days"][0]["items"]] == [
        ("11:00", "Arrive in Lisbon"), ("13:00", "Belem Tower"), ("15:00", "Jeronimos"), ("00:30", "Late bar"),
        ("lunch", "Lunch near Belem")]


def test_departure_day_drops_late_items_and_keeps_untimed_ones_before_leaving():
    from travel_planner.agents.itinerary_tidy import tidy_plan
    plan = {"transport": {"outbound": None, "return": _leg("2027-05-12T18:00", "2027-05-12T23:00")},
            "days": [{"day": 3, "date": "2027-05-12", "items": [
                {"time": "00:30", "kind": "bar", "name": "Late bar"},
                {"time": "", "kind": "free", "name": "Souvenirs"},
                {"time": "10:00", "kind": "sight", "name": "Castle"}]}]}
    tidy_plan(plan, {"destination": "Lisbon"})
    assert [i["name"] for i in plan["days"][0]["items"]] == ["Castle", "Souvenirs", "Depart from Lisbon"]


def test_checks_read_12_hour_and_after_midnight_times():
    from tests.test_review import _state, fixed_plan
    from travel_planner.agents.review_checks import check_plan
    plan = fixed_plan()
    day2 = plan["days"][1]["items"]  # Wednesday 2027-05-12
    day2[0]["time"] = "4:30 PM"  # MUDE is open 10:00-18:00 (misread as 04:30 it would be closed)
    day2.append({"time": "00:30", "kind": "restaurant", "name": "Leitaria Camponeza", "lat": 38.7117, "lon": -9.1386})
    state = _state(plan)
    state["food_findings"] = state["food_findings"].replace("Daily 19:00-23:00", "Daily 19:00-02:00")
    report = check_plan(plan, state)
    assert [x["detail"] for x in report["must_fix"] if x["issue"] == "outside_hours"] == []  # 00:30 is in 19:00-02:00


# ---------------------------------------------------------------- retries after malformed tool calls

def _malformed():
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    return LlmResponse(finish_reason=types.FinishReason.MALFORMED_FUNCTION_CALL,
                       error_code=types.FinishReason.MALFORMED_FUNCTION_CALL,
                       error_message="Arguments were not a valid JSON object: exit_loop")


def test_every_review_round_gets_its_own_retries():
    from types import SimpleNamespace
    from travel_planner.agents.retry import MAX_RETRIES, retry_malformed_call
    state = {}
    ctx = SimpleNamespace(state=state, invocation_id="loop-run", agent_name="itinerary_reviser")
    for round_no in (1, 2, 3):
        state["review_round"] = round_no
        retried = [retry_malformed_call(ctx, _malformed()) is not None for _ in range(MAX_RETRIES + 1)]
        assert retried == [True] * MAX_RETRIES + [False], round_no


def test_retry_counts_do_not_grow_without_limit():
    from types import SimpleNamespace
    from travel_planner.agents import retry
    for n in range(retry.MAX_TRACKED + 50):
        retry.retry_malformed_call(SimpleNamespace(state={}, invocation_id=f"inv-{n}", agent_name="a"), _malformed())
    assert len(retry._counts) <= retry.MAX_TRACKED


async def test_concierge_gets_a_retry_after_a_malformed_tool_call():
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner
    from google.genai import types
    from tests.test_review import MalformedOnceLlm
    from travel_planner.agents.concierge import build_concierge
    from travel_planner.agents.retry import RETRY_TOOL
    concierge = build_concierge(LlmAgent(name="planning_pipeline", model=MalformedOnceLlm(), instruction="Plan."))
    concierge.model = MalformedOnceLlm()
    runner = InMemoryRunner(agent=concierge, app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u")
    async for _ in runner.run_async(user_id="u", session_id=session.id, new_message=types.Content(
            role="user", parts=[types.Part(text="5 days in Lisbon")])):
        pass
    await runner.close()
    assert concierge.model.seen == [[], [RETRY_TOOL]]  # the failed call was answered with "call it again"


# ---------------------------------------------------------------- problems left after the review

GUIDE = "# Lisbon trip guide\n\n## Getting there and back\nx\n\n## Day by day\ny\n"


def _save_guide_args(plan):
    from types import SimpleNamespace
    from tests.test_review import _state
    from travel_planner.agents.guide_writer import build_guide_writer
    args = {"markdown_text": GUIDE, "filename_slug": "lisbon"}
    ctx = SimpleNamespace(state=_state(plan))
    for callback in build_guide_writer().canonical_before_tool_callbacks:
        callback(SimpleNamespace(name="save_guide"), args, ctx)
    return args["markdown_text"]


def test_problems_the_review_could_not_fix_are_listed_in_the_guide():
    from tests.test_review import bad_plan
    text = _save_guide_args(bad_plan())
    assert "## Still to check" in text and "Museum of Invented Things" in text and "Day 2" in text


def test_a_plan_without_problems_adds_nothing_to_the_guide_but_the_day_rhythm():
    from tests.test_review import BRIEF, fixed_plan
    from travel_planner.agents.guide_writer import add_day_rhythm
    assert _save_guide_args(fixed_plan()) == add_day_rhythm(GUIDE, BRIEF)


def test_safety_net_save_also_lists_the_open_problems(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tests.test_review import _state, bad_plan
    from travel_planner import config
    from travel_planner.agents.guide_writer import save_reply_if_not_saved
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    state = _state(bad_plan()) | {"guide_reply": GUIDE}
    save_reply_if_not_saved(SimpleNamespace(state=state))
    assert "## Still to check" in state["final_guide"]


# ---------------------------------------------------------------- smaller fixes

@pytest.mark.parametrize("start, end, want", [
    ("2028-02-28", "2028-03-01", ["2028-02-28", "2028-02-29", "2028-03-01"]),
    ("2028-02-29", "2028-03-02", ["2028-02-29", "2028-03-01", "2028-03-02"]),
])
async def test_typical_weather_covers_every_trip_day_around_29_february(monkeypatch, start, end, want):
    from datetime import timedelta
    import travel_planner.tools.weather as weather

    async def archive(url, *, namespace, params, **kw):  # answers exactly the dates asked for
        s, e = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
        days = [(s + timedelta(days=i)).isoformat() for i in range((e - s).days + 1)]
        n = len(days)
        return {"daily": {"time": days, "weather_code": [1] * n, "temperature_2m_max": [15] * n,
                          "temperature_2m_min": [8] * n, "precipitation_sum": [0.0] * n}}
    monkeypatch.setattr(weather, "get_json", archive)
    result = await weather.get_forecast(43.77, 11.25, start, end)
    assert [d["date"] for d in result["results"]] == want
    assert [d["weekday"] for d in result["results"]] == [date.fromisoformat(d).strftime("%a") for d in want]


async def test_wiki_summary_only_accepts_a_language_code(tmp_cache, mock_http):
    from travel_planner.tools import wiki_summary
    result = await wiki_summary("Belém Tower", lang="attacker.example/x?")
    assert result["status"] == "error" and mock_http.calls == []


def test_saved_html_guide_has_no_raw_html_or_script_links(tmp_path, monkeypatch):
    from pathlib import Path
    from travel_planner import config
    from travel_planner.tools import save_guide
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    text = "# Guide\n\n<img src=x onerror=\"alert(1)\">\n\n[click](javascript:alert(1)) and [ok](https://kiwi.com/u/a)\n"
    html = Path(save_guide(text, "lisbon")["results"][0]["html_path"]).read_text(encoding="utf-8")
    assert "<img" not in html and 'href="javascript' not in html
    assert 'href="https://kiwi.com/u/a"' in html
    assert "<small>" in html and 'href="https://transitous.org/sources/"' in html  # the footer is still HTML


def test_generic_place_names_do_not_ground_other_places():
    from travel_planner.agents.review_checks import find_place
    places = [{"name": "Miradouro"}, {"name": "Museu"}, {"name": "Jerónimos Monastery"}, {"name": "Faca & Garfo"},
              {"name": "Newcastle Arms"}]
    assert find_place("Miradouro do Inventado", places) is None
    assert find_place("Museu Nacional Inventado", places) is None
    assert find_place("Castle", places) is None  # not "Newcastle Arms": containment respects word boundaries
    assert find_place("Miradouro", places)["name"] == "Miradouro"
    assert find_place("Jeronimos Monastery Church", places)["name"] == "Jerónimos Monastery"
    assert find_place("Faca & Garfo restaurant", places)["name"] == "Faca & Garfo"


@pytest.mark.parametrize("interest, want", [
    ("parties", "historic,cultural,architecture"),  # the default: "art" is not in "parties"
    ("agriculture", "historic,cultural,architecture"),
    ("street art", "museums,theatres_and_entertainments"),
    ("historical sites", "historic"),
    ("museums", "museums"),
])
def test_interests_match_whole_words(interest, want):
    from travel_planner.tools.sights import kinds_for
    assert kinds_for([interest]) == want


def test_google_flights_duration_includes_layovers():
    from travel_planner.tools import flights
    f = {"price": 412, "airlines": ["Finnair", "TAP"], "flights": [
        {"from_airport": {"code": "HEL"}, "to_airport": {"code": "FRA"}, "duration": 165,
         "departure": {"date": (2026, 12, 10), "time": (7, 5)}, "arrival": {"date": (2026, 12, 10), "time": (8, 50)}},
        {"from_airport": {"code": "FRA"}, "to_airport": {"code": "LIS"}, "duration": 185,
         "departure": {"date": (2026, 12, 11), "time": (6, 0)}, "arrival": {"date": (2026, 12, 11), "time": (8, 5)}}]}
    option = flights.flight_to_option(f, "https://g.example")
    assert option.duration_min == 165 + (21 * 60 + 10) + 185  # an overnight layover in Frankfurt counts
    assert "layover" in option.details


def _paid_tool_agents():
    from travel_planner.agents import RESEARCH_AGENTS
    from travel_planner.agents.plan_critic import build_plan_critic
    from travel_planner.agents.planner import build_itinerary_planner
    return list(RESEARCH_AGENTS.values()) + [build_itinerary_planner, build_plan_critic]


@pytest.mark.parametrize("factory", _paid_tool_agents())
async def test_tool_budget_is_enforced_in_code(factory):
    import inspect
    from types import SimpleNamespace
    agent = factory()
    ctx = SimpleNamespace(state={}, invocation_id="run-1", agent_name=agent.name)

    async def call():
        for callback in agent.canonical_before_tool_callbacks:  # as ADK does: the first answer wins
            result = callback(SimpleNamespace(name="web_search"), {}, ctx)
            result = await result if inspect.isawaitable(result) else result
            if result is not None:
                return result
        return None

    answers = [await call() for _ in range(40)]
    refused = [i for i, a in enumerate(answers) if a is not None]
    assert refused and all(answers[i]["status"] == "error" for i in refused)
    assert 3 <= refused[0] <= 17  # the prompt's budget plus a little slack, never unlimited


# ================================================================ findings from the live runs (Tallinn, Kraków)

def _live_state(mutate=None):
    """fixed_plan() from the review tests (no problems), optionally changed by `mutate(plan, research)` first."""
    import json
    from tests.test_review import _state, fixed_plan
    plan = fixed_plan()
    state = _state(plan)
    if mutate:
        research = {k: json.loads(state[k]) for k in ("culture_findings", "food_findings", "stay_summary",
                                                     "transport_summary")}
        mutate(plan, research)
        state.update({k: json.dumps(v, ensure_ascii=False) for k, v in research.items()})
        state["itinerary"] = "```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```"
    return plan, state


def _issues_of(plan, state, level):
    from travel_planner.agents.review_checks import check_plan
    return {x["issue"]: x for x in check_plan(plan, state)[level]}


FAR = {"kind": "hotel", "name": "Far Hostel", "area": "Suburb", "lat": 38.7800, "lon": -9.1000,  # ~8 km out
       "price_per_night_eur": 75.0, "total_price_eur": 150.0, "price_source": "live", "source": "x",
       "url": "https://example.org/far"}
CENTRAL = {**FAR, "name": "Central Hostel", "area": "Baixa", "lat": 38.7105, "lon": -9.1365, "total_price_eur": 170.0,
           "price_per_night_eur": 85.0, "url": "https://example.org/central"}


def test_far_stay_with_a_central_option_for_little_more_is_flagged():
    def far(plan, research):
        plan["stay"] = dict(FAR)
        research["stay_summary"] = {"cheapest": FAR, "best_value": CENTRAL, "top_rated": CENTRAL, "by_area": []}
    plan, state = _live_state(far)
    issue = _issues_of(plan, state, "should_fix")["far_stay"]
    assert [a["name"] for a in issue["alternatives"]] == ["Central Hostel"] and "km" in issue["detail"]


def test_far_stay_is_not_flagged_when_central_stays_cost_much_more():
    def far(plan, research):
        plan["stay"] = dict(FAR)
        pricey = {**CENTRAL, "total_price_eur": 600.0}
        research["stay_summary"] = {"cheapest": FAR, "best_value": pricey, "top_rated": pricey, "by_area": []}
    plan, state = _live_state(far)
    assert "far_stay" not in _issues_of(plan, state, "should_fix")


def test_placeholder_meal_next_to_an_unused_restaurant_is_flagged():
    def nearby(plan, research):  # day 3 has "Lunch near Alfama" (a free item)
        research["food_findings"]["results"].append({"kind": "restaurant", "name": "Unused Tasca", "lat": 38.7104,
                                                     "lon": -9.1330, "hours": "Daily 12:00-23:00", "budget_tier": "mid"})
    plan, state = _live_state(nearby)
    issue = _issues_of(plan, state, "decide")["placeholder_meal"]  # a matter of taste: the critic decides
    assert issue["day"] == 3 and [a["name"] for a in issue["alternatives"]] == ["Unused Tasca"]


def _afternoon_day(items):
    def change(plan, research):
        plan["days"][1]["items"] = items
    return change


def test_a_whole_empty_afternoon_with_unused_sights_nearby_is_for_the_critic_to_decide():
    from tests.test_review import _item
    plan, state = _live_state(_afternoon_day([  # Wednesday: nothing between lunch (12:30) and dinner (19:30)
        _item("10:30", "sight", "MUDE - Museu do Design", None, 38.7091, -9.1366),
        _item("12:30", "restaurant", "Taberna Nearby", None, 38.7108, -9.1330),
        _item("19:30", "free", "Dinner near Baixa")]))
    issue = _issues_of(plan, state, "decide")["empty_afternoon"]
    assert issue["day"] == 2 and issue["alternatives"] and "12:30-19:30" in issue["detail"]


def test_an_empty_evening_or_a_short_gap_stays_a_should_fix():
    from tests.test_review import _item
    plan, state = _live_state(_afternoon_day([  # busy until 17:30, then nothing: an evening, not an afternoon
        _item("10:30", "sight", "MUDE - Museu do Design", None, 38.7091, -9.1366),
        _item("12:30", "restaurant", "Taberna Nearby", None, 38.7108, -9.1330),
        _item("14:00", "sight", "Museu Arqueológico do Carmo", 7.0, 38.7120, -9.1406),
        _item("16:00", "sight", "Museu do Dinheiro", 0.0, 38.7086, -9.1380)]))
    assert "empty_afternoon" not in {**_issues_of(plan, state, "must_fix"), **_issues_of(plan, state, "decide")}


# ---------------------------------------------------------------- day shape (live runs only, not the demo)

def _late_start_day(plan, research):  # Wednesday: lunch first, though MUDE opens at 10:00
    from tests.test_review import _item
    plan["days"][1]["items"] = [
        _item("12:30", "restaurant", "Taberna Nearby", None, 38.7108, -9.1330),
        _item("14:00", "sight", "MUDE - Museu do Design", None, 38.7091, -9.1366),
        _item("16:00", "sight", "Museu do Dinheiro", 0.0, 38.7086, -9.1380),
        _item("19:30", "free", "Dinner near Baixa")]


def test_a_full_day_starting_with_lunch_is_an_empty_morning():
    plan, state = _live_state(_late_start_day)
    issue = _issues_of(plan, state, "must_fix")["empty_morning"]
    assert issue["day"] == 2 and "before 12:30" in issue["detail"] and "MUDE" in issue["detail"]
    # the same morning is not reported again as an ordinary gap
    assert not any(x["day"] == 2 and x["issue"] == "empty_gap" and "09:00" in x["detail"]
                   for x in check_plan_of(plan, state)["should_fix"])


def test_a_full_day_starting_at_10_30_is_fine():
    from tests.test_review import _item
    plan, state = _live_state(_afternoon_day([
        _item("10:30", "sight", "MUDE - Museu do Design", None, 38.7091, -9.1366),
        _item("13:00", "restaurant", "Taberna Nearby", None, 38.7108, -9.1330),
        _item("15:00", "sight", "Museu do Dinheiro", 0.0, 38.7086, -9.1380)]))
    assert "empty_morning" not in _issues_of(plan, state, "must_fix")


def test_two_restaurants_close_together_are_one_meal_too_many():
    from tests.test_review import _item

    def two_dinners(plan, research):
        plan["days"][1]["items"] = [
            _item("10:30", "sight", "MUDE - Museu do Design", None, 38.7091, -9.1366),
            _item("13:00", "free", "Lunch near Baixa"),
            _item("15:00", "sight", "Museu do Dinheiro", 0.0, 38.7086, -9.1380),
            _item("19:30", "restaurant", "Taberna Nearby", None, 38.7108, -9.1330),
            _item("20:30", "free", "Dinner near Baixa")]
    plan, state = _live_state(two_dinners)
    issue = _issues_of(plan, state, "must_fix")["double_meal"]
    assert issue["day"] == 2 and "'Taberna Nearby' at 19:30 and 'Dinner near Baixa' at 20:30" in issue["detail"]
    # lunch at 13:00 and dinner at 19:30 are fine
    assert sum(x["issue"] == "double_meal" for x in check_plan_of(plan, state)["must_fix"]) == 1


def test_obvious_day_shape_faults_stay_must_fix_and_the_rest_is_for_the_critic(monkeypatch):
    def all_faults(plan, research):
        _late_start_day(plan, research)  # lunch first although MUDE, planned that day, opens at 10:00
        plan["days"][1]["items"].insert(1, plan["days"][1]["items"][-1] | {"time": "13:30"})  # a meal 1 h later
        research["food_findings"]["results"].append({"kind": "restaurant", "name": "Unused Tasca", "lat": 38.7104,
                                                     "lon": -9.1330, "hours": "Daily 12:00-23:00", "budget_tier": "mid"})
    plan, state = _live_state(all_faults)
    for demo in (False, True):  # the demo runs the same checks since it was recorded again (2026-11-19)
        if demo:
            monkeypatch.setenv("DEMO", "1")
        must, decide = _issues_of(plan, state, "must_fix"), _issues_of(plan, state, "decide")
        assert "only the order changes" in must["empty_morning"]["obvious"]
        assert "less than 2 hours" in must["double_meal"]["obvious"]
        assert "placeholder_meal" in decide and not {"empty_morning", "double_meal"} & set(decide)


def test_a_late_start_with_only_other_sights_open_early_or_meals_2_5_hours_apart_are_the_critics_call():
    from tests.test_review import _item

    def change(plan, research):  # nothing planned that day opens early; Carmo (unused, nearby) opens at 10:00
        plan["days"][1]["items"] = [
            _item("12:00", "restaurant", "Taberna Nearby", None, 38.7108, -9.1330),
            _item("14:30", "restaurant", "Faca & Garfo", None, 38.7125, -9.1410),
            _item("16:00", "sight", "Late Museum", 5.0, 38.7100, -9.1340),
            _item("19:30", "free", "Dinner near Baixa")]
        research["culture_findings"]["results"].append(
            {"kind": "sight", "name": "Late Museum", "lat": 38.7100, "lon": -9.1340, "hours": "Daily 15:00-20:00",
             "ticket_eur": 5.0})
    plan, state = _live_state(change)
    decide = _issues_of(plan, state, "decide")
    assert {"empty_morning", "double_meal"} <= set(decide) and not decide["empty_morning"].get("obvious")


def test_planner_and_reviser_get_the_day_shape_rules():
    from travel_planner.agents.itinerary_reviser import build_itinerary_reviser
    from travel_planner.agents.planner import build_itinerary_planner
    for build in (build_itinerary_planner, build_itinerary_reviser):
        assert "Never start a full day with lunch" in build().instruction
    assert "Empty morning:" in build_itinerary_reviser().instruction


def check_plan_of(plan, state):
    from travel_planner.agents.review_checks import check_plan
    return check_plan(plan, state)


async def test_forecast_days_without_data_are_unknown_not_dry(monkeypatch):
    import travel_planner.tools.weather as weather

    async def forecast(url, *, namespace, params, **kw):  # the last forecast day comes back empty, as in Tallinn
        return {"daily": {"time": ["2026-11-28", "2026-11-29"], "weather_code": [61, None],
                          "temperature_2m_max": [3.0, None], "temperature_2m_min": [0.5, None],
                          "precipitation_sum": [2.1, None], "precipitation_probability_max": [80, None]}}
    monkeypatch.setattr(weather, "get_json", forecast)
    days = (await weather.get_forecast(59.44, 24.75, "2026-11-28", "2026-11-29"))["results"]
    assert days[1]["weather"] == "unknown (no forecast data yet)" and days[1]["rainy"] is None


async def test_the_last_forecast_day_falls_back_to_typical_weather(monkeypatch):
    import travel_planner.tools.weather as weather
    asked = []

    async def any_weather(url, *, namespace, params, **kw):
        asked.append(url)
        return {"daily": {"time": [params["start_date"]], "weather_code": [1], "temperature_2m_max": [3.0],
                          "temperature_2m_min": [0.5], "precipitation_sum": [0.0]}}
    monkeypatch.setattr(weather, "get_json", any_weather)
    result = await weather.get_forecast(59.44, 24.75, "2026-12-02", "2026-12-02")  # today (pinned) + 15 days
    assert "archive" in asked[0] and result["kind"].startswith("typical")


def test_concierge_answer_gets_the_trip_dates_right():
    from types import SimpleNamespace
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from travel_planner.agents.concierge import fix_day_dates
    ctx = SimpleNamespace(state={"trip_brief": {"start_date": "2026-12-01", "end_date": "2026-12-03"}})

    def answer(text):
        return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))
    fixed = fix_day_dates(ctx, answer("**Vegetarian lunch on Day 2 (12 Dec 2026)** and Day 3 (2026-12-09)."))
    assert fixed.content.parts[0].text == "**Vegetarian lunch on Day 2 (2 Dec 2026)** and Day 3 (2026-12-03)."
    assert fix_day_dates(ctx, answer("Day 2 (2 Dec 2026) is Kadriorg.")) is None  # already right


def test_concierge_answer_gets_outbound_and_return_dates_right():
    from types import SimpleNamespace
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from travel_planner.agents.concierge import fix_day_dates
    ctx = SimpleNamespace(state={"trip_brief": {"start_date": "2026-12-01", "end_date": "2026-12-03"}})
    text = ("- **Outbound (12 Dec 2026):** Departs 10:02\n- **Return (14 Dec 2026):** Departs 16:15\n"
            "- **Return (3 Dec 2026)** is right.")  # seen in the second live Tallinn run
    fixed = fix_day_dates(ctx, LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)])))
    assert fixed.content.parts[0].text == ("- **Outbound (1 Dec 2026):** Departs 10:02\n- **Return (3 Dec 2026):** "
                                           "Departs 16:15\n- **Return (3 Dec 2026)** is right.")


async def test_a_tool_called_with_underscores_instead_of_hyphens_still_runs():
    from google.adk.agents import LlmAgent
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types
    from pydantic import Field
    from travel_planner.agents.retry import offer_retry_tool
    from travel_planner.tools import calculate_trip_cost

    class Misnamed(BaseLlm):  # GPT OSS called "search_flight" for Kiwi's "search-flight"
        model: str = "misnamed"
        replies: list = Field(default_factory=list)

        async def generate_content_async(self, llm_request, stream=False):
            done = [p.function_response for c in llm_request.contents for p in c.parts or [] if p.function_response]
            if done:
                self.replies.append(done[0].response)
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="ok")]))
            else:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
                    name="calculate-trip-cost", args={"transport_total_eur": 100, "stay_total_eur": 200,
                                                      "activities_total_eur": 0, "days": 2, "travellers": 2}))]))
    llm = Misnamed()
    agent = LlmAgent(name="a", model=llm, instruction="x", tools=[calculate_trip_cost], before_model_callback=offer_retry_tool)
    runner = InMemoryRunner(agent=agent, app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u")
    async for _ in runner.run_async(user_id="u", session_id=session.id,
                                    new_message=types.Content(role="user", parts=[types.Part(text="go")])):
        pass
    await runner.close()
    assert llm.replies and llm.replies[0]["status"] == "ok"


async def test_wikivoyage_page_titles_are_url_decoded(tmp_cache, mock_http):
    from urllib.parse import parse_qs, urlsplit
    from travel_planner.tools import wikivoyage_section
    mock_http.handler = lambda req: mock_http.json_response({"error": {"info": "stop here"}})
    await wikivoyage_section("Krak%C3%B3w", "See")
    assert parse_qs(urlsplit(str(mock_http.calls[0].url)).query)["page"] == ["Kraków"]


def test_a_link_with_a_doubled_scheme_without_colon_is_repaired():
    from travel_planner.agents import grounding
    known = _known("https://en.wikipedia.org/wiki/Kadriorg_Palace")
    assert grounding.ground_url("httpshttps://en.wikipedia.org/wiki/Kadriorg_Palace", known) == \
        "https://en.wikipedia.org/wiki/Kadriorg_Palace"


def _plan_and_guide(stay_name=None, extra_day2="", cost_rows=None):
    """A checked plan (fixed_plan with its cost) and a guide written from it, optionally drifting from it."""
    import json
    from tests.test_review import BRIEF, _state, fixed_plan
    from travel_planner.agents.itinerary_tidy import tidy_plan
    plan = fixed_plan()
    tidy_plan(plan, BRIEF)
    state = _state(plan)
    cost = plan["cost"]["categories_eur"]
    rows = cost_rows or [("Transport", cost["transport"]), ("Stay", cost["stay"]), ("Food", cost["food"]),
                         ("Activities", cost["activities"]), ("Local transport", cost["local_transport"]),
                         ("**Total**", plan["cost"]["total_eur"])]
    days = []
    for d in plan["days"]:
        bullets = [f"- **{i['time']} {i['name']}** – note." for i in d["items"]]
        if d["day"] == 2 and extra_day2:
            bullets.append(extra_day2)
        days.append(f"### Day {d['day']} – {d['date']}: area\n" + "\n".join(bullets))
    guide = ("# Lisbon trip guide\n\nTwo travellers.\n\n## Getting there and back\nFinnair.\n\n## Where to stay\n"
             f"**{stay_name or plan['stay']['name']}** – Baixa.\n\n| Area | Hotel |\n|---|---|\n| Baixa | Nice Hotel |\n\n"
             "## Day by day\n" + "\n\n".join(days) + "\n\n## Cost estimate\n| Category | Total (€) |\n|---|---|\n"
             + "\n".join(f"| {k} | {v} |" for k, v in rows) + "\n\n## Good to know\n- Check the hours.\n")
    return plan, state, guide, json


def _finalised(state, guide):
    from types import SimpleNamespace
    from travel_planner.agents.guide_writer import build_guide_writer
    args = {"markdown_text": guide, "filename_slug": "lisbon"}
    for callback in build_guide_writer().canonical_before_tool_callbacks:
        callback(SimpleNamespace(name="save_guide"), args, SimpleNamespace(state=state))
    return args["markdown_text"]


def test_a_guide_that_follows_the_plan_is_saved_unchanged_but_for_the_day_rhythm():
    from travel_planner.agents.guide_writer import add_day_rhythm
    _, state, guide, _ = _plan_and_guide()
    assert _finalised(state, guide) == add_day_rhythm(guide, state["trip_brief"])


def test_cost_table_is_rebuilt_from_the_plan_when_the_guide_changed_numbers():
    plan, state, guide, _ = _plan_and_guide(cost_rows=[("Transport", 550.0), ("Stay", 156.0), ("**Total**", 1114.0)])
    text = _finalised(state, guide)
    cost = text.split("## Cost estimate", 1)[1].split("\n## ", 1)[0]
    assert "1114" not in cost and "156" not in cost and "908" in cost and "Food" in cost


def test_stay_and_day_items_that_drift_from_the_plan_are_listed():
    _, state, guide, _ = _plan_and_guide(stay_name="Nice Hotel", extra_day2="- **20:30 Scotland Yard** – dinner.")
    still = _finalised(state, guide).split("## Still to check", 1)[1]
    assert "Cheap Guesthouse" in still and "Scotland Yard" in still and "Day 2" in still


def test_wikivoyage_ticket_is_the_adult_price_of_a_price_list():
    from travel_planner.tools.wiki import parse_listings
    text = ("==See==\n"
            "* {{see | name=Belém Tower | price=€6 (adults), €3 (seniors), free (children) | content=Tower.}}\n"
            "* {{see | name=Town Hall Tower | price=Adult 10 zł, conc 8 zł | content=Tower.}}\n"
            "* {{see | name=St Olaf | price=Tower €3 | content=Church.}}\n"
            "* {{see | name=Garden | price=€15-30 | content=Garden.}}\n")
    belem, hall, olaf, garden = parse_listings(text, "https://en.wikivoyage.org/wiki/X", rates={"PLN": 4.0})
    assert (belem.ticket_eur, hall.ticket_eur, olaf.ticket_eur, garden.ticket_eur) == (6.0, 2.5, None, None)
    assert "€6 (adults), €3 (seniors)" in belem.summary  # the full price list stays in the text


def test_researched_places_get_missing_tickets_and_hours_from_the_tool_results():
    import json
    from types import SimpleNamespace
    from travel_planner.agents.enrich import fill_from_tool_results
    from travel_planner.agents.output import extract_json
    output = {"results": [
        {"kind": "sight", "name": "Vabamu Museum of Occupations", "ticket_eur": None, "hours": None,
         "source_urls": ["https://opentripmap.com/en/card/1"]},
        {"kind": "sight", "name": "Kumu Art Museum", "ticket_eur": 11.0, "hours": "Tu-Su 10:00-18:00", "source_urls": []}],
        "notes": "ok."}
    tool = {"status": "ok", "results": [
        {"kind": "sight", "name": "Vabamu", "ticket_eur": 15.0, "hours": "Daily 10:00-18:00",
         "source_urls": ["https://en.wikivoyage.org/wiki/Tallinn"]},
        {"kind": "sight", "name": "Kumu", "ticket_eur": 8.0, "hours": "Th 10:00-20:00", "source_urls": []}]}
    state = {"culture_findings": "```json\n" + json.dumps(output) + "\n```"}
    fill_from_tool_results("culture_findings")(SimpleNamespace(state=state, session=_session_with_tool_result(tool)))
    vabamu, kumu = extract_json(state["culture_findings"])["results"]
    assert (vabamu["ticket_eur"], vabamu["hours"]) == (15.0, "Daily 10:00-18:00")
    assert "https://en.wikivoyage.org/wiki/Tallinn" in vabamu["source_urls"]  # the price keeps its source
    assert (kumu["ticket_eur"], kumu["hours"]) == (11.0, "Tu-Su 10:00-18:00")  # known values are never replaced


def _travel_state(outbound, back, summary_out, summary_back):
    import json
    from tests.test_review import BRIEF, _state, fixed_plan
    from travel_planner.agents.itinerary_tidy import tidy_plan
    plan = fixed_plan()
    plan["transport"] = {"outbound": outbound, "return": back, "why": "cheapest"}
    tidy_plan(plan, BRIEF)
    state = _state(plan)
    state["transport_summary"] = json.dumps({"outbound": {"ranked": summary_out}, "return": {"ranked": summary_back}})
    return state


def test_an_early_return_is_an_important_warning_naming_the_later_option():
    from tests.test_review import FAST_OUT, _leg
    early, late = _leg("Ryanair", "2027-05-13T06:50", "2027-05-13T11:00", 283.0), \
        _leg("Finnair", "2027-05-13T16:35", "2027-05-13T21:00", 387.0)
    state = _travel_state(FAST_OUT, early, [FAST_OUT], [early, late])
    text = _finalised(state, "# Lisbon trip guide\n\nIntro.\n\n## Getting there and back\nx\n")
    important = text.split("## Getting there", 1)[0]
    assert "**Important**" in important and "06:50" in important and "Finnair at 16:35" in important
    assert "€104 more" in important


def test_a_late_arrival_is_an_important_warning_too():
    from tests.test_review import BACK, _leg
    late, early = _leg("Norwegian", "2027-05-11T18:00", "2027-05-11T21:30", 200.0), \
        _leg("Finnair", "2027-05-11T07:00", "2027-05-11T11:00", 260.0)
    state = _travel_state(late, BACK, [late, early], [BACK])
    important = _finalised(state, "# Guide\n\nIntro.\n\n## Getting there and back\nx\n").split("## Getting", 1)[0]
    assert "21:30" in important and "Finnair" in important and "€60 more" in important


def test_convenient_travel_times_give_no_warning():
    from tests.test_review import BACK, FAST_OUT
    state = _travel_state(FAST_OUT, BACK, [FAST_OUT], [BACK])
    assert "Important" not in _finalised(state, "# Guide\n\nIntro.\n\n## Getting there and back\nx\n")


BRIEF_POINTS = {"origin_lat": 60.16952, "origin_lon": 24.93545, "destination_lat": 38.72509, "destination_lon": -9.1498,
                "start_date": "2027-05-10", "end_date": "2027-05-14"}


def _snap(args):
    from types import SimpleNamespace
    from travel_planner.agents.transport import snap_transit_points
    snap_transit_points(SimpleNamespace(name="plan_public_transport"), args,
                        SimpleNamespace(state={"trip_brief": BRIEF_POINTS}))
    return args


def test_a_transit_endpoint_far_from_both_trip_places_is_snapped_to_the_brief():
    # seen live: the return search started at (-9.13659, -9.13659), the longitude copied into both fields
    args = _snap({"from_lat": -9.13659, "from_lon": -9.13659, "to_lat": 60.16952, "to_lon": 24.93545,
                  "depart_date": "2027-05-14"})
    assert (args["from_lat"], args["from_lon"]) == (38.72509, -9.1498)


def test_both_transit_endpoints_wrong_are_set_from_the_travel_date():
    args = _snap({"from_lat": 0.0, "from_lon": 0.0, "to_lat": 1.0, "to_lon": 1.0, "depart_date": "2027-05-10"})
    assert (args["from_lat"], args["to_lat"]) == (60.16952, 38.72509)  # start date: origin -> destination


def test_transit_endpoints_near_the_trip_places_are_left_alone():
    sane = {"from_lat": 60.17, "from_lon": 24.94, "to_lat": 38.70775, "to_lon": -9.13659, "depart_date": "2027-05-10"}
    assert _snap(dict(sane)) == sane


@pytest.mark.parametrize("text, day, want", [
    # seen in the re-recorded Lisbon research (Belém Tower)
    ("9:30 AM–6:00 PM (last entry 5:30 PM); closed Mondays", TUE, [(570, 1080)]),
    ("9:30 AM–6:00 PM (last entry 5:30 PM); closed Mondays", MON, "closed"),
    ("Tu-Su 10am-6pm", WED, [(600, 1080)]),
    ("Daily 12 noon - 11:30 pm", WED, [(720, 1410)]),
])
def test_parse_hours_reads_12_hour_times(text, day, want):
    assert parse_hours(text, day) == want


def test_concierge_follow_up_rule_appears_only_once_a_guide_exists():
    from types import SimpleNamespace
    from travel_planner.agents.concierge import concierge_instruction
    before = concierge_instruction(SimpleNamespace(state={}))
    after = concierge_instruction(SimpleNamespace(state={"trip_brief": {"destination": "Lisbon"},
                                                         "final_guide": "# Lisbon guide", "guide_paths": {}}))
    assert "never answer with a different place" not in before
    assert "never answer with a different place" in after


def test_concierge_explains_what_the_planners_cannot_do():
    from types import SimpleNamespace
    from travel_planner.agents.concierge import concierge_instruction
    live = concierge_instruction(SimpleNamespace(state={}))
    assert "WHAT THE PLANNING TEAM CANNOT DO" in live
    assert "cheapest city" in live and "total_budget_eur" in live
    assert live.index("CANNOT DO") < live.index("AFTER A GUIDE EXISTS")


# ---------------------------------------------------------------- a second plan in the same session

STALE = {"trip_brief": {"destination": "Tallinn", "start_date": "2027-06-01", "end_date": "2027-06-04", "days": 4},
         "culture_findings": "Lisbon sights", "food_findings": "Lisbon food", "flight_options": "HEL-LIS",
         "ground_options": "x", "airbnb_options": "x", "hotel_options": "x", "transport_summary": "x",
         "stay_summary": "x", "itinerary": "Lisbon plan", "final_guide": "# Lisbon trip guide",
         "guide_paths": {"markdown": "lisbon.md"}, "guide_reply": "Saved the Lisbon guide."}


def test_a_new_plan_starts_without_the_previous_plans_results():
    from types import SimpleNamespace
    from travel_planner.agents.pipeline import build_planning_pipeline
    state = dict(STALE)
    for callback in build_planning_pipeline().canonical_before_agent_callbacks:
        callback(SimpleNamespace(state=state))
    assert state["trip_brief"]["destination"] == "Tallinn"  # the new brief stays
    assert all(state[k] is None for k in STALE if k != "trip_brief")


def test_guide_writer_safety_net_saves_the_second_plans_guide(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from travel_planner import config
    from travel_planner.agents.guide_writer import save_reply_if_not_saved
    from travel_planner.agents.pipeline import build_planning_pipeline
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    state = dict(STALE)
    for callback in build_planning_pipeline().canonical_before_agent_callbacks:
        callback(SimpleNamespace(state=state))
    state["guide_reply"] = "# Tallinn trip guide\n\n## Getting there and back\nx\n\n## Day by day\ny\n"
    assert save_reply_if_not_saved(SimpleNamespace(state=state)) is not None
    assert state["final_guide"].startswith("# Tallinn") and (tmp_path / "tallinn-2027-06-01-to-2027-06-04.md").exists()


def test_research_output_is_left_alone_when_the_session_has_no_tool_results():
    import json
    from types import SimpleNamespace
    from travel_planner.agents.grounding import ground_research_output
    text = "```json\n" + json.dumps({"results": [{"source_urls": ["https://x.example/a"]}], "notes": ""}) + "\n```"
    state = {"culture_findings": text}
    ground_research_output("culture_findings")(SimpleNamespace(state=state, session=SimpleNamespace(events=[])))
    assert state["culture_findings"] == text


# ---------------------------------------------------------------- the user's day rhythm (start, end, pace)

def _with_brief(state, **fields):
    return {**state, "trip_brief": {**state["trip_brief"], **fields}}


@pytest.mark.parametrize("change, field", [({"day_start": "7am"}, "day_start"), ({"day_end": "25:00"}, "day_end"),
                                           ({"day_start": "20:00"}, "4 hours"), ({"pace": "fast"}, "pace")])
def test_save_trip_brief_rejects_a_bad_day_rhythm(change, field):
    from types import SimpleNamespace
    from travel_planner.tools.trip import save_trip_brief
    result = save_trip_brief(tool_context=SimpleNamespace(state={}), **{**FUTURE, **change})
    assert result["status"] == "error" and field in result["reason"]


def test_the_day_rhythm_is_saved_only_when_the_user_gave_it():
    from types import SimpleNamespace
    from travel_planner.tools.trip import save_trip_brief
    state = {}
    save_trip_brief(tool_context=SimpleNamespace(state=state), **FUTURE)
    plain = state["trip_brief"]
    assert not {"day_start", "day_end", "pace"} & set(plain) and state["trip_pace_research"] == ""
    save_trip_brief(tool_context=SimpleNamespace(state=state), **FUTURE, day_start="07:00", pace="packed")
    assert state["trip_brief"]["day_start"] == "07:00" and state["trip_brief"]["pace"] == "packed"
    assert state["previous_trip_brief"] == plain  # the brief of the plan before
    assert "choose up to" in state["trip_pace_research"]


def test_only_a_packed_trip_asks_the_culture_agent_for_more_sights():
    from travel_planner.agents.culture import build_culture_agent
    assert build_culture_agent().instruction.endswith("{trip_pace_research?}")


def test_a_brief_without_a_day_rhythm_keeps_the_checks_as_they_were():
    from travel_planner.agents.review_checks import DAY_END, DAY_START, PACES, day_rhythm
    rhythm = day_rhythm({"destination": "Lisbon"})
    assert (rhythm.start, rhythm.end, rhythm.morning) == (DAY_START, DAY_END, 9 * 60 + 30)
    assert rhythm.pace == PACES["balanced"] and (rhythm.user_start, rhythm.user_end) == (None, None)
    balanced = PACES["balanced"]
    assert (balanced.min_sights, balanced.max_sights, balanced.gap_min, balanced.min_between,
            balanced.empty_afternoon, balanced.morning_grace) == (2, 5, 240, 45, 150, 90)


def test_the_pace_sets_how_full_a_day_must_be():
    plan, state = _live_state()
    assert any(x["issue"] == "empty_gap" for x in check_plan_of(plan, state)["should_fix"])  # day 1, balanced
    assert not check_plan_of(plan, _with_brief(state, pace="relaxed"))["should_fix"]  # long breaks are fine
    packed = check_plan_of(plan, _with_brief(state, pace="packed"))["should_fix"]
    assert {x["day"] for x in packed if x["issue"] == "empty_gap"} == {1, 2, 3}  # 16:00-19:30 is too long
    plan["days"][1]["items"] = [i for i in plan["days"][1]["items"] if i["name"] != "Museu do Dinheiro"]
    assert any(x["day"] == 2 and x["issue"] == "thin_day"
               for x in check_plan_of(plan, _with_brief(state, pace="packed"))["should_fix"])
    assert not any(x["issue"] == "thin_day" for x in check_plan_of(plan, _with_brief(state, pace="relaxed"))["should_fix"])


def test_items_outside_the_users_day_hours_are_must_fix():
    plan, state = _live_state()  # dinner at 19:30 on days 1-2; day 2 starts at 10:30, day 3 at 10:00
    found = [(x["day"], x["issue"], x["detail"]) for x in
             check_plan_of(plan, _with_brief(state, day_start="11:00", day_end="19:00"))["must_fix"]
             if x["issue"] in ("before_day_start", "after_day_end")]
    assert [(day, issue) for day, issue, _ in found] == [(1, "after_day_end"), (2, "before_day_start"),
                                                         (2, "after_day_end"), (3, "before_day_start")]
    assert "'MUDE - Museu do Design' at 10:30 is before 11:00" in found[1][2] and "19:30" in found[2][2]
    # without hours from the user nothing is too early or too late, and the transport items never are
    assert not {"before_day_start", "after_day_end"} & set(_issues_of(plan, state, "must_fix"))


def test_a_late_riser_is_not_told_their_morning_is_empty():
    plan, state = _live_state(_late_start_day)  # first item 12:30
    assert "empty_morning" in _issues_of(plan, state, "must_fix")
    assert "empty_morning" not in _issues_of(plan, _with_brief(state, day_start="12:00"), "must_fix")


def test_day_hours_checks_run_in_the_demo_too(monkeypatch):
    plan, state = _live_state()
    monkeypatch.setenv("DEMO", "1")
    assert {"before_day_start", "after_day_end"} <= set(
        _issues_of(plan, _with_brief(state, day_start="11:00", day_end="19:00"), "must_fix"))


def test_concierge_learns_the_optional_day_rhythm():
    from types import SimpleNamespace
    from travel_planner.agents.concierge import concierge_instruction
    live = concierge_instruction(SimpleNamespace(state={}))
    assert "OPTIONAL DAY RHYTHM (never ask for it" in live and '"packed"' in live


def test_planner_rules_follow_the_briefs_day_hours_and_pace():
    from travel_planner.agents.planner import build_itinerary_planner
    instruction = build_itinerary_planner().instruction
    assert "the brief's day_start and day_end (09:30 and 22:00" in instruction and "packed = 4-6" in instruction


def test_the_guide_says_how_its_days_run_and_how_to_change_that():
    from travel_planner.agents.guide_writer import add_day_rhythm, rhythm_sentence
    assert rhythm_sentence({}) == ("Days in this plan start around 09:30 and end by 22:00, at a balanced pace "
                                   "(the defaults).")
    assert rhythm_sentence({"day_start": "07:00", "pace": "packed"}) == \
        "Days in this plan start at 07:00 and end by 22:00, at a packed pace."
    text = add_day_rhythm("# Guide\n\n## Day by day\n### Day 1\n", {})
    assert text.startswith("# Guide\n\n## Day by day\n*Days in this plan") and "tell the concierge" in text
    assert text.endswith("\n\n### Day 1\n")
    assert add_day_rhythm("# Guide without days\n", {}) == "# Guide without days\n"


def test_the_last_chat_message_offers_another_day_rhythm():
    from types import SimpleNamespace
    from travel_planner.agents.guide_writer import finish_guide
    state = {"trip_brief": {"destination": "Lisbon"}, "final_guide": "# Lisbon", "guide_reply": "Saved."}
    content = finish_guide(SimpleNamespace(state=state))
    assert "start around 09:30" in content.parts[0].text and "plan the days again" in content.parts[0].text
    assert finish_guide(SimpleNamespace(state={**state, "final_guide": None})) is None  # nothing saved, no offer
