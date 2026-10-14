# 3. Start the services

Step 3 of 5 — setup steps 2 and 3, **The semantic layer** and **The sandbox**. At the end of it,
both services are running and the portal has checked each one.
{ .ax-step }

The portal starts nothing itself. It gives you a compose file for each service, you run it, and
you tell the portal the address. There are two services, and they share nothing:

| | The semantic layer | The sandbox |
|---|---|---|
| What it is | The model store, Cube and Cube Store | Where the agent runs Python |
| Holds | Your warehouse credentials | No credential at all |
| Address on this machine | `http://localhost:8000` | `http://localhost:8600` |

## The semantic layer

1. Click **Download docker-compose.yml**. The file contains your warehouse credentials — keep it
   private, and do not commit it.
2. Put it in a folder of its own, and in that folder run:

    ```bash
    docker compose up -d
    ```

3. Type its address — `http://localhost:8000` on the same machine — and click
   **Check and continue**.

The portal checks that something answers at the address, that it is the model store, and that
it accepts the token in the file. A wrong address is refused with a reason; see
[Troubleshooting](../operations/troubleshooting.md#connecting).


<div class="ui">
--8<-- "docs/.snippets/ui-setup.html"
<div class="ui-main narrow">
<ol class="ui-steps">
<li class="live"><span class="ui-n">2</span><div class="ui-body">
<div class="ui-h2">The semantic layer</div>
<p class="ui-muted">Cube, the model store that holds your data model, and Cube Store. Everything the agent answers comes through it.</p>
<div class="ui-row"><span class="ui-btn">⤓ Download docker-compose.yml</span><span class="ui-faint">It holds your warehouse password and this deployment's secrets.</span></div>
<p style="margin-top:10px">Save it anywhere, then in that directory — nothing else needs to be there:</p>
<div class="ui-code">docker compose up -d</div>
<label class="ui-field"><span>Its address, as this machine reaches it</span><span class="ui-input focus">http://localhost:8000</span></label>
<span class="ui-btn primary">Check and continue</span>
<div class="ui-banner good">It answered.</div>
</div></li>
</ol>
</div>
</div>
<p class="ui-caption">Setup step 2, after <b>Check and continue</b>. A wrong address shows a red reason here instead.</p>

!!! warning "Apple Silicon"
    If `docker compose up` fails with **no matching manifest**, the Cube Store line is for the
    wrong architecture. The file's comment marks the line to change. See
    [The two deployments](../operations/deployment.md).

## The sandbox

The same three steps, with its own file, in its own folder. Its address is
`http://localhost:8600`.

When both are checked, setup step 4 opens and the portal starts reading your warehouse's tables.
This is the first time anything connects to the warehouse — a wrong password shows up here, not
earlier.

What is inside each compose file, and why there are two, is in
[The two deployments](../operations/deployment.md).

**Next:** [4. Build the data model](build-the-model.md)
