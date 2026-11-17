"""Uniform tool results: {"status": "ok", "results": [...]} or {"status": "error", "reason": ...}.

Tools never raise to the agent. An error dict lets the agent switch to its fallback.
"""
import functools
import inspect
import logging
from typing import Any, Callable, Iterable

from pydantic import BaseModel

log = logging.getLogger(__name__)


class ToolError(Exception):
    """Raise inside a tool for an expected failure; the message becomes the error reason."""


def _dump(item: Any) -> Any:
    return item.model_dump(mode="json") if isinstance(item, BaseModel) else item


def ok(results: Iterable[Any], **extra: Any) -> dict:
    """Success result. Pydantic models are dumped to JSON-safe dicts."""
    items = [_dump(r) for r in results]
    return {"status": "ok", "count": len(items), "results": items, **extra}


def error(reason: str, **extra: Any) -> dict:
    return {"status": "error", "reason": reason, **extra}


def _reason(exc: Exception) -> str:
    if isinstance(exc, ToolError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def safe_tool(fn: Callable) -> Callable:
    """Turns any exception from a tool into error(...). Works for sync and async tools.

    functools.wraps keeps the name, docstring, annotations and signature, which ADK
    reads to build the tool declaration the model sees.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - tools must never raise
                log.warning("tool %s failed: %s", fn.__name__, exc)
                return error(_reason(exc))

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning("tool %s failed: %s", fn.__name__, exc)
            return error(_reason(exc))

    return sync_wrapper
