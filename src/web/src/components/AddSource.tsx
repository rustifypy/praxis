import { useEffect, useMemo, useState } from "react";

import type { DriverSpec, SourceRow } from "../sources";

/**
 * The connection form, built from the driver specs themselves — so a field Cube
 * documents appears here without any frontend change. This is the only way a source is
 * created; there is no CLI equivalent to drift from.
 *
 * Also the edit form. Passing `editing` switches it to patching one source: the name,
 * driver and port are fixed, and a blank field keeps the stored value — which is the
 * only way a password can be changed without the browser ever being sent it.
 *
 * It offers to swap a `localhost` host for `host.docker.internal`, because from inside
 * the container the first cannot work and the mistake costs a whole failed connect
 * cycle. A `path` field is typed rather than picked: a file input yields the file's
 * contents and never its path, and the path is what gets bind-mounted.
 */
export function AddSource({
  drivers,
  localHosts,
  localhostNote,
  nextPort,
  taken,
  editing,
  onCancel,
  onCreate,
  onSave,
}: {
  drivers: DriverSpec[];
  localHosts: string[];
  localhostNote: string;
  nextPort: number;
  taken: string[];
  /** The source being edited, or undefined when adding. */
  editing?: SourceRow;
  onCancel: () => void;
  onCreate: (body: {
    name: string;
    description: string;
    driver: string;
    port: number;
    values: Record<string, string>;
  }) => Promise<void>;
  onSave: (name: string, body: { description: string; values: Record<string, string> }) => Promise<void>;
}) {
  const [driver, setDriver] = useState(editing?.driver ?? "");
  const [name, setName] = useState(editing?.name ?? "");
  const [description, setDescription] = useState(editing?.description ?? "");
  const [port, setPort] = useState(editing?.port ?? nextPort);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const spec = useMemo(() => drivers.find((d) => d.key === driver), [drivers, driver]);

  // `drivers` is fetched, so the first render has none and no field would appear. Adopt
  // the first one as soon as the list arrives.
  useEffect(() => {
    if (!driver && drivers.length) setDriver(drivers[0].key);
  }, [driver, drivers]);

  // Blank when editing: the form is never sent a stored value, and blank is what the
  // server reads as "unchanged".
  useEffect(() => {
    setValues(
      Object.fromEntries((spec?.fields ?? []).map((f) => [f.env, editing ? "" : f.default])),
    );
  }, [spec, editing]);

  // Live feedback while typing. The server validates the same rules and its answer is
  // the one that counts — this only saves a round trip on an obvious mistake.
  const nameError = editing
    ? ""
    : name && !/^[a-z][a-z0-9-]*$/.test(name)
      ? "lowercase letters, digits and hyphens, starting with a letter"
      : taken.includes(name)
        ? `${name} already exists`
        : "";

  // Which fields are needed is the server's answer (`required_envs`); whether a box is empty
  // is the only part that has to be local. When editing, an empty field means "keep it".
  const needed = spec?.required_envs ?? [];
  const missing = editing
    ? []
    : needed
        .filter((env) => !(values[env] ?? "").trim())
        .map((env) => spec?.fields.find((f) => f.env === env)?.label || env);

  const hostField = spec?.fields.find((f) => f.env === "CUBEJS_DB_HOST");
  const hostIsLocal =
    hostField && localHosts.includes((values.CUBEJS_DB_HOST ?? "").trim().toLowerCase());

  const submit = async () => {
    setError("");
    setSaving(true);
    try {
      if (editing) {
        // Only what was typed: an untouched field must not be sent as empty.
        const changed = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ""));
        await onSave(editing.name, { description, values: changed });
      } else {
        await onCreate({ name, description, driver, port, values });
      }
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="drawer-scrim" onClick={onCancel}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header>
          <h2>{editing ? `Edit ${editing.name}` : "Add a data source"}</h2>
          <button className="icon" onClick={onCancel} title="Close">
            ×
          </button>
        </header>

        <p className="drawer-blurb">
          {editing
            ? "Leave a field blank to keep what is stored. A container's environment is fixed when it is created, so this takes effect the next time Connect runs."
            : "One Cube container per source, started from what you type here. Nothing is written to disk — credentials live in the sources database."}
        </p>

        {!editing && (
          <label>
            Name
            <input
              value={name}
              autoFocus
              placeholder="warehouse"
              onChange={(e) => setName(e.target.value.toLowerCase())}
            />
            <small>{nameError || "Becomes the container and directory name."}</small>
          </label>
        )}

        <label>
          Description
          <input
            value={description}
            placeholder="What lives in here"
            onChange={(e) => setDescription(e.target.value)}
          />
          <small>The agent reads this when routing questions.</small>
        </label>

        <label>
          Database
          <select
            value={driver}
            disabled={Boolean(editing)}
            onChange={(e) => setDriver(e.target.value)}
          >
            {drivers.map((d) => (
              <option key={d.key} value={d.key}>
                {d.label}
                {d.extracts_tables ? "" : " — no table extraction yet"}
              </option>
            ))}
          </select>
        </label>

        {spec?.fields.map((field) =>
          field.boolean ? (
            <label key={field.env} className="check">
              <input
                type="checkbox"
                checked={(values[field.env] ?? "false") === "true"}
                onChange={(e) =>
                  setValues((v) => ({ ...v, [field.env]: String(e.target.checked) }))
                }
              />
              {field.label}
            </label>
          ) : (
            <label key={field.env}>
              {field.label}
              {!needed.includes(field.env) && <span className="optional"> optional</span>}
              <input
                type={field.secret ? "password" : "text"}
                value={values[field.env] ?? ""}
                placeholder={
                  // A path field cannot say "unchanged": the credential holds the
                  // container path, so the stored host path is the only useful prompt
                  // and it comes from `mounts`, not from `credential_keys`.
                  field.mount_to
                    ? (editing?.mounts?.[field.mount_to] ?? "/Users/you/service-account.json")
                    : editing && editing.credential_keys.includes(field.env)
                      ? "unchanged"
                      : ""
                }
                spellCheck={field.mount_to ? false : undefined}
                autoComplete={field.secret ? "new-password" : "off"}
                onChange={(e) => setValues((v) => ({ ...v, [field.env]: e.target.value }))}
              />
              {field.help && <small>{field.help}</small>}
              {field.mount_to && (
                <small>
                  A path on the machine running this portal — typed, not chosen, because a
                  file picker hands the browser the contents and never the location.
                </small>
              )}
            </label>
          ),
        )}

        {hostIsLocal && (
          <div className="nudge">
            {localhostNote}
            <button
              onClick={() => setValues((v) => ({ ...v, CUBEJS_DB_HOST: "host.docker.internal" }))}
            >
              Use host.docker.internal
            </button>
          </div>
        )}

        <label>
          Port on this machine
          <input
            type="number"
            value={port}
            disabled={Boolean(editing)}
            onChange={(e) => setPort(Number(e.target.value))}
          />
          <small>Where Cube answers. Bound to localhost only.</small>
        </label>

        {error && <p className="drawer-error">{error}</p>}

        <footer>
          <button onClick={onCancel}>Cancel</button>
          <button
            className="primary"
            disabled={saving || !name || Boolean(nameError) || missing.length > 0}
            onClick={() => void submit()}
            title={missing.length ? `Still needed: ${missing.join(", ")}` : undefined}
          >
            {saving ? "Saving…" : editing ? "Save and reconnect" : "Add and connect"}
          </button>
        </footer>
      </aside>
    </div>
  );
}
