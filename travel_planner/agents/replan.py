"""The change router (REVIEW.md A1): after a plan exists, the CONCIERGE decides which research a change needs.

The concierge calls `request_replan(rerun, change_note, reason)` with the research teams the user's change affects
("a cheaper hotel" -> ["stay"], "more museums" -> ["culture"], "start at 8" -> []). Code only guards facts and
refuses with a reason when the scope is unsafe:
- no finished research to reuse (a failed or missing plan);
- a new destination, dates, origin or number of travellers: that is a new trip, everything is researched again;
- a hard fact the scope ignores: changed `avoid_modes` without transport, a switch to a packed pace without culture
  (it needs more sights).
An accepted scope is stored in state; the concierge then saves the brief with save_trip_brief, which starts the
plan (in the same turn, a brief that changes only fields that keep the trip, such as the pace or the budget, needs
no second confirmation: the user has just confirmed the change). `pipeline.reset_plan_state` uses the scope when
the plan starts if it was accepted in this turn (or for exactly the brief that was saved) and is still safe for it. Research branches out of scope are skipped by
`skip_branch` and keep their results. If the concierge does not call the tool, everything is researched again:
code never decides on its own to reuse research. Every request, accepted or refused, goes to `replan_log`.
"""
from google.adk.tools import ToolContext
from google.genai import types

from ..tools._result import error, ok, safe_tool

BRANCHES = {  # research team -> the state keys it writes (teams.RESEARCH_KEYS, in branch order)
    "transport": ("flight_options", "ground_options", "transport_summary"),
    "stay": ("airbnb_options", "hotel_options", "stay_summary"),
    "culture": ("culture_findings",),
    "food": ("food_findings",),
}
REUSABLE_NEEDS = ("transport_summary", "stay_summary", "culture_findings", "food_findings")
NEW_TRIP_FIELDS = ("destination", "destination_country", "start_date", "end_date", "origin", "travellers")
COORD_FIELDS = ("destination_lat", "destination_lon", "origin_lat", "origin_lon")
COORD_TOLERANCE_DEG = 0.05  # as concierge.COORD_TOLERANCE_DEG: the same place geocoded twice
DERIVED = ("nights", "days")
SCOPE_KEY = "replan_scope"     # the accepted scope, waiting for the next plan
LOG_KEY = "replan_log"         # every request: accepted or refused, and why
REUSED_KEY = "research_reused"  # the branches the current plan reused (None: everything was researched)
NOTE_KEY = "plan_change_note"  # rendered into every planning agent's instruction (prompts.REQUIREMENTS)
PLANNED_KEY = "planned_brief"  # the brief of the latest plan, i.e. of the research in state (set at its start)


def normal(brief: dict | None) -> dict:
    """A brief in comparable form: the saved brief and the brief shown to the user (concierge._shown) differ in
    case, in the order of interests and in which empty fields they list."""
    out = {}
    for key, value in (brief or {}).items():
        if key in DERIVED or value in (None, "", []):
            continue
        if key in ("destination", "origin", "destination_country") and isinstance(value, str):
            value = value.casefold()
        elif key in ("interests", "avoid_modes", "requirements") and isinstance(value, list):
            value = sorted({str(v).strip().casefold() for v in value})
        out[key] = value
    return out


def same_brief(a: dict | None, b: dict | None) -> bool:
    x, y = normal(a), normal(b)
    if x.keys() != y.keys():
        return False
    for key in x:
        if key in COORD_FIELDS:
            try:
                if abs(float(x[key]) - float(y[key])) > COORD_TOLERANCE_DEG:
                    return False
            except (TypeError, ValueError):
                return False
        elif x[key] != y[key]:
            return False
    return True


def changed_fields(base: dict | None, target: dict | None) -> set[str]:
    x, y = normal(base), normal(target)
    changed = set()
    for key in set(x) | set(y):
        a, b = x.get(key), y.get(key)
        if key in COORD_FIELDS and a is not None and b is not None:
            try:
                if abs(float(a) - float(b)) > COORD_TOLERANCE_DEG:
                    changed.add(key)
            except (TypeError, ValueError):
                changed.add(key)
        elif a != b:
            changed.add(key)
    return changed


def scope_problem(state, base: dict | None, target: dict | None, rerun: list[str]) -> str | None:
    """Why this scope is unsafe for a re-plan from `base` (the brief of the finished plan) to `target`, or None."""
    unknown = [r for r in rerun if r not in BRANCHES]
    if unknown:
        return f"Unknown research team(s) {unknown}: use only {list(BRANCHES)}."
    missing = [k for k in REUSABLE_NEEDS if not state.get(k)]
    if not base or missing or not state.get("review_log"):
        return ("There is no finished plan whose research could be reused. Save the brief with save_trip_brief "
                "(everything is researched again).")
    changed = changed_fields(base, target)
    new_trip = sorted(changed & set(NEW_TRIP_FIELDS + COORD_FIELDS))
    if new_trip:
        return (f"This is a new trip ({', '.join(new_trip)} changed): the research cannot be reused. Do not call "
                "request_replan; confirm and save the new brief with save_trip_brief.")
    if "avoid_modes" in changed and "transport" not in rerun:
        return "The excluded transport modes changed, so the transport research must run again: add \"transport\"."
    if (target or {}).get("pace") == "packed" and (base or {}).get("pace") != "packed" and "culture" not in rerun:
        return "A packed pace needs more sights than the earlier research found: add \"culture\"."
    return None


def _log(state, entry: dict) -> None:
    state[LOG_KEY] = list(state.get(LOG_KEY) or []) + [entry]


@safe_tool
def request_replan(rerun: list[str], tool_context: ToolContext, change_note: str = "", reason: str = "") -> dict:
    """Plans an existing trip again after the user asked for a change, reusing the research the change does not
    affect. Call it only when a plan or guide already exists.

    Args:
        rerun: The research teams to run again, any of "transport" (flights, trains, buses, ferries), "stay"
            (hotels and Airbnb), "culture" (sights) and "food" (restaurants, dishes). The others keep their results.
            Use [] when only the days need planning again (e.g. a new day start or pace).
        change_note: The user's wish in one sentence, for the planning team, e.g. "a hotel under 100 EUR a night".
        reason: One short sentence: why this scope.
    """
    from .concierge import PENDING_KEY

    state = tool_context.state
    rerun = [r for r in dict.fromkeys(str(r).strip().lower() for r in (rerun or []))]
    # compared with the brief the research was made for, not the saved one: save_trip_brief may already have run
    base, saved = state.get(PLANNED_KEY), state.get("trip_brief")
    pending = (state.get(PENDING_KEY) or {}).get("brief")
    brief_changes = bool(pending) and not same_brief(pending, saved)
    target = pending if brief_changes else saved
    entry = {"rerun": rerun, "change_note": change_note, "reason": reason}
    problem = scope_problem(state, base, target, rerun)
    if problem:
        _log(state, {**entry, "accepted": False, "refused": problem})
        return error(problem)
    state[SCOPE_KEY] = {**entry, "base": base, "target": target, "invocation_id": tool_context.invocation_id}
    _log(state, {**entry, "accepted": True})
    kept = [b for b in BRANCHES if b not in rerun]
    # In a live recording the concierge confirmed a new pace in words only and then asked for the scope; starting
    # the plan here lost the pace. The brief is always saved next, with every change the user confirmed.
    return ok([{"rerun": rerun, "reused": kept}], next_step=(
        "Accepted. Now call save_trip_brief with all the trip details, including every change the user confirmed "
        "(the same details if no trip detail changes). Planning starts when it is saved, with this scope."))


def scoped_save(state, shown: dict, invocation_id) -> bool:
    """True when save_trip_brief may save without another confirmation: a scope was accepted in this turn and the
    brief keeps the trip (only fields such as the pace, the day hours, the budget or the interests change)."""
    scope = state.get(SCOPE_KEY) or {}
    if scope.get("invocation_id") != invocation_id or not state.get(PLANNED_KEY):
        return False
    return not changed_fields(state.get(PLANNED_KEY), shown) & set(NEW_TRIP_FIELDS + COORD_FIELDS)


def accepted_scope(state, invocation_id=None) -> dict | None:
    """The scope waiting for this plan, if it was accepted in this turn (or for exactly the saved brief) and still
    holds for the brief that was saved. Clears it either way."""
    scope = state.get(SCOPE_KEY)
    state[SCOPE_KEY] = None
    if not scope:
        return None
    brief = state.get("trip_brief")
    this_turn = invocation_id is not None and scope.get("invocation_id") == invocation_id
    if not this_turn and not same_brief(scope.get("target"), brief):
        _log(state, {"rerun": scope.get("rerun"), "accepted": False,
                     "refused": "the saved brief is not the one the scope was checked against: full re-plan"})
        return None
    problem = scope_problem(state, scope.get("base"), brief, scope.get("rerun") or [])
    if problem:
        _log(state, {"rerun": scope.get("rerun"), "accepted": False, "refused": problem})
        return None
    return scope


def note_block(scope: dict | None) -> str | None:
    note = (scope or {}).get("change_note", "").strip()
    if not note:
        return None
    return ("\n\nCHANGE REQUESTED BY THE TRAVELLERS (this plan replaces an earlier one; make sure it meets this "
            f"wish where it concerns your task): {note}")


def skip_branch(branch: str, output_key: str | None = None):
    """Before-agent callback factory for a research branch: skips it when the current plan reuses its research.

    An LlmAgent's before-agent content is written to its output_key, so an agent with one returns the kept value
    (unchanged); a workflow agent returns a short status line."""
    def callback(callback_context):
        state = callback_context.state
        if branch not in (state.get(REUSED_KEY) or []):
            return None
        text = state.get(output_key) if output_key else None
        if not text:
            text = f"Reusing the {branch} research from the previous plan."
        return types.Content(role="model", parts=[types.Part(text=text)])

    callback.__name__ = f"skip_{branch}_if_reused"
    return callback


