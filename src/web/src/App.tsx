import { useCallback, useEffect, useRef, useState } from "react";

import * as api from "./api";
import { AddSource } from "./components/AddSource";
import { ChatHeader } from "./components/ChatHeader";
import { ChatNav } from "./components/ChatNav";
import { Dashboard } from "./components/Dashboard";
import { FlowNav } from "./components/FlowNav";
import { FlowPage } from "./components/FlowPage";
import { DashboardNav } from "./components/DashboardNav";
import type { ComposerHandle } from "./components/Composer";
import { Button } from "./components/Button";
import { Composer } from "./components/Composer";
import { Rail } from "./components/Rail";
import type { Mode } from "./components/Rail";
import { McpPage } from "./components/McpPage";
import { Setup } from "./components/Setup";
import { SourceDetail } from "./components/SourceDetail";
import { Transcript } from "./components/Transcript";
import { Welcome } from "./components/Welcome";
import { applyEvent, buildTranscript } from "./events";
import { useHash } from "./hooks";
import * as sourcesApi from "./sources";
import type { JobState, SourceRow } from "./sources";
import type { Chat, DashboardRow, FlowSummary, Item } from "./types";

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
  // Whether the first load has answered. Without it `source` being null on the first paint
  // would flash the setup wizard over every configured install.
  const [booted, setBooted] = useState(false);

  // Which mode the rail is on. A hash rather than a router: four views do not justify
  // one, and the hash is the only state — a mirrored React copy let the two disagree.
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
  // Which dashboard is open, read off the hash for the same reason the mode is: a mirrored
  // React copy disagreed with the address bar after a back button.
  const openBoard = hash.startsWith("#boards/")
    ? decodeURIComponent(hash.slice("#boards/".length))
    : null;
  // Which flow is open, read off the hash for the reason the board is: a mirrored React copy
  // disagreed with the address bar after a back button.
  const openFlow = hash.startsWith("#flows/")
    ? decodeURIComponent(hash.slice("#flows/".length))
    : null;
  const [flows, setFlows] = useState<FlowSummary[]>([]);
  const [dashboards, setDashboards] = useState<DashboardRow[]>([]);
  const [source, setSource] = useState<SourceRow | null>(null);
  const [job, setJob] = useState<JobState | null>(null);
  // Whether the setup wizard is still building the model — reported by `BuildModel`, because the
  // gap between one stage finishing and the next starting is invisible from here.
  const [building, setBuilding] = useState(false);
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

  const refreshFlows = useCallback(async () => {
    try {
      setFlows(await api.listFlows());
    } catch {
      /* The page says why; an empty list is the honest fallback. */
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
    // Both concurrently: the connection body costs a health probe, and awaiting the job after
    // it serialised the slowest call for no reason.
    const [connected, current] = await Promise.all([
      sourcesApi.connection(),
      sourcesApi.job(),
    ]);
    setSource(connected);
    // The stream is the sole carrier of events; this only refreshes the header, so an
    // in-flight job keeps the events it has already received.
    setJob((prev) =>
      current.id === null
        ? null
        : prev?.id === current.id
          ? { ...prev, ...current }
          : current,
    );
    // Returned as well as set, so boot can route on it without asking a second time.
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
      // Not in the block above, and not fatal: it answers 503 until a deployment
      // is configured, which is the state every install starts in. Failing it here took the
      // whole portal down with the "cannot reach the server" page — including the data panel
      // that is the only way to configure one.
      try {
        const served = await api.modelSummary();
        setCubeUrl(served.cube_url ?? "");
        setEntities(served.entities ?? []);
      } catch {
        /* No model to suggest from. `Welcome` renders without suggestions. */
      }
      // The data page is independent of the chat: a broken Cube must not hide the panel
      // that is how you fix it.
      try {
        await refreshSource();
      } catch {
        /* The connection body carries the reason; the panel says so itself. */
      } finally {
        // In `finally`: a failure here must still let the app render. Otherwise a portal that
        // could not reach one of these calls would sit on a blank screen forever, since the
        // setup wizard is gated on this having happened.
        setBooted(true);
      }
    })();
  }, [open, refreshSource, refreshDashboards, refreshFlows]);

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

  // Setup owns the whole screen until it finishes, rail included: until then there is no model,
  // so chat cannot answer and a board cannot refresh, and offering three dead modes beside the
  // one live one is an invitation to click them.
  //
  // `booted` is what keeps this from flashing over an install that *is* configured — `source`
  // is null for the first paint of every session, and a wizard that appears for 200ms every
  // time you open the portal is worse than one that appears a moment late.
  // `building` is what keeps the wizard up through the whole pipeline. `onboarded` goes true the
  // moment cubes land, which is mid-sequence — and unmounting there would take the queue of
  // remaining stages with it, so descriptions and views would never run.
  if (booted && (!source?.onboarded || building)) {
    return (
      <Setup
        source={source}
        job={job}
        onJob={setJob}
        onBuilding={setBuilding}
        onChanged={() => {
          void refreshSource();
        }}
      />
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

      {drawer && (
        <AddSource
          editing={drawer === "edit" && source ? source : undefined}
          onCancel={() => setDrawer(null)}
          onConnect={async (body) => {
            await sourcesApi.connect(body);
            setDrawer(null);
            // Onto the data page, where the pipeline is. POST /api/onboarding/connection has already made
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
        // `source` is non-null here by construction: setup owns the whole screen until it is
        // finished, and finishing it requires a connection. There is no "nothing connected yet"
        // state on this page any more, which is what the wizard replaced.
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

          {/* Scrolling up to re-read something stops the transcript following the stream, and
            without this nothing says so — an answer arriving off-screen looks like no answer. */}
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
    </div>
  );
}
