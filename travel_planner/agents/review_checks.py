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
writer can fix them without inventing anything.
"""
import difflib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from google.adk.tools import ToolContext

from .. import config
from ..models import minutes_of
from ..tools._result import ok
from ..tools.airports import far_airport
from ..tools.geo import haversine_km
from ..tools.serpapi import is_hostel
from ..tools.trip import calculate_trip_cost
from .output import extract_json, was_repaired

FOOD_KINDS = {"restaurant", "cafe", "bar"}
PLACE_KINDS = {"sight"} | FOOD_KINDS
# the day's edges for the gap check when the brief sets no day_start/day_end: the defaults the prompts state
DAY_START, DAY_END = minutes_of(config.DEFAULT_DAY_START), minutes_of(config.DEFAULT_DAY_END)
FAR_MEAL_KM = 2.0
FUZZY_RATIO, FUZZY_MIN_LEN = 0.9, 8  # a typo of a researched name still matches it
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
MEAL_AFTER_SIGHT_MIN = 45  # a meal planned sooner after a sight cuts the visit short
MEAL_GAP_MIN = 180  # two restaurants (or meal placeholders) closer than this are one meal too many
# Day-shape findings that are a matter of taste (REVIEW.md A4): the code check reports them under "decide" and the
# plan critic decides each one (fix it, or decline it with a reason; see plan_critic.enforce_verdict). An obvious
# case stays must_fix with its reason in "obvious": a full day starting late although that day's own planned sights
# open in the morning (only the order changes), or two meals less than OBVIOUS_MEAL_GAP_MIN apart.
TASTE_ISSUES = ("empty_morning", "double_meal", "placeholder_meal", "empty_afternoon")
OBVIOUS_MEAL_GAP_MIN = 120
MEAL_NAME = re.compile(r"lunch|dinner|supper|breakfast|brunch|\bmeal|\beat", re.IGNORECASE)  # a meal placeholder
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
    # "Mon-Fri 07:00-16:00, Sat 08:00-16:00": a comma after a time and before a day starts a new rule (a live Åland
    # run read all three ranges as Friday's); "12:00-15:30, 19:00-22:30" stays one rule
    for rule in re.split(rf"[;\n]|(?<=\d),\s*(?={_D}\b)", s):
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

_SAINT = re.compile(r"\b(?:st|sv|sto)\b")


def _key(name) -> str:
    """A name in comparable form: no accents, case or punctuation; any dash is a space (a live run wrote
    "Notre‑Dam" with a non-breaking hyphen, which the ASCII step used to delete); "St."/"Sv." -> "saint"."""
    s = unicodedata.normalize("NFKD", _DASH.sub(" ", str(name or ""))).encode("ascii", "ignore").decode().lower()
    return _SAINT.sub("saint", re.sub(r"[^a-z0-9]+", " ", s)).strip()


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


def transport_summary(state) -> dict:
    """The transport summary; when it cannot be read, the searchers' own options, in the summary's shape.

    Without this, an unreadable summary made every transport check (lost travel day, ungrounded option, cheaper
    option) silently pass."""
    summary = _parse(state.get("transport_summary"))
    if summary or not state.get("transport_summary"):
        return summary
    merged = {}
    for leg in ("outbound", "return"):
        options = [o for key in ("flight_options", "ground_options")
                   for o in _parse(state.get(key)).get(leg) or [] if isinstance(o, dict)]
        merged[leg] = {"cheapest": None, "fastest": None, "ranked": options}
    return merged if any(merged[leg]["ranked"] for leg in merged) else {}


RESEARCH_KINDS = {"culture_findings": {"sight"}, "food_findings": FOOD_KINDS}


def unreadable_research(state) -> list[str]:
    """Research keys that hold text but no JSON object (e.g. an answer cut off mid-way): their places cannot be
    checked, so the checks must not report every planned place as invented. Text that only parses after repair
    counts too: a cut-off answer repairs into a partial list, and the places after the cut would look invented."""
    return [key for key in RESEARCH_KINDS
            if isinstance(state.get(key), str) and state[key].strip()
            and (not _parse(state[key]) or was_repaired(state[key]))]


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
    # a typo of a researched name ("Notre-Dam Treasury"): nearly the same letters, long and specific enough
    best, ratio = None, 0.0
    for p in places:
        pk = _key(p.get("name"))
        if min(len(pk), len(k)) >= FUZZY_MIN_LEN and _specific(pk) and _specific(k):
            r = difflib.SequenceMatcher(None, k, pk).ratio()
            if r > ratio:
                best, ratio = p, r
    return best if ratio >= FUZZY_RATIO else None


def walk_km(items: list[dict]) -> float | None:
    """Straight-line km between consecutive items that have coordinates (None if fewer than two)."""
    pts = [c for c in (_coords(i) for i in items) if c]
    if len(pts) < 2:
        return None
    return round(sum(haversine_km(*a, *b) for a, b in zip(pts, pts[1:])), 1)


# ---------------------------------------------------------------- cost

def stay_nights(plan: dict, brief: dict) -> tuple[int, int, str | None] | None:
    """(nights needed, nights in the brief, check-in date) when the travel times leave fewer nights to pay than the
    trip has: an overnight ferry or flight arrives the day after start_date, so the first night is on board (live
    Turku-Stockholm and Svalbard runs paid a stay for it). None when every night is needed."""
    try:
        start, end = date.fromisoformat(brief["start_date"]), date.fromisoformat(brief["end_date"])
    except (KeyError, TypeError, ValueError):
        return None
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    checkin, checkout = start, end
    out, back = transport.get("outbound"), transport.get("return")
    try:
        if isinstance(out, dict) and out.get("arrive"):
            checkin = max(start, min(end, date.fromisoformat(str(out["arrive"])[:10])))
        if isinstance(back, dict) and back.get("depart"):
            checkout = min(end, max(checkin, date.fromisoformat(str(back["depart"])[:10])))
    except ValueError:
        return None
    nights, booked = (checkout - checkin).days, (end - start).days
    return (nights, booked, checkin.isoformat()) if nights < booked else None


def splurge_eur(plan: dict, brief: dict) -> float:
    """The extra a meal costs at each planned restaurant above the trip's budget tier (its researched tier, tagged by
    tidy), for all travellers: the typical daily food cost of a low trip does not cover a Michelin dinner."""
    from ..tools.trip import MEAL_PER_PERSON
    tier = brief.get("budget_tier") if brief.get("budget_tier") in MEAL_PER_PERSON else "mid"
    extra = sum(MEAL_PER_PERSON[i["budget_tier"]] - MEAL_PER_PERSON[tier]
                for d in plan.get("days") or [] if isinstance(d, dict) for i in d.get("items") or []
                if isinstance(i, dict) and i.get("kind") == "restaurant" and i.get("budget_tier") in MEAL_PER_PERSON
                and MEAL_PER_PERSON[i["budget_tier"]] > MEAL_PER_PERSON[tier])
    return round(extra * int(brief.get("travellers") or 1), 2)


def cost_inputs(plan: dict, brief: dict) -> dict:
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    legs = [transport.get("outbound"), transport.get("return")]
    stay = plan.get("stay") if isinstance(plan.get("stay"), dict) else {}
    stay_total = _price(stay.get("total_price_eur")) or 0.0
    nights = stay_nights(plan, brief) if stay else None
    per_night = _price(stay.get("price_per_night_eur"))
    if nights and per_night is not None:  # pay only the nights the travellers sleep there
        stay_total = round(per_night * nights[0], 2)
    travellers = int(brief.get("travellers") or 1)
    tickets = [_price(i.get("ticket_eur")) for d in plan.get("days") or [] if isinstance(d, dict)
               for i in d.get("items") or [] if isinstance(i, dict) and i.get("kind") == "sight"]
    return {
        "transport_total_eur": sum(_price(leg.get("price_eur")) or 0 for leg in legs if isinstance(leg, dict)),
        "stay_total_eur": stay_total,
        "activities_total_eur": sum(t for t in tickets if t) * travellers,
        "days": int(brief.get("days") or len(plan.get("days") or []) or 1),
        "travellers": travellers,
        "budget_tier": brief.get("budget_tier") or "mid",
        "total_budget_eur": brief.get("total_budget_eur"),
        "extra_food_eur": splurge_eur(plan, brief),
        **({"local_transport_per_person_per_day_eur": 0.0} if brief.get("own_car") else {}),  # they have the car
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
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    unpriced = [f"the {leg} {o.get('mode') or 'trip'} fare ({o.get('operator')})" for leg in ("outbound", "return")
                for o in [transport.get(leg)] if isinstance(o, dict) and _price(o.get("price_eur")) is None]
    if unpriced:  # a live car-ferry plan counted the unknown fare as €0 and looked cheap
        cost["incomplete"] = unpriced
        cost["assumptions"].append(f"not included because unknown: {', '.join(unpriced)}; the real total is higher")
    if unknown["sights with unknown ticket"]:
        cost["assumptions"].append(f"{unknown['sights with unknown ticket']} sight(s) with an unknown ticket price, counted as €0")
    nights = stay_nights(plan, brief) if isinstance(plan.get("stay"), dict) else None
    if nights and _price(plan["stay"].get("price_per_night_eur")) is not None:
        cost["assumptions"].append(f"stay: {nights[0]} of the trip's {nights[1]} nights (check in on {nights[2]}; the "
                                   "other nights are spent travelling), at the listed price per night")
    if brief.get("own_car"):  # electric or not, the cost of the drive depends on the car: say so, never guess it
        cost["assumptions"].append("own car: fuel or charging, tolls and parking are not included; no local public "
                                   "transport")
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
    summary = transport_summary(state)
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


_STARS = re.compile(r"([1-5])\s*★")


def stars(stay: dict) -> int | None:
    m = _STARS.search(str(stay.get("details") or ""))
    return int(m.group(1)) if m else None


def _stay_tier_issue(plan: dict, brief: dict, summary: dict, cost: dict | None) -> dict | None:
    """A stay below the budget tier while the stay summary has a fitting one within the budget: a hostel on a mid
    or high budget (live runs: 5 of 21), or less than 4★ on a high budget ("money is no object, best hotel" got a
    €367 village Airbnb next to a 5★ hotel in the summary)."""
    stay = plan.get("stay") if isinstance(plan.get("stay"), dict) else None
    tier = brief.get("budget_tier") or "mid"
    if not stay or tier == "low":
        return None
    hostel = is_hostel(stay.get("name"), stay.get("details"))
    below = hostel or (tier == "high" and (stars(stay) or 0) < 4)
    if not below:
        return None
    budget = _price(brief.get("total_budget_eur"))
    room = (budget - cost["total_eur"] + (_price(stay.get("total_price_eur")) or 0)) if budget and cost else None
    options = {}
    for d in _dicts(summary):
        total = _price(d.get("total_price_eur"))
        if d.get("kind") not in ("hotel", "airbnb") or total is None or d.get("name") == stay.get("name"):
            continue
        if is_hostel(d.get("name"), d.get("details")) or (tier == "high" and (stars(d) or 0) < 4):
            continue
        if room is not None and total > room:
            continue
        options[d["name"]] = {"name": d["name"], "kind": d.get("kind"), "area": d.get("area"),
                              "total_price_eur": total, "rating": d.get("rating"), "stars": stars(d)}
    if not options:
        return None
    what = "a hostel" if hostel else "below 4★"
    ranked = sorted(options.values(), key=lambda o: (-(o["rating"] or 0), o["total_price_eur"]))
    return {"day": None, "issue": "stay_tier",
            "detail": f"The stay '{stay.get('name')}' is {what} on a {tier} budget: choose one of the listed stays from "
                      "the stay summary (copied unchanged)" + (", all within the budget." if room is not None else "."),
            "alternatives": ranked[:3]}


# ---------------------------------------------------------------- interests and food tier

GENERIC_INTERESTS = {"sightseeing", "culture", "cultural", "relax", "relaxing", "relaxation", "everything", "anything",
                     "general", "fun", "travel", "tourism", "city", "local", "life", "and", "the", "of", "for"}


def _stems(text: str) -> list[str]:
    """The words of an interest, cut to 4 letters ("architecture" -> "arch"), without generic words."""
    return [w[:4] for w in re.findall(r"[a-z]+", _key(text)) if len(w) >= 3 and w not in GENERIC_INTERESTS]


def _matches(stems: list[str], text: str) -> bool:
    words = re.findall(r"[a-z]+", _key(text))
    return any(w.startswith(s) for s in stems for w in words)


def _interest_issues(plan: dict, brief: dict, places: list[dict], used: set[str], must: list, should: list) -> None:
    """Each stated interest needs at least one planned place that matches it (name, note or researched summary).
    Live runs missed Sagrada Família and Park Güell on an "architecture" trip, surfing and wine on a Porto trip.
    A researched match left unused is must_fix (with alternatives); no match in the research at all is reported
    so the guide can say so."""
    items = [i for d in plan.get("days") or [] if isinstance(d, dict) for i in d.get("items") or []
             if isinstance(i, dict) and i.get("kind") in PLACE_KINDS]

    def text_of(item: dict) -> str:
        found = find_place(item.get("name"), places) or {}
        return " ".join(str(x or "") for x in (item.get("name"), item.get("note"), found.get("summary")))

    planned = [text_of(i) for i in items]
    for interest in brief.get("interests") or []:
        stems = _stems(str(interest))
        if not stems or any(_matches(stems, t) for t in planned):
            continue
        candidates = [p for p in places if _key(p.get("name")) not in used
                      and _matches(stems, f"{p.get('name')} {p.get('summary') or ''}")]
        if candidates:
            must.append({"day": None, "issue": "interest_missing",
                         "detail": f"The travellers are interested in '{interest}', but no planned place matches it: "
                                   "add one of the listed researched places on a day it fits (open, nearby).",
                         "alternatives": [{"name": p["name"], "kind": p.get("kind"), "hours": p.get("hours"),
                                           "ticket_eur": p.get("ticket_eur")} for p in candidates[:3]]})
        else:
            should.append({"day": None, "issue": "interest_not_found",
                           "detail": f"The research found nothing for the interest '{interest}', so the plan has "
                                     "nothing for it."})


TIER_RANK = {"low": 0, "mid": 1, "high": 2}


def _food_tier_issues(days: list[dict], brief: dict, places: list[dict], used: set[str], must: list) -> None:
    """A splurge restaurant on a low budget: swap it for an unused low or mid place nearby, or a meal placeholder when
    none is left (live €200 Reykjavík runs planned Hotel Holt and the Michelin-starred Dill)."""
    if (brief.get("budget_tier") or "mid") != "low":
        return
    for n, d in enumerate(days):
        for item in d.get("items") or []:
            if not isinstance(item, dict) or item.get("kind") not in FOOD_KINDS:
                continue
            found = find_place(item.get("name"), places) or {}
            if found.get("budget_tier") != "high":
                continue
            near = _alternatives(_coords(item), used, places, FOOD_KINDS, n=6) if _coords(item) else []
            cheaper = [a for a in near if a.get("budget_tier") in ("low", "mid", None)][:3]
            how = ("swap it for one of the listed nearby places." if cheaper else
                   "no cheaper researched place is left, so replace it with a \"free\" item \"Lunch near <area>\" or "
                   "\"Dinner near <area>\" at the same time.")
            must.append({"day": d.get("day") or n + 1, "issue": "food_tier",
                         "detail": f"'{item.get('name')}' is a splurge (high budget tier) on a low-budget trip: {how}",
                         "alternatives": cheaper})


def fits_tier(stay: dict, tier: str) -> bool:
    """A stay the budget tier accepts (see _stay_tier_issue): no hostel above low, 4-5★ on high."""
    if tier != "low" and is_hostel(stay.get("name"), stay.get("details")):
        return False
    return not (tier == "high" and (stars(stay) or 0) < 4)


CHEAPER_WORDS = re.compile(r"cheap|less expensive|lower (?:price|cost)|budget|save|halvemp|edullis", re.I)


def asked_cheaper_stay(state) -> dict | None:
    """The previous plan's stay when the travellers' change asked for a cheaper stay (a re-plan), else None."""
    from .replan import NOTE_KEY, STAY_WORDS
    note = str(state.get(NOTE_KEY) or "")
    before = (state.get("previous_plan_choices") or {}).get("stay")
    if isinstance(before, dict) and STAY_WORDS.search(note) and CHEAPER_WORDS.search(note) \
            and _price(before.get("total_price_eur")) is not None:
        return before
    return None


def _cheaper_stay_issue(plan: dict, state, summary: dict) -> dict | None:
    """A re-plan for a cheaper stay that chose a dearer one (a live Kraków re-plan went from €68 to €96) while the
    new stay summary has one cheaper than before."""
    before, stay = asked_cheaper_stay(state), plan.get("stay")
    if not (before and isinstance(stay, dict)) or (_price(stay.get("total_price_eur")) or 0) < before["total_price_eur"]:
        return None
    tier = (state.get("trip_brief") or {}).get("budget_tier") or "mid"
    cheaper = {d["name"]: {"name": d["name"], "kind": d.get("kind"), "area": d.get("area"),
                           "total_price_eur": _price(d.get("total_price_eur")), "rating": d.get("rating")}
               for d in _dicts(summary) if d.get("kind") in ("hotel", "airbnb") and d.get("name")
               and _price(d.get("total_price_eur")) is not None
               and _price(d.get("total_price_eur")) < before["total_price_eur"] and fits_tier(d, tier)}
    if not cheaper:
        return None
    return {"day": None, "issue": "change_not_met",
            "detail": f"The travellers asked for a cheaper stay than '{before.get('name')}' (€{before['total_price_eur']:g}), "
                      f"but the plan has '{stay.get('name')}' (€{stay.get('total_price_eur')}): choose one of the listed "
                      "cheaper stays from the stay summary (copied unchanged).",
            "alternatives": sorted(cheaper.values(), key=lambda o: o["total_price_eur"])[:3]}


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


def _fmt_option(o: dict, when: str) -> str:
    price = _price(o.get("price_eur"))
    return f"{o.get('operator')} {o.get(when)}" + (f", €{price:g}" if price is not None else ", price unknown")


def usable_options(summary: dict, leg: str, brief: dict) -> list[dict]:
    """The leg's options in the transport summary that the travellers can take: a mode they accept, and arriving
    at (or leaving from) the destination, not an airport far away. Each option once."""
    avoid = set(brief.get("avoid_modes") or [])
    found = {}
    for d in _dicts(summary.get(leg) if isinstance(summary.get(leg), (dict, list)) else []):
        if not (d.get("operator") and d.get("depart") and d.get("mode")) or d.get("mode") in avoid:
            continue
        if far_airport(d, leg, brief.get("destination_lat"), brief.get("destination_lon")):
            continue
        found.setdefault((d.get("operator"), d.get("depart"), _price(d.get("price_eur"))), d)
    return list(found.values())


# ---------------------------------------------------------------- transport value (price + time, by budget tier)

VALUE_PER_HOUR = {"low": 5.0, "mid": 15.0, "high": 40.0}  # € per traveller per hour of travel or of lost trip time
AIRPORT_HOURS = 2.0       # check-in, security and getting to and from the airport
DAY_FROM, DAY_TO = 10 * 60, 20 * 60  # the part of a trip day that counts as lost when travel eats it
LOST_DAY_HOURS = 12.0     # a whole trip day lost to travel
LONG_BUS_MIN = 6 * 60     # longer buses (and any overnight bus) do not suit travellers with these needs:
CARE_NEEDS = re.compile(r"wheelchair|mobility|disab|child|kid|infant|baby|toddler|stroller|pram|elderly|pregnan", re.I)
POOR_CHOICE = (0.10, 30.0)  # the chosen leg is a poor choice when it scores 10 % and €30 worse than the best one


def _iso_day_min(iso) -> tuple[str, int] | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{1,2}):(\d{2})", str(iso or ""))
    return (m.group(1), int(m.group(2)) * 60 + int(m.group(3))) if m else None


HOME_MORNING = 8 * 60  # getting home after midnight but before 08:00 costs a short night, not a whole day


def home_hours_lost(end_day, back) -> float:
    """Hours of the travellers' own time a return arriving after the trip's last day costs: the hours past midnight
    when it lands in the early morning (an 00:40 landing is a late night), a whole day (12 h) when it lands later
    that day (a 22:45 departure with an overnight layover reached Helsinki at 11:15), and 12 h for each further day."""
    if not (end_day and back):
        return 0.0
    days = (date.fromisoformat(back[0]) - date.fromisoformat(end_day)).days
    if days <= 0:
        return 0.0
    first = back[1] / 60 if days == 1 and back[1] <= HOME_MORNING else LOST_DAY_HOURS
    return first + (days - 1) * LOST_DAY_HOURS


def lost_hours(option: dict, leg: str, brief: dict) -> float:
    """Trip hours at the destination that this leg uses up: the first day after 10:00 until arrival, or the last
    day from leaving for the airport/station until 20:00; a whole day (12 h) for each day arriving late or leaving
    early. A live Copenhagen plan took an 08:55 return although the 20:10 flight cost €5 more."""
    def days_between(a, b) -> int:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days

    if leg == "outbound":
        when, day = _iso_day_min(option.get("arrive")), brief.get("start_date")
        if not (when and day):
            return 0.0
        left = _iso_day_min(option.get("depart"))
        home = max(0, days_between(left[0], day)) if left else 0  # leaving home days before the trip
        late = days_between(day, when[0])
        return (max(0, late) + home) * LOST_DAY_HOURS + max(0, min(when[1], DAY_TO) - DAY_FROM) / 60
    when, day = _iso_day_min(option.get("depart")), brief.get("end_date")
    if not (when and day):
        return 0.0
    back = _iso_day_min(option.get("arrive"))
    home = home_hours_lost(day, back)  # getting home after the trip's last day
    early = days_between(when[0], day)
    if early < 0:  # leaves after the trip: no trip day lost (night_without_stay reports the missing bed)
        return home
    leave = when[1] - LEAVE_BUFFER_MIN.get(option.get("mode"), 60)
    return early * LOST_DAY_HOURS + home + max(0, DAY_TO - max(leave, DAY_FROM)) / 60


def unsuitable_for_needs(option: dict, brief: dict) -> bool:
    """A long or overnight bus for travellers whose requirements mention a wheelchair, children and the like (a live
    run sent a wheelchair user and a 2-year-old on a 31-hour bus)."""
    if option.get("mode") != "bus" or not any(CARE_NEEDS.search(str(r)) for r in brief.get("requirements") or []):
        return False
    a, b = _iso_day_min(option.get("depart")), _iso_day_min(option.get("arrive"))
    overnight = bool(a and b and b[0] > a[0])
    return overnight or int(option.get("duration_min") or 0) > LONG_BUS_MIN


def leg_cost(option: dict, leg: str, brief: dict) -> float | None:
    """What a leg costs the group: its price plus the travel time and the trip time it uses up, valued per traveller
    and hour by the budget tier. None without a price (it cannot be compared)."""
    price = _price(option.get("price_eur"))
    if price is None:
        return None
    hours = int(option.get("duration_min") or 0) / 60 + (AIRPORT_HOURS if option.get("mode") == "flight" else 0)
    value = VALUE_PER_HOUR.get(brief.get("budget_tier") or "mid", VALUE_PER_HOUR["mid"])
    return round(price + int(brief.get("travellers") or 1) * value * (hours + lost_hours(option, leg, brief)), 2)


def recommended_option(summary: dict, leg: str, brief: dict, extra: list[dict] | None = None) -> dict | None:
    """The leg's best option by leg_cost among those the travellers can take (mode, airport, needs)."""
    picked = (summary.get("recommended") or {}).get(leg) if isinstance(summary.get("recommended"), dict) else None
    pool = usable_options({leg: list(_dicts(summary.get(leg))) + list(extra or []) + ([picked] if isinstance(picked, dict) else [])},
                          leg, brief)
    # on the trip's own travel days when any option is (a live Åland run "recommended" a ferry home the day after
    # the trip ended, which leaves a night without a bed)
    when, day = ("arrive", brief.get("start_date")) if leg == "outbound" else ("depart", brief.get("end_date"))
    on_day = [o for o in pool if day and str(o.get(when, ""))[:10] == day]
    pool = on_day or pool
    scored = [(leg_cost(o, leg, brief), o) for o in pool if not unsuitable_for_needs(o, brief)]
    scored = [(c, o) for c, o in scored if c is not None]
    return min(scored, key=lambda x: x[0])[1] if scored else None


def _transport_value_issues(plan: dict, brief: dict, summary: dict, cost: dict | None, must: list) -> None:
    """The chosen transport is unsuitable for the travellers' needs, or clearly worse value than another option in
    the summary (price plus time, weighted by the budget tier) while switching keeps the plan within budget. The
    value check stays quiet when the plan is over budget: then the budget check names the cheaper options."""
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    budget = _price(brief.get("total_budget_eur"))
    over = bool(budget and cost and cost["total_eur"] > budget)
    for leg in ("outbound", "return"):
        chosen = transport.get(leg)
        if not isinstance(chosen, dict):
            continue
        best = recommended_option(summary, leg, brief)
        if unsuitable_for_needs(chosen, brief):
            alt = f" Use e.g. {_fmt_option(best, 'depart')}." if best else " Say so in notes if there is no other."
            must.append({"day": None, "issue": "unsuitable_transport",
                         "detail": f"The {leg} option ({option_label(chosen)}, {int(chosen.get('duration_min') or 0) // 60} h) "
                                   "is a long or overnight bus, which does not suit the travellers' needs "
                                   f"({'; '.join(brief.get('requirements') or [])}).{alt}"})
            continue
        if over or not best or (best.get("operator"), best.get("depart")) == (chosen.get("operator"), chosen.get("depart")):
            continue
        mine, theirs = leg_cost(chosen, leg, brief), leg_cost(best, leg, brief)
        if mine is None or theirs is None or mine - theirs < max(POOR_CHOICE[1], POOR_CHOICE[0] * mine):
            continue
        extra = (_price(best.get("price_eur")) or 0) - (_price(chosen.get("price_eur")) or 0)
        if budget and cost and cost["total_eur"] + extra > budget:
            continue
        why = []
        if lost_hours(chosen, leg, brief) - lost_hours(best, leg, brief) >= 3:
            why.append(f"it loses about {lost_hours(chosen, leg, brief) - lost_hours(best, leg, brief):.0f} fewer "
                       "hours of the trip days (or of a day at home)")
        if int(chosen.get("duration_min") or 0) - int(best.get("duration_min") or 0) >= 180:
            why.append(f"it takes {(int(chosen['duration_min']) - int(best.get('duration_min') or 0)) // 60} h less")
        must.append({"day": None, "issue": "poor_transport_choice",
                     "detail": f"The {leg} option ({_fmt_option(chosen, 'depart')}) is poor value for this "
                               f"{brief.get('budget_tier') or 'mid'}-budget trip: {_fmt_option(best, 'depart')} "
                               f"{'costs €%g more' % extra if extra > 0 else 'costs €%g less' % -extra}"
                               + (f" and {' and '.join(why)}" if why else "") + ". Switch to it (copied unchanged "
                               "from the transport summary, where it is the recommended option)."})


def _transport_issues(plan: dict, brief: dict, summary: dict, must: list, should: list) -> None:
    """Transport that does not get the travellers there or back as the trip needs:
    - a flight landing (or leaving) far from the destination (live Kyiv run: Chișinău, 409 km away);
    - a return leaving after the trip's last day, so the travellers have no bed that night (a live group run's bus
      left at 01:49 the day after the trip ended)."""
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    destination = brief.get("destination") or "the destination"
    for leg in ("outbound", "return"):
        option = transport.get(leg)
        if not isinstance(option, dict):
            continue
        far = far_airport(option, leg, brief.get("destination_lat"), brief.get("destination_lon"))
        if not far:
            continue
        # only an option that keeps the trip's days (a live Kyiv run was offered a bus arriving two days later)
        when, day = ("arrive", brief.get("start_date")) if leg == "outbound" else ("depart", brief.get("end_date"))
        others = [o for o in usable_options(summary, leg, brief) if not day or str(o.get(when, ""))[:10] == day]
        where = (f"The {leg} flight ({option.get('operator')}) {'lands at' if leg == 'outbound' else 'leaves from'} "
                 f"{far['iata']} ({far['city']}), {far['km']} km from {destination}")
        if others:
            must.append({"day": None, "issue": "far_airport",
                         "detail": f"{where}. Use an option that arrives at {destination}, e.g. "
                                   f"{_fmt_option(others[0], 'depart')}."})
        else:
            should.append({"day": None, "issue": "far_airport",
                           "detail": f"{where}, and no option in the transport summary gets closer. Say so in "
                                     f"notes: the travellers must get from {far['city']} to {destination} themselves."})
    back, end = transport.get("return"), brief.get("end_date")
    leaves = str((back or {}).get("depart", ""))[:10] if isinstance(back, dict) else ""
    if end and leaves and leaves > end and not brief.get("own_accommodation"):
        on_time = [o for o in usable_options(summary, "return", brief) if str(o.get("depart", ""))[:10] == end]
        detail = (f"The return ({option_label(back)}) leaves on {leaves}, after the trip's last day ({end}): the "
                  "travellers have no bed for that night.")
        if on_time:
            must.append({"day": None, "issue": "night_without_stay",
                         "detail": detail + f" Use a return on {end}, e.g. {_fmt_option(on_time[0], 'depart')}."})
        else:
            should.append({"day": None, "issue": "night_without_stay",
                           "detail": detail + " No return in the summary leaves on that day: say so in notes."})


LEAVE_BUFFER_MIN = {"flight": 150, "train": 60, "bus": 60, "ferry": 60}  # be at the airport / station in time


def leave_buffer(plan: dict, item: dict) -> int:
    """Minutes the travellers need before the departure: the mode of the plan's return option (the departure item's
    note only when there is none, e.g. an own-car trip)."""
    back = (plan.get("transport") or {}).get("return") if isinstance(plan.get("transport"), dict) else None
    mode = back.get("mode") if isinstance(back, dict) else None
    if mode in LEAVE_BUFFER_MIN:
        return LEAVE_BUFFER_MIN[mode]
    return 150 if "flight" in str(item.get("note", "")).lower() else 60


def _no_time_to_leave(must: list, label, items: list[dict], depart: int, buffer: int) -> None:
    """An item on the departure day that ends (time + typical visit length) after departure - buffer: in live
    runs "09:00 Casa Batlló · 10:45 Depart (flight)" and "11:00 Mix kiosk · 12:10 Depart (flight)"."""
    latest = depart - buffer
    for item in items:
        t = _minutes(item)
        if item.get("kind") == "transport" or t is None or t >= depart:
            continue
        length = leave_visit_min(item)
        ends = t + length
        if ends > latest:
            how = "the airport" if buffer >= 150 else "the station or port"
            must.append({"day": label, "issue": "no_time_to_leave",
                         "detail": f"'{item.get('name')}' at {item.get('time')} lasts until about {_fmt(ends)}, but "
                                   f"the travellers must leave for {how} by {_fmt(latest)} ({buffer // 60} h "
                                   f"{buffer % 60:02d} min before the {_fmt(depart)} departure): start it by "
                                   f"{_fmt(latest - length)} at the latest (still inside its opening hours and after "
                                   "the item before it), or drop it."})


def leave_visit_min(item: dict) -> int:
    """How long an item lasts for the departure-day check: a short visit still fits; a long one never."""
    return min(VISIT_MIN.get(item.get("kind"), 60), 60)


def too_late_to_leave(plan: dict) -> list[tuple[int, dict]]:
    """(day index, item) of the departure-day items that end after the travellers must leave (see _no_time_to_leave)."""
    days = [d for d in plan.get("days") or [] if isinstance(d, dict)]
    if not days:
        return []
    items = [i for i in days[-1].get("items") or [] if isinstance(i, dict)]
    departs = [i for i in items if i.get("kind") == "transport" and str(i.get("name") or "").lower().startswith("depart")
               and _minutes(i) is not None]
    if not departs:
        return []
    depart = _minutes(departs[-1])
    latest = depart - leave_buffer(plan, departs[-1])
    return [(len(days) - 1, i) for i in items if i.get("kind") != "transport" and _minutes(i) is not None
            and _minutes(i) < depart and _minutes(i) + leave_visit_min(i) > latest]


DAY_TRIP_KM = 15.0  # a sight this far from the destination is an excursion: it fills the day by itself


def is_day_trip(sights: list[dict], brief: dict) -> bool:
    try:
        centre = float(brief["destination_lat"]), float(brief["destination_lon"])
    except (KeyError, TypeError, ValueError):
        return False
    return any(c and haversine_km(*centre, *c) > DAY_TRIP_KM for c in (_coords(i) for i in sights))


def travel_free(plan: dict, day) -> bool:
    """False for a day the travel takes: on or before the outbound's arrival date, or on or after the return's
    departure date (a long-haul flight to Tokyo landed at 20:55 on day 2; that day is not a full day)."""
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    out, back = transport.get("outbound"), transport.get("return")
    arrive = str(out.get("arrive") or "")[:10] if isinstance(out, dict) else ""
    leave = str(back.get("depart") or "")[:10] if isinstance(back, dict) else ""
    day = str(day or "")
    return not (day and ((arrive and day <= arrive) or (leave and day >= leave)))


def option_label(o: dict) -> str:
    return f"{o.get('operator')} {o.get('mode') or ''} {str(o.get('depart') or '')[11:16]}".replace("  ", " ").strip()


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
    for key, what in (("transport_summary", "the searchers' own options were used for the transport checks"),
                      ("stay_summary", "the stay could not be checked")):
        if isinstance(state.get(key), str) and state[key].strip() and not _parse(state[key]):
            should.append({"day": None, "issue": "research_unreadable",
                           "detail": f"{key} is not valid JSON: {what}."})

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
    tsum_ = transport_summary(state)
    for leg, when, want, later in (("outbound", "arrive", brief.get("start_date"), True),
                                   ("return", "depart", brief.get("end_date"), False)):
        option = transport_.get(leg)
        day_ = str((option or {}).get(when, ""))[:10] if isinstance(option, dict) else ""
        if not (want and day_) or day_ == want or (day_ > want) != later:
            continue
        same_day = [d for d in _dicts(tsum_.get(leg)) if str(d.get(when, ""))[:10] == want and d.get("operator")
                    and d.get("mode") not in set(brief.get("avoid_modes") or [])]
        if same_day:
            best = recommended_option(tsum_, leg, brief)  # the same pick as poor_transport_choice, when it fits
            alt = best if best and str(best.get(when, ""))[:10] == want else same_day[0]
            must.append({"day": None, "issue": "travel_day_lost",
                         "detail": f"The {leg} option {'arrives' if later else 'leaves'} on {day_}, so the trip loses a "
                                   f"day; the transport summary has options on {want} (e.g. {alt.get('operator')} "
                                   f"{alt.get(when)}, €{alt.get('price_eur')}). Use one of those."})

    # a trip in the travellers' own car over the sea needs the car ferry the research found
    if brief.get("own_car"):
        for leg in ("outbound", "return"):
            ferries = [d for d in _dicts(tsum_.get(leg)) if d.get("mode") == "ferry" and d.get("operator")]
            if ferries and not isinstance(transport_.get(leg), dict):
                f = ferries[0]
                must.append({"day": None, "issue": "car_ferry_missing",
                             "detail": f"The road route crosses the sea, so the {leg} drive needs the car ferry: copy "
                                       f"it from the transport summary (e.g. {f.get('operator')} {f.get('depart')})."})

    _transport_issues(plan, brief, tsum_, must, should)

    # transport and stay must be options from the summaries, in modes the travellers accept
    transport = plan.get("transport") if isinstance(plan.get("transport"), dict) else {}
    tsum = transport_summary(state)
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
                end = min(end, t - leave_buffer(plan, i))
                _no_time_to_leave(must, label, items, t, leave_buffer(plan, i))
        full_day = 0 < n < len(days) - 1 and travel_free(plan, d.get("date"))
        day_trip = is_day_trip(sights, brief)  # an excursion out of town fills the day by itself
        if full_day and not day_trip and not sights:
            must.append({"day": label, "issue": "empty_day",
                         "detail": "No sight at all on a full day: add sights (the listed alternatives, or a day trip "
                                   "from the research) so the day is not empty."})
        elif full_day and not day_trip and len(sights) < pace.min_sights:
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
                if b - a > pace.gap_min and not day_trip and not (full_day and not sights):  # empty_day says it
                    should.append({"day": label, "issue": "empty_gap",
                                   "detail": f"Nothing planned {_fmt(a)}-{_fmt(b)}."})
                    gap_spans[id(should[-1])] = (a, a + (length.get(a, 0) if a != start else 0), b)
            meals = [(i, _minutes(i)) for i in items
                     if i.get("kind") in FOOD_KINDS or (i.get("kind") == "free" and MEAL_NAME.search(str(i.get("name"))))]
            for meal, lo, hi in (("lunch", 11 * 60 + 30, 15 * 60 + 30), ("dinner", 18 * 60, 22 * 60 + 30)):
                if start <= lo + 60 and end >= lo + 120 and not any(t is not None and lo <= t <= hi for _, t in meals):
                    should.append({"day": label, "issue": "missing_meal", "detail": f"No {meal} planned."})
        if len(sights) > pace.max_sights:
            should.append({"day": label, "issue": "packed_day", "detail": f"{len(sights)} sights in one day."})
        # a sight needs time: the next item (another sight, or a meal: a live run had lunch 30 min after a hammam)
        timed = sorted(((t, i) for i in items if i.get("kind") != "transport" for t in [_minutes(i)] if t is not None),
                       key=lambda x: x[0])
        for (t1, a), (t2, b) in zip(timed, timed[1:]):
            # a meal after a sight needs less room than the next sight (a relaxed pace's 60 min would collide with
            # the departure-day limits: the recorded demo went back and forth between the two)
            room = pace.min_between if b.get("kind") == "sight" else min(pace.min_between, MEAL_AFTER_SIGHT_MIN)
            if a.get("kind") == "sight" and t2 - t1 < room:
                should.append({"day": label, "issue": "packed_day",
                               "detail": f"Only {t2 - t1} min between '{a.get('name')}' and '{b.get('name')}' (at "
                                         f"least {room} min): move the later one, but never against a must-fix "
                                         "point such as the time to leave or opening hours."})
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
            if item.get("kind") == "free" and MEAL_NAME.search(str(item.get("name"))):
                alternatives = _alternatives(centre, used, places, FOOD_KINDS)
                if alternatives:
                    must.append({"day": label, "issue": "placeholder_meal",
                                   "detail": f"'{item.get('name')}' at {item.get('time')} is a placeholder, but unused "
                                             "researched food places are nearby: use one of the alternatives.",
                                   "alternatives": alternatives})
        thin = [x for x in should + must if x["day"] == label
                and x["issue"] in ("thin_day", "empty_gap", "missing_meal", "empty_day")]
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

    _interest_issues(plan, brief, places, used, must, should)
    _food_tier_issues(days, brief, places, used, must)
    far = _far_stay_issue(plan, days, ssum)
    if far:
        should.append(far)

    cost = plan_cost(plan, brief) if brief.get("travellers") else None
    budget = _budget_issue(plan, brief, state, cost)
    if budget:
        (must if budget[0] == "must_fix" else should).append(budget[1])
    pinned = state.get("pinned_choices") or {}  # kept from the previous plan by a re-plan: not open to review
    if not pinned.get("transport"):
        _transport_value_issues(plan, brief, tsum, cost, must)
    tier_issue = None if pinned.get("stay") else _stay_tier_issue(plan, brief, ssum, cost)
    if tier_issue:
        must.append(tier_issue)
    change_issue = _cheaper_stay_issue(plan, state, ssum)
    if change_issue:
        must.append(change_issue)
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
    """check_plan on state["itinerary"]; also stores the result in state["itinerary_check"] for the writer."""
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
