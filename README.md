# Praxis by rustify

[![Documentation](https://img.shields.io/badge/docs-rustify.tech%2Fpraxis-3355dd.svg)](https://rustify.tech/praxis/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Contributions welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg)](CONTRIBUTING.md)

Ask questions about your data in plain English, and run repeatable data work step by step.

[![A one-minute tour of the portal: a question answered with a chart, the semantic layer, and a Flow Run](.github/assets/portal-tour.gif)](https://rustify.tech/praxis/)

Praxis sits on top of your SQL warehouse (Postgres, Snowflake, BigQuery, Databricks and
more). It:

- **builds a semantic layer**: what each table means, how tables join, which numbers add up;
- **answers questions through Cube queries**, never SQL written by an AI, so every name it uses
  is checked against your model before anything reaches the warehouse;
- **runs Flows**: work you repeat, written down as a folder of numbered steps, with rows going to
  files rather than to the AI and a person checking each step.

```
browser ─▶ Praxis portal ──Cube JSON──▶ model store + Cube ──SQL──▶ your warehouse
                          └─Python────▶ sandbox (no database password)
```

## Quick start

You need Python with [uv](https://docs.astral.sh/uv/), Node.js, Docker and an Azure OpenAI key.

```bash
git clone https://github.com/rustifypy/praxis.git
cd praxis
cp .env.example .env      # your Azure OpenAI key and endpoint
make install
make web                  # builds the web app and opens the portal
```

Then **Data → Connect a database**, run the two `docker compose` files it gives you, paste their
addresses back, and build the model. The [getting started guide](docs/getting-started/index.md)
walks through it.

## Documentation

**[rustify.tech/praxis](https://rustify.tech/praxis/)** — or read it here in [`docs/`](docs/index.md):

| | |
|---|---|
| [Getting started](docs/getting-started/index.md) | install, connect, build the model, first question |
| [User guide](docs/user-guide/index.md) | chat, dashboards, running Flows, editing the model, MCP |
| [Flow authoring](docs/flows/index.md) | the folder layout, prepared work, every check that fails a Phase |
| [Operating](docs/operations/index.md) | deployment, configuration, security, upgrading, troubleshooting |
| [Architecture](docs/architecture/index.md) | layering, the query gate, events, storage, decisions |
| [API](docs/api/index.md) | HTTP routes and MCP tools |

`make docs` serves the documentation locally at http://127.0.0.1:8001.

## Development

```bash
make test         # deterministic tests: no containers, no AI calls
make lint         # ruff check and format check
make web-build    # rebuild the web app
make serve        # start the portal without rebuilding
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request, and the
[Code of Conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE)
