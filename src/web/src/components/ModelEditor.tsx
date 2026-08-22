import { useCallback, useEffect, useState } from "react";

import * as api from "../sources";
import type { Grounding, Impact, ModelValidation, RefDiff, Refs, ServedModel } from "../sources";
import { EditorChat } from "./EditorChat";
import { ServedModel as ModelList } from "./ServedModel";

const NEW_VIEW = "views/new_view.yml";
const NEW_VIEW_BODY = `views:
- name: new_view
  cubes:
  - join_path:
    includes: []
`;

/** What a ref would break, with the nearest surviving name so a fix is one edit. */
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
 * The model: what Cube serves, and — on a ref of its own — how to change it.
 *
 * One panel, and one body with three modes: the model, one file, or the review before a
 * merge. Editing does not get a toolbar of its own — that would stack three rows of controls
 * over a list that already has its own. Everything that acts on the ref is in the top bar,
 * everything that acts on an entity is in that entity's detail header.
 *
 * The ref selector changes what you are *looking at* and nothing else. One Cube compiles every
 * ref at once, so there is no checkout, nothing to switch, and no state on the server that
 * could disagree with this dropdown.
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
  const [refs, setRefs] = useState<Refs | null>(null);
  const [on, setOn] = useState("main");
  const [mode, setMode] = useState<"model" | "file" | "review">("model");
  const [path, setPath] = useState("");
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState<{ tone: "ok" | "bad"; lines: string[] } | null>(null);
  const [validation, setValidation] = useState<ModelValidation | null>(null);
  const [diff, setDiff] = useState<RefDiff | null>(null);
  const [naming, setNaming] = useState(false);
  const [newName, setNewName] = useState("");
  const [chatting, setChatting] = useState(false);

  const main = refs?.main ?? "main";
  const editable = on !== main;
  const changed = text !== saved;
  /** Non-zero turns Merge into a forced, destructive action rather than hiding it. */
  const breaks = diff?.impact?.broken.length ?? 0;

  const load = useCallback(async () => {
    setRefs(await api.refs());
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
      const body = await api.modelFile(file, on);
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
      const result = await api.writeModelFile(path, text, on);
      setSaved(text);
      await load();
      onReload();
      setNote({
        tone: "ok",
        lines: [
          `Saved to ${on} — and live. Validate reports what compiled and what broke.`,
          ...result.warnings,
        ],
      });
    });

  return (
    <div className="editor">
      <div className="editor-bar">
        <select
          value={on}
          disabled={Boolean(busy)}
          onChange={(e) => {
            setOn(e.target.value);
            setMode("model");
            setValidation(null);
            setDiff(null);
          }}
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
              void guard("ref", async () => {
                const name = newName.trim();
                setRefs(await api.createRef(name));
                setOn(name);
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
          <button onClick={() => setNaming(true)} disabled={Boolean(busy)}>
            New ref
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
        <button
          disabled={!running || Boolean(busy)}
          title="Compile the model and report what it breaks"
          onClick={() =>
            void guard("validate", async () => {
              setValidation(await api.validateModel(on));
              onReload();
            })
          }
        >
          {busy === "validate" ? "Validating…" : "Validate"}
        </button>
        {editable && (
          <button
            className="primary"
            disabled={Boolean(busy)}
            onClick={() =>
              void guard("diff", async () => {
                setDiff(await api.refDiff(on));
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
              disabled={!diff?.changed.length || Boolean(busy)}
              onClick={() =>
                void guard("merge", async () => {
                  const result = await api.mergeRef(on, breaks > 0);
                  setValidation(result);
                  setDiff(null);
                  setMode("model");
                  setOn(main);
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
          <pre className="editor-diff">{diff?.patch || "Nothing changed on this ref yet."}</pre>
          <p className="editor-hint">
            Merging lands this ref on {main}. Cube compiling it does not mean a measure is right,
            so read the diff.
          </p>
        </div>
      )}

      {mode === "model" && (
        <>
          {!editable && (
            <p className="editor-notice">
              <code>{main}</code> is what the pipeline generates, and re-running a step rewrites
              every file in it. Create a ref to edit — Cube serves every ref at once, so a save
              is live on that ref immediately and {main} is untouched until you merge.
            </p>
          )}
          {model && model.unloaded.length > 0 && (
            <p className="editor-notice">
              In the model but not being served:{" "}
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
              running ? undefined : "Cube is not answering, so it cannot say what it would serve."
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
          {chatting && <EditorChat modelRef={on} onChanged={() => void load().then(onReload)} />}
        </div>
      )}
    </div>
  );
}

