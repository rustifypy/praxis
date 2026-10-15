/**
 * The connection, its deployment, the model-building pipeline and the model. A job is
 * subscribed to, not awaited, so a page refresh re-attaches instead of losing progress.
 */

import { json, streamTurn } from "./api";

export type StepName = "cubes" | "describe" | "views";

export const STEP_LABEL: Record<StepName, string> = {
  cubes: "Build cubes",
  describe: "Describe with AI",
  views: "Build views",
};

/** Includes `read`: it has no step row but appears in a job's progress. */
export const ACTION_LABEL: Record<string, string> = {
  ...STEP_LABEL,
  read: "Read the database",
};

/** Four stages to a person; `read` has no `steps` row only because it writes no model files. */
export type StageName = StepName | "read";

/** From `serving.state`. No "stopped": the portal starts nothing, so it never observes a stop. */
export type ServingState =
  "not deployed" | "unreachable" | "broken" | "empty" | "ok";

const SERVING_NOTE: Record<Exclude<ServingState, "ok">, string> = {
  "not deployed": "not deployed",
  unreachable: "not answering",
  broken: "model not compiling",
  empty: "no model yet",
};

interface StepRecord {
  status?: "done" | "error";
  finished_at?: number;
  summary?: Record<string, unknown>;
  notes?: string[];
  errors?: Record<string, string>;
  error?: string;
}

export interface SourceRow {
  /** The database this connection opens, or the driver's name when it has none. */
  label: string;
  description: string;
  driver: string;
  /** The model store's address, which also proxies Cube; "" until deployed. */
  store_url: string;
  /** Where the agent runs Python, or "". Optional, and not part of `deployed`. */
  sandbox_url: string;
  credential_keys: string[];
  steps: Partial<Record<StepName, StepRecord>>;
  /** Whether the semantic layer's address is set — told, not arranged, by the portal. */
  deployed: boolean;
  /** Both addresses **and** a model in the store: whether the wizard is still owed. */
  onboarded: boolean;
  health: Reached;
  /** What Cube is serving now, from /v1/meta; `steps` is only what this portal did. */
  serving: { cubes: number; views: string[]; described: number } | null;
  /** Why `serving` is null: a stopped container and a model that won't compile look alike. */
  serving_state: ServingState;
  /** What the model store holds, so a disagreement with `serving` is visible. */
  cubes: string[];
  views: string[];
  /** Why the model store did not answer, or "". */
  store_error: string;
  /** Why the sandbox is unavailable, or "". Never fatal. */
  sandbox_reason: string;
}

interface TableInfo {
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

/** The *warehouse's* tables cached by `read`, for the picker. Not the served model. */
export interface TableCache {
  read: boolean;
  truncated: boolean;
  notes: string[];
  selection: { schema?: string; tables?: string[] };
  schemas: SchemaInfo[];
}

/** Parsed by the agent's own `Catalog.from_meta`; `operators` is what the gate accepts. */
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
  /** On a cube member, the views that expose it. Empty: reachable only via the cube. */
  exposed_by: string[];
}

interface ServedFolder {
  name: string;
  members: string[];
}

export interface ServedEntity {
  /** The model file defining it, "" when nothing in this model does. */
  file: string;
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
  /** Files defining entities Cube is not serving — usually one that failed to compile. */
  unloaded: string[];
  /** False when Cube would not answer; `error` says why. */
  serving: boolean;
  error: string;
  entities: ServedEntity[];
  /** Over cubes only, since view members are re-exports. Absent when Cube would not answer. */
  counts?: ModelCounts;
}

export interface ModelCounts {
  cubes: number;
  views: number;
  members: number;
  /** Cube members some view re-exports — never all, since views omit surrogate keys. */
  exposed: number;
  described: number;
}

export interface Closure {
  tables: string[];
  /** Pulled in by foreign key because the picked tables reference them. */
  added: string[];
  cut_joins: string[];
  keyless: string[];
}

export interface JobEvent {
  type:
    "job_start" | "step_start" | "progress" | "log" | "step_end" | "job_end";
  data: Record<string, any>;
  seq: number;
}

export interface JobState {
  id: string | null;
  steps?: string[];
  current?: string;
  started_at?: number;
  done?: boolean;
  error?: string;
  events?: JobEvent[];
}

const post = (path: string, body?: unknown) =>
  json(path, { method: "POST", body: JSON.stringify(body ?? {}) });


/** The connection, or null before one has been made. */
export const connection = () => json<SourceRow | null>("/api/onboarding/connection");
export const tables = () => json<TableCache>("/api/onboarding/pipeline/tables");

/** The compiled model for one ref; Cube compiles each independently, so no merge is needed. */
export const model = (ref = "main") =>
  json<ServedModel>(`/api/ide/model?ref=${encodeURIComponent(ref)}`);

/** Probe now rather than waiting out the server's 15s health cache. */
export const probe = () => post("/api/onboarding/connection/probe") as Promise<SourceRow>;

/** Store the credentials and mint the secrets. **Starts nothing.** Resets steps and cached
 *  tables, since they describe the previous database. */
export const connect = (body: {
  description: string;
  driver: string;
  values: Record<string, string>;
}) => post("/api/onboarding/connection", body) as Promise<SourceRow>;

/** Credentials only. Invalidates nothing: a changed host may be the same database moved. */
export const edit = (body: { description?: string; values?: Record<string, string> }) =>
  json<SourceRow>("/api/onboarding/connection", { method: "PATCH", body: JSON.stringify(body) });

export type Service = "semantic" | "sandbox";

/** A download, not a fetch: it carries credentials, which should never land in the DOM. */
export const composeUrl = (service: Service) => `/api/onboarding/deployment/${service}/compose`;

/** Whether a deployment answers — the wizard's check and the health probe share this shape. */
export interface Reached {
  ok: boolean;
  /** A word, for the chip. */
  status: string;
  /** A sentence, for the panel beside it. */
  detail: string;
  /** Not reasons to refuse (e.g. a contract mismatch); shown either way. */
  warnings: string[];
}

/** Step two of connecting. Each address is checked before it is stored; `checked` says which
 *  failed and why. */
export const deployed = (body: { store_url?: string; sandbox_url?: string }) =>
  json<{ checked: Partial<Record<Service, Reached>>; connection: SourceRow }>("/api/onboarding/deployment", {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const closure = (schema_name: string, tables: string[]) =>
  post("/api/onboarding/pipeline/tables/closure", { schema_name, tables }) as Promise<Closure>;

/** Start one action. Answers as soon as the job exists, not when it finishes. */
export const runStage = (
  stage: StageName,
  body: { schema_name?: string; tables?: string[] | null } = {},
) => post(`/api/onboarding/pipeline/${stage}`, body) as Promise<JobState>;

export const job = () => json<JobState>("/api/onboarding/pipeline/job");
export const cancelJob = () =>
  json("/api/onboarding/pipeline/job", { method: "DELETE" });

// --- refs -----------------------------------------------------------------------------

export interface RefInfo {
  name: string;
  /** Files this ref changed against main — what a reviewer is about to read. */
  changed: number;
}

export interface Refs {
  main: string;
  refs: RefInfo[];
}

export interface BrokenQuery {
  /** "tile". */
  kind: string;
  /** The dashboard it sits on. */
  where: string;
  title: string;
  members: string[];
  nearest: Record<string, string[]>;
}

/** Saved queries checked against one ref's model. */
export interface Impact {
  checked: number;
  broken: BrokenQuery[];
}

/** What Cube makes of one ref's model. `entities` is what it serves for that ref. */
export interface ModelValidation {
  ref: string;
  compiled: boolean;
  compile_error: string;
  entities: string[];
  member_errors: Record<string, string>;
  impact: Impact | null;
}

/** A merge plus main's compile afterwards — two clean branches can merge into a broken model.
 *  `deleted` is best-effort. */
export interface Merged extends ModelValidation {
  merged: string;
  deleted: boolean;
}

/** A branch's draft; `dirty`/`deleted` are against what the store last handed over. */
export interface ModelFiles {
  ref: string;
  editable: boolean;
  files: string[];
  dirty: string[];
  deleted: string[];
  /** The store revision the draft was taken from. */
  rev: number;
}

/** `ok` is the gate passing *and* Cube compiling; `member_errors` and `impact` never make it
 *  false — breaking a tile is the merge's business to refuse. */
export interface CheckResult {
  ref: string;
  ok: boolean;
  /** Gate refusals — append-only violations, malformed YAML, a view Cube would reject. */
  errors: string[];
  warnings: string[];
  compiled: boolean;
  compile_error: string;
  member_errors: Record<string, string>;
  /** Null when nothing compiled, so there was no catalog to check saved queries against. */
  impact: Impact | null;
}

export interface RefDiff {
  ref: string;
  changed: { status: string; path: string }[];
  patch: string;
  impact: Impact | null;
}

export const refs = () => json<Refs>("/api/ide/refs");

/** It can only merge into main, so a branch off a branch lands that branch's commits too. */
export const createRef = (name: string, fromRef: string) =>
  post("/api/ide/refs", { name, from_ref: fromRef }) as Promise<Refs>;

export const deleteRef = (ref: string) =>
  json<Refs>(`/api/ide/refs/${encodeURIComponent(ref)}`, { method: "DELETE" });

export const refDiff = (ref: string) => json<RefDiff>(`/api/ide/refs/${ref}/diff`);

/** `force` lands a branch that breaks saved queries; `deleteAfter` is best-effort cleanup. */
export const mergeRef = (ref: string, force = false, deleteAfter = false) =>
  post(
    `/api/ide/refs/${ref}/merge?force=${force}&delete_after=${deleteAfter}`,
  ) as Promise<Merged>;

/** Every model file the editor may open on a branch, checked out if it never has been. */
export const modelFiles = (ref: string) =>
  json<ModelFiles>(`/api/ide/files?ref=${encodeURIComponent(ref)}`);

export const modelFile = (path: string, ref: string) =>
  json<{ path: string; ref: string; text: string }>(
    `/api/ide/files/${path}?ref=${encodeURIComponent(ref)}`,
  );

/** Save one file into the branch's draft. Local: nothing is deployed and nothing compiles. */
export const saveFile = (path: string, ref: string, text: string) =>
  json<{ ref: string; path: string; dirty: string[] }>(`/api/ide/files/${path}`, {
    method: "PUT",
    body: JSON.stringify({ ref, text }),
  });

/** Drop one file from the branch's draft. 404 when it was not there. */
export const deleteFile = (path: string, ref: string) =>
  json<{ ref: string; path: string; deleted: string[] }>(
    `/api/ide/files/${path}?ref=${encodeURIComponent(ref)}`,
    { method: "DELETE" },
  );

/** Put the draft back to what the store last handed over — one file, or all of it. */
export const discardDraft = (ref: string, path?: string) =>
  post("/api/ide/discard", { ref, path: path ?? null }) as Promise<ModelFiles>;

/** Would Cube accept the whole draft? Compiled on a throwaway ref; **nothing is published.**
 *  A refused model is still a 200 — read `ok`. */
export const checkChanges = (ref: string) =>
  post("/api/ide/check", { ref }) as Promise<CheckResult>;

/** Publish the draft as one commit. This is the deploy; the gate reruns server-side. */
export const commitChanges = (body: { ref: string; message?: string }) =>
  post("/api/ide/commit", body) as Promise<{
    ref: string;
    written: string[];
    deleted: string[];
    warnings: string[];
  }>;

// --- the editor chat -----------------------------------------------------------------

export const createEditChat = (ref: string) =>
  post("/api/ide/chats", { ref }) as Promise<{
    id: string;
    title: string;
    ref: string;
  }>;

/** The agent reads the branch's draft, so unsaved editor text is invisible to it. */
export const sendEditMessage = (chatId: string, question: string, signal: AbortSignal) =>
  streamTurn(`/api/ide/chats/${chatId}/messages`, { question }, signal);

/** `EventSource` works because the token is a cookie; the server replays its buffer first, so
 *  reconnecting after a refresh is identical to connecting. */
export function watchJob(onEvent: (event: JobEvent) => void): () => void {
  const stream = new EventSource("/api/onboarding/pipeline/job/stream");
  stream.onmessage = (message) => onEvent(JSON.parse(message.data) as JobEvent);
  // The server closes the stream after job_end; EventSource would reconnect forever.
  stream.onerror = () => stream.close();
  return () => stream.close();
}

/** The model in one phrase. Uses `serving_state`, since a null `serving` can't tell a compile
 *  failure from a stopped container. */
export function servingNote(source: SourceRow): string {
  return source.serving_state === "ok" && source.serving
    ? servingSummary(source.serving)
    : SERVING_NOTE[source.serving_state as Exclude<ServingState, "ok">];
}

/** "11 cubes, 3 views, all described". */
function servingSummary(serving: NonNullable<SourceRow["serving"]>): string {
  const total = serving.cubes + serving.views.length;
  const parts = [`${serving.cubes} cube${serving.cubes === 1 ? "" : "s"}`];
  if (serving.views.length) {
    parts.push(
      `${serving.views.length} view${serving.views.length === 1 ? "" : "s"}`,
    );
  }
  if (serving.described) {
    parts.push(
      serving.described >= total
        ? "all described"
        : `${serving.described} described`,
    );
  }
  return parts.join(", ");
}

export function healthOf(source: SourceRow): {
  tone: "ok" | "warn" | "bad";
  label: string;
} {
  // Not a fault — the normal state before the compose file is run — so not red.
  if (!source.deployed) return { tone: "warn", label: "not deployed" };
  if (!source.health.ok) {
    return { tone: "bad", label: source.health.detail || source.health.status };
  }
  return { tone: "ok", label: "connected" };
}
