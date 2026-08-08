import { useEffect, useMemo, useRef, useState } from "react";

import { ago, dayGroup } from "../hooks";
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

/**
 * The chat mode's whole sidebar.
 *
 * Grouped by day and searchable, because the list only grows: `chats.map` is fine at ten
 * and unusable at two hundred, and until now there was no way to find an old conversation
 * except scrolling for its title.
 */
export function ChatNav({
  chats,
  activeId,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: {
  chats: Chat[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}) {
  const [filter, setFilter] = useState("");

  // Grouped in the order the server sent, which is newest first — so the buckets come
  // out chronological without a second sort.
  const groups = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const matching = needle
      ? chats.filter((chat) => chat.title.toLowerCase().includes(needle))
      : chats;
    const out: { label: string; chats: Chat[] }[] = [];
    for (const chat of matching) {
      const label = dayGroup(chat.updated_at);
      const last = out[out.length - 1];
      if (last?.label === label) last.chats.push(chat);
      else out.push({ label, chats: [chat] });
    }
    return out;
  }, [chats, filter]);

  return (
    <aside className="nav">
      <header className="nav-head">
        <h2>Chats</h2>
        <button className="nav-add" onClick={onCreate} title="New chat  ⌘K" aria-label="New chat">
          +
        </button>
      </header>

      {chats.length > 4 && (
        <input
          className="nav-filter"
          placeholder="Search chats"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      )}

      <div className="nav-list">
        {groups.map((group) => (
          <div key={group.label} className="chat-group">
            <h3>{group.label}</h3>
            {group.chats.map((chat) => (
              <Row
                key={chat.id}
                chat={chat}
                active={chat.id === activeId}
                onSelect={() => onSelect(chat.id)}
                onRename={(title) => onRename(chat.id, title)}
                onDelete={() => onDelete(chat.id)}
              />
            ))}
          </div>
        ))}

        {chats.length === 0 && <p className="empty">No chats yet.</p>}
        {chats.length > 0 && groups.length === 0 && (
          <p className="empty">Nothing matches “{filter}”.</p>
        )}
      </div>
    </aside>
  );
}
