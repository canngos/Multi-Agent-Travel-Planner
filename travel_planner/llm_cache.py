"""LLM response cache, used for the replayable demo (DEMO=1) and when LLM_CACHE=1.

Tool results are cached by the tools themselves; this caches the model's answers, so a recorded conversation
can be replayed without the LLM endpoint: the same user messages produce the same requests, the same
answers, the same tool calls and therefore the same cached tool results.

The key is the agent's request without things that change between runs but not the meaning:
- function-call ids (the provider or ADK makes new ones each run);
- "For context:" messages, i.e. other agents' replies that ADK shows to an agent. Every agent gets the data it
  needs from session state through its instruction, and with parallel branches the order (and so the text) of
  these messages depends on timing;
- reasoning ("thought") parts.
"""
import json
import logging

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_response import LlmResponse

log = logging.getLogger(__name__)
NAMESPACE = "llm"
OTHER_AGENT_PREFIX = "For context:"


def _part(p) -> dict | None:
    if getattr(p, "thought", None):
        return None
    if p.text is not None:
        return {"text": p.text}
    if p.function_call is not None:
        return {"call": p.function_call.name, "args": p.function_call.args}
    if p.function_response is not None:
        return {"result": p.function_response.name, "response": p.function_response.response}
    return None


def request_key(llm_request) -> dict:
    contents = []
    for c in llm_request.contents or []:
        parts = [x for x in (_part(p) for p in c.parts or []) if x]
        if c.role == "user" and parts and str(parts[0].get("text", "")).startswith(OTHER_AGENT_PREFIX):
            continue
        if parts:
            contents.append({"role": c.role, "parts": parts})
    config = llm_request.config
    tools = sorted(fd.name for t in (getattr(config, "tools", None) or [])
                   for fd in (getattr(t, "function_declarations", None) or []))
    return {"model": llm_request.model, "system": str(getattr(config, "system_instruction", "") or ""),
            "tools": tools, "temperature": getattr(config, "temperature", None), "contents": contents}


class CachedLiteLlm(LiteLlm):
    """LiteLlm that reads and writes the LLM cache when caching is on (see config.llm_cache_on)."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        from . import config
        from .tools import _cache

        if not config.llm_cache_on():
            async for response in super().generate_content_async(llm_request, stream=stream):
                yield response
            return
        key = request_key(llm_request)
        hit, stored = _cache.read(NAMESPACE, key)
        if hit:
            for item in stored:
                yield LlmResponse.model_validate(item)
            return
        if config.offline():
            log.warning("LLM cache miss in offline mode (agent instruction starts: %.80s)", key["system"])
            _cache.write(NAMESPACE + "_misses", key, None)  # the request, to diff against the recorded ones
            yield LlmResponse(error_code="LLM_CACHE_MISS",
                              error_message="Offline demo mode: this request was not recorded. Replay the demo "
                                            "with exactly the recorded messages, or turn off DEMO/OFFLINE.")
            return
        final = []
        async for response in super().generate_content_async(llm_request, stream=stream):
            if not response.partial:
                final.append(response.model_dump(mode="json", exclude_none=True))
                if not any(r.get("error_code") for r in final):
                    # write BEFORE yielding: after a transfer or exit_loop ADK may stop reading this generator,
                    # and code after the loop would never run (the first recording lost the concierge's save turn)
                    _cache.write(NAMESPACE, key, final)
            yield response


def dump_key(llm_request) -> str:  # debugging aid
    return json.dumps(request_key(llm_request), ensure_ascii=False, indent=1, default=str)
