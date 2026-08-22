import re

from ..core.deployment import Deployment

_VAR = re.compile(r"\$\{(\w+)\}")

# Where the renderer writes a block rather than a value, so it cannot be a `${...}` scalar:
# which CUBEJS_DB_* keys a warehouse needs is per-connection, so no fixed set of lines could
# serve every one of them.
CREDENTIALS = "# ANALYST:CREDENTIALS"

# The hand-over is rendered from these rather than read off disk, so a wheel can produce one.
# The copies under `services/` are what the images are built from and carry a `build:` each;
# these are the same files with the build context dropped and the tags pinned.
SEMANTIC = """\
# The semantic layer. Paste this wherever it should run — the workspace only ever talks to one
# URL here, the model store's.
#
# The store is the only door. Cube's port is not published; queries reach it through
# `model-store` at `/cube/...`, which mints Cube's JWT itself, so CUBEJS_API_SECRET never
# leaves this file.
#
# Cube Store is here because production mode makes it the queue/cache driver and refuses to
# run a query without it.
#
# To move the published port, change the left-hand number only. On Apple Silicon set the
# cubestore image to `cubejs/cubestore:arm64v8` — `latest` is amd64 only and the pull fails
# outright rather than falling back. A driver whose Cube build needs the JDK (Databricks) runs
# `analyst-cube:1-jdk` with `platform: linux/amd64`.

name: analyst-semantic

services:
  model-store:
    image: analyst-model-store:1
    environment:
      MODEL_STORE_READ_TOKEN: ${MODEL_STORE_READ_TOKEN}
      MODEL_STORE_WRITE_TOKEN: ${MODEL_STORE_WRITE_TOKEN}
      CUBE_URL: http://cube:4000
      CUBEJS_API_SECRET: ${CUBEJS_API_SECRET}
    ports:
      - "127.0.0.1:8500:8500"
    volumes:
      - model:/data

  cube:
    image: analyst-cube:1
    depends_on: [model-store, cubestore]
    environment:
      # The warehouse, as it was configured in the portal.
      # ANALYST:CREDENTIALS
      CUBEJS_DEV_MODE: ${CUBEJS_DEV_MODE}
      CUBEJS_API_SECRET: ${CUBEJS_API_SECRET}
      CUBEJS_CUBESTORE_HOST: cubestore
      CUBEJS_CUBESTORE_PORT: 3030
      MODEL_STORE_URL: http://model-store:8500
      MODEL_STORE_TOKEN: ${MODEL_STORE_READ_TOKEN}
    expose:
      - "4000"

  cubestore:
    image: cubejs/cubestore:latest
    environment:
      CUBESTORE_HTTP_PORT: 3030
    volumes:
      - cubestore:/cube/data

volumes:
  model:
  cubestore:
"""

SANDBOX = """\
# The code sandbox. Its own deployment, sharing nothing with the semantic layer: no network,
# no volume, no secret.
#
# It holds no credential — it cannot sign a Cube token and has no warehouse password — so
# there is no route through it to a number that did not come through the gate. It *can* reach
# the network, which is why the port is bound to loopback and the token is not optional.
#
# To move the published port, change the left-hand number only.

name: analyst-sandbox

services:
  sandbox:
    image: analyst-sandbox:1
    environment:
      SANDBOX_TOKEN: ${SANDBOX_TOKEN}
    ports:
      - "127.0.0.1:8600:8600"
    volumes:
      - workspaces:/workspaces
    mem_limit: 2g
    memswap_limit: 2g
    cpus: 2
    pids_limit: 512
    init: true
    security_opt:
      - no-new-privileges
    cap_drop:
      - ALL

volumes:
  workspaces:
"""


def semantic(deployment: Deployment) -> str:
    return render(
        SEMANTIC,
        {
            "CUBEJS_API_SECRET": deployment.api_secret,
            "MODEL_STORE_READ_TOKEN": deployment.store_read_token,
            "MODEL_STORE_WRITE_TOKEN": deployment.store_token,
            # Dev mode embeds Cube Store and disables JWT verification entirely: a decision to
            # make while looking at the file, not a default to inherit.
            "CUBEJS_DEV_MODE": "false",
        },
        blocks={CREDENTIALS: [f"{key}: {value}" for key, value in deployment.credentials.items()]},
    )


def sandbox(deployment: Deployment) -> str:
    return render(SANDBOX, {"SANDBOX_TOKEN": deployment.sandbox_token})


def render(text: str, values: dict[str, str], blocks: dict[str, list[str]] | None = None) -> str:
    lines = []
    for line in text.splitlines():
        marker = next((name for name in (blocks or {}) if line.strip().startswith(name)), None)
        if marker:
            # A block is lines, not a scalar, so it replaces the marker's whole line at its
            # indentation — which is what keeps the YAML valid.
            padding = " " * (len(line) - len(line.lstrip()))
            lines += [f"{padding}{written}" for written in blocks[marker]]
            continue
        for name in _VAR.findall(line):
            # Every variable is required, and empty counts as missing the way it does to
            # compose: an empty secret renders as a key with nothing after it and hands over a
            # deployment that starts cleanly and refuses every query.
            if not values.get(name):
                raise RuntimeError(f"{name} is required by the compose file and there is none")
            line = line.replace(f"${{{name}}}", values[name])
        lines.append(line)

    if leftover := [line.strip() for line in lines if "$" in line]:
        # A file with an unresolved variable reads as complete and dies at `up`.
        raise RuntimeError(f"unresolved in the rendered file: {'; '.join(leftover)}")
    return "\n".join(lines) + "\n"
