import * as api from "../api";
import type { Item } from "../types";

import { Answer } from "./Answer";
import { FileBlock } from "./FileBlock";
import { ResultBlock } from "./ResultBlock";
import { Thinking } from "./Thinking";
import { ToolCall } from "./ToolCall";

/**
 * One event stream, rendered. Both agents' chats use this — the reducer that builds `items`
 * is already shared, and a second renderer over the same shape is what left the model
 * editor with no reasoning panel and no compaction notice.
 *
 * Rows only: the scroller, the empty state and the composer belong to whoever is placing it.
 */
export function Transcript({ items, chatId }: { items: Item[]; chatId: string }) {
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
