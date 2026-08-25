import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { getChunks, unifiedMergeView } from "@codemirror/merge";
import { EditorState } from "@codemirror/state";
import { yaml } from "@codemirror/lang-yaml";
import { tags } from "@lezer/highlight";
import { EditorView, basicSetup } from "codemirror";
import { useEffect, useRef } from "react";

/**
 * One CodeMirror instance, in one of two modes: editing a file, or reviewing a proposed one.
 *
 * **Its own component because a CodeMirror view is imperative.** It owns a DOM node, is
 * configured once, and is changed by dispatching transactions rather than by re-rendering — so
 * mixing its lifecycle into a component that also draws a toolbar is how the two get tangled and
 * the editor starts being torn down on every keystroke.
 *
 * **Review mode is the same instance with one extension added.** `unifiedMergeView` puts the
 * *proposal* in the document and the committed text in `original`, drawing the deleted lines
 * above the new ones with an accept and a reject button per chunk. Accepting keeps the new lines;
 * rejecting restores the old ones. Either way the document is edited, which means `onChange`
 * fires and the buffer above receives the result — there is no separate "apply a hunk" code to
 * write, and no second implementation of it to disagree with this one.
 *
 * Once no chunks are left the review is over and `onReviewed` says so, because a merge view with
 * nothing to merge is a normal editor wearing a gutter.
 */
export function CodeEditor({
  path,
  value,
  original,
  readOnly,
  onChange,
  onReviewed,
}: {
  /** Which file this is. Changing it rebuilds the view — a new file is a new document, and its
   *  undo history must not continue from the last one's. */
  path: string;
  value: string;
  /** The text to diff against, putting the editor in review mode. Null is plain editing. */
  original: string | null;
  readOnly: boolean;
  onChange: (text: string) => void;
  onReviewed: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  // Read inside CodeMirror's own callbacks, which are created once and would otherwise close
  // over the first render's props forever.
  const latest = useRef({ onChange, onReviewed });
  latest.current = { onChange, onReviewed };

  // Rebuilt only when something structural moves: the file, the mode, or whether it is writable.
  // `value` is deliberately not a dependency — it arrives back from `onChange` on every
  // keystroke, and rebuilding here would destroy the cursor as you type.
  useEffect(() => {
    if (!host.current) return;

    const state = EditorState.create({
      doc: value,
      extensions: [
        basicSetup,
        // Anything that is not YAML gets no language mode: a wrong grammar highlighting
        // prose is worse than none.
        ...(path.endsWith(".yml") ? [yaml()] : []),
        syntaxHighlighting(HIGHLIGHT),
        THEME,
        EditorView.editable.of(!readOnly),
        EditorState.readOnly.of(readOnly),
        ...(original === null
          ? []
          : [unifiedMergeView({ original, mergeControls: true, gutter: true })]),
        EditorView.updateListener.of((update) => {
          if (!update.docChanged) return;
          latest.current.onChange(update.state.doc.toString());
          // Asked after the change, so accepting the last chunk ends the review rather than
          // leaving an empty merge view on screen.
          if (original !== null && getChunks(update.state)?.chunks.length === 0) {
            latest.current.onReviewed();
          }
        }),
      ],
    });

    view.current = new EditorView({ state, parent: host.current });
    return () => {
      view.current?.destroy();
      view.current = null;
    };
  }, [path, original, readOnly]);

  // A change that came from somewhere else — the chat replacing a file wholesale, a revert, a
  // branch switch. Compared against the current document first: dispatching what is already
  // there would reset the selection on every render.
  useEffect(() => {
    const editor = view.current;
    if (!editor || editor.state.doc.toString() === value) return;
    editor.dispatch({
      changes: { from: 0, to: editor.state.doc.length, insert: value },
    });
  }, [value]);

  return <div className="cm-host" ref={host} />;
}

/**
 * Syntax colours from the app's own tokens, rather than a packaged theme.
 *
 * CodeMirror's default highlight style is built for a white page and is unreadable on the dark
 * palette. A theme package would be a fourth dependency carrying colours that match nothing else
 * on the screen; these are the variables every other panel already uses, so the editor is dark
 * when the app is dark with no second source of truth about what "accent" means.
 */
const HIGHLIGHT = HighlightStyle.define([
  { tag: tags.keyword, color: "var(--accent)" },
  // A YAML key. This is the tag the grammar uses for `name:`, and it is the one that carries
  // most of the meaning in a model file.
  { tag: tags.definition(tags.propertyName), color: "var(--accent)", fontWeight: "600" },
  { tag: tags.propertyName, color: "var(--accent)" },
  { tag: tags.string, color: "var(--ok)" },
  { tag: tags.number, color: "var(--ok)" },
  { tag: tags.bool, color: "var(--warn)" },
  { tag: tags.null, color: "var(--warn)" },
  { tag: tags.comment, color: "var(--faint)", fontStyle: "italic" },
  { tag: tags.meta, color: "var(--muted)" },
  { tag: tags.invalid, color: "var(--bad)" },
]);

/**
 * The chrome: fonts, colours and the height rule.
 *
 * `height: 100%` on the scroller with `overflow: auto` rather than a fixed pixel height, so the
 * editor fills whatever the pane gives it. CodeMirror will grow without bound otherwise, and a
 * 400-line cube file would push the branch bar off the top of the page.
 */
const THEME = EditorView.theme({
  "&": {
    height: "100%",
    fontSize: "13px",
    backgroundColor: "var(--raised)",
    color: "var(--ink)",
  },
  ".cm-scroller": { overflow: "auto", fontFamily: "var(--mono)", lineHeight: "1.55" },
  ".cm-gutters": {
    backgroundColor: "var(--panel)",
    color: "var(--faint)",
    border: "none",
    borderRight: "1px solid var(--line)",
  },
  ".cm-activeLine": { backgroundColor: "var(--accent-soft)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--accent-soft)" },
  ".cm-selectionBackground, ::selection": { backgroundColor: "var(--accent-soft)" },
  ".cm-cursor": { borderLeftColor: "var(--ink)" },
  // The merge view's own decorations. Red for what would go, green for what would arrive — the
  // two colours the rest of the app already uses for bad and ok, at a background's opacity.
  ".cm-deletedChunk": { backgroundColor: "rgba(179, 64, 47, 0.13)" },
  ".cm-changedLine": { backgroundColor: "rgba(47, 125, 79, 0.13)" },
  ".cm-changedText": { backgroundColor: "rgba(47, 125, 79, 0.22)" },
  ".cm-deletedChunk .cm-deletedText": { backgroundColor: "rgba(179, 64, 47, 0.22)" },
  ".cm-chunkButtons button": {
    font: "inherit",
    fontSize: "11px",
    cursor: "pointer",
    border: "1px solid var(--line)",
    borderRadius: "4px",
    padding: "0 6px",
    margin: "0 2px",
    background: "var(--raised)",
    color: "var(--ink)",
  },
});
