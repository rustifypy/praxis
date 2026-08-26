import { useEffect, useMemo, useState } from "react";

import * as api from "../sources";
import type { TableCache, Closure, SchemaInfo } from "../sources";
import { Button } from "./Button";
import { Empty } from "./Empty";

/**
 * Choose a schema and the tables to model.
 *
 * The catalog was read once by a throwaway container and cached, so this is instant and
 * touches no database — re-picking never starts anything.
 *
 * The closure is computed server-side rather than here. Ticking `invoice_line` models
 * `invoice`, `track` and `customer` too, and that rule also decides what the job builds;
 * a picker that disagreed with the pipeline would be worse than none.
 */
export function TablePicker({
  catalog,
  busy,
  label,
  confirm,
  extra,
  onRun,
  onReread,
}: {
  catalog: TableCache;
  busy: boolean;
  /** What the run is called. Building for the first time and rebuilding are the same picker and
   *  the same job, and the only honest difference is the word on the button. */
  label: string;
  /** Asked before the run, or "" for no question. Empty during onboarding, where there is no
   *  model to lose; set for a rebuild, which rewrites every file in main. */
  confirm: string;
  /** Rendered beside the count, for a caller with one more decision to offer. */
  extra?: React.ReactNode;
  onRun: (schema: string, tables: string[] | null) => void;
  onReread: () => void;
}) {
  const schemas = catalog.schemas;
  const [schema, setSchema] = useState(
    () => catalog.selection.schema ?? schemas.find((s) => s.schema === "public")?.schema ?? schemas[0]?.schema ?? "",
  );
  const current: SchemaInfo | undefined = schemas.find((s) => s.schema === schema);
  const tables = current?.tables ?? [];

  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [closure, setClosure] = useState<Closure | null>(null);
  const [filter, setFilter] = useState("");

  // Everything, when there is little enough that choosing is busywork. Above that the
  // user picks, because describing is one model call per table.
  useEffect(() => {
    const previous = catalog.selection.tables;
    const seed =
      previous && catalog.selection.schema === schema
        ? previous
        : tables.length <= 25
          ? tables.map((t) => t.name)
          : tables.filter((t) => t.fact).map((t) => t.name);
    setPicked(new Set(seed));
  }, [schema, tables.length]);

  // What the selection really means, from the same code the job will use. Debounced:
  // ticking thirty tables should be one request, not thirty.
  useEffect(() => {
    if (!schema || picked.size === 0) {
      setClosure(null);
      return;
    }
    let live = true;
    const timer = window.setTimeout(() => {
      void api
        .closure(schema, [...picked])
        .then((result) => live && setClosure(result))
        .catch(() => live && setClosure(null));
    }, 200);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [schema, picked]);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return needle ? tables.filter((t) => t.name.toLowerCase().includes(needle)) : tables;
  }, [tables, filter]);

  const toggle = (table: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(table) ? next.delete(table) : next.add(table);
      return next;
    });

  const modelled = closure?.tables.length ?? picked.size;
  const everything = tables.length > 0 && modelled === tables.length;

  return (
    <div className="picker">
      <div className="picker-head">
        <label>
          Schema
          <select value={schema} onChange={(e) => setSchema(e.target.value)} disabled={busy}>
            {schemas.map((s) => (
              <option key={s.schema} value={s.schema}>
                {s.schema} · {s.tables.length} tables
              </option>
            ))}
          </select>
        </label>

        <input
          className="picker-filter"
          placeholder={`Filter ${tables.length} tables`}
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />

        <span className="picker-bulk">
          <Button onClick={() => setPicked(new Set(tables.map((t) => t.name)))} disabled={busy}>
            All
          </Button>
          <Button onClick={() => setPicked(new Set())} disabled={busy}>
            None
          </Button>
          <Button
            title="Tables nothing points at — where additive facts live"
            onClick={() => setPicked(new Set(tables.filter((t) => t.fact).map((t) => t.name)))}
            disabled={busy}
          >
            Facts
          </Button>
        </span>

        <Button variant="link" disabled={busy} onClick={onReread} title="Pick up a migration">
          Re-read
        </Button>
      </div>

      <div className="picker-list">
        {shown.map((table) => {
          const ticked = picked.has(table.name);
          const pulled = !ticked && closure?.tables.includes(table.name);
          return (
            <label
              key={table.name}
              className={`picker-row ${ticked ? "on" : ""} ${pulled ? "pulled" : ""}`}
              title={pulled ? "Included because your selection references it" : undefined}
            >
              <input
                type="checkbox"
                checked={ticked || Boolean(pulled)}
                onChange={() => toggle(table.name)}
                disabled={busy}
              />
              <span className="picker-name">
                {table.name}
                {table.fact && <span className="tag">fact</span>}
                {table.primary_key.length === 0 && (
                  <span className="tag warn" title="Cube cannot join to a table with no key">
                    no key
                  </span>
                )}
                {pulled && <span className="tag soft">referenced</span>}
              </span>
              <span className="picker-meta">
                {table.columns} cols
                {table.references.length > 0 && ` · → ${table.references.join(", ")}`}
              </span>
            </label>
          );
        })}
        {shown.length === 0 && <Empty tight>Nothing matches “{filter}”.</Empty>}
      </div>

      <div className="picker-foot">
        <div className="picker-says">
          <strong>
            {modelled} of {tables.length} tables
          </strong>
          {closure && closure.added.length > 0 && (
            <span>
              {" "}
              — including {closure.added.join(", ")} because your selection references them
            </span>
          )}
          {closure && closure.cut_joins.length > 0 && (
            <span className="cut">
              {" "}
              Dropping {closure.cut_joins.length} join
              {closure.cut_joins.length === 1 ? "" : "s"}: {closure.cut_joins.join(", ")}
            </span>
          )}
          {closure && closure.keyless.length > 0 && (
            <span className="cut"> No key on {closure.keyless.join(", ")} — nothing can join to it.</span>
          )}
        </div>

        {extra}

        <Button
          variant="primary"
          disabled={busy || modelled === 0}
          onClick={() => {
            // A rebuild replaces every model file in one commit. Recoverable from the store's
            // history, which is not the same as intended — so it is still worth asking.
            if (confirm && !window.confirm(confirm)) return;
            onRun(schema, everything ? null : [...picked]);
          }}
        >
          {label.replace("{n}", String(modelled))}
        </Button>
      </div>
    </div>
  );
}
