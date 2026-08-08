/**
 * The sources API, and the shapes it speaks.
 *
 * A job is subscribed to rather than posted: starting a step answers immediately and the
 * narration arrives on `/api/job/stream`, so a three-minute describe does not hold a
 * request open and a page refresh re-attaches instead of losing the progress.
 */

import { json } from "./api";

export type StepName = "connect" | "cubes" | "describe" | "views";
export const STEPS: StepName[] = ["connect", "cubes", "describe", "views"];

export const STEP_LABEL: Record<StepName, string> = {
  connect: "Connect",
  cubes: "Build cubes",
  describe: "Describe with AI",
  views: "Build views",
};

/** Every action a job can run, `read` included — it has no stage row but does appear in
 *  a job's progress, so it needs a name there. */
export const ACTION_LABEL: Record<string, string> = {
  ...STEP_LABEL,
  read: "Read the database",
};

export const STEP_BLURB: Record<StepName, string> = {
  connect: "Start this source's Cube container and check the database answers.",
  cubes: "Read the tables, then emit one cube each with its keys and joins.",
  describe: "One model call per table: descriptions, measures and disambiguation notes.",
  views: "Business-facing views over the join paths, validated across every join.",
};

/**
 * What the pipeline looks like to a person: five stages, not four.
 *
 * `read` has no row in `steps` because it caches a catalog rather than producing model
 * files — a backend distinction that has no business leaking into the UI. Hiding it
 * inside "Build cubes" meant the page promised four steps, and expanding the second one
 * offered a button that did something else. This is `run.generate`'s own order.
 */
export type StageName = StepName | "read";
export const STAGES: StageName[] = ["connect", "read", "cubes", "describe", "views"];

export const STAGE_LABEL: Record<StageName, string> = { ...STEP_LABEL, read: "Read tables" };

export const STAGE_BLURB: Record<StageName, string> = {
  ...STEP_BLURB,
  read: "One throwaway container introspects every schema at once, so choosing tables afterwards is instant and touches no database.",
  cubes: "Pick the tables to model, then emit one cube each with its keys and joins.",
};

export interface StepRecord {
  status?: "done" | "error";
  finished_at?: number;
  summary?: Record<string, unknown>;
  notes?: string[];
  errors?: Record<string, string>;
  error?: string;
}

export interface SourceRow {
  name: string;
  description: string;
  driver: string;
  port: number;
  url: string;
  container: string;
  credential_keys: string[];
  /** container path -> host path, for a driver that needs a key file. Not a secret, and
   *  the edit form needs it: the credential holds the container path, not this. */
  mounts: Record<string, string>;
  steps: Partial<Record<StepName, StepRecord>>;
  running: boolean;
  /** Structured, so a dot's colour comes from a boolean rather than from wording. */
  health: { healthy: boolean; status: string; detail: string };
  container_state: string;
  /** What Cube is serving right now, read from /v1/meta. This is the truth about the
   *  model; `steps` is only what this portal did, and the two can disagree. */
  serving: { cubes: number; views: string[]; described: number } | null;
  /** Per step, the later steps re-running it would discard. Computed server-side so the
   *  confirmation and the deletion come from one rule. */
  invalidates: Record<StepName, StepName[]>;
  /** The one source the agent queries, decided by CUBE_URL. */
  active: boolean;
}

export interface System {
  ok: boolean;
  reason?: string;
  docker?: string;
  cubestore?: boolean;
  orphans?: string[];
  cube_url?: string;
  /** Cube skips JWT verification when this is on. The panel says so.  */
  dev_mode?: boolean;
}

export interface DriverField {
  env: string;
  label: string;
  default: string;
  secret: boolean;
  required: boolean;
  boolean: boolean;
  /** Non-empty when the value is a path to a file on this machine, and then also where
   *  that file is mounted in the container — which is the key into `SourceRow.mounts`.
   *  Typed, not picked: a file input yields the file's contents and never its path. */
  mount_to: string;
  help: string;
}

export interface DriverSpec {
  key: string;
  label: string;
  extracts_tables: boolean;
  fields: DriverField[];
}

export interface TableInfo {
  name: string;
  columns: number;
  primary_key: string[];
  references: string[];
  /** Nothing points at it, so it is where additive facts live. */
  fact: boolean;
}

export interface SchemaInfo {
  schema: string;
  tables: TableInfo[];
}

export interface CatalogInfo {
  read: boolean;
  truncated: boolean;
  notes: string[];
  selection: { schema?: string; tables?: string[] };
  schemas: SchemaInfo[];
}

/** The compiled semantic layer, parsed server-side by the agent's own `Catalog.from_meta`
 *  — so this is what a question can reach. `operators` is what the gate will accept. */
export interface ServedMember {
  id: string;
  name: string;
  kind: "measure" | "dimension" | "segment";
  type: string;
  title: string;
  description: string;
  ai_context: string;
  granularities: string[];
  operators: string[];
  /** On a view member, the cube member it re-exports. Empty on a cube's own members. */
  alias: string;
  /** On a cube member, the views that expose it. Empty means the agent can only reach it
   *  by querying the cube directly. */
  exposed_by: string[];
}

export interface ServedFolder {
  name: string;
  members: string[];
}

export interface ServedEntity {
  name: string;
  is_view: boolean;
  title: string;
  description: string;
  ai_context: string;
  folders: ServedFolder[];
  members: ServedMember[];
  /** A view's cubes. Empty on a cube. */
  draws_from: string[];
  /** A cube's views. Empty on a view, and empty on a cube no view reaches. */
  exposed_by: string[];
}

export interface ServedModel {
  /** False when Cube would not answer — often a model that failed to compile rather than
   *  a container that is down. `error` says which. */
  serving: boolean;
  error: string;
  entities: ServedEntity[];
}

export interface Closure {
  tables: string[];
  /** Pulled in by foreign key because the picked tables reference them. */
  added: string[];
  cut_joins: string[];
  keyless: string[];
}

export interface JobEvent {
  type: "job_start" | "step_start" | "progress" | "log" | "step_end" | "job_end";
  data: Record<string, any>;
  seq: number;
}

export interface JobState {
  id: string | null;
  source?: string;
  steps?: string[];
  current?: string;
  started_at?: number;
  done?: boolean;
  error?: string;
  events?: JobEvent[];
}

const post = (path: string, body?: unknown) =>
  json(path, { method: "POST", body: JSON.stringify(body ?? {}) });

export const system = () => json<System>("/api/system");
export const drivers = () =>
  json<{ drivers: DriverSpec[]; local_hosts: string[]; localhost_note: string }>("/api/drivers");
export const list = () => json<{ sources: SourceRow[]; next_port: number }>("/api/sources");
export const catalog = (name: string) => json<CatalogInfo>(`/api/sources/${name}/catalog`);
export const logs = (name: string) =>
  json<{ container: string; logs: string }>(`/api/sources/${name}/logs`);
export const servedModel = (name: string) => json<ServedModel>(`/api/sources/${name}/model`);

/** Probe now rather than waiting out the server's 15s health cache. */
export const checkNow = (name: string) => post(`/api/sources/${name}/check`) as Promise<SourceRow>;

export const create = (body: {
  name: string;
  description: string;
  driver: string;
  port: number;
  values: Record<string, string>;
}) => post("/api/sources", body) as Promise<SourceRow>;

export const edit = (
  name: string,
  body: { description?: string; values?: Record<string, string> },
) => json<SourceRow>(`/api/sources/${name}`, { method: "PATCH", body: JSON.stringify(body) });

export const remove = (name: string) => json(`/api/sources/${name}`, { method: "DELETE" });

export const control = (name: string, action: "start" | "stop" | "restart") =>
  post(`/api/sources/${name}/container/${action}`) as Promise<SourceRow>;

export const removeOrphan = (container: string) =>
  json(`/api/containers/${container}`, { method: "DELETE" });

export const closure = (name: string, schema_name: string, tables: string[]) =>
  post(`/api/sources/${name}/closure`, { schema_name, tables }) as Promise<Closure>;

/**
 * Start one action. Answers as soon as the job exists, not when it finishes.
 *
 * `read` is an action but not a step: it produces the catalog the picker needs, so it has
 * to be triggerable on its own and records no row in the panel.
 */
export const runAction = (
  name: string,
  action: StepName | "read",
  body: { schema_name?: string; tables?: string[] | null } = {},
) => post(`/api/sources/${name}/actions/${action}`, body) as Promise<JobState>;

export const job = () => json<JobState>("/api/job");
export const cancelJob = () => post("/api/job/cancel");

/**
 * Watch the running job.
 *
 * `EventSource` rather than the hand-parsed SSE the chat uses: a job is not
 * request-scoped, and the token already lives in a cookie so a plain GET authenticates.
 * The server replays its buffer before going live, which is what makes reconnecting
 * after a refresh identical to connecting for the first time.
 */
export function watchJob(onEvent: (event: JobEvent) => void): () => void {
  const stream = new EventSource("/api/job/stream");
  stream.onmessage = (message) => onEvent(JSON.parse(message.data) as JobEvent);
  // The server closes the stream after job_end; EventSource would reconnect forever.
  stream.onerror = () => stream.close();
  return () => stream.close();
}

/** "11 cubes, 3 views, all described" — what Cube is actually serving, in one phrase. */
export function servingSummary(serving: NonNullable<SourceRow["serving"]>): string {
  const total = serving.cubes + serving.views.length;
  const parts = [`${serving.cubes} cube${serving.cubes === 1 ? "" : "s"}`];
  if (serving.views.length) {
    parts.push(`${serving.views.length} view${serving.views.length === 1 ? "" : "s"}`);
  }
  if (serving.described) {
    parts.push(serving.described >= total ? "all described" : `${serving.described} described`);
  }
  return parts.join(", ");
}

/** The three states the panel draws: no container, up but unreachable, healthy. */
export function healthOf(source: SourceRow): { tone: "ok" | "warn" | "bad"; label: string } {
  if (!source.running) return { tone: "warn", label: "stopped" };
  if (!source.health.healthy) {
    return { tone: "bad", label: source.health.detail || source.health.status };
  }
  return { tone: "ok", label: "connected" };
}

/** The step that may run next, or null once every one has succeeded. */
export function nextStep(source: SourceRow): StepName | null {
  return STEPS.find((step) => source.steps[step]?.status !== "done") ?? null;
}

/** Where a stage stands. `read` has no step record, so its state is whether a catalog
 *  was cached; every other stage reads the row `run.action` wrote. */
export function stageState(
  stage: StageName,
  source: SourceRow,
  catalogRead: boolean,
): "done" | "error" | "todo" {
  if (stage === "read") return catalogRead ? "done" : "todo";
  const status = source.steps[stage]?.status;
  return status === "done" ? "done" : status === "error" ? "error" : "todo";
}

/** The first stage not yet done, or null once all five are. Drives the primary action. */
export function nextStage(source: SourceRow, catalogRead: boolean): StageName | null {
  return STAGES.find((stage) => stageState(stage, source, catalogRead) !== "done") ?? null;
}

/** Why a stage cannot run yet, or "" when it can. The server enforces the same rule in
 *  `run.action`; this only explains it before the round trip. */
export function stageBlocker(stage: StageName, source: SourceRow, catalogRead: boolean): string {
  const index = STAGES.indexOf(stage);
  if (index <= 0) return "";
  const previous = STAGES[index - 1];
  return stageState(previous, source, catalogRead) === "done"
    ? ""
    : `Run ${STAGE_LABEL[previous]} first`;
}

/** A step's summary as one readable line. Lists are joined; counts are labelled. */
export function summarise(step: StepName, record: StepRecord | undefined): string {
  const summary = record?.summary;
  if (!summary) return "";
  const parts: string[] = [];
  const say = (key: string, singular: string, plural = `${singular}s`) => {
    const value = summary[key];
    if (typeof value === "number" && value > 0) {
      parts.push(`${value} ${value === 1 ? singular : plural}`);
    }
  };
  if (step === "connect") {
    // Cube's own status word is "HEALTH"; that is not something to show a person.
    const status = String(summary.status ?? "");
    return status === "HEALTH" || !status ? "connected" : status;
  }
  if (step === "cubes") {
    say("cubes", "cube");
    say("keys", "key");
    say("joins", "join");
  }
  if (step === "describe") {
    say("described_cubes", "cube described", "cubes described");
    say("described_members", "member");
    say("measures", "measure");
    say("dropped", "dropped", "dropped");
  }
  if (step === "views") {
    const names = summary.names;
    if (Array.isArray(names) && names.length) return names.join(", ");
    say("views", "view");
  }
  return parts.join(" · ");
}
