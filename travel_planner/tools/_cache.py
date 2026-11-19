"""Disk cache keyed by a hash of the request.

Layout: <cache_dir>/<namespace>/<sha256>.json holding {"request", "saved_at", "response"}.
Secrets (API keys, tokens) are removed from both the hash and the stored request,
so cache files are safe to commit and work with anyone's key.

Switches (read at call time, see config.py):
  OFFLINE=1        serve only from cache; a miss raises CacheMiss
  REFRESH_CACHE=1  skip reads, still write (re-record fixtures)
  CACHE_DIR=...    cache root
"""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .. import config
from ._result import ToolError

SECRET_NAMES = {"apikey", "api_key", "key", "token", "access_token", "authorization", "x-api-key", "auth"}


class CacheMiss(ToolError):
    """Raised in OFFLINE mode when a request has no cached response."""


def redact(mapping: dict | None) -> dict:
    """Drops secret-looking entries (by name) from params or headers."""
    return {k: v for k, v in (mapping or {}).items() if k.lower() not in SECRET_NAMES}


def make_key(request: dict) -> str:
    canonical = json.dumps(request, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _path(namespace: str, key: str) -> Path:
    return config.cache_dir() / namespace / f"{key}.json"


def read(namespace: str, request: dict, max_age_s: float | None = None) -> tuple[bool, Any]:
    """Returns (hit, response). Entries older than max_age_s (or their own shorter "ttl_s") count as misses,
    except in OFFLINE mode, where any cached entry is better than nothing."""
    path = _path(namespace, make_key(request))
    if not path.exists():
        return False, None
    entry = json.loads(path.read_text(encoding="utf-8"))
    limits = [a for a in (max_age_s, entry.get("ttl_s")) if a is not None]
    if limits and not config.offline() and time.time() - entry["saved_at"] > min(limits):
        return False, None
    return True, entry["response"]


def write(namespace: str, request: dict, response: Any, ttl_s: float | None = None) -> None:
    """Stores a response; ttl_s gives this entry a shorter life than its namespace (e.g. an empty answer)."""
    path = _path(namespace, make_key(request))
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"request": request, "saved_at": time.time(), "response": response}
    if ttl_s is not None:
        entry["ttl_s"] = ttl_s
    # Atomic write: parallel agents may fetch the same thing at once.
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


async def cached(
    namespace: str,
    request: dict,
    produce: Callable[[], Awaitable[Any]],
    max_age_s: float | None = None,
    ttl_for: Callable[[Any], float | None] | None = None,
) -> Any:
    """Cache-or-produce for anything JSON-serializable (HTTP bodies, library calls, MCP results).

    `request` must already be free of secrets; it is hashed and stored as-is.
    ttl_for(response) may give a fresh response a shorter life, e.g. an empty answer that may fill up soon.
    """
    if not config.refresh_cache():
        hit, response = read(namespace, request, max_age_s)
        if hit:
            return response
    if config.offline():
        raise CacheMiss(f"OFFLINE mode and no cached response for {namespace} request")
    response = await produce()
    write(namespace, request, response, ttl_for(response) if ttl_for else None)
    return response
