/** The agent tree as exported by scripts/export_agent_graph.py. */
export interface AgentNode {
  name: string;
  kind: "llm" | "sequential" | "parallel" | "loop";
  description: string;
  tools?: string[];
  output_key?: string | null;
  role?: string;
  transfers_to?: string[];
  max_iterations?: number | null;
  children?: AgentNode[];
}

/** One part of an ADK event's content (camelCase, as the API server sends it). */
export interface AdkPart {
  text?: string;
  thought?: boolean;
  functionCall?: { id?: string; name: string; args?: Record<string, unknown> };
  functionResponse?: { id?: string; name: string; response?: Record<string, unknown> };
}

export interface AdkEvent {
  id: string;
  author: string;
  timestamp: number;
  invocationId?: string;
  branch?: string;
  partial?: boolean;
  content?: { role?: string; parts?: AdkPart[] } | null;
  actions?: { stateDelta?: Record<string, unknown>; transferToAgent?: string; escalate?: boolean } | null;
  errorCode?: string;
  errorMessage?: string;
  usageMetadata?: { promptTokenCount?: number; candidatesTokenCount?: number };
}

/** A recorded run (scripts/record_ui_replay.py, or "Save this run" in the UI). */
export interface ReplayFile {
  title: string;
  recorded_at: string;
  turns: { user: string; sent_at: number; events: AdkEvent[] }[];
  final_state?: Record<string, unknown>;
}
