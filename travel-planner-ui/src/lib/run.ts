/**
 * Turns the stream of ADK events into what the UI shows: agent states, tool calls, chat, state writes.
 *
 * ADK sends an event only after a model answer, not when an agent starts thinking. So start times come from
 * the agent tree: when a step of a sequential agent finishes, the next one starts; a parallel agent starts all
 * its children; a loop starts over until a child escalates (the critic approves) or the round limit is reached. Events
 * never move a finished agent back to "working" (after-agent callbacks still send events); only the tree does.
 */
import { humanize, type GraphIndex } from "./graph";
import type { AdkEvent } from "./types";

export type AgentStatus = "idle" | "thinking" | "tool" | "done" | "waiting";

export interface AgentRun {
  status: AgentStatus;
  spans: { start: number; end?: number }[];
  toolCalls: string[];
  outputs: string[];
  rounds: number;
  lastEventAt?: number;
  lastText?: string;
  reasoning: string[];
  llmCalls: number;
  tokensIn: number;
  tokensOut: number;
}

export interface ToolCall {
  id: string;
  agent: string;
  name: string;
  args: Record<string, unknown>;
  response?: Record<string, unknown>;
  status: "running" | "ok" | "error";
  startedAt: number;
  endedAt?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "agent" | "notice";
  author: string;
  text: string;
  at: number;
}

export interface StateWrite {
  key: string;
  by: string;
  at: number;
}

export interface ActivityItem {
  id: string;
  at: number;
  agent: string;
  kind: "start" | "tool" | "result" | "answer" | "state" | "transfer" | "loop" | "user" | "error";
  text: string;
  toolId?: string;
  stateKey?: string;
}

export interface Arrival {
  id: string;
  agent: string;
  at: number;
}

export interface RunState {
  agents: Record<string, AgentRun>;
  tools: Record<string, ToolCall>;
  toolOrder: string[];
  chat: ChatMessage[];
  state: Record<string, unknown>;
  writes: StateWrite[];
  activity: ActivityItem[];
  arrivals: Arrival[];
  escalated: Record<string, boolean>;
  startedAt?: number;
  now: number;
  eventCount: number;
  busy: boolean;
}

export type RunAction =
  | { type: "reset" }
  | { type: "user"; text: string; at: number }
  | { type: "event"; event: AdkEvent }
  | { type: "error"; message: string; at: number }
  | { type: "idle"; at: number };

export function initialRun(index: GraphIndex): RunState {
  const agents: Record<string, AgentRun> = {};
  for (const name of Object.keys(index.nodes)) {
    agents[name] = { status: "idle", spans: [], toolCalls: [], outputs: [], rounds: 0, reasoning: [], llmCalls: 0,
      tokensIn: 0, tokensOut: 0 };
  }
  return { agents, tools: {}, toolOrder: [], chat: [], state: {}, writes: [], activity: [], arrivals: [],
    escalated: {}, now: 0, eventCount: 0, busy: false };
}

// one line of plain text from Markdown (for the activity list)
const plain = (md: string) => md.replace(/[*_`#>|]+/g, "").replace(/\s+/g, " ").trim();
const clip = (text: string, n = 140) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);

export function createReducer(index: GraphIndex) {
  const { nodes, parent } = index;
  const rootName = index.root.name;

  return function reduce(prev: RunState, action: RunAction): RunState {
    if (action.type === "reset") return initialRun(index);
    // copy-on-write: the state is small enough to clone shallowly per action
    const s: RunState = {
      ...prev,
      agents: Object.fromEntries(Object.entries(prev.agents).map(([k, v]) => [k, { ...v, spans: [...v.spans] }])),
      tools: { ...prev.tools },
      toolOrder: [...prev.toolOrder],
      chat: [...prev.chat],
      state: { ...prev.state },
      writes: [...prev.writes],
      activity: [...prev.activity],
      arrivals: prev.arrivals.slice(-40),
      escalated: { ...prev.escalated },
    };
    let seq = s.activity.length;
    const log = (item: Omit<ActivityItem, "id">) => s.activity.push({ ...item, id: `a${seq++}` });

    const open = (name: string, at: number) => {
      const run = s.agents[name];
      if (!run) return;
      const last = run.spans[run.spans.length - 1];
      if (!last || last.end !== undefined) run.spans.push({ start: at });
    };
    const close = (name: string, at: number) => {
      const run = s.agents[name];
      const last = run?.spans[run.spans.length - 1];
      if (last && last.end === undefined) last.end = at;
    };

    const activate = (name: string, at: number) => {
      const node = nodes[name];
      const run = s.agents[name];
      if (!node || !run) return;
      run.status = "thinking";
      open(name, at);
      s.arrivals.push({ id: `${name}@${at}@${seq}`, agent: name, at });
      if (node.kind === "llm") {
        log({ at, agent: name, kind: "start", text: `${humanize(name)} starts` });
        return;
      }
      if (node.kind === "loop") {  // a new run of the loop (a re-plan): the last run's approval no longer counts
        run.rounds = 1;
        s.escalated[name] = false;
      }
      const kids = node.children ?? [];
      if (node.kind === "parallel") kids.forEach((k) => activate(k.name, at));
      else if (kids.length) activate(kids[0].name, at);
    };

    const complete = (name: string, at: number) => {
      const run = s.agents[name];
      if (!run || run.status === "done") return;
      run.status = "done";
      close(name, at);
      const up = parent[name];
      if (!up) return;
      const holder = nodes[up];
      const kids = holder.children ?? [];
      const i = kids.findIndex((k) => k.name === name);
      if (holder.kind === "llm") {
        // a transfer target finished (the pipeline): the concierge waits for the user again
        s.agents[up].status = "waiting";
        return;
      }
      if (holder.kind === "parallel") {
        if (kids.every((k) => s.agents[k.name]?.status === "done")) complete(up, at);
        return;
      }
      if (i < kids.length - 1) {
        activate(kids[i + 1].name, at);
        return;
      }
      if (holder.kind === "loop") {
        const loop = s.agents[up];
        const limit = holder.max_iterations ?? Infinity;
        if (!s.escalated[up] && loop.rounds < limit) {
          loop.rounds += 1;
          log({ at, agent: up, kind: "loop", text: `Review round ${loop.rounds} of ${holder.max_iterations ?? "∞"}` });
          activate(kids[0].name, at);
          return;
        }
      }
      complete(up, at);
    };

    const finishBelow = (name: string, at: number) => {
      for (const kid of nodes[name]?.children ?? []) {
        finishBelow(kid.name, at);
        const kr = s.agents[kid.name];
        if (kr && kr.status !== "done" && kr.status !== "idle") {
          kr.status = "done";
          close(kid.name, at);
        }
      }
    };

    const enclosingLoop = (name: string): string | undefined => {
      for (let up = parent[name]; up; up = parent[up]) if (nodes[up]?.kind === "loop") return up;
      return undefined;
    };

    if (action.type === "user") {
      s.chat.push({ id: `u${s.chat.length}`, role: "user", author: "you", text: action.text, at: action.at });
      log({ at: action.at, agent: rootName, kind: "user", text: `You: ${clip(action.text, 90)}` });
      s.startedAt ??= action.at;
      s.now = Math.max(s.now, action.at);
      s.busy = true;
      const root = s.agents[rootName];
      root.status = "thinking";
      open(rootName, action.at);
      return s;
    }
    if (action.type === "error") {
      s.chat.push({ id: `e${s.chat.length}`, role: "notice", author: "system", text: action.message, at: action.at });
      log({ at: action.at, agent: rootName, kind: "error", text: action.message });
      s.busy = false;
      for (const [name, run] of Object.entries(s.agents)) {  // nothing keeps working after an error
        if (run.status === "thinking" || run.status === "tool") {
          run.status = name === rootName ? "waiting" : "idle";
          close(name, action.at);
        }
      }
      return s;
    }
    if (action.type === "idle") {
      // the server finished the turn: nothing is working any more
      s.busy = false;
      for (const [name, run] of Object.entries(s.agents)) {
        if (run.status === "thinking" || run.status === "tool") {
          run.status = name === rootName ? "waiting" : run.status;
          if (name === rootName) close(name, action.at);
        }
      }
      return s;
    }

    // ------------------------------------------------------------ an ADK event
    const ev = action.event;
    if (ev.partial) return prev;
    const at = ev.timestamp;
    s.now = Math.max(s.now, at);
    s.startedAt ??= at;
    s.eventCount += 1;
    const name = ev.author;
    const run = s.agents[name];
    if (run && run.status === "idle") activate(name, at);
    if (run) run.lastEventAt = at; // not predicted by the tree (e.g. an unknown agent)
    if (run && run.status === "waiting" && name === rootName) { run.status = "thinking"; open(name, at); }

    if (run && ev.usageMetadata) {  // one model answer: count it and its tokens (the metrics panel)
      run.llmCalls += 1;
      run.tokensIn += ev.usageMetadata.promptTokenCount ?? 0;
      run.tokensOut += ev.usageMetadata.candidatesTokenCount ?? 0;
    }

    if (ev.errorCode) {
      const msg = `${humanize(name)}: ${ev.errorMessage ?? ev.errorCode}`;
      s.chat.push({ id: `e${s.chat.length}`, role: "notice", author: name, text: msg, at });
      log({ at, agent: name, kind: "error", text: msg });
    }

    const parts = ev.content?.parts ?? [];
    let answer = "";
    let calls = 0;
    for (const part of parts) {
      if (part.thought && part.text) {
        run?.reasoning.push(part.text);
      } else if (part.text) {
        answer += part.text;
      }
      if (part.functionCall) {
        calls += 1;
        const id = part.functionCall.id || `${ev.id}:${part.functionCall.name}`;
        s.tools[id] = { id, agent: name, name: part.functionCall.name, args: part.functionCall.args ?? {},
          status: "running", startedAt: at };
        s.toolOrder.push(id);
        run?.toolCalls.push(id);
        if (run && run.status !== "done") run.status = "tool";
        if (part.functionCall.name !== "transfer_to_agent") {
          log({ at, agent: name, kind: "tool", text: `${humanize(name)} calls ${part.functionCall.name}`, toolId: id });
        }
      }
      if (part.functionResponse) {
        const id = part.functionResponse.id || `${ev.id}:${part.functionResponse.name}`;
        const response = part.functionResponse.response ?? {};
        const failed = response.status === "error" || response.status === "needs_confirmation";
        const call = s.tools[id] ?? { id, agent: name, name: part.functionResponse.name, args: {}, startedAt: at, status: "running" as const };
        s.tools[id] = { ...call, response, status: response.status === "error" ? "error" : "ok", endedAt: at };
        if (!s.toolOrder.includes(id)) s.toolOrder.push(id);
        if (run && run.status === "tool") run.status = "thinking";
        if (part.functionResponse.name !== "transfer_to_agent") {
          const reason = typeof response.reason === "string" ? `: ${clip(response.reason, 80)}` : "";
          log({ at, agent: name, kind: "result",
            text: `${part.functionResponse.name} ${failed ? String(response.status) : "returned"}${failed ? reason : ""}`, toolId: id });
        }
      }
    }

    const delta = ev.actions?.stateDelta ?? {};
    for (const [key, value] of Object.entries(delta)) {
      s.state[key] = value;
      s.writes.push({ key, by: name, at });
      const node = nodes[name];
      if (run && node?.kind === "llm" && node.output_key === key && !run.outputs.includes(key)) {
        run.outputs.push(key);
        log({ at, agent: name, kind: "state", text: `${humanize(name)} writes ${key}`, stateKey: key });
      }
    }
    if (delta.final_guide) {
      s.chat.push({ id: `g${s.chat.length}`, role: "notice", author: name, text: "The trip guide is ready.", at });
    }

    const transfer = ev.actions?.transferToAgent;
    if (transfer && nodes[transfer]) {
      log({ at, agent: name, kind: "transfer", text: `${humanize(name)} hands over to ${humanize(transfer)}` });
      if (run) { run.status = "waiting"; close(name, at); }
      activate(transfer, at);
      return s;
    }

    if (ev.actions?.escalate) {
      const loop = enclosingLoop(name);
      if (loop) {
        s.escalated[loop] = true;
        // The escalation can come from an after-agent callback, after the tree already started the next round
        // (code checks the critic's verdict after its answer). Undo a round that has not sent any event.
        const first = nodes[loop].children?.[0]?.name;
        const fr = first ? s.agents[first] : undefined;
        const span = fr?.spans[fr.spans.length - 1];
        if (first && fr && span && span.end === undefined && (fr.lastEventAt ?? -Infinity) < span.start
            && s.agents[loop].status !== "done") {
          fr.spans.pop();
          fr.status = "done";
          s.agents[loop].rounds -= 1;
          s.activity = s.activity.filter((a) => !(a.at === span.start
            && ((a.agent === first && a.kind === "start") || (a.agent === loop && a.kind === "loop"))));
          s.arrivals = s.arrivals.filter((a) => !(a.agent === first && a.at === span.start));
        }
        log({ at, agent: name, kind: "loop", text: `${humanize(name)} ends the review loop` });
        complete(name, at);
        complete(loop, at);
        return s;
      }
      complete(name, at);
      return s;
    }

    const isFinal = answer.trim() !== "" && calls === 0 && ev.content?.role !== "user";
    if (isFinal && run) {
      run.lastText = answer;
      const node = nodes[name];
      const structured = /^\s*(```|\{|\[)/.test(answer);  // a JSON result, not a sentence
      log({ at, agent: name, kind: "answer", stateKey: structured && node?.output_key ? node.output_key : undefined,
        text: structured ? `${humanize(name)} delivers its result${node?.output_key ? ` (${node.output_key})` : ""}`
          : `${humanize(name)}: ${clip(plain(answer), 110)}` });
      if (name === rootName) {
        s.chat.push({ id: `c${s.chat.length}`, role: "agent", author: name, text: answer, at });
        run.status = "waiting";
        close(name, at);
      } else {
        // the guide writer's reply, and the reviewed plan the pipeline shows for approval
        if (name === "guide_writer" || name === "planning_pipeline") {
          s.chat.push({ id: `w${s.chat.length}`, role: "agent", author: name, text: answer, at });
        }
        // A workflow agent speaks for itself only when it ends without running its children (a re-plan skipping a
        // research team that keeps its results) or after them (the pipeline presenting the plan): either way,
        // nothing below it is still working.
        if (node && node.kind !== "llm") finishBelow(name, at);
        complete(name, at);
      }
    }
    return s;
  };
}

export const isWorking = (status: AgentStatus) => status === "thinking" || status === "tool";

/** Per-agent cost and time for the metrics panel: model answers, tokens, tool calls and seconds spent working. */
export interface AgentMetrics {
  name: string;
  llmCalls: number;
  tokensIn: number;
  tokensOut: number;
  toolCalls: number;
  seconds: number;
}

export function agentMetrics(run: RunState, agents: string[], now: number): AgentMetrics[] {
  return agents.map((name) => {
    const a = run.agents[name];
    const seconds = a.spans.reduce((sum, sp) => sum + Math.max(0, (sp.end ?? now) - sp.start), 0);
    const toolCalls = a.toolCalls.filter((id) => run.tools[id]?.name !== "transfer_to_agent").length;
    return { name, llmCalls: a.llmCalls, tokensIn: a.tokensIn, tokensOut: a.tokensOut, toolCalls, seconds };
  });
}
