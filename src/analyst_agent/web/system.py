"""Machine-wide state: docker, the shared Cube Store, and the driver specs the connection
form is built from. Nothing here is about what is connected."""

import asyncio

from fastapi import APIRouter

from ..core.config import settings
from ..sources import docker, modelgen
from ..sources.drivers import DRIVERS, LOCAL_HOSTS, LOCALHOST_NOTE, required_envs

router = APIRouter(prefix="/api")


@router.get("/system")
async def system() -> dict:
    """Whether this feature can work at all, in one call."""
    if not docker.installed():
        return {"ok": False, "reason": "docker is not installed on this machine"}
    info = await docker.daemon()
    if not info.ok:
        return {"ok": False, "reason": "Docker is not running — start Docker Desktop"}

    container, cubestore = await asyncio.gather(docker.state(), docker.cubestore_running())
    return {
        "ok": True,
        "docker": info.out.strip(),
        "cubestore": cubestore,
        "container_state": container or "missing",
        "cube_url": settings.cube_url,
        "dev_mode": settings.cubejs_dev_mode,
    }


@router.get("/drivers")
async def drivers() -> dict:
    """The connection form, described by the driver specs themselves."""
    return {
        "local_hosts": list(LOCAL_HOSTS),
        "localhost_note": LOCALHOST_NOTE,
        "drivers": [
            {
                "key": spec.key,
                "label": spec.label,
                "extracts_tables": key in modelgen.read.DIALECTS,
                "required_envs": required_envs(spec),
                "fields": [
                    {
                        "env": f.env,
                        "label": f.prompt,
                        "default": f.default,
                        "secret": f.secret,
                        "boolean": f.boolean,
                        "mount_to": f.mount_to,
                        "help": f.comment,
                    }
                    for f in spec.fields
                    if not f.fixed
                ],
            }
            for key, spec in DRIVERS.items()
        ],
    }
