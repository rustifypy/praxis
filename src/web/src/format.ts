import type { ResultColumn } from "./types";

/**
 * How a result value is rendered, in one place.
 *
 * Two callers — the table's cells and the chart's axis ticks — and them disagreeing would
 * put "12404.5" on an axis beside "12,404.50" in the table for the same number.
 *
 * Everything here keys off `cube.shape.Column`, so the rules are Cube's own metadata
 * rather than a guess from the column name.
 */

const GRAINS = ["year", "quarter", "month", "week", "day", "hour", "minute", "second"] as const;
type Grain = (typeof GRAINS)[number];

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** The grain a grouped time column was built with — `cube.shape` appends it to the id. */
function grainOf(column: ResultColumn): Grain | null {
  const tail = column.id.split(".").pop();
  return GRAINS.includes(tail as Grain) ? (tail as Grain) : null;
}

const grouped = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
// Money always shows its minor unit, which is also what separates it from a plain count
// once the symbol is gone. And the symbol has to go: Cube's `format: currency` says the
// measure is money, never which currency, so any symbol we picked would be wrong for the
// first source that is not priced in it.
const money = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function time(value: string, grain: Grain | null): string {
  // Cube returns "2024-03-01T00:00:00.000" and the tail is noise at every grain above an
  // hour. Sliced rather than parsed through Date: a bare timestamp has no zone, so
  // `new Date(...)` reads it as local and can shift a month boundary by a day.
  const [date, clock = ""] = value.split("T");
  const [year, month, day] = date.split("-");
  switch (grain) {
    case "year":
      return year;
    case "quarter":
      return `${year} Q${Math.floor((Number(month) - 1) / 3) + 1}`;
    case "month":
      return `${MONTHS[Number(month) - 1] ?? month} ${year}`;
    case "hour":
    case "minute":
    case "second":
      return `${date} ${clock.slice(0, 8)}`.trim();
    default:
      // week, day, and an ungrouped timestamp dimension.
      return [year, month, day].filter(Boolean).join("-");
  }
}

export function formatCell(value: unknown, column: ResultColumn): string {
  if (value === null || value === undefined || value === "") return "—";

  if (column.kind === "time" || column.type === "time") {
    return typeof value === "string" ? time(value, grainOf(column)) : String(value);
  }

  // Cube sends numbers as strings often enough — a SUM over a decimal column comes back
  // as "12404.50" — that testing the value's type rather than the column's would leave
  // every currency measure unformatted.
  const numeric = typeof value === "number" ? value : Number(value);
  if (column.kind === "measure" && Number.isFinite(numeric)) {
    if (column.format === "currency") return money.format(numeric);
    // Cube's `percent` means the value is already in percentage units, not a 0–1
    // fraction, so it is never multiplied here.
    if (column.format === "percent") return `${grouped.format(numeric)}%`;
    return grouped.format(numeric);
  }

  return String(value);
}

const compact = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});

/**
 * A number short enough for an axis tick — 12.4K rather than 12,404.50.
 *
 * Axis labels only. The full form goes everywhere a value is *read* rather than
 * positioned: six long numbers down a y-axis collide with each other, and the table
 * carries the exact ones anyway.
 */
export function formatTick(value: number, column: ResultColumn): string {
  if (!Number.isFinite(value)) return "";
  if (column.format === "percent") return `${grouped.format(value)}%`;
  if (Math.abs(value) >= 10_000) return compact.format(value);
  return column.format === "currency" ? money.format(value) : grouped.format(value);
}

/** Compare two cells of one column, for a click-to-sort header. */
export function compareCells(a: unknown, b: unknown, column: ResultColumn): number {
  if (column.kind === "measure") {
    const left = Number(a);
    const right = Number(b);
    // A non-numeric measure cell is only ever null, and nulls sort last either way.
    if (Number.isFinite(left) && Number.isFinite(right)) return left - right;
    return Number.isFinite(left) ? -1 : Number.isFinite(right) ? 1 : 0;
  }
  // Times are ISO, so lexical order is chronological order — no parsing needed.
  return String(a ?? "").localeCompare(String(b ?? ""), undefined, { numeric: true });
}
