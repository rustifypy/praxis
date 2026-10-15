import type { AgentEvent, Item, ToolEnd } from "./types";

/** Close every open block. Returns new objects: React won't re-render a mutated item. */
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
 * Fold one event into the transcript. Serves both replay and live streaming: a live `delta`
 * appends to the open block, a replayed `text` starts a new one.
 */
export function applyEvent(items: Item[], event: AgentEvent): Item[] {
  const delta: string | undefined = event.data.delta;
  const whole: string | undefined = event.data.text;
  const streaming = delta !== undefined;

  // The open block of this kind may not be the last item (results land after it).
  function extend(kind: "thinking" | "answer"): Item[] | null {
    if (!streaming) return null;
    const open = items.findLastIndex((item) => item.kind === kind && item.live);
    if (open === -1) return null;
    const block = items[open] as Extract<Item, { kind: "thinking" | "answer" }>;
    const next = [...items];
    next[open] = { ...block, text: block.text + delta };
    return next;
  }

  switch (event.type) {
    case "user":
      return [
        ...settle(items),
        { kind: "user", text: whole ?? "", files: event.data.files ?? [] },
      ];

    case "thinking":
      return (
        extend("thinking") ?? [
          ...settle(items),
          {
            kind: "thinking",
            text: delta ?? whole ?? "",
            open: streaming,
            live: streaming,
            startedAt: streaming ? Date.now() : undefined,
          },
        ]
      );

    case "text": {
      const extended = extend("answer");
      if (extended) return extended;
      // Results stay in order, beside the step that fetched them, not hoisted below the prose.
      return [...settle(items), { kind: "answer", text: delta ?? whole ?? "", live: streaming }];
    }

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

    // Folds onto the step that is asking, so the script sits above its own output.
    case "approval": {
      const i = items.findLastIndex((item) => item.kind === "tool" && !item.end);
      if (i === -1) return items;
      const tool = items[i] as Extract<Item, { kind: "tool" }>;
      const next = [...items];
      next[i] = { ...tool, script: event.data.script ?? "", awaiting: true };
      return next;
    }

    // Its own block: a proposal outlives its step and still awaits a click after the turn ends.
    case "proposal":
      return [
        ...items,
        {
          kind: "proposal",
          path: event.data.path,
          text: event.data.text ?? null,
          ref: event.data.ref ?? "",
        },
      ];

    case "output": {
      const i = items.findLastIndex((item) => item.kind === "tool" && !item.end);
      if (i === -1) return items;
      const tool = items[i] as Extract<Item, { kind: "tool" }>;
      const next = [...items];
      next[i] = { ...tool, live: (tool.live ?? "") + (delta ?? "") };
      return next;
    }

    case "tool_end": {
      const i = items.findLastIndex((item) => item.kind === "tool" && !item.end);
      if (i === -1) return items;
      const tool = items[i] as Extract<Item, { kind: "tool" }>;
      const end = event.data as ToolEnd;
      const next = [...items];
      next[i] = {
        ...tool,
        end,
        // Fallback timing; the server's own query time is preferred when present.
        ms: tool.startedAt ? Date.now() - tool.startedAt : undefined,
      };
      if (tool.tool === "run_query" && end.ok && end.columns?.length && end.preview?.length) {
        next.push({
          kind: "result",
          resultId: end.result_id ?? "",
          label: tool.label,
          columns: end.columns,
          rows: end.preview,
          rowCount: end.rows_total ?? end.preview.length,
          chart: end.chart ?? null,
        });
      }
      if (tool.tool === "run_python" && end.wrote?.length) {
        next.push({ kind: "files", label: tool.label, paths: end.wrote });
      }
      // The newest result is the one `set_chart` validated against.
      if (tool.tool === "set_chart" && end.ok && end.chart) {
        const target = next.findLastIndex((item) => item.kind === "result");
        if (target !== -1) {
          const result = next[target] as Extract<Item, { kind: "result" }>;
          next[target] = { ...result, chart: end.chart };
        }
      }
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
