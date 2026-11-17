"""guide_writer: writes the final Markdown guide and saves it with save_guide -> final_guide.

save_guide stores the full guide (with the attribution footer) in state["final_guide"] and the file paths in
state["guide_paths"]. The agent's own short reply goes to "guide_reply". If the model writes the guide as its
reply instead of calling save_guide, the after-agent callback saves that reply, so a guide file always exists.
Either way, every link is grounded first (grounding.py).
"""
from google.adk.agents import LlmAgent
from google.genai import types

from ..config import MODEL
from ..tools import save_guide
from .grounding import RESEARCH_KEYS, ground_guide_before_save, ground_markdown, known_urls
from .prompts import BRIEF
from .retry import offer_retry_tool, retry_malformed_call

ROLE = """You are the GUIDE WRITER of a trip-planning team. Write the final trip guide for the travellers in
Markdown, from the plan and research below, then save it with save_guide."""

INPUTS = """THE PLAN (itinerary, JSON): {itinerary?}

TRANSPORT SUMMARY: {transport_summary?}

STAY SUMMARY: {stay_summary?}

SIGHTS with history (culture_findings): {culture_findings?}

FOOD (food_findings): {food_findings?}"""

STRUCTURE = """GUIDE STRUCTURE (Markdown, English, friendly and compact):
# <Destination> trip guide: <start date> – <end date>
One short paragraph: who travels (travellers, origin), budget tier and total budget, the interests, and
whether the weather is a forecast or only typical for these dates.

## Getting there and back
The chosen outbound and return (from the plan) first, then the cheapest and fastest alternatives from the
transport summary, as one table per direction:
| | Mode | Operator | Depart → Arrive | Duration | Stops | Price (all travellers) | Link |
Then any warning from the options' details (airport change, self-transfer, bags, price per person).

## Where to stay
The chosen stay with price per night, total, rating (say the scale) and why. Then a table of the stay
summary's by_area: | Area | Airbnb | Hotel | From €/night |, each name a link. Then the recommendation and
any caveats (e.g. liteAPI rates may be below public prices, city tax not included).

## Day by day
For each day: "### Day N – <weekday> <date>: <area>" and a line with the weather and walking distance.
Then a bullet per item in time order: **HH:MM Name** – what to see or eat. For sights add 1-2 sentences of
history from the SIGHTS summary, the hours, the item's "ticket" text copied word for word (e.g. "ticket €15",
"ticket price: see the website") and a [source] link. For restaurants add what to order and the budget tier.

## Food to try
One bullet for EVERY entry in FOOD's "dishes" list: **name** – its description – [source](its source_url).

## Cost estimate
A table from the plan's "cost" result: | Category | Total (€) | Share |, then total, per person, per person per
day, and the budget comparison (within budget or by how much over). Take every figure from "cost" only, never
from "notes" (code recomputed the cost; notes may be out of date). List the cost assumptions (typical food
and local transport costs) right under the table.

## Good to know
Short bullets: failed sources or fallbacks from the agents' notes, prices to double-check, closed days."""

RULES = """RULES:
- Use ONLY facts, prices, hours and links from the inputs above. Never add places, prices or links from memory.
- Mark every transport and stay price with its source: "€297 (live)" when price_source is "live", "€34 (web
  estimate)" when it is "web_estimate". Sight tickets come from OpenStreetMap or the sights' own pages: give
  them without a marker.
- Use each item's own url / source_url for links, copied character for character (keep "http://" as it is);
  never shorten, "fix" or invent URLs. Write links as [text](url): no quotes or spaces inside the brackets.
- Keep it scannable: tables for comparisons, bullets for days, no long paragraphs."""

SAVE = """WHEN THE GUIDE IS READY:
1. Call save_guide(markdown_text=<the whole guide>, filename_slug="<destination>-<start_date>-to-<end_date>"),
   e.g. "lisbon-2027-05-10-to-2027-05-14". Call it exactly once; do not write the guide anywhere else.
2. Then reply in 3-5 short lines: where the guide was saved (markdown_path and html_path from the tool
   result), the recommended transport and stay with the total cost, and whether it fits the budget.
If save_guide returns an error, fix what the reason says and call it again (at most 2 tries)."""


def _slug(brief: dict) -> str:
    return f"{brief.get('destination', 'trip')}-{brief.get('start_date', '')}-to-{brief.get('end_date', '')}"


def save_reply_if_not_saved(callback_context):
    """Safety net: if the model wrote the guide as its reply instead of calling save_guide, save the reply."""
    state = callback_context.state
    reply = state.get("guide_reply") or ""
    if state.get("final_guide") or reply.count("\n## ") < 2:
        return None
    reply, _, _ = ground_markdown(reply, known_urls(state, RESEARCH_KEYS + ("itinerary",)))
    result = save_guide(reply, _slug(state.get("trip_brief") or {}), callback_context)
    if result.get("status") != "ok":
        return None
    paths = result["results"][0]
    return types.Content(role="model", parts=[types.Part(
        text=f"Saved the guide to {paths['markdown_path']} and {paths['html_path']}.")])


def build_guide_writer() -> LlmAgent:
    instruction = "\n\n".join([ROLE, BRIEF, INPUTS, STRUCTURE, RULES, SAVE])
    return LlmAgent(
        name="guide_writer",
        model=MODEL,
        description="Writes the final trip guide and saves it as Markdown and HTML.",
        instruction=instruction,
        tools=[save_guide],
        output_key="guide_reply",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0.3),
        before_tool_callback=ground_guide_before_save,  # links in the guide must come from the research
        after_agent_callback=save_reply_if_not_saved,
        before_model_callback=offer_retry_tool,
        after_model_callback=retry_malformed_call,
    )
