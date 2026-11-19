import { useMemo, useState } from "react";

import { argSummary, download, guideHtml, parseValue, pretty, renderMarkdown, seconds } from "../lib/format";
import { humanize, type GraphIndex, type MapLayout } from "../lib/graph";
import { agentMetrics, type ActivityItem, type RunState } from "../lib/run";
import type { Selection } from "./selection";

interface Common {
  index: GraphIndex;
  run: RunState;
  onSelect: (s: Selection) => void;
}

const lineColor = (layout: MapLayout, agent: string) =>
  layout.stations.find((s) => s.name === agent)?.line.color ?? "var(--line-main)";

// ------------------------------------------------------------------ activity

export function ActivityPanel({ index, run, layout, now, onSelect }: Common & { layout: MapLayout; now: number }) {
  const start = run.startedAt ?? now;
  const end = Math.max(now, start + 1);
  const span = end - start;
  const rows = layout.stations.map((s) => s.name).filter((n) => index.nodes[n]);
  const [filter, setFilter] = useState<"all" | "tools" | "answers">("all");
  const items = run.activity.filter((a) => filter === "all" || (filter === "tools" ? a.kind === "tool" || a.kind === "result"
    : a.kind === "answer" || a.kind === "transfer" || a.kind === "loop" || a.kind === "user"));
  const click = (a: ActivityItem) => onSelect(a.toolId ? { kind: "tool", id: a.toolId } : a.stateKey
    ? { kind: "state", key: a.stateKey } : { kind: "agent", name: a.agent });

  if (!run.activity.length) return <Empty>Agent activity appears here as soon as you send a message.</Empty>;
  return (
    <div className="activity">
      <figure className="gantt" aria-label="When each agent was working">
        <figcaption>When each agent worked ({seconds(span)} so far). Ticks are tool calls.</figcaption>
        {rows.map((name) => {
          const a = run.agents[name];
          return (
            <button type="button" key={name} className="gantt-row" onClick={() => onSelect({ kind: "agent", name })}>
              <span className="gantt-name">{humanize(name)}</span>
              <span className="gantt-track">
                {a.spans.map((sp, i) => (
                  <span key={i} className={`gantt-bar ${sp.end === undefined ? "open" : ""}`}
                        style={{ left: `${((sp.start - start) / span) * 100}%`,
                          width: `${Math.max(0.6, (((sp.end ?? now) - sp.start) / span) * 100)}%`,
                          background: lineColor(layout, name) }} />
                ))}
                {a.toolCalls.map((id) => run.tools[id]).filter(Boolean).map((t) => (
                  <span key={t.id} className="gantt-tick" title={t.name} style={{ left: `${((t.startedAt - start) / span) * 100}%` }} />
                ))}
              </span>
            </button>
          );
        })}
      </figure>
      <div className="activity-list">
        <div className="segmented" role="radiogroup" aria-label="Show">
          {(["all", "tools", "answers"] as const).map((f) => (
            <button key={f} type="button" role="radio" aria-checked={filter === f} onClick={() => setFilter(f)}>
              {f === "all" ? "Everything" : f === "tools" ? "Tool calls" : "Answers and hand-overs"}
            </button>
          ))}
        </div>
        <ol>
          {items.slice().reverse().map((a) => (
            <li key={a.id} className={`act act-${a.kind}`}>
              <button type="button" onClick={() => click(a)}>
                <time>+{seconds(Math.max(0, a.at - start))}</time>
                <span className="act-dot" style={{ background: lineColor(layout, a.agent) }} />
                <span>{a.text}</span>
              </button>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ guide

export function GuidePanel({ run }: { run: RunState }) {
  const guide = typeof run.state.final_guide === "string" ? run.state.final_guide : "";
  const html = useMemo(() => (guide ? renderMarkdown(guide) : ""), [guide]);
  if (!guide) return <Empty>The guide appears here when the guide writer has saved it, usually 4 to 7 minutes after you confirm the trip.</Empty>;
  const brief = run.state.trip_brief as { destination?: string; start_date?: string; end_date?: string } | undefined;
  const slug = `${brief?.destination ?? "trip"}-${brief?.start_date ?? ""}-to-${brief?.end_date ?? ""}`.toLowerCase();
  const title = guide.match(/^#\s+(.+)$/m)?.[1] ?? "Trip guide";
  return (
    <div className="guide">
      <div className="guide-actions">
        <button type="button" onClick={() => download(`${slug}.md`, guide, "text/markdown")}>Download Markdown</button>
        <button type="button" onClick={() => download(`${slug}.html`, guideHtml(guide, title), "text/html")}>Download HTML</button>
      </div>
      <article className="md guide-body" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}

// ------------------------------------------------------------------ review

interface Decline { day: number | null; issue: string; reason: string }
interface ReviewRound { round: number; verdict: string; critic_said?: string; overridden?: boolean; must_fix: number; should_fix: number; total_eur?: number;
  decide?: number; declined?: Decline[]; declines_refused?: Decline[] }
interface RevisionRound { round: number; verdict?: string; changes?: string[] | null; restored?: string; exit?: string }

export function ReviewPanel({ run, onSelect }: { run: RunState; onSelect: (s: Selection) => void }) {
  const reviews = (run.state.review_log as ReviewRound[] | null) ?? [];
  const revisions = (run.state.revision_log as RevisionRound[] | null) ?? [];
  const critique = typeof run.state.critique === "string" ? run.state.critique : "";
  const overridden = typeof run.state.critique_overridden === "string" ? run.state.critique_overridden : "";
  if (!reviews.length) return <Empty>The plan critic reviews the plan after the itinerary planner, and the reviser fixes what it finds, for up to three rounds.</Empty>;
  return (
    <div className="review">
      <ol className="rounds">
        {reviews.map((r) => {
          const rev = revisions.find((x) => x.round === r.round);
          return (
            <li key={r.round} className={`round verdict-${r.verdict.toLowerCase()}`}>
              <h3>Round {r.round}: {r.verdict === "APPROVED" ? "approved" : "revise"}</h3>
              <p>
                {r.must_fix} must-fix and {r.should_fix} should-fix problems found by the code check
                {typeof r.total_eur === "number" ? `; plan total €${r.total_eur.toLocaleString("en")}` : ""}.
                {r.overridden ? ` The critic said ${r.critic_said}; the code check decided ${r.verdict}.` : ""}
                {r.decide ? ` ${r.decide} day-shape point${r.decide === 1 ? "" : "s"} for the critic to decide.` : ""}
              </p>
              {r.declined?.length ? (
                <ul className="declined">{r.declined.map((d, i) => (
                  <li key={i}>Critic declined day {d.day} {d.issue.replace(/_/g, " ")}: {d.reason}</li>))}</ul>
              ) : null}
              {r.declines_refused?.length ? (
                <p className="muted">Declines refused by code (no reason about this trip): {r.declines_refused.map((d) => `day ${d.day} ${d.issue.replace(/_/g, " ")}`).join(", ")}.</p>
              ) : null}
              {rev?.changes?.length ? (
                <ul>{rev.changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
              ) : rev?.exit ? <p className="muted">The reviser ended the loop ({rev.exit === "exit_loop" ? "called exit_loop" : "ended by the code guard"}).</p> : null}
              {rev?.restored && <p className="muted">Kept the previous plan: {rev.restored}.</p>}
            </li>
          );
        })}
      </ol>
      {critique && (
        <details className="critique">
          <summary>Latest critique</summary>
          <pre>{critique}</pre>
          {overridden && <><p className="muted">What the critic wrote before the code check approved:</p><pre>{overridden}</pre></>}
        </details>
      )}
      <button type="button" className="link-button" onClick={() => onSelect({ kind: "state", key: "itinerary_check" })}>
        Show the code check result
      </button>
    </div>
  );
}

// ------------------------------------------------------------------ metrics (cost and time per agent)

interface ReplanEntry { rerun?: string[]; accepted?: boolean; refused?: string; reason?: string; change_note?: string }
const BRANCH_NAMES = ["transport", "stay", "culture", "food"];

export function MetricsPanel({ index, run, now, onSelect }: Common & { now: number }) {
  const rows = agentMetrics(run, index.llmAgents, now).filter((m) => m.llmCalls || m.toolCalls || m.seconds);
  const reused = Array.isArray(run.state.research_reused) ? (run.state.research_reused as string[]) : [];
  const replans = (run.state.replan_log as ReplanEntry[] | null) ?? [];
  const extra = (run.state.extra_sights_log as { added?: string[] }[] | null) ?? [];
  if (!rows.length) return <Empty>Model answers, tokens, tool calls and working time per agent appear here during a run.</Empty>;
  const total = rows.reduce((t, m) => ({ llm: t.llm + m.llmCalls, tin: t.tin + m.tokensIn, tout: t.tout + m.tokensOut,
    tools: t.tools + m.toolCalls }), { llm: 0, tin: 0, tout: 0, tools: 0 });
  const n = (x: number) => x.toLocaleString("en");
  return (
    <div className="metrics">
      {reused.length > 0 && (
        <p className="badge badge-reuse" role="status">
          Research reused: {reused.join(", ")}. Researched again: {BRANCH_NAMES.filter((b) => !reused.includes(b)).join(", ") || "nothing"} (the concierge's choice).
        </p>
      )}
      {replans.length > 0 && (
        <ul className="replans">
          {replans.map((r, i) => (
            <li key={i} className={r.accepted ? "" : "refused"}>
              Re-plan {r.accepted ? "accepted" : "refused"}: rerun {r.rerun?.length ? r.rerun.join(", ") : "nothing"}
              {r.change_note ? ` ("${r.change_note}")` : ""}{r.refused ? `. Code refused: ${r.refused}` : ""}
            </li>
          ))}
        </ul>
      )}
      {extra.some((e) => e.added?.length) && (
        <p className="muted">Sights researched on demand by the reviser: {extra.flatMap((e) => e.added ?? []).join(", ")}.</p>
      )}
      <table className="data-table">
        <thead><tr><th scope="col">Agent</th><th scope="col">Model answers</th><th scope="col">Tokens in / out</th>
          <th scope="col">Tool calls</th><th scope="col">Working time</th></tr></thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.name}>
              <td><button type="button" className="link-button" onClick={() => onSelect({ kind: "agent", name: m.name })}>{humanize(m.name)}</button></td>
              <td>{m.llmCalls}</td>
              <td>{n(m.tokensIn)} / {n(m.tokensOut)}</td>
              <td>{m.toolCalls}</td>
              <td>{seconds(m.seconds)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot><tr><th scope="row">Total</th><td>{total.llm}</td><td>{n(total.tin)} / {n(total.tout)}</td><td>{total.tools}</td><td /></tr></tfoot>
      </table>
      <p className="muted">Working time is when each agent was active; parallel agents overlap. A replayed run keeps the recorded timing.</p>
    </div>
  );
}

// ------------------------------------------------------------------ data (state keys)

export function DataPanel({ run, onSelect }: { run: RunState; onSelect: (s: Selection) => void }) {
  const latest = new Map<string, { by: string; at: number; count: number }>();
  for (const w of run.writes) {
    const prev = latest.get(w.key);
    latest.set(w.key, { by: w.by, at: w.at, count: (prev?.count ?? 0) + 1 });
  }
  if (!latest.size) return <Empty>Agents share results through session state. Each key appears here when an agent writes it.</Empty>;
  const start = run.startedAt ?? 0;
  return (
    <table className="data-table">
      <thead><tr><th scope="col">Key</th><th scope="col">Last written by</th><th scope="col">When</th><th scope="col">Size</th></tr></thead>
      <tbody>
        {[...latest.entries()].map(([key, w]) => (
          <tr key={key}>
            <td><button type="button" className="link-button" onClick={() => onSelect({ kind: "state", key })}>{key}</button></td>
            <td>{humanize(w.by)}{w.count > 1 ? ` (${w.count} writes)` : ""}</td>
            <td>+{seconds(Math.max(0, w.at - start))}</td>
            <td>{sizeOf(run.state[key])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const sizeOf = (v: unknown) => {
  const n = typeof v === "string" ? v.length : JSON.stringify(v ?? null).length;
  return n > 1024 ? `${(n / 1024).toFixed(1)} KB` : `${n} B`;
};

// ------------------------------------------------------------------ details (inspector)

export function DetailsPanel({ index, run, selection, onSelect }: Common & { selection: Selection | null }) {
  if (!selection) return <Empty>Select an agent on the map, a tool call or a state key to see its details.</Empty>;
  if (selection.kind === "tool") {
    const t = run.tools[selection.id];
    if (!t) return <Empty>That tool call is not part of this run.</Empty>;
    return (
      <div className="details">
        <h3>{t.name}</h3>
        <p className="muted">
          Called by <button type="button" className="link-button" onClick={() => onSelect({ kind: "agent", name: t.agent })}>{humanize(t.agent)}</button>
          {t.endedAt !== undefined ? `, answered after ${seconds(t.endedAt - t.startedAt)}` : ", still running"}
          {t.status === "error" ? ", returned an error" : ""}.
        </p>
        <h4>Arguments</h4>
        <pre className="json">{JSON.stringify(t.args, null, 2)}</pre>
        <h4>Result</h4>
        <pre className="json">{t.response ? JSON.stringify(t.response, null, 2) : "Waiting for the result…"}</pre>
      </div>
    );
  }
  if (selection.kind === "state") {
    const value = run.state[selection.key];
    const writes = run.writes.filter((w) => w.key === selection.key);
    return (
      <div className="details">
        <h3>{selection.key}</h3>
        <p className="muted">{writes.length
          ? `Written by ${[...new Set(writes.map((w) => humanize(w.by)))].join(", ")}.`
          : "Not written yet in this run."}</p>
        {value !== undefined && <pre className="json">{pretty(value)}</pre>}
      </div>
    );
  }
  const node = index.nodes[selection.name];
  const a = run.agents[selection.name];
  if (!node || !a) return <Empty>Unknown agent.</Empty>;
  const total = a.spans.reduce((t, s) => t + ((s.end ?? run.now) - s.start), 0);
  const kindText = { llm: "LLM agent", sequential: "Runs its steps one after another", parallel: "Runs its branches at the same time",
    loop: `Repeats its steps, up to ${node.max_iterations ?? "?"} rounds` }[node.kind];
  return (
    <div className="details">
      <h3>{humanize(node.name)}</h3>
      <p className="muted">{kindText}. {a.spans.length ? `Worked ${seconds(total)}${a.spans.length > 1 ? ` in ${a.spans.length} runs` : ""}.` : "Has not started yet."}</p>
      <p>{node.description}</p>
      {node.role && <blockquote>{node.role}</blockquote>}
      {node.tools && node.tools.length > 0 && (<>
        <h4>Tools</h4>
        <ul className="pill-list">{node.tools.map((t) => <li key={t}>{t}</li>)}</ul>
      </>)}
      {node.output_key && (
        <p>Writes <button type="button" className="link-button" onClick={() => onSelect({ kind: "state", key: node.output_key! })}>{node.output_key}</button> to the session state.</p>
      )}
      {a.toolCalls.length > 0 && (<>
        <h4>Tool calls in this run</h4>
        <ol className="call-list">
          {a.toolCalls.map((id) => run.tools[id]).filter(Boolean).map((t) => (
            <li key={t.id}>
              <button type="button" onClick={() => onSelect({ kind: "tool", id: t.id })}>
                <strong>{t.name}</strong> <span className={`badge badge-${t.status}`}>{t.status === "ok" ? "ok" : t.status}</span>
                <span className="muted"> {argSummary(t.args)}</span>
              </button>
            </li>
          ))}
        </ol>
      </>)}
      {a.reasoning.length > 0 && (
        <details>
          <summary>Model reasoning ({a.reasoning.length} notes)</summary>
          {a.reasoning.map((r, i) => <p key={i} className="reasoning">{r}</p>)}
        </details>
      )}
      {a.lastText && (
        <details>
          <summary>Last answer</summary>
          <pre className="json">{pretty(parseValue(a.lastText))}</pre>
        </details>
      )}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="empty-state">{children}</p>;
}
