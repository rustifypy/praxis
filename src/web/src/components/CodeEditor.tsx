import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { getChunks, unifiedMergeView } from "@codemirror/merge";
import { EditorState } from "@codemirror/state";
import { yaml } from "@codemirror/lang-yaml";
import { tags } from "@lezer/highlight";
import { EditorView, basicSetup } from "codemirror";
import { useEffect, useRef } from "react";

/**
 * One CodeMirror instance, editing a file or reviewing a proposal. Review is `unifiedMergeView`
 * over the same document, so accepting or rejecting a chunk is just an edit through `onChange`.
 */
export default function CodeEditor({
  path,
  value,
  original,
  readOnly,
  onChange,
  onReviewed,
}: {
  /** Changing it rebuilds the view, so undo history doesn't carry across files. */
  path: string;
  value: string;
  /** The text to diff against (review mode). Null is plain editing. */
  original: string | null;
  readOnly: boolean;
  onChange: (text: string) => void;
  onReviewed: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  // CodeMirror's callbacks are created once and would otherwise close over stale props.
  const latest = useRef({ onChange, onReviewed });
  latest.current = { onChange, onReviewed };

  // `value` is deliberately not a dependency: rebuilding on each keystroke loses the cursor.
  useEffect(() => {
    if (!host.current) return;

    const state = EditorState.create({
      doc: value,
      extensions: [
        basicSetup,
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
          // Accepting the last chunk ends the review.
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

  // An outside change (chat, revert, branch switch). Compared first: re-dispatching the same
  // text would reset the selection.
  useEffect(() => {
    const editor = view.current;
    if (!editor || editor.state.doc.toString() === value) return;
    editor.dispatch({
      changes: { from: 0, to: editor.state.doc.length, insert: value },
    });
  }, [value]);

  return <div className="cm-host" ref={host} />;
}

/** Syntax colours from the app's own tokens, so the editor follows the theme. */
const HIGHLIGHT = HighlightStyle.define([
  { tag: tags.keyword, color: "var(--accent)" },
  // A YAML key.
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

/** `height: 100%` with a scrolling scroller; otherwise CodeMirror grows without bound. */
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
  // Merge view decorations, in the app's bad/ok colours.
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
