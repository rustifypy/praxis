# Configuration

The portal reads its own settings from `.env` in the directory it is started from (names are
case-insensitive). These are the portal's settings only. Warehouse credentials, Cube's secret,
service tokens and service ports are generated into the downloaded compose files and do **not**
belong here.

!!! note
    `pydantic-settings` reads `.env` into the portal's settings object and puts nothing into the
    process environment. A variable set only in `.env` is invisible to other tools.

## Model

| Setting | Default | What it does |
|---|---|---|
| `AZURE_OPENAI_API_KEY` | — | Azure OpenAI key |
| `AZURE_OPENAI_ENDPOINT` | — | e.g. `https://<resource>.openai.azure.com` |
| `AZURE_OPENAI_API_VERSION` | — | `2025-03-01-preview` or later (the example uses `2025-04-01-preview`). Earlier versions return no reasoning summaries |
| `AZURE_OPENAI_DEPLOYMENT` | — | Only if the deployment is not named after the model |
| `QUERY_MODEL` | `azure_openai:gpt-5.1` | The model every agent uses, through the Responses API |
| `REASONING_EFFORT` | `medium` | Passed as the reasoning effort |
| `REASONING_SUMMARY` | `detailed` | `detailed` is what actually fills the thinking panel; `auto` often emits nothing |

## Limits

| Setting | Default | What it does |
|---|---|---|
| `MAX_ROWS` | `5000` | Row cap on a chat query, a dashboard tile and an MCP query, applied even when the query sets no limit |
| `QUERY_TIMEOUT_S` | `60` | Timeout for a query sent through the model store. Must stay under the store's own 120 s |
| `FLOW_PAGE_ROWS` | `50000` | Rows per request when a Flow `extract` pages through a result. 50,000 is Cube's per-request maximum |
| `FLOW_MAX_ROWS` | `5000000` | The most rows one `extract` writes. A larger result writes nothing and fails that step |

## Location

| Setting | Default | What it does |
|---|---|---|
| `WEB_HOST` | `127.0.0.1` | Address the portal listens on |
| `WEB_PORT` | `8420` | Port the portal listens on |
| `DATA_DIR` | `~/.analyst` | Holds `analyst.db`, `jobs.lock` and `drafts/`. A scratch `DATA_DIR` is a whole scratch install, connection included |
| `FLOWS_DIR` | `flows` | Where Flows live. Relative paths resolve against the directory `analyst` was started from; set an absolute path to pin it |

## Behaviour

| Setting | Default | What it does |
|---|---|---|
| `APPROVE_SCRIPTS` | `true` | Each Python script in a chat waits for a click before it runs, unless that chat chose "always". This is a courtesy, not the safety boundary — the sandbox holding no credential is |

Changes take effect on the next start.
