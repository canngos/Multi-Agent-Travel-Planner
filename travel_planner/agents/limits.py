"""Tool budgets enforced in code.

Each prompt asks for at most N tool calls, but a model that loops anyway would burn the free quotas (SerpApi
~250 searches a month, Tavily 1,000 credits). After N + SLACK calls in one agent run (one review round for the
critic) every further call is refused with an error telling the model to answer with what it has.
"""
from collections import OrderedDict

from .retry import RETRY_TOOL

SLACK = 2  # room for a retried call
MAX_TRACKED = 512  # recent agent runs; older ones are long finished
_calls: OrderedDict[tuple, int] = OrderedDict()


def limit_tool_calls(max_calls: int, only: tuple[str, ...] | None = None, slack: int = SLACK):
    """Before-tool callback factory: refuses calls beyond max_calls + slack in one agent run (one review round).

    `only` counts just the named tools (e.g. the reviser's on-demand researcher), leaving the agent's other tools
    alone; each limiter keeps its own count."""
    limit = max_calls + slack
    name = f"limit_tool_calls_{max_calls}" + (f"_{'_'.join(only)}" if only else "")

    def callback(tool, args, tool_context):
        tool_name = getattr(tool, "name", "")
        if tool_name == RETRY_TOOL or (only and tool_name not in only):  # the framework's retry is not a real call
            return None
        state = getattr(tool_context, "state", None) or {}
        key = (name, tool_context.invocation_id, tool_context.agent_name, state.get("review_round"))
        _calls[key] = _calls.get(key, 0) + 1
        _calls.move_to_end(key)
        while len(_calls) > MAX_TRACKED:
            _calls.popitem(last=False)
        if _calls[key] <= limit:
            return None
        if only:
            return {"status": "error",
                    "reason": f"{tool_name} may be called at most {limit} time(s) per review round: call it no "
                              "more. Use the places you have, or leave the gap and say so in \"notes\"."}
        return {"status": "error",
                "reason": f"Tool budget used up ({limit} calls): call no more tools. Write your final answer now "
                          "from the results you have, and say in \"notes\" what is missing."}

    callback.__name__ = name
    return callback
