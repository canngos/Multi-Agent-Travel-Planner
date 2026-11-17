import json
import time

import httpx
import pytest

from travel_planner.tools import _cache, _http
from travel_planner.tools._cache import CacheMiss
from travel_planner.tools._result import ToolError, safe_tool

URL = "https://api.example.com/things"


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr(_http.asyncio, "sleep", instant)


async def test_miss_fetches_then_hit_serves_from_disk(tmp_cache, mock_http):
    mock_http.handler = lambda req: mock_http.json_response({"n": 1})
    assert await _http.get_json(URL, namespace="ex", params={"q": "lisbon"}) == {"n": 1}
    assert await _http.get_json(URL, namespace="ex", params={"q": "lisbon"}) == {"n": 1}
    assert len(mock_http.calls) == 1  # second call came from cache
    files = list((tmp_cache / "ex").glob("*.json"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text(encoding="utf-8"))
    assert entry["request"]["params"] == {"q": "lisbon"} and entry["response"] == {"n": 1}


async def test_different_params_are_different_entries(tmp_cache, mock_http):
    await _http.get_json(URL, namespace="ex", params={"q": "a"})
    await _http.get_json(URL, namespace="ex", params={"q": "b"})
    assert len(mock_http.calls) == 2


async def test_sends_user_agent(tmp_cache, mock_http, monkeypatch):
    monkeypatch.setenv("APP_USER_AGENT", "TestAgent/1.0")
    monkeypatch.setenv("CONTACT_EMAIL", "dev@example.org")
    await _http.get_json(URL, namespace="ex")
    assert mock_http.calls[0].headers["User-Agent"] == "TestAgent/1.0 (dev@example.org)"


async def test_secrets_are_sent_but_not_hashed_or_stored(tmp_cache, mock_http):
    await _http.get_json(URL, namespace="ex", params={"q": "x", "apikey": "SECRET1"}, headers={"X-API-Key": "SECRET2"})
    assert mock_http.calls[0].url.params["apikey"] == "SECRET1"
    assert mock_http.calls[0].headers["X-API-Key"] == "SECRET2"
    text = next((tmp_cache / "ex").glob("*.json")).read_text(encoding="utf-8")
    assert "SECRET" not in text
    # Same request with someone else's key is a cache hit.
    await _http.get_json(URL, namespace="ex", params={"q": "x", "apikey": "OTHER"})
    assert len(mock_http.calls) == 1


async def test_offline_miss_raises_and_never_calls_network(tmp_cache, mock_http, monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    with pytest.raises(CacheMiss):
        await _http.get_json(URL, namespace="ex")
    assert mock_http.calls == []


async def test_offline_hit_works_even_if_stale(tmp_cache, mock_http, monkeypatch):
    await _http.get_json(URL, namespace="ex")
    path = next((tmp_cache / "ex").glob("*.json"))
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["saved_at"] = time.time() - 10_000
    path.write_text(json.dumps(entry), encoding="utf-8")
    monkeypatch.setenv("OFFLINE", "1")
    assert await _http.get_json(URL, namespace="ex", max_age_s=60) == {"ok": True}


async def test_max_age_refetches_when_online(tmp_cache, mock_http):
    await _http.get_json(URL, namespace="ex")
    path = next((tmp_cache / "ex").glob("*.json"))
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["saved_at"] = time.time() - 10_000
    path.write_text(json.dumps(entry), encoding="utf-8")
    await _http.get_json(URL, namespace="ex", max_age_s=60)
    assert len(mock_http.calls) == 2


async def test_refresh_skips_read_but_rewrites(tmp_cache, mock_http, monkeypatch):
    mock_http.handler = lambda req: mock_http.json_response({"v": 1})
    await _http.get_json(URL, namespace="ex")
    monkeypatch.setenv("REFRESH_CACHE", "1")
    mock_http.handler = lambda req: mock_http.json_response({"v": 2})
    assert await _http.get_json(URL, namespace="ex") == {"v": 2}
    monkeypatch.setenv("REFRESH_CACHE", "0")
    assert await _http.get_json(URL, namespace="ex") == {"v": 2}
    assert len(mock_http.calls) == 2


async def test_retries_on_503_then_succeeds(tmp_cache, mock_http):
    replies = iter([httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"ok": 1})])
    mock_http.handler = lambda req: next(replies)
    assert await _http.get_json(URL, namespace="ex") == {"ok": 1}
    assert len(mock_http.calls) == 3


async def test_error_status_raises_and_is_not_cached(tmp_cache, mock_http):
    mock_http.handler = lambda req: httpx.Response(404, text="not here")
    with pytest.raises(ToolError, match="HTTP 404"):
        await _http.get_json(URL, namespace="ex")
    assert not (tmp_cache / "ex").exists()


async def test_persistent_429_gives_up(tmp_cache, mock_http):
    mock_http.handler = lambda req: httpx.Response(429)
    with pytest.raises(ToolError, match="HTTP 429"):
        await _http.get_json(URL, namespace="ex")
    assert len(mock_http.calls) == _http.MAX_RETRIES + 1


async def test_network_error_becomes_tool_error(tmp_cache, mock_http):
    def boom(req):
        raise httpx.ConnectError("no route")

    mock_http.handler = boom
    with pytest.raises(ToolError, match="network error"):
        await _http.get_json(URL, namespace="ex")


async def test_non_json_body_is_tool_error(tmp_cache, mock_http):
    mock_http.handler = lambda req: httpx.Response(200, text="<html>")
    with pytest.raises(ToolError, match="not JSON"):
        await _http.get_json(URL, namespace="ex")


async def test_text_response_and_string_body(tmp_cache, mock_http):
    mock_http.handler = lambda req: httpx.Response(200, text=f"got {req.content.decode()}")
    assert await _http.fetch("POST", URL, namespace="ex", data="[out:json];", response="text") == "got [out:json];"


async def test_safe_tool_turns_http_failure_into_error_dict(tmp_cache, mock_http):
    mock_http.handler = lambda req: httpx.Response(500)

    @safe_tool
    async def my_tool(q: str) -> dict:
        return await _http.get_json(URL, namespace="ex", params={"q": q})

    result = await my_tool("x")
    assert result["status"] == "error" and "HTTP 500" in result["reason"]


async def test_rate_limit_spaces_requests(tmp_cache, mock_http, monkeypatch):
    slept = []

    async def record(seconds):
        slept.append(seconds)

    monkeypatch.setattr(_http.asyncio, "sleep", record)
    monkeypatch.setitem(_http.MIN_INTERVAL_S, "api.example.com", 1.0)
    monkeypatch.setattr(_http, "_next_slot", {})
    for q in "abc":
        await _http.get_json(URL, namespace="ex", params={"q": q})
    assert len(slept) == 2 and all(0.9 < s <= 2.0 for s in slept)


async def test_cached_helper_for_non_http_producers(tmp_cache, monkeypatch):
    monkeypatch.setenv("OFFLINE", "0")
    calls = []

    async def produce():
        calls.append(1)
        return [{"flight": "HEL-LIS"}]

    req = {"from": "HEL", "to": "LIS"}
    assert await _cache.cached("lib", req, produce) == [{"flight": "HEL-LIS"}]
    assert await _cache.cached("lib", req, produce) == [{"flight": "HEL-LIS"}]
    assert calls == [1]


def test_unicode_round_trip(tmp_cache):
    """GPT OSS output contains characters that Windows cp1252 cannot encode."""
    _cache.write("u", {"k": 1}, {"text": "horse‑drawn\u202fTram 28 — São Jorge"})
    assert _cache.read("u", {"k": 1}) == (True, {"text": "horse‑drawn\u202fTram 28 — São Jorge"})


async def test_slim_is_applied_before_caching(tmp_cache, mock_http):
    mock_http.handler = lambda req: mock_http.json_response({"keep": 1, "huge": "x" * 10_000})
    slim = lambda body: {"keep": body["keep"]}  # noqa: E731
    assert await _http.get_json(URL, namespace="ex", slim=slim) == {"keep": 1}
    stored = next((tmp_cache / "ex").glob("*.json")).read_text(encoding="utf-8")
    assert "huge" not in stored
    assert await _http.get_json(URL, namespace="ex", slim=slim) == {"keep": 1}  # served slim from cache
    assert len(mock_http.calls) == 1
