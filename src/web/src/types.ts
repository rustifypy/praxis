type EventType =
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

/** One column of a result set, computed server-side by `cube.shape.columns_of` — so the
 *  table's order, headings and number formats are the same ones the chart reads. */
export interface ResultColumn {
  id: string;
  label: string;
  kind: "time" | "dimension" | "measure";
  type: string;
  /** Cube's own display format for a measure: `currency`, `percent`, or empty. */
  format: string;
  align: "left" | "right";
}

/**
 * What to draw, derived server-side by `cube.visualize.chart_for` — or replaced by the
 * agent's `set_chart`. Column ids only: the labels and number formats come from
 * `ResultColumn`, and carrying a second copy here is how the two would drift.
 *
 * `null` is a normal outcome. Two dimensions crossed or forty categories is a table.
 */
export interface ChartSpec {
  type: "line" | "bar" | "kpi";
  y: string[];
  x: string;
  /** Column whose values split `y` into one line each. Empty for a single series. */
  series: string;
}

export interface ToolEnd {
  ok: boolean;
  chart?: ChartSpec | null;
  sql?: string;
  query?: unknown;
  rows_total?: number;
  columns?: ResultColumn[];
  /** Handle for the whole result set. Empty on the `/ask` path, which has no chat to
   *  hang one on, so a result block always checks before offering "show all". */
  result_id?: string;
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
  /**
   * A query's data, as its own block rather than a fold inside the trace step.
   *
   * `rows` is the head the event carried, which is what makes a replayed transcript
   * render without a fetch; `rowCount` is how many there really are, and the difference
   * is what "show all" closes by way of `resultId`.
   */
  | {
      kind: "result";
      resultId: string;
      label: string;
      columns: ResultColumn[];
      rows: Record<string, unknown>[];
      rowCount: number;
      chart: ChartSpec | null;
    }
  | { kind: "compacted"; before: number; after: number }
  | { kind: "error"; message: string };
