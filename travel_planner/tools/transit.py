"""Train / bus / ferry connections from Transitous (MOTIS 2 API). Schedules only, no prices.

Published timetables reach only a few months ahead, and not equally far for every operator: for a trip in
May 2027 (asked in November 2026) Transitous knew FlixBus but none of the Helsinki-Tallinn ferries. For a trip
more than TYPICAL_AFTER_DAYS away the tool therefore also asks for the same weekday about two weeks from today
and adds connections found only there, moved to the trip date and labelled as a typical timetable (like the
weather tool's "typical weather").
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config
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
TYPICAL_AFTER_DAYS = 60
TYPICAL_LEAD_DAYS = 14
MAX_AGE_S = 7 * 24 * 3600  # timetables change
EMPTY_MAX_AGE_S = 24 * 3600  # "nothing found" often means "not published yet": ask again tomorrow


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
    n = max(1, min(int(max_results), 5))
    trip_day = date.fromisoformat(depart_date)
    main_error = None
    try:
        options = await _query(from_lat, from_lon, to_lat, to_lon, depart_date, depart_time, n)
    except ToolError as exc:  # e.g. nothing published yet for that date
        options, main_error = [], exc
    if (trip_day - config.today()).days > TYPICAL_AFTER_DAYS:
        near = typical_day(trip_day, config.today())
        try:
            typical = await _query(from_lat, from_lon, to_lat, to_lon, near.isoformat(), depart_time, n)
        except Exception:  # noqa: BLE001 - the typical timetable is only a supplement
            typical = []
        known = {(o["mode"], o["operator"]) for o in options}
        extra = [move_to_day(o, near, trip_day) for o in typical if (o["mode"], o["operator"]) not in known]
        options = options + extra[: max(0, 5 - len(options))]
    if not options:
        raise main_error or ToolError("No public-transport connection found for this route and day")
    return ok(options, attribution=ATTRIBUTION)


async def _query(from_lat, from_lon, to_lat, to_lon, depart_date, depart_time, n) -> list[dict]:
    data = await get_json(
        TRANSITOUS_PLAN,
        namespace="transitous",
        params={
            "fromPlace": f"{round(from_lat, 4)},{round(from_lon, 4)}",
            "toPlace": f"{round(to_lat, 4)},{round(to_lon, 4)}",
            "time": f"{depart_date}T{depart_time}:00Z",
            "numItineraries": n,
        },
        slim=slim_plan,
        max_age_s=MAX_AGE_S,
        ttl_for=lambda body: None if isinstance(body, dict) and body.get("itineraries") else EMPTY_MAX_AGE_S,
    )
    return [o.model_dump() for o in (itinerary_to_option(it) for it in data.get("itineraries", [])) if o][:n]


def typical_day(trip_day: date, today: date) -> date:
    """The trip's weekday, at least TYPICAL_LEAD_DAYS from today (inside the published timetables)."""
    near = today + timedelta(days=TYPICAL_LEAD_DAYS)
    return near + timedelta(days=(trip_day.weekday() - near.weekday()) % 7)


def move_to_day(option: dict, near: date, trip_day: date) -> dict:
    """A connection from the typical day, with its dates moved to the trip day and a clear label."""
    shift = trip_day - near
    moved = dict(option)
    for key in ("depart", "arrive"):
        moved[key] = (datetime.fromisoformat(option[key]) + shift).strftime("%Y-%m-%dT%H:%M")
    moved["details"] = (f"{option.get('details', '')} | TYPICAL TIMETABLE: times from {near.isoformat()} (the trip "
                        "date is beyond the published timetable); check the real times before booking").lstrip(" |")
    return moved
