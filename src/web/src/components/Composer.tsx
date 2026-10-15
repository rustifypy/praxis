import { useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Ref } from "react";

import * as api from "../api";
import type { Upload } from "../api";
import { duration, useElapsed } from "../hooks";
import { Button } from "./Button";

export interface ComposerHandle {
  fill: (text: string) => void;
}

/** Files upload on drop, not on Send, so an unreadable CSV shows on its chip before a turn
 *  is spent on it. */
export function Composer({
  chatId,
  busy,
  disabled,
  startedAt,
  status,
  onSend,
  onStop,
  ref,
}: {
  chatId: string | null;
  busy: boolean;
  disabled: boolean;
  startedAt?: number;
  status: string;
  onSend: (question: string, files: string[]) => void;
  onStop: () => void;
  ref?: Ref<ComposerHandle>;
}) {
  const [value, setValue] = useState("");
  const [attached, setAttached] = useState<Upload[]>([]);
  const [sending, setSending] = useState<string[]>([]);
  const [failed, setFailed] = useState("");
  const [over, setOver] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const elapsed = useElapsed(startedAt, busy);

  useImperativeHandle(ref, () => ({
    fill: (text: string) => {
      setValue(text);
      box.current?.focus();
    },
  }));

  useEffect(() => {
    setAttached([]);
    setSending([]);
    setFailed("");
  }, [chatId]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  useEffect(() => {
    if (!busy && !disabled) box.current?.focus();
  }, [busy, disabled]);

  const take = useCallback(
    async (files: FileList | null) => {
      if (!chatId || !files?.length) return;
      setFailed("");
      for (const file of Array.from(files)) {
        setSending((prev) => [...prev, file.name]);
        try {
          const upload = await api.uploadFile(chatId, file);
          setAttached((prev) => [...prev, upload]);
        } catch (err) {
          setFailed(String(err instanceof Error ? err.message : err));
        } finally {
          setSending((prev) => prev.filter((name) => name !== file.name));
        }
      }
    },
    [chatId],
  );

  // On the window: a file dropped elsewhere would be navigated to, losing the turn in flight.
  useEffect(() => {
    const dragging = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const over = (e: DragEvent) => {
      if (!dragging(e)) return;
      e.preventDefault();
      setOver(true);
    };
    // relatedTarget is null only when the pointer has left the window itself.
    const leave = (e: DragEvent) => e.relatedTarget === null && setOver(false);
    const drop = (e: DragEvent) => {
      e.preventDefault();
      setOver(false);
      if (dragging(e)) void take(e.dataTransfer?.files ?? null);
    };
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [take]);

  const submit = () => {
    const question = value.trim();
    if (!question || busy) return;
    setValue("");
    onSend(
      question,
      attached.map((f) => f.path),
    );
    setAttached([]);
  };

  return (
    <div className="composer-wrap">
      <div className={`status ${busy ? "on" : ""}`}>
        <span className="status-dot" />
        {status}
        <span className="status-time">{duration(elapsed)}</span>
      </div>

      {failed && <p className="composer-failed">{failed}</p>}

      <div className={`composer ${busy ? "busy" : ""} ${over ? "over" : ""}`}>
        {(attached.length > 0 || sending.length > 0) && (
          <div className="attached">
            {attached.map((file) => (
              <span key={file.path} className="attach" title={file.note || undefined}>
                <span className="attach-name">{file.path.replace(/^uploads\//, "")}</span>
                <span className="attach-meta">
                  {file.rows
                    ? `${file.rows.toLocaleString()} × ${file.columns}`
                    : file.note
                      ? "no shape read"
                      : `${Math.max(1, Math.round(file.bytes / 1024))} kB`}
                </span>
                <button
                  className="attach-drop"
                  title="Remove this file"
                  onClick={() => {
                    setAttached((prev) => prev.filter((f) => f.path !== file.path));
                    void api.deleteChatFile(chatId!, file.path).catch(() => {});
                  }}
                >
                  ✕
                </button>
              </span>
            ))}
            {sending.map((name) => (
              <span key={name} className="attach pending">
                <span className="attach-name">{name}</span>
                <span className="attach-meta">reading…</span>
              </span>
            ))}
          </div>
        )}

        <textarea
          ref={box}
          rows={1}
          value={value}
          disabled={disabled}
          placeholder={disabled ? "Create a chat to start" : "Ask about your data…"}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />

        <input
          ref={picker}
          className="composer-picker"
          type="file"
          multiple
          onChange={(e) => {
            void take(e.target.files);
            e.target.value = "";
          }}
        />
        <Button
          className="attach-add"
          disabled={disabled || busy}
          title="Attach a file"
          onClick={() => picker.current?.click()}
        >
          +
        </Button>

        {busy ? (
          <Button variant="danger" className="stop" onClick={onStop} title="Stop the agent">
            <span className="square" /> Stop
          </Button>
        ) : (
          <Button variant="primary" className="send" onClick={submit} disabled={disabled || !value.trim()}>
            Send
          </Button>
        )}
      </div>
    </div>
  );
}
