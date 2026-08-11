import type {
  AgentEvent,
  Chat,
  DashboardDetail,
  DashboardRow,
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
  const response = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
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
  signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const response = await fetch(`/api/chats/${id}/messages`, {
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
