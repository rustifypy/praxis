import { useCallback, useEffect, useState } from "react";

import * as api from "../api";
import { ago } from "../hooks";
import type { DashboardDetail, LayoutItem, RefreshOutcome } from "../types";
import { Tile } from "./Tile";

/**
 * One dashboard: the grid, the summary paragraph, and the only Refresh button.
 *
 * No per-tile progress, because the refresh is atomic — showing tiles land one by one would
 * display the partial state a refusal exists to prevent. A refusal is one banner, and the
 * tiles keep the numbers they already had rather than blanking.
 *
 * Two banners kept separate because they fail separately: tiles that no longer compile, and a
 * container that is down (which says nothing about the first).
 *
 * Rearranging goes through `POST /layout`, the same validator the agent's `set_layout` uses,
 * and each drop is its own version — `layouts` is append-only, so the history is the undo.
 */

export function Dashboard({
  id,
  onRenamed,
  onDeleted,
}: {
  id: string;
  onRenamed: () => void;
  onDeleted: () => void;
}) {
  const [detail, setDetail] = useState<DashboardDetail | null>(null);
  const [failed, setFailed] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<RefreshOutcome | null>(null);
  const [items, setItems] = useState<LayoutItem[]>([]);
  const [columns, setColumns] = useState(2);
  const [refused, setRefused] = useState("");
  const [undo, setUndo] = useState<{ columns: number; items: LayoutItem[] } | null>(null);
  /** The tile whose grip is held. A cell is draggable only while it is armed, so a chart
   *  keeps its hover targets and the text in a tile stays selectable. */
  const [armed, setArmed] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  // A display preference, so it belongs neither on the board row nor in the hash — one is
  // shared state and the other is navigation.
  const [captions, setCaptions] = useState(
    () => localStorage.getItem("dash-captions") !== "off",
  );

  const load = useCallback(async () => {
    setFailed("");
    try {
      setDetail(await api.dashboard(id));
    } catch (err) {
      setFailed(String(err));
    }
  }, [id]);

  useEffect(() => {
    setOutcome(null);
    setUndo(null);
    void load();
  }, [load]);

  // The server's layout is the truth; the local copy exists so a drop lands before the round
  // trip. A tile deleted since the layout was written is dropped, since naming it would be
  // rejected by the validator.
  useEffect(() => {
    if (!detail) return;
    const live = new Set(detail.tiles.map((t) => t.id));
    setItems(detail.layout.items.filter((i) => live.has(i.tile)));
    setColumns(detail.layout.columns);
  }, [detail]);

  async function refresh() {
    setBusy(true);
    setOutcome(null);
    try {
      const result = await api.refreshDashboard(id);
      setOutcome(result);
      // Reload either way: a refused run wrote nothing, but the checks and the last run's
      // numbers are still what should be on screen afterwards.
      await load();
    } catch (err) {
      setFailed(String(err));
    } finally {
      setBusy(false);
    }
  }

  if (failed) return <div className="dash-fail">{failed}</div>;
  if (!detail) return <div className="dash-fail muted">Loading…</div>;

  const brokenBy = new Map(detail.checks.broken.map((b) => [b.tile_id, b.errors]));
  const byId = new Map(detail.tiles.map((t) => [t.id, t]));
  const name = (tile: string) => byId.get(tile)?.title ?? "a tile";

  async function save(next: LayoutItem[], width: number, note: string, remember = true) {
    const before = { columns, items };
    setItems(next);
    setColumns(width);
    setRefused("");
    try {
      await api.saveLayout(id, { columns: width, items: next }, note);
      setUndo(remember ? before : null);
    } catch (err) {
      setItems(before.items);
      setColumns(before.columns);
      setRefused(`Layout not saved — ${err}`);
    }
  }


  /** Dragging down lands after the target, dragging up lands before it — the asymmetry falls
   *  out of splicing at an index taken before the removal, and is what a list reorder does. */
  function reorder(from: string, to: string) {
    const at = items.findIndex((i) => i.tile === from);
    const target = items.findIndex((i) => i.tile === to);
    if (at < 0 || target < 0 || at === target) return;
    const next = [...items];
    next.splice(target, 0, next.splice(at, 1)[0]);
    void save(next, columns, `moved ${name(from)}`);
  }

  function nudge(tile: string, delta: number) {
    const at = items.findIndex((i) => i.tile === tile);
    const target = at + delta;
    if (at < 0 || target < 0 || target >= items.length) return;
    const next = [...items];
    next.splice(target, 0, next.splice(at, 1)[0]);
    void save(next, columns, `moved ${name(tile)}`);
  }

  function cycleSpan(tile: string) {
    const current = items.find((i) => i.tile === tile);
    if (!current) return;
    const span = (current.span % columns) + 1;
    const next = items.map((i) => (i.tile === tile ? { ...i, span } : i));
    void save(next, columns, `${name(tile)} across ${span} of ${columns}`);
  }

  function setWidth(width: number) {
    // Clamped here rather than left to the server: a span wider than the board is the one
    // rejection narrowing the grid would otherwise cause every time.
    void save(
      items.map((i) => ({ ...i, span: Math.min(i.span, width) })),
      width,
      `${width} column${width === 1 ? "" : "s"}`,
    );
  }

  return (
    <div className="dash">
      <header className="dash-head">
        <div className="dash-title">
          <h2>{detail.title}</h2>
          <span className="dash-meta">
            {detail.tiles.length} tiles ·{" "}
            {detail.run ? `refreshed ${ago(detail.run.started_at)}` : "never refreshed"}
          </span>
        </div>
        <div className="dash-actions">
          {undo && (
            <button
              className="dash-undo"
              onClick={() => void save(undo.items, undo.columns, "undo", false)}
            >
              Undo
            </button>
          )}
          {detail.tiles.length > 0 && (
            <select
              className="dash-select"
              value={columns}
              aria-label="Columns"
              onChange={(e) => setWidth(Number(e.target.value))}
            >
              {Array.from({ length: detail.max_columns }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  {n} column{n === 1 ? "" : "s"}
                </option>
              ))}
            </select>
          )}
          {detail.tiles.length > 0 && (
            <button
              className="dash-toggle"
              aria-pressed={captions}
              title="Show or hide the one-line caption under each tile"
              onClick={() => {
                const next = !captions;
                setCaptions(next);
                localStorage.setItem("dash-captions", next ? "on" : "off");
              }}
            >
              Captions
            </button>
          )}
          <button
            className="primary"
            onClick={() => void refresh()}
            disabled={busy || detail.tiles.length === 0}
          >
            {busy ? "Refreshing…" : "Refresh"}
          </button>
          <button
            onClick={async () => {
              const title = window.prompt("Rename dashboard", detail.title);
              if (!title) return;
              await api.renameDashboard(id, title);
              onRenamed();
              await load();
            }}
          >
            Rename
          </button>
          <button
            className="danger"
            onClick={async () => {
              if (!window.confirm(`Delete ${detail.title}? Its tiles go with it.`)) return;
              await api.deleteDashboard(id);
              onDeleted();
            }}
          >
            Delete
          </button>
        </div>
      </header>

      {refused && <div className="dash-banner bad">{refused}</div>}

      {!detail.checks.available && (
        <div className="dash-banner muted">
          Cannot check the tiles right now — {detail.checks.reason}. The numbers below are from
          the last refresh.
        </div>
      )}

      {detail.checks.broken.length > 0 && (
        <div className="dash-banner bad">
          {detail.checks.broken.length} tile
          {detail.checks.broken.length === 1 ? "" : "s"} no longer compile against the served
          model, so the whole dashboard cannot be refreshed until they are fixed or removed.
          Ask in chat to fix them.
        </div>
      )}

      {outcome && !outcome.ok && (
        <div className="dash-banner bad">
          <strong>Refresh refused — nothing was executed.</strong>
          {outcome.error && <div>{outcome.error}</div>}
          {outcome.tiles
            .filter((t) => !t.ok)
            .map((t) => (
              <div key={t.tile_id}>
                {t.title}: {t.errors.join("; ")}
              </div>
            ))}
        </div>
      )}

      {busy && <div className="dash-progress" role="status" aria-label="Refreshing" />}

      {detail.run?.insight && (
        <section className="dash-insight">
          <header className="dash-insight-head">
            <span className="dash-insight-tag">Summary</span>
            {/* An insight is a claim about numbers, so one with no "as of" invites being
                quoted long after the data moved. */}
            <span className="dash-insight-when">as of {ago(detail.run.started_at)}</span>
          </header>
          <p>{detail.run.insight}</p>
        </section>
      )}

      {detail.tiles.length === 0 ? (
        <div className="dash-empty">
          <h3>No tiles yet</h3>
          <p>
            Ask in chat: run a query you want to keep, then say “save that to the{" "}
            {detail.title} dashboard”.
          </p>
        </div>
      ) : (
        <div
          className="dash-grid"
          style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
        >
          {items.map((item) => {
            const tile = byId.get(item.tile);
            // A layout version can name a tile deleted since. Skipped rather than treated as
            // an error — the stored version was valid when it was written.
            if (!tile) return null;
            return (
              <div
                key={item.tile}
                className={`dash-cell${armed === item.tile ? " lifted" : ""}${
                  over === item.tile ? " over" : ""
                }`}
                style={{ gridColumn: `span ${Math.min(item.span, columns)}` }}
                draggable={armed === item.tile}
                onDragStart={(e) => {
                  // Firefox will not start a drag without payload, even though the tile being
                  // dragged is already in `armed`.
                  e.dataTransfer.effectAllowed = "move";
                  e.dataTransfer.setData("text/plain", item.tile);
                }}
                onDragEnd={() => {
                  setArmed(null);
                  setOver(null);
                }}
                onDragOver={(e) => {
                  // Guarded on `armed` so a file dragged over the page never lights a tile up.
                  if (!armed || armed === item.tile) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                  setOver(item.tile);
                }}
                onDragLeave={() => setOver((cur) => (cur === item.tile ? null : cur))}
                onDrop={(e) => {
                  e.preventDefault();
                  const from = armed;
                  setArmed(null);
                  setOver(null);
                  if (from) reorder(from, item.tile);
                }}
              >
                <Tile
                  tile={tile}
                  result={detail.results[tile.id]}
                  broken={brokenBy.get(tile.id)}
                  degraded={detail.results[tile.id]?.degraded}
                  span={item.span}
                  columns={columns}
                  onGrab={() => setArmed(item.tile)}
                  onRelease={() => setArmed((cur) => (cur === item.tile ? null : cur))}
                  onNudge={(delta) => nudge(item.tile, delta)}
                  onSpan={() => cycleSpan(item.tile)}
                  captions={captions}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
