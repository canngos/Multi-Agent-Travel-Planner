"""Stress-test driver: plays one scenario against the real root agent (live LLM + tools).

usage: python driver.py <scenario_id> <out_root>
Turn directives: "@CONFIRM" (send a confirmation only if no plan has started), "@APPROVE" (approve only if a plan is
waiting), anything else is sent verbatim. Writes <out_root>/<id>/log.txt, state.json, result.json.
"""
import asyncio
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

REPO = Path(r"C:\Users\canba\Desktop\university\AI-Engineering\Multi-Agent-Travel-Planner")
sid, out_root = sys.argv[1], Path(sys.argv[2]).resolve()
sys.path.insert(0, str(Path(__file__).parent))
from scenarios import SCENARIOS  # noqa: E402

sc = SCENARIOS[sid]
out = out_root / sid
out.mkdir(parents=True, exist_ok=True)
os.environ["OUTPUT_DIR"] = str(out)
sys.path.insert(0, str(REPO))
os.chdir(REPO)
sys.stdout.reconfigure(encoding="utf-8")

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from travel_planner.agents.output import check_output, item_urls  # noqa: E402
from travel_planner.agents.pipeline import build_root_agent  # noqa: E402
from travel_planner.agents.review_checks import check_plan  # noqa: E402

CONFIRM = "Yes, that's right, go ahead."
APPROVE = "Looks good, please write the guide."
TURN_TIMEOUT = int(os.environ.get("TURN_TIMEOUT", 20 * 60))  # seconds per turn
LOG = open(out / "log.txt", "w", encoding="utf-8")


def log(msg=""):
    LOG.write(msg + "\n")
    LOG.flush()


async def main():
    runner = InMemoryRunner(agent=build_root_agent(), app_name="stress")
    session = await runner.session_service.create_session(app_name="stress", user_id="u")
    t0 = time.perf_counter()
    result = {"id": sid, "title": sc["title"], "turns": [], "errors": [], "tool_errors": [], "tool_calls": 0,
              "agents": {}}

    async def get_state():
        s = await runner.session_service.get_session(app_name="stress", user_id="u", session_id=session.id)
        return dict(s.state)

    async def send(text):
        log(f"\n>>> USER: {text}")
        replies, t_start = [], time.perf_counter()

        async def consume():
            async for ev in runner.run_async(user_id="u", session_id=session.id,
                                             new_message=types.Content(role="user", parts=[types.Part(text=text)])):
                now = time.perf_counter() - t0
                a = result["agents"].setdefault(ev.author, {"events": 0, "tool_calls": 0, "first": now, "last": now})
                a["events"] += 1
                a["last"] = now
                for c in ev.get_function_calls():
                    result["tool_calls"] += 1
                    a["tool_calls"] += 1
                    log(f"[{now:6.1f}s] {ev.author:<22} CALL {c.name}({json.dumps(dict(c.args or {}), ensure_ascii=False)[:300]})")
                for r in ev.get_function_responses():
                    resp = r.response or {}
                    st = resp.get("status") if isinstance(resp, dict) else None
                    if st not in (None, "ok"):
                        msg = f"{ev.author}: {r.name} -> {st} {str(resp.get('reason') or resp.get('error') or '')[:300]}"
                        result["tool_errors"].append(msg)
                        log(f"[{now:6.1f}s]   ERR {msg}")
                if ev.error_code or ev.error_message:
                    result["errors"].append(f"{ev.author}: {ev.error_code} {ev.error_message}")
                    log(f"[{now:6.1f}s]   EVENT ERROR {ev.author}: {ev.error_code} {ev.error_message}")
                if ev.content and ev.content.parts and ev.is_final_response() and not ev.partial:
                    txt = "".join(p.text or "" for p in ev.content.parts if not getattr(p, "thought", False))
                    if txt.strip() and ev.author in ("concierge", "guide_writer", "planning_pipeline", "stress"):
                        replies.append((ev.author, txt))
                        log(f"[{now:6.1f}s] <<< {ev.author.upper()}:\n{txt}\n")
        try:
            await asyncio.wait_for(consume(), timeout=TURN_TIMEOUT)
        except asyncio.TimeoutError:
            result["errors"].append(f"TIMEOUT after {TURN_TIMEOUT}s on turn: {text[:80]}")
            log("!!! TIMEOUT")
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"EXCEPTION on turn {text[:60]!r}: {type(e).__name__}: {e}")
            log("!!! EXCEPTION\n" + traceback.format_exc())
        result["turns"].append({"user": text, "seconds": round(time.perf_counter() - t_start, 1),
                                "replies": [{"author": a, "text": t} for a, t in replies]})

    try:
        for turn in sc["turns"]:
            st = await get_state()
            if turn == "@CONFIRM":
                for attempt in range(3):  # the concierge may need more than one confirmation round
                    st = await get_state()
                    if st.get("plan_status") or st.get("itinerary"):
                        if attempt == 0:
                            log("(skip @CONFIRM: planning already ran)")
                        break
                    if attempt:
                        log(f"(@CONFIRM retry {attempt}: planning has not started)")
                    await send(CONFIRM)
                    if any("TIMEOUT" in e or "EXCEPTION" in e for e in result["errors"]):
                        break
                continue
            elif turn == "@APPROVE":
                if st.get("plan_status") != "awaiting_approval":
                    log(f"(skip @APPROVE: plan_status={st.get('plan_status')})")
                    continue
                turn = APPROVE
            await send(turn)
            if any("TIMEOUT" in e or "EXCEPTION" in e for e in result["errors"]):
                break
        state = await get_state()
    finally:
        await runner.close()

    result["seconds"] = round(time.perf_counter() - t0, 1)
    (out / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    # checks
    brief = state.get("trip_brief")
    result["brief"] = brief
    result["plan_status"] = state.get("plan_status")
    plan_text = state.get("itinerary") or ""
    checks = {}
    if plan_text:
        plan, problems = check_output("itinerary", plan_text)
        days = plan.get("days") or []
        known = "\n".join(str(state.get(k, "")) for k in
                          ("transport_summary", "stay_summary", "culture_findings", "food_findings"))
        known = known.replace("http://", "https://")
        ungrounded = [u for u in item_urls(plan) if u.replace("http://", "https://") not in known]
        try:
            final = check_plan(plan, state)
        except Exception as e:  # noqa: BLE001
            final = {"must_fix": [f"check_plan crashed: {e}"], "should_fix": []}
        result["cost_total"] = (plan.get("cost") or {}).get("total_eur")
        result["n_days"] = len(days)
        result["review"] = [(r.get("round"), r.get("verdict")) for r in (state.get("review_log") or [])]
        result["final_must_fix"] = final.get("must_fix")
        result["final_should_fix"] = final.get("should_fix")
        checks["schema_ok"] = not problems
        checks["schema_problems"] = problems[:5]
        checks["days_match"] = bool(brief) and len(days) == brief.get("days")
        checks["ungrounded"] = ungrounded[:5]
    guide = state.get("final_guide") or ""
    if guide:
        paths = state.get("guide_paths") or {}
        checks["guide_files"] = {k: Path(v).exists() for k, v in paths.items()}
        checks["guide_len"] = len(guide)
    result["checks"] = checks
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log(f"\n=== done in {result['seconds']} s, {result['tool_calls']} tool calls, errors={result['errors']}")


asyncio.run(main())
