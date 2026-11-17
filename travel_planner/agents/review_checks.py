"""Code checks of the itinerary, used by the review loop (Phase 8).

The budget critic calls `check_itinerary` as a tool; the critic's verdict guard runs the same checks, so a
"must fix" problem can never be approved by the model. Everything here is deterministic:

must_fix  (the plan is wrong):  over budget while a cheaper option exists; a visit on a closed day or outside
          the opening hours; a sight planned twice (a restaurant too, while unused food places exist); a place or ticket price that is not in the research; a transport or stay option
          that is not in the summaries; wrong number of days or dates.
should_fix (the plan is weak):  thin days (few sights, long empty gaps, missing meals), packed days, long
          walks, meals far from the day's area, places used twice, over budget with no cheaper option left.

Thin days and far meals come with "alternatives": unused researched places near that day's area, so the
reviser can fix them without inventing anything.
"""
import json
import re
import unicodedata
from datetime import date, timedelta

from google.adk.tools import ToolContext

from ..tools._result import ok
from ..tools.geo import haversine_km
from ..tools.trip import calculate_trip_cost
from .output import extract_json

FOOD_KINDS = {"restaurant", "cafe", "bar"}
PLACE_KINDS = {"sight"} | FOOD_KINDS
GAP_MIN = 240          # a gap longer than this between planned items (or the day's edges) is a thin spot
DAY_START, DAY_END = 9 * 60, 22 * 60
FAR_MEAL_KM = 2.0
LONG_WALK_KM = 8.0     # straight-line km between consecutive items
ALT_RADIUS_KM = 2.5
MIN_SAVING_EUR = 10.0  # a cheaper transport option must save at least this much to be suggested

# ---------------------------------------------------------------- opening hours

_DASH = re.compile(r"[‐-―−]")
_SPACES = re.compile(r"[   ]")
_DAY_IDX = {"mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}
_MONTH_IDX = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_D = r"(?:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|mo|tu|we|th|fr|sa|su)"
_M = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DAY_LIST = rf"{_D}(?:\s*(?:-|,|&|/|and)\s*{_D})*"
_CLOSED_DAYS = re.compile(rf"(?:closed|off)\s*(?:on\s*)?({_DAY_LIST})\b|\b({_DAY_LIST})\s*(?:closed|off)\b")
_MONTH_RANGE = re.compile(rf"\b({_M})\s*-\s*({_M})\b")
_DATE = re.compile(rf"\b({_M})\s+(\d{{1,2}})\b|\b(\d{{1,2}})\s+({_M})\b")
_TIME_RANGE = re.compile(r"(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})")
_ALL_DAYS = re.compile(r"\b(daily|every\s*day|all\s*days|mo\s*-\s*su|mon\s*-\s*sun)\b")


def _day(token: str) -> int:
    return _DAY_IDX[token.lower()[:2]]


def _days_in(text: str) -> set[int]:
    """Weekday numbers mentioned in text: ranges ("Tu-Su", wrapping) and single days."""
    found = set()
    for m in re.finditer(rf"\b({_D})\s*-\s*({_D})\b", text):
        a, b = _day(m.group(1)), _day(m.group(2))
        found |= {(a + i) % 7 for i in range((b - a) % 7 + 1)}
    text = re.sub(rf"\b{_D}\s*-\s*{_D}\b", " ", text)
    found |= {_day(m) for m in re.findall(rf"\b({_D})\b", text)}
    return found


def _in_months(month: int, a: str, b: str) -> bool:
    start, end = _MONTH_IDX[a[:3]], _MONTH_IDX[b[:3]]
    return start <= month <= end if start <= end else (month >= start or month <= end)


def parse_hours(text, day: date):
    """Opening hours on `day`: "closed", a list of (open_min, close_min), or None when unknown/unparseable.

    Understands the formats the research returns: OSM ("Tu-Su 10:00-18:00; Mo off"), English ("Tue-Sun
    12:30-15:30 & 19:00-22:30; closed Mon", "Daily 19:00-23:00") and month qualifiers ("Oct-May 10:00-17:30").
    Unclear text gives None, so the checks never complain about hours they cannot read.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    s = _SPACES.sub(" ", _DASH.sub("-", text)).lower()
    wd = day.weekday()
    closed_days, ranges, explicit, timed_rules = set(), [], False, 0
    for rule in re.split(r"[;\n]", s):
        months = _MONTH_RANGE.search(rule)
        if months:
            if not _in_months(day.month, months.group(1), months.group(2)):
                continue
            rule = rule[:months.start()] + " " + rule[months.end():]
        dates = _DATE.findall(rule)
        if dates:  # holiday exceptions ("Jan 01 off", "closed 25 Dec"): only the matching date counts
            hit = any((_MONTH_IDX[(m1 or m2)[:3]], int(d1 or d2)) == (day.month, day.day) for m1, d1, d2, m2 in dates)
            if hit and re.search(r"\b(off|closed)\b", rule):
                return "closed"
            continue
        for m in _CLOSED_DAYS.finditer(rule):
            closed_days |= _days_in(m.group(1) or m.group(2))
        rule = _CLOSED_DAYS.sub(" ", rule)
        times = [(int(a) * 60 + int(b), int(c) * 60 + int(d)) for a, b, c, d in _TIME_RANGE.findall(rule)]
        if not times:
            continue
        timed_rules += 1
        times = [(o, c if c > o else c + 24 * 60) for o, c in times]
        days = set(range(7)) if _ALL_DAYS.search(rule) else _days_in(_TIME_RANGE.sub(" ", rule))
        if days:
            explicit = True
        if not days or wd in days:
            ranges += times
    if ranges:
        return "closed" if wd in closed_days and not explicit else sorted(ranges)
    if wd in closed_days or (timed_rules and explicit):
        return "closed"  # "Tu-Su 10:00-18:00" on a Monday
    return None


def _fmt(minutes: int) -> str:
    return f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"


# ---------------------------------------------------------------- helpers

def _key(name) -> str:
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _minutes(item: dict) -> int | None:
    m = re.match(r"^(\d{1,2}):(\d{2})", str(item.get("time") or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _coords(item) -> tuple[float, float] | None:
    try:
        return float(item["lat"]), float(item["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def _parse(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return extract_json(value or "")
    except ValueError:
        return {}


def _dicts(node):
    """Every dict inside parsed JSON."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _dicts(v)


def _price(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def research_places(state) -> list[dict]:
    """The researched sights and food places (culture_findings + food_findings results)."""
    places = []
    for key in ("culture_findings", "food_findings"):
        results = _parse(state.get(key)).get("results")
        places += [p for p in results or [] if isinstance(p, dict) and p.get("name")]
    return places


def find_place(name, places: list[dict]) -> dict | None:
    """The researched place with this name (accents, case and punctuation ignored; containment allowed)."""
    k = _key(name)
    if not k:
        return None
    for p in places:
        if _key(p.get("name")) == k:
            return p
    for p in places:
        pk = _key(p.get("name"))
        if min(len(pk), len(k)) >= 5 and (pk in k or k in pk):
            return p
    return None


def walk_km(items: list[dict]) -> float | None:
    """Straight-line km between consecutive items that have coordinates (None if fewer than two)."""
    pts = [c for c in (_coords(i) for i in items) if c]
    if len(pts) < 2:
        return None
    return round(sum(haversine_km(*a, *b) for a, b in zip(pts, pts[1:])), 1)


# ---------------------------------------------------------------- cost

def cost_inputs(plan: dict, brief: dict) -> dict:
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    legs = [transport.get("outbound"), transport.get("return")]
    stay = plan.get("stay") if isinstance(plan.get("stay"), dict) else {}
    travellers = int(brief.get("travellers") or 1)
    tickets = [_price(i.get("ticket_eur")) for d in plan.get("days") or [] if isinstance(d, dict)
               for i in d.get("items") or [] if isinstance(i, dict) and i.get("kind") == "sight"]
    return {
        "transport_total_eur": sum(_price(leg.get("price_eur")) or 0 for leg in legs if isinstance(leg, dict)),
        "stay_total_eur": _price(stay.get("total_price_eur")) or 0.0,
        "activities_total_eur": sum(t for t in tickets if t) * travellers,
        "days": int(brief.get("days") or len(plan.get("days") or []) or 1),
        "travellers": travellers,
        "budget_tier": brief.get("budget_tier") or "mid",
        "total_budget_eur": brief.get("total_budget_eur"),
        "_unknown": {"transport legs without price": sum(1 for leg in legs if isinstance(leg, dict)
                                                         and _price(leg.get("price_eur")) is None),
                     "sights with unknown ticket": sum(1 for t in tickets if t is None)},
    }


def plan_cost(plan: dict, brief: dict) -> dict | None:
    """calculate_trip_cost on the plan's own choices, so the cost always matches the plan."""
    inputs = cost_inputs(plan, brief)
    unknown = inputs.pop("_unknown")
    result = calculate_trip_cost(**inputs)
    if result.get("status") != "ok":
        return None
    cost = result["results"][0]
    if unknown["transport legs without price"]:
        cost["assumptions"].append(f"{unknown['transport legs without price']} transport leg(s) without a price, counted as €0")
    if unknown["sights with unknown ticket"]:
        cost["assumptions"].append(f"{unknown['sights with unknown ticket']} sight(s) with an unknown ticket price, counted as €0")
    return cost


def _budget_issue(plan, brief, state, cost) -> tuple[str, dict] | None:
    budget = _price(brief.get("total_budget_eur"))
    if not cost or not budget or cost["total_eur"] <= budget:
        return None
    over = round(cost["total_eur"] - budget, 2)
    options = []
    stay = plan.get("stay") if isinstance(plan.get("stay"), dict) else {}
    cheapest_stay = _parse(state.get("stay_summary")).get("cheapest")
    if isinstance(cheapest_stay, dict) and (_price(cheapest_stay.get("total_price_eur")) or 0) < (_price(stay.get("total_price_eur")) or 0):
        saves = _price(stay.get("total_price_eur")) - _price(cheapest_stay.get("total_price_eur"))
        options.append(f"stay at the cheapest option '{cheapest_stay.get('name')}' (€{cheapest_stay['total_price_eur']:g} total, saves €{saves:g})")
    summary = _parse(state.get("transport_summary"))
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    for leg, when in (("outbound", "arrive"), ("return", "depart")):
        chosen = transport.get(leg) if isinstance(transport.get(leg), dict) else {}
        cheapest = (summary.get(leg) or {}).get("cheapest") if isinstance(summary.get(leg), dict) else None
        # only a real saving that keeps the travel day (a night flight arriving a day later loses a trip day)
        if isinstance(cheapest, dict) and _price(cheapest.get("price_eur")) is not None \
                and _price(cheapest["price_eur"]) <= (_price(chosen.get("price_eur")) or 0) - MIN_SAVING_EUR \
                and str(cheapest.get(when, ""))[:10] == str(chosen.get(when, ""))[:10]:
            options.append(f"{leg}: the cheapest option ({cheapest.get('operator')} {cheapest.get('depart')}, "
                           f"€{cheapest['price_eur']:g}, saves €{_price(chosen.get('price_eur')) - _price(cheapest['price_eur']):g})")
    if cost["categories_eur"].get("activities"):
        options.append(f"remove paid sights from the plan (tickets total €{cost['categories_eur']['activities']:g}; "
                       "ticket prices themselves never change)")
    detail = (f"Total €{cost['total_eur']:g} is €{over:g} over the €{budget:g} budget. Food and local transport "
              "are fixed typical costs, not options.")
    if options:
        return "must_fix", {"day": None, "issue": "over_budget", "detail": detail + " Options: " + "; ".join(options) + "."}
    return "should_fix", {"day": None, "issue": "over_budget",
                          "detail": detail + " The plan already uses the cheapest options: say so in notes."}


# ---------------------------------------------------------------- the checks

def _alternatives(centre, used: set[str], places: list[dict], kinds: set[str], n: int = 3) -> list[dict]:
    out = []
    for p in places:
        c = _coords(p)
        if p.get("kind") in kinds and c and _key(p.get("name")) not in used:
            km = haversine_km(*centre, *c)
            if km <= ALT_RADIUS_KM:
                out.append({"name": p["name"], "kind": p.get("kind"), "km": round(km, 1), "hours": p.get("hours"),
                            "ticket_eur": p.get("ticket_eur"), "budget_tier": p.get("budget_tier")})
    return sorted(out, key=lambda a: a["km"])[:n]


def _option_known(option: dict, summary: dict, price_key: str, match_keys: tuple[str, ...]) -> bool:
    for d in _dicts(summary):
        if all(str(d.get(k)) == str(option.get(k)) for k in match_keys) and _price(d.get(price_key)) == _price(option.get(price_key)):
            return True
    return False


def check_plan(plan: dict, state) -> dict:
    """Runs every check on a parsed plan. Returns {"must_fix", "should_fix", "cost", "facts"}."""
    brief = state.get("trip_brief") or {}
    must, should = [], []
    days = [d for d in plan.get("days") or [] if isinstance(d, dict)]
    if not days:
        return {"must_fix": [{"day": None, "issue": "no_plan", "detail": "The plan has no days (or is not valid JSON)."}],
                "should_fix": [], "cost": None, "facts": {}}
    places = research_places(state)

    # days and dates
    expected = int(brief.get("days") or 0)
    if expected and len(days) != expected:
        must.append({"day": None, "issue": "day_count", "detail": f"The plan has {len(days)} days; the trip has {expected}."})
    if brief.get("start_date"):
        start = date.fromisoformat(brief["start_date"])
        for n, d in enumerate(days):
            want = (start + timedelta(days=n)).isoformat()
            if d.get("date") != want:
                must.append({"day": d.get("day"), "issue": "wrong_date", "detail": f"Day {n + 1} should be {want}, not {d.get('date')}."})

    # travel days: arriving after day 1 (or leaving before the last day) loses a paid trip day
    transport_ = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    tsum_ = _parse(state.get("transport_summary"))
    for leg, when, want, later in (("outbound", "arrive", brief.get("start_date"), True),
                                   ("return", "depart", brief.get("end_date"), False)):
        option = transport_.get(leg)
        day_ = str((option or {}).get(when, ""))[:10] if isinstance(option, dict) else ""
        if not (want and day_) or day_ == want or (day_ > want) != later:
            continue
        same_day = [d for d in _dicts(tsum_.get(leg)) if str(d.get(when, ""))[:10] == want and d.get("operator")]
        if same_day:
            alt = same_day[0]
            must.append({"day": None, "issue": "travel_day_lost",
                         "detail": f"The {leg} option {'arrives' if later else 'leaves'} on {day_}, so the trip loses a "
                                   f"day; the transport summary has options on {want} (e.g. {alt.get('operator')} "
                                   f"{alt.get(when)}, €{alt.get('price_eur')}). Use one of those."})

    # transport and stay must be options from the summaries
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    tsum = _parse(state.get("transport_summary"))
    for leg in ("outbound", "return"):
        option = transport.get(leg)
        if isinstance(option, dict) and tsum and not _option_known(option, tsum, "price_eur", ("operator", "depart")):
            must.append({"day": None, "issue": "ungrounded_transport",
                         "detail": f"The {leg} option ({option.get('operator')} {option.get('depart')}, €{option.get('price_eur')}) "
                                   "is not in the transport summary: copy an option from there unchanged."})
    stay = plan.get("stay")
    ssum = _parse(state.get("stay_summary"))
    if isinstance(stay, dict) and ssum and not _option_known(stay, ssum, "total_price_eur", ("name",)):
        must.append({"day": None, "issue": "ungrounded_stay",
                     "detail": f"The stay '{stay.get('name')}' (€{stay.get('total_price_eur')}) is not in the stay summary: "
                               "copy a stay from there unchanged."})

    # items
    used, seen = {_key(i.get("name")) for d in days for i in d.get("items") or [] if isinstance(i, dict)}, {}
    for n, d in enumerate(days):
        label = d.get("day") or n + 1
        items = [i for i in d.get("items") or [] if isinstance(i, dict)]
        try:
            today = date.fromisoformat(str(d.get("date")))
        except ValueError:
            today = None
        sights = [i for i in items if i.get("kind") == "sight"]
        for item in items:
            kind, name, t = item.get("kind"), item.get("name"), _minutes(item)
            if kind not in PLACE_KINDS:
                continue
            found = find_place(name, places)
            if found is None:
                must.append({"day": label, "issue": "ungrounded_place",
                             "detail": f"'{name}' is not in the research results: use a researched place (same name) or drop it."})
            elif kind == "sight" and _price(item.get("ticket_eur")) != _price(found.get("ticket_eur")):
                must.append({"day": label, "issue": "ungrounded_price",
                             "detail": f"'{name}' has ticket_eur {item.get('ticket_eur')} but the research says "
                                       f"{found.get('ticket_eur')} (null = unknown): copy the research value."})
            k = _key(name)
            if k in seen:  # a sight twice is an error; a restaurant twice too, unless no other food place is left
                spare = [p["name"] for p in places if p.get("kind") in FOOD_KINDS and _key(p.get("name")) not in used]
                level = must if kind == "sight" or spare else should
                level.append({"day": label, "issue": "repeated_place",
                              "detail": f"'{name}' is also on day {seen[k]}: use each place once."
                                        + (f" Unused food places: {', '.join(spare[:4])}." if kind in FOOD_KINDS and spare else "")})
            seen.setdefault(k, label)
            hours = (found or {}).get("hours") or item.get("hours")
            open_ = parse_hours(hours, today) if today else None
            if open_ == "closed":
                must.append({"day": label, "issue": "closed",
                             "detail": f"'{name}' is closed on {today:%A}s ({hours}): move it to an open day or drop it."})
            elif open_ and t is not None and not any(o <= t <= c - 30 for o, c in open_):
                spans = ", ".join(f"{_fmt(o)}-{_fmt(c)}" for o, c in open_)
                must.append({"day": label, "issue": "outside_hours",
                             "detail": f"'{name}' at {item.get('time')}, but it is open {spans} on {today:%A}s: "
                                       "move it inside the opening hours (at least 30 min before closing)."})

        # pacing: the day's usable window runs from arrival (+1.5 h) to departure (-2.5 h flight / -1 h other)
        times = sorted(t for t in (_minutes(i) for i in items if i.get("kind") != "transport") if t is not None)
        start, end = DAY_START, DAY_END
        for i in items:
            name, t = str(i.get("name") or "").lower(), _minutes(i)
            if i.get("kind") != "transport" or t is None:
                continue
            if name.startswith("arrive"):
                start = max(start, t + 90)
            elif name.startswith("depart") and n == len(days) - 1:
                end = min(end, t - (150 if "flight" in str(i.get("note", "")).lower() else 60))
        full_day = 0 < n < len(days) - 1
        if full_day and len(sights) < 2:
            should.append({"day": label, "issue": "thin_day", "detail": f"Only {len(sights)} sight(s) on a full day."})
        if end - start >= 120:
            edges = [start] + [t for t in times if start <= t <= end] + [end]
            for a, b in zip(edges, edges[1:]):
                if b - a > GAP_MIN:
                    should.append({"day": label, "issue": "empty_gap",
                                   "detail": f"Nothing planned {_fmt(a)}-{_fmt(b)}."})
            meals = [(i, _minutes(i)) for i in items
                     if i.get("kind") in FOOD_KINDS or (i.get("kind") == "free" and re.search(r"lunch|dinner|eat", str(i.get("name")), re.I))]
            for meal, lo, hi in (("lunch", 11 * 60 + 30, 15 * 60 + 30), ("dinner", 18 * 60, 22 * 60 + 30)):
                if start <= lo + 60 and end >= lo + 120 and not any(t is not None and lo <= t <= hi for _, t in meals):
                    should.append({"day": label, "issue": "missing_meal", "detail": f"No {meal} planned."})
        if len(sights) > 5:
            should.append({"day": label, "issue": "packed_day", "detail": f"{len(sights)} sights in one day."})
        timed = sorted((t, i) for i in items if i.get("kind") == "sight" for t in [_minutes(i)] if t is not None)
        for (t1, a), (t2, b) in zip(timed, timed[1:]):
            if t2 - t1 < 45:
                should.append({"day": label, "issue": "packed_day",
                               "detail": f"Only {t2 - t1} min between '{a.get('name')}' and '{b.get('name')}'."})
        km = walk_km(items)
        if km and km > LONG_WALK_KM:
            should.append({"day": label, "issue": "long_walk",
                           "detail": f"{km} km straight-line between the day's stops: regroup or add a metro/tram note."})

        # far meals and alternatives near the day's area
        pts = [c for c in (_coords(i) for i in sights) if c]
        centre = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else _coords(stay or {})
        if not centre:
            continue
        for item in items:
            c = _coords(item)
            if item.get("kind") in FOOD_KINDS and c and pts and haversine_km(*centre, *c) > FAR_MEAL_KM:
                should.append({"day": label, "issue": "far_meal",
                               "detail": f"'{item.get('name')}' is {haversine_km(*centre, *c):.1f} km from the day's sights.",
                               "alternatives": _alternatives(centre, used, places, FOOD_KINDS)})
        thin = [x for x in should if x["day"] == label and x["issue"] in ("thin_day", "empty_gap", "missing_meal")]
        for x in thin:
            kinds = FOOD_KINDS if x["issue"] == "missing_meal" else {"sight"}
            x["alternatives"] = _alternatives(centre, used, places, kinds)

    cost = plan_cost(plan, brief) if brief.get("travellers") else None
    budget = _budget_issue(plan, brief, state, cost)
    if budget:
        (must if budget[0] == "must_fix" else should).append(budget[1])
    facts = {"days": len(days), "sights": sum(1 for d in days for i in d.get("items") or [] if isinstance(i, dict) and i.get("kind") == "sight"),
             "total_eur": (cost or {}).get("total_eur"), "budget_eur": brief.get("total_budget_eur")}
    return {"must_fix": must, "should_fix": should, "cost": cost, "facts": facts}


def check_state(state) -> dict:
    """check_plan on state["itinerary"]; also stores the result in state["itinerary_check"] for the reviser."""
    report = check_plan(_parse(state.get("itinerary")), state)
    state["itinerary_check"] = json.dumps({k: report[k] for k in ("must_fix", "should_fix", "facts")}, ensure_ascii=False)
    return report


def check_itinerary(tool_context: ToolContext) -> dict:
    """Checks the current itinerary in code and lists its problems.

    Checks the budget (cost recomputed from the plan), opening hours against visit times, places and prices
    missing from the research, transport and stay against the summaries, the day count, thin or packed days,
    long walks, meals far from the day's sights and repeated places. Problems are split into "must_fix" and
    "should_fix"; thin days and far meals list unused researched alternatives nearby.
    """
    return ok([check_state(tool_context.state)])
