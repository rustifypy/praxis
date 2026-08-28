import { useEffect, useRef, useState } from "react";

import * as api from "../api";
import { applyEvent, buildTranscript } from "../events";
import type { Item } from "../types";

import { Button } from "./Button";
import { Empty } from "./Empty";
import { Transcript } from "./Transcript";

const EXAMPLES = [
  "What steps should a weekly revenue report have?",
  "Add a phase that turns the numbers into a short summary",
  "Why won't this flow run?",
];

/**
 * The flow's authoring chat: the pane beside the files.
 *
 * The same reducer, blocks and stream as the other two chats, because it is the same event stream
 * — `EditorChat` is the shape this follows, and the reason both exist rather than one is that the
 * three agents share no tools.
 *
 * **One chat per flow, for the life of the flow.** Unlike the editor's pane, which starts fresh
 * each session, this one loads its history: a flow is built over days and "what did we decide
 * about phase 2" is a question the transcript answers. The binding lives in the flow's folder, so
 * the chat follows the flow rather than the tab.
 *
 * **It writes files.** Every message can change the flow on disk, so `onChanged` fires after each
 * turn and the page re-reads — otherwise the tree beside it would show the flow as it was before
 * the sentence that changed it.
 */
export function FlowChat({
  flow,
  onChanged,
}: {
  flow: string;
  onChanged: () => void;
}) {
  const [chatId, setChatId] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);
  const tail = useRef<HTMLDivElement>(null);

  // A different flow is a different conversation, and its own stored history.
  useEffect(() => {
    let live = true;
    setItems([]);
    setChatId("");
    setError("");
    (async () => {
      try {
        const opened = await api.openFlowChat(flow);
        if (!live) return;
        setChatId(opened.chat_id);
        setItems(buildTranscript(await api.flowChatEvents(opened.chat_id)));
      } catch (e) {
        if (live) setError((e as Error).message);
      }
    })();
    return () => {
      live = false;
    };
  }, [flow]);

  useEffect(() => {
    tail.current?.scrollIntoView({ block: "end" });
  }, [items]);

  const ask = async (text: string) => {
    if (!text.trim() || busy || !chatId) return;
    setBusy(true);
    setError("");
    setQuestion("");
    const controller = new AbortController();
    abort.current = controller;
    try {
      for await (const event of api.sendFlowMessage(chatId, text, controller.signal)) {
        setItems((prev) => applyEvent(prev, event));
      }
    } catch (e) {
      // An aborted fetch is the stop button working, not a failure.
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      setBusy(false);
      abort.current = null;
      // Whatever it wrote is on disk now, and the tree is showing what was there before.
      onChanged();
    }
  };

  const stop = async () => {
    if (chatId) await api.stopFlowChat(chatId);
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
          It writes this flow's files — the phases, their instructions, and the rules they share.
          It does not run the work: starting a run is the Runs tab.
        </Empty>
      ) : (
        <div className="edchat-log">
          <Transcript items={items} chatId={chatId} />
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
          placeholder={`Ask about ${flow}…`}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={busy || !chatId}
        />
        {busy ? (
          <Button type="button" onClick={() => void stop()}>
            Stop
          </Button>
        ) : (
          <Button type="submit" variant="primary" disabled={!question.trim() || !chatId}>
            Send
          </Button>
        )}
      </form>
    </div>
  );
}
