"""ADK entry point. Phase 0: Spike 1 (single agent + dummy tool).

Replaced by the concierge in Phase 7.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from google.adk.agents import LlmAgent

from .config import MODEL

_CITY_TZ = {
    "lisbon": "Europe/Lisbon",
    "helsinki": "Europe/Helsinki",
    "tampere": "Europe/Helsinki",
    "rome": "Europe/Rome",
    "tokyo": "Asia/Tokyo",
}


def get_time(city: str) -> dict:
    """Returns the current local time in a city.

    Args:
        city: City name, e.g. "Lisbon".
    """
    tz = _CITY_TZ.get(city.strip().lower())
    if tz is None:
        return {"status": "error", "reason": f"Unknown city: {city}. Known: {sorted(_CITY_TZ)}"}
    now = datetime.now(ZoneInfo(tz))
    return {"status": "ok", "city": city, "timezone": tz, "local_time": now.strftime("%Y-%m-%d %H:%M")}


root_agent = LlmAgent(
    name="time_agent",
    model=MODEL,
    description="Tells the local time in a city.",
    instruction=(
        "You answer questions about the local time in cities. "
        "Always call the get_time tool; never guess the time. "
        "If the tool returns an error, tell the user which cities are known."
    ),
    tools=[get_time],
)
