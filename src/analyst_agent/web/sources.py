"""The data source: what it is, what it is serving, and editing its connection."""

import shutil
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..cube.client import CubeError, served_catalog
from ..sources import docker, grounding, modelgen, vcs
from ..sources.drivers import credentials_for, get, missing_fields, split_mounts
from ..sources.editing import edits, workspace
from ..sources.modelgen import selection
from ..sources.registry import Source, grounding_dir, model_dir, scaffold_dirs
from ..sources.store import merge_credentials
from .state import find, read, view

router = APIRouter(prefix="/api")

CONTAINER_ACTIONS = ("start", "stop", "restart")


class NewSource(BaseModel):
    description: str = ""
    driver: str
    values: dict[str, str] = Field(default_factory=dict)


class EditSource(BaseModel):
    description: str | None = None
    values: dict[str, str] = Field(default_factory=dict)
    """A key with an empty value keeps what is stored — the form is never sent the
    password it would otherwise overwrite with blank."""


class TableSelection(BaseModel):
    """Which tables the picker is asking about. Its own model rather than the one the cubes
    action takes: the shape matches today, the question does not."""

    schema_name: str = "public"
    tables: list[str] | None = None


@router.get("/source")
async def get_source(request: Request) -> dict | None:
    """The connection, or null before one has been made."""
    store = request.app.state.sources
    source = await store.get()
    if source is None:
        return None
    return await view(request, source, await docker.state(), await store.steps())


@router.get("/source/logs")
async def source_logs(request: Request, tail: int = 200) -> dict:
    return {"container": docker.CONTAINER, "logs": await docker.logs(tail)}


@router.get("/source/catalog")
async def source_catalog(request: Request) -> dict:
    """The cached catalog, for the picker. Never reads the database."""
    store = request.app.state.sources
    current = await store.state()
    catalog = {
        schema: modelgen.state.load_schema(raw)
        for schema, raw in (current.get("catalog") or {}).items()
    }
    return {
        "read": bool(catalog),
        "truncated": bool(current.get("catalog_truncated")),
        "notes": current.get("catalog_notes") or [],
        "selection": current.get("selection") or {},
        "schemas": [
            {"schema": schema_name, "tables": selection.describe(schema)}
            for schema_name, schema in sorted(catalog.items())
        ],
    }


@router.get("/source/model")
async def source_model(request: Request) -> dict:
    """What Cube has compiled, read live from its own `/v1/meta`."""
    # Which file defines each entity, so the panel that lists what Cube serves is also the
    # panel that edits it. Keyed by name and read from the working tree rather than assumed
    # from `cubes/<name>.yml`: a hand-edited model may put a cube anywhere.
    repo = model_dir()
    on_disk, _ = edits.read(workspace.files())
    branch = await vcs.current(repo) if vcs.is_repo(repo) else ""
    editing_state = {
        "branch": branch,
        "editable": bool(branch) and branch != vcs.MAIN,
        "dirty": await vcs.status(repo) if branch else [],
    }

    try:
        catalog = await served_catalog()
    except CubeError as exc:
        # A stopped container does not stop the editing. The files are on this machine, and
        # the branch is still what a restart would load.
        return {
            "serving": False,
            "error": str(exc)[:400],
            "entities": [],
            "unloaded": sorted({e.file for e in on_disk.values()}),
            **editing_state,
        }

    return {
        "serving": True,
        "error": "",
        **editing_state,
        # Shipped rather than recomputed in the browser: "over cubes only, because a view's
        # members are the cube's re-exported" is a fact about the model, not about the panel.
        "counts": asdict(catalog.counts()),
        # Defined on disk but absent from what Cube serves. With the file watcher on this is
        # a file that failed to compile; with dev mode off it is simply one not loaded yet.
        "unloaded": sorted(
            {e.file for e in on_disk.values() if e.name not in {c.name for c in catalog.entities}}
        ),
        "entities": [
            {
                "name": e.name,
                "file": on_disk[e.name].file if e.name in on_disk else "",
                "is_view": e.is_view,
                "title": e.title,
                "description": e.description,
                "ai_context": e.ai_context,
                "folders": [asdict(f) for f in e.folders],
                "draws_from": catalog.draws_from(e),
                "exposed_by": catalog.exposed_by(e),
                "members": [
                    {
                        "id": m.id,
                        "name": m.id.rpartition(".")[2],
                        "kind": m.kind,
                        "type": m.type,
                        "title": m.title,
                        "description": m.description,
                        "ai_context": m.ai_context,
                        "granularities": m.granularities,
                        "operators": m.valid_operators,
                        "alias": m.alias_member,
                        "exposed_by": [] if e.is_view else catalog.exposure.get(m.id, []),
                    }
                    for m in e.members
                ],
            }
            for e in catalog.entities
        ],
    }


@router.get("/source/grounding")
async def source_grounding(request: Request) -> dict:
    """The markdown the agent is grounded with, exactly as it loads it."""
    loaded = grounding.load()
    kinds = (
        ("rule", loaded.rules),
        ("skill", loaded.skills),
        ("certified", loaded.certified),
    )
    return {
        "root": str(grounding_dir()),
        "exists": grounding_dir().is_dir(),
        "artefacts": [
            {
                "kind": kind,
                "name": artefact.name,
                "file": artefact.file,
                "description": artefact.description,
                # Rules only, and defaulted the way `always_rules` defaults it: an
                # untyped rule loads every turn, which is the expensive half.
                "loading": artefact.meta.get("type", "always") if kind == "rule" else "",
                "user_request": artefact.meta.get("user_request", ""),
                "body": artefact.body,
            }
            for kind, artefacts in kinds
            for artefact in artefacts
        ],
    }


@router.post("/source/check")
async def check_now(request: Request) -> dict:
    """Probe now, ignoring `HEALTH_TTL_S`. Nothing else drops that entry without also
    starting or rebuilding something."""
    request.app.state.health = None
    return await read(request)


@router.post("/source/closure")
async def closure(body: TableSelection, request: Request) -> dict:
    """What a tick-list actually models, and what it gives up."""
    # Only the requested schema is rebuilt: this fires on every checkbox tick, and
    # `cached_catalog` would reconstruct every Column of every schema each time.
    raw = (await request.app.state.sources.state()).get("catalog") or {}
    if body.schema_name not in raw:
        raise HTTPException(404, f"schema {body.schema_name!r} has not been read")
    schema = modelgen.state.load_schema(raw[body.schema_name])
    picked = body.tables or []
    chosen = sorted(selection.closure(schema, picked))
    return {
        "tables": chosen,
        "added": [t for t in chosen if t not in picked],
        "cut_joins": selection.cut_joins(schema, chosen),
        "keyless": [t for t in selection.keyless(schema) if t in chosen],
    }


# --- writing -----------------------------------------------------------------


def _paths(spec, values: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Split host paths out of a form, refusing one that is not there."""
    kept, mounts = split_mounts(spec, values)
    for container_path, host_path in mounts.items():
        if not Path(host_path).is_file():
            field = next(f for f in spec.fields if f.mount_to == container_path)
            raise HTTPException(400, f"{field.prompt}: no file at {host_path}")
    return kept, mounts


@router.post("/source")
async def connect(body: NewSource, request: Request) -> dict:
    """Replace the connection. Steps and cached state go with it: they describe a database,
    and this is a different one."""
    if request.app.state.jobs.busy:
        raise HTTPException(409, "a job is running — wait for it to finish")
    try:
        spec = get(body.driver)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from None
    # Validated on what was typed: a `mount_to` field is required as a host path, and
    # rewriting it to the container path first would hide a blank one.
    if missing := missing_fields(spec, body.values):
        raise HTTPException(400, f"missing required fields: {', '.join(missing)}")
    values, mounts = _paths(spec, body.values)

    source = Source(
        driver=body.driver,
        description=body.description,
        mounts=mounts,
        credentials=credentials_for(spec, values),
    )
    await request.app.state.sources.save(source)
    scaffold_dirs()
    request.app.state.health = None
    return await view(request, source, await docker.state(), steps={})


@router.patch("/source")
async def edit(body: EditSource, request: Request) -> dict:
    """Change the description or the credentials."""
    store = request.app.state.sources
    source = await find(request)
    merged = mounts = None
    if body.values:
        values, supplied = _paths(source.spec, body.values)
        merged = merge_credentials(source.credentials, values)
        # Only the paths actually retyped: blank means "unchanged" here too, and
        # replacing the map wholesale would drop a key file the form did not ask about.
        mounts = {**source.mounts, **supplied}
    await store.update(description=body.description, credentials=merged, mounts=mounts)
    request.app.state.health = None
    return await read(request)


@router.delete("/source")
async def remove(request: Request, keep_model: bool = False) -> dict:
    """Remove the row, the container, and unless kept the model files."""
    await find(request)
    if request.app.state.jobs.busy:
        raise HTTPException(409, "a job is running — wait for it to finish")

    await docker.remove()
    if not keep_model:
        shutil.rmtree(model_dir(), ignore_errors=True)
    await request.app.state.sources.remove()
    request.app.state.health = None
    return {"removed": True}


@router.post("/source/container/{action}")
async def control(action: str, request: Request) -> dict:
    """start / stop / restart the container, without touching the model."""
    if action not in CONTAINER_ACTIONS:
        raise HTTPException(
            404, f"no such action {action!r} — one of {', '.join(CONTAINER_ACTIONS)}"
        )
    await find(request)
    result = await getattr(docker, action)()
    request.app.state.health = None
    if not result.ok:
        raise HTTPException(400, result.message or f"could not {action} {docker.CONTAINER}")
    return await read(request)
