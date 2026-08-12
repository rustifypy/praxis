import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import * as api from "../sources";
import type { AgentEvent } from "../types";

interface Tool {
  label: string;
  ok?: boolean;
  note?: string;
  errors?: string[];
}

interface Turn {
  role: "you" | "agent";
  text: string;
  tools: Tool[];
}

const EXAMPLES = [
  "Describe the measures on invoice_line",
  "Add a gross_amount measure to invoice_line",
  "Make a view of track economics",
];

/**
 * The model editor's chat.
 *
 * Markdown, not plain text: this agent answers with the YAML it wrote, and a fenced block
 * rendered as literal backticks was the worst of it. `.edchat-*` rather than the chat page's
 * `.answer`, which carries a streaming caret on its last child.
 */
export function EditorChat({ name, onChanged }: { name: string; onChanged: () => void }) {
  const [chat, setChat] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);
  const tail = useRef<HTMLDivElement>(null);

  useEffect(() => {
    tail.current?.scrollIntoView({ block: "end" });
  }, [turns]);

  /** Fold one event into the open agent turn, copying rather than mutating it. */
  const apply = (event: AgentEvent) =>
    setTurns((prev) => {
      const turns = [...prev];
      const last = { ...turns[turns.length - 1] };
      const tools = [...last.tools];
      if (event.type === "tool_start") {
        tools.push({ label: String(event.data.label ?? event.data.tool) });
      } else if (event.type === "tool_end" && tools.length) {
        tools[tools.length - 1] = {
          ...tools[tools.length - 1],
          ok: Boolean(event.data.ok),
          note: event.data.note,
          errors: event.data.errors,
        };
      } else if (event.type === "text") {
        last.text += String(event.data.delta ?? event.data.text ?? "");
      } else if (event.type === "error") {
        last.text += `\n\n${event.data.message}`;
      }
      last.tools = tools;
      turns[turns.length - 1] = last;
      return turns;
    });

  const ask = async (text: string) => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setError("");
    setQuestion("");
    try {
      const id = chat ?? (await api.createEditChat(name)).id;
      setChat(id);
      setTurns((prev) => [
        ...prev,
        { role: "you", text, tools: [] },
        { role: "agent", text: "", tools: [] },
      ]);
      abort.current = new AbortController();
      for await (const event of api.sendEditMessage(name, id, text, abort.current.signal)) {
        if (event.type !== "user") apply(event);
      }
      // Files, branch state and the dirty list all move underneath the panel above.
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      abort.current = null;
    }
  };

  return (
    <div className="edchat">
      {turns.length === 0 ? (
        <div className="edchat-empty">
          <p>It reads before it writes, and every write goes through the same gate. It cannot merge.</p>
          <div className="edchat-examples">
            {EXAMPLES.map((e) => (
              <button key={e} onClick={() => setQuestion(e)} disabled={busy}>
                {e}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="edchat-log">
          {turns.map((turn, i) =>
            turn.role === "you" ? (
              <p key={i} className="edchat-you">
                {turn.text}
              </p>
            ) : (
              <div key={i} className="edchat-agent">
                {turn.tools.map((t, j) => (
                  <div key={j} className={`edchat-tool ${t.ok === false ? "bad" : ""}`}>
                    <span className="edchat-tick" aria-hidden>
                      {t.ok === undefined ? "·" : t.ok ? "✓" : "✕"}
                    </span>
                    <span className="edchat-label">{t.label}</span>
                    {t.note && <span className="edchat-note">{t.note}</span>}
                    {t.errors?.map((e, k) => (
                      <span key={k} className="edchat-note">
                        {e}
                      </span>
                    ))}
                  </div>
                ))}
                {turn.text ? (
                  <div className="edchat-md">
                    <Markdown remarkPlugins={[remarkGfm]}>{turn.text}</Markdown>
                  </div>
                ) : (
                  busy && i === turns.length - 1 && <p className="edchat-wait">Working…</p>
                )}
              </div>
            ),
          )}
          <div ref={tail} />
        </div>
      )}

      {error && <div className="editor-note bad">{error}</div>}

      <form
        className="edchat-ask"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(question);
        }}
      >
        <input
          value={question}
          placeholder="Ask for a change to this model…"
          onChange={(e) => setQuestion(e.target.value)}
          disabled={busy}
        />
        {busy ? (
          <button type="button" onClick={() => abort.current?.abort()}>
            Stop
          </button>
        ) : (
          <button type="submit" className="primary" disabled={!question.trim()}>
            Send
          </button>
        )}
      </form>
    </div>
  );
}
