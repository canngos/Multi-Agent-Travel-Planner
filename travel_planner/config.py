"""Shared configuration: loads .env and builds the model used by every agent.

Importing this module never fails on missing keys, so offline tests and tools
work on a fresh clone. A missing LLM setting is reported as a warning here and
surfaces as an error on the first model call.
"""
import logging
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

log = logging.getLogger(__name__)

_missing = [n for n in ("LEHMUS_API_BASE", "LEHMUS_API_KEY", "LEHMUS_MODEL_ID") if not os.environ.get(n)]
if _missing:
    log.warning("Missing %s in .env (see .env.example); LLM calls will fail.", ", ".join(_missing))

LEHMUS_API_BASE = os.environ.get("LEHMUS_API_BASE", "")  # must end with /v1
LEHMUS_MODEL_ID = os.environ.get("LEHMUS_MODEL_ID", "azsydsttjnlbfjbgqnwd")  # GPT OSS 120B

DEFAULT_CACHE_DIR = REPO_ROOT / "cache"
DEMO_CACHE_DIR = REPO_ROOT / "cache" / "demo"
DEMO_TODAY = date(2026, 11, 17)  # the day the demo was recorded; prompts and the weather tool use "today"
OUTPUT_DIR = REPO_ROOT / "output"


def demo() -> bool:
    """DEMO=1: replay the recorded demo from cache/demo (tools and LLM answers), no network needed.
    With DEMO_RECORD=1 as well, the demo is recorded live into cache/demo instead."""
    return os.environ.get("DEMO") == "1"


def demo_recording() -> bool:
    return demo() and os.environ.get("DEMO_RECORD") == "1"


def cache_dir() -> Path:
    """Cache root, read at call time so tests and demo runs can switch it via CACHE_DIR."""
    if os.environ.get("CACHE_DIR"):
        return Path(os.environ["CACHE_DIR"])
    return DEMO_CACHE_DIR if demo() else DEFAULT_CACHE_DIR


def offline() -> bool:
    """OFFLINE=1 (or demo replay): serve only from cache; a cache miss is an error, never a network call."""
    return os.environ.get("OFFLINE") == "1" or (demo() and not demo_recording())


def llm_cache_on() -> bool:
    """Cache LLM answers too: in demo mode, or with LLM_CACHE=1."""
    return demo() or os.environ.get("LLM_CACHE") == "1"


def today() -> date:
    """Today's date; pinned to the recording day in demo mode, so replayed prompts are identical."""
    return DEMO_TODAY if demo() else date.today()


def refresh_cache() -> bool:
    """REFRESH_CACHE=1: skip cache reads but still write (used to re-record fixtures)."""
    return os.environ.get("REFRESH_CACHE") == "1"


def user_agent() -> str:
    """Descriptive User-Agent required by Nominatim, Wikipedia and Transitous."""
    base = os.environ.get("APP_USER_AGENT", "TripPlannerCourseProject/0.1")
    contact = os.environ.get("CONTACT_EMAIL", "")
    return f"{base} ({contact})" if contact else base


from .llm_cache import CachedLiteLlm  # noqa: E402  (imports config lazily, so this order is safe)

MODEL = CachedLiteLlm(
    model=f"openai/{LEHMUS_MODEL_ID}",
    api_base=LEHMUS_API_BASE or None,
    api_key=os.environ.get("LEHMUS_API_KEY") or None,
)
