/**
 * The sources API, and the shapes it speaks.
 *
 * A job is subscribed to rather than posted: starting a step answers immediately and the
 * narration arrives on `/api/job/stream`, so a three-minute describe does not hold a
 * request open and a page refresh re-attaches instead of losing the progress.
 */

import { json } from "./api";
import type { AgentEvent } from "./types";

export type StepName = "connect" | "cubes" | "describe" | "views";

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

const STEP_BLURB: Record<StepName, string> = {
  connect: "Start this source's Cube container and check the database answers.",
  cubes: "Read the tables, then emit one cube each with its keys and joins.",
  describe: "One model call per table: descriptions, measures and disambiguation notes.",
  views: "Business-facing views over the join paths, validated across every join.",
};

/**
 * What the pipeline looks like to a person: five stages, not four.
 *
 * `read` has no row in `steps` because it caches a catalog rather than producing model
 * files — a backend distinction that has no business leaking into the UI. Folding it into
 * "Build cubes" would promise four steps and then offer a button that does a fifth thing.
 *
 * The order, the states and what blocks what all arrive on `SourceRow.stages`, from
 * `store.STAGES`. Only the copy below is the browser's: a label is presentation, a sequence
 * is the workflow, and two sequences let a sidebar and a page name different next steps.
 */
export type StageName = StepName | "read";

export const STAGE_LABEL: Record<StageName, string> = { ...STEP_LABEL, read: "Read tables" };

export const STAGE_BLURB: Record<StageName, string> = {
  ...STEP_BLURB,
  read: "One throwaway container introspects every schema at once, so choosing tables afterwards is instant and touches no database.",
  cubes: "Pick the tables to model, then emit one cube each with its keys and joins.",
};

export interface StageState {
  name: StageName;
  state: "done" | "error" | "todo";
  /** The step that must succeed first, or "" when nothing is holding it up. */
  blocked_by: StepName | "";
}

/** From `health.serving_state`. The copy for each is below, in SERVING_NOTE — the state is
 *  the server's answer, the wording is ours. */
export type ServingState = "stopped" | "unreachable" | "broken" | "empty" | "ok";

const SERVING_NOTE: Record<Exclude<ServingState, "ok">, string> = {
  stopped: "stopped",
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
  /** Why `serving` is what it is. A null `serving` has two unrelated causes — the container
   *  is stopped, and Cube refuses to compile the model — so it cannot be read as either. */
  serving_state: ServingState;
  /** Per step, the later steps re-running it would discard. Computed server-side so the
   *  confirmation and the deletion come from one rule. */
  invalidates: Record<StepName, StepName[]>;
  /** Where every stage stands and what is holding it up, in order. Server-side, because
   *  `run.action` enforces the same rule and only one of the two can be authoritative. */
  stages: StageState[];
  /** The first stage not done, or null once all five are. The primary action every screen
   *  offers, so a sidebar and a page cannot name different ones. */
  next_stage: StageName | null;
  /** The one source the agent queries, decided by CUBE_URL. */
  active: boolean;
  /** The checked-out branch, which is the model this source serves. "" before the model
   *  directory is a repo. */
  branch: string;
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

interface DriverField {
  env: string;
  label: string;
  default: string;
  secret: boolean;
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
  /** The envs a person must fill, from `drivers.required_envs` — the same function POST
   *  /sources rejects on. There is deliberately no per-field `required` flag to derive this
   *  from a second time. */
  required_envs: string[];
  fields: DriverField[];
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

interface ServedFolder {
  name: string;
  members: string[];
}

export interface ServedEntity {
  /** The model file defining it, "" when nothing on this branch does. */
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
  /** The checked-out branch, and whether it may be edited — main is generated. */
  branch: string;
  editable: boolean;
  dirty: string[];
  /** Files defining entities Cube is not serving: written but not validated, which is the
   *  normal state between a save and a restart rather than a fault. */
  unloaded: string[];
  /** False when Cube would not answer — often a model that failed to compile rather than
   *  a container that is down. `error` says which. */
  serving: boolean;
  error: string;
  entities: ServedEntity[];
  /** Model size, from the server. Over cubes only — a view's members are the cube's members
   *  re-exported, so counting every entity reports roughly twice the real size. Absent when
   *  Cube would not answer. */
  counts?: ModelCounts;
}

export interface ModelCounts {
  cubes: number;
  views: number;
  members: number;
  /** Cube members some view re-exports. Views omit surrogate keys on purpose, so this is
   *  never the whole membership of a healthy model. */
  exposed: number;
  described: number;
}

/** One markdown file under `cube/sources/<name>/grounding/`, as the agent loads it. */
export interface GroundingArtefact {
  kind: "rule" | "skill" | "certified";
  /** From frontmatter, and what `load_grounding` is called with. */
  name: string;
  /** Path under the grounding directory — the file to go and edit. */
  file: string;
  description: string;
  /** Rules only. `always` is in every system prompt; `agent_requested` is offered by
   *  name and its body loads on demand. The difference is the whole cost model. */
  loading: string;
  /** Certified queries only: the phrasings it was written to match. */
  user_request: string;
  body: string;
}

export interface SourceGrounding {
  /** Absolute, as resolved — not the tidy relative form. Grounding is the one agent
   *  input that resolves from the directory the portal was launched in, so when it is
   *  unexpectedly empty this path is the answer. */
  root: string;
  exists: boolean;
  artefacts: GroundingArtefact[];
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
  json<{
    drivers: DriverSpec[];
    local_hosts: string[];
    localhost_note: string;
    /** What a source name may be, from `registry.NAME_PATTERN` — the rule `POST /sources`
     *  rejects on, so the live feedback cannot be more permissive than the server. */
    name_pattern: string;
  }>("/api/drivers");
export const list = () => json<{ sources: SourceRow[]; next_port: number }>("/api/sources");
export const catalog = (name: string) => json<CatalogInfo>(`/api/sources/${name}/catalog`);
export const logs = (name: string) =>
  json<{ container: string; logs: string }>(`/api/sources/${name}/logs`);
export const servedModel = (name: string) => json<ServedModel>(`/api/sources/${name}/model`);
export const sourceGrounding = (name: string) =>
  json<SourceGrounding>(`/api/sources/${name}/grounding`);

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

// --- branches ------------------------------------------------------------------------

export interface BranchInfo {
  name: string;
  current: boolean;
  /** Files this branch changed against main — what a reviewer is about to read. */
  changed: number;
}

export interface Branches {
  current: string;
  main: string;
  /** Paths edited but not committed. A branch cannot be switched while this is non-empty. */
  dirty: string[];
  branches: BranchInfo[];
}

export interface ModelFiles {
  branch: string;
  editable: boolean;
  dirty: string[];
  files: string[];
}

export interface BrokenQuery {
  /** "tile" or "certified". */
  kind: string;
  /** The dashboard it sits on, or the grounding file it lives in. */
  where: string;
  title: string;
  members: string[];
  nearest: Record<string, string[]>;
}

/** Saved queries against the model being served. Null when it could not be read — and on a
 *  diff for a branch that is not checked out, where it would describe the wrong model. */
export interface Impact {
  checked: number;
  broken: BrokenQuery[];
}

/** What Cube makes of the checked-out model. `entities` is what it is serving now. */
export interface ModelValidation {
  compiled: boolean;
  compile_error: string;
  entities: string[];
  member_errors: Record<string, string>;
  impact: Impact | null;
}

export interface BranchDiff {
  branch: string;
  changed: { status: string; path: string }[];
  patch: string;
  dirty: string[];
  impact: Impact | null;
}

export const branches = (name: string) => json<Branches>(`/api/sources/${name}/branches`);

export const createBranch = (name: string, branch: string) =>
  post(`/api/sources/${name}/branches`, { name: branch }) as Promise<Branches>;

export const checkoutBranch = (name: string, branch: string) =>
  post(`/api/sources/${name}/branches/${branch}/checkout`) as Promise<Branches>;

export const deleteBranch = (name: string, branch: string) =>
  json<Branches>(`/api/sources/${name}/branches/${branch}`, { method: "DELETE" });

export const branchDiff = (name: string, branch: string) =>
  json<BranchDiff>(`/api/sources/${name}/branches/${branch}/diff`);

/** `force` lands a branch that breaks saved queries — refused otherwise, server-side. */
export const mergeBranch = (name: string, branch: string, force = false) =>
  post(`/api/sources/${name}/branches/${branch}/merge${force ? "?force=true" : ""}`) as Promise<
    ModelValidation & { merged: string; current: string }
  >;

export const modelFiles = (name: string) => json<ModelFiles>(`/api/sources/${name}/files`);

export const modelFile = (name: string, path: string) =>
  json<{ path: string; text: string }>(`/api/sources/${name}/files/${path}`);

/** Tiers 1-2 then the write. A rejection arrives as the thrown error's message. */
export const writeModelFile = (name: string, path: string, text: string) =>
  json<{ path: string; warnings: string[]; dirty: string[] }>(
    `/api/sources/${name}/files/${path}`,
    { method: "PUT", body: JSON.stringify({ text }) },
  );

export const deleteModelFile = (name: string, path: string) =>
  json<{ deleted: string; dirty: string[] }>(`/api/sources/${name}/files/${path}`, {
    method: "DELETE",
  });

/** Tier 3. Restarts the container, so it is what makes an edit visible as well as checked. */
export const validateModel = (name: string) =>
  post(`/api/sources/${name}/validate`) as Promise<ModelValidation>;

export const commitModel = (name: string, message: string) =>
  post(`/api/sources/${name}/commit`, { message }) as Promise<{
    branch: string;
    dirty: string[];
  }>;

export const discardModel = (name: string) =>
  post(`/api/sources/${name}/discard`) as Promise<{ dirty: string[] }>;

// --- the editor chat -----------------------------------------------------------------

export interface EditChat {
  id: string;
  title: string;
  branch: string;
  updated_at: number;
}

export const editChats = (name: string) => json<EditChat[]>(`/api/sources/${name}/chats`);

export const createEditChat = (name: string) =>
  post(`/api/sources/${name}/chats`) as Promise<{ id: string; title: string; branch: string }>;

/** Same SSE framing as the query chat — frames split on a blank line, since one read can
 *  hold a partial frame or several whole ones. */
export async function* sendEditMessage(
  name: string,
  chatId: string,
  question: string,
  signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const response = await fetch(`/api/sources/${name}/chats/${chatId}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!response.body) throw new Error("no response body");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split: number;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      if (frame.startsWith("data: ")) yield JSON.parse(frame.slice(6)) as AgentEvent;
    }
  }
}

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

/**
 * What to say about a source's model, in one phrase — one function for all four screens.
 *
 * The interesting cases are the ones with no model, and `serving_state` is the server's
 * answer to which one it is. Guessing from `serving` being null cannot tell a compile failure
 * from a stopped container, and calling the first one "no model yet" is wrong rather than
 * vague for a source with a dozen working cubes.
 */
export function servingNote(source: SourceRow): string {
  return source.serving_state === "ok" && source.serving
    ? servingSummary(source.serving)
    : SERVING_NOTE[source.serving_state as Exclude<ServingState, "ok">];
}

/** "11 cubes, 3 views, all described" — what Cube is actually serving, in one phrase. */
function servingSummary(serving: NonNullable<SourceRow["serving"]>): string {
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
