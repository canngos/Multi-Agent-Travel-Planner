"""All REST calls go through fetch(): User-Agent, per-host rate limit, retries, disk cache.

Only successful (2xx) responses are cached. Failures raise ToolError, which
safe_tool turns into {"status": "error", ...} for the agent.
"""
import asyncio
import logging
import os
import threading
import time
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

import httpx

from .. import config
from . import _cache
from ._result import ToolError

log = logging.getLogger(__name__)

# Minimum seconds between requests per host (API usage policies).
MIN_INTERVAL_S: dict[str, float] = {
    "nominatim.openstreetmap.org": 1.0,
    "overpass-api.de": 1.0,
    "maps.mail.ru": 1.0,
    "api.transitous.org": 1.0,  # policy: stay well under 720 /plan requests a day
    "api.opentripmap.com": 0.25,
}
TIMEOUT_S = 30.0
MAX_RETRIES = 2
RETRY_STATUS = {429, 500, 502, 503, 504}

# Tests replace this with httpx.MockTransport. None = real network.
TRANSPORT: httpx.AsyncBaseTransport | None = None

_rate_lock = threading.Lock()
_next_slot: dict[str, float] = {}
# At most this many requests in flight per host, across every session of this process (the web UI serves several
# users at once). Overpass gives an IP about 2 query slots; with 5 plans at once the live stress test got 502/429s.
MAX_IN_FLIGHT = {"overpass-api.de": 2, "nominatim.openstreetmap.org": 1, "maps.mail.ru": 2}
_in_flight = {host: threading.BoundedSemaphore(n) for host, n in MAX_IN_FLIGHT.items()}


async def _acquire(host: str) -> threading.BoundedSemaphore | None:
    """Waits for a free slot for the host (polling, so it works across event loops and threads)."""
    sem = _in_flight.get(host)
    if sem is None:
        return None
    while not sem.acquire(blocking=False):
        await asyncio.sleep(0.2)
    return sem


async def _wait_for_slot(host: str) -> None:
    """Spaces requests to a host. Uses a thread lock plus asyncio.sleep, so it is
    independent of any particular event loop (pytest creates one per test)."""
    interval = MIN_INTERVAL_S.get(host)
    if not interval:
        return
    with _rate_lock:
        now = time.monotonic()
        slot = max(now, _next_slot.get(host, 0.0))
        _next_slot[host] = slot + interval
    if slot > now:
        await asyncio.sleep(slot - now)


async def _send(method: str, url: str, *, timeout_s: float = TIMEOUT_S, retries: int = MAX_RETRIES, **kwargs: Any) -> httpx.Response:
    host = urlsplit(url).hostname or ""
    headers = {"User-Agent": config.user_agent(), **(kwargs.pop("headers", None) or {})}
    async with httpx.AsyncClient(timeout=timeout_s, transport=TRANSPORT, follow_redirects=True) as client:
        for attempt in range(retries + 1):
            sem = await _acquire(host)
            await _wait_for_slot(host)
            try:
                resp = await client.request(method, url, headers=headers, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == retries:
                    kind = "timed out" if isinstance(exc, httpx.TimeoutException) else "network error"
                    raise ToolError(f"{host}: {kind} after {attempt + 1} tries ({timeout_s:.0f}s timeout) {exc}".rstrip()) from exc
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            finally:
                if sem:
                    sem.release()
            if resp.status_code in RETRY_STATUS and attempt < retries:
                retry_after = resp.headers.get("Retry-After", "")
                backoff = (2.0 if resp.status_code == 429 else 0.5) * 2**attempt  # "too many requests": wait longer
                delay = min(float(retry_after), 10.0) if retry_after.isdigit() else backoff
                log.info("%s %s -> %s, retrying in %.1fs", method, host, resp.status_code, delay)
                await asyncio.sleep(delay)
                continue
            if resp.is_error:
                raise ToolError(f"{host}: HTTP {resp.status_code}: {resp.text[:200]}")
            return resp
    raise AssertionError("unreachable")


async def fetch(
    method: str,
    url: str,
    *,
    namespace: str,
    params: dict | None = None,
    json: Any = None,
    data: dict | str | None = None,
    headers: dict | None = None,
    response: Literal["json", "text"] = "json",
    max_age_s: float | None = None,
    timeout_s: float = TIMEOUT_S,
    retries: int = MAX_RETRIES,
    slim: Callable[[Any], Any] | None = None,
    ttl_for: Callable[[Any], float | None] | None = None,
) -> Any:
    """Cached HTTP request. Returns the parsed JSON body (or text).

    namespace: cache subfolder, one per API (e.g. "nominatim").
    slim: optional function that shrinks the body to the fields the tool uses,
          applied before caching (keeps cache files and the demo cache small).
    ttl_for: optional function giving a (slimmed) body a shorter cache life than max_age_s.
    Secret params/headers are sent but never hashed or stored.
    """
    cache_request = {
        "method": method.upper(),
        "url": url,
        "params": _cache.redact(params),
        "json": json,
        "data": data,
        "response": response,
    }

    async def produce() -> Any:
        body = {"content": data} if isinstance(data, str) else {"data": data}
        resp = await _send(method, url, params=params, json=json, headers=headers, timeout_s=timeout_s, retries=retries, **body)
        if response == "text":
            parsed = resp.text
        else:
            try:
                parsed = resp.json()
            except ValueError as exc:
                raise ToolError(f"{urlsplit(url).hostname}: response is not JSON: {resp.text[:200]}") from exc
        return slim(parsed) if slim else parsed

    return await _cache.cached(namespace, cache_request, produce, max_age_s, ttl_for)


def api_key(name: str) -> str:
    """The API key from .env. In offline mode a missing key is fine: requests are served from the cache only, and
    keys are never part of a cache key (headers are not hashed, secret parameters are redacted), so a fresh clone
    without .env replays the recorded responses."""
    key = os.environ.get(name)
    if key:
        return key
    if config.offline():
        return "offline-no-key"
    raise ToolError(f"{name} is not set in .env")


async def get_json(url: str, *, namespace: str, params: dict | None = None, **kwargs: Any) -> Any:
    return await fetch("GET", url, namespace=namespace, params=params, **kwargs)


async def post_json(url: str, *, namespace: str, **kwargs: Any) -> Any:
    return await fetch("POST", url, namespace=namespace, **kwargs)
