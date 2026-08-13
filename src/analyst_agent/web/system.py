"""Machine-wide state: docker, the shared Cube Store, orphaned containers, and the driver
specs the connection form is built from. Nothing here is about one source."""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..sources import docker, model
from ..sources.drivers import DRIVERS, LOCAL_HOSTS, LOCALHOST_NOTE, required_envs
from ..sources.registry import NAME_PATTERN

router = APIRouter(prefix="/api")


@router.get("/system")
async def system(request: Request) -> dict:
    """Whether this feature can work at all, in one call."""
    if not docker.installed():
        return {"ok": False, "reason": "docker is not installed on this machine"}
    info = await docker.daemon()
    if not info.ok:
        return {"ok": False, "reason": "Docker is not running — start Docker Desktop"}

    containers, cubestore, known = await asyncio.gather(
        docker.ours(), docker.cubestore_running(), request.app.state.sources.names()
    )
    return {
        "ok": True,
        "docker": info.out.strip(),
        "cubestore": cubestore,
        "orphans": docker.orphans(containers, known),
        "cube_url": settings.cube_url,
        "dev_mode": settings.cubejs_dev_mode,
    }


@router.get("/drivers")
async def drivers() -> dict:
    """The connection form, described by the driver specs themselves."""
    return {
        "local_hosts": list(LOCAL_HOSTS),
        # One rule for a source name, tested on both sides of the wire.
        "name_pattern": NAME_PATTERN,
        "localhost_note": LOCALHOST_NOTE,
        "drivers": [
            {
                "key": spec.key,
                "label": spec.label,
                "extracts_tables": key in model.read.DIALECTS,
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


@router.delete("/containers/{container}")
async def remove_orphan(container: str, request: Request) -> dict:
    """Delete a `cube-*` container that has no source row."""
    known = await request.app.state.sources.names()
    if container not in docker.orphans(await docker.ours(), known):
        raise HTTPException(400, f"{container} is not an orphaned source container")
    result = await docker.remove(container)
    if not result.ok:
        raise HTTPException(400, result.message)
    return {"removed": container}
