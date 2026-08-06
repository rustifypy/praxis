export type EventType =
  | "user"
  | "thinking"
  | "text"
  | "tool_start"
  | "tool_end"
  | "compacted"
  | "error"
  | "done";

export interface AgentEvent {
  id: string;
  type: EventType;
  // Live events carry `delta`; replayed ones carry the whole `text`. The reducer
  // treats them differently — appending a replayed block would double it.
  data: Record<string, any>;
  parent_id: string | null;
  depth: number;
  seq?: number;
  turn_id?: string;
}

export interface Chat {
  id: string;
  title: string;
  owner_id: string;
  cube_url: string;
  created_at: number;
  updated_at: number;
}

export interface ToolEnd {
  ok: boolean;
  sql?: string;
  query?: unknown;
  rows_total?: number;
  preview?: Record<string, unknown>[];
  duration_ms?: number;
  warnings?: string[];
  errors?: string[];
  hits?: number;
}

/**
 * `live` marks a block still being streamed into — it drives the caret, the shimmer
 * and the running spinner. Replayed transcripts never set it, which is why a reopened
 * chat renders instantly instead of pretending to work.
 */
export type Item =
  | { kind: "user"; text: string }
  | { kind: "thinking"; text: string; open: boolean; live: boolean; startedAt?: number; ms?: number }
  | { kind: "answer"; text: string; live: boolean }
  | {
      kind: "tool";
      tool: string;
      label: string;
      query?: unknown;
      end?: ToolEnd;
      startedAt?: number;
      ms?: number;
    }
  | { kind: "compacted"; before: number; after: number }
  | { kind: "error"; message: string };
