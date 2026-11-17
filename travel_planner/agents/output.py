"""Reads and checks the JSON that research agents write to session state.

Used by the dev harness and tests now, and by the pipeline's sanity checks later.
"""
import json
import re

from pydantic import ValidationError

from ..models import Place, StayOption, TransportOption

# output_key -> {path: model}. A path ending in "[]" must be a list of items; otherwise the value is one
# object or null. "by_area[].hotel" checks the "hotel" object of every element of the "by_area" list.
SCHEMAS = {
    "culture_findings": {"results[]": Place},
    "food_findings": {"results[]": Place},
    "flight_options": {"outbound[]": TransportOption, "return[]": TransportOption},
    "ground_options": {"outbound[]": TransportOption, "return[]": TransportOption},
    "airbnb_options": {"results[]": StayOption},
    "hotel_options": {"results[]": StayOption},
    "transport_summary": {f"{d}.{k}": TransportOption for d in ("outbound", "return") for k in ("cheapest", "fastest")}
    | {"outbound.ranked[]": TransportOption, "return.ranked[]": TransportOption},
    "stay_summary": {"cheapest": StayOption, "best_value": StayOption, "top_rated": StayOption,
                     "by_area[].airbnb": StayOption, "by_area[].hotel": StayOption},
}
_MISSING = object()


def _resolve(data, path: str) -> list[tuple[str, object]]:
    """(label, value) pairs for a path like "by_area[].hotel"; _MISSING marks an absent key."""
    found = [("", data)]
    for part in path.split("."):
        is_list = part.endswith("[]")
        key = part[:-2] if is_list else part
        nxt = []
        for label, node in found:
            value = node.get(key, _MISSING) if isinstance(node, dict) else _MISSING
            here = f"{label}.{key}" if label else key
            if is_list and isinstance(value, list):
                nxt.extend((f"{here}[{i}]", v) for i, v in enumerate(value))
            elif is_list and value is not _MISSING:
                nxt.append((here, "not a list"))
            else:
                nxt.append((here, value))
        found = nxt
    return found

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json(text: str) -> dict:
    """The JSON object from an agent reply: the last ```json fence, else the first {...} in the text."""
    fences = _FENCE.findall(text or "")
    candidates = list(reversed(fences))
    start = (text or "").find("{")
    if start >= 0:
        candidates.append(text[start:])
    for candidate in candidates:
        try:
            obj, _ = json.JSONDecoder().raw_decode(candidate.strip())
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("no JSON object found in the agent output")


def check_output(output_key: str, text: str) -> tuple[dict, list[str]]:
    """Parses an agent's output and validates every item against the shared models.

    Returns (data, problems). `problems` is empty when everything is valid.
    """
    try:
        data = extract_json(text)
    except ValueError as exc:
        return {}, [str(exc)]
    problems = []
    for path, model in SCHEMAS.get(output_key, {}).items():
        for label, value in _resolve(data, path):
            if value is _MISSING:
                problems.append(f"'{label}' is missing" + (" or not a list" if path.endswith("[]") else ""))
            elif value == "not a list":
                problems.append(f"'{label}' is missing or not a list")
            elif value is not None:
                try:
                    model.model_validate(value)
                except ValidationError as exc:
                    first = exc.errors()[0]
                    problems.append(f"{label}: {'.'.join(map(str, first['loc']))}: {first['msg']}")
    if not isinstance(data.get("notes", ""), str):
        problems.append("'notes' must be a string")
    links = data.get("fallback_links", [])
    if not (isinstance(links, list) and all(isinstance(u, str) for u in links)):
        problems.append("'fallback_links' must be a list of strings")
    return data, problems


def item_urls(data: dict) -> list[str]:
    """Every URL the agent reports (fallback_links, url fields, source_urls lists), for grounding checks."""
    urls = [u for u in data.get("fallback_links") or [] if isinstance(u, str)]

    def walk(node, top: bool) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, False)
        elif isinstance(node, dict):
            if not top:
                for key in ("url", "source_url"):
                    if isinstance(node.get(key), str):
                        urls.append(node[key])
                urls.extend(u for u in node.get("source_urls") or [] if isinstance(u, str))
            for key, value in node.items():
                if key != "fallback_links" and isinstance(value, (list, dict)):
                    walk(value, False)

    walk(data, True)
    return list(dict.fromkeys(urls))
