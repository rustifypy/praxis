import { useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Ref } from "react";

import { duration, useElapsed } from "../hooks";

export interface ComposerHandle {
  fill: (text: string) => void;
}

export function Composer({
  busy,
  disabled,
  startedAt,
  status,
  onSend,
  onStop,
  ref,
}: {
  busy: boolean;
  disabled: boolean;
  startedAt?: number;
  status: string;
  onSend: (question: string) => void;
  onStop: () => void;
  ref?: Ref<ComposerHandle>;
}) {
  const [value, setValue] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const elapsed = useElapsed(startedAt, busy);

  useImperativeHandle(ref, () => ({
    fill: (text: string) => {
      setValue(text);
      box.current?.focus();
    },
  }));

  // Grow with the question rather than scrolling a three-line box.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  // The composer is where attention already is, so focus it when a turn ends.
  useEffect(() => {
    if (!busy && !disabled) box.current?.focus();
  }, [busy, disabled]);

  const submit = () => {
    const question = value.trim();
    if (!question || busy) return;
    setValue("");
    onSend(question);
  };

  return (
    <div className="composer-wrap">
      <div className={`status ${busy ? "on" : ""}`}>
        <span className="status-dot" />
        {status}
        <span className="status-time">{duration(elapsed)}</span>
      </div>

      <div className={`composer ${busy ? "busy" : ""}`}>
        <textarea
          ref={box}
          rows={1}
          value={value}
          disabled={disabled}
          placeholder={disabled ? "Create a chat to start" : "Ask about your data…"}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends, Shift-Enter writes a newline — the convention everywhere else.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {busy ? (
          <button className="stop" onClick={onStop} title="Stop the agent">
            <span className="square" /> Stop
          </button>
        ) : (
          <button className="send" onClick={submit} disabled={disabled || !value.trim()}>
            Send
          </button>
        )}
      </div>
    </div>
  );
}
