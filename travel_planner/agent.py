"""ADK entry point: `adk web` (run from the repo root) loads `root_agent` from here."""
from .agents.pipeline import build_root_agent

root_agent = build_root_agent()
