"""REVIEW.md A3: scripts/evaluate.py on recorded runs, and the CRITIC_MODE=code_only ablation."""
import importlib.util
import json
from pathlib import Path

from tests.test_review import ScriptedLlm, _run_loop, _state, bad_plan, fixed_plan

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("evaluate", ROOT / "scripts" / "evaluate.py")
evaluate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluate)


def _metrics(*turns):
    return {"seconds": 400.0, "tool_calls": 60, "llm_cache_misses": 0,
            "agents": {"culture_agent": {"llm_calls": 5, "tool_calls": 4, "prompt_tokens": 1000, "output_tokens": 200,
                                         "first_s": 1.0, "last_s": 90.0}},
            "turns": list(turns)}


ALL = ["culture_agent", "food_agent", "transport_comparer", "stay_comparer", "itinerary_planner"]


def test_evaluate_reports_who_decided_grounding_and_reuse():
    state = _state(fixed_plan())
    state.update(review_log=[{"round": 1, "verdict": "REVISE", "critic_said": "APPROVED", "overridden": True,
                              "must_fix": 4, "should_fix": 2},
                             {"round": 2, "verdict": "APPROVED", "critic_said": "APPROVED", "overridden": False,
                              "must_fix": 0, "should_fix": 1}],
                 review_verdict="APPROVED",
                 revision_log=[{"round": 1, "restored": "reply was not a valid plan"}],
                 research_link_fixes={"culture_findings": {"fixed": [], "removed": ["https://x.org/a"]}},
                 itinerary_link_fixes={"fixed": [], "removed": ["https://x.org/b", "https://x.org/c"]},
                 replan_log=[{"rerun": ["stay"], "accepted": True}, {"rerun": [], "accepted": False}])
    metrics = _metrics({"turn": 2, "seconds": 300.0, "agents": ALL, "llm_agents": ALL},
                       {"turn": 5, "seconds": 80.0, "agents": ALL,  # skipped agents still post an event
                        "llm_agents": ["stay_comparer", "itinerary_planner"]})
    row = evaluate.evaluate(state, metrics)
    assert row["rounds"] == 2 and row["agreement"] == "1/2"
    assert row["overrides"] == "r1: critic APPROVED -> REVISE"
    assert row["must_should_per_round"] == "4/2 → 0/1"
    assert row["revisions_restored"] == 1 and row["links_removed"] == 3
    found, total = row["grounded"].split("/")
    assert found == total and int(total) > 5  # every planned place of the fixed plan is researched
    assert row["replans"] == "rerun ['stay']" and row["replan_refused"] == 1
    assert row["reuse_seconds_saved"] == 220.0
    assert row["tokens"] == 1200 and row["budget_eur"] == 1500.0
    table = evaluate.markdown({"lisbon": row})
    assert table.startswith("| Measure | lisbon |") and "| Review rounds | 2 |" in table
    assert "| culture_agent | 5 | 4 | 1000 | 200 | 89.0 |" in evaluate.agent_table(metrics)


def test_evaluate_main_reads_state_and_metrics_files(tmp_path, capsys):
    runs = tmp_path / "runs"
    runs.mkdir()
    state = _state(bad_plan())
    state["review_log"] = [{"round": 1, "verdict": "REVISE", "critic_said": "REVISE", "overridden": False,
                            "must_fix": 5, "should_fix": 1}]
    (runs / "demo-state.json").write_text(json.dumps(state), encoding="utf-8")
    (runs / "demo-metrics.json").write_text(json.dumps(_metrics()), encoding="utf-8")
    assert evaluate.main([str(runs / "demo-state.json"), "--agents"]) == 0
    out = capsys.readouterr().out
    assert "| Measure | demo |" in out and "### demo: per agent" in out
    assert "| Final plan must/should (code check) |" in out


async def test_code_only_critic_skips_the_llm_and_logs_its_verdict(monkeypatch):
    monkeypatch.setenv("CRITIC_MODE", "code_only")
    llm = ScriptedLlm(fixes=True)
    state = await _run_loop(llm)
    assert "critic" not in llm.calls  # the critic's LLM was never asked
    assert llm.calls == ["reviser:revise", "reviser:exit_loop"]
    assert [(r["round"], r["verdict"], r["critic_said"], r["overridden"]) for r in state["review_log"]] == \
        [(1, "REVISE", "REVISE", False), (2, "APPROVED", "APPROVED", False)]
    assert state["critic_mode"] == "code_only" and state["critique"].endswith("VERDICT: APPROVED")
