import type { AgentEvent, Chat } from "./types";

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

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`${init?.method ?? "GET"} ${path} → ${response.status}`);
  return response.json() as Promise<T>;
}

export interface CatalogEntity {
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
