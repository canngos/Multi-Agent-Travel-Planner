/**
 * Client for ADK's API server (`adk api_server`), reached through the dev-server proxy at /adk.
 * Endpoints: GET /list-apps, POST /apps/{app}/users/{user}/sessions, GET .../sessions/{id}, POST /run_sse.
 */
import type { AdkEvent } from "./types";

const BASE = "/adk";
export const APP = "travel_planner";
const USER = "web-user";

export async function checkServer(): Promise<{ ok: boolean; reason?: string }> {
  try {
    const res = await fetch(`${BASE}/list-apps`);
    if (!res.ok) return { ok: false, reason: `the API server answered ${res.status}` };
    const apps = (await res.json()) as string[];
    return apps.includes(APP) ? { ok: true } : { ok: false, reason: `the server has no "${APP}" app` };
  } catch {
    return { ok: false, reason: "the API server is not reachable" };
  }
}

export async function createSession(): Promise<string> {
  const res = await fetch(`${BASE}/apps/${APP}/users/${USER}/sessions`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  });
  if (!res.ok) throw new Error(`Could not start a session (${res.status}).`);
  return ((await res.json()) as { id: string }).id;
}

export async function getSessionState(sessionId: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE}/apps/${APP}/users/${USER}/sessions/${sessionId}`);
  if (!res.ok) return {};
  return ((await res.json()) as { state?: Record<string, unknown> }).state ?? {};
}

/** Sends one user message and calls onEvent for every event the agents produce, as they arrive. */
export async function runTurn(sessionId: string, text: string, onEvent: (e: AdkEvent) => void,
                              signal?: AbortSignal): Promise<void> {
  const res = await fetch(`${BASE}/run_sse`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ app_name: APP, user_id: USER, session_id: sessionId, streaming: false,
      new_message: { role: "user", parts: [{ text }] } }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`The planner did not accept the message (${res.status}).`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = chunk.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
      if (!data) continue;
      const parsed = JSON.parse(data) as AdkEvent & { error?: string };
      if (parsed.error) throw new Error(parsed.error);
      onEvent(parsed);
    }
  }
}
