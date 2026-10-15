import json
import platform
import re

from .deployment import Deployment

CUBE_VERSION = "v1.7.26"

# How long the store waits on Cube before answering 502. Rendered into the hand-over rather than
# left at the store's own default, because every Cube-bound cap on this side has to sit under it:
# above it they can never elapse and a slow query arrives as a proxy failure with nothing to act
# on. `test_timeouts` is what holds the two in order.
CUBE_TIMEOUT_S = 120.0

# The model store is built and published from its own repository. Pinned, and this is the line
# to move when it is upgraded: the store reports no contract, so which build a hand-over names
# is the only compatibility control the portal has. `latest` would make that silently untrue on
# whichever machine happened to re-pull.
MODELSTORE_IMAGE = "skshmjn/modelstore:0.1.0"
CUBE_IMAGE = f"cubejs/cube:{CUBE_VERSION}"

# Cube Store publishes one architecture per tag and no multi-arch manifest, so a pull for the
# wrong one fails with "no matching manifest" instead of falling back — the hand-over cannot
# name a tag that works everywhere. The architecture the portal is running on is the only signal
# available at render time, and it is right whenever the file is run on this machine, which is
# what the download button implies. Taken to a host of the other kind, this is the one line to
# change, and the rendered file's comment says so.
_ARM = platform.machine() in ("arm64", "aarch64")
CUBESTORE_IMAGE = f"cubejs/cubestore:{CUBE_VERSION}{'-arm64v8' if _ARM else ''}"

# The sandbox is built and published from its own repository too. Pinned for the same reason as
# the store: it is the analysis stack a saved script reproduces against, so which build a
# hand-over names is what makes last month's script mean the same thing today.
SANDBOX_IMAGE = "skshmjn/praxis-sandbox:0.1.0"

# What the sandbox gives a script, and a profile of an upload. Rendered into the hand-over rather
# than left at the image's defaults so `sandbox.client`'s caps can be held *above* them from this
# side — a client that gives up first abandons a run that succeeded over there. `test_timeouts`
# is what holds the pairs in order.
SANDBOX_EXEC_TIMEOUT_S = 120.0
SANDBOX_PROFILE_TIMEOUT_S = 60.0

_VAR = re.compile(r"\$\{(\w+)\}")

# What each warehouse cannot connect without: the variable, and the plain word for it.
#
# Both halves are here because the refusal is read by a person. The variable names are an
# implementation detail of the engine that consumes them and appear in no message and on no
# screen — someone connecting a database is told they are owed *a host and a database*, not the
# spelling of an environment variable. The form owns the rest of the presentation: labels,
# placeholders, which fields are advanced, the soft warnings.
#
# Only what defines *which* database this is. A password is deliberately absent: trust
# authentication, key-pair authentication and token-in-the-URL are all real, so a blank one is a
# warning the form draws and never a refusal here.
#
# A driver missing from this map is not refused. An unlisted warehouse is configured by typing
# its own variables, and inventing a required set for it would refuse exactly that case.
REQUIRED: dict[str, dict[str, str]] = {
    "postgres": {
        "CUBEJS_DB_HOST": "a host",
        "CUBEJS_DB_NAME": "a database",
        "CUBEJS_DB_USER": "a user",
    },
    "mysql": {
        "CUBEJS_DB_HOST": "a host",
        "CUBEJS_DB_NAME": "a database",
        "CUBEJS_DB_USER": "a user",
    },
    "mssql": {
        "CUBEJS_DB_HOST": "a host",
        "CUBEJS_DB_NAME": "a database",
        "CUBEJS_DB_USER": "a user",
    },
    "redshift": {
        "CUBEJS_DB_HOST": "a host",
        "CUBEJS_DB_NAME": "a database",
        "CUBEJS_DB_USER": "a user",
    },
    "clickhouse": {"CUBEJS_DB_HOST": "a host"},
    "snowflake": {
        "CUBEJS_DB_SNOWFLAKE_ACCOUNT": "an account identifier",
        "CUBEJS_DB_SNOWFLAKE_WAREHOUSE": "a warehouse",
        "CUBEJS_DB_NAME": "a database",
        "CUBEJS_DB_USER": "a user",
    },
    "bigquery": {
        "CUBEJS_DB_BQ_PROJECT_ID": "a project",
        "CUBEJS_DB_BQ_CREDENTIALS": "a service account key",
    },
    # The token may be carried inside the JDBC URL instead of on its own, so the URL is the only
    # one always owed. The policy flag is the driver's own: it refuses to load without it, which
    # is why the form ticks it rather than asking.
    "databricks-jdbc": {
        "CUBEJS_DB_DATABRICKS_URL": "a JDBC URL",
        "CUBEJS_DB_DATABRICKS_ACCEPT_POLICY": "the accepted driver licence",
    },
    # Athena is why `connection.ENV_PREFIXES` has a second entry: its variables are AWS ones
    # rather than database ones, and a query has nowhere to write its result without the output
    # location.
    "athena": {
        "CUBEJS_AWS_KEY": "an access key",
        "CUBEJS_AWS_SECRET": "a secret key",
        "CUBEJS_AWS_REGION": "a region",
        "CUBEJS_AWS_S3_OUTPUT_LOCATION": "an S3 location for query results",
    },
    "trino": {
        "CUBEJS_DB_HOST": "a host",
        "CUBEJS_DB_USER": "a user",
        "CUBEJS_DB_PRESTO_CATALOG": "a catalog",
    },
    # DuckDB with no path is an in-memory database, which starts happily.
    "duckdb": {},
}

# Where the renderer writes a block rather than a value, so it cannot be a `${...}` scalar:
# which CUBEJS_DB_* keys a warehouse needs is per-connection, so no fixed set of lines could
# serve every one of them.
CREDENTIALS = "# PRAXIS:CREDENTIALS"

# Cube's config, shipped as an inline compose config rather than baked into an image of ours —
# so the hand-over needs no registry but Docker Hub. A block for the same reason as the
# credentials: it contains a `$` (the anchor in SAFE_REF), and compose interpolates the whole
# file after parsing it, so every one has to be doubled and the renderer's unresolved-variable
# guard has to skip it.
CUBE_PY = "# PRAXIS:CUBE_PY"

# One Cube process serves every ref: `context_to_app_id` keys the compiled-model cache on
# <ref>@<rev> and `repository_factory` reads that revision's immutable snapshot off the shared
# volume. The store mints the JWT those come from. Kept byte-for-byte as the store's own
# repository ships it — the two must agree about the volume layout or nothing compiles.
CUBE_CONFIG = '''\
import re
from pathlib import Path

from cube import config, file_repository

DATA = Path("/data")
SAFE_REF = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,100}$")


def resolve(ctx):
    """-> (ref, snapshot_dir). Raises on anything unexpected."""
    sc = ctx.get("securityContext") or {}
    ref, rev = sc.get("ref") or "main", sc.get("rev")
    if not SAFE_REF.match(ref) or ".." in ref:
        raise Exception("invalid ref")
    if rev is None:  # no rev in context (e.g. an internal call): use the ref's tip
        rev = (DATA / "state" / f"{ref}.rev").read_text().strip()
    if not str(rev).isdigit():
        raise Exception("invalid rev")
    snap = (DATA / "snap" / ref / str(rev)).resolve()
    if not str(snap).startswith(str((DATA / "snap").resolve())):  # traversal guard
        raise Exception("invalid ref")
    if not snap.is_dir():
        raise Exception(f"unknown revision: {ref}@{rev}")  # GC'd or never made
    return ref, snap


@config("context_to_app_id")
def context_to_app_id(ctx):
    ref, snap = resolve(ctx)
    return f"{ref}@{snap.name}"  # <- hot reload lives here


@config("repository_factory")
def repository_factory(ctx):
    return file_repository(str(resolve(ctx)[1]))  # immutable snapshot, never the worktree


@config("pre_aggregations_schema")
def pre_aggregations_schema(ctx):
    # Keyed on ref, never rev, so a branch can never read tables built from another model.
    return "pre_aggs_" + re.sub(r"[^a-z0-9]+", "_", resolve(ctx)[0].lower())


# Every save mints a new app_id, so this LRU is the memory ceiling.
config.compiler_cache_size = 32
'''

# The hand-over is rendered from here rather than read off disk, so a wheel can produce one.
# Nothing is built from this repository any more: every image named below is published elsewhere.
SEMANTIC = """\
# The semantic layer. Paste this wherever it should run — the workspace only ever talks to one
# URL here, the model store's.
#
# The store is the only door. Cube's port is not published; queries reach it at
# `/refs/<ref>/query`, and the store mints the ref-scoped JWT itself, so CUBEJS_API_SECRET
# never leaves this file. That JWT names a ref *and* a revision, which is what lets one Cube
# serve every branch of the model at once and pick up a change within seconds, with no restart.
#
# Cube Store is here because production mode makes it the queue/cache driver and refuses to
# run a query without it.
#
# CUBEJS_DEV_MODE is false and there is no second Cube. Dev mode disables JWT verification
# outright, and it exists upstream only to serve warehouse introspection and to build
# pre-aggregations — the portal reads the warehouse through the production Cube instead, and
# builds no pre-aggregations. There is deliberately no port here that must never be exposed.
#
# To move the published port, change the left-hand number only. A driver whose Cube build needs
# the JDK (Databricks) runs `cubejs/cube:VERSION-jdk` with `platform: linux/amd64`.
#
# The images were chosen for the architecture this file was generated on. Cube Store publishes
# one architecture per tag with no multi-arch manifest, so running this on a machine of the
# other kind means changing that one line: `-arm64v8` for arm64, no suffix for amd64. A pull
# for the wrong one fails outright rather than falling back.
#
# Every image is pinned, so this file describes one deployment rather than whatever was newest
# the day it was run. Upgrading is a fresh download from the portal, not an edit here.

name: praxis-semantic

x-cube-env: &cube-env
  CUBEJS_API_SECRET: ${CUBEJS_API_SECRET}
  # The warehouse, as it was configured in the portal.
  # PRAXIS:CREDENTIALS
  CUBEJS_CUBESTORE_HOST: cubestore
  CUBEJS_CUBESTORE_PORT: "3030"
  CUBEJS_DEFAULT_API_SCOPES: meta,data,graphql,sql
  CUBEJS_LOG_LEVEL: warn

services:
  modelstore:
    image: ${MODELSTORE_IMAGE}
    restart: unless-stopped
    environment:
      MODELSTORE_DATA_DIR: /data
      MODELSTORE_PROD_REF: main
      MODELSTORE_CUBE_URL: http://cube-api:4000
      # Introspection is Cube's dev server, which is not running here. The portal never asks
      # for it; pointed at the real Cube so anyone who does gets the explanation, not a hang.
      MODELSTORE_CUBE_INTROSPECT_URL: http://cube-api:4000
      MODELSTORE_SERVICE_TOKEN: ${MODELSTORE_SERVICE_TOKEN}
      CUBEJS_API_SECRET: ${CUBEJS_API_SECRET}
      # Every cap in the workspace sits under this one, so a slow query is reported there
      # rather than arriving here as a 502 with nothing to act on.
      MODELSTORE_CUBE_TIMEOUT_SECONDS: "${CUBE_TIMEOUT_S}"
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      # The git repo, the worktrees and the model snapshots. This is the source of truth for
      # every data model you have — back it up.
      - model:/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz')"]
      interval: 10s
      timeout: 3s
      retries: 12
      start_period: 15s

  cube-api:
    image: ${CUBE_IMAGE}
    restart: unless-stopped
    # The store bootstraps the volume first: with no snapshot and no revision on disk, Cube's
    # repository_factory has nothing to serve.
    depends_on:
      modelstore:
        condition: service_healthy
    environment:
      <<: *cube-env
      CUBEJS_DEV_MODE: "false"
      CUBEJS_REFRESH_WORKER: "false"
    volumes:
      - model:/data:ro
    configs:
      - source: cube_py
        target: /cube/conf/cube.py
    expose:
      - "4000"

  cubestore:
    image: ${CUBESTORE_IMAGE}
    restart: unless-stopped
    environment:
      CUBESTORE_REMOTE_DIR: /cube/data
    volumes:
      - cubestore:/cube/data

volumes:
  model:
  cubestore:

configs:
  cube_py:
    content: |
      # PRAXIS:CUBE_PY
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

name: praxis-sandbox

services:
  sandbox:
    image: ${SANDBOX_IMAGE}
    restart: unless-stopped
    environment:
      SANDBOX_TOKEN: ${SANDBOX_TOKEN}
      # The workspace's own caps sit above these, so a slow run is reported there rather than
      # abandoned here.
      SANDBOX_EXEC_TIMEOUT_S: "${SANDBOX_EXEC_TIMEOUT_S}"
      SANDBOX_PROFILE_TIMEOUT_S: "${SANDBOX_PROFILE_TIMEOUT_S}"
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


def missing(driver: str, credentials: dict[str, str]) -> list[str]:
    """What this warehouse cannot connect without and was not given, in plain words.

    Words rather than variable names because the answer becomes a message someone reads. It
    refuses nothing itself — it answers, and the caller decides. A driver this has never heard
    of is owed nothing, because an unlisted warehouse is configured by typing its own variables
    and a guessed requirement would refuse exactly that.
    """
    return [
        word
        for key, word in REQUIRED.get(driver, {}).items()
        if not credentials.get(key, "").strip()
    ]


def semantic(deployment: Deployment) -> str:
    return render(
        SEMANTIC,
        {
            "CUBEJS_API_SECRET": deployment.api_secret,
            "MODELSTORE_SERVICE_TOKEN": deployment.store_token,
            "MODELSTORE_IMAGE": MODELSTORE_IMAGE,
            "CUBE_IMAGE": CUBE_IMAGE,
            "CUBESTORE_IMAGE": CUBESTORE_IMAGE,
            "CUBE_TIMEOUT_S": f"{CUBE_TIMEOUT_S:.0f}",
        },
        # Every value is quoted, and quoted as JSON because YAML's double-quoted style takes the
        # same escapes: a service-account key is a JSON object, whose bare `{` would parse as a
        # YAML flow mapping, and a certificate is several lines, which is not a scalar at all.
        # Both reach the container intact through `"...\n..."` — YAML unescapes it and Cube reads
        # real newlines. Then `$` doubles, because compose interpolates its own variables after
        # parsing the YAML, so a password containing one would otherwise arrive truncated with a
        # warning about an environment nobody set.
        blocks={
            CREDENTIALS: [
                f"{key}: {json.dumps(value).replace('$', '$$')}"
                for key, value in deployment.credentials.items()
            ],
            # Doubled for the same reason, and a block for the same reason: compose interpolates
            # the config's content too, so the `$` anchoring SAFE_REF would arrive as a warning
            # about an environment nobody set and a regex that matches the wrong things.
            CUBE_PY: [line.replace("$", "$$") for line in CUBE_CONFIG.splitlines()],
        },
    )


def sandbox(deployment: Deployment) -> str:
    return render(
        SANDBOX,
        {
            "SANDBOX_TOKEN": deployment.sandbox_token,
            "SANDBOX_IMAGE": SANDBOX_IMAGE,
            "SANDBOX_EXEC_TIMEOUT_S": f"{SANDBOX_EXEC_TIMEOUT_S:.0f}",
            "SANDBOX_PROFILE_TIMEOUT_S": f"{SANDBOX_PROFILE_TIMEOUT_S:.0f}",
        },
    )


def render(text: str, values: dict[str, str], blocks: dict[str, list[str]] | None = None) -> str:
    lines = []
    for line in text.splitlines():
        marker = next((name for name in (blocks or {}) if line.strip().startswith(name)), None)
        if marker:
            # A block is lines, not a scalar, so it replaces the marker's whole line at its
            # indentation — which is what keeps the YAML valid. It is also the one thing the
            # guard below does not see, because the caller has already escaped it: a password
            # may legitimately contain a dollar sign, and what the guard is about is an
            # unresolved variable of *this template's*.
            padding = " " * (len(line) - len(line.lstrip()))
            # A blank line stays blank rather than becoming the indent: inside a `|` block
            # scalar the padding would be content, so it would put trailing whitespace into
            # every paragraph break of the file being handed over.
            lines += [f"{padding}{written}" if written else "" for written in blocks[marker]]
            continue
        for name in _VAR.findall(line):
            # Every variable is required, and empty counts as missing the way it does to
            # compose: an empty secret renders as a key with nothing after it and hands over a
            # deployment that starts cleanly and refuses every query.
            if not values.get(name):
                raise RuntimeError(f"{name} is required by the compose file and there is none")
            line = line.replace(f"${{{name}}}", values[name])
        if "$" in line:
            # A file with an unresolved variable reads as complete and dies at `up`.
            raise RuntimeError(f"unresolved in the rendered file: {line.strip()}")
        lines.append(line)

    return "\n".join(lines) + "\n"
