# Security

## Who holds what

| Secret | Lives in | Who uses it |
|---|---|---|
| Warehouse credentials | `praxis.db` (`deployment` row), and the semantic compose file | Cube only |
| `CUBEJS_API_SECRET` | `praxis.db`, and the semantic compose file | the model store, to sign Cube JWTs. Never leaves that compose file at run time |
| Model store token | `praxis.db`, and the semantic compose file | the portal, to call the store |
| Sandbox token | `praxis.db`, and the sandbox compose file | the portal, to call the sandbox |
| Portal token | memory only, new each start | your browser |
| MCP token | `praxis.db` | external agents calling `/mcp` |

The three deployment secrets are minted with `secrets.token_urlsafe` when you first connect, and
leave the portal only inside the compose files you download.

## The database file

`~/.praxis/praxis.db` holds the warehouse credentials **as plaintext JSON**. The portal sets
it to mode `600` (readable only by you) every time it starts. Keep `DATA_DIR` on a disk you
trust, and treat a copy of the file as a copy of the password.

`GET /api/onboarding/deployment/{service}/compose` is the only route that returns credential
values, and it answers as a download (`Content-Disposition: attachment`).

## What the agents cannot do

- **No agent writes SQL.** Every query is Cube JSON and passes the
  [query gate](../architecture/query-gate.md); only joins and measures the model defines can be
  used.
- **The sandbox holds no credential.** It cannot sign a Cube token and has no warehouse
  password, so the Python the agent runs there can only see rows the gate already let through.
- **Script approval** (`APPROVE_SCRIPTS`) shows each chat script before it runs. In a Flow, an
  unchanged prepared script runs without approval because it was reviewed when it was saved;
  any other script asks.
- **The question chat cannot start a Flow run**, and no agent can refresh a dashboard. Both are
  buttons, so an instruction like "tidy up this board" cannot turn into a dozen warehouse
  queries.

## What is exposed on the network

Every published port binds to `127.0.0.1` by default: the portal (`8420`), the model store
(`8000`) and the sandbox (`8600`). Cube's port is not published at all. To expose a service
beyond the machine, change the left-hand side of its compose `ports:` line deliberately — and note
that the sandbox can reach the network, which is why its token is mandatory.

## MCP

`/mcp` has its own Bearer token, separate from the portal token, and it survives restarts. When
MCP is switched off, `/mcp` answers 404. **Rotate** on the Agents page mints a new token and
breaks every pasted client configuration. MCP tools are read-only: search the model, describe
it, and run a gated query capped at `MAX_ROWS`.
