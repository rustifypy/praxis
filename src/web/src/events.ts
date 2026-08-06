import type { AgentEvent, Item, ToolEnd } from "./types";

/**
 * Close every open block and stop its animations.
 *
 * Returns new objects rather than mutating. Mutating an item in place left React with
 * the same object identity, so a collapsed reasoning panel stayed visually expanded and
 * a finished block kept its caret blinking — the transcript looked frozen.
 */
function settle(items: Item[]): Item[] {
  return items.map((item) => {
    if (item.kind === "thinking" && (item.open || item.live)) {
      return {
        ...item,
        open: false,
        live: false,
        ms: item.ms ?? (item.startedAt ? Date.now() - item.startedAt : undefined),
      };
    }
    if (item.kind === "answer" && item.live) return { ...item, live: false };
    return item;
  });
}

/**
 * Fold one event into the rendered transcript.
 *
 * The same reducer serves replay and live streaming, so what you see after a reload is
 * what you saw as it happened. The only difference is the payload key: a live delta
 * appends to the block already open, a replayed block starts a new one.
 */
export function applyEvent(items: Item[], event: AgentEvent): Item[] {
  const last = items[items.length - 1];
  const delta: string | undefined = event.data.delta;
  const whole: string | undefined = event.data.text;
  const streaming = delta !== undefined;

  switch (event.type) {
    case "user":
      return [...settle(items), { kind: "user", text: whole ?? "" }];

    case "thinking":
      if (streaming && last?.kind === "thinking" && last.live) {
        return [...items.slice(0, -1), { ...last, text: last.text + delta }];
      }
      return [
        ...settle(items),
        {
          kind: "thinking",
          text: delta ?? whole ?? "",
          open: streaming,
          live: streaming,
          startedAt: streaming ? Date.now() : undefined,
        },
      ];

    case "text":
      if (streaming && last?.kind === "answer" && last.live) {
        return [...items.slice(0, -1), { ...last, text: last.text + delta }];
      }
      return [...settle(items), { kind: "answer", text: delta ?? whole ?? "", live: streaming }];

    case "tool_start":
      return [
        ...settle(items),
        {
          kind: "tool",
          tool: event.data.tool,
          label: event.data.label ?? "",
          query: event.data.query,
          startedAt: Date.now(),
        },
      ];

    case "tool_end": {
      // Attach to the most recent tool still waiting for its result.
      const i = items.findLastIndex((item) => item.kind === "tool" && !item.end);
      if (i === -1) return items;
      const tool = items[i] as Extract<Item, { kind: "tool" }>;
      const next = [...items];
      next[i] = {
        ...tool,
        end: event.data as ToolEnd,
        // The server times the query; this times the whole call including the model's
        // wait. Only shown when the server has nothing more precise.
        ms: tool.startedAt ? Date.now() - tool.startedAt : undefined,
      };
      return next;
    }

    case "compacted":
      return [
        ...settle(items),
        { kind: "compacted", before: event.data.before, after: event.data.after },
      ];

    case "error":
      return [
        ...settle(items),
        { kind: "error", message: event.data.message ?? "Something went wrong." },
      ];

    case "done":
      return settle(items);

    default:
      return items;
  }
}

export function buildTranscript(events: AgentEvent[]): Item[] {
  return events.reduce(applyEvent, [] as Item[]);
}
