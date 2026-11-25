"""transport_summary and stay_summary, built in code from the searchers' results.

Comparing options is sorting and filtering, so code does it instead of an LLM agent: the result is exact, every
option is copied unchanged from a searcher's (already grounded) output, and it costs no LLM call. The review
checks measure the plan against the same choice (`recommended_option`).

Each research team's after-agent callback writes its summary (teams.py).
"""
from ..tools.geo import haversine_km
from ..tools.serpapi import is_hostel
from .output import fenced_json
from .review_checks import FAR_STAY_KM, _parse, _price, leg_cost, recommended_option, usable_options

LEGS = ("outbound", "return")
MAX_AREAS = 6


# ---------------------------------------------------------------- transport

def _unique(options: list[dict], key) -> list[dict]:
    found = {}
    for o in options:
        found.setdefault(key(o), o)
    return list(found.values())


def _transport_key(o: dict) -> tuple:
    return o.get("operator"), o.get("depart"), _price(o.get("price_eur"))


def _cheapest(options: list[dict], field: str) -> dict | None:
    priced = [o for o in options if _price(o.get(field)) is not None]
    return min(priced, key=lambda o: _price(o[field])) if priced else None


def rank_transport(options: list[dict], leg: str, brief: dict) -> list[dict]:
    """Best value first (price plus travel time and lost trip time, valued by the budget tier), then the options
    without a price (a ferry or bus whose fare nobody found is often the cheap way there), then the options the
    travellers cannot take (an avoided mode, an airport far from the destination)."""
    usable = {_transport_key(o) for o in usable_options({leg: options}, leg, brief)}

    def order(o):
        cost = leg_cost(o, leg, brief)
        return _transport_key(o) not in usable, cost is None, cost or 0.0

    return sorted(options, key=order)


def build_transport_summary(flights: dict, ground: dict, brief: dict) -> dict:
    """Per direction: the cheapest and the fastest option the travellers can take, and every option ranked; plus the
    recommended option per direction (review_checks.recommended_option, the choice the review checks expect)."""
    summary = {}
    for leg in LEGS:
        options = _unique([o for source in (flights, ground) for o in source.get(leg) or [] if isinstance(o, dict)],
                          _transport_key)
        usable = usable_options({leg: options}, leg, brief)
        summary[leg] = {"cheapest": _cheapest(usable, "price_eur"), "fastest": _cheapest(usable, "duration_min"),
                        "ranked": rank_transport(options, leg, brief)}
    summary["recommended"] = {leg: recommended_option(summary, leg, brief) for leg in LEGS}
    summary["fallback_links"] = _links(flights, ground)
    summary["notes"] = _notes(("Flights", flights), ("Ground transport", ground))
    return summary


# ---------------------------------------------------------------- stay

def rating_of(stay: dict) -> float | None:
    """The stay's rating out of 5 (liteAPI rates out of 10)."""
    rating = _price(stay.get("rating"))
    if rating is None:
        return None
    return rating / 2 if rating > 5 else rating


def best_value(stays: list[dict], centre: tuple[float, float] | None) -> dict | None:
    """The best-rated stay that is not a hostel, not far from the centre and not dearer than the median of those;
    a tie goes to the cheaper one. Each condition is dropped when no stay meets it."""
    def narrow(pool, keep):
        return [s for s in pool if keep(s)] or pool

    pool = [s for s in stays if _price(s.get("total_price_eur")) is not None]
    pool = narrow(pool, lambda s: not is_hostel(s.get("name"), s.get("details"), s.get("kind")))
    pool = narrow(pool, lambda s: _km_from(s, centre) <= FAR_STAY_KM)
    if not pool:
        return None
    median = sorted(_price(s["total_price_eur"]) for s in pool)[(len(pool) - 1) // 2]
    pool = narrow(pool, lambda s: _price(s["total_price_eur"]) <= median)
    return max(pool, key=lambda s: (rating_of(s) or 0, -_price(s["total_price_eur"])))


def _km_from(stay: dict, centre: tuple[float, float] | None) -> float:
    """Distance from the centre; 0 when it is unknown (the stay is not ruled out)."""
    here = (_price(stay.get("lat")), _price(stay.get("lon")))
    if centre is None or None in here:
        return 0.0
    return haversine_km(*here, *centre)


def by_area(stays: list[dict], centre: tuple[float, float] | None) -> list[dict]:
    """The best Airbnb and the best hotel of each area, best-covered areas first (at most MAX_AREAS)."""
    areas: dict[str, list[dict]] = {}
    for s in stays:
        areas.setdefault(str(s.get("area") or "").strip() or "Other", []).append(s)
    rows = []
    for area, here in areas.items():
        nightly = [p for p in (_price(s.get("price_per_night_eur")) for s in here) if p is not None]
        rows.append({"area": area,
                     "airbnb": best_value([s for s in here if s.get("kind") == "airbnb"], centre),
                     "hotel": best_value([s for s in here if s.get("kind") == "hotel"], centre),
                     "min_price_per_night_eur": min(nightly) if nightly else None})
    rows.sort(key=lambda r: (-len(areas[r["area"]]), r["min_price_per_night_eur"] or float("inf")))
    return rows[:MAX_AREAS]


def build_stay_summary(airbnb: dict, hotels: dict, brief: dict) -> dict:
    centre = _centre(brief)
    stays = _unique([s for source in (airbnb, hotels) for s in source.get("results") or [] if isinstance(s, dict)],
                    lambda s: (s.get("name"), _price(s.get("total_price_eur"))))
    rated = [s for s in stays if rating_of(s) is not None]
    return {"by_area": by_area(stays, centre),
            "cheapest": _cheapest(stays, "total_price_eur"),
            "best_value": best_value(stays, centre),
            "top_rated": max(rated, key=rating_of) if rated else None,
            "fallback_links": _links(airbnb, hotels),
            "notes": _notes(("Airbnb", airbnb), ("Hotels", hotels))}


# ---------------------------------------------------------------- shared

def _centre(brief: dict) -> tuple[float, float] | None:
    lat, lon = _price(brief.get("destination_lat")), _price(brief.get("destination_lon"))
    return None if lat is None or lon is None else (lat, lon)


def _links(*sources: dict) -> list[str]:
    return list(dict.fromkeys(u for s in sources for u in s.get("fallback_links") or [] if isinstance(u, str)))


def _notes(*named: tuple[str, dict]) -> str:
    """The searchers' own notes (a failed source, a fallback), so the summary still says why something is missing."""
    return " ".join(f"{name}: {data.get('notes') or 'no readable search result.'}" for name, data in named)


def summarize_transport(callback_context):
    """After-agent callback of the transport team."""
    state = callback_context.state
    summary = build_transport_summary(_parse(state.get("flight_options")), _parse(state.get("ground_options")),
                                      state.get("trip_brief") or {})
    state["transport_summary"] = fenced_json(summary)
    return None


def summarize_stay(callback_context):
    """After-agent callback of the stay team."""
    state = callback_context.state
    state["stay_summary"] = fenced_json(build_stay_summary(_parse(state.get("airbnb_options")),
                                                           _parse(state.get("hotel_options")),
                                                           state.get("trip_brief") or {}))
    return None
