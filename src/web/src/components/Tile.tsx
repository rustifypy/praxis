import { useMemo } from "react";

import { formatCell } from "../format";
import type { ChartSpec, TileChart, TileResultRow, TileRow } from "../types";
import { ResultChart } from "./ResultChart";

/**
 * One tile: a title, the window it read, and either its chart or its table.
 *
 * Reuses `ResultChart` with no table under it, which is only safe because every palette slot
 * clears 3:1 unaided — a light hue leaning on that table for contrast relief would not.
 *
 * The window label is `resolved_ranges`: the only thing distinguishing a tile whose window
 * moves on refresh from one pinned to a fixed period. Both are legal; a board where you
 * cannot tell them apart is not.
 */

function Delta({ change, percent }: { change: number; percent: number | null }) {
  if (change === 0) return <span className="dash-delta flat">no change</span>;
  const tone = change > 0 ? "up" : "down";
  // The arrow and the sign both carry direction, so colour is never the only cue.
  const arrow = change > 0 ? "▲" : "▼";
  const shown =
    percent === null
      ? `${change > 0 ? "+" : ""}${change.toLocaleString()}`
      : `${percent > 0 ? "+" : ""}${percent.toFixed(1)}%`;
  return (
    <span className={`dash-delta ${tone}`}>
      {arrow} {shown}
    </span>
  );
}

export function Tile({
  tile,
  result,
  broken,
  degraded,
  span,
  columns,
  onGrab,
  onRelease,
  onNudge,
  onSpan,
  captions,
}: {
  tile: TileRow;
  result?: TileResultRow;
  broken?: string[];
  degraded?: string;
  /** Grid columns this tile covers — not the date span below, which is `range`. */
  span: number;
  columns: number;
  onGrab: () => void;
  onRelease: () => void;
  onNudge: (delta: number) => void;
  onSpan: () => void;
  captions: boolean;
}) {
  const chart: TileChart = tile.chart;
  const rows = result?.rows ?? [];

  const spec: ChartSpec | null = useMemo(() => {
    if (chart.type === "table" || degraded || !rows.length) return null;
    return {
      type: chart.type,
      y: chart.y ?? [],
      x: chart.x ?? "",
      series: chart.series ?? "",
      target: chart.target ?? null,
    };
  }, [chart, degraded, rows.length]);

  const range = Object.values(result?.resolved_ranges ?? {})[0];
  const window = range ? `${range[0].split("T")[0]} → ${range[1].split("T")[0]}` : "";

  return (
    <section className="dash-tile">
      <header className="dash-tile-head">
        {/* The grip, not the tile, is what arms a drag — dragging the whole tile would take
            the chart's hover targets and text selection with it. */}
        <button
          className="dash-grip"
          aria-label={`Move ${tile.title}`}
          title="Drag to move, or focus and use the arrow keys"
          onMouseDown={onGrab}
          onMouseUp={onRelease}
          onKeyDown={(e) => {
            const delta =
              e.key === "ArrowLeft" || e.key === "ArrowUp"
                ? -1
                : e.key === "ArrowRight" || e.key === "ArrowDown"
                  ? 1
                  : 0;
            if (!delta) return;
            e.preventDefault();
            onNudge(delta);
          }}
        >
          <span aria-hidden="true">⠿</span>
        </button>
        <h3>{tile.title}</h3>
        {window && <span className="dash-tile-window">{window}</span>}
        {columns > 1 && (
          <button
            className="dash-span"
            onClick={onSpan}
            aria-label={`${tile.title} covers ${span} of ${columns} columns — change`}
            title="Width — click to change"
          >
            {span}/{columns}
          </button>
        )}
      </header>

      {broken?.length ? (
        <div className="dash-tile-broken">
          <strong>This tile no longer compiles.</strong>
          {broken.map((e, i) => (
            <div key={i}>{e}</div>
          ))}
        </div>
      ) : !result ? (
        <div className="dash-waiting">
          <div className="dash-skeleton" aria-hidden="true">
            <span />
            <span />
            <span />
            <span />
            <span />
          </div>
          <p className="dash-tile-empty">Not refreshed yet — press Refresh to load it.</p>
        </div>
      ) : result.pruned ? (
        <p className="dash-tile-empty">
          {result.row_count.toLocaleString()} rows, since pruned. Refresh to load them again.
        </p>
      ) : !rows.length ? (
        <p className="dash-tile-empty">No data matched.</p>
      ) : chart.type === "kpi" ? (
        <div className="dash-kpi-row">
          {(chart.y ?? []).map((id) => {
            const column = result.columns.find((c) => c.id === id);
            const change = result.deltas.find((d) => d.column === id);
            return (
              <div key={id} className="dash-kpi">
                <span className="dash-kpi-value">
                  {column ? formatCell(rows[0][id], column) : String(rows[0][id] ?? "")}
                </span>
                <span className="dash-kpi-label">{column?.label ?? id}</span>
                {change && <Delta change={change.change} percent={change.percent} />}
              </div>
            );
          })}
        </div>
      ) : spec ? (
        <div className="dash-tile-chart">
          <ResultChart spec={spec} columns={result.columns} rows={rows} />
        </div>
      ) : (
        <div className="dash-tile-table">
          <table>
            <thead>
              <tr>
                {result.columns.map((c) => (
                  <th key={c.id} className={c.align}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 12).map((row, i) => (
                <tr key={i}>
                  {result.columns.map((c) => (
                    <td key={c.id} className={c.align}>
                      {formatCell(row[c.id], c)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > 12 && (
            <p className="dash-tile-more">
              showing 12 of {result.row_count.toLocaleString()} rows
            </p>
          )}
        </div>
      )}

      {captions && result?.insight && <p className="dash-caption">{result.insight}</p>}

      {degraded && <p className="dash-tile-note">Drawn as a table: {degraded}.</p>}
    </section>
  );
}
