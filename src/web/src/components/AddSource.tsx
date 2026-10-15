import { useEffect, useState } from "react";

import type { SourceRow } from "../sources";
import { Button } from "./Button";
import { Collapse } from "./Collapse";

/** From inside a container these dial the deployment itself. Offered a swap, not refused. */
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "::1"];

/** `key` and `certificate` are multi-line pastes: nothing here reads a file off disk. */
type Kind = "text" | "number" | "secret" | "select" | "key" | "certificate";

interface Field {
  /** The deployment's environment variable. Never shown: the screen speaks in `label`s. */
  env: string;
  label: string;
  kind: Kind;
  /** Prefilled when connecting. For a `select`, which option starts chosen. */
  value?: string;
  placeholder?: string;
  hint?: string;
  options?: { value: string; label: string }[];
  /** Blocks while blank. Must match what the server refuses on. */
  required?: boolean;
  /** Folded away under "More settings" until asked for. */
  advanced?: boolean;
  /** A blocking complaint, or "". Only called on non-blank values; blank is `required`'s. */
  bad?: (value: string) => string;
  /** A non-blocking complaint, e.g. a blank password (trust authentication is real). */
  iffy?: (value: string) => string;
  /** A hostname, so the swap for a database on this machine is offered on it. */
  host?: boolean;
  /** What the deployment reads is base64 of what was pasted. */
  base64?: boolean;
  /** The pasted key's property holding this value, so it is offered and a mismatch noticed. */
  from?: string;
}

interface Warehouse {
  key: string;
  label: string;
  blurb: string;
  group: "Databases" | "Warehouses and lakehouses";
  fields: Field[];
}

const port = (value: string) =>
  /^\d+$/.test(value) && +value > 0 && +value < 65536
    ? ""
    : "a port is a number between 1 and 65535";

const hostname = (value: string) =>
  /^[\w.-]+$/.test(value) ? "" : "just the host on its own — no https://, no port, no path";

/** Checks the two fields that sign requests, catching a pasted OAuth client file here rather
 *  than in a deployment that starts and then cannot authenticate. */
const keyfile = (value: string) => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    return "this is not valid JSON — paste the whole file, braces included";
  }
  if (!parsed || typeof parsed !== "object") return "a key file is a JSON object";
  const key = parsed as Record<string, unknown>;
  const absent = ["client_email", "private_key"].filter((name) => !key[name]);
  return absent.length ? "this JSON is not a service account key — no private key in it" : "";
};

const certificate = (value: string) =>
  value.includes("-----BEGIN")
    ? ""
    : "paste the certificate whole, including its BEGIN and END lines";

const blank = (what: string) => (value: string) => (value ? "" : what);

/** Host/port/database/user/password warehouses. Its required fields must stay the ones the
 *  server refuses on. */
const server = (defaultPort: string): Field[] => [
  {
    env: "CUBEJS_DB_HOST",
    label: "Host",
    kind: "text",
    required: true,
    host: true,
    value: "host.docker.internal",
    bad: hostname,
    hint: "A database on this machine is host.docker.internal from inside a container, never localhost.",
  },
  { env: "CUBEJS_DB_PORT", label: "Port", kind: "number", value: defaultPort, bad: port },
  { env: "CUBEJS_DB_NAME", label: "Database", kind: "text", required: true },
  { env: "CUBEJS_DB_USER", label: "User", kind: "text", required: true },
  {
    env: "CUBEJS_DB_PASS",
    label: "Password",
    kind: "secret",
    iffy: blank("no password — right only if this database lets that user in without one"),
  },
];

/** Folded away: managed databases accept the default; the certificate is for a private CA. */
const encrypted: Field[] = [
  {
    env: "CUBEJS_DB_SSL",
    label: "Encrypt the connection",
    kind: "select",
    advanced: true,
    options: [
      { value: "", label: "No" },
      { value: "true", label: "Yes" },
    ],
  },
  {
    env: "CUBEJS_DB_SSL_CA",
    label: "Server certificate",
    kind: "certificate",
    advanced: true,
    bad: certificate,
    hint: "Only when the server's certificate was signed by your own authority. Paste it — nothing here reads files.",
  },
];

/** Each warehouse's fields, labelled in the product's own words. Not a server rule, so it lives
 *  here; an unlisted warehouse still connects through "Other settings". */
const WAREHOUSES: Warehouse[] = [
  {
    key: "postgres",
    label: "PostgreSQL",
    blurb: "Postgres, or anything speaking its protocol",
    group: "Databases",
    fields: [...server("5432"), ...encrypted],
  },
  {
    key: "mysql",
    label: "MySQL",
    blurb: "MySQL or MariaDB",
    group: "Databases",
    fields: [...server("3306"), ...encrypted],
  },
  {
    key: "mssql",
    label: "SQL Server",
    blurb: "Microsoft SQL Server",
    group: "Databases",
    fields: [
      ...server("1433"),
      { env: "CUBEJS_DB_DOMAIN", label: "Windows domain", kind: "text", advanced: true },
      ...encrypted,
    ],
  },
  {
    key: "clickhouse",
    label: "ClickHouse",
    blurb: "ClickHouse over HTTP",
    group: "Databases",
    fields: [
      {
        env: "CUBEJS_DB_HOST",
        label: "Host",
        kind: "text",
        required: true,
        host: true,
        bad: hostname,
      },
      { env: "CUBEJS_DB_PORT", label: "Port", kind: "number", value: "8123", bad: port },
      { env: "CUBEJS_DB_NAME", label: "Database", kind: "text", value: "default" },
      { env: "CUBEJS_DB_USER", label: "User", kind: "text", value: "default" },
      { env: "CUBEJS_DB_PASS", label: "Password", kind: "secret" },
      ...encrypted,
    ],
  },
  {
    key: "duckdb",
    label: "DuckDB",
    blurb: "A file, or MotherDuck",
    group: "Databases",
    fields: [
      {
        env: "CUBEJS_DB_DUCKDB_DATABASE_PATH",
        label: "Database file",
        kind: "text",
        placeholder: "/data/warehouse.duckdb",
        hint: "A path inside the container, so the file has to be mounted into it. Leave empty for an in-memory database.",
      },
      {
        env: "CUBEJS_DB_DUCKDB_MOTHERDUCK_TOKEN",
        label: "MotherDuck token",
        kind: "secret",
        advanced: true,
      },
    ],
  },
  {
    key: "snowflake",
    label: "Snowflake",
    blurb: "An account, a warehouse and a database",
    group: "Warehouses and lakehouses",
    fields: [
      {
        env: "CUBEJS_DB_SNOWFLAKE_ACCOUNT",
        label: "Account identifier",
        kind: "text",
        required: true,
        placeholder: "xy12345.eu-central-1",
        hint: "The part before .snowflakecomputing.com.",
        bad: (value) =>
          value.includes("snowflakecomputing.com")
            ? "the identifier only, without .snowflakecomputing.com"
            : "",
      },
      {
        env: "CUBEJS_DB_SNOWFLAKE_WAREHOUSE",
        label: "Warehouse",
        kind: "text",
        required: true,
        hint: "The compute that runs the queries — not the database.",
      },
      { env: "CUBEJS_DB_NAME", label: "Database", kind: "text", required: true },
      { env: "CUBEJS_DB_USER", label: "User", kind: "text", required: true },
      {
        env: "CUBEJS_DB_PASS",
        label: "Password",
        kind: "secret",
        iffy: blank("no password — right only with key pair or OAuth below"),
      },
      { env: "CUBEJS_DB_SNOWFLAKE_ROLE", label: "Role", kind: "text", advanced: true },
      {
        env: "CUBEJS_DB_SNOWFLAKE_AUTHENTICATOR",
        label: "Sign in with",
        kind: "select",
        advanced: true,
        options: [
          { value: "", label: "Password" },
          { value: "SNOWFLAKE_JWT", label: "A key pair" },
          { value: "OAUTH", label: "OAuth" },
          { value: "USERNAME_PASSWORD_MFA", label: "Password and MFA" },
        ],
      },
    ],
  },
  {
    key: "bigquery",
    label: "BigQuery",
    blurb: "A Google Cloud project and a service account",
    group: "Warehouses and lakehouses",
    fields: [
      {
        env: "CUBEJS_DB_BQ_PROJECT_ID",
        label: "Project",
        kind: "text",
        required: true,
        from: "project_id",
        placeholder: "my-project-417",
        hint: "The project ID, not its display name.",
      },
      {
        env: "CUBEJS_DB_BQ_CREDENTIALS",
        label: "Service account key",
        kind: "key",
        required: true,
        base64: true,
        bad: keyfile,
        hint: "Paste the whole JSON key file. It is stored and handed over as base64.",
      },
      {
        env: "CUBEJS_DB_BQ_LOCATION",
        label: "Dataset location",
        kind: "text",
        advanced: true,
        placeholder: "EU",
        hint: "Only when the datasets are not in the project's default location.",
      },
    ],
  },
  {
    key: "databricks-jdbc",
    label: "Databricks",
    blurb: "A SQL warehouse's JDBC URL and a token",
    group: "Warehouses and lakehouses",
    fields: [
      {
        env: "CUBEJS_DB_DATABRICKS_URL",
        label: "JDBC URL",
        kind: "text",
        required: true,
        placeholder: "jdbc:databricks://…",
        hint: "From the SQL warehouse's connection details, under JDBC.",
        bad: (value) =>
          value.startsWith("jdbc:") ? "" : "this should be the JDBC URL, starting with jdbc:",
      },
      {
        env: "CUBEJS_DB_DATABRICKS_TOKEN",
        label: "Access token",
        kind: "secret",
        iffy: blank("no token — right only if the URL above already carries one"),
      },
      {
        env: "CUBEJS_DB_DATABRICKS_ACCEPT_POLICY",
        label: "Accept the Databricks driver licence",
        kind: "select",
        required: true,
        value: "true",
        options: [
          { value: "true", label: "Accepted" },
          { value: "", label: "Not accepted" },
        ],
        hint: "Their driver refuses to load without this, so it is asked rather than assumed.",
      },
      { env: "CUBEJS_DB_NAME", label: "Database", kind: "text", value: "default" },
      { env: "CUBEJS_DB_DATABRICKS_CATALOG", label: "Catalog", kind: "text", advanced: true },
    ],
  },
  {
    key: "redshift",
    label: "Redshift",
    blurb: "Amazon Redshift",
    group: "Warehouses and lakehouses",
    fields: [...server("5439"), ...encrypted],
  },
  {
    key: "athena",
    label: "Athena",
    blurb: "Query S3 through Amazon Athena",
    group: "Warehouses and lakehouses",
    fields: [
      { env: "CUBEJS_AWS_KEY", label: "Access key ID", kind: "text", required: true },
      { env: "CUBEJS_AWS_SECRET", label: "Secret access key", kind: "secret", required: true },
      {
        env: "CUBEJS_AWS_REGION",
        label: "Region",
        kind: "text",
        required: true,
        placeholder: "us-east-1",
      },
      {
        env: "CUBEJS_AWS_S3_OUTPUT_LOCATION",
        label: "Where results are written",
        kind: "text",
        required: true,
        placeholder: "s3://my-bucket/athena-results/",
        hint: "Athena writes every result to S3 first, so it cannot run without a bucket to write to.",
        bad: (value) => (value.startsWith("s3://") ? "" : "an S3 path, starting with s3://"),
      },
      { env: "CUBEJS_AWS_ATHENA_WORKGROUP", label: "Workgroup", kind: "text", advanced: true },
    ],
  },
  {
    key: "trino",
    label: "Trino",
    blurb: "Trino or Presto",
    group: "Warehouses and lakehouses",
    fields: [
      {
        env: "CUBEJS_DB_HOST",
        label: "Host",
        kind: "text",
        required: true,
        host: true,
        bad: hostname,
      },
      { env: "CUBEJS_DB_PORT", label: "Port", kind: "number", value: "8080", bad: port },
      { env: "CUBEJS_DB_USER", label: "User", kind: "text", required: true },
      { env: "CUBEJS_DB_PASS", label: "Password", kind: "secret" },
      { env: "CUBEJS_DB_PRESTO_CATALOG", label: "Catalog", kind: "text", required: true },
      { env: "CUBEJS_DB_SCHEMA", label: "Schema", kind: "text" },
      ...encrypted,
    ],
  },
];

/** Falls back to the stored key: an unlisted warehouse has no other name. */
export const warehouseName = (driver: string) =>
  WAREHOUSES.find((w) => w.key === driver)?.label ?? driver;

/** Rows, not a map: order is as typed, and a blank name is a row being filled in. */
interface Extra {
  env: string;
  value: string;
}

/** Base64 of the UTF-8 bytes: `btoa` alone throws on a non-ASCII character. */
const encoded = (text: string) => {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
};

/**
 * Connecting a database, or with `editing`, patching the stored one: the kind is fixed and a
 * blank keeps what is stored, so the browser is never sent a password. Keys and certificates are
 * pasted, since the deployment that reads them may not be on this machine.
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
  const [values, setValues] = useState<Record<string, string>>({});
  const [extras, setExtras] = useState<Extra[]>([]);
  /** Which fields have been left, so a complaint waits until someone has finished typing. */
  const [seen, setSeen] = useState<Record<string, boolean>>({});
  /** Set by pressing the button, which is when every complaint is shown at once. */
  const [tried, setTried] = useState(false);
  const [more, setMore] = useState(false);
  const [other, setOther] = useState(false);
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  // Undefined for a hand-made connection, never defaulted to a card: that would offer to
  // overwrite settings the connection does not have.
  const warehouse = WAREHOUSES.find((w) => w.key === driver);
  const fields = warehouse?.fields ?? [];
  const stored = new Set(editing?.credential_keys ?? []);

  // Editing starts blank: the server reads blank as "unchanged" and never sends stored values.
  useEffect(() => {
    setValues(Object.fromEntries(fields.map((f) => [f.env, editing ? "" : (f.value ?? "")])));
    setSeen({});
    setTried(false);
    setError("");
    // Stored settings with no field here go to the free-form rows rather than being dropped.
    const covered = new Set(fields.map((f) => f.env));
    const rest = [...stored].filter((env) => env !== "CUBEJS_DB_TYPE" && !covered.has(env)).sort();
    setExtras(rest.map((env) => ({ env, value: "" })));
    setOther(rest.length > 0);
    setMore(false);
    // `stored` and `fields` are rebuilt every render, so depend on their sources.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [warehouse, editing]);

  /** Blank only complains when connecting: on an edit it means "keep what is stored". */
  const complaint = (field: Field) => {
    const value = (values[field.env] ?? "").trim();
    if (!value) return !editing && field.required ? "still needed" : "";
    return field.bad?.(value) ?? "";
  };

  const wrong = fields.filter((field) => complaint(field));
  const owed = wrong.filter((field) => !field.advanced);
  const host = fields.find(
    (field) => field.host && LOCAL_HOSTS.includes((values[field.env] ?? "").trim().toLowerCase()),
  );

  // A pasted key names its project, so that field can be filled from it and a mismatch caught.
  const pasted = fields.find((field) => field.base64 && !complaint(field));
  const filled = fields.find((field) => field.from);
  let inKey = "";
  if (pasted && filled) {
    try {
      inKey = String(JSON.parse(values[pasted.env] || "{}")[filled.from!] ?? "");
    } catch {
      // Bad JSON is already the field's own blocking complaint.
      inKey = "";
    }
  }
  const named = filled ? (values[filled.env] ?? "").trim() : "";
  const mismatch = inKey && inKey !== named ? inKey : "";

  const submit = async () => {
    setTried(true);
    if (wrong.length) {
      // Open the fold if a complaint is hidden in it, or the button looks broken.
      if (wrong.some((field) => field.advanced)) setMore(true);
      return;
    }
    setError("");
    setSaving(true);
    try {
      const sent: Record<string, string> = {};
      for (const field of fields) {
        const value = (values[field.env] ?? "").trim();
        // Omitted, not sent empty: "unchanged" on an edit, "not set" on a new connection.
        if (value) sent[field.env] = field.base64 ? encoded(value) : value;
      }
      for (const row of extras) {
        if (row.env.trim() && row.value.trim()) sent[row.env.trim().toUpperCase()] = row.value;
      }
      if (editing) await onSave({ description, values: sent });
      else await onConnect({ description, driver, values: sent });
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setSaving(false);
    }
  };

  const setting = (field: Field) => {
    const value = values[field.env] ?? "";
    const show = tried || seen[field.env];
    const bad = show ? complaint(field) : "";
    const iffy = show && !bad ? (field.iffy?.(value.trim()) ?? "") : "";
    const keep = Boolean(editing && stored.has(field.env));
    const set = (next: string) => setValues((prev) => ({ ...prev, [field.env]: next }));
    const leave = () => setSeen((prev) => ({ ...prev, [field.env]: true }));

    return (
      <label className={`conn-field ${bad ? "bad" : ""}`} key={field.env}>
        <span className="conn-label">
          {field.label}
          {!editing && !field.required && <em>optional</em>}
          {keep && <em>stored</em>}
          {/* In the label's row: what sits below the box varies per field. */}
          {field.kind === "secret" && value && (
            <Button
              className="conn-reveal"
              type="button"
              onClick={() => setShown((prev) => ({ ...prev, [field.env]: !prev[field.env] }))}
            >
              {shown[field.env] ? "Hide" : "Show"}
            </Button>
          )}
        </span>

        {field.kind === "select" ? (
          <select value={value} onChange={(e) => set(e.target.value)}>
            {/* The stored choice is unknown here, so "unchanged" leads on an edit. */}
            {keep && <option value="">Leave as it is</option>}
            {field.options?.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        ) : field.kind === "key" || field.kind === "certificate" ? (
          <textarea
            value={value}
            rows={field.kind === "key" ? 6 : 4}
            spellCheck={false}
            placeholder={
              keep
                ? "unchanged — paste a new one to replace it"
                : field.kind === "key"
                  ? '{\n  "type": "service_account",\n  …\n}'
                  : "-----BEGIN CERTIFICATE-----"
            }
            onChange={(e) => set(e.target.value)}
            onBlur={leave}
          />
        ) : (
          <input
            type={field.kind === "secret" && !shown[field.env] ? "password" : "text"}
            inputMode={field.kind === "number" ? "numeric" : undefined}
            value={value}
            placeholder={keep ? "unchanged" : field.placeholder}
            spellCheck={false}
            autoComplete={field.kind === "secret" ? "new-password" : "off"}
            onChange={(e) => set(e.target.value)}
            onBlur={leave}
          />
        )}

        {bad ? (
          <small className="conn-bad">{bad}</small>
        ) : iffy ? (
          <small className="conn-iffy">{iffy}</small>
        ) : (
          field.hint && <small>{field.hint}</small>
        )}
      </label>
    );
  };

  const advanced = fields.filter((field) => field.advanced);

  // One element owning its row spacing: the drawer and the wizard lay out children differently.
  return (
    <div className="conn-form">
      <p className="drawer-blurb">
        {editing
          ? "Leave a box empty to keep what is stored. A deployment's settings are fixed when it is created, so this takes effect once you re-run it from a freshly downloaded file."
          : "Stored in analyst.db, which is owner-readable only, and written into the file you run the deployment with. Nothing is contacted yet."}
      </p>

      {!editing && (
        <div className="conn-pick">
          {(["Databases", "Warehouses and lakehouses"] as const).map((group) => (
            <div className="conn-group" key={group}>
              <span className="conn-group-name">{group}</span>
              <div className="conn-cards">
                {WAREHOUSES.filter((w) => w.group === group).map((w) => (
                  <button
                    className={`conn-card ${w.key === driver ? "on" : ""}`}
                    key={w.key}
                    type="button"
                    aria-pressed={w.key === driver}
                    onClick={() => setDriver(w.key)}
                  >
                    <strong>{w.label}</strong>
                    <small>{w.blurb}</small>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <label className="conn-field">
        <span className="conn-label">
          Description<em>optional</em>
        </span>
        <input
          value={description}
          placeholder="What lives in here"
          onChange={(e) => setDescription(e.target.value)}
        />
        <small>Shown on the data page. Not read by the agent.</small>
      </label>

      {warehouse ? (
        <div className="conn-fields">{fields.filter((field) => !field.advanced).map(setting)}</div>
      ) : (
        <p className="drawer-blurb">
          This connection was made by hand rather than from the list, so all of its settings are
          under Other settings.
        </p>
      )}

      {host && (
        <div className="nudge">
          A database on this machine is host.docker.internal from inside the container.
          <Button
            type="button"
            onClick={() => setValues((prev) => ({ ...prev, [host.env]: "host.docker.internal" }))}
          >
            Use host.docker.internal
          </Button>
        </div>
      )}

      {mismatch && filled && (
        <div className="nudge">
          {named
            ? `The key you pasted belongs to ${mismatch}, not ${named}.`
            : `The key you pasted belongs to ${mismatch}.`}
          <Button
            type="button"
            onClick={() => setValues((prev) => ({ ...prev, [filled.env]: mismatch }))}
          >
            Use {mismatch}
          </Button>
        </div>
      )}

      {advanced.length > 0 && (
        <div className="conn-fold">
          <button className="conn-fold-head" type="button" onClick={() => setMore(!more)}>
            <span className="caret">{more ? "▾" : "▸"}</span>
            More settings
            <small>{advanced.map((field) => field.label).join(" · ")}</small>
          </button>
          <Collapse open={more}>
            <div className="conn-fields">{advanced.map(setting)}</div>
          </Collapse>
        </div>
      )}

      <div className="conn-fold">
        <button className="conn-fold-head" type="button" onClick={() => setOther(!other)}>
          <span className="caret">{other ? "▾" : "▸"}</span>
          Other settings
          <small>
            {extras.length
              ? `${extras.length} set by hand`
              : "for a warehouse that is not on the list"}
          </small>
        </button>
        <Collapse open={other}>
          <div className="envs">
            <div className="envs-head">
              <small>
                Anything the boxes above do not cover, named exactly as the database expects it.
                This is how a warehouse missing from the list is connected.
              </small>
            </div>

            {extras.map((row, i) => (
              <div className="env-row" key={i}>
                <input
                  className="env-name"
                  value={row.env}
                  placeholder="SETTING_NAME"
                  spellCheck={false}
                  autoCapitalize="characters"
                  onChange={(e) =>
                    setExtras((prev) =>
                      prev.map((old, at) => (at === i ? { ...old, env: e.target.value } : old)),
                    )
                  }
                />
                <input
                  className="env-value"
                  type="text"
                  value={row.value}
                  placeholder={editing && stored.has(row.env) ? "unchanged" : ""}
                  spellCheck={false}
                  autoComplete="off"
                  onChange={(e) =>
                    setExtras((prev) =>
                      prev.map((old, at) => (at === i ? { ...old, value: e.target.value } : old)),
                    )
                  }
                />
                <Button
                  className="env-drop"
                  type="button"
                  title="Remove"
                  onClick={() => setExtras((prev) => prev.filter((_, at) => at !== i))}
                >
                  ×
                </Button>
              </div>
            ))}

            <Button
              className="env-add"
              type="button"
              onClick={() => setExtras((prev) => [...prev, { env: "", value: "" }])}
            >
              + Add a setting
            </Button>
          </div>
        </Collapse>
      </div>

      <p className="drawer-blurb">
        {editing
          ? "The deployment reads these from the file it was started with, so a change here is applied by re-running it from a freshly downloaded one — a container's settings cannot be edited in place."
          : "These go into the file you run the deployment with, here or on a server."}
      </p>

      {error && <p className="drawer-error">{error}</p>}

      {tried && wrong.length > 0 && (
        <p className="drawer-error">
          {owed.length ? `Still needed: ${owed.map((f) => f.label).join(", ")}.` : ""}
          {owed.length && wrong.length > owed.length ? " " : ""}
          {wrong.length > owed.length ? "And something under More settings." : ""}
        </p>
      )}

      <footer>
        {onCancel && (
          <Button type="button" onClick={onCancel}>
            Cancel
          </Button>
        )}
        <Button variant="primary" type="button" disabled={saving} onClick={() => void submit()}>
          {saving ? "Saving…" : editing ? "Save" : "Store the connection"}
        </Button>
      </footer>
    </div>
  );
}

/** `ConnectionForm` as a drawer, for the data page; the wizard renders the form inline. */
export function AddSource(
  props: React.ComponentProps<typeof ConnectionForm> & { onCancel: () => void },
) {
  return (
    <div className="drawer-scrim" onClick={props.onCancel}>
      <aside className="drawer wide" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>{props.editing ? "Edit the connection" : "Connect a database"}</h2>
          <Button icon onClick={props.onCancel} title="Close">
            ×
          </Button>
        </header>
        <ConnectionForm {...props} />
      </aside>
    </div>
  );
}
