import { useCallback, useEffect, useRef, useState } from "react";

import * as api from "./api";
import { AddSource } from "./components/AddSource";
import { Answer } from "./components/Answer";
import { ChatHeader } from "./components/ChatHeader";
import { ChatNav } from "./components/ChatNav";
import { Dashboard } from "./components/Dashboard";
import { DashboardNav } from "./components/DashboardNav";
import type { ComposerHandle } from "./components/Composer";
import { Composer } from "./components/Composer";
import { Connect } from "./components/Connect";
import { FileBlock } from "./components/FileBlock";
import { Rail } from "./components/Rail";
import type { Mode } from "./components/Rail";
import { McpPage } from "./components/McpPage";
import { ResultBlock } from "./components/ResultBlock";
import { SourceDetail } from "./components/SourceDetail";
import { Thinking } from "./components/Thinking";
import { ToolCall } from "./components/ToolCall";
import { Welcome } from "./components/Welcome";
import { applyEvent, buildTranscript } from "./events";
import { useHash } from "./hooks";
import * as sourcesApi from "./sources";
import type { DriverSpec, JobState, SourceRow, System } from "./sources";
import type { Chat, DashboardRow, Item } from "./types";

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
  const [entities, setEntities] = useState<
    { name: string; view: boolean; members: number }[]
  >([]);
  const [fatal, setFatal] = useState<string | null>(null);

  // Which mode the rail is on. A hash rather than a router: four views do not justify
  // one, and the hash is the only state — a mirrored React copy let the two disagree.
  const hash = useHash();
  const view: Mode = hash.startsWith("#data")
    ? "data"
    : hash.startsWith("#agents")
      ? "agents"
      : hash.startsWith("#boards")
        ? "dashboards"
        : "chat";
  // Which dashboard is open, read off the hash for the same reason the mode is: a mirrored
  // React copy disagreed with the address bar after a back button.
  const openBoard = hash.startsWith("#boards/")
    ? decodeURIComponent(hash.slice("#boards/".length))
    : null;
  const [dashboards, setDashboards] = useState<DashboardRow[]>([]);
  const [system, setSystem] = useState<System>({
    sandbox_reason: "",
    store_url: "",
    sandbox_url: "",
    semantic_dir: "",
  });
  const [source, setSource] = useState<SourceRow | null>(null);
  const [drivers, setDrivers] = useState<DriverSpec[]>([]);
  const [driverMeta, setDriverMeta] = useState({
    local_hosts: [] as string[],
    localhost_note: "",
  });
  const [job, setJob] = useState<JobState | null>(null);
  // The connection drawer: "new" to connect one, "edit" to change its credentials.
  const [drawer, setDrawer] = useState<"new" | "edit" | null>(null);

  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const composer = useRef<ComposerHandle>(null);
  // Stop following the stream once the reader scrolls up to re-read something. A ref, because
  // the scroll effect reads it on every token and state there would re-render the transcript
  // on every scroll event; `tailing` mirrors it for the one thing that has to be drawn.
  const pinned = useRef(true);
  const [tailing, setTailing] = useState(true);

  const follow = useCallback(() => {
    pinned.current = true;
    setTailing(true);
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  const open = useCallback(async (id: string) => {
    abort.current?.abort();
    setActiveId(id);
    setBusy(false);
    pinned.current = true;
    setTailing(true);
    setItems(buildTranscript(await api.chatEvents(id)));
  }, []);

  const refreshDashboards = useCallback(async () => {
    try {
      setDashboards(await api.listDashboards());
    } catch {
      /* The page itself reports why; an empty list is the honest fallback. */
    }
  }, []);

  const showBoards = useCallback(
    (id?: string) => {
      window.location.hash = id
        ? `#boards/${encodeURIComponent(id)}`
        : "#boards";
      void refreshDashboards();
    },
    [refreshDashboards],
  );

  const refreshSource = useCallback(async () => {
    // All three concurrently: /api/system spawns docker, and awaiting it after the
    // others serialised the slowest call for no reason.
    const [connected, current, health] = await Promise.all([
      sourcesApi.source(),
      sourcesApi.job(),
      sourcesApi.system(),
    ]);
    setSource(connected);
    setSystem(health);
    // The stream is the sole carrier of events; this only refreshes the header, so an
    // in-flight job keeps the events it has already received.
    setJob((prev) =>
      current.id === null
        ? null
        : prev?.id === current.id
          ? { ...prev, ...current }
          : current,
    );
  }, []);

  useEffect(() => {
    (async () => {
      try {
        await api.claimToken();
        const [loaded, catalog] = await Promise.all([
          api.listChats(),
          api.catalog(),
        ]);
        setChats(loaded);
        setCubeUrl(catalog.cube_url ?? "");
        setEntities(catalog.entities ?? []);
        if (loaded.length) await open(loaded[0].id);
        await refreshDashboards();
      } catch (err) {
        setFatal(String(err));
      }
      // The data page is independent of the chat: a broken Cube must not hide the panel
      // that is how you fix it.
      try {
        const [meta] = await Promise.all([
          sourcesApi.drivers(),
          refreshSource(),
        ]);
        setDrivers(meta.drivers);
        setDriverMeta({
          local_hosts: meta.local_hosts,
          localhost_note: meta.localhost_note,
        });
      } catch {
        /* /api/system reports why; the panel says so itself. */
      }
    })();
  }, [open, refreshSource, refreshDashboards]);

  // Follow a running job, and re-attach to one already in flight after a refresh — the
  // server replays its buffer before going live, so both are the same code path.
  useEffect(() => {
    if (!job || job.done) return;
    return sourcesApi.watchJob((event) => {
      setJob((prev) => {
        if (!prev) return prev;
        // The buffer replay can repeat an event the snapshot already carried.
        if ((prev.events ?? []).some((seen) => seen.seq === event.seq))
          return prev;
        const next = { ...prev, events: [...(prev.events ?? []), event] };
        if (event.type === "job_end") {
          next.done = true;
          next.error = String(event.data.error ?? "");
        }
        return next;
      });
      // `job_end` always follows the last `step_end`; refreshing on both ran every
      // docker call twice back to back.
      if (event.type === "job_end") void refreshSource();
    });
  }, [job?.id, job?.done, refreshSource]);

  const showData = useCallback(() => {
    window.location.hash = "#data";
    void refreshSource();
  }, [refreshSource]);

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

  const send = async (question: string, files: string[]) => {
    if (!activeId) return;
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setTurnStart(Date.now());
    follow();

    try {
      for await (const event of api.sendMessage(
        activeId,
        question,
        files,
        controller.signal,
      )) {
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

  // Data and agents have no list beside their page — one connection, one endpoint — so the
  // middle column is absent there rather than empty.
  return (
    <div
      className={`app ${view === "data" || view === "agents" ? "no-nav" : ""}`}
    >
      <Rail
        view={view}
        jobRunning={Boolean(job && !job.done)}
        onSelect={(next) => {
          if (next === "chat") window.location.hash = "";
          else if (next === "dashboards") showBoards();
          else if (next === "agents") window.location.hash = "#agents";
          else showData();
        }}
      />

      {view === "dashboards" ? (
        <DashboardNav
          dashboards={dashboards}
          selected={openBoard}
          onOpen={(id) => showBoards(id)}
          onCreate={async () => {
            try {
              const made = await api.createDashboard("New dashboard");
              await refreshDashboards();
              showBoards(made.id);
            } catch (err) {
              window.alert(String(err));
            }
          }}
        />
      ) : view === "chat" ? (
        <ChatNav
          chats={chats}
          activeId={activeId}
          onSelect={(id) => void open(id)}
          onCreate={() => void create()}
          onRename={(id, title) => void rename(id, title)}
          onDelete={(id) => void remove(id)}
        />
      ) : null}

      {drawer && (
        <AddSource
          drivers={drivers}
          localHosts={driverMeta.local_hosts}
          localhostNote={driverMeta.localhost_note}
          editing={drawer === "edit" && source ? source : undefined}
          onCancel={() => setDrawer(null)}
          onConnect={async (body) => {
            await sourcesApi.connect(body);
            setDrawer(null);
            // Onto the data page, where the pipeline is. POST /api/source has already made
            // the connection usable — there is no Connect action to fire after it.
            showData();
            await refreshSource();
          }}
          onSave={async (body) => {
            await sourcesApi.edit(body);
            setDrawer(null);
            await refreshSource();
          }}
        />
      )}

      {view === "dashboards" ? (
        <main className="main">
          {openBoard ? (
            <Dashboard
              id={openBoard}
              onRenamed={() => void refreshDashboards()}
              onDeleted={() => {
                void refreshDashboards();
                showBoards();
              }}
            />
          ) : (
            <div className="dash-fail muted">
              {dashboards.length
                ? "Pick a dashboard on the left."
                : "No dashboards yet. Create one, then ask in chat to add tiles to it."}
            </div>
          )}
        </main>
      ) : view === "agents" ? (
        <main className="main">
          <McpPage />
        </main>
      ) : view === "data" ? (
        <main className="main">
          {source ? (
            <SourceDetail
              source={source}
              system={system}
              job={job}
              busy={Boolean(job && !job.done)}
              onRefresh={() => void refreshSource()}
              onEdit={() => setDrawer("edit")}
            />
          ) : (
            <Connect system={system} onAdd={() => setDrawer("new")} />
          )}
        </main>
      ) : (
        <main className="main chat-main">
          <ChatHeader source={source} cubeUrl={cubeUrl} onShowData={showData} />

          <div
            className="transcript"
            ref={scroller}
            onScroll={(e) => {
              const el = e.currentTarget;
              const near =
                el.scrollHeight - el.scrollTop - el.clientHeight < 80;
              if (near !== pinned.current) {
                pinned.current = near;
                setTailing(near);
              }
            }}
          >
            {items.length === 0 && (
              <Welcome
                entities={entities}
                onPick={(q) => {
                  if (!activeId)
                    void create().then(() => composer.current?.fill(q));
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
                      {item.files.length > 0 && (
                        <div className="attached sent">
                          {item.files.map((path) => (
                            <a
                              key={path}
                              className="attach"
                              href={
                                activeId
                                  ? api.chatFileUrl(activeId, path)
                                  : undefined
                              }
                              download
                            >
                              <span className="attach-name">
                                {path.replace(/^uploads\//, "")}
                              </span>
                            </a>
                          ))}
                        </div>
                      )}
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
                        chatId={activeId ?? ""}
                        tool={item.tool}
                        label={item.label}
                        query={item.query}
                        script={item.script}
                        awaiting={item.awaiting}
                        end={item.end}
                        startedAt={item.startedAt}
                        ms={item.ms}
                        live={item.live}
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
                case "files":
                  return (
                    <div key={i} className="row">
                      <FileBlock
                        chatId={activeId ?? ""}
                        label={item.label}
                        paths={item.paths}
                      />
                    </div>
                  );
                case "compacted":
                  return (
                    <div key={i} className="row compacted">
                      <span>
                        Earlier messages were summarized to fit the context —{" "}
                        {item.before} became {item.after}
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

          {/* Scrolling up to re-read something stops the transcript following the stream, and
            without this nothing says so — an answer arriving off-screen looks like no answer. */}
          {!tailing && items.length > 0 && (
            <button className="jump" onClick={follow}>
              ↓ {busy ? "Jump to what it is writing" : "Jump to the end"}
            </button>
          )}

          <Composer
            ref={composer}
            chatId={activeId}
            busy={busy}
            disabled={!activeId}
            startedAt={turnStart}
            status={statusOf(items, busy)}
            onSend={(q, files) => void send(q, files)}
            onStop={() => void stop()}
          />
        </main>
      )}
    </div>
  );
}
