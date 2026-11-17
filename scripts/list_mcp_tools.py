"""Prints the tools (name, description, input schema) of both MCP servers.

Usage (from the repo root):  python scripts/list_mcp_tools.py [--schema]
Needs network access and Node.js (npx) unless the tool lists are cached and OFFLINE=1.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from travel_planner.tools.mcp import airbnb_toolset, kiwi_toolset  # noqa: E402


async def show(toolset, show_schema: bool) -> None:
    print(f"\n=== {toolset.server} ===")
    try:
        tools = await toolset.get_tools()
        for tool in tools:
            spec = tool.raw_mcp_tool
            print(f"- {spec.name}: {(spec.description or '').strip().splitlines()[0][:150]}")
            if show_schema:
                print(json.dumps(spec.input_schema, indent=2, ensure_ascii=False))
    except Exception as exc:  # show the problem, keep going with the other server
        print(f"FAILED: {type(exc).__name__}: {exc}")
    finally:
        await toolset.close()


async def main() -> None:
    show_schema = "--schema" in sys.argv
    for factory in (kiwi_toolset, airbnb_toolset):
        await show(factory(), show_schema)


if __name__ == "__main__":
    asyncio.run(main())
