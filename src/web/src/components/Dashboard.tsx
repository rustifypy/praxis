import { useCallback, useEffect, useState } from "react";

import * as api from "../api";
import { ago } from "../hooks";
import type { DashboardDetail, RefreshOutcome } from "../types";
import { Tile } from "./Tile";

/**
 * One dashboard: the grid, the summary paragraph, and the only Refresh button.
 *
 * No per-tile progress, because the refresh is atomic — showing tiles land one by one would
 * display the partial state a refusal exists to prevent. A refusal is one banner, and the
 * tiles keep the numbers they already had rather than blanking.
 *
 * Three banners kept separate because they fail separately: wrong active source, tiles that
 * no longer compile, and a container that is down (which says nothing about the other two).
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
    void load();
  }, [load]);

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
  const placed = detail.layout.items;
  const byId = new Map(detail.tiles.map((t) => [t.id, t]));

  return (
    <div className="dash">
      <header className="dash-head">
        <div className="dash-title">
          <h2>{detail.title}</h2>
          <span className="dash-meta">
            {detail.source_name} · {detail.tiles.length} tiles ·{" "}
            {detail.run ? `refreshed ${ago(detail.run.started_at)}` : "never refreshed"}
          </span>
        </div>
        <div className="dash-actions">
          <button
            className="primary"
            onClick={() => void refresh()}
            disabled={busy || Boolean(detail.mismatch) || detail.tiles.length === 0}
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

      {detail.mismatch && <div className="dash-banner warn">{detail.mismatch}</div>}

      {!detail.checks.available && (
        <div className="dash-banner muted">
          Cannot check the tiles right now — {detail.checks.reason}. The numbers below are from
          the last refresh.
        </div>
      )}

      {detail.checks.broken.length > 0 && (
        <div className="dash-banner bad">
          {detail.checks.broken.length} tile
          {detail.checks.broken.length === 1 ? "" : "s"} no longer compile against this
          source's model, so the whole dashboard cannot be refreshed until they are fixed or
          removed. Ask in chat to fix them.
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

      {detail.run?.insight && (
        <p className="dash-insight">
          {detail.run.insight}
          <span className="dash-insight-when"> — as of {ago(detail.run.started_at)}</span>
        </p>
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
          style={{ gridTemplateColumns: `repeat(${detail.layout.columns}, minmax(0, 1fr))` }}
        >
          {placed.map((item) => {
            const tile = byId.get(item.tile);
            // A layout version can name a tile deleted since. Skipped rather than treated as
            // an error — the stored version was valid when it was written.
            if (!tile) return null;
            return (
              <div
                key={item.tile}
                className="dash-cell"
                style={{ gridColumn: `span ${Math.min(item.span, detail.layout.columns)}` }}
              >
                <Tile
                  tile={tile}
                  result={detail.results[tile.id]}
                  broken={brokenBy.get(tile.id)}
                  degraded={detail.results[tile.id]?.degraded}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
