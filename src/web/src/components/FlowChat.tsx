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
 * The flow's authoring chat. One chat per flow, history included, since a flow is built over
 * days. It writes files, so `onChanged` fires after every turn.
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
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      setBusy(false);
      abort.current = null;
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
