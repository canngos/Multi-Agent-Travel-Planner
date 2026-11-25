"""Writes the real agent tree to travel-planner-ui/src/generated/agent-graph.json for the web UI.

  .venv/Scripts/python scripts/export_agent_graph.py

The UI draws its route map from this file, so the picture always matches the code: agent names, kinds
(LLM / sequential / parallel / loop), descriptions, tools, output keys and each agent's role paragraph.
Re-run it after changing the agent tree.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from google.adk.agents import LlmAgent, LoopAgent, ParallelAgent, SequentialAgent  # noqa: E402

from travel_planner.agent import root_agent  # noqa: E402
from travel_planner.agents import concierge, itinerary_writer  # noqa: E402
from travel_planner.agents.itinerary_writer import WriterTools  # noqa: E402
from travel_planner.tools.mcp import CachedMcpToolset  # noqa: E402

OUT = ROOT / "travel-planner-ui" / "src" / "generated" / "agent-graph.json"


def tool_names(tool) -> list[str]:
    if isinstance(tool, CachedMcpToolset):
        return [f"{tool.server} (MCP)"]
    if isinstance(tool, WriterTools):  # the tools of both its tasks
        return [t.name for tools in tool.by_task.values() for t in tools]
    return [getattr(tool, "__name__", None) or getattr(tool, "name", None) or type(tool).__name__]


# agents whose instruction is built per call (from state), by name: their fixed role paragraph
BUILT_PER_CALL = {"concierge": concierge.ROLE, "itinerary_writer": itinerary_writer.ROLE}


def role_of(agent: LlmAgent) -> str:
    text = agent.instruction if isinstance(agent.instruction, str) else BUILT_PER_CALL[agent.name]
    return " ".join(text.split("\n\n")[0].split())


def node(agent) -> dict:
    kind = ("llm" if isinstance(agent, LlmAgent) else "loop" if isinstance(agent, LoopAgent)
            else "parallel" if isinstance(agent, ParallelAgent) else "sequential" if isinstance(agent, SequentialAgent)
            else type(agent).__name__)
    out = {"name": agent.name, "kind": kind, "description": agent.description or ""}
    if isinstance(agent, LlmAgent):
        out |= {"tools": [name for t in agent.tools for name in tool_names(t)], "output_key": agent.output_key, "role": role_of(agent)}
        if agent.sub_agents:
            out["transfers_to"] = [a.name for a in agent.sub_agents]
    if isinstance(agent, LoopAgent):
        out["max_iterations"] = agent.max_iterations
    if agent.sub_agents:
        out["children"] = [node(a) for a in agent.sub_agents]
    return out


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(node(root_agent), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
