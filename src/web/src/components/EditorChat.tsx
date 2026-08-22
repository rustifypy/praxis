import { useEffect, useRef, useState } from "react";

import * as api from "../api";
import { applyEvent, buildTranscript } from "../events";
import * as sourcesApi from "../sources";
import type { Item } from "../types";

import { Transcript } from "./Transcript";

const EXAMPLES = [
  "Describe the measures on invoice_line",
  "Add a gross_amount measure to invoice_line",
  "Make a view of track economics",
];

/**
 * The model editor's chat.
 *
 * The same reducer and the same blocks as the question chat, because it is the same event
 * stream — this had its own four-type reducer, so the editor's reasoning was emitted and
 * never shown. Only the frame around it is local: a narrow panel, one ref, no uploads.
 */
export function EditorChat({
  modelRef,
  onChanged,
}: {
  /** The ref this conversation edits. Not `ref` — React reserves that prop name. */
  modelRef: string;
  onChanged: () => void;
}) {
  const [chat, setChat] = useState<string | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);
  const tail = useRef<HTMLDivElement>(null);

  // The newest conversation about this ref, rather than a fresh one every time the panel
  // opens: `run_turn` stores an editor turn like any other, and it was being written and
  // never read back.
  useEffect(() => {
    let current = true;
    setChat(null);
    setItems([]);
    void (async () => {
      const chats = await sourcesApi.editChats();
      const latest = chats.find((c) => c.ref === modelRef);
      if (!current || !latest) return;
      setChat(latest.id);
      setItems(buildTranscript(await api.chatEvents(latest.id)));
    })();
    return () => {
      current = false;
    };
  }, [modelRef]);

  useEffect(() => {
    tail.current?.scrollIntoView({ block: "end" });
  }, [items]);

  const ask = async (text: string) => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setError("");
    setQuestion("");
    const controller = new AbortController();
    abort.current = controller;
    try {
      const id = chat ?? (await sourcesApi.createEditChat(modelRef)).id;
      setChat(id);
      for await (const event of sourcesApi.sendEditMessage(id, text, controller.signal)) {
        setItems((prev) => applyEvent(prev, event));
      }
      // The file list and the ref's changed count both move underneath the panel above.
      onChanged();
    } catch (e) {
      // An aborted fetch is the stop button working, not a failure.
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      setBusy(false);
      abort.current = null;
    }
  };

  const stop = async () => {
    if (chat) await api.stopChat(chat);
    abort.current?.abort();
    setBusy(false);
  };

  return (
    <div className="edchat">
      {items.length === 0 ? (
        <div className="edchat-empty">
          <p>
            It reads before it writes, and every write goes through the same gate. It cannot
            merge.
          </p>
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
          <Transcript items={items} chatId={chat ?? ""} />
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
          <button type="button" onClick={() => void stop()}>
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
