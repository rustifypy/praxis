import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";

import * as api from "./api";
import { Button } from "./components/Button";
import { ChatHeader } from "./components/ChatHeader";
import { ChatNav } from "./components/ChatNav";
import { Composer } from "./components/Composer";
import type { ComposerHandle } from "./components/Composer";
import { DashboardNav } from "./components/DashboardNav";
import { FlowNav } from "./components/FlowNav";
import { Rail } from "./components/Rail";
import type { Mode } from "./components/Rail";
import { Transcript } from "./components/Transcript";
import { Welcome } from "./components/Welcome";
import { applyEvent, buildTranscript } from "./events";
import { useHash } from "./hooks";
import * as sourcesApi from "./sources";
import type { JobState, SourceRow } from "./sources";
import type { Chat, DashboardRow, FlowSummary, Item } from "./types";

// Chat is the landing page and ships in the entry chunk; every other page loads on first visit.
const AddSource = lazy(() =>
  import("./components/AddSource").then((m) => ({ default: m.AddSource })),
);
const Dashboard = lazy(() =>
  import("./components/Dashboard").then((m) => ({ default: m.Dashboard })),
);
const FlowPage = lazy(() =>
  import("./components/FlowPage").then((m) => ({ default: m.FlowPage })),
);
const McpPage = lazy(() => import("./components/McpPage").then((m) => ({ default: m.McpPage })));
const Setup = lazy(() => import("./components/Setup").then((m) => ({ default: m.Setup })));
const SourceDetail = lazy(() =>
  import("./components/SourceDetail").then((m) => ({ default: m.SourceDetail })),
);

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
  // Without it, `source` being null on first paint flashes the setup wizard.
  const [booted, setBooted] = useState(false);

  // The hash is the only state: a mirrored React copy disagrees with it after a back button.
  const hash = useHash();
  const view: Mode = hash.startsWith("#data")
    ? "data"
    : hash.startsWith("#agents")
      ? "agents"
      : hash.startsWith("#flows")
        ? "flows"
        : hash.startsWith("#boards")
          ? "dashboards"
          : "chat";
  const openBoard = hash.startsWith("#boards/")
    ? decodeURIComponent(hash.slice("#boards/".length))
    : null;
  const openFlow = hash.startsWith("#flows/")
    ? decodeURIComponent(hash.slice("#flows/".length))
    : null;
  const [flows, setFlows] = useState<FlowSummary[]>([]);
  const [dashboards, setDashboards] = useState<DashboardRow[]>([]);
  const [source, setSource] = useState<SourceRow | null>(null);
  const [job, setJob] = useState<JobState | null>(null);
  // Reported by `BuildModel`: the gap between stages is invisible from here.
  const [building, setBuilding] = useState(false);
  const [drawer, setDrawer] = useState<"new" | "edit" | null>(null);

  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const composer = useRef<ComposerHandle>(null);
  // A ref so scrolling doesn't re-render the transcript; `tailing` mirrors it for drawing.
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
      /* The page reports why. */
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

  const refreshFlows = useCallback(async () => {
    try {
      setFlows(await api.listFlows());
    } catch {
      /* The page reports why. */
    }
  }, []);

  const showFlows = useCallback(
    (name?: string) => {
      window.location.hash = name ? `#flows/${encodeURIComponent(name)}` : "#flows";
      void refreshFlows();
    },
    [refreshFlows],
  );

  const refreshSource = useCallback(async () => {
    const [connected, current] = await Promise.all([
      sourcesApi.connection(),
      sourcesApi.job(),
    ]);
    setSource(connected);
    // The stream carries events; this only refreshes the header, keeping events received.
    setJob((prev) =>
      current.id === null
        ? null
        : prev?.id === current.id
          ? { ...prev, ...current }
          : current,
    );
    return connected;
  }, []);

  useEffect(() => {
    (async () => {
      try {
        await api.claimToken();
        const loaded = await api.listChats();
        setChats(loaded);
        if (loaded.length) await open(loaded[0].id);
        await refreshDashboards();
        await refreshFlows();
      } catch (err) {
        setFatal(String(err));
      }
      // Not fatal: it answers 503 until a deployment is configured.
      try {
        const served = await api.modelSummary();
        setCubeUrl(served.cube_url ?? "");
        setEntities(served.entities ?? []);
      } catch {
        /* `Welcome` renders without suggestions. */
      }
      try {
        await refreshSource();
      } catch {
        /* The panel reports why. */
      } finally {
        // Even on failure, or the app sits on a blank screen.
        setBooted(true);
      }
    })();
  }, [open, refreshSource, refreshDashboards, refreshFlows]);

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
      if (event.type === "job_end") void refreshSource();
    });
  }, [job?.id, job?.done, refreshSource]);

  const showData = useCallback(() => {
    window.location.hash = "#data";
    void refreshSource();
  }, [refreshSource]);

  // Not scrollIntoView: a smooth scroll retriggered on every token judders.
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

  // Setup owns the whole screen, rail included, since no other mode works without a model.
  // `building`: `onboarded` turns true mid-pipeline, and unmounting would drop the stage queue.
  if (booted && (!source?.onboarded || building)) {
    return (
      <Suspense fallback={null}>
        <Setup
          source={source}
          job={job}
          onJob={setJob}
          onBuilding={setBuilding}
          onChanged={() => {
            void refreshSource();
          }}
        />
      </Suspense>
    );
  }

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
          else if (next === "flows") showFlows();
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
      ) : view === "flows" ? (
        <FlowNav
          flows={flows}
          selected={openFlow}
          onOpen={(name) => showFlows(name)}
          onCreate={async (name, title) => {
            await api.createFlow(name, title);
            await refreshFlows();
            showFlows(name);
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

      <Suspense fallback={null}>
        {drawer && (
          <AddSource
            editing={drawer === "edit" && source ? source : undefined}
            onCancel={() => setDrawer(null)}
            onConnect={async (body) => {
              await sourcesApi.connect(body);
              setDrawer(null);
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
      </Suspense>

      <Suspense fallback={<main className="main" />}>
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
        ) : view === "flows" ? (
          <main className="main">
            {openFlow ? (
              <FlowPage key={openFlow} flow={openFlow} />
            ) : (
              <div className="dash-fail muted">
                {flows.length
                  ? "Pick a flow on the left."
                  : "No flows yet. A flow is a folder of numbered steps you run again and again, reviewing each one."}
              </div>
            )}
          </main>
        ) : view === "agents" ? (
          <main className="main">
            <McpPage />
          </main>
        ) : view === "data" ? (
          // `source` is non-null here: setup owns the screen until a connection exists.
          <main className="main">
            {source && (
              <SourceDetail
                source={source}
                job={job}
                onJob={setJob}
                onRefresh={() => void refreshSource()}
                onEdit={() => setDrawer("edit")}
              />
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

              <Transcript items={items} chatId={activeId ?? ""} />
            </div>

            {!tailing && items.length > 0 && (
              <Button className="jump" onClick={follow}>
                ↓ {busy ? "Jump to what it is writing" : "Jump to the end"}
              </Button>
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
      </Suspense>
    </div>
  );
}
