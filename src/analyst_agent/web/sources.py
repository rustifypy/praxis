"""One source: what it is, what it is serving, and editing its connection."""

import asyncio
import shutil
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..core.config import settings
from ..cube.client import CubeError, catalog_of
from ..sources import docker, modelgen, vcs
from ..sources.drivers import credentials_for, get, split_mounts
from ..sources.editing import edits, workspace
from ..sources.grounding import for_source
from ..sources.modelgen import selection
from ..sources.registry import Source, next_port, scaffold_source_dirs, validate_new
from ..sources.store import merge_credentials
from .state import find, view

router = APIRouter(prefix="/api")

CONTAINER_ACTIONS = ("start", "stop", "restart")


class NewSource(BaseModel):
    name: str
    description: str = ""
    driver: str
    port: int = 0
    """0 asks for the next free port."""
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


@router.get("/sources")
async def list_sources(request: Request) -> dict:
    store = request.app.state.sources
    sources, steps, containers, read = await asyncio.gather(
        store.all(), store.all_steps(), docker.ours(), store.all_catalog_read()
    )
    # Concurrently: a cold cache costs each source a /readyz and a /v1/meta, and a
    # stopped-but-listening one can burn five seconds before the next even starts.
    views = await asyncio.gather(
        *(
            view(request, s, containers, steps.get(s.name, {}), read.get(s.name, False))
            for s in sources
        )
    )
    # A blocking connect_ex per candidate port, off the event loop.
    port = await asyncio.to_thread(next_port, [s.port for s in sources])
    return {"sources": list(views), "next_port": port}


@router.get("/sources/{name}/logs")
async def source_logs(name: str, request: Request, tail: int = 200) -> dict:
    source = await find(request, name)
    return {"container": source.service, "logs": await docker.logs(source.service, tail)}


@router.get("/sources/{name}/catalog")
async def source_catalog(name: str, request: Request) -> dict:
    """The cached catalog, for the picker. Never reads the database."""
    store = request.app.state.sources
    await find(request, name)
    state = await store.state(name)
    catalog = {
        schema: modelgen.state.load_schema(raw)
        for schema, raw in (state.get("catalog") or {}).items()
    }
    return {
        "read": bool(catalog),
        "truncated": bool(state.get("catalog_truncated")),
        "notes": state.get("catalog_notes") or [],
        "selection": state.get("selection") or {},
        "schemas": [
            {"schema": schema_name, "tables": selection.describe(schema)}
            for schema_name, schema in sorted(catalog.items())
        ],
    }


@router.get("/sources/{name}/model")
async def source_model(name: str, request: Request) -> dict:
    """What Cube has compiled, read live from this source's own `/v1/meta`."""
    source = await find(request, name)
    # Which file defines each entity, so the panel that lists what Cube serves is also the
    # panel that edits it. Keyed by name and read from the working tree rather than assumed
    # from `cubes/<name>.yml`: a hand-edited model may put a cube anywhere.
    model_dir = source.model_dir()
    on_disk, _ = edits.read(workspace.files(source))
    branch = await vcs.current(model_dir) if vcs.is_repo(model_dir) else ""
    state = {
        "branch": branch,
        "editable": bool(branch) and branch != vcs.MAIN,
        "dirty": await vcs.status(model_dir) if branch else [],
    }

    try:
        catalog = await catalog_of(source)
    except CubeError as exc:
        # A stopped container does not stop the editing. The files are on this machine, and
        # the branch is still what a restart would load.
        return {
            "serving": False,
            "error": str(exc)[:400],
            "entities": [],
            "unloaded": sorted({e.file for e in on_disk.values()}),
            **state,
        }

    return {
        "serving": True,
        "error": "",
        **state,
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


@router.get("/sources/{name}/grounding")
async def source_grounding(name: str, request: Request) -> dict:
    """The markdown this source grounds the agent with, exactly as the agent loads it."""
    source = await find(request, name)
    loaded = for_source(source)
    kinds = (
        ("rule", loaded.rules),
        ("skill", loaded.skills),
        ("certified", loaded.certified),
    )
    return {
        "root": str(source.grounding_dir()),
        "exists": source.grounding_dir().is_dir(),
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


@router.post("/sources/{name}/check")
async def check_now(name: str, request: Request) -> dict:
    """Probe now, ignoring `HEALTH_TTL_S`. Nothing else drops that entry without also
    starting or rebuilding something."""
    source = await find(request, name)
    request.app.state.health.pop(name, None)
    steps = await request.app.state.sources.steps(name)
    return await view(request, source, await docker.ours(), steps)


@router.post("/sources/{name}/closure")
async def closure(name: str, body: TableSelection, request: Request) -> dict:
    """What a tick-list actually models, and what it gives up."""
    # Only the requested schema is rebuilt: this fires on every checkbox tick, and
    # `cached_catalog` would reconstruct every Column of every schema each time.
    raw = (await request.app.state.sources.state(name)).get("catalog") or {}
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


@router.post("/sources")
async def create(body: NewSource, request: Request) -> dict:
    store = request.app.state.sources
    existing = await store.all()
    port = body.port or await asyncio.to_thread(next_port, [s.port for s in existing])

    # Validated on what was typed: a `mount_to` field is required as a host path, and
    # rewriting it to the container path first would hide a blank one.
    if error := validate_new(body.name, body.driver, port, body.values, existing):
        raise HTTPException(400, error)
    spec = get(body.driver)
    values, mounts = _paths(spec, body.values)

    source = Source(
        name=body.name,
        description=body.description,
        driver=body.driver,
        port=port,
        mounts=mounts,
        credentials=credentials_for(spec, values),
    )
    await store.add(source)
    scaffold_source_dirs(source)
    return await view(request, source, await docker.ours(), steps={})


@router.patch("/sources/{name}")
async def edit(name: str, body: EditSource, request: Request) -> dict:
    """Change the description or the credentials."""
    store = request.app.state.sources
    source = await find(request, name)
    merged = mounts = None
    if body.values:
        values, supplied = _paths(source.spec, body.values)
        merged = merge_credentials(source.credentials, values)
        # Only the paths actually retyped: blank means "unchanged" here too, and
        # replacing the map wholesale would drop a key file the form did not ask about.
        mounts = {**source.mounts, **supplied}
    await store.update(name, description=body.description, credentials=merged, mounts=mounts)
    request.app.state.health.pop(name, None)
    updated = await store.get(name)
    return await view(request, updated, await docker.ours(), await store.steps(name))


@router.delete("/sources/{name}")
async def remove(name: str, request: Request, keep_model: bool = False) -> dict:
    """Remove the row, the container, and unless kept the model files."""
    source = await find(request, name)
    if source.is_active:
        raise HTTPException(
            409,
            f"{name} is the source this portal queries (CUBE_URL={settings.cube_url}) — "
            "point CUBE_URL elsewhere and restart before removing it",
        )
    if request.app.state.jobs.busy:
        raise HTTPException(409, "a job is running — wait for it to finish")

    await docker.remove(source.service)
    if not keep_model:
        shutil.rmtree(source.model_dir(), ignore_errors=True)
    await request.app.state.sources.remove(name)
    request.app.state.health.pop(name, None)
    return {"removed": name}


@router.post("/sources/{name}/container/{action}")
async def control(name: str, action: str, request: Request) -> dict:
    """start / stop / restart the container, without touching the model."""
    if action not in CONTAINER_ACTIONS:
        raise HTTPException(
            404, f"no such action {action!r} — one of {', '.join(CONTAINER_ACTIONS)}"
        )
    source = await find(request, name)
    result = await getattr(docker, action)(source.service)
    request.app.state.health.pop(name, None)
    if not result.ok:
        raise HTTPException(400, result.message or f"could not {action} {source.service}")
    steps = await request.app.state.sources.steps(name)
    return await view(request, source, await docker.ours(), steps)
