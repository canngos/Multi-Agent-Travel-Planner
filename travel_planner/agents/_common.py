"""Shared construction for the research agents."""
from google.adk.agents import LlmAgent
from google.genai import types

from ..config import MODEL
from .prompts import BRIEF, GROUNDING, output_format, tool_budget
from .retry import offer_retry_tool, retry_malformed_call


def research_agent(
    *, name: str, description: str, role: str, steps: str, shape: str, example: str, tools: list,
    output_key: str, max_calls: int, **kwargs,
) -> LlmAgent:
    """An LlmAgent that reads {trip_brief}, calls its tools and writes one JSON object to `output_key`.

    include_contents="none": the agent sees only its instruction and its own tool calls of this
    turn, not the concierge chat or other branches (less tokens, no cross-talk in parallel runs).
    """
    instruction = "\n\n".join([role, BRIEF, steps, tool_budget(max_calls), GROUNDING, output_format(shape, example)])
    return LlmAgent(
        name=name,
        model=MODEL,
        description=description,
        instruction=instruction,
        tools=tools,
        output_key=output_key,
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0.2),
        before_model_callback=offer_retry_tool,
        after_model_callback=retry_malformed_call,
        **kwargs,
    )
