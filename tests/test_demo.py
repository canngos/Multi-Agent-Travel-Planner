"""Phase 9 demo mode: LLM answer cache, pinned date, cache-dir switching (no network, no model)."""
from datetime import date

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from travel_planner import config
from travel_planner.llm_cache import CachedLiteLlm, request_key


def _request(*contents, system="You are the PLANNER."):
    return LlmRequest(model="openai/x", contents=list(contents),
                      config=types.GenerateContentConfig(system_instruction=system, temperature=0.2))


def _user(text):
    return types.Content(role="user", parts=[types.Part(text=text)])


def _call(name, call_id):
    return types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(id=call_id, name=name, args={"a": 1}))])


def test_request_key_ignores_ids_other_agents_and_thoughts():
    a = _request(_user("Start."), _user("For context: [food_agent] said: X"), _call("get_forecast", "call_1"))
    b = _request(_user("Start."), _user("For context: [culture_agent] said: Y"), _call("get_forecast", "call_999"),
                 types.Content(role="model", parts=[types.Part(text="thinking...", thought=True)]))
    assert request_key(a) == request_key(b)
    assert request_key(a) != request_key(_request(_user("Start."), system="You are the CRITIC."))


async def test_demo_records_then_replays_offline(tmp_path, monkeypatch):
    calls = []

    async def fake_live(self, llm_request, stream=False):
        calls.append(llm_request)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="plan v1")]))

    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_live)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    llm = CachedLiteLlm(model="openai/x")
    request = _request(_user("Start."))

    monkeypatch.delenv("DEMO", raising=False)
    monkeypatch.delenv("LLM_CACHE", raising=False)
    monkeypatch.delenv("OFFLINE", raising=False)
    [r async for r in llm.generate_content_async(request)]
    assert len(calls) == 1 and not (tmp_path / "llm").exists()  # normal runs never cache LLM answers

    monkeypatch.setenv("DEMO", "1")
    monkeypatch.setenv("DEMO_RECORD", "1")
    recorded = [r async for r in llm.generate_content_async(request)]
    assert len(calls) == 2 and len(list((tmp_path / "llm").glob("*.json"))) == 1

    monkeypatch.delenv("DEMO_RECORD")  # replay: offline, served from the cache
    assert config.offline() and config.today() == config.DEMO_TODAY
    replayed = [r async for r in llm.generate_content_async(request)]
    assert len(calls) == 2 and replayed[0].content.parts[0].text == recorded[0].content.parts[0].text == "plan v1"

    missed = [r async for r in llm.generate_content_async(_request(_user("Something new.")))]
    assert len(calls) == 2 and missed[0].error_code == "LLM_CACHE_MISS"


def test_demo_switches_cache_dir_offline_and_today(monkeypatch):
    monkeypatch.delenv("CACHE_DIR", raising=False)
    monkeypatch.delenv("OFFLINE", raising=False)
    monkeypatch.setenv("DEMO", "1")
    monkeypatch.delenv("DEMO_RECORD", raising=False)
    assert config.cache_dir() == config.DEMO_CACHE_DIR and config.offline() and config.llm_cache_on()
    monkeypatch.setenv("DEMO_RECORD", "1")
    assert not config.offline() and config.cache_dir() == config.DEMO_CACHE_DIR
    monkeypatch.delenv("DEMO")
    monkeypatch.delenv("DEMO_RECORD")
    assert config.today() == date.today() and not config.llm_cache_on()


async def test_recording_survives_a_consumer_that_stops_early(tmp_path, monkeypatch):
    """Regression: after a transfer ADK stops reading the generator; the answer must already be saved."""
    async def fake_live(self, llm_request, stream=False):
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="saved, transferring")]))

    monkeypatch.setattr(LiteLlm, "generate_content_async", fake_live)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("DEMO", "1")
    monkeypatch.setenv("DEMO_RECORD", "1")
    monkeypatch.delenv("OFFLINE", raising=False)  # conftest sets it for the other tests
    gen = CachedLiteLlm(model="openai/x").generate_content_async(_request(_user("Yes, go ahead.")))
    async for _ in gen:
        break  # like ADK after a transfer
    await gen.aclose()
    assert len(list((tmp_path / "llm").glob("*.json"))) == 1
