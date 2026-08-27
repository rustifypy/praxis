import { Layout, Tab, Tabs } from "@cube-dev/ui-kit";
import { Suspense, lazy, useCallback, useEffect, useState } from "react";

import * as api from "../sources";
import type { CheckResult, ModelFiles, RefDiff, Refs, ServedModel } from "../sources";
import { Button } from "./Button";
import { BranchPicker } from "./BranchPicker";
import { Changes } from "./Changes";
import { Confirm } from "./Confirm";
import { EditorChat } from "./EditorChat";
import { FileTree } from "./FileTree";
import { Info } from "./Info";
import { MergeDialog } from "./MergeDialog";
import { Problems } from "./Problems";
import { ServedModel as ModelList } from "./ServedModel";

/**
 * CodeMirror, loaded when a file is first opened rather than with the app.
 *
 * It is a third of the bundle and the only thing that imports `@codemirror/*`, so a static import
 * put all of it in front of the chat page — which never opens a file. The `.then` unwrap is
 * because `lazy` wants a default export and the component is named.
 */
const CodeEditor = lazy(() =>
  import("./CodeEditor").then((module) => ({ default: module.CodeEditor })),
);

/**
 * One open file: what the draft holds on the server, and what has been typed since.
 *
 * `saved` is the draft's text — "" for a file that does not exist on the branch yet. `text` is
 * the editor's, or null for a file about to be deleted. They differ only while something is
 * unsaved; Save writes `text` through and they converge again.
 */
interface Buffered {
  saved: string;
  text: string | null;
}

const typed = (entry: Buffered) => entry.text !== entry.saved;

const NEW_VIEW = `views:
- name: new_view
  cubes:
  - join_path:
    includes: []
`;

/**
 * The model: what Cube serves for a branch, and — on any branch but main — how to change it.
 *
 * **The branch's draft lives on the server, and this holds only what has been typed since.** So
 * there are three steps and not two: Save writes the open file into the draft and touches no
 * deployment; Validate compiles the whole draft on a throwaway ref; Publish lands it as one
 * commit. A reload now loses only what was being typed at the time.
 *
 * **The whole draft is one unit from Validate onwards.** A change that spans two files — a new
 * cube and the view that includes it — cannot compile a file at a time, so one verdict covers
 * all of it and one commit lands all of it.
 *
 * Main is never editable: it is what the pipeline generates, and a rebuild rewrites every file in
 * it, so an edit there is work waiting to be discarded. The server refuses it too — `workspace`
 * is the one rule — and this only decides what to offer.
 */
export function ModelEditor({
  deployed,
  onChanged,
}: {
  /** Whether a semantic deployment is configured at all. Without one there is nothing to read. */
  deployed: boolean;
  /** The page's header shows the model's size, which a commit changes. */
  onChanged: () => void;
}) {
  const [refs, setRefs] = useState<Refs | null>(null);
  const [on, setOn] = useState("main");
  const [tab, setTab] = useState<"files" | "model">("files");

  const [listing, setListing] = useState<ModelFiles | null>(null);
  const [buffer, setBuffer] = useState<Record<string, Buffered>>({});
  const [openPath, setOpenPath] = useState("");
  // Which files have a tab, left to right. `buffer` already holds every file that has been
  // touched — including ones closed and reopened — so it cannot double as this: a tab strip is
  // an ordering someone arranged, and dropping a tab must not drop what was typed into it.
  const [order, setOrder] = useState<string[]>([]);
  // The committed text a proposal is being diffed against, which is what puts the editor in
  // review mode. Null is plain editing.
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState<Set<string>>(new Set());

  const [model, setModel] = useState<ServedModel | null>(null);
  const [loading, setLoading] = useState(false);
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [note, setNote] = useState<{ tone: "ok" | "bad"; lines: string[] } | null>(null);
  const [busy, setBusy] = useState("");
  const [naming, setNaming] = useState(false);
  const [newName, setNewName] = useState("");
  // Which ref a new branch starts from. Its own state and not just `on`, because the picker can
  // be reopened and the base changed without leaving the branch being shown.
  const [newFrom, setNewFrom] = useState("main");
  const [merging, setMerging] = useState(false);
  const [diff, setDiff] = useState<RefDiff | null>(null);
  // Which branch a delete is being confirmed for, or "" for none. The name and not a boolean,
  // because the row that asked is not necessarily the branch being shown.
  const [deleting, setDeleting] = useState("");
  // Held here rather than left to the panel, so the bar can offer the toggle — a pane you can
  // only close by dragging it to nothing is one nobody closes.
  const [chatOpen, setChatOpen] = useState(true);

  const main = refs?.main ?? "main";
  const editable = on !== main;
  const files = listing?.files ?? [];
  // Typed and not written through, against changed in the draft and not published. The first is
  // this component's to know; the second is the server's, and is read rather than derived.
  const unsaved = new Set(
    Object.entries(buffer)
      .filter(([, entry]) => typed(entry))
      .map(([path]) => path),
  );
  const dirty = new Set([...(listing?.dirty ?? []), ...(listing?.deleted ?? [])]);
  // How far along the publish path this branch is. Derived and never stored: every input is
  // already state, and a fourth copy of "what can I do next" is a fourth thing to keep in step.
  const stage =
    unsaved.size > 0 ? "save" : dirty.size === 0 ? "none" : check?.ok ? "publish" : "validate";

  /** Every action reports the same way, so the panel has one place errors can appear. */
  const guard = async (label: string, work: () => Promise<void>) => {
    setBusy(label);
    setNote(null);
    try {
      await work();
    } catch (e) {
      setNote({ tone: "bad", lines: (e as Error).message.split("\n") });
    } finally {
      setBusy("");
    }
  };

  const loadRefs = useCallback(async () => setRefs(await api.refs()), []);

  /**
   * Read a branch's files and its compiled model.
   *
   * A failure here is reported and the branch list re-read, because the likeliest cause is a
   * branch that is no longer there — deleted from another tab, or by whoever else is on this
   * install. Swallowing it left the picker offering a name the store had never heard of and the
   * editor blank with nothing saying why, which reads as "only main can be opened".
   */
  const loadBranch = useCallback(
    async (ref: string) => {
      setLoading(true);
      try {
        setListing(await api.modelFiles(ref));
      } catch (e) {
        setListing(null);
        setNote({ tone: "bad", lines: [(e as Error).message] });
        await loadRefs().catch(() => {});
      }
      // The compiled model for *this* branch, not for main: it is how you check that the view you
      // just saved exposes the members you meant, without merging first.
      try {
        setModel(await api.model(ref));
      } catch (err) {
        setModel({ serving: false, error: String(err), entities: [], unloaded: [] });
      }
      setLoading(false);
    },
    [loadRefs],
  );

  useEffect(() => {
    void loadRefs().catch((e: Error) => setNote({ tone: "bad", lines: [e.message] }));
  }, [loadRefs]);

  useEffect(() => {
    if (deployed) void loadBranch(on);
  }, [deployed, on, loadBranch]);

  /** Read a file into the buffer if it is not there, and return what the draft holds. */
  const load = async (path: string): Promise<string> => {
    if (buffer[path]) return buffer[path].saved;
    const body = await api.modelFile(path, on);
    setBuffer((prev) => ({ ...prev, [path]: { saved: body.text, text: body.text } }));
    return body.text;
  };

  /** Give a path a tab if it has not got one, and make it the active one. */
  const focus = (path: string) => {
    setOrder((prev) => (prev.includes(path) ? prev : [...prev, path]));
    setOpenPath(path);
  };

  const open = (path: string) =>
    void guard("open", async () => {
      await load(path);
      focus(path);
      setReviewing(null);
    });

  /**
   * Drop a tab, and with it everything the browser was holding for that file.
   *
   * The draft on the server is untouched — closing a tab is not discarding a change — so what is
   * actually at risk is only what has been typed since the last Save, which is exactly what the
   * question asks about. Reopening re-reads the draft.
   *
   * The neighbour on the right inherits focus, falling back to the left at the end of the strip:
   * closing the last of several tabs should not empty the editor.
   */
  const close = (path: string) => {
    if (buffer[path] && typed(buffer[path])) {
      const what = buffer[path].text === null ? "the pending deletion of" : "unsaved edits to";
      if (!window.confirm(`Discard ${what} ${path}?`)) return;
    }
    const at = order.indexOf(path);
    const rest = order.filter((p) => p !== path);
    setOrder(rest);
    setBuffer((prev) => {
      const next = { ...prev };
      delete next[path];
      return next;
    });
    if (openPath !== path) return;
    setOpenPath(order[at + 1] ?? order[at - 1] ?? "");
    setReviewing(null);
  };

  /**
   * Take a proposal from the chat into the buffer, and show it as a diff.
   *
   * The file is opened *first* and the draft's text becomes what it is diffed against, so a
   * proposal never lands on a file that is not on screen — and the diff is always against
   * something definite, whether or not the file had been opened before.
   */
  const review = (path: string, text: string | null) =>
    void guard("open", async () => {
      const base = await load(path);
      setBuffer((prev) => ({
        ...prev,
        [path]: { saved: prev[path]?.saved ?? base, text },
      }));
      focus(path);
      // A proposed deletion has no diff to show — accepting it is the click on the card, and the
      // pane says what will happen instead.
      setReviewing(text === null ? null : base);
      setReviewed((prev) => new Set(prev).add(path));
      setTab("files");
    });

  const edit = (text: string) =>
    setBuffer((prev) => ({ ...prev, [openPath]: { ...prev[openPath], text } }));

  /** Throw away what was typed. Still on the draft — `discard` is what undoes that. */
  const revert = (path: string) =>
    setBuffer((prev) => ({ ...prev, [path]: { ...prev[path], text: prev[path].saved } }));

  /** Undo the draft's change to one file, back to what the store handed over. */
  const discard = (path: string) =>
    void guard("discard", async () => {
      setListing(await api.discardDraft(on, path));
      const body = await api.modelFile(path, on).catch(() => null);
      setBuffer((prev) => {
        const next = { ...prev };
        if (body) next[path] = { saved: body.text, text: body.text };
        else delete next[path];
        return next;
      });
      // A file the draft only ever added is gone once the draft's change to it is undone, so its
      // tab goes too — there is nothing left for it to show.
      if (!body) {
        setOrder((prev) => prev.filter((p) => p !== path));
        if (openPath === path) setOpenPath("");
      }
      setCheck(null);
    });

  const create = () => {
    // A name nothing has taken, so creating two in a row does not silently reopen the first.
    let path = "views/new_view.yml";
    for (let n = 2; files.includes(path) || buffer[path]; n += 1) {
      path = `views/new_view-${n}.yml`;
    }
    // `saved: ""` means the file is not in the draft yet, so it reads as unsaved at once.
    setBuffer((prev) => ({ ...prev, [path]: { saved: "", text: NEW_VIEW } }));
    focus(path);
    setReviewing(null);
    setTab("files");
  };

  /** Save: write everything typed into the branch's draft. Compiles nothing, deploys nothing. */
  const save = () =>
    void guard("save", async () => {
      for (const path of unsaved) {
        const entry = buffer[path];
        if (entry.text === null) await api.deleteFile(path, on);
        else await api.saveFile(path, on, entry.text);
      }
      setBuffer((prev) => {
        const next = { ...prev };
        for (const path of unsaved) {
          const entry = next[path];
          if (entry.text === null) delete next[path];
          else next[path] = { saved: entry.text, text: entry.text };
        }
        return next;
      });
      // A save invalidates the last verdict: it was about a draft that has since moved.
      setCheck(null);
      setListing(await api.modelFiles(on));
      // A saved deletion leaves nothing to show, so those tabs close with it.
      const gone = [...unsaved].filter((path) => buffer[path].text === null);
      if (gone.length) {
        setOrder((prev) => prev.filter((path) => !gone.includes(path)));
        if (gone.includes(openPath)) setOpenPath("");
      }
      setNote({
        tone: "ok",
        lines: [`Saved ${unsaved.size} file${unsaved.size === 1 ? "" : "s"}.`],
      });
    });

  /** Validate: would Cube accept the draft as it stands? Nothing is published. */
  const validate = () =>
    void guard("validate", async () => {
      const result = await api.checkChanges(on);
      setCheck(result);
      // The failure is not restated here: `Problems` renders the same `check` under the editor,
      // and a list of compile errors in two places is two places to keep in step.
      if (!result.ok) return;
      setNote({ tone: "ok", lines: ["Cube compiled it. Ready to publish.", ...result.warnings] });
    });

  /** Publish: land the draft on the branch as one commit. This is the deploy. */
  const publish = () =>
    void guard("publish", async () => {
      const touched = dirty.size;
      const result = await api.commitChanges({
        ref: on,
        message: touched === 1 ? `edit ${[...dirty][0]}` : `edit ${touched} model files`,
      });
      setCheck(null);
      setNote({
        tone: "ok",
        lines: [`Published to ${on} — Cube is serving it within seconds.`, ...result.warnings],
      });
      await loadRefs();
      await loadBranch(on);
      onChanged();
    });

  /** Show a different branch, dropping everything the browser held for the last one. */
  const moveTo = (ref: string) => {
    setOn(ref);
    setBuffer({});
    setOrder([]);
    setOpenPath("");
    setReviewing(null);
    setReviewed(new Set());
    setCheck(null);
    setNote(null);
  };

  const switchTo = (ref: string) => {
    // Only what is typed is at risk — the draft is on the server and will still be there — but
    // losing it because someone changed a dropdown is still worth one question. Separate from
    // `moveTo` because leaving a branch that no longer exists has nothing to ask about.
    if (unsaved.size && !window.confirm(`Discard ${unsaved.size} unsaved edit(s)?`)) return;
    moveTo(ref);
  };

  /**
   * Drop a branch: the ref at the store and the working copy of it, in one call.
   *
   * Anything committed to it and never merged goes with it, which is why this is behind a typed
   * confirmation rather than a click. Moving to main is unconditional when the branch being
   * deleted is the one on screen — `switchTo` would ask about unsaved edits belonging to a
   * branch that no longer exists.
   */
  const remove = (ref: string) =>
    void guard("delete-branch", async () => {
      setRefs(await api.deleteRef(ref));
      if (ref === on) moveTo(main);
      setNote({ tone: "ok", lines: [`Branch ${ref} deleted.`] });
    });

  const current = openPath ? buffer[openPath] : undefined;

  return (
    <div className="editor">
      {/* The branch bar: what you are looking at on the left, what you can do to it on the
          right. It is the top of the model section rather than of the page, because the header
          above it is the *connection* and these two are different questions. */}
      <div className="editor-bar">
        <BranchPicker
          refs={refs}
          on={on}
          main={main}
          busy={Boolean(busy)}
          onSwitch={switchTo}
          onCreate={() => {
            // Offer the branch being shown as the base, because "new branch" from somewhere is
            // usually meant to build on it — but main stays the default from main.
            setNewFrom(on);
            setNaming(true);
          }}
          onDelete={setDeleting}
        />

        {naming && (
          <form
            className="editor-name"
            onSubmit={(e) => {
              e.preventDefault();
              void guard("branch", async () => {
                const name = newName.trim();
                setRefs(await api.createRef(name, newFrom));
                setNaming(false);
                setNewName("");
                switchTo(name);
              });
            }}
          >
            <input
              autoFocus
              value={newName}
              placeholder="add-margin"
              onChange={(e) => setNewName(e.target.value)}
            />
            {/* What it branches from. A `<select>` and not a second picker: it is one field of a
                form being filled in, and it is only ever on screen while that form is. */}
            <label className="editor-from">
              from
              <select value={newFrom} onChange={(e) => setNewFrom(e.target.value)}>
                {(refs?.refs ?? [{ name: main, changed: 0 }]).map((r) => (
                  <option key={r.name} value={r.name}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
            <Button type="submit" variant="primary" disabled={!newName.trim()}>
              Create
            </Button>
            <Button type="button" variant="subtle" onClick={() => setNaming(false)}>
              Cancel
            </Button>
            {/* Only when it matters: a branch can only ever merge into main, so one started from
                another branch brings that branch's commits with it when it lands. */}
            {newFrom !== main && (
              <small className="editor-from-note">
                carries {newFrom}'s commits into {main} when it merges
              </small>
            )}
          </form>
        )}

        {/* Moved off the page's "Model" heading, which went with it: the bar is what this screen
            has instead of a section title. Beside the branch rather than after the spacer — on
            main every action to its right is hidden, which left it stranded against the far edge
            of the window with nothing to belong to. */}
        <Info label="About the model">
          Everything the agent brings to a question, and where you change it. The file tree is what
          the model store holds for the branch you are on; the Served model tab is read live from
          Cube's <code>/v1/meta</code> and parsed by the same code the agent queries through, so it
          is what a question can actually reach rather than what the YAML says. Main is generated
          and read-only — edits go on a branch, and Cube serves every branch at once, so a commit is
          live on that branch immediately and main is untouched until you merge.
        </Info>

        {/* Out of the slot above the editor, which now belongs to the open files. It is not a
            document but a choice of what this whole screen is showing, and the bar's rule is
            already that what you are *looking at* lives on the left with the branch and what you
            can *do* lives on the right. */}
        <div className="segmented" role="group" aria-label="What to show">
          <button className={tab === "files" ? "on" : ""} onClick={() => setTab("files")}>
            Files
          </button>
          <button className={tab === "model" ? "on" : ""} onClick={() => setTab("model")}>
            Served model
          </button>
        </div>

        <span className="editor-spacer" />

        {/* Three steps in the order they happen — write it down, ask Cube, land it — with the one
            you are up to live and the others ahead of it dimmed.

            This was one slot that changed its label, on the argument that three buttons would
            leave two permanently dead. The cost was that the path was invisible: Validate did not
            exist until you had saved, so there was no way to learn the shape of the thing from
            looking at it. Dimmed *steps* read as "not yet" where dead *buttons* read as broken —
            and the whole group is hidden when there is nothing to publish, which is what the
            original objection was actually about. */}
        {editable && stage !== "none" && (
          <div className="segmented pipeline" role="group" aria-label="Publishing a change">
            <button
              className={stage === "save" ? "on" : undefined}
              disabled={stage !== "save" || Boolean(busy)}
              onClick={save}
            >
              {busy === "save" ? "Saving…" : `Save${unsaved.size ? ` ${unsaved.size}` : ""}`}
            </button>
            <span aria-hidden>›</span>
            <button
              className={stage === "validate" ? "on" : undefined}
              disabled={stage !== "validate" || Boolean(busy)}
              onClick={validate}
            >
              {busy === "validate" ? "Compiling…" : "Validate"}
            </button>
            <span aria-hidden>›</span>
            <button
              className={stage === "publish" ? "on" : undefined}
              disabled={stage !== "publish" || Boolean(busy)}
              onClick={publish}
            >
              {busy === "publish" ? "Publishing…" : `Publish${dirty.size ? ` ${dirty.size}` : ""}`}
            </button>
          </div>
        )}
        {editable && (
          <Button
            disabled={Boolean(busy)}
            onClick={() =>
              void guard("merge", async () => {
                setMerging(true);
                setDiff(null);
                setDiff(await api.refDiff(on));
              })
            }
          >
            Merge…
          </Button>
        )}

        {/* Only where there is a pane to toggle: on main the chat is not rendered at all. */}
        {editable && tab === "files" && (
          <Button
            onClick={() => setChatOpen((open) => !open)}
            aria-pressed={chatOpen}
            title="Show or hide the assistant"
          >
            {chatOpen ? "Hide assistant" : "Assistant"}
          </Button>
        )}
      </div>

      {/* What went wrong is under the editor now, in `Problems` — a compile failure used to push
          CodeMirror down by a third of the screen at the exact moment you needed to read it. What
          is left here is the short confirmation: what a click just did. */}
      {note && (
        <div className={`editor-note ${note.tone}`}>
          {note.lines.map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>
      )}

      {tab === "files" ? (
        /* `Layout` positions each side panel absolutely and insets the content by its size, which
           is what lets a pane be dragged without the middle one being re-measured by hand. The
           sizes persist per pane: an editor that forgets how wide you made the tree is one you
           re-drag every visit. `contentPadding="0"` because both panes bring their own. */
        <Layout doNotOverflow hasTransition className="editor-panes">
          <Layout.Panel
            side="left"
            isResizable
            defaultSize={220}
            minSize={170}
            maxSize={420}
            contentPadding="0"
            sizeStorageKey="analyst.editor.tree"
          >
            {/* Changes above the tree, because on a branch it is the shorter list and the one
                being worked on — the tree is where you go to find something, this is where you
                go to see what you have done to it. */}
            <div className="pane-left">
              <Changes
                unsaved={unsaved}
                dirty={dirty}
                open={openPath}
                busy={Boolean(busy)}
                onOpen={open}
                onRevert={revert}
                onDiscard={discard}
              />
              <FileTree
                files={files.concat(Object.keys(buffer).filter((path) => !files.includes(path)))}
                open={openPath}
                unsaved={unsaved}
                dirty={dirty}
                onOpen={open}
                onNew={create}
                editable={editable}
              />
            </div>
          </Layout.Panel>

          <div className="editor-file">
            {/* The strip, and only the strip: every tab's panel is empty and the file renders
                below it, because there is one CodeMirror and it is reconfigured per file rather
                than one per tab. A view per open file would multiply the most expensive thing on
                the screen by however many tabs someone left open.

                The mark is the tree's, deliberately — a file is marked in one vocabulary
                wherever it appears, so a filled dot means "typed, not saved to the draft" in both
                places rather than two similar things in two. */}
            {order.length > 0 && (
              /* Wrapped rather than given the class directly: a tasty component does not put
                 `className` on its DOM node, so the rule silently never applied and the strip
                 was living on the kit's defaults. */
              <div className="editor-filetabs">
                <Tabs
                  type="file"
                  size="small"
                  label="Open files"
                  activeKey={openPath}
                  onChange={(key) => open(String(key))}
                  onDelete={(key) => close(String(key))}
                  isReorderable
                  keyOrder={order}
                  onReorder={setOrder}
                  showTabPicker="auto"
                >
                  {order.map((path) => (
                    <Tab
                      key={path}
                      id={path}
                      tooltip={path}
                      title={
                        <span className="filetab">
                          <span
                            className={`tree-mark ${
                              unsaved.has(path) ? "dirty" : dirty.has(path) ? "staged" : ""
                            }`}
                            aria-hidden
                          />
                          {path.slice(path.indexOf("/") + 1)}
                        </span>
                      }
                    />
                  ))}
                </Tabs>
              </div>
            )}

            {!openPath || !current ? (
              <p className="muted editor-blank">
                {editable
                  ? "Pick a file, or ask the assistant on the right for a change."
                  : `${main} is generated by the pipeline and cannot be edited. Create a branch to change the model.`}
              </p>
            ) : (
              <>
                <div className="editor-actions">
                  <code>{openPath}</code>
                  {reviewing !== null && (
                    <span className="editor-dirty">
                      reviewing a proposal — accept or reject each change
                    </span>
                  )}
                  {/* No state word and no undo here any more. The dot on this file's tab already
                      says which state it is in, and Changes owns both undos — saying "unsaved" a
                      third time next to a Revert that is also in the list was the redundancy this
                      row contributed. Delete stays: it is not an undo but an edit, and it applies
                      to the file that is open. */}
                  <span className="editor-spacer" />
                  {editable && current.text !== null && (
                    <Button
                      onClick={() =>
                        setBuffer((prev) => ({
                          ...prev,
                          [openPath]: { ...prev[openPath], text: null },
                        }))
                      }
                      disabled={Boolean(busy)}
                    >
                      Delete
                    </Button>
                  )}
                </div>

                {current.text === null ? (
                  <p className="editor-blank banner warn">
                    <code>{openPath}</code> will be deleted when you save. Revert to keep it.
                  </p>
                ) : (
                  <Suspense fallback={<p className="muted editor-blank">Loading the editor…</p>}>
                    <CodeEditor
                      path={openPath}
                      value={current.text}
                      original={reviewing}
                      readOnly={!editable}
                      onChange={edit}
                      onReviewed={() => setReviewing(null)}
                    />
                  </Suspense>
                )}
              </>
            )}

            {/* Under the editor and inside its column, so a verdict about the whole draft is
                read where the draft is — and so finding out what is wrong no longer shortens the
                thing you need to read to fix it. */}
            {check && <Problems check={check} />}
          </div>

          {/* Only on a branch: on main there is nothing it could propose that could be saved.
              Collapsible now rather than a fixed column, because reviewing a wide diff and
              talking to the agent are not things you do in the same minute. */}
          {editable && (
            <Layout.Panel
              side="right"
              isResizable
              defaultSize={320}
              minSize={260}
              maxSize={560}
              contentPadding="0"
              sizeStorageKey="analyst.editor.chat"
              isOpen={chatOpen}
              onOpenChange={setChatOpen}
            >
              <EditorChat
                modelRef={on}
                textOf={(path) => buffer[path]?.text ?? ""}
                reviewed={reviewed}
                onReview={review}
              />
            </Layout.Panel>
          )}
        </Layout>
      ) : (
        /* The page no longer scrolls, so this scrolls itself — it is a long list and the tab
           beside it is a full-height pane. */
        <div className="editor-served">
          {model && model.unloaded.length > 0 && (
            <p className="editor-notice">
              In the model but not being served:{" "}
              {model.unloaded.map((f) => (
                <Button key={f} variant="link" onClick={() => open(f)}>
                  {f}
                </Button>
              ))}{" "}
              — usually a file Cube could not compile. Save says why.
            </p>
          )}
          <ModelList
            model={model}
            loading={loading}
            onRefresh={() => void loadBranch(on)}
            unavailable={
              deployed
                ? undefined
                : "No semantic deployment is configured, so nothing can say what it would serve."
            }
            onEdit={editable ? open : undefined}
          />
        </div>
      )}

      {merging && (
        <MergeDialog
          branch={on}
          main={main}
          diff={diff}
          busy={busy === "merge-now"}
          onCancel={() => setMerging(false)}
          onMerge={(force, deleteAfter) =>
            void guard("merge-now", async () => {
              const result = await api.mergeRef(on, force, deleteAfter);
              setMerging(false);
              setNote({
                tone: result.compiled ? "ok" : "bad",
                lines: [
                  result.compiled
                    ? `Merged into ${main} — ${result.entities.length} entities serving.`
                    : `Merged into ${main}, but it does not compile: ${result.compile_error}`,
                  ...(result.deleted ? [`Branch ${on} deleted.`] : []),
                ],
              });
              await loadRefs();
              // Onto main either way: the branch may be gone, and if it is not, it is now empty.
              switchTo(main);
              onChanged();
            })
          }
        />
      )}

      {deleting && (
        <Confirm
          word={deleting}
          title={`Delete branch ${deleting}?`}
          confirmLabel="Delete branch"
          onCancel={() => setDeleting("")}
          onConfirm={() => {
            const ref = deleting;
            setDeleting("");
            remove(ref);
          }}
        >
          <p>
            The branch and this machine's working copy of it both go. Anything committed to{" "}
            <code>{deleting}</code> and never merged into <code>{main}</code> is gone with it, and
            Cube stops serving it.
          </p>
          <p>
            {main} is untouched — a branch is only ever a copy of it until you merge. Nothing else
            reads a branch, so no dashboard or chat is affected.
          </p>
        </Confirm>
      )}
    </div>
  );
}
