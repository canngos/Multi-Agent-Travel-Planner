import inspect

from google.adk.tools import FunctionTool, ToolContext

from travel_planner.models import Place
from travel_planner.tools._result import ToolError, error, ok, safe_tool


def test_ok_dumps_models_and_dicts():
    r = ok([Place(kind="cafe", name="A Brasileira", lat=38.71, lon=-9.14), {"raw": 1}], note="n")
    assert r["status"] == "ok" and r["count"] == 2 and r["note"] == "n"
    assert r["results"][0]["name"] == "A Brasileira" and r["results"][1] == {"raw": 1}


def test_error():
    assert error("boom", source="x") == {"status": "error", "reason": "boom", "source": "x"}


def test_safe_tool_sync_catches():
    @safe_tool
    def tool(x: int) -> dict:
        raise ValueError("bad x")

    assert tool(1) == {"status": "error", "reason": "ValueError: bad x"}


async def test_safe_tool_async_catches_and_keeps_tool_error_message():
    @safe_tool
    async def tool(x: int) -> dict:
        raise ToolError("no results for x")

    assert inspect.iscoroutinefunction(tool)
    assert await tool(1) == {"status": "error", "reason": "no results for x"}


async def test_safe_tool_passes_through_success():
    @safe_tool
    async def tool(x: int) -> dict:
        return ok([x])

    assert (await tool(3))["results"] == [3]


def test_safe_tool_keeps_adk_declaration():
    """ADK must still see the real parameters and docstring through the decorator."""

    @safe_tool
    async def find_things(city: str, limit: int, tool_context: ToolContext) -> dict:
        """Finds things in a city.

        Args:
            city: City name.
            limit: Max results.
        """
        return ok([])

    decl = FunctionTool(find_things)._get_declaration()
    assert decl.name == "find_things"
    assert "Finds things in a city" in decl.description
    schema = decl.parameters_json_schema  # ADK 2.x uses JSON schema, not `parameters`
    assert set(schema["properties"]) == {"city", "limit"}  # tool_context hidden from the model
    assert schema["required"] == ["city", "limit"]
