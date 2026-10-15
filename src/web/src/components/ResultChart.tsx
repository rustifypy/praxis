import { useMemo, useState } from "react";

import { formatCell, formatTick } from "../format";
import type { ChartSpec, ResultColumn } from "../types";

/**
 * The chart for a result, as hand-rolled SVG: the spec is decided in Python, so the browser only
 * draws it, and the marks use the app's own series tokens in both themes.
 */

/** Logical canvas. The SVG scales to its container, so these are ratios, not pixels. */
const W = 720;
const H = 240;
const PAD = { l: 58, r: 14, t: 14, b: 30 };
const PLOT_W = W - PAD.l - PAD.r;
const PLOT_H = H - PAD.t - PAD.b;

const BAR_MAX = 24;
const CORNER = 4;
/** Room for roughly this many x labels before they touch; above it, labels are thinned. */
const MAX_TICKS = 9;
/** Approximate width of one 11px tick character, to fit labels without measuring text. */
const CHAR_W = 6;
/** Below this many characters a truncated label says nothing, so thin instead. */
const MIN_CHARS = 5;
/** Points a line may have and still carry a marker on each without them crowding. */
const MAX_MARKERS = 12;
const SEGMENT_GAP = 2;

const CONTINUOUS = ["line", "area", "stacked_area"];
const BARS = ["bar", "stacked_bar", "grouped_bar"];
const STACKED = ["stacked_area", "stacked_bar"];

type Point = { x: string; value: number | null };
type Series = { key: string; label: string; slot: number; points: Point[] };

function niceStep(raw: number): number {
  const magnitude = 10 ** Math.floor(Math.log10(Math.abs(raw) || 1));
  const normal = raw / magnitude;
  return (normal <= 1 ? 1 : normal <= 2 ? 2 : normal <= 5 ? 5 : 10) * magnitude;
}

/** Gridlines on round numbers, spanning zero — a bar chart that does not start at zero
 *  misstates every comparison on it. */
function niceTicks(min: number, max: number, count = 4) {
  const low = Math.min(0, Number.isFinite(min) ? min : 0);
  const high = Math.max(0, Number.isFinite(max) ? max : 0);
  const step = niceStep((high - low || 1) / count);
  const lo = Math.floor(low / step) * step;
  const hi = Math.max(Math.ceil(high / step) * step, lo + step);
  const steps = Math.round((hi - lo) / step);
  // By index, not accumulation: summing 0.1 four times gives 0.4000000001.
  return { lo, hi, ticks: Array.from({ length: steps + 1 }, (_, i) => lo + i * step) };
}

function numeric(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function barPath(x: number, w: number, from: number, to: number): string {
  const up = to <= from;
  const height = Math.abs(to - from);
  const r = Math.min(CORNER, height, w / 2);
  const top = Math.min(from, to);
  const bottom = Math.max(from, to);
  if (up) {
    return `M${x},${bottom} L${x},${top + r} Q${x},${top} ${x + r},${top} L${x + w - r},${top} Q${x + w},${top} ${x + w},${top + r} L${x + w},${bottom} Z`;
  }
  return `M${x},${top} L${x},${bottom - r} Q${x},${bottom} ${x + r},${bottom} L${x + w - r},${bottom} Q${x + w},${bottom} ${x + w},${bottom - r} L${x + w},${top} Z`;
}

export function ResultChart({ spec, columns, rows }: ChartProps) {
  const [active, setActive] = useState<number | null>(null);
  const byId = useMemo(() => new Map(columns.map((c) => [c.id, c])), [columns]);

  const measure = byId.get(spec.y[0]);
  const axis = byId.get(spec.x);
  const seriesColumn = spec.series ? byId.get(spec.series) : undefined;

  const model = useMemo(() => {
    if (!measure || !axis) return null;

    // A line's x must be sorted (ISO strings sort chronologically); a bar keeps the query's
    // row order, which is the point of a top-N.
    const seen: string[] = [];
    for (const row of rows) {
      const key = String(row[axis.id] ?? "");
      if (!seen.includes(key)) seen.push(key);
    }
    const xs = CONTINUOUS.includes(spec.type) ? [...seen].sort() : seen;

    if (!seriesColumn) {
      const values = new Map(rows.map((r) => [String(r[axis.id] ?? ""), numeric(r[measure.id])]));
      return {
        xs,
        series: [
          {
            key: measure.id,
            label: measure.label,
            slot: 1,
            points: xs.map((x) => ({ x, value: values.get(x) ?? null })),
          },
        ] as Series[],
      };
    }

    const keys: string[] = [];
    for (const row of rows) {
      const key = String(row[seriesColumn.id] ?? "");
      if (!keys.includes(key)) keys.push(key);
    }
    const cell = new Map<string, number | null>();
    for (const row of rows) {
      cell.set(
        `${String(row[axis.id] ?? "")}\0${String(row[seriesColumn.id] ?? "")}`,
        numeric(row[measure.id]),
      );
    }
    return {
      xs,
      series: keys.map((key, i) => ({
        key,
        label: formatCell(key, seriesColumn),
        // Never cycled: `chart_for` caps series at the palette's eight.
        slot: i + 1,
        points: xs.map((x) => ({ x, value: cell.get(`${x}\0${key}`) ?? null })),
      })) as Series[],
    };
  }, [rows, spec.type, axis, measure, seriesColumn]);

  if (spec.type === "kpi") return <Kpi spec={spec} columns={columns} rows={rows} />;
  if (spec.type === "stat") return <Stats spec={spec} columns={columns} rows={rows} />;
  if (spec.type === "gauge") return <Gauge spec={spec} columns={columns} rows={rows} />;
  if (spec.type === "sparkline") return <Bare spec={spec} columns={columns} rows={rows} />;
  if (spec.type === "donut") return <Donut spec={spec} columns={columns} rows={rows} />;
  if (spec.type === "hbar") return <HBar spec={spec} columns={columns} rows={rows} />;
  if (spec.type === "scatter") return <Scatter spec={spec} columns={columns} rows={rows} />;
  if (spec.type === "heatmap") return <Heatmap spec={spec} columns={columns} rows={rows} />;
  if (!model || !measure || !axis || model.xs.length === 0) return null;

  const { xs, series } = model;
  const continuous = CONTINUOUS.includes(spec.type);
  const bars = BARS.includes(spec.type);
  const banded = spec.type === "area" || spec.type === "stacked_area";

  const stacks = STACKED.includes(spec.type)
    ? series.reduce<number[][]>((acc, s) => {
        const under = acc.length ? acc[acc.length - 1] : xs.map(() => 0);
        acc.push(s.points.map((p, i) => under[i] + (p.value ?? 0)));
        return acc;
      }, [])
    : null;

  const flat: number[] = stacks
    ? stacks[stacks.length - 1]
    : (series.flatMap((s) => s.points.map((p) => p.value)).filter((v) => v !== null) as number[]);
  const { lo, hi, ticks } = niceTicks(Math.min(...flat), Math.max(...flat));

  const y = (value: number) => PAD.t + PLOT_H - ((value - lo) / (hi - lo)) * PLOT_H;
  const band = PLOT_W / xs.length;
  // A line's points sit on the gridline; a bar's sit in the middle of its band.
  const x = (i: number) =>
    continuous
      ? PAD.l + (xs.length === 1 ? PLOT_W / 2 : (i / (xs.length - 1)) * PLOT_W)
      : PAD.l + band * (i + 0.5);

  const baseline = y(Math.max(lo, Math.min(0, hi)));

  // A bar's label names that bar, so bars truncate every label to its band rather than thin
  // them; a time axis thins. Bars thin too only when a band can't fit a useful prefix.
  const fit = Math.floor(band / CHAR_W);
  const truncate = bars && fit >= MIN_CHARS;
  const every = truncate ? 1 : Math.ceil(xs.length / MAX_TICKS);

  return (
    <div className="res-chart">
      {series.length > 1 && (
        <ul className="res-legend">
          {series.map((s) => (
            <li key={s.key}>
              <span className="res-key" style={{ background: `var(--series-${s.slot})` }} />
              {s.label}
            </li>
          ))}
        </ul>
      )}

      <div className="res-plot">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${measure.label} by ${axis.label}`}>
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                className="res-grid"
                x1={PAD.l}
                x2={W - PAD.r}
                y1={y(tick)}
                y2={y(tick)}
              />
              <text className="res-tick res-tick-y" x={PAD.l - 8} y={y(tick) + 4}>
                {formatTick(tick, measure)}
              </text>
            </g>
          ))}

          {xs.map((label, i) => {
            if (i % every !== 0) return null;
            const text = formatCell(label, axis);
            const shown = truncate && text.length > fit ? `${text.slice(0, fit - 1)}…` : text;
            return (
              <text className="res-tick" key={label} x={x(i)} y={H - 10} textAnchor="middle">
                {shown}
              </text>
            );
          })}

          {spec.type === "grouped_bar"
            ? xs.map((label, i) => {
                const slotW = Math.min(BAR_MAX, Math.max(2, (band - 4) / series.length));
                const groupW = slotW * series.length;
                return series.map((s, si) => {
                  const value = s.points[i].value;
                  if (value === null) return null;
                  return (
                    <path
                      key={`${label} ${s.key}`}
                      className="res-bar"
                      d={barPath(
                        x(i) - groupW / 2 + si * slotW,
                        Math.max(1, slotW - 2),
                        baseline,
                        y(value),
                      )}
                      style={{ fill: `var(--series-${s.slot})` }}
                      opacity={active === null || active === i ? 1 : 0.45}
                    />
                  );
                });
              })
            : spec.type === "stacked_bar" && stacks
              ? xs.map((label, i) => {
                  const width = Math.min(BAR_MAX, Math.max(2, band - 2));
                  return series.map((s, si) => {
                    if (!s.points[i].value) return null;
                    const top = y(stacks[si][i]);
                    const bottom = y(si === 0 ? 0 : stacks[si - 1][i]);
                    return (
                      <rect
                        key={`${label} ${s.key}`}
                        className="res-bar"
                        x={x(i) - width / 2}
                        y={top}
                        width={width}
                        height={Math.max(0, bottom - top - (si === 0 ? 0 : SEGMENT_GAP))}
                        style={{ fill: `var(--series-${s.slot})` }}
                        opacity={active === null || active === i ? 1 : 0.45}
                      />
                    );
                  });
                })
              : bars
                ? xs.map((label, i) => {
                    const value = series[0].points[i].value;
                    if (value === null) return null;
                    // Capped, not fitted: the leftover air separates bars without a stroke.
                    const width = Math.min(BAR_MAX, Math.max(2, band - 2));
                    return (
                      <path
                        key={label}
                        className="res-bar"
                        d={barPath(x(i) - width / 2, width, baseline, y(value))}
                        style={{ fill: "var(--series-1)" }}
                        opacity={active === null || active === i ? 1 : 0.45}
                      />
                    );
                  })
                : series.map((s, si) => (
                    <g key={s.key}>
                      {banded && (
                        <path
                          className="res-band"
                          d={bandPath(
                            stacks && si > 0 ? stacks[si - 1] : null,
                            stacks ? stacks[si] : s.points.map((p) => p.value),
                            x,
                            y,
                            baseline,
                          )}
                          style={{ fill: `var(--series-${s.slot})` }}
                        />
                      )}
                      <path
                        className="res-line"
                        d={stacks ? ridgePath(stacks[si], x, y) : linePath(s.points, x, y)}
                        style={{ stroke: `var(--series-${s.slot})` }}
                      />
                      {/* Markers only while short: a bare two-point line reads as a stray
                          rule, and past a dozen they crowd. */}
                      {!stacks &&
                        xs.length <= MAX_MARKERS &&
                        s.points.map((point, i) =>
                          point.value === null ? null : (
                            <circle
                              key={point.x}
                              className="res-dot"
                              cx={x(i)}
                              cy={y(point.value)}
                              r={4}
                              style={{ fill: `var(--series-${s.slot})` }}
                            />
                          ),
                        )}
                    </g>
                  ))}

          <line className="res-axis" x1={PAD.l} x2={W - PAD.r} y1={baseline} y2={baseline} />

          {active !== null && continuous && (
            <g>
              <line
                className="res-cross"
                x1={x(active)}
                x2={x(active)}
                y1={PAD.t}
                y2={PAD.t + PLOT_H}
              />
              {series.map((s) =>
                s.points[active].value === null ? null : (
                  <circle
                    key={s.key}
                    className="res-dot"
                    cx={x(active)}
                    cy={y(s.points[active].value as number)}
                    r={4.5}
                    style={{ fill: `var(--series-${s.slot})` }}
                  />
                ),
              )}
            </g>
          )}

          {/* Full-height hit target per band: a 2px line or 8px dot is too small to land on. */}
          {xs.map((label, i) => (
            <rect
              key={label}
              className="res-hit"
              x={continuous ? x(i) - band / 2 : PAD.l + band * i}
              y={PAD.t}
              width={band}
              height={PLOT_H}
              onMouseEnter={() => setActive(i)}
              onMouseLeave={() => setActive(null)}
            />
          ))}
        </svg>

        {active !== null && (
          <div
            className="res-tip"
            style={{ left: `${Math.min(88, Math.max(12, (x(active) / W) * 100))}%` }}
          >
            <div className="res-tip-x">{formatCell(xs[active], axis)}</div>
            {series.map((s) => (
              <div key={s.key} className="res-tip-row">
                <span className="res-key" style={{ background: `var(--series-${s.slot})` }} />
                <span className="res-tip-name">{s.label}</span>
                <span className="res-tip-value">
                  {s.points[active].value === null
                    ? "—"
                    : formatCell(s.points[active].value, measure)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ridgePath(values: number[], x: (i: number) => number, y: (v: number) => number): string {
  return `M${values.map((v, i) => `${x(i)},${y(v)}`).join(" L")}`;
}

function bandPath(
  under: number[] | null,
  over: (number | null)[],
  x: (i: number) => number,
  y: (v: number) => number,
  baseline: number,
): string {
  const top = over.map((v, i) => `${x(i)},${v === null ? baseline : y(v)}`);
  const bottom = over.map((_, i) => `${x(i)},${under ? y(under[i]) : baseline}`).reverse();
  return `M${top.join(" L")} L${bottom.join(" L")} Z`;
}

/** Move-to across gaps rather than through them: a missing month is a break in the line,
 *  not a straight segment implying values that were never fetched. */
function linePath(
  points: Point[],
  x: (i: number) => number,
  y: (value: number) => number,
): string {
  let path = "";
  let open = false;
  points.forEach((point, i) => {
    if (point.value === null) {
      open = false;
      return;
    }
    path += `${open ? "L" : "M"}${x(i)},${y(point.value)} `;
    open = true;
  });
  return path.trim();
}

const SPARK_W = 150;
const SPARK_H = 30;
const SPARK_PAD = 3;

function sparkPath(values: number[], w = SPARK_W, h = SPARK_H): { line: string; area: string } {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const step = values.length > 1 ? w / (values.length - 1) : 0;
  const y = (v: number) => h - SPARK_PAD - ((v - lo) / span) * (h - SPARK_PAD * 2);
  const points = values.map((v, i) => `${i * step},${y(v)}`);
  return {
    line: `M${points.join(" L")}`,
    area: `M0,${h} L${points.join(" L")} L${(values.length - 1) * step},${h} Z`,
  };
}

function Stat({
  column,
  slot,
  labels,
  values,
}: {
  column: ResultColumn;
  slot: number;
  labels: string[];
  values: (number | null)[];
}) {
  const real = values.filter((v): v is number => v !== null);
  if (!real.length) return null;

  const latest = real[real.length - 1];
  const previous = real.length > 1 ? real[real.length - 2] : null;
  const change = previous === null ? null : latest - previous;
  const percent =
    change === null || previous === null || previous === 0
      ? null
      : (change / Math.abs(previous)) * 100;
  const spark = real.length > 1 ? sparkPath(real) : null;

  return (
    <div className="res-stat">
      <span className="res-stat-label">{column.label}</span>
      <div className="res-stat-figure">
        <span className="res-stat-value">{formatCell(latest, column)}</span>
        {change !== null && (
          <span
            className={`res-stat-delta ${change > 0 ? "up" : change < 0 ? "down" : "flat"}`}
            title={`${labels[labels.length - 2]} → ${labels[labels.length - 1]}`}
          >
            {change === 0
              ? "no change"
              : `${change > 0 ? "▲" : "▼"} ${
                  percent === null
                    ? `${change > 0 ? "+" : ""}${change.toLocaleString()}`
                    : `${percent > 0 ? "+" : ""}${percent.toFixed(1)}%`
                }`}
          </span>
        )}
      </div>
      {spark && (
        <svg
          className="res-spark"
          viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`${column.label} trend`}
        >
          <path className="res-spark-area" d={spark.area} style={{ fill: `var(--series-${slot})` }} />
          <path className="res-spark-line" d={spark.line} style={{ stroke: `var(--series-${slot})` }} />
        </svg>
      )}
    </div>
  );
}

function Stats({
  spec,
  columns,
  rows,
}: {
  spec: ChartSpec;
  columns: ResultColumn[];
  rows: Record<string, unknown>[];
}) {
  const byId = new Map(columns.map((c) => [c.id, c]));
  const axis = byId.get(spec.x);
  const measures = spec.y.map((id) => byId.get(id)).filter((c): c is ResultColumn => Boolean(c));
  if (!axis || !measures.length) return null;

  const ordered = [...rows].sort((a, b) =>
    String(a[axis.id] ?? "").localeCompare(String(b[axis.id] ?? "")),
  );
  const labels = ordered.map((row) => formatCell(row[axis.id], axis));

  return (
    <div className="res-stats">
      {measures.map((column, i) => (
        <Stat
          key={column.id}
          column={column}
          slot={i + 1}
          labels={labels}
          values={ordered.map((row) => numeric(row[column.id]))}
        />
      ))}
    </div>
  );
}

type ChartProps = {
  spec: ChartSpec;
  columns: ResultColumn[];
  rows: Record<string, unknown>[];
};

const BARE_W = 600;
const BARE_H = 90;
const DONUT = 230;
const DONUT_OUTER = 96;
const DONUT_INNER = 58;
const HBAR_ROW = 26;
const HBAR_LABEL = 140;
const HEAT_LABEL = 120;
const HEAT_ROW = 26;
const HEAT_STEPS = [0.18, 0.37, 0.56, 0.75, 0.95];

function ordered(rows: Record<string, unknown>[], key: string) {
  return [...rows].sort((a, b) => String(a[key] ?? "").localeCompare(String(b[key] ?? "")));
}

function distinct(rows: Record<string, unknown>[], key: string): string[] {
  const seen: string[] = [];
  for (const row of rows) {
    const value = String(row[key] ?? "");
    if (!seen.includes(value)) seen.push(value);
  }
  return seen;
}

function annulus(cx: number, cy: number, ri: number, ro: number, from: number, to: number) {
  const large = to - from > Math.PI ? 1 : 0;
  const at = (r: number, a: number) => `${cx + r * Math.cos(a)},${cy + r * Math.sin(a)}`;
  return (
    `M${at(ro, from)} A${ro},${ro} 0 ${large},1 ${at(ro, to)} ` +
    `L${at(ri, to)} A${ri},${ri} 0 ${large},0 ${at(ri, from)} Z`
  );
}

function Bare({ spec, columns, rows }: ChartProps) {
  const byId = new Map(columns.map((c) => [c.id, c]));
  const axis = byId.get(spec.x);
  const measure = byId.get(spec.y[0]);
  if (!axis || !measure) return null;

  const values = ordered(rows, axis.id)
    .map((row) => numeric(row[measure.id]))
    .filter((v): v is number => v !== null);
  if (values.length < 2) return null;

  const path = sparkPath(values, BARE_W, BARE_H);
  return (
    <div className="res-bare">
      <svg
        viewBox={`0 0 ${BARE_W} ${BARE_H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${measure.label} over ${axis.label}`}
      >
        <path className="res-spark-area" d={path.area} style={{ fill: "var(--series-1)" }} />
        <path className="res-spark-line" d={path.line} style={{ stroke: "var(--series-1)" }} />
      </svg>
      <div className="res-bare-ends">
        <span>{formatCell(values[0], measure)}</span>
        <span>{formatCell(values[values.length - 1], measure)}</span>
      </div>
    </div>
  );
}

function Donut({ spec, columns, rows }: ChartProps) {
  const byId = new Map(columns.map((c) => [c.id, c]));
  const axis = byId.get(spec.x);
  const measure = byId.get(spec.y[0]);
  if (!axis || !measure) return null;

  const slices = rows
    .map((row) => ({
      label: formatCell(row[axis.id], axis),
      value: numeric(row[measure.id]) ?? 0,
    }))
    .filter((slice) => slice.value > 0);
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  if (!total) return null;

  const middle = DONUT / 2;
  let angle = -Math.PI / 2;
  const arcs = slices.map((slice, i) => {
    const sweep = Math.min((slice.value / total) * Math.PI * 2, Math.PI * 2 - 0.001);
    const d = annulus(middle, middle, DONUT_INNER, DONUT_OUTER, angle, angle + sweep);
    angle += sweep;
    return { ...slice, d, slot: i + 1, share: (slice.value / total) * 100 };
  });

  return (
    <div className="res-donut">
      <svg
        viewBox={`0 0 ${DONUT} ${DONUT}`}
        role="img"
        aria-label={`${measure.label} by ${axis.label}`}
      >
        {arcs.map((a) => (
          <path
            key={a.label}
            className="res-slice"
            d={a.d}
            style={{ fill: `var(--series-${a.slot})` }}
          />
        ))}
        <text className="res-donut-total" x={middle} y={middle} textAnchor="middle">
          {formatCell(total, measure)}
        </text>
        <text className="res-donut-cap" x={middle} y={middle + 16} textAnchor="middle">
          total
        </text>
      </svg>
      <ul className="res-legend res-donut-legend">
        {arcs.map((a) => (
          <li key={a.label}>
            <span className="res-key" style={{ background: `var(--series-${a.slot})` }} />
            <span className="res-donut-name">{a.label}</span>
            <span className="res-donut-share">{a.share.toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function HBar({ spec, columns, rows }: ChartProps) {
  const byId = new Map(columns.map((c) => [c.id, c]));
  const axis = byId.get(spec.x);
  const measure = byId.get(spec.y[0]);
  if (!axis || !measure) return null;

  const bars = rows.map((row) => ({
    label: formatCell(row[axis.id], axis),
    value: numeric(row[measure.id]) ?? 0,
  }));
  const top = Math.max(...bars.map((b) => b.value), 0) || 1;
  const height = bars.length * HBAR_ROW + 8;
  const plot = W - HBAR_LABEL - 70;

  return (
    <div className="res-plot">
      <svg
        viewBox={`0 0 ${W} ${height}`}
        role="img"
        aria-label={`${measure.label} by ${axis.label}`}
      >
        {bars.map((bar, i) => {
          const width = Math.max(1, (bar.value / top) * plot);
          return (
            <g key={bar.label}>
              <text className="res-tick res-hbar-name" x={HBAR_LABEL - 10} y={i * HBAR_ROW + 18}>
                {bar.label}
              </text>
              <rect
                className="res-bar"
                x={HBAR_LABEL}
                y={i * HBAR_ROW + 5}
                width={width}
                height={HBAR_ROW - 11}
                rx={3}
                style={{ fill: "var(--series-1)" }}
              />
              <text className="res-tick" x={HBAR_LABEL + width + 7} y={i * HBAR_ROW + 18}>
                {formatCell(bar.value, measure)}
              </text>
            </g>
          );
        })}
        <line
          className="res-axis"
          x1={HBAR_LABEL}
          x2={HBAR_LABEL}
          y1={0}
          y2={bars.length * HBAR_ROW}
        />
      </svg>
    </div>
  );
}

function Scatter({ spec, columns, rows }: ChartProps) {
  const byId = new Map(columns.map((c) => [c.id, c]));
  const across = byId.get(spec.x);
  const up = byId.get(spec.y[0]);
  if (!across || !up) return null;

  const points = rows
    .map((row) => ({ x: numeric(row[across.id]), y: numeric(row[up.id]) }))
    .filter((p): p is { x: number; y: number } => p.x !== null && p.y !== null);
  if (!points.length) return null;

  const xs = niceTicks(Math.min(...points.map((p) => p.x)), Math.max(...points.map((p) => p.x)));
  const ys = niceTicks(Math.min(...points.map((p) => p.y)), Math.max(...points.map((p) => p.y)));
  const px = (v: number) => PAD.l + ((v - xs.lo) / (xs.hi - xs.lo)) * PLOT_W;
  const py = (v: number) => PAD.t + PLOT_H - ((v - ys.lo) / (ys.hi - ys.lo)) * PLOT_H;

  return (
    <div className="res-plot">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${up.label} against ${across.label}`}>
        {ys.ticks.map((tick) => (
          <g key={tick}>
            <line className="res-grid" x1={PAD.l} x2={W - PAD.r} y1={py(tick)} y2={py(tick)} />
            <text className="res-tick res-tick-y" x={PAD.l - 8} y={py(tick) + 4}>
              {formatTick(tick, up)}
            </text>
          </g>
        ))}
        {xs.ticks.map((tick) => (
          <text className="res-tick" key={tick} x={px(tick)} y={H - 10} textAnchor="middle">
            {formatTick(tick, across)}
          </text>
        ))}
        {points.map((point, i) => (
          <circle
            key={i}
            className="res-dot"
            cx={px(point.x)}
            cy={py(point.y)}
            r={4.5}
            style={{ fill: "var(--series-1)" }}
          />
        ))}
        <line className="res-axis" x1={PAD.l} x2={W - PAD.r} y1={py(ys.lo)} y2={py(ys.lo)} />
      </svg>
    </div>
  );
}

function Heatmap({ spec, columns, rows }: ChartProps) {
  const byId = new Map(columns.map((c) => [c.id, c]));
  const axis = byId.get(spec.x);
  const down = spec.series ? byId.get(spec.series) : undefined;
  const measure = byId.get(spec.y[0]);
  if (!axis || !down || !measure) return null;

  const across = axis.kind === "time" ? distinct(ordered(rows, axis.id), axis.id) : distinct(rows, axis.id);
  const bands = distinct(rows, down.id);
  const cells = new Map<string, number>();
  for (const row of rows) {
    const value = numeric(row[measure.id]);
    if (value !== null) cells.set(`${row[axis.id]} ${row[down.id]}`, value);
  }
  const values = [...cells.values()];
  if (!values.length) return null;

  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const cell = (W - HEAT_LABEL - 10) / across.length;
  const height = bands.length * HEAT_ROW + 26;
  const step = (v: number) =>
    HEAT_STEPS[Math.min(HEAT_STEPS.length - 1, Math.floor(((v - lo) / (hi - lo || 1)) * HEAT_STEPS.length))];

  return (
    <div className="res-heat">
      <svg
        viewBox={`0 0 ${W} ${height}`}
        role="img"
        aria-label={`${measure.label} by ${axis.label} and ${down.label}`}
      >
        {bands.map((bandName, r) => (
          <g key={bandName}>
            <text className="res-tick res-hbar-name" x={HEAT_LABEL - 10} y={r * HEAT_ROW + 18}>
              {formatCell(bandName, down)}
            </text>
            {across.map((columnName, c) => {
              const value = cells.get(`${columnName} ${bandName}`);
              return (
                <rect
                  key={columnName}
                  className="res-cell"
                  x={HEAT_LABEL + c * cell}
                  y={r * HEAT_ROW + 3}
                  width={Math.max(1, cell - SEGMENT_GAP)}
                  height={HEAT_ROW - SEGMENT_GAP - 3}
                  rx={2}
                  style={{
                    fill: "var(--series-1)",
                    fillOpacity: value === undefined ? 0.05 : step(value),
                  }}
                >
                  <title>{`${formatCell(columnName, axis)} · ${formatCell(bandName, down)}: ${
                    value === undefined ? "—" : formatCell(value, measure)
                  }`}</title>
                </rect>
              );
            })}
          </g>
        ))}
        {across.map((columnName, c) =>
          c % Math.ceil(across.length / MAX_TICKS) !== 0 ? null : (
            <text
              className="res-tick"
              key={columnName}
              x={HEAT_LABEL + c * cell + cell / 2}
              y={height - 8}
              textAnchor="middle"
            >
              {formatCell(columnName, axis)}
            </text>
          ),
        )}
      </svg>
      <div className="res-heat-scale">
        <span>{formatCell(lo, measure)}</span>
        {HEAT_STEPS.map((opacity) => (
          <span
            key={opacity}
            className="res-heat-step"
            style={{ background: "var(--series-1)", opacity }}
          />
        ))}
        <span>{formatCell(hi, measure)}</span>
      </div>
    </div>
  );
}

function Gauge({ spec, columns, rows }: ChartProps) {
  const byId = new Map(columns.map((c) => [c.id, c]));
  const measure = byId.get(spec.y[0]);
  const value = measure ? numeric(rows[0]?.[measure.id]) : null;
  if (!measure || value === null) return null;

  const target = spec.target ?? null;
  const share = target && target > 0 ? Math.max(0, Math.min(1, value / target)) : null;
  const middle = DONUT / 2;
  const from = Math.PI * 0.75;
  const sweep = Math.PI * 1.5;

  return (
    <div className="res-gauge">
      <svg
        viewBox={`0 0 ${DONUT} ${DONUT / 2 + DONUT_OUTER * Math.sin(Math.PI * 0.75) + 6}`}
        role="img"
        aria-label={
          target === null
            ? `${measure.label} ${formatCell(value, measure)}`
            : `${measure.label} ${formatCell(value, measure)} against a target of ${formatCell(target, measure)}`
        }
      >
        <path
          className="res-gauge-track"
          d={annulus(middle, middle, DONUT_INNER, DONUT_OUTER, from, from + sweep)}
        />
        {share !== null && share > 0 && (
          <path
            className="res-gauge-fill"
            d={annulus(middle, middle, DONUT_INNER, DONUT_OUTER, from, from + sweep * share)}
            style={{ fill: "var(--series-1)" }}
          />
        )}
        <text className="res-donut-total" x={middle} y={middle - 4} textAnchor="middle">
          {formatCell(value, measure)}
        </text>
        <text className="res-donut-cap" x={middle} y={middle + 14} textAnchor="middle">
          {target === null
            ? measure.label
            : `${(share! * 100).toFixed(0)}% of ${formatCell(target, measure)}`}
        </text>
      </svg>
    </div>
  );
}

/** Ungrouped measures as figures. Values are exact, not compacted: `ResultBlock` drops the
 *  table for a kpi, so the tile must carry the full value. */
function Kpi({
  spec,
  columns,
  rows,
}: {
  spec: ChartSpec;
  columns: ResultColumn[];
  rows: Record<string, unknown>[];
}) {
  const byId = new Map(columns.map((c) => [c.id, c]));
  const row = rows[0] ?? {};
  const tiles = spec.y.map((id) => byId.get(id)).filter((c): c is ResultColumn => Boolean(c));
  if (!tiles.length) return null;
  return (
    <div className="res-kpis">
      {tiles.map((column) => (
        <div className="res-kpi" key={column.id}>
          <span className="res-kpi-label">{column.label}</span>
          <span className="res-kpi-value">{formatCell(row[column.id], column)}</span>
        </div>
      ))}
    </div>
  );
}
