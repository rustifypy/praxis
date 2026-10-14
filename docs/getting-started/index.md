# Get started

These five steps go from nothing to an answered question, in order. Each step ends where the next
one begins.

```mermaid
flowchart LR
    A[1. Install<br/>start the portal] --> B[2. Connect<br/>your warehouse]
    B --> C[3. Start<br/>the services]
    C --> D[4. Build<br/>the data model]
    D --> E[5. Ask<br/>your first question]
```

| Step | You do | You end with |
|---|---|---|
| [1. Install and start the portal](install.md) | Clone, fill in `.env`, `make web` | The portal open on its setup screen |
| [2. Connect your warehouse](connect.md) | Fill in the connection form | Connection settings stored; nothing contacted yet |
| [3. Start the services](services.md) | Download two compose files, `docker compose up -d`, paste two addresses | The semantic layer and the sandbox running and checked |
| [4. Build the data model](build-the-model.md) | Pick tables, click **Build** | Cube serving a model of your data |
| [5. Ask your first question](first-question.md) | Type a question | An answer, with the query and every step behind it |

## Before you begin

| You need | For |
|---|---|
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | the portal |
| Node.js 22 and npm | building the browser app |
| Docker with Compose | the two services the portal talks to |
| An Azure OpenAI deployment, API version `2025-03-01-preview` or later | every agent |
| A warehouse Docker can reach, and a user that can read it | Cube connects to it; the portal never does |

Steps 2 to 4 happen on the portal's setup screen, **Set up your data**, whose four numbered steps
open one after another. Steps 4 and 5 call the AI; the rest do not.

<div class="ui">
--8<-- "docs/.snippets/ui-setup.html"
<div class="ui-main narrow">
<div class="ui-h1">Set up your data</div>
<p class="ui-muted">The portal runs nothing itself. You give it a connection, it gives you a file per deployment, and you run those wherever you like — this machine or a server. Then it needs the addresses back, and it builds your data model.</p>
<ol class="ui-steps">
<li class="done"><span class="ui-n">✓</span><div class="ui-body"><div class="ui-h2">Your database or warehouse</div><strong>retail</strong> · PostgreSQL — stored, and the deployment secrets are minted.</div></li>
<li class="live"><span class="ui-n">2</span><div class="ui-body"><div class="ui-h2">The semantic layer</div><p class="ui-muted">Cube, the model store that holds your data model, and Cube Store. Everything the agent answers comes through it.</p><span class="ui-btn">⤓ Download docker-compose.yml</span></div></li>
<li class="later"><span class="ui-n">3</span><div class="ui-body"><div class="ui-h2">The sandbox</div><span class="ui-faint">Once the previous step is done.</span></div></li>
<li class="later"><span class="ui-n">4</span><div class="ui-body"><div class="ui-h2">The data model</div><span class="ui-faint">Once the previous step is done.</span></div></li>
</ol>
</div>
</div>
<p class="ui-caption">The setup screen once step 1 is done — the semantic layer is next.</p>


**Start:** [1. Install and start the portal](install.md)
