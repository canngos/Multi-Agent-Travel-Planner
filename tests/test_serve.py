"""Production entry points: scripts/serve.py routing and the LLM settings (no server, no model)."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_serve():
    spec = importlib.util.spec_from_file_location("serve", ROOT / "scripts" / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recorder:
    def __init__(self, name):
        self.name, self.calls = name, []

    async def __call__(self, scope, receive, send):
        self.calls.append(scope)


@pytest.mark.parametrize("path, target, inner", [
    ("/adk/list-apps", "adk", "/list-apps"),
    ("/adk/run_sse", "adk", "/run_sse"),
    ("/adk", "adk", "/"),
    ("/", "ui", None),
    ("/assets/index.js", "ui", None),
    ("/adkfoo", "ui", None),
])
async def test_planner_app_routes_api_and_ui(path, target, inner):
    serve = _load_serve()
    adk, ui = Recorder("adk"), Recorder("ui")
    app = serve.PlannerApp(adk, ui)
    await app({"type": "http", "path": path, "raw_path": path.encode()}, None, None)
    hit = adk if target == "adk" else ui
    assert len(hit.calls) == 1 and len((ui if target == "adk" else adk).calls) == 0
    if inner:
        assert hit.calls[0]["path"] == inner and hit.calls[0]["raw_path"] == inner.encode()


async def test_lifespan_goes_to_adk():
    serve = _load_serve()
    adk, ui = Recorder("adk"), Recorder("ui")
    await serve.PlannerApp(adk, ui)({"type": "lifespan"}, None, None)
    assert len(adk.calls) == 1 and not ui.calls


def test_llm_settings_default_override_and_demo(monkeypatch):
    from travel_planner import config
    monkeypatch.delenv("DEMO", raising=False)
    monkeypatch.setattr(config, "LLM_MODEL", "")
    assert config.llm_settings()["model"] == f"openai/{config.LEHMUS_MODEL_ID}"
    monkeypatch.setattr(config, "LLM_MODEL", "openai/gpt-4.1-mini")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    assert config.llm_settings() == {"model": "openai/gpt-4.1-mini", "api_base": None, "api_key": "sk-test"}
    monkeypatch.setenv("DEMO", "1")  # the recording's model, whatever is configured
    assert config.llm_settings()["model"] == f"openai/{config.DEFAULT_MODEL_ID}"
