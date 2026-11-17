"""Phase 8 review loop: code checks, verdict and revision guards, and the loop itself with a scripted model."""
import copy
import json
from datetime import date
from types import SimpleNamespace

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import Field

from travel_planner.agents.budget_critic import enforce_verdict, verdict_of
from travel_planner.agents.itinerary_reviser import gate_exit_loop, guard_revision
from travel_planner.agents.itinerary_tidy import tidy_plan
from travel_planner.agents.output import extract_json
from travel_planner.agents.pipeline import build_review_loop
from travel_planner.agents.review_checks import check_plan, find_place, parse_hours

BRIEF = {"origin": "Helsinki", "destination": "Lisbon", "start_date": "2027-05-11", "end_date": "2027-05-13",
         "travellers": 2, "budget_tier": "mid", "total_budget_eur": 1500.0, "nights": 2, "days": 3}


def _sight(name, lat, lon, hours, ticket):
    return {"kind": "sight", "name": name, "lat": lat, "lon": lon, "hours": hours, "ticket_eur": ticket,
            "source_urls": [f"https://example.org/{name.split()[0].lower()}"]}


def _food(name, lat, lon, hours):
    return {"kind": "restaurant", "name": name, "lat": lat, "lon": lon, "hours": hours, "budget_tier": "mid",
            "source_urls": ["https://example.org/food"]}


def _leg(operator, depart, arrive, price):
    return {"mode": "flight", "operator": operator, "depart": depart, "arrive": arrive, "duration_min": 300,
            "stops": 0, "price_eur": price, "price_source": "live", "source": "kiwi", "url": f"https://kiwi.com/u/{operator}"}


def _stay(name, total):
    return {"kind": "hotel", "name": name, "area": "Baixa", "lat": 38.7110, "lon": -9.1370,
            "price_per_night_eur": total / 2, "total_price_eur": total, "price_source": "live", "source": "x",
            "url": "https://example.org/stay"}


FAST_OUT = _leg("Finnair", "2027-05-11T06:00", "2027-05-11T10:00", 400.0)
CHEAP_OUT = _leg("Norwegian", "2027-05-11T07:00", "2027-05-11T11:00", 200.0)
BACK = _leg("TAP", "2027-05-13T18:00", "2027-05-13T23:00", 150.0)
NICE, CHEAP = _stay("Nice Hotel", 600.0), _stay("Cheap Hostel", 150.0)


def _state(plan) -> dict:
    culture = [_sight("São Jorge Castle", 38.7139, -9.1335, "Mar-Oct 09:00-21:00", 17.0),
               _sight("Roman Theatre Museum", 38.7105, -9.1324, "Tu-Su 10:00-18:00", 3.0),
               _sight("MUDE - Museu do Design", 38.7091, -9.1366, "Tu-Su 10:00-18:00", None),
               _sight("Museu do Aljube", 38.7102, -9.1326, "Tu-Su 10:00-19:00", 3.0),
               _sight("Museu Arqueológico do Carmo", 38.7120, -9.1406, "Tu-Su 10:00-18:00", 7.0),
               _sight("Museu do Dinheiro", 38.7086, -9.1380, "We-Sa 10:00-18:00", 0.0)]
    food = [_food("Leitaria Camponeza", 38.7117, -9.1386, "Daily 19:00-23:00"),
            _food("Faca & Garfo", 38.7125, -9.1410, "Mon-Sat 08:00-24:00"),
            _food("Far Away Diner", 38.7500, -9.2000, "Daily 12:00-22:00"),
            _food("Taberna Nearby", 38.7108, -9.1330, "Daily 12:00-23:00")]
    return {
        "trip_brief": dict(BRIEF),
        "culture_findings": json.dumps({"results": culture}, ensure_ascii=False),
        "food_findings": json.dumps({"results": food, "dishes": []}, ensure_ascii=False),
        "transport_summary": json.dumps({"outbound": {"cheapest": CHEAP_OUT, "fastest": FAST_OUT, "ranked": [FAST_OUT, CHEAP_OUT]},
                                         "return": {"cheapest": BACK, "fastest": BACK, "ranked": [BACK]}}),
        "stay_summary": json.dumps({"cheapest": CHEAP, "best_value": NICE, "top_rated": NICE, "by_area": []}),
        "itinerary": "```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```",
    }


def _item(time, kind, name, ticket=None, lat=None, lon=None, note=""):
    return {"time": time, "kind": kind, "name": name, "note": note, "ticket_eur": ticket, "hours": None,
            "lat": lat, "lon": lon, "source_url": None}


def bad_plan() -> dict:
    """Over budget (best-value stay, fastest flight), MUDE before opening, an invented museum, a wrong ticket,
    a repeated restaurant, an empty afternoon without lunch and a far-away lunch."""
    return {"transport": {"outbound": copy.deepcopy(FAST_OUT), "return": copy.deepcopy(BACK), "why": "fastest"},
            "stay": copy.deepcopy(NICE), "stay_why": "best value", "weather_kind": "typical",
            "days": [
                {"day": 1, "date": "2027-05-11", "weekday": "Tue", "items": [
                    _item("10:00", "transport", "Arrive in Lisbon", note="Flight with Finnair"),
                    _item("12:00", "sight", "São Jorge Castle", 17.0, 38.7139, -9.1335),
                    _item("13:30", "restaurant", "Faca & Garfo", None, 38.7125, -9.1410),
                    _item("15:00", "sight", "Roman Theatre Museum", 3.0, 38.7105, -9.1324),
                    _item("19:30", "restaurant", "Leitaria Camponeza", None, 38.7117, -9.1386)]},
                {"day": 2, "date": "2027-05-12", "weekday": "Wed", "items": [
                    _item("09:00", "sight", "MUDE - Museu do Design", None, 38.7091, -9.1366),
                    _item("13:00", "sight", "Museum of Invented Things", 10.0, 38.7100, -9.1350),
                    _item("19:30", "restaurant", "Leitaria Camponeza", None, 38.7117, -9.1386)]},
                {"day": 3, "date": "2027-05-13", "weekday": "Thu", "items": [
                    _item("10:00", "sight", "Museu do Aljube", 5.0, 38.7102, -9.1326),
                    _item("12:30", "restaurant", "Far Away Diner", None, 38.7500, -9.2000),
                    _item("18:00", "transport", "Depart from Lisbon", note="Flight with TAP")]}],
            "cost": None, "notes": ""}


def fixed_plan() -> dict:
    plan = bad_plan()
    plan["stay"] = copy.deepcopy(CHEAP)
    day2 = plan["days"][1]["items"]
    day2[0]["time"] = "10:30"
    day2[1].update(name="Museu Arqueológico do Carmo", ticket_eur=7.0, lat=38.7120, lon=-9.1406, time="14:00")
    day2.insert(1, _item("12:30", "restaurant", "Taberna Nearby", None, 38.7108, -9.1330))
    day2.insert(3, _item("16:00", "sight", "Museu do Dinheiro", 0.0, 38.7086, -9.1380))
    day2[-1] = _item("19:30", "free", "Dinner near Baixa")
    day3 = plan["days"][2]["items"]
    day3[0]["ticket_eur"] = 3.0
    day3[1] = _item("12:30", "free", "Lunch near Alfama")
    plan["changes"] = ["cheaper stay", "MUDE 10:30", "Carmo instead of an unknown museum"]
    return plan


# ---------------------------------------------------------------- opening hours

def test_parse_hours_reads_the_research_formats():
    mon, tue, sun = date(2027, 5, 10), date(2027, 5, 11), date(2027, 5, 16)
    assert parse_hours("Tu-Su 10:00-18:00", mon) == "closed"
    assert parse_hours("Tu-Su 10:00-18:00", tue) == [(600, 1080)]
    assert parse_hours("May‑Sep 10:00-18:30; Oct‑Apr 10:00-17:30; closed Mon", mon) == "closed"
    assert parse_hours("May‑Sep 10:00-18:30; Oct‑Apr 10:00-17:30; closed Mon", tue) == [(600, 1110)]
    assert parse_hours("Tue‑Sun 12:30‑15:30 & 19:00‑22:30; closed Mon", tue) == [(750, 930), (1140, 1350)]
    assert parse_hours("Mon‑Fri 12:00-15:30 19:30-22:30; Sat 19:30-22:30", sun) == "closed"
    assert parse_hours("Sun‑Thu 12:00-24:00; Fri‑Sat 12:00-01:00", date(2027, 5, 14)) == [(720, 1500)]
    assert parse_hours("Daily 19:00-23:00", mon) == [(1140, 1380)]
    assert parse_hours("Tu-Su 10:00-18:00; Jan 01,May 01,Dec 25 off", date(2027, 5, 1)) == "closed"  # a Saturday
    assert parse_hours("Oct-May Tue-Sun 10:00-17:30; closed 1 Jan, Easter, 1 May", tue) == [(600, 1050)]
    assert parse_hours("open daily, see website", tue) is None and parse_hours(None, tue) is None


def test_find_place_ignores_accents_case_and_extra_words():
    places = [{"name": "Mosteiro dos Jerónimos"}, {"name": "Faca & Garfo"}]
    assert find_place("mosteiro dos jeronimos", places)["name"] == "Mosteiro dos Jerónimos"
    assert find_place("Faca & Garfo restaurant", places)["name"] == "Faca & Garfo"
    assert find_place("Museum of Invented Things", places) is None


# ---------------------------------------------------------------- checks

def _issues(report, level):
    return {(x["day"], x["issue"]) for x in report[level]}


def test_check_plan_finds_every_planted_problem():
    plan = bad_plan()
    report = check_plan(plan, _state(plan))
    assert _issues(report, "must_fix") == {(None, "over_budget"), (2, "outside_hours"), (2, "ungrounded_place"),
                                           (3, "ungrounded_price")}
    assert {(2, "repeated_place"), (2, "missing_meal"), (2, "empty_gap"), (3, "far_meal")} <= _issues(report, "should_fix")
    budget = next(x for x in report["must_fix"] if x["issue"] == "over_budget")["detail"]
    assert "€1568 is €68 over" in budget and "Cheap Hostel" in budget and "Norwegian" in budget
    gap = next(x for x in report["should_fix"] if x["issue"] == "empty_gap")
    assert {a["name"] for a in gap["alternatives"]} >= {"Museu Arqueológico do Carmo", "Museu do Dinheiro"}
    meal = next(x for x in report["should_fix"] if x["issue"] == "missing_meal")
    assert [a["name"] for a in meal["alternatives"]] == ["Taberna Nearby"]  # unused and close; Far Away Diner is used


def test_check_plan_passes_the_fixed_plan():
    plan = fixed_plan()
    report = check_plan(plan, _state(plan))
    assert report["must_fix"] == []
    assert report["facts"]["total_eur"] == 1108.0  # 550 transport + 150 stay + 300 food + 48 local + (17+3+7+0+3) x 2


def test_check_plan_flags_options_not_in_the_summaries_and_closed_days():
    plan = fixed_plan()
    plan["stay"]["total_price_eur"] = 99.0
    plan["transport"]["outbound"]["price_eur"] = 10.0
    plan["days"][1]["items"][-2]["name"] = "Roman Theatre Museum"  # 16:00 on a Wednesday: open
    plan["days"][1]["date"] = "2027-05-17"  # a Monday, and the wrong date
    issues = _issues(check_plan(plan, _state(plan)), "must_fix")
    assert {(None, "ungrounded_stay"), (None, "ungrounded_transport"), (2, "wrong_date"), (2, "closed")} <= issues


def test_over_budget_without_cheaper_options_is_only_should_fix():
    plan = fixed_plan()
    for day in plan["days"]:
        for item in day["items"]:
            if item["kind"] == "sight" and item["ticket_eur"] is not None:
                item["ticket_eur"] = 0.0
    state = _state(plan)
    state["trip_brief"]["total_budget_eur"] = 800.0  # plan costs 848
    state["culture_findings"] = state["culture_findings"].replace('"ticket_eur": 17.0', '"ticket_eur": 0.0') \
        .replace('"ticket_eur": 3.0', '"ticket_eur": 0.0').replace('"ticket_eur": 7.0', '"ticket_eur": 0.0')
    plan["transport"]["outbound"] = copy.deepcopy(CHEAP_OUT)
    report = check_plan(plan, state)
    assert report["must_fix"] == []
    assert "already uses the cheapest" in next(x for x in report["should_fix"] if x["issue"] == "over_budget")["detail"]


def test_tidy_recomputes_cost_and_walk_km():
    plan = bad_plan()
    plan["cost"] = {"total_eur": 1400.0}
    changes = tidy_plan(plan, BRIEF)
    assert plan["cost"]["total_eur"] == 1568.0 and plan["cost"]["categories_eur"]["activities"] == 70.0
    assert any("cost recomputed" in c for c in changes)
    assert plan["days"][0]["walk_km"] > 0 and isinstance(plan["days"][0]["walk_km"], float)


# ---------------------------------------------------------------- callbacks

def test_enforce_verdict_overrides_an_approval_with_must_fix_problems():
    state = _state(bad_plan()) | {"critique": "Looks great.\nVERDICT: **APPROVED**", "review_round": 1}
    enforce_verdict(SimpleNamespace(state=state))
    assert state["review_verdict"] == "REVISE" and state["critique"].endswith("VERDICT: REVISE")
    assert "MUST FIX (found by the code check" in state["critique"] and "Museum of Invented Things" in state["critique"]
    assert state["review_log"][-1]["overridden"] is True and json.loads(state["itinerary_check"])["must_fix"]

    state = _state(fixed_plan()) | {"critique": "MUST FIX:\nnone\nVERDICT: APPROVED", "review_round": 2}
    enforce_verdict(SimpleNamespace(state=state))
    assert state["review_verdict"] == "APPROVED" and state["critique"].count("VERDICT") == 1
    assert verdict_of("all fine, approved?") is None and verdict_of("...\nREVISE") == "REVISE"


def test_exit_loop_is_refused_while_the_verdict_is_revise():
    tool = SimpleNamespace(name="exit_loop")
    ctx = SimpleNamespace(state={"review_verdict": "REVISE"})
    assert gate_exit_loop(tool, {}, ctx)["status"] == "error"
    ctx.state["review_verdict"] = "APPROVED"
    assert gate_exit_loop(tool, {}, ctx) is None and ctx.state["review_exit_called"] is True


def test_guard_revision_keeps_the_plan_safe():
    good = _state(fixed_plan())["itinerary"]

    def ctx(**state):
        return SimpleNamespace(state={"trip_brief": BRIEF, "itinerary_before_revision": good, **state},
                               actions=SimpleNamespace(escalate=False))

    approved = ctx(itinerary="APPROVED, nothing to change", review_verdict="APPROVED", review_exit_called=False)
    guard_revision(approved)  # text instead of exit_loop: plan restored, loop ended in code
    assert approved.state["itinerary"] == good and approved.actions.escalate is True

    broken = ctx(itinerary='```json\n{"days": [1, 2]}\n```', review_verdict="REVISE")
    guard_revision(broken)
    assert broken.state["itinerary"] == good and "restored" in broken.state["revision_log"][-1]

    revised = _state(fixed_plan())["itinerary"]
    kept = ctx(itinerary=revised, review_verdict="REVISE")
    guard_revision(kept)
    assert kept.actions.escalate is False
    expected = fixed_plan()
    changes = expected.pop("changes")
    assert extract_json(kept.state["itinerary"]) == expected  # kept, minus "changes" (logged instead)
    assert kept.state["revision_log"][-1]["changes"] == changes


# ---------------------------------------------------------------- the loop with a scripted model

class ScriptedLlm(BaseLlm):
    """Stands in for GPT OSS: the critic always 'approves', the reviser either fixes the plan or repeats it."""
    model: str = "scripted"
    fixes: bool = False
    calls: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        system = str(llm_request.config.system_instruction or "")
        if "BUDGET CRITIC" in system:
            self.calls.append("critic")
            part = types.Part(text="MUST FIX:\nnone\nSHOULD FIX:\nnone\nKEEP: all of it.\nVERDICT: APPROVED")
        elif "VERDICT OF THE REVIEW: APPROVED" in system:
            self.calls.append("reviser:exit_loop")
            part = types.Part(function_call=types.FunctionCall(name="exit_loop", args={}))
        else:
            self.calls.append("reviser:revise")
            plan = fixed_plan() if self.fixes else bad_plan()
            part = types.Part(text="```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


async def _run_loop(llm) -> dict:
    runner = InMemoryRunner(agent=build_review_loop(llm), app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u", state=_state(bad_plan()))
    async for _ in runner.run_async(user_id="u", session_id=session.id,
                                    new_message=types.Content(role="user", parts=[types.Part(text="Start.")])):
        pass
    state = (await runner.session_service.get_session(app_name="t", user_id="u", session_id=session.id)).state
    await runner.close()
    return state


async def test_over_budget_plan_is_revised_then_approved():
    llm = ScriptedLlm(fixes=True)
    state = await _run_loop(llm)
    assert llm.calls == ["critic", "reviser:revise", "critic", "reviser:exit_loop"]
    assert [(r["round"], r["verdict"], r["overridden"]) for r in state["review_log"]] == \
        [(1, "REVISE", True), (2, "APPROVED", False)]
    plan = extract_json(state["itinerary"])
    assert plan["stay"]["name"] == "Cheap Hostel" and plan["cost"]["within_budget"] is True
    assert state["revision_log"][-1]["exit"] == "exit_loop"


async def test_loop_never_runs_more_than_three_rounds():
    llm = ScriptedLlm(fixes=False)
    state = await _run_loop(llm)
    assert llm.calls == ["critic", "reviser:revise"] * 3
    assert [r["verdict"] for r in state["review_log"]] == ["REVISE"] * 3
    assert len(state["revision_log"]) == 3


# ---------------------------------------------------------------- retry after a malformed tool call

class MalformedOnceLlm(BaseLlm):
    """First answer: a tool call whose arguments did not parse (as LiteLlm reports it); then a normal reply."""
    model: str = "malformed-once"
    seen: list = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        replies = [p.function_response for c in llm_request.contents for p in c.parts or [] if p.function_response]
        self.seen.append([r.name for r in replies])
        if not replies:
            yield LlmResponse(content=types.Content(role="model", parts=[]),
                              finish_reason=types.FinishReason.MALFORMED_FUNCTION_CALL,
                              error_code=types.FinishReason.MALFORMED_FUNCTION_CALL,
                              error_message="Arguments for the following function calls were not a valid JSON object: "
                                            "calculate_trip_cost")
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text='{"done": true}')]))


async def test_malformed_tool_call_gets_a_retry():
    from google.adk.agents import LlmAgent
    from travel_planner.agents.retry import RETRY_TOOL, offer_retry_tool, retry_malformed_call
    llm = MalformedOnceLlm()
    agent = LlmAgent(name="planner_like", model=llm, instruction="Plan.", output_key="out", include_contents="none",
                     before_model_callback=offer_retry_tool, after_model_callback=retry_malformed_call)
    runner = InMemoryRunner(agent=agent, app_name="t")
    session = await runner.session_service.create_session(app_name="t", user_id="u")
    async for _ in runner.run_async(user_id="u", session_id=session.id,
                                    new_message=types.Content(role="user", parts=[types.Part(text="Start.")])):
        pass
    state = (await runner.session_service.get_session(app_name="t", user_id="u", session_id=session.id)).state
    await runner.close()
    assert llm.seen == [[], [RETRY_TOOL]] and state["out"] == '{"done": true}'
    assert RETRY_TOOL not in {t.__name__ for t in agent.tools if hasattr(t, "__name__")}  # never declared


def test_should_fix_only_revision_needs_a_fix_and_progress():
    from travel_planner.agents.budget_critic import worth_revising
    gap_with_alt = {"day": 2, "issue": "empty_gap", "alternatives": [{"name": "Museu do Dinheiro"}]}
    gap_without = {"day": 3, "issue": "empty_gap", "alternatives": []}
    assert worth_revising([gap_with_alt], None, 1)
    assert not worth_revising([gap_without], None, 1)                     # nothing researched to add
    assert not worth_revising([gap_with_alt], {"should_fix": 1}, 2)       # no progress since round 1
    assert worth_revising([gap_with_alt], {"should_fix": 3}, 2)
    assert not worth_revising([gap_with_alt], None, 3)                    # last round

    state = _state(fixed_plan()) | {"critique": "SHOULD FIX:\n1. Day 3: add something.\nVERDICT: REVISE", "review_round": 3}
    enforce_verdict(SimpleNamespace(state=state))
    assert state["review_verdict"] == "APPROVED" and "Approved by the code check" in state["critique"]


def test_budget_options_skip_flights_that_lose_a_trip_day():
    """Regression: the critic suggested a night flight arriving a day later to save €4."""
    plan = bad_plan()
    state = _state(plan)
    late = _leg("Ryanair", "2027-05-10T23:15", "2027-05-12T09:55", 390.0)  # arrives a day later, saves only €10
    state["transport_summary"] = state["transport_summary"].replace('"Norwegian"', '"Ryanair"') \
        .replace('"2027-05-11T07:00"', '"2027-05-10T23:15"').replace('"2027-05-11T11:00"', '"2027-05-12T09:55"')
    budget = next(x for x in check_plan(plan, state)["must_fix"] if x["issue"] == "over_budget")["detail"]
    assert "Ryanair" not in budget and "Cheap Hostel" in budget and "fixed typical costs" in budget
    assert late["arrive"][:10] != FAST_OUT["arrive"][:10]


def test_ground_tickets_restores_research_prices():
    """Regression: the reviser set real ticket prices to 0 to fit the budget."""
    from travel_planner.agents.itinerary_tidy import ground_tickets
    from travel_planner.agents.review_checks import research_places
    plan = bad_plan()
    plan["days"][0]["items"][1]["ticket_eur"] = 0.0          # São Jorge Castle, research says 17
    changes = ground_tickets(plan, research_places(_state(plan)))
    tickets = {i["name"]: i["ticket_eur"] for d in plan["days"] for i in d["items"] if i["kind"] == "sight"}
    assert tickets["São Jorge Castle"] == 17.0 and tickets["Museu do Aljube"] == 3.0
    assert tickets["Museum of Invented Things"] == 10.0      # not researched: left for the critic to flag
    assert len(changes) == 2


def test_late_arrival_is_must_fix_when_a_same_day_option_exists():
    plan = fixed_plan()
    late = _leg("Ryanair", "2027-05-10T23:15", "2027-05-12T09:55", 195.0)
    plan["transport"]["outbound"] = late
    state = _state(plan)
    summary = json.loads(state["transport_summary"])
    summary["outbound"]["ranked"].append(late)
    state["transport_summary"] = json.dumps(summary)
    lost = [x for x in check_plan(plan, state)["must_fix"] if x["issue"] == "travel_day_lost"]
    assert len(lost) == 1 and "2027-05-11" in lost[0]["detail"]


def test_keep_best_plan_undoes_a_worse_last_revision():
    from travel_planner.agents.itinerary_reviser import keep_best_plan
    good, bad = _state(fixed_plan())["itinerary"], _state(bad_plan())["itinerary"]
    state = _state(fixed_plan()) | {"itinerary_before_revision": good, "itinerary": bad, "review_round": 3,
                                    "review_verdict": "REVISE", "revision_log": [{"round": 3}]}
    keep_best_plan(SimpleNamespace(state=state))
    assert state["itinerary"] == good and "kept the plan from round 0" in state["revision_log"][-1]["restored"]

    state = _state(fixed_plan()) | {"itinerary_before_revision": bad, "itinerary": good, "review_round": 1,
                                    "review_verdict": "REVISE"}
    keep_best_plan(SimpleNamespace(state=state))
    assert state["itinerary"] == good and state["best_itinerary"]["round"] == 1
