"""Checks that this computer is ready for live trip planning, and says what each missing piece costs.

  .venv/Scripts/python scripts/check_setup.py

Free checks only, plus one tiny request to the LLM (a one-line answer). Nothing is booked or bought, and no
search quota is used except that single model call. Exit code 0 = ready (possibly with reduced features),
1 = not ready (no working LLM).
"""
import asyncio
import importlib.util
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

OK, WARN, FAIL = "ok  ", "warn", "FAIL"
rows: list[tuple[str, str, str]] = []


def row(status: str, what: str, detail: str) -> None:
    rows.append((status, what, detail))
    print(f"[{status}] {what:<28} {detail}")


def check_python() -> None:
    v = sys.version_info
    row(OK if v >= (3, 11) else FAIL, "Python", f"{v.major}.{v.minor}.{v.micro}" + ("" if v >= (3, 11) else " (needs 3.11+)"))
    missing = [m for m in ("google.adk", "litellm", "httpx", "pydantic", "dotenv", "markdown", "fast_flights")
               if importlib.util.find_spec(m) is None]
    row(FAIL if missing else OK, "Python packages",
        f"missing {', '.join(missing)}: run pip install -r requirements.txt" if missing else "all installed")


def check_env() -> None:
    env = ROOT / ".env"
    row(OK if env.exists() else WARN, ".env file", "found" if env.exists() else
        "not found: copy .env.example to .env and fill in the keys")


async def check_llm() -> bool:
    from travel_planner import config

    settings = config.llm_settings()
    if config.demo():
        row(WARN, "LLM", "DEMO=1 is set: answers come from the recording, live planning is off")
        return True
    if not settings["api_key"] and not config.LLM_MODEL:
        row(FAIL, "LLM", "LEHMUS_API_KEY is empty in .env (or set LLM_MODEL and LLM_API_KEY for another model)")
        return False
    import litellm

    t0 = time.perf_counter()
    try:
        resp = await asyncio.wait_for(litellm.acompletion(
            model=settings["model"], api_base=settings["api_base"], api_key=settings["api_key"],
            messages=[{"role": "user", "content": "Reply with the single word OK."}], max_tokens=200), timeout=60)
        text = (resp.choices[0].message.content or "").strip()
        row(OK, "LLM", f"{settings['model']} answered in {time.perf_counter() - t0:.1f} s ({text[:20]!r})")
        return True
    except Exception as exc:  # noqa: BLE001 - report any failure in plain words
        hint = " Lehmus works only on the University of Oulu network or VPN." if "lehmus" in str(settings["api_base"]) else ""
        row(FAIL, "LLM", f"{settings['model']}: {type(exc).__name__}: {str(exc)[:160]}.{hint}")
        return False


KEYS = [
    ("TAVILY_API_KEY", "web search", "no opening hours or ticket prices from the web, no ferry/bus fares (prices shown as unknown)"),
    ("OPENTRIPMAP_API_KEY", "sights", "sights come from Wikivoyage only (fewer, often without coordinates)"),
    ("LITEAPI_KEY", "hotel rates", "hotels from SerpApi only, or Booking.com search links without prices"),
    ("SERPAPI_KEY", "Google Hotels/Flights", "hotels from liteAPI only; flight fallback uses the fast-flights library"),
]


def check_keys() -> None:
    for name, what, loss in KEYS:
        if os.environ.get(name):
            row(OK, f"{name}", f"set ({what})")
        else:
            row(WARN, f"{name}", f"not set: {loss}")
    contact = os.environ.get("CONTACT_EMAIL")
    row(OK if contact else WARN, "CONTACT_EMAIL", "set" if contact else
        "not set: Wikipedia, Wikivoyage and Nominatim block clients without a contact address (HTTP 403)")


async def check_services() -> None:
    import httpx

    from travel_planner import config

    services = [
        ("Kiwi.com MCP (flights)", "https://mcp.kiwi.com", "no flights from Kiwi; Google Flights fallback only"),
        ("Nominatim (geocoding)", "https://nominatim.openstreetmap.org/status", "geocoding falls back to Open-Meteo"),
        ("Open-Meteo (weather)", "https://api.open-meteo.com/v1/forecast?latitude=60.17&longitude=24.94&daily=temperature_2m_max",
         "no weather in the plan"),
        ("Transitous (trains, ferries)", "https://api.transitous.org/", "no ground transport options"),
        ("Wikivoyage", "https://en.wikivoyage.org/w/api.php?action=query&format=json&meta=siteinfo", "fewer sights and dishes"),
    ]
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": config.user_agent()}, follow_redirects=True) as client:
        async def status_of(url: str) -> int:
            # headers only: the MCP endpoint would otherwise keep a streaming connection open
            async with client.stream("GET", url) as r:
                return r.status_code

        for name, url, loss in services:
            try:
                code = await asyncio.wait_for(status_of(url), timeout=15)
                good = code < 400
                blocked = " (blocked: set CONTACT_EMAIL in .env; Wikimedia and Nominatim refuse clients without one)"                     if code in (403, 429) and not os.environ.get("CONTACT_EMAIL") else ""
                row(OK if good else WARN, name, f"reachable (HTTP {code})" if good else f"HTTP {code}{blocked}: {loss}")
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                row(WARN, name, f"not reachable ({type(exc).__name__}): {loss}")


def check_node() -> None:
    npx = shutil.which("npx")
    row(OK if npx else WARN, "Node.js / npx", "found (Airbnb search, web UI)" if npx else
        "not found: no Airbnb listings (hotels still work) and the web UI cannot be built; use adk web instead")
    dist = ROOT / "travel-planner-ui" / "dist" / "index.html"
    row(OK if dist.exists() else WARN, "Web UI build", "built" if dist.exists() else
        "not built yet: scripts/serve.py builds it on first start (needs npm)")


async def main() -> int:
    print("Checking this computer for live trip planning...\n")
    check_python()
    check_env()
    if any(r[0] == FAIL for r in rows):
        print("\nNot ready: install the Python requirements first.")
        return 1
    from travel_planner import config  # noqa: F401  (loads .env)

    llm_ok = await check_llm()
    check_keys()
    await check_services()
    check_node()
    warnings = [r for r in rows if r[0] == WARN]
    if not llm_ok:
        print("\nNot ready: the planner needs a working LLM (see the FAIL line above).")
        return 1
    if warnings:
        print(f"\nReady, with {len(warnings)} warning(s): the planner works, and the features above fall back as described.")
    else:
        print("\nReady: everything works.")
    print("Start it with: .venv/Scripts/python scripts/serve.py")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
