"""End to end: the recorded Lisbon demo replays offline with no LLM cache miss (REVIEW.md F1).

Runs scripts/run_trip.py --demo in a subprocess, because DEMO must be set before the package is imported (the
model is built at import time). OUTPUT_DIR points at a temp folder so output/ is never overwritten; the cache key
maps that folder back to <repo>/output, as recorded.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_recorded_demo_replays_with_no_cache_miss(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("CACHE_DIR", "OFFLINE", "REFRESH_CACHE", "TODAY",
                                                            "DEMO_RECORD", "DEMO_PACE", "LLM_CACHE")}
    env.update(OUTPUT_DIR=str(tmp_path), PYTHONIOENCODING="utf-8")
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_trip.py"), "--demo"], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert "LLM cache misses: 0" in done.stdout, done.stdout[-3000:] + done.stderr[-3000:]
    assert done.returncode == 0, done.stdout[-3000:]
    guides = {p.suffix for p in tmp_path.glob("lisbon-*")}
    assert {".md", ".html"} <= guides
    metrics = json.loads((tmp_path / "runs" / "lisbon-metrics.json").read_text(encoding="utf-8"))
    assert metrics["llm_cache_misses"] == 0 and metrics["demo"] and metrics["tool_calls"] > 0
    assert (tmp_path / "runs" / "lisbon-state.json").exists()


def test_the_web_ui_suggests_the_recorded_demo_messages():
    import re

    from travel_planner.demo_script import APPROVE, DEMO_TURNS
    chat = (ROOT / "travel-planner-ui" / "src" / "components" / "Chat.tsx").read_text(encoding="utf-8")
    block = chat[chat.index("export const DEMO_LINES = ["):]
    block = block[:block.index("];")]
    lines = [APPROVE if line.strip() == "APPROVE_TEXT," else json.loads(line.strip().rstrip(","))
             for line in block.splitlines()[1:] if line.strip()]
    assert lines == DEMO_TURNS
    assert re.search(r'APPROVE_TEXT = "([^"]+)"', chat).group(1) == APPROVE
