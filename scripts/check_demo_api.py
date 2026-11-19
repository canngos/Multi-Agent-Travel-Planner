"""Plays the recorded demo against a running `adk api_server` (the API that adk web and the web UI use).

  DEMO=1 .venv/Scripts/adk api_server --port 8766 .        (in another terminal)
  .venv/Scripts/python scripts/check_demo_api.py 8766
  .venv/Scripts/python scripts/check_demo_api.py http://localhost:8000/adk      (scripts/serve.py --demo)
"""
import json
import runpy
import sys
import time

from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8")
TARGET = sys.argv[1] if len(sys.argv) > 1 else "8766"  # a port (adk api_server) or a base URL
BASE = f"http://127.0.0.1:{TARGET}" if TARGET.isdigit() else TARGET.rstrip("/")
APP, USER = "travel_planner", "demo"
TURNS = runpy.run_path(str(Path(__file__).resolve().parents[1] / "travel_planner" / "demo_script.py"))["DEMO_TURNS"]

for _ in range(60):  # wait for the server
    try:
        httpx.get(f"{BASE}/list-apps", timeout=2)
        break
    except httpx.HTTPError:
        time.sleep(1)
with httpx.Client(timeout=600) as client:
    print("apps:", client.get(f"{BASE}/list-apps").json())
    session = client.post(f"{BASE}/apps/{APP}/users/{USER}/sessions", json={}).json()
    t0 = time.perf_counter()
    for turn in TURNS:
        events = client.post(f"{BASE}/run", json={
            "app_name": APP, "user_id": USER, "session_id": session["id"],
            "new_message": {"role": "user", "parts": [{"text": turn}]}}).json()
        authors = [e.get("author") for e in events]
        errors = [e.get("error_code") for e in events if e.get("error_code")]
        last = next((p.get("text") for e in reversed(events) if e.get("author") == "concierge"
                     for p in (e.get("content") or {}).get("parts", []) if p.get("text") and not p.get("thought")), "")
        print(f"> {turn}\n  {len(events)} events from {sorted(set(authors))}; errors {errors}\n  concierge: {last[:160]!r}")
    state = client.get(f"{BASE}/apps/{APP}/users/{USER}/sessions/{session['id']}").json()["state"]
    print(f"\n{time.perf_counter() - t0:.1f}s; guide {len(state.get('final_guide') or '')} chars, "
          f"review {[(r['round'], r['verdict']) for r in state.get('review_log') or []]}")
    ok = state.get("final_guide") and state.get("review_verdict") == "APPROVED" and last.strip()
    print("PASS" if ok else "FAIL  (no guide, no approval, or no answer to the follow-up question)")
