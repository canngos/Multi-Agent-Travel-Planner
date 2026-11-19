"""Code checks of the itinerary, used by the review loop (Phase 8).

The plan critic calls `check_itinerary` as a tool; the critic's verdict guard runs the same checks, so a
"must fix" problem can never be approved by the model. Everything here is deterministic:

must_fix  (the plan is wrong):  over budget while a cheaper option exists; a visit on a closed day or outside
          the opening hours; a sight planned twice (a restaurant too, while unused food places exist); a place or ticket price that is not in the research; a transport or stay option
          that is not in the summaries; wrong number of days or dates.
          Also an item outside the day hours the travellers stated (a requirement they set, like "no flights").
decide    (taste, the plan critic decides each one: fix it or decline it with a reason): an empty morning on a
          full day, two meals close together, a "Lunch near ..." placeholder while a researched food place is
          nearby, a whole empty afternoon with researched sights nearby. Obvious cases stay must_fix ("obvious":
          why): a late start although the day's own sights open in the morning, two meals under 2 hours apart.
should_fix (the plan is weak):  thin days (few sights, long empty gaps, missing meals), packed days, long
          walks, meals far from the day's area, places used twice, over budget with no cheaper option left.

Thin days and far meals come with "alternatives": unused researched places near that day's area, so the
reviser can fix them without inventing anything.
"""
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from google.adk.tools import ToolContext

from .. import config
from ..models import minutes_of
from ..tools._result import ok
from ..tools.geo import haversine_km
from ..tools.trip import calculate_trip_cost
from .output import extract_json

FOOD_KINDS = {"restaurant", "cafe", "bar"}
PLACE_KINDS = {"sight"} | FOOD_KINDS
# the day's edges for the gap check when the brief sets no day_start/day_end: the defaults the prompts state
DAY_START, DAY_END = minutes_of(config.DEFAULT_DAY_START), minutes_of(config.DEFAULT_DAY_END)
FAR_MEAL_KM = 2.0
LONG_WALK_KM = 8.0     # straight-line km between consecutive items
ALT_RADIUS_KM = 2.5
MIN_SAVING_EUR = 10.0  # a cheaper transport option must save at least this much to be suggested
AFTERNOON = (12 * 60, 18 * 60)
MORNING_OPEN_AFTER_START = 60  # a sight open within this many minutes of day_start can start the day


@dataclass(frozen=True)
class PaceLimits:
    min_sights: int        # fewer sights on a full day is a thin day
    max_sights: int        # more is a packed day
    gap_min: int           # a longer gap between planned items (or the day's edges) is a thin spot
    min_between: int       # sights closer together than this (minutes) are rushed
    empty_afternoon: int   # this much idle time inside the afternoon is a lost afternoon
    morning_grace: int     # a full day whose first item is later than day_start + this has lost its morning


# "balanced" holds the values these checks always used; the brief's pace picks another row (TripBrief.pace).
# The sight counts are deliberately more tolerant than the prompts (planner/concierge ask for relaxed 1-3,
# balanced 2-4, packed 4-6; the check accepts 1-3, 2-5 and 3-7): the prompts aim at the target, the check flags
# only a clear miss, so a plan one sight off is not sent back for another review round.
PACES = {"relaxed": PaceLimits(1, 3, 360, 60, 240, 150),
         "balanced": PaceLimits(2, 5, 240, 45, 150, 90),
         "packed": PaceLimits(3, 7, 150, 30, 90, 60)}
MEAL_GAP_MIN = 180  # two restaurants (or meal placeholders) closer than this are one meal too many
# Day-shape findings that are a matter of taste (REVIEW.md A4): the code check reports them under "decide" and the
# plan critic decides each one (fix it, or decline it with a reason; see plan_critic.enforce_verdict). An obvious
# case stays must_fix with its reason in "obvious": a full day starting late although that day's own planned sights
# open in the morning (only the order changes), or two meals less than OBVIOUS_MEAL_GAP_MIN apart.
TASTE_ISSUES = ("empty_morning", "double_meal", "placeholder_meal", "empty_afternoon")
OBVIOUS_MEAL_GAP_MIN = 120
MEAL_NAME = re.compile(r"lunch|dinner|eat", re.IGNORECASE)
VISIT_MIN = {"sight": 90, "restaurant": 60, "cafe": 60, "bar": 60, "free": 60}  # typical length of a planned item
FAR_STAY_KM = 4.0          # stay farther than this from the planned sights ...
NEAR_STAY_KM = 2.0         # ... while the summary has one this close
FAR_STAY_EXTRA = (30.0, 0.25)  # ... costing at most €30 or 25 % more (whichever is larger)

# ---------------------------------------------------------------- opening hours

_DASH = re.compile(r"[‐-―−]")
_SPACES = re.compile(r"[   ]")
_DAY_IDX = {"mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}
_MONTH_IDX = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_D = (r"(?:mon(?:days?)?|tue(?:s(?:days?)?)?|wed(?:nesdays?)?|thu(?:r(?:s(?:days?)?)?)?|fri(?:days?)?"
      r"|sat(?:urdays?)?|sun(?:days?)?|mo|tu|we|th|fr|sa|su)")
_M = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_RANGE_WORDS = r"(?:to|through|thru|till|until)"
_DAY_RANGE = re.compile(rf"\b({_D})(?:\s*-\s*|\s+{_RANGE_WORDS}\s+)({_D})\b")  # "Tu-Su", "Tuesday to Sunday"
_DAY_LIST = rf"{_D}(?:(?:\s*(?:-|,|&|/)\s*|\s+(?:and|{_RANGE_WORDS})\s+){_D})*"
_CLOSED_DAYS = re.compile(rf"(?:closed|off|except)\s*(?:on\s*)?({_DAY_LIST})\b|\b({_DAY_LIST})\s*(?:closed|off)\b")
_MONTH_RANGE = re.compile(rf"\b({_M})\s*-\s*({_M})\b")
_DATE = re.compile(rf"\b({_M})\s+(\d{{1,2}})\b|\b(\d{{1,2}})\s+({_M})\b")
_TIME_RANGE = re.compile(r"(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})")
_ALL_DAYS = re.compile(r"\b(daily|every\s*day|all\s*days)\b")  # names no day ("Mo-Su" names them all: a range)
# a two-letter day followed by an ordinary word is English text ("we recommend"), not a day ("We off", "Mo Tu")
_NOT_A_DAY = re.compile(rf"\b(?:mo|tu|we|th|fr|sa|su)\b(?=\s+[a-z])(?!\s+(?:off|closed|and|{_RANGE_WORDS}|{_D})\b)")
# the whole place is closed ("closed", "Temporarily closed for renovations"), not just some days or hours
_CLOSED_PLACE = re.compile(rf"^\W*(?:temporarily|permanently|currently)?\s*closed\b(?!\s*(?:on\s+)?(?:{_DAY_LIST}\b|"
                           rf"{_M}\b|\d|public|holidays?|ph\b|for\s+(?:lunch|the\s+day)|in\b|during\b))")
_UNTIL = re.compile(rf"(?:reopen\w*|until)\b\D{{0,30}}?(?:\b({_M})\w*\.?\s+)?(\d{{4}})\b")
_TWELVE_HOUR = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?m\b\.?")  # "9:30 AM", "6pm", "11.30 p.m."
_NOON = re.compile(r"\b(?:12\s*)?noon\b")


def _to_24h(m: re.Match) -> str:
    hour = int(m.group(1)) % 12 + (12 if m.group(3) == "p" else 0)
    return f"{hour:02d}:{m.group(2) or '00'}"
_BRACKET = re.compile(r"\(([^()]*)\)")


def _day(token: str) -> int:
    return _DAY_IDX[token.lower()[:2]]


def _days_in(text: str) -> set[int]:
    """Weekday numbers mentioned in text: ranges ("Tu-Su", "Tuesday to Sunday", wrapping) and single days."""
    found = set()
    for m in _DAY_RANGE.finditer(text):
        a, b = _day(m.group(1)), _day(m.group(2))
        found |= {(a + i) % 7 for i in range((b - a) % 7 + 1)}
    text = _DAY_RANGE.sub(" ", text)
    found |= {_day(m) for m in re.findall(rf"\b({_D})\b", text)}
    return found


def _in_months(month: int, a: str, b: str) -> bool:
    start, end = _MONTH_IDX[a[:3]], _MONTH_IDX[b[:3]]
    return start <= month <= end if start <= end else (month >= start or month <= end)


def _closed_place(s: str, day: date) -> bool:
    """True when the text says the place itself is closed (and gives no reopening date before `day`)."""
    if _TIME_RANGE.search(s) or not _CLOSED_PLACE.match(s):
        return False
    until = _UNTIL.search(s)
    if until:  # "planned reopening Oct 2021", "closed until June 2027"
        year = int(until.group(2))
        if until.group(1):
            return (year, _MONTH_IDX[until.group(1)[:3]]) >= (day.year, day.month)
        return year >= day.year
    return True


def _rules(s: str, month: int) -> list[str]:
    """The rules of an hours text: split on ";" and newlines, and before each month range when a rule names several
    seasons ("Mar-Oct: daily 10:00-21:00, Nov-Feb daily 09:00-18:00"). A month range in brackets qualifies only
    the bracket: "(Jun-Sep: Fri Sat until 22:00)" is dropped outside June-September."""
    def bracket(m: re.Match) -> str:
        inner = m.group(1)
        months = _MONTH_RANGE.search(inner)
        if not months:
            return m.group(0)
        if not _in_months(month, months.group(1), months.group(2)):
            return " "
        return " " + inner[:months.start()] + inner[months.end():] + " "

    out = []
    for rule in re.split(r"[;\n]", s):
        rule = _BRACKET.sub(bracket, rule)
        starts = [m.start() for m in _MONTH_RANGE.finditer(rule)]
        if len(starts) > 1:
            out += [rule[:starts[0]]] + [rule[a:b] for a, b in zip(starts, starts[1:] + [len(rule)])]
        else:
            out.append(rule)
    return [r for r in out if r.strip()]


def parse_hours(text, day: date):
    """Opening hours on `day`: "closed", a list of (open_min, close_min), or None when unknown/unparseable.

    Understands the formats the research returns: OSM ("Tu-Su 10:00-18:00; Mo off"), English ("Tuesday to Sunday
    12:30-15:30 & 19:00-22:30; closed Mon", "Daily except Monday 19:00-23:00"), month qualifiers ("Oct-May
    10:00-17:30") and closed places ("Temporarily closed for renovations"). As in OSM, a later rule overrides an
    earlier one; "daily" names no day, so a closed day always wins over it. Unclear text gives None, so the
    checks never complain about hours they cannot read.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    s = _SPACES.sub(" ", _DASH.sub("-", text)).lower()
    s = _TWELVE_HOUR.sub(_to_24h, _NOON.sub("12:00", s))  # "9:30 AM-6:00 PM" -> "09:30-18:00"
    s = _NOT_A_DAY.sub(" ", s)
    if _closed_place(s, day):
        return "closed"
    wd = day.weekday()
    closed_at, open_at = {}, {}  # weekday -> index of the last rule that closes it / names it with times
    ranges, explicit, timed_rules = [], False, 0
    for idx, rule in enumerate(_rules(s, day.month)):
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
            for d in _days_in(m.group(1) or m.group(2)):
                closed_at[d] = idx
        rule = _CLOSED_DAYS.sub(" ", rule)
        times = [(int(a) * 60 + int(b), int(c) * 60 + int(d)) for a, b, c, d in _TIME_RANGE.findall(rule)]
        if not times:
            continue
        timed_rules += 1
        times = [(o, c if c > o else c + 24 * 60) for o, c in times]
        if _ALL_DAYS.search(rule):
            days = set(range(7))
        else:
            days = _days_in(_TIME_RANGE.sub(" ", rule))
            explicit = explicit or bool(days)
            open_at.update({d: idx for d in days})
        if not days or wd in days:
            ranges += times
    if wd in closed_at and closed_at[wd] > open_at.get(wd, -1):
        return "closed"  # "Tu-Su 10:00-18:00; Mo off", "Daily except Monday ..." on a Monday
    if ranges:
        return sorted(ranges)
    if timed_rules and explicit:
        return "closed"  # "Tu-Su 10:00-18:00" on a Monday
    return None


def _fmt(minutes: int) -> str:
    return f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"


# ---------------------------------------------------------------- helpers

def _key(name) -> str:
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_CLOCK = re.compile(r"^\s*(\d{1,2})(?:[:.h](\d{2}))?(?:\s*([ap])\.?\s*m\b\.?)?", re.IGNORECASE)  # "10:00 park" is AM
LATE_NIGHT_END = 5 * 60  # a visit before 05:00 belongs to the evening before (a bar after dinner)


def clock_minutes(text) -> int | None:
    """Minutes after midnight of a time like "13:00", "9:30", "1:00 PM", "1 pm", "13h00" or "13.00"; None if none."""
    m = _CLOCK.match(str(text or ""))
    if not m or not (m.group(2) or m.group(3)):  # a bare number ("2") is not a time
        return None
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if half:
        if hour > 12:
            return None
        hour = hour % 12 + (12 if half == "p" else 0)
    return hour * 60 + minute if hour <= 24 and minute < 60 else None


def _minutes(item: dict) -> int | None:
    """The item's time in minutes; after midnight (before 05:00) counts as the same evening, except travel."""
    t = clock_minutes(item.get("time"))
    if t is not None and t < LATE_NIGHT_END and item.get("kind") != "transport":
        t += 24 * 60
    return t


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


RESEARCH_KINDS = {"culture_findings": {"sight"}, "food_findings": FOOD_KINDS}


def unreadable_research(state) -> list[str]:
    """Research keys that hold text but no JSON object (e.g. an answer cut off mid-way): their places cannot be
    checked, so the checks must not report every planned place as invented."""
    return [key for key in RESEARCH_KINDS
            if isinstance(state.get(key), str) and state[key].strip() and not _parse(state[key])]


# Words that name a kind of place, not a place: "Miradouro" or "Museu" alone must not match "Miradouro da Graça"
_GENERIC = set("""museum museu museo musee church igreja iglesia chiesa eglise cathedral se castle castelo castillo
castello chateau palace palacio palazzo tower torre viewpoint miradouro mirador park parque parco garden gardens
jardim jardin square praca plaza piazza market mercado mercato monastery mosteiro convent convento bridge ponte
puente gallery galeria restaurant restaurante ristorante cafe bar tasca taberna national nacional old town city
centre center the of de do da dos das del della di la le el o a""".split())


def _specific(key: str) -> bool:
    return any(word not in _GENERIC for word in key.split())


def find_place(name, places: list[dict]) -> dict | None:
    """The researched place with this name (accents, case and punctuation ignored).

    One name may contain the other ("Faca & Garfo restaurant"), as whole words, when the shorter one names a
    specific place: a generic research entry like "Miradouro" matches only itself."""
    k = _key(name)
    if not k:
        return None
    for p in places:
        if _key(p.get("name")) == k:
            return p
    for p in places:
        pk = _key(p.get("name"))
        shorter = pk if len(pk) <= len(k) else k
        if len(shorter) >= 5 and _specific(shorter) and (f" {pk} " in f" {k} " or f" {k} " in f" {pk} "):
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
    options, savings = [], 0.0
    stay = plan.get("stay") if isinstance(plan.get("stay"), dict) else {}
    cheapest_stay = _parse(state.get("stay_summary")).get("cheapest")
    if isinstance(cheapest_stay, dict) and (_price(cheapest_stay.get("total_price_eur")) or 0) < (_price(stay.get("total_price_eur")) or 0):
        saves = _price(stay.get("total_price_eur")) - _price(cheapest_stay.get("total_price_eur"))
        savings += saves
        options.append(f"stay at the cheapest option '{cheapest_stay.get('name')}' (€{cheapest_stay['total_price_eur']:g} total, saves €{saves:g})")
    summary = _parse(state.get("transport_summary"))
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    avoid = set(brief.get("avoid_modes") or [])
    for leg, when in (("outbound", "arrive"), ("return", "depart")):
        chosen = transport.get(leg) if isinstance(transport.get(leg), dict) else {}
        cheapest = (summary.get(leg) or {}).get("cheapest") if isinstance(summary.get(leg), dict) else None
        # only a real saving that keeps the travel day (a night flight arriving a day later loses a trip day)
        # and uses a mode the travellers accept
        if isinstance(cheapest, dict) and cheapest.get("mode") not in avoid and _price(cheapest.get("price_eur")) is not None \
                and _price(cheapest["price_eur"]) <= (_price(chosen.get("price_eur")) or 0) - MIN_SAVING_EUR \
                and str(cheapest.get(when, ""))[:10] == str(chosen.get(when, ""))[:10]:
            saves = _price(chosen.get("price_eur")) - _price(cheapest["price_eur"])
            savings += saves
            options.append(f"{leg}: the cheapest option ({cheapest.get('operator')} {cheapest.get('depart')}, "
                           f"€{cheapest['price_eur']:g}, saves €{saves:g})")
    if cost["categories_eur"].get("activities"):
        savings += cost["categories_eur"]["activities"]
        options.append(f"remove paid sights from the plan (tickets total €{cost['categories_eur']['activities']:g}; "
                       "ticket prices themselves never change)")
    detail = (f"Total €{cost['total_eur']:g} is €{over:g} over the €{budget:g} budget. Food and local transport "
              "are fixed typical costs, not options.")
    if options and savings >= over:
        return "must_fix", {"day": None, "issue": "over_budget", "detail": detail + " Options: " + "; ".join(options) + "."}
    if options:  # stripping every paid sight would not reach the budget either: not a fault of the plan
        return "should_fix", {"day": None, "issue": "over_budget",
                              "detail": detail + f" The budget cannot be met: all cheaper options together save at "
                                                 f"most €{savings:g}. Keep the plan and say so in notes."}
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


def _far_stay_issue(plan: dict, days: list[dict], stay_summary: dict) -> dict | None:
    """The chosen stay is far from the planned sights while the stay summary has a central one for little more.

    A live Tallinn run picked the cheapest hostel, 9 km out, over a central one costing €6 more: every day then
    starts and ends with a long commute that no other check sees."""
    stay = plan.get("stay") if isinstance(plan.get("stay"), dict) else None
    here = _coords(stay or {})
    pts = [c for d in days for i in d.get("items") or [] if isinstance(i, dict) and i.get("kind") == "sight"
           for c in [_coords(i)] if c]
    if not (here and pts):
        return None
    centre = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    km = haversine_km(*here, *centre)
    if km <= FAR_STAY_KM:
        return None
    price = _price(stay.get("total_price_eur")) or 0.0
    limit = price + max(FAR_STAY_EXTRA[0], FAR_STAY_EXTRA[1] * price)
    options = {}
    for d in _dicts(stay_summary):
        c, total = _coords(d), _price(d.get("total_price_eur"))
        if d.get("kind") in ("hotel", "airbnb") and c and total is not None and total <= limit \
                and d.get("name") != stay.get("name") and haversine_km(*c, *centre) <= NEAR_STAY_KM:
            options[d.get("name")] = {"name": d.get("name"), "kind": d.get("kind"), "area": d.get("area"),
                                      "km": round(haversine_km(*c, *centre), 1), "total_price_eur": total,
                                      "rating": d.get("rating")}
    if not options:
        return None
    return {"day": None, "issue": "far_stay",
            "detail": f"The stay '{stay.get('name')}' is {km:.1f} km from the planned sights: switch to a central "
                      "stay from the stay summary (listed below, copied unchanged).",
            "alternatives": sorted(options.values(), key=lambda o: o["total_price_eur"])[:3]}


def _option_known(option: dict, summary: dict, price_key: str, match_keys: tuple[str, ...]) -> bool:
    for d in _dicts(summary):
        if all(str(d.get(k)) == str(option.get(k)) for k in match_keys) and _price(d.get(price_key)) == _price(option.get(price_key)):
            return True
    return False


def _opens_by(hours, day: date | None, limit: int) -> bool:
    """True if the hours say the place is open at `limit` (minutes) or opens by then, on that day."""
    spans = parse_hours(hours, day) if hours and day else None
    return isinstance(spans, list) and any(o <= limit < c for o, c in spans)


@dataclass(frozen=True)
class DayRhythm:
    """The travellers' day from the brief: edges for the gap check, the morning start, pace limits, and the hours
    the user set (None when they set none: then nothing is "too early" or "too late")."""
    start: int
    end: int
    morning: int
    pace: PaceLimits
    pace_name: str
    user_start: int | None
    user_end: int | None


def day_rhythm(brief: dict) -> DayRhythm:
    """With no day_start, day_end or pace in the brief, every value is what the checks always used."""
    def clock(key):
        try:
            return minutes_of(brief[key]) if brief.get(key) else None
        except (TypeError, ValueError):
            return None
    user_start, user_end = clock("day_start"), clock("day_end")
    name = brief.get("pace") if brief.get("pace") in PACES else config.DEFAULT_PACE
    return DayRhythm(start=DAY_START if user_start is None else user_start,
                     end=DAY_END if user_end is None else user_end,
                     morning=minutes_of(config.DEFAULT_DAY_START) if user_start is None else user_start,
                     pace=PACES[name], pace_name=name, user_start=user_start, user_end=user_end)


def check_plan(plan: dict, state) -> dict:
    """Runs every check on a parsed plan. Returns {"must_fix", "should_fix", "cost", "facts"}."""
    brief = state.get("trip_brief") or {}
    must, should = [], []
    rhythm = day_rhythm(brief)
    pace = rhythm.pace
    days = [d for d in plan.get("days") or [] if isinstance(d, dict)]
    if not days:
        return {"must_fix": [{"day": None, "issue": "no_plan", "detail": "The plan has no days (or is not valid JSON)."}],
                "should_fix": [], "decide": [], "cost": None, "facts": {}}
    places = research_places(state)
    unreadable = unreadable_research(state)
    unchecked_kinds = set().union(*(RESEARCH_KINDS[key] for key in unreadable))
    for key in unreadable:
        should.append({"day": None, "issue": "research_unreadable",
                       "detail": f"{key} is not valid JSON, so its places and prices could not be checked."})

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
        same_day = [d for d in _dicts(tsum_.get(leg)) if str(d.get(when, ""))[:10] == want and d.get("operator")
                    and d.get("mode") not in set(brief.get("avoid_modes") or [])]
        if same_day:
            alt = same_day[0]
            must.append({"day": None, "issue": "travel_day_lost",
                         "detail": f"The {leg} option {'arrives' if later else 'leaves'} on {day_}, so the trip loses a "
                                   f"day; the transport summary has options on {want} (e.g. {alt.get('operator')} "
                                   f"{alt.get(when)}, €{alt.get('price_eur')}). Use one of those."})

    # transport and stay must be options from the summaries, in modes the travellers accept
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    tsum = _parse(state.get("transport_summary"))
    for leg in ("outbound", "return"):
        option = transport.get(leg)
        if isinstance(option, dict) and option.get("mode") in set(brief.get("avoid_modes") or []):
            must.append({"day": None, "issue": "avoided_mode",
                         "detail": f"The {leg} option is a {option.get('mode')} ({option.get('operator')}), but the "
                                   f"travellers do not want to travel by {option.get('mode')}: choose another option "
                                   "from the transport summary, or none if there is no other."})
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
            if found is None and kind in unchecked_kinds:
                pass  # the research for this kind is unreadable: nothing to compare with
            elif found is None:
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
        start, end = rhythm.start, rhythm.end
        for i in items:
            name, t = str(i.get("name") or "").lower(), _minutes(i)
            if i.get("kind") != "transport" or t is None:
                continue
            if name.startswith("arrive"):
                start = max(start, t + 90)
            elif name.startswith("depart") and n == len(days) - 1:
                end = min(end, t - (150 if "flight" in str(i.get("note", "")).lower() else 60))
        full_day = 0 < n < len(days) - 1
        if full_day and len(sights) < pace.min_sights:
            should.append({"day": label, "issue": "thin_day", "detail": f"Only {len(sights)} sight(s) on a full day."})
        gap_spans = {}  # id(issue) -> (from, idle from, to), to spot a whole empty afternoon below
        length = {}  # item time -> how long the item there takes
        for i in items:
            t = _minutes(i)
            if t is not None:
                length[t] = max(length.get(t, 0), VISIT_MIN.get(i.get("kind"), 0))
        if end - start >= 120:
            edges = [start] + [t for t in times if start <= t <= end] + [end]
            for a, b in zip(edges, edges[1:]):
                if b - a > pace.gap_min:
                    should.append({"day": label, "issue": "empty_gap",
                                   "detail": f"Nothing planned {_fmt(a)}-{_fmt(b)}."})
                    gap_spans[id(should[-1])] = (a, a + (length.get(a, 0) if a != start else 0), b)
            meals = [(i, _minutes(i)) for i in items
                     if i.get("kind") in FOOD_KINDS or (i.get("kind") == "free" and re.search(r"lunch|dinner|eat", str(i.get("name")), re.I))]
            for meal, lo, hi in (("lunch", 11 * 60 + 30, 15 * 60 + 30), ("dinner", 18 * 60, 22 * 60 + 30)):
                if start <= lo + 60 and end >= lo + 120 and not any(t is not None and lo <= t <= hi for _, t in meals):
                    should.append({"day": label, "issue": "missing_meal", "detail": f"No {meal} planned."})
        if len(sights) > pace.max_sights:
            should.append({"day": label, "issue": "packed_day", "detail": f"{len(sights)} sights in one day."})
        timed = sorted((t, i) for i in items if i.get("kind") == "sight" for t in [_minutes(i)] if t is not None)
        for (t1, a), (t2, b) in zip(timed, timed[1:]):
            if t2 - t1 < pace.min_between:
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
        for item in items:  # "Lunch near X" is for days without a researched place nearby, not instead of one
            if item.get("kind") == "free" and re.search(r"lunch|dinner|eat", str(item.get("name")), re.I):
                alternatives = _alternatives(centre, used, places, FOOD_KINDS)
                if alternatives:
                    must.append({"day": label, "issue": "placeholder_meal",
                                   "detail": f"'{item.get('name')}' at {item.get('time')} is a placeholder, but unused "
                                             "researched food places are nearby: use one of the alternatives.",
                                   "alternatives": alternatives})
        thin = [x for x in should if x["day"] == label and x["issue"] in ("thin_day", "empty_gap", "missing_meal")]
        for x in thin:
            kinds = FOOD_KINDS if x["issue"] == "missing_meal" else {"sight"}
            x["alternatives"] = _alternatives(centre, used, places, kinds)
        lost_afternoon_from = set()  # gaps already reported as a whole empty afternoon
        for x in thin:  # 1-2 free hours are fine; a whole afternoon with researched sights nearby is not
            a, idle_from, b = gap_spans.get(id(x), (0, 0, 0))
            covered = min(b, AFTERNOON[1]) - max(idle_from, AFTERNOON[0])
            if x["issue"] == "empty_gap" and x["alternatives"] and covered >= pace.empty_afternoon:
                should.remove(x)
                lost_afternoon_from.add(a)
                must.append({"day": label, "issue": "empty_afternoon",
                             "detail": f"Nothing planned {_fmt(a)}-{_fmt(b)}: a whole afternoon. Add one of the "
                                       "listed alternatives at a time it is open.",
                             "alternatives": x["alternatives"]})
        _day_shape_issues(must, should, label, items, sights, times, start, full_day, today, centre, used,
                          places, gap_spans, lost_afternoon_from, rhythm)

    far = _far_stay_issue(plan, days, ssum)
    if far:
        should.append(far)

    cost = plan_cost(plan, brief) if brief.get("travellers") else None
    budget = _budget_issue(plan, brief, state, cost)
    if budget:
        (must if budget[0] == "must_fix" else should).append(budget[1])
    facts = {"days": len(days), "sights": sum(1 for d in days for i in d.get("items") or [] if isinstance(i, dict) and i.get("kind") == "sight"),
             "total_eur": (cost or {}).get("total_eur"), "budget_eur": brief.get("total_budget_eur")}
    decide = [x for x in must if x["issue"] in TASTE_ISSUES and not x.get("obvious")]
    must = [x for x in must if x not in decide]
    return {"must_fix": must, "should_fix": should, "decide": decide, "cost": cost, "facts": facts}


def _day_shape_issues(must, should, label, items, sights, times, start, full_day, today, centre, used, places,
                      gap_spans, lost_afternoon_from, rhythm: DayRhythm) -> None:
    """Empty mornings, two meals close together, and items outside the hours the user set (must_fix).

    Live runs and the recorded demo began every full day with lunch at 12:30-13:00, though the day's own sights
    opened at 09:00-10:00, and put two restaurants 2.5 h apart on the arrival day."""
    open_by = rhythm.morning + MORNING_OPEN_AFTER_START
    late = times[0] > rhythm.morning + rhythm.pace.morning_grace if times else False
    if full_day and late and start not in lost_afternoon_from:
        early = [i.get("name") for i in sights
                 if _opens_by((find_place(i.get("name"), places) or {}).get("hours") or i.get("hours"), today, open_by)]
        alternatives = [a for a in _alternatives(centre, used, places, {"sight"})
                        if _opens_by(a.get("hours"), today, open_by)]
        if early or alternatives:
            should[:] = [x for x in should if not (x["day"] == label and x["issue"] == "empty_gap"
                                                   and gap_spans.get(id(x), (None,))[0] == start)]
            how = (f"one of the day's sights open by {_fmt(open_by)} ({', '.join(repr(n) for n in early)})"
                   if early else "one of the listed alternatives")
            issue = {"day": label, "issue": "empty_morning",
                     "detail": f"Nothing planned before {_fmt(times[0])} on a full day: start around "
                               f"{_fmt(rhythm.morning)} with {how}, and have lunch after it.",
                     "alternatives": alternatives}
            if early:
                issue["obvious"] = f"the day's own sights open by {_fmt(open_by)}: only the order changes"
            must.append(issue)
    for item in items:  # the hours the travellers asked for (none when they did not say)
        t = _minutes(item)
        if item.get("kind") == "transport" or t is None:
            continue
        if rhythm.user_start is not None and t < rhythm.user_start:
            must.append({"day": label, "issue": "before_day_start",
                         "detail": f"'{item.get('name')}' at {item.get('time')} is before {_fmt(rhythm.user_start)}, "
                                   "when the travellers start their days: move it later or drop it."})
        elif rhythm.user_end is not None and t > rhythm.user_end:
            must.append({"day": label, "issue": "after_day_end",
                         "detail": f"'{item.get('name')}' at {item.get('time')} is after {_fmt(rhythm.user_end)}, "
                                   "when the travellers end their days: move it earlier or drop it."})
    meals = sorted((t, str(i.get("name"))) for i in items
                   if i.get("kind") == "restaurant" or (i.get("kind") == "free" and MEAL_NAME.search(str(i.get("name"))))
                   for t in [_minutes(i)] if t is not None)
    for (t1, a), (t2, b) in zip(meals, meals[1:]):
        if t2 - t1 < MEAL_GAP_MIN:
            issue = {"day": label, "issue": "double_meal",
                     "detail": f"'{a}' at {_fmt(t1)} and '{b}' at {_fmt(t2)} are two meals {t2 - t1} min apart: "
                               "keep one, and use the other slot for one of the listed alternatives or drop it.",
                     "alternatives": _alternatives(centre, used, places, {"sight"})}
            if t2 - t1 < OBVIOUS_MEAL_GAP_MIN:
                issue["obvious"] = f"two meals less than {OBVIOUS_MEAL_GAP_MIN // 60} hours apart"
            must.append(issue)


def check_state(state) -> dict:
    """check_plan on state["itinerary"]; also stores the result in state["itinerary_check"] for the reviser."""
    report = check_plan(_parse(state.get("itinerary")), state)
    state["itinerary_check"] = json.dumps({k: report[k] for k in ("must_fix", "should_fix", "decide", "facts")},
                                          ensure_ascii=False)
    return report


def check_itinerary(tool_context: ToolContext) -> dict:
    """Checks the current itinerary in code and lists its problems.

    Checks the budget (cost recomputed from the plan), opening hours against visit times, places and prices
    missing from the research, transport and stay against the summaries, the day count, thin or packed days,
    long walks, meals far from the day's sights and repeated places. Problems are split into "must_fix" (always
    fixed), "decide" (day-shape matters of taste: you decide each one, fix it or decline it with a reason) and
    "should_fix"; thin days, far meals and day-shape findings list unused researched alternatives nearby.
    """
    return ok([check_state(tool_context.state)])
