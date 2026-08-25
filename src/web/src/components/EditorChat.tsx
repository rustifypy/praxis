import { useEffect, useRef, useState } from "react";

import * as api from "../api";
import { applyEvent } from "../events";
import * as sourcesApi from "../sources";
import type { Item } from "../types";

import { Transcript } from "./Transcript";

const EXAMPLES = [
  "Describe the measures on invoice_line",
  "Add a gross_amount measure to invoice_line",
  "Make a view of track economics",
];

/**
 * The model editor's chat: the third pane, beside the tree and the file.
 *
 * The same reducer and the same blocks as the question chat, because it is the same event stream
 * — this had its own four-type reducer once, so the editor's reasoning was emitted and never
 * shown. Only the frame is local: one branch, no uploads, no chat list.
 *
 * **It sends the unsaved buffer with every message.** Staging is the browser's, so the server has
 * no idea what the model currently looks like; an agent reading only the committed branch would
 * propose a change that silently reverts whatever was accepted a minute ago.
 *
 * **Nothing it says is saved.** The agent proposes; each proposal arrives as a card, and clicking
 * it opens the diff in the editor pane where hunks are accepted one at a time. This component
 * never writes to the buffer itself — it hands the proposal up to the editor, which owns it.
 *
 * The pane starts empty each session and creates a chat on the first message. Every turn is still
 * stored and replayable, but a history list would be a fourth column in a three-column layout.
 */
export function EditorChat({
  modelRef,
  textOf,
  reviewed,
  onReview,
}: {
  /** The branch this conversation edits. Not `ref` — React reserves that prop name. */
  modelRef: string;
  /** What the editor currently holds for a path, so a proposal card can size its own diff. */
  textOf: (path: string) => string;
  /** Paths already opened for review, so a settled card says so. */
  reviewed: Set<string>;
  onReview: (path: string, text: string | null) => void;
}) {
  const [chat, setChat] = useState<string | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);
  const tail = useRef<HTMLDivElement>(null);

  // A branch switch is a different model, so it is a different conversation.
  useEffect(() => {
    setChat(null);
    setItems([]);
    setError("");
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
            It reads before it proposes, and it saves nothing — every change comes back as a diff
            for you to accept. It cannot commit or merge.
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
          <Transcript
            items={items}
            chatId={chat ?? ""}
            proposals={{ textOf, reviewed, onReview }}
          />
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
          placeholder={`Ask for a change to ${modelRef}…`}
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
