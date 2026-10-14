# Storage

## One file, one schema

`core/db.py` declares every table this process creates (SQLAlchemy `metadata`), and `DATA_DIR/analyst.db`
(`~/.analyst/analyst.db`) holds all of them:

| Tables | Accessors |
|---|---|
| `chats`, `events` — the transcript | `core/transcript.py` |
| `results` — full rows of a query result | `core/transcript.py` |
| `dashboards`, `tiles`, `layouts`, `runs`, `tile_results` — dashboards | `core/boards.py` |
| `deployment` — one row (`CHECK id = 1`): credentials, secrets, addresses, cached introspection | `core/deployment.py` |
| `mcp` — MCP enabled flag and token | `core/access.py` |
| `checkpoints`, `writes` — LangGraph | LangGraph's sqlite saver |

`core.db.engine` is the one opener — one cached engine per file — so `PRAGMA foreign_keys=ON` cannot drift; the file runs in
WAL mode because the checkpointer shares it. It is chmod `600` because `deployment.credentials`
is plaintext JSON with the warehouse password.

**Accessors live beside the schema**, one module per group. A module belongs in `core` when it
needs nothing from above, and a table's accessors never do.

## No migrations, deliberately

`create_all` does not diff. A new table appears on an existing database; a new column silently does not, and changing one means deleting the file. Affordable for one machine,
and the first thing to revisit if this ever runs shared. It is also why the flow chat's binding
is `flows/<flow>/chat.json`, not a column on `chats`.

## A file is not a row

An uploaded CSV, a script's chart and a file's profile live in the **sandbox service's** volume,
one directory per chat, with no table — a row would only ever be a stale copy of `ls`.

Everything about a Flow — definition, runs, event logs, artifacts, Gate state — is files under
`FLOWS_DIR`. See [decision 0006](../adr/0006-a-flow-keeps-its-state-in-its-folder.md).

## Elsewhere in `DATA_DIR`

- `jobs.lock` — one model-build job at a time, machine-wide.
- `drafts/<ref>/` — the model editor's working copy of each branch.
