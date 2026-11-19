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

# Optional: any LiteLLM model instead of Lehmus, e.g. LLM_MODEL=openai/gpt-4.1-mini with LLM_API_KEY. The prompts
# were developed and tested on GPT OSS 120B (Lehmus); other models are not tested.
LLM_MODEL = os.environ.get("LLM_MODEL", "").strip()

_missing = [] if LLM_MODEL else [n for n in ("LEHMUS_API_BASE", "LEHMUS_API_KEY") if not os.environ.get(n)]
if _missing and os.environ.get("DEMO") != "1":
    log.warning("Missing %s in .env (see .env.example); LLM calls will fail.", ", ".join(_missing))

LEHMUS_API_BASE = os.environ.get("LEHMUS_API_BASE", "")  # must end with /v1
DEFAULT_MODEL_ID = "azsydsttjnlbfjbgqnwd"  # GPT OSS 120B on Lehmus; also the model the demo was recorded with
LEHMUS_MODEL_ID = os.environ.get("LEHMUS_MODEL_ID", DEFAULT_MODEL_ID)

DEFAULT_CACHE_DIR = REPO_ROOT / "cache"
DEMO_CACHE_DIR = REPO_ROOT / "cache" / "demo"
DEMO_TODAY = date(2026, 11, 17)  # the day the demo was recorded; prompts and the weather tool use "today"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "output"
# OUTPUT_DIR=<folder> writes guides elsewhere (the demo-replay test uses a temp folder, so output/ is left alone)
OUTPUT_DIR = Path(os.environ["OUTPUT_DIR"]) if os.environ.get("OUTPUT_DIR") else DEFAULT_OUTPUT_DIR
# The day rhythm a trip brief gets when the user states none (TripBrief.day_start / day_end / pace)
DEFAULT_DAY_START, DEFAULT_DAY_END, DEFAULT_PACE = "09:30", "22:00", "balanced"


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
    """Today's date; pinned to the recording day in demo mode, so replayed prompts are identical.
    TODAY=YYYY-MM-DD pins it otherwise (the tests use it, so dates in their fixtures never fall into the past)."""
    if demo():
        return DEMO_TODAY
    return date.fromisoformat(os.environ["TODAY"]) if os.environ.get("TODAY") else date.today()


def refresh_cache() -> bool:
    """REFRESH_CACHE=1: skip cache reads but still write (used to re-record fixtures)."""
    return os.environ.get("REFRESH_CACHE") == "1"


def user_agent() -> str:
    """Descriptive User-Agent required by Nominatim, Wikipedia and Transitous."""
    base = os.environ.get("APP_USER_AGENT", "TripPlannerCourseProject/0.1")
    contact = os.environ.get("CONTACT_EMAIL", "")
    return f"{base} ({contact})" if contact else base


from .llm_cache import CachedLiteLlm  # noqa: E402  (imports config lazily, so this order is safe)



def llm_settings() -> dict:
    """model / api_base / api_key for LiteLLM. Demo mode always uses the recorded model's id (part of the cache key)."""
    if demo():
        return {"model": f"openai/{DEFAULT_MODEL_ID}", "api_base": LEHMUS_API_BASE or None,
                "api_key": os.environ.get("LEHMUS_API_KEY") or None}
    if LLM_MODEL:
        return {"model": LLM_MODEL, "api_base": os.environ.get("LLM_API_BASE") or None,
                "api_key": os.environ.get("LLM_API_KEY") or None}
    return {"model": f"openai/{LEHMUS_MODEL_ID}", "api_base": LEHMUS_API_BASE or None,
            "api_key": os.environ.get("LEHMUS_API_KEY") or None}


MODEL = CachedLiteLlm(**llm_settings())
