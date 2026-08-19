type EventType =
  | "user"
  | "thinking"
  | "text"
  | "tool_start"
  | "output"
  | "approval"
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
  /** 'question' or 'model' — which agent this chat runs. */
  kind: string;
  branch: string;
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
  /** Handle for the whole result set. Empty on the `/ask` path, which has no chat to
   *  hang one on, so a result block always checks before offering "show all". */
  result_id?: string;
  preview?: Record<string, unknown>[];
  duration_ms?: number;
  warnings?: string[];
  errors?: string[];
  hits?: number;
  /** What a dashboard tool did, in a few words — the whole of what those tools say, since
   *  they may not put state in an event. Used as the trace step's summary. */
  note?: string;
  /** Workspace paths a script wrote or changed, from `run_python`. Paths only: the files are on
   *  disk in the bind mount, and the download route is how they are read. */
  wrote?: string[];
  /** What a script printed, trimmed. The whole of it is in the workspace log. */
  output?: string;
}

/**
 * `live` marks a block still being streamed into — it drives the caret, the shimmer
 * and the running spinner. Replayed transcripts never set it, which is why a reopened
 * chat renders instantly instead of pretending to work.
 */
export type Item =
  /** `files` are workspace paths the question arrived with. They ride on the user event because
   *  an upload happens between turns and has no turn of its own to belong to. */
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
      /** What a running script has printed so far. Live only — `output` events are not stored,
       *  so a replayed transcript reads the whole thing off `end` instead. */
      live?: string;
      /** The script this step is about to run, from the `approval` event. Live only, and the
       *  step falls back to reading `label` off disk — so a replayed run still shows it. */
      script?: string;
      /** This step is the one waiting on a click. Not derived from `script`, because after
       *  "always" a run is approved with no approval event at all. */
      awaiting?: boolean;
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
  /**
   * What a script wrote, as its own block below the answer.
   *
   * No new event type: `run_python` names its files on `tool_end`, the same way `set_chart`
   * authors its own events. `label` is the script that made them.
   */
  | { kind: "files"; label: string; paths: string[] }
  | { kind: "compacted"; before: number; after: number }
  | { kind: "error"; message: string };

/** `results` is keyed by tile id rather than nested, because a tile always exists and its
 *  data may not — an unrefreshed dashboard has every tile and no results. */
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
  /** Why this tile draws a table instead of its chart. Recomputed server-side on every read,
   *  so a reload cannot resurrect a chart the shape outgrew. */
  degraded: string;
  columns: ResultColumn[];
  rows: Record<string, unknown>[];
  row_count: number;
  /** What Cube resolved each dateRange to. The only thing saying whether a refresh moved
   *  this tile's window — two tiles can differ under the same click. */
  resolved_ranges: Record<string, string[]>;
  duration_ms: number | null;
  /** The run happened and its rows were pruned. Count survives, data does not. */
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
  /** From `layout.MAX_COLUMNS`, so the column picker cannot offer a width the server refuses. */
  max_columns: number;
  layout_version: number | null;
  history: { version: number; note: string; created_at: number }[];
  run: {
    id: string;
    status: string;
    started_at: number;
    finished_at: number | null;
  } | null;
  results: Record<string, TileResultRow>;
  /** Tiles that no longer compile. `available` is false when the container is down, which is
   *  not the same as nothing being broken. */
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
