# Operating

This section is for whoever brings up praxis's services and keeps them running. A first
install is [Get started](../getting-started/index.md); this is everything after it.

**The portal runs nothing.** It holds credentials, mints secrets, renders a `docker compose` file
per service, and is told where those services are. It never starts, stops or watches a container.
So there are three things to run, owned separately:

```mermaid
flowchart TB
    subgraph you["Your machine — praxis (this repo)"]
        P[portal<br/>127.0.0.1:8420]
        DB[(~/.praxis/praxis.db)]
        F[flows/]
    end
    subgraph sem["Semantic deployment — compose project praxis-semantic"]
        MS[modelstore<br/>127.0.0.1:8000]
        CA[cube-api<br/>not published]
        CS[cubestore]
        MS --> CA --> CS
    end
    subgraph sb["Sandbox — compose project praxis-sandbox"]
        S[sandbox<br/>127.0.0.1:8600]
    end
    W[(your warehouse)]
    P -- "store_url + token" --> MS
    P -- "sandbox_url + token" --> S
    CA -- SQL --> W
    P --- DB
    P --- F
```

| Page | What it covers |
|---|---|
| [The two deployments](deployment.md) | What each compose file contains, and the connect → hand over → paste addresses sequence |
| [Configuration](configuration.md) | Every setting in `.env` |
| [Security](security.md) | Where secrets live, what holds which credential, what is exposed |
| [Upgrading](upgrading.md) | Image pins, and why a pin is the only compatibility control |
| [Troubleshooting](troubleshooting.md) | Every common error, by the words you see |
