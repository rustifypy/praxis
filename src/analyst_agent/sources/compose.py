"""Renders docker-compose.sources.yml from sources.yml.

Reads only the registry, never the credential files — secrets stay in
.env.sources/<name>.env and reach the container through env_file.
"""

from pathlib import Path

import yaml

from .registry import COMPOSE_FILE, ENV_DIR, MODEL_ROOT, Source, load, project_root

HEADER = "# GENERATED from sources.yml by `analyst source add|rm`. Do not edit by hand.\n"

ROUTER = "ds-cubestore-router"
WORKER = "ds-cubestore-worker"
VOLUME = "ds-cubestore"


def service(source: Source) -> dict:
    spec = source.spec
    body = {
        "image": spec.image,
        "env_file": [f"{ENV_DIR}/{source.name}.env"],
        "environment": {
            "CUBEJS_API_SECRET": "${CUBEJS_API_SECRET:?set CUBEJS_API_SECRET in .env}",
            "CUBEJS_DEV_MODE": "${CUBEJS_DEV_MODE:-false}",
            "CUBEJS_CUBESTORE_HOST": ROUTER,
            "CUBEJS_CUBESTORE_PORT": "3030",
        },
        "ports": [f"{source.port}:4000"],
        "volumes": [f"./{MODEL_ROOT}/{source.name}:/cube/conf/model"],
        "depends_on": {ROUTER: {"condition": "service_started"}},
    }
    for container_path, host_path in source.mounts.items():
        body["volumes"].append(f"{host_path}:{container_path}:ro")
    if spec.platform:
        body["platform"] = spec.platform
    return body


def render(sources: list[Source]) -> str:
    doc = {
        "services": {
            **{s.service: service(s) for s in sources},
            ROUTER: {
                "image": "cubejs/cubestore:${CUBESTORE_TAG:-latest}",
                "environment": {
                    "CUBESTORE_HTTP_PORT": 3030,
                    "CUBESTORE_WORKERS": f"{WORKER}:10001",
                    "CUBESTORE_REMOTE_DIR": "/cube/data",
                    "CUBESTORE_META_PORT": 9999,
                },
                "volumes": [f"{VOLUME}:/cube/data"],
            },
            WORKER: {
                "image": "cubejs/cubestore:${CUBESTORE_TAG:-latest}",
                "environment": {
                    "CUBESTORE_WORKER_PORT": 10001,
                    "CUBESTORE_SERVER_NAME": f"{WORKER}:10001",
                    "CUBESTORE_META_ADDR": f"{ROUTER}:9999",
                    "CUBESTORE_WORKERS": f"{WORKER}:10001",
                    "CUBESTORE_REMOTE_DIR": "/cube/data",
                },
                "depends_on": [ROUTER],
                "volumes": [f"{VOLUME}:/cube/data"],
            },
        },
        "volumes": {VOLUME: None},
    }
    return HEADER + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def write(sources: list[Source] | None = None, root: Path | None = None) -> Path:
    root = root or project_root()
    path = root / COMPOSE_FILE
    path.write_text(render(load(root) if sources is None else sources))
    return path
