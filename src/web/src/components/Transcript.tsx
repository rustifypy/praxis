import * as api from "../api";
import type { Item } from "../types";

import { Answer } from "./Answer";
import { FileBlock } from "./FileBlock";
import { Proposal } from "./Proposal";
import { ResultBlock } from "./ResultBlock";
import { Thinking } from "./Thinking";
import { ToolCall } from "./ToolCall";

/** How a proposal becomes reviewable; supplied only by the model editor, which owns the buffer. */
interface Proposals {
  textOf: (path: string) => string;
  reviewed: Set<string>;
  onReview: (path: string, text: string | null) => void;
}

/** One event stream's rows, shared by every agent's chat. The caller owns scroller and composer. */
export function Transcript({
  items,
  chatId,
  approve,
  proposals,
}: {
  items: Item[];
  chatId: string;
  /** Where approval answers go; a Run passes its own route, a chat omits it. */
  approve?: (choice: "yes" | "always" | "no") => Promise<unknown>;
  proposals?: Proposals;
}) {
  return (
    <>
      {items.map((item, i) => {
        switch (item.kind) {
          case "user":
            return (
              <div key={i} className="row user">
                {item.text}
                {item.files.length > 0 && (
                  <div className="attached sent">
                    {item.files.map((path) => (
                      <a
                        key={path}
                        className="attach"
                        href={chatId ? api.chatFileUrl(chatId, path) : undefined}
                        download
                      >
                        <span className="attach-name">{path.replace(/^uploads\//, "")}</span>
                      </a>
                    ))}
                  </div>
                )}
              </div>
            );
          case "thinking":
            return (
              <div key={i} className="row">
                <Thinking
                  text={item.text}
                  open={item.open}
                  live={item.live}
                  startedAt={item.startedAt}
                  ms={item.ms}
                />
              </div>
            );
          case "tool":
            return (
              <div key={i} className="row">
                <ToolCall
                  chatId={chatId}
                  approve={approve}
                  tool={item.tool}
                  label={item.label}
                  query={item.query}
                  script={item.script}
                  awaiting={item.awaiting}
                  end={item.end}
                  startedAt={item.startedAt}
                  ms={item.ms}
                  live={item.live}
                />
              </div>
            );
          case "answer":
            return (
              <div key={i} className="row">
                <Answer text={item.text} live={item.live} />
              </div>
            );
          case "result":
            return (
              <div key={i} className="row">
                <ResultBlock
                  resultId={item.resultId}
                  label={item.label}
                  columns={item.columns}
                  rows={item.rows}
                  rowCount={item.rowCount}
                  chart={item.chart}
                />
              </div>
            );
          case "files":
            return (
              <div key={i} className="row">
                <FileBlock chatId={chatId} label={item.label} paths={item.paths} />
              </div>
            );
          case "proposal":
            return (
              <div key={i} className="row">
                <Proposal
                  path={item.path}
                  text={item.text}
                  current={proposals?.textOf(item.path) ?? ""}
                  reviewed={proposals?.reviewed.has(item.path) ?? false}
                  onReview={() => proposals?.onReview(item.path, item.text)}
                />
              </div>
            );
          case "compacted":
            return (
              <div key={i} className="row compacted">
                <span>
                  Earlier messages were summarized to fit the context — {item.before} became{" "}
                  {item.after}
                </span>
              </div>
            );
          case "error":
            return (
              <div key={i} className="row error">
                {item.message}
              </div>
            );
        }
      })}
    </>
  );
}
