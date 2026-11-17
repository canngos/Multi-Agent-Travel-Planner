"""Shared Overpass (OpenStreetMap) access: official instance first, then a public mirror.

Both often take 15-35 s, so: long timeout, no retry on timeout, results cached for 30 days.
"""
from ._http import post_json
from ._result import ToolError

OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
OVERPASS_TIMEOUT_S = 60.0
MAX_AGE_S = 30 * 24 * 3600
ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"


async def overpass(query: str, *, unavailable_hint: str) -> dict:
    """Runs an Overpass QL query; raises ToolError naming both failures plus a hint for the agent."""
    errors = []
    for url in OVERPASS_URLS:
        try:
            return await post_json(url, namespace="overpass", data={"data": query}, max_age_s=MAX_AGE_S,
                                   timeout_s=OVERPASS_TIMEOUT_S, retries=0)
        except ToolError as exc:
            errors.append(str(exc))
    raise ToolError("Overpass unavailable (" + " | ".join(errors) + "). " + unavailable_hint)


def element_point(el: dict) -> tuple[float | None, float | None]:
    return el.get("lat") or el.get("center", {}).get("lat"), el.get("lon") or el.get("center", {}).get("lon")


def osm_url(el: dict) -> str:
    return f"https://www.openstreetmap.org/{el['type']}/{el['id']}"
