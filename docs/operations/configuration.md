# Configuration

The portal reads its own settings from `.env` in the directory it is started from (names are
case-insensitive). These are the portal's settings only. Warehouse credentials, Cube's secret,
service tokens and service ports are generated into the downloaded compose files and do **not**
belong here.

!!! note
    `pydantic-settings` reads `.env` into the portal's settings object and puts nothing into the
    process environment. A variable set only in `.env` is invisible to other tools.

## Model

`QUERY_MODEL` names a provider and a model, as `<provider>:<model>`. Everything before the first
`:` is the provider, so `ollama:qwen3:8b` is Ollama's `qwen3:8b`. `praxis` refuses to start when
the provider is unknown, its package is not installed, or its key is empty, and says which.

| Provider | `QUERY_MODEL` example | Install | Settings it reads |
|---|---|---|---|
| Azure OpenAI | `azure_openai:gpt-5.1` | included | `AZURE_OPENAI_*` |
| OpenAI | `openai:gpt-5.1` | included | `OPENAI_API_KEY`, `OPENAI_BASE_URL` |
| An OpenAI-compatible server (vLLM, LM Studio, llama.cpp) | `openai_compatible:qwen3-32b` | included | `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_API_KEY` |
| Anthropic | `anthropic:claude-opus-5-5` | `praxis[anthropic]` | `ANTHROPIC_API_KEY` |
| Gemini | `google_genai:gemini-2.5-pro` | `praxis[google]` | `GOOGLE_API_KEY` |
| Ollama | `ollama:qwen3:8b` | `praxis[ollama]` | `OLLAMA_BASE_URL`, `OLLAMA_REASONING` |
| LiteLLM, for any other provider | `litellm:bedrock/<model-id>` | `praxis[litellm]` | `LITELLM_API_KEY`, `LITELLM_API_BASE` |

`praxis[all]` installs every provider, and `make install` does the same for a checkout.

| Setting | Default | What it does |
|---|---|---|
| `QUERY_MODEL` | `azure_openai:gpt-5.1` | The model every agent uses |
| `MODELGEN_MODEL` | — | The model for building the model's descriptions and views, which makes one structured call per table. Empty uses `QUERY_MODEL`; set it to put that bulk work on a cheaper model |
| `REASONING_EFFORT` | `medium` | How hard the model thinks. OpenAI and Azure take it as the reasoning effort, Anthropic as `effort`, LiteLLM as `reasoning_effort`; Gemini only shows its thoughts and chooses its own budget. Empty asks no provider for reasoning, which a model that cannot reason requires |
| `REASONING_SUMMARY` | `detailed` | OpenAI and Azure only. `detailed` is what actually fills the thinking panel; `auto` often emits nothing |
| `ON_PROVIDER_CHANGE` | `refuse` | What a chat does when its earlier answers came from another provider, whose blocks the new one would refuse. `refuse` ends the turn and says to start a new chat; `new_thread` answers without the earlier turns, which stay in the transcript. Switching back finds the original thread |
| `AZURE_OPENAI_API_KEY` | — | Azure OpenAI key |
| `AZURE_OPENAI_ENDPOINT` | — | e.g. `https://<resource>.openai.azure.com` |
| `AZURE_OPENAI_API_VERSION` | — | `2025-03-01-preview` or later (the example uses `2025-04-01-preview`). Earlier versions return no reasoning summaries |
| `AZURE_OPENAI_DEPLOYMENT` | — | Only if the deployment is not named after the model |
| `OPENAI_API_KEY` | — | OpenAI key |
| `OPENAI_BASE_URL` | — | Only for a proxy in front of OpenAI's own API |
| `OPENAI_COMPATIBLE_BASE_URL` | — | e.g. `http://localhost:8000/v1`. Chat completions only, so the thinking panel stays empty |
| `OPENAI_COMPATIBLE_API_KEY` | `unused` | Most local servers ignore it, but the client will not start without one |
| `ANTHROPIC_API_KEY` | — | Anthropic key |
| `GOOGLE_API_KEY` | — | Gemini API key |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where Ollama listens |
| `OLLAMA_REASONING` | `false` | Ask the model to think. Only for a model that can: Ollama refuses the request otherwise |
| `LITELLM_API_KEY` | — | Passed to LiteLLM as `api_key`. LiteLLM also reads its providers' own variables from the process environment — not from `.env` |
| `LITELLM_API_BASE` | — | Passed to LiteLLM as `api_base` |

!!! warning "A small local model is a weak agent"
    Any model that calls tools will run, but the agent writes Cube queries, recovers from the
    gate's rejections, and keeps a Flow Phase within its model-call limit. Small local models
    do this poorly, so expect failed Phases before the plumbing is the problem.

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
| `DATA_DIR` | `~/.praxis` | Holds `praxis.db`, `jobs.lock` and `drafts/`. A scratch `DATA_DIR` is a whole scratch install, connection included |
| `FLOWS_DIR` | `flows` | Where Flows live. Relative paths resolve against the directory `praxis` was started from; set an absolute path to pin it |

## Behaviour

| Setting | Default | What it does |
|---|---|---|
| `APPROVE_SCRIPTS` | `true` | Each Python script in a chat waits for a click before it runs, unless that chat chose "always". This is a courtesy, not the safety boundary — the sandbox holding no credential is |

Changes take effect on the next start.
