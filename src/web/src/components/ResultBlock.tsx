import { useMemo, useState } from "react";

import * as api from "../api";
import { compareCells, formatCell } from "../format";
import type { ChartSpec, ResultColumn } from "../types";
import { Button } from "./Button";
import { ResultChart } from "./ResultChart";

/**
 * A query's data. Renders from the rows the event carried; `resultId` fetches the rest only on
 * request. Sorting covers loaded rows only, and says so.
 */

type Sort = { id: string; dir: "asc" | "desc" };

export function ResultBlock({
  resultId,
  label,
  columns,
  rows,
  rowCount,
  chart,
}: {
  resultId: string;
  label: string;
  columns: ResultColumn[];
  rows: Record<string, unknown>[];
  rowCount: number;
  chart: ChartSpec | null;
}) {
  const [loaded, setLoaded] = useState<Record<string, unknown>[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState("");
  const [sort, setSort] = useState<Sort | null>(null);

  const shown = loaded ?? rows;
  const held = rowCount - shown.length;

  const ordered = useMemo(() => {
    if (!sort) return shown;
    const column = columns.find((c) => c.id === sort.id);
    if (!column) return shown;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...shown].sort((a, b) => sign * compareCells(a[column.id], b[column.id], column));
  }, [shown, sort, columns]);

  async function showAll() {
    setLoading(true);
    setFailed("");
    try {
      const page = await api.resultPage(resultId, 0, rowCount);
      setLoaded(page.rows);
    } catch (err) {
      setFailed(String(err));
    } finally {
      setLoading(false);
    }
  }

  function toggle(id: string) {
    setSort((prev) => {
      if (prev?.id !== id) return { id, dir: "desc" };
      // Third click clears it, restoring the query's own order that the answer describes.
      return prev.dir === "desc" ? { id, dir: "asc" } : null;
    });
  }

  return (
    <div className="res">
      <div className="res-head">
        <span className="res-label">{label}</span>
        <span className="res-count">
          {shown.length.toLocaleString()}
          {held > 0 && ` of ${rowCount.toLocaleString()}`} row{rowCount === 1 ? "" : "s"}
        </span>
        {held > 0 && resultId && (
          <Button className="res-more" onClick={showAll} disabled={loading}>
            {loading ? "Loading…" : `Show all ${rowCount.toLocaleString()}`}
          </Button>
        )}
        {resultId && (
          <a className="res-csv" href={api.resultCsvUrl(resultId)} download>
            CSV
          </a>
        )}
      </div>

      {failed && <p className="res-failed">{failed}</p>}

      {/* From the event's rows, never `loaded`: the server chose the chart to fit those. */}
      {chart && <ResultChart spec={chart} columns={columns} rows={rows} />}

      {/* A kpi/gauge already shows exact values, so its table would repeat them. */}
      {chart?.type !== "kpi" && chart?.type !== "gauge" && (
        <div className="res-scroll">
          <table className="res-table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th
                    key={c.id}
                    className={c.align === "right" ? "res-num" : undefined}
                    aria-sort={
                      sort?.id === c.id ? (sort.dir === "asc" ? "ascending" : "descending") : "none"
                    }
                  >
                    <Button className="res-sort" onClick={() => toggle(c.id)} title={c.id}>
                      {c.label}
                      <span className="res-arrow">
                        {sort?.id === c.id ? (sort.dir === "asc" ? "↑" : "↓") : ""}
                      </span>
                    </Button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ordered.map((row, i) => (
                <tr key={i}>
                  {columns.map((c) => (
                    <td key={c.id} className={c.align === "right" ? "res-num" : undefined}>
                      {formatCell(row[c.id], c)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {held > 0 && sort && (
        <p className="res-note">
          Sorted over the {shown.length.toLocaleString()} rows loaded, not all{" "}
          {rowCount.toLocaleString()}.
        </p>
      )}
    </div>
  );
}
