import { useCallback, useEffect, useRef, useState } from "react";

import * as api from "./api";
import { Answer } from "./components/Answer";
import type { ComposerHandle } from "./components/Composer";
import { Composer } from "./components/Composer";
import { Sidebar } from "./components/Sidebar";
import { Thinking } from "./components/Thinking";
import { ToolCall } from "./components/ToolCall";
import { Welcome } from "./components/Welcome";
import { applyEvent, buildTranscript } from "./events";
import type { Chat, Item } from "./types";

/** What the agent is doing right now, read off the tail of the transcript. */
function statusOf(items: Item[], busy: boolean): string {
  if (!busy) return "";
  const last = items[items.length - 1];
  if (last?.kind === "tool" && !last.end) return last.label || last.tool;
  if (last?.kind === "thinking" && last.live) return "Thinking";
  if (last?.kind === "answer" && last.live) return "Writing the answer";
  return "Working";
}

export default function App() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [busy, setBusy] = useState(false);
  const [turnStart, setTurnStart] = useState<number | undefined>();
  const [cubeUrl, setCubeUrl] = useState("");
  const [entities, setEntities] = useState<{ name: string; view: boolean; members: number }[]>([]);
  const [fatal, setFatal] = useState<string | null>(null);

  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const composer = useRef<ComposerHandle>(null);
  // Stop following the stream once the reader scrolls up to re-read something.
  const pinned = useRef(true);

  const open = useCallback(async (id: string) => {
    abort.current?.abort();
    setActiveId(id);
    setBusy(false);
    pinned.current = true;
    setItems(buildTranscript(await api.chatEvents(id)));
  }, []);

  useEffect(() => {
    (async () => {
      try {
        await api.claimToken();
        const [loaded, catalog] = await Promise.all([api.listChats(), api.catalog()]);
        setChats(loaded);
        setCubeUrl(catalog.cube_url ?? "");
        setEntities(catalog.entities ?? []);
        if (loaded.length) await open(loaded[0].id);
      } catch (err) {
        setFatal(String(err));
      }
    })();
  }, [open]);

  // Set scrollTop directly rather than scrollIntoView: a smooth scroll retriggered on
  // every token fights itself and the transcript judders.
  useEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [items]);

  const create = useCallback(async () => {
    const chat = await api.createChat();
    setChats((prev) => [chat, ...prev]);
    setActiveId(chat.id);
    setItems([]);
  }, []);

  useEffect(() => {
    const keys = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        void create();
      }
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, [create]);

  const send = async (question: string) => {
    if (!activeId) return;
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setTurnStart(Date.now());
    pinned.current = true;

    try {
      for await (const event of api.sendMessage(activeId, question, controller.signal)) {
        setItems((prev) => applyEvent(prev, event));
      }
    } catch (err) {
      // An aborted fetch is the stop button working, not a failure.
      if (!controller.signal.aborted) {
        setItems((prev) => [...prev, { kind: "error", message: String(err) }]);
      }
    } finally {
      setBusy(false);
      setTurnStart(undefined);
      // The first question renames the chat server-side; pick that up.
      setChats(await api.listChats());
    }
  };

  const stop = async () => {
    if (activeId) await api.stopChat(activeId);
    abort.current?.abort();
    setBusy(false);
  };

  const remove = async (id: string) => {
    await api.deleteChat(id);
    const remaining = chats.filter((c) => c.id !== id);
    setChats(remaining);
    if (id === activeId) {
      setActiveId(null);
      setItems([]);
      if (remaining.length) await open(remaining[0].id);
    }
  };

  const rename = async (id: string, title: string) => {
    await api.renameChat(id, title);
    setChats((prev) => prev.map((c) => (c.id === id ? { ...c, title } : c)));
  };

  if (fatal) {
    return (
      <div className="fatal">
        <h1>Cannot reach the analyst server</h1>
        <p>{fatal}</p>
        <p>
          Open the URL printed by <code>analyst web</code>, token included.
        </p>
      </div>
    );
  }

  return (
    <div className="app">
      <Sidebar
        chats={chats}
        activeId={activeId}
        cubeUrl={cubeUrl}
        onSelect={(id) => void open(id)}
        onCreate={() => void create()}
        onRename={(id, title) => void rename(id, title)}
        onDelete={(id) => void remove(id)}
      />

      <main className="main">
        <div
          className="transcript"
          ref={scroller}
          onScroll={(e) => {
            const el = e.currentTarget;
            pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          }}
        >
          {items.length === 0 && (
            <Welcome
              entities={entities}
              onPick={(q) => {
                if (!activeId) void create().then(() => composer.current?.fill(q));
                else composer.current?.fill(q);
              }}
            />
          )}

          {items.map((item, i) => {
            switch (item.kind) {
              case "user":
                return (
                  <div key={i} className="row user">
                    {item.text}
                  </div>
                );
              case "thinking":
                return (
                  <div key={i} className="row">
                    <Thinking
                      text={item.text}
                      open={item.open}
                      live={item.live}
                      startedAt={item.startedAt}
                      ms={item.ms}
                    />
                  </div>
                );
              case "tool":
                return (
                  <div key={i} className="row">
                    <ToolCall
                      tool={item.tool}
                      label={item.label}
                      query={item.query}
                      end={item.end}
                      startedAt={item.startedAt}
                      ms={item.ms}
                    />
                  </div>
                );
              case "answer":
                return (
                  <div key={i} className="row">
                    <Answer text={item.text} live={item.live} />
                  </div>
                );
              case "compacted":
                return (
                  <div key={i} className="row compacted">
                    <span>
                      Earlier messages were summarized to fit the context — {item.before} became{" "}
                      {item.after}
                    </span>
                  </div>
                );
              case "error":
                return (
                  <div key={i} className="row error">
                    {item.message}
                  </div>
                );
            }
          })}
        </div>

        <Composer
          ref={composer}
          busy={busy}
          disabled={!activeId}
          startedAt={turnStart}
          status={statusOf(items, busy)}
          onSend={(q) => void send(q)}
          onStop={() => void stop()}
        />
      </main>
    </div>
  );
}
