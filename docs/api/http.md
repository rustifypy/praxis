# HTTP

Every route below needs the portal token — `?token=<token>` once, after which the
`praxis_token` cookie (HTTP-only, SameSite=Strict) carries it — except `GET /api/healthz`.
Without it: `401 Unauthorized. Open the URL printed by praxis.`

Errors are JSON `{"detail": "..."}`. Statuses worth knowing everywhere:

| Status | Means |
|---|---|
| 409 `a job is running — wait for it to finish` | a model build holds the machine-wide job lock |
| 503 `no Cube deployment is configured` | no model store address is stored |
| 503 (sandbox) | the sandbox is not configured or did not answer |
| 422 | the request body failed validation |

**Streaming routes** answer `text/event-stream`: `data: {event}\n\n` frames carrying the
[event format](../architecture/events.md). They are POSTs, read with `fetch`. Closing the
connection cancels the turn.

The route list is held by `tests/surface.json`, and `tests/test_docs.py` fails when a route there
is missing from this page.

## Onboarding — `/api/onboarding`

| Method | Path | Does |
|---|---|---|
| GET | `/api/onboarding/connection` | The current connection, or `null` |
| POST | `/api/onboarding/connection` | Store warehouse settings and mint the three secrets. Body `{description, driver, values}`. 400 for a missing required setting or an unknown key |
| PATCH | `/api/onboarding/connection` | Edit description or settings; a blank value keeps what is stored |
| POST | `/api/onboarding/connection/probe` | Check health now, ignoring the 15 s cache |
| GET | `/api/onboarding/deployment/{service}/compose` | Download the compose file for `semantic` or `sandbox`. **The only route that returns credential values.** 404 before connecting; 503 if it cannot be rendered |
| PUT | `/api/onboarding/deployment` | Check and store `{store_url, sandbox_url}`. Returns `{checked, connection}`; only addresses that pass are saved |
| GET | `/api/onboarding/pipeline/tables` | The cached tables for the picker. Never opens the warehouse |
| POST | `/api/onboarding/pipeline/tables/closure` | Tables a selection pulls in through joins, and tables nothing can join to. Body `{schema_name, tables}` |
| GET | `/api/onboarding/pipeline/job` | The running job and its buffered lines; `{"id": null}` when idle |
| DELETE | `/api/onboarding/pipeline/job` | Cancel the running job |
| GET | `/api/onboarding/pipeline/job/stream` | The job's narration as SSE, replayed from the buffer on connect |
| POST | `/api/onboarding/pipeline/{stage}` | Start a stage (`read`, `cubes`, `describe`, `views`). 404 unknown stage; 409 a job is already running |

## Model editor — `/api/ide`

Workspace refusals map to: `ref` → 409, `path` → 400, `missing` → 404, `rejected` → 400,
`store` → 502.

| Method | Path | Does |
|---|---|---|
| GET | `/api/ide/model` | The compiled model on `?ref=` (default `main`). 200 with `serving: false` when Cube is down |
| GET | `/api/ide/model/summary` | One line per entity |
| GET | `/api/ide/refs` | Branches, each with its number of changed files |
| POST | `/api/ide/refs` | Create a branch. Body `{name, from_ref}` |
| DELETE | `/api/ide/refs/{ref}` | Delete a branch and its draft. 409 for `main` |
| GET | `/api/ide/refs/{ref}/diff` | Changed files, the patch, and which saved queries would break |
| POST | `/api/ide/refs/{ref}/merge` | Land the branch on main. `?force=` to merge past broken tiles, `?delete_after=` |
| GET | `/api/ide/files` | Files on `?ref=`, with draft state |
| GET | `/api/ide/files/{path}` | Read a file from the draft |
| PUT | `/api/ide/files/{path}` | Save a file into the draft. Body `{ref, text}`. Compiles nothing |
| DELETE | `/api/ide/files/{path}` | Remove a file from the draft |
| POST | `/api/ide/discard` | Reset the draft, or one `path` of it |
| POST | `/api/ide/check` | **Validate**: compile the draft on a throwaway ref and check saved queries. 200 even when rejected |
| POST | `/api/ide/commit` | **Publish**: one commit of the draft to the branch |
| POST | `/api/ide/chats` | Create a model-editor chat bound to a branch |
| POST | `/api/ide/chats/{chat_id}/messages` | One editor turn, SSE. Body `{question}` |

## Chats — `/api/chats`

| Method | Path | Does |
|---|---|---|
| GET | `/api/chats` | Question chats, most recent first |
| POST | `/api/chats` | Create a chat. Body `{title}` |
| PATCH | `/api/chats/{chat_id}` | Rename. Body `{title}` |
| DELETE | `/api/chats/{chat_id}` | Delete the chat, its events and results, and its sandbox workspace |
| GET | `/api/chats/{chat_id}/events` | The transcript |
| POST | `/api/chats/{chat_id}/messages` | One question turn, SSE. Body `{question, files}` |
| POST | `/api/chats/{chat_id}/stop` | Cancel the turn in flight |
| POST | `/api/chats/{chat_id}/approve` | Answer a script approval. Body `{decision: "yes" \| "always" \| "no"}`. 404 if nothing is waiting |
| POST | `/api/chats/{chat_id}/files` | Upload (multipart `file`) to the sandbox; returns its profile. 413 over 100 MB |
| GET | `/api/chats/{chat_id}/files/{path}` | Download a workspace file |
| GET | `/api/chats/{chat_id}/preview/{path}` | The head of a workspace file, as a table or text |
| DELETE | `/api/chats/{chat_id}/files/{path}` | Delete a workspace file |

## Results — `/api/results`

| Method | Path | Does |
|---|---|---|
| GET | `/api/results/{result_id}` | One page of a stored result: `?offset=&limit=` (default 200) |
| GET | `/api/results/{result_id}/csv` | The whole result as CSV |

A result is addressed by id alone because the browser finds it from a `tool_end` event.

## Dashboards — `/api/dashboard`

| Method | Path | Does |
|---|---|---|
| GET | `/api/dashboard` | Boards, with tile count and last refresh |
| POST | `/api/dashboard` | Create a board. Body `{title}` |
| GET | `/api/dashboard/{dashboard_id}` | Tiles, layout, last rows, and a compile pass over every tile |
| PATCH | `/api/dashboard/{dashboard_id}` | Rename |
| DELETE | `/api/dashboard/{dashboard_id}` | Delete |
| POST | `/api/dashboard/{dashboard_id}/refresh` | Run every tile — all compile or none execute. 502 when Cube is not answering |
| POST | `/api/dashboard/{dashboard_id}/layout` | Save a layout version. Body `{spec, note}`. 400 with the validation errors |

## Flows — `/api/flows`

| Method | Path | Does |
|---|---|---|
| GET | `/api/flows` | Every Flow, with whether it runs and its errors and warnings |
| POST | `/api/flows` | Create a Flow folder. Body `{name, title, description}`. 409 if it exists |
| GET | `/api/flows/{flow}` | One Flow |
| GET | `/api/flows/{flow}/files` | Its definition files |
| GET | `/api/flows/{flow}/files/{path}` | Read one, as text |
| PUT | `/api/flows/{flow}/files/{path}` | Write one, then re-check the Flow. Body `{text}`. Returns `{path, ok, errors, warnings}` |
| DELETE | `/api/flows/{flow}/files/{path}` | Delete one; an emptied Phase folder goes too |
| GET | `/api/flows/{flow}/runs` | Its runs, newest first |
| POST | `/api/flows/{flow}/runs` | Start a run. Body `{brief}`. Returns `{run_id, current}`. 422 if the Flow cannot run |
| GET | `/api/flows/{flow}/runs/{run_id}` | `run.json`, plus each Phase's plan |
| DELETE | `/api/flows/{flow}/runs/{run_id}` | Delete a run. 409 while it is going |
| GET | `/api/flows/{flow}/runs/{run_id}/events` | The run's `events.jsonl` |
| POST | `/api/flows/{flow}/runs/{run_id}/advance` | Run Phases until the next Gate, SSE. 409 if done, failed, or already going |
| POST | `/api/flows/{flow}/runs/{run_id}/stop` | Stop the Phase in progress |
| POST | `/api/flows/{flow}/runs/{run_id}/approve` | Answer a script approval. Body `{choice}` (default `"no"`) |
| GET | `/api/flows/{flow}/runs/{run_id}/artifacts/{path}` | Read a run file, as text |
| PUT | `/api/flows/{flow}/runs/{run_id}/artifacts/{path}` | Edit an output at its Gate. 409 if not at a Gate or not that Phase's output. Later Phases become stale |
| GET | `/api/flows/{flow}/runs/{run_id}/preview/{path}` | The head of a run file, with every row counted |
| POST | `/api/flows/{flow}/runs/{run_id}/phases/{dir_name}/rerun` | Point the run back at a Phase. Does not execute it |
| POST | `/api/flows/chats` | Get or create a Flow's authoring chat. Body `{flow}` |
| GET | `/api/flows/chats/{chat_id}/events` | Its transcript |
| POST | `/api/flows/chats/{chat_id}/messages` | One authoring turn, SSE |
| POST | `/api/flows/chats/{chat_id}/stop` | Cancel the turn |

`chats` is reserved as a Flow name, because `/api/flows/chats` would otherwise be a Flow.

## MCP settings — `/api/mcp`

| Method | Path | Does |
|---|---|---|
| GET | `/api/mcp` | Endpoint URL, enabled flag, token, tool list, and client configurations |
| POST | `/api/mcp/enabled` | Switch MCP on or off. Body `{enabled}` |
| POST | `/api/mcp/rotate` | Mint a new MCP token |

## Other

| Method | Path | Does |
|---|---|---|
| GET | `/` | The browser app. 503 if it has not been built |
| GET | `/api/healthz` | `{"ok": true}`. No token needed |
