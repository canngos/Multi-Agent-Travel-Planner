/**
 * The agent tree, indexed, and laid out as a transit map.
 *
 * Layout rules (recursive): an LLM agent is a station; a sequential agent puts its children left to right on
 * one line; a parallel agent forks into one line per child and joins again; a loop agent is sequential with a
 * return track underneath. Each branch of the top-level parallel team gets its own line colour.
 */
import type { AgentNode } from "./types";

export interface GraphIndex {
  root: AgentNode;
  nodes: Record<string, AgentNode>;
  parent: Record<string, string | undefined>;
  llmAgents: string[];
}

export function indexGraph(root: AgentNode): GraphIndex {
  const nodes: Record<string, AgentNode> = {};
  const parent: Record<string, string | undefined> = {};
  const llmAgents: string[] = [];
  const walk = (node: AgentNode, up?: string) => {
    nodes[node.name] = node;
    parent[node.name] = up;
    if (node.kind === "llm") llmAgents.push(node.name);
    for (const child of node.children ?? []) walk(child, node.name);
  };
  walk(root);
  return { root, nodes, parent, llmAgents };
}

export function humanize(name: string): string {
  const words = name.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// ------------------------------------------------------------------ lines

export interface Line {
  id: string;
  label: string;
  color: string;
}

export const MAIN_LINE: Line = { id: "main", label: "Main line", color: "var(--line-main)" };
export const REVIEW_LINE: Line = { id: "review", label: "Review loop", color: "var(--line-review)" };
const BRANCH_COLORS = ["var(--line-transport)", "var(--line-stay)", "var(--line-culture)", "var(--line-food)"];

// ------------------------------------------------------------------ layout

export const COL = 150;
export const ROW = 84;
const FORK = 46;
const LOOP_DROP = 62; // below the station labels and status lines

export interface Pt {
  x: number;
  y: number;
}
export interface Station extends Pt {
  name: string;
  line: Line;
}
export interface Segment {
  from: Pt;
  to: Pt;
  line: Line;
  kind: "track" | "loop" | "transfer";
  /** Agent (or container) at each end; used to animate the track into an agent when it starts. */
  fromAgent: string;
  toAgent: string;
}
export interface Group {
  name: string;
  kind: "parallel" | "loop";
  x: number;
  y: number;
  w: number;
  h: number;
  line: Line;
  maxIterations?: number | null;
}
interface Box {
  w: number;
  h: number;
  entry: Pt;
  exit: Pt;
  entryAgent: string;
  exitAgent: string;
  stations: Station[];
  segments: Segment[];
  groups: Group[];
}
export interface MapLayout {
  width: number;
  height: number;
  stations: Station[];
  segments: Segment[];
  groups: Group[];
  lines: Line[];
}

const shift = (box: Box, dx: number, dy: number): Box => {
  const p = (q: Pt) => ({ x: q.x + dx, y: q.y + dy });
  return {
    ...box,
    entry: p(box.entry),
    exit: p(box.exit),
    stations: box.stations.map((s) => ({ ...s, ...p(s) })),
    segments: box.segments.map((s) => ({ ...s, from: p(s.from), to: p(s.to) })),
    groups: box.groups.map((g) => ({ ...g, x: g.x + dx, y: g.y + dy })),
  };
};

function sequence(boxes: Box[], line: Line, links: Segment["kind"][]): Omit<Box, "entryAgent" | "exitAgent"> {
  const h = Math.max(...boxes.map((b) => b.h));
  let x = 0;
  const placed = boxes.map((b) => {
    const moved = shift(b, x, (h - b.h) / 2);
    x += b.w;
    return moved;
  });
  const segments = placed.flatMap((b) => b.segments);
  placed.slice(1).forEach((b, i) => {
    const a = placed[i];
    segments.push({ from: a.exit, to: b.entry, line, kind: links[i] ?? "track",
      fromAgent: a.exitAgent, toAgent: b.entryAgent });
  });
  return {
    w: x, h, entry: placed[0].entry, exit: placed[placed.length - 1].exit,
    stations: placed.flatMap((b) => b.stations), segments, groups: placed.flatMap((b) => b.groups),
  };
}

function layoutNode(node: AgentNode, line: Line, lines: Line[]): Box {
  const children = node.children ?? [];
  if (node.kind === "llm") {
    const station: Station = { name: node.name, x: COL / 2, y: ROW / 2, line };
    const own: Box = { w: COL, h: ROW, entry: station, exit: station, entryAgent: node.name, exitAgent: node.name,
      stations: [station], segments: [], groups: [] };
    if (!children.length) return own;
    // an LLM agent that transfers to sub-agents (the concierge): its station, then the sub-agents, dashed link
    const rest = children.map((c) => layoutNode(c, line, lines));
    const seq = sequence([own, ...rest], line, rest.map(() => "transfer"));
    return { ...seq, entryAgent: node.name, exitAgent: rest[rest.length - 1].exitAgent };
  }
  if (node.kind === "sequential" || node.kind === "loop") {
    const loopLine = node.kind === "loop" && line.id === MAIN_LINE.id ? REVIEW_LINE : line;
    if (loopLine !== line && !lines.includes(loopLine)) lines.push(loopLine);
    const boxes = children.map((c) => layoutNode(c, loopLine, lines));
    const seq = sequence(boxes, loopLine, []);
    const box: Box = { ...seq, entryAgent: boxes[0].entryAgent, exitAgent: boxes[boxes.length - 1].exitAgent };
    if (node.kind !== "loop") return box;
    const h = box.h + LOOP_DROP;
    box.segments.push({ from: box.exit, to: box.entry, line: loopLine, kind: "loop",
      fromAgent: box.exitAgent, toAgent: box.entryAgent });
    box.groups.push({ name: node.name, kind: "loop", x: 0, y: 0, w: box.w, h, line: loopLine,
      maxIterations: node.max_iterations });
    return { ...box, h };
  }
  // parallel: one line per child, forking at the left edge and joining at the right edge
  const branchLines = children.map((c, i) => {
    if (line.id !== MAIN_LINE.id) return line;
    const own: Line = { id: c.name, label: humanize(c.name.replace(/_(team|agent)$/, "")), color: BRANCH_COLORS[i % BRANCH_COLORS.length] };
    lines.push(own);
    return own;
  });
  const boxes = children.map((c, i) => layoutNode(c, branchLines[i], lines));
  const inner = Math.max(...boxes.map((b) => b.w));
  const w = inner + 2 * FORK;
  const h = boxes.reduce((sum, b) => sum + b.h, 0);
  const fork: Pt = { x: 0, y: h / 2 };
  const join: Pt = { x: w, y: h / 2 };
  let y = 0;
  const stations: Station[] = [];
  const segments: Segment[] = [];
  const groups: Group[] = [];
  boxes.forEach((b, i) => {
    const moved = shift(b, FORK + (inner - b.w) / 2, y);
    y += b.h;
    stations.push(...moved.stations);
    segments.push(...moved.segments);
    groups.push(...moved.groups);
    segments.push({ from: fork, to: moved.entry, line: branchLines[i], kind: "track", fromAgent: node.name, toAgent: moved.entryAgent });
    segments.push({ from: moved.exit, to: join, line: branchLines[i], kind: "track", fromAgent: moved.exitAgent, toAgent: `${node.name}:join` });
  });
  groups.push({ name: node.name, kind: "parallel", x: 0, y: 0, w, h, line });
  return { w, h, entry: fork, exit: join, entryAgent: node.name, exitAgent: `${node.name}:join`, stations, segments, groups };
}

export function layoutMap(root: AgentNode): MapLayout {
  const lines: Line[] = [MAIN_LINE];
  const box = layoutNode(root, MAIN_LINE, lines);
  const pad = 24;
  const moved = shift(box, pad, pad + 18);
  return { width: box.w + 2 * pad, height: box.h + 2 * pad + 30, stations: moved.stations, segments: moved.segments,
    groups: moved.groups, lines };
}

/** SVG path for a track: straight when level, otherwise an S-curve like a modern metro map. */
export function trackPath(s: Segment): string {
  const { from: a, to: b } = s;
  if (s.kind === "loop") {
    const low = Math.max(a.y, b.y) + LOOP_DROP + 4;
    return `M ${a.x} ${a.y} C ${a.x + 30} ${a.y}, ${a.x + 30} ${low}, ${a.x - 10} ${low} L ${b.x + 10} ${low} C ${b.x - 30} ${low}, ${b.x - 30} ${b.y}, ${b.x} ${b.y}`;
  }
  if (Math.abs(a.y - b.y) < 0.5) return `M ${a.x} ${a.y} L ${b.x} ${b.y}`;
  const mx = (a.x + b.x) / 2;
  return `M ${a.x} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x} ${b.y}`;
}
