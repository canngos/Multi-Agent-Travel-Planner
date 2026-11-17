"""Test modes.

Default (`pytest`): OFFLINE=1, cache = tests/fixtures/cache. Tool tests replay recorded
responses and never touch the network; a missing fixture fails the test.

Live (`pytest -m live`): REFRESH_CACHE=1, same cache dir. Tests hit the real APIs and
re-record the fixtures that the offline run uses.
"""
from pathlib import Path

import pytest

from travel_planner.tools import _http

FIXTURE_CACHE = Path(__file__).parent / "fixtures" / "cache"


@pytest.fixture(autouse=True)
def _cache_mode(request, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(FIXTURE_CACHE))
    if request.node.get_closest_marker("live"):
        monkeypatch.setenv("OFFLINE", "0")
        monkeypatch.setenv("REFRESH_CACHE", "1")
    else:
        monkeypatch.setenv("OFFLINE", "1")
        monkeypatch.setenv("REFRESH_CACHE", "0")


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    """Isolated, empty cache dir for unit tests of the cache itself."""
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def mock_http(monkeypatch):
    """Routes _http through an httpx.MockTransport. Set `.handler` to control replies;
    `.calls` records every request that reached the 'network'."""

    class Mock:
        def __init__(self):
            self.calls: list = []
            self.handler = lambda req: _json_response({"ok": True})

    def _json_response(body, status=200, headers=None):
        import httpx

        return httpx.Response(status, json=body, headers=headers)

    import httpx

    mock = Mock()
    mock.json_response = _json_response

    def dispatch(req):
        mock.calls.append(req)
        return mock.handler(req)

    monkeypatch.setattr(_http, "TRANSPORT", httpx.MockTransport(dispatch))
    monkeypatch.setenv("OFFLINE", "0")
    return mock
