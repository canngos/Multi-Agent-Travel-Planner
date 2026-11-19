"""Records the Lisbon demo again into cache/demo with the live LLM (needs the Lehmus LLM: the university VPN).

  .venv/Scripts/python scripts/record_demo.py
  .venv/Scripts/python scripts/prune_demo_cache.py --delete      # then drop the entries the new demo never reads
  .venv/Scripts/python scripts/run_trip.py --demo                # must report "LLM cache misses: 0"

Plays travel_planner/demo_script.py through scripts/run_trip.py --record, with two changes that keep the new
recording close to the old one, so only what changed is fetched again:
- every cached tool result is reused whatever its age (a live run would refetch results past their cache life);
- Nominatim is skipped, as it failed in the first recording (the geocoder fell back to Open-Meteo). Other
  coordinates would change every later tool call.
Re-record after any change to a prompt, a tool's output or a check's output, or the replay misses the cache.
"""
import json
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["DEMO"], os.environ["DEMO_RECORD"] = "1", "1"  # before the package reads its settings
sys.path.insert(0, str(ROOT))

from travel_planner.tools import _cache, geo  # noqa: E402
from travel_planner.tools._result import ToolError  # noqa: E402


def read_any_age(namespace, request, max_age_s=None):
    path = _cache._path(namespace, _cache.make_key(request))
    if not path.exists():
        return False, None
    return True, json.loads(path.read_text(encoding="utf-8"))["response"]


_get_json = geo.get_json


async def without_nominatim(url, *args, namespace=None, **kwargs):
    if namespace == "nominatim":
        raise ToolError("Nominatim is not used while recording the demo (the first recording fell back to Open-Meteo)")
    return await _get_json(url, *args, namespace=namespace, **kwargs)


_cache.read = read_any_age
geo.get_json = without_nominatim
sys.argv = ["run_trip.py", "--record"]
runpy.run_path(str(ROOT / "scripts" / "run_trip.py"), run_name="__main__")
