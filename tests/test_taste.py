"""REVIEW.md A4: day-shape findings are the plan critic's decision; code checks facts and that each one is decided."""
from types import SimpleNamespace

import pytest

from tests.test_edge_cases import _afternoon_day, _live_state
from tests.test_review import _item
from travel_planner.agents.guide_writer import open_issues
from travel_planner.agents.plan_critic import (declines_of, enforce_verdict, reason_is_specific)
from travel_planner.agents.review_checks import check_plan

REASON = "the pace is relaxed and the morning museum visit is long, so the travellers rest in the afternoon"


def _empty_afternoon_state(critique: str, round_no: int = 1) -> dict:
    """A plan whose only open point is a whole empty afternoon on day 2 (a "decide" item)."""
    plan, state = _live_state(_afternoon_day([
        _item("10:30", "sight", "MUDE - Museu do Design", None, 38.7091, -9.1366),
        _item("12:30", "restaurant", "Taberna Nearby", None, 38.7108, -9.1330),
        _item("19:30", "free", "Dinner near Baixa")]))
    report = check_plan(plan, state)
    assert not report["must_fix"] and [x["issue"] for x in report["decide"]] == ["empty_afternoon"]
    state.update(critique=critique, review_round=round_no)
    return state


def _verdict(critique: str, round_no: int = 1) -> dict:
    state = _empty_afternoon_state(critique, round_no)
    enforce_verdict(SimpleNamespace(state=state))
    return state


def test_a_decline_with_a_reason_about_this_trip_is_the_critics_decision():
    state = _verdict(f"MUST FIX:\nnone\nDECLINED:\n1. Day 2 empty_afternoon: {REASON}.\nSHOULD FIX:\nnone\n"
                     "KEEP: all.\nVERDICT: APPROVED")
    assert state["review_verdict"] == "APPROVED"
    entry = state["review_log"][-1]
    assert entry["overridden"] is False and entry["decide"] == 1 and entry["declines_refused"] == []
    assert entry["declined"] == [{"day": 2, "issue": "empty_afternoon", "reason": REASON + "."}]
    assert state["taste_declined"] == entry["declined"]


def test_a_finding_the_critic_does_not_decide_must_be_fixed():
    """The fault seen live: GPT OSS approved a weak day without a word about it."""
    state = _verdict("MUST FIX:\nnone\nSHOULD FIX:\nnone\nKEEP: a lovely plan.\nVERDICT: APPROVED")
    assert state["review_verdict"] == "REVISE" and state["review_log"][-1]["overridden"] is True
    assert "not fixed or declined with a reason" in state["critique"] and "12:30-19:30" in state["critique"]


@pytest.mark.parametrize("reason", ["fine", "It's acceptable.", "minor", "not a big deal", "ok for now"])
def test_a_decline_with_a_stock_phrase_is_refused(reason):
    state = _verdict(f"MUST FIX:\nnone\nDECLINED:\n1. Day 2 empty_afternoon: {reason}\nVERDICT: APPROVED")
    assert state["review_verdict"] == "REVISE"
    assert state["review_log"][-1]["declines_refused"][0]["reason"] == reason
    assert state["taste_declined"] == []


def test_a_finding_the_critic_asks_to_fix_is_not_repeated_in_the_critique():
    state = _verdict("MUST FIX:\n1. Day 2: add Museu do Dinheiro at 15:00.\nSHOULD FIX:\nnone\nVERDICT: REVISE")
    assert state["review_verdict"] == "REVISE" and state["review_log"][-1]["overridden"] is False
    assert "not fixed or declined" not in state["critique"]


def test_declines_are_read_in_the_ways_the_model_writes_them():
    text = ("MUST FIX:\n1. Day 1: x\nDECLINED:\n- Day 3 empty afternoon - the rain that day keeps them indoors\n"
            "2. day 4, Double_Meal: lunch is a snack at the market before a long walk\nSHOULD FIX:\n"
            "1. Day 5 empty_morning: not a decline, it is under SHOULD FIX\nVERDICT: REVISE")
    assert declines_of(text) == {("3", "empty_afternoon"): "the rain that day keeps them indoors",
                                 ("4", "double_meal"): "lunch is a snack at the market before a long walk"}
    assert reason_is_specific(REASON) and not reason_is_specific("fine") and not reason_is_specific("too short a b")


def test_the_code_only_ablation_declines_nothing(monkeypatch):
    from travel_planner.agents.plan_critic import code_only_critique
    monkeypatch.setenv("CRITIC_MODE", "code_only")
    state = _empty_afternoon_state("")
    code_only_critique(SimpleNamespace(state=state))
    assert state["review_verdict"] == "REVISE" and state["review_log"][-1]["declined"] == []


def test_the_guide_lists_undecided_day_shape_points_but_not_the_declined_ones():
    state = _empty_afternoon_state("")
    assert "Nothing planned 12:30-19:30" in open_issues(state)
    state["taste_declined"] = [{"day": 2, "issue": "empty_afternoon", "reason": REASON}]
    assert open_issues(state) == ""


def test_the_critic_is_told_which_findings_are_its_call():
    from travel_planner.agents.plan_critic import build_plan_critic
    text = build_plan_critic().instruction
    assert "DAY-SHAPE DECISIONS" in text and "DECLINED:" in text and "An item you leave out counts as MUST FIX" in text
    assert text.index("DAY-SHAPE DECISIONS") < text.index("RULES")
