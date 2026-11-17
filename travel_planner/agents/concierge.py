"""concierge (root): chats until the trip brief is complete, saves it, then hands over to planning_pipeline.

The instruction is built per turn (today's date for resolving "10-14 May", and the finished guide for
follow-up questions). The concierge sees the whole chat, but the pipeline's agents post dozens of tool calls and
JSON results into the same session; `hide_pipeline_events` drops those from its prompt so follow-up turns stay
small. The guide itself reaches the concierge through the instruction instead.
"""
from datetime import date

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.genai import types

from ..config import MODEL
from ..tools import geocode_place, save_trip_brief

OTHER_AGENT_PREFIX = "For context:"  # how ADK presents another agent's events to the current agent

ROLE = """You are the CONCIERGE of a trip-planning service. You talk with the user, collect the trip details,
then hand over to the planning team. You never plan the trip, suggest places or quote prices yourself."""

COLLECT = """TRIP DETAILS TO COLLECT
- destination: one city (required)
- start_date and end_date (required). Resolve dates like "10-14 May" to ISO YYYY-MM-DD; if the user gives no year,
  use the next date in the future. Trips of 2-10 days work best (3, 5 or 7 are typical); "5 days" from 10 May
  means 10-14 May.
- origin: the departure city (default Helsinki; ask once, accept the default if the user does not care)
- travellers: number of adults (default 2)
- budget_tier: "low", "mid" or "high" (default "mid"); total_budget_eur for the whole group if the user has one
- interests: e.g. history, street food, viewpoints, museums, nightlife (optional)

HOW TO WORK
1. Ask for the missing required details in ONE short message (do not ask one question at a time).
2. When destination, dates and origin are known, call geocode_place for the destination and for the origin.
   If a place is not found, ask the user to rephrase.
3. Call save_trip_brief with all details, including origin_lat/origin_lon, destination_lat/destination_lon and
   destination_country from geocode_place. The first call only checks the details and answers
   "needs_confirmation": then show a short summary (destination with country, dates and number of days, origin,
   travellers, budget, interests) and ask the user to confirm. Stop and wait for their answer.
4. When the user confirms, call save_trip_brief again with the same details: now it saves them. If the user
   changes something, call it with the new details (that asks for confirmation again). If it returns an error,
   fix the fields it lists (ask the user if needed).
5. When save_trip_brief returns "ok", tell the user in one sentence that planning starts and takes a few minutes,
   then call transfer_to_agent(agent_name="planning_pipeline"). Do not write anything after the transfer."""

AFTER = """AFTER A GUIDE EXISTS
Answer questions about the trip ONLY from the guide below; if the guide does not say, say so. If the user wants
a different trip or changes (other dates, budget, destination), confirm the new details, save the new brief with
save_trip_brief and transfer to planning_pipeline again."""


def concierge_instruction(ctx: ReadonlyContext) -> str:
    parts = [ROLE, f"Today is {date.today().isoformat()} ({date.today():%A}).", COLLECT, AFTER]
    brief = ctx.state.get("trip_brief")
    if brief:
        parts.append(f"CURRENT SAVED TRIP BRIEF: {brief}")
    guide = ctx.state.get("final_guide")
    paths = ctx.state.get("guide_paths")
    if guide:
        parts.append(f"THE FINISHED GUIDE (saved to {paths}):\n{guide}")
    else:
        parts.append("No guide has been made yet in this session.")
    return "\n\n".join(parts)


def hide_pipeline_events(callback_context, llm_request):
    """Before-model callback: keep only the user's and the concierge's own turns in the prompt."""
    llm_request.contents = [
        c for c in llm_request.contents
        if not (c.role == "user" and c.parts and (c.parts[0].text or "").startswith(OTHER_AGENT_PREFIX))
    ]
    return None


PENDING_KEY = "concierge_pending_brief"
CORE_FIELDS = ("destination", "start_date", "end_date", "origin", "travellers", "budget_tier", "total_budget_eur")


def _core(args: dict) -> dict:
    return {k: args.get(k) for k in CORE_FIELDS}


def require_confirmation(tool, args, tool_context):
    """Before-tool callback: save_trip_brief only saves details the user saw and confirmed in a later turn.

    The first call of a turn stores the details as pending and answers "needs_confirmation". A call in a
    later invocation (the user has replied) with the same core details goes through.
    """
    if getattr(tool, "name", "") != "save_trip_brief":
        return None
    pending = tool_context.state.get(PENDING_KEY) or {}
    if pending and pending.get("invocation_id") != tool_context.invocation_id and pending.get("core") == _core(args):
        tool_context.state[PENDING_KEY] = None
        return None
    tool_context.state[PENDING_KEY] = {"invocation_id": tool_context.invocation_id, "core": _core(args)}
    return {"status": "needs_confirmation",
            "reason": "Not saved yet. Show the user a short summary of these details and ask them to confirm. "
                      "When they confirm in their next message, call save_trip_brief again with the same details."}


def build_concierge(planning_pipeline) -> LlmAgent:
    return LlmAgent(
        name="concierge",
        model=MODEL,
        description="Talks with the user, collects and saves the trip brief, then starts the planning pipeline.",
        instruction=concierge_instruction,
        tools=[geocode_place, save_trip_brief],
        sub_agents=[planning_pipeline],
        before_model_callback=hide_pipeline_events,
        before_tool_callback=require_confirmation,
        generate_content_config=types.GenerateContentConfig(temperature=0.3),
    )
