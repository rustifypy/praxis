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
import { Kit } from "./Kit";
import { MergeDialog } from "./MergeDialog";
import { Problems } from "./Problems";
import { ServedModel as ModelList } from "./ServedModel";

/** Loaded when a file is first opened: CodeMirror is a quarter of the bundle. */
const CodeEditor = lazy(() => import("./CodeEditor"));

/** `saved` is the server draft's text ("" if not on the branch yet); `text` is the editor's,
 *  or null for a pending deletion. */
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
 * The model Cube serves for a branch and, off main, how to change it. The draft lives on the
 * server; this holds only what was typed since. Save writes the draft, Validate compiles all of
 * it, Publish lands it as one commit. Main is generated, so never editable.
 */
export function ModelEditor({
  deployed,
  onChanged,
}: {
  /** Whether a semantic deployment is configured at all. */
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
  // Tab order, left to right; separate from `buffer` because a strip is an arranged ordering.
  const [order, setOrder] = useState<string[]>([]);
  // The text a proposal is diffed against; non-null puts the editor in review mode.
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState<Set<string>>(new Set());

  const [model, setModel] = useState<ServedModel | null>(null);
  const [loading, setLoading] = useState(false);
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [note, setNote] = useState<{ tone: "ok" | "bad"; lines: string[] } | null>(null);
  const [busy, setBusy] = useState("");
  const [naming, setNaming] = useState(false);
  const [newName, setNewName] = useState("");
  // A new branch's base: not just `on`, since it can change without leaving the shown branch.
  const [newFrom, setNewFrom] = useState("main");
  const [merging, setMerging] = useState(false);
  const [diff, setDiff] = useState<RefDiff | null>(null);
  // A name, not a boolean: the branch being deleted need not be the one shown.
  const [deleting, setDeleting] = useState("");
  // Held here, not in the panel, so the bar can offer the toggle.
  const [chatOpen, setChatOpen] = useState(true);

  const main = refs?.main ?? "main";
  const editable = on !== main;
  const files = listing?.files ?? [];
  // Unsaved is typed but not in the draft (local); dirty is in the draft but unpublished (server).
  const unsaved = new Set(
    Object.entries(buffer)
      .filter(([, entry]) => typed(entry))
      .map(([path]) => path),
  );
  const dirty = new Set([...(listing?.dirty ?? []), ...(listing?.deleted ?? [])]);
  // Derived, never stored: every input is already state.
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

  // On failure, re-read the branch list: the likeliest cause is a branch deleted elsewhere.
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
      // This branch's compiled model, not main's, so a saved view can be checked before merging.
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

  // Closing leaves the server draft untouched, so only text typed since Save is at risk.
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

  // Load the file first, so a chat proposal lands on screen and diffs against the draft's text.
  const review = (path: string, text: string | null) =>
    void guard("open", async () => {
      const base = await load(path);
      setBuffer((prev) => ({
        ...prev,
        [path]: { saved: prev[path]?.saved ?? base, text },
      }));
      focus(path);
      // A proposed deletion has no diff; the pane says what will happen instead.
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
      // A file the draft only added is gone now, so its tab goes too.
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
      // Failures are rendered once, by `Problems`.
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
    // Separate from `moveTo`: leaving a deleted branch has nothing to ask about.
    if (unsaved.size && !window.confirm(`Discard ${unsaved.size} unsaved edit(s)?`)) return;
    moveTo(ref);
  };

  // Drops the ref at the store and its working copy; unmerged commits go too.
  const remove = (ref: string) =>
    void guard("delete-branch", async () => {
      setRefs(await api.deleteRef(ref));
      if (ref === on) moveTo(main);
      setNote({ tone: "ok", lines: [`Branch ${ref} deleted.`] });
    });

  const current = openPath ? buffer[openPath] : undefined;

  return (
    <Kit>
      <div className="editor">
        <div className="editor-bar">
          <BranchPicker
            refs={refs}
            on={on}
            main={main}
            busy={Boolean(busy)}
            onSwitch={switchTo}
            onCreate={() => {
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
              {/* Branches only merge into main, so this carries its base's commits too. */}
              {newFrom !== main && (
                <small className="editor-from-note">
                  carries {newFrom}'s commits into {main} when it merges
                </small>
              )}
            </form>
          )}

          <Info label="About the model">
            Everything the agent brings to a question, and where you change it. The file tree is what
            the model store holds for the branch you are on; the Served model tab is read live from
            Cube's <code>/v1/meta</code> and parsed by the same code the agent queries through, so it
            is what a question can actually reach rather than what the YAML says. Main is generated
            and read-only — edits go on a branch, and Cube serves every branch at once, so a commit is
            live on that branch immediately and main is untouched until you merge.
          </Info>

          <div className="segmented" role="group" aria-label="What to show">
            <button className={tab === "files" ? "on" : ""} onClick={() => setTab("files")}>
              Files
            </button>
            <button className={tab === "model" ? "on" : ""} onClick={() => setTab("model")}>
              Served model
            </button>
          </div>

          <span className="editor-spacer" />

          {/* All three steps always shown, later ones dimmed, so the path is visible up front. */}
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

        {note && (
          <div className={`editor-note ${note.tone}`}>
            {note.lines.map((line, i) => (
              <p key={i}>{line}</p>
            ))}
          </div>
        )}

        {tab === "files" ? (
          /* Pane sizes persist per pane; `contentPadding="0"` because both bring their own. */
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
              {/* Tab panels stay empty: one CodeMirror below is reconfigured per file, not one
                  per tab. The mark is the tree's, so a dot means the same in both. */}
              {order.length > 0 && (
                /* Wrapped: a tasty component does not put `className` on its DOM node. */
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

              {check && <Problems check={check} />}
            </div>

            {/* Only on a branch: on main nothing it proposes could be saved. */}
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
          /* Scrolls itself: the page does not scroll. */
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
    </Kit>
  );
}
