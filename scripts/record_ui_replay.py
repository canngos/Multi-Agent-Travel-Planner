"""Records a chat with the running ADK API server as a replay file for the web UI.

  DEMO=1 DEMO_PACE=0.2 adk api_server --port 8766 .      (in another terminal)
  .venv/Scripts/python scripts/record_ui_replay.py 8766 travel-planner-ui/public/replays/lisbon-demo.json

Uses /run_sse, the streaming endpoint the UI uses, and keeps every event with its timestamp, so the UI can
play the run back with the original timing (and any speed).
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8")
PORT, OUT = sys.argv[1], Path(sys.argv[2])
BASE, APP, USER = f"http://127.0.0.1:{PORT}", "travel_planner", "replay"
TURNS = [
    "Plan 5 days in Lisbon for two, mid budget, we love history and street food.",
    "We fly from Helsinki, 10-14 May. Total budget about 2500 euros.",
    "Yes, that's right, go ahead.",
    "Thanks! Which day do we visit Belém, and what does the tower cost?",
]

for _ in range(60):
    try:
        httpx.get(f"{BASE}/list-apps", timeout=2)
        break
    except httpx.HTTPError:
        time.sleep(1)

with httpx.Client(timeout=None) as client:
    session = client.post(f"{BASE}/apps/{APP}/users/{USER}/sessions", json={}).json()
    turns = []
    for text in TURNS:
        sent_at = time.time()
        events = []
        with client.stream("POST", f"{BASE}/run_sse", json={
                "app_name": APP, "user_id": USER, "session_id": session["id"], "streaming": False,
                "new_message": {"role": "user", "parts": [{"text": text}]}}) as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))
        turns.append({"user": text, "sent_at": sent_at, "events": events})
        print(f"{len(events):3d} events  {text}")
    state = client.get(f"{BASE}/apps/{APP}/users/{USER}/sessions/{session['id']}").json()["state"]

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({
    "title": "Lisbon, 5 days for two (recorded demo)",
    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "turns": turns,
    "final_state": state,
}, ensure_ascii=False), encoding="utf-8")
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
