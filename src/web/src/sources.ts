/**
 * Everything the portal offers besides the chat, and the shapes it speaks. Four nouns, in the
 * order the work happens: the connection, the deployment that serves it, the pipeline that
 * builds the model, and the model itself.
 *
 * A job is subscribed to rather than posted: starting a stage answers immediately and the
 * narration arrives on `/api/onboarding/pipeline/job/stream`, so a three-minute describe does not hold a
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

/**
 * What the pipeline looks like to a person: four stages, not three.
 *
 * `read` has no row in `steps` because it caches a catalog rather than producing model files — a
 * backend distinction that has no business leaking into the UI.
 *
 * There is no Connect stage: connecting is a form, and the row it writes either opened the
 * database or did not. A stage records something that ran, and a connection is a condition.
 *
 * The order is `evidence.STAGES` server-side and `BuildModel`'s queue here — nothing ships a rail
 * of stage states any more, because the two screens that run the pipeline both follow the job.
 */
export type StageName = StepName | "read";

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
  /** Whether setup finished: both addresses **and** a model in the store. A different question
   *  from `deployed`, and the thing that decides whether the wizard is still owed — the wizard's
   *  last step is what builds the model, so two answering addresses and an empty store is the
   *  state it is entered in, not the state it is finished in. Computed server-side, never a
   *  column: `CREATE TABLE IF NOT EXISTS` cannot add one. */
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
  /** The cubes and views the model store holds, by name. What says a stage produced something,
   *  so a disagreement with `serving` is visible rather than inferred. */
  cubes: string[];
  views: string[];
  /** Why the model store did not answer, or "". Its own field rather than a failed request,
   *  because the data page is where you find out something is down, and everything the page
   *  says about the model reads as "nothing built" whenever it is set. */
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

/** The compiled model for one branch.
 *
 *  Takes a branch because one Cube compiles every ref independently: this is how you check that
 *  the view you just saved exposes the members you meant, without merging it into main first.
 *  Defaults to main, which is what every chat and every dashboard reads. */
export const model = (ref = "main") =>
  json<ServedModel>(`/api/ide/model?ref=${encodeURIComponent(ref)}`);

/** Probe now rather than waiting out the server's 15s health cache. */
export const probe = () => post("/api/onboarding/connection/probe") as Promise<SourceRow>;

/** Store the credentials and mint the secrets. **Starts nothing** — the environment file this
 *  makes available is what someone runs, and the address comes back through `edit`. The steps
 *  and the cached catalog go with it: they describe a database, and this is a different one. */
export const connect = (body: {
  description: string;
  driver: string;
  values: Record<string, string>;
}) => post("/api/onboarding/connection", body) as Promise<SourceRow>;

/** The stored connection. Credentials only — the addresses are `deployed`. Nothing is
 *  invalidated: a changed host may be the same database moved and a changed name may expose the
 *  same tables, so which happened is not derivable from the values. Rebuilding the model is the
 *  explicit action for the case where it was a different database. */
export const edit = (body: { description?: string; values?: Record<string, string> }) =>
  json<SourceRow>("/api/onboarding/connection", { method: "PATCH", body: JSON.stringify(body) });

/** The two deployments the portal renders a compose file for. */
export type Service = "semantic" | "sandbox";

/** Where a service's compose file is downloaded from.
 *
 *  A download and not a fetched string: it carries the warehouse password and all four secrets,
 *  and the only thing to do with it is run it somewhere. Rendering it into the page would put
 *  those values in the DOM to no purpose. */
export const composeUrl = (service: Service) => `/api/onboarding/deployment/${service}/compose`;

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
  json<{ checked: Partial<Record<Service, Reached>>; connection: SourceRow }>("/api/onboarding/deployment", {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const closure = (schema_name: string, tables: string[]) =>
  post("/api/onboarding/pipeline/tables/closure", { schema_name, tables }) as Promise<Closure>;

/**
 * Start one action. Answers as soon as the job exists, not when it finishes.
 *
 * `read` is an action but not a step: it produces the catalog the picker needs, so it has
 * to be triggerable on its own and records no row in the panel.
 */
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

/** The outcome of a merge, plus main's own compile afterwards — two clean branches can merge
 *  into a model that does not compile, and this is the moment to know. `deleted` is whether the
 *  branch went with it, which is best-effort server-side. */
export interface Merged extends ModelValidation {
  merged: string;
  deleted: boolean;
}

/**
 * A branch's files as this machine holds them, and what the draft has done to them.
 *
 * `dirty` and `deleted` are the draft against what the store last handed over — computed on the
 * server, because the server owns the bytes now and a second answer here could only disagree.
 */
export interface ModelFiles {
  ref: string;
  editable: boolean;
  files: string[];
  dirty: string[];
  deleted: string[];
  /** The store revision the draft was taken from. */
  rev: number;
}

/**
 * What Validate learned, without anything being published.
 *
 * `ok` is the gate passing *and* Cube compiling. `member_errors` and `impact` never make it
 * false: a model that compiles with a measure that fails at query time still beats one that will
 * not compile, and a tile this would break is the merge's business to refuse.
 */
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

/** Branch off `fromRef` — main unless something else is picked. Whatever it starts from, it can
 *  only ever merge back into main, so a branch off a branch lands that branch's commits too. */
export const createRef = (name: string, fromRef: string) =>
  post("/api/ide/refs", { name, from_ref: fromRef }) as Promise<Refs>;

export const deleteRef = (ref: string) =>
  json<Refs>(`/api/ide/refs/${encodeURIComponent(ref)}`, { method: "DELETE" });

export const refDiff = (ref: string) => json<RefDiff>(`/api/ide/refs/${ref}/diff`);

/** Land a branch on main. `force` lands one that breaks saved queries — refused otherwise,
 *  server-side — and `deleteAfter` removes the branch in the same call, best-effort: the merge
 *  has happened by then, so a failed cleanup is a stale name in a dropdown and not a failure. */
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

/**
 * Validate: would Cube accept this branch's draft? **Nothing is published.**
 *
 * The whole draft goes at once, because a change spanning two files cannot compile a file at a
 * time. The server writes it to a throwaway ref, asks Cube, and deletes the ref — so this is the
 * only thing that can tell you a change actually compiles.
 *
 * It answers 200 even when the model is refused: a rejection is the answer to the question, and
 * `ok` is what says which. Only a bad request throws.
 */
export const checkChanges = (ref: string) =>
  post("/api/ide/check", { ref }) as Promise<CheckResult>;

/** Publish: land the branch's draft as one commit. This is the deploy — Cube is serving it
 *  within seconds. The gate runs again server-side, so a refusal is still possible. */
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

/**
 * Ask the editor agent for a change.
 *
 * Nothing about the model travels with it: the agent reads the branch's draft, which is where
 * every unpublished edit already lives. Anything typed and not yet saved is invisible to it,
 * which is why Save comes before asking for a change to the same file.
 */
export const sendEditMessage = (chatId: string, question: string, signal: AbortSignal) =>
  streamTurn(`/api/ide/chats/${chatId}/messages`, { question }, signal);

/**
 * Watch the running job.
 *
 * `EventSource` rather than the hand-parsed SSE the chat uses: a job is not
 * request-scoped, and the token already lives in a cookie so a plain GET authenticates.
 * The server replays its buffer before going live, which is what makes reconnecting
 * after a refresh identical to connecting for the first time.
 */
export function watchJob(onEvent: (event: JobEvent) => void): () => void {
  const stream = new EventSource("/api/onboarding/pipeline/job/stream");
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
