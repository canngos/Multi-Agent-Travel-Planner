"""Concierge, re-plan and chat fixes from the 2026-11-19 stress test (reports/stress-test-2026-11-19/REPORT.md)."""
import json
from types import SimpleNamespace

from google.genai import types

from travel_planner.agents.approval import _leg, plan_summary, present_plan
from travel_planner.agents.concierge import redact_card_numbers, require_confirmation
from travel_planner.agents.itinerary_tidy import apply_pins
from travel_planner.agents.pipeline import pinned_choices
from travel_planner.agents.review_checks import check_plan
from travel_planner.agents.stay import skip_without_stay
from travel_planner.tools import save_trip_brief

FUTURE = {"destination": "Tallinn", "start_date": "2027-05-15", "end_date": "2027-05-15", "origin": "Helsinki",
          "destination_lat": 59.437, "destination_lon": 24.745, "origin_lat": 60.17, "origin_lon": 24.94,
          "destination_country": "Estonia"}


def _confirm(**extra) -> dict:
    ctx = SimpleNamespace(state={}, invocation_id="turn-1")
    return require_confirmation(SimpleNamespace(name="save_trip_brief"), {**FUTURE, **extra}, ctx)


def test_a_day_trip_is_saved_with_no_stay_searched():
    state = {}
    result = save_trip_brief(tool_context=SimpleNamespace(state=state), **FUTURE)
    assert result["status"] == "ok" and state["trip_brief"]["nights"] == 0 and state["trip_brief"]["days"] == 1
    assert "day trip" in state["trip_requirements"]
    content = skip_without_stay(SimpleNamespace(state=state))
    assert "day trip" in content.parts[0].text and json.loads(state["stay_summary"].strip("`json\n"))["cheapest"] is None


def test_a_day_trip_plan_passes_the_day_checks():
    brief = {**FUTURE, "travellers": 2, "budget_tier": "mid", "nights": 0, "days": 1}
    out = {"mode": "ferry", "operator": "Tallink", "depart": "2027-05-15T07:30", "arrive": "2027-05-15T09:30",
           "duration_min": 120, "price_eur": 60.0, "details": ""}
    back = {**out, "depart": "2027-05-15T19:30", "arrive": "2027-05-15T21:30"}
    plan = {"transport": {"outbound": out, "return": back}, "stay": None, "days": [{
        "day": 1, "date": "2027-05-15", "items": [
            {"time": "09:30", "kind": "transport", "name": "Arrive in Tallinn"},
            {"time": "11:00", "kind": "sight", "name": "Old Town", "lat": 59.437, "lon": 24.745, "ticket_eur": 0},
            {"time": "18:00", "kind": "sight", "name": "Late Museum", "lat": 59.437, "lon": 24.745, "ticket_eur": 0},
            {"time": "19:30", "kind": "transport", "name": "Depart from Tallinn"}]}]}
    report = check_plan(plan, {"trip_brief": brief})
    issues = {x["issue"] for x in report["must_fix"]}
    assert "day_count" not in issues and "empty_day" not in issues
    assert "no_time_to_leave" in issues  # 18:00 + about an hour runs into the 1 h before the 19:30 ferry


def test_confirmation_states_the_length_and_warns_about_budget_and_groups():
    reason = _confirm(end_date="2027-05-19", travellers=2, total_budget_eur=200)["reason"]
    assert "5 days and 4 nights" in reason and "budget looks too small" in reason
    assert "a day trip (1 day, no night)" in _confirm()["reason"]
    assert "group desk" in _confirm(end_date="2027-05-18", travellers=14)["reason"]


def test_card_numbers_never_reach_the_model():
    request = SimpleNamespace(contents=[
        types.Content(role="user", parts=[types.Part(text="Book it with my card 4111 1111 1111 1111 please")])])
    redact_card_numbers(None, request)
    text = request.contents[0].parts[0].text
    assert "4111" not in text and "[card number removed]" in text and "never books or pays" in text


def test_a_day_swap_replan_keeps_the_previous_transport():
    previous = {"transport": {"outbound": {"operator": "Finnair", "depart": "2027-05-10T07:00"}, "return": None},
                "stay": {"name": "Hotel A", "total_price_eur": 300}}
    brief = {"destination": "Riga", "pace": "relaxed"}
    pins = pinned_choices({"rerun": [], "base": {"destination": "Riga"}, "change_note": "swap days 2 and 3"},
                          previous, brief)
    assert pins["transport"] == previous["transport"] and pins["stay"] == previous["stay"]
    plan = {"transport": {"outbound": {"operator": "airBaltic", "depart": "2027-05-10T09:15"}}, "stay": None}
    assert apply_pins(plan, {"pinned_choices": pins}) and plan["transport"]["outbound"]["operator"] == "Finnair"
    assert pinned_choices({"rerun": ["stay"], "base": {}, "change_note": "a cheaper hotel"}, previous, {}).get("stay") is None
    assert pinned_choices({"rerun": [], "base": {"total_budget_eur": 900}, "change_note": "cheaper"}, previous,
                          {"total_budget_eur": 600}) is None


def test_the_same_cheapest_stay_after_a_cheaper_hotel_request_is_explained():
    stay = {"kind": "hotel", "name": "Bresson Hostel", "total_price_eur": 64.0}
    state = {"itinerary": json.dumps({"days": [{"day": 1, "items": []}], "stay": stay}),
             "trip_brief": {"destination": "Krakow"}, "previous_plan_choices": {"stay": stay},
             "plan_change_note": "CHANGE REQUESTED BY THE TRAVELLERS: a cheaper hotel",
             "stay_summary": json.dumps({"cheapest": stay})}
    content = present_plan(SimpleNamespace(state=state))
    assert "already the cheapest" in content.parts[0].text


def test_a_multi_day_leg_shows_plus_one_day():
    line = _leg("Outbound", {"operator": "FlixBus", "mode": "bus", "depart": "2027-04-20T14:38",
                             "arrive": "2027-04-21T19:09", "duration_min": 1711, "price_eur": 90})
    assert "+1 day, 28 h 31 min" in line
    assert "a day trip" in plan_summary({"days": []}, {"nights": 0, "destination": "Tallinn"})


def test_the_guide_writers_own_reply_is_held_and_a_code_message_sent():
    from google.adk.models.llm_response import LlmResponse
    from travel_planner.agents.guide_writer import finish_guide, hold_guide_reply
    state = {"trip_brief": {"destination": "Kyiv"}}
    reply = LlmResponse(content=types.Content(role="model", parts=[types.Part(
        text=r"Saved to C:\Users\someone\AppData\Local\Temp\x\kyiv.md")]))
    out = hold_guide_reply(SimpleNamespace(state=state), reply)  # no guide saved yet: a short neutral line
    assert out.content.parts[0].text == "Saving the trip guide." and "AppData" in state["guide_reply_raw"]
    call = LlmResponse(content=types.Content(role="model", parts=[types.Part(
        function_call=types.FunctionCall(name="save_guide", args={}))]))
    assert hold_guide_reply(SimpleNamespace(state=state), call) is None
    plan = {"days": [{"day": 1}], "transport": {"outbound": {"operator": "Finnair", "mode": "flight",
                                                             "depart": "2027-06-10T06:20"}},
            "stay": {"name": "City Loft", "total_price_eur": 25.0},
            "cost": {"total_eur": 1090.0, "budget_eur": 1200.0, "within_budget": True, "difference_eur": 110.0}}
    state.update(final_guide="# Kyiv", itinerary=json.dumps(plan), guide_exports={"calendar": "x", "map": "y"},
                 guide_paths={"markdown": r"C:\out\kyiv.md", "html": r"C:\out\kyiv.html",
                              "calendar": r"C:\out\kyiv.ics", "map": r"C:\out\kyiv.geojson"})
    text = finish_guide(SimpleNamespace(state=state)).parts[0].text
    assert "kyiv.md" in text and "kyiv.geojson" in text and "C:\\" not in text
    assert "Finnair" in text and "City Loft" in text and "within your €1200 budget" in text


def test_links_without_a_target_are_unlinked():
    from travel_planner.agents.grounding import ground_markdown
    text, _, _ = ground_markdown("Palace – [source](null). See [Casino](None).", {})
    assert text == "Palace. See Casino."


def test_the_guide_ready_message_replaces_the_reply_once():
    from google.adk.models.llm_response import LlmResponse
    from travel_planner.agents.guide_writer import finish_guide, hold_guide_reply
    plan = {"days": [{"day": 1}], "cost": {"total_eur": 500.0}}
    state = {"trip_brief": {"destination": "Riga"}, "final_guide": "# Riga", "itinerary": json.dumps(plan),
             "guide_exports": {"calendar": "x", "map": "y"}, "guide_paths": {"markdown": r"C:\out\riga.md"}}
    ctx = SimpleNamespace(state=state, invocation_id="inv-1")
    reply = LlmResponse(content=types.Content(role="model", parts=[types.Part(text=r"Saved to C:\temp\riga.md")]))
    text = hold_guide_reply(ctx, reply).content.parts[0].text
    assert "riga.md" in text and "C:\\" not in text and "Estimated total: €500" in text
    assert finish_guide(ctx) is None  # not sent twice


def test_a_card_number_gets_a_fixed_warning_in_the_next_reply():
    from google.adk.models.llm_response import LlmResponse
    from travel_planner.agents.concierge import CARD_WARNING, warn_about_card
    ctx = SimpleNamespace(state={})
    request = SimpleNamespace(contents=[types.Content(role="user", parts=[types.Part(text="card 4111 1111 1111 1111")])])
    redact_card_numbers(ctx, request)
    reply = LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Here is your summary.")]))
    assert warn_about_card(ctx, reply) is None and reply.content.parts[0].text.startswith(CARD_WARNING)
    again = LlmResponse(content=types.Content(role="model", parts=[types.Part(text="Next answer.")]))
    warn_about_card(ctx, again)
    assert again.content.parts[0].text == "Next answer."  # warned once


def test_a_cheaper_stay_request_that_got_a_dearer_stay():
    from travel_planner.agents.review_checks import _cheaper_stay_issue
    before = {"kind": "airbnb", "name": "Room at Zosia's", "total_price_eur": 68.0}
    new = {"kind": "hotel", "name": "K12 Apartments", "total_price_eur": 96.0}
    state = {"previous_plan_choices": {"stay": before}, "trip_brief": {"budget_tier": "mid"},
             "plan_change_note": "CHANGE REQUESTED BY THE TRAVELLERS: a cheaper hotel"}
    summary = {"cheapest": {"kind": "hotel", "name": "Cheap Inn", "total_price_eur": 60.0},
               "by_area": [{"hotel": {"kind": "hotel", "name": "Wow Hostel", "total_price_eur": 40.0}}]}
    issue = _cheaper_stay_issue({"stay": new}, state, summary)
    assert issue["issue"] == "change_not_met" and [a["name"] for a in issue["alternatives"]] == ["Cheap Inn"]
    assert _cheaper_stay_issue({"stay": new}, state, {"cheapest": new}) is None  # nothing cheaper to offer
    content = present_plan(SimpleNamespace(state={**state, "itinerary": json.dumps({"days": [{"day": 1}], "stay": new}),
                                                  "stay_summary": json.dumps({"cheapest": new})}))
    assert "no suitable stay cheaper" in content.parts[0].text
