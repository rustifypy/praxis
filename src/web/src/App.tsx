import { useCallback, useEffect, useRef, useState } from "react";

import * as api from "./api";
import { AddSource } from "./components/AddSource";
import { Answer } from "./components/Answer";
import { ChatHeader } from "./components/ChatHeader";
import { ChatNav } from "./components/ChatNav";
import type { ComposerHandle } from "./components/Composer";
import { Composer } from "./components/Composer";
import { DataNav } from "./components/DataNav";
import { Rail } from "./components/Rail";
import { ResultBlock } from "./components/ResultBlock";
import { SourceDetail } from "./components/SourceDetail";
import { Sources } from "./components/Sources";
import { Thinking } from "./components/Thinking";
import { ToolCall } from "./components/ToolCall";
import { Welcome } from "./components/Welcome";
import { applyEvent, buildTranscript } from "./events";
import { useHash } from "./hooks";
import * as sourcesApi from "./sources";
import type { DriverSpec, JobState, SourceRow, System } from "./sources";
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

  // Which mode the rail is on. A hash rather than a router: three views do not justify
  // one, and the hash is the only state — a mirrored React copy let the two disagree.
  const hash = useHash();
  const view: "chat" | "sources" = hash.startsWith("#sources") ? "sources" : "chat";
  const selected = hash.startsWith("#sources/")
    ? decodeURIComponent(hash.slice("#sources/".length))
    : null;
  const [system, setSystem] = useState<System>({ ok: true });
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [nextPort, setNextPort] = useState(4001);
  const [drivers, setDrivers] = useState<DriverSpec[]>([]);
  const [driverMeta, setDriverMeta] = useState({ local_hosts: [] as string[], localhost_note: "" });
  const [job, setJob] = useState<JobState | null>(null);
  // The connection drawer: "new" to add, a source row to edit its credentials.
  const [drawer, setDrawer] = useState<"new" | SourceRow | null>(null);

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

  const refreshSources = useCallback(async () => {
    // All three concurrently: /api/system spawns docker, and awaiting it after the
    // others serialised the slowest call for no reason.
    const [listed, current, health] = await Promise.all([
      sourcesApi.list(),
      sourcesApi.job(),
      sourcesApi.system(),
    ]);
    setSources(listed.sources);
    setNextPort(listed.next_port);
    setSystem(health);
    // The stream is the sole carrier of events; this only refreshes the header, so an
    // in-flight job keeps the events it has already received.
    setJob((prev) =>
      current.id === null ? null : prev?.id === current.id ? { ...prev, ...current } : current,
    );
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
      // Sources are independent of the chat: a broken Cube must not hide the panel that
      // is how you fix it.
      try {
        const [meta] = await Promise.all([sourcesApi.drivers(), refreshSources()]);
        setDrivers(meta.drivers);
        setDriverMeta({ local_hosts: meta.local_hosts, localhost_note: meta.localhost_note });
      } catch {
        /* /api/system reports why; the panel says so itself. */
      }
    })();
  }, [open, refreshSources]);

  // Follow a running job, and re-attach to one already in flight after a refresh — the
  // server replays its buffer before going live, so both are the same code path.
  useEffect(() => {
    if (!job || job.done) return;
    return sourcesApi.watchJob((event) => {
      setJob((prev) => {
        if (!prev) return prev;
        // The buffer replay can repeat an event the snapshot already carried.
        if ((prev.events ?? []).some((seen) => seen.seq === event.seq)) return prev;
        const next = { ...prev, events: [...(prev.events ?? []), event] };
        if (event.type === "job_end") {
          next.done = true;
          next.error = String(event.data.error ?? "");
        }
        return next;
      });
      // `job_end` always follows the last `step_end`; refreshing on both ran every
      // docker call twice back to back.
      if (event.type === "job_end") void refreshSources();
    });
  }, [job?.id, job?.done, refreshSources]);

  const showSources = useCallback(
    (name?: string) => {
      window.location.hash = name ? `#sources/${encodeURIComponent(name)}` : "#sources";
      void refreshSources();
    },
    [refreshSources],
  );

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
    window.location.hash = "";
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
          Open the URL printed by <code>analyst</code>, token included.
        </p>
      </div>
    );
  }

  return (
    <div className="app">
      <Rail
        view={view}
        jobRunning={Boolean(job && !job.done)}
        onSelect={(next) => (next === "chat" ? (window.location.hash = "") : showSources())}
      />

      {view === "chat" ? (
        <ChatNav
          chats={chats}
          activeId={activeId}
          onSelect={(id) => void open(id)}
          onCreate={() => void create()}
          onRename={(id, title) => void rename(id, title)}
          onDelete={(id) => void remove(id)}
        />
      ) : (
        <DataNav
          sources={sources}
          selected={selected}
          job={job}
          canAdd={system.ok}
          onOpen={(name) => showSources(name)}
          onAll={() => showSources()}
          onAdd={() => setDrawer("new")}
        />
      )}

      {drawer && (
        <AddSource
          drivers={drivers}
          localHosts={driverMeta.local_hosts}
          localhostNote={driverMeta.localhost_note}
          nextPort={nextPort}
          taken={sources.map((s) => s.name)}
          editing={drawer === "new" ? undefined : drawer}
          onCancel={() => setDrawer(null)}
          onCreate={async (body) => {
            const created = await sourcesApi.create(body);
            setDrawer(null);
            // Onto its own page — connecting is about to narrate progress there.
            showSources(created.name);
            // Straight into connecting: adding a source nobody starts is a dead end.
            await sourcesApi.runAction(created.name, "connect");
            await refreshSources();
          }}
          onSave={async (name, body) => {
            await sourcesApi.edit(name, body);
            setDrawer(null);
            await refreshSources();
            // Credentials only take effect on a fresh container, so reconnect for them.
            await sourcesApi.runAction(name, "connect");
            await refreshSources();
          }}
        />
      )}

      {view === "sources" ? (
        <main className="main">
          {selected && sources.some((s) => s.name === selected) ? (
            <SourceDetail
              source={sources.find((s) => s.name === selected)!}
              job={job?.source === selected ? job : null}
              busy={Boolean(job && !job.done)}
              onRefresh={() => void refreshSources()}
              onEdit={() => setDrawer(sources.find((s) => s.name === selected)!)}
              onBack={() => showSources()}
            />
          ) : (
            <Sources
              system={system}
              sources={sources}
              job={job}
              nextPort={nextPort}
              onRefresh={() => void refreshSources()}
              onAdd={() => setDrawer("new")}
              onOpen={(name) => showSources(name)}
            />
          )}
        </main>
      ) : (
      <main className="main">
        <ChatHeader sources={sources} cubeUrl={cubeUrl} onShowSources={showSources} />

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
              case "result":
                return (
                  <div key={i} className="row">
                    <ResultBlock
                      resultId={item.resultId}
                      label={item.label}
                      columns={item.columns}
                      rows={item.rows}
                      rowCount={item.rowCount}
                      chart={item.chart}
                    />
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
      )}
    </div>
  );
}
