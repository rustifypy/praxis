import { useEffect, useRef, useState } from "react";

import { ago } from "../hooks";
import type { Chat } from "../types";

/**
 * Renaming and deleting happen in place.
 *
 * `window.prompt` and `window.confirm` freeze the page, cannot be styled, and look
 * like a browser error — the wrong feel for a two-word rename.
 */
function Row({
  chat,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  chat: Chat;
  active: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [draft, setDraft] = useState(chat.title);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);

  // A stray click elsewhere should abandon a delete, not leave it armed.
  useEffect(() => {
    if (!confirming) return;
    const cancel = () => setConfirming(false);
    window.addEventListener("click", cancel);
    return () => window.removeEventListener("click", cancel);
  }, [confirming]);

  const commit = () => {
    const title = draft.trim();
    if (title && title !== chat.title) onRename(title);
    else setDraft(chat.title);
    setEditing(false);
  };

  if (editing) {
    return (
      <div className="chat-row editing">
        <input
          ref={input}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") {
              setDraft(chat.title);
              setEditing(false);
            }
          }}
        />
      </div>
    );
  }

  return (
    <div
      className={`chat-row ${active ? "active" : ""} ${confirming ? "confirming" : ""}`}
      onClick={onSelect}
      onDoubleClick={() => setEditing(true)}
    >
      <span className="chat-text">
        <span className="chat-title">{chat.title}</span>
        <span className="chat-when">{ago(chat.updated_at)}</span>
      </span>

      {confirming ? (
        <span className="chat-actions open">
          <button
            className="danger"
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
          >
            Delete
          </button>
        </span>
      ) : (
        <span className="chat-actions">
          <button
            title="Rename"
            onClick={(e) => {
              e.stopPropagation();
              setEditing(true);
            }}
          >
            ✎
          </button>
          <button
            title="Delete"
            onClick={(e) => {
              e.stopPropagation();
              setConfirming(true);
            }}
          >
            ×
          </button>
        </span>
      )}
    </div>
  );
}

export function Sidebar({
  chats,
  activeId,
  cubeUrl,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: {
  chats: Chat[];
  activeId: string | null;
  cubeUrl: string;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="dot" />
        analyst
      </div>

      <button className="new-chat" onClick={onCreate}>
        <span className="plus">+</span> New chat
      </button>

      <nav className="chat-list">
        {chats.map((chat) => (
          <Row
            key={chat.id}
            chat={chat}
            active={chat.id === activeId}
            onSelect={() => onSelect(chat.id)}
            onRename={(title) => onRename(chat.id, title)}
            onDelete={() => onDelete(chat.id)}
          />
        ))}
        {chats.length === 0 && <p className="empty">No chats yet.</p>}
      </nav>

      <footer className="source" title={cubeUrl}>
        <span className="live-dot" />
        {cubeUrl.replace(/^https?:\/\//, "")}
      </footer>
    </aside>
  );
}
