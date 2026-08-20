# The semantic layer

One deployable unit, three containers, run wherever Cube should live — a laptop or a server.
The workspace only ever talks to two URLs from here: Cube's, and the store's.

| container | what it is |
|---|---|
| `cube` | stock `cubejs/cube`, configured by `cube.js` to pull its data model from the store |
| `model-store` | the data model: files, refs and history, over HTTP. Built from `store/` |
| `cubestore` | Cube's queue and cache driver. Production mode refuses to run a query without it |

```
docker compose up -d
```

Required environment:

| | |
|---|---|
| `MODEL_STORE_READ_TOKEN` | given to Cube — read-only |
| `MODEL_STORE_WRITE_TOKEN` | given to the workspace — the only thing that writes the model |
| `CUBEJS_API_SECRET` | must equal the workspace's `api_secret` for this deployment |
| `CUBEJS_DB_*` | the warehouse Cube queries |

On Apple Silicon add `CUBESTORE_TAG=arm64v8`: `cubejs/cubestore:latest` is amd64 only and the
pull fails outright rather than emulating.

`CUBEJS_DEV_MODE=true` embeds Cube Store in the Cube process, so `cubestore` becomes
unnecessary — but it also disables JWT verification entirely. Only correct while nothing
outside this host can reach the port.

`cube.js` is the whole integration and it has two traps in it; the comments in the file say
which. `make store-check` drives all of this against a real Cube.
