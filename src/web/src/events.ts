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
  const delta: string | undefined = event.data.delta;
  const whole: string | undefined = event.data.text;
  const streaming = delta !== undefined;

  /**
   * Append a delta to the open block of this kind, wherever it sits.
   *
   * Not `items[items.length - 1]`: an answer takes ownership of its turn's results, so the
   * moment one is hoisted the open answer is no longer the last item and every further
   * token started a *new* answer block — one word per line down the page. Only live
   * streaming showed it, because `service.flush` coalesces deltas into one block before
   * they are stored, so a replayed transcript looked correct.
   */
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
      // Everything stays in the order it happened, results included: with two queries in a
      // turn, moving the data below the prose separates each table from the step that
      // fetched it and leaves the reader matching them up by name.
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

    // Onto the step that is asking, not a block of its own. A separate card sat *below* the
    // run, which put a script's output above the script that printed it, and once answered it
    // was a dead card carrying a copy of a file the step already shows.
    case "approval": {
      const i = items.findLastIndex((item) => item.kind === "tool" && !item.end);
      if (i === -1) return items;
      const tool = items[i] as Extract<Item, { kind: "tool" }>;
      const next = [...items];
      next[i] = { ...tool, script: event.data.script ?? "", awaiting: true };
      return next;
    }

    // Its own block, unlike `approval`, which folds onto the step that is asking. A proposal
    // outlives the step that made it — it is still waiting for a click after the turn has ended,
    // and after a reload it is the only record that the change was ever offered.
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
      // Attach to the most recent tool still waiting for its result.
      const i = items.findLastIndex((item) => item.kind === "tool" && !item.end);
      if (i === -1) return items;
      const tool = items[i] as Extract<Item, { kind: "tool" }>;
      const end = event.data as ToolEnd;
      const next = [...items];
      next[i] = {
        ...tool,
        end,
        // The server times the query; this times the whole call including the model's
        // wait. Only shown when the server has nothing more precise.
        ms: tool.startedAt ? Date.now() - tool.startedAt : undefined,
      };
      // Data earns its own block. The label is the server's sentence for the query, so it
      // says what this table is without the trace step above it being open.
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
      // A file a script wrote earns a block too — otherwise a chart it drew is a file nobody
      // knows exists. `wrote` is paths; the block fetches each one from the workspace.
      if (tool.tool === "run_python" && end.wrote?.length) {
        next.push({ kind: "files", label: tool.label, paths: end.wrote });
      }
      // The agent overriding the chart it was given. It authors its own tool events like
      // everything else, so this needs no new event type — and it lands on the newest
      // result because that is the one `set_chart` validated itself against.
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
