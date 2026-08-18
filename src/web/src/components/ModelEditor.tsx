import { useCallback, useEffect, useState } from "react";

import * as api from "../sources";
import type { BranchDiff, Branches, Grounding, Impact, ModelValidation, ServedModel } from "../sources";
import { EditorChat } from "./EditorChat";
import { ServedModel as ModelList } from "./ServedModel";

const NEW_VIEW = "views/new_view.yml";
const NEW_VIEW_BODY = `views:
- name: new_view
  cubes:
  - join_path:
    includes: []
`;

/** What a branch would break, with the nearest surviving name so a fix is one edit. */
function Breakage({ impact }: { impact: Impact }) {
  if (!impact.broken.length) {
    return <p>No saved query breaks — {impact.checked} checked.</p>;
  }
  return (
    <>
      <p>
        {impact.broken.length} of {impact.checked} saved queries would break:
      </p>
      <ul className="editor-broken">
        {impact.broken.map((b) => (
          <li key={`${b.kind}:${b.where}:${b.title}`}>
            {b.kind === "tile" ? "Tile" : "Certified query"} <strong>{b.title}</strong> (
            {b.where}) reads{" "}
            {b.members.map((m, i) => (
              <span key={m}>
                {i > 0 && ", "}
                <code>{m}</code>
                {b.nearest[m]?.length ? ` — closest: ${b.nearest[m].join(", ")}` : ""}
              </span>
            ))}
          </li>
        ))}
      </ul>
    </>
  );
}

/**
 * The model: what Cube serves, and — on a branch — how to change it.
 *
 * One panel, and one body with three modes: the model, one file, or the review before a
 * merge. Editing does not get a toolbar of its own — that would stack three rows of controls
 * over a list that already has its own. Everything that acts on the branch is in the top bar,
 * everything that acts on an entity is in that entity's detail header.
 *
 * The list is the *compiled* model, because a view's members only exist once Cube has
 * resolved its includes and aliases; the YAML is what you edit to move them.
 */
export function ModelEditor({
  running,
  model,
  grounding,
  loading,
  onReload,
}: {
  running: boolean;
  model: ServedModel | null;
  grounding: Grounding | null;
  loading: boolean;
  onReload: () => void;
}) {
  const [branches, setBranches] = useState<Branches | null>(null);
  const [mode, setMode] = useState<"model" | "file" | "review">("model");
  const [path, setPath] = useState("");
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState<{ tone: "ok" | "bad"; lines: string[] } | null>(null);
  const [validation, setValidation] = useState<ModelValidation | null>(null);
  const [diff, setDiff] = useState<BranchDiff | null>(null);
  const [naming, setNaming] = useState(false);
  const [newName, setNewName] = useState("");
  const [chatting, setChatting] = useState(false);

  const on = branches?.current ?? model?.branch ?? "";
  const main = branches?.main ?? "main";
  const editable = on !== "" && on !== main;
  const dirty = branches?.dirty ?? model?.dirty ?? [];
  const changed = text !== saved;
  /** Non-zero turns Merge into a forced, destructive action rather than hiding it. */
  const breaks = diff?.impact?.broken.length ?? 0;

  const load = useCallback(async () => {
    setBranches(await api.branches());
  }, []);

  useEffect(() => {
    void load().catch((e: Error) => setNote({ tone: "bad", lines: [e.message] }));
  }, [load]);

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

  const open = (file: string) =>
    void guard("open", async () => {
      const body = await api.modelFile(file);
      setPath(file);
      setText(body.text);
      setSaved(body.text);
      setMode("file");
    });

  /** A file that does not exist yet, so it is seeded rather than fetched. */
  const startNewView = () => {
    setPath(NEW_VIEW);
    setText(NEW_VIEW_BODY);
    setSaved("");
    setMode("file");
  };

  const save = () =>
    void guard("save", async () => {
      const result = await api.writeModelFile(path, text);
      setSaved(text);
      await load();
      onReload();
      setNote({
        tone: "ok",
        lines: ["Saved — and live. Validate reports what compiled and what broke.", ...result.warnings],
      });
    });

  return (
    <div className="editor">
      <div className="editor-bar">
        <select
          value={on}
          disabled={Boolean(busy) || dirty.length > 0}
          onChange={(e) =>
            void guard("checkout", async () => {
              setBranches(await api.checkoutBranch(e.target.value));
              setMode("model");
              setValidation(null);
              onReload();
            })
          }
        >
          {branches?.branches.map((b) => (
            <option key={b.name} value={b.name}>
              {b.name}
              {b.changed ? ` (${b.changed} changed)` : ""}
            </option>
          ))}
        </select>

        {naming ? (
          <form
            className="editor-name"
            onSubmit={(e) => {
              e.preventDefault();
              void guard("branch", async () => {
                setBranches(await api.createBranch(newName.trim()));
                setNaming(false);
                setNewName("");
                onReload();
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
          <button onClick={() => setNaming(true)} disabled={Boolean(busy) || dirty.length > 0}>
            New branch
          </button>
        )}
        {/* Grouped left with the other "make something" control, not buried in the chat. */}
        {editable && !naming && (
          <button onClick={startNewView} disabled={Boolean(busy)}>
            New view
          </button>
        )}

        <span className="editor-spacer" />

        {/* Only what applies right now: on main this bar is three controls, not seven. */}
        {dirty.length > 0 && <span className="editor-dirty">{dirty.length} uncommitted</span>}
        <button
          disabled={!running || Boolean(busy)}
          title="Compile the model and report what it breaks"
          onClick={() =>
            void guard("validate", async () => {
              setValidation(await api.validateModel());
              onReload();
            })
          }
        >
          {busy === "validate" ? "Validating…" : "Validate"}
        </button>
        {editable && dirty.length > 0 && (
          <>
            <button
              disabled={Boolean(busy)}
              onClick={() =>
                void guard("commit", async () => {
                  await api.commitModel(`edit ${on}`);
                  await load();
                  onReload();
                })
              }
            >
              Commit
            </button>
            <button
              className="danger"
              disabled={Boolean(busy)}
              onClick={() =>
                void guard("discard", async () => {
                  await api.discardModel();
                  setMode("model");
                  await load();
                  onReload();
                })
              }
            >
              Discard
            </button>
          </>
        )}
        {editable && (
          <button
            className="primary"
            disabled={Boolean(busy)}
            onClick={() =>
              void guard("diff", async () => {
                setDiff(await api.branchDiff(on));
                setMode("review");
              })
            }
          >
            Review
          </button>
        )}
      </div>

      {/* Under the bar, next to whatever produced them. */}
      {note && (
        <div className={`editor-note ${note.tone}`}>
          {note.lines.map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>
      )}

      {validation && (
        <div className={`editor-note ${validation.compiled ? "ok" : "bad"}`}>
          {validation.compiled ? (
            <p>Cube compiled it — serving {validation.entities.length} entities.</p>
          ) : (
            <p>Cube refused it: {validation.compile_error}</p>
          )}
          {Object.entries(validation.member_errors).map(([member, message]) => (
            <p key={member}>
              <code>{member}</code> {message}
            </p>
          ))}
          {validation.impact && <Breakage impact={validation.impact} />}
        </div>
      )}

      {mode === "file" && (
        <div className="editor-pane">
          <div className="editor-actions">
            <button onClick={() => setMode("model")} disabled={Boolean(busy)}>
              ← Model
            </button>
            <code>{path}</code>
            <span className="editor-spacer" />
            {changed && <span className="editor-dirty">unsaved</span>}
            <button className="primary" disabled={!changed || Boolean(busy)} onClick={save}>
              {busy === "save" ? "Saving…" : "Save"}
            </button>
          </div>
          <textarea spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
        </div>
      )}

      {mode === "review" && (
        <div className="editor-pane">
          <div className="editor-actions">
            <button onClick={() => setMode("model")} disabled={Boolean(busy)}>
              ← Model
            </button>
            <span className="editor-spacer" />
            <button
              className={breaks ? "danger" : "primary"}
              disabled={!diff?.changed.length || dirty.length > 0 || Boolean(busy)}
              onClick={() =>
                void guard("merge", async () => {
                  const result = await api.mergeBranch(on, breaks > 0);
                  setValidation(result);
                  setDiff(null);
                  setMode("model");
                  await load();
                  onReload();
                })
              }
            >
              {breaks ? `Merge anyway, breaking ${breaks}` : `Merge into ${main}`}
            </button>
          </div>
          {diff?.impact && (
            <div className={`editor-note ${breaks ? "bad" : "ok"}`}>
              <Breakage impact={diff.impact} />
            </div>
          )}
          <pre className="editor-diff">
            {diff?.patch || "Nothing committed on this branch yet."}
          </pre>
          <p className="editor-hint">
            Merging installs this branch and makes it {main}. Cube compiling it does not mean a
            measure is right, so read the diff.
          </p>
        </div>
      )}

      {mode === "model" && (
        <>
          {!editable && (
            <p className="editor-notice">
              <code>{main}</code> is what the pipeline generates, and re-running a step rewrites
              every file in it. Create a branch to edit — it is served as soon as it is checked
              out, so there is nothing to deploy afterwards.
            </p>
          )}
          {model && model.unloaded.length > 0 && (
            <p className="editor-notice">
              On disk but not being served:{" "}
              {model.unloaded.map((f) => (
                <button key={f} className="link" onClick={() => open(f)}>
                  {f}
                </button>
              ))}{" "}
              — usually a file Cube could not compile. Validate says why.
            </p>
          )}
          <ModelList
            model={model}
            grounding={grounding}
            loading={loading}
            onRefresh={onReload}
            unavailable={
              running
                ? undefined
                : "The container is not running, so Cube cannot say what it would serve."
            }
            onEdit={editable ? open : undefined}
          />
        </>
      )}

      {/* Shut by default. An assistant that is always on screen is a second thing to read
          before you have asked it anything. */}
      {editable && mode === "model" && (
        <div className="editor-assist">
          <button className="editor-assist-head" onClick={() => setChatting(!chatting)}>
            <span aria-hidden>{chatting ? "▾" : "▸"}</span> Ask the editor to change something
          </button>
          {chatting && <EditorChat onChanged={() => void load().then(onReload)} />}
        </div>
      )}
    </div>
  );
}

