import { Children, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Copy } from "./Copy";

/**
 * A fenced code block, with the language it was tagged with and a button that takes it.
 *
 * The answer already has one copy button, and it takes the whole answer — which is the wrong
 * unit for the thing people actually copy out of an analyst's reply, a SQL statement or a
 * snippet in the middle of three paragraphs of prose.
 */
function Fence({ children }: { children?: ReactNode }) {
  // react-markdown hands `pre` the `code` element it wraps, and both the language and the raw
  // text are on that child.
  const code = Children.toArray(children).find(isValidElement) as
    | ReactElement<{ className?: string; children?: ReactNode }>
    | undefined;
  const language = /language-([\w-]+)/.exec(code?.props.className ?? "")?.[1] ?? "";

  return (
    <div className="fence">
      <div className="fence-head">
        <span className="fence-lang">{language || "text"}</span>
        <Copy value={textOf(code?.props.children)} />
      </div>
      <pre>{children}</pre>
    </div>
  );
}

function textOf(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) =>
      isValidElement(child)
        ? textOf((child.props as { children?: ReactNode }).children)
        : String(child),
    )
    .join("");
}

// At module scope: a fresh object here would be a new component map on every token, and
// react-markdown remounts the tree when that changes.
const COMPONENTS = { pre: Fence };

export function Answer({ text, live }: { text: string; live: boolean }) {
  return (
    <div className={`answer ${live ? "streaming" : ""}`}>
      <Markdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {text}
      </Markdown>
      {!live && text.trim() && (
        <div className="answer-tools">
          <Copy value={text} label="Copy answer" />
        </div>
      )}
    </div>
  );
}
