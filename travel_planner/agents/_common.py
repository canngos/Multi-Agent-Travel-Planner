"""Shared construction for the research agents."""
from google.adk.agents import LlmAgent
from google.genai import types

from ..config import MODEL
from .grounding import ground_research_output
from .limits import limit_tool_calls
from .prompts import BRIEF, GROUNDING, REQUIREMENTS, output_format, tool_budget
from .retry import offer_retry_tool, retry_malformed_call


def research_agent(
    *, name: str, description: str, role: str, steps: str, shape: str, example: str, tools: list,
    output_key: str, max_calls: int, before_grounding: list | None = None, strict_grounding: bool = False, **kwargs,
) -> LlmAgent:
    """An LlmAgent that reads {trip_brief}, calls its tools and writes one JSON object to `output_key`.

    include_contents="none": the agent sees only its instruction and its own tool calls of this
    turn, not the concierge chat or other branches (less tokens, no cross-talk in parallel runs).
    """
    instruction = "\n\n".join([role, BRIEF, steps, tool_budget(max_calls), GROUNDING, output_format(shape, example)]) \
        + REQUIREMENTS
    # the prompt's tool budget, enforced in code before any agent-specific tool callback (e.g. the MCP cache)
    before_tool = [limit_tool_calls(max_calls)] + ([kwargs.pop("before_tool_callback")]
                                                   if "before_tool_callback" in kwargs else [])
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
        # e.g. filling gaps from the tool results, then: links only from tool results
        after_agent_callback=[*(before_grounding or []), ground_research_output(output_key, strict_grounding)],
        before_tool_callback=before_tool,
        **kwargs,
    )
