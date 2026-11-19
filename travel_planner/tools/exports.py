"""Calendar (.ics) and map (GeoJSON) files of the finished plan, written next to the guide (REVIEW.md A7).

Both tools read the reviewed itinerary from session state, so the guide writer calls them without arguments, and
every time and place in the files is the plan's own. Times are local to the destination ("floating" times in the
calendar: a phone shows them as written, whatever its time zone).
"""
import json
import re
from datetime import date, datetime, timedelta, timezone

from google.adk.tools import ToolContext

from .. import config
from ..agents.output import extract_json
from ._result import ToolError, ok, safe_tool

ITEM_MINUTES = {"sight": 90, "restaurant": 60, "cafe": 45, "bar": 60, "free": 60, "transport": 60}
_TIME = re.compile(r"^(\d{1,2}):(\d{2})")


def _plan_and_brief(state) -> tuple[dict, dict]:
    try:
        plan = extract_json(state.get("itinerary") or "")
    except ValueError:
        plan = {}
    if not plan.get("days"):
        raise ToolError("There is no day-by-day plan in the session to export")
    return plan, state.get("trip_brief") or {}


def _stem(state, brief: dict) -> str:
    """The guide's file name without extension, so the files sit next to it."""
    from .trip import slugify

    markdown = (state.get("guide_paths") or {}).get("markdown")
    if markdown:
        return re.sub(r"\.md$", "", markdown.replace("\\", "/").rsplit("/", 1)[-1])
    return slugify(f"{brief.get('destination', 'trip')}-{brief.get('start_date', '')}-to-{brief.get('end_date', '')}")


def _remember(state, kind: str, path) -> None:
    paths = dict(state.get("guide_paths") or {})
    paths[kind] = str(path)
    state["guide_paths"] = paths


def _start(day: dict, item: dict) -> datetime | None:
    m = _TIME.match(str(item.get("time") or ""))
    try:
        d = date.fromisoformat(str(day.get("date")))
    except ValueError:
        return None
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2)))


def _ics_text(value) -> str:
    """RFC 5545 TEXT: backslash, semicolon, comma and newline escaped."""
    text = str(value or "")
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line after a space (RFC 5545)."""
    out, current = [], ""
    for ch in line:
        if len((current + ch).encode("utf-8")) > 75:
            out.append(current)
            current = " " + ch
        else:
            current += ch
    out.append(current)
    return "\r\n".join(out)


def calendar_events(plan: dict) -> list[dict]:
    """One event per timed item: start, end (the next item's start at the latest), title, place and link."""
    events = []
    for day in plan.get("days") or []:
        items = [(i, _start(day, i)) for i in day.get("items") or [] if isinstance(i, dict)]
        timed = [(i, t) for i, t in items if t is not None]
        for n, (item, start) in enumerate(timed):
            end = start + timedelta(minutes=ITEM_MINUTES.get(item.get("kind"), 60))
            if n + 1 < len(timed) and start < timed[n + 1][1] < end:
                end = timed[n + 1][1]
            events.append({"day": day.get("day"), "start": start, "end": end, "title": item.get("name") or "Plan item",
                           "kind": item.get("kind"), "note": item.get("note") or "",
                           "url": item.get("source_url") or item.get("url"), "lat": item.get("lat"),
                           "lon": item.get("lon")})
    return events


def calendar_text(plan: dict, brief: dict, stem: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Trip planner course project//EN", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{_ics_text('Trip to ' + str(brief.get('destination') or 'the destination'))}"]
    for n, ev in enumerate(calendar_events(plan), 1):
        lines += ["BEGIN:VEVENT", f"UID:{stem}-{n}@trip-planner", f"DTSTAMP:{stamp}",
                  f"DTSTART:{ev['start']:%Y%m%dT%H%M%S}", f"DTEND:{ev['end']:%Y%m%dT%H%M%S}",
                  f"SUMMARY:{_ics_text(ev['title'])}"]
        description = " ".join(x for x in (f"Day {ev['day']}." if ev["day"] else "", ev["note"],
                                           ev["url"] or "") if x)
        if description:
            lines.append(f"DESCRIPTION:{_ics_text(description)}")
        if isinstance(ev["lat"], (int, float)) and isinstance(ev["lon"], (int, float)):
            lines.append(f"GEO:{ev['lat']:.6f};{ev['lon']:.6f}")
        if ev["url"]:
            lines.append(f"URL:{ev['url']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def stops_geojson(plan: dict) -> dict:
    """A FeatureCollection of the plan's places with coordinates (and the stay), in visiting order."""
    features = []
    stay = plan.get("stay")
    if isinstance(stay, dict) and isinstance(stay.get("lat"), (int, float)) and isinstance(stay.get("lon"), (int, float)):
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [stay["lon"], stay["lat"]]},
                         "properties": {"kind": "stay", "name": stay.get("name"), "url": stay.get("url")}})
    order = 0
    for day in plan.get("days") or []:
        for item in day.get("items") or []:
            if not isinstance(item, dict):
                continue
            lat, lon = item.get("lat"), item.get("lon")
            if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
                continue
            order += 1
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                             "properties": {"order": order, "day": day.get("day"), "date": day.get("date"),
                                            "time": item.get("time"), "kind": item.get("kind"),
                                            "name": item.get("name"), "url": item.get("source_url")}})
    return {"type": "FeatureCollection", "features": features}


@safe_tool
def export_calendar(tool_context: ToolContext) -> dict:
    """Writes the plan's days as a calendar file (.ics) next to the guide: one event per planned item, with its
    time, place and link. Call it after save_guide; it needs no arguments."""
    plan, brief = _plan_and_brief(tool_context.state)
    stem = _stem(tool_context.state, brief)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUTPUT_DIR / f"{stem}.ics"
    text = calendar_text(plan, brief, stem)
    path.write_bytes(text.encode("utf-8"))
    _remember(tool_context.state, "calendar", path)
    return ok([{"calendar_path": str(path), "events": text.count("BEGIN:VEVENT")}])


@safe_tool
def export_map_stops(tool_context: ToolContext) -> dict:
    """Writes the plan's places with coordinates (and the stay) as a GeoJSON map file next to the guide, in
    visiting order. Call it after save_guide; it needs no arguments."""
    plan, brief = _plan_and_brief(tool_context.state)
    data = stops_geojson(plan)
    if not data["features"]:
        raise ToolError("No planned place has coordinates, so there is nothing to put on a map")
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUTPUT_DIR / f"{_stem(tool_context.state, brief)}.geojson"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    _remember(tool_context.state, "map", path)
    return ok([{"map_path": str(path), "stops": len(data["features"])}])
