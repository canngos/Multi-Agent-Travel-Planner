import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useMemo, useRef } from "react";

import { humanize, trackPath, type GraphIndex, type MapLayout, type Segment, type Station } from "../lib/graph";
import { seconds } from "../lib/format";
import { isWorking, type RunState, type ToolCall } from "../lib/run";
import type { Selection } from "./selection";

interface Props {
  index: GraphIndex;
  layout: MapLayout;
  run: RunState;
  now: number;
  selection: Selection | null;
  onSelect: (s: Selection) => void;
}

const RECENT = 2.5; // seconds a finished tool chip stays visible

export function RouteMap({ index, layout, run, now, selection, onSelect }: Props) {
  const reduced = useReducedMotion();
  const loop = layout.groups.find((g) => g.kind === "loop");
  const loopRun = loop ? run.agents[loop.name] : undefined;

  const recentArrivals = run.arrivals.filter((a) => now - a.at < 1.6 && now >= a.at - 0.1);
  const lit = (seg: Segment) => {
    if (seg.toAgent.endsWith(":join")) return run.agents[seg.fromAgent]?.status === "done";
    return (run.agents[seg.toAgent]?.spans.length ?? 0) > 0;
  };

  return (
    <svg className="route-map" viewBox={`0 0 ${layout.width} ${layout.height}`} role="img"
         aria-label="Map of the planning agents and their current state">
      {layout.groups.filter((g) => g.line.id === "main" || g.kind === "loop").map((g) => (
        <g key={g.name} className={`group group-${g.kind}`}>
          <text x={g.x + 8} y={g.y - 6} className="group-label">
            {g.kind === "parallel" ? `${humanize(g.name)}: all branches run at the same time`
              : `${humanize(g.name)}: round ${Math.max(1, loopRun?.rounds ?? 0)} of ${g.maxIterations ?? "?"}`}
          </text>
        </g>
      ))}

      {layout.segments.map((seg, i) => (
        <path key={i} d={trackPath(seg)} className={`track track-${seg.kind} ${lit(seg) ? "lit" : ""}`}
              style={{ stroke: seg.line.color }} />
      ))}

      {!reduced && recentArrivals.flatMap((a) =>
        layout.segments.filter((s) => s.toAgent === a.agent && s.kind !== "loop").map((s, i) => (
          <Train key={`${a.id}-${i}`} d={trackPath(s)} color={s.line.color} />
        )))}
      {!reduced && loop && recentArrivals.some((a) => a.agent === index.nodes[loop.name].children?.[0]?.name
        && (loopRun?.rounds ?? 0) > 1) && layout.segments.filter((s) => s.kind === "loop").map((s, i) => (
        <Train key={`loop-${loopRun?.rounds}-${i}`} d={trackPath(s)} color={s.line.color} slow />
      ))}

      {layout.stations.map((st) => (
        <StationMark key={st.name} station={st} run={run} now={now}
                     selected={selection?.kind === "agent" && selection.name === st.name}
                     onSelect={onSelect} reduced={!!reduced} />
      ))}
    </svg>
  );
}

function Train({ d, color, slow }: { d: string; color: string; slow?: boolean }) {
  const motionRef = useRef<SVGAnimateMotionElement>(null);
  const fadeRef = useRef<SVGAnimateElement>(null);
  useEffect(() => {
    motionRef.current?.beginElement();
    fadeRef.current?.beginElement();
  }, []);
  const dur = slow ? "1.4s" : "0.9s";
  return (
    <circle r={6} className="train" style={{ fill: color }}>
      <animateMotion ref={motionRef} begin="indefinite" dur={dur} fill="freeze" path={d} calcMode="spline"
                     keyTimes="0;1" keySplines="0.4 0 0.2 1" />
      <animate ref={fadeRef} attributeName="opacity" begin="indefinite" dur={dur} values="0;1;1;0" keyTimes="0;0.15;0.8;1"
               fill="freeze" />
    </circle>
  );
}

function statusText(name: string, run: RunState, now: number): string {
  const a = run.agents[name];
  const span = a.spans[a.spans.length - 1];
  if (a.status === "waiting") return name === "concierge" && run.busy ? "planning team at work" : "waiting for you";
  if (a.status === "done" && span?.end !== undefined) {
    const total = a.spans.reduce((t, s) => t + ((s.end ?? s.start) - s.start), 0);
    return `done in ${seconds(total)}`;
  }
  if (a.status === "tool") {
    const running = a.toolCalls.map((id) => run.tools[id]).filter((t) => t?.status === "running");
    return running.length ? `calling ${running[running.length - 1].name}` : "working";
  }
  if (a.status === "thinking") return span ? `thinking ${seconds(Math.max(0, now - span.start))}` : "thinking";
  return "";
}

function StationMark({ station, run, now, selected, onSelect, reduced }: {
  station: Station; run: RunState; now: number; selected: boolean; onSelect: (s: Selection) => void; reduced: boolean;
}) {
  const a = run.agents[station.name];
  const working = isWorking(a.status);
  const tools = useMemo(() => a.toolCalls.map((id) => run.tools[id]).filter(Boolean) as ToolCall[], [a.toolCalls, run.tools]);
  const visible = tools.filter((t) => t.name !== "transfer_to_agent"
    && (t.status === "running" || (t.endedAt !== undefined && now - t.endedAt < RECENT && now >= t.startedAt))).slice(-1); // one chip: rows are close together
  const color = station.line.color;
  const label = humanize(station.name);
  const select = () => onSelect({ kind: "agent", name: station.name });

  return (
    <g className={`station status-${a.status} ${selected ? "selected" : ""}`} transform={`translate(${station.x} ${station.y})`}
       role="button" tabIndex={0} aria-label={`${label}: ${statusText(station.name, run, now) || "not started"}`}
       onClick={select} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), select())}>
      <rect x={-62} y={-16} width={124} height={66} className="hit-area" />
      {working && !reduced && <circle r={13} className="halo" style={{ stroke: color }} />}
      {selected && <circle r={19} className="selection-ring" />}
      <circle r={11} className="dot" style={{ stroke: color, fill: a.status === "done" ? color : undefined }} />
      {a.status === "done" && <path d="M -4.5 0.5 L -1.2 3.8 L 4.8 -3.2" className="check" />}
      {a.status === "waiting" && <g className="ellipsis">{[-4, 0, 4].map((x) => <circle key={x} cx={x} r={1.6} />)}</g>}
      <text y={29} className="station-label">{label}</text>
      <text y={44} className="station-status">{statusText(station.name, run, now)}</text>
      {tools.length > 0 && (
        <g transform="translate(14 -16)" className="tool-count">
          <rect width={tools.length > 9 ? 26 : 20} height={15} rx={7.5} />
          <text x={tools.length > 9 ? 13 : 10} y={11}>{tools.length}</text>
        </g>
      )}
      <AnimatePresence>
        {visible.map((t, i) => (
          <motion.g key={t.id} className={`tool-chip chip-${t.status}`}
                    initial={reduced ? false : { opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: -27 - i * 21 }}
                    exit={reduced ? undefined : { opacity: 0, y: -40 }}
                    transition={{ duration: 0.35, ease: [0.3, 0.7, 0.2, 1] }}>
            <rect x={-Math.min(64, 12 + t.name.length * 3.4)} y={-9} width={Math.min(128, 24 + t.name.length * 6.8)} height={18}
                  rx={4} style={{ stroke: color }} />
            <text x={6} y={4} textAnchor="middle">{t.name}</text>
            {t.status === "running" && !reduced && (
              <circle cx={-Math.min(64, 12 + t.name.length * 3.4) + 8} cy={0} r={3.2} className="spinner" style={{ stroke: color }} />
            )}
            {t.status !== "running" && (
              <text x={-Math.min(64, 12 + t.name.length * 3.4) + 8} y={4} className="chip-mark" textAnchor="middle">
                {t.status === "ok" ? "✓" : "!"}
              </text>
            )}
          </motion.g>
        ))}
      </AnimatePresence>
    </g>
  );
}
