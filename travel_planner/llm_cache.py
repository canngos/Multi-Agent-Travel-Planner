"""LLM response cache, used for the replayable demo (DEMO=1) and when LLM_CACHE=1.

Tool results are cached by the tools themselves; this caches the model's answers, so a recorded conversation
can be replayed without the LLM endpoint: the same user messages produce the same requests, the same
answers, the same tool calls and therefore the same cached tool results.

The key is the agent's request without things that change between runs but not the meaning:
- function-call ids (the provider or ADK makes new ones each run);
- "For context:" messages, i.e. other agents' replies that ADK shows to an agent. Every agent gets the data it
  needs from session state through its instruction, and with parallel branches the order (and so the text) of
  these messages depends on timing;
- reasoning ("thought") parts;
- where the project lives: the repository folder in file paths (save_guide's result, the concierge's view of the
  saved guide) becomes "<repo>", so a clone in another folder still matches the recording. A guide folder set
  with OUTPUT_DIR counts as "<repo>/output" for the same reason.
"""
import asyncio
import json
import logging
import os
from pathlib import Path

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_response import LlmResponse

log = logging.getLogger(__name__)
NAMESPACE = "llm"
OTHER_AGENT_PREFIX = "For context:"
MISSES = 0  # LLM cache misses in offline mode since the process started (run_trip.py reports them)


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
    key = {"model": llm_request.model, "system": str(getattr(config, "system_instruction", "") or ""),
           "tools": tools, "temperature": getattr(config, "temperature", None), "contents": contents}
    return without_repo_path(key)


def without_repo_path(key: dict) -> dict:
    """The key with every spelling of the repository folder replaced by "<repo>" (and of the guide folder by
    "<repo>/output")."""
    from . import config

    text = json.dumps(key, ensure_ascii=False, default=str)
    output, default_output = Path(config.OUTPUT_DIR), config.REPO_ROOT / "output"
    if output.resolve() != default_output.resolve():
        for there, here in zip(_spellings(output), _spellings(default_output)):
            text = text.replace(there, here)
    for spelling in sorted(set(_spellings(config.REPO_ROOT)), key=len, reverse=True):
        text = text.replace(spelling, "<repo>")  # longest first: the doubled form contains no plain one
    return json.loads(text)


def _spellings(path: Path) -> list[str]:
    """A path as it appears inside JSON text: doubled backslashes (a path printed inside a Python dict), plain,
    and with slashes."""
    plain = str(path)
    return [json.dumps(s)[1:-1] for s in (plain.replace("\\", "\\\\"), plain, path.as_posix())]


def replay_delay(item: dict) -> float:
    """Seconds to wait before a replayed answer: DEMO_PACE x an estimated real latency (0 = instant, default).

    The estimate uses the recorded token counts (GPT OSS on Lehmus: roughly 1 s overhead, ~80 output tokens/s,
    ~4000 prompt tokens/s), so a paced replay shows agents working in parallel like the live run did.
    """
    try:
        pace = float(os.environ.get("DEMO_PACE") or 0)
    except ValueError:
        pace = 0.0
    if pace <= 0:
        return 0.0
    usage = item.get("usage_metadata") or {}
    seconds = 1.0 + (usage.get("candidates_token_count") or 0) / 80 + (usage.get("prompt_token_count") or 0) / 4000
    return pace * seconds


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
                await asyncio.sleep(replay_delay(item))
                yield LlmResponse.model_validate(item)
            return
        if config.offline():
            global MISSES
            MISSES += 1
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
