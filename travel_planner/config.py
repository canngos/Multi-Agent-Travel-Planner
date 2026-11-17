"""Shared configuration: loads .env and builds the model used by every agent.

Importing this module never fails on missing keys, so offline tests and tools
work on a fresh clone. A missing LLM setting is reported as a warning here and
surfaces as an error on the first model call.
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.models.lite_llm import LiteLlm

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

log = logging.getLogger(__name__)

_missing = [n for n in ("LEHMUS_API_BASE", "LEHMUS_API_KEY", "LEHMUS_MODEL_ID") if not os.environ.get(n)]
if _missing:
    log.warning("Missing %s in .env (see .env.example); LLM calls will fail.", ", ".join(_missing))

LEHMUS_API_BASE = os.environ.get("LEHMUS_API_BASE", "")  # must end with /v1
LEHMUS_MODEL_ID = os.environ.get("LEHMUS_MODEL_ID", "azsydsttjnlbfjbgqnwd")  # GPT OSS 120B

MODEL = LiteLlm(
    model=f"openai/{LEHMUS_MODEL_ID}",
    api_base=LEHMUS_API_BASE or None,
    api_key=os.environ.get("LEHMUS_API_KEY") or None,
)

DEFAULT_CACHE_DIR = REPO_ROOT / "cache"
OUTPUT_DIR = REPO_ROOT / "output"


def cache_dir() -> Path:
    """Cache root, read at call time so tests and demo runs can switch it via CACHE_DIR."""
    return Path(os.environ.get("CACHE_DIR") or DEFAULT_CACHE_DIR)


def offline() -> bool:
    """OFFLINE=1: serve only from cache; a cache miss is an error, never a network call."""
    return os.environ.get("OFFLINE") == "1"


def refresh_cache() -> bool:
    """REFRESH_CACHE=1: skip cache reads but still write (used to re-record fixtures)."""
    return os.environ.get("REFRESH_CACHE") == "1"


def user_agent() -> str:
    """Descriptive User-Agent required by Nominatim, Wikipedia and Transitous."""
    base = os.environ.get("APP_USER_AGENT", "TripPlannerCourseProject/0.1")
    contact = os.environ.get("CONTACT_EMAIL", "")
    return f"{base} ({contact})" if contact else base
