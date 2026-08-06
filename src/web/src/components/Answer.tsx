import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Copy } from "./Copy";

export function Answer({ text, live }: { text: string; live: boolean }) {
  return (
    <div className={`answer ${live ? "streaming" : ""}`}>
      <Markdown remarkPlugins={[remarkGfm]}>{text}</Markdown>
      {!live && text.trim() && (
        <div className="answer-tools">
          <Copy value={text} label="Copy answer" />
        </div>
      )}
    </div>
  );
}
