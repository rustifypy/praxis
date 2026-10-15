import { Children, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Copy } from "./Copy";

/** A fenced code block with its language and its own copy button. */
function Fence({ children }: { children?: ReactNode }) {
  // react-markdown hands `pre` the `code` element, which carries the language and raw text.
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

// Module scope: a new component map per token makes react-markdown remount the tree.
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
