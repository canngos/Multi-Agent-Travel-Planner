import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import graphJson from "../generated/agent-graph.json";
import { indexGraph, layoutMap } from "./graph";
import { flattenReplay } from "./replay";
import { createReducer, initialRun, type RunState } from "./run";
import type { AgentNode, ReplayFile } from "./types";

const index = indexGraph(graphJson as AgentNode);
const replay = JSON.parse(readFileSync(new URL("../../public/replays/lisbon-demo.json", import.meta.url), "utf-8")) as ReplayFile;

function play(upTo = Infinity): RunState {
  const reduce = createReducer(index);
  let state = initialRun(index);
  for (const item of flattenReplay(replay)) {
    if (item.at > upTo) break;
    state = reduce(state, item.action);
  }
  return state;
}

describe("graph", () => {
  it("indexes the 13 LLM agents of the real tree", () => {
    expect(index.llmAgents).toHaveLength(13);
    expect(index.parent.flight_searcher).toBe("transport_search");
    expect(index.nodes.review_loop.kind).toBe("loop");
  });

  it("lays out one station per LLM agent, with a line per research branch and a loop track", () => {
    const map = layoutMap(index.root);
    expect(map.stations.map((s) => s.name).sort()).toEqual([...index.llmAgents].sort());
    expect(map.lines.map((l) => l.id)).toEqual(["main", "transport_team", "stay_team", "culture_agent", "food_agent", "review"]);
    expect(map.segments.filter((s) => s.kind === "loop")).toHaveLength(1);
    expect(map.segments.filter((s) => s.kind === "transfer")).toHaveLength(1);
    const x = Object.fromEntries(map.stations.map((s) => [s.name, s.x]));
    expect(x.concierge).toBeLessThan(x.flight_searcher);
    expect(x.transport_comparer).toBeGreaterThan(x.flight_searcher);
    expect(x.itinerary_planner).toBeGreaterThan(x.transport_comparer);
    expect(x.guide_writer).toBeGreaterThan(x.itinerary_reviser);
  });
});

describe("run reducer on the recorded Lisbon demo", () => {
  const final = play();

  it("finishes every LLM agent and leaves the concierge waiting for the user", () => {
    for (const name of index.llmAgents.filter((n) => n !== "concierge")) {
      expect(final.agents[name].status, name).toBe("done");
    }
    expect(final.agents.concierge.status).toBe("waiting");
    expect(final.agents.planning_pipeline.status).toBe("done");
  });

  it("pairs all 38 tool calls with their results", () => {
    expect(final.toolOrder).toHaveLength(38);
    expect(Object.values(final.tools).every((t) => t.status !== "running")).toBe(true);
  });

  it("starts all six research agents at the hand-over, before any of them sends an event", () => {
    const transfer = final.activity.find((a) => a.kind === "transfer")!;
    for (const name of ["flight_searcher", "ground_searcher", "airbnb_searcher", "hotel_searcher", "culture_agent", "food_agent"]) {
      expect(final.agents[name].spans[0].start, name).toBe(transfer.at);
    }
    expect(final.agents.transport_comparer.spans[0].start).toBeGreaterThan(transfer.at);
  });

  it("runs the review loop twice; the escalation after round 2 does not leave a third round behind", () => {
    // in this recording the critic approved in round 2 and the reviser ended the loop with exit_loop
    expect((replay.final_state!.revision_log as { exit?: string }[]).at(-1)!.exit).toBe("exit_loop");
    expect(final.agents.review_loop.rounds).toBe(2);
    expect(final.agents.review_loop.status).toBe("done");
    expect(final.agents.budget_critic.spans).toHaveLength(2);
    expect(final.escalated.review_loop).toBe(true);
    expect(final.agents.guide_writer.spans[0].start).toBeGreaterThanOrEqual(final.agents.review_loop.spans[0].end!);
  });

  it("collects the chat, the state keys and the guide", () => {
    expect(final.chat.filter((m) => m.role === "user")).toHaveLength(4);
    expect(final.chat.filter((m) => m.author === "concierge").length).toBeGreaterThanOrEqual(3);
    expect(typeof final.state.final_guide).toBe("string");
    for (const key of ["flight_options", "transport_summary", "itinerary", "critique"]) expect(final.state).toHaveProperty(key);
  });

  it("shows the planner working only after the whole research team finished", () => {
    // the planner follows research_team in the sequential pipeline: it waits for the last of the four branches
    const branches = ["transport_comparer", "stay_comparer", "culture_agent", "food_agent"];
    const researchDone = Math.max(...branches.map((name) => final.agents[name].spans.at(-1)!.end!));
    expect(final.agents.itinerary_planner.spans[0].start).toBe(researchDone);
    const mid = play(researchDone - 0.001);
    expect(mid.agents.itinerary_planner.status).toBe("idle");
  });
});
