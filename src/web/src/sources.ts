/**
 * Everything the portal offers besides the chat, and the shapes it speaks. Four nouns, in the
 * order the work happens: the connection, the deployment that serves it, the pipeline that
 * builds the model, and the model itself.
 *
 * A job is subscribed to rather than posted: starting a stage answers immediately and the
 * narration arrives on `/api/pipeline/job/stream`, so a three-minute describe does not hold a
 * request open and a page refresh re-attaches instead of losing the progress.
 */

import { json, streamTurn } from "./api";

export type StepName = "cubes" | "describe" | "views";

export const STEP_LABEL: Record<StepName, string> = {
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
  cubes: "Read the tables, then emit one cube each with its keys and joins.",
  describe:
    "One model call per table: descriptions, measures and disambiguation notes.",
  views:
    "Business-facing views over the join paths, validated across every join.",
};

/**
 * What the pipeline looks like to a person: four stages, not three.
 *
 * `read` has no row in `steps` because it caches a catalog rather than producing model
 * files — a backend distinction that has no business leaking into the UI. Folding it into
 * "Build cubes" would promise three steps and then offer a button that does a fourth thing.
 *
 * There is no Connect stage: connecting is a form, and the row it writes either opened the
 * database or did not. A stage records something that ran, and a connection is a condition.
 *
 * The order, the states and what blocks what all arrive on `SourceRow.stages`, from
 * `store.STAGES`. Only the copy below is the browser's: a label is presentation, a sequence
 * is the workflow, and two sequences let two screens name different next steps.
 */
export type StageName = StepName | "read";

export const STAGE_LABEL: Record<StageName, string> = {
  ...STEP_LABEL,
  read: "Read tables",
};

export const STAGE_BLURB: Record<StageName, string> = {
  ...STEP_BLURB,
  read: "One throwaway container introspects every schema at once, so choosing tables afterwards is instant and touches no database.",
  cubes:
    "Pick the tables to model, then emit one cube each with its keys and joins.",
};

export interface StageState {
  name: StageName;
  state: "done" | "error" | "todo";
  /** The step that must succeed first, or "" when nothing is holding it up. */
  blocked_by: StepName | "";
  /** How much of the stage is done, when "done" is not the whole story — "9 of 11
   *  described". Empty for every stage that is simply run or not. */
  note: string;
}

/** From `serving.state`. The copy for each is below, in SERVING_NOTE — the state is the
 *  server's answer, the wording is ours.
 *
 *  There is no "stopped": the portal starts nothing, so it can never observe a stop. */
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
  /** The database this connection opens, or the driver's name when it has none. Derived
   *  server-side from the credentials — there is nothing to name, only one connection. */
  label: string;
  description: string;
  driver: string;
  /** The semantic layer's one address: the model store, which also proxies Cube at
   *  `/cube/...`. Empty until someone has run it. There is no Cube address — Cube is not
   *  published, and where it is, is the store's own configuration. */
  store_url: string;
  /** Where the agent runs Python, or "" — optional, and not part of `deployed`: a deployment
   *  without one answers questions and just cannot do arithmetic Cube will not express. */
  sandbox_url: string;
  credential_keys: string[];
  steps: Partial<Record<StepName, StepRecord>>;
  /** Whether the semantic layer's address is set. The portal does not run the deployment, so
   *  this is something it was told rather than something it arranged — and it is the difference
   *  between "nothing has been run yet" and "it is down". */
  deployed: boolean;
  /** Whether setup finished: both addresses. A different question from `deployed`, because the
   *  sandbox step is required — and it is what decides whether the wizard is still owed. */
  onboarded: boolean;
  /** Structured, so a dot's colour comes from a boolean rather than from wording. The same
   *  shape the wizard's check returns — one question asked of a stored address rather than a
   *  pasted one. */
  health: Reached;
  /** What Cube is serving right now, read from /v1/meta. This is the truth about the
   *  model; `steps` is only what this portal did, and the two can disagree. */
  serving: { cubes: number; views: string[]; described: number } | null;
  /** Why `serving` is what it is. A null `serving` has two unrelated causes — the container
   *  is stopped, and Cube refuses to compile the model — so it cannot be read as either. */
  serving_state: ServingState;
  /** Cube is serving a model this portal did not build — a colleague's, or one restored from
   *  the store. Every stage reads "todo" and that is honest, so the rail says so rather than
   *  presenting a working model as an unstarted one. */
  adopted: boolean;
  /** The cubes and views the model store holds, by name. What the rail is read off, so a
   *  disagreement with `serving` is visible rather than inferred. */
  cubes: string[];
  views: string[];
  /** Per step, the later steps re-running it would discard. Computed server-side so the
   *  confirmation and the deletion come from one rule. */
  invalidates: Record<StepName, StepName[]>;
  /** Where every stage stands and what is holding it up, in order. Server-side, because
   *  `steps.action` enforces the same rule and only one of the two can be authoritative. */
  stages: StageState[];
  /** The first stage not done, or null once all four are. The primary action every screen
   *  offers, so the header and the stage panel cannot name different ones. */
  next_stage: StageName | null;
  /** Why the model store did not answer, or "". Its own field rather than a failed request,
   *  because this page is where you find out something is down — and the rail below reads as
   *  "nothing built" whenever it is set, which would be a lie left unexplained. */
  store_error: string;
  /** Why the sandbox is unavailable, or "". Never fatal — every service runs wherever its
   *  owner put it, so this machine's own state is not a precondition for anything. Probed
   *  alongside the health dot and cached with it. */
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

/** What the `read` stage cached: the *warehouse's* tables, for the picker. Not the served
 *  model — that is `model()`, and calling both a catalog is how the two got confused. */
export interface TableCache {
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

/** One markdown file under the model's `grounding/`, as the agent loads it. */
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

export interface Grounding {
  /** The model store this was read from, so when grounding is unexpectedly empty the
   *  address it came from is the answer. */
  root: string;
  /** Whether the store answered at all. False is an outage, not an empty model. */
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
export const connection = () => json<SourceRow | null>("/api/connection");
export const tables = () => json<TableCache>("/api/pipeline/tables");
export const model = () => json<ServedModel>("/api/model");
export const grounding = () => json<Grounding>("/api/model/grounding");

/** Probe now rather than waiting out the server's 15s health cache. */
export const probe = () => post("/api/connection/probe") as Promise<SourceRow>;

/** Store the credentials and mint the secrets. **Starts nothing** — the environment file this
 *  makes available is what someone runs, and the address comes back through `edit`. The steps
 *  and the cached catalog go with it: they describe a database, and this is a different one. */
export const connect = (body: {
  description: string;
  driver: string;
  values: Record<string, string>;
}) => post("/api/connection", body) as Promise<SourceRow>;

/** The stored connection. Credentials only — the addresses are `deployed`. Nothing is
 *  invalidated: a changed host may be the same database moved, so `resetPipeline` is explicit. */
export const edit = (body: { description?: string; values?: Record<string, string> }) =>
  json<SourceRow>("/api/connection", { method: "PATCH", body: JSON.stringify(body) });

/** The two deployments the portal renders a compose file for. */
export type Service = "semantic" | "sandbox";

/** Where a service's compose file is downloaded from.
 *
 *  A download and not a fetched string: it carries the warehouse password and all four secrets,
 *  and the only thing to do with it is run it somewhere. Rendering it into the page would put
 *  those values in the DOM to no purpose. */
export const composeUrl = (service: Service) => `/api/deployment/${service}/compose`;

/** Whether a deployment answers. One shape for both questions the server asks — the wizard's
 *  full check on a pasted address, and the recurring probe behind the health dot — because they
 *  differ in what they check rather than in what they can say about it. */
export interface Reached {
  ok: boolean;
  /** A word, for the chip. */
  status: string;
  /** A sentence, for the panel beside it. */
  detail: string;
  /** Not reasons to refuse — a contract mismatch above all. Shown either way, because a
   *  deployment that works today and speaks an older API is worth knowing about now. */
  warnings: string[];
}

/** Step two of connecting: what was run, and where. Each address is checked before it is
 *  stored, so one that does not answer never has to be undone — `checked` says which failed and
 *  why. The same call is how a deployment that moved is followed. */
export const deployed = (body: { store_url?: string; sandbox_url?: string }) =>
  json<{ checked: Partial<Record<Service, Reached>>; connection: SourceRow }>("/api/deployment", {
    method: "PUT",
    body: JSON.stringify(body),
  });

/** Discard what the pipeline recorded and the catalog it cached, for when the connection now
 *  points somewhere else. The served model keeps answering until a rebuild replaces it. */
export const resetPipeline = () =>
  json<SourceRow>("/api/pipeline", { method: "DELETE" });

/** Mint all four secrets again. Both deployments must be re-run from freshly downloaded files
 *  afterwards, and until they are, nothing answers. */
export const rotate = () => post("/api/deployment/secrets") as Promise<SourceRow>;

export const closure = (schema_name: string, tables: string[]) =>
  post("/api/pipeline/tables/closure", { schema_name, tables }) as Promise<Closure>;

/**
 * Start one action. Answers as soon as the job exists, not when it finishes.
 *
 * `read` is an action but not a step: it produces the catalog the picker needs, so it has
 * to be triggerable on its own and records no row in the panel.
 */
export const runStage = (
  stage: StageName,
  body: { schema_name?: string; tables?: string[] | null } = {},
) => post(`/api/pipeline/${stage}`, body) as Promise<JobState>;

export const job = () => json<JobState>("/api/pipeline/job");
export const cancelJob = () =>
  json("/api/pipeline/job", { method: "DELETE" });

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
  /** "tile" or "certified". */
  kind: string;
  /** The dashboard it sits on, or the grounding file it lives in. */
  where: string;
  title: string;
  members: string[];
  nearest: Record<string, string[]>;
}

/** Saved queries against one ref's model. One Cube compiles every ref, so this is answerable
 *  for any of them — null only when the model could not be read at all. */
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

export interface RefDiff {
  ref: string;
  changed: { status: string; path: string }[];
  patch: string;
  impact: Impact | null;
}

export const refs = () => json<Refs>("/api/model/refs");

export const createRef = (name: string) =>
  post("/api/model/refs", { name }) as Promise<Refs>;

export const refDiff = (ref: string) => json<RefDiff>(`/api/model/refs/${ref}/diff`);

/** `force` lands a ref that breaks saved queries — refused otherwise, server-side. */
export const mergeRef = (ref: string, force = false) =>
  post(
    `/api/model/refs/${ref}/merge${force ? "?force=true" : ""}`,
  ) as Promise<ModelValidation & { merged: string }>;

export const modelFile = (path: string, ref: string) =>
  json<{ path: string; ref: string; text: string }>(
    `/api/model/files/${path}?ref=${encodeURIComponent(ref)}`,
  );

/** Tiers 1-2 then the commit. A rejection arrives as the thrown error's message. */
export const writeModelFile = (path: string, text: string, ref: string) =>
  json<{ path: string; ref: string; warnings: string[] }>(
    `/api/model/files/${path}?ref=${encodeURIComponent(ref)}`,
    {
      method: "PUT",
      body: JSON.stringify({ text }),
    },
  );

/** Tier 3 — asks Cube itself. It does not load the edit; the commit already did. */
export const validateModel = (ref: string) =>
  post(`/api/model/validate?ref=${encodeURIComponent(ref)}`) as Promise<ModelValidation>;

// --- the editor chat -----------------------------------------------------------------

export interface EditChat {
  id: string;
  title: string;
  /** The ref this conversation edits, fixed when it was created. */
  ref: string;
  updated_at: number;
}

export const editChats = () => json<EditChat[]>("/api/model/chats");

export const createEditChat = (ref: string) =>
  post("/api/model/chats", { ref }) as Promise<{
    id: string;
    title: string;
    ref: string;
  }>;

export const sendEditMessage = (chatId: string, question: string, signal: AbortSignal) =>
  streamTurn(`/api/model/chats/${chatId}/messages`, { question }, signal);

/**
 * Watch the running job.
 *
 * `EventSource` rather than the hand-parsed SSE the chat uses: a job is not
 * request-scoped, and the token already lives in a cookie so a plain GET authenticates.
 * The server replays its buffer before going live, which is what makes reconnecting
 * after a refresh identical to connecting for the first time.
 */
export function watchJob(onEvent: (event: JobEvent) => void): () => void {
  const stream = new EventSource("/api/pipeline/job/stream");
  stream.onmessage = (message) => onEvent(JSON.parse(message.data) as JobEvent);
  // The server closes the stream after job_end; EventSource would reconnect forever.
  stream.onerror = () => stream.close();
  return () => stream.close();
}

/**
 * What to say about the model, in one phrase — one function for every screen that says it.
 *
 * The interesting cases are the ones with no model, and `serving_state` is the server's
 * answer to which one it is. Guessing from `serving` being null cannot tell a compile failure
 * from a stopped container, and calling the first one "no model yet" is wrong rather than
 * vague for a model with a dozen working cubes.
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

/** The three states the panel draws: not deployed yet, up but unreachable, healthy. */
export function healthOf(source: SourceRow): {
  tone: "ok" | "warn" | "bad";
  label: string;
} {
  // Not a fault: it is the normal state between storing credentials and running the compose
  // file, so it is neutral rather than red.
  if (!source.deployed) return { tone: "warn", label: "not deployed" };
  if (!source.health.ok) {
    return { tone: "bad", label: source.health.detail || source.health.status };
  }
  return { tone: "ok", label: "connected" };
}

/** A step's summary as one readable line. Lists are joined; counts are labelled. */
export function summarise(
  step: StepName,
  record: StepRecord | undefined,
): string {
  const summary = record?.summary;
  if (!summary) return "";
  const parts: string[] = [];
  const say = (key: string, singular: string, plural = `${singular}s`) => {
    const value = summary[key];
    if (typeof value === "number" && value > 0) {
      parts.push(`${value} ${value === 1 ? singular : plural}`);
    }
  };
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
