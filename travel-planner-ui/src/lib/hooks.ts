import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch } from "react";

import { checkServer, createSession, getSessionState, runTurn } from "./adk";
import { buildReplay, flattenReplay } from "./replay";
import type { RunAction } from "./run";
import type { AdkEvent, ReplayFile } from "./types";

export type ServerStatus = { state: "checking" } | { state: "ok" } | { state: "down"; reason: string };

export function useServerStatus(): [ServerStatus, () => void] {
  const [status, setStatus] = useState<ServerStatus>({ state: "checking" });
  const check = useCallback(() => {
    setStatus({ state: "checking" });
    checkServer().then((r) => setStatus(r.ok ? { state: "ok" } : { state: "down", reason: r.reason ?? "unknown" }));
  }, []);
  useEffect(check, [check]);
  return [status, check];
}

/** Live chat with the planner through the API server. */
export function useLiveRun(dispatch: Dispatch<RunAction>) {
  const session = useRef<string | null>(null);
  const turns = useRef<{ user: string; sent_at: number; events: AdkEvent[] }[]>([]);
  const abort = useRef<AbortController | null>(null);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<string[]>([]);

  const send = useCallback(async (text: string) => {
    const now = Date.now() / 1000;
    setSending(true);
    setSent((s) => [...s, text]);
    dispatch({ type: "user", text, at: now });
    const turn = { user: text, sent_at: now, events: [] as AdkEvent[] };
    turns.current.push(turn);
    abort.current = new AbortController();
    try {
      session.current ??= await createSession();
      await runTurn(session.current, text, (event) => {
        turn.events.push(event);
        dispatch({ type: "event", event });
      }, abort.current.signal);
      dispatch({ type: "idle", at: Date.now() / 1000 });
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        if (!turn.events.length) {  // nothing reached the planner: let the user send the same message again
          turns.current.pop();
          setSent((s) => s.slice(0, -1));
        }
        dispatch({ type: "error", message: `The planner stopped: ${(err as Error).message}`, at: Date.now() / 1000 });
      } else {
        dispatch({ type: "idle", at: Date.now() / 1000 });
      }
    } finally {
      setSending(false);
    }
  }, [dispatch]);

  const restart = useCallback(() => {
    abort.current?.abort();
    session.current = null;
    turns.current = [];
    setSent([]);
    dispatch({ type: "reset" });
  }, [dispatch]);

  const save = useCallback(async () => {
    const state = session.current ? await getSessionState(session.current) : {};
    const brief = state.trip_brief as { destination?: string } | undefined;
    const file = buildReplay(`${brief?.destination ?? "Trip"} (saved run)`, turns.current, state);
    const blob = new Blob([JSON.stringify(file)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `trip-planner-run-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, []);

  return { send, restart, save, sending, sent, hasRun: sent.length > 0 };
}

const MAX_GAP = 4; // seconds; longer quiet stretches in a recording are shortened when playing

/** Plays a recorded run through the same reducer, on a virtual clock. */
export function useReplay(dispatch: Dispatch<RunAction>) {
  const [file, setFile] = useState<ReplayFile | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [time, setTime] = useState(0);
  const items = useMemo(() => (file ? flattenReplay(file) : []), [file]);
  const clock = useRef({ t: 0, idx: 0 });
  const speedRef = useRef(speed);
  speedRef.current = speed;
  const start = items[0]?.at ?? 0;
  const end = items[items.length - 1]?.at ?? 0;

  const load = useCallback((f: ReplayFile, autoplay = true) => {
    dispatch({ type: "reset" });
    setFile(f);
    const first = flattenReplay(f)[0]?.at ?? 0;
    clock.current = { t: first, idx: 0 };
    setTime(first);
    setPlaying(autoplay);
  }, [dispatch]);

  const seek = useCallback((position: number) => {
    // the slider rounds to its step: treat "at the end" as after the last event
    const target = position >= (items[items.length - 1]?.at ?? 0) - 0.25 ? Infinity : position;
    dispatch({ type: "reset" });
    let idx = 0;
    while (idx < items.length && items[idx].at <= target) dispatch(items[idx++].action);
    const t = Number.isFinite(target) ? target : items[items.length - 1]?.at ?? 0;
    clock.current = { t, idx };
    setTime(t);
  }, [dispatch, items]);

  useEffect(() => {
    if (!playing || !items.length) return;
    let frame = 0;
    let last = performance.now();
    let lastPaint = 0;
    const tick = (nowMs: number) => {
      const dt = Math.min(0.25, (nowMs - last) / 1000);
      last = nowMs;
      const c = clock.current;
      c.t += dt * speedRef.current;
      const next = items[c.idx];
      if (next && next.at - c.t > MAX_GAP) c.t = next.at - 0.5; // skip long waits
      while (c.idx < items.length && items[c.idx].at <= c.t) dispatch(items[c.idx++].action);
      if (nowMs - lastPaint > 80) { setTime(c.t); lastPaint = nowMs; }
      if (c.idx >= items.length) { setTime(c.t); setPlaying(false); return; }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, items, dispatch]);

  return {
    file, load, playing, setPlaying, speed, setSpeed, seek, time, start, end,
    finished: !!items.length && clock.current.idx >= items.length,
  };
}

export function useTicker(active: boolean, ms = 400): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now() / 1000), ms);
    return () => clearInterval(id);
  }, [active, ms]);
  return now;
}
