import { useEffect, useRef, useState } from "react";

import * as api from "../api";
import { applyEvent } from "../events";
import * as sourcesApi from "../sources";
import type { Item } from "../types";

import { Button } from "./Button";
import { Empty } from "./Empty";
import { Transcript } from "./Transcript";

const EXAMPLES = [
  "Describe the measures on invoice_line",
  "Add a gross_amount measure to invoice_line",
  "Make a view of track economics",
];

/**
 * The model editor's chat. It never writes the buffer: proposals go up to the editor, which owns
 * it. Starts empty each session; a chat is created on the first message.
 */
export function EditorChat({
  modelRef,
  textOf,
  reviewed,
  onReview,
}: {
  /** The branch being edited. Not `ref`, which React reserves. */
  modelRef: string;
  textOf: (path: string) => string;
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

  // A branch switch starts a new conversation.
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
        <Empty
          action={
            <div className="edchat-examples">
            {EXAMPLES.map((e) => (
              <Button key={e} onClick={() => setQuestion(e)} disabled={busy}>
                {e}
              </Button>
              ))}
            </div>
          }
        >
          It reads before it proposes, and it saves nothing — every change comes back as a diff
          for you to accept. It cannot commit or merge.
        </Empty>
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
          <Button type="button" onClick={() => void stop()}>
            Stop
          </Button>
        ) : (
          <Button type="submit" variant="primary" disabled={!question.trim()}>
            Send
          </Button>
        )}
      </form>
    </div>
  );
}
