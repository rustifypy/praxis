import { useEffect, useState } from "react";

import type { SourceRow } from "../sources";
import { Collapse } from "./Collapse";

/** Values that cannot work from inside a container: the deployment would be dialling itself.
 *  The form offers the swap rather than refusing, because a database really can be there. */
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "::1"];

/** How a setting is asked for. `secret` masks with a reveal, `key` and `certificate` are
 *  multi-line pastes — nothing here reads a file off disk, so a service account key and a CA
 *  certificate are both text someone pastes in. */
type Kind = "text" | "number" | "secret" | "select" | "key" | "certificate";

interface Field {
  /** The environment variable the deployment is handed. **Never shown.** Someone connecting a
   *  database recognises "Host", not the spelling an engine happens to read it under, and every
   *  message on this screen is in the same words as the labels. */
  env: string;
  label: string;
  kind: Kind;
  /** Prefilled when connecting. For a `select`, which option starts chosen. */
  value?: string;
  placeholder?: string;
  hint?: string;
  options?: { value: string; label: string }[];
  /** Blocks the button while blank. This list is the same one the server refuses on — the point
   *  of having it here is that the complaint arrives while the form is still on screen. */
  required?: boolean;
  /** Folded away under "More settings" until asked for. */
  advanced?: boolean;
  /** A shape complaint that blocks, or "" when the value is fine. Only ever called on a
   *  non-blank value: whether blank is allowed is `required`'s question, not this one. */
  bad?: (value: string) => string;
  /** A complaint that does **not** block: probably a mistake, and legitimately might not be.
   *  A blank password is the reason this exists — trust authentication is real. */
  iffy?: (value: string) => string;
  /** A hostname, so the swap for a database on this machine is offered on it. */
  host?: boolean;
  /** What the deployment reads is base64 of what was pasted. */
  base64?: boolean;
  /** Which property of the pasted key on this same card carries this field's value, so it can
   *  be offered rather than retyped — and so a value that disagrees with the key is noticed. */
  from?: string;
}

interface Warehouse {
  key: string;
  label: string;
  /** One line on the card, so the choice can be made without knowing the product. */
  blurb: string;
  /** Which half of the picker it sits in. */
  group: "Databases" | "Warehouses and lakehouses";
  fields: Field[];
}

const port = (value: string) =>
  /^\d+$/.test(value) && +value > 0 && +value < 65536
    ? ""
    : "a port is a number between 1 and 65535";

const hostname = (value: string) =>
  /^[\w.-]+$/.test(value) ? "" : "just the host on its own — no https://, no port, no path";

/** A pasted service account key. Checked for the two fields that actually sign the request, so
 *  the common mistake — pasting the OAuth client file, or one field of the key — is caught here
 *  rather than by a deployment that starts and then cannot authenticate. */
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

/** Host, port, database, user, password — the four products that are exactly that, differing
 *  only in which port they answer on. Written once because four warehouses use it, and because
 *  the three that block on blank have to stay the same three the server refuses on. */
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

/** Offered on everything that speaks TLS, and folded away: the default is what a managed
 *  database already accepts, and the certificate box is for the private authority case only. */
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

/**
 * The picker, and the settings each choice asks for.
 *
 * All of it is here rather than on the server because none of it is a server rule: the server
 * checks that what arrives can open a connection and stores it. So this table is what makes the
 * form specific — only the fields a Snowflake connection has, in Snowflake's own words — and a
 * warehouse missing from it is still connectable through "Other settings" at the bottom.
 *
 * Every field's `label` is the product's word for the thing, taken from its own connection
 * dialog. What each one is called in the deployment's environment is in `env` and appears
 * nowhere on screen.
 */
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

/** The picker's word for a connection someone already made, for the two screens that name one.
 *  Falls back to the stored key: an unlisted warehouse was connected through the free-form rows
 *  and that key is the only name anybody gave it. */
export const warehouseName = (driver: string) =>
  WAREHOUSES.find((w) => w.key === driver)?.label ?? driver;

/** Rows rather than a map, for the settings the picker has no field for: order is what was
 *  typed, and a blank name is a row being filled in. */
interface Extra {
  env: string;
  value: string;
}

/** What the deployment is handed for a pasted key: base64 of the bytes, not of the characters,
 *  so a key with a non-ASCII character in it survives — `btoa` alone throws on one. */
const encoded = (text: string) => {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
};

/**
 * Connecting a database: which kind it is, then the settings that kind actually has.
 *
 * **The fields are per warehouse and the validation is in the browser**, because the alternative
 * is what this replaced: one list of name/value rows where every warehouse looked identical and
 * a typo in a variable name was discovered by a deployment that started cleanly and then failed
 * every query. Nothing is asked for that the chosen warehouse does not read.
 *
 * **Nothing is a file.** A service account key and a CA certificate are pasted as text, because
 * the deployment that reads them may not be on this machine and a path would name a file it
 * cannot see. The key is base64-encoded on the way out; the certificate goes as it was pasted.
 *
 * **The escape hatch stays.** "Other settings" is the same free-form rows this form used to be,
 * for a warehouse that is not on the list or a setting that is not on the card. Without it,
 * making the form specific would have made it a smaller form.
 *
 * Also the edit form. Passing `editing` switches it to patching the stored connection: the kind
 * is fixed, blanks keep what is stored — which is the only way a password is changed without
 * the browser ever being sent it — and nothing is required, since a blank takes nothing away.
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

  // Undefined for a connection made through the free-form rows, and **not** substituted with
  // the first card: showing Postgres's boxes for a warehouse someone typed in by hand is how an
  // edit silently offers to overwrite settings that connection does not have.
  const warehouse = WAREHOUSES.find((w) => w.key === driver);
  const fields = warehouse?.fields ?? [];
  const stored = new Set(editing?.credential_keys ?? []);

  // Editing starts every box empty, because blank is what the server reads as "unchanged" and
  // the browser is never sent a stored value. Connecting starts from the card's own defaults.
  useEffect(() => {
    setValues(Object.fromEntries(fields.map((f) => [f.env, editing ? "" : (f.value ?? "")])));
    setSeen({});
    setTried(false);
    setError("");
    // A stored setting this card has no field for is not dropped on an edit — it is shown where
    // it can be changed, which is the same place an unlisted one is added.
    const covered = new Set(fields.map((f) => f.env));
    const rest = [...stored].filter((env) => env !== "CUBEJS_DB_TYPE" && !covered.has(env)).sort();
    setExtras(rest.map((env) => ({ env, value: "" })));
    setOther(rest.length > 0);
    setMore(false);
    // `stored` and `fields` are rebuilt every render, so the two things they are derived from
    // are the dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [warehouse, editing]);

  /** Blank-or-broken, in the field's own words. Blank is only ever a complaint when connecting:
   *  on an edit it means "leave what is stored alone", so requiring one would demand a password
   *  be retyped to change a port. */
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

  // A pasted key names the project it was minted in, so one field can be filled from another —
  // and a value that disagrees with the key is a connection to somewhere nobody meant.
  const pasted = fields.find((field) => field.base64 && !complaint(field));
  const filled = fields.find((field) => field.from);
  let inKey = "";
  if (pasted && filled) {
    try {
      inKey = String(JSON.parse(values[pasted.env] || "{}")[filled.from!] ?? "");
    } catch {
      // Unparseable JSON is already the field's own blocking complaint; there is nothing to
      // offer to copy out of it.
      inKey = "";
    }
  }
  const named = filled ? (values[filled.env] ?? "").trim() : "";
  const mismatch = inKey && inKey !== named ? inKey : "";

  const submit = async () => {
    setTried(true);
    if (wrong.length) {
      // Nothing is sent, and a complaint hidden inside a fold is why this opens it: the button
      // going nowhere with no visible reason is indistinguishable from a broken button.
      if (wrong.some((field) => field.advanced)) setMore(true);
      return;
    }
    setError("");
    setSaving(true);
    try {
      const sent: Record<string, string> = {};
      for (const field of fields) {
        const value = (values[field.env] ?? "").trim();
        // A blank is left out rather than sent empty, which is what makes it mean "unchanged"
        // on an edit and "not set" on a new connection.
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

  /** One setting. Rendered from the same function above and below the fold so the two cannot
   *  drift into looking like different kinds of thing. */
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
          {/* In the label's row rather than floating over the box's right edge: the row below
              the box is sometimes a hint, sometimes a complaint and sometimes nothing, so
              anything positioned against it lands somewhere different per field. */}
          {field.kind === "secret" && value && (
            <button
              className="conn-reveal"
              type="button"
              onClick={() => setShown((prev) => ({ ...prev, [field.env]: !prev[field.env] }))}
            >
              {shown[field.env] ? "Hide" : "Show"}
            </button>
          )}
        </span>

        {field.kind === "select" ? (
          <select value={value} onChange={(e) => set(e.target.value)}>
            {/* On an edit the stored choice is unknown here, so an unchanged option leads —
                picking any other one is what changes it. */}
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

  // One element rather than a fragment, and it owns the spacing between its own rows. The two
  // places this renders lay their children out differently — the drawer is a flex column with a
  // gap, the wizard's step is not — and a form whose rows only breathe in one of them is the
  // "onboarding looks unfinished" bug in its entirety.
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

      {/* Empty for a connection made through the free-form rows, which is why this says so
          rather than rendering an empty grid: the settings are all in the fold below. */}
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
          <button
            type="button"
            onClick={() => setValues((prev) => ({ ...prev, [host.env]: "host.docker.internal" }))}
          >
            Use host.docker.internal
          </button>
        </div>
      )}

      {mismatch && filled && (
        <div className="nudge">
          {named
            ? `The key you pasted belongs to ${mismatch}, not ${named}.`
            : `The key you pasted belongs to ${mismatch}.`}
          <button
            type="button"
            onClick={() => setValues((prev) => ({ ...prev, [filled.env]: mismatch }))}
          >
            Use {mismatch}
          </button>
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
                <button
                  className="env-drop"
                  type="button"
                  title="Remove"
                  onClick={() => setExtras((prev) => prev.filter((_, at) => at !== i))}
                >
                  ×
                </button>
              </div>
            ))}

            <button
              className="env-add"
              type="button"
              onClick={() => setExtras((prev) => [...prev, { env: "", value: "" }])}
            >
              + Add a setting
            </button>
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
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
        <button className="primary" type="button" disabled={saving} onClick={() => void submit()}>
          {saving ? "Saving…" : editing ? "Save" : "Store the connection"}
        </button>
      </footer>
    </div>
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
      <aside className="drawer wide" onClick={(e) => e.stopPropagation()}>
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
