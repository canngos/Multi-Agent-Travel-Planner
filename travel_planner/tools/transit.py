"""Train / bus / ferry connections from Transitous (MOTIS 2 API). Schedules only, no prices."""
from datetime import datetime
from zoneinfo import ZoneInfo

from ..models import TransportOption
from ._http import get_json
from ._result import ToolError, ok, safe_tool

TRANSITOUS_PLAN = "https://api.transitous.org/api/v5/plan"
ATTRIBUTION = "Routing by Transitous (https://transitous.org/sources/)"

MODE_MAP = {
    "AIRPLANE": "flight",
    "FERRY": "ferry",
    "BUS": "bus", "COACH": "bus",
    "RAIL": "train", "HIGHSPEED_RAIL": "train", "LONG_DISTANCE": "train", "NIGHT_RAIL": "train",
    "REGIONAL_FAST_RAIL": "train", "REGIONAL_RAIL": "train", "SUBURBAN": "train",
    "SUBWAY": "train", "METRO": "train", "TRAM": "train", "FUNICULAR": "train", "CABLE_CAR": "train",
}
LOCAL_MODES = {"WALK", "BIKE", "CAR", "RENTAL", "FLEX"}


def _local(iso_utc: str, tz: str | None) -> str:
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    if tz:
        dt = dt.astimezone(ZoneInfo(tz))
    return dt.strftime("%Y-%m-%dT%H:%M")


def _leg_label(leg: dict) -> str:
    mode = leg["mode"].lower().replace("_", " ")
    route = leg.get("routeShortName") or ""
    agency = leg.get("agencyName") or ""
    frm, to = leg.get("from", {}).get("name", "?"), leg.get("to", {}).get("name", "?")
    label = f"{mode} {route}".strip()
    if agency:
        label += f" ({agency})"
    return f"{label}: {frm} → {to}"


def itinerary_to_option(it: dict) -> TransportOption | None:
    transit = [leg for leg in it.get("legs", []) if leg.get("mode") not in LOCAL_MODES]
    if not transit:
        return None
    main = max(transit, key=lambda leg: leg.get("duration", 0))
    first, last = it["legs"][0], it["legs"][-1]
    return TransportOption(
        mode=MODE_MAP.get(main["mode"], "train"),
        operator=main.get("agencyName") or main["mode"].title(),
        depart=_local(it["startTime"], first.get("from", {}).get("tz")),
        arrive=_local(it["endTime"], last.get("to", {}).get("tz")),
        duration_min=round(it["duration"] / 60),
        stops=it.get("transfers", 0),
        price_eur=None,
        price_source=None,
        source="transitous",
        url=main.get("agencyUrl") or "https://transitous.org/",
        details=" | ".join(_leg_label(leg) for leg in transit),
    )


LEG_FIELDS = ("mode", "duration", "routeShortName", "agencyName", "agencyUrl", "startTime", "endTime")


def slim_plan(data: dict) -> dict:
    """Drops geometry, stop lists and debug output; keeps what itinerary_to_option reads."""
    if not isinstance(data, dict):
        return data

    def place(p: dict) -> dict:
        return {k: p.get(k) for k in ("name", "tz", "lat", "lon")}

    return {"itineraries": [
        {
            **{k: it.get(k) for k in ("duration", "startTime", "endTime", "transfers")},
            "legs": [{**{k: leg.get(k) for k in LEG_FIELDS},
                      "from": place(leg.get("from", {})), "to": place(leg.get("to", {}))} for leg in it.get("legs", [])],
        }
        for it in data.get("itineraries", [])
    ]}


@safe_tool
async def plan_public_transport(
    from_lat: float, from_lon: float, to_lat: float, to_lon: float, depart_date: str, depart_time: str = "08:00",
    max_results: int = 3,
) -> dict:
    """Plans train, bus and ferry connections between two points on a given day.

    Returns schedules (times, duration, transfers, operator, every leg) but NO
    prices: find fares with web_search on the operator and tag them "web_estimate".
    Coverage is best in Europe; an empty result means no public-transport route.

    Args:
        from_lat: Origin latitude.
        from_lon: Origin longitude.
        to_lat: Destination latitude.
        to_lon: Destination longitude.
        depart_date: Departure day, ISO "YYYY-MM-DD".
        depart_time: Earliest departure, "HH:MM" (treated as UTC, which is close enough for Europe).
        max_results: Number of connections (1-5).
    """
    datetime.fromisoformat(f"{depart_date}T{depart_time}")  # validates the inputs
    data = await get_json(
        TRANSITOUS_PLAN,
        namespace="transitous",
        params={
            "fromPlace": f"{round(from_lat, 4)},{round(from_lon, 4)}",
            "toPlace": f"{round(to_lat, 4)},{round(to_lon, 4)}",
            "time": f"{depart_date}T{depart_time}:00Z",
            "numItineraries": max(1, min(int(max_results), 5)),
        },
        slim=slim_plan,
    )
    options = [o for o in (itinerary_to_option(it) for it in data.get("itineraries", [])) if o]
    if not options:
        raise ToolError("No public-transport connection found for this route and day")
    return ok(options[: max(1, min(int(max_results), 5))], attribution=ATTRIBUTION)
