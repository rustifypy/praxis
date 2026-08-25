import { Suspense, lazy, useCallback, useEffect, useState } from "react";

import * as api from "../sources";
import type { CheckResult, ModelFiles, RefDiff, Refs, ServedModel } from "../sources";
import { EditorChat } from "./EditorChat";
import { FileTree } from "./FileTree";
import { Breakage, MergeDialog } from "./MergeDialog";
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
  const [merging, setMerging] = useState(false);
  const [diff, setDiff] = useState<RefDiff | null>(null);

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

  const loadBranch = useCallback(async (ref: string) => {
    setLoading(true);
    try {
      setListing(await api.modelFiles(ref));
    } catch {
      setListing(null);
    }
    // The compiled model for *this* branch, not for main: it is how you check that the view you
    // just saved exposes the members you meant, without merging first.
    try {
      setModel(await api.model(ref));
    } catch (err) {
      setModel({ serving: false, error: String(err), entities: [], unloaded: [] });
    }
    setLoading(false);
  }, []);

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

  const open = (path: string) =>
    void guard("open", async () => {
      await load(path);
      setOpenPath(path);
      setReviewing(null);
    });

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
      setOpenPath(path);
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
      if (!body && openPath === path) setOpenPath("");
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
    setOpenPath(path);
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
      if (openPath && buffer[openPath]?.text === null) setOpenPath("");
      setNote({ tone: "ok", lines: [`Saved ${unsaved.size} file${unsaved.size === 1 ? "" : "s"}.`] });
    });

  /** Validate: would Cube accept the draft as it stands? Nothing is published. */
  const validate = () =>
    void guard("validate", async () => {
      const result = await api.checkChanges(on);
      setCheck(result);
      if (!result.ok) {
        setNote({
          tone: "bad",
          lines: [
            "The model would not compile.",
            ...result.errors,
            ...(result.compile_error ? [result.compile_error] : []),
          ],
        });
        return;
      }
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

  const switchTo = (ref: string) => {
    // Only what is typed is at risk — the draft is on the server and will still be there — but
    // losing it because someone changed a dropdown is still worth one question.
    if (unsaved.size && !window.confirm(`Discard ${unsaved.size} unsaved edit(s)?`)) return;
    setOn(ref);
    setBuffer({});
    setOpenPath("");
    setReviewing(null);
    setReviewed(new Set());
    setCheck(null);
    setNote(null);
  };

  const current = openPath ? buffer[openPath] : undefined;

  return (
    <div className="editor">
      {/* The branch bar: what you are looking at on the left, what you can do to it on the
          right. It is the top of the model section rather than of the page, because the header
          above it is the *connection* and these two are different questions. */}
      <div className="editor-bar">
        <span className="editor-branch" aria-hidden>
          ⎇
        </span>
        <select
          value={on}
          disabled={Boolean(busy)}
          onChange={(e) => switchTo(e.target.value)}
          title="Which branch of the model you are looking at"
        >
          {refs?.refs.map((r) => (
            <option key={r.name} value={r.name}>
              {r.name}
              {r.changed ? ` (${r.changed} changed)` : ""}
            </option>
          ))}
        </select>

        {naming ? (
          <form
            className="editor-name"
            onSubmit={(e) => {
              e.preventDefault();
              void guard("branch", async () => {
                const name = newName.trim();
                setRefs(await api.createRef(name));
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
            <button type="submit" className="primary" disabled={!newName.trim()}>
              Create
            </button>
            <button type="button" onClick={() => setNaming(false)}>
              Cancel
            </button>
          </form>
        ) : (
          <button onClick={() => setNaming(true)} disabled={Boolean(busy)}>
            New branch
          </button>
        )}

        <span className="editor-spacer" />

        {/* One slot, three states in the order they happen: write it down, ask Cube, land it.
            Three buttons side by side would leave two of them permanently dead. */}
        {editable && unsaved.size > 0 && (
          <button className="primary" disabled={Boolean(busy)} onClick={save}>
            {busy === "save" ? "Saving…" : `Save ${unsaved.size} file${unsaved.size === 1 ? "" : "s"}`}
          </button>
        )}
        {editable && unsaved.size === 0 && dirty.size > 0 && !check?.ok && (
          <button className="primary" disabled={Boolean(busy)} onClick={validate}>
            {busy === "validate" ? "Compiling…" : `Validate ${dirty.size}`}
          </button>
        )}
        {editable && unsaved.size === 0 && dirty.size > 0 && check?.ok && (
          <button className="primary" disabled={Boolean(busy)} onClick={publish}>
            {busy === "publish" ? "Publishing…" : `Publish ${dirty.size}`}
          </button>
        )}
        {editable && (
          <button
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
          </button>
        )}
      </div>

      {/* What a refresh costs, said once and where the buttons are. Only what is typed is at
          risk now: the draft is on the server and survives a reload. */}
      {editable && unsaved.size > 0 && (
        <p className="editor-warn">
          {unsaved.size} file{unsaved.size === 1 ? " has" : "s have"} edits that are not saved to
          the branch's draft yet — a reload discards them.
        </p>
      )}

      {note && (
        <div className={`editor-note ${note.tone}`}>
          {note.lines.map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>
      )}

      {/* Member errors and broken queries are reported and never refuse a Save: a model that
          compiles with a measure that fails at query time still beats one that will not compile,
          and a tile this would break is the merge's business. */}
      {check?.ok && Object.keys(check.member_errors).length > 0 && (
        <div className="editor-note bad">
          <p>Compiled, but these fail at query time:</p>
          {Object.entries(check.member_errors).map(([member, message]) => (
            <p key={member}>
              <code>{member}</code> {message}
            </p>
          ))}
        </div>
      )}
      {check?.ok && check.impact && <Breakage impact={check.impact} />}

      <div className="editor-tabs">
        <button className={tab === "files" ? "on" : ""} onClick={() => setTab("files")}>
          Files
        </button>
        <button className={tab === "model" ? "on" : ""} onClick={() => setTab("model")}>
          Served model
        </button>
      </div>

      {tab === "files" ? (
        <div className="editor-panes">
          <FileTree
            files={files.concat(Object.keys(buffer).filter((path) => !files.includes(path)))}
            open={openPath}
            unsaved={unsaved}
            dirty={dirty}
            onOpen={open}
            onNew={create}
            editable={editable}
          />

          <div className="editor-file">
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
                  {unsaved.has(openPath) && reviewing === null && (
                    <span className="editor-dirty">unsaved</span>
                  )}
                  {!unsaved.has(openPath) && dirty.has(openPath) && (
                    <span className="editor-dirty ok">in the draft</span>
                  )}
                  <span className="editor-spacer" />
                  {typed(current) && (
                    <button onClick={() => revert(openPath)} disabled={Boolean(busy)}>
                      Revert
                    </button>
                  )}
                  {!unsaved.has(openPath) && dirty.has(openPath) && (
                    <button onClick={() => discard(openPath)} disabled={Boolean(busy)}>
                      Discard
                    </button>
                  )}
                  {editable && current.text !== null && (
                    <button
                      onClick={() =>
                        setBuffer((prev) => ({
                          ...prev,
                          [openPath]: { ...prev[openPath], text: null },
                        }))
                      }
                      disabled={Boolean(busy)}
                    >
                      Delete
                    </button>
                  )}
                </div>

                {current.text === null ? (
                  <p className="editor-blank banner warn">
                    <code>{openPath}</code> will be deleted when you save. Revert to keep it.
                  </p>
                ) : (
                  <Suspense
                    fallback={<p className="muted editor-blank">Loading the editor…</p>}
                  >
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
          </div>

          {/* Only on a branch: on main there is nothing it could propose that could be saved. */}
          {editable && (
            <EditorChat
              modelRef={on}
              textOf={(path) => buffer[path]?.text ?? ""}
              reviewed={reviewed}
              onReview={review}
            />
          )}
        </div>
      ) : (
        <>
          {model && model.unloaded.length > 0 && (
            <p className="editor-notice">
              In the model but not being served:{" "}
              {model.unloaded.map((f) => (
                <button key={f} className="link" onClick={() => open(f)}>
                  {f}
                </button>
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
        </>
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
    </div>
  );
}
