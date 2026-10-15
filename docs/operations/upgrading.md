# Upgrading

## The pin is the only compatibility control

Neither service offers a version handshake the portal can rely on. The model store reports no
contract at all; the sandbox reports a contract number (`1` today), and a mismatch is only a
warning. So **every image in a compose file is pinned**, and the pin is how a portal and its
services are kept compatible. A mismatch surfaces as an error at the first call, never at
startup.

| Image | Pinned to | Set in |
|---|---|---|
| Model store | `skshmjn/modelstore:0.1.0` | `core/deployenv.py` → `MODELSTORE_IMAGE` |
| Cube | `cubejs/cube:v1.7.26` | `core/deployenv.py` → `CUBE_VERSION` |
| Cube Store | `cubejs/cubestore:v1.7.26` (`-arm64v8` on arm64) | `core/deployenv.py` → `CUBESTORE_IMAGE` |
| Sandbox | `skshmjn/praxis-sandbox:0.1.0` | `core/deployenv.py` → `SANDBOX_IMAGE` |

## Upgrading the portal

```bash
git pull
make install
make web          # rebuilds the browser app and restarts
```

If the new portal moved a pin, **download a fresh compose file** from the portal and run
`docker compose up -d` with it. Do not edit the image line in your old file: the rest of the file
may have changed too.

## Upgrading a service

Move the pin in `core/deployenv.py`, re-download the file, `docker compose up -d`. The `model`
volume carries over, so the data model and its history survive.

## Moving an install from analyst-agent

This project was called analyst-agent. The rename changed the names an existing install keeps its
data under, so an install from before it needs these steps once. Nothing is deleted; the old
volumes and directory stay until you remove them.

| What | Before | After |
|---|---|---|
| Data directory | `~/.analyst/analyst.db` | `~/.praxis/praxis.db` |
| Compose projects | `analyst-semantic`, `analyst-sandbox` | `praxis-semantic`, `praxis-sandbox` |
| Command | `analyst` | `praxis` |

1. **Stop both deployments**, in each old folder: `docker compose down` (no `-v`: that deletes the
   volumes).
2. **Move the database** (skip if you set `DATA_DIR`; rename only the file inside it):

    ```bash
    mv ~/.analyst ~/.praxis && mv ~/.praxis/analyst.db ~/.praxis/praxis.db
    ```

3. **Download both compose files** from the portal again (`make serve`, then **Data**) and create
   the new volumes without starting anything: `docker compose up --no-start` beside each.
4. **Copy the volumes across.** `model` holds every data model and `workspaces` the uploaded
   files; `cubestore` is a cache and rebuilds itself.

    ```bash
    docker run --rm -v analyst-semantic_model:/from -v praxis-semantic_model:/to \
      alpine cp -a /from/. /to/
    docker run --rm -v analyst-sandbox_workspaces:/from -v praxis-sandbox_workspaces:/to \
      alpine cp -a /from/. /to/
    ```

5. **Start both:** `docker compose up -d` beside each new file. The addresses are unchanged, so
   the portal needs no new connection.

Once the portal answers questions again, `docker volume rm` the three `analyst-*` volumes.
The browser keeps its theme choice and editor pane widths under new keys, so set those once more.

## The database has no migrations

`praxis.db` is created with `CREATE TABLE IF NOT EXISTS`. A release that adds a **table** works
on an existing file. A release that adds or changes a **column** does not, and needs the file
deleted — which also deletes the connection, chats and dashboards. Release notes say when this is
needed. Flows are files, not rows, and are never affected.
