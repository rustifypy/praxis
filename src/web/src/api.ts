import type {
  AgentEvent,
  Chat,
  DashboardDetail,
  DashboardRow,
  FlowDetail,
  FlowSummary,
  McpAccess,
  RefreshOutcome,
  ResultColumn,
  RunDetail,
  RunSummary,
} from "./types";

/** Trade the URL token for the cookie. Required under `npm run dev`, where Vite serves the
 *  page and only /api sets the cookie. */
export async function claimToken(): Promise<void> {
  const url = new URL(window.location.href);
  if (!url.searchParams.get("token")) return;
  await fetch(`/api/chats${url.search}`);
  url.searchParams.delete("token");
  window.history.replaceState({}, "", url.pathname);
}

/** Every JSON call. Errors unwrap FastAPI's `detail`, which is written for a person. */
export async function json<T>(path: string, init?: RequestInit): Promise<T> {
  // The browser must set a FormData content type itself: it carries the multipart boundary.
  const form = init?.body instanceof FormData;
  const response = await fetch(path, {
    ...init,
    headers: form ? init?.headers : { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await response.text();
  if (!response.ok) {
    let message = `${init?.method ?? "GET"} ${path} → ${response.status}`;
    try {
      message = JSON.parse(body).detail ?? message;
    } catch {
      /* not JSON — keep the method, path and status */
    }
    throw new Error(message);
  }
  return (body ? JSON.parse(body) : {}) as T;
}

interface ModelEntity {
  name: string;
  view: boolean;
  members: number;
  description?: string;
}

/** One line per entity, for the chat's example questions and the header's link. */
export const modelSummary = () =>
  json<{ cube_url: string; entities: ModelEntity[] }>("/api/ide/model/summary");
export const listChats = () => json<Chat[]>("/api/chats");
export const createChat = () => json<Chat>("/api/chats", { method: "POST", body: "{}" });
export const chatEvents = (id: string) => json<AgentEvent[]>(`/api/chats/${id}/events`);
export const deleteChat = (id: string) => json(`/api/chats/${id}`, { method: "DELETE" });
export const stopChat = (id: string) => json(`/api/chats/${id}/stop`, { method: "POST" });
/** Answer the script this chat is waiting on. "always" stops it asking again until a restart. */
export const approveScript = (id: string, decision: "yes" | "always" | "no") =>
  json(`/api/chats/${id}/approve`, { method: "POST", body: JSON.stringify({ decision }) });
export const renameChat = (id: string, title: string) =>
  json(`/api/chats/${id}`, { method: "PATCH", body: JSON.stringify({ title }) });

/** A page of a stored result set, for "show all"; the head comes off the event. */
export const resultPage = (id: string, offset: number, limit: number) =>
  json<{
    id: string;
    columns: ResultColumn[];
    rows: Record<string, unknown>[];
    row_count: number;
    offset: number;
  }>(`/api/results/${id}?offset=${offset}&limit=${limit}`);

/** A plain link the cookie authenticates. `/csv`, not `.csv`: the bare `{result_id}` route
 *  would match `{id}.csv`. */
export const resultCsvUrl = (id: string) => `/api/results/${id}/csv`;

/** `rows` is 0 when the sandbox could not read the file, and `note` says why. */
export interface Upload {
  path: string;
  bytes: number;
  rows: number;
  columns: number;
  note: string;
}

export async function uploadFile(chatId: string, file: File): Promise<Upload> {
  const body = new FormData();
  body.append("file", file);
  return json<Upload>(`/api/chats/${chatId}/files`, { method: "POST", body });
}

const encodePath = (path: string) => path.split("/").map(encodeURIComponent).join("/");

/** A plain link; the cookie authenticates it. */
export const chatFileUrl = (chatId: string, path: string) =>
  `/api/chats/${chatId}/files/${encodePath(path)}`;

/** The head of a workspace file. Columns are names only: the bytes never went through Cube,
 *  so there is no `ResultColumn` metadata to give. */
export interface FilePreview {
  kind: "table" | "text" | "none";
  columns: string[];
  rows: string[][];
  text: string;
  bytes: number;
  truncated: boolean;
  /** Every data row in the file. Only a Run's output preview counts them. */
  rows_total?: number | null;
}

export const filePreview = (chatId: string, path: string) =>
  json<FilePreview>(`/api/chats/${chatId}/preview/${encodePath(path)}`);

/** The prompt is built from the workspace directory, so a dropped chip must drop its file. */
export const deleteChatFile = (chatId: string, path: string) =>
  json(chatFileUrl(chatId, path), { method: "DELETE" });

/** Stream one turn. EventSource is GET-only, so SSE frames are parsed by hand; a read may
 *  hold a partial frame or several. */
export async function* streamTurn(
  path: string,
  body: unknown,
  signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  // A refused turn has a JSON error body; parsing it as frames would read as a silent turn.
  if (!response.ok) {
    const body = await response.text();
    let message = `POST ${path} → ${response.status}`;
    try {
      message = JSON.parse(body).detail ?? message;
    } catch {
      /* not JSON — keep the path and status */
    }
    throw new Error(message);
  }
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

export const sendMessage = (
  id: string,
  question: string,
  files: string[],
  signal: AbortSignal,
) => streamTurn(`/api/chats/${id}/messages`, { question, files }, signal);

// --- dashboards ---------------------------------------------------------------------

export const listDashboards = () => json<DashboardRow[]>("/api/dashboard");

export const createDashboard = (title: string) =>
  json<{ id: string }>("/api/dashboard", { method: "POST", body: JSON.stringify({ title }) });

export const dashboard = (id: string) => json<DashboardDetail>(`/api/dashboard/${id}`);

export const renameDashboard = (id: string, title: string) =>
  json(`/api/dashboard/${id}`, { method: "PATCH", body: JSON.stringify({ title }) });

export const deleteDashboard = (id: string) =>
  json(`/api/dashboard/${id}`, { method: "DELETE" });

/** A plain POST, not a stream: the refresh is atomic, so there is no partial state to show. */
export const refreshDashboard = (id: string) =>
  json<RefreshOutcome>(`/api/dashboard/${id}/refresh`, { method: "POST" });

export const saveLayout = (id: string, spec: unknown, note: string) =>
  json<{ version: number }>(`/api/dashboard/${id}/layout`, {
    method: "POST",
    body: JSON.stringify({ spec, note }),
  });

export const mcpAccess = () => json<McpAccess>("/api/mcp");

export const setMcpEnabled = (enabled: boolean) =>
  json<{ enabled: boolean }>("/api/mcp/enabled", {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });

/** Returns the rebuilt configs too, so none still quotes the old token. */
export const rotateMcpToken = () =>
  json<Pick<McpAccess, "token" | "url" | "desktop" | "cli">>("/api/mcp/rotate", { method: "POST" });

// --- flows --------------------------------------------------------------------

/** A route that answers `text/plain`. A refusal is still JSON with `detail`. */
export async function text(path: string, init?: RequestInit): Promise<string> {
  const response = await fetch(path, init);
  const body = await response.text();
  if (!response.ok) {
    let message = `${init?.method ?? "GET"} ${path} → ${response.status}`;
    try {
      message = JSON.parse(body).detail ?? message;
    } catch {
      /* not JSON — keep the method, path and status */
    }
    throw new Error(message);
  }
  return body;
}

export const listFlows = () => json<FlowSummary[]>("/api/flows");

export const createFlow = (name: string, title: string, description = "") =>
  json<FlowSummary>("/api/flows", {
    method: "POST",
    body: JSON.stringify({ name, title, description }),
  });

export const flowDetail = (flow: string) => json<FlowDetail>(`/api/flows/${flow}`);

export const flowFile = (flow: string, path: string) =>
  text(`/api/flows/${flow}/files/${encodePath(path)}`);

/** `ok` is whether the *flow* still runs; the write lands either way. */
export const writeFlowFile = (flow: string, path: string, body: string) =>
  json<{ path: string; ok: boolean; errors: string[]; warnings: string[] }>(
    `/api/flows/${flow}/files/${encodePath(path)}`,
    { method: "PUT", body: JSON.stringify({ text: body }) },
  );

export const deleteFlowFile = (flow: string, path: string) =>
  json<{ ok: boolean; errors: string[] }>(`/api/flows/${flow}/files/${encodePath(path)}`, {
    method: "DELETE",
  });

export const listRuns = (flow: string) => json<RunSummary[]>(`/api/flows/${flow}/runs`);

export const beginRun = (flow: string, brief: string) =>
  json<{ run_id: string; current: string }>(`/api/flows/${flow}/runs`, {
    method: "POST",
    body: JSON.stringify({ brief }),
  });

export const runDetail = (flow: string, run: string) =>
  json<RunDetail>(`/api/flows/${flow}/runs/${run}`);

export const runEvents = (flow: string, run: string) =>
  json<AgentEvent[]>(`/api/flows/${flow}/runs/${run}/events`);

/** Run phases until the next Gate; call again to continue past it. A closed tab loses the
 *  stream, never the Run. */
export const advanceRun = (flow: string, run: string, signal: AbortSignal) =>
  streamTurn(`/api/flows/${flow}/runs/${run}/advance`, {}, signal);

export const stopRun = (flow: string, run: string) =>
  json<{ stopped: boolean }>(`/api/flows/${flow}/runs/${run}/stop`, { method: "POST" });

export const approveRunScript = (flow: string, run: string, choice: "yes" | "always" | "no") =>
  json(`/api/flows/${flow}/runs/${run}/approve`, {
    method: "POST",
    body: JSON.stringify({ choice }),
  });

export const runArtifact = (flow: string, run: string, path: string) =>
  text(`/api/flows/${flow}/runs/${run}/artifacts/${encodePath(path)}`);

/** One Phase output's head, with every row counted. */
export const runPreview = (flow: string, run: string, path: string) =>
  json<FilePreview>(`/api/flows/${flow}/runs/${run}/preview/${encodePath(path)}`);

/** Edit a Phase's output at its Gate; a 409 anywhere else. */
export const writeRunArtifact = (flow: string, run: string, path: string, body: string) =>
  json<{ path: string; ok: boolean }>(
    `/api/flows/${flow}/runs/${run}/artifacts/${encodePath(path)}`,
    { method: "PUT", body: JSON.stringify({ text: body }) },
  );

/** Point the Run back at one Phase; `advanceRun` runs it. */
export const rerunPhase = (flow: string, run: string, phase: string) =>
  json<{ run_id: string; current: string }>(
    `/api/flows/${flow}/runs/${run}/phases/${phase}/rerun`,
    { method: "POST" },
  );

export const deleteRun = (flow: string, run: string) =>
  json(`/api/flows/${flow}/runs/${run}`, { method: "DELETE" });

export const openFlowChat = (flow: string) =>
  json<{ chat_id: string; flow: string; title: string }>("/api/flows/chats", {
    method: "POST",
    body: JSON.stringify({ flow }),
  });

export const flowChatEvents = (id: string) =>
  json<AgentEvent[]>(`/api/flows/chats/${id}/events`);

export const sendFlowMessage = (id: string, question: string, signal: AbortSignal) =>
  streamTurn(`/api/flows/chats/${id}/messages`, { question }, signal);

export const stopFlowChat = (id: string) =>
  json(`/api/flows/chats/${id}/stop`, { method: "POST" });
