import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type ChangeEvent } from "react";

import { Chat } from "./components/Chat";
import { ActivityPanel, DataPanel, DetailsPanel, GuidePanel, ReviewPanel } from "./components/Panels";
import { RouteMap } from "./components/RouteMap";
import type { Selection } from "./components/selection";
import graphJson from "./generated/agent-graph.json";
import { seconds } from "./lib/format";
import { indexGraph, layoutMap } from "./lib/graph";
import { useLiveRun, useReplay, useServerStatus, useTicker } from "./lib/hooks";
import { isReplayFile } from "./lib/replay";
import { createReducer, initialRun, isWorking } from "./lib/run";
import type { AgentNode, ReplayFile } from "./lib/types";

const index = indexGraph(graphJson as AgentNode);
const layout = layoutMap(index.root);
const DEMO_REPLAY = "/replays/lisbon-demo.json";

type Tab = "activity" | "guide" | "review" | "details" | "data";
const TABS: { id: Tab; label: string }[] = [
  { id: "activity", label: "Activity" },
  { id: "guide", label: "Guide" },
  { id: "review", label: "Review" },
  { id: "details", label: "Details" },
  { id: "data", label: "Shared state" },
];

export default function App() {
  const reducer = useMemo(() => createReducer(index), []);
  const [run, dispatch] = useReducer(reducer, index, initialRun);
  const [mode, setMode] = useState<"live" | "replay">("live");
  const [server, recheck] = useServerStatus();
  const live = useLiveRun(dispatch);
  const replay = useReplay(dispatch);
  const [tab, setTab] = useState<Tab>("activity");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [replayError, setReplayError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const wall = useTicker(mode === "live" && run.busy);
  const now = mode === "replay" ? replay.time : Math.max(run.now, run.busy ? wall : run.now);

  const select = useCallback((s: Selection) => { setSelection(s); setTab("details"); }, []);

  const loadReplay = useCallback(async (source: string | ReplayFile) => {
    setReplayError("");
    try {
      const file = typeof source === "string" ? await (await fetch(source)).json() : source;
      if (!isReplayFile(file)) throw new Error("This file is not a saved trip-planner run.");
      setSelection(null);
      setTab("activity");
      replay.load(file);
    } catch (err) {
      setReplayError((err as Error).message);
    }
  }, [replay]);

  const switchMode = (next: "live" | "replay") => {
    if (next === mode) return;
    replay.setPlaying(false);
    live.restart();
    setSelection(null);
    setTab("activity");
    setMode(next);
    if (next === "replay") void loadReplay(DEMO_REPLAY);
  };

  useEffect(() => {
    if (typeof run.state.final_guide === "string" && tab === "activity" && !run.busy && mode === "live") setTab("guide");
  }, [run.state.final_guide, run.busy, mode, tab]);

  const openFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    try {
      await loadReplay(JSON.parse(await f.text()));
    } catch {
      setReplayError("This file is not valid JSON.");
    }
  };

  const working = index.llmAgents.filter((n) => isWorking(run.agents[n].status)).length;
  const done = index.llmAgents.filter((n) => run.agents[n].status === "done").length;
  const toolCount = run.toolOrder.filter((id) => run.tools[id]?.name !== "transfer_to_agent").length;
  const canSend = mode === "live" && server.state === "ok" && !live.sending;

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <h1>Trip planner</h1>
          <p>Thirteen AI agents research, plan and check your trip. Watch them work.</p>
        </div>
        <div className="segmented mode" role="radiogroup" aria-label="Mode">
          <button type="button" role="radio" aria-checked={mode === "live"} onClick={() => switchMode("live")}>Plan a trip</button>
          <button type="button" role="radio" aria-checked={mode === "replay"} onClick={() => switchMode("replay")}>Watch a recorded run</button>
        </div>
        {mode === "live" ? (
          <div className={`server server-${server.state}`} role="status">
            <span className="server-dot" />
            {server.state === "ok" ? "Planner server connected" : server.state === "checking" ? "Connecting to the planner server…"
              : <>Planner server not running <button type="button" className="link-button" onClick={recheck}>Try again</button></>}
          </div>
        ) : (
          <div className="server server-ok" role="status"><span className="server-dot" />{replay.file?.title ?? "Loading the recording…"}</div>
        )}
      </header>

      {mode === "live" && server.state === "down" && (
        <div className="banner" role="alert">
          <p>
            The planner server is not reachable ({server.reason}). Start it in the project folder with{" "}
            <code>adk api_server .</code>, or with <code>DEMO=1</code> set to replay the recorded Lisbon demo without
            network access.
          </p>
          <button type="button" onClick={() => switchMode("replay")}>Watch a recorded run instead</button>
        </div>
      )}

      <main className="layout">
        <Chat messages={run.chat} mode={mode} canSend={canSend} busy={run.busy && mode === "live"} sent={live.sent}
              onSend={(t) => void live.send(t)} onOpenGuide={() => setTab("guide")} />

        <div className="work">
          <section className="map-card" aria-labelledby="map-title">
            <header className="map-head">
              <h2 id="map-title">How your trip is planned</h2>
              <p className="map-stats">
                {run.eventCount === 0 ? "Each station is an agent. Lines show who hands work to whom."
                  : `${working} working, ${done} of ${index.llmAgents.length} done, ${toolCount} tool calls${run.startedAt ? `, ${seconds(Math.max(0, now - run.startedAt))}` : ""}`}
              </p>
              {mode === "live" ? (
                <div className="map-actions">
                  <button type="button" onClick={() => void live.save()} disabled={!live.hasRun || live.sending}>Save this run</button>
                  <button type="button" onClick={() => { live.restart(); setSelection(null); }} disabled={!live.hasRun}>New conversation</button>
                </div>
              ) : (
                <div className="map-actions">
                  <button type="button" onClick={() => fileInput.current?.click()}>Open a saved run</button>
                  <input ref={fileInput} type="file" accept="application/json,.json" hidden onChange={openFile} />
                </div>
              )}
            </header>
            <div className="map-scroll">
              <RouteMap index={index} layout={layout} run={run} now={now} selection={selection} onSelect={select} />
            </div>
            <ul className="legend" aria-label="Lines">
              {layout.lines.map((l) => (
                <li key={l.id}><span style={{ background: l.color }} />{l.label}</li>
              ))}
              <li className="legend-note">Click a station for its tools, results and reasoning.</li>
            </ul>
            {mode === "replay" && (
              <div className="player">
                <button type="button" className="play" onClick={() => (replay.finished ? replay.seek(replay.start) : null, replay.setPlaying(!replay.playing))}
                        aria-label={replay.playing ? "Pause" : "Play"}>{replay.playing ? "Pause" : replay.finished ? "Play again" : "Play"}</button>
                <label className="visually-hidden" htmlFor="scrub">Position in the recording</label>
                <input id="scrub" type="range" min={replay.start} max={replay.end} step="any" value={Math.min(replay.time, replay.end)}
                       onChange={(e) => replay.seek(Number(e.target.value))} />
                <span className="player-time">{seconds(Math.max(0, Math.min(replay.time, replay.end) - replay.start))} of {seconds(Math.max(0, replay.end - replay.start))}</span>
                <label className="speed">Speed{" "}
                  <select value={replay.speed} onChange={(e) => replay.setSpeed(Number(e.target.value))}>
                    {[0.5, 1, 2, 4, 8].map((s) => <option key={s} value={s}>{s}×</option>)}
                  </select>
                </label>
                {replayError && <span className="error-text" role="alert">{replayError}</span>}
              </div>
            )}
          </section>

          <section className="tabs-card">
            <div className="tabs" role="tablist" aria-label="Run details">
              {TABS.map((t) => (
                <button key={t.id} type="button" role="tab" id={`tab-${t.id}`} aria-selected={tab === t.id} aria-controls="tab-panel"
                        onClick={() => setTab(t.id)}>
                  {t.label}
                  {t.id === "guide" && typeof run.state.final_guide === "string" && <span className="tab-dot" aria-label="ready" />}
                </button>
              ))}
            </div>
            <div className="tab-panel" id="tab-panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
              {tab === "activity" && <ActivityPanel index={index} run={run} layout={layout} now={now} onSelect={select} />}
              {tab === "guide" && <GuidePanel run={run} />}
              {tab === "review" && <ReviewPanel run={run} onSelect={select} />}
              {tab === "details" && <DetailsPanel index={index} run={run} selection={selection} onSelect={select} />}
              {tab === "data" && <DataPanel run={run} onSelect={select} />}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
