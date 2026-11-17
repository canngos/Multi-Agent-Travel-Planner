"""Deterministic clean-up of the planner's itinerary (after-agent callback, runs after link grounding).

Rules the model sometimes breaks and code can fix reliably:
- items in time order within each day;
- arrival / departure items built from the chosen transport options, and nothing planned before the
  arrival or after the departure;
- a display-ready "ticket" text per sight, so the guide writer copies it instead of interpreting null
  (null means "unknown", never "free").
"""
import json
import re

from .output import extract_json

_TIME = re.compile(r"^(\d{1,2}):(\d{2})")


def _minutes(item: dict) -> int | None:
    m = _TIME.match(str(item.get("time") or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def ticket_text(ticket) -> str | None:
    if ticket is None:
        return "ticket price: see the website"
    try:
        value = float(ticket)
    except (TypeError, ValueError):
        return "ticket price: see the website"
    return "free entry" if value == 0 else f"ticket €{value:g}"


_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{1,2}:\d{2})")
_TRAVEL_WORDS = re.compile(r"arriv|depart|land|flight|fly|ferry|train|bus|airport|terminal|port", re.I)


def _when(iso) -> tuple[str, str] | None:
    m = _ISO.match(str(iso or ""))
    return (m.group(1), m.group(2).zfill(5)) if m else None


def _leg_item(option: dict, time: str, name: str) -> dict:
    mode = option.get("mode") or "trip"
    return {"time": time, "kind": "transport", "name": name,
            "note": f"{mode.capitalize()} with {option.get('operator', '?')}; see Getting there and back.",
            "ticket_eur": None, "hours": None, "lat": None, "lon": None, "source_url": option.get("url")}


def tidy_plan(plan: dict, brief: dict | None = None) -> list[str]:
    """Edits the plan in place; returns a list of the changes made.

    Arrival and departure items are rebuilt from the chosen transport (the model sometimes copies the
    times of a different option); nothing stays before the arrival or after the departure.
    """
    brief = brief or {}
    changes = []
    days = [d for d in plan.get("days") or [] if isinstance(d, dict)] if isinstance(plan.get("days"), list) else []
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    outbound = transport.get("outbound") if isinstance(transport.get("outbound"), dict) else None
    back = transport.get("return") if isinstance(transport.get("return"), dict) else None
    leave_out, arrive = (_when(outbound.get("depart")), _when(outbound.get("arrive"))) if outbound else (None, None)
    leave_back = _when(back.get("depart")) if back else None
    dates = [d.get("date") for d in days]
    anchored = bool(arrive and arrive[0] in dates) or bool(leave_back and leave_back[0] in dates)

    for day in days:
        original = [i for i in day.get("items") or [] if isinstance(i, dict)]
        items = list(original)
        if anchored:  # replace the model's travel items with ones built from the chosen options
            items = [i for i in items
                     if not (i.get("kind") == "transport" and _TRAVEL_WORDS.search(str(i.get("name", ""))))]
            if len(items) != len(original):
                changes.append(f"day {day.get('day')}: travel items rebuilt from the chosen transport")
            date = day.get("date")
            if leave_out and arrive and leave_out[0] == date and leave_out[0] != arrive[0]:
                items.append(_leg_item(outbound, leave_out[1], f"Depart from {brief.get('origin', 'home')}"))
            if arrive and arrive[0] == date:
                start_min = _minutes({"time": arrive[1]})
                items = [i for i in items if i.get("kind") == "transport" or (_minutes(i) or 0) >= start_min]
                items.append(_leg_item(outbound, arrive[1], f"Arrive in {brief.get('destination', 'the city')}"))
            elif arrive and date and date < arrive[0]:
                items = [i for i in items if i.get("kind") == "transport"]
            if leave_back and leave_back[0] == date:
                end_min = _minutes({"time": leave_back[1]})
                items = [i for i in items if i.get("kind") == "transport" or (_minutes(i) or 0) <= end_min]
                items.append(_leg_item(back, leave_back[1], f"Depart from {brief.get('destination', 'the city')}"))
            dropped = [i for i in original if i.get("kind") != "transport" and i not in items]
            changes += [f"day {day.get('day')}: dropped '{i.get('name')}' (outside the travel times)" for i in dropped]
        ordered = sorted(items, key=lambda i: (_minutes(i) is None, _minutes(i) or 0))
        for item in ordered:
            if item.get("kind") == "sight":
                item["ticket"] = ticket_text(item.get("ticket_eur"))
        day["items"] = ordered
    return changes


def tidy_itinerary(callback_context):
    """After-agent callback for the planner (and the Phase 8 reviser)."""
    state = callback_context.state
    try:
        plan = extract_json(state.get("itinerary") or "")
    except ValueError:
        return None
    changes = tidy_plan(plan, state.get("trip_brief") or {})
    state["itinerary"] = "```json\n" + json.dumps(plan, ensure_ascii=False, indent=1) + "\n```"
    if changes:
        state["itinerary_tidy_changes"] = changes
    return None
