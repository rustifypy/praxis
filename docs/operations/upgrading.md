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
| Sandbox | `skshmjn/analyst-sandbox:0.1.0` | `core/deployenv.py` → `SANDBOX_IMAGE` |

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

## The database has no migrations

`analyst.db` is created with `CREATE TABLE IF NOT EXISTS`. A release that adds a **table** works
on an existing file. A release that adds or changes a **column** does not, and needs the file
deleted — which also deletes the connection, chats and dashboards. Release notes say when this is
needed. Flows are files, not rows, and are never affected.
