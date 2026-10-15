type EventType =
  | "user"
  | "thinking"
  | "text"
  | "tool_start"
  | "output"
  | "approval"
  | "proposal"
  | "tool_end"
  | "compacted"
  | "error"
  | "done";

export interface AgentEvent {
  id: string;
  type: EventType;
  // Live events carry `delta`; replayed ones carry the whole `text`.
  data: Record<string, any>;
  parent_id: string | null;
  depth: number;
  seq?: number;
  turn_id?: string;
}

export interface Chat {
  id: string;
  title: string;
  /** 'question' or 'model' — which agent this chat runs. */
  kind: string;
  /** The model ref a 'model' chat edits. Empty on a question chat, which always reads main. */
  ref: string;
  created_at: number;
  updated_at: number;
}

/** Computed server-side by `shape.columns_of`, so the table and chart agree. */
export interface ResultColumn {
  id: string;
  label: string;
  kind: "time" | "dimension" | "measure";
  type: string;
  /** Cube's own display format for a measure: `currency`, `percent`, or empty. */
  format: string;
  align: "left" | "right";
}

export type ChartType =
  | "line"
  | "area"
  | "stacked_area"
  | "bar"
  | "stacked_bar"
  | "grouped_bar"
  | "hbar"
  | "donut"
  | "scatter"
  | "heatmap"
  | "gauge"
  | "sparkline"
  | "kpi"
  | "stat";

/** Column ids only — labels and formats come from `ResultColumn`. A null chart is normal. */
export interface ChartSpec {
  type: ChartType;
  y: string[];
  x: string;
  /** Column whose values split `y` into one line each. Empty for a single series. */
  series: string;
  /** The figure a gauge is measured against. Null on every other type. */
  target?: number | null;
}

export interface ToolEnd {
  ok: boolean;
  chart?: ChartSpec | null;
  sql?: string;
  query?: unknown;
  rows_total?: number;
  columns?: ResultColumn[];
  /** Handle for the whole result set. Empty on the `/ask` path, which has no chat. */
  result_id?: string;
  preview?: Record<string, unknown>[];
  duration_ms?: number;
  warnings?: string[];
  errors?: string[];
  hits?: number;
  /** What a dashboard tool did, in a few words; they may not put state in an event. */
  note?: string;
  /** Workspace paths a script wrote or changed, from `run_python`. */
  wrote?: string[];
  /** What a script printed, trimmed. The whole of it is in the workspace log. */
  output?: string;
}

/** `live` marks a block still streaming; replayed transcripts never set it. */
export type Item =
  /** `files`: workspace paths uploaded with the question — an upload has no turn of its own. */
  | { kind: "user"; text: string; files: string[] }
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
      /** What a running script has printed so far. Live only; replay reads `end`. */
      live?: string;
      /** The script about to run, from the `approval` event. Live only. */
      script?: string;
      /** Waiting on a click. Not derived from `script`: "always" approves with no event. */
      awaiting?: boolean;
    }
  /** `rows` is the head the event carried; `rowCount` is the true total, fetched via `resultId`. */
  | {
      kind: "result";
      resultId: string;
      label: string;
      columns: ResultColumn[];
      rows: Record<string, unknown>[];
      rowCount: number;
      chart: ChartSpec | null;
    }
  /** What a script wrote; `label` is the script. */
  | { kind: "files"; label: string; paths: string[] }
  /** A model file the editor agent wants to write; reviewed in the editor pane, not here.
   *  `text` is null for a proposed deletion. */
  | { kind: "proposal"; path: string; text: string | null; ref: string }
  | { kind: "compacted"; before: number; after: number }
  | { kind: "error"; message: string };

/** `desktop` and `cli` are built from the address the server binds, not the browser's. */
export interface McpAccess {
  enabled: boolean;
  token: string;
  url: string;
  desktop: string;
  cli: string;
  tools: { name: string; description: string }[];
}

export interface DashboardRow {
  id: string;
  title: string;
  tiles: number;
  refreshed_at: number | null;
  updated_at: number;
}

export interface TileRow {
  id: string;
  title: string;
  query: unknown;
  chart: TileChart;
}

/** `table` has no axes — it draws whatever columns the query returned. */
export interface TileChart {
  type: ChartType | "table";
  y?: string[];
  x?: string;
  series?: string;
  target?: number | null;
}

export interface TileDelta {
  column: string;
  label: string;
  value: number;
  previous: number;
  change: number;
  /** Null when the previous value was zero; the absolute change is still shown. */
  percent: number | null;
}

export interface TileResultRow {
  /** Why this tile draws a table instead of its chart. Recomputed on every read. */
  degraded: string;
  columns: ResultColumn[];
  rows: Record<string, unknown>[];
  row_count: number;
  /** What Cube resolved each dateRange to — the only sign a refresh moved the window. */
  resolved_ranges: Record<string, string[]>;
  duration_ms: number | null;
  /** Rows were pruned; the count survives. */
  pruned: boolean;
  deltas: TileDelta[];
  compared_at: number | null;
}

export interface LayoutItem {
  tile: string;
  span: number;
}

export interface DashboardDetail {
  id: string;
  title: string;
  tiles: TileRow[];
  layout: { columns: number; items: LayoutItem[] };
  /** From `layout.MAX_COLUMNS`. */
  max_columns: number;
  layout_version: number | null;
  history: { version: number; note: string; created_at: number }[];
  run: {
    id: string;
    status: string;
    started_at: number;
    finished_at: number | null;
  } | null;
  /** Keyed by tile id: an unrefreshed dashboard has every tile and no results. */
  results: Record<string, TileResultRow>;
  /** Tiles that no longer compile. `available: false` means unchecked, not unbroken. */
  checks: { available: boolean; reason: string; broken: { tile_id: string; title: string; errors: string[] }[] };
}

export interface RefreshOutcome {
  ok: boolean;
  status: string;
  run_id: string;
  error: string;
  tiles: {
    tile_id: string;
    title: string;
    ok: boolean;
    errors: string[];
    warnings: string[];
    row_count: number;
    degraded: string;
  }[];
}

// --- flows -------------------------------------------------------------------

/** `ok` is whether the folder is a runnable Flow, decided server-side. */
export interface FlowSummary {
  name: string;
  title: string;
  description: string;
  phases: string[];
  ok: boolean;
  errors: string[];
  warnings: string[];
  chat_id: string;
}

/** A Phase's contract, as `phase.yml` declares it. */
export interface PhaseSpec {
  dir_name: string;
  number: number;
  kind: "agent" | "script";
  review: "required" | "none";
  tools: string[];
  inputs: { kind: "reference" | "working"; path: string }[];
  outputs: string[];
  script: string;
}

export interface FlowDetail extends FlowSummary {
  phases_detail: PhaseSpec[];
  files: string[];
}

/** From the Phase's folder as it is now; `title`/`purpose` are the first heading and
 *  paragraph of its CONTEXT.md. */
export interface PhasePlan {
  kind: "agent" | "script";
  review: "required" | "none";
  tools: string[];
  inputs: { kind: "reference" | "working"; path: string }[];
  outputs: string[];
  may_be_empty: string[];
  /** Prepared with the Flow: query files in queries/, scripts beside phase.yml. */
  queries: string[];
  scripts: string[];
  title: string;
  purpose: string;
}

/** `outputs` is what it declared and produced; `output`/`scratch` are what is on disk. */
export interface RunPhase {
  dir_name: string;
  status: "pending" | "running" | "done" | "failed" | "stale";
  started_at: number;
  ended_at: number;
  error: string;
  outputs: string[];
  output: string[];
  scratch: string[];
  /** Null when the Phase was renamed or removed since this run began. */
  plan: PhasePlan | null;
}

export interface RunSummary {
  id: string;
  flow: string;
  status: "running" | "paused" | "done" | "failed";
  current: string;
  /** A Gate is the only pause `run.json` holds; an approval lives on the live stream. */
  waiting: "" | "gate";
  started_at: number;
  ended_at: number;
  error: string;
  phase_count: number;
}

export interface RunDetail extends Omit<RunSummary, "phase_count"> {
  phases: RunPhase[];
  brief: string;
  /** The one Phase whose output may be edited now (only at its Gate), or "". */
  editable: string;
}
