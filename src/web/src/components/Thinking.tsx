import { useEffect, useRef, useState } from "react";

import { duration, useElapsed } from "../hooks";
import { Collapse } from "./Collapse";

/** Splits reasoning on the model's `**Heading**` markers into real headings. */
function sections(text: string): { head?: string; body: string }[] {
  const out: { head?: string; body: string }[] = [];
  const pattern = /\*\*(.+?)\*\*/g;
  let current: { head?: string; body: string } = { body: "" };
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text))) {
    current.body += text.slice(cursor, match.index);
    if (current.head || current.body.trim()) out.push(current);
    current = { head: match[1], body: "" };
    cursor = match.index + match[0].length;
  }
  current.body += text.slice(cursor);
  if (current.head || current.body.trim()) out.push(current);
  return out.length ? out : [{ body: text }];
}

export function Thinking({
  text,
  open,
  live,
  startedAt,
  ms,
}: {
  text: string;
  open: boolean;
  live: boolean;
  startedAt?: number;
  ms?: number;
}) {
  const [expanded, setExpanded] = useState(open);
  const [pinnedOpen, setPinnedOpen] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const elapsed = useElapsed(startedAt, live);

  // Follow the agent, but never close a panel the reader opened.
  useEffect(() => {
    if (!pinnedOpen) setExpanded(open);
  }, [open, pinnedOpen]);

  useEffect(() => {
    if (expanded && live && body.current) {
      body.current.scrollTop = body.current.scrollHeight;
    }
  }, [text, expanded, live]);

  const parts = sections(text);
  const peek = parts[parts.length - 1]?.head ?? text.split("\n").find((l) => l.trim()) ?? "";
  const took = live ? duration(elapsed) : ms ? duration(ms) : "";

  return (
    <div className={`thinking ${live ? "live" : ""}`}>
      <button
        className="thinking-head"
        onClick={() => {
          setPinnedOpen(!expanded);
          setExpanded(!expanded);
        }}
      >
        <span className="caret">{expanded ? "▾" : "▸"}</span>
        <span className="thinking-label">
          {live ? "Thinking" : took ? `Thought for ${took}` : "Thought"}
          {live && <span className="dots" />}
        </span>
        {!expanded && <span className="thinking-peek">{peek}</span>}
      </button>

      <Collapse open={expanded}>
        <div className="thinking-body" ref={body}>
          {parts.map((part, i) => (
            <div className="think-part" key={i}>
              {part.head && <div className="think-head">{part.head}</div>}
              {part.body.trim() && (
                <p className={live && i === parts.length - 1 ? "streaming" : ""}>
                  {part.body.trim()}
                </p>
              )}
            </div>
          ))}
        </div>
      </Collapse>
    </div>
  );
}
