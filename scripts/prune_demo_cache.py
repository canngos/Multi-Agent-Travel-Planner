"""Replays the demo (DEMO=1) while logging every cache read, then deletes cache/demo entries nobody read.

  .venv/Scripts/python scripts/prune_demo_cache.py          # dry run: lists what would be deleted
  .venv/Scripts/python scripts/prune_demo_cache.py --delete

Run it after re-recording the demo (scripts/run_trip.py --record), so cache/demo holds only what the replay
uses.

Only the request-hash namespaces are pruned; mcp_tools (the MCP tool lists) is kept.
"""
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DEMO"] = "1"
os.environ.pop("DEMO_RECORD", None)

from travel_planner import config  # noqa: E402
from travel_planner.tools import _cache  # noqa: E402

used: set[Path] = set()
_read = _cache.read


def logging_read(namespace, request, max_age_s=None):
    used.add(_cache._path(namespace, _cache.make_key(request)).resolve())
    return _read(namespace, request, max_age_s)


_cache.read = logging_read
delete = "--delete" in sys.argv
sys.argv = ["run_trip.py", "--demo"]
try:
    runpy.run_path(str(ROOT / "scripts" / "run_trip.py"), run_name="__main__")
except SystemExit as done:
    if done.code:
        sys.exit("The demo replay failed; nothing was deleted.")

files = [p.resolve() for p in config.DEMO_CACHE_DIR.rglob("*.json") if p.parent.name != "mcp_tools"]
unused = [p for p in files if p not in used]
print(f"\n{len(files)} cache files, {len(used)} read during the replay, {len(unused)} unused")
for p in unused:
    print(("deleting " if delete else "unused   ") + str(p.relative_to(ROOT)))
    if delete:
        p.unlink()
