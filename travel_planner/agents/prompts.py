"""Instruction blocks shared by the research agents.

ADK fills `{trip_brief}` from session state. Any other {...} in an instruction is left
alone unless its content is a plain identifier, so JSON examples are safe here.
"""

BRIEF = """TRIP BRIEF (from the session): {trip_brief}
Dates are ISO (YYYY-MM-DD). "travellers" is the number of adults. "nights" = end_date - start_date."""

GROUNDING = """GROUNDING RULES (strict):
- Every place, price, time and link you report must come from a tool result in this conversation.
  Never invent names, prices, opening hours or URLs, and never fill gaps from memory.
- Keep each item's url / source_urls exactly as the tool returned them.
- Keep price_source as the tool gave it: "live" (booking/search sites) or "web_estimate" (read from a web page via web_search).
  A price you read from a web_search result is always "web_estimate".
- If a tool returns {"status": "error"}, read its "reason", then use the fallback described below.
  Mention every failed source in "notes".
- If no tool gives you something, leave it out (or null) and say so in "notes"."""

TOOL_BUDGET = """TOOL USE: call tools one at a time and keep it short: at most {max_calls} tool calls in total.
Do not repeat a call with the same arguments."""

# Appended to every planning agent's instruction. save_trip_brief fills it with the travellers' hard requirements
# (excluded transport modes, children, pets, diet, rooms for a group); it renders as "" for an ordinary trip.
# On a re-plan, the user's change request follows (replan.note_block; "" otherwise).
REQUIREMENTS = "{trip_requirements?}{plan_change_note?}"


def output_format(shape: str, example: str) -> str:
    return f"""OUTPUT: when you are done, reply with ONLY one JSON object inside a ```json fence, nothing before or after it.
Shape: {shape}
Example (values are illustrative only):
```json
{example}
```
Use double quotes, no comments, no trailing commas. "notes" is one short paragraph: which sources worked or failed,
fallbacks used, caveats (e.g. estimated prices). Write it in English."""


def tool_budget(max_calls: int) -> str:
    return TOOL_BUDGET.replace("{max_calls}", str(max_calls))
