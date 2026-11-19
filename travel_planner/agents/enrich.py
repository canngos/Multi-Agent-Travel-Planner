"""Fills gaps in the research agents' results from the raw tool results: sight tickets and opening hours.

The culture agent is told to copy tickets and hours from its tools, but in a live Tallinn run it left every
museum's ticket empty although Wikivoyage listed them, so the trip cost counted €0 for all tickets. Values the
agent did write are never replaced; a filled value brings its tool's source link along.
"""
import json

from .output import extract_json
from .review_checks import FOOD_KINDS, find_place


def tool_result_items(session) -> list[dict]:
    """Every named result item of every tool response in the session so far."""
    items = []
    for event in getattr(session, "events", None) or []:
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", None) or []:
            response = getattr(getattr(part, "function_response", None), "response", None)
            if isinstance(response, dict):
                items += [r for r in response.get("results") or [] if isinstance(r, dict) and r.get("name")]
    return items


def _fill(place: dict, donors: list[dict]) -> bool:
    need_ticket = place.get("kind") == "sight" and place.get("ticket_eur") is None
    need_hours = not place.get("hours")
    filled = False
    for donor in donors:
        if not (find_place(place["name"], [donor]) or find_place(donor["name"], [place])):
            continue
        used = False
        if need_ticket and donor.get("ticket_eur") is not None:
            place["ticket_eur"], need_ticket, used = donor["ticket_eur"], False, True
        if need_hours and donor.get("hours"):
            place["hours"], need_hours, used = donor["hours"], False, True
        if used:
            filled = True
            urls = place.setdefault("source_urls", [])
            new = [u for u in donor.get("source_urls") or [] if u not in urls]
            urls += new[:1]
        if not (need_ticket or need_hours):
            break
    return filled


def fill_from_tool_results(output_key: str):
    """After-agent callback factory (before link grounding) for the culture and food agents."""
    def callback(callback_context):
        state = callback_context.state
        try:
            data = extract_json(state.get(output_key) or "")
        except ValueError:
            return None
        items = tool_result_items(callback_context.session)
        filled = 0
        for place in data.get("results") or []:
            if isinstance(place, dict) and place.get("name"):
                kinds = {"sight"} if place.get("kind") == "sight" else FOOD_KINDS
                filled += _fill(place, [i for i in items if i.get("kind") in kinds])
        if filled:
            data["notes"] = (str(data.get("notes") or "") + f" Filled {filled} missing ticket price(s) or opening "
                                                             "hours from the tool results.").strip()
            state[output_key] = "```json\n" + json.dumps(data, ensure_ascii=False, indent=1) + "\n```"
        return None

    callback.__name__ = f"fill_{output_key}"
    return callback
