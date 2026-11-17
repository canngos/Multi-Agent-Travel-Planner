/** Replay files: flattening into timed actions, and building one from a live run ("Save this run"). */
import type { RunAction } from "./run";
import type { AdkEvent, ReplayFile } from "./types";

export interface TimedAction {
  at: number;
  action: RunAction;
}

export function flattenReplay(file: ReplayFile): TimedAction[] {
  const items: TimedAction[] = [];
  for (const turn of file.turns) {
    const first = turn.events[0]?.timestamp ?? turn.sent_at;
    const userAt = Math.min(turn.sent_at, first);
    items.push({ at: userAt, action: { type: "user", text: turn.user, at: userAt } });
    for (const event of turn.events) items.push({ at: event.timestamp, action: { type: "event", event } });
    const last = turn.events[turn.events.length - 1]?.timestamp ?? userAt;
    items.push({ at: last, action: { type: "idle", at: last } });
  }
  return items.sort((a, b) => a.at - b.at);
}

export function buildReplay(title: string, turns: { user: string; sent_at: number; events: AdkEvent[] }[],
                            finalState: Record<string, unknown>): ReplayFile {
  return { title, recorded_at: new Date().toISOString(), turns, final_state: finalState };
}

export function isReplayFile(value: unknown): value is ReplayFile {
  const v = value as ReplayFile;
  return !!v && Array.isArray(v.turns) && v.turns.every((t) => typeof t.user === "string" && Array.isArray(t.events));
}
