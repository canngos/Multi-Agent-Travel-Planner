"""Retry after a malformed tool call (GPT OSS sometimes writes tool arguments that are not valid JSON).

ADK's LiteLlm adapter drops such a call and marks the response MALFORMED_FUNCTION_CALL; the agent then ends
without output (a live planner run ended this way and left no itinerary). These two callbacks give the model
another chance, the same way ADK's ReflectAndRetryModelPlugin does, but per agent instead of per runner:

- `offer_retry_tool` (before-model) makes the hidden `tool_call_failed` tool executable. It is added to the
  request's tools_dict only, so it is never declared to the model;
- `retry_malformed_call` (after-model) replaces the failed response with a call to that tool. ADK runs it and
  the model receives its reply: "your call was not run, call it again with valid JSON". At most
  MAX_RETRIES per agent run (per review round for the critic and the itinerary writer, which run up to four times in one
  invocation).
"""
import uuid
from collections import OrderedDict

from google.adk.models.llm_response import LlmResponse
from google.adk.tools.function_tool import FunctionTool
from google.genai import types

RETRY_TOOL = "tool_call_failed"
MAX_RETRIES = 2
MAX_TRACKED = 512  # recent agent runs whose retries are counted; older ones are long finished
_counts: OrderedDict[tuple, int] = OrderedDict()


def tool_call_failed(tool_names: str, attempt: int) -> dict:
    """Reserved for the framework. Never call this yourself."""
    return {"status": "error",
            "reason": f"Your call to {tool_names} was NOT run: its arguments were not valid JSON. Call it again with "
                      "valid JSON arguments (double quotes, no comments, no trailing commas, numbers without units), "
                      f"or continue without it. Attempt {attempt} of {MAX_RETRIES}."}


_TOOL = FunctionTool(func=tool_call_failed)


def offer_retry_tool(callback_context, llm_request):
    """Before-model callback: makes the hidden retry tool callable, and lets a tool be called with "_" for "-" or
    the other way round (GPT OSS called Kiwi's "search-flight" as "search_flight"). Aliases are only added to
    the lookup table, never declared to the model."""
    tools = llm_request.tools_dict
    for name, tool in list(tools.items()):
        tools.setdefault(name.replace("-", "_"), tool)
        tools.setdefault(name.replace("_", "-"), tool)
    tools[RETRY_TOOL] = _TOOL
    return None


def _key(callback_context) -> tuple:
    state = getattr(callback_context, "state", None) or {}
    return callback_context.invocation_id, callback_context.agent_name, state.get("review_round")


def retry_malformed_call(callback_context, llm_response):
    if llm_response.finish_reason != types.FinishReason.MALFORMED_FUNCTION_CALL or not llm_response.error_code:
        return None
    key = _key(callback_context)
    _counts[key] = _counts.get(key, 0) + 1
    _counts.move_to_end(key)
    while len(_counts) > MAX_TRACKED:
        _counts.popitem(last=False)
    if _counts[key] > MAX_RETRIES:
        return None
    message = llm_response.error_message or ""
    names = message.split(": ", 1)[1] if ": " in message else "a tool"
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
        id=f"retry-{uuid.uuid4().hex[:12]}", name=RETRY_TOOL, args={"tool_names": names, "attempt": _counts[key]}))]))
