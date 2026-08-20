"""What this install can reach, and the driver specs the connection form is built from.

**This process runs nothing.** Cube, the model store and the sandbox are all services someone
else started, so there is no docker here to report on and nothing here is fatal — a machine
without docker runs the portal perfectly well, because the deployment is not on it.
"""

from fastapi import APIRouter

from ..core.deployment import current
from ..datamodel import modelgen
from ..datamodel.drivers import DRIVERS, LOCAL_HOSTS, LOCALHOST_NOTE, required_envs
from ..datamodel.registry import semantic_dir
from ..sandbox import SandboxError, open_sandbox

router = APIRouter(prefix="/api")


@router.get("/system")
async def system() -> dict:
    """The two addresses and whether the optional one answers, in one call. There is no Cube
    address: it is reached through the store, which is the only door in."""
    deployment = await current()
    sandbox_reason = ""
    try:
        async with await open_sandbox(timeout=5.0) as sandbox:
            await sandbox._send("GET", "/health")
    except SandboxError as exc:
        # Its own field rather than a failed request: code execution is the only thing that
        # needs it, and everything else on the page works without one.
        sandbox_reason = str(exc)

    return {
        "store_url": deployment.store_url if deployment else "",
        "sandbox_url": deployment.sandbox_url if deployment else "",
        "sandbox_reason": sandbox_reason,
        # Where the compose file the deployment is run from lives, so the panel can say
        # *which* directory the environment file goes in.
        "semantic_dir": str(semantic_dir()),
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
