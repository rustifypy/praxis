# Troubleshooting

Find the words you see, then read the fix.

## Starting the portal

| You see | Cause | Fix |
|---|---|---|
| `The frontend has not been built. Run make web.` (503) | Fresh clone or clean checkout | `make web-build` |
| `Unauthorized. Open the URL printed by praxis.` (401) | No token, or a token from before a restart | Use the URL printed at the last start |
| An empty thinking panel | `REASONING_SUMMARY=auto`, or API version earlier than `2025-03-01-preview` | Set `REASONING_SUMMARY=detailed` and a newer API version |

## Connecting

| You see | Cause | Fix |
|---|---|---|
| `pick the kind of database this is` | No warehouse type chosen | Choose one |
| `still needs a host, a database, …` | A required setting is blank | Fill it in — see [Warehouses](deployment.md#warehouses) |
| `'X' is not a connection setting` | A setting without the `CUBEJS_DB_`/`CUBEJS_AWS_` prefix | Use the warehouse's Cube setting name |
| `no database is connected yet` | Downloading a compose file before connecting | Connect first |
| `no matching manifest` at `docker compose up` | Cube Store image for the wrong CPU architecture | Add or remove the `-arm64v8` suffix on the `cubestore` image line |
| `nothing listening on …` | The container is not running, or the port is different | `docker compose ps`; check the address |
| `no response from … in Ns` | Firewall, or the service is hung | `docker compose logs` |
| `ReadError reaching …` | The container is still starting | Wait a few seconds and try again |
| `… answered, but not as the model store` (or sandbox) | Another program is on that port, or the two addresses are swapped | Check which address is which |
| `the store rejected the portal's token — this deployment was run from a file rendered for a different connection` | The services were started from an old compose file | Download the file again and `docker compose up -d` |
| `Cube is not ready behind the store` | Cube is still compiling, or cannot reach Cube Store or the warehouse | `docker compose logs cube-api` |
| `the model store has nothing published to main yet` | Normal before the first model is built | Run the four build steps |
| `this sandbox speaks contract … and the portal expects 1` | Sandbox image does not match the portal | Re-run it from a freshly downloaded file |

A wrong **warehouse password** passes every connection check. It shows up at **Read tables**,
the first step that connects to the warehouse. Fix it with **Edit connection**, then download the
semantic compose file again and re-run it.

A database on the same machine as Docker is `host.docker.internal`, not `localhost`.

## Building and editing the model

| You see | Cause | Fix |
|---|---|---|
| `a job is already running` (409) | Only one build step runs at a time, across the whole machine | Wait for it, or cancel it |
| `schema '…' has not been read` | Building cubes before reading tables | Run **Read tables** first |
| Cube reports zero cubes | A stray `.yml` outside `cubes/`/`views/` — Cube compiles every YAML under the model | The editor refuses such paths; remove any added by hand |
| `ambiguous column` once a join is involved | A multi-column `sql:` expression without `{CUBE}.` | Qualify each column with `{CUBE}.` |
| A merge refused because it would break dashboard tiles | The change removes or renames members a saved tile uses | Fix the tiles, or merge with force |

## Asking questions

| You see | Cause | Fix |
|---|---|---|
| `no Cube deployment is configured` (503) | No model store address stored | Finish connecting |
| `Cube is not answering` (502) | The semantic deployment is down | `docker compose ps` in its folder |
| A query rejected with "closest matches" | A member name that does not exist | The agent normally retries on its own |
| A slow query arrives as a 502 | It ran past the store's 120 s limit | Narrow the question, or add a filter |
| Upload refused with 413 | Over 100 MB | Split the file |

## Flows

See [What fails a Phase](../flows/failures.md) for every run-time message. The common ones:

| You see | Fix |
|---|---|
| `input missing: 02_x/output/…` | A Phase folder was renamed; update the `working:` path |
| `declared output has no rows: …` | Find out why nothing matched, or add the file to `may_be_empty` |
| `This query returns more than 5,000,000 rows, so nothing was written` | Narrow the query, or extract it in parts |
| `the phase stopped before it finished` | The portal restarted or the stream dropped — re-run the Phase |
| `No streaming chunk received for 120.0s` | The LLM stream stalled — re-run the Phase |
| 409 when starting a run | A run of this Flow is already going |
| A Flow missing from the list | Its folder has no `flow.yml`, or `FLOWS_DIR` points elsewhere — Flows are found relative to where `praxis` was started |
