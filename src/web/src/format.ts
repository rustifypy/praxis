import type { ResultColumn } from "./types";

/** How a result value is rendered, shared by table cells and chart ticks so they agree. */

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
// No symbol: Cube's `format: currency` never says which currency.
const money = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function time(value: string, grain: Grain | null): string {
  // Sliced, not parsed: `new Date` reads a zoneless timestamp as local and can shift a day.
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

  // Cube often sends numbers as strings (a decimal SUM is "12404.50").
  const numeric = typeof value === "number" ? value : Number(value);
  if (column.kind === "measure" && Number.isFinite(numeric)) {
    if (column.format === "currency") return money.format(numeric);
    // Cube's `percent` is already in percentage units, not a 0–1 fraction.
    if (column.format === "percent") return `${grouped.format(numeric)}%`;
    return grouped.format(numeric);
  }

  return String(value);
}

const compact = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});

/** A number short enough for an axis tick — 12.4K rather than 12,404.50. Axis labels only. */
export function formatTick(value: number, column: ResultColumn): string {
  if (!Number.isFinite(value)) return "";
  if (column.format === "percent") return `${grouped.format(value)}%`;
  if (Math.abs(value) >= 10_000) return compact.format(value);
  return column.format === "currency" ? money.format(value) : grouped.format(value);
}

export function compareCells(a: unknown, b: unknown, column: ResultColumn): number {
  if (column.kind === "measure") {
    const left = Number(a);
    const right = Number(b);
    // A non-numeric measure cell is only ever null, and nulls sort last either way.
    if (Number.isFinite(left) && Number.isFinite(right)) return left - right;
    return Number.isFinite(left) ? -1 : Number.isFinite(right) ? 1 : 0;
  }
  // Times are ISO, so lexical order is chronological.
  return String(a ?? "").localeCompare(String(b ?? ""), undefined, { numeric: true });
}
