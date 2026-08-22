import { useEffect, useState } from "react";

import type { SourceRow } from "../sources";

/** Rows rather than a map: order is what the person typed, and a blank name is a row being
 *  filled in. Collapsed to a map only on submit. */
interface Row {
  env: string;
  value: string;
}

const PREFIX = "CUBEJS_DB_";

/** Values that cannot work from inside a container. The form offers a swap. */
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "::1"];

/**
 * The dropdown, and a starting set of variables for each.
 *
 * All of it lives here because none of it is a server rule: the key becomes CUBEJS_DB_TYPE,
 * the rows are whatever was typed, and `POST /source` checks only that every name starts with
 * CUBEJS_DB_. So a warehouse missing from this list is configured the same way — pick the
 * closest, then edit the rows. Cube's driver documentation is what lists the real variables.
 */
const WAREHOUSES: { key: string; label: string; rows: [string, string][] }[] = [
  {
    key: "postgres",
    label: "PostgreSQL",
    rows: [
      ["CUBEJS_DB_HOST", "host.docker.internal"],
      ["CUBEJS_DB_PORT", "5432"],
      ["CUBEJS_DB_NAME", ""],
      ["CUBEJS_DB_USER", ""],
      ["CUBEJS_DB_PASS", ""],
    ],
  },
  {
    key: "mysql",
    label: "MySQL",
    rows: [
      ["CUBEJS_DB_HOST", "host.docker.internal"],
      ["CUBEJS_DB_PORT", "3306"],
      ["CUBEJS_DB_NAME", ""],
      ["CUBEJS_DB_USER", ""],
      ["CUBEJS_DB_PASS", ""],
    ],
  },
  {
    key: "snowflake",
    label: "Snowflake",
    rows: [
      ["CUBEJS_DB_SNOWFLAKE_ACCOUNT", ""],
      ["CUBEJS_DB_SNOWFLAKE_WAREHOUSE", ""],
      ["CUBEJS_DB_NAME", ""],
      ["CUBEJS_DB_USER", ""],
      ["CUBEJS_DB_PASS", ""],
    ],
  },
  {
    key: "bigquery",
    label: "Google BigQuery",
    rows: [
      ["CUBEJS_DB_BQ_PROJECT_ID", ""],
      ["CUBEJS_DB_BQ_CREDENTIALS", ""],
    ],
  },
  {
    key: "databricks-jdbc",
    label: "Databricks",
    rows: [
      ["CUBEJS_DB_DATABRICKS_URL", ""],
      ["CUBEJS_DB_DATABRICKS_TOKEN", ""],
      ["CUBEJS_DB_DATABRICKS_ACCEPT_POLICY", "true"],
    ],
  },
  { key: "redshift", label: "Amazon Redshift", rows: [] },
  { key: "athena", label: "Amazon Athena", rows: [] },
  { key: "clickhouse", label: "ClickHouse", rows: [] },
  { key: "trino", label: "Trino", rows: [] },
  { key: "mssql", label: "SQL Server", rows: [] },
  { key: "duckdb", label: "DuckDB", rows: [] },
];

/** Masked in the box. A display rule and nothing more — the server stores every value the
 *  same way, so being wrong about one costs nothing but a visible password. */
const secret = (env: string) => /PASS|TOKEN|SECRET|CREDENTIALS|KEY$/.test(env);

/**
 * The connection form: a warehouse type and the CUBEJS_DB_* variables it needs.
 *
 * Variables and not labelled fields, because there is no per-driver field list any more — the
 * portal writes what it is given into the compose file and Cube is what reads them. So a
 * warehouse nobody here has heard of is configured the same as Postgres.
 *
 * Also the edit form. Passing `editing` switches it to patching the stored connection: the
 * type is fixed, and a blank value keeps what is stored — which is the only way a password is
 * changed without the browser ever being sent it.
 */
export function ConnectionForm({
  editing,
  onCancel,
  onConnect,
  onSave,
}: {
  /** The stored connection when editing, or undefined when connecting for the first time. */
  editing?: SourceRow;
  /** Omitted by the wizard, which has nothing to cancel back to. */
  onCancel?: () => void;
  onConnect: (body: {
    description: string;
    driver: string;
    values: Record<string, string>;
  }) => Promise<void>;
  onSave: (body: { description: string; values: Record<string, string> }) => Promise<void>;
}) {
  const [driver, setDriver] = useState(editing?.driver ?? "postgres");
  const [description, setDescription] = useState(editing?.description ?? "");
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  // Editing shows the stored names with empty values, since blank is what the server reads as
  // "unchanged" and the browser is never sent a value. Connecting starts from the template.
  useEffect(() => {
    if (editing) {
      const stored = editing.credential_keys.filter((env) => env !== "CUBEJS_DB_TYPE");
      setRows(stored.map((env) => ({ env, value: "" })));
    } else {
      const known = WAREHOUSES.find((w) => w.key === driver);
      const start: [string, string][] = known?.rows.length ? known.rows : [["", ""]];
      setRows(start.map(([env, value]) => ({ env, value })));
    }
  }, [driver, editing]);

  const set = (index: number, row: Partial<Row>) =>
    setRows((prev) => prev.map((old, i) => (i === index ? { ...old, ...row } : old)));

  const filled = rows.filter((row) => row.env.trim());
  const local = filled.find((row) => LOCAL_HOSTS.includes(row.value.trim().toLowerCase()));

  const submit = async () => {
    setError("");
    setSaving(true);
    try {
      const values = Object.fromEntries(
        filled
          // An untouched row when editing must not be sent as empty, or it would overwrite.
          .filter((row) => !editing || row.value !== "")
          .map((row) => [row.env.trim().toUpperCase(), row.value]),
      );
      if (editing) await onSave({ description, values });
      else await onConnect({ description, driver, values });
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <p className="drawer-blurb">
        {editing
          ? "Leave a value blank to keep what is stored. A deployment's environment is fixed when it is created, so this takes effect once you re-run it from a freshly downloaded file."
          : "Stored in analyst.db, which is owner-readable only, and rendered into the compose file you run the deployment with."}
      </p>

      <label>
        Description
        <input
          value={description}
          placeholder="What lives in here"
          onChange={(e) => setDescription(e.target.value)}
        />
        <small>Shown on this page. Not read by the agent.</small>
      </label>

      <label>
        Warehouse
        <select
          value={driver}
          disabled={Boolean(editing)}
          onChange={(e) => setDriver(e.target.value)}
        >
          {WAREHOUSES.map((w) => (
            <option key={w.key} value={w.key}>
              {w.label}
            </option>
          ))}
        </select>
        <small>Becomes CUBEJS_DB_TYPE.</small>
      </label>

      <div className="envs">
        <div className="envs-head">
          <span>Connection variables</span>
          <small>
            Whatever {PREFIX}* variables this warehouse reads — Cube's driver documentation is
            what lists them.
          </small>
        </div>

        {rows.map((row, i) => (
          <div className="env-row" key={i}>
            <input
              className="env-name"
              value={row.env}
              placeholder={`${PREFIX}HOST`}
              spellCheck={false}
              autoCapitalize="characters"
              onChange={(e) => set(i, { env: e.target.value })}
            />
            <input
              className="env-value"
              type={secret(row.env) ? "password" : "text"}
              value={row.value}
              placeholder={editing && row.env ? "unchanged" : ""}
              spellCheck={false}
              autoComplete={secret(row.env) ? "new-password" : "off"}
              onChange={(e) => set(i, { value: e.target.value })}
            />
            <button
              className="env-drop"
              title="Remove"
              onClick={() => setRows((prev) => prev.filter((_, at) => at !== i))}
            >
              ×
            </button>
          </div>
        ))}

        <button className="env-add" onClick={() => setRows((prev) => [...prev, { env: "", value: "" }])}>
          + Add a variable
        </button>
      </div>

      {local && (
        <div className="nudge">
          A database on this machine is host.docker.internal from inside the container.
          <button
            onClick={() =>
              setRows((prev) =>
                prev.map((row) =>
                  row === local ? { ...row, value: "host.docker.internal" } : row,
                ),
              )
            }
          >
            Use host.docker.internal
          </button>
        </div>
      )}

      <p className="drawer-blurb">
        {editing
          ? "The deployment reads these from the file it was started with, so a change here is applied by re-running it from a freshly downloaded one — a container's environment cannot be edited in place."
          : "These go into the compose file you run the deployment with, here or on a server. Nothing starts yet."}
      </p>

      {error && <p className="drawer-error">{error}</p>}

      <footer>
        {onCancel && <button onClick={onCancel}>Cancel</button>}
        <button
          className="primary"
          disabled={saving || (!editing && filled.length === 0)}
          onClick={() => void submit()}
        >
          {saving ? "Saving…" : editing ? "Save" : "Store the connection"}
        </button>
      </footer>
    </>
  );
}

/**
 * The same form as a drawer, which is how the data page opens it.
 *
 * The split exists so the wizard can render the fields inline without a second copy of them:
 * what a valid connection is lives in `ConnectionForm`, and this is only the chrome around it.
 */
export function AddSource(
  props: React.ComponentProps<typeof ConnectionForm> & { onCancel: () => void },
) {
  return (
    <div className="drawer-scrim" onClick={props.onCancel}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>{props.editing ? "Edit the connection" : "Connect a database"}</h2>
          <button className="icon" onClick={props.onCancel} title="Close">
            ×
          </button>
        </header>
        <ConnectionForm {...props} />
      </aside>
    </div>
  );
}
