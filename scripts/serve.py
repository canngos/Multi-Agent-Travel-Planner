"""Starts the trip planner for real use: the agents and the web UI, one command, one port.

  .venv/Scripts/python scripts/serve.py               # live planning     -> http://localhost:8000
  .venv/Scripts/python scripts/serve.py --demo        # the recorded demo (no keys, no network)

Options: --port 8000, --host 127.0.0.1 (use 0.0.0.0 to reach it from other machines on your network),
--no-browser, --rebuild-ui.

What runs: ADK's API server (the same one `adk api_server` starts) under /adk, and the built React UI from
travel-planner-ui/dist at /. The UI is built on first start if needed (requires Node.js and npm). Run
scripts/check_setup.py first to see which keys and services work.
"""
import argparse
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "travel-planner-ui"
DIST = UI / "dist"

sys.path.insert(0, str(ROOT))


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo", action="store_true", help="replay the recorded Lisbon demo (DEMO=1)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--rebuild-ui", action="store_true", help="build the UI again even if it exists")
    return parser.parse_args(argv)


def build_ui() -> None:
    npm = shutil.which("npm")
    if not npm:
        sys.exit("The web UI is not built and npm was not found. Install Node.js 18+ (https://nodejs.org) and run "
                 "this again, or use ADK's own chat UI instead: .venv/Scripts/adk web")
    if not (UI / "node_modules").exists():
        print("Installing the UI's packages (npm ci, once)...")
        subprocess.run([npm, "ci", "--no-fund", "--no-audit"], cwd=UI, check=True)
    print("Building the web UI (npm run build)...")
    subprocess.run([npm, "run", "build"], cwd=UI, check=True)


class PlannerApp:
    """ASGI app: /adk/* goes to ADK's API server (prefix removed), everything else to the static UI.

    Lifespan events go to ADK's app, so its startup and shutdown (runners, MCP connections) work as usual.
    """

    def __init__(self, adk_app, ui_app):
        self.adk, self.ui = adk_app, ui_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.adk(scope, receive, send)
        path = scope.get("path", "")
        if path == "/adk" or path.startswith("/adk/"):
            inner = path[4:] or "/"
            scope = dict(scope, path=inner, raw_path=inner.encode())
            return await self.adk(scope, receive, send)
        return await self.ui(scope, receive, send)


def main() -> None:
    args = parse_args()
    if args.demo:
        os.environ["DEMO"] = "1"  # before importing the package: config reads it at import time
        os.environ.setdefault("DEMO_PACE", "0.2")  # watchable speed, about 2 minutes for the whole run
    if args.rebuild_ui or not (DIST / "index.html").exists():
        build_ui()

    import uvicorn
    from google.adk.cli.fast_api import get_fast_api_app
    from starlette.staticfiles import StaticFiles

    from travel_planner import config

    adk_app = get_fast_api_app(agents_dir=str(ROOT), web=False, host=args.host, port=args.port)
    app = PlannerApp(adk_app, StaticFiles(directory=DIST, html=True))

    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    mode = "demo replay (recorded Lisbon run; send the suggested messages)" if config.demo() else "live planning"
    print(f"\nTrip planner: {url}\n  mode:   {mode}\n  model:  {config.llm_settings()['model']}"
          f"\n  guides: {config.OUTPUT_DIR}\n  stop:   Ctrl+C\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
