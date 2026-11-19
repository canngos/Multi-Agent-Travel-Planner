"""Measures finished runs: who decided (critic vs code), how good and how grounded the plan is, and what it cost.

  .venv/Scripts/python scripts/evaluate.py                       # every output/runs/*-state.json, offline
  .venv/Scripts/python scripts/evaluate.py output/runs/lisbon-state.json --agents
  .venv/Scripts/python scripts/evaluate.py --judge               # + LLM-as-judge scores (live LLM needed)

Reads the session state that scripts/run_trip.py saves (review_log, revision_log, replan_log, the *_link_fixes
keys, the itinerary and the guide) and its metrics file (seconds, tool calls and tokens per agent), and prints
Markdown tables. Everything except --judge runs offline on recorded runs.

Ablation: run the same trip with CRITIC_MODE=code_only (the critic's text is replaced by the code check's
verdict) and RUN_LABEL=code_only, then evaluate both state files side by side.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from travel_planner import config  # noqa: E402
from travel_planner.agents.review_checks import (  # noqa: E402
    FOOD_KINDS, _parse, check_plan, find_place, research_places)

PLACE_KINDS = {"sight"} | FOOD_KINDS
LINK_FIX_KEYS = ("itinerary_link_fixes", "guide_link_fixes")
RESEARCHERS = {"culture_agent", "food_agent", "transport_comparer", "stay_comparer"}  # one per research branch


def load(path: Path) -> tuple[dict, dict]:
    state = json.loads(path.read_text(encoding="utf-8"))
    metrics_path = path.with_name(path.name.replace("-state.json", "-metrics.json"))
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    return state, metrics


def _jsonish(value):
    """State values saved with default=str may be dicts written as text."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def links_removed(state: dict) -> int:
    n = 0
    for fixes in (_jsonish(state.get("research_link_fixes")) or {}).values():
        n += len((fixes or {}).get("removed") or [])
    for key in LINK_FIX_KEYS:
        n += len((_jsonish(state.get(key)) or {}).get("removed") or [])
    return n


def grounded_items(plan: dict, state: dict) -> tuple[int, int]:
    """(planned places found in the research, planned places): sights, restaurants, cafés and bars."""
    places = research_places(state)
    items = [i for d in plan.get("days") or [] for i in d.get("items") or []
             if isinstance(i, dict) and i.get("kind") in PLACE_KINDS]
    return sum(1 for i in items if find_place(i.get("name"), places)), len(items)


def planning_turns(metrics: dict) -> list[dict]:
    """The turns in which the planner ran (a full plan, or a re-plan), with "ran": the agents that called the LLM
    (a skipped research agent still posts an event, but makes no LLM call)."""
    return [{**t, "ran": set(t.get("llm_agents", t.get("agents", [])))}
            for t in metrics.get("turns") or [] if "itinerary_planner" in t.get("agents", [])]


def evaluate(state: dict, metrics: dict) -> dict:
    """One row of numbers for a finished run."""
    plan = _parse(state.get("itinerary"))
    brief = state.get("trip_brief") or {}
    review = state.get("review_log") or []
    revisions = state.get("revision_log") or []
    final = check_plan(plan, state) if plan else {"must_fix": [], "should_fix": [], "facts": {}}
    found, total = grounded_items(plan, state)
    overridden = [r for r in review if r.get("overridden")]
    turns = planning_turns(metrics)
    replans = [r for r in state.get("replan_log") or [] if r.get("accepted")]
    # time saved: each re-plan that skipped research, against the run's slowest plan with all research
    full = max((t["seconds"] for t in turns if RESEARCHERS <= t["ran"]), default=None)
    reused = [t for t in turns if not RESEARCHERS <= t["ran"]]
    saved = round(sum(full - t["seconds"] for t in reused), 1) if full is not None and reused else None
    return {
        "rounds": len(review),
        "final_verdict": state.get("review_verdict"),
        "critic_mode": state.get("critic_mode") or "llm",
        "agreement": f"{len(review) - len(overridden)}/{len(review)}" if review else "-",
        "overrides": ", ".join(f"r{r['round']}: critic {r.get('critic_said')} -> {r['verdict']}" for r in overridden)
                     or "none",
        "must_should_per_round": " → ".join(f"{r.get('must_fix')}/{r.get('should_fix')}" for r in review) or "-",
        "decide_per_round": " → ".join(str(r.get("decide", "-")) for r in review) or "-",
        "declined": sum(len(r.get("declined") or []) for r in review),
        "declines_refused": sum(len(r.get("declines_refused") or []) for r in review),
        "declined_examples": "; ".join(f"day {d['day']} {d['issue']}: {d['reason'][:60]}"
                                       for r in review for d in (r.get("declined") or [])[:1]) or "-",
        "final_must_should": f"{len(final['must_fix'])}/{len(final['should_fix'])}",
        "revisions_restored": sum(1 for r in revisions if r.get("restored")),
        "grounded": f"{found}/{total}" if total else "-",
        "grounded_ratio": round(found / total, 2) if total else None,
        "links_removed": links_removed(state),
        "budget_eur": brief.get("total_budget_eur"),
        "total_eur": (final.get("facts") or {}).get("total_eur"),
        "extra_sights": sum(len(e.get("added") or []) for e in state.get("extra_sights_log") or []),
        "replans": "; ".join(f"rerun {r.get('rerun') or 'nothing'}" for r in replans) or "none",
        "replan_refused": sum(1 for r in state.get("replan_log") or [] if not r.get("accepted")),
        "seconds": metrics.get("seconds"),
        "reuse_seconds_saved": saved,
        "tool_calls": metrics.get("tool_calls"),
        "tokens": sum((a.get("prompt_tokens") or 0) + (a.get("output_tokens") or 0)
                      for a in (metrics.get("agents") or {}).values()) or None,
        "cache_misses": metrics.get("llm_cache_misses"),
    }


ROWS = [  # (label, key)
    ("Review rounds", "rounds"), ("Final verdict", "final_verdict"), ("Critic mode", "critic_mode"),
    ("Critic = code verdict (rounds)", "agreement"), ("Code overrides", "overrides"),
    ("Must/should per round", "must_should_per_round"), ("Day-shape findings to decide per round", "decide_per_round"),
    ("Declined by the critic (with a reason)", "declined"), ("Declines refused by code (stock reason)", "declines_refused"),
    ("A decline, as written", "declined_examples"), ("Final plan must/should (code check)", "final_must_should"),
    ("Revisions undone by code", "revisions_restored"), ("Planned places found in research", "grounded"),
    ("Links removed (invented)", "links_removed"), ("Budget EUR", "budget_eur"), ("Computed total EUR", "total_eur"),
    ("Sights researched on demand", "extra_sights"), ("Re-plans (agent's scope)", "replans"),
    ("Re-plan requests refused", "replan_refused"), ("Seconds (whole chat)", "seconds"),
    ("Seconds saved by reusing research", "reuse_seconds_saved"), ("Tool calls", "tool_calls"),
    ("Tokens (prompt + output)", "tokens"), ("LLM cache misses", "cache_misses"),
]


def _cell(value) -> str:
    return "-" if value is None or value == "" else str(value).replace("|", "\\|")


def markdown(results: dict[str, dict]) -> str:
    names = list(results)
    lines = ["| Measure | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for label, key in ROWS:
        lines.append(f"| {label} | " + " | ".join(_cell(results[n].get(key)) for n in names) + " |")
    return "\n".join(lines)


def agent_table(metrics: dict) -> str:
    lines = ["| Agent | LLM calls | Tool calls | Prompt tokens | Output tokens | Seconds (first → last event) |",
             "|---|---|---|---|---|---|"]
    for name, a in sorted((metrics.get("agents") or {}).items(), key=lambda kv: kv[1].get("first_s") or 0):
        span = (a["last_s"] - a["first_s"]) if "first_s" in a and "last_s" in a else None
        lines.append(f"| {name} | {a.get('llm_calls', 0)} | {a.get('tool_calls', 0)} | {a.get('prompt_tokens', 0)} | "
                     f"{a.get('output_tokens', 0)} | {_cell(round(span, 1) if span is not None else None)} |")
    return "\n".join(lines)


JUDGE_PROMPT = """You are judging a day-by-day trip plan for the travellers below. Score each criterion from 1 (poor)
to 5 (excellent) and answer with JSON only: {{"interest_match": n, "pacing": n, "realism": n, "comment": "one
sentence"}}.
- interest_match: do the sights and meals fit the travellers' interests?
- pacing: are the days well filled without rushing (the brief's pace, if any)? Do mornings start with sights?
- realism: sensible order and distances within each day, meals at meal times, transport and stay that fit.

TRIP BRIEF: {brief}

PLAN (JSON): {plan}"""


def judge(state: dict) -> dict:
    """LLM-as-judge scores for the final plan (needs the live LLM; not cached)."""
    import litellm

    from travel_planner.agents.output import extract_json

    settings = config.llm_settings()
    reply = litellm.completion(
        model=settings["model"], api_base=settings["api_base"], api_key=settings["api_key"], temperature=0,
        messages=[{"role": "user", "content": JUDGE_PROMPT.format(
            brief=json.dumps(state.get("trip_brief"), ensure_ascii=False),
            plan=json.dumps(_parse(state.get("itinerary")), ensure_ascii=False))}])
    try:
        return extract_json(reply.choices[0].message.content or "")
    except ValueError:
        return {"comment": "the judge did not answer with JSON"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("states", nargs="*", type=Path, help="state files (default: output/runs/*-state.json)")
    parser.add_argument("--agents", action="store_true", help="also print a per-agent table for each run")
    parser.add_argument("--judge", action="store_true", help="add LLM-as-judge scores (live LLM)")
    args = parser.parse_args(argv)
    paths = args.states or sorted((Path(config.OUTPUT_DIR) / "runs").glob("*-state.json"))
    if not paths:
        print("No runs found: run scripts/run_trip.py first (it writes output/runs/<trip>-state.json).")
        return 1
    results, metrics_of = {}, {}
    for path in paths:
        state, metrics = load(path)
        name = path.name.removesuffix("-state.json")
        results[name], metrics_of[name] = evaluate(state, metrics), metrics
        if args.judge:
            scores = judge(state)
            results[name].update({f"judge_{k}": v for k, v in scores.items()})
    if args.judge:
        ROWS.extend([("Judge: interest match (1-5)", "judge_interest_match"), ("Judge: pacing (1-5)", "judge_pacing"),
                     ("Judge: realism (1-5)", "judge_realism"), ("Judge: comment", "judge_comment")])
    print(markdown(results))
    if args.agents:
        for name, metrics in metrics_of.items():
            print(f"\n### {name}: per agent\n")
            print(agent_table(metrics))
    return 0


if __name__ == "__main__":
    sys.exit(main())
