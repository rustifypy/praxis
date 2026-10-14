# 1. Install and start the portal

Step 1 of 5. At the end of it, the portal is open in your browser on its setup screen.
{ .ax-step }

You need the tools in [Before you begin](index.md#before-you-begin).

## Install and start

```bash
git clone https://github.com/rustifypy/analyst-agent.git
cd analyst-agent
cp .env.example .env      # fill in the AZURE_OPENAI_* lines
make install              # uv sync --all-groups
make web                  # build the browser app, then start the portal
```

`analyst` prints a URL and opens it in your browser:

```text
Analyst portal is running at:
    http://127.0.0.1:8420/?token=x2W_lbP79sgZBAxfrNbpdBpGefsgcuc2
Press Ctrl-C to stop.
```

**The token changes on every start.** It is minted when the server starts and is never stored.
The first request carrying `?token=` sets an `analyst_token` cookie (HTTP-only, SameSite=Strict),
so later page loads in the same browser do not need it. Any other request gets
`401 Unauthorized. Open the URL printed by analyst.`

!!! warning "Always start the portal with `analyst`"
    `analyst` takes no arguments. Host, port and data directory come from `.env`
    (`WEB_PORT=8481 uv run analyst` moves it). Do not run `uvicorn analyst_agent.api.server:app`
    directly: the token is printed only by the launcher, so a portal started that way cannot be
    logged into.

You now see **Set up your data**, the setup screen, with four numbered steps.

**Next:** [2. Connect your warehouse](connect.md)

## Good to know

### The browser app is built, not committed

Vite hashes its file names, so a committed build would conflict on every branch. A fresh clone
therefore has no browser app, and `analyst` answers
`503 The frontend has not been built. Run make web.` until you run `make web-build` (or `make web`,
which builds and then serves). The build goes into `src/analyst_agent/api/static/`, inside the
package, because that is the only directory the Python wheel ships.

For frontend work, `npm run dev` in `src/web/` gives hot reload against a running `analyst`.

### Make targets

| Command | Does |
|---|---|
| `make install` | `uv sync --all-groups` |
| `make web` | build the browser app, then start the portal |
| `make web-build` | build the browser app only |
| `make serve` | start the portal only (`uv run analyst`) |
| `make test` | the test suite — no containers, no LLM calls |
| `make lint` | `ruff check` and `ruff format --check` |
| `make fmt` | format and auto-fix |
| `make docs` | serve this documentation with live reload at <http://127.0.0.1:8001> |
| `make docs-build` | build this documentation into `site/`, failing on any broken link |

Nothing in the Makefile starts a service. The semantic deployment and the sandbox are published
images, brought up from the compose files the portal gives you — see
[The two deployments](../operations/deployment.md).
