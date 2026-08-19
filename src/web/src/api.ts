import type {
  AgentEvent,
  Chat,
  DashboardDetail,
  DashboardRow,
  McpAccess,
  RefreshOutcome,
  ResultColumn,
} from "./types";

/**
 * Trade the URL token for the cookie, then clean the address bar.
 *
 * Serving index.html already sets the cookie, but `npm run dev` serves the page from
 * Vite and only proxies /api — so one authenticated API call is what establishes the
 * session there. Harmless in production, required in dev.
 */
export async function claimToken(): Promise<void> {
  const url = new URL(window.location.href);
  if (!url.searchParams.get("token")) return;
  await fetch(`/api/chats${url.search}`);
  url.searchParams.delete("token");
  window.history.replaceState({}, "", url.pathname);
}

/**
 * Every JSON call in the app.
 *
 * FastAPI puts its message in `detail`, and those messages are written to be read by a
 * person — "port 4001 is already assigned", not "400". Unwrapping it here means both the
 * chat and the sources panel show the same quality of error.
 */
export async function json<T>(path: string, init?: RequestInit): Promise<T> {
  // An upload sends FormData, and the browser has to set that content type itself — it carries
  // the multipart boundary, which nothing here can know.
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

interface CatalogEntity {
  name: string;
  view: boolean;
  members: number;
  description?: string;
}

export const catalog = () =>
  json<{ cube_url: string; entities: CatalogEntity[] }>("/api/catalog");
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

/** A page of a stored result set. Only reached by "show all" — the transcript renders the
 *  head of the rows straight off the event, so opening a chat costs no extra request. */
export const resultPage = (id: string, offset: number, limit: number) =>
  json<{
    id: string;
    columns: ResultColumn[];
    rows: Record<string, unknown>[];
    row_count: number;
    offset: number;
  }>(`/api/results/${id}?offset=${offset}&limit=${limit}`);

/** The CSV is a plain link, not a fetch: the token cookie authenticates it and the browser
 *  handles the download. `csv` is a path segment because `{id}.csv` would be matched by the
 *  bare `{result_id}` route and never reach this one. */
export const resultCsvUrl = (id: string) => `/api/results/${id}/csv`;

/** What one upload landed as. `rows` is 0 when the sandbox could not read it, and `note` says
 *  why — an upload with no profile is still an upload. */
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

/** A workspace file as a plain link, like the result CSV — the cookie authenticates it. */
export const chatFileUrl = (chatId: string, path: string) =>
  `/api/chats/${chatId}/files/${encodePath(path)}`;

/**
 * The head of a workspace file, parsed enough to render — `table` for a CSV, `text` for
 * anything readable, `none` for bytes with no useful head.
 *
 * Columns are names only. These bytes never went through Cube, so there is no `ResultColumn`
 * to say a column is money, and a guess dressed as metadata is worse than plain strings.
 */
export interface FilePreview {
  kind: "table" | "text" | "none";
  columns: string[];
  rows: string[][];
  text: string;
  bytes: number;
  truncated: boolean;
}

export const filePreview = (chatId: string, path: string) =>
  json<FilePreview>(`/api/chats/${chatId}/preview/${encodePath(path)}`);

/** Take a file back off the workspace. Only the composer calls it, and only before the question
 *  it was attached to has been sent — the prompt is built from the directory, so dropping the
 *  chip without dropping the file would leave the agent still holding it. */
export const deleteChatFile = (chatId: string, path: string) =>
  json(chatFileUrl(chatId, path), { method: "DELETE" });

/**
 * Stream one turn.
 *
 * EventSource is GET-only, so the SSE frames are parsed by hand off a fetch body.
 * Frames are split on a blank line because a single read can contain a partial frame
 * or several whole ones.
 */
export async function* sendMessage(
  id: string,
  question: string,
  files: string[],
  signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const response = await fetch(`/api/chats/${id}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, files }),
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

// --- dashboards ---------------------------------------------------------------------

export const listDashboards = () => json<DashboardRow[]>("/api/dashboards");

export const createDashboard = (title: string) =>
  json<{ id: string }>("/api/dashboards", { method: "POST", body: JSON.stringify({ title }) });

export const dashboard = (id: string) => json<DashboardDetail>(`/api/dashboards/${id}`);

export const renameDashboard = (id: string, title: string) =>
  json(`/api/dashboards/${id}`, { method: "PATCH", body: JSON.stringify({ title }) });

export const deleteDashboard = (id: string) =>
  json(`/api/dashboards/${id}`, { method: "DELETE" });

/** A plain POST, not a stream: the refresh is atomic, so there is no partial state to show. */
export const refreshDashboard = (id: string) =>
  json<RefreshOutcome>(`/api/dashboards/${id}/refresh`, { method: "POST" });

export const saveLayout = (id: string, spec: unknown, note: string) =>
  json<{ version: number }>(`/api/dashboards/${id}/layout`, {
    method: "POST",
    body: JSON.stringify({ spec, note }),
  });

export const mcpAccess = () => json<McpAccess>("/api/mcp");

export const setMcpEnabled = (enabled: boolean) =>
  json<{ enabled: boolean }>("/api/mcp/enabled", {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });

/** Returns the rebuilt configs alongside the token, so the panel never shows a new token
 *  beside a block still quoting the old one. */
export const rotateMcpToken = () =>
  json<Pick<McpAccess, "token" | "url" | "desktop" | "cli">>("/api/mcp/rotate", { method: "POST" });
