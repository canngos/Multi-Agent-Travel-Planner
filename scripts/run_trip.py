"""Plays a scripted chat with the planner (the real root agent) and checks the result.

  .venv/Scripts/python scripts/run_trip.py --demo                 # the recorded Lisbon demo, offline, ~3 s
  .venv/Scripts/python scripts/run_trip.py --trip tallinn         # live: needs .env keys and the Lehmus LLM
  .venv/Scripts/python scripts/run_trip.py --trip lisbon --record # re-record the demo into cache/demo

Trips: lisbon (the demo), tallinn (low budget, ferry), rome (7 days, high budget), krakow (from Tampere).
The conversation, every tool call and an agent timeline are printed; the final session state is written to
output/runs/<trip>-state.json. The checks at the end are the ones used to harden the pipeline (Phase 9).
"""
import argparse
import os
import sys

TRIPS = {
    "lisbon": [
        "Plan 5 days in Lisbon for two, mid budget, we love history and street food.",
        "We fly from Helsinki, 10-14 May. Total budget about 2500 euros.",
        "Yes, that's right, go ahead.",
        "Thanks! Which day do we visit Belém, and what does the tower cost?",
    ],
    "tallinn": [
        "We'd like a cheap 3-day trip to Tallinn for two, we like old towns and simple local food.",
        "From Helsinki, 12-14 May 2027, we'd take the ferry. Total budget about 450 euros.",
        "Yes, please go ahead.",
        "Which ferry do we take and how much does it cost?",
    ],
    "rome": [
        "Plan a week in Rome for two adults, high budget: ancient history, art and great restaurants.",
        "Flying from Helsinki, 3-9 June 2027. No fixed total budget.",
        "Correct, go ahead.",
        "On which day do we see the Colosseum?",
    ],
    "krakow": [
        "3 days in Kraków for two, mid budget, history and pierogi.",
        "We leave from Tampere, 17-19 May 2027, about 900 euros in total.",
        "Yes, that's right.",
        "What local dishes should we try?",
    ],
}

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--trip", choices=sorted(TRIPS), default="lisbon")
parser.add_argument("--demo", action="store_true", help="replay the recorded demo offline (DEMO=1; Lisbon only)")
parser.add_argument("--record", action="store_true", help="record into cache/demo (DEMO=1 DEMO_RECORD=1)")
args = parser.parse_args()
if args.demo or args.record:
    os.environ["DEMO"] = "1"  # before importing the package: config reads it at import time
    if args.record:
        os.environ["DEMO_RECORD"] = "1"

import asyncio  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from travel_planner.agents.output import check_output, item_urls  # noqa: E402
from travel_planner.agents.pipeline import build_root_agent  # noqa: E402
from travel_planner.agents.review_checks import check_plan  # noqa: E402

RESEARCH = ("transport_summary", "stay_summary", "culture_findings", "food_findings")
ANSWERS: dict[int, str] = {}  # turn number -> the concierge's answer
ALWAYS_OK = ("openstreetmap.org/copyright", "transitous.org/sources")


async def run(turns: list[str]) -> dict:
    runner = InMemoryRunner(agent=build_root_agent(), app_name="run_trip")
    session = await runner.session_service.create_session(app_name="run_trip", user_id="u")
    spans, t0, ncalls = {}, time.perf_counter(), 0
    try:
        for n, turn in enumerate(turns):
            print(f"\n>>> YOU: {turn}")
            async for ev in runner.run_async(user_id="u", session_id=session.id,
                                             new_message=types.Content(role="user", parts=[types.Part(text=turn)])):
                now = time.perf_counter() - t0
                first, _ = spans.get(ev.author, (now, now))
                spans[ev.author] = (first, now)
                for c in ev.get_function_calls():
                    ncalls += 1
                    print(f"[{now:6.1f}s] {ev.author:<20} calls {c.name}"
                          f"({json.dumps(dict(c.args or {}), ensure_ascii=False)[:100]})")
                for r in ev.get_function_responses():
                    resp = r.response or {}
                    if resp.get("status") not in (None, "ok"):
                        print(f"[{now:6.1f}s] {ev.author:<20}   -> {r.name}: {resp.get('status')} "
                              f"{str(resp.get('reason') or '')[:100]}")
                if ev.author in ("concierge", "guide_writer") and ev.content and ev.content.parts and ev.is_final_response():
                    text = "".join(p.text or "" for p in ev.content.parts if not p.thought)
                    if text.strip():
                        print(f"[{now:6.1f}s] {ev.author.upper()}:\n{text[:1200]}")
                        if ev.author == "concierge":
                            ANSWERS[n] = text
        state = (await runner.session_service.get_session(app_name="run_trip", user_id="u", session_id=session.id)).state
    finally:
        await runner.close()
    print(f"\n--- {time.perf_counter() - t0:.0f} s, {ncalls} tool calls. Agent timeline (first -> last event):")
    for author, (start, end) in sorted(spans.items(), key=lambda kv: kv[1][0]):
        print(f"   {author:<20} {start:7.1f}s -> {end:7.1f}s")
    return dict(state)


def report(state: dict) -> bool:
    if not state.get("trip_brief"):
        print("FAIL  the concierge did not save a trip brief")
        return False
    plan_text = state.get("itinerary") or ""
    plan, problems = check_output("itinerary", plan_text)
    days = plan.get("days") or []
    known = "\n".join(str(state.get(k, "")) for k in RESEARCH).replace("http://", "https://")
    ungrounded = [u for u in item_urls(plan) if u.replace("http://", "https://") not in known]
    guide = state.get("final_guide") or ""
    guide_urls = sorted({u.rstrip(").,") for u in re.findall(r"https?://[^\s)\]>|]+", guide)})
    guide_ungrounded = [u for u in guide_urls if u.replace("http://", "https://") not in known
                        and not any(ok in u for ok in ALWAYS_OK)]
    final = check_plan(plan, state)
    paths = state.get("guide_paths") or {}
    review = state.get("review_log") or []
    print(f"\nitinerary: {len(days)} days, cost total {(plan.get('cost') or {}).get('total_eur')} EUR; "
          f"review rounds: {[(r['round'], r['verdict']) for r in review]}")
    print(f"guide: {paths.get('markdown', 'not saved')}")
    checks = {
        "itinerary is valid JSON in the shared schema": not problems,
        f"itinerary has {state['trip_brief']['days']} days": len(days) == state["trip_brief"]["days"],
        "every itinerary link came from research or tool results": not ungrounded,
        "guide saved as .md and .html": bool(paths) and all(Path(p).exists() for p in paths.values()),
        "every guide link came from research or tool results": not guide_ungrounded,
        "review loop ran (approved, or stopped after 3 rounds)": bool(review) and (
            state.get("review_verdict") == "APPROVED" or len(review) == 3),
        "final plan has no must-fix problems (code check)": not final["must_fix"],
        "the concierge answered the follow-up question": bool(ANSWERS.get(len(TRIPS[args.trip]) - 1, "").strip()),
    }
    for label, passed in checks.items():
        print(("PASS  " if passed else "FAIL  ") + label)
    for p in problems[:5]:
        print("   schema:", p)
    for u in (ungrounded + guide_ungrounded)[:5]:
        print("   ungrounded link:", u)
    return all(checks.values())


def main() -> int:
    if args.demo and args.trip != "lisbon":
        print("Only the Lisbon trip is recorded for the offline demo.")
        return 2
    state = asyncio.run(run(TRIPS[args.trip]))
    out = ROOT / "output" / "runs"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.trip}-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str),
                                                 encoding="utf-8")
    ok = report(state)
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
