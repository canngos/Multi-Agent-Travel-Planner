"""Deterministic clean-up of the itinerary writer's plan (after-agent callback, runs after link grounding).

Rules the model sometimes breaks and code can fix reliably:
- items in time order within each day;
- arrival / departure items built from the chosen transport options, and nothing planned before the
  arrival or after the departure;
- the chosen transport and stay copied unchanged from the summaries (`restore_chosen_options`);
- ticket prices reset to the researched values (`ground_tickets`), and a display-ready "ticket" text per sight, so the guide writer copies it instead of interpreting null
  (null means "unknown", never "free");
- "walk_km" per day (straight-line km between consecutive stops) and "cost" recomputed from the plan's own
  transport, stay and tickets (the model once forgot to multiply tickets by the number of travellers).
"""
import json
import re

from .output import extract_json
from .review_checks import (MEAL_NAME, _dicts, _key, _minutes, _parse, _price, clock_minutes, find_place, plan_cost,
                            research_places, transport_summary, walk_km)

_OTHER_FORMAT = re.compile(r"\d\s*[ap]\.?\s*m\b|\d[h.]\d{2}", re.IGNORECASE)  # "1:00 PM", "1 pm", "13h00", "13.00"


# names of "free" items that plan nothing (the live runs' "Leisure morning", "Free snack", "Afternoon coffee break",
# "Check-in and settle", "Indoor activity"); a named activity such as "Souvenirs" or "Beach" stays
_FILLER = re.compile(r"leisure|\bfree\b|snack|coffee|\bbreak\b|\brest\b|relax|settle|check[- ]?in|indoor activit|"
                     r"own time|explore|stroll|wander|\btips?\b|taxi|transfer|unwind|downtime|refresh", re.I)


def drop_filler(items: list[dict], label) -> tuple[list[dict], list[str]]:
    """Removes items that plan nothing: "free" items that are not a meal placeholder ("Leisure morning", "Free snack",
    "Check-in and settle", "Indoor activity") and "transport" items that are not a journey ("Taxi to accommodation",
    "Metro tip"). Live runs filled 8 of 21 plans with them, and a 21-day plan's days 9-20 with "Leisure morning"
    only; the checks then saw no gap. Removed, the gap is reported (thin day, empty afternoon) with researched
    alternatives."""
    kept, changes = [], []
    for item in items:
        name = str(item.get("name") or "")
        filler = (item.get("kind") == "free" and not MEAL_NAME.search(name) and _FILLER.search(name)) or \
                 (item.get("kind") == "transport" and not _TRAVEL_WORDS.search(name))
        if filler:
            changes.append(f"day {label}: removed '{name}' (plans nothing)")
        else:
            kept.append(item)
    return kept, changes


def _normalise_times(items: list[dict], label) -> list[str]:
    """"1:00 PM" / "13h00" -> "13:00", so the guide shows one format and nothing misreads the time."""
    changes = []
    for item in items:
        text = str(item.get("time") or "")
        t = clock_minutes(text)
        if t is not None and _OTHER_FORMAT.search(text):
            item["time"] = f"{t // 60 % 24:02d}:{t % 60:02d}"
            changes.append(f"day {label}: time '{text}' -> {item['time']}")
    return changes


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
        changes += _normalise_times(original, day.get("day"))
        original, dropped_filler = drop_filler(original, day.get("day"))
        changes += dropped_filler
        items = list(original)
        untimed_at = float("inf")  # items without a time go last (on the departure day: just before leaving)
        if anchored:  # replace the model's travel items with ones built from the chosen options
            items = [i for i in items
                     if not (i.get("kind") == "transport" and _TRAVEL_WORDS.search(str(i.get("name", ""))))]
            if len(items) != len(original):
                changes.append(f"day {day.get('day')}: travel items rebuilt from the chosen transport")
            date = day.get("date")
            if leave_out and arrive and leave_out[0] == date and leave_out[0] != arrive[0]:
                items.append(_leg_item(outbound, leave_out[1], f"Depart from {brief.get('origin', 'home')}"))
            if arrive and arrive[0] == date:
                start_min = clock_minutes(arrive[1])
                items = [i for i in items if i.get("kind") == "transport" or _minutes(i) is None
                         or _minutes(i) >= start_min]
                items.append(_leg_item(outbound, arrive[1], f"Arrive in {brief.get('destination', 'the city')}"))
            elif arrive and date and date < arrive[0]:
                items = [i for i in items if i.get("kind") == "transport"]
            if leave_back and leave_back[0] == date:
                end_min = clock_minutes(leave_back[1])
                items = [i for i in items if i.get("kind") == "transport" or _minutes(i) is None
                         or _minutes(i) <= end_min]
                items.append(_leg_item(back, leave_back[1], f"Depart from {brief.get('destination', 'the city')}"))
                untimed_at = end_min - 0.5
            dropped = [i for i in original if i.get("kind") != "transport" and i not in items]
            changes += [f"day {day.get('day')}: dropped '{i.get('name')}' (outside the travel times)" for i in dropped]
        ordered = sorted(items, key=lambda i: untimed_at if _minutes(i) is None else _minutes(i))
        for item in ordered:
            if item.get("kind") == "sight":
                item["ticket"] = ticket_text(item.get("ticket_eur"))
        day["items"] = ordered
        day["walk_km"] = walk_km(ordered)
    if brief.get("travellers") and days:
        cost = plan_cost(plan, brief)
        old_total = (plan.get("cost") or {}).get("total_eur") if isinstance(plan.get("cost"), dict) else None
        if cost and (old_total is None or abs(float(old_total) - cost["total_eur"]) > 1):
            changes.append(f"cost recomputed from the plan: total €{cost['total_eur']:g} (was {old_total})")
        if cost:
            plan["cost"] = cost
    return changes


def ground_tickets(plan: dict, places: list[dict]) -> list[str]:
    """Sets every researched sight's ticket_eur to the research value (prices are facts, not plan choices).

    The writer once "saved money" by setting real ticket prices to 0. A sight that is not in the research keeps
    its value; the critic's check reports it as an ungrounded place.
    """
    changes = []
    for day in plan.get("days") or []:
        for item in (day.get("items") or []) if isinstance(day, dict) else []:
            if not isinstance(item, dict) or item.get("kind") != "sight":
                continue
            found = find_place(item.get("name"), places)
            if found is not None and item.get("ticket_eur") != found.get("ticket_eur"):
                changes.append(f"day {day.get('day')}: '{item.get('name')}' ticket {item.get('ticket_eur')} -> "
                               f"{found.get('ticket_eur')} (research value)")
                item["ticket_eur"] = found.get("ticket_eur")
    return changes


def apply_pins(plan: dict, state) -> list[str]:
    """A re-plan that keeps the transport (or stay) research and changed nothing that bears on it keeps the previous
    plan's choice (state["pinned_choices"], set by pipeline.reset_plan_state)."""
    pins = state.get("pinned_choices") or {}
    changes = []
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else None
    old = pins.get("transport") if isinstance(pins.get("transport"), dict) else {}
    for leg in ("outbound", "return"):
        if transport is not None and isinstance(old.get(leg), dict) and transport.get(leg) != old[leg]:
            transport[leg] = old[leg]
            changes.append(f"{leg} kept from the previous plan (the change did not concern transport)")
    if isinstance(pins.get("stay"), dict) and plan.get("stay") != pins["stay"]:
        plan["stay"] = pins["stay"]
        changes.append("stay kept from the previous plan (the change did not concern the stay)")
    return changes


def tag_food_tiers(plan: dict, places: list[dict]) -> None:
    """Copies each planned restaurant's researched budget tier onto the item, so the cost can price a splurge."""
    for day in plan.get("days") or []:
        for item in (day.get("items") or []) if isinstance(day, dict) else []:
            if isinstance(item, dict) and item.get("kind") in ("restaurant", "cafe", "bar"):
                tier = (find_place(item.get("name"), places) or {}).get("budget_tier")
                if tier:
                    item["budget_tier"] = tier
                else:
                    item.pop("budget_tier", None)


def rename_typos(plan: dict, places: list[dict]) -> list[str]:
    """A planned place whose name is a typo of a researched one ("Notre-Dam Treasury") gets the researched name, so
    the guide spells it right. Names that contain each other ("Faca & Garfo restaurant") are left alone."""
    changes = []
    for day in plan.get("days") or []:
        for item in (day.get("items") or []) if isinstance(day, dict) else []:
            if not isinstance(item, dict) or item.get("kind") not in ("sight", "restaurant", "cafe", "bar"):
                continue
            found = find_place(item.get("name"), places)
            if not found:
                continue
            a, b = _key(item.get("name")), _key(found.get("name"))
            if a != b and f" {a} " not in f" {b} " and f" {b} " not in f" {a} ":
                changes.append(f"day {day.get('day')}: '{item.get('name')}' renamed to the researched '{found['name']}'")
                item["name"] = found["name"]
    return changes


def restore_chosen_options(plan: dict, state) -> list[str]:
    """Replaces the chosen transport options and stay with the matching summary entries, copied unchanged.

    The model is told to copy them unchanged but once mistyped a Kiwi short link (".../wh8w2p" for
    ".../wh8w2w"); grounding then removed the link. Matching: transport on operator, departure and price; the
    stay on name and total price.
    """
    changes = []
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else None
    tsum = transport_summary(state)
    for leg in ("outbound", "return"):
        chosen = (transport or {}).get(leg)
        if not isinstance(chosen, dict):
            continue
        picked = (tsum.get("recommended") or {}).get(leg) if isinstance(tsum.get("recommended"), dict) else None
        for d in list(_dicts(tsum.get(leg))) + ([picked] if isinstance(picked, dict) else []):
            if d.get("operator") == chosen.get("operator") and d.get("depart") == chosen.get("depart") \
                    and _price(d.get("price_eur")) == _price(chosen.get("price_eur")) and "url" in d:
                if d != chosen:
                    transport[leg] = dict(d)
                    changes.append(f"{leg} option restored from the transport summary")
                break
    stay = plan.get("stay")
    if isinstance(stay, dict):
        for d in _dicts(_parse(state.get("stay_summary"))):
            if d.get("name") == stay.get("name") and _price(d.get("total_price_eur")) == _price(stay.get("total_price_eur")) \
                    and "url" in d:
                if d != stay:
                    plan["stay"] = dict(d)
                    changes.append("stay restored from the stay summary")
                break
    return changes


def drop_unneeded_stay(plan: dict, brief: dict) -> list[str]:
    """The travellers stay in their own place (brief own_accommodation): a stay the model planned anyway goes, so it
    costs nothing and the guide does not offer one. A fact the user stated, like an excluded transport mode."""
    if not brief.get("own_accommodation") or not plan.get("stay"):
        return []
    name = plan["stay"].get("name") if isinstance(plan["stay"], dict) else plan["stay"]
    plan["stay"] = None
    plan["stay_why"] = "The travellers stay in their own place."
    return [f"stay '{name}' removed: the travellers need no accommodation"]


def drop_unneeded_transport(plan: dict, brief: dict) -> list[str]:
    """The travellers drive their own car (brief own_car): a flight, train or bus the model planned anyway goes (a car
    ferry stays). A fact the user stated, like an excluded transport mode."""
    transport = plan.get("transport")
    if not brief.get("own_car") or not isinstance(transport, dict):
        return []
    changes = []
    for leg in ("outbound", "return"):
        option = transport.get(leg)
        if isinstance(option, dict) and option.get("mode") != "ferry":
            transport[leg] = None
            changes.append(f"{leg} {option.get('mode')} ({option.get('operator')}) removed: the travellers drive "
                           "their own car")
    if changes and not any(isinstance(transport.get(leg), dict) for leg in ("outbound", "return")):
        transport["why"] = "Own car."
    return changes


def tidy_itinerary(callback_context):
    """After-agent callback of the itinerary writer."""
    state = callback_context.state
    try:
        plan = extract_json(state.get("itinerary") or "")
    except ValueError:
        return None
    changes = drop_unneeded_stay(plan, state.get("trip_brief") or {})
    changes += drop_unneeded_transport(plan, state.get("trip_brief") or {})
    changes += apply_pins(plan, state)
    places = research_places(state)
    changes += restore_chosen_options(plan, state) + rename_typos(plan, places) + ground_tickets(plan, places)
    tag_food_tiers(plan, places)
    changes += tidy_plan(plan, state.get("trip_brief") or {})
    state["itinerary"] = "```json\n" + json.dumps(plan, ensure_ascii=False, indent=1) + "\n```"
    if changes:
        state["itinerary_tidy_changes"] = changes
    return None
